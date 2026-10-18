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
    # actions/checkout follows an existing link at its path and empties the
    # target, so reaching one here is the defect, not a fixture problem.
    assert not destination.is_symlink() and not destination.exists(), (
        f"checkout of {options['path']} reached a path the release tree already holds"
    )
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


def run_steps(steps, *, sha, workspace, job_env, runner_temp, registry, expect_failure=False):
    """Run steps in order; with expect_failure, return the first failing step and its output."""
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
        if expect_failure and result.returncode != 0:
            return step.get("name"), result.stdout + result.stderr
        assert result.returncode == 0, f"{step.get('name')}: {result.stdout}{result.stderr}"
    assert not expect_failure, "every step succeeded, but a failure was expected"
    return None


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


RESERVED_PATH = ".verjson-npm-acquisition-contract"
RESERVED_MESSAGE = f"{RESERVED_PATH} is reserved for the canonical dependency acquisition contract"


def verify_reserved_contract_path(label, steps, *, sha, root):
    """A release tree carrying the contract checkout path is refused before checkout.

    actions/checkout follows a symlink at its path and empties the target, so a
    committed link would let the release tree aim that at runner state.
    """
    for kind in ("symlink", "directory"):
        case = root / kind
        case.mkdir()
        workspace = case / "workspace"
        workspace.mkdir()
        outside = case / "runner-state"
        outside.mkdir()
        (outside / "keep").write_text("runner state\n")
        if kind == "symlink":
            (workspace / RESERVED_PATH).symlink_to(outside)
        else:
            planted = workspace / RESERVED_PATH / "scripts"
            planted.mkdir(parents=True)
            (planted / "npm-private-acquisition.sh").write_text("echo planted\n")
        trace = case / "npm-trace.log"
        wrapper_dir = case / "bin"
        wrapper_dir.mkdir()
        (wrapper_dir / "npm").write_text(f"#!/bin/sh\necho \"$*\" >> {trace}\n")
        (wrapper_dir / "npm").chmod(0o755)
        runner_temp = case / "runner-temp"
        runner_temp.mkdir()
        job_env = os.environ | {
            "PATH": f"{wrapper_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_WORKSPACE": str(workspace),
            "RUNNER_TEMP": str(runner_temp),
        }
        registry = type("Registry", (), {"origin": "http://127.0.0.1:9"})()
        failed = run_steps(
            steps, sha=sha, workspace=workspace, job_env=job_env,
            runner_temp=runner_temp, registry=registry, expect_failure=True,
        )
        assert failed is not None and RESERVED_MESSAGE in failed[1], (
            f"{label}: a committed {kind} at {RESERVED_PATH} was not refused: {failed}"
        )
        assert not trace.exists(), f"{label}: npm ran before the reserved-path refusal"
        assert (outside / "keep").read_text() == "runner state\n", label


SELECTION_PATH = ".changelog-contract"
SELECTION_MESSAGE = f"{SELECTION_PATH} is reserved for the canonical selection contract"


def selection_checkout_steps(workflow):
    """The verify steps from the release-tree checkout to the selection checkout."""
    steps = workflow["jobs"]["verify"]["steps"]
    start = next(
        index for index, step in enumerate(steps)
        if step.get("name") == "Check out the tree that will be released"
    )
    end = next(
        index for index, step in enumerate(steps)
        if step.get("name") == "Check out the canonical selection contract"
    )
    return steps[start + 1:end + 1]


def verify_reserved_selection_path(label, workflow, *, sha, root):
    """The verify job refuses a release tree carrying the selection contract path."""
    for kind in ("symlink", "directory"):
        case = root / f"selection-{kind}"
        case.mkdir()
        workspace = case / "workspace"
        workspace.mkdir()
        outside = case / "runner-state"
        outside.mkdir()
        (outside / "keep").write_text("runner state\n")
        if kind == "symlink":
            (workspace / SELECTION_PATH).symlink_to(outside)
        else:
            (workspace / SELECTION_PATH / "scripts").mkdir(parents=True)
        runner_temp = case / "runner-temp"
        runner_temp.mkdir()
        registry = type("Registry", (), {"origin": "http://127.0.0.1:9"})()
        failed = run_steps(
            selection_checkout_steps(workflow), sha=sha, workspace=workspace,
            job_env=os.environ | {"GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(runner_temp)},
            runner_temp=runner_temp, registry=registry, expect_failure=True,
        )
        assert failed is not None and SELECTION_MESSAGE in failed[1], (
            f"{label}: a committed {kind} at {SELECTION_PATH} was not refused before checkout: {failed}"
        )
        assert (outside / "keep").read_text() == "runner state\n", label


def private_lock_validation_step(workflow):
    steps = workflow["jobs"]["acquire-private-dependencies"]["steps"]
    return next(step for step in steps if step.get("name") == "Validate the approved private dependency lock")


def verify_private_lock_validation(workflow, root):
    """The release-artifact allowlist covers every GitHub Packages download.

    The acquisition script fetches every locked npm.pkg.github.com download
    with the token, so an npm alias that installs an unapproved private package
    under another name must fail validation rather than pass as public.
    """
    root.mkdir()
    step = private_lock_validation_step(workflow)
    approved = "@verjson/fixture"
    integrity = "sha512-" + "A" * 86 + "=="
    canonical = {
        "": {"name": "fixture"},
        "node_modules/@verjson/fixture": {
            "version": "1.0.0",
            "resolved": "https://npm.pkg.github.com/download/@verjson/fixture/1.0.0/0123abcd",
            "integrity": integrity,
        },
        "node_modules/zod": {
            "version": "1.0.0",
            "resolved": "https://registry.npmjs.org/zod/-/zod-1.0.0.tgz",
            "integrity": integrity,
        },
    }
    cases = {
        "approved lock": (canonical, None, None),
        "aliased unapproved @verjson package": (canonical | {
            "node_modules/innocent": {
                "name": "@verjson/other",
                "version": "1.0.0",
                "resolved": "https://npm.pkg.github.com/download/@verjson/other/1.0.0/0123abcd",
                "integrity": integrity,
            },
        }, None, "unapproved internal dependency: innocent"),
        "other scope on GitHub Packages": (canonical | {
            "node_modules/@acme/private": {
                "version": "1.0.0",
                "resolved": "https://NPM.pkg.github.com/download/@acme/private/1.0.0/0123abcd",
                "integrity": integrity,
            },
        }, None, "unapproved internal dependency: @acme/private"),
        "reserved contract directory": (canonical, "directory", RESERVED_MESSAGE),
        "reserved contract symlink": (canonical, "symlink", RESERVED_MESSAGE),
    }
    for case, (packages, reserved, message) in cases.items():
        workspace = root / case.replace(" ", "-")
        workspace.mkdir()
        (workspace / "package-lock.json").write_text(
            json.dumps({"lockfileVersion": 3, "packages": packages})
        )
        if reserved == "directory":
            (workspace / RESERVED_PATH).mkdir()
        elif reserved == "symlink":
            (workspace / RESERVED_PATH).symlink_to(root)
        with tempfile.TemporaryDirectory(prefix="release-caller-lock-") as script_dir:
            script_path = Path(script_dir) / "step.sh"
            script_path.write_text(step["run"])
            result = subprocess.run(
                shell_command(step, script_path), cwd=workspace, text=True, capture_output=True,
                env=os.environ | {"APPROVED_INTERNAL_PACKAGES": approved},
            )
        output = result.stdout + result.stderr
        if message is None:
            assert result.returncode == 0, f"{case}: {output}"
        else:
            assert result.returncode != 0 and message in output, f"{case}: accepted or wrong reason: {output}"


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
    with tempfile.TemporaryDirectory(prefix="release-caller-reserved-") as temp:
        for label, workflow in workflows.items():
            case_root = Path(temp) / label.replace(" ", "_").replace("/", "_")
            case_root.mkdir()
            verify_reserved_contract_path(
                f"{label} verify", verify_dependency_steps(workflow), sha=sha, root=case_root,
            )
            verify_reserved_selection_path(f"{label} verify", workflow, sha=sha, root=case_root)
        verify_private_lock_validation(workflows["release-artifact"], Path(temp) / "lock")
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
