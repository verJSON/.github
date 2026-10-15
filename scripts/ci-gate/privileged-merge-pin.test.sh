#!/usr/bin/env bash
# ADR 0042 requires a consumer's thin caller to pin `@main`, and until #278
# nothing enforced it. The caller file lives in the CONSUMER's repository, so
# `scripts/gen-privileged-merge-caller.sh` and `scripts/node-workflow-pins.test.sh`
# — both of which run here — bind nothing downstream. The existing runtime anchor
# pins the GATE's revision (`trusted_workflow_sha`), not this workflow's own, so
# ai-privileged-merge.yml could itself be executing from a fork or a stale ref
# with every other check passing.
#
# The step's `run:` block is extracted from the shipped workflow (single source
# of truth) and exercised against a stubbed `gh`, per house convention.
#
# Every negative case asserts the SPECIFIC terminal error, so a harness
# regression cannot make a case pass by failing somewhere else — the failure
# mode that let a fail-open guard ship green before.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
wf="$repo_root/.github/workflows/ai-privileged-merge.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

step_name="Authorize terminal merge from trusted metadata"
extract_terminal_merge_script() {
  local source="$1" destination="$2"
  python3 - "$source" "$step_name" >"$destination" <<'EXTRACT_PY'
import sys

import yaml

workflow = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
steps = workflow["jobs"]["privileged_merge"]["steps"]
matches = [step for step in steps if step.get("name") == sys.argv[2]]
if len(matches) != 1:
    raise SystemExit(f"expected exactly one {sys.argv[2]!r} step, found {len(matches)}")
script = matches[0].get("run")
if not isinstance(script, str) or not script.strip():
    raise SystemExit(f"{sys.argv[2]!r} has no non-empty run script")
print(script, end="" if script.endswith("\n") else "\n")
EXTRACT_PY
}

script="$tmp/privileged.sh"
extract_terminal_merge_script "$wf" "$script" \
  && [ -s "$script" ] \
  && grep -qF 'canonical_main="$(gh api repos/Verjson/.github/commits/main' "$script" \
  && pass "the terminal merge step is found and its non-empty script is extracted" \
  || { echo "FAIL - could not extract the pin check from $wf"; exit 1; }

python3 - "$wf" "$tmp/renamed.yml" "$step_name" <<'MUTATE_NAME_PY'
import sys

import yaml

workflow = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for step in workflow["jobs"]["privileged_merge"]["steps"]:
    if step.get("name") == sys.argv[3]:
        step["name"] = "Renamed terminal merge step"
yaml.safe_dump(workflow, open(sys.argv[2], "w", encoding="utf-8"), sort_keys=False)
MUTATE_NAME_PY
if extract_terminal_merge_script "$tmp/renamed.yml" "$tmp/renamed.sh" 2>/dev/null; then
  fail "renaming the terminal merge step left the extraction guard green"
else
  pass "renaming the terminal merge step makes extraction fail closed"
fi

python3 - "$wf" "$tmp/empty.yml" "$step_name" <<'MUTATE_RUN_PY'
import sys

import yaml

workflow = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for step in workflow["jobs"]["privileged_merge"]["steps"]:
    if step.get("name") == sys.argv[3]:
        step["run"] = ""
yaml.safe_dump(workflow, open(sys.argv[2], "w", encoding="utf-8"), sort_keys=False)
MUTATE_RUN_PY
if extract_terminal_merge_script "$tmp/empty.yml" "$tmp/empty.sh" 2>/dev/null; then
  fail "an empty terminal merge script left the extraction guard green"
else
  pass "an empty terminal merge script makes extraction fail closed"
fi

# --- the WIRING, not just the values ----------------------------------------
# Everything below injects EXECUTING_WORKFLOW_* directly, so assert their
# placement structurally as well as exercising their values.
python3 - "$wf" <<'WIRING_PY' && pass "workflow identity is declared on the STEP, where the job context exists" \
  || fail "EXECUTING_WORKFLOW_* is missing or declared at job level, where \`job\` resolves empty"
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
job = d["jobs"]["privileged_merge"]
# Job-level env must NOT carry them: `job` is not an available context there.
if any(k.startswith("EXECUTING_WORKFLOW_") for k in (job.get("env") or {})):
    sys.exit(1)
matches = [step for step in job["steps"] if step.get("name") == "Authorize terminal merge from trusted metadata"]
if len(matches) != 1:
    sys.exit(1)
step = matches[0]
env = step.get("env") or {}
want = {
    "EXECUTING_WORKFLOW_SHA": "${{ job.workflow_sha }}",
    "EXECUTING_WORKFLOW_REPOSITORY": "${{ job.workflow_repository }}",
}
sys.exit(0 if all(env.get(k) == v for k, v in want.items()) else 1)
WIRING_PY

MAIN_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
OLD_MAIN_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
FORK_SHA=cccccccccccccccccccccccccccccccccccccccc

mkdir -p "$tmp/bin"
printf '#!/usr/bin/env bash\nexit 0\n' >"$tmp/bin/sleep"

# The stub answers only what the pin check needs and then makes the run stop.
# Anything unstubbed exits non-zero: falling through to a bare success with
# empty stdout is the fail-open shape this whole file exists to prevent.
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
set -uo pipefail
ARGV=("$@")
args="$*"
emit() {
  local payload="$1" filter="" i
  for ((i = 0; i < ${#ARGV[@]}; i++)); do
    [ "${ARGV[$i]}" = "--jq" ] && filter="${ARGV[$((i + 1))]}"
  done
  if [ -n "$filter" ]; then jq -r "$filter" <<<"$payload"; else printf '%s\n' "$payload"; fi
  exit 0
}
case "$args" in
  *"/compare/"*)
    [ "${COMPARE_RC:-0}" -eq 0 ] || exit "${COMPARE_RC}"
    emit "$COMPARE_FIXTURE" ;;
  *"repos/Verjson/.github/actions/workflows/ai-review-merge.yml"*) emit '{"id":312358392}' ;;
  *"repos/Verjson/.github/commits/main"*) emit "{\"sha\":\"$MAIN_SHA\"}" ;;
  *".default_branch"*) emit '{"default_branch":"main"}' ;;
  # Reached only AFTER the pin check passes. Failing here is how a positive case
  # proves the check let the run through rather than silently ending early.
  *"pr view"*) echo "REACHED_PAST_PIN_CHECK" >&2; exit 42 ;;
esac
echo "UNSTUBBED gh $args" >&2
exit 1
GH
chmod +x "$tmp/bin/gh" "$tmp/bin/sleep"

run_case() {
  PATH="$tmp/bin:$PATH" LC_ALL=C.UTF-8 \
  GH_TOKEN=stub-token RUNNER_TEMP="$tmp" \
  TARGET_REPO="${GITHUB_REPOSITORY_OVERRIDE:-Verjson/example}" TARGET_OWNER=Verjson \
  GITHUB_REPOSITORY="${GITHUB_REPOSITORY_OVERRIDE:-Verjson/example}" \
  GITHUB_REPOSITORY_OWNER=Verjson CALLER_REF=refs/heads/main \
  AUTHORIZATION_CHECK_ID=123 \
  PR_NUMBER=7 EXPECTED_HEAD_SHA=f7d77ea9044bc2352423d4e6eca5c63c1847201d \
  GITHUB_EVENT_NAME=pull_request_target \
  MAIN_SHA="$MAIN_SHA" \
  EXECUTING_WORKFLOW_SHA="${EXECUTING_WORKFLOW_SHA:-}" \
  EXECUTING_WORKFLOW_REPOSITORY="${EXECUTING_WORKFLOW_REPOSITORY:-}" \
  COMPARE_FIXTURE="${COMPARE_FIXTURE:-}" COMPARE_RC="${COMPARE_RC:-0}" \
    bash "$script" 2>&1
}

# --- the executing revision IS main -----------------------------------------
# Equality short-circuits before any compare call. The stub proves the run got
# past the check by failing loudly at the next API call.
out="$(EXECUTING_WORKFLOW_SHA="$MAIN_SHA" EXECUTING_WORKFLOW_REPOSITORY=Verjson/.github run_case)"
grep -qF 'REACHED_PAST_PIN_CHECK' <<<"$out" \
  && pass "a run at main's tip passes without a compare call" \
  || fail "the tip revision was rejected: $out"

# --- reachable from main, i.e. main advanced mid-flight ----------------------
out="$(EXECUTING_WORKFLOW_SHA="$OLD_MAIN_SHA" EXECUTING_WORKFLOW_REPOSITORY=Verjson/.github \
  COMPARE_FIXTURE='{"status":"ahead","behind_by":0}' run_case)"
grep -qF 'REACHED_PAST_PIN_CHECK' <<<"$out" \
  && pass "a revision still reachable from main is accepted" \
  || fail "an ancestor of main was rejected: $out"

# --- not on main at all ------------------------------------------------------
for status in behind diverged; do
  out="$(EXECUTING_WORKFLOW_SHA="$FORK_SHA" EXECUTING_WORKFLOW_REPOSITORY=Verjson/.github \
    COMPARE_FIXTURE="{\"status\":\"$status\",\"behind_by\":3}" run_case)"
  grep -qF 'executing promotion revision is not reachable from canonical main' <<<"$out" \
    && ! grep -qF 'REACHED_PAST_PIN_CHECK' <<<"$out" \
    && pass "a '$status' revision is rejected and the run stops there" \
    || fail "a '$status' revision was not rejected: $out"
done

# --- the comparison itself cannot be read ------------------------------------
# Undetermined provenance must stop the run rather than falling through.
out="$(EXECUTING_WORKFLOW_SHA="$FORK_SHA" EXECUTING_WORKFLOW_REPOSITORY=Verjson/.github \
  COMPARE_RC=1 run_case)"
if ! grep -qF 'REACHED_PAST_PIN_CHECK' <<<"$out"; then
  pass "an unreadable comparison fails closed"
else
  fail "unreadable comparison did not fail closed: $out"
fi

# A well-formed response missing `.status` must not be read as acceptance.
out="$(EXECUTING_WORKFLOW_SHA="$FORK_SHA" EXECUTING_WORKFLOW_REPOSITORY=Verjson/.github \
  COMPARE_FIXTURE='{}' run_case)"
grep -qF 'executing promotion revision is not reachable from canonical main' <<<"$out" \
  && ! grep -qF 'REACHED_PAST_PIN_CHECK' <<<"$out" \
  && pass "a comparison with no status is rejected" \
  || fail "an empty comparison was accepted: $out"

# --- the workflow is not this repository's -----------------------------------
out="$(EXECUTING_WORKFLOW_SHA="$MAIN_SHA" EXECUTING_WORKFLOW_REPOSITORY=Attacker/evil run_case)"
grep -qF 'promotion is not executing from the canonical workflow repository' <<<"$out" \
  && ! grep -qF 'REACHED_PAST_PIN_CHECK' <<<"$out" \
  && pass "a workflow executing from another repository is rejected" \
  || fail "a foreign workflow repository was accepted: $out"

# --- the canonical repository in its renamed display case --------------------
# GitHub names are ASCII-case-insensitive identities (ADR 0213). The 2026-10-02
# organization rename made a local `./` reusable call report `verJSON/.github`:
# AiB run 38790031475 refused its promotion retry on exactly this spelling.
for repository in verJSON/.github VERJSON/.GITHUB; do
  out="$(EXECUTING_WORKFLOW_SHA="$MAIN_SHA" EXECUTING_WORKFLOW_REPOSITORY="$repository" run_case)"
  grep -qF 'REACHED_PAST_PIN_CHECK' <<<"$out" \
    && pass "the canonical repository spelled '$repository' is accepted" \
    || fail "the canonical repository '$repository' was rejected: $out"
done

# Case folding is an identity only over ASCII. Each rejected spelling defeats a
# specific weaker comparison: an owner-only match, a prefix or substring match,
# a regex whose `.` is a wildcard, untrimmed whitespace, and locale-aware
# lowering — `${x,,}` under the runner's C.UTF-8 folds U+0130 to `i` and
# casefold() folds U+017F to `s`, while U+0456 is a plain Cyrillic lookalike.
for repository in verjson/other evil/.github Verjson/.github-x Verjson/xgithub \
  evil/Verjson/.github ' Verjson/.github' $'Verjson/.github\n' \
  'Verjson/.gİthub' 'Verjſon/.github' 'Verjson/.gіthub'; do
  out="$(EXECUTING_WORKFLOW_SHA="$MAIN_SHA" EXECUTING_WORKFLOW_REPOSITORY="$repository" run_case)"
  grep -qF 'promotion is not executing from the canonical workflow repository' <<<"$out" \
    && ! grep -qF 'REACHED_PAST_PIN_CHECK' <<<"$out" \
    && pass "a non-canonical workflow repository $(printf '%q' "$repository") is rejected" \
    || fail "a non-canonical workflow repository $(printf '%q' "$repository") was accepted: $out"
done

# --- the revision cannot be determined ---------------------------------------
out="$(run_case)"
grep -qF 'promotion is not executing from the canonical workflow repository' <<<"$out" \
  && ! grep -qF 'REACHED_PAST_PIN_CHECK' <<<"$out" \
  && pass "an absent workflow identity fails closed" \
  || fail "absent workflow identity did not fail closed: $out"

# A malformed SHA must be rejected on shape before it can reach the comparison.
out="$(EXECUTING_WORKFLOW_SHA='main; rm -rf /' EXECUTING_WORKFLOW_REPOSITORY=Verjson/.github run_case)"
if ! grep -qF 'REACHED_PAST_PIN_CHECK' <<<"$out"; then
  pass "a non-SHA revision value is rejected before the pin check can pass"
else
  fail "a malformed revision was accepted: $out"
fi

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
