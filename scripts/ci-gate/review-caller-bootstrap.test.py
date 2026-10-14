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
NONCE = "a" * 64
EXTERNAL_ID = f"ai-review:v1:{TARGET_REPO}:{PR_NUMBER}:{HEAD}:7001:1:{NONCE}"


def policy(authority: str | None) -> str:
    document = {"actor": "trusted-arm", "actor_permission": "automation", "budget_usd": "auto",
                "explicit_rereview": False, "fallback_budget_usd": "", "fallback_model": "",
                "model": "auto", "pricing_version": "anthropic-native-v1", "provider": "anthropic"}
    if authority is not None:
        document["authority"] = authority
    encoded = base64.urlsafe_b64encode(json.dumps(document, sort_keys=True).encode()).decode()
    return encoded.rstrip("=")

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
        routes[f"repos/{TARGET_REPO}/check-runs/9001"] = {
            "id": 9001, "name": "AI review authorization", "head_sha": HEAD,
            "external_id": EXTERNAL_ID, "app": {"id": 15368, "slug": "github-actions"},
            "status": "in_progress", "conclusion": None,
        }
        routes.update(self.overrides)
        for path in self.missing:
            routes.pop(path, None)
        return routes


GH_STUB = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_CALLS"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"token": os.environ.get("GH_TOKEN", ""), "args": args}) + "\n")
routes = json.load(open(os.environ["GH_ROUTES"], encoding="utf-8"))
state_path = os.environ["GH_STATE"]
state = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}
if args[:1] != ["api"]:
    sys.exit(2)
rest = args[1:]
method = "GET"
if rest[:1] == ["--method"]:
    method, rest = rest[1], rest[2:]
path, fields = rest[0], {}
index = 1
while index < len(rest):
    if rest[index] == "-f":
        key, _, value = rest[index + 1].partition("=")
        fields[key] = value
        index += 2
    else:
        index += 1
fail = os.environ.get("GH_FAIL", "")
if fail and fail in method + " " + path:
    sys.exit(1)
if method == "POST" and path.endswith("/reviews"):
    review = {"id": 555, "state": "APPROVED", "commit_id": fields.get("commit_id"),
              "user": {"login": os.environ.get("REVIEW_LOGIN", "verjson-ai-review[bot]")},
              "body": fields.get("body", "")}
    if fields.get("event") != "APPROVE":
        review["state"] = "COMMENTED"
    state["review"] = review
    json.dump(state, open(state_path, "w", encoding="utf-8"))
    print(json.dumps(review))
    sys.exit(0)
if method == "GET" and path.endswith("/reviews/555") and "review" in state:
    print(json.dumps(state["review"]))
    sys.exit(0)
if method == "PATCH" and "/check-runs/" in path:
    check = dict(state.get("check") or routes.get(path, {}))
    check.update({"status": fields.get("status"), "conclusion": fields.get("conclusion"),
                  "output": {"title": fields.get("output[title]"), "summary": fields.get("output[summary]")}})
    state["check"] = check
    json.dump(state, open(state_path, "w", encoding="utf-8"))
    print(json.dumps(check))
    sys.exit(0)
if method != "GET" or path not in routes:
    sys.stderr.write("stub: no route for " + method + " " + path + "\n")
    sys.exit(1)
if "/check-runs/" in path and "check" in state:
    print(json.dumps(state["check"]))
    sys.exit(0)
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

    def reset_state(self) -> None:
        (self.tmp / "state.json").unlink(missing_ok=True)

    def recorded(self) -> list[dict]:
        return [json.loads(line) for line in self.calls.read_text(encoding="utf-8").splitlines()]

    def complete(self, consumer: Consumer, authority: str | None = "ai-merge", **overrides: str):
        self.reset_state()
        _, outputs = self.run_step("bootstrap", consumer)
        env = {
            "EXPECTED_EXTERNAL_ID": EXTERNAL_ID,
            "REVIEW_POLICY": policy(authority),
            "BOOTSTRAP_ELIGIBLE": outputs.get("eligible", ""),
            "STALE_SHA": outputs.get("stale_sha", ""),
            "CONTRACT_SHA": outputs.get("contract_sha", ""),
            "APP_TOKEN": "app-token",
            "APP_SLUG": "verjson-ai-review",
            "MINTED_APP_SLUG": "verjson-ai-review",
            "INSTALLATION_ID": "42",
            "GITHUB_RUN_ID": "7001",
        }
        env.update(overrides)
        result, completion = self.run_step("bootstrap-complete", consumer, env)
        return result, completion, self.recorded()

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
        self.calls = calls
        env = {
            "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "RUNNER_TEMP": str(self.tmp),
            "GITHUB_OUTPUT": str(output),
            "GH_ROUTES": str(routes),
            "GH_CALLS": str(calls),
            "GH_STATE": str(self.tmp / "state.json"),
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

    steps = harness.steps
    dispatch = steps.get("dispatch", {})
    report(
        "steps.bootstrap.outputs.eligible != 'true'" in dispatch.get("if", ""),
        "an eligible bootstrap never dispatches the stale default-branch review",
    )

    result, _, calls = harness.complete(Consumer())
    approvals = [c for c in calls if c["args"][:4] == ["api", "--method", "POST", f"repos/{TARGET_REPO}/pulls/{PR_NUMBER}/reviews"]]
    patches = [c for c in calls if c["args"][:4] == ["api", "--method", "PATCH", f"repos/{TARGET_REPO}/check-runs/9001"]]
    report(
        result.returncode == 0
        and len(approvals) == 1 and approvals[0]["token"] == "app-token"
        and "event=APPROVE" in approvals[0]["args"] and f"commit_id={HEAD}" in approvals[0]["args"]
        and any(f"ai-review-bootstrap:{STALE}:{CONTRACT}" in arg for arg in approvals[0]["args"])
        and len(patches) == 1 and patches[0]["token"] == "actions-token"
        and "conclusion=success" in patches[0]["args"]
        and any(f"ai-review-authorized:v1:9001:{HEAD}:ai-merge" in arg for arg in patches[0]["args"]),
        "ai-merge authority persists an exact-head App approval and completes the check as success",
    )

    if failures:
        print(f"{failures} review caller bootstrap check(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
