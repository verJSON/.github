"""Adversarial contract for live, report-only caller-pin discovery (ADR 0135)."""

from __future__ import annotations

import base64
import copy
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/capability-floor-discovery.py"
WORKFLOW = ROOT / ".github/workflows/capability-floor-observe.yml"
ACTION = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
CLIENT_ID = "${{ vars.RENOVATE_COMPATIBILITY_CLIENT_ID }}"
PRIVATE_KEY = "${{ secrets.RENOVATE_COMPATIBILITY_APP_PRIVATE_KEY }}"
TOKEN = "${{ steps.observer-app-token.outputs.token }}"
SCOPE = "${{ steps.scope.outputs.repositories }}"
PIN = "a" * 40
SOURCE_SHA = "b" * 40
BLOB_SHA = "c" * 40


def workflow_problems(document: dict, raw: str) -> list[str]:
    problems: list[str] = []
    job = document["jobs"]["observe"]
    steps = job["steps"]
    mint = next(step for step in steps if step.get("id") == "observer-app-token")
    expected_mint = {
        "client-id": CLIENT_ID,
        "private-key": PRIVATE_KEY,
        "owner": "Verjson",
        "repositories": SCOPE,
        "permission-contents": "read",
    }
    if document.get("permissions") != {"contents": "read"}:
        problems.append("workflow GITHUB_TOKEN is not contents-read only")
    if "vars.CI_LANE_PRIVILEGED" not in str(job.get("runs-on")):
        problems.append("secret-bearing observer is not on the privileged lane")
    if mint.get("uses") != ACTION or mint.get("with") != expected_mint:
        problems.append("App mint is not exact-scope contents-read only")
    if not re.search(
        r'\[\[ -z "\$RENOVATE_COMPATIBILITY_CLIENT_ID" \|\| '
        r'"\$RENOVATE_COMPATIBILITY_CLIENT_ID" =~ \^\[0-9\]\+\$ \]\]',
        raw,
    ):
        problems.append("missing or numeric App client IDs do not fail closed")
    if raw.count(PRIVATE_KEY) != 1:
        problems.append("App private key escaped the mint step")
    token_consumers = [
        step.get("name") for step in steps if (step.get("env") or {}).get("GH_TOKEN") == TOKEN
    ]
    if token_consumers != ["Discover immutable caller pins"]:
        problems.append("App token escaped the discovery step")
    scope_step = next(step for step in steps if step.get("id") == "scope")
    if "capability-floor-discovery.py --print-repositories" not in scope_step.get("run", ""):
        problems.append("repository scope is not derived from the reviewed allowlist")
    checkout = next(step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@"))
    if (checkout.get("with") or {}).get("persist-credentials") is not False:
        problems.append("checkout persists a credential")
    forbidden = (
        "ORG_ADMIN_TOKEN",
        "RENOVATE_COMPATIBILITY_PAT",
        "permission-contents: write",
        "permission-issues",
        "permission-pull-requests",
        "gh pr ",
        "gh issue ",
        "repository_dispatch",
    )
    if any(value in raw for value in forbidden):
        problems.append("workflow contains a write or broad credential path")
    if "workflow_dispatch: {}" not in raw or "schedule:" not in raw:
        problems.append("workflow is not both scheduled and manually dispatchable")
    if "capability-floor-report.json" not in raw or "GITHUB_STEP_SUMMARY" not in raw:
        problems.append("report artifact or workflow summary evidence is absent")
    return problems


def assert_mutation_rejected(label: str, document: dict, raw: str) -> None:
    if not workflow_problems(document, raw):
        raise AssertionError(f"workflow mutation survived: {label}")


def allowlist(path: pathlib.Path, repository: str = "Verjson/example", workflow: str = "gate-rearm.yml") -> pathlib.Path:
    payload = {
        "schema_version": 1,
        "consumers": [
            {
                "repository": repository,
                "callers": [
                    {
                        "path": ".github/workflows/gate-rearm.yml",
                        "generator": "scripts/gen-gate-rearm-caller.sh",
                        "canonical_workflows": [workflow],
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def run_discovery(tmp: pathlib.Path, caller: str, *, repository: str = "Verjson/example", mode: str = "ok"):
    config = allowlist(tmp / "allowlist.json", repository)
    env = os.environ.copy()
    env["PATH"] = f"{tmp}{os.pathsep}{env['PATH']}"
    env["CALLER_TEXT_B64"] = base64.b64encode(caller.encode()).decode()
    env["STUB_MODE"] = mode
    for output in ("pins.json", "receipts.json"):
        (tmp / output).unlink(missing_ok=True)
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--allowlist",
            str(config),
            "--pins-output",
            str(tmp / "pins.json"),
            "--receipts-output",
            str(tmp / "receipts.json"),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> None:
    raw = WORKFLOW.read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    problems = workflow_problems(document, raw)
    assert not problems, problems

    mutant = copy.deepcopy(document)
    next(step for step in mutant["jobs"]["observe"]["steps"] if step.get("id") == "observer-app-token")["with"]["permission-pull-requests"] = "write"
    assert_mutation_rejected("write permission", mutant, raw)
    mutant = copy.deepcopy(document)
    next(step for step in mutant["jobs"]["observe"]["steps"] if step.get("id") == "observer-app-token")["with"]["repositories"] = "*"
    assert_mutation_rejected("repository widening", mutant, raw)
    mutant = copy.deepcopy(document)
    next(step for step in mutant["jobs"]["observe"]["steps"] if step.get("id") == "observer-app-token")["uses"] = "actions/create-github-app-token@v3"
    assert_mutation_rejected("mutable token action", mutant, raw)
    mutant = copy.deepcopy(document)
    next(step for step in mutant["jobs"]["observe"]["steps"] if step.get("id") == "observer-app-token")["with"].pop("private-key")
    assert_mutation_rejected("missing private key", mutant, raw)

    with tempfile.TemporaryDirectory() as directory:
        tmp = pathlib.Path(directory)
        stub = tmp / "gh"
        stub.write_text(
            """#!/usr/bin/env python3
import json, os, sys
arguments = sys.argv[1:]
endpoint = arguments[arguments.index('GET') + 1]
fields = dict(arguments[index + 1].split('=', 1) for index, value in enumerate(arguments) if value == '-f')
mode = os.environ.get('STUB_MODE', 'ok')
def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)
def require_source_ref():
    if fields.get('ref') != 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb':
        fail('contents read is not bound to the resolved source commit')
if mode == 'api-failure':
    fail('denied')
if endpoint == 'repos/Verjson/example':
    if mode == 'repo-404':
        fail('gh: Not Found (HTTP 404)')
    print(json.dumps({'default_branch': 'main'}))
elif endpoint == 'repos/Verjson/example/commits/main':
    print(json.dumps({'sha': 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'}))
elif endpoint == 'repos/Verjson/example/contents/.github/workflows':
    require_source_ref()
    if mode == 'listing-404':
        fail('gh: Not Found (HTTP 404)')
    if mode == 'listing-403':
        fail('gh: Resource not accessible by integration (HTTP 403)')
    if mode == 'listing-500':
        fail('gh: Server Error (HTTP 500)')
    if mode == 'listing-malformed-json':
        print('[{"name": ')
        raise SystemExit(0)
    if mode == 'listing-object':
        print(json.dumps({'type': 'file', 'path': '.github/workflows'}))
        raise SystemExit(0)
    entries = [{'name': 'ci.yml', 'path': '.github/workflows/ci.yml', 'type': 'file'}]
    if mode == 'listing-at-api-limit':
        entries = [{'name': f'w{index}.yml', 'path': f'.github/workflows/w{index}.yml', 'type': 'file'} for index in range(1000)]
    elif mode == 'listing-entry-without-path':
        entries.append({'name': 'gate-rearm.yml', 'type': 'file'})
    elif mode != 'caller-absent':
        entries.append({'name': 'gate-rearm.yml', 'path': '.github/workflows/gate-rearm.yml', 'type': 'file'})
    print(json.dumps(entries))
elif endpoint == 'repos/Verjson/example/contents/.github/workflows/gate-rearm.yml':
    require_source_ref()
    if mode in ('caller-absent', 'caller-404'):
        fail('gh: Not Found (HTTP 404)')
    if mode == 'caller-403':
        fail('gh: Resource not accessible by integration (HTTP 403)')
    if mode == 'caller-500':
        fail('gh: Server Error (HTTP 500)')
    print(json.dumps({'type': 'file', 'encoding': 'base64', 'sha': 'cccccccccccccccccccccccccccccccccccccccc', 'content': os.environ['CALLER_TEXT_B64']}))
else:
    fail('unexpected endpoint: ' + endpoint)
""",
            encoding="utf-8",
        )
        stub.chmod(0o755)

        valid = f"jobs:\n  arm:\n    uses: Verjson/.github/.github/workflows/gate-rearm.yml@{PIN}\n"
        result = run_discovery(tmp, valid)
        assert result.returncode == 0, result.stderr
        pins = json.loads((tmp / "pins.json").read_text())
        receipts = json.loads((tmp / "receipts.json").read_text())
        assert pins == [{"generator": "scripts/gen-gate-rearm-caller.sh", "pinned_sha": PIN, "repo": "Verjson/example"}]
        assert receipts["mode"] == "observe-only"
        assert receipts["receipts"][0]["source_sha"] == SOURCE_SHA
        assert receipts["receipts"][0]["blob_sha"] == BLOB_SHA

        # GitHub owner names are case-insensitive; current generators emit `verJSON/` (#1779).
        renamed = run_discovery(tmp, f"jobs:\n  arm:\n    uses: verJSON/.github/.github/workflows/gate-rearm.yml@{PIN}\n")
        assert renamed.returncode == 0, f"display-case owner was rejected: {renamed.stderr}"
        assert json.loads((tmp / "pins.json").read_text())[0]["pinned_sha"] == PIN

        scope_config = allowlist(tmp / "scope.json")
        scope = subprocess.run(
            [sys.executable, str(SCRIPT), "--allowlist", str(scope_config), "--print-repositories"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert scope.returncode == 0 and scope.stdout == "example\n"

        for label, caller in (
            ("mutable pin", "uses: Verjson/.github/.github/workflows/gate-rearm.yml@main\n"),
            ("uppercase pin", f"uses: Verjson/.github/.github/workflows/gate-rearm.yml@{PIN.upper()}\n"),
            ("wrong canonical target", f"uses: Verjson/.github/.github/workflows/ai-review-merge.yml@{PIN}\n"),
            ("mixed duplicate references", valid + f"  retry:\n    uses: Verjson/.github/.github/workflows/gate-rearm.yml@{'d' * 40}\n"),
            ("missing reference", "jobs: {}\n"),
            (
                "block scalar decoy",
                f"jobs:\n  decoy:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: |\n          uses: Verjson/.github/.github/workflows/gate-rearm.yml@{PIN}\n",
            ),
            (
                "step-level uses decoy",
                f"jobs:\n  decoy:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: Verjson/.github/.github/workflows/gate-rearm.yml@{PIN}\n",
            ),
            (
                "real target plus block scalar decoy",
                valid
                + f"  decoy:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: |\n          Verjson/.github/.github/workflows/gate-rearm.yml@{PIN}\n",
            ),
            (
                "real target plus mixed input decoy",
                f"jobs:\n  arm:\n    uses: Verjson/.github/.github/workflows/gate-rearm.yml@{PIN}\n    with:\n      decoy: Verjson/.github/.github/workflows/gate-rearm.yml@{PIN}\n",
            ),
            (
                "ambiguous duplicate real target",
                valid + f"  retry:\n    uses: Verjson/.github/.github/workflows/gate-rearm.yml@{PIN}\n",
            ),
            (
                "duplicate exact mapping key",
                f"jobs:\n  arm:\n    uses: Verjson/.github/.github/workflows/gate-rearm.yml@{PIN}\njobs: {{}}\n",
            ),
            (
                "duplicate quoted-equivalent mapping key",
                f"jobs:\n  arm:\n    uses: Verjson/.github/.github/workflows/gate-rearm.yml@{PIN}\n\"jobs\": {{}}\n",
            ),
            (
                "duplicate YAML-equivalent boolean key",
                f"on: pull_request\ntrue: push\njobs:\n  arm:\n    uses: Verjson/.github/.github/workflows/gate-rearm.yml@{PIN}\n",
            ),
            (
                "real target plus display-case block scalar decoy",
                valid
                + f"  decoy:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: |\n          verJSON/.github/.github/workflows/gate-rearm.yml@{PIN}\n",
            ),
            (
                "real target plus display-case step-level decoy",
                valid + f"  decoy:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: VERJSON/.github/.github/workflows/gate-rearm.yml@{PIN}\n",
            ),
            (
                "real target plus non-ASCII case-folding step-level decoy",
                valid + f"  decoy:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: verj\u017fon/.github/.github/workflows/gate-rearm.yml@{PIN}\n",
            ),
            ("lookalike owner", f"jobs:\n  arm:\n    uses: xverjson/.github/.github/workflows/gate-rearm.yml@{PIN}\n"),
            ("non-ASCII case-folding owner", f"jobs:\n  arm:\n    uses: verj\u017fon/.github/.github/workflows/gate-rearm.yml@{PIN}\n"),
            (
                "malformed YAML",
                f"jobs:\n  arm: [\n    uses: Verjson/.github/.github/workflows/gate-rearm.yml@{PIN}\n",
            ),
        ):
            rejected = run_discovery(tmp, caller)
            assert rejected.returncode == 2, f"{label} survived: {rejected.stderr}"

        api_failure = run_discovery(tmp, valid, mode="api-failure")
        assert api_failure.returncode == 2 and "GitHub API read failed" in api_failure.stderr

        absent = run_discovery(tmp, valid, mode="caller-absent")
        assert absent.returncode == 0, f"a provably absent caller aborted discovery: {absent.stderr}"
        assert json.loads((tmp / "pins.json").read_text()) == []
        absent_receipts = json.loads((tmp / "receipts.json").read_text())
        assert absent_receipts["receipts"] == []
        assert absent_receipts["callers_absent"] == 1
        assert absent_receipts["absent_callers"] == [
            {
                "repository": "Verjson/example",
                "default_branch": "main",
                "source_sha": SOURCE_SHA,
                "path": ".github/workflows/gate-rearm.yml",
                "generator": "scripts/gen-gate-rearm-caller.sh",
                "canonical_workflows": ["gate-rearm.yml"],
                "finding": "allowlisted-caller-absent",
            }
        ], absent_receipts
        assert "::warning" in absent.stdout and "Verjson/example" in absent.stdout and "non-conforming" in absent.stdout

        # Absence is recorded only from a successful listing; every failed read stays fatal.
        for mode, diagnostic in (
            ("repo-404", "GitHub API read failed for repos/Verjson/example:"),
            ("listing-404", "GitHub API read failed for repos/Verjson/example/contents/.github/workflows:"),
            ("listing-403", "GitHub API read failed for repos/Verjson/example/contents/.github/workflows:"),
            ("listing-500", "GitHub API read failed for repos/Verjson/example/contents/.github/workflows:"),
            ("listing-malformed-json", "GitHub API returned malformed JSON for repos/Verjson/example/contents/.github/workflows:"),
            ("listing-object", "workflows directory listing is malformed for Verjson/example"),
            ("listing-entry-without-path", "workflows directory listing entry is malformed for Verjson/example"),
            ("listing-at-api-limit", "workflows directory listing may be truncated for Verjson/example"),
            ("caller-404", "GitHub API read failed for repos/Verjson/example/contents/.github/workflows/gate-rearm.yml:"),
            ("caller-403", "GitHub API read failed for repos/Verjson/example/contents/.github/workflows/gate-rearm.yml:"),
            ("caller-500", "GitHub API read failed for repos/Verjson/example/contents/.github/workflows/gate-rearm.yml:"),
        ):
            failed = run_discovery(tmp, valid, mode=mode)
            assert failed.returncode == 2, f"{mode} did not fail closed: {failed.returncode} {failed.stderr}"
            assert diagnostic in failed.stderr, f"{mode} failed for the wrong reason: {failed.stderr}"
            assert not (tmp / "pins.json").exists() and not (tmp / "receipts.json").exists(), f"{mode} wrote a receipt"

        invalid_config = allowlist(tmp / "invalid-owner.json", "attacker/example")
        invalid = subprocess.run(
            [sys.executable, str(SCRIPT), "--allowlist", str(invalid_config), "--print-repositories"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert invalid.returncode == 2 and "outside the Verjson allowlist boundary" in invalid.stderr

    print("OK: capability-floor-discovery.test.py")


if __name__ == "__main__":
    main()
