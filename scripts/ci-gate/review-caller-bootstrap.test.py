#!/usr/bin/env python3
"""Execute gate-rearm.yml's stale review caller bootstrap steps against stubs.

The bootstrap (ADR 0225, #1696) lets the trusted arm authorize the one pull
request a stale default-branch review caller cannot authorize: the byte-exact
generated update of that caller to a current canonical revision. Every case runs
the real named workflow steps; the stub `gh` serves fixture API responses.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/gate-rearm.yml"
GENERATOR = ROOT / "scripts/gen-ai-review-caller.sh"

TARGET_REPO = "Verjson/example"
API_REPO = "verJSON/example"
PR_NUMBER = "7"
CALLER = ".github/workflows/ai-review-merge.yml"
FLOOR = "65529002f4fecf2a5bb634047b07ead3ff0bdeaa"
STALE = "68b823b133dabc8d8e9f11f9ab5c179e78ae5692"
CONTRACT = "1111111111111111111111111111111111111111"
MAIN = "2222222222222222222222222222222222222222"
HEAD = "3333333333333333333333333333333333333333"
MAIN_TREE = "4444444444444444444444444444444444444444"
HEAD_TREE = "5555555555555555555555555555555555555555"
CANONICAL = "repos/Verjson/.github"

failures = 0


def report(ok: bool, name: str) -> None:
    global failures
    print(("ok   - " if ok else "FAIL - ") + name)
    if not ok:
        failures += 1


def generate_caller(sha: str) -> bytes:
    return subprocess.run(["bash", str(GENERATOR), sha], check=True, capture_output=True).stdout


def git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def arm_steps() -> dict[str, dict]:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["arm"]["steps"]
    return {step.get("id") or step.get("name"): step for step in steps}


class Consumer:
    """A consumer repository state: a protected main commit and one PR head."""

    def __init__(self) -> None:
        self.base = {CALLER: generate_caller(STALE), "README.md": b"# example\n"}
        self.head = dict(self.base)
        self.head[CALLER] = generate_caller(CONTRACT)
        self.modes: dict[tuple[str, str], str] = {}
        self.canonical = {
            f"{FLOOR}...{STALE}": "behind",
            f"{FLOOR}...{CONTRACT}": "ahead",
            f"{STALE}...{CONTRACT}": "ahead",
            f"{CONTRACT}...main": "ahead",
        }
        self.overrides: dict[str, object] = {}
        self.missing: set[str] = set()

    def tree(self, side: str, files: dict[str, bytes], sha: str) -> dict:
        entries = []
        for path, content in sorted(files.items()):
            entries.append({
                "path": path,
                "mode": self.modes.get((side, path), "100644"),
                "type": "blob",
                "sha": git_blob_sha(content),
                "size": len(content),
            })
        return {"sha": sha, "truncated": False, "tree": entries}

    def routes(self) -> dict[str, object]:
        changed = sorted(p for p in set(self.base) | set(self.head) if self.base.get(p) != self.head.get(p))
        files = []
        for path in changed:
            status = "added" if path not in self.base else "removed" if path not in self.head else "modified"
            files.append({"filename": path, "status": status})
        routes: dict[str, object] = {
            f"repos/{TARGET_REPO}/pulls/{PR_NUMBER}": {
                "state": "open",
                "changed_files": len(changed),
                "head": {"sha": HEAD, "repo": {"full_name": API_REPO}},
                "base": {"ref": "main", "repo": {"full_name": API_REPO}},
            },
            f"repos/{TARGET_REPO}/pulls/{PR_NUMBER}/files?per_page=100": files,
            f"repos/{TARGET_REPO}/branches/main": {"protected": True, "commit": {"sha": MAIN}},
            f"repos/{TARGET_REPO}/compare/{MAIN}...{HEAD}": {
                "status": "ahead", "ahead_by": 1, "behind_by": 0,
                "base_commit": {"sha": MAIN}, "merge_base_commit": {"sha": MAIN},
            },
            f"repos/{TARGET_REPO}/git/commits/{MAIN}": {"sha": MAIN, "tree": {"sha": MAIN_TREE}},
            f"repos/{TARGET_REPO}/git/commits/{HEAD}": {"sha": HEAD, "tree": {"sha": HEAD_TREE}},
            f"repos/{TARGET_REPO}/git/trees/{MAIN_TREE}?recursive=1": self.tree("base", self.base, MAIN_TREE),
            f"repos/{TARGET_REPO}/git/trees/{HEAD_TREE}?recursive=1": self.tree("head", self.head, HEAD_TREE),
        }
        for content in list(self.base.values()) + list(self.head.values()):
            sha = git_blob_sha(content)
            routes[f"repos/{TARGET_REPO}/git/blobs/{sha}"] = {
                "sha": sha, "size": len(content), "encoding": "base64",
                "content": base64.encodebytes(content).decode(),
            }
        for pair, status in self.canonical.items():
            base, head = pair.split("...")
            routes[f"{CANONICAL}/compare/{pair}"] = {
                "status": status,
                "ahead_by": 1 if status in ("ahead", "diverged") else 0,
                "behind_by": 1 if status in ("behind", "diverged") else 0,
                "base_commit": {"sha": base},
                "merge_base_commit": {"sha": base if status in ("ahead", "identical") else "9" * 40},
            }
        routes.update(self.overrides)
        for path in self.missing:
            routes.pop(path, None)
        return routes


GH_STUB = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_CALLS"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
routes = json.load(open(os.environ["GH_ROUTES"], encoding="utf-8"))
if args[:1] != ["api"]:
    sys.exit(2)
rest = args[1:]
if rest[:1] == ["--method"]:
    sys.exit(3)
path = rest[0]
if path not in routes:
    sys.stderr.write("stub: no route for " + path + "\n")
    sys.exit(1)
sys.stdout.write(json.dumps(routes[path]))
"""


class Harness:
    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "bin").mkdir()
        stub = self.tmp / "bin/gh"
        stub.write_text(GH_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.steps = arm_steps()

    def run_step(self, step_id: str, consumer: Consumer, extra_env: dict[str, str] | None = None):
        step = self.steps.get(step_id)
        if step is None:
            raise AssertionError(f"arm step {step_id!r} is missing")
        script = self.tmp / f"{step_id}.sh"
        script.write_text(step["run"], encoding="utf-8")
        routes = self.tmp / "routes.json"
        routes.write_text(json.dumps(consumer.routes()), encoding="utf-8")
        output = self.tmp / f"{step_id}.out"
        output.write_text("", encoding="utf-8")
        calls = self.tmp / "calls.jsonl"
        calls.write_text("", encoding="utf-8")
        env = {
            "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "RUNNER_TEMP": str(self.tmp),
            "GITHUB_OUTPUT": str(output),
            "GH_ROUTES": str(routes),
            "GH_CALLS": str(calls),
            "GH_TOKEN": "actions-token",
            "ACTIONS_TOKEN": "actions-token",
            "TARGET_REPO": TARGET_REPO,
            "PR_NUMBER": PR_NUMBER,
            "DEFAULT_BRANCH": "main",
            "EXPECTED_HEAD_SHA": HEAD,
            "CHECK_ID": "9001",
        }
        env.update(extra_env or {})
        result = subprocess.run(["bash", "-e", str(script)], env=env, capture_output=True, text=True)
        outputs = dict(
            line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines() if "=" in line
        )
        return result, outputs


def main() -> int:
    harness = Harness()

    result, outputs = harness.run_step("bootstrap", Consumer())
    report(
        result.returncode == 0
        and outputs.get("eligible") == "true"
        and outputs.get("stale_sha") == STALE
        and outputs.get("contract_sha") == CONTRACT,
        "a byte-exact generated stale caller update is classified as a bootstrap",
    )

    if failures:
        print(f"{failures} review caller bootstrap check(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
