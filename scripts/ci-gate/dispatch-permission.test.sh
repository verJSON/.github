#!/usr/bin/env bash
# Least-privilege topology of ai-review-merge.yml, and the hardening of the one
# job that holds `actions: write` on the PR-facing side (Verjson/.github#1320).
#
# The original harness pinned the pre-ADR-0079 four-job shape with global
# `grep -c` counts and drove the retired "Dispatch fixed trusted merge
# continuation" step, whose merge-probe/postcondition loop ADR 0081 replaced with
# event-driven retries. Counts could not survive the `complete-authorization` job
# that ADR 0079 added, so the whole file went stale at once and was deregistered.
#
# Counts are the wrong assertion anyway: they say "one job has actions: write"
# without saying WHICH, so moving the grant to another job keeps them green. The
# map below is exact and per job, so a new job or a widened grant has to be
# stated here to land. That is the point — this is the sensitive merge-gate
# class, where a silent permission widening is the failure being guarded.
#
# House method: parse the shipped workflow and exercise the named step's real
# `run:` block against a stubbed `gh`, so the test cannot drift from the shipped
# logic. Plain bash + python3 + jq.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
wf="$root/.github/workflows/ai-review-merge.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$wf" ] || { echo "FAIL - workflow not found: $wf"; exit 1; }

if python3 - "$wf" <<'PY'
import json
import sys

import yaml

document = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
jobs = document["jobs"]

expected = {
    # Permissionless rejection for public pull-request callers.
    "reject-public-pull-request-caller": {},
    # Permissionless event-time title classification.
    "title-policy": {},
    # Reusable app-key policy callee; reads its own workflow metadata only.
    # Nested reusable App-key policy calls receive this explicit read-only
    # boundary from the caller.
    "app-key-policy": {"actions": "read", "contents": "read"},
    # Classifies and, when behind, updates the branch: the only PR write here.
    "preflight": {
        "actions": "read",
        "checks": "read",
        "contents": "read",
        "pull-requests": "write",
        "statuses": "read",
    },
    # Checks out the PR head and runs the model; files follow-up issues.
    "gate": {
        "actions": "read",
        "checks": "read",
        "contents": "read",
        "issues": "write",
        "pull-requests": "write",
        "statuses": "read",
    },
    # Completes the exact authorization check-run with the run-scoped token;
    # dispatches nothing itself. This is the only job allowed checks: write.
    "complete-authorization": {
        "actions": "write",
        "checks": "write",
        "contents": "read",
        "pull-requests": "read",
    },
    # Fire-and-forget dispatch of the trusted promotion. No PR or check access.
    "dispatch-merge": {"actions": "write", "contents": "read"},
}

actual = {name: job.get("permissions") for name, job in jobs.items()}
if actual != expected:
    raise SystemExit(
        "permission topology drifted\n"
        f"  expected: {json.dumps(expected, sort_keys=True)}\n"
        f"  actual:   {json.dumps(actual, sort_keys=True)}"
    )

# Only the terminalizer may use checks: write; its script binds the update
# to the authorization check created for this run. contents: write would let
# a gate job push to the PR head it reviews.
for name, granted in expected.items():
    for scope in ("contents", "security-events", "id-token"):
        if granted.get(scope) == "write":
            raise SystemExit(f"{name} holds {scope}: write on shared workflow token")
    if granted.get("checks") == "write" and name != "complete-authorization":
        raise SystemExit(f"{name} holds checks: write outside authorization terminalizer")
PY
then
  pass "every gate job declares its exact least-privilege permission set"
else
  fail "gate permission topology drifted or widened the shared workflow token"
fi

dispatch="$(awk '
  $0 == "  dispatch-merge:" { cap = 1; print; next }
  cap && /^  [A-Za-z_-]+:/ { exit }
  cap { print }
' "$wf")"
[ -n "$dispatch" ] || { echo "FAIL - dispatch-merge job not found in $wf"; exit 1; }

# `actions: write` can dispatch any workflow in the repository, so this job is
# the one place a PR-controlled string must never reach. It therefore checks out
# nothing, restores no cache or artifact, never evaluates a string, and never
# reads PR prose.
if grep -qE 'uses:|actions/(checkout|cache|upload-artifact|download-artifact)|\beval\b|^[[:space:]]*(source|\.)[[:space:]]|github\.event\.pull_request\.(title|body)' <<<"$dispatch"; then
  fail "dispatch job can consume or execute PR-controlled content"
else
  pass "dispatch job has no checkout, artifact/cache, eval/source, or PR prose"
fi

grep -qF 'GH_TOKEN: ${{ github.token }}' <<<"$dispatch" \
  && ! grep -q 'secrets\.' <<<"$dispatch" \
  && pass "dispatch runs on the scoped workflow token and receives no secret" \
  || fail "dispatch token binding exposes authority beyond the scoped workflow token"

grep -qF 'needs: [preflight, gate, complete-authorization]' <<<"$dispatch" \
  && grep -qF "needs.complete-authorization.result == 'success'" <<<"$dispatch" \
  && grep -qF "needs.complete-authorization.outputs.ai_authorized == 'true'" <<<"$dispatch" \
  && grep -qF "needs.preflight.outputs.authority == 'ai-merge'" <<<"$dispatch" \
  && pass "dispatch requires a successful authorization and an ai-merge authority" \
  || fail "dispatch dependency or authorization condition drifted"

# --- the shipped dispatch step, against a stubbed gh --------------------------
python3 - "$wf" >"$tmp/dispatch.sh" <<'PY'
import sys

import yaml

document = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
steps = document["jobs"]["dispatch-merge"]["steps"]
matches = [s for s in steps if s.get("name") == "Dispatch trusted terminal promotion"]
if len(matches) != 1 or not matches[0].get("run"):
    raise SystemExit("missing unique non-empty step: Dispatch trusted terminal promotion")
print(matches[0]["run"])
PY
[ -s "$tmp/dispatch.sh" ] || { echo "FAIL - could not extract the dispatch step from $wf"; exit 1; }

mkdir "$tmp/bin"
cat >"$tmp/bin/gh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CALLS"
case "$*" in
  "api repos/"*" --jq .default_branch // \"\"") printf '%s\n' "${DEFAULT_BRANCH_VALUE-main}" ;;
  "workflow run "*) [ "${DISPATCH_RC:-0}" -eq 0 ] || exit "$DISPATCH_RC" ;;
  *) echo "unexpected gh call: $*" >&2; exit 2 ;;
esac
STUB
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" CALLS="$tmp/calls"

run_dispatch() {
  : >"$CALLS"
  (
    export TARGET_REPO=Verjson/example
    export PR_NUMBER="${PR_NUMBER_VALUE-7}"
    export EXPECTED_HEAD_SHA="${HEAD_VALUE-1111111111111111111111111111111111111111}"
    export AUTHORIZATION_CHECK_ID="${CHECK_VALUE-9001}"
    export ARM_RUN_ID=8001 ARM_RUN_ATTEMPT=1 REVIEW_POLICY=cG9saWN5 GITHUB_RUN_ID=9999
    bash "$tmp/dispatch.sh"
  ) >"$tmp/out" 2>&1
}

if run_dispatch && [ "$(grep -c '^workflow run ' "$CALLS")" -eq 1 ] \
  && grep -q '^workflow run ai-privileged-merge.yml --repo Verjson/example --ref main ' "$CALLS"; then
  pass "a valid authorization dispatches the promotion once on the default branch"
else
  fail "valid trusted dispatch failed or did not target the default branch exactly once"
fi

grep -q -- '-f source_run_id=9999' "$CALLS" \
  && grep -q -- '-f expected_head_sha=1111111111111111111111111111111111111111' "$CALLS" \
  && grep -q -- '-f authorization_check_id=9001' "$CALLS" \
  && pass "dispatch forwards the exact head, authorization, and source-run identity" \
  || fail "dispatch identity forwarding drifted"

# Every identity the promotion later re-verifies is validated here first, so a
# malformed one cannot reach `gh workflow run` and mint a promotion attempt.
forged_case() {
  local label="$1" var="$2" value="$3"
  if (export "$var=$value"; run_dispatch); then
    fail "forged $label input dispatched"
  elif ! grep -q '^workflow run ' "$CALLS"; then
    pass "forged $label input is rejected before dispatch"
  else
    fail "forged $label input reached the dispatch call"
  fi
}
forged_case "pr number" PR_NUMBER_VALUE '7; rm -rf /'
forged_case "zero pr number" PR_NUMBER_VALUE 0
forged_case "head sha" HEAD_VALUE 'not-a-sha'
forged_case "short head sha" HEAD_VALUE 1111111111111111111111111111111111111
forged_case "uppercase head sha" HEAD_VALUE AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
forged_case "authorization check id" CHECK_VALUE 'abc'
forged_case "default branch" DEFAULT_BRANCH_VALUE 'main;evil'
forged_case "empty default branch" DEFAULT_BRANCH_VALUE ''

if (export DISPATCH_RC=1; run_dispatch); then
  fail "a failed dispatch reported green"
else
  pass "a failed dispatch fails the job"
fi

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."
exit 1
