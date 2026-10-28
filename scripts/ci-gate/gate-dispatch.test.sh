#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/gate-rearm.yml"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
fails=0; pass(){ printf 'ok   - %s\n' "$1"; }; fail(){ printf 'FAIL - %s\n' "$1"; fails=$((fails+1)); }
awk '$0=="      - name: Dispatch trusted review after receipt publication"{f=1;next} f&&$0=="        run: |"{r=1;next} r{if($0~/^      - name:/)exit;sub(/^          /,"");print}' "$workflow" >"$tmp/dispatch.sh"
cp "$tmp/dispatch.sh" "$tmp/dispatch-explicit.sh"
cp "$tmp/dispatch.sh" "$tmp/dispatch-ai-review.sh"
sed -i 's/${{ steps.arm.outputs.explicit_rereview || false }}/false/' "$tmp/dispatch.sh"
sed -i 's/${{ steps.arm.outputs.explicit_rereview || false }}/true/' "$tmp/dispatch-explicit.sh"
sed -i 's/${{ steps.arm.outputs.explicit_rereview || false }}/false/' "$tmp/dispatch-ai-review.sh"
sed -i 's/${{ steps.arm.outputs.explicit_ai_review || false }}/false/' "$tmp/dispatch.sh"
sed -i 's/${{ steps.arm.outputs.explicit_ai_review || false }}/false/' "$tmp/dispatch-explicit.sh"
sed -i 's/${{ steps.arm.outputs.explicit_ai_review || false }}/true/' "$tmp/dispatch-ai-review.sh"
sed -i 's/${{ steps.arm.outputs.review_policy }}/eyJhY3RvciI6InRydXN0ZWQtYXJtIiwiYWN0b3JfcGVybWlzc2lvbiI6ImF1dG9tYXRpb24iLCJidWRnZXRfdXNkIjoiYXV0byIsIm1vZGVsIjoiYXV0byIsInByaWNpbmdfdmVyc2lvbiI6ImFudGhyb3BpYy1uYXRpdmUtdjEiLCJwcm92aWRlciI6ImFudGhyb3BpYyJ9/' "$tmp/dispatch.sh"
sed -i 's/${{ steps.arm.outputs.review_policy }}/eyJhY3RvciI6InRydXN0ZWQtYXJtIiwiYWN0b3JfcGVybWlzc2lvbiI6ImF1dG9tYXRpb24iLCJidWRnZXRfdXNkIjoiYXV0byIsIm1vZGVsIjoiYXV0byIsInByaWNpbmdfdmVyc2lvbiI6ImFudGhyb3BpYy1uYXRpdmUtdjEiLCJwcm92aWRlciI6ImFudGhyb3BpYyJ9/' "$tmp/dispatch-explicit.sh"
sed -i 's/${{ steps.arm.outputs.review_policy }}/eyJhY3RvciI6InRydXN0ZWQtYXJtIiwiYWN0b3JfcGVybWlzc2lvbiI6ImF1dG9tYXRpb24iLCJidWRnZXRfdXNkIjoiYXV0byIsIm1vZGVsIjoiYXV0byIsInByaWNpbmdfdmVyc2lvbiI6ImFudGhyb3BpYy1uYXRpdmUtdjEiLCJwcm92aWRlciI6ImFudGhyb3BpYyJ9/' "$tmp/dispatch-ai-review.sh"
[ -s "$tmp/dispatch.sh" ] || { echo "FAIL - dispatch block missing"; exit 1; }
mkdir "$tmp/bin"
cat >"$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$CALLS"
case "$*" in
  "workflow run "*) [ "${DISPATCH_FAIL:-false}" != true ] ;;
  "api --method PATCH "*) exit 0 ;;
  "pr edit "*)
    [ "${REMOVE_FAIL:-false}" != true ] || exit 1
    printf 'absent\n' >"$LABEL_FILE" ;;
  "pr view "*)
    if [ "$(cat "$LABEL_FILE")" = present ]; then
      printf '{"labels":[{"name":"%s"}]}\n' "$LABEL_NAME"
    else
      printf '{"labels":[]}\n'
    fi ;;
  *) exit 2 ;;
esac
SH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" DEFAULT_BRANCH=main TARGET_REPO=Verjson/example PR_NUMBER=7
export AI_REVIEW_DISPATCH_WORKFLOW=ai-review-merge.yml
export EXPECTED_HEAD_SHA=0123456789abcdef0123456789abcdef01234567 CHECK_ID=9001 ACTIONS_TOKEN=actions-token GH_TOKEN=actions-token
export GITHUB_RUN_ID=7001 GITHUB_RUN_ATTEMPT=2 EVENT_ACTION=synchronize EVENT_LABEL=''
export LABEL_FILE="$tmp/label-state"
export LABEL_NAME=re-review
printf 'absent\n' >"$LABEL_FILE"
run_dispatch(){ : >"$CALLS"; bash "$tmp/dispatch.sh"; }
run_explicit_dispatch(){ : >"$CALLS"; bash "$tmp/dispatch-explicit.sh"; }
run_ai_review_dispatch(){ : >"$CALLS"; bash "$tmp/dispatch-ai-review.sh"; }
if run_dispatch >/dev/null 2>&1 && grep -q 'arm_run_id=7001' "$CALLS" && ! grep -q 'method PATCH' "$CALLS"; then pass "successful dispatch carries exact arm receipt identity once"; else fail "successful trusted dispatch drifted"; fi
export DISPATCH_FAIL=true
if run_dispatch >/dev/null 2>&1; then fail "dispatch failure reported green"; elif grep -q 'method PATCH.*check-runs/9001' "$CALLS"; then pass "dispatch failure completes the dedicated-App check as failure"; else fail "dispatch failure did not fail the authorization"; fi
unset DISPATCH_FAIL
DEFAULT_BRANCH='../bad?ref'
if run_dispatch >/dev/null 2>&1; then fail "unsafe default branch reached dispatch"; else pass "unsafe dispatch ref fails closed"; fi
DEFAULT_BRANCH=main

printf 'present\n' >"$LABEL_FILE"
if run_explicit_dispatch >/dev/null 2>&1 \
  && grep -q 'pr edit 7 --repo Verjson/example --remove-label re-review' "$CALLS" \
  && grep -q 'pr view 7 --repo Verjson/example --json labels' "$CALLS" \
  && grep -q 'workflow run ai-review-merge.yml' "$CALLS"; then
  pass "explicit dispatch consumes and verifies the re-review label before provider dispatch"
else
  fail "explicit dispatch did not make label consumption authoritative"
fi

printf 'present\n' >"$LABEL_FILE"
export REMOVE_FAIL=true
if run_explicit_dispatch >/dev/null 2>&1; then
  fail "retained re-review label reached provider dispatch"
elif grep -q 'method PATCH.*check-runs/9001' "$CALLS" \
  && ! grep -q 'workflow run ai-review-merge.yml' "$CALLS"; then
  pass "failed label removal with a retained label fails the check before provider spend"
else
  fail "retained label failure did not close the authorization check"
fi
unset REMOVE_FAIL

LABEL_NAME=ai-review
printf 'present\n' >"$LABEL_FILE"
if run_ai_review_dispatch >/dev/null 2>&1 \
  && grep -q 'pr edit 7 --repo Verjson/example --remove-label ai-review' "$CALLS" \
  && grep -q 'workflow run ai-review-merge.yml' "$CALLS"; then
  pass "synchronized explicit dispatch consumes the ai-review label before provider dispatch"
else
  fail "synchronized explicit dispatch did not consume ai-review authorization"
fi

printf 'present\n' >"$LABEL_FILE"
export REMOVE_FAIL=true
if run_ai_review_dispatch >/dev/null 2>&1; then
  fail "retained ai-review label reached provider dispatch"
elif grep -q 'method PATCH.*check-runs/9001' "$CALLS" \
  && ! grep -q 'workflow run ai-review-merge.yml' "$CALLS"; then
  pass "failed ai-review label consumption fails before provider spend"
else
  fail "retained ai-review label did not fail the authorization check"
fi
unset REMOVE_FAIL

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }; echo "$fails test(s) failed."; exit 1
