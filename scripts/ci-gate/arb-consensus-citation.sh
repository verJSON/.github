#!/usr/bin/env bash
# Companion to independent-review-receipt.sh (ADR 0271 / #2184 Gate R).
#
# After an ADR 0202 exact-head marker receipt is selected, require a *preceding*
# COMMENTED review on the same head whose entire trimmed body is the canonical
# arb-consensus citation marker, then fail-closed-verify the cited slate digest
# against an immutable archived chair record / ledger locator.
#
# The ADR 0202 marker body stays digest-free. Digest and run id live only here.
#
# usage: arb-consensus-citation.sh <owner/repo> <pr-number> <40-hex-head> \
#          <receipt-review-id> <reviews-json-file>
#
# reviews-json-file is the paginated reviews array already fetched by the receipt
# verifier (jq -s '.' shape: array of page arrays, or a flat array). Exit codes
# match independent-review-receipt.sh: 0 ok, 1 refused, 2 bad args, 3 unanswered.
set -euo pipefail

[ "$#" -eq 5 ] || {
  echo "usage: $0 <owner/repo> <pr-number> <40-hex-head> <receipt-review-id> <reviews-json-file>" >&2
  exit 2
}
TARGET_REPO="$1"
PR_NUMBER="$2"
EXPECTED_HEAD_SHA="$3"
RECEIPT_REVIEW_ID="$4"
REVIEWS_FILE="$5"

[[ "$TARGET_REPO" =~ ^[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]+/[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]+$ ]] \
  || { echo "::error::target repository identity is malformed"; exit 2; }
[[ "$PR_NUMBER" =~ ^[123456789][0123456789]{0,14}$ ]] \
  || { echo "::error::pull request number is malformed"; exit 2; }
[[ "$EXPECTED_HEAD_SHA" =~ ^[0123456789abcdef]{40}$ ]] \
  || { echo "::error::expected head SHA is malformed"; exit 2; }
[[ "$RECEIPT_REVIEW_ID" =~ ^[123456789][0123456789]{0,17}$ ]] \
  || { echo "::error::receipt review id is malformed"; exit 2; }
[ -f "$REVIEWS_FILE" ] || { echo "::error::reviews json file is missing"; exit 2; }

# Exact citation marker. Digest stays out of the ADR 0202 receipt marker.
# archive locator: owner/repo@40-hex:relative/path (no .., no absolute path).
citation_re='^<!-- arb-consensus:v1 run:(arb-[A-Za-z0-9][A-Za-z0-9._-]{1,62}) slate:([0-9a-f]{64}) archive:([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)@([0-9a-f]{40}):([A-Za-z0-9._/-]+[A-Za-z0-9._-]) -->$'

MAX_CANDIDATE_LOOKUPS=20
select_preceding_citation() {
  # Walk privileged COMMENTED reviews from highest id down; skip junk comments
  # whose body is not the canonical citation marker so an intervening note cannot
  # hide a lower-id valid citation (#2184 / Bugbot).
  local reviews_json="$1" rid rlogin role attempts=0 body
  while IFS=$'\t' read -r rid rlogin body; do
    [ -n "$rid" ] || continue
    attempts=$((attempts + 1))
    [ "$attempts" -le "$MAX_CANDIDATE_LOOKUPS" ] || return 1
    [[ "$body" =~ $citation_re ]] || continue
    role="$(gh api "repos/$TARGET_REPO/collaborators/$rlogin/permission" \
      --jq '.role_name // ""')" || {
      echo "::error::could not read the repository permission of $rlogin on $TARGET_REPO" >&2
      return 3
    }
    case "$role" in
      admin|maintain)
        printf '%s\t%s\t%s\n' "$rid" "$rlogin" "$body"
        return 0
        ;;
    esac
  done < <(jq -r --arg head "$EXPECTED_HEAD_SHA" --argjson receipt "$RECEIPT_REVIEW_ID" '
      (if type == "array" and (.[0] | type) == "array" then [.[][]] else . end) as $all
      | [ $all[]
        | select(
            (.id | tonumber) < $receipt and
            .commit_id == $head and
            .state == "COMMENTED" and
            .user.type == "User" and
            (.user.login | type == "string" and test("^[A-Za-z0-9][A-Za-z0-9-]*$")) and
            (.body | type == "string")
          )
        | {id, login: .user.login, body: (.body | gsub("^\\s+|\\s+$"; ""))}
      ]
      | sort_by(.id) | reverse
      | .[] | [.id, .login, .body] | @tsv
    ' <<<"$reviews_json")
  return 1
}

reviews="$(cat "$REVIEWS_FILE")"
selected="$(select_preceding_citation "$reviews")" || {
  status=$?
  [ "$status" -eq 3 ] && exit 3
  echo "::error::an exact-head preceding arb-consensus citation COMMENT is required before autonomous merge"
  exit 1
}

citation_id="$(cut -f1 <<<"$selected")"
citation_login="$(cut -f2 <<<"$selected")"
citation_body="$(cut -f3- <<<"$selected")"

[[ "$citation_body" =~ $citation_re ]] \
  || { echo "::error::preceding arb-consensus citation COMMENT on review $citation_id is not the canonical marker"; exit 1; }
run_id="${BASH_REMATCH[1]}"
slate_digest="${BASH_REMATCH[2]}"
archive_repo="${BASH_REMATCH[3]}"
archive_sha="${BASH_REMATCH[4]}"
archive_path="${BASH_REMATCH[5]}"

[[ "$archive_path" != *..* && "$archive_path" != /* && "$archive_path" == *[!/] ]] \
  || { echo "::error::arb-consensus archive locator path is unsafe"; exit 1; }

# Fail closed: only the org policy repository's chair records are trusted archives.
# A self-authored file in the PR repo (or any other locator) cannot satisfy Gate R.
archive_repo_cf="$(printf '%s' "$archive_repo" | tr '[:upper:]' '[:lower:]')"
[ "$archive_repo_cf" = "verjson/.github" ] \
  || { echo "::error::arb-consensus archive locator must name verJSON/.github, not $archive_repo"; exit 1; }
[[ "$archive_path" == docs/decisions/*/arb-record.md ]] \
  || { echo "::error::arb-consensus archive path must be docs/decisions/<adr>/arb-record.md"; exit 1; }

# Fail closed: the immutable archive must contain both the run id and the slate digest.
# Parse .content locally so test stubs that return the Contents JSON object work
# without implementing gh's --jq filter.
archive_json="$(gh api "repos/$archive_repo/contents/$archive_path?ref=$archive_sha")" || {
  echo "::error::could not fetch arb-consensus archive $archive_repo@$archive_sha:$archive_path"
  exit 3
}
archive_b64="$(jq -r '.content // empty' <<<"$archive_json")"
[ -n "$archive_b64" ] || {
  echo "::error::arb-consensus archive $archive_repo@$archive_sha:$archive_path has no content"
  exit 1
}
archive_text="$(printf '%s' "$archive_b64" | tr -d '\n\r ' | base64 -d 2>/dev/null)" || {
  echo "::error::arb-consensus archive $archive_repo@$archive_sha:$archive_path is not decodable"
  exit 1
}
printf '%s' "$archive_text" | grep -Fq -- "$run_id" \
  || { echo "::error::arb-consensus archive does not cite run id $run_id"; exit 1; }
printf '%s' "$archive_text" | grep -Fq -- "$slate_digest" \
  || { echo "::error::arb-consensus archive does not cite slate digest $slate_digest"; exit 1; }

# Re-fetch the citation review to prove it was not edited after listing.
live="$(gh api "repos/$TARGET_REPO/pulls/$PR_NUMBER/reviews/$citation_id")" || {
  echo "::error::could not re-fetch arb-consensus citation review $citation_id"
  exit 1
}
jq -e --argjson id "$citation_id" --arg head "$EXPECTED_HEAD_SHA" \
  --arg login "$citation_login" --arg body "$citation_body" '
    .id == $id and
    .state == "COMMENTED" and
    .commit_id == $head and
    .user.type == "User" and
    .user.login == $login and
    (.body | type == "string" and gsub("^\\s+|\\s+$"; "") == $body)
  ' <<<"$live" >/dev/null || {
    echo "::error::arb-consensus citation review $citation_id is edited, withdrawn, dismissed, or not approving"
    exit 1
  }

printf '%s\t%s\t%s\t%s\n' "$citation_id" "$citation_login" "$run_id" "$slate_digest"
