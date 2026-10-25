#!/usr/bin/env python3
"""Converge the external organization contract for canonical CI adopters."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app_client_id_contract import is_valid_client_id  # noqa: E402


SHA = re.compile(r"[0-9a-f]{40}")
NAME = re.compile(r"[A-Z][A-Z0-9_]*")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MODES = {"check", "dry-run", "apply"}
GENERATORS = {
    "privileged-merge": "gen-privileged-merge-caller.sh",
    "changelog": "gen-changelog-caller.sh",
    "container-candidate": "gen-container-candidate.sh",
    "container-release": "gen-container-release.sh",
    "container-deployment": "gen-container-deployment.sh",
    "renovate-compatibility": "gen-renovate-compatibility-caller.sh",
}
APP_PERMISSIONS = {
    "AI_REVIEW_APP": {"checks": "write", "contents": "read", "pull_requests": "write", "metadata": "read"},
    "MERGE_APP": {"contents": "write", "pull_requests": "write", "metadata": "read"},
    "RELEASE_APP": {"contents": "write", "metadata": "read"},
    "RENOVATE_COMPATIBILITY_APP": {
        "actions": "read", "checks": "read", "contents": "read",
        "pull_requests": "read", "statuses": "read", "metadata": "read",
    },
    "DEPENDENCY_SUPERSESSION_APP": {
        "contents": "read", "pull_requests": "write", "metadata": "read",
    },
}
ENVIRONMENT_ONLY_APP_ROLES = frozenset({"AI_REVIEW_APP", "MERGE_APP", "RELEASE_APP"})
APP_PRIVATE_KEY_CREDENTIALS = {
    role: f"{role}_PRIVATE_KEY" for role in APP_PERMISSIONS
}
APP_PRIVATE_KEY_ROLES = {
    credential: role for role, credential in APP_PRIVATE_KEY_CREDENTIALS.items()
}
ENVIRONMENT_ONLY_APP_PRIVATE_KEYS = frozenset(
    APP_PRIVATE_KEY_CREDENTIALS[role] for role in ENVIRONMENT_ONLY_APP_ROLES
)
ORGANIZATION_CREDENTIALS = frozenset({"NODE_AUTH_TOKEN"})
PROVISIONING_PATH = "docs/app-key-environment-rollout.md"


class BootstrapError(RuntimeError):
    pass


class ProvisioningRequiredError(BootstrapError):
    pass


class ConvergenceError(BootstrapError):
    def __init__(self, message: str, receipt: dict[str, Any]) -> None:
        super().__init__(message)
        self.receipt = receipt


def object_value(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BootstrapError(f"{label} must be an object")
    return value


def list_value(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise BootstrapError(f"{label} must be an array")
    return value


def exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise BootstrapError(f"{label} keys must be exactly {sorted(expected)}")


def safe_relative(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise BootstrapError(f"{label} must be a normalized relative path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BootstrapError(f"{label} must be a normalized relative path")
    return path


def reject_environment_only_app_keys(secrets: dict[str, Any]) -> None:
    rejected = sorted(set(secrets).intersection(ENVIRONMENT_ONLY_APP_PRIVATE_KEYS))
    if rejected:
        names = ", ".join(rejected)
        raise ProvisioningRequiredError(
            "environment-only App-key provisioning is required; the legacy "
            f"organization-secret bootstrap cannot handle {names}"
        )


class GitHub:
    def __init__(self, executable: str) -> None:
        self.executable = executable

    def run(self, arguments: list[str], *, stdin: str | None = None, sensitive: bool = False) -> str:
        result = subprocess.run(
            [self.executable, *arguments], input=stdin, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if result.returncode:
            message = "secret upload failed" if sensitive else (
                result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "GitHub command failed"
            )
            raise BootstrapError(message)
        return result.stdout

    def json(self, endpoint: str) -> dict[str, Any]:
        try:
            return object_value(json.loads(self.run(["api", endpoint])), endpoint)
        except json.JSONDecodeError as error:
            raise BootstrapError(f"{endpoint} returned malformed JSON") from error


def validate_manifest(raw: Any) -> dict[str, Any]:
    manifest = object_value(raw, "manifest")
    exact_keys(manifest, {"organization", "contract_sha", "variables", "secrets", "apps", "callers"}, "manifest")
    organization = manifest["organization"]
    if not isinstance(organization, str) or SLUG.fullmatch(organization.lower()) is None or organization.lower() != organization:
        raise BootstrapError("organization must be a lowercase GitHub login")
    if not isinstance(manifest["contract_sha"], str) or SHA.fullmatch(manifest["contract_sha"]) is None:
        raise BootstrapError("contract_sha must be an immutable lowercase 40-hex SHA")

    variables = object_value(manifest["variables"], "variables")
    for name, spec_raw in variables.items():
        if NAME.fullmatch(name) is None or not name.startswith("CI_"):
            raise BootstrapError(f"variable {name!r} must use the neutral CI_ contract")
        spec = object_value(spec_raw, f"variable {name}")
        exact_keys(spec, {"value", "visibility"}, f"variable {name}")
        if not isinstance(spec["value"], str) or spec["visibility"] not in {"all", "private"}:
            raise BootstrapError(f"variable {name} has an invalid value or visibility")

    seen_roles: set[str] = set()
    for index, app_raw in enumerate(list_value(manifest["apps"], "apps")):
        app = object_value(app_raw, f"apps[{index}]")
        exact_keys(app, {"role", "slug", "app_id", "client_id", "installation_id", "repository_selection", "permissions", "events"}, f"apps[{index}]")
        role = app["role"]
        if role not in APP_PERMISSIONS or role in seen_roles:
            raise BootstrapError("App roles must be unique canonical role identifiers")
        seen_roles.add(role)
        if not isinstance(app["slug"], str) or SLUG.fullmatch(app["slug"]) is None:
            raise BootstrapError(f"App {role} has an invalid slug")
        if type(app["app_id"]) is not int or app["app_id"] <= 0 or type(app["installation_id"]) is not int or app["installation_id"] <= 0:
            raise BootstrapError(f"App {role} IDs must be positive integers")
        if not is_valid_client_id(app["client_id"]):
            raise BootstrapError(f"App {role} has an invalid client ID")
        if app["repository_selection"] != "all":
            raise BootstrapError(f"App {role} must use exact all-repository selection")
        permissions = object_value(app["permissions"], f"App {role} permissions")
        if permissions != APP_PERMISSIONS[role]:
            raise BootstrapError(f"App {role} permissions differ from the canonical role")
        events = list_value(app["events"], f"App {role} events")
        if events:
            raise BootstrapError(f"App {role} must subscribe to no events")

    secrets = object_value(manifest["secrets"], "secrets")
    for name, spec_raw in secrets.items():
        if NAME.fullmatch(name) is None or name.startswith("VERJSON_"):
            raise BootstrapError(f"secret {name!r} is not organization-neutral")
        if name in ENVIRONMENT_ONLY_APP_PRIVATE_KEYS:
            raise ProvisioningRequiredError(
                "environment-only App-key provisioning is required; the legacy "
                f"organization-secret bootstrap cannot handle {name}"
            )
        spec = object_value(spec_raw, f"secret {name}")
        kind = spec.get("kind")
        app_role = APP_PRIVATE_KEY_ROLES.get(name)
        if kind == "organization":
            exact_keys(spec, {"environment", "visibility", "kind"}, f"secret {name}")
            if app_role is not None:
                raise BootstrapError(
                    f"secret {name} is an App-private-key credential and must declare "
                    "kind 'app_private_key'"
                )
            if name not in ORGANIZATION_CREDENTIALS:
                raise BootstrapError(
                    f"secret {name} is not an explicitly supported organization credential"
                )
        elif kind == "app_private_key":
            exact_keys(spec, {"environment", "visibility", "kind", "role"}, f"secret {name}")
            role = spec["role"]
            if not isinstance(role, str) or role not in seen_roles:
                raise BootstrapError(f"secret {name} names an undeclared App role")
            expected_name = APP_PRIVATE_KEY_CREDENTIALS[role]
            if name != expected_name:
                if role in ENVIRONMENT_ONLY_APP_ROLES:
                    raise ProvisioningRequiredError(
                        "environment-only App-key provisioning is required; "
                        f"secret {name} for role {role} must use its canonical "
                        "environment secret name"
                    )
                raise BootstrapError(f"secret {name} must use canonical App-key name {expected_name}")
        else:
            raise BootstrapError(
                f"secret {name} must declare kind 'organization' or 'app_private_key'"
            )
        if NAME.fullmatch(str(spec["environment"])) is None or spec["visibility"] not in {"all", "private"}:
            raise BootstrapError(f"secret {name} has an invalid environment source or visibility")
    reject_environment_only_app_keys(secrets)

    seen_outputs: set[tuple[str, str]] = set()
    for index, caller_raw in enumerate(list_value(manifest["callers"], "callers")):
        caller = object_value(caller_raw, f"callers[{index}]")
        exact_keys(caller, {"repository", "generator", "arguments", "output"}, f"callers[{index}]")
        repository = str(safe_relative(caller["repository"], f"callers[{index}].repository"))
        output = str(safe_relative(caller["output"], f"callers[{index}].output"))
        if caller["generator"] not in GENERATORS:
            raise BootstrapError(f"callers[{index}] names an unsupported generator")
        arguments = list_value(caller["arguments"], f"callers[{index}].arguments")
        if any(not isinstance(argument, str) or "\x00" in argument for argument in arguments):
            raise BootstrapError(f"callers[{index}] arguments must be strings")
        identity = (repository, output)
        if identity in seen_outputs:
            raise BootstrapError(f"duplicate caller output {repository}/{output}")
        seen_outputs.add(identity)
    return manifest


def indexed(items: list[Any], key: str, label: str) -> dict[Any, dict[str, Any]]:
    result: dict[Any, dict[str, Any]] = {}
    for raw in items:
        item = object_value(raw, label)
        value = item.get(key)
        if value in result:
            raise BootstrapError(f"duplicate {label} identity {value}")
        result[value] = item
    return result


def installation_inventory(gh: GitHub, organization: str) -> dict[int, dict[str, Any]]:
    payload = gh.json(f"/orgs/{organization}/installations?per_page=100")
    installations = list_value(payload.get("installations"), "installation inventory")
    total = payload.get("total_count")
    if type(total) is not int or total != len(installations):
        raise BootstrapError("installation inventory is incomplete")
    return indexed(installations, "id", "installation")


def app_expectations(manifest: dict[str, Any], live: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    receipt: list[dict[str, Any]] = []
    for app in manifest["apps"]:
        installation = live.get(app["installation_id"])
        if installation is None:
            raise BootstrapError(f"App {app['role']} installation is absent")
        expected = {
            "app_id": app["app_id"], "app_slug": app["slug"], "client_id": app["client_id"],
        }
        observed = {key: installation.get(key) for key in expected}
        if observed != expected:
            raise BootstrapError(f"App {app['role']} identity differs")
        if installation.get("suspended_at") is not None:
            raise BootstrapError(f"App {app['role']} installation is suspended")
        if installation.get("repository_selection") != app["repository_selection"]:
            raise BootstrapError(f"App {app['role']} repository selection differs")
        if installation.get("permissions") != app["permissions"]:
            raise BootstrapError(f"App {app['role']} permissions differ")
        if sorted(installation.get("events", [])) != sorted(app["events"]):
            raise BootstrapError(f"App {app['role']} events differ")
        receipt.append({"role": app["role"], "slug": app["slug"], "installation_id": app["installation_id"], "status": "verified"})
    return receipt


def generate_callers(manifest: dict[str, Any], workspace: Path, contract_root: Path, mode: str) -> list[dict[str, str]]:
    generated: list[tuple[Path, bytes]] = []
    receipt: list[dict[str, str]] = []
    workspace_root = workspace.resolve(strict=True)
    for caller in manifest["callers"]:
        repository = (workspace_root / caller["repository"]).resolve(strict=True)
        if repository != workspace_root and workspace_root not in repository.parents:
            raise BootstrapError("caller repository escapes workspace")
        if not (repository / ".git").exists() and not (repository / ".git").is_file():
            raise BootstrapError(f"caller repository is not a Git worktree: {caller['repository']}")
        output = repository / caller["output"]
        resolved_parent = output.parent.resolve(strict=True) if output.parent.is_dir() else None
        if resolved_parent is None or (resolved_parent != repository and repository not in resolved_parent.parents):
            raise BootstrapError(f"caller output parent escapes or is absent: {caller['output']}")
        if output.exists() and output.is_symlink():
            raise BootstrapError(f"caller output is a symlink: {caller['output']}")
        generator = contract_root / "scripts" / GENERATORS[caller["generator"]]
        arguments = list(caller["arguments"])
        if caller["generator"] == "changelog":
            command = [str(generator), arguments[0], manifest["contract_sha"], *arguments[1:]] if arguments else []
        else:
            command = [str(generator), manifest["contract_sha"], *arguments]
        if not command:
            raise BootstrapError("changelog generator requires a mode argument")
        result = subprocess.run(command, cwd=repository, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise BootstrapError(f"generator failed for {caller['repository']}/{caller['output']}")
        generated.append((output, result.stdout))
        current = output.read_bytes() if output.exists() else None
        status = "current" if current == result.stdout else "drifted"
        receipt.append({"repository": caller["repository"], "output": caller["output"], "status": status})
    if mode == "check" and any(item["status"] == "drifted" for item in receipt):
        raise BootstrapError("one or more generated callers drift from the manifest")
    if mode == "apply":
        staged: list[tuple[Path, Path]] = []
        originals: dict[Path, tuple[bytes, int] | None] = {}
        for output, content in generated:
            originals[output] = (output.read_bytes(), output.stat().st_mode & 0o777) if output.exists() else None
            with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
                stream.write(content)
                temporary = Path(stream.name)
            temporary.chmod(0o755 if output.suffix == ".sh" else 0o644)
            staged.append((temporary, output))
        applied: list[Path] = []
        try:
            for temporary, output in staged:
                os.replace(temporary, output)
                applied.append(output)
        except OSError as error:
            for output in reversed(applied):
                original = originals[output]
                if original is None:
                    output.unlink(missing_ok=True)
                else:
                    output.write_bytes(original[0])
                    output.chmod(original[1])
            raise BootstrapError("generated caller replacement failed; prior files restored") from error
        finally:
            for temporary, _ in staged:
                temporary.unlink(missing_ok=True)
    return receipt


def converge(manifest: dict[str, Any], gh: GitHub, workspace: Path, contract_root: Path, mode: str) -> dict[str, Any]:
    manifest = validate_manifest(manifest)
    reject_environment_only_app_keys(manifest["secrets"])
    contract_head = subprocess.run(
        ["git", "-C", str(contract_root), "rev-parse", "HEAD"], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if contract_head.returncode or contract_head.stdout.strip() != manifest["contract_sha"]:
        raise BootstrapError("contract root HEAD does not equal the immutable manifest SHA")
    contract_status = subprocess.run(
        ["git", "-C", str(contract_root), "status", "--porcelain", "--untracked-files=all"],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if contract_status.returncode or contract_status.stdout:
        raise BootstrapError("contract root must be clean before generator execution")
    if mode == "apply":
        missing = [spec["environment"] for spec in manifest["secrets"].values() if not os.environ.get(spec["environment"])]
        if missing:
            raise BootstrapError(f"secret environment {missing[0]} is missing")
    caller_plan = generate_callers(manifest, workspace, contract_root, "dry-run" if mode == "apply" else mode)
    authenticated = gh.json("/user").get("login")
    if not isinstance(authenticated, str) or not authenticated:
        raise BootstrapError("authenticated GitHub identity is unavailable")
    membership = gh.json(f"/orgs/{manifest['organization']}/memberships/{authenticated}")
    if membership.get("state") != "active" or membership.get("role") != "admin":
        raise BootstrapError("authenticated identity is not an active owner of the target organization")
    live_variables_payload = gh.json(f"/orgs/{manifest['organization']}/actions/variables?per_page=100")
    live_variables_list = list_value(live_variables_payload.get("variables"), "variable inventory")
    if live_variables_payload.get("total_count") != len(live_variables_list):
        raise BootstrapError("variable inventory is incomplete")
    live_variables = indexed(live_variables_list, "name", "variable")
    live_secrets_payload = gh.json(f"/orgs/{manifest['organization']}/actions/secrets?per_page=100")
    live_secrets_list = list_value(live_secrets_payload.get("secrets"), "secret inventory")
    if live_secrets_payload.get("total_count") != len(live_secrets_list):
        raise BootstrapError("secret inventory is incomplete")
    live_secrets = indexed(live_secrets_list, "name", "secret")

    receipt: dict[str, Any] = {
        "organization": manifest["organization"], "contract_sha": manifest["contract_sha"],
        "mode": mode, "authenticated_as": authenticated, "variables": [], "secrets": [],
        "apps": app_expectations(manifest, installation_inventory(gh, manifest["organization"])),
    }
    for name, spec in manifest["variables"].items():
        live = live_variables.get(name)
        current = live is not None and live.get("value") == spec["value"] and live.get("visibility") == spec["visibility"]
        variable_receipt = {"name": name, "status": "current" if current else "drifted"}
        receipt["variables"].append(variable_receipt)
        if mode == "check" and not current:
            raise BootstrapError(f"variable {name} differs")
        if mode == "apply" and not current:
            endpoint = f"/orgs/{manifest['organization']}/actions/variables" + (f"/{name}" if live else "")
            method = "PATCH" if live else "POST"
            try:
                gh.run(["api", "--method", method, endpoint, "-f", f"name={name}", "-f", f"value={spec['value']}", "-f", f"visibility={spec['visibility']}"])
                variable_receipt["status"] = "applied"
            except BootstrapError as error:
                receipt["status"] = "failed"
                raise ConvergenceError(str(error), receipt) from error
    for name, spec in manifest["secrets"].items():
        live = live_secrets.get(name)
        exists = live is not None and live.get("visibility") == spec["visibility"]
        secret_receipt = {"name": name, "status": "present" if exists else "missing", "value": "REDACTED"}
        receipt["secrets"].append(secret_receipt)
        if mode == "check" and not exists:
            raise BootstrapError(f"secret {name} is absent or has different visibility")
        if mode == "apply":
            value = os.environ[spec["environment"]]
            try:
                gh.run(
                    ["secret", "set", name, "--org", manifest["organization"], "--visibility", spec["visibility"]],
                    stdin=value, sensitive=True,
                )
                secret_receipt["status"] = "applied"
            except BootstrapError as error:
                receipt["status"] = "failed"
                raise ConvergenceError(str(error), receipt) from error
    try:
        receipt["callers"] = generate_callers(manifest, workspace, contract_root, "apply") if mode == "apply" else caller_plan
    except BootstrapError as error:
        receipt["callers"] = caller_plan
        receipt["status"] = "failed"
        raise ConvergenceError(str(error), receipt) from error
    receipt["status"] = "planned" if mode == "dry-run" else "converged"
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=sorted(MODES))
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--contract-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--gh", default="gh")
    parser.add_argument("--receipt", type=Path)
    arguments = parser.parse_args()
    receipt: dict[str, Any]
    exit_code = 0
    try:
        manifest = validate_manifest(json.loads(arguments.manifest.read_text(encoding="utf-8")))
        receipt = converge(manifest, GitHub(arguments.gh), arguments.workspace, arguments.contract_root, arguments.mode)
    except (BootstrapError, OSError, json.JSONDecodeError) as error:
        status = "provisioning_required" if isinstance(error, ProvisioningRequiredError) else "failed"
        receipt = error.receipt if isinstance(error, ConvergenceError) else {"mode": arguments.mode, "status": status}
        if isinstance(error, ProvisioningRequiredError):
            receipt["provisioning_path"] = PROVISIONING_PATH
        receipt["error"] = str(error)
        exit_code = 1
    rendered = json.dumps(receipt, sort_keys=True, indent=2) + "\n"
    if arguments.receipt:
        arguments.receipt.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
