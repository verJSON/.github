#!/usr/bin/env bash
# The one definition of an ADR 0202 independent-review receipt check. The terminal
# merge workflow (ai-privileged-merge.yml) and the merge admission assertion
# (scripts/assert-mergeable-head.sh, Gate R, ADR 0257) both run this file, so the two
# merge paths cannot disagree on what a valid receipt is.
#
# usage: independent-review-receipt.sh <owner/repo> <pr-number> <40-hex-head> <result-file>
#
# Exits 0 and writes "<review-id>\t<reviewer-login>" to <result-file> only when the
# newest exact-head review from a current maintain/admin account is a COMMENTED review
# whose entire trimmed body is the canonical approval marker, and a second read finds
# no later verdict. Every refusal prints one `::error::` line naming its reason and
# exits non-zero; an API failure is a refusal, never a pass. Needs a token that can
# read pull-request reviews and collaborator permission (GH_TOKEN).
set -euo pipefail

[ "$#" -eq 4 ] || { echo "usage: $0 <owner/repo> <pr-number> <40-hex-head> <result-file>" >&2; exit 2; }
TARGET_REPO="$1"
PR_NUMBER="$2"
EXPECTED_HEAD_SHA="$3"
result_file="$4"
[[ "$TARGET_REPO" =~ ^[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]+/[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]+$ ]] \
  || { echo "::error::target repository identity is malformed"; exit 2; }
[[ "$PR_NUMBER" =~ ^[123456789][0123456789]{0,14}$ ]] \
  || { echo "::error::pull request number is malformed"; exit 2; }
[[ "$EXPECTED_HEAD_SHA" =~ ^[0123456789abcdef]{40}$ ]] \
  || { echo "::error::expected head SHA is malformed"; exit 2; }

marker="<!-- independent-review:v1 pr:${PR_NUMBER} head:${EXPECTED_HEAD_SHA} verdict:approved -->"

# author_association is not used as a gate: it reflects the *calling token's*
# visibility into org membership, not the reviewer's actual standing (#1615 — a
# default GITHUB_TOKEN without org-membership read access reports a real MEMBER as
# "NONE"). The live collaborator-permission check is the authoritative eligibility
# test instead.
#
# In a PUBLIC repository, dropping author_association means ANY GitHub account can
# leave a review at the exact head SHA. Picking "whichever candidate has the highest
# review ID" outright would let such a review — if posted after the real approval —
# block or supersede it. Selection therefore walks candidates from the highest ID down
# and accepts the first whose account currently holds admin/maintain, skipping any that
# don't, rather than trusting the highest ID unconditionally. The same walk is run
# again later to revalidate that nothing supersedes the accepted receipt, so a single
# function is the one place this selection is defined (ADR 0208).
#
# The walk is bounded: without a cap, an attacker posting enough junk reviews above
# the real approval could force this step through an unbounded number of live
# permission lookups. Capping at MAX_CANDIDATE_LOOKUPS makes the worst case a
# predictable, small amount of API calls rather than a griefing PR's review count —
# exceeding it fails closed (no autonomous merge), same as finding no candidate at
# all, rather than exhausting the token's request budget.
MAX_CANDIDATE_LOOKUPS=20
select_privileged_candidate() {
  local reviews_json="$1" cid clogin role attempts=0
  while IFS=$'\t' read -r cid clogin; do
    [ -n "$cid" ] || continue
    attempts=$((attempts + 1))
    [ "$attempts" -le "$MAX_CANDIDATE_LOOKUPS" ] || return 1
    role="$(gh api "repos/$TARGET_REPO/collaborators/$clogin/permission" \
      --jq '.role_name // ""' 2>/dev/null)" || role=""
    case "$role" in
      admin|maintain) printf '%s\t%s\n' "$cid" "$clogin"; return 0 ;;
    esac
  done < <(jq -r --arg head "$EXPECTED_HEAD_SHA" '
      [ .[][]
        | select(
            .commit_id == $head and
            .user.type == "User" and
            (.user.login | type == "string" and test("^[A-Za-z0-9][A-Za-z0-9-]*$"))
          )
        | {id, login: .user.login}
      ]
      | sort_by(.id) | reverse | .[] | [.id, .login] | @tsv
    ' <<<"$reviews_json")
  return 1
}

reviews="$(gh api --paginate \
  "repos/$TARGET_REPO/pulls/$PR_NUMBER/reviews?per_page=100" | jq -s '.')" || {
  echo "::error::could not list the reviews of $TARGET_REPO#$PR_NUMBER"; exit 1;
}
selected="$(select_privileged_candidate "$reviews")" || {
  echo "::error::an exact-head independent-review verdict is required before autonomous merge"; exit 1;
}
review_id="$(cut -f1 <<<"$selected")"
reviewer="$(cut -f2 <<<"$selected")"

live_review="$(gh api "repos/$TARGET_REPO/pulls/$PR_NUMBER/reviews/$review_id")" || {
  echo "::error::could not re-fetch independent-review verdict $review_id"; exit 1;
}
jq -e --argjson id "$review_id" --arg head "$EXPECTED_HEAD_SHA" \
  --arg login "$reviewer" --arg marker "$marker" '
    .id == $id and
    .state == "COMMENTED" and
    .commit_id == $head and
    .user.type == "User" and
    .user.login == $login and
    (.body | type == "string" and gsub("^\\s+|\\s+$"; "") == $marker)
  ' <<<"$live_review" >/dev/null || {
    echo "::error::latest independent-review verdict is edited, withdrawn, dismissed, or not approving"; exit 1;
  }

latest_reviews="$(gh api --paginate \
  "repos/$TARGET_REPO/pulls/$PR_NUMBER/reviews?per_page=100" | jq -s '.')" || {
  echo "::error::could not list the reviews of $TARGET_REPO#$PR_NUMBER"; exit 1;
}
latest_selected="$(select_privileged_candidate "$latest_reviews")" || {
  echo "::error::could not revalidate latest independent-review verdict"; exit 1;
}
[ "$latest_selected" = "$selected" ] || {
  echo "::error::a later independent-review verdict superseded receipt $review_id"; exit 1;
}
printf '%s\t%s\n' "$review_id" "$reviewer" >"$result_file"
