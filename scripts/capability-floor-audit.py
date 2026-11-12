#!/usr/bin/env python3
"""Report consumer generated-caller pins that predate a capability this
canonical contract's generators -- and any org-wide variable that assumes
it -- already require.

Read-only and report-only (ADR 0114, issue #933): this script only computes
staleness. It never regenerates a caller, opens a pull request, or mutates
anything, in this repository or any consumer's. See
config/capability-floors.json for the capability facts it reads, and
docs/decisions/0114-generated-caller-capability-floor-audit/README.md for the
mechanism this implements.

"Stale" means: a consumer's pinned contract SHA is not a git ancestor of the
commit that introduced a capability whose generators cover that pin. Ancestry
is decided with `git merge-base --is-ancestor <introduced_at> <pinned_sha>`
against *this* repository's own history -- Verjson/.github is the canonical
source every generated-caller pin is drawn from, so no external clone or API
call is needed to answer "does this pin already include capability X".

This first increment does not discover consumer pins itself -- it audits a
pins snapshot supplied on the command line. Live cross-repository discovery
(reading each known consumer's generated caller workflow via the GitHub API)
is deliberately out of scope here; see the ADR's rollout section for why and
what a follow-up PR should do.

Usage:
  scripts/capability-floor-audit.py --pins <pins.json> [--capabilities <capabilities.json>] [--fail-on-stale]

Pins file schema -- a JSON array of objects:
  [{"repo": "Verjson/toquorum", "generator": "scripts/gen-gate-rearm-caller.sh", "pinned_sha": "<40-hex-sha>"}]

A pin's `generator` is matched against each capability's `generators` list
(exact string match). A pin naming a generator no capability currently tracks
is not an error -- it is simply not checked against anything yet.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# `python3 -I` does not put the script directory on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import shared_checkout_fetch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+$")


class AuditError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def read_json(path: str, label: str):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuditError(f"cannot read {label} ({path}): {error}") from None


_unshallow_attempted = False


def _is_shallow_repository() -> bool:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--is-shallow-repository"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.decode().strip() == "true"


def _attempt_unshallow() -> bool:
    """Bounded, once-per-run deepening fetch for a shallow checkout.

    `actions-ci.yml` checks this repo out with `fetch-depth: 1` for cost
    reasons, but `commit_exists`/`is_ancestor` need history reaching back to
    each capability's `introduced_at` commit and each pin's `pinned_sha`.
    Mirrors the retry-then-fail-closed idiom already established for this
    exact shallow-history problem in `ai-review-merge.yml`'s "Prepare bounded
    review context" step (see `scripts/ci-gate/review-context.test.sh`): try
    the check first, and only pay for a deepening fetch -- once -- when the
    checkout turns out to be shallow. Callers still fail closed with a typed
    `AuditError` if the retry after this doesn't resolve it.

    Other actions-ci rows fetch into the same checkout concurrently, and git
    aborts a shallow update another one raced (#2003), so the fetch goes
    through `shared_checkout_fetch`, which also succeeds without fetching when
    another row completed the history while this one waited.
    """
    global _unshallow_attempted
    if _unshallow_attempted:
        return False
    _unshallow_attempted = True
    if not _is_shallow_repository():
        return False
    try:
        return shared_checkout_fetch.unshallow(ROOT, capture_output=True)
    except shared_checkout_fetch.SharedCheckoutError as error:
        raise AuditError(str(error)) from None


def commit_exists(sha: str) -> bool:
    def check() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True,
            check=False,
        )

    result = check()
    if result.returncode != 0 and _attempt_unshallow():
        result = check()
    return result.returncode == 0


def is_ancestor(ancestor: str, descendant: str) -> bool:
    def check() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True,
            check=False,
        )

    result = check()
    if result.returncode not in (0, 1) and _attempt_unshallow():
        result = check()
    if result.returncode not in (0, 1):
        raise AuditError(
            f"git merge-base --is-ancestor failed unexpectedly for {ancestor}..{descendant}: "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    return result.returncode == 0


def load_capabilities(path: str) -> list[dict]:
    payload = read_json(path, "capabilities file")
    require(isinstance(payload, dict), "capabilities file must be a JSON object")
    require(payload.get("schema_version") == 1, "unsupported capabilities schema_version")
    capabilities = payload.get("capabilities")
    require(isinstance(capabilities, list) and capabilities, "capabilities list is missing or empty")

    parsed = []
    seen_ids = set()
    for index, entry in enumerate(capabilities):
        require(isinstance(entry, dict), f"capability {index} must be an object")
        cap_id = entry.get("id")
        description = entry.get("description")
        introduced_at = entry.get("introduced_at")
        generators = entry.get("generators")
        issue = entry.get("issue")
        variable = entry.get("assumed_by_org_variable")

        require(isinstance(cap_id, str) and cap_id, f"capability {index} id is invalid")
        require(cap_id not in seen_ids, f"duplicate capability id {cap_id!r}")
        seen_ids.add(cap_id)
        require(isinstance(description, str) and description, f"capability {cap_id} description is invalid")
        require(
            isinstance(introduced_at, str) and SHA_RE.match(introduced_at),
            f"capability {cap_id} introduced_at must be a 40-character lowercase hex sha",
        )
        require(
            commit_exists(introduced_at),
            f"capability {cap_id} introduced_at {introduced_at} is not a commit reachable in this checkout",
        )
        require(
            isinstance(generators, list)
            and generators
            and all(isinstance(value, str) and value for value in generators)
            and len(generators) == len(set(generators)),
            f"capability {cap_id} generators must be a non-empty list of unique strings",
        )
        for generator in generators:
            require(
                (ROOT / generator).is_file(),
                f"capability {cap_id} generator {generator} does not exist in this checkout",
            )
        if issue is not None:
            require(
                isinstance(issue, int) and not isinstance(issue, bool) and issue > 0,
                f"capability {cap_id} issue must be a positive integer or omitted",
            )
        if variable is not None:
            require(
                isinstance(variable, dict)
                and set(variable) == {"name", "value"}
                and isinstance(variable["name"], str) and variable["name"]
                and isinstance(variable["value"], str) and variable["value"],
                f"capability {cap_id} assumed_by_org_variable must be null or {{name, value}} strings",
            )

        parsed.append(
            {
                "id": cap_id,
                "description": description,
                "introduced_at": introduced_at,
                "issue": issue,
                "generators": generators,
                "assumed_by_org_variable": variable,
            }
        )
    return parsed


def load_pins(path: str) -> list[dict]:
    payload = read_json(path, "pins file")
    require(isinstance(payload, list), "pins file must be a JSON array")

    parsed = []
    for index, entry in enumerate(payload):
        require(isinstance(entry, dict), f"pin {index} must be an object")
        repo = entry.get("repo")
        generator = entry.get("generator")
        pinned_sha = entry.get("pinned_sha")
        require(isinstance(repo, str) and REPO_RE.match(repo), f"pin {index} repo is invalid: {repo!r}")
        require(isinstance(generator, str) and generator, f"pin {index} generator is invalid")
        require(
            isinstance(pinned_sha, str) and SHA_RE.match(pinned_sha),
            f"pin {index} pinned_sha must be a 40-character lowercase hex sha",
        )
        parsed.append({"repo": repo, "generator": generator, "pinned_sha": pinned_sha})
    return parsed


def audit(capabilities: list[dict], pins: list[dict]) -> dict:
    stale = []
    current = []
    unresolvable = []
    pairs_checked = 0

    for pin in pins:
        if not commit_exists(pin["pinned_sha"]):
            unresolvable.append({**pin, "reason": "pinned_sha not found in Verjson/.github history"})
            continue
        for capability in capabilities:
            if pin["generator"] not in capability["generators"]:
                continue
            pairs_checked += 1
            record = {
                **pin,
                "capability_id": capability["id"],
                "description": capability["description"],
                "introduced_at": capability["introduced_at"],
                "issue": capability["issue"],
                "assumed_by_org_variable": capability["assumed_by_org_variable"],
            }
            if is_ancestor(capability["introduced_at"], pin["pinned_sha"]):
                current.append(record)
            else:
                stale.append(record)

    return {
        "schema_version": 1,
        "capabilities_checked": len(capabilities),
        "pins_checked": len(pins),
        "capability_pin_pairs_checked": pairs_checked,
        "stale": stale,
        "current": current,
        "unresolvable": unresolvable,
        "any_stale": bool(stale) or bool(unresolvable),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pins", required=True, help="path to a JSON array of consumer pin records")
    parser.add_argument(
        "--capabilities",
        default=str(ROOT / "config/capability-floors.json"),
        help="path to the capability-floors config (default: config/capability-floors.json)",
    )
    parser.add_argument(
        "--fail-on-stale",
        action="store_true",
        help="exit 1 when any stale or unresolvable pin is found (default: report-only, exit 0)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        capabilities = load_capabilities(args.capabilities)
        pins = load_pins(args.pins)
        report = audit(capabilities, pins)
    except AuditError as error:
        print(f"ERROR: capability-floor-audit-invalid: {error}", file=sys.stderr)
        return 2

    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    if args.fail_on_stale and report["any_stale"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
