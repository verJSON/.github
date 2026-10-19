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

RELEASE_CALLER_ISOLATION_PART selects one part of the case space (see
parts()) so actions-ci can run it as several rows inside their budgets.
Unset, or `all`, runs every case; an unknown part exits 2 and runs nothing.
scripts/release-caller-credential-isolation-parts.test.py pins the partition.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
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
    sequence = copy.deepcopy(steps[setup + 2:end])
    assert sequence, "the acquisition job installs no dependencies"
    # The harness registry serves @fixture/private-dependency, so the approved
    # list the script enforces is pointed at it; the generated value is asserted.
    acquisition = next(
        step for step in sequence if step.get("name") == "Acquire private registry packages with credentials"
    )
    assert acquisition["env"]["APPROVED_PRIVATE_PACKAGES"] == "@verjson/fixture", acquisition["env"]
    acquisition["env"]["APPROVED_PRIVATE_PACKAGES"] = "@fixture/private-dependency"
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
        # variable, so the recorder itself cannot become an exposure. No pipe:
        # under pipefail an early-exiting reader can kill the writer with
        # SIGPIPE and turn a match into "absent".
        f"if [[ \"$(env)\" == *{shlex.quote(TOKEN)}* ]]; then credential=present; else credential=absent; fi\n"
        "printf '%s|%s\\n' \"$*\" \"$credential\" >> \"$NPM_TRACE\"\n"
        "exec \"$REAL_NPM\" \"$@\"\n"
    )
    wrapper.chmod(0o755)
    return {
        "PATH": f"{wrapper_dir}{os.pathsep}{env['PATH']}",
        "REAL_NPM": real_npm,
        "NPM_TRACE": str(root / "npm-trace.log"),
    }


def verify_token_tracing_is_reliable(root):
    """The recorder must see the token however large the environment around it is.

    Undercounting is the dangerous direction: a credentialed npm call recorded as
    absent would let a real exposure pass the isolation assertions below.
    """
    fake_bin = root / "fake-npm-bin"
    fake_bin.mkdir(parents=True)
    (fake_bin / "npm").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "npm").chmod(0o755)
    tracing = token_tracing_path(root, {"PATH": f"{fake_bin}{os.pathsep}/usr/bin:/bin"})
    # 512 KiB after the token keeps the environment dump writing after an
    # early-exiting reader has seen the token: a piped probe then misrecords
    # every call (500 of 500 locally), while below ~320 KiB it does so only
    # intermittently. Three calls per direction are enough to fail it.
    padding = {f"TRACE_PADDING_{index:04d}": "x" * 1024 for index in range(512)}
    trace = Path(tracing["NPM_TRACE"])
    for credential, expected in ((TOKEN, "present"), (None, "absent")):
        trace.unlink(missing_ok=True)
        env = ({"NODE_AUTH_TOKEN": credential} if credential else {}) | tracing | padding
        for _ in range(3):
            subprocess.run([str(root / "npm-trace-bin/npm"), "--version"], env=env, check=True)
        recorded = [line.rsplit("|", 1)[1] for line in trace.read_text().splitlines()]
        assert recorded == [expected] * 3, f"npm trace recorder misrecorded the credential: {recorded}"


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


FETCH_RECORDER = """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$CALLS_LOG"
case "$1" in
  --version) echo 11.19.1 ;;
  cache)
    url="$3"
    printf '%s\\n' "$url" >> "$FETCH_LOG"
    content="archive $url"
    digest="$(printf %s "$content" | sha512sum | cut -d' ' -f1)"
    target="$npm_config_cache/_cacache/content-v2/sha512/${digest:0:2}/${digest:2:2}/${digest:4}"
    mkdir -p "$(dirname "$target")"
    printf %s "$content" > "$target" ;;
  *) exit 97 ;;
esac
"""


def private_download(name, *, host="https://npm.pkg.github.com"):
    """A lock entry whose integrity matches the bytes FETCH_RECORDER caches for it."""
    url = f"{host}/download/{name}/1.0.0/0123abcd"
    return {"version": "1.0.0", "resolved": url, "integrity": integrity_of(f"archive {url}".encode())}


def integrity_of(data):
    return harness.integrity_of(data)


def token_fetches(workspace, root, approved=None, github_workspace=None):
    """The URLs the real acquisition script fetches with the token for this tree.

    With `approved`, the script is given that allowlist, as release-artifact
    gives it; without, it runs as node-release and the verify jobs run it.
    """
    bin_dir = root / "fetch-bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "npm").write_text(FETCH_RECORDER)
    (bin_dir / "npm").chmod(0o755)
    runner_temp = workspace.parent / f"{workspace.name}-runner-temp"
    runner_temp.mkdir(exist_ok=True)
    log = workspace.parent / f"{workspace.name}-fetches.log"
    calls = workspace.parent / f"{workspace.name}-npm-calls.log"
    for stale in (log, calls):
        stale.unlink(missing_ok=True)
    allowlist = {} if approved is None else {"APPROVED_PRIVATE_PACKAGES": approved}
    result = subprocess.run(
        ["bash", "-e", str(REPO_ROOT / "scripts/npm-private-acquisition.sh")],
        cwd=workspace, text=True, capture_output=True,
        env=os.environ | {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_WORKSPACE": str(github_workspace or workspace),
            "RUNNER_TEMP": str(runner_temp),
            "PRIVATE_PACKAGE_REGISTRY": CANONICAL_REGISTRY,
            "npm_config_cache": str(runner_temp / "npm-cache"),
            "FETCH_LOG": str(log),
            "CALLS_LOG": str(calls),
        } | allowlist,
    )
    fetched = set(log.read_text().split()) if log.exists() else set()
    npm_calls = calls.read_text().splitlines() if calls.exists() else []
    return result, fetched, npm_calls


def verify_private_lock_validation(workflow, root):
    """Validation accepts only trees whose token fetches are all approved.

    The acquisition script fetches every locked npm.pkg.github.com download
    with the token, wherever it sits in the lock and from whichever lockfile
    npm ci will read, so the property checked is end to end: when validation
    accepts a tree, the real script's credentialed fetches are a subset of the
    approved downloads. Each rejected attack is also shown to reach an
    unapproved download through the script, so its rejection is not vacuous.
    """
    root.mkdir()
    step = private_lock_validation_step(workflow)
    approved_entry = private_download("@verjson/fixture")
    approved_urls = {approved_entry["resolved"]}
    canonical = {
        "": {"name": "fixture"},
        "node_modules/@verjson/fixture": approved_entry,
        "node_modules/zod": {
            "version": "1.0.0",
            "resolved": "https://registry.npmjs.org/zod/-/zod-1.0.0.tgz",
            "integrity": integrity_of(b"zod"),
        },
    }
    secret = private_download("@verjson/secret")
    cases = {
        "approved lock": (canonical, {}, None),
        "aliased unapproved @verjson package": (
            canonical | {"node_modules/innocent": {**private_download("@verjson/other"), "name": "@verjson/other"}},
            {}, "unapproved internal dependency: innocent",
        ),
        "other scope on GitHub Packages": (
            canonical | {"node_modules/@acme/private": private_download("@acme/private")},
            {}, "unapproved internal dependency: @acme/private",
        ),
        # L2: a download keyed outside node_modules/ is still fetched with the token.
        "private download outside node_modules": (
            canonical | {"vendor/x": secret}, {},
            "private release acquisition rejects a private package download outside node_modules: vendor/x",
        ),
        # A workspace member named in the scope is local, never fetched.
        "workspace member in the private scope": (
            canonical | {"packages/tool": {"name": "@verjson/tool", "version": "1.0.0"}}, {}, None,
        ),
        # The project's own entry is never a download, but the script reads
        # every resolved URL in the packages map, the root's included.
        "root entry with a resolved download": (
            canonical | {"": {"name": "fixture", **secret}}, {},
            "private release acquisition rejects a resolved root lock entry",
        ),
        # WHATWG parsing drops a tab and keeps a trailing host dot; the script
        # refuses both, and so must validation.
        "tab in an approved download path": (
            canonical | {"node_modules/@verjson/fixture": {
                **approved_entry, "resolved": approved_entry["resolved"].replace("0123abcd", "0123\tabcd"),
            }}, {"script-refuses": True},
            "internal dependency is not pinned to its exact GitHub Packages download URL",
        ),
        "trailing-dot private host": (
            canonical | {"node_modules/innocent": private_download(
                "@verjson/secret", host="https://npm.pkg.github.com.",
            )}, {"script-refuses": True},
            "unapproved internal dependency: innocent",
        ),
        # L1: npm ci and the acquisition script both prefer npm-shrinkwrap.json.
        "npm-shrinkwrap.json beside a clean package-lock.json": (
            canonical, {"shrinkwrap": canonical | {"node_modules/@verjson/secret": secret}},
            "private release acquisition requires package-lock.json without npm-shrinkwrap.json",
        ),
        "npm-shrinkwrap.json symlink": (
            canonical, {"shrinkwrap-link": True},
            "private release acquisition requires package-lock.json without npm-shrinkwrap.json",
        ),
        "reserved contract directory": (canonical, {"reserved": "directory"}, RESERVED_MESSAGE),
        "reserved contract symlink": (canonical, {"reserved": "symlink"}, RESERVED_MESSAGE),
    }
    failures = []
    for case, (packages, extras, message) in cases.items():
        try:
            check_lock_case(step, root, case, packages, extras, message, approved_urls)
        except AssertionError as error:
            failures.append(str(error))
    assert not failures, "release-artifact lock validation:\n" + "\n".join(failures)


def check_lock_case(step, root, case, packages, extras, message, approved_urls):
    workspace = root / case.replace(" ", "-").replace("/", "-")
    workspace.mkdir()
    (workspace / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": packages})
    )
    if "shrinkwrap" in extras:
        (workspace / "npm-shrinkwrap.json").write_text(
            json.dumps({"lockfileVersion": 3, "packages": extras["shrinkwrap"]})
        )
    if extras.get("shrinkwrap-link"):
        (workspace / "npm-shrinkwrap.json").symlink_to(workspace / "package-lock.json")
    if extras.get("reserved") == "directory":
        (workspace / RESERVED_PATH).mkdir()
    elif extras.get("reserved") == "symlink":
        (workspace / RESERVED_PATH).symlink_to(root)
    with tempfile.TemporaryDirectory(prefix="release-caller-lock-") as script_dir:
        script_path = Path(script_dir) / "step.sh"
        script_path.write_text(step["run"])
        result = subprocess.run(
            shell_command(step, script_path), cwd=workspace, text=True, capture_output=True,
            env=os.environ | {"APPROVED_INTERNAL_PACKAGES": "@verjson/fixture"},
        )
    output = result.stdout + result.stderr
    if message is None:
        assert result.returncode == 0, f"{case}: {output}"
        acquired, fetched, _ = token_fetches(workspace, root)
        assert acquired.returncode == 0, f"{case}: {acquired.stdout}{acquired.stderr}"
        assert fetched and fetched <= approved_urls, f"{case}: token fetched {sorted(fetched)}"
    else:
        assert result.returncode != 0 and message in output, f"{case}: accepted or wrong reason: {output}"
        # The script itself refuses or skips these URLs; validation must too.
        if extras.get("reserved") is None and not extras.get("shrinkwrap-link") and not extras.get("script-refuses"):
            _, fetched, _ = token_fetches(workspace, root)
            assert fetched - approved_urls, f"{case}: the attack reaches no unapproved download"


APPROVED = "@verjson/fixture"
APPROVED_URLS = {
    private_download("@verjson/fixture")["resolved"],
    # GitHub Packages records the owner's own spelling of the scope.
    private_download("@verJSON/fixture")["resolved"],
}
UNAPPROVED_MESSAGE = "is not an approved private package"


def verify_script_allowlist(root):
    """Given an allowlist, the script refuses any other private download before npm runs."""
    root.mkdir()
    base = {
        "": {"name": "fixture"},
        "node_modules/@verjson/fixture": private_download("@verjson/fixture"),
    }
    cases = {
        "approved only": (base, APPROVED, True),
        "owner-spelled approved scope": (
            base | {"node_modules/@verjson/fixture": private_download("@verJSON/fixture")}, APPROVED, True,
        ),
        "unapproved download": (
            base | {"node_modules/@verjson/secret": private_download("@verjson/secret")}, APPROVED, False,
        ),
        "unapproved root download": (
            base | {"": {"name": "fixture", **private_download("@verjson/secret")}}, APPROVED, False,
        ),
        "empty allowlist": (base, "", False),
        # A version segment of dots alone is a path GitHub Packages need not
        # resolve to the named package, so the script never sends it the token.
        "dot-only version segment": (
            base | {"node_modules/@verjson/fixture": private_download("@verjson/fixture") | {
                "resolved": "https://npm.pkg.github.com/download/@verjson/fixture/.../0123abcd",
            }}, APPROVED, "is not an exact private registry download URL",
        ),
    }
    for case, (packages, approved, allowed) in cases.items():
        workspace = root / case.replace(" ", "-")
        workspace.mkdir()
        (workspace / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": packages}))
        result, fetched, calls = token_fetches(workspace, root, approved=approved)
        output = result.stdout + result.stderr
        if allowed is True:
            assert result.returncode == 0, f"{case}: {output}"
            assert fetched and fetched <= APPROVED_URLS, f"{case}: {sorted(fetched)}"
        else:
            message = allowed if isinstance(allowed, str) else UNAPPROVED_MESSAGE
            assert result.returncode != 0 and message in output, f"{case}: accepted: {output}"
            assert calls == [], f"{case}: npm ran before the allowlist refusal: {calls}"
    # The quarantined .npmrc is $GITHUB_WORKSPACE's, so the lockfile must come
    # from that same tree: any other working directory is refused before npm.
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": base}))
    (elsewhere / ".npmrc").write_text("//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}\n")
    result, fetched, calls = token_fetches(elsewhere, root, approved=APPROVED, github_workspace=root / "approved-only")
    assert result.returncode != 0 and "must run from GITHUB_WORKSPACE" in result.stdout + result.stderr, (
        f"acquisition ran outside GITHUB_WORKSPACE: {result.stdout}{result.stderr}"
    )
    assert calls == [], f"npm ran outside GITHUB_WORKSPACE: {calls}"
    # The same tree through a symlinked path is the same workspace.
    linked = root / "linked-workspace"
    linked.symlink_to(root / "approved-only")
    result, fetched, _ = token_fetches(root / "approved-only", root, approved=APPROVED, github_workspace=linked)
    assert result.returncode == 0 and fetched <= APPROVED_URLS, result.stdout + result.stderr
    # Without an allowlist the script fetches every locked private download:
    # node-release and the verify jobs trust the caller's reviewed lockfile.
    workspace = root / "no-allowlist"
    workspace.mkdir()
    secret = private_download("@verjson/secret")
    (workspace / "package-lock.json").write_text(json.dumps(
        {"lockfileVersion": 3, "packages": base | {"node_modules/@verjson/secret": secret}}
    ))
    result, fetched, _ = token_fetches(workspace, root)
    assert result.returncode == 0 and secret["resolved"] in fetched, result.stdout + result.stderr


# Every kind of packages-map entry the acquisition script reads a resolved URL from.
ENTRY_KINDS = {
    "root": lambda entry: {"": {"name": "fixture", **entry}},
    "workspace member": lambda entry: {"packages/tool": {"name": "@verjson/tool", **entry}},
    "top-level dependency": lambda entry: {"node_modules/dep": entry},
    "scoped dependency": lambda entry: {"node_modules/@verjson/dep": entry},
    "nested dependency": lambda entry: {"node_modules/a/node_modules/@verjson/dep": entry},
    "workspace dependency": lambda entry: {"packages/tool/node_modules/dep": entry},
    "link": lambda entry: {"node_modules/linked": {**entry, "link": True}},
    "bundled": lambda entry: {"node_modules/a/node_modules/b": {**entry, "inBundle": True}},
    "outside node_modules": lambda entry: {"vendor/x": entry},
}
PAYLOADS = {
    "approved": private_download("@verjson/fixture"),
    "owner-spelled approved": private_download("@verJSON/fixture"),
    "unapproved": private_download("@verjson/secret"),
    "other scope": private_download("@acme/secret"),
    "aliased unapproved": {**private_download("@verjson/secret"), "name": "@verjson/secret"},
    "uppercase host": private_download("@verjson/secret", host="https://NPM.PKG.GITHUB.COM"),
    "full-width dot host": private_download("@verjson/secret", host="https://npm\uff0epkg.github.com"),
    "backslash authority": private_download("@verjson/secret", host="https://npm.pkg.github.com\\@evil.example"),
    "public": {
        "version": "1.0.0",
        "resolved": "https://registry.npmjs.org/zod/-/zod-1.0.0.tgz",
        "integrity": integrity_of(b"zod"),
    },
}


def verify_allowlist_sweep(workflow, root):
    """Sweep every entry kind, payload, and lockfile the script can read.

    For each generated tree: (1) the script given the allowlist fetches
    only approved downloads with the token, by construction and whatever
    the validators select; (2) whenever the release-artifact validation step
    accepts, the script without an allowlist also fetches only approved
    downloads, so the validators remain a sound second layer.
    """
    root.mkdir()
    step = private_lock_validation_step(workflow)
    base = {
        "": {"name": "fixture"},
        "node_modules/@verjson/fixture": private_download("@verjson/fixture"),
    }
    exposures = 0
    failures = []
    for kind, place in ENTRY_KINDS.items():
        for payload_name, payload in PAYLOADS.items():
            for lockfile in ("package-lock.json", "npm-shrinkwrap.json"):
                case = f"{kind} / {payload_name} / {lockfile}"
                workspace = root / f"case-{len(list(root.iterdir()))}"
                workspace.mkdir()
                packages = base | place(payload)
                (workspace / lockfile).write_text(json.dumps({"lockfileVersion": 3, "packages": packages}))
                if lockfile != "package-lock.json":
                    (workspace / "package-lock.json").write_text(
                        json.dumps({"lockfileVersion": 3, "packages": base})
                    )
                _, guarded, _ = token_fetches(workspace, root, approved=APPROVED)
                if not guarded <= APPROVED_URLS:
                    failures.append(f"{case}: the allowlisted script fetched {sorted(guarded - APPROVED_URLS)}")
                _, unguarded, _ = token_fetches(workspace, root)
                exposed = unguarded - APPROVED_URLS
                exposures += bool(exposed)
                with tempfile.TemporaryDirectory(prefix="release-caller-sweep-") as script_dir:
                    script_path = Path(script_dir) / "step.sh"
                    script_path.write_text(step["run"])
                    validated = subprocess.run(
                        shell_command(step, script_path), cwd=workspace, text=True, capture_output=True,
                        env=os.environ | {"APPROVED_INTERNAL_PACKAGES": APPROVED},
                    )
                if validated.returncode == 0 and exposed:
                    failures.append(f"{case}: validation accepted a tree whose token fetches include {sorted(exposed)}")
    assert not failures, "allowlist sweep:\n" + "\n".join(failures)
    # Without the allowlist, many generated trees reach an unapproved download,
    # so the two properties above are not vacuous.
    assert exposures >= len(ENTRY_KINDS) * 2, f"only {exposures} trees exercise an unapproved download"


PART_VARIABLE = "RELEASE_CALLER_ISOLATION_PART"
ACQUISITION_LABEL = "release-artifact acquire-private-dependencies"
CONTRACT_CHECKS = (
    *(f"reserved paths {label} verify" for label in VERIFY_MODES),
    "private lock validation",
    "script allowlist",
    "allowlist sweep",
)


def case_space():
    """Every case this test runs: contract checks need no npm; isolation cases run per npm major."""
    labels = [f"{label} verify" for label in VERIFY_MODES] + [ACQUISITION_LABEL]
    return {("contract", check) for check in CONTRACT_CHECKS} | {
        ("isolation", label, major) for label in labels for major in harness.SUPPORTED_NPM_RELEASES
    }


def parts():
    """Deterministic actions-ci parts: the npm-free contract checks, then one part per npm major."""
    cases = case_space()
    return {"contract": {case for case in cases if case[0] == "contract"}} | {
        f"npm{major}": {case for case in cases if case[0] == "isolation" and case[2] == major}
        for major in harness.SUPPORTED_NPM_RELEASES
    }


def selected_cases(part):
    """The cases a part runs; `all` runs everything; None for an unknown part."""
    return case_space() if part == "all" else parts().get(part)


def main():
    part = os.environ.get(PART_VARIABLE, "all")
    selected = selected_cases(part)
    if selected is None:
        print(f"unknown {PART_VARIABLE} {part!r}: expected all or one of {sorted(parts())}", file=sys.stderr)
        return 2
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True, capture_output=True, check=True,
    ).stdout.strip()
    workflows = {label: generate(arguments, sha) for label, arguments in VERIFY_MODES.items()}
    cases = {
        ("isolation", f"{label} verify", major): (verify_dependency_steps(workflow), True, VERIFY_REQUIRED_HOOKS)
        for label, workflow in workflows.items()
        for major in harness.SUPPORTED_NPM_RELEASES
    } | {
        # Lifecycle scripts never run there; the git dependency's preparation,
        # which the attacker registry observes, is what exercises the path.
        ("isolation", ACQUISITION_LABEL, major): (
            private_acquisition_job_steps(workflows["release-artifact"]), False, set(),
        )
        for major in harness.SUPPORTED_NPM_RELEASES
    }
    # Every part runs the recorder guard: the isolation assertions rely on it.
    with tempfile.TemporaryDirectory(prefix="release-caller-trace-") as temp:
        verify_token_tracing_is_reliable(Path(temp))
    ran = set()
    with tempfile.TemporaryDirectory(prefix="release-caller-reserved-") as temp:
        contract_checks = {
            f"reserved paths {label} verify": (
                lambda label=label, workflow=workflow: (
                    verify_reserved_contract_path(
                        f"{label} verify", verify_dependency_steps(workflow), sha=sha,
                        root=Path(temp) / label.replace(" ", "_").replace("/", "_"),
                    ),
                    verify_reserved_selection_path(
                        f"{label} verify", workflow, sha=sha,
                        root=Path(temp) / label.replace(" ", "_").replace("/", "_"),
                    ),
                )
            )
            for label, workflow in workflows.items()
        } | {
            "private lock validation": lambda: verify_private_lock_validation(
                workflows["release-artifact"], Path(temp) / "lock",
            ),
            "script allowlist": lambda: verify_script_allowlist(Path(temp) / "script-allowlist"),
            "allowlist sweep": lambda: verify_allowlist_sweep(workflows["release-artifact"], Path(temp) / "sweep"),
        }
        for kind, check in sorted(case for case in selected if case[0] == "contract"):
            if check.startswith("reserved paths "):
                (Path(temp) / check.removeprefix("reserved paths ").removesuffix(" verify")
                 .replace(" ", "_").replace("/", "_")).mkdir()
            contract_checks[check]()
            ran.add((kind, check))
    for major, (version, integrity) in sorted(harness.SUPPORTED_NPM_RELEASES.items()):
        major_cases = sorted(case for case in selected if case[0] == "isolation" and case[2] == major)
        if not major_cases:
            continue
        with tempfile.TemporaryDirectory(prefix=f"release-caller-npm{major}-") as temp:
            npm_bin = harness.provision_npm(version, integrity, Path(temp) / "npm")
            for case in major_cases:
                steps, setup_node_registry, required_hooks = cases[case]
                label = case[1]
                case_root = Path(temp) / label.replace(" ", "_").replace("/", "_")
                case_root.mkdir()
                verify_steps_isolate_credential(
                    label, steps, sha=sha, npm_bin=npm_bin, version=version, root=case_root,
                    setup_node_registry=setup_node_registry, required_hooks=required_hooks,
                )
                ran.add(case)
    assert ran == selected, f"unexercised cases: {sorted(selected - ran)}"
    isolated = sum(case[0] == "isolation" for case in ran)
    print(f"ok - part {part}: {len(ran) - isolated} contract checks and {isolated} generated "
          "dependency-installation cases isolate the package credential")
    return 0

if __name__ == "__main__":
    sys.exit(main())
