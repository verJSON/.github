#!/usr/bin/env bash
# Lists the pull-request heads one merge-admission producer run evaluates (ADR 0260,
# #1990, #2031), as a JSON array of {pr, head_sha} on stdout.
#
#   merge-admission-plan.sh <owner/repo> [head-sha]
#
# GH_TOKEN reads the repository's open pull requests. A draft is skipped: it cannot be
# merged, and marking it ready is itself an evaluation trigger.
set -uo pipefail

repo="$1"
# An event names one head; a sweep (no head) plans every open pull request.
event_head="${2-}"

# Precondition (a) of ADR 0260: an unreadable listing plans nothing and succeeds; the next
# trigger or sweep evaluates again.
none() { printf '::warning::merge-admission could not list the open pull requests of %s, so this run evaluates none\n' "$repo" >&2; echo '[]'; exit 0; }

pulls="$(gh api --paginate "repos/$repo/pulls?state=open&per_page=100" </dev/null)" || none
# Types are checked, never truthiness: the evaluation refuses a malformed number or head
# as a usage error, which would fail the producer's own job, so such an entry is dropped
# and counted here instead.
planned="$(jq -cs --arg head "$event_head" '
  def well_formed:
    (.number | type) == "number" and .number >= 1 and (.number | floor) == .number
    and (.head.sha | type) == "string" and (.head.sha | test("^[0123456789abcdef]{40}$"));
  if all(.[]; type == "array") then [ .[][] ] else error("a page is not an array") end
  | [ .[] | select(type == "object" and .draft != true and ($head == "" or .head.sha? == $head)) ]
  | { plan: [ .[] | select(well_formed) | { pr: .number, head_sha: .head.sha } ],
      skipped: [ .[] | select(well_formed | not) ] | length }' <<<"$pulls")" || none
skipped="$(jq -r '.skipped' <<<"$planned")" || none
[ "$skipped" = 0 ] \
  || printf '::warning::merge-admission skipped %s open pull request(s) of %s whose number or head SHA is malformed\n' "$skipped" "$repo" >&2
jq -c '.plan' <<<"$planned" || none
