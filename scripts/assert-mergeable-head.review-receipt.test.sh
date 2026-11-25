#!/usr/bin/env bash
# Exercises Gate R in scripts/assert-mergeable-head.sh (ADR 0257, #2031) against a
# stubbed `gh`.
#
# On a promotion-enabled repository (one whose base ref carries the organization's
# required `gate-rearm.yml` workflows rule) an autonomous merge must find a valid
# ADR 0202 independent-review receipt bound to the exact head it is about to merge.
# Every fixture below passes Gates A, W, B, and C, so a refusal can only be Gate R's.
#
# Every negative case asserts the gate, the exit code, and the specific reason.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$here/assert-mergeable-head.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

REPO=verJSON/verjson-identity-contracts
PR=121
HEAD_SHA=3b001575b09549217775577a20b9091be6de44a2
OTHER_SHA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
HEAD_BRANCH=feat/example
APP_ID=15368
ARM_RULESET_ID=20722935
WF_REQUIRED=332379840
GATE_PATH=.github/workflows/gate-rearm.yml
PINNED_SHA=24db445fe410df7bfbd740d8766b2d9ad8e73915
MARKER="<!-- independent-review:v1 pr:$PR head:$HEAD_SHA verdict:approved -->"
ARB_RUN=arb-test-2026-11-17
# #2189: digest is SHA-256 of the canonical-slate-json fence body (single JSON line).
ARB_SLATE_JSON="$(jq -nc --arg run "$ARB_RUN" \
  '{issue:"verJSON/.github#2189",items:["S-1"],run_id:$run,status:"APPROVED"}')"
ARB_SLATE="$(printf '%s' "$ARB_SLATE_JSON" | sha256sum | awk '{print $1}')"
ARB_ARCHIVE_REPO=verJSON/.github
ARB_ARCHIVE_SHA=ee9899ba3803ed95f5883bd65a30952777f4ce7b
ARB_ARCHIVE_PATH=docs/decisions/0271-arb-consensus-is-approval-authority/arb-record.md
CITATION="<!-- arb-consensus:v1 run:$ARB_RUN slate:$ARB_SLATE archive:$ARB_ARCHIVE_REPO@$ARB_ARCHIVE_SHA:$ARB_ARCHIVE_PATH -->"
# Archive must carry a recomputable canonical-slate-json fence (#2189).
ARCHIVE_TEXT="# chair"$'\n'"run \`$ARB_RUN\`"$'\n'"slate digest \`$ARB_SLATE\`"$'\n'"\`\`\`canonical-slate-json"$'\n'"$ARB_SLATE_JSON"$'\n'"\`\`\`"$'\n'
ARCHIVE_B64="$(printf '%s' "$ARCHIVE_TEXT" | base64 -w0 2>/dev/null || printf '%s' "$ARCHIVE_TEXT" | base64)"

mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
set -uo pipefail
args="$*"
printf '%s\n' "$args" >>"$CALLS"
if [ -n "${FAIL_PERMISSION_FOR:-}" ] && [[ "$args" == *"/collaborators/$FAIL_PERMISSION_FOR/permission"* ]]; then
  echo "gh: Server Error (HTTP 502)" >&2; exit 1
fi
if [ "${FAIL_ARCHIVE:-0}" -ne 0 ] && [[ "$args" == *"/contents/"* ]]; then
  echo "gh: Server Error (HTTP 502)" >&2; exit 1
fi
case "$args" in
  # Like gh, answer only the requested fields, so a field the script stops asking for
  # is absent rather than served from the fixture anyway.
  *"pr view "*"--json headRefOid,baseRefName"*)
    fields="${args#*--json }"; fields="${fields%% *}"
    jq -c --arg fields "$fields" '($fields | split(",")) as $asked | with_entries(select(.key as $k | $asked | index($k)))' <<<"$PR_JSON_FIXTURE" ;;
  *"/rules/branches/"*) printf '%s\n' "$RULES_FIXTURE" ;;
  *"/rulesets/"*) printf '%s\n' "$RULESET_FIXTURE" ;;
  *"/check-runs?per_page"*) printf '%s\n' "$CHECK_RUNS_FIXTURE" ;;
  *"/commits/"*"/status"*) printf '{"total_count":0,"statuses":[]}\n' ;;
  *"/actions/runs?head_sha="*) printf '%s\n' "$RUNS_FIXTURE" ;;
  *"/annotations?per_page"*) printf '[]\n' ;;
  # The head's recursive tree: TREE_FIXTURE, or every non-removed listed file as a
  # regular blob, the shape an archive pull request has.
  *"/git/trees/$HEAD_SHA?recursive=1"*)
    [ "${FAIL_TREE:-0}" -eq 0 ] || { echo "gh: Server Error (HTTP 502)" >&2; exit 1; }
    if [ -n "${TREE_FIXTURE:-}" ]; then printf '%s\n' "$TREE_FIXTURE"
    else
      jq -c '{truncated: false, tree: [.[] | select(.status != "removed")
        | {path: .filename, mode: "100644", type: "blob"}]}' <<<"$FILES_FIXTURE"
    fi ;;
  # Like GitHub, a file answers with its own type and path. A symlink to a file answers
  # with the target's content and path, which CONTENTS_SERVED_PATH stands in for.
  *"/contents/"*)
    path="${args#*/contents/}"; path="${path%%\?*}"
    # BAD_RECORD_PATH serves BAD_RECORD_B64 instead, so one record of several can differ.
    content="$ARCHIVE_B64"; [ "$path" != "${BAD_RECORD_PATH:-}" ] || content="$BAD_RECORD_B64"
    # Like GitHub, only a file carries content and encoding; a directory is an array.
    jq -nc --arg c "$content" --arg type "${CONTENTS_TYPE:-file}" --arg path "${CONTENTS_SERVED_PATH:-$path}" \
      --arg encoding "${CONTENTS_ENCODING:-base64}" '
      if $type == "dir" then [{type: "file", path: ($path + "/README.md")}]
      else {type: $type, path: $path}
        + (if $type == "file" then {content: $c, encoding: $encoding} else {} end)
        | if env.CONTENTS_MALFORMED == "1" then del(.type) else . end
      end' ;;
  # The archive-only admission lane (#2285) reads the pull request's changed files.
  *"/pulls/$PR/files?per_page=100"*)
    [ "${FAIL_FILES:-0}" -eq 0 ] || { echo "gh: Server Error (HTTP 502)" >&2; exit 1; }
    printf '%s\n' "$FILES_FIXTURE" ;;
  # The list is read twice: once to select the receipt, once to prove nothing later
  # superseded it. LATEST_REVIEWS_FIXTURE stands in for a review that lands between.
  # FAIL_REVIEW_LIST fails every listing; FAIL_REVIEW_LIST_AT fails only that one.
  *"/pulls/$PR/reviews?per_page=100"*)
    [ "${FAIL_REVIEW_LIST:-0}" -eq 0 ] || { echo "gh: Server Error (HTTP 502)" >&2; exit 1; }
    count="$(cat "$REVIEW_LIST_CALLS")"; count=$((count + 1)); printf '%s\n' "$count" >"$REVIEW_LIST_CALLS"
    [ "${FAIL_REVIEW_LIST_AT:-0}" -ne "$count" ] || { echo "gh: Server Error (HTTP 502)" >&2; exit 1; }
    if [ "$count" -le 1 ]; then printf '%s\n' "$REVIEWS_FIXTURE"
    else printf '%s\n' "${LATEST_REVIEWS_FIXTURE:-$REVIEWS_FIXTURE}"; fi ;;
  *"/pulls/$PR/reviews/"[0-9]*)
    id="$(sed -n 's|.*/reviews/\([0-9][0-9]*\).*|\1|p' <<<"$args")"
    [ -n "$id" ] || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; }
    # Prefer the active reviews fixture (may include citation + receipt).
    jq -ce --argjson id "$id" '
      (if type == "array" then . else [] end) as $rows
      | ($rows[] | select(.id == $id))
    ' <<<"${LATEST_REVIEWS_FIXTURE:-$REVIEWS_FIXTURE}" \
      || jq -ce --argjson id "$id" '.[] | select(.id == $id)' <<<"$REVIEWS_FIXTURE" \
      || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; } ;;
  # The caller passes --jq '.role_name // ""', so the stub answers with the role itself.
  *"/collaborators/independent-reviewer/permission"*) printf '%s\n' "$REVIEWER_ROLE" ;;
  *"/collaborators/"*"/permission"*) printf '%s\n' none ;;
  *) echo "gh: unstubbed call: $args" >&2; exit 1 ;;
esac
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" REVIEW_LIST_CALLS="$tmp/review-list-calls" PR HEAD_SHA ARCHIVE_B64

check_run() { # id name suite
  printf '{"id":%s,"name":"%s","status":"completed","conclusion":"success","app":{"id":%s},"check_suite":{"id":%s}}' \
    "$1" "$2" "$APP_ID" "$3"
}
workflow_entry="{\"repository_id\":1269388380,\"path\":\"$GATE_PATH\",\"sha\":\"$PINNED_SHA\",\"ref\":\"refs/heads/main\"}"
status_rule="{\"type\":\"required_status_checks\",\"parameters\":{\"required_status_checks\":[{\"context\":\"CI / test\",\"integration_id\":$APP_ID}]}}"
arm_rule="{\"type\":\"workflows\",\"parameters\":{\"do_not_enforce_on_create\":true,\"workflows\":[$workflow_entry]},\"ruleset_source_type\":\"Organization\",\"ruleset_source\":\"verJSON\",\"ruleset_id\":$ARM_RULESET_ID}"
arm_ruleset="{\"id\":$ARM_RULESET_ID,\"name\":\"ai-authorization-arm-required\",\"source_type\":\"Organization\",\"source\":\"verJSON\",\"enforcement\":\"active\",\"updated_at\":\"2026-11-03T11:39:01.138-05:00\",\"rules\":[{\"type\":\"workflows\",\"parameters\":{\"do_not_enforce_on_create\":true,\"workflows\":[$workflow_entry]}}]}"
# The org-required arm succeeded on the head, after the ruleset last changed.
required_run() { # target-repo
  jq -nc --arg head "$HEAD_SHA" --arg branch "$HEAD_BRANCH" --arg repo "$1" --arg path "$GATE_PATH" \
  --argjson wf "$WF_REQUIRED" '
  { id: 41711448295, check_suite_id: 703, workflow_id: $wf, run_number: 211,
    event: "pull_request_target", status: "completed", conclusion: "success",
    head_sha: $head, name: "AI review authorization arm", path: $path, run_attempt: 1,
    created_at: "2026-11-03T20:19:05Z", run_started_at: "2026-11-03T20:19:05Z",
    head_branch: $branch, head_repository: { full_name: $repo }, pull_requests: [],
    workflow_url: "https://api.github.com/repos/\($repo)/actions/required_workflows/\($wf)",
    referenced_workflows: [] }'
}
# review id state commit login type body
review() {
  jq -nc --argjson id "$1" --arg state "$2" --arg commit "$3" --arg login "$4" --arg type "$5" --arg body "$6" \
    '{id: $id, state: $state, commit_id: $commit, user: {login: $login, type: $type}, body: $body}'
}
citation="$(review 81 COMMENTED "$HEAD_SHA" independent-reviewer User "$CITATION")"
receipt="$(review 82 COMMENTED "$HEAD_SHA" independent-reviewer User "$MARKER")"
default_check_runs="$(printf '{"total_count":3,"check_runs":[%s,%s,%s]}' \
  "$(check_run 1 'CI / test' 500)" "$(check_run 31 'event-policy' 703)" "$(check_run 32 'arm' 703)")"

DEFAULT_REPO="$REPO"
reset_env() { # [target-repo]
  REPO="${1:-$DEFAULT_REPO}"
  : >"$CALLS"
  printf '0\n' >"$REVIEW_LIST_CALLS"
  unset LATEST_REVIEWS_FIXTURE FAIL_REVIEW_LIST FAIL_REVIEW_LIST_AT FAIL_PERMISSION_FOR FAIL_ARCHIVE FAIL_FILES CONTENTS_SERVED_PATH CONTENTS_TYPE CONTENTS_ENCODING CONTENTS_MALFORMED BAD_RECORD_PATH BAD_RECORD_B64 FAIL_TREE TREE_FIXTURE
  export REVIEWER_ROLE=maintain
  export PR_JSON_FIXTURE="{\"headRefOid\":\"$HEAD_SHA\",\"baseRefName\":\"main\",\"headRefName\":\"$HEAD_BRANCH\",\"headRepository\":{\"nameWithOwner\":\"$REPO\"},\"author\":{\"login\":\"delivery-author\"}}"
  export RULES_FIXTURE="[$status_rule,$arm_rule]"
  export RULESET_FIXTURE="$arm_ruleset"
  export CHECK_RUNS_FIXTURE="$default_check_runs"
  RUNS_FIXTURE="$(required_run "$REPO" | jq -c '{total_count: 1, workflow_runs: [.]}')"
  export RUNS_FIXTURE
  export REVIEWS_FIXTURE="[$citation,$receipt]"
  export FILES_FIXTURE='[]'
}

run() {
  OUT="$("$script" "$REPO" "$PR" 2>&1)"
  RC=$?
}
expect() { # label expected-rc needle
  if [ "$RC" -eq "$2" ] && grep -qF -- "$3" <<<"$OUT"; then pass "$1"
  else fail "$1 (rc=$RC, output: $OUT)"; fi
}

# --- absent ---------------------------------------------------------------------
reset_env
REVIEWS_FIXTURE='[]'
run
expect "a promotion-enabled head with no independent-review receipt fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — an exact-head independent-review verdict is required before autonomous merge"

# --- valid -----------------------------------------------------------------------
reset_env
run
if [ "$RC" -eq 0 ] && [ "$OUT" = true ]; then
  pass "a valid exact-head receipt from a maintain reviewer passes Gate R"
else
  fail "a valid exact-head receipt from a maintain reviewer passes Gate R (rc=$RC, output: $OUT)"
fi

# --- shared identity ---------------------------------------------------------------
# Until #1964 issues separate identities, agents author and review through one account.
# ADR 0257 accepts that receipt as a named, time-boxed exception, so it still passes,
# but the output must name it and never present it as plain independent review.
reset_env
PR_JSON_FIXTURE="$(jq -c '.author.login = "independent-reviewer"' <<<"$PR_JSON_FIXTURE")"
run
if [ "$RC" -eq 0 ] && [ "$(tail -n 1 <<<"$OUT")" = true ] \
  && grep -qF -- "::notice::Gate R: receipt 82 on $REPO#$PR head $HEAD_SHA is a shared-identity-review: its reviewer independent-reviewer is the pull request's author" <<<"$OUT"; then
  pass "a receipt from the pull request's author passes Gate R and is named shared-identity-review"
else
  fail "a receipt from the pull request's author passes Gate R and is named shared-identity-review (rc=$RC, output: $OUT)"
fi

# --- arb-consensus citation (#2184) ------------------------------------------------
# Marker receipt alone is insufficient once Gate R parses the preceding COMMENT.
reset_env
REVIEWS_FIXTURE="[$receipt]"
run
expect "a valid ADR 0202 marker without a preceding arb-consensus citation fails Gate R" 6 \
  "an exact-head preceding arb-consensus citation COMMENT is required before autonomous merge"

# Archive must carry a recomputable canonical-slate-json fence (fail closed; #2189).
reset_env
ARCHIVE_B64="$(printf '%s' "run \`$ARB_RUN\`"$'\n'"slate digest \`$ARB_SLATE\`" | base64 -w0 2>/dev/null || printf '%s' "run \`$ARB_RUN\`"$'\n'"slate digest \`$ARB_SLATE\`" | base64)"
export ARCHIVE_B64
run
expect "a citation whose archive omits the canonical-slate-json fence fails Gate R" 6 \
  "arb-consensus archive has no canonical-slate-json fence"
ARCHIVE_B64="$(printf '%s' "$ARCHIVE_TEXT" | base64 -w0 2>/dev/null || printf '%s' "$ARCHIVE_TEXT" | base64)"
export ARCHIVE_B64

# Wrong encoding (pretty-printed slate) must not verify even if the digest string appears.
reset_env
pretty="$(jq -n --arg run "$ARB_RUN" \
  '{issue:"verJSON/.github#2189",items:["S-1"],run_id:$run,status:"APPROVED"}')"
pretty_archive="# chair"$'\n'"run \`$ARB_RUN\`"$'\n'"slate digest \`$ARB_SLATE\`"$'\n'"\`\`\`canonical-slate-json"$'\n'"$pretty"$'\n'"\`\`\`"$'\n'
ARCHIVE_B64="$(printf '%s' "$pretty_archive" | base64 -w0 2>/dev/null || printf '%s' "$pretty_archive" | base64)"
export ARCHIVE_B64
run
expect "a citation whose archive uses a pretty-printed slate fails Gate R" 6 \
  "canonical-slate-json fence must contain a single JSON line"
ARCHIVE_B64="$(printf '%s' "$ARCHIVE_TEXT" | base64 -w0 2>/dev/null || printf '%s' "$ARCHIVE_TEXT" | base64)"
export ARCHIVE_B64

# Digest mismatch: fence hashes to a different value than the citation claims.
reset_env
other_json="$(jq -nc --arg run "$ARB_RUN" \
  '{issue:"verJSON/.github#2189",items:["S-2"],run_id:$run,status:"APPROVED"}')"
mismatch_archive="# chair"$'\n'"run \`$ARB_RUN\`"$'\n'"slate digest \`$ARB_SLATE\`"$'\n'"\`\`\`canonical-slate-json"$'\n'"$other_json"$'\n'"\`\`\`"$'\n'
ARCHIVE_B64="$(printf '%s' "$mismatch_archive" | base64 -w0 2>/dev/null || printf '%s' "$mismatch_archive" | base64)"
export ARCHIVE_B64
run
expect "a citation whose archive slate digest does not match fails Gate R" 6 \
  "does not match citation digest $ARB_SLATE"
ARCHIVE_B64="$(printf '%s' "$ARCHIVE_TEXT" | base64 -w0 2>/dev/null || printf '%s' "$ARCHIVE_TEXT" | base64)"
export ARCHIVE_B64

# Truncated / invalid JSON inside the fence fails closed.
reset_env
bad_json_archive="# chair"$'\n'"\`\`\`canonical-slate-json"$'\n'"{run_id"$'\n'"\`\`\`"$'\n'
ARCHIVE_B64="$(printf '%s' "$bad_json_archive" | base64 -w0 2>/dev/null || printf '%s' "$bad_json_archive" | base64)"
export ARCHIVE_B64
run
expect "a citation whose archive slate JSON is invalid fails Gate R" 6 \
  "canonical-slate-json fence is not valid JSON"
ARCHIVE_B64="$(printf '%s' "$ARCHIVE_TEXT" | base64 -w0 2>/dev/null || printf '%s' "$ARCHIVE_TEXT" | base64)"
export ARCHIVE_B64

# Digest must stay out of the ADR 0202 marker body.
reset_env
run
if [ "$RC" -eq 0 ] && [ "$OUT" = true ] \
  && ! grep -qF -- "$ARB_SLATE" <<<"$MARKER" \
  && grep -qF -- "$ARB_SLATE" <<<"$CITATION"; then
  pass "the ADR 0202 marker stays digest-free while the citation carries the slate digest"
else
  fail "the ADR 0202 marker stays digest-free while the citation carries the slate digest (rc=$RC)"
fi

# An intervening privileged junk COMMENT (id between citation and receipt) must not
# hide the lower-id valid citation when walking candidates.
reset_env
early_citation="$(review 70 COMMENTED "$HEAD_SHA" independent-reviewer User "$CITATION")"
junk="$(review 75 COMMENTED "$HEAD_SHA" independent-reviewer User 'looks good')"
REVIEWS_FIXTURE="[$early_citation,$junk,$receipt]"
run
if [ "$RC" -eq 0 ] && [ "$OUT" = true ]; then
  pass "an intervening privileged junk COMMENT does not hide a lower-id arb-consensus citation"
else
  fail "an intervening privileged junk COMMENT does not hide a lower-id arb-consensus citation (rc=$RC, output: $OUT)"
fi

# Archive Contents outage is unanswered (exit 3 → Gate R unevaluated), not a soft pass.
reset_env
FAIL_ARCHIVE=1
export FAIL_ARCHIVE
run
expect "an unreadable arb-consensus archive leaves Gate R unevaluated" 1 \
  "could not fetch arb-consensus archive"
unset FAIL_ARCHIVE

# A cited archive is a file: any other object the Contents API serves is not a record.
reset_env
export CONTENTS_TYPE=symlink
run
expect "a citation whose archive is served as a symlink fails Gate R" 6 \
  "arb-consensus archive $ARB_ARCHIVE_REPO@$ARB_ARCHIVE_SHA:$ARB_ARCHIVE_PATH is a symlink, not a file"

reset_env
export CONTENTS_TYPE=submodule
run
expect "a citation whose archive is served as a submodule fails Gate R" 6 \
  "arb-consensus archive $ARB_ARCHIVE_REPO@$ARB_ARCHIVE_SHA:$ARB_ARCHIVE_PATH is a submodule, not a file"

reset_env
export CONTENTS_TYPE=dir
run
expect "a citation whose archive is served as a directory fails Gate R" 6 \
  "arb-consensus archive $ARB_ARCHIVE_REPO@$ARB_ARCHIVE_SHA:$ARB_ARCHIVE_PATH is a directory"

# Bash drops NUL bytes from a decoded record, so a record must be checked as bytes: a
# NUL after the slate would otherwise vanish and leave the digest matching.
reset_env
ARCHIVE_B64="$(printf '# chair\n```canonical-slate-json\n%s\0\n```\n' "$ARB_SLATE_JSON" | base64 -w0)"
export ARCHIVE_B64
run
expect "a citation whose archive contains a NUL byte fails Gate R" 6 \
  "arb-consensus archive $ARB_ARCHIVE_REPO@$ARB_ARCHIVE_SHA:$ARB_ARCHIVE_PATH contains a NUL byte"
ARCHIVE_B64="$(printf '%s' "$ARCHIVE_TEXT" | base64 -w0 2>/dev/null || printf '%s' "$ARCHIVE_TEXT" | base64)"
export ARCHIVE_B64

# The slate encoding is UTF-8, so a record that is not UTF-8 is not a record.
reset_env
ARCHIVE_B64="$( { printf '# chair \377\n'; printf '%s' "$ARCHIVE_TEXT"; } | base64 -w0)"
export ARCHIVE_B64
run
expect "a citation whose archive is not valid UTF-8 fails Gate R" 6 \
  "arb-consensus archive $ARB_ARCHIVE_REPO@$ARB_ARCHIVE_SHA:$ARB_ARCHIVE_PATH is not valid UTF-8"
ARCHIVE_B64="$(printf '%s' "$ARCHIVE_TEXT" | base64 -w0 2>/dev/null || printf '%s' "$ARCHIVE_TEXT" | base64)"
export ARCHIVE_B64

# GitHub serves a file too large to inline with another encoding: its bytes are unknown.
reset_env
export CONTENTS_ENCODING=none
run
expect "a citation whose archive is not served base64-encoded fails Gate R" 6 \
  "arb-consensus archive $ARB_ARCHIVE_REPO@$ARB_ARCHIVE_SHA:$ARB_ARCHIVE_PATH is encoded as none, not base64"

# A response without the fields every Contents API file answer carries says nothing
# about the record: unanswered, not refused.
reset_env
export CONTENTS_MALFORMED=1
run
expect "a malformed archive contents response leaves Gate R unevaluated" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: arb-consensus archive $ARB_ARCHIVE_REPO@$ARB_ARCHIVE_SHA:$ARB_ARCHIVE_PATH returned a malformed contents response"

# Foreign archive repos are refused even when they contain the digest strings.
reset_env
bad_citation_body="<!-- arb-consensus:v1 run:$ARB_RUN slate:$ARB_SLATE archive:$REPO@$ARB_ARCHIVE_SHA:docs/decisions/0271-arb-consensus-is-approval-authority/arb-record.md -->"
bad_citation="$(review 81 COMMENTED "$HEAD_SHA" independent-reviewer User "$bad_citation_body")"
REVIEWS_FIXTURE="[$bad_citation,$receipt]"
run
expect "a citation whose archive locator is not $ARB_ARCHIVE_REPO fails Gate R" 6 \
  "arb-consensus archive locator must name $ARB_ARCHIVE_REPO"

# --- archive-only admission lane (#2285) ------------------------------------------
# A verJSON/.github pull request that only archives chair records needs no citation:
# the exact-head receipt remains required. Each case starts from a conforming archive
# PR with a receipt and no citation, and changes the one fact under test.
LANE_REPO=verJSON/.github
LANE_ADR=docs/decisions/0309-example-delivery-arb
LANE_BASE_SHA=cccccccccccccccccccccccccccccccccccccccc
# Like GitHub, a removed file's contents_url names the base, every other file's the head.
pr_file() { # status filename
  jq -nc --arg status "$1" --arg name "$2" --arg repo "$LANE_REPO" --arg head "$HEAD_SHA" --arg base "$LANE_BASE_SHA" \
    '{filename: $name, status: $status,
      contents_url: "https://api.github.com/repos/\($repo)/contents/\($name | @uri)?ref=\(if $status == "removed" then $base else $head end)"}'
}
archive_only_files="[$(pr_file added "$LANE_ADR/arb-record.md"),$(pr_file added "$LANE_ADR/README.md"),$(pr_file modified docs/decisions/README.md),$(pr_file added NEXT/2026-11-25-issue-1-example-archive.md)]"
reset_lane_env() {
  reset_env "$LANE_REPO"
  export REVIEWS_FIXTURE="[$receipt]"
  export FILES_FIXTURE="$archive_only_files"
}

reset_lane_env
run
if [ "$RC" -eq 0 ] && [ "$(tail -n 1 <<<"$OUT")" = true ] \
  && grep -qF -- "::notice::archive-only admission lane: $LANE_REPO#$PR head $HEAD_SHA only archives ARB chair records; no arb-consensus citation is required" <<<"$OUT" \
  && grep -qF -- "contents/$LANE_ADR/arb-record.md?ref=$HEAD_SHA" "$CALLS"; then
  pass "an archive-only verJSON/.github pull request passes Gate R without a citation and names the lane"
else
  fail "an archive-only verJSON/.github pull request passes Gate R without a citation and names the lane (rc=$RC, output: $OUT)"
fi

CITATION_REQUIRED="an exact-head preceding arb-consensus citation COMMENT is required before autonomous merge"
LANE_SKIPPED="the archive-only admission lane does not apply:"

reset_lane_env
FILES_FIXTURE="$(jq -c --argjson extra "$(pr_file added scripts/example.sh)" '. + [$extra]' <<<"$archive_only_files")"
run
expect "an archive PR that also changes another path still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED scripts/example.sh (added) is outside the archive-only shape"

# Release-note fragments live directly under NEXT/; anything deeper is not a fragment.
reset_lane_env
FILES_FIXTURE="$(jq -c --argjson nested "$(pr_file added NEXT/drafts/example.md)" '. + [$nested]' <<<"$archive_only_files")"
run
expect "an archive PR that adds a file below a NEXT subdirectory still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED NEXT/drafts/example.md (added) is outside the archive-only shape"

# Without a record there is nothing to archive: an index or fragment edit alone cites.
reset_lane_env
FILES_FIXTURE="[$(pr_file modified docs/decisions/README.md),$(pr_file added NEXT/2026-11-25-issue-1-example.md)]"
run
expect "a pull request that only edits the index and adds a fragment still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED the pull request adds no arb-record.md"

# Archived records are immutable: editing one is never an archive.
reset_lane_env
archived=docs/decisions/0271-arb-consensus-is-approval-authority/arb-record.md
FILES_FIXTURE="$(jq -c --argjson edit "$(pr_file modified "$archived")" '. + [$edit]' <<<"$archive_only_files")"
run
expect "an archive PR that modifies an existing record still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED $archived (modified) is outside the archive-only shape"

# Each archived record arrives with the ADR that explains it, and vice versa.
reset_lane_env
FILES_FIXTURE="$(jq -c --arg readme "$LANE_ADR/README.md" 'map(select(.filename != $readme))' <<<"$archive_only_files")"
run
expect "an archive PR whose ADR directory lacks its README still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED $LANE_ADR/ must add both README.md and arb-record.md"

# Falling back is not refusing: a verJSON/.github pull request outside the lane cites.
reset_lane_env
REVIEWS_FIXTURE="[$citation,$receipt]"
FILES_FIXTURE="[$(pr_file modified scripts/example.sh)]"
run
if [ "$RC" -eq 0 ] && [ "$OUT" = true ]; then
  pass "a verJSON/.github pull request outside the archive-only lane passes Gate R with a citation"
else
  fail "a verJSON/.github pull request outside the archive-only lane passes Gate R with a citation (rc=$RC, output: $OUT)"
fi

# The lane belongs to the archive repository alone: elsewhere the files are never read.
reset_env
REVIEWS_FIXTURE="[$receipt]"
FILES_FIXTURE="$archive_only_files"
run
if [ "$RC" -eq 6 ] && grep -qF -- "has no valid ADR 0202 independent-review receipt — $CITATION_REQUIRED" <<<"$OUT" \
  && ! grep -qF -- "$LANE_SKIPPED" <<<"$OUT" && ! grep -q '/files' "$CALLS"; then
  pass "an archive-shaped pull request in another repository still requires a citation"
else
  fail "an archive-shaped pull request in another repository still requires a citation (rc=$RC, output: $OUT)"
fi

# GitHub names are case-insensitive, so the archive repository's identity is too.
reset_lane_env
REPO=Verjson/.github
run
expect "a differently cased verJSON/.github still takes the archive-only lane" 0 \
  "::notice::archive-only admission lane: Verjson/.github#$PR head $HEAD_SHA"

# An added record must parse as Gate R parses a cited archive, or no one can cite it.
reset_lane_env
ARCHIVE_B64="$(printf '%s' "# chair without a slate" | base64 -w0 2>/dev/null || printf '%s' "# chair without a slate" | base64)"
export ARCHIVE_B64
run
expect "an archive PR whose added record has no canonical-slate-json fence still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED $LANE_ADR/arb-record.md: arb-consensus archive has no canonical-slate-json fence"
ARCHIVE_B64="$(printf '%s' "$ARCHIVE_TEXT" | base64 -w0 2>/dev/null || printf '%s' "$ARCHIVE_TEXT" | base64)"
export ARCHIVE_B64

# Without the file list nothing shows the pull request is an archive: unanswered.
reset_lane_env
export FAIL_FILES=1
run
expect "an unreadable file list leaves Gate R unevaluated, never admitted" 1 \
  "Gate R cannot evaluate $LANE_REPO#$PR head $HEAD_SHA: could not list the files of $LANE_REPO#$PR"

# A record that is a symlink to an existing archive is served as that archive: it parses,
# but it is not the file the pull request adds.
reset_lane_env
export CONTENTS_SERVED_PATH="$archived"
run
expect "an archive PR whose added record resolves to another path still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED arb-consensus archive $LANE_REPO@$HEAD_SHA:$LANE_ADR/arb-record.md is served as $archived"

# The head's tree, not the Contents API, says what each added path is. A record that is
# a symlink is not an archive, whatever its target holds.
lane_tree_with_mode() { # path mode -> the archive PR's head tree with that entry's mode
  jq -c --arg p "$1" --arg mode "$2" '{truncated: false, tree: [.[] | {path: .filename, mode: "100644", type: "blob"}]}
    | (.tree[] | select(.path == $p) | .mode) = $mode' <<<"$archive_only_files"
}
reset_lane_env
TREE_FIXTURE="$(lane_tree_with_mode "$LANE_ADR/arb-record.md" 120000)"
export TREE_FIXTURE
run
expect "an archive PR whose added record is a symlink still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED $LANE_ADR/arb-record.md is not a regular file at head $HEAD_SHA (blob 120000)"

# The same holds for the README and fragments the archive adds beside its records.
reset_lane_env
TREE_FIXTURE="$(lane_tree_with_mode NEXT/2026-11-25-issue-1-example-archive.md 120000)"
export TREE_FIXTURE
run
expect "an archive PR whose NEXT fragment is a symlink still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED NEXT/2026-11-25-issue-1-example-archive.md is not a regular file at head $HEAD_SHA (blob 120000)"

# A tree GitHub truncated may omit a listed path's entry, so it cannot show them regular.
reset_lane_env
TREE_FIXTURE="$(lane_tree_with_mode NEXT/2026-11-25-issue-1-example-archive.md 100644 | jq -c '.truncated = true')"
export TREE_FIXTURE
run
expect "an archive PR whose head tree is truncated still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED the tree of head $HEAD_SHA is truncated"

# Without the head's tree nothing shows the archive's paths are regular files.
reset_lane_env
export FAIL_TREE=1
run
expect "an unreadable head tree leaves Gate R unevaluated, never admitted" 1 \
  "Gate R cannot evaluate $LANE_REPO#$PR head $HEAD_SHA: could not read the tree of $LANE_REPO@$HEAD_SHA"

# Nor can an added record that cannot be read be shown to parse.
reset_lane_env
export FAIL_ARCHIVE=1
run
expect "an unreadable added record leaves Gate R unevaluated, never admitted" 1 \
  "Gate R cannot evaluate $LANE_REPO#$PR head $HEAD_SHA: could not fetch arb-consensus archive $LANE_REPO@$HEAD_SHA:$LANE_ADR/arb-record.md"

# Pages are separate requests against a base that can move between them, so a row can
# be skipped. Only a list that fits in one page, fewer than 100 rows, is read whole.
reset_lane_env
FILES_FIXTURE="$(jq -c --argjson fragment "$(pr_file added NEXT/2026-11-25-issue-1-fragment.md)" \
  '. + [range(100 - length) | $fragment]' <<<"$archive_only_files")"
run
expect "an archive PR whose file list fills a page of 100 rows still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED the file list fills a page of 100 rows, so it is not read in one request"

# The list GitHub serves belongs to whatever the head is now. A list of another head says
# nothing about the one being merged: a push that drops a non-archive path must not
# vouch for the head that still carries it.
reset_lane_env
FILES_FIXTURE="$(jq -c --arg head "$HEAD_SHA" --arg other "$OTHER_SHA" \
  'map(.contents_url |= sub("ref=" + $head; "ref=" + $other))' <<<"$archive_only_files")"
run
expect "a file list that describes another head leaves Gate R unevaluated, never admitted" 1 \
  "Gate R cannot evaluate $LANE_REPO#$PR head $HEAD_SHA: the file list of $LANE_REPO#$PR does not describe head $HEAD_SHA"

# Whether a list is an archive is only meaningful for the head being merged: a list of
# another head is unanswered even when it also falls outside the archive shape.
reset_lane_env
FILES_FIXTURE="$(jq -c --arg head "$HEAD_SHA" --arg other "$OTHER_SHA" --argjson extra "$(pr_file added scripts/example.sh)" \
  '. + [$extra] | map(.contents_url |= sub("ref=" + $head; "ref=" + $other))' <<<"$archive_only_files")"
run
expect "a non-archive file list that describes another head leaves Gate R unevaluated" 1 \
  "Gate R cannot evaluate $LANE_REPO#$PR head $HEAD_SHA: the file list of $LANE_REPO#$PR does not describe head $HEAD_SHA"

# Deleting an archived ADR, record and README together, is never an archive.
reset_lane_env
FILES_FIXTURE="$(jq -c --argjson record "$(pr_file removed "$archived")" \
  --argjson readme "$(pr_file removed "${archived%/*}/README.md")" '. + [$record, $readme]' <<<"$archive_only_files")"
run
expect "an archive PR that removes an archived ADR still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED $archived (removed) is outside the archive-only shape"

# Moving an archived record into a new ADR directory is not adding one.
reset_lane_env
SECOND_ADR=docs/decisions/0310-second-delivery-arb
FILES_FIXTURE="$(jq -c --argjson record "$(pr_file renamed "$SECOND_ADR/arb-record.md")" \
  --argjson readme "$(pr_file added "$SECOND_ADR/README.md")" '. + [$record, $readme]' <<<"$archive_only_files")"
run
expect "an archive PR that renames a record into a new ADR directory still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED $SECOND_ADR/arb-record.md (renamed) is outside the archive-only shape"

# Every ADR directory is complete, not just the first one.
reset_lane_env
FILES_FIXTURE="$(jq -c --argjson record "$(pr_file added "$SECOND_ADR/arb-record.md")" '. + [$record]' <<<"$archive_only_files")"
run
expect "an archive PR whose second ADR directory lacks its README still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED $SECOND_ADR/ must add both README.md and arb-record.md"

# Paths are case-sensitive: a record below docs/Decisions is outside the ADR tree.
reset_lane_env
FILES_FIXTURE="$(jq -c --argjson record "$(pr_file added "docs/Decisions/0310-second-delivery-arb/arb-record.md")" \
  '. + [$record]' <<<"$archive_only_files")"
run
expect "an archive PR that adds a record below docs/Decisions still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED docs/Decisions/0310-second-delivery-arb/arb-record.md (added) is outside the archive-only shape"

# ADR directories are NNNN-<lowercase-kebab-slug>; any other name is not an ADR.
lane_with_adr() { # adr-directory
  jq -c --argjson record "$(pr_file added "$1/arb-record.md")" \
    --argjson readme "$(pr_file added "$1/README.md")" '. + [$record, $readme]' <<<"$archive_only_files"
}
reset_lane_env
FILES_FIXTURE="$(lane_with_adr docs/decisions/0310-Second_Delivery-ARB)"
run
expect "an archive PR whose ADR slug has uppercase letters or underscores still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED docs/decisions/0310-Second_Delivery-ARB/arb-record.md (added) is outside the archive-only shape"

reset_lane_env
FILES_FIXTURE="$(lane_with_adr docs/decisions/310-second-delivery-arb)"
run
expect "an archive PR whose ADR number is not four digits still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED docs/decisions/310-second-delivery-arb/arb-record.md (added) is outside the archive-only shape"

# The decision index is regenerated, never created or deleted, by an archive.
reset_lane_env
FILES_FIXTURE="$(jq -c 'map(if .filename == "docs/decisions/README.md" then .status = "added" else . end)' <<<"$archive_only_files")"
run
expect "an archive PR that adds rather than modifies the decision index still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED docs/decisions/README.md (added) is outside the archive-only shape"

# An executable record is not a regular file either.
reset_lane_env
TREE_FIXTURE="$(lane_tree_with_mode "$LANE_ADR/arb-record.md" 100755)"
export TREE_FIXTURE
run
expect "an archive PR whose added record is executable still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED $LANE_ADR/arb-record.md is not a regular file at head $HEAD_SHA (blob 100755)"

# A gitlink names another repository's commit, not a file in this one.
reset_lane_env
TREE_FIXTURE="$(lane_tree_with_mode "$LANE_ADR/arb-record.md" 160000 \
  | jq -c --arg p "$LANE_ADR/arb-record.md" '(.tree[] | select(.path == $p) | .type) = "commit"')"
export TREE_FIXTURE
run
expect "an archive PR whose added record is a gitlink still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED $LANE_ADR/arb-record.md is not a regular file at head $HEAD_SHA (commit 160000)"

# The lane removes only the citation: an archive PR without a receipt still fails Gate R.
reset_lane_env
REVIEWS_FIXTURE='[]'
run
expect "an archive-only pull request without a receipt still fails Gate R" 6 \
  "Gate R: $LANE_REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — an exact-head independent-review verdict is required before autonomous merge"

# Every added record parses, not just the first.
reset_lane_env
FILES_FIXTURE="$(lane_with_adr "$SECOND_ADR")"
export BAD_RECORD_PATH="$SECOND_ADR/arb-record.md"
BAD_RECORD_B64="$(printf '# chair without a slate' | base64 -w0)"
export BAD_RECORD_B64
run
expect "an archive PR whose second added record does not parse still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED $SECOND_ADR/arb-record.md: arb-consensus archive has no canonical-slate-json fence"

# Git paths may contain a newline, and jq's `$` also matches before a trailing one, so
# "README.md<LF>" must not pass the shape and pair as README.md.
reset_lane_env
FILES_FIXTURE="$(jq -c --argjson record "$(pr_file added "$SECOND_ADR/arb-record.md")" \
  --argjson readme "$(pr_file added "$SECOND_ADR/README.md"$'\n')" '. + [$record, $readme]' <<<"$archive_only_files")"
run
expect "an archive PR whose README path ends in a newline still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED $SECOND_ADR/README.md  (added) is outside the archive-only shape"

# A fragment name is a plain file name: a control character makes it something else.
reset_lane_env
FILES_FIXTURE="$(jq -c --argjson odd "$(pr_file added NEXT/2026-11-25-issue-1-ex$'\t'ample.md)" '. + [$odd]' <<<"$archive_only_files")"
run
expect "an archive PR whose NEXT fragment name has a control character still requires a citation" 6 \
  "$CITATION_REQUIRED; $LANE_SKIPPED NEXT/2026-11-25-issue-1-ex ample.md (added) is outside the archive-only shape"

# --- scope -----------------------------------------------------------------------
# A base ref without the organization's arm rule is not promotion-enabled: Gate R
# neither demands a receipt nor reads the reviews.
reset_env
RULES_FIXTURE="[$status_rule]"
REVIEWS_FIXTURE='[]'
run
if [ "$RC" -eq 0 ] && [ "$OUT" = true ] && ! grep -q '/reviews' "$CALLS"; then
  pass "a repository without the arm rule is not asked for a receipt"
else
  fail "a repository without the arm rule is not asked for a receipt (rc=$RC, output: $OUT)"
fi

# Gate R recognizes the arm by the identity the reviewed arm ruleset image requires. If
# that image moves and the script does not, Gate R would stop seeing armed repositories.
config="$here/../config/ai-review-required-workflow-rollout.json"
reviewed_arm="$(jq -r '[.arm_ruleset.rules[] | select(.type == "workflows") | .parameters.workflows[]
  | "\(.repository_id) \(.path)"] | unique | join(",")' "$config")"
script_arm="$(sed -n 's/^readonly ARM_WORKFLOW_REPOSITORY_ID=//p' "$script") $(sed -n 's/^readonly ARM_WORKFLOW_PATH=//p' "$script")"
if [ -n "$reviewed_arm" ] && [ "$reviewed_arm" = "$script_arm" ]; then
  pass "Gate R's arm identity equals the reviewed arm ruleset image"
else
  fail "Gate R's arm identity equals the reviewed arm ruleset image (script: $script_arm, config: $reviewed_arm)"
fi

# An entry at the arm's path whose repository cannot be read must not read as "not
# promotion-enabled", which is the direction that skips the receipt.
reset_env
RULES_FIXTURE="$(jq -c '(.[] | select(.type == "workflows") | .parameters.workflows[0]) |= del(.repository_id)' <<<"$RULES_FIXTURE")"
RULESET_FIXTURE="$(jq -c '.rules[0].parameters.workflows[0] |= del(.repository_id)' <<<"$RULESET_FIXTURE")"
REVIEWS_FIXTURE='[]'
run
expect "an arm-path workflows entry without a repository id cannot be classified" 1 \
  "Gate R cannot establish whether $REPO@main is promotion-enabled: a workflows rule names $GATE_PATH without a numeric repository_id"

# A present but non-numeric id is the same unreadable repository, not a foreign one: a
# truthiness check would accept the string and then find it unequal to the numeric arm.
reset_env
RULES_FIXTURE="$(jq -c '(.[] | select(.type == "workflows") | .parameters.workflows[0].repository_id) |= tostring' <<<"$RULES_FIXTURE")"
RULESET_FIXTURE="$(jq -c '.rules[0].parameters.workflows[0].repository_id |= tostring' <<<"$RULESET_FIXTURE")"
REVIEWS_FIXTURE='[]'
run
expect "an arm-path workflows entry with a string repository id cannot be classified" 1 \
  "Gate R cannot establish whether $REPO@main is promotion-enabled: a workflows rule names $GATE_PATH without a numeric repository_id"

# --- malformed -------------------------------------------------------------------
reset_env
REVIEWS_FIXTURE="[$(review 82 COMMENTED "$HEAD_SHA" independent-reviewer User "LGTM $MARKER")]"
run
expect "a receipt marker inside other text fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — latest independent-review verdict is edited, withdrawn, dismissed, or not approving"

# Only a COMMENTED review carries the receipt: a dismissed review keeps its exact-head
# commit and its marker body, so the state is the one field that withdraws it.
reset_env
REVIEWS_FIXTURE="[$(review 82 DISMISSED "$HEAD_SHA" independent-reviewer User "$MARKER")]"
run
expect "a dismissed review carrying a valid marker fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — latest independent-review verdict is edited, withdrawn, dismissed, or not approving"

# --- stale head ------------------------------------------------------------------
# The receipt the reviewer published for the head before the last push.
reset_env
stale_marker="<!-- independent-review:v1 pr:$PR head:$OTHER_SHA verdict:approved -->"
REVIEWS_FIXTURE="[$(review 82 COMMENTED "$OTHER_SHA" independent-reviewer User "$stale_marker")]"
run
expect "a receipt for the previous head fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — an exact-head independent-review verdict is required before autonomous merge"

# --- App-authored ----------------------------------------------------------------
reset_env
REVIEWS_FIXTURE="[$(review 82 COMMENTED "$HEAD_SHA" independent-reviewer Bot "$MARKER")]"
run
expect "an App-authored receipt fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — an exact-head independent-review verdict is required before autonomous merge"

# --- superseded ------------------------------------------------------------------
# A later exact-head verdict from the same reviewer governs, so a request for changes
# after the receipt withdraws it.
reset_env
REVIEWS_FIXTURE="[$receipt,$(review 83 CHANGES_REQUESTED "$HEAD_SHA" independent-reviewer User 'one more fix')]"
run
expect "a later changes-requested verdict supersedes the receipt and fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — latest independent-review verdict is edited, withdrawn, dismissed, or not approving"

# A verdict that lands while the receipt is being revalidated also supersedes it.
reset_env
LATEST_REVIEWS_FIXTURE="[$receipt,$(review 83 COMMENTED "$HEAD_SHA" independent-reviewer User 'later verdict')]"
export LATEST_REVIEWS_FIXTURE
run
expect "a verdict landing during revalidation supersedes the receipt and fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — a later independent-review verdict superseded receipt 82"

# --- insufficiently authorized ---------------------------------------------------
reset_env
REVIEWER_ROLE='write'
run
expect "a receipt from a write-only collaborator fails Gate R" 6 \
  "Gate R: $REPO#$PR head $HEAD_SHA has no valid ADR 0202 independent-review receipt — an exact-head independent-review verdict is required before autonomous merge"

# --- unreadable ----------------------------------------------------------------------
# A read that fails leaves the receipt unanswered, not refused: like every other API
# failure in the assertion it exits 1, never 6, and names the read.
# A reviewer whose permission cannot be read is unknown, not unprivileged. Skipping it
# would let an older receipt govern past a newer verdict that may have withdrawn it.
reset_env
REVIEWS_FIXTURE="[$receipt,$(review 83 CHANGES_REQUESTED "$HEAD_SHA" second-maintainer User 'one more fix')]"
export FAIL_PERMISSION_FOR=second-maintainer
run
expect "an unreadable reviewer permission leaves Gate R unevaluated and names the lookup that failed" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: could not read the repository permission of second-maintainer on $REPO"

# The same holds on the revalidating search: a newer reviewer that appears only on the
# second read, and whose permission cannot be read, must not let the receipt stand.
reset_env
LATEST_REVIEWS_FIXTURE="[$receipt,$(review 83 CHANGES_REQUESTED "$HEAD_SHA" second-maintainer User 'one more fix')]"
export LATEST_REVIEWS_FIXTURE FAIL_PERMISSION_FOR=second-maintainer
run
expect "an unreadable permission on the revalidating search leaves Gate R unevaluated and names the lookup" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: could not read the repository permission of second-maintainer on $REPO"

reset_env
export FAIL_REVIEW_LIST=1
run
expect "an unreadable review listing leaves Gate R unevaluated and names the read that failed" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: could not list the reviews of $REPO#$PR"

# Only the revalidating listing fails: the receipt was found, but nothing proves that no
# later verdict superseded it.
reset_env
export FAIL_REVIEW_LIST_AT=2
run
expect "an unreadable revalidating review listing leaves Gate R unevaluated" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: could not list the reviews of $REPO#$PR"

# --- verifier contract -------------------------------------------------------------
# Gate R trusts only the verifier's documented results: 0 with a well-formed result
# line, 1 (refused), and 3 (a read failed). Anything else is an evaluation fault. The
# author is null, as for a deleted account, so the shared-identity comparison cannot
# stand in for a check on the result.
run_with_verifier() { # verifier-body
  rm -rf "$tmp/stubbed"; mkdir -p "$tmp/stubbed/ci-gate"
  cp "$script" "$tmp/stubbed/assert-mergeable-head.sh"
  printf '#!/usr/bin/env bash\n%s\n' "$1" >"$tmp/stubbed/ci-gate/independent-review-receipt.sh"
  OUT="$(bash "$tmp/stubbed/assert-mergeable-head.sh" "$REPO" "$PR" 2>&1)"
  RC=$?
}
reset_env
PR_JSON_FIXTURE="$(jq -c '.author = null' <<<"$PR_JSON_FIXTURE")"
run_with_verifier 'exit 0'
expect "a verifier that passes without writing its result leaves Gate R unevaluated" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: could not read the verifier's result"

reset_env
PR_JSON_FIXTURE="$(jq -c '.author = null' <<<"$PR_JSON_FIXTURE")"
# A digit prefix is not a review id: only an anchored match rejects it.
run_with_verifier 'printf "%s\t%s\n" 82x independent-reviewer >"$4"'
expect "a verifier result without a numeric review id leaves Gate R unevaluated" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: the verifier's result is malformed"

reset_env
PR_JSON_FIXTURE="$(jq -c '.author = null' <<<"$PR_JSON_FIXTURE")"
run_with_verifier 'printf "%s\t\n" 82 >"$4"'
expect "a verifier result without a reviewer leaves Gate R unevaluated" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: the verifier's result is malformed"

reset_env
run_with_verifier 'echo "::error::pull request number is malformed"; exit 2'
expect "a verifier usage failure (exit 2) leaves Gate R unevaluated, not refused" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: its receipt verifier exited with unexpected status 2"

reset_env
run_with_verifier 'kill -TERM $$'
expect "a verifier killed by a signal leaves Gate R unevaluated, not refused" 1 \
  "Gate R cannot evaluate $REPO#$PR head $HEAD_SHA: its receipt verifier exited with unexpected status 143"

# --- verifier unavailable ------------------------------------------------------------
# Gate R runs the verifier beside this script in the canonical checkout. A copy of the
# script without it cannot evaluate the receipt: that is exit 1, not a receipt refusal.
reset_env
mkdir -p "$tmp/standalone"
cp "$script" "$tmp/standalone/assert-mergeable-head.sh"
OUT="$(bash "$tmp/standalone/assert-mergeable-head.sh" "$REPO" "$PR" 2>&1)"
RC=$?
expect "a script copied without its receipt verifier cannot evaluate Gate R" 1 \
  "Gate R cannot evaluate $REPO#$PR: its receipt verifier $tmp/standalone/ci-gate/independent-review-receipt.sh is missing; run the assertion from a canonical verJSON/.github checkout"

[ "$fails" -eq 0 ] || { printf '%s failure(s)\n' "$fails"; exit 1; }
printf 'all Gate R cases passed\n'
