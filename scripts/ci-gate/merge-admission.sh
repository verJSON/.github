#!/usr/bin/env bash
# One merge-admission evaluation for one pull-request head (ADR 0260, #1990, #2031).
#
#   merge-admission.sh <owner/repo> <pr-number> <head-sha>
#
# Environment: APP_TOKEN, an AI review App installation token with Checks write, used only
# for the merge-admission check run; GH_TOKEN, the read-only token the assertion reads
# with; DETAILS_URL, optionally, the producer run that evaluated the head.
#
# Order is the contract (ADR 0260, action 4). First, a NEW `merge-admission` check run is
# created in progress on the exact head, superseding any earlier result. Then the
# canonical assertion beside this script runs for that head.
set -uo pipefail

usage() { echo "usage: $0 <owner/repo> <pr-number> <head-sha>" >&2; exit 2; }
[ "$#" -eq 3 ] || usage
repo="$1"
pr="$2"
head_sha="$3"
[[ "$repo" =~ ^[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]+/[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]+$ ]] || usage
[[ "$pr" =~ ^[123456789][0123456789]*$ ]] || usage
[[ "$head_sha" =~ ^[0123456789abcdef]{40}$ ]] || usage

# The AI review App, the only producer whose merge-admission checks the assertion sets
# aside. It equals MERGE_ADMISSION_APP_ID in scripts/assert-mergeable-head.sh, and
# scripts/ci-gate/merge-admission.test.sh fails when the two disagree.
readonly MERGE_ADMISSION_APP_ID=4528902
assertion="$(dirname "${BASH_SOURCE[0]}")/../assert-mergeable-head.sh"

# Precondition (a) of ADR 0260: a producer that cannot evaluate says why and succeeds; the
# check it may have left in progress is the only blocking signal.
skip() { printf '::warning::merge-admission for %s#%s head %s was not evaluated: %s\n' "$repo" "$pr" "$head_sha" "$1" >&2; exit 0; }

[ -n "${APP_TOKEN:-}" ] || skip "no AI review App token is available"

create_fields=(-f name=merge-admission -f head_sha="$head_sha" -f status=in_progress)
[ -z "${DETAILS_URL:-}" ] || create_fields+=(-f details_url="$DETAILS_URL")
created="$(GH_TOKEN="$APP_TOKEN" gh api -X POST "repos/$repo/check-runs" "${create_fields[@]}" </dev/null)" \
  || skip "could not create its merge-admission check run"
# An empty body is no JSON value, on which `jq -e` exits 0, so the App is read as a value
# and compared here, never left to jq's exit status.
created_app="$(jq -r 'if (.app.id? | type) == "number" then .app.id else "" end' <<<"$created" 2>/dev/null)"
[ "$created_app" = "$MERGE_ADMISSION_APP_ID" ] \
  || skip "the created check run is not the AI review App's (app $MERGE_ADMISSION_APP_ID)"
check_id="$(jq -r 'if (.id? | type) == "number" then .id else "" end' <<<"$created" 2>/dev/null)"
[[ "$check_id" =~ ^[123456789][0123456789]*$ ]] \
  || skip "the created check run carries no numeric id"

# Admission is exit 0 AND the exact output `true`; anything else leaves the check in
# progress, which blocks natively (ADR 0260, action 4). The assertion reads with GH_TOKEN
# alone; the App token, which can write check runs, is withheld from it.
verdict="$(env -u APP_TOKEN bash "$assertion" "$repo" "$pr" "$head_sha" </dev/null)"
assertion_status=$?
if [ "$assertion_status" -eq 0 ] && [ "$verdict" = true ]; then
  GH_TOKEN="$APP_TOKEN" gh api -X PATCH "repos/$repo/check-runs/$check_id" \
    -f status=completed -f conclusion=success </dev/null >/dev/null \
    || printf '::warning::merge-admission for %s#%s head %s was admitted, but check run %s could not be completed as success, so it stays in progress\n' \
      "$repo" "$pr" "$head_sha" "$check_id" >&2
fi
exit 0
