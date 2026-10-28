#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/ai-post-merge.yml"
script="$root/scripts/ci-gate/post-merge-reconcile.sh"
grep -q 'pull_request_target:' "$workflow"
grep -q 'github.event.pull_request.merged == true' "$workflow"
grep -q 'ai-review-authorization:' "$workflow"
grep -q 'ai-review-run:' "$workflow"
grep -qF "AI_REVIEW_DISPATCH_WORKFLOW: \${{ github.repository == 'Verjson/.github' && 'ai-review-merge-dispatch.yml' || 'ai-review-merge.yml' }}" "$workflow"
grep -qF "AI_REVIEW_LEGACY_DISPATCH_WORKFLOW: \${{ github.repository == 'Verjson/.github' && 'ai-review-merge.yml' || '' }}" "$workflow"
grep -qF 'legacy_workflow_path != "" and .path == ".github/workflows/" + $legacy_workflow_path' "$workflow"
grep -q 'ref: \${{ github.sha }}' "$workflow"
! grep -q 'pull_request.base.sha' "$workflow"
grep -q "if: steps.evidence.outputs.eligible == 'true'" "$workflow"
! grep -Eq 'gh pr merge|workflow run ai-review-merge|sleep [0-9]' "$workflow" "$script"

python3 - "$root/.github/workflows/ai-privileged-merge.yml" <<'PY'
import json
import os
import re
import subprocess
import sys

import yaml

document = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
runs = [step.get("run", "") for job in document["jobs"].values() for step in job.get("steps", [])]
run = next(run for run in runs if 'authorization="$(gh api "repos/$TARGET_REPO/check-runs/$AUTHORIZATION_CHECK_ID")"' in run)
match = re.search(r'jq -e --arg head "\$EXPECTED_HEAD_SHA" \'(.*?)\' <<<"\$authorization"', run, re.S)
assert match, "privileged merge must validate the authorization check identity"

head = "1" * 40
for app_id, slug, accepted in (
    (15368, "github-actions", True),
    (4242, "ai-review-authorization", False),
    (9999, "untrusted-app", False),
):
    check = {
        "name": "AI review authorization",
        "head_sha": head,
        "status": "completed",
        "conclusion": "success",
        "app": {"id": app_id, "slug": slug},
    }
    result = subprocess.run(
        ["jq", "-e", "--arg", "head", head, match.group(1)],
        input=json.dumps(check),
        text=True,
        capture_output=True,
        env=os.environ,
        check=False,
    )
    assert (result.returncode == 0) == accepted, (app_id, slug, result.stderr)
PY
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

awk '$0=="      - name: Validate exact-head review evidence"{f=1;next} f&&$0=="        run: |"{r=1;next} r{if($0~/^      - name:/)exit;sub(/^          /,"");print}' \
  "$workflow" >"$tmp/evidence.sh"
[ -s "$tmp/evidence.sh" ] || { echo "FAIL - post-merge evidence block missing"; exit 1; }

mkdir "$tmp/evidence-bin"
cat >"$tmp/evidence-bin/gh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$EVIDENCE_LOG"
case "$*" in
  *"commits/"*"/check-runs --jq "*) cat "$CHECK_FILE" ;;
  "api --paginate "*"/reviews?per_page=100") cat "$REVIEWS_FILE" ;;
  "api repos/"*"/check-runs/9001") cat "$CHECK_FILE" ;;
  "api repos/"*"/actions/runs/8001") cat "$RUN_FILE" ;;
  "api repos/"*"/actions/runs/8001/jobs?per_page=100") cat "$JOBS_FILE" ;;
  "api repos/"*"/actions/runs/8001/artifacts?per_page=100") cat "$ARTIFACTS_FILE" ;;
  "api https://api.github.test/artifacts/81") printf 'fixture zip\n' ;;
  *) echo "unexpected evidence gh call: $*" >&2; exit 2 ;;
esac
STUB
cat >"$tmp/evidence-bin/unzip" <<'STUB'
#!/usr/bin/env bash
[ "$*" = "-p $RUNNER_TEMP/post-merge-evidence/evidence.zip attestation.json" ] || exit 2
cat "$ATTESTATION_SOURCE"
STUB
chmod +x "$tmp/evidence-bin/gh" "$tmp/evidence-bin/unzip"

export PATH="$tmp/evidence-bin:$PATH" EVIDENCE_LOG="$tmp/evidence.log"
export CHECK_FILE="$tmp/check.json" REVIEWS_FILE="$tmp/reviews.json" RUN_FILE="$tmp/run.json"
export JOBS_FILE="$tmp/jobs.json" ARTIFACTS_FILE="$tmp/artifacts.json"
export ATTESTATION_SOURCE="$tmp/attestation-source.json"
export TARGET_REPO=Verjson/example PR_NUMBER=7
export AI_REVIEW_LEGACY_DISPATCH_WORKFLOW=
export MERGED_HEAD_SHA=1111111111111111111111111111111111111111
export EXPECTED_APP_ID=4242 EXPECTED_APP_SLUG=ai-review-authorization
export DEFAULT_BRANCH=main RUNNER_TEMP="$tmp" GITHUB_OUTPUT="$tmp/evidence-output"
export AI_REVIEW_DISPATCH_WORKFLOW=ai-review-merge.yml

write_check() {
  local summary="$1" status="${2:-completed}" conclusion="${3:-success}"
  local app_id="${4:-15368}" app_slug="${5:-github-actions}"
  jq -nc --arg head "$MERGED_HEAD_SHA" --arg summary "$summary" --arg status "$status" \
    --arg conclusion "$conclusion" --argjson app_id "$app_id" --arg app_slug "$app_slug" \
    '{id:9001,name:"AI review authorization",head_sha:$head,status:$status,conclusion:$conclusion,
      app:{id:$app_id,slug:$app_slug},output:{summary:$summary}}' >"$CHECK_FILE"
}
write_valid_ai_evidence() {
  local workflow_path="${1:-.github/workflows/ai-review-merge.yml}"
  jq -nc --arg head "$MERGED_HEAD_SHA" \
    '{state:"APPROVED",commit_id:$head,user:{login:"ai-review-authorization[bot]"},
      body:"<!-- ai-review-authorization:9001 -->\n<!-- ai-review-run:8001 -->"}' \
    | jq -s '.' >"$REVIEWS_FILE"
  jq -nc --arg head "$MERGED_HEAD_SHA" --arg workflow_path "$workflow_path" --arg repo "$TARGET_REPO" \
    '{repository:{full_name:$repo},head_sha:"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",head_branch:"main",
      event:"workflow_dispatch",status:"completed",
      conclusion:"success",path:$workflow_path}' >"$RUN_FILE"
  printf '{"artifacts":[{"name":"merge-attestation-8001","expired":false,"archive_download_url":"https://api.github.test/artifacts/81"}]}\n' >"$ARTIFACTS_FILE"
  jq -nc --arg head "$MERGED_HEAD_SHA" --arg repo "$TARGET_REPO" \
    '{version:1,repository:$repo,pr_number:7,head_sha:$head,run_id:8001,followups:[]}' \
    >"$ATTESTATION_SOURCE"
}
run_evidence() {
  : >"$EVIDENCE_LOG"; : >"$GITHUB_OUTPUT"
  rm -rf "$RUNNER_TEMP/post-merge-evidence"
  bash "$tmp/evidence.sh" >"$tmp/evidence.out" 2>&1
}
expect_noop() {
  local label="$1"
  if run_evidence && grep -qx 'eligible=false' "$GITHUB_OUTPUT" \
     && ! grep -q '/reviews\|/artifacts' "$EVIDENCE_LOG"; then
    printf 'ok   - %s\n' "$label"
  else
    printf 'FAIL - %s\n' "$label"
    exit 1
  fi
}
expect_rejected() {
  local label="$1"
  if ! run_evidence && ! grep -q '/artifacts' "$EVIDENCE_LOG"; then
    printf 'ok   - %s\n' "$label"
  else
    printf 'FAIL - %s\n' "$label"
    exit 1
  fi
}
expect_legacy_noop() {
  local label="$1"
  if run_evidence && grep -qx 'eligible=false' "$GITHUB_OUTPUT" \
     && grep -q '/reviews' "$EVIDENCE_LOG" && grep -q '/jobs?per_page=100' "$EVIDENCE_LOG" \
     && ! grep -q '/artifacts' "$EVIDENCE_LOG"; then
    printf 'ok   - %s\n' "$label"
  else
    printf 'FAIL - %s\n' "$label"
    exit 1
  fi
}
write_dispatch_job() {
  jq -nc --arg conclusion "$1" \
    '{jobs:[{id:71,name:"dispatch-merge",status:"completed",conclusion:$conclusion}]}' >"$JOBS_FILE"
}

for outcome in human skipped inconclusive blocking failed-app-approval; do
  write_check "$outcome path; ordinary branch protection authorized the merge"
  expect_noop "$outcome post-merge path is a successful no-op"
done

write_check $'AI approval persisted.\n<!-- ai-review-authorized:v1:9001:1111111111111111111111111111111111111111:ai-approve -->'
expect_noop "ai-approve post-merge path is a successful no-op"
write_check $'AI approval persisted.\n<!-- ai-review-authorized:v1:9001:1111111111111111111111111111111111111111:ai-approve -->' completed failure
expect_rejected "unsuccessful ai-approve evidence fails closed"

write_valid_ai_evidence
write_check $'AI approval persisted.\n<!-- ai-review-authorized:v1:9001:1111111111111111111111111111111111111111:ai-merge -->'
if run_evidence && grep -qx 'eligible=true' "$GITHUB_OUTPUT" \
   && grep -q '^path=.*/post-merge-evidence/attestation.json$' "$GITHUB_OUTPUT"; then
  printf 'ok   - exact-head ai-merge evidence reaches attestation processing\n'
else
  echo 'FAIL - exact-head ai-merge evidence did not reach attestation processing'
  exit 1
fi
TARGET_REPO=Verjson/.github
AI_REVIEW_DISPATCH_WORKFLOW=ai-review-merge-dispatch.yml
AI_REVIEW_LEGACY_DISPATCH_WORKFLOW=ai-review-merge.yml
write_valid_ai_evidence .github/workflows/ai-review-merge-dispatch.yml
if run_evidence && grep -qx 'eligible=true' "$GITHUB_OUTPUT"; then
  printf 'ok - canonical current dispatch receipt path accepted\n'
else
  printf 'FAIL - canonical current dispatch receipt path rejected\n'
  exit 1
fi
write_valid_ai_evidence .github/workflows/ai-review-merge.yml
if run_evidence && grep -qx 'eligible=true' "$GITHUB_OUTPUT"; then
  printf 'ok - canonical legacy dispatch receipt path accepted\n'
else
  printf 'FAIL - canonical legacy dispatch receipt path rejected\n'
  exit 1
fi
write_valid_ai_evidence .github/workflows/ai-review-merge-dispatch-lookalike.yml
expect_rejected "canonical lookalike dispatch path rejected"
TARGET_REPO=Verjson/example
AI_REVIEW_DISPATCH_WORKFLOW=ai-review-merge.yml
AI_REVIEW_LEGACY_DISPATCH_WORKFLOW=

write_valid_ai_evidence
write_check $'AI approval persisted.\n<!-- ai-review-authorized:v1:9001:1111111111111111111111111111111111111111:ai-merge -->' completed success 15368 github-actions
write_dispatch_job success
if run_evidence && grep -qx 'eligible=true' "$GITHUB_OUTPUT"; then
  printf 'ok - Actions-owned exact-head authorization check is accepted\n'
else
  echo 'FAIL - Actions-owned authorization check did not reach attestation processing'
  exit 1
fi

write_valid_ai_evidence
write_check $'AI approval persisted.\n<!-- ai-review-authorized:v1:9001:1111111111111111111111111111111111111111:ai-merge -->' completed success "$EXPECTED_APP_ID" "$EXPECTED_APP_SLUG"
expect_rejected "legacy review-App-owned authorization is rejected after compatibility retirement"

write_valid_ai_evidence
write_check $'Legacy AI approval persisted.\n<!-- ai-review-authorized:v1:9001:1111111111111111111111111111111111111111 -->'
write_dispatch_job success
if run_evidence && grep -qx 'eligible=true' "$GITHUB_OUTPUT" \
   && grep -q '/jobs?per_page=100' "$EVIDENCE_LOG" \
   && grep -q '^path=.*/post-merge-evidence/attestation.json$' "$GITHUB_OUTPUT"; then
  printf 'ok   - legacy exact-head marker with successful ai-merge dispatch is accepted\n'
else
  echo "FAIL - legacy ai-merge dispatch evidence was not accepted: $(tail -1 "$tmp/evidence.out")"
  sed 's/^/call: /' "$EVIDENCE_LOG"
  exit 1
fi

write_dispatch_job skipped
expect_legacy_noop "legacy ai-approve marker with skipped dispatch is a successful no-op"
write_dispatch_job failure
expect_rejected "legacy marker with failed dispatch evidence fails closed"
printf '{"jobs":[]}\n' >"$JOBS_FILE"
expect_rejected "legacy marker without dispatch evidence fails closed"
printf '{"jobs":[{"name":"dispatch-merge","status":"completed","conclusion":"success"},{"name":"dispatch-merge","status":"completed","conclusion":"success"}]}\n' >"$JOBS_FILE"
expect_rejected "ambiguous legacy dispatch evidence fails closed"

write_check $'AI approval persisted.\n<!-- ai-review-authorized:v1:9001:2222222222222222222222222222222222222222:ai-merge -->'
expect_rejected "stale AI authorization marker fails closed"
write_check $'AI approval persisted.\n<!-- ai-review-authorized:v1:9999:1111111111111111111111111111111111111111:ai-merge -->'
expect_rejected "forged AI authorization marker fails closed"
write_check $'AI approval persisted.\n<!-- ai-review-authorized:v1:not-a-check:1111111111111111111111111111111111111111:ai-merge -->'
expect_rejected "malformed AI authorization marker fails closed"
write_check $'AI approval persisted.\n<!-- ai-review-authorized:v1:9001:1111111111111111111111111111111111111111:ai-merge -->' completed failure
expect_rejected "unsuccessful check cannot carry AI merge evidence"
write_check $'AI approval persisted.\n<!-- ai-review-authorized:v1:9001:1111111111111111111111111111111111111111:ai-merge -->'
jq '.[0].body="<!-- ai-review-authorization:9999 -->\n<!-- ai-review-run:8001 -->"' \
  "$REVIEWS_FILE" >"$tmp/changed-reviews.json" && mv "$tmp/changed-reviews.json" "$REVIEWS_FILE"
expect_rejected "approval bound to another authorization check fails closed"

PATH="${PATH#*:}"
mkdir "$tmp/bin"
cat >"$tmp/bin/gh" <<'STUB'
#!/usr/bin/env bash
case "$1 $2" in
  "issue list") printf '%s\n' "${EXISTING:-0}" ;;
  "issue create") printf 'create %s\n' "$*" >>"$GH_LOG" ;;
  "api repos/"*) printf '%s\n' "${LIVE_SHA:-}" ;;
  "api --method")
    printf 'delete %s\n' "$*" >>"$GH_LOG"
    [ -z "${DELETE_ERR:-}" ] || printf '%s\n' "$DELETE_ERR" >&2
    exit "${DELETE_RC:-0}" ;;
  *) exit 2 ;;
esac
STUB
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" GH_LOG="$tmp/gh.log"
export TARGET_REPO=Verjson/example PR_NUMBER=7 HEAD_REPO=Verjson/example HEAD_REF=feature/test
export BASE_REF=main DEFAULT_BRANCH=main MERGED_HEAD_SHA=1111111111111111111111111111111111111111
export LIVE_SHA="$MERGED_HEAD_SHA"
export ATTESTATION_FILE="$tmp/attestation.json"
jq -n '{followups:[{location:"src/a.ts:3",note:"Add coverage."},{location:"src/a.ts:3",note:"Add coverage."}]}' >"$ATTESTATION_FILE"
bash "$script"
[ "$(grep -c '^create ' "$GH_LOG")" -eq 1 ]
grep -q '^delete ' "$GH_LOG"
: >"$GH_LOG"
EXISTING=1 HEAD_REPO=someone/fork bash "$script"
[ ! -s "$GH_LOG" ]

: >"$GH_LOG"
EXISTING=1 LIVE_SHA=2222222222222222222222222222222222222222 bash "$script"
[ ! -s "$GH_LOG" ]

: >"$GH_LOG"
EXISTING=1 HEAD_REF=main LIVE_SHA="$MERGED_HEAD_SHA" bash "$script"
[ ! -s "$GH_LOG" ]

: >"$GH_LOG"
EXISTING=1 HEAD_REF=release BASE_REF=release DEFAULT_BRANCH=main LIVE_SHA="$MERGED_HEAD_SHA" bash "$script"
[ ! -s "$GH_LOG" ]

# Branch cleanup is housekeeping, never a verdict (#458, carried onto the ADR 0081
# post-merge topology by #1320). A repository with auto-delete-branch-on-merge has
# already removed the ref, so the DELETE 404s; if that status reached the job the
# PR would be merged and the reconcile red, which is what #458 reported. The
# notice must be emitted and the exit status must stay 0.
: >"$GH_LOG"
DELETE_RC=1 DELETE_ERR='gh: Reference does not exist (HTTP 404)' EXISTING=1 \
  bash "$script" >"$tmp/cleanup.out" 2>&1
grep '^delete ' "$GH_LOG" >/dev/null
grep '::notice::merged head ref already absent or protected' "$tmp/cleanup.out" >/dev/null

# ...but only a 404 is that race. The read on the line above the DELETE returned the
# expected sha, so any other failure means a cleanup path that is not working, and
# reporting it as "already absent" was a guess about a result the step never read.
# It stays exit 0 for the same #458 reason, and now says what actually happened.
: >"$GH_LOG"
DELETE_RC=1 DELETE_ERR='gh: Resource not accessible by integration (HTTP 403)' EXISTING=1 \
  bash "$script" >"$tmp/cleanup.out" 2>&1
grep '^delete ' "$GH_LOG" >/dev/null
grep '::warning::could not delete the merged head ref: .*HTTP 403' "$tmp/cleanup.out" >/dev/null
! grep 'already absent or protected' "$tmp/cleanup.out" >/dev/null

echo "All tests passed."
