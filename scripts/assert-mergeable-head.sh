#!/usr/bin/env bash
# Replacement for the organization's pre-merge assertion
#
#   gh pr view N --json statusCheckRollup --jq \
#     '[.statusCheckRollup[].conclusion] | all(. == "SUCCESS" or . == "NEUTRAL" or . == "SKIPPED")'
#
# which ADR 0178 made ambiguous in the one direction that matters. That form
# answers a single question — "is anything red?" — so a deferred head, a head
# missing a required check entirely, and a head with a genuinely failing
# advisory check all collapse into one bare `false`. The predictable field fix
# for an opaque `false` is to widen the accepted-conclusion list, which is
# precisely how `Verjson/verjson-cli#251`'s unexercised-head class returns.
#
# This script answers three separable questions and names which one failed, so
# that relaxing any of them is a deliberate, reviewable act rather than an
# allowlist edit made under time pressure:
#
#   Gate A  every REQUIRED context for the base ref exists on the head, was
#           produced by the app the ruleset binds it to, has completed, and
#           passed. An absent required check fails closed (ADR 0024) instead of
#           being silently satisfied by its own absence. Every required context
#           needs a SUCCESS of its own (a skip is not evidence its work ran), and
#           any workflow run on the head that has not finished is pending (ADR 0229).
#   Gate B  positive evidence that verification actually executed: the head
#           carries no ADR 0178 `deferred-ci` check that ran, and no ADR 0156
#           deferral annotation. This is the gate a deferred Renovate head
#           fails, and it says so in those words.
#   Gate C  no other check on the head is pending or non-passing.
#
# Gates A and C set aside one shape only: a CANCELLED check run whose workflow run
# was cancelled and then superseded by a later successful run of the same workflow,
# event, and head that reports that check as SUCCESS or not at all (ADR 0226, #1754)
# -- the draft-then-ready concurrency cancel. No conclusion is added to the passing
# set, and Gate B always reads the raw inventory.
#
# Prints "true" and exits 0 only when all three hold. Every other outcome is a
# fault: a `::error::` naming the gate, and a distinct exit code — never a bare
# "false", and never a zero exit on an unanswered question.
#
# PERMISSIONS. The script reads pull-request metadata, check runs, commit statuses,
# and `repos/{owner}/{repo}/rules/branches/{base}`; the last is Gate A's ONLY source
# of the required set. Workflow runs (`actions/runs`) are read for every head (ADR 0229):
# an unfinished run is pending, and ADR 0226 places cancelled check runs in their runs.
# A caller missing any read cannot evaluate the assertion, and
# this script refuses rather than falling back to partial data. `administration` is
# NOT required, and an organization-level ruleset is returned through this repository
# endpoint without organization read, verified against
# `repos/Verjson/.github/rules/branches/main`, which returns its org-sourced
# `required_status_checks` rule to a wholly unauthenticated caller.
#
# The complete grant per token type is stated in `permission_help()` below and quoted
# into access-related failures. Each endpoint also names its specific fine-grained and
# Actions grant so an early refusal is actionable. An authorization refusal (401/403, or the 404
# GitHub substitutes when it masks an unauthorized read of a private repository)
# exits 1 with a message naming the token as the cause and quoting that help; it is
# never a silent pass and never an undifferentiated API error.
#
# An adopter lacking that read therefore fails CLOSED with a message naming the token as
# the cause -- it is never handed a degraded or empty required set. That matters because
# the converse was once assumed: that a token able to read the repository but not the
# organization's rulesets would receive a 200 carrying an empty rule list, and so be
# misreported as an ungoverned ref. It does not. Verified against the live endpoint
# (2026-09-18): `repos/Verjson/.github/rules/branches/main` returns its organization-sourced
# rules in full, `ruleset_source_type: Organization` included, to a WHOLLY UNAUTHENTICATED
# caller; a caller without repository read is refused 401/403/404 instead. Read access to
# the repository is the whole requirement, and an unreadable ruleset is not a cause of an
# empty list. What does produce a 200 with an empty list is an ungoverned ref OR a ref that
# does not exist under the path requested -- also verified: a nonexistent branch answers
# 200 `[]` exactly as an ungoverned one does. Gate A names that ambiguity outright rather
# than asserting either cause, because the two remedies are opposite (#1437).
#
#   2  usage error
#   3  Gate A failed (required contexts absent, misattributed, pending, or not passing)
#   4  Gate B failed (the head was deferred; nothing on it was verified)
#   5  Gate C failed (some other check is pending or non-passing)
#   1  the assertion could not be evaluated (API failure, unresolvable state)
#
# There is deliberately NO environment override for any gate's matching rule.
# An earlier draft exposed the deferral patterns as tunable variables, which
# made disabling Gate B a one-variable edit — cheaper than the allowlist
# widening this script exists to prevent, and invisible in the output. A gate
# whose strictness is configurable at the call site is not a gate.
# `-e` is deliberately absent: the advisory-check loop below ends on a test that
# is legitimately false, which would abort under `-e`. Correctness therefore
# rests on every command substitution carrying its own `|| fault` — do not add
# an unguarded `$(...)` here.
set -uo pipefail

usage() { echo "usage: $0 <owner/repo> <pr-number>" >&2; exit 2; }
[ "$#" -eq 2 ] || usage
repo="$1"
pr="$2"
[[ "$repo" =~ ^[^/[:space:]]+/[^/[:space:]]+$ ]] || usage
[[ "$pr" =~ ^[0-9]+$ ]] || usage

# ADR 0178 fixes the job id. A caller renames its own job, not the reusable's,
# so the deferral surfaces as `deferred-ci` or `<caller-job> / deferred-ci`, and
# a matrix value is appended in parentheses. A commit-status context uses the
# conventional `prefix/deferred-ci` form with no space, so both separators are
# accepted on each side. Names are matched as reported, never trimmed: Gate A's
# exact-name comparison must stay whitespace-sensitive, so a padded name reports
# `absent` and fails closed rather than silently satisfying a required context.
readonly DEFERRED_JOB_PATTERN='(^|[/ ])deferred-ci([ (/]|$)'
readonly DEFERRED_ANNOTATION_PATTERN='^CI deferred$'
readonly PASSING='["SUCCESS","NEUTRAL","SKIPPED"]'

# Every line this script prints that starts with `::` is a workflow command when it runs
# inside Actions, and messages carry names a pull request's author controls: a workflow's
# `name:`, a job name, a check-run name, all defined in the head under judgment. A CR or
# LF in one would start a new line, and `::error::...` there would be EXECUTED as a
# command (Actions splits on either byte). So the one encoder for annotations replaces
# every control character with a space at this sink, rather than trusting each
# interpolation site to remember: a future message cannot forget it. Names are only
# rewritten for display; every comparison above still sees the exact reported bytes.
annotate() { # kind message
  printf '::%s::%s\n' "$1" "${2//[[:cntrl:]]/ }" >&2
}
fault() { annotate error "$2"; exit "$1"; }

# The complete caller permission set. Access-related failures append this list after naming
# the endpoint-specific grant, and the PERMISSIONS header points here to avoid a second list
# that could drift.
# Printed as one line, because `fault` emits one `::error::` annotation.
permission_help() {
  printf '%s' \
    "fine-grained token: Repository permissions > Metadata (read), Pull requests (read), Checks (read), Commit statuses (read), and Actions (read); " \
    "classic OAuth token: 'repo' for a private repository, no scope at all for a public one; " \
    "inside Actions: permissions.contents: read, permissions.pull-requests: read, permissions.checks: read, permissions.statuses: read, permissions.actions: read. " \
    "The 'administration' scope is NOT required, and an organization-level ruleset is " \
    "returned through this repository endpoint without organization read. " \
    "Note that GitHub masks an unauthorized read of a private repository as 404, so do " \
    "not conclude from a 404 that the repository or base ref is missing until the token " \
    "has been checked."
}

permission_fault() {
  local message="$1" fine_grained="$2" actions="$3"
  fault 1 "$message; if authorization failed, this endpoint needs fine-grained $fine_grained (read) or Actions permissions.$actions: read. Full caller requirements: $(permission_help)"
}

# Both inventory endpoints state how many entries exist. Reconciling that claim
# against what the page walk actually collected is what turns a truncated walk
# into a fault instead of a short green inventory. Defaulting the CLAIM to the
# OBSERVATION would make the check vacuous exactly when it matters — an empty
# body, or a shape without `total_count`, would reconcile 0 against 0 and let the
# gate reason over nothing at all. So the attestation is required, not optional.
reconcile_pages() {
  jq -se --arg field "$2" '
    [.[]] as $pages
    | if ($pages | length) == 0 then
        error("\($field): the response carried no page at all")
      elif ($pages[0].total_count | type) != "number" then
        error("\($field): the response states no total_count, so the walk is unattested")
      else . end
    | ($pages | map(.[$field]? // []) | add // [] | length) as $got
    | $pages[0].total_count as $claimed
    | if $got == $claimed then true
      else error("\($field): the page walk returned \($got) of \($claimed)") end' \
    >/dev/null <<<"$1"
}

pr_json="$(gh pr view "$pr" --repo "$repo" --json headRefOid,baseRefName </dev/null)" \
  || permission_fault "failed to fetch pull request metadata for $repo#$pr" "Pull requests" "pull-requests"

head_sha="$(jq -r '.headRefOid // ""' <<<"$pr_json")"
[[ "$head_sha" =~ ^[0-9a-f]{40}$ ]] \
  || fault 1 "could not resolve a head SHA for $repo#$pr"
base_ref="$(jq -r '.baseRefName // ""' <<<"$pr_json")"
[ -n "$base_ref" ] \
  || fault 1 "could not resolve a base ref for $repo#$pr"
# The head's checks are read from REST rather than from `gh pr view`'s
# `statusCheckRollup`, because the rollup projection carries no app identity and
# Gate A has to know WHO produced a required check, not merely that something
# with the right display name reported.
# `total_count` is reconciled against what was actually collected: a truncated
# pagination would otherwise shrink the inventory silently, and a gate that
# reasons over a short inventory is the failure this script exists to prevent.
check_runs_raw="$(gh api --paginate "repos/$repo/commits/$head_sha/check-runs?per_page=100" </dev/null)" \
  || permission_fault "failed to fetch check runs for $repo@$head_sha" "Checks" "checks"
reconcile_pages "$check_runs_raw" check_runs \
  || fault 1 "the check-run inventory for $repo@$head_sha is incomplete or unattested; it cannot be reasoned over"
check_runs="$(jq -s '[ .[].check_runs[]? | {
      name: (.name // ""),
      status: ((.status // "completed") | ascii_upcase),
      conclusion: ((.conclusion // "") | ascii_upcase),
      app_id: (.app.id // null),
      id: .id,
      suite_id: (.check_suite.id // null),
      kind: "check_run" } ]' <<<"$check_runs_raw")" \
  || fault 1 "failed to normalize check runs for $repo@$head_sha"

# Legacy commit statuses are a separate endpoint and carry no app id at all.
# `--paginate` matters here as much as on check runs: this endpoint returns 30
# statuses per page by default, and a `failure` stranded on page 2 would be
# invisible to Gate C, which is the gate that exists to see it.
statuses_raw="$(gh api --paginate "repos/$repo/commits/$head_sha/status?per_page=100" </dev/null)" \
  || permission_fault "failed to fetch commit statuses for $repo@$head_sha" "Commit statuses" "statuses"
reconcile_pages "$statuses_raw" statuses \
  || fault 1 "the commit-status inventory for $repo@$head_sha is incomplete or unattested; it cannot be reasoned over"
statuses="$(jq -s '[ .[] | .statuses[]? | {
      name: (.context // ""),
      status: (if ((.state // "") | ascii_upcase) == "PENDING" then "IN_PROGRESS" else "COMPLETED" end),
      conclusion: (((.state // "") | ascii_upcase) | if . == "PENDING" then "" elif . == "ERROR" then "FAILURE" else . end),
      app_id: null,
      id: null,
      kind: "status" } ]' <<<"$statuses_raw")" \
  || fault 1 "failed to normalize commit statuses for $repo@$head_sha"

checks="$(jq -n --argjson a "$check_runs" --argjson b "$statuses" '$a + $b')" \
  || fault 1 "failed to assemble the check inventory for $repo@$head_sha"

# ------------------------------------------------- superseded cancellations ----
# ADR 0226 (#1754). A pull request opened as a draft and then marked ready starts a
# second run of the same workflow on the same head, and concurrency cancels the
# first. Both runs are separate check suites, so the check-run endpoint's default
# `filter=latest` (which collapses re-run attempts WITHIN a suite) keeps both, and
# the union of conclusions per context reads `SUCCESS,CANCELLED` on a green head.
#
# A cancelled check run is set aside for Gates A and C -- and ONLY a cancelled one --
# when its own workflow run concluded `cancelled` and the NEWEST later run of the
# SAME workflow (workflow_id), under the SAME event, on THIS head, completed with
# conclusion `success` AND its own check suite reports that check name only as a
# completed SUCCESS, or not at all. A run concludes `success` with its guarded jobs
# SKIPPED, and `event` cannot tell a draft-time run from a ready-time one, so the
# run-level conclusion alone would let a skip vouch for work never done. Omission
# is accepted because a matrix parent is reported only as its expanded legs; Gate A
# then requires a required context set aside this way to carry a SUCCESS of its
# own. "Later" is `run_number`: GitHub's per-workflow event
# sequence, an integer assigned at creation and unique within a workflow, so two
# distinct runs never tie; it is the order concurrency itself supersedes by. Job
# timestamps are second-granular and a cancelled job can report completed_at before
# started_at; check-run ids are re-issued by a re-run of an OLDER event. Neither is
# trusted to order events. App id is no provenance here: every workflow's checks are
# published by the one GitHub Actions app.
#
# Everything else is untouched: failures, pending checks, a cancellation that nothing
# later superseded, a cancellation in a run that ended any other way, commit
# statuses, and every check Gate B reads (it keeps the raw inventory). Dismissal
# removes evidence and never manufactures it: a required context reported only by a
# superseded cancellation is then ABSENT, which Gate A refuses.
#
# The workflow-run inventory this reasons over is read for every head (below, ADR 0229).
# ------------------------------------------------------------ workflow runs ----
# ADR 0229 (#1755). Every workflow run on the head is read, not only when a cancelled
# check run makes supersession worth asking about (ADR 0226 made the read conditional;
# ADR 0229 supersedes that clause). Marking a draft ready creates a run BEFORE that run
# creates any job, so the check inventory alone cannot see the verification that is
# about to replace the draft-time skips; only the run inventory can.
runs_raw="$(gh api --paginate "repos/$repo/actions/runs?head_sha=$head_sha&per_page=100" </dev/null)" \
  || permission_fault "failed to fetch workflow runs for $repo@$head_sha" "Actions" "actions"
reconcile_pages "$runs_raw" workflow_runs \
  || fault 1 "the workflow-run inventory for $repo@$head_sha is incomplete or unattested; it cannot be reasoned over"
# Types are checked, never truthiness: jq orders every string above every number,
# so a string run_number would always read as "later", and two absent workflow ids
# or events would compare equal (`null == null`) and forge a same-workflow match.
runs="$(jq -s --arg head "$head_sha" '
  def well_formed:
    type == "object"
    and (.id | type) == "number"
    and (.check_suite_id | type) == "number"
    and (.workflow_id | type) == "number"
    and (.run_number | type) == "number"
    and (.event | type) == "string" and .event != ""
    and (.status | type) == "string"
    and ((.conclusion | type) == "string" or .conclusion == null)
    # A completed run without a conclusion never settled; reading it as finished
    # would release the unfinished-run guard in Gate A (ADR 0229).
    and (.status != "completed" or ((.conclusion | type) == "string" and .conclusion != ""))
    and .head_sha == $head;
  [ .[].workflow_runs[]? ]
  | if all(.[]; well_formed) | not then error("malformed workflow run")
    elif (map(.check_suite_id) | unique | length) != length then error("check suite claimed twice")
    else . end' <<<"$runs_raw")" \
  || fault 1 "the workflow-run inventory for $repo@$head_sha carries a malformed record; it cannot be reasoned over"

gated_checks="$checks"
superseded_names='[]'
cancelled_count="$(jq '[ .[] | select(.kind == "check_run" and .conclusion == "CANCELLED") ] | length' <<<"$checks")" \
  || fault 1 "failed to count cancelled check runs for $repo@$head_sha"
if [ "$cancelled_count" -gt 0 ]; then
  verdict="$(jq -c --argjson runs "$runs" '
    . as $all
    # The latest successful later run of the same workflow and event -- provided its
    # own check suite did that check'"'"'s work -- or null.
    | def superseder:
      if .kind == "check_run" and .status == "COMPLETED" and .conclusion == "CANCELLED" then
        .name as $name
        | .suite_id as $sid
        | [ $runs[] | select(.check_suite_id == $sid) ] as $own
        | if ($own | length) != 1 then null
          else $own[0] as $o
          | if $o.status != "completed" or $o.conclusion != "cancelled" then null
            else [ $runs[]
                   | select(.status == "completed" and .conclusion == "success"
                            and .workflow_id == $o.workflow_id and .event == $o.event
                            and .run_number > $o.run_number) ]
                 | max_by(.run_number)
                 | if . == null then null
                   else .check_suite_id as $ssid
                   # A run concludes `success` with its guarded jobs SKIPPED, and its
                   # event cannot say whether it was a draft-time run. So the
                   # superseder must not report this check as anything but a
                   # completed SUCCESS; it may omit it (a matrix parent is reported
                   # only as expanded legs), which Gate A then backs with a SUCCESS.
                   | if all($all[] | select(.kind == "check_run" and .suite_id == $ssid and .name == $name);
                            .status == "COMPLETED" and .conclusion == "SUCCESS")
                     then { cancelled_run: $o.id, superseded_by: .id }
                     else null end
                   end
            end
          end
      else null end;
    map(. + { superseded: superseder })
    | { kept: [ .[] | select(.superseded == null) | del(.superseded) ],
        names: [ .[] | select(.superseded != null) | .name ] | unique,
        dismissed: [ .[] | select(.superseded != null)
                     | "\(.name) (check run \(.id), run \(.superseded.cancelled_run) superseded by run \(.superseded.superseded_by))" ] }
  ' <<<"$checks")" \
    || fault 1 "failed to evaluate superseded cancellations for $repo@$head_sha"
  gated_checks="$(jq -c '.kept' <<<"$verdict")" \
    || fault 1 "failed to assemble the gated check inventory for $repo@$head_sha"
  superseded_names="$(jq -c '.names' <<<"$verdict")" \
    || fault 1 "failed to list superseded check names for $repo@$head_sha"
  dismissed="$(jq -r '.dismissed | join("; ")' <<<"$verdict")" \
    || fault 1 "failed to list superseded cancellations for $repo@$head_sha"
  [ -z "$dismissed" ] \
    || annotate notice "Gates A and C set aside cancelled check runs superseded by a later successful run of the same workflow, event, and head (ADR 0226): $dismissed"
fi

# ---------------------------------------------------------------- Gate A ----
# The required set is read from the ruleset that actually governs the pull
# request's BASE REF. It is never inferred from the head: that is the same
# error as trusting an adopter-supplied header, and it lets a head satisfy the
# gate by reporting nothing at all.
# `gh api` exits 1 for an authorization refusal and for a transient 5xx alike,
# so the exit code alone cannot tell an operator whether to retry or to fix a
# token. Gate A is not merely failed when this read is refused -- it is
# UNEVALUABLE, which is the fail-open shape this script exists to prevent, so
# the status is read off stderr and the permission cause is named outright.
rules_err="$(mktemp)" \
  || fault 1 "could not allocate a scratch file to capture the ruleset read's diagnostics"
trap 'rm -f "$rules_err"' EXIT
# LOAD-BEARING: `set -o pipefail` at the top of this file is what makes the failure of
# `gh` on the LEFT of this pipe reach the assignment at all. `jq -s add` succeeds on empty
# input, so without pipefail this would take the success path, and -- worse -- a future
# edit that treated its result as ungoverned would make it fail OPEN. Do not remove
# pipefail, and do not rewrite this as a pipeline whose exit status comes from `jq`.
#
# `add` carries NO `// []` default, and that omission is the point: `//` treats `null` and
# `false` as absent, so `add // []` turned a body of `null`, a body of `false`, and an
# empty body all into `[]` -- indistinguishable below from a genuine empty rule list, and
# reported with that arm's remedies. Those are exactly the bodies a proxy or a cached
# error page produces. Preserving the shape is what lets the discriminator name it.
# The base ref is spent as ONE path segment of `rules/branches/<ref>`, so it is
# percent-encoded in full: a `release/1.x` base ref travels as `release%2F1.x`.
# Measured live on 2026-09-26 (#1470): `rules/branches/` resolves the same ruleset
# for `release%2F2026.09` and `release/2026.09`, so the path form is the strictly
# safer spelling, and it is the one every other `rules/branches/` reader here uses.
# Keep this assignment bare (no `|| fault` guard, no continuation): the URI-encoding
# scan recognizes the encoder only in this exact single-line shape, and a jq failure
# here still fails closed below (an empty segment cannot read a ruleset).
base_ref_path="$(jq -rn --arg r "$base_ref" '$r | @uri')"
readonly base_ref_path
rules_json="$(gh api --paginate "repos/$repo/rules/branches/$base_ref_path" </dev/null 2>"$rules_err" | jq -s 'add')"
rules_rc=$?
# Replay unconditionally, not only on failure. Capturing stderr to inspect the status
# redirects it away from the caller on the SUCCESS path too, and a 200 can still carry a
# rate-limit or deprecation warning -- which reached the caller before this capture
# existed and is precisely the notice that is worth acting on BEFORE the limit is hit.
[ -s "$rules_err" ] && cat "$rules_err" >&2
if [ "$rules_rc" -ne 0 ]; then
  # Match the LAST status in the stream: `--paginate` may emit several lines,
  # and the refusal that ended the walk is the one that explains it.
  rules_status="$(sed -n 's/.*(HTTP \([0-9]\{3\}\)).*/\1/p' "$rules_err" | tail -1)"
  case "$rules_status" in
    401 | 403 | 404)
      fault 1 "Gate A cannot read the ruleset at repos/$repo/rules/branches/$base_ref_path (HTTP $rules_status): the token presented cannot read it. Gate A's required set comes only from this endpoint and is never inferred from the head, so this refusal is fatal rather than degraded. Grant the caller read access to $repo -- $(permission_help)" ;;
  esac
  fault 1 "failed to read branch rules for $repo@$base_ref; cannot establish the required-check set"
fi

required="$(jq -c '
  [ .[]?
    | select(type == "object" and .type == "required_status_checks")
    | .parameters.required_status_checks[]?
    | select(type == "object" and (.context // "") != "")
    | { context: .context, integration_id: (.integration_id // null) }
  ] | unique
' <<<"$rules_json")" \
  || fault 1 "failed to parse branch rules for $repo@$base_ref"

required_count="$(jq 'length' <<<"$required")" \
  || fault 1 "failed to count the required contexts of $repo@$base_ref"
# A zero required set has more than one cause, and they have OPPOSITE remedies. On a merge
# gate a wrong attribution is a wrong remedy: the ungoverned-ref sentence tells an operator
# to add a ruleset, which is the wrong move when the ref already has one -- or when the ref
# itself is what is missing. So the two shapes are named separately (#1437). Both still fail
# closed with exit 3; the taxonomy ADR 0184 fixed is deliberately not touched here.
if [ "$required_count" -eq 0 ]; then
  rule_count="$(jq 'if type == "array" then length else -1 end' <<<"$rules_json")" \
    || fault 1 "failed to count the rules returned for $repo@$base_ref"
  # A body that is not a JSON array at all is a third shape again, and it is neither empty
  # nor ungoverned: `jq -s add` preserves it and `.[]?` silently selects nothing from it,
  # so it arrives here with a zero required set like the other two. Reporting it as either
  # of them would name a remedy for a condition that is not the one observed. `null`,
  # `false`, and an empty body reach this guard only because the `// []` default was
  # removed above; with it they were reported as an empty rule list.
  [ "$rule_count" -ge 0 ] \
    || fault 3 "$repo@$base_ref returned a rules response that is not a JSON array, so Gate A cannot establish a required set from it and refuses rather than reading it as an empty one. This is a malformed or unexpected response shape, not evidence about the ref's governance: neither adding a ruleset nor changing the ref is the remedy. Re-read repos/$repo/rules/branches/$base_ref_path directly and check for a proxy, a cached error body, or an API change."
  if [ "$rule_count" -gt 0 ]; then
    # The fallback sits OUTSIDE the object filter on purpose: `objects | (.type // "?")`
    # drops non-objects before the default can apply, so an array of non-objects rendered
    # an empty type list into a sentence that promises one.
    rule_types="$(jq -r '[ .[]? | if type == "object" then (.type // "?") else "?" end ] | unique | join(", ")' <<<"$rules_json")" \
      || rule_types="unreadable"
    fault 3 "$repo@$base_ref declares no required status checks, so a green rollup proves nothing about it; this assertion cannot gate an ungoverned ref. Its ruleset WAS read and does govern the ref -- $rule_count rule(s) of type: $rule_types -- so the remedy is to add a required_status_checks rule to the ruleset that already exists, not to create a ruleset."
  fi
  fault 3 "$repo@$base_ref returned an EMPTY rule list, so Gate A has nothing to evaluate. An empty list does NOT by itself establish an ungoverned ref, so do not act on it by adding a ruleset before confirming which cause it is. A ruleset the token cannot SEE is not one of the causes: this endpoint returns organization-sourced rules in full even to a wholly unauthenticated caller, and a caller that cannot read the repository is refused 401/403/404 rather than handed an empty list -- that refusal is already fatal above. The two causes that do produce this shape are indistinguishable from the response alone: (1) the ref exists and no ruleset targets it -- remedy: add a ruleset carrying a required_status_checks rule; (2) nothing matches the path requested, repos/$repo/rules/branches/$base_ref_path, because the ref was deleted, renamed, or encoded wrongly -- this endpoint answers 200 with an empty list for a nonexistent branch exactly as it does for an ungoverned one -- remedy: fix the ref, do NOT add a ruleset. Check that the ref exists under that exact path before choosing."
fi

# A required context is matched by EXACT name — the form branch protection
# itself uses — and, when the ruleset binds it to an app, by that app's id.
# An earlier draft also matched a synthesized "<workflowName> / <name>", which
# let any workflow satisfy a required context by naming a job after it.
gate_a="$(jq -r --argjson pass "$PASSING" --argjson required "$required" '
  [ $required[]
    | . as $req
    | ([ $checks[] | select(.name == $req.context) ]) as $named
    | if ($named | length) == 0 then "absent: \($req.context)"
      else
        ( if $req.integration_id == null then $named
          else [ $named[] | select(.app_id == $req.integration_id) ] end ) as $owned
        | if ($owned | length) == 0 then
            "wrong producer: \($req.context) (ruleset binds app \($req.integration_id); head has \($named | map(.app_id | tostring) | join(",")))"
          elif ($owned | any(.status != "COMPLETED")) then "pending: \($req.context)"
          elif ($owned | any(.conclusion as $c | ($pass | index($c)) == null)) then
            "not passing: \($req.context) (\($owned | map(.conclusion) | join(",")))"
          # A set-aside cancellation was backed by nothing in its superseder when that
          # run omitted the check (ADR 0226), so the context needs a SUCCESS of its own.
          elif any($superseded[]; . == $req.context) and ($owned | any(.conclusion == "SUCCESS") | not) then
            "not passing: \($req.context) (\($owned | map(.conclusion) | join(",")); a superseded cancellation needs a SUCCESS)"
          # ADR 0229 (#1755). SKIPPED and NEUTRAL stay passing conclusions, but neither
          # is evidence that the work of the context ran on this head: a draft-time run skips
          # the heavy jobs (ADR 0223), and until the ready-time run creates its jobs that
          # skip is the only report the context has. So a required context needs a SUCCESS.
          elif ($owned | any(.conclusion == "SUCCESS") | not) then
            "not passing: \($req.context) (\($owned | map(.conclusion) | join(",")); a required context needs a SUCCESS report, and a skip is not evidence its work ran)"
          else empty end
      end
  ]
  # ADR 0229 (#1755). A run that has not finished may not have created its jobs yet,
  # so it is pending whatever the check inventory says; which contexts it will report
  # is unknowable until it does, so every unfinished run on the head counts.
  #
  # A run can stay unfinished for days (a queued run with no runner, or one a
  # concurrency group holds behind such a run on an OLDER head), so the refusal names
  # the run, links it, and states its age. Those fields are display only: they are not
  # validated, never select anything, and an unreadable one is left out.
  + ( [ $runs[] | select(.status != "completed")
        | ( try ((now - (.created_at | fromdateiso8601)) | floor | if . < 0 then 0 else . end)
            catch null ) as $age
        | [ (.name | strings),
            ( if $age == null then empty
              else "created \(.created_at) (\(
                if $age >= 86400 then "\($age / 86400 | floor)d \($age % 86400 / 3600 | floor)h"
                elif $age >= 3600 then "\($age / 3600 | floor)h \($age % 3600 / 60 | floor)m"
                else "\($age / 60 | floor)m" end) ago)" end ),
            (.html_url | strings) ] as $detail
        | "pending: workflow run \(.id) (\(.status | ascii_upcase))"
          + (if ($detail | length) > 0 then " " + ($detail | join(", ")) else "" end) ]
      | if length > 0
        then . + [ "a workflow run on the head has not finished: cancel the stuck run, or wait for it; never widen the admission gate" ]
        else . end )
  | join("; ")
' --argjson checks "$gated_checks" --argjson superseded "$superseded_names" --argjson runs "$runs" <<<'null')" \
  || fault 1 "failed to evaluate required contexts for $repo#$pr"

[ -z "$gate_a" ] \
  || fault 3 "Gate A: $repo#$pr head $head_sha does not satisfy every required context of $base_ref — $gate_a"

# A ruleset may declare a required context without binding it to an app. Gate A
# then has nothing to check provenance against and matches on display name
# alone, which is the ADR 0024 class it otherwise closes. That degradation is
# real and must be visible rather than silent; it is a warning and not a fault
# because refusing every unbound context would refuse rulesets that are
# currently correct, including this organization's own hub.
unbound="$(jq -r --argjson required "$required" -n '
  [ $required[] | select(.integration_id == null) | .context ] | join(", ")')" \
  || fault 1 "failed to evaluate unbound required contexts for $repo#$pr"
[ -z "$unbound" ] \
  || annotate warning "Gate A matched these required contexts on display name alone, because $base_ref binds them to no app: $unbound"

# ---------------------------------------------------------------- Gate B ----
# A check entry proves a check REPORTED; it does not prove the check did work.
# ADR 0178's `deferred-ci` runs only on a defer, so its having RUN at all — any
# conclusion other than SKIPPED, including one this gate would otherwise call
# passing — is direct evidence the head was never exercised.
deferred_checks="$(jq -r --arg pat "$DEFERRED_JOB_PATTERN" '
  [ $checks[]
    | select(.name | test($pat; "i"))
    | select(.conclusion != "SKIPPED")
    | "\(.name)=\(if .status != "COMPLETED" then .status else .conclusion end)"
  ] | unique | join(", ")
' --argjson checks "$checks" <<<'null')" \
  || fault 1 "failed to evaluate deferral checks for $repo#$pr"

[ -z "$deferred_checks" ] \
  || fault 4 "Gate B: $repo#$pr head $head_sha was DEFERRED, not verified — $deferred_checks. No test, lint, type check, or repository-local contract guard ran on this head (ADR 0178, Verjson/verjson-cli#251). Wait for the release-age gate to clear and Renovate to rebase, or force a real run with workflow_dispatch. Do not merge, and do not relax this gate to make the head look green."

# ADR 0156's annotation still covers consumers of the `ci-eligibility` composite
# action, which build their own jobs and never emit a `deferred-ci` check.
check_run_ids="$(jq -r '[ $checks[] | select(.kind == "check_run" and .conclusion != "") | .id ] | unique | .[]' \
  --argjson checks "$checks" <<<'null')" \
  || fault 1 "failed to enumerate check runs for $repo@$head_sha"

deferred_annotations=""
while IFS= read -r id; do
  [ -n "$id" ] || continue
  titles="$(gh api --paginate "repos/$repo/check-runs/$id/annotations?per_page=100" </dev/null \
    | jq -s -r --arg pat "$DEFERRED_ANNOTATION_PATTERN" '
        [.[][]? | select(.title != null and (.title | test($pat))) | .title] | join(", ")
      ')" \
    || permission_fault "failed to fetch annotations for check-run $id on $repo" "Checks" "checks"
  [ -n "$titles" ] && deferred_annotations="${deferred_annotations:+$deferred_annotations; }check-run $id: $titles"
done <<<"$check_run_ids"

[ -z "$deferred_annotations" ] \
  || fault 4 "Gate B: $repo#$pr head $head_sha carries a deferral annotation, so a check reported without executing its work — $deferred_annotations (ADR 0156)"

# ---------------------------------------------------------------- Gate C ----
gate_c="$(jq -r --argjson pass "$PASSING" '
  [ $checks[]
    | select(.conclusion as $c | (.status != "COMPLETED") or (($pass | index($c)) == null))
    | "\(.name)=\(if .status != "COMPLETED" then .status else .conclusion end)"
  ] | unique | join(", ")
' --argjson checks "$gated_checks" <<<'null')" \
  || fault 1 "failed to evaluate non-required checks for $repo#$pr"

[ -z "$gate_c" ] \
  || fault 5 "Gate C: $repo#$pr head $head_sha has pending or non-passing checks outside the required set — $gate_c"

echo true
