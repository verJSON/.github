#!/usr/bin/env python3
"""Execute gate-rearm.yml's stale review caller bootstrap steps against stubs.

The bootstrap (ADR 0227, #1696) lets the trusted arm authorize the one pull
request a stale default-branch review caller cannot authorize: the byte-exact
generated update of that caller to a current canonical revision. Every case runs
the real named workflow steps; the stub `gh` serves fixture API responses.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/gate-rearm.yml"
GENERATOR = ROOT / "scripts/gen-ai-review-caller.sh"
FRAGMENT_GENERATOR = ROOT / "scripts/gen-ai-review-caller-bootstrap-fragment.sh"

TARGET_REPO = "Verjson/example"
API_REPO = "verJSON/example"
PR_NUMBER = "7"
CALLER = ".github/workflows/ai-review-merge.yml"
FLOOR = "65529002f4fecf2a5bb634047b07ead3ff0bdeaa"
MINIMUM_SAFE = "12098a0e495317699131e97f1b2e136c5297ccad"
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


def report(ok: bool, name: str, result: subprocess.CompletedProcess | None = None) -> None:
    global failures
    print(("ok   - " if ok else "FAIL - ") + name)
    if not ok:
        failures += 1
        if result is not None:
            # JSON-escaped so step output (which quotes untrusted paths) cannot emit commands.
            tail = (result.stdout + result.stderr)[-600:]
            print(f"       exit={result.returncode} output-tail={json.dumps(tail)}")


def generate_caller(sha: str) -> bytes:
    return subprocess.run(["bash", str(GENERATOR), sha], check=True, capture_output=True).stdout


def digest(sha: str) -> str:
    return hashlib.sha256(generate_caller(sha)).hexdigest()


def mirror(sha: str) -> bytes:
    return (f"const reviewCallerContractSha = '{sha}';\n"
            f"const reviewCallerDigest = '{digest(sha)}';\n").encode()


def fragment(date: str, identity: str, stale: str = STALE, contract: str = CONTRACT) -> tuple[str, bytes]:
    content = subprocess.run(["bash", str(FRAGMENT_GENERATOR), date, identity, stale, contract],
                             check=True, capture_output=True).stdout
    return f"NEXT/{date}-issue-{identity}-ai-review-caller-bootstrap.md", content


INJECTED_PATH = "src/x\n::warning title=pwned::owned.ts"


def live_commands(text: str) -> list[str]:
    """Workflow-command lines the runner would execute: outside every stop-commands region."""
    live, token = [], None
    for line in text.splitlines():
        if token is None:
            match = re.fullmatch(r"::stop-commands::([0-9a-f]{32})", line)
            if match:
                token = match[1]
            elif line.lstrip().startswith("::"):
                live.append(line)
        elif line == f"::{token}::":
            token = None
    if token is not None:
        live.append("<unterminated stop-commands region>")
    return live


def git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def arm_steps(text: str | None = None) -> dict[str, dict]:
    steps = yaml.safe_load(text if text is not None else WORKFLOW.read_text(encoding="utf-8"))["jobs"]["arm"]["steps"]
    return {step.get("id") or step.get("name"): step for step in steps}


class Consumer:
    """A consumer repository state: a protected main commit and one PR head."""

    def __init__(self) -> None:
        self.base = {CALLER: generate_caller(STALE), "README.md": b"# example\n"}
        self.head = dict(self.base)
        self.head[CALLER] = generate_caller(CONTRACT)
        self.modes: dict[tuple[str, str], str] = {}
        self.types: dict[tuple[str, str], str] = {}
        self.canonical = {
            f"{FLOOR}...{STALE}": "behind",
            f"{MINIMUM_SAFE}...{CONTRACT}": "ahead",
            f"{STALE}...{CONTRACT}": "ahead",
            f"{CONTRACT}...main": "ahead",
        }
        self.overrides: dict[str, object] = {}
        self.missing: set[str] = set()
        self.tree_edits: dict[str, object] = {}

    def tree(self, side: str, files: dict[str, bytes], sha: str) -> dict:
        entries = []
        for path, content in sorted(files.items()):
            entries.append({
                "path": path,
                "mode": self.modes.get((side, path), "100644"),
                "type": self.types.get((side, path), "blob"),
                "sha": git_blob_sha(content),
                "size": len(content),
            })
        listing = {"sha": sha, "truncated": False, "tree": entries}
        if side in self.tree_edits:
            self.tree_edits[side](listing)
        return listing

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
if isinstance(routes[path], dict) and "__raw__" in routes[path]:
    sys.stdout.write(routes[path]["__raw__"])
    sys.exit(0)
sys.stdout.write(json.dumps(routes[path]))
"""


STEP_ENVIRONMENT_ALLOWLIST = frozenset({"PATH", "LD_LIBRARY_PATH", "LANG", "TMPDIR", "pythonLocation"})


class Harness:
    def __init__(self, workflow_text: str | None = None) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "bin").mkdir()
        stub = self.tmp / "bin/gh"
        stub.write_text(GH_STUB, encoding="utf-8")
        stub.chmod(0o755)
        self.steps = arm_steps(workflow_text)
        self.path_prefix = ""

    def reset_state(self) -> None:
        (self.tmp / "state.json").unlink(missing_ok=True)

    def recorded(self) -> list[dict]:
        return [json.loads(line) for line in self.calls.read_text(encoding="utf-8").splitlines()]

    def eligible(self, consumer: Consumer) -> bool:
        result, outputs = self.run_step("bootstrap", consumer)
        self.last = result
        if result.returncode != 0:
            raise AssertionError(f"classification step failed: {result.stderr}")
        return outputs.get("eligible") == "true"

    def complete(self, consumer: Consumer, authority: str | None = "ai-merge", later: Consumer | None = None,
                 **overrides: str):
        self.reset_state()
        _, outputs = self.run_step("bootstrap", consumer)
        consumer = later or consumer
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
        # Allowlist, never a denylist: BASH_ENV, ENV, or an exported BASH_FUNC_gh%% would
        # shadow the stubs. PATH locates the tools the step runs (bash, jq, openssl, python3).
        # LD_LIBRARY_PATH and pythonLocation keep setup-python's shared-libpython build
        # loadable (actions-ci run 38495189807). LANG/LC_* keep text handling identical to
        # the job. TMPDIR keeps mktemp on the runner's temporary filesystem. The harness
        # sets its own HOME, RUNNER_TEMP, and fake tokens below.
        env = {key: value for key, value in os.environ.items()
               if key in STEP_ENVIRONMENT_ALLOWLIST or key.startswith("LC_")}
        env |= {
            "PATH": f"{self.path_prefix}{self.tmp / 'bin'}:{os.environ['PATH']}",
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
            "GITHUB_SERVER_URL": "https://github.com",
        }
        env.update(extra_env or {})
        result = subprocess.run(["bash", "-e", str(script)], env=env, capture_output=True, text=True)
        outputs = dict(
            line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines() if "=" in line
        )
        return result, outputs


def mutated(old: str, new: str) -> str | None:
    text = WORKFLOW.read_text(encoding="utf-8")
    return text.replace(old, new) if text.count(old) == 1 else None


def tamper(content: bytes) -> bytes:
    return content + b"# hand edited\n"


def with_files(**files: bytes) -> Consumer:
    consumer = Consumer()
    for path, (before, after) in files.items():
        if before is not None:
            consumer.base[path] = before
        if after is not None:
            consumer.head[path] = after
    return consumer


def compare_override(consumer: Consumer, pair: str, status: str, merge_base: str | None = None,
                     base_commit: str | None = None) -> Consumer:
    base, _ = pair.split("...")
    consumer.overrides[f"{CANONICAL}/compare/{pair}"] = {
        "status": status, "ahead_by": 1, "behind_by": 1 if status in ("behind", "diverged") else 0,
        "base_commit": {"sha": base_commit or base}, "merge_base_commit": {"sha": merge_base or base},
    }
    return consumer


def pull_override(consumer: Consumer, **fields: object) -> Consumer:
    pull = consumer.routes()[f"repos/{TARGET_REPO}/pulls/{PR_NUMBER}"]
    for key, value in fields.items():
        target = pull
        *parents, leaf = key.split("__")
        for parent in parents:
            target = target[parent]
        target[leaf] = value
    consumer.overrides[f"repos/{TARGET_REPO}/pulls/{PR_NUMBER}"] = pull
    return consumer


def ineligible_cases() -> list[tuple[str, Consumer, tuple[str, str] | None]]:
    """(behavior, consumer state, guard mutation that must make the case pass wrongly)."""
    cases: list[tuple[str, Consumer, tuple[str, str] | None]] = []

    def add(name, consumer, mutation=None):
        cases.append((name, consumer, mutation))

    add("a closed pull request is not a bootstrap", pull_override(Consumer(), state="closed"),
        ('require(pull.get("state") == "open",', 'require(True,'))
    add("a moved head is not a bootstrap", pull_override(Consumer(), head__sha="6" * 40),
        ('require(pull["head"]["sha"] == head,', 'require(True,'))
    add("a pull request into another branch is not a bootstrap", pull_override(Consumer(), base__ref="release"),
        ('require(pull["base"]["ref"] == branch,', 'require(True,'))
    add("a fork pull request is not a bootstrap",
        pull_override(Consumer(), head__repo={"full_name": "attacker/example"}),
        ('require(same_repository(pull["head"]["repo"]["full_name"], repo)', 'require(True or same_repository(pull["head"]["repo"]["full_name"], repo)'))
    large = Consumer()
    for index in range(32):
        large.base[f"tests/pins-{index}.test.ts"] = mirror(STALE)
        large.head[f"tests/pins-{index}.test.ts"] = mirror(CONTRACT)
    add("a pull request over the path budget is not a bootstrap", large,
        ("0 < changed_files <= MAX_PATHS,", "0 < changed_files,"))
    partial = Consumer()
    partial.overrides[f"repos/{TARGET_REPO}/pulls/{PR_NUMBER}/files?per_page=100"] = []
    add("an incomplete pull request file list is not a bootstrap", partial, None)
    repeated = Consumer()
    repeated.overrides[f"repos/{TARGET_REPO}/pulls/{PR_NUMBER}/files?per_page=100"] = [
        {"filename": CALLER, "status": "modified"}, {"filename": CALLER, "status": "modified"}]
    pull_override(repeated, changed_files=2)
    add("a file list repeating a path is not a bootstrap", repeated,
        ('require(len(names) == len(listed), "pull request file list repeats a path")',
         'require(True, "pull request file list repeats a path")'))
    hidden = with_files(**{"tests/pins.test.ts": (mirror(STALE), mirror(CONTRACT))})
    hidden.overrides[f"repos/{TARGET_REPO}/pulls/{PR_NUMBER}/files?per_page=100"] = [
        {"filename": CALLER, "status": "modified"}]
    pull_override(hidden, changed_files=1)
    add("a file list that disagrees with the exact trees is not a bootstrap", hidden,
        ('require(changed == names,', 'require(True,'))
    unprotected = Consumer()
    unprotected.overrides[f"repos/{TARGET_REPO}/branches/main"] = {"protected": False, "commit": {"sha": MAIN}}
    add("an unprotected default branch admits no bootstrap", unprotected,
        ('require(protected.get("protected") is True,', 'require(True,'))
    behind = Consumer()
    behind.overrides[f"repos/{TARGET_REPO}/compare/{MAIN}...{HEAD}"] = {
        "status": "diverged", "ahead_by": 1, "behind_by": 2,
        "base_commit": {"sha": MAIN}, "merge_base_commit": {"sha": "7" * 40}}
    add("a head that does not descend from the protected tip is not a bootstrap", behind,
        ('require(comparison.get("status") == "ahead" and comparison.get("behind_by") == 0', 'require(True or comparison.get("status") == "ahead" and comparison.get("behind_by") == 0'))
    truncated = Consumer()
    truncated.tree_edits["head"] = lambda listing: listing.update(truncated=True)
    add("a truncated tree listing is not a bootstrap", truncated,
        ('require(listing.get("truncated") is False,', 'require(True,'))
    duplicate = Consumer()
    forged = generate_caller(CONTRACT) + b"  extra: true\n"
    duplicate.overrides[f"repos/{TARGET_REPO}/git/blobs/{git_blob_sha(forged)}"] = {
        "sha": git_blob_sha(forged), "size": len(forged), "encoding": "base64",
        "content": base64.encodebytes(forged).decode()}
    duplicate.tree_edits["head"] = lambda listing: listing["tree"].insert(0, {
        "path": "README.md", "mode": "100644", "type": "blob", "sha": git_blob_sha(forged)})
    add("a tree listing that repeats a path is not a bootstrap", duplicate,
        ('require(entry["path"] not in entries,', 'require(True,'))
    substituted = Consumer()
    substitute_sha = "f" * 40
    substituted.overrides[f"repos/{TARGET_REPO}/git/blobs/{substitute_sha}"] = {
        "sha": substitute_sha, "size": len(generate_caller(CONTRACT)), "encoding": "base64",
        "content": base64.encodebytes(generate_caller(CONTRACT)).decode()}

    def point_caller_at_substitute(listing):
        for entry in listing["tree"]:
            if entry["path"] == CALLER:
                entry["sha"] = substitute_sha
    substituted.tree_edits["head"] = point_caller_at_substitute
    add("blob bytes that do not hash to the tree's object ID are rejected", substituted,
        ('require(len(content) == size and git_blob_sha(content) == sha,', 'require(len(content) == size,'))
    add("a hand-edited default-branch caller is not a bootstrap",
        with_files(**{CALLER: (tamper(generate_caller(STALE)), generate_caller(CONTRACT))}),
        ('require(base_caller == render_caller(stale),', 'require(True,'))
    add("a hand-edited proposed caller is not a bootstrap",
        with_files(**{CALLER: (generate_caller(STALE), tamper(generate_caller(CONTRACT)))}),
        ('require(head_caller == render_caller(contract),', 'require(True,'))
    add("a default-branch caller that already contains the casing repair admits no bootstrap",
        compare_override(Consumer(), f"{FLOOR}...{STALE}", "ahead"),
        ('require(floor_to_stale.get("status") in ("behind", "diverged"),', 'require(True,'))
    min_safe_guard = ('require(contains(MINIMUM_SAFE_REVIEW, contract),', 'require(True,')
    add("a proposed revision before the casing repair is not a bootstrap",
        compare_override(Consumer(), f"{MINIMUM_SAFE}...{CONTRACT}", "behind"), min_safe_guard)
    add("a proposed revision between the casing repair and the minimum safe review fix is not a bootstrap",
        compare_override(Consumer(), f"{MINIMUM_SAFE}...{CONTRACT}", "behind"), min_safe_guard)
    add("a proposed revision that does not descend from the stale pin is not a bootstrap",
        compare_override(Consumer(), f"{STALE}...{CONTRACT}", "diverged"),
        ('require(contains(stale, contract),', 'require(True,'))
    add("a proposed revision absent from canonical main is not a bootstrap",
        compare_override(Consumer(), f"{CONTRACT}...main", "diverged"),
        ('require(contains(contract, "main"),', 'require(True,'))
    add("an ahead comparison whose merge base is not the older commit proves nothing",
        compare_override(Consumer(), f"{MINIMUM_SAFE}...{CONTRACT}", "ahead", merge_base="8" * 40),
        ('or (status == "ahead" and comparison["merge_base_commit"]["sha"] == older))', 'or status == "ahead")'))
    add("a canonical comparison of a different base proves nothing",
        compare_override(Consumer(), f"{FLOOR}...{STALE}", "behind", base_commit="8" * 40),
        ('require(comparison["base_commit"]["sha"] == base,', 'require(True,'))
    path_rule = ('require(not folded.startswith(".github/") and folded.rsplit("/", 1)[-1] not in ("action.yml", "action.yaml"),',
                 'require(True,')
    add("a pin swap in workflow configuration is not a bootstrap",
        with_files(**{".github/pins.json": (mirror(STALE), mirror(CONTRACT))}), path_rule)
    add("a pin swap in action metadata is not a bootstrap",
        with_files(**{"tools/check/action.yml": (mirror(STALE), mirror(CONTRACT))}), path_rule)
    folded_rule = ('path.casefold()', 'path')
    add("a pin swap under a mixed-case .GitHub directory is not a bootstrap",
        with_files(**{".GitHub/pins.json": (mirror(STALE), mirror(CONTRACT))}), folded_rule)
    add("a pin swap in mixed-case Action.yml metadata is not a bootstrap",
        with_files(**{"tools/check/Action.yml": (mirror(STALE), mirror(CONTRACT))}), folded_rule)
    add("a mirror that changes more than the pin and digest is not a bootstrap",
        with_files(**{"tests/pins.test.ts": (mirror(STALE), mirror(CONTRACT) + b"export const extra = 1;\n")}),
        ("require(after != before and after == swap.sub(lambda match: swaps[match.group(0)], before),",
         "require(after != before,"))
    executable = with_files(**{"tests/pins.test.ts": (mirror(STALE), mirror(CONTRACT))})
    executable.modes[("head", "tests/pins.test.ts")] = "100755"
    add("a mirror that becomes executable is not a bootstrap", executable,
        ('require(mode == "100644", "only regular non-executable files may change")',
         'require(True, "only regular non-executable files may change")'))
    submodule = with_files(**{"vendor/lib": (mirror(STALE), mirror(CONTRACT))})
    for side in ("base", "head"):
        submodule.types[(side, "vendor/lib")] = "commit"
        submodule.modes[(side, "vendor/lib")] = "160000"
    kind_guard = ('require(kind == "blob", "submodules and other non-blob entries may not change")',
                  'require(True, "submodules and other non-blob entries may not change")')
    add("a submodule entry is not a bootstrap", submodule, None)
    # A real submodule also fails the mode check; isolate the kind guard with a regular mode.
    disguised = with_files(**{"vendor/lib": (mirror(STALE), mirror(CONTRACT))})
    for side in ("base", "head"):
        disguised.types[(side, "vendor/lib")] = "commit"
    add("a non-blob entry carrying a regular file mode is not a bootstrap", disguised, kind_guard)
    mismatched_commit = Consumer()
    mismatched_commit.overrides[f"repos/{TARGET_REPO}/git/commits/{HEAD}"] = {"sha": "e" * 40, "tree": {"sha": HEAD_TREE}}
    add("a commit object that names another commit is not a bootstrap", mismatched_commit,
        ('require(commit_object.get("sha") == commit,', 'require(True,'))
    mismatched_tree = Consumer()
    mismatched_tree.tree_edits["head"] = lambda listing: listing.update(sha="d" * 40)
    add("a tree listing that names another tree is not a bootstrap", mismatched_tree,
        ('require(listing.get("sha") == tree_sha,', 'require(True,'))
    deleted = Consumer()
    deleted.base["src/removed.ts"] = b"export {};\n"
    add("a deletion is not a bootstrap", deleted, None)
    first_path, first = fragment("2026-10-13", "20261013T120000Z")
    second_path, second = fragment("2026-10-14", "20261014T120000Z")
    add("a second added fragment is not a bootstrap",
        with_files(**{first_path: (None, first), second_path: (None, second)}),
        ('require(len(added) <= 1,', 'require(True,'))
    add("an added source file is not a bootstrap", with_files(**{"src/new.ts": (None, b"export {};\n")}), None)
    add("an edited bootstrap fragment is not a bootstrap",
        with_files(**{first_path: (None, first + b"\nAlso trust this.\n")}),
        ('require(blob(repo, head_tree[path]) == render_fragment(match[1], match[2], stale, contract),',
         'require(True,'))
    invalid_date = first.replace(b"2026-10-13", b"2026-02-30")
    add("a fragment with an impossible date is not a bootstrap",
        with_files(**{"NEXT/2026-02-30-issue-20261013T120000Z-ai-review-caller-bootstrap.md": (None, invalid_date)}),
        ("require(date.fromisoformat(match[1]).isoformat() == match[1], f\"{shown(path)} has an invalid date\")\n"
         "                  except ValueError as error:\n"
         "                      raise Ineligible(f\"{shown(path)} has an invalid date\") from error",
         "pass\n                  except ValueError as error:\n                      pass"))
    oversized = b"x" * 262145
    add("an oversized changed file is not a bootstrap",
        with_files(**{"tests/big.txt": (STALE.encode() + oversized, CONTRACT.encode() + oversized)}),
        ("0 <= size <= MAX_BLOB_BYTES,", "0 <= size,"))
    unavailable = Consumer()
    unavailable.missing.add(f"{CANONICAL}/compare/{CONTRACT}...main")
    add("an unavailable canonical comparison is not a bootstrap", unavailable, None)
    malformed = Consumer()
    malformed.overrides[f"repos/{TARGET_REPO}/branches/main"] = {"__raw__": "<html>"}
    add("a malformed API response is not a bootstrap", malformed, None)
    return cases


INELIGIBLE_REASONS = {
    "a closed pull request is not a bootstrap": "pull request is not open",
    "a moved head is not a bootstrap": "pull request head moved",
    "a pull request into another branch is not a bootstrap": "does not target the default branch",
    "a fork pull request is not a bootstrap": "pull request crosses repositories",
    "a pull request over the path budget is not a bootstrap": "pull request is too large",
    "an incomplete pull request file list is not a bootstrap": "file list is incomplete",
    "a file list repeating a path is not a bootstrap": "file list repeats a path",
    "a file list that disagrees with the exact trees is not a bootstrap": "disagrees with the exact trees",
    "an unprotected default branch admits no bootstrap": "default branch is not protected",
    "a head that does not descend from the protected tip is not a bootstrap": "does not descend from the protected default branch tip",
    "a truncated tree listing is not a bootstrap": "tree listing is truncated",
    "a tree listing that repeats a path is not a bootstrap": "tree listing repeats a path",
    "blob bytes that do not hash to the tree's object ID are rejected": "blob content does not match its ID",
    "a hand-edited default-branch caller is not a bootstrap": "default-branch review caller is not generated",
    "a hand-edited proposed caller is not a bootstrap": "proposed review caller is not generated",
    "a default-branch caller that already contains the casing repair admits no bootstrap": "already contains the casing repair",
    "a proposed revision before the casing repair is not a bootstrap": "predates the minimum safe review revision",
    "a proposed revision between the casing repair and the minimum safe review fix is not a bootstrap": "predates the minimum safe review revision",
    "a proposed revision that does not descend from the stale pin is not a bootstrap": "does not descend from the stale revision",
    "a proposed revision absent from canonical main is not a bootstrap": "is not on canonical main",
    "an ahead comparison whose merge base is not the older commit proves nothing": "predates the minimum safe review revision",
    "a canonical comparison of a different base proves nothing": "canonical comparison base is not exact",
    "a pin swap in workflow configuration is not a bootstrap": "is workflow or action configuration",
    "a pin swap in action metadata is not a bootstrap": "is workflow or action configuration",
    "a pin swap under a mixed-case .GitHub directory is not a bootstrap": "is workflow or action configuration",
    "a pin swap in mixed-case Action.yml metadata is not a bootstrap": "is workflow or action configuration",
    "a mirror that changes more than the pin and digest is not a bootstrap": "changes more than the review caller pin and digest",
    "a mirror that becomes executable is not a bootstrap": "only regular non-executable files may change",
    "a deletion is not a bootstrap": "is not modified in place",
    "a submodule entry is not a bootstrap": "submodules and other non-blob entries may not change",
    "a non-blob entry carrying a regular file mode is not a bootstrap": "submodules and other non-blob entries may not change",
    "a commit object that names another commit is not a bootstrap": "commit object does not match its ID",
    "a tree listing that names another tree is not a bootstrap": "tree listing does not match its ID",
    "a second added fragment is not a bootstrap": "adds more than one file",
    "an added source file is not a bootstrap": "is not the generated bootstrap changelog fragment",
    "an edited bootstrap fragment is not a bootstrap": "is not the generated bootstrap changelog fragment",
    "a fragment with an impossible date is not a bootstrap": "has an invalid date",
    "an oversized changed file is not a bootstrap": "blob is too large",
    "an unavailable canonical comparison is not a bootstrap": "GitHub API read failed",
    "a malformed API response is not a bootstrap": "GitHub API returned malformed JSON",
}


def poison_outer_environment() -> Path:
    """Simulate a hostile or merely noisy job environment that could shadow the stubs."""
    hostile = Path(tempfile.mkdtemp()) / "hostile-bash-env.sh"
    hostile.write_text("gh() { echo hostile-gh >&2; return 97; }\n", encoding="utf-8")
    os.environ["BASH_ENV"] = str(hostile)
    os.environ["ENV"] = str(hostile)
    os.environ["BASH_FUNC_gh%%"] = "() { echo hostile-gh >&2; return 97; }"
    return hostile


def main() -> int:
    poison_outer_environment()
    harness = Harness()
    # Classification calls gh from Python, so only the completion step's bash
    # invocations of gh can observe a shadowing function.
    result, _ = harness.run_step("bootstrap", Consumer())
    completed, _, _ = harness.complete(Consumer())
    report("hostile-gh" not in result.stdout + result.stderr + completed.stdout + completed.stderr
           and completed.returncode == 0,
           "an outer BASH_ENV, ENV, or exported gh function never reaches a step", completed)

    result, outputs = harness.run_step("bootstrap", Consumer())
    report(result.returncode == 0 and outputs.get("eligible") == "true" and outputs.get("stale_sha") == STALE
           and outputs.get("contract_sha") == CONTRACT,
           "a byte-exact generated stale caller update is classified as a bootstrap", result)
    runner_bootstrap = Consumer()
    runner_bootstrap.base[CALLER] = generate_caller("2d1ea177fdafe361f9369526cbab8bc05f9e476d")
    runner_bootstrap.canonical = {
        f"{FLOOR}...2d1ea177fdafe361f9369526cbab8bc05f9e476d": "behind",
        f"{MINIMUM_SAFE}...{CONTRACT}": "ahead",
        f"2d1ea177fdafe361f9369526cbab8bc05f9e476d...{CONTRACT}": "ahead",
        f"{CONTRACT}...main": "ahead",
    }
    report(harness.eligible(runner_bootstrap), "the runner fleet's stale 2d1ea177 caller can bootstrap", harness.last)
    report(harness.eligible(with_files(**{"tests/pins.test.ts": (mirror(STALE), mirror(CONTRACT))})),
           "a pin mirror that only swaps the stale pin and caller digest is accepted", harness.last)
    for identity in ("20261013T120000Z", "1696", "a49c6d20f3"):
        path, content = fragment("2026-10-13", identity)
        report(harness.eligible(with_files(**{path: (None, content)})),
               f"the generated bootstrap changelog fragment is accepted ({identity})", harness.last)

    ordinary = with_files(**{CALLER: (generate_caller(STALE), generate_caller(STALE)),
                             "src/feature.ts": (None, b"export const feature = true;\n")})
    result, outputs = harness.run_step("bootstrap", ordinary)
    report(result.returncode == 0 and outputs.get("eligible") == "false",
           "an ordinary pull request keeps the ordinary dispatch and never fails classification")
    outage = Consumer()
    outage.missing.update(outage.routes())
    result, outputs = harness.run_step("bootstrap", outage)
    report(result.returncode == 0 and outputs.get("eligible") == "false",
           "an API outage leaves the ordinary dispatch in place instead of failing the arm")
    (harness.tmp / "nopython").mkdir(exist_ok=True)
    (harness.tmp / "nopython/python3").write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
    (harness.tmp / "nopython/python3").chmod(0o755)
    harness.path_prefix = f"{harness.tmp / 'nopython'}:"
    result, outputs = harness.run_step("bootstrap", Consumer())
    harness.path_prefix = ""
    report(result.returncode == 0 and outputs.get("eligible") == "false" and outputs.get("stale_sha") == "",
           "a runner without the proof interpreter is never eligible")
    for env_name, value in (("TARGET_REPO", "Verjson/example/../x"), ("PR_NUMBER", "07"),
                            ("EXPECTED_HEAD_SHA", "g" * 40), ("DEFAULT_BRANCH", "main/../x")):
        result, outputs = harness.run_step("bootstrap", Consumer(), {env_name: value})
        report(result.returncode == 0 and outputs.get("eligible") == "false",
               f"a malformed {env_name} is never eligible")

    for name, consumer, mutation in ineligible_cases():
        result, outputs = harness.run_step("bootstrap", consumer)
        reason = INELIGIBLE_REASONS[name]
        report(result.returncode == 0 and outputs.get("eligible") == "false" and reason in result.stdout,
               f"{name} ({reason})", result)
        if mutation is None:
            continue
        text = mutated(*mutation)
        if text is None:
            report(False, f"mutation for '{name}' no longer applies exactly once")
            continue
        mutant = Harness(text)
        report(mutant.eligible(consumer), f"mutation proof: removing the guard admits '{name}'", mutant.last)

    rendered = subprocess.run(["bash", "-c", harness.steps["bootstrap"]["run"].split("eligible=false", 1)[0]],
                              env={**os.environ, "RUNNER_TEMP": str(harness.tmp)}, capture_output=True)
    program = harness.tmp / "review-caller-bootstrap.py"
    for sha in (STALE, CONTRACT, FLOOR, "2d1ea177fdafe361f9369526cbab8bc05f9e476d", hashlib.sha1(b"drift").hexdigest()):
        arm_bytes = subprocess.run(["python3", "-I", str(program), "render-caller", sha], capture_output=True).stdout
        report(rendered.returncode == 0 and arm_bytes == generate_caller(sha),
               f"the arm's embedded caller template matches the canonical generator at {sha[:8]}")
    for bad in (("2026-02-30", "1696", STALE, CONTRACT), ("2026-10-13", "ABCDEF", STALE, CONTRACT),
                ("2026-10-13", "0", STALE, CONTRACT), ("2026-10-13", "1696", STALE, STALE)):
        generated = subprocess.run(["bash", str(FRAGMENT_GENERATOR), *bad], capture_output=True)
        report(generated.returncode == 2 and not generated.stdout,
               f"the fragment generator refuses {bad[0]} {bad[1]} {bad[2][:8]}..{bad[3][:8]}")
    validate_root = harness.tmp / "changelog-repo"
    (validate_root / "NEXT").mkdir(parents=True)
    for identity in ("20261013T120000Z", "1696", "a49c6d20f3"):
        path, content = fragment("2026-10-13", identity)
        (validate_root / path).write_bytes(content)
    validated = subprocess.run(["python3", str(ROOT / "scripts/changelog.py"), "validate", "--repo-root", str(validate_root)],
                               capture_output=True, text=True)
    report(validated.returncode == 0, "every generated bootstrap fragment satisfies the canonical changelog contract")

    steps = harness.steps
    dispatch = steps.get("dispatch", {})
    report(
        dispatch.get("if") == "steps.arm.outputs.check_id != '' && steps.bootstrap.outputs.eligible != 'true'",
        "an eligible bootstrap never dispatches the stale default-branch review",
    )
    report(
        steps["bootstrap"].get("if") == "steps.arm.outputs.check_id != '' && steps.arm.outputs.explicit_rereview != 'true' && steps.arm.outputs.explicit_ai_review != 'true'",
        "explicit paid-review labels always take the ordinary dispatch",
    )
    report(
        steps["bootstrap-app-token"].get("if") == "steps.bootstrap.outputs.eligible == 'true'"
        and steps["bootstrap-complete"].get("if") == "steps.bootstrap.outputs.eligible == 'true'"
        and steps["bootstrap-app-token"]["with"]["permission-pull-requests"] == "write"
        and steps["bootstrap-app-token"]["with"]["permission-contents"] == "read"
        and set(k for k in steps["bootstrap-app-token"]["with"] if k.startswith("permission-")) == {"permission-pull-requests", "permission-contents"},
        "the App token is minted only for a proven bootstrap and only with review permissions",
    )
    order = list(steps)
    report(order.index("receipt") < order.index("bootstrap") < order.index("bootstrap-app-token")
           < order.index("bootstrap-complete") < order.index("dispatch"),
           "the bootstrap runs after receipt publication and before the ordinary dispatch")

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
        and any(f"ai-review-authorized:v1:9001:{HEAD}:ai-approve" in arg for arg in patches[0]["args"])
        and not any("ai-review-authorized:v1:9001:" + HEAD + ":ai-merge" in arg for arg in patches[0]["args"])
        and not any("workflow" in c["args"] for c in calls),
        "ai-merge authority approves the exact head but records approval-only authority: no terminal promotion",
    )
    promoting = mutated('"<!-- ai-review-authorized:v1:$CHECK_ID:$EXPECTED_HEAD_SHA:ai-approve -->")"',
                        '"<!-- ai-review-authorized:v1:$CHECK_ID:$EXPECTED_HEAD_SHA:$authority -->")"')
    _, _, promoted = Harness(promoting).complete(Consumer()) if promoting else (None, None, [])
    report(promoting is not None and any(f"ai-review-authorized:v1:9001:{HEAD}:ai-merge" in arg
                                         for c in promoted for arg in c["args"]),
           "mutation proof: recording the receipt authority would grant terminal promotion")
    result, _, calls = harness.complete(Consumer(), authority="ai-approve")
    report(result.returncode == 0 and any("conclusion=success" in c["args"] for c in calls)
           and any(f"ai-review-authorized:v1:9001:{HEAD}:ai-approve" in arg for c in calls for arg in c["args"]),
           "ai-approve authority also persists the exact-head App approval")

    def neutral_without_approval(calls) -> bool:
        return (not any(c["args"][:3] == ["api", "--method", "POST"] for c in calls)
                and not any(c["token"] == "app-token" for c in calls)
                and any("conclusion=neutral" in c["args"] for c in calls)
                and not any("conclusion=success" in c["args"] for c in calls))

    completion_cases = [
        ("human authority completes neutrally without App approval", {"authority": "human"},
         neutral_without_approval,
         ('if [ "$authority" = ai-approve ] || [ "$authority" = ai-merge ]; then', "if true; then")),
        ("a legacy policy without authority defaults to the human path", {"authority": None},
         neutral_without_approval,
         ('(.authority // "human")', '(.authority // "ai-merge")')),
    ]
    for name, kwargs, predicate, mutation in completion_cases:
        result, _, calls = harness.complete(Consumer(), **kwargs)
        report(result.returncode == 0 and predicate(calls), name)
        text = mutated(*mutation)
        if text is None:
            report(False, f"mutation for '{name}' no longer applies exactly once")
            continue
        result, _, calls = Harness(text).complete(Consumer(), **kwargs)
        report(not (result.returncode == 0 and predicate(calls)), f"mutation proof: {name}")

    def refused(calls) -> bool:
        return not any(c["args"][:3] in (["api", "--method", "POST"], ["api", "--method", "PATCH"]) for c in calls)

    moved = Consumer()
    moved.head["README.md"] = b"# moved\n"
    completed_check = Consumer()
    completed_check.overrides[f"repos/{TARGET_REPO}/check-runs/9001"] = {
        **Consumer().routes()[f"repos/{TARGET_REPO}/check-runs/9001"], "status": "completed", "conclusion": "failure"}
    foreign_check = Consumer()
    foreign_check.overrides[f"repos/{TARGET_REPO}/check-runs/9001"] = {
        **Consumer().routes()[f"repos/{TARGET_REPO}/check-runs/9001"], "external_id": EXTERNAL_ID.replace(":7001:", ":7002:")}
    app_check = Consumer()
    app_check.overrides[f"repos/{TARGET_REPO}/check-runs/9001"] = {
        **Consumer().routes()[f"repos/{TARGET_REPO}/check-runs/9001"], "app": {"id": 4242, "slug": "verjson-ai-review"}}
    refusal_cases = [
        ("a bootstrap whose proof breaks before granting is refused", {"later": moved},
         ('proof="$(GH_TOKEN="$ACTIONS_TOKEN" python3 -I "$RUNNER_TEMP/review-caller-bootstrap.py" 2>"$recheck_errors")" || {',
          'proof="$(printf \'{"stale_sha":"%s","contract_sha":"%s","head_sha":"%s"}\' "$STALE_SHA" "$CONTRACT_SHA" "$EXPECTED_HEAD_SHA")" || {')),
        ("a completion whose proof names other revisions is refused", {"STALE_SHA": "c" * 40},
         ("'.stale_sha == $stale and .contract_sha == $contract and .head_sha == $head' <<<\"$proof\" >/dev/null || {",
          "'true' <<<\"$proof\" >/dev/null || {")),
        ("an already completed check is never overwritten", {"later": completed_check},
         ('.status == "in_progress" and .conclusion == null\n          \' <<<"$check"', 'true\n          \' <<<"$check"')),
        ("a check from another arm run is never completed", {"later": foreign_check},
         ('.external_id == $external and .app.id == 15368 and .app.slug == "github-actions" and\n            .status',
          '.app.id == 15368 and .app.slug == "github-actions" and\n            .status')),
        ("a check owned by another App is never completed", {"later": app_check},
         ('.app.id == 15368 and .app.slug == "github-actions" and\n            .status', 'true and\n            .status')),
        ("a minted App other than the authorization App is refused", {"MINTED_APP_SLUG": "other-app"},
         ('[ -n "$APP_TOKEN" ] && [ "$MINTED_APP_SLUG" = "$APP_SLUG" ] || {', 'true || {')),
        ("an unproven classification is refused", {"BOOTSTRAP_ELIGIBLE": "false"},
         ('[ "$BOOTSTRAP_ELIGIBLE" = true ] || { echo "::error::stale review caller bootstrap was not proven"; exit 1; }', ':')),
        ("a forged receipt authority is refused", {"REVIEW_POLICY": policy("root")}, None),
        ("a missing App installation identity is refused", {"INSTALLATION_ID": ""}, None),
    ]
    for name, overrides, mutation in refusal_cases:
        kwargs = dict(overrides)
        later = kwargs.pop("later", None)
        result, _, calls = harness.complete(Consumer(), later=later, **kwargs)
        report(result.returncode != 0 and refused(calls), name)
        if mutation is None:
            continue
        text = mutated(*mutation)
        if text is None:
            report(False, f"mutation for '{name}' no longer applies exactly once")
            continue
        result, _, calls = Harness(text).complete(Consumer(), later=later, **kwargs)
        report(not (result.returncode != 0 and refused(calls)), f"mutation proof: {name}")

    def approval_withheld(calls) -> bool:
        return (any("conclusion=neutral" in c["args"] for c in calls)
                and not any("conclusion=success" in c["args"] for c in calls))

    approval_cases = [
        ("an approval by another identity never completes as success", {"REVIEW_LOGIN": "someone-else[bot]"},
         ("select(.state == \"APPROVED\" and .commit_id == $head and .user.login == $login and",
          "select(.state == \"APPROVED\" and .commit_id == $head and")),
        ("a failed approval request leaves only the human path", {"GH_FAIL": "POST repos/"}, None),
        ("an unreadable persisted approval leaves only the human path", {"GH_FAIL": "GET repos/Verjson/example/pulls/7/reviews/555"}, None),
    ]
    for name, overrides, mutation in approval_cases:
        result, _, calls = harness.complete(Consumer(), **overrides)
        report(result.returncode == 0 and approval_withheld(calls), name)
        if mutation is None:
            continue
        text = mutated(*mutation)
        if text is None:
            report(False, f"mutation for '{name}' no longer applies exactly once")
            continue
        result, _, calls = Harness(text).complete(Consumer(), **overrides)
        report(not (result.returncode == 0 and approval_withheld(calls)), f"mutation proof: {name}")
    result, _, calls = harness.complete(Consumer(), GH_FAIL="PATCH repos/")
    report(result.returncode != 0, "an unconfirmed check completion fails the step for the terminalizer")

    injected = with_files(**{INJECTED_PATH: (None, b"export {};\n")})
    result, outputs = harness.run_step("bootstrap", injected)
    log = result.stdout + result.stderr
    report(result.returncode == 0 and outputs.get("eligible") == "false" and json.dumps(INJECTED_PATH) in log
           and not any(line.startswith("::warning") for line in log.splitlines()),
           "an untrusted path is reported JSON-escaped, never as a raw workflow command")
    report(json.dumps(INJECTED_PATH) in log and not live_commands(log),
           "classification output from untrusted paths runs inside stop-commands", result)
    result, _ = harness.run_step("bootstrap-complete", injected, {
        "EXPECTED_EXTERNAL_ID": EXTERNAL_ID, "REVIEW_POLICY": policy("ai-merge"), "BOOTSTRAP_ELIGIBLE": "true",
        "STALE_SHA": STALE, "CONTRACT_SHA": CONTRACT, "APP_TOKEN": "app-token", "APP_SLUG": "verjson-ai-review",
        "MINTED_APP_SLUG": "verjson-ai-review", "INSTALLATION_ID": "42", "GITHUB_RUN_ID": "7001"})
    log = result.stdout + result.stderr
    report(result.returncode != 0 and json.dumps(INJECTED_PATH) in log and not live_commands(
               "\n".join(line for line in log.splitlines() if not line.startswith("::error::"))),
           "a failed re-proof reports untrusted paths only inside stop-commands")
    unescaped = mutated('require(match is not None, f"{shown(path)} is not the generated bootstrap changelog fragment")',
                        'require(match is not None, f"{path} is not the generated bootstrap changelog fragment")')
    if unescaped:
        result, _ = Harness(unescaped).run_step("bootstrap", injected)
        log = result.stdout + result.stderr
        report(any(line.startswith("::warning") for line in log.splitlines()) and not live_commands(log),
               "mutation proof: without JSON escaping the command appears, still neutralized by stop-commands")
        unwrapped = unescaped.replace('echo "::stop-commands::$token"', ':', 1)
        result, _ = Harness(unwrapped).run_step("bootstrap", injected)
        report(any(line.startswith("::warning") for line in live_commands(result.stdout + result.stderr)),
               "mutation proof: without either layer the injected command would run", result)
        completion_env = {
            "EXPECTED_EXTERNAL_ID": EXTERNAL_ID, "REVIEW_POLICY": policy("ai-merge"), "BOOTSTRAP_ELIGIBLE": "true",
            "STALE_SHA": STALE, "CONTRACT_SHA": CONTRACT, "APP_TOKEN": "app-token", "APP_SLUG": "verjson-ai-review",
            "MINTED_APP_SLUG": "verjson-ai-review", "INSTALLATION_ID": "42", "GITHUB_RUN_ID": "7001"}
        bare = Harness(unescaped)
        bare.run_step("bootstrap", injected)
        wrapped_completion, _ = bare.run_step("bootstrap-complete", injected, completion_env)
        bare.steps["bootstrap-complete"]["run"] = bare.steps["bootstrap-complete"]["run"].replace(
            'echo "::stop-commands::$token"', ':', 1)
        unwrapped_completion, _ = bare.run_step("bootstrap-complete", injected, completion_env)
        strip = lambda text: "\n".join(line for line in text.splitlines() if not line.startswith("::error::"))
        report(not live_commands(strip(wrapped_completion.stdout + wrapped_completion.stderr))
               and any(line.startswith("::warning")
                       for line in live_commands(strip(unwrapped_completion.stdout + unwrapped_completion.stderr))),
               "mutation proof: the completion step's stop-commands wrapper is load-bearing", unwrapped_completion)
    else:
        report(False, "mutation for the path escaping layer no longer applies")

    terminalizer = steps.get("Complete the authorization when no review was dispatched", {})
    report(
        terminalizer.get("if") == "always() && steps.arm.outputs.check_id != '' && steps.dispatch.outcome != 'success' && steps.bootstrap-complete.outcome != 'success'",
        "a completed bootstrap owns its check and is never terminalized again",
    )
    harness.reset_state()
    result, _ = harness.run_step(
        "Complete the authorization when no review was dispatched", Consumer(),
        {"EXPECTED_EXTERNAL_ID": EXTERNAL_ID, "ARM_FAILURE_REASON": "", "AI_REVIEW_ENVIRONMENT": "ai-review-app",
         "BOOTSTRAP_ELIGIBLE": "true"},
    )
    patches = [c for c in harness.recorded() if c["args"][:3] == ["api", "--method", "PATCH"]]
    report(
        result.returncode == 0 and len(patches) == 1 and "conclusion=failure" in patches[0]["args"]
        and any(arg.startswith("output[summary]=The stale review caller bootstrap did not complete")
                for arg in patches[0]["args"]),
        "a failed bootstrap terminalizes its check as a bootstrap failure, never as success",
    )

    if failures:
        print(f"{failures} review caller bootstrap check(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
