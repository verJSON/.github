#!/usr/bin/env python3
"""Generated release callers never expose the package token to dependency code.

`npm ci --ignore-scripts` is not a credential boundary (ADR 0220, 2026-10-18):
npm 10 runs `prepare` for linked and directory dependencies, and npm 10 and 11
prepare a git dependency through a child `npm install` that inherits the token
and reads the dependency's own .npmrc. This test runs the dependency steps that
scripts/gen-changelog-caller.sh emits, in order and as Actions runs them, under
pinned npm 10 and 11 against a hostile git dependency, and fails if the token
reaches anything but the exact locked private download.

The harness (pinned npm, fixture registries, lifecycle recorder) is the one
scripts/node-release-credential-isolation.test.py uses for node-release.yml.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR = REPO_ROOT / "scripts/gen-changelog-caller.sh"
_SPEC = importlib.util.spec_from_file_location(
    "node_release_credential_isolation",
    REPO_ROOT / "scripts/node-release-credential-isolation.test.py",
)
harness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(harness)

TOKEN = harness.TOKEN
CANONICAL_REGISTRY = "https://npm.pkg.github.com"
# actions/setup-node exports this placeholder when registry-url is set and the
# step itself has no NODE_AUTH_TOKEN, so later steps see it, never the secret.
SETUP_NODE_PLACEHOLDER = "XXXXX-XXXXX-XXXXX-XXXXX"
SELECTED = "steps.release-version.outputs.selected == 'true'"
KNOWN_CONDITIONS = {
    None,
    SELECTED,
    f"{SELECTED} && hashFiles('package.json') != ''",
}
# The verify-job steps between setup-node and package preparation that are not
# part of dependency installation.
NON_INSTALL_VERIFY_STEPS = {"Capture trusted release verification runtime"}

# Install hooks every dependency kind runs once `npm rebuild` restores lifecycle
# execution without the credential.
VERIFY_REQUIRED_HOOKS = {
    f"{package}:dependency-{hook}"
    for package in (
        "fixture-dependency",
        "fixture-transitive-dependency",
        "git-dependency",
        "@fixture/private-dependency",
    )
    for hook in ("preinstall", "install", "postinstall")
}

# Every generated mode that installs dependencies in `verify`. release-node with
# `--only-package-dir .` is this repository's own .github/workflows/release.yml
# as regenerated (scripts/release-verify.sh requires that file to be exactly
# this output at its pinned contract).
VERIFY_MODES = {
    "release-node --only-package-dir .": ["release-node", "--only-package-dir", "."],
    "release-snapshot": ["release-snapshot"],
    "release-artifact": [
        "release-artifact", "--build-runner", "ubuntu-24.04",
        "--approved-internal-package", "@verjson/fixture",
    ],
}


def generate(arguments, sha):
    result = subprocess.run(
        ["bash", str(GENERATOR), arguments[0], sha, *arguments[1:]],
        cwd=REPO_ROOT, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return yaml.safe_load(result.stdout)


def verify_dependency_steps(workflow):
    steps = workflow["jobs"]["verify"]["steps"]
    setup = next(
        index for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/setup-node@")
    )
    assert steps[setup]["with"]["registry-url"] == CANONICAL_REGISTRY
    end = next(
        index for index, step in enumerate(steps)
        if step.get("name") == "Prepare release package metadata"
    )
    sequence = [
        step for step in steps[setup + 1:end]
        if step.get("name") not in NON_INSTALL_VERIFY_STEPS
    ]
    for step in steps[setup + 1:end]:
        if step.get("name") in NON_INSTALL_VERIFY_STEPS:
            assert "secrets" not in json.dumps(step).lower(), step.get("name")
    assert sequence, "verify installs no dependencies"
    return sequence


def private_acquisition_job_steps(workflow):
    """release-artifact's acquisition job, which hands node_modules to build runners."""
    steps = workflow["jobs"]["acquire-private-dependencies"]["steps"]
    setup = next(
        index for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/setup-node@")
    )
    assert "registry-url" not in steps[setup].get("with", {}), steps[setup]
    end = next(
        index for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/cache/save@")
    )
    # The lock validation pins @verjson entries to https://npm.pkg.github.com,
    # which the relocated fixture registry cannot be; it runs before any
    # credential and is covered by the generated contract test.
    validation = steps[setup + 1]
    assert validation.get("name") == "Validate the approved private dependency lock", validation
    assert "secrets" not in json.dumps(validation).lower()
    sequence = steps[setup + 2:end]
    assert sequence, "the acquisition job installs no dependencies"
    return sequence


def relocate(text, registry):
    """Point the canonical private registry at the fixture registry."""
    host = registry.origin.split("://", 1)[1]
    return text.replace(CANONICAL_REGISTRY, registry.origin).replace(
        CANONICAL_REGISTRY.split("://", 1)[1], host,
    )


def resolve_env(step, runner_temp, registry):
    resolved = {}
    for key, value in (step.get("env") or {}).items():
        text = str(value).replace("${{ runner.temp }}", str(runner_temp))
        text = text.replace("${{ secrets.NODE_AUTH_TOKEN }}", TOKEN)
        assert "${{" not in text, f"{step.get('name')}: unresolved expression in env {key}: {value}"
        resolved[key] = relocate(text, registry)
    return resolved


def materialize_contract_checkout(step, sha, workspace):
    """Emulate actions/checkout of the canonical contract at the pinned commit."""
    uses = str(step.get("uses", ""))
    options = step.get("with") or {}
    assert uses.startswith("actions/checkout@"), f"unexpected action in dependency steps: {uses}"
    assert str(options.get("repository", "")).lower() == "verjson/.github", options
    assert options.get("ref") == sha, options
    assert options.get("persist-credentials") is False, options
    destination = workspace / options["path"]
    shutil.copytree(
        REPO_ROOT / "scripts", destination / "scripts",
        ignore=shutil.ignore_patterns("__pycache__"),
    )


def shell_command(step, script_path):
    shell = step.get("shell")
    if shell is None:
        return ["bash", "-e", str(script_path)]
    assert shell == "bash", f"{step.get('name')}: unsupported shell {shell}"
    return ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script_path)]


def run_steps(steps, *, sha, workspace, job_env, runner_temp, registry):
    for step in steps:
        assert step.get("if") in KNOWN_CONDITIONS, f"{step.get('name')}: if {step.get('if')}"
        if "uses" in step:
            materialize_contract_checkout(step, sha, workspace)
            continue
        with tempfile.TemporaryDirectory(prefix="release-caller-step-") as script_dir:
            script_path = Path(script_dir) / "step.sh"
            script_path.write_text(relocate(step["run"], registry))
            result = subprocess.run(
                shell_command(step, script_path),
                cwd=workspace,
                env=job_env | resolve_env(step, runner_temp, registry),
                text=True,
                capture_output=True,
            )
        assert result.returncode == 0, f"{step.get('name')}: {result.stdout}{result.stderr}"


def token_tracing_path(root, env):
    """Record every npm invocation and whether the real token is in its environment."""
    wrapper_dir = root / "npm-trace-bin"
    wrapper_dir.mkdir()
    real_npm = shutil.which("npm", path=env["PATH"])
    assert real_npm, "the provisioned npm is not on PATH"
    wrapper = wrapper_dir / "npm"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        # The token is written into this file, never into an environment
        # variable, so the recorder itself cannot become an exposure.
        f"if env | grep -qF {TOKEN}; then credential=present; else credential=absent; fi\n"
        "printf '%s|%s\\n' \"$*\" \"$credential\" >> \"$NPM_TRACE\"\n"
        "exec \"$REAL_NPM\" \"$@\"\n"
    )
    wrapper.chmod(0o755)
    return {
        "PATH": f"{wrapper_dir}{os.pathsep}{env['PATH']}",
        "REAL_NPM": real_npm,
        "NPM_TRACE": str(root / "npm-trace.log"),
    }


def build_fixture(root, npm_bin, private_registry, attacker_registry):
    project, download_path = harness.write_dependency_fixture(root, private_registry, attacker_registry)
    harness.run(
        ["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=project,
        env=harness.npm_environment(root, npm_bin, private_registry, "setup") | {"NODE_AUTH_TOKEN": TOKEN},
    )
    return project, download_path


def exposed_records(records):
    return [
        record for record in records
        if f":{TOKEN}:" in record or not record.endswith(":[]:[]")
    ]


def verify_steps_isolate_credential(
    label, steps, *, sha, npm_bin, version, root, setup_node_registry, required_hooks,
):
    private_registry = harness.FixtureRegistry(require_token=True)
    attacker_registry = harness.FixtureRegistry(require_token=False)
    try:
        fixture, download_path = build_fixture(root, npm_bin, private_registry, attacker_registry)
        workspace = root / "workspace"
        shutil.copytree(fixture, workspace)
        git = ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid"]
        for command in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "release tree"]):
            subprocess.run(git + command, cwd=workspace, check=True, capture_output=True)
        home = root / "home"
        home.mkdir()
        runner_temp = root / "runner-temp-3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b"
        runner_temp.mkdir()
        job_env = harness.npm_environment(root, npm_bin, private_registry, "release") | {
            "HOME": str(home),
            "GITHUB_WORKSPACE": str(workspace),
            "RUNNER_TEMP": str(runner_temp),
        }
        del job_env["npm_config_cache"]
        if setup_node_registry:
            job_env["NODE_AUTH_TOKEN"] = SETUP_NODE_PLACEHOLDER
        else:
            # setup-node without registry-url writes no user config.
            for key in ("NPM_CONFIG_USERCONFIG", "NPM_CONFIG_GLOBALCONFIG"):
                del job_env[key]
        job_env |= token_tracing_path(root, job_env)
        private_registry.requests.clear()
        attacker_registry.requests.clear()

        run_steps(
            steps, sha=sha, workspace=workspace, job_env=job_env,
            runner_temp=runner_temp, registry=private_registry,
        )

        prefix = f"{label} under npm {version}"
        leaked = [
            path for path, authorization in attacker_registry.requests
            if authorization and TOKEN in authorization
        ]
        assert not leaked, f"{prefix}: a dependency-chosen registry received the credential: {leaked}"
        assert attacker_registry.requests, (
            f"{prefix}: the git dependency was never prepared, so the exfiltration path went unexercised"
        )
        records = Path(job_env["LIFECYCLE_LOG"]).read_text().splitlines() if Path(
            job_env["LIFECYCLE_LOG"]
        ).exists() else []
        exposed = exposed_records(records)
        assert not exposed, f"{prefix}: dependency code observed the credential: {exposed}"
        ran = {":".join(record.split(":")[:2]) for record in records}
        assert required_hooks <= ran, (
            f"{prefix}: dependency code never ran, so its isolation went unexercised: "
            f"{sorted(required_hooks - ran)}"
        )
        download_url = f"{private_registry.origin}{download_path}"
        trace = Path(job_env["NPM_TRACE"]).read_text().splitlines()
        credentialed = [line.rsplit("|", 1)[0] for line in trace if line.endswith("|present")]
        assert credentialed == ["--version", f"cache add {download_url}"], (
            f"{prefix}: unexpected npm invocations with the credential: {credentialed}"
        )
        # A tokenless request would carry no Authorization header, so this also
        # proves the credentialless steps never reached the private registry.
        assert private_registry.requests and all(
            request == (download_path, f"Bearer {TOKEN}") for request in private_registry.requests
        ), (
            f"{prefix}: the private registry saw more than the locked credentialed download: "
            f"{private_registry.requests}"
        )
        assert (workspace / "node_modules/@fixture/private-dependency/package.json").is_file(), prefix
        assert not list(runner_temp.glob("verjson-private-acquisition.*")), list(runner_temp.iterdir())
        untracked = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", ".", ":!node_modules"],
            cwd=workspace, text=True, capture_output=True, check=True,
        ).stdout
        assert untracked == "", f"{prefix}: dependency installation left workspace changes: {untracked}"
    finally:
        private_registry.close()
        attacker_registry.close()


def main():
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, capture_output=True, check=True,
    ).stdout.strip()
    workflows = {label: generate(arguments, sha) for label, arguments in VERIFY_MODES.items()}
    cases = {
        (f"{label} verify", major): (verify_dependency_steps(workflow), True, VERIFY_REQUIRED_HOOKS)
        for label, workflow in workflows.items()
        for major in harness.SUPPORTED_NPM_RELEASES
    } | {
        # Lifecycle scripts never run there; the git dependency's preparation,
        # which the attacker registry observes, is what exercises the path.
        ("release-artifact acquire-private-dependencies", major): (
            private_acquisition_job_steps(workflows["release-artifact"]), False, set(),
        )
        for major in harness.SUPPORTED_NPM_RELEASES
    }
    verified = set()
    for major, (version, integrity) in sorted(harness.SUPPORTED_NPM_RELEASES.items()):
        with tempfile.TemporaryDirectory(prefix=f"release-caller-npm{major}-") as temp:
            npm_bin = harness.provision_npm(version, integrity, Path(temp) / "npm")
            for (label, case_major), (steps, setup_node_registry, required_hooks) in cases.items():
                if case_major != major:
                    continue
                case_root = Path(temp) / label.replace(" ", "_").replace("/", "_")
                case_root.mkdir()
                verify_steps_isolate_credential(
                    label, steps, sha=sha, npm_bin=npm_bin, version=version, root=case_root,
                    setup_node_registry=setup_node_registry, required_hooks=required_hooks,
                )
                verified.add((label, major))
    assert verified == set(cases), f"unexercised cases: {sorted(set(cases) - verified)}"
    print(f"ok - {len(verified)} generated dependency-installation cases isolate the package credential")


if __name__ == "__main__":
    main()
