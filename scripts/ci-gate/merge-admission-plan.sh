#!/usr/bin/env bash
# Lists the pull-request heads one merge-admission producer run evaluates (ADR 0260,
# #1990, #2031), as a JSON array of {head_sha, prs} on stdout: each head once, with the
# numbers of the open pull requests at it. A check run belongs to a commit, not a pull
# request, so one evaluation answers for every pull request that shares the head.
#
#   merge-admission-plan.sh <owner/repo> [head-sha]
#
# GH_TOKEN reads the repository's open pull requests. A head whose open pull requests are
# all drafts is skipped: none can be merged, and marking one ready is itself an evaluation
# trigger. A draft at a planned head is still listed, because the result posted on that head
# would stand for it as soon as it is marked ready.
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
  def well_formed_head: (.head.sha | type) == "string" and (.head.sha | test("^[0123456789abcdef]{40}$"));
  def well_formed:
    (.number | type) == "number" and .number >= 1 and (.number | floor) == .number
    and well_formed_head;
  if all(.[]; type == "array") then [ .[][] ] else error("a page is not an array") end
  | [ .[] | select(type == "object" and ($head == "" or .head.sha? == $head)) ]
  # A head with an unevaluable pull request at it is not planned for the rest.
  | [ .[] | select(well_formed_head and (well_formed | not)) | .head.sha ] as $incomplete
  | { plan: [ .[] | select(well_formed) ] | group_by(.head.sha)
          | map(select(any(.[]; .draft != true) and (.[0].head.sha as $sha | $incomplete | index([$sha]) == null))
                | { head_sha: .[0].head.sha, prs: (map(.number) | sort) }),
      skipped: [ .[] | select(well_formed | not) ] | length }' <<<"$pulls")" || none
skipped="$(jq -r '.skipped' <<<"$planned")" || none
[ "$skipped" = 0 ] \
  || printf '::warning::merge-admission skipped %s open pull request(s) of %s whose number or head SHA is malformed\n' "$skipped" "$repo" >&2
jq -c '.plan' <<<"$planned" || none
