#!/usr/bin/env bash
# Permit an explicit re-review workflow rerun only when every earlier attempt
# stopped before the paid provider boundary and the immutable arm receipt still
# binds the exact check, head, policy, and App identity.
set -euo pipefail

for value in TARGET_REPO PR_NUMBER EXPECTED_HEAD_SHA AUTHORIZATION_CHECK_ID ARM_RUN_ID ARM_RUN_ATTEMPT EXPECTED_APP_ID EXPECTED_APP_SLUG REVIEW_POLICY REVIEW_RUN_ID REVIEW_RUN_ATTEMPT DEFAULT_BRANCH; do
  [ -n "${!value:-}" ] || { echo "::error::missing zero-provider recovery identity: $value"; exit 1; }
done
[[ "$TARGET_REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || exit 1
if [[ "${TARGET_REPO,,}" == "verjson/.github" ]]; then
  dispatch_workflow="ai-review-merge-dispatch.yml"
else
  dispatch_workflow="ai-review-merge.yml"
fi
# Both the canonical dispatcher and generated adopter caller invoke the
# reusable review workflow from a job named `review`; GitHub prefixes each
# called job with that caller job name in the Actions API.
dispatch_job_prefix="review / "
dispatch_workflow_path=".github/workflows/$dispatch_workflow"
[[ "$PR_NUMBER" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$EXPECTED_HEAD_SHA" =~ ^[0-9a-f]{40}$ ]] || exit 1
[[ "$AUTHORIZATION_CHECK_ID" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$ARM_RUN_ID" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$ARM_RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$EXPECTED_APP_ID" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$EXPECTED_APP_SLUG" =~ ^[a-z0-9][a-z0-9-]*$ ]] || exit 1
[[ "$REVIEW_POLICY" =~ ^[A-Za-z0-9_-]{1,2048}$ ]] || exit 1
[[ "$REVIEW_RUN_ID" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$REVIEW_RUN_ATTEMPT" =~ ^[1-9][0-9]*$ ]] || exit 1
[[ "$DEFAULT_BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] &&
  [[ "$DEFAULT_BRANCH" != /* && "$DEFAULT_BRANCH" != *..* && "$DEFAULT_BRANCH" != *//* ]] || exit 1
for tool in gh jq; do command -v "$tool" >/dev/null || exit 1; done

receipt_verifier="${ZERO_PROVIDER_RECEIPT_VERIFIER:-$(dirname "$0")/verify-arm-receipt.sh}"
[ -f "$receipt_verifier" ] || { echo "::error::zero-provider receipt verifier is unavailable"; exit 1; }
GH_TOKEN="${GH_TOKEN:-}" bash "$receipt_verifier" || {
  echo "::error::zero-provider recovery receipt identity mismatch"
  exit 1
}

tmp="$(mktemp -d "${RUNNER_TEMP:-/tmp}/ai-zero-provider-recovery.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
api() {
  local phase="$1" output="$2"
  shift 2
  if ! gh api "$@" >"$output" 2>"$tmp/$phase.stderr"; then
    echo "::error::$phase lookup failed"
    return 1
  fi
}

api review-run "$tmp/review-run.json" "repos/$TARGET_REPO/actions/runs/$REVIEW_RUN_ID"
expected_title="AI review authorization $AUTHORIZATION_CHECK_ID from arm $ARM_RUN_ID.$ARM_RUN_ATTEMPT"
jq -e --argjson run "$REVIEW_RUN_ID" --argjson attempt "$REVIEW_RUN_ATTEMPT" \
  --arg title "$expected_title" --arg branch "$DEFAULT_BRANCH" --arg repo "$TARGET_REPO" \
  --arg workflow_path "$dispatch_workflow_path" '
    .id == $run and .run_attempt == $attempt and .event == "workflow_dispatch" and
  .path == $workflow_path and .display_title == $title and
    .head_branch == $branch and (.head_repository.full_name | ascii_downcase) == ($repo | ascii_downcase) and
    (.repository.full_name | ascii_downcase) == ($repo | ascii_downcase) and
    # A workflow_dispatch run can remain queued while an environment-gated
    # sibling job is waiting for admission. That live status is still the
    # current run and must not be mistaken for a different dispatch (#1393).
    (.status == "queued" or .status == "in_progress")
  ' "$tmp/review-run.json" >/dev/null || {
    echo "::error::zero-provider recovery run identity mismatch"
    exit 1
  }

api current-head "$tmp/pr.json" "repos/$TARGET_REPO/pulls/$PR_NUMBER"
jq -e --arg head "$EXPECTED_HEAD_SHA" '
  .state == "open" and .head.sha == $head
  ' "$tmp/pr.json" >/dev/null || {
    echo "::error::zero-provider recovery head is stale"
    exit 1
  }

api authorization-check "$tmp/check.json" "repos/$TARGET_REPO/check-runs/$AUTHORIZATION_CHECK_ID"
jq -e --argjson id "$AUTHORIZATION_CHECK_ID" --arg head "$EXPECTED_HEAD_SHA" '
    .id == $id and .name == "AI review authorization" and .head_sha == $head and
    .app.id == 15368 and .app.slug == "github-actions"
  ' "$tmp/check.json" >/dev/null || {
    echo "::error::zero-provider recovery check or App identity mismatch"
    exit 1
  }

if [ "$REVIEW_RUN_ATTEMPT" -eq 1 ]; then
  jq -e '.status == "in_progress" and .conclusion == null' "$tmp/check.json" >/dev/null || {
    echo "::error::initial direct review cannot replay a retained authorization"
    exit 1
  }
  jq -e '
    .actor.login == "github-actions[bot]" and
    .triggering_actor.login == "github-actions[bot]"
    ' "$tmp/review-run.json" >/dev/null || {
      echo "::error::initial direct review dispatch is not trusted-arm owned"
      exit 1
    }
  # Verjson/.github#1480 — this predicate used to fold three unrelated
  # conditions behind one "missing or not unique" message, so a run that had
  # simply not been indexed yet was reported identically to a genuine duplicate
  # dispatch and neither could be triaged from the log.
  #
  # They are now separated, because they call for opposite handling. An absent
  # run is eventual consistency on GET /actions/runs and self-heals, so it is
  # bound-retried. A duplicate never self-heals: retrying it would only widen
  # the window in which a second dispatch could be accepted as the trusted one,
  # so it is refused on the first look. Neither assertion is relaxed — more than
  # one match and a non-1 run_attempt both still fail closed.
  #
  # Read "absent" precisely: the retry tolerates an empty *result* inside a
  # successful, parseable listing. It does not tolerate an absent answer. A
  # failed listing request returns 1 from api() and `set -e` aborts the script
  # before this loop can re-ask, so a transient fetch failure fails closed
  # immediately rather than being retried. That is the #1476 boundary applied
  # here: an absence within an answer may be retried, an absent answer may not.
select_matching='[.[].workflow_runs[] | select(
      .display_title == $title and .event == "workflow_dispatch" and
  .path == $workflow_path and .head_branch == $branch and
      (.head_repository.full_name | ascii_downcase) == ($repo | ascii_downcase) and (.repository.full_name | ascii_downcase) == ($repo | ascii_downcase)
    )]'
  runlist_attempts=5
  matching_count=0
  poll=1
  while [ "$poll" -le "$runlist_attempts" ]; do
    api correlated-runs "$tmp/correlated-runs.json" --paginate --slurp \
  "repos/$TARGET_REPO/actions/workflows/$dispatch_workflow/runs?event=workflow_dispatch&per_page=100"
    matching_count="$(jq --arg title "$expected_title" --arg branch "$DEFAULT_BRANCH" \
    --arg repo "$TARGET_REPO" --arg workflow_path "$dispatch_workflow_path" \
    "$select_matching | length" "$tmp/correlated-runs.json")"
    [ "$matching_count" -eq 0 ] || break
    [ "$poll" -eq "$runlist_attempts" ] || sleep 3
    poll=$((poll + 1))
  done

  if [ "$matching_count" -eq 0 ]; then
    echo "::error::initial direct review dispatch $REVIEW_RUN_ID has not appeared in the workflow run listing after $runlist_attempts attempts"
    exit 1
  fi
  if [ "$matching_count" -gt 1 ]; then
    echo "::error::initial direct review dispatch is duplicated: $matching_count workflow runs share the authorization title \"$expected_title\""
    exit 1
  fi
  jq -e --argjson run "$REVIEW_RUN_ID" --arg title "$expected_title" \
  --arg branch "$DEFAULT_BRANCH" --arg repo "$TARGET_REPO" \
  --arg workflow_path "$dispatch_workflow_path" \
    "$select_matching | .[0].id == \$run" "$tmp/correlated-runs.json" >/dev/null || {
      echo "::error::the sole correlated dispatch is not review run $REVIEW_RUN_ID"
      exit 1
    }
jq -e --arg title "$expected_title" --arg branch "$DEFAULT_BRANCH" --arg repo "$TARGET_REPO" \
  --arg workflow_path "$dispatch_workflow_path" \
    "$select_matching | .[0].run_attempt == 1" "$tmp/correlated-runs.json" >/dev/null || {
      echo "::error::initial direct review dispatch was retried; only run_attempt 1 may claim the initial dispatch"
      exit 1
    }
  echo "Initial trusted-arm review dispatch verified for run $REVIEW_RUN_ID."
  exit 0
fi

jq -e '.status == "completed" and .conclusion == "failure"' "$tmp/check.json" >/dev/null || {
  echo "::error::zero-provider recovery authorization is not the failed prior attempt"
  exit 1
}

api prior-jobs "$tmp/prior-jobs.json" --paginate --slurp \
  "repos/$TARGET_REPO/actions/runs/$REVIEW_RUN_ID/jobs?filter=all&per_page=100"
jq -e --argjson attempt "$REVIEW_RUN_ATTEMPT" --arg job_prefix "$dispatch_job_prefix" '
  [.[].jobs[] | select(.run_attempt < $attempt)] as $prior |
  ($attempt > 1) and
  ([range(1; $attempt)] | all(.[]; . as $n |
    ([$prior[] | select(.run_attempt == $n and .name == ($job_prefix + "preflight"))] | length) == 1 and
    ([$prior[] | select(.run_attempt == $n and .name == ($job_prefix + "gate"))] | length) == 1 and
    ([$prior[] | select(.run_attempt == $n and .name == ($job_prefix + "complete-authorization"))] | length) == 1 and
    ([$prior[] | select(.run_attempt == $n and .name == ($job_prefix + "dispatch-merge"))] | length) == 1 and
    all($prior[] | select(.run_attempt == $n and .name == ($job_prefix + "preflight"));
      .status == "completed" and (.conclusion == "success" or .conclusion == "failure" or .conclusion == "skipped")) and
    all($prior[] | select(.run_attempt == $n and .name == ($job_prefix + "gate"));
      .status == "completed" and .conclusion == "skipped" and (.steps | length) == 0) and
    all($prior[] | select(.run_attempt == $n and .name == ($job_prefix + "complete-authorization"));
      .status == "completed" and .conclusion == "failure") and
    all($prior[] | select(.run_attempt == $n and .name == ($job_prefix + "dispatch-merge"));
      .status == "completed" and .conclusion == "skipped" and (.steps | length) == 0)
  ))
  ' "$tmp/prior-jobs.json" >/dev/null || {
    echo "::error::prior review attempt reached or ambiguously approached the provider boundary"
    exit 1
  }

api prior-reviews "$tmp/reviews.json" --paginate --slurp \
  "repos/$TARGET_REPO/pulls/$PR_NUMBER/reviews?per_page=100"
jq -e --arg head "$EXPECTED_HEAD_SHA" --arg login "${EXPECTED_APP_SLUG}[bot]" \
  --arg check "$AUTHORIZATION_CHECK_ID" '
    all(.[][];
      ((.commit_id // "") != $head) or
      (((.user.login // "") != $login) and
       ((.body // "") | contains("check:" + $check + " head:" + $head) | not)))
  ' "$tmp/reviews.json" >/dev/null || {
    echo "::error::provider reservation, submission, or review evidence exists for this exact head"
    exit 1
  }

echo "Zero-provider recovery verified for review run $REVIEW_RUN_ID attempt $REVIEW_RUN_ATTEMPT."
