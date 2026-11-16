#!/usr/bin/env bash
# Contract for scripts/gen-merge-admission-caller.sh (ADR 0260 C10 (iv)).
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
generator="$repo_root/scripts/gen-merge-admission-caller.sh"
actions_ci="$repo_root/scripts/actions-ci-groups.tsv"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

contract_sha=0123456789abcdef0123456789abcdef01234567
workflows='["Merge admission trigger","actions-ci","AI review + auto-merge"]'
caller="$tmp/merge-admission-caller.yml"
trigger="$tmp/merge-admission-trigger.yml"

if [ ! -x "$generator" ]; then
  fail "merge-admission caller generator is missing or not executable"
else
  "$generator" "$contract_sha" "$workflows" >"$caller" \
    && pass "generator emits a caller for an immutable contract SHA" \
    || fail "generator rejected a valid immutable contract SHA and workflow list"
  "$generator" --trigger >"$trigger" \
    && pass "generator emits the keyless trigger" \
    || fail "generator rejected --trigger"
fi

if [ -s "$caller" ]; then
  python3 - "$caller" "$contract_sha" <<'PY' \
    && pass "generated caller has the exact thin producer-call contract" \
    || fail "generated caller shape, permissions, trigger, or immutable target drifted"
import json
import sys
import yaml

path, sha = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    doc = yaml.load(stream, Loader=yaml.BaseLoader)

expected_uses = f"Verjson/.github/.github/workflows/merge-admission.yml@{sha}"
assert set(doc) == {"name", "on", "concurrency", "permissions", "jobs"}
assert doc["name"] == "Merge admission"
assert doc["on"]["workflow_run"]["types"] == ["completed"]
assert "Merge admission trigger" in doc["on"]["workflow_run"]["workflows"]
assert doc["on"]["schedule"] == [{"cron": "*/15 * * * *"}]
assert doc["on"]["workflow_dispatch"] == {}
assert doc["concurrency"]["cancel-in-progress"] == "false"
assert doc["concurrency"]["group"].startswith("merge-admission-caller-")
assert doc["permissions"] == {"contents": "read"}
assert set(doc["jobs"]) == {"admit"}
job = doc["jobs"]["admit"]
assert job == {
    "permissions": {
        "actions": "read",
        "checks": "read",
        "contents": "read",
        "pull-requests": "read",
        "statuses": "read",
    },
    "uses": expected_uses,
    "secrets": "inherit",
    "with": {"ai_review_environment": "ai-review-app"},
}
PY

  grep -qF "scripts/gen-merge-admission-caller.sh $contract_sha" "$caller" \
    && pass "generated caller records an exact reproducible command" \
    || fail "generated caller lacks exact regeneration provenance"
  grep -qE '^    secrets: inherit$' "$caller" \
    && pass "caller inherits the ai-review-app environment context" \
    || fail "caller lacks inherited environment context"
  if grep -qE 'actions/checkout|github\.event\.pull_request\.(head|body|title)' "$caller" \
     || grep -qE '^[[:space:]]+run:' "$caller"; then
    fail "generated caller executes or exposes PR-controlled content"
  else
    pass "generated caller delegates without checkout, run steps, or PR prose"
  fi
fi

if [ -s "$trigger" ]; then
  python3 - "$trigger" <<'PY' \
    && pass "generated trigger is keyless and head-bound" \
    || fail "generated trigger shape drifted"
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    doc = yaml.load(stream, Loader=yaml.BaseLoader)

assert doc["name"] == "Merge admission trigger"
assert set(doc["on"]) == {"pull_request_target", "pull_request_review"}
assert doc["permissions"] == {}
assert set(doc["jobs"]) == {"signal"}
job = doc["jobs"]["signal"]
assert job["permissions"] == {}
assert job["timeout-minutes"] == "2"
assert "secrets" not in job
assert len(job["steps"]) == 1
assert "run" in job["steps"][0]
assert "uses" not in job["steps"][0]
PY
  grep -qF "scripts/gen-merge-admission-caller.sh --trigger" "$trigger" \
    && pass "generated trigger records regeneration provenance" \
    || fail "generated trigger lacks regeneration provenance"
fi

"$generator" 2>/dev/null && fail "generator accepted no arguments" || pass "generator refuses missing arguments"
"$generator" notasha "$workflows" 2>/dev/null \
  && fail "generator accepted a non-hex SHA" \
  || pass "generator refuses a non-hex SHA"
"$generator" "$contract_sha" '["actions-ci"]' 2>/dev/null \
  && fail "generator accepted a workflow list without the trigger" \
  || pass "generator refuses a workflow list without Merge admission trigger"
"$generator" "$contract_sha" '[]' 2>/dev/null \
  && fail "generator accepted an empty workflow list" \
  || pass "generator refuses an empty workflow list"
"$generator" --trigger extra 2>/dev/null \
  && fail "generator accepted --trigger with extra args" \
  || pass "generator refuses --trigger with extra args"

if grep -qE $'merge-gate\tbash scripts/ci-gate/merge-admission-caller-contract\\.test\\.sh$' "$actions_ci"; then
  pass "actions-ci registers the merge-admission caller contract test"
else
  fail "actions-ci does not register the merge-admission caller contract test"
fi

# This repository hosts the producer, so its caller uses a relative path rather than
# the consumer pin. Presence of both workflows is still required for C8.
for path in \
  .github/workflows/merge-admission-caller.yml \
  .github/workflows/merge-admission-trigger.yml
do
  if [ -f "$repo_root/$path" ]; then
    pass "canary repository carries $path"
  else
    fail "canary repository is missing $path"
  fi
done

if [ "$fails" -ne 0 ]; then
  printf '%s\n' "$fails failure(s)"
  exit 1
fi
printf 'all checks passed\n'
exit 0
