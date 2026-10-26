#!/usr/bin/env python3
"""Resolve the reviewed, opt-in Postgres sandbox profile."""

from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import argparse
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
_RUNNER_PROFILE_CONFIG = Path(__file__).with_name("ci-sandbox-profiles.json")
PROFILE_CONFIG = (
    _RUNNER_PROFILE_CONFIG
    if _RUNNER_PROFILE_CONFIG.is_file()
    else ROOT / "config" / "ci-sandbox-profiles.json"
)
PROFILE_FIELDS = {
    "image",
    "digest",
    "platform",
    "serverVersion",
    "clientVersion",
    "networkMode",
    "publishedPorts",
}
PROFILE_NAME = "postgres-17.11"
SCHEMA_VALIDATION_PROFILE_NAME = "postgres-schema-validation-17.11"
PROFILE_NAMES = {PROFILE_NAME, SCHEMA_VALIDATION_PROFILE_NAME}
SCHEMA_VALIDATION_DATABASES = {
    "schema_snapshot": "DATABASE_URL_SCHEMA_SNAPSHOT",
    "schema_migrations": "DATABASE_URL_SCHEMA_MIGRATIONS",
    "schema_reference": "DATABASE_URL_SCHEMA_REFERENCE",
}
PROFILE_ROOT_PREFIX = "verjson-postgres-profile-"
PROFILE_ROOT_NAME_PATTERN = re.compile(r"verjson-postgres-profile-[a-z0-9_]{8}\Z")
PROFILE_IMAGE_DIGEST = (
    "sha256:3518071376d0b6156b02e1db2747a78511b5b9dc3763cc79c506ebe2dc2ee71c"
)
POSTGRES_INITIALIZATION_COMPLETE_MARKER = (
    "PostgreSQL init process complete; ready for start up."
)
PROFILE_STATE_FIELDS = {
    "schemaVersion",
    "profileName",
    "containerName",
    "containerId",
    "rootDirectory",
    "socketDirectory",
    "clientBundleDirectory",
    "socketDevice",
    "socketInode",
    "socketMode",
    "status",
}
SOCKET_SANDBOX_PATH = "/run/verjson-postgres"
CLIENT_SANDBOX_PATH = "/opt/verjson/postgres-client"
POSTGRES_SOCKET_NAME = ".s.PGSQL.5432"
POSTGRES_HBA_NAME = "pg_hba.conf"
POSTGRES_HBA_SANDBOX_PATH = "/var/run/postgresql/pg_hba.conf"


class PostgresSandboxError(ValueError):
    """A requested Postgres sandbox profile is not a reviewed configuration."""


def _cleanup_failed_staging(
    root: Path,
    state_path: Path,
    failure: BaseException,
    *,
    remove_state: bool,
) -> None:
    if remove_state:
        try:
            state_path.unlink(missing_ok=True)
        except BaseException as cleanup_failure:
            failure.add_note(
                f"Postgres staging receipt cleanup failed: {cleanup_failure!r}"
            )
    try:
        if root.exists():
            _make_tree_removable(root)
            shutil.rmtree(root, ignore_errors=False)
    except BaseException as cleanup_failure:
        failure.add_note(
            f"Postgres staging directory cleanup failed: {cleanup_failure!r}"
        )


def local_auth_policy(profile_name: str = PROFILE_NAME) -> str:
    """Allow trusted maintenance and only the selected profile's candidate databases."""
    if profile_name == PROFILE_NAME:
        candidate_rules = ["local app_test verjson_ci trust"]
    elif profile_name == SCHEMA_VALIDATION_PROFILE_NAME:
        candidate_rules = [
            f"local {database} verjson_ci trust"
            for database in SCHEMA_VALIDATION_DATABASES
        ]
    else:
        raise PostgresSandboxError(f"unknown Postgres sandbox profile: {profile_name!r}")
    return "\n".join(
        [
            "local all postgres peer",
            *candidate_rules,
            "local all all reject",
            "host all all 0.0.0.0/0 reject",
            "host all all ::/0 reject",
            "",
        ]
    )


def _profile_document() -> dict[str, Any]:
    try:
        metadata = PROFILE_CONFIG.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 64_000:
            raise PostgresSandboxError("Postgres sandbox profile config is unsafe")
        document = json.loads(PROFILE_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PostgresSandboxError("Postgres sandbox profile config is unreadable") from exc
    if not isinstance(document, dict) or set(document) != {"schemaVersion", "profiles"}:
        raise PostgresSandboxError("Postgres sandbox profile config has an invalid shape")
    if document["schemaVersion"] != 1 or type(document["schemaVersion"]) is not int:
        raise PostgresSandboxError("unsupported Postgres sandbox profile schema")
    if not isinstance(document["profiles"], dict):
        raise PostgresSandboxError("Postgres sandbox profiles must be an object")
    return document


def resolve_profile(name: str | None) -> dict[str, Any] | None:
    """Resolve only the reviewed profile; an empty value leaves it disabled."""
    if name is None or name == "":
        return None
    if not isinstance(name, str) or name not in PROFILE_NAMES:
        raise PostgresSandboxError(f"unknown Postgres sandbox profile: {name!r}")

    profiles = _profile_document()["profiles"]
    if set(profiles) != PROFILE_NAMES:
        raise PostgresSandboxError("Postgres sandbox profile allowlist changed unexpectedly")
    profile = profiles[name]
    if not isinstance(profile, dict) or set(profile) != PROFILE_FIELDS:
        raise PostgresSandboxError("Postgres sandbox profile has an invalid shape")
    if (
        profile["image"] != "docker.io/library/postgres"
        or profile["digest"] != PROFILE_IMAGE_DIGEST
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", profile["digest"])
        or profile["platform"] != "linux/amd64"
        or profile["serverVersion"] != "17.11"
        or profile["clientVersion"] != "17.11"
        or profile["networkMode"] != "none"
        or profile["publishedPorts"] != []
    ):
        raise PostgresSandboxError("Postgres sandbox profile is outside the reviewed boundary")
    return {**profile, "name": name, "image": f"{profile['image']}@{profile['digest']}"}


def validate_profile_request(
    name: str | None,
    *,
    db_image: str = "",
    cache_image: str = "",
    artifact_role: str = "",
    compatibility_ranges: str = "",
    secretless_mode: bool = True,
) -> dict[str, Any] | None:
    """Reject incompatible services, compatibility requests, and D1 sharing."""
    profile = resolve_profile(name)
    if profile is None:
        return None
    if not secretless_mode:
        raise PostgresSandboxError("Postgres sandbox profile requires the secretless script-plan lane")
    if compatibility_ranges.strip() not in {"", "{}"}:
        raise PostgresSandboxError(
            "Postgres sandbox profile is incompatible with secretless compatibility requests"
        )
    conflicts = {
        "db-image": db_image,
        "cache-image": cache_image,
    }
    active_conflicts = [key for key, value in conflicts.items() if value != ""]
    if artifact_role not in {"", "level"}:
        active_conflicts.append("build-artifact-role")
    if active_conflicts:
        raise PostgresSandboxError(
            "Postgres sandbox profile is incompatible with " + ", ".join(active_conflicts)
        )
    return profile


def validate_profile_state(
    state: Any,
    runner_temp: str | Path,
    *,
    allow_missing_root: bool = False,
) -> dict[str, Any]:
    """Validate the per-run receipt before any host path or container is trusted."""
    if not isinstance(state, dict) or set(state) != PROFILE_STATE_FIELDS:
        raise PostgresSandboxError("Postgres sandbox state has an invalid shape")
    if state["schemaVersion"] != 1 or type(state["schemaVersion"]) is not int:
        raise PostgresSandboxError("unsupported Postgres sandbox state schema")
    if state["profileName"] not in PROFILE_NAMES:
        raise PostgresSandboxError("Postgres sandbox state names an unreviewed profile")
    if not isinstance(state["containerName"], str) or not re.fullmatch(
        r"verjson-postgres-[a-f0-9]{12}", state["containerName"]
    ):
        raise PostgresSandboxError("Postgres sandbox state has an invalid container name")
    container_id = state["containerId"]
    if container_id is not None and (
        not isinstance(container_id, str) or not re.fullmatch(r"[a-f0-9]{64}", container_id)
    ):
        raise PostgresSandboxError("Postgres sandbox state has an invalid container identity")
    runner = Path(runner_temp).resolve(strict=True)
    if not runner.is_dir() or runner.is_symlink():
        raise PostgresSandboxError("runner temp must be a real directory")
    root_directory = state["rootDirectory"]
    if not isinstance(root_directory, str):
        raise PostgresSandboxError("Postgres sandbox root path is invalid")
    root = Path(root_directory)
    root_exists = root.exists()
    if (
        not root.is_absolute()
        or root_directory != str(root)
        or not PROFILE_ROOT_NAME_PATTERN.fullmatch(root.name)
        or root.is_symlink()
        or root.parent != runner
        or (root_exists and not root.is_dir())
        or (not root_exists and not allow_missing_root)
    ):
        raise PostgresSandboxError("Postgres sandbox root escaped per-run runner temp")
    if state["socketDirectory"] != str(root / "socket"):
        raise PostgresSandboxError("Postgres socket directory escaped its per-run root")
    if state["clientBundleDirectory"] != str(root / "client"):
        raise PostgresSandboxError("Postgres client bundle escaped its per-run root")
    if state["status"] not in {"starting", "ready"}:
        raise PostgresSandboxError("Postgres sandbox state has an invalid lifecycle status")
    if state["status"] == "ready":
        if (
            container_id is None
            or type(state["socketDevice"]) is not int
            or type(state["socketInode"]) is not int
            or type(state["socketMode"]) is not int
        ):
            raise PostgresSandboxError("ready Postgres sandbox state is missing its receipt identity")
    elif any(state[key] is not None for key in ("socketDevice", "socketInode", "socketMode")):
        raise PostgresSandboxError("starting Postgres sandbox state carries an unexpected socket receipt")
    return state


def verify_profile_socket(state: dict[str, Any]) -> None:
    socket_directory = Path(state.get("socketDirectory", ""))
    if socket_directory.is_symlink() or not socket_directory.is_dir():
        raise PostgresSandboxError("Postgres socket directory was replaced")
    verify_local_auth_policy(
        socket_directory, profile_name=state.get("profileName", PROFILE_NAME)
    )
    socket_path = socket_directory / POSTGRES_SOCKET_NAME
    try:
        metadata = socket_path.lstat()
    except OSError as exc:
        raise PostgresSandboxError("Postgres Unix socket disappeared") from exc
    if (
        not stat.S_ISSOCK(metadata.st_mode)
        or metadata.st_dev != state.get("socketDevice")
        or metadata.st_ino != state.get("socketInode")
        or metadata.st_mode & 0o777 != state.get("socketMode")
    ):
        raise PostgresSandboxError("Postgres Unix socket was replaced or mutated")


def build_container_args(
    profile: dict[str, Any],
    *,
    container_name: str,
    socket_dir: str | Path,
) -> list[str]:
    """Build the trusted server command without TCP or a published port."""
    if profile.get("name") not in PROFILE_NAMES or profile.get("networkMode") != "none":
        raise PostgresSandboxError("container command requires the reviewed Postgres profile")
    if not re.fullmatch(r"verjson-postgres-[a-f0-9]{12}", container_name):
        raise PostgresSandboxError("invalid per-run Postgres container name")
    socket_path = Path(socket_dir)
    if not socket_path.is_absolute() or socket_path.is_symlink() or not socket_path.is_dir():
        raise PostgresSandboxError("Postgres socket directory must be a real absolute directory")
    return [
        "docker",
        "run",
        "--detach",
        "--name",
        container_name,
        "--platform",
        profile["platform"],
        "--user",
        "999:999",
        "--network",
        "none",
        "--tmpfs",
        "/var/lib/postgresql/data:rw,nosuid,nodev,noexec,size=512m,uid=999,gid=999,mode=700",
        "--mount",
        f"type=bind,source={socket_path},target=/var/run/postgresql",
        "--env",
        "POSTGRES_PASSWORD",
        profile["image"],
        "postgres",
        "-c",
        "listen_addresses=",
        "-c",
        "unix_socket_directories=/var/run/postgresql",
        "-c",
        "unix_socket_permissions=0777",
        "-c",
        f"hba_file={POSTGRES_HBA_SANDBOX_PATH}",
    ]


def container_environment(admin_password: str) -> dict[str, str]:
    """Keep the random bootstrap password out of Docker's process arguments."""
    if not admin_password or "\n" in admin_password or "\r" in admin_password:
        raise PostgresSandboxError("invalid Postgres administrator password")
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "POSTGRES_PASSWORD": admin_password,
    }


def _require_directory(path: str | Path, label: str) -> Path:
    resolved = Path(path)
    try:
        metadata = resolved.lstat()
    except OSError as exc:
        raise PostgresSandboxError(f"{label} is unavailable") from exc
    if not resolved.is_absolute() or not stat.S_ISDIR(metadata.st_mode):
        raise PostgresSandboxError(f"{label} must be a real absolute directory")
    return resolved


def write_local_auth_policy(
    socket_dir: str | Path, *, profile_name: str = PROFILE_NAME
) -> Path:
    socket_path = _require_directory(socket_dir, "Postgres socket directory")
    policy_path = socket_path / POSTGRES_HBA_NAME
    try:
        with policy_path.open("x", encoding="utf-8") as policy_file:
            policy_file.write(local_auth_policy(profile_name))
        policy_path.chmod(0o444)
    except OSError as exc:
        raise PostgresSandboxError("Postgres local auth policy could not be staged") from exc
    return policy_path


def verify_local_auth_policy(
    socket_dir: str | Path, *, profile_name: str = PROFILE_NAME
) -> None:
    policy_path = _require_directory(socket_dir, "Postgres socket directory") / POSTGRES_HBA_NAME
    try:
        metadata = policy_path.lstat()
        policy = policy_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PostgresSandboxError("Postgres local auth policy is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o777 != 0o444
        or metadata.st_size > 4096
        or policy != local_auth_policy(profile_name)
    ):
        raise PostgresSandboxError("Postgres local auth policy was replaced or mutated")


def build_bubblewrap_profile_args(
    *,
    socket_dir: str | Path,
    client_bundle: str | Path,
    profile_name: str = PROFILE_NAME,
) -> list[str]:
    """Expose only the server socket and verified client tree as read-only mounts."""
    socket_path = _require_directory(socket_dir, "Postgres socket directory")
    verify_local_auth_policy(socket_path, profile_name=profile_name)
    socket_file = socket_path / POSTGRES_SOCKET_NAME
    try:
        if not stat.S_ISSOCK(socket_file.lstat().st_mode):
            raise PostgresSandboxError("Postgres socket was replaced or is not a socket")
    except OSError as exc:
        raise PostgresSandboxError("Postgres Unix socket is unavailable") from exc

    client_path = _require_directory(client_bundle, "Postgres client bundle")
    binary_path = client_path / "bin"
    if not stat.S_ISDIR(binary_path.lstat().st_mode):
        raise PostgresSandboxError("Postgres client bundle has no verified bin directory")
    for name in ("psql", "pg_dump"):
        executable = binary_path / name
        try:
            metadata = executable.lstat()
        except OSError as exc:
            raise PostgresSandboxError(f"Postgres client bundle is missing {name}") from exc
        if not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & 0o111:
            raise PostgresSandboxError(f"Postgres client bundle has an unsafe {name} entry")

    return [
        "--dir",
        "/run",
        "--ro-bind",
        str(socket_path),
        SOCKET_SANDBOX_PATH,
        "--dir",
        "/opt",
        "--dir",
        "/opt/verjson",
        "--ro-bind",
        str(client_path),
        CLIENT_SANDBOX_PATH,
    ]


def profile_environment(
    path: str = "",
    *,
    profile_name: str = PROFILE_NAME,
) -> dict[str, str]:
    """Return candidate connection settings that can only target the Unix socket."""
    encoded_socket_path = SOCKET_SANDBOX_PATH.replace("/", "%2F")
    if profile_name == PROFILE_NAME:
        environment = {
            "DATABASE_URL": (
                f"postgresql://verjson_ci@{encoded_socket_path}/app_test?port=5432"
            ),
            "PGHOST": SOCKET_SANDBOX_PATH,
            "PGPORT": "5432",
            "PGUSER": "verjson_ci",
            "PGDATABASE": "app_test",
            "DB_HOST": SOCKET_SANDBOX_PATH,
            "DB_PORT": "5432",
        }
    elif profile_name == SCHEMA_VALIDATION_PROFILE_NAME:
        environment = {
            key: f"postgresql://verjson_ci@{encoded_socket_path}/{database}?port=5432"
            for database, key in SCHEMA_VALIDATION_DATABASES.items()
        }
        environment.update(
            {
                "PGHOST": SOCKET_SANDBOX_PATH,
                "PGPORT": "5432",
                "PGUSER": "verjson_ci",
            }
        )
    else:
        raise PostgresSandboxError(f"unknown Postgres sandbox profile: {profile_name!r}")
    if path:
        environment["PATH"] = f"{CLIENT_SANDBOX_PATH}/bin:{path}"
    return environment


def candidate_environment(
    inherited: dict[str, str],
    path: str = "",
    *,
    profile_name: str = PROFILE_NAME,
) -> dict[str, str]:
    """Remove ambient libpq routes and install only the reviewed socket settings."""
    candidate = {
        name: value
        for name, value in inherited.items()
        if not name.upper().startswith("PG")
        and name.upper() != "DATABASE_URL"
        and not (
            profile_name == SCHEMA_VALIDATION_PROFILE_NAME
            and name.upper().startswith("DATABASE_URL_")
        )
    }
    candidate.update(profile_environment(path, profile_name=profile_name))
    return candidate


def database_bootstrap_sql(*, profile_name: str = PROFILE_NAME) -> str:
    """Create only the databases and object permissions required by the selected profile."""
    if profile_name == PROFILE_NAME:
        return "\n".join(
            (
                "CREATE ROLE verjson_ci LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;",
                "CREATE DATABASE app_test OWNER postgres;",
                "REVOKE ALL ON DATABASE app_test FROM PUBLIC;",
                "GRANT CONNECT ON DATABASE app_test TO verjson_ci;",
                "\\connect app_test",
                "REVOKE ALL ON SCHEMA public FROM PUBLIC;",
                "GRANT USAGE, CREATE ON SCHEMA public TO verjson_ci;",
            )
        )
    if profile_name != SCHEMA_VALIDATION_PROFILE_NAME:
        raise PostgresSandboxError(f"unknown Postgres sandbox profile: {profile_name!r}")

    statements = [
        "CREATE ROLE verjson_ci LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;",
        "CREATE ROLE authenticated NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;",
    ]
    for database in SCHEMA_VALIDATION_DATABASES:
        statements.extend(
            (
                f"CREATE DATABASE {database} OWNER postgres;",
                f"REVOKE ALL ON DATABASE {database} FROM PUBLIC;",
                f"GRANT CONNECT ON DATABASE {database} TO verjson_ci;",
            )
        )
    statements.append("GRANT TEMP ON DATABASE schema_migrations TO verjson_ci;")
    for database in SCHEMA_VALIDATION_DATABASES:
        statements.extend(
            (
                f"\\connect {database}",
                "REVOKE ALL ON SCHEMA public FROM PUBLIC;",
                "GRANT USAGE, CREATE ON SCHEMA public TO verjson_ci;",
            )
        )
        if database == "schema_migrations":
            statements.extend(
                (
                    "CREATE SCHEMA auth AUTHORIZATION verjson_ci;",
                    "CREATE SCHEMA storage AUTHORIZATION verjson_ci;",
                    "SET ROLE verjson_ci;",
                    "CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), email text);",
                    "CREATE TABLE IF NOT EXISTS auth.identities (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid, provider text, provider_id text, identity_data jsonb);",
                    "CREATE TABLE IF NOT EXISTS storage.objects (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), bucket_id text, name text, owner uuid);",
                    "CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT NULL::uuid $$;",
                    "CREATE OR REPLACE FUNCTION storage.foldername(name text) RETURNS text[] LANGUAGE sql IMMUTABLE AS $$ SELECT string_to_array(name, '/') $$;",
                    "RESET ROLE;",
                )
            )
    return "\n".join(statements)


def parse_ldd_dependencies(output: str) -> dict[str, str]:
    """Parse only concrete dynamic-library paths from the pinned image's ldd output."""
    if not isinstance(output, str) or len(output) > 64_000:
        raise PostgresSandboxError("Postgres client dependency output is invalid")
    dependencies: dict[str, str] = {}
    for line in output.splitlines():
        if "not found" in line:
            raise PostgresSandboxError("Postgres client bundle has a missing library")
        match = re.fullmatch(r"\s*(\S+)\s+=>\s+(/[^\s]+)\s+\([^)]*\)\s*", line)
        if match is None:
            match = re.fullmatch(r"\s*(/[^\s]+)\s+\([^)]*\)\s*", line)
            if match is None:
                if not line.strip() or line.lstrip().startswith("linux-vdso.so"):
                    continue
                raise PostgresSandboxError("Postgres client dependency output is unsupported")
            soname, path = Path(match.group(1)).name, match.group(1)
        else:
            soname, path = match.groups()
        if (
            soname in {"", ".", ".."}
            or "/" in soname
            or not Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            raise PostgresSandboxError("Postgres client dependency path is unsafe")
        existing = dependencies.get(soname)
        if existing is not None and existing != path:
            raise PostgresSandboxError("Postgres client dependency has an ambiguous soname")
        dependencies[soname] = path
    if not dependencies or not any(name.startswith("ld-linux") for name in dependencies):
        raise PostgresSandboxError("Postgres client dependency output has no ELF loader")
    return dependencies


def _client_bundle_files(
    bundle: str | Path, *, require_read_only_directories: bool = True
) -> list[dict[str, str]]:
    root = _require_directory(bundle, "Postgres client bundle")
    entries: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if path == root / "manifest.json":
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o222:
                raise PostgresSandboxError("Postgres client manifest is writable or unsafe")
            continue
        if stat.S_ISDIR(metadata.st_mode):
            if path.parent != root or path.name not in {"bin", "lib"}:
                raise PostgresSandboxError("Postgres client bundle has an unexpected directory")
            if require_read_only_directories and metadata.st_mode & 0o222:
                raise PostgresSandboxError("Postgres client bundle directory is writable")
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o222:
            raise PostgresSandboxError("Postgres client bundle contains an unsafe file")
        relative = path.relative_to(root).as_posix()
        if relative.split("/", 1)[0] not in {"bin", "lib"}:
            raise PostgresSandboxError("Postgres client bundle has a file outside bin or lib")
        if relative.count("/") != 1:
            raise PostgresSandboxError("Postgres client bundle has a nested file path")
        entries.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    names = {entry["path"] for entry in entries}
    required = {"bin/psql", "bin/pg_dump"}
    if not required.issubset(names) or not any(name.startswith("lib/ld-linux") for name in names):
        raise PostgresSandboxError("Postgres client bundle is incomplete")
    return entries


def write_client_manifest(bundle: str | Path, profile: dict[str, Any]) -> Path:
    root = _require_directory(bundle, "Postgres client bundle")
    entries = _client_bundle_files(root, require_read_only_directories=False)
    manifest = {
        "schemaVersion": 1,
        "profile": profile.get("name"),
        "image": profile.get("image"),
        "clientVersion": profile.get("clientVersion"),
        "files": entries,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o444)
    for directory in (root / "bin", root / "lib", root):
        directory.chmod(0o555)
    return manifest_path


def verify_client_bundle(bundle: str | Path, profile: dict[str, Any]) -> None:
    root = _require_directory(bundle, "Postgres client bundle")
    manifest_path = root / "manifest.json"
    try:
        metadata = manifest_path.lstat()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PostgresSandboxError("Postgres client manifest is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o222:
        raise PostgresSandboxError("Postgres client manifest is writable or unsafe")
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schemaVersion", "profile", "image", "clientVersion", "files"}
        or manifest["schemaVersion"] != 1
        or manifest["profile"] != profile.get("name")
        or manifest["image"] != profile.get("image")
        or manifest["clientVersion"] != profile.get("clientVersion")
        or not isinstance(manifest["files"], list)
    ):
        raise PostgresSandboxError("Postgres client manifest does not match the reviewed profile")
    actual = _client_bundle_files(root)
    expected = manifest["files"]
    if expected != actual:
        raise PostgresSandboxError("Postgres client bundle differs from its trusted manifest")
    for name in ("bin/psql", "bin/pg_dump"):
        if not (root / name).stat().st_mode & 0o111:
            raise PostgresSandboxError(f"Postgres client bundle {name} is not executable")


def _copy_from_container(container_id: str, source: str, destination: Path) -> None:
    _run_docker(["docker", "cp", f"{container_id}:{source}", str(destination)])


def _require_client_version(output: str, executable: str, expected: str) -> None:
    if not re.fullmatch(
        rf"{re.escape(executable)} \(PostgreSQL\) {re.escape(expected)}(?: \([^\r\n]*\))?",
        output,
    ):
        raise PostgresSandboxError(f"pinned Postgres {executable} client version mismatch")


def _run_staged_client(loader: Path, library_directory: Path, executable: Path) -> str:
    try:
        result = subprocess.run(
            [
                str(loader),
                "--library-path",
                str(library_directory),
                str(executable),
                "--version",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PostgresSandboxError("staged Postgres client could not be executed") from exc
    if result.returncode != 0:
        raise PostgresSandboxError("staged Postgres client could not be executed")
    return result.stdout.strip()


def stage_client_bundle(
    container_id: str, *, profile: dict[str, Any], bundle: str | Path
) -> None:
    """Copy only the pinned image's two clients and their runtime libraries."""
    if not re.fullmatch(r"[a-f0-9]{64}", container_id):
        raise PostgresSandboxError("invalid Postgres container identity")
    bundle_path = _require_directory(bundle, "Postgres client bundle staging directory")
    if any(bundle_path.iterdir()):
        raise PostgresSandboxError("Postgres client bundle staging directory is not empty")
    image = _run_docker(["docker", "inspect", "--format={{.Config.Image}}", container_id])
    if image != profile.get("image"):
        raise PostgresSandboxError("Postgres server container image differs from the reviewed digest")

    binary_sources = {
        name: f"/usr/lib/postgresql/17/bin/{name}" for name in ("psql", "pg_dump")
    }
    for name, source in binary_sources.items():
        output = _run_docker(["docker", "exec", container_id, source, "--version"])
        _require_client_version(output, name, profile["clientVersion"])

    dependencies: dict[str, str] = {}
    for source in binary_sources.values():
        ldd_output = _run_docker(["docker", "exec", container_id, "ldd", source])
        for soname, path in parse_ldd_dependencies(ldd_output).items():
            existing = dependencies.get(soname)
            if existing is not None and existing != path:
                raise PostgresSandboxError("Postgres clients require conflicting library paths")
            dependencies[soname] = path

    library_directory = bundle_path / "lib"
    binary_directory = bundle_path / "bin"
    library_directory.mkdir()
    binary_directory.mkdir()
    for name, source in binary_sources.items():
        _copy_from_container(container_id, source, library_directory / f"{name}.elf")

    loader_name = "ld-linux-x86-64.so.2"
    if loader_name not in dependencies:
        raise PostgresSandboxError("pinned Postgres clients use an unsupported ELF loader")
    for soname, path in sorted(dependencies.items()):
        resolved = _run_docker(["docker", "exec", container_id, "readlink", "-f", path])
        resolved_path = Path(resolved)
        if (
            not resolved_path.is_absolute()
            or ".." in resolved_path.parts
            or not (resolved_path.is_relative_to("/lib") or resolved_path.is_relative_to("/usr/lib"))
        ):
            raise PostgresSandboxError("Postgres client dependency escaped system library roots")
        _copy_from_container(container_id, str(resolved_path), library_directory / soname)

    loader = library_directory / loader_name
    for name in dependencies:
        target = library_directory / name
        target.chmod(0o555 if name.startswith("ld-linux") else 0o444)
    for name in binary_sources:
        (library_directory / f"{name}.elf").chmod(0o444)

    for name in binary_sources:
        wrapper = binary_directory / name
        wrapper.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            f"exec {CLIENT_SANDBOX_PATH}/lib/{loader_name} "
            f"--library-path {CLIENT_SANDBOX_PATH}/lib "
            f"{CLIENT_SANDBOX_PATH}/lib/{name}.elf \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o555)
        output = _run_staged_client(loader, library_directory, library_directory / f"{name}.elf")
        _require_client_version(output, name, profile["clientVersion"])

    write_client_manifest(bundle_path, profile)
    verify_client_bundle(bundle_path, profile)


def _write_state(path: Path, state: dict[str, Any], runner_temp: Path) -> None:
    validate_profile_state(state, runner_temp)
    if path.parent != runner_temp or path.is_symlink():
        raise PostgresSandboxError("Postgres sandbox state path escaped runner temp")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def read_profile_state(
    state_file: str | Path,
    runner_temp: str | Path,
    *,
    allow_missing_root: bool = False,
) -> dict[str, Any]:
    path = Path(state_file)
    if path.is_symlink() or not path.is_absolute() or path.parent != Path(runner_temp).resolve():
        raise PostgresSandboxError("Postgres sandbox state path is unsafe")
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 64_000:
            raise PostgresSandboxError("Postgres sandbox state file is unsafe")
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PostgresSandboxError("Postgres sandbox state file is unavailable") from exc
    return validate_profile_state(
        state, runner_temp, allow_missing_root=allow_missing_root
    )


def _run_docker(
    command: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 30,
    environment: dict[str, str] | None = None,
) -> str:
    try:
        result = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PostgresSandboxError("trusted Postgres provisioner command failed") from exc
    if result.returncode != 0:
        raise PostgresSandboxError("trusted Postgres provisioner command failed")
    return result.stdout.strip()


def _wait_for_postgres(container_id: str, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    previous_start_time: str | None = None
    stable_samples = 0
    initialization_complete = False
    while time.monotonic() < deadline:
        try:
            if not initialization_complete:
                startup_log = _run_docker(
                    ["docker", "logs", "--tail=100", container_id], timeout=5
                )
                initialization_complete = (
                    POSTGRES_INITIALIZATION_COMPLETE_MARKER in startup_log
                )
                if not initialization_complete:
                    time.sleep(0.5)
                    continue
            start_time = _run_docker(
                [
                    "docker",
                    "exec",
                    container_id,
                    "/usr/lib/postgresql/17/bin/psql",
                    "-X",
                    "-A",
                    "-t",
                    "--username=postgres",
                    "--dbname=postgres",
                    "--command=SELECT pg_postmaster_start_time()::text;",
                ],
                timeout=5,
            )
            if not start_time:
                raise PostgresSandboxError("Postgres postmaster start time is empty")
            if start_time == previous_start_time:
                stable_samples += 1
            else:
                previous_start_time = start_time
                stable_samples = 1
            if stable_samples >= 3:
                return
        except PostgresSandboxError:
            previous_start_time = None
            stable_samples = 0
        time.sleep(0.5)
    raise PostgresSandboxError("pinned Postgres server did not become ready before timeout")


def _assert_container_boundary(container_id: str, profile: dict[str, Any]) -> None:
    image = _run_docker(["docker", "inspect", "--format={{.Config.Image}}", container_id])
    network = _run_docker(["docker", "inspect", "--format={{.HostConfig.NetworkMode}}", container_id])
    ports = _run_docker(["docker", "inspect", "--format={{json .HostConfig.PortBindings}}", container_id])
    if image != profile["image"] or network != "none" or ports not in {"null", "{}"}:
        raise PostgresSandboxError("Postgres container escaped its network or port boundary")
    running = _run_docker(["docker", "inspect", "--format={{.State.Running}}", container_id])
    if running != "true":
        raise PostgresSandboxError("Postgres sandbox server is not running")


def _assert_postgres_privileges(
    container_id: str, *, profile_name: str = PROFILE_NAME
) -> None:
    psql = [
        "docker",
        "exec",
        container_id,
        "/usr/lib/postgresql/17/bin/psql",
        "-X",
        "-A",
        "-t",
        "-v",
        "ON_ERROR_STOP=1",
        "--username=postgres",
    ]
    roles = _run_docker(
        [
            *psql,
            "--dbname=postgres",
            "--command=SELECT rolsuper || ':' || rolcreatedb || ':' || rolcreaterole || ':' || rolinherit FROM pg_roles WHERE rolname='verjson_ci';",
        ]
    )
    if roles != "false:false:false:false":
        raise PostgresSandboxError("Postgres candidate role has administrative privileges")

    if profile_name not in PROFILE_NAMES:
        raise PostgresSandboxError(f"unknown Postgres sandbox profile: {profile_name!r}")
    if profile_name == PROFILE_NAME:
        socket_only = _run_docker(
            [
                *psql,
                "--dbname=app_test",
                "--command=SELECT inet_server_addr() IS NULL;",
            ]
        )
        if socket_only != "t":
            raise PostgresSandboxError("Postgres server accepted a non-Unix-socket connection")
        schema_access = _run_docker(
            [
                *psql,
                "--dbname=app_test",
                "--command=SELECT has_schema_privilege('verjson_ci', 'public', 'USAGE') AND has_schema_privilege('verjson_ci', 'public', 'CREATE');",
            ]
        )
        if schema_access != "t":
            raise PostgresSandboxError("Postgres candidate role lacks required schema privileges")
        return

    databases = {
        "schema_snapshot": False,
        "schema_migrations": True,
        "schema_reference": False,
    }

    for database, expects_temp in databases.items():
        socket_only = _run_docker(
            [
                *psql,
                f"--dbname={database}",
                "--command=SELECT inet_server_addr() IS NULL;",
            ]
        )
        if socket_only != "t":
            raise PostgresSandboxError("Postgres server accepted a non-Unix-socket connection")
        database_privileges = _run_docker(
            [
                *psql,
                "--dbname=postgres",
                "--command="
                f"SELECT has_database_privilege('verjson_ci', '{database}', 'CONNECT') || ':' || "
                f"has_database_privilege('verjson_ci', '{database}', 'TEMP') || ':' || "
                f"has_database_privilege('verjson_ci', '{database}', 'CREATE');",
            ]
        )
        if database_privileges != f"true:{str(expects_temp).lower()}:false":
            raise PostgresSandboxError(
                "Postgres candidate database privileges are outside the profile"
            )
        schema_access = _run_docker(
            [
                *psql,
                f"--dbname={database}",
                "--command=SELECT has_schema_privilege('verjson_ci', 'public', 'USAGE') AND has_schema_privilege('verjson_ci', 'public', 'CREATE');",
            ]
        )
        if schema_access != "t":
            raise PostgresSandboxError("Postgres candidate role lacks required schema privileges")

    if profile_name == SCHEMA_VALIDATION_PROFILE_NAME:
        prelude = _run_docker(
            [
                *psql,
                "--dbname=schema_migrations",
                "--command=SELECT "
                "(SELECT NOT rolcanlogin FROM pg_roles WHERE rolname='authenticated') AND "
                "(SELECT count(*) = 2 FROM pg_namespace WHERE nspname IN ('auth', 'storage') AND pg_get_userbyid(nspowner)='verjson_ci') AND "
                "(SELECT count(*) = 3 FROM pg_class WHERE relname IN ('users', 'identities', 'objects') AND pg_get_userbyid(relowner)='verjson_ci') AND "
                "to_regprocedure('auth.uid()') IS NOT NULL AND "
                "to_regprocedure('storage.foldername(text)') IS NOT NULL;",
            ]
        )
        if prelude != "t":
            raise PostgresSandboxError("Postgres schema migration prelude is incomplete or misowned")


def provision_profile(
    profile_name: str,
    *,
    runner_temp: str | Path,
    state_file: str | Path,
) -> dict[str, Any]:
    """Start one isolated Unix-socket-only database and write its cleanup receipt."""
    profile = validate_profile_request(profile_name)
    if profile is None:
        raise PostgresSandboxError("Postgres sandbox profile must be explicitly selected")
    if os.name != "posix" or os.uname().machine != "x86_64":
        raise PostgresSandboxError("Postgres sandbox profile requires linux/amd64")
    runner = Path(runner_temp).resolve(strict=True)
    state_path = Path(state_file)
    if not runner.is_dir() or state_path.parent != runner or state_path.exists() or state_path.is_symlink():
        raise PostgresSandboxError("Postgres sandbox state must be new under runner temp")

    root = Path(tempfile.mkdtemp(prefix=PROFILE_ROOT_PREFIX, dir=runner))
    socket_dir = root / "socket"
    client_bundle = root / "client"
    try:
        socket_dir.mkdir(mode=0o777)
        os.chmod(socket_dir, 0o777)
        client_bundle.mkdir(mode=0o700)
    except BaseException as failure:
        _cleanup_failed_staging(root, state_path, failure, remove_state=False)
        raise
    container_name = f"verjson-postgres-{secrets.token_hex(6)}"
    state: dict[str, Any] = {
        "schemaVersion": 1,
        "profileName": profile_name,
        "containerName": container_name,
        "containerId": None,
        "rootDirectory": str(root),
        "socketDirectory": str(socket_dir),
        "clientBundleDirectory": str(client_bundle),
        "socketDevice": None,
        "socketInode": None,
        "socketMode": None,
        "status": "starting",
    }
    try:
        _write_state(state_path, state, runner)
    except BaseException as failure:
        _cleanup_failed_staging(root, state_path, failure, remove_state=True)
        raise
    try:
        write_local_auth_policy(socket_dir, profile_name=profile_name)
        admin_password = secrets.token_urlsafe(32)
        container_id = _run_docker(
            build_container_args(
                profile,
                container_name=container_name,
                socket_dir=socket_dir,
            ),
            timeout=120,
            environment=container_environment(admin_password),
        )
        if not re.fullmatch(r"[a-f0-9]{64}", container_id):
            raise PostgresSandboxError("Docker returned an invalid Postgres container identity")
        state["containerId"] = container_id
        _write_state(state_path, state, runner)
        _wait_for_postgres(container_id)
        _assert_container_boundary(container_id, profile)
        _run_docker(
            [
                "docker",
                "exec",
                "--interactive",
                container_id,
                "/usr/lib/postgresql/17/bin/psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "--username=postgres",
                "--dbname=postgres",
            ],
            input_text=database_bootstrap_sql(profile_name=profile_name),
            timeout=30,
        )
        _assert_postgres_privileges(container_id, profile_name=profile_name)
        socket_file = socket_dir / POSTGRES_SOCKET_NAME
        metadata = socket_file.lstat()
        if not stat.S_ISSOCK(metadata.st_mode):
            raise PostgresSandboxError("Postgres did not create its expected Unix socket")
        state["socketDevice"] = metadata.st_dev
        state["socketInode"] = metadata.st_ino
        state["socketMode"] = metadata.st_mode & 0o777
        state["status"] = "ready"
        _write_state(state_path, state, runner)
        return state
    except BaseException:
        try:
            cleanup_profile(state_path, runner_temp=runner)
        except BaseException:
            pass
        raise


def _remove_container(container_name: str) -> None:
    try:
        result = subprocess.run(
            ["docker", "rm", "--force", container_name],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PostgresSandboxError("Postgres container cleanup failed") from exc
    if result.returncode == 0:
        return
    message = result.stderr.lower()
    if "no such container" in message or "no such object" in message:
        return
    raise PostgresSandboxError("Postgres container cleanup failed")


def _make_tree_removable(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        try:
            metadata = path.lstat()
        except OSError:
            continue
        if stat.S_ISDIR(metadata.st_mode) and not metadata.st_mode & 0o222:
            path.chmod(0o700)


def cleanup_profile(state_file: str | Path, *, runner_temp: str | Path) -> None:
    path = Path(state_file)
    if not path.exists() and not path.is_symlink():
        return
    state = read_profile_state(path, runner_temp, allow_missing_root=True)
    _remove_container(state["containerName"])
    root = Path(state["rootDirectory"])
    if root.exists():
        _make_tree_removable(root)
        shutil.rmtree(root, ignore_errors=False)
    path.unlink(missing_ok=True)


def prepare_profile_for_candidate(
    state_file: str | Path, *, runner_temp: str | Path
) -> dict[str, Any]:
    state = read_profile_state(state_file, runner_temp)
    if state["status"] != "ready":
        raise PostgresSandboxError("Postgres sandbox is not ready for candidate execution")
    profile = resolve_profile(state["profileName"])
    _assert_container_boundary(state["containerId"], profile)
    verify_profile_socket(state)
    _assert_postgres_privileges(
        state["containerId"], profile_name=state["profileName"]
    )
    stage_client_bundle(
        state["containerId"],
        profile=profile,
        bundle=state["clientBundleDirectory"],
    )
    verify_client_bundle(state["clientBundleDirectory"], profile)
    verify_profile_socket(state)
    return state


def verify_profile_runtime(state_file: str | Path, *, runner_temp: str | Path) -> None:
    state = read_profile_state(state_file, runner_temp)
    if state["status"] != "ready":
        raise PostgresSandboxError("Postgres sandbox left the ready state")
    profile = resolve_profile(state["profileName"])
    _assert_container_boundary(state["containerId"], profile)
    verify_profile_socket(state)
    verify_client_bundle(state["clientBundleDirectory"], profile)


def profile_mount_args(state: dict[str, Any]) -> list[str]:
    return build_bubblewrap_profile_args(
        socket_dir=state["socketDirectory"],
        client_bundle=state["clientBundleDirectory"],
        profile_name=state["profileName"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--profile", default="")
    validate.add_argument("--db-image", default="")
    validate.add_argument("--cache-image", default="")
    validate.add_argument("--artifact-role", default="")
    validate.add_argument("--compatibility-ranges", default="")
    validate.add_argument("--secretless-mode", choices=("true", "false"), default="false")
    provision = commands.add_parser("provision")
    provision.add_argument("--profile", required=True)
    provision.add_argument("--runner-temp", required=True)
    provision.add_argument("--state-file", required=True)
    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("--runner-temp", required=True)
    cleanup.add_argument("--state-file", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            validate_profile_request(
                args.profile,
                db_image=args.db_image,
                cache_image=args.cache_image,
                artifact_role=args.artifact_role,
                compatibility_ranges=args.compatibility_ranges,
                secretless_mode=args.secretless_mode == "true",
            )
        elif args.command == "provision":
            provision_profile(
                args.profile,
                runner_temp=args.runner_temp,
                state_file=args.state_file,
            )
        else:
            cleanup_profile(args.state_file, runner_temp=args.runner_temp)
    except PostgresSandboxError as exc:
        print(f"postgres sandbox: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
