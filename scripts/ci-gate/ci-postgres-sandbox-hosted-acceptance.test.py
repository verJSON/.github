#!/usr/bin/env python3
"""Exercise the reviewed Postgres boundary on an isolated GitHub-hosted runner."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

import ci_build_artifacts
import ci_postgres_sandbox


def _checked_output(command: list[str], *, timeout: float = 30) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": os.environ.get("RUNNER_TEMP", "/tmp")},
    )
    if result.returncode != 0:
        raise AssertionError(
            f"trusted acceptance command failed ({result.returncode}): {result.stderr[-2000:]}"
        )
    return result.stdout.strip()


class PostgresSandboxHostedAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get("VERJSON_CI_POSTGRES_HOSTED_ACCEPTANCE") != "1":
            raise unittest.SkipTest("requires the explicit Postgres hosted acceptance step")
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise unittest.SkipTest("requires GitHub Actions")
        if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
            raise AssertionError("Postgres sandbox acceptance requires a GitHub-hosted runner")

        cls.runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
        cls.primary_state_file = cls.runner_temp / "ci-postgres-sandbox-acceptance-state.json"
        cls.secondary_state_file = cls.runner_temp / "ci-postgres-sandbox-fresh-state.json"
        cls.primary = None
        cls.secondary = None
        cls.npm_global_config = cls.runner_temp / "ci-postgres-sandbox-npm-globalconfig"
        cls.candidate_state_file = None
        cls.candidate_state_snapshot = None
        candidate_state_path = os.environ.get("VERJSON_CI_POSTGRES_CANDIDATE_STATE")
        if candidate_state_path:
            cls.candidate_state_file = Path(candidate_state_path)
            candidate_state = ci_postgres_sandbox.read_profile_state(
                cls.candidate_state_file,
                cls.runner_temp,
            )
            if candidate_state["status"] != "ready":
                raise AssertionError("production Postgres candidate profile is not ready")
            candidate_bundle = Path(candidate_state["clientBundleDirectory"])
            if any(candidate_bundle.iterdir()):
                raise AssertionError("production Postgres profile was staged before acceptance")
            cls.candidate_state_snapshot = cls.candidate_state_file.read_bytes()
        try:
            if cls.primary_state_file.exists() or cls.primary_state_file.is_symlink():
                raise AssertionError("disposable Postgres acceptance state path already exists")
            cls.primary = ci_postgres_sandbox.provision_profile(
                ci_postgres_sandbox.PROFILE_NAME,
                runner_temp=cls.runner_temp,
                state_file=cls.primary_state_file,
            )
            cls.primary = ci_postgres_sandbox.prepare_profile_for_candidate(
                cls.primary_state_file,
                runner_temp=cls.runner_temp,
            )
            cls.profile = ci_postgres_sandbox.resolve_profile(cls.primary["profileName"])
            cls.bwrap = shutil.which("bwrap")
            cls.node = shutil.which("node")
            if cls.bwrap is None or cls.node is None:
                raise AssertionError("hosted acceptance requires Bubblewrap and the configured Node runtime")

            downloaded_fixture = SCRIPT_DIR / "postgres-sandbox-drizzle"
            checkout_fixture = SCRIPT_DIR / "fixtures/postgres-sandbox-drizzle"
            cls.drizzle_dir = (
                downloaded_fixture if downloaded_fixture.is_dir() else checkout_fixture
            )
            query = cls.drizzle_dir / "query.mjs"
            package_json = cls.drizzle_dir / "package.json"
            package_lock = cls.drizzle_dir / "package-lock.json"
            if (
                not query.is_file()
                or not (cls.drizzle_dir / "schema.ts").is_file()
                or not (cls.drizzle_dir / "migration.sql").is_file()
                or not package_json.is_file()
                or not package_lock.is_file()
            ):
                raise AssertionError("pinned Drizzle acceptance fixture is incomplete")
            if not (
                (cls.drizzle_dir / "node_modules/drizzle-orm").is_dir()
                and (cls.drizzle_dir / "node_modules/drizzle-kit").is_dir()
            ):
                with cls.npm_global_config.open("x", encoding="utf-8"):
                    pass
                cls.npm_global_config.chmod(0o600)
                _checked_output(
                    [
                        "npm",
                        "ci",
                        "--prefix",
                        str(cls.drizzle_dir),
                        "--registry=https://registry.npmjs.org",
                        "--userconfig=/dev/null",
                    f"--globalconfig={cls.npm_global_config}",
                        "--ignore-scripts",
                        "--no-audit",
                        "--no-fund",
                    ],
                    timeout=120,
                )
            cls.query = query
            cls.node_root = Path(cls.node).resolve().parent.parent

            if cls.secondary_state_file.exists() or cls.secondary_state_file.is_symlink():
                raise AssertionError("secondary Postgres state path already exists")
            cls.secondary = ci_postgres_sandbox.provision_profile(
                cls.primary["profileName"],
                runner_temp=cls.runner_temp,
                state_file=cls.secondary_state_file,
            )
            cls.secondary = ci_postgres_sandbox.prepare_profile_for_candidate(
                cls.secondary_state_file,
                runner_temp=cls.runner_temp,
            )
        except BaseException:
            ci_postgres_sandbox.cleanup_profile(
                cls.secondary_state_file,
                runner_temp=cls.runner_temp,
            )
            ci_postgres_sandbox.cleanup_profile(
                cls.primary_state_file,
                runner_temp=cls.runner_temp,
            )
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        secondary = getattr(cls, "secondary", None)
        ci_postgres_sandbox.cleanup_profile(
            cls.secondary_state_file,
            runner_temp=cls.runner_temp,
        )
        if secondary is not None:
            inspect = subprocess.run(
                ["docker", "inspect", secondary["containerName"]],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            )
            if inspect.returncode == 0:
                raise AssertionError("secondary Postgres container survived cleanup")
        if cls.secondary_state_file.exists():
            raise AssertionError("secondary Postgres cleanup receipt survived cleanup")
        primary = getattr(cls, "primary", None)
        ci_postgres_sandbox.cleanup_profile(
            cls.primary_state_file,
            runner_temp=cls.runner_temp,
        )
        cls.npm_global_config.unlink(missing_ok=True)
        if primary is not None:
            inspect = subprocess.run(
                ["docker", "inspect", primary["containerName"]],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            )
            if inspect.returncode == 0:
                raise AssertionError("primary Postgres container survived cleanup")
        if cls.primary_state_file.exists():
            raise AssertionError("primary Postgres cleanup receipt survived cleanup")
        if cls.candidate_state_file is not None:
            if not cls.candidate_state_file.is_file() or cls.candidate_state_file.is_symlink():
                raise AssertionError("production Postgres candidate receipt disappeared or was replaced")
            if cls.candidate_state_file.read_bytes() != cls.candidate_state_snapshot:
                raise AssertionError("hosted acceptance changed the production candidate receipt")
            candidate_state = ci_postgres_sandbox.read_profile_state(
                cls.candidate_state_file,
                cls.runner_temp,
            )
            candidate_bundle = Path(candidate_state["clientBundleDirectory"])
            if any(candidate_bundle.iterdir()):
                raise AssertionError("hosted acceptance staged the production candidate profile")

    def _bubblewrap_command(
        self,
        state: dict[str, object],
        command: list[str],
        *,
        extra_mounts: tuple[str, ...] = (),
    ) -> list[str]:
        state_file = (
            self.primary_state_file
            if state["containerName"] == self.primary["containerName"]
            else self.secondary_state_file
        )
        ci_postgres_sandbox.verify_profile_runtime(
            state_file,
            runner_temp=self.runner_temp,
        )
        environment = ci_postgres_sandbox.profile_environment(
            "/opt/verjson/node/bin:/opt/verjson/postgres-client/bin:/usr/bin:/bin"
        )
        arguments = [
            self.bwrap,
            "--unshare-user",
            "--unshare-pid",
            "--as-pid-1",
            "--unshare-net",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
            "--disable-userns",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--tmpfs",
            "/",
            "--tmpfs",
            "/tmp",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/lib64",
            "/lib64",
            "--ro-bind",
            "/etc",
            "/etc",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/dev/shm",
            *ci_postgres_sandbox.profile_mount_args(state),
            "--dir",
            "/opt/verjson/node",
            "--ro-bind",
            str(self.node_root),
            "/opt/verjson/node",
            "--dir",
            "/opt/verjson/drizzle",
            "--ro-bind",
            str(self.drizzle_dir),
            "/opt/verjson/drizzle",
            *extra_mounts,
        ]
        for name, value in environment.items():
            arguments.extend(("--setenv", name, value))
        arguments.extend(
            (
                "--setenv",
                "HOME",
                "/tmp",
                "--setenv",
                "PATH",
                "/opt/verjson/node/bin:/opt/verjson/postgres-client/bin:/usr/bin:/bin",
                "--",
                *command,
            )
        )
        return arguments

    def _candidate(
        self,
        state: dict[str, object],
        command: list[str],
        *,
        success: bool = True,
        timeout: float = 30,
        extra_mounts: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            self._bubblewrap_command(state, command, extra_mounts=extra_mounts),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.runner_temp)},
        )
        if success and result.returncode != 0:
            self.fail(f"sandbox command failed: {result.stderr[-2000:]}")
        if not success and result.returncode == 0:
            self.fail("sandbox command unexpectedly succeeded")
        return result

    def _assert_supervisor_stops_candidate(self, cause: str) -> None:
        self.assertIn(cause, {"timeout", "host-cancellation"})
        candidate_started_at: float | None = None
        host_signals = ci_build_artifacts.HostSignalQueue()

        def test_hook(event: str) -> None:
            nonlocal candidate_started_at
            if event != "candidate-started":
                return
            candidate_started_at = time.monotonic()
            if cause == "host-cancellation":
                host_signals.capture(signal.SIGTERM, None)

        def timeout_signal() -> int | None:
            if (
                cause == "timeout"
                and candidate_started_at is not None
                and time.monotonic() - candidate_started_at >= 0.25
            ):
                return signal.SIGTERM
            return None

        supervisor_signals = (
            {"host_signals": host_signals}
            if cause == "host-cancellation"
            else {"cancellation_signal": timeout_signal}
        )
        with self.assertRaises(ci_build_artifacts.ScriptPlanInterrupted) as interrupted:
            ci_build_artifacts.run_supervised_bubblewrap(
                self._bubblewrap_command(
                    self.secondary,
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-c",
                        ci_build_artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT,
                        "/usr/bin/sleep",
                        "30",
                    ],
                ),
                environment={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": str(self.runner_temp),
                },
                pass_fds=(),
                test_hook=test_hook,
                **supervisor_signals,
            )
        self.assertTrue(interrupted.exception.candidate_started)
        self.assertEqual(interrupted.exception.signum, signal.SIGTERM)
        self.assertEqual(interrupted.exception.cleanup_status, "verified")

    def _psql(self, state: dict[str, object], sql: str, *, success: bool = True) -> str:
        result = self._candidate(
            state,
            [
                "/opt/verjson/postgres-client/bin/psql",
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "--command",
                sql,
            ],
            success=success,
        )
        return result.stdout.strip()

    def _push_drizzle_schema(self, state: dict[str, object]) -> None:
        config_path = self.runner_temp / f"drizzle-config-{state['containerName']}.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema": "/opt/verjson/drizzle/schema.ts",
                    "out": "/tmp/ci-postgres-sandbox-drizzle-out",
                    "dialect": "postgresql",
                    "dbCredentials": {
                        "url": ci_postgres_sandbox.profile_environment()["DATABASE_URL"]
                    },
                    "strict": False,
                    "verbose": False,
                }
            ),
            encoding="utf-8",
        )
        try:
            self._candidate(
                state,
                [
                    "/opt/verjson/node/bin/node",
                    "/opt/verjson/drizzle/node_modules/drizzle-kit/bin.cjs",
                    "push",
                    "--config=/tmp/ci-postgres-sandbox-drizzle.json",
                    "--force",
                ],
                timeout=60,
                extra_mounts=(
                    "--ro-bind",
                    str(config_path),
                    "/tmp/ci-postgres-sandbox-drizzle.json",
                ),
            )
        finally:
            config_path.unlink(missing_ok=True)

    def _apply_routines(self, state: dict[str, object]) -> None:
        migration = (self.drizzle_dir / "migration.sql").read_text(encoding="utf-8")
        routines = re.findall(
            r"(?ms)^-- BEGIN schema-routines: ([^\s]+)\s*\n(.*?)^-- END schema-routines: \1\s*$",
            migration,
        )
        if not routines:
            self.fail("schema migration fixture has no marked routine block")
        routine_sql = "\n\n".join(body.strip() for _name, body in routines)
        routines_path = self.runner_temp / f"postgres-routines-{state['containerName']}.sql"
        routines_path.write_text(routine_sql + "\n", encoding="utf-8")
        try:
            self._candidate(
                state,
                [
                    "/opt/verjson/postgres-client/bin/psql",
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "--file",
                    "/tmp/ci-postgres-sandbox-routines.sql",
                ],
                extra_mounts=(
                    "--ro-bind",
                    str(routines_path),
                    "/tmp/ci-postgres-sandbox-routines.sql",
                ),
            )
        finally:
            routines_path.unlink(missing_ok=True)

    def _apply_migration(self, state: dict[str, object]) -> None:
        self._candidate(
            state,
            [
                "/opt/verjson/postgres-client/bin/psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "--file",
                "/opt/verjson/drizzle/migration.sql",
            ],
        )

    def _normalized_schema_dump(self, state: dict[str, object]) -> str:
        result = self._candidate(
            state,
            [
                "/opt/verjson/postgres-client/bin/pg_dump",
                "--schema-only",
                "--no-owner",
                "--no-privileges",
                "--no-comments",
                "--schema=public",
                "app_test",
            ],
        )
        lines = [
            line
            for line in result.stdout.splitlines()
            if not re.match(r"^\\(un)?restrict\s", line)
            and not re.match(r"^-- Dumped (from database|by pg_dump) version ", line)
            and line != "CREATE SCHEMA public;"
        ]
        return "\n".join(lines) + "\n"

    def test_real_bubblewrap_postgres_boundary_and_failure_receipts(self) -> None:
        self._push_drizzle_schema(self.primary)
        self._apply_routines(self.primary)
        self._apply_migration(self.secondary)
        reference_dump = self._normalized_schema_dump(self.primary)
        migration_dump = self._normalized_schema_dump(self.secondary)
        self.assertEqual(reference_dump, migration_dump)

        for state in (self.primary, self.secondary):
            versions = self._psql(
                state,
                "SELECT current_user || '|' || current_setting('server_version') || '|' || (inet_server_addr() IS NULL)::text;",
            )
            version_fields = versions.split("|")
            self.assertEqual(version_fields[0], "verjson_ci")
            self.assertTrue(version_fields[1].startswith("17.11"), versions)
            self.assertEqual(version_fields[2], "true")
            for role, expected_error in (
                ("postgres", "Peer authentication failed"),
                ("ci_unreviewed", "pg_hba.conf rejects connection"),
            ):
                with self.subTest(role=role):
                    denied = self._candidate(
                        state,
                        [
                            "/opt/verjson/postgres-client/bin/psql",
                            "-X",
                            "--username=" + role,
                            "--dbname=app_test",
                            "--command=SELECT 1",
                        ],
                        success=False,
                    )
                    self.assertIn(expected_error, denied.stderr)
            dump_version = self._candidate(
                state,
                ["/opt/verjson/postgres-client/bin/pg_dump", "--version"],
            )
            self.assertIn("17.11", dump_version.stdout)

            privileges = self._psql(
                state,
                "SELECT rolsuper::text || '|' || rolcreatedb::text || '|' || rolcreaterole::text || '|' || has_schema_privilege(current_user, 'public', 'CREATE')::text FROM pg_roles WHERE rolname = current_user;",
            )
            self.assertEqual(privileges, "false|false|false|true")
            for forbidden_sql in (
                "CREATE ROLE d2_forbidden;",
                "CREATE DATABASE d2_forbidden;",
                "CREATE EXTENSION dblink;",
                "COPY (SELECT 1) TO PROGRAM 'true';",
            ):
                with self.subTest(statement=forbidden_sql):
                    self._psql(state, forbidden_sql, success=False)

        first = self._candidate(
            self.primary,
            ["/opt/verjson/node/bin/node", "/opt/verjson/drizzle/query.mjs"],
        )
        second = self._candidate(
            self.secondary,
            ["/opt/verjson/node/bin/node", "/opt/verjson/drizzle/query.mjs"],
        )
        self.assertIn('"fresh_database":true', first.stdout)
        self.assertIn('"fresh_database":true', second.stdout)

        network_probe = r"""
import os
import socket
import subprocess
from pathlib import Path

def must_be_blocked(label, operation):
    try:
        operation()
    except OSError:
        return
    raise SystemExit(f"{label} unexpectedly succeeded")

dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
must_be_blocked("DNS egress", lambda: dns.sendto(b"verjson-probe", ("8.8.8.8", 53)))
for label, destination in (
    ("TCP egress", ("1.1.1.1", 443)),
    ("metadata access", ("169.254.169.254", 80)),
):
    must_be_blocked(label, lambda: socket.create_connection(destination, timeout=2))

socket_path = Path("/run/verjson-postgres/.s.PGSQL.5432")
for label, mutation in (
    ("socket permission mutation", lambda: os.chmod(socket_path, 0o600)),
    ("socket replacement", lambda: socket_path.unlink()),
):
    must_be_blocked(label, mutation)
must_be_blocked(
    "socket directory mutation",
    lambda: (socket_path.parent / "candidate-write").write_text("forbidden"),
)

docker_socket = Path("/var/run/docker.sock")
if docker_socket.exists():
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(docker_socket))
    except OSError:
        pass
    else:
        raise SystemExit("Docker socket was reachable")
docker = subprocess.run(["docker", "info"], capture_output=True, timeout=3, check=False)
if docker.returncode == 0:
    raise SystemExit("Docker daemon was reachable")
print("network, metadata, Docker, and read-only socket checks passed")
"""
        network = self._candidate(
            self.primary,
            ["/usr/bin/python3", "-I", "-c", network_probe],
        )
        self.assertIn("checks passed", network.stdout)

        container_id = str(self.primary["containerId"])
        server_egress = subprocess.run(
            [
                "docker",
                "exec",
                container_id,
                "/usr/lib/postgresql/17/bin/psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "--username=postgres",
                "--dbname=postgres",
                "--command",
                "COPY (SELECT 1) TO PROGRAM 'bash -c ''echo > /dev/tcp/1.1.1.1/443''';",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
        self.assertNotEqual(server_egress.returncode, 0, server_egress.stdout)

        bad_profile = {**self.profile, "digest": "sha256:" + "0" * 64}
        with tempfile.TemporaryDirectory(dir=self.runner_temp) as directory:
            invalid_config = Path(directory) / "profiles.json"
            document = json.loads(ci_postgres_sandbox.PROFILE_CONFIG.read_text(encoding="utf-8"))
            document["profiles"][ci_postgres_sandbox.PROFILE_NAME]["digest"] = bad_profile["digest"]
            invalid_config.write_text(json.dumps(document), encoding="utf-8")
            with patch.object(ci_postgres_sandbox, "PROFILE_CONFIG", invalid_config):
                with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                    ci_postgres_sandbox.resolve_profile(ci_postgres_sandbox.PROFILE_NAME)
            with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                ci_postgres_sandbox.resolve_profile("unreviewed")

        bad_state_file = self.runner_temp / "ci-postgres-sandbox-invalid-runtime-state.json"
        bad_state = dict(self.primary)
        bad_state["socketMode"] = int(bad_state["socketMode"]) ^ 0o100
        ci_postgres_sandbox._write_state(bad_state_file, bad_state, self.runner_temp)
        entries = [{"directory": ".", "script": "boundary", "unsetEnv": [], "requiresServices": False}]
        identity = {
            "repository": "Verjson/hosted-acceptance",
            "event": "workflow_dispatch",
            "ref": "refs/heads/main",
            "commit": "a" * 40,
            "workflowSha": "b" * 40,
            "runId": os.environ["GITHUB_RUN_ID"],
            "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
            "jobId": "postgres-sandbox-acceptance",
            "levelId": "postgres-17.11",
        }

        def fail_on_invalid_boundary(_ordinal: int, _entry: dict[str, object]):
            ci_postgres_sandbox.verify_profile_runtime(
                bad_state_file,
                runner_temp=self.runner_temp,
            )
            return ci_build_artifacts.ScriptEntryExecutionResult(0)

        with self.assertRaises(ci_build_artifacts.ScriptPlanExecutionError) as failure:
            ci_build_artifacts.run_script_plan_with_receipts(
                entries,
                identity,
                True,
                self.runner_temp,
                fail_on_invalid_boundary,
            )
        receipt_directory = failure.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipt = json.loads((receipt_directory / "entry-000000.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(receipt["state"], "infrastructure-failure")
        bad_state_file.unlink(missing_ok=True)

    def test_production_supervisor_cleans_timeout_and_host_cancellation(self) -> None:
        self._assert_supervisor_stops_candidate("timeout")
        self._assert_supervisor_stops_candidate("host-cancellation")


if __name__ == "__main__":
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("SKIP: hosted Postgres acceptance runs in GitHub Actions")
        raise SystemExit(0)
    unittest.main()
