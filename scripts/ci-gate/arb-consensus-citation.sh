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
#
# Archive-only admission lane (ADR 0309): a verJSON/.github pull request whose exact
# head only adds ADR chair records with their READMEs, regenerates the decision index
# and adds NEXT fragments passes without a citation, with a ::notice:: naming the lane,
# once each added record parses as a cited archive would. Any other pull request falls
# back to the citation requirement; an unreadable file list or record exits 3.
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

# Print the decoded content of <path> at <ref> in <repo>. On failure print the reason
# and return 3 when the read failed (unanswered) or 1 when the content is unusable.
# Parse .content locally so test stubs that return the Contents JSON object work
# without implementing gh's --jq filter.
read_record_text() { # repo ref path
  local repo="$1" ref="$2" path="$3" json b64 text
  # gh api ?ref= must sit beside a live sha guard (default-branch-uri-encoding.test.sh).
  [[ "$ref" =~ ^[0-9a-f]{40}$ ]] || {
    printf 'arb-consensus archive ref %s is not a 40-hex object name' "$ref"
    return 1
  }
  json="$(gh api "repos/$repo/contents/$path?ref=$ref")" || {
    printf 'could not fetch arb-consensus archive %s@%s:%s' "$repo" "$ref" "$path"
    return 3
  }
  b64="$(jq -r '.content // empty' <<<"$json")"
  [ -n "$b64" ] || {
    printf 'arb-consensus archive %s@%s:%s has no content' "$repo" "$ref" "$path"
    return 1
  }
  text="$(printf '%s' "$b64" | tr -d '\n\r ' | base64 -d 2>/dev/null)" || {
    printf 'arb-consensus archive %s@%s:%s is not decodable' "$repo" "$ref" "$path"
    return 1
  }
  printf '%s' "$text"
}

# Encoding (ADR 0271): UTF-8 bytes of the single-line JSON object inside the first
# ```canonical-slate-json fence, with one trailing newline removed if present.
# Schema: JSON object whose string run_id is non-empty. Prints "<digest>\t<run_id>",
# or the specific reason and returns 1.
parse_slate_fence() { # record-text
  local err_file out err
  err_file="$(mktemp)"
  out="$(python3 -c '
import hashlib, json, re, sys
text = sys.stdin.read()
match = re.search(
    r"^[ \t]*```canonical-slate-json\r?\n(.*?)^[ \t]*```",
    text,
    re.M | re.S,
)
if match is None:
    sys.stderr.write("arb-consensus archive has no canonical-slate-json fence\n")
    sys.exit(1)
body = match.group(1)
if body.endswith("\n"):
    body = body[:-1]
if body.endswith("\r"):
    body = body[:-1]
if "\n" in body or "\r" in body:
    sys.stderr.write("canonical-slate-json fence must contain a single JSON line\n")
    sys.exit(1)
if not body:
    sys.stderr.write("canonical-slate-json fence is empty\n")
    sys.exit(1)
try:
    obj = json.loads(body)
except json.JSONDecodeError as exc:
    sys.stderr.write(f"canonical-slate-json fence is not valid JSON: {exc}\n")
    sys.exit(1)
if not isinstance(obj, dict):
    sys.stderr.write("canonical-slate-json fence must be a JSON object\n")
    sys.exit(1)
archived_run = obj.get("run_id")
if not isinstance(archived_run, str) or not archived_run:
    sys.stderr.write("canonical-slate-json fence is missing string run_id\n")
    sys.exit(1)
digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
sys.stdout.write(f"{digest}\t{archived_run}\n")
' <<<"$1" 2>"$err_file")" || {
    # Read the whole stderr file, then take the first line — do not pipe into
    # head (pipefail-sigpipe / #1430).
    err="$(tr -d '\r' <"$err_file")"
    err="${err%%$'\n'*}"
    rm -f "$err_file"
    printf '%s' "${err:-arb-consensus archive slate verification failed}"
    return 1
  }
  rm -f "$err_file"
  printf '%s' "$out"
}

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

# Archive-only admission lane (ADR 0309 / #2285). A verJSON/.github pull request that
# only archives ARB chair records needs no citation of its own: requiring one is
# circular. Prints the reason the lane does not apply and returns 1, or returns 0.
readonly PR_FILES_LIST_LIMIT=3000
readonly ADR_DIRECTORY_RE='^docs/decisions/[0-9]{4}-[a-z0-9][a-z0-9-]*/'
archive_only_lane() {
  local files outside unpaired records record text status
  files="$(gh api --paginate "repos/$TARGET_REPO/pulls/$PR_NUMBER/files?per_page=100" | jq -cs '
      if all(.[]; type == "array") then add // [] else error("not a file list") end
      | if all(.[]; type == "object" and (.filename | type == "string") and (.status | type == "string"))
        then . else error("not a file list") end')" || {
    printf 'could not list the files of %s#%s' "$TARGET_REPO" "$PR_NUMBER"
    return 3
  }
  # The list is the current head's, which can move after the receipt. Each entry's
  # contents_url names its commit: the head, or the base for a removed file. A list of
  # any other head cannot vouch for the one being merged, archive-shaped or not.
  jq -e --arg head "$EXPECTED_HEAD_SHA" 'all(.[] | select(.status != "removed");
      .contents_url | type == "string" and test("[?&]ref=" + $head + "(&|\\z)"))' <<<"$files" >/dev/null || {
    printf 'the file list of %s#%s does not describe head %s' "$TARGET_REPO" "$PR_NUMBER" "$EXPECTED_HEAD_SHA"
    return 3
  }
  # GitHub lists at most 3000 files; a list that long may hide a path outside the shape.
  [ "$(jq length <<<"$files")" -lt "$PR_FILES_LIST_LIMIT" ] || {
    printf "the file list reached GitHub's %s-file limit and may be truncated" "$PR_FILES_LIST_LIMIT"
    return 1
  }
  # jq's `$` also matches before a trailing newline, so every lane pattern ends at `\z`.
  # Every changed file is an added record, an added ADR README, the regenerated
  # decision index, or an added release-note fragment; anything else needs a citation.
  outside="$(jq -r --arg adr "$ADR_DIRECTORY_RE" 'first(.[] | select(
      (.status == "added" and (.filename | test($adr + "(arb-record|README)[.]md\\z")))
      or (.status == "modified" and .filename == "docs/decisions/README.md")
      or (.status == "added" and (.filename | test("^NEXT/[^/]+[.]md\\z")))
      | not)
    | "\(.filename | gsub("[[:cntrl:]]"; " ")) (\(.status))") // empty' <<<"$files")" \
    || { printf 'could not evaluate the archive-only shape'; return 3; }
  [ -z "$outside" ] || { printf '%s is outside the archive-only shape' "$outside"; return 1; }
  unpaired="$(jq -r '[.[] | .filename | select(startswith("docs/decisions/") and . != "docs/decisions/README.md")
      | capture("^(?<dir>docs/decisions/[^/]+/)(?<file>.+)\\z")]
    | group_by(.dir) | map(select((map(.file) | sort) != ["README.md", "arb-record.md"]) | .[0].dir)
    | first(.[]) // empty' <<<"$files")" || { printf 'could not evaluate the archive-only shape'; return 3; }
  [ -z "$unpaired" ] || { printf '%s must add both README.md and arb-record.md' "$unpaired"; return 1; }
  records="$(jq -r --arg adr "$ADR_DIRECTORY_RE" '.[] | select(.status == "added")
    | .filename | select(test($adr + "arb-record[.]md\\z"))' <<<"$files")" \
    || { printf 'could not evaluate the archive-only shape'; return 3; }
  [ -n "$records" ] || { printf 'the pull request adds no arb-record.md'; return 1; }
  while IFS= read -r record; do
    text="$(read_record_text "$TARGET_REPO" "$EXPECTED_HEAD_SHA" "$record")" || {
      status=$?
      printf '%s' "$text"
      return "$status"
    }
    text="$(parse_slate_fence "$text")" || { printf '%s: %s' "$record" "$text"; return 1; }
  done <<<"$records"
}

# Fall back to the citation requirement when the lane does not apply, keeping its reason.
lane_reason=""
target_repo_cf="$(printf '%s' "$TARGET_REPO" | tr '[:upper:]' '[:lower:]')"
if [ "$target_repo_cf" = "verjson/.github" ]; then
  lane_status=0
  lane_reason="$(archive_only_lane)" || lane_status=$?
  case "$lane_status" in
    0)
      echo "::notice::archive-only admission lane: $TARGET_REPO#$PR_NUMBER head $EXPECTED_HEAD_SHA only archives ARB chair records; no arb-consensus citation is required (ADR 0309)"
      exit 0
      ;;
    1) ;;
    *) echo "::error::$lane_reason"; exit 3 ;;
  esac
fi

reviews="$(cat "$REVIEWS_FILE")"
selected="$(select_preceding_citation "$reviews")" || {
  status=$?
  [ "$status" -eq 3 ] && exit 3
  echo "::error::an exact-head preceding arb-consensus citation COMMENT is required before autonomous merge${lane_reason:+; the archive-only admission lane does not apply: $lane_reason}"
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

# Fail closed: recompute SHA-256 over the archive's canonical-slate-json fence
# (ADR 0271 / #2189). Substring presence of the digest string is not enough.
archive_text="$(read_record_text "$archive_repo" "$archive_sha" "$archive_path")" || {
  status=$?
  echo "::error::$archive_text"
  exit "$status"
}
slate_verify_out="$(parse_slate_fence "$archive_text")" || {
  echo "::error::$slate_verify_out"
  exit 1
}
IFS=$'\t' read -r computed_digest archived_run_id <<<"$slate_verify_out"
[[ "$computed_digest" =~ ^[0-9a-f]{64}$ ]] \
  || { echo "::error::arb-consensus archive slate digest computation failed"; exit 1; }
[ "$archived_run_id" = "$run_id" ] \
  || { echo "::error::arb-consensus archive run_id $archived_run_id does not match citation run id $run_id"; exit 1; }
[ "$computed_digest" = "$slate_digest" ] \
  || { echo "::error::arb-consensus archive slate digest $computed_digest does not match citation digest $slate_digest"; exit 1; }

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
