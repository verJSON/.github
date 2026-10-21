#!/usr/bin/env bash
# Provenance for a trusted check produced by an organization ruleset-required workflow
# (#1861, ADR 0081 2026-10-21 correction).
#
# Such a run executes a workflow file stored in Verjson/.github at the revision the
# organization ruleset pins; the consumer repository has no workflow entity for it, so
# the consumer-workflow checks (workflow metadata, head blob equal to the default-branch
# blob) cannot apply. This helper replaces them with two reads, both fail-closed:
#
#   required-workflow-provenance.sh pinned-revision <target-repo> <branch> <workflow-path>
#       Print the one 40-hex revision that every active ORGANIZATION `workflows` rule
#       on the branch pins for <workflow-path> in Verjson/.github, after proving that
#       revision is canonical main or an ancestor of it. Absent, ref-only, ambiguous,
#       repository-level, or unreadable pins fail.
#
#   required-workflow-provenance.sh run-revision <run-node-id> <run-id> <workflow-id> <workflow-path> <pinned-sha>
#       Exit 0 only when GitHub's GraphQL WorkflowRun.file reports that this exact run
#       executed exactly <workflow-path> from Verjson/.github at <pinned-sha>. The REST
#       run payload names no source revision; WorkflowRun.file.repositoryFileUrl is the
#       field GitHub populates with it (recorded from verjson-cli-projects#159).
#
# Diagnostics go to stderr; the caller owns the ::error annotation and the exit.
set -euo pipefail

readonly CANONICAL_REPOSITORY=Verjson/.github

fail() { printf 'required-workflow provenance: %s\n' "$1" >&2; exit 1; }

# `jq -e <predicate>` alone is not a gate: jq 1.6 exits 0 on empty input, and it judges
# only the first of several documents. Exactly one well-formed document must satisfy it.
json_satisfies() { # json_satisfies <jq-predicate> [jq-options...]  (stdin)
  local predicate="$1"
  shift
  jq -en "$@" "[inputs] | length == 1 and (.[0] | $predicate)" >/dev/null 2>&1
}

is_workflow_path() { [[ "$1" =~ ^\.github/workflows/[A-Za-z0-9._-]+\.ya?ml$ ]]; }
is_id() { [[ "$1" =~ ^[123456789][0123456789]{0,19}$ ]]; }

pinned_revision() {
  [ "$#" -eq 3 ] || fail "pinned-revision takes <target-repo> <branch> <workflow-path>"
  local target_repo="$1" branch="$2" workflow_path="$3"
  local branch_path canonical canonical_id rules pins pinned canonical_main relation
  [[ "$target_repo" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || fail "malformed target repository"
  [[ "$branch" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "malformed branch name"
  is_workflow_path "$workflow_path" || fail "malformed workflow path"
  # A path segment, so "/" is encoded too: a literal one would re-address the endpoint.
  branch_path="$(jq -rn --arg branch "$branch" '$branch | @uri')"

  canonical="$(gh api "repos/$CANONICAL_REPOSITORY")" || fail "canonical repository identity is unreadable"
  json_satisfies '(.id | type == "number" and . > 0 and floor == .) and
    (.full_name | type == "string" and ascii_downcase == "verjson/.github")' <<<"$canonical" ||
    fail "canonical repository identity is malformed or is not Verjson/.github"
  canonical_id="$(jq -r .id <<<"$canonical")"

  # Effective rules for the branch the PR merges into; only active enforcement appears.
  rules="$(gh api --paginate "repos/$target_repo/rules/branches/$branch_path?per_page=100")" ||
    fail "effective branch rules are unreadable"
  # Every page must be an array of rule objects, and every `workflows` rule must carry a
  # readable source and workflow list: a malformed rule is refused, never skipped.
  pins="$(jq -sc --argjson repo_id "$canonical_id" --arg path "$workflow_path" '
    if length > 0 and all(.[]; type == "array" and all(.[]; type == "object")) then
      [.[][] | select(.type == "workflows")] as $rules
      | if all($rules[];
          (.ruleset_source_type | type == "string") and
          (.ruleset_source | type == "string") and
          (.parameters.workflows | type == "array" and all(.[]; type == "object")))
        then [$rules[]
              | select(.ruleset_source_type == "Organization" and
                       (.ruleset_source | ascii_downcase) == "verjson")
              | .parameters.workflows[]
              | select(.repository_id == $repo_id and .path == $path)]
        else error("malformed workflows rule") end
    else error("malformed effective rules") end' <<<"$rules" 2>/dev/null)" ||
    fail "effective branch rules are malformed"
  json_satisfies 'type == "array" and length > 0 and
    all(.[]; .sha | type == "string" and test("^[0123456789abcdef]{40}$")) and
    ([.[].sha] | unique | length) == 1' <<<"$pins" ||
    fail "no single exact organization ruleset pin for $workflow_path in $CANONICAL_REPOSITORY: $pins"
  pinned="$(jq -r '.[0].sha' <<<"$pins")"
  [[ "$pinned" =~ ^[0123456789abcdef]{40}$ ]] || fail "pinned revision is malformed"

  # The ruleset is the trust root, but a pin to an unmerged commit object is not a
  # reviewed revision (the same reachability ADR 0043 demands of the gate itself).
  canonical_main="$(gh api "repos/$CANONICAL_REPOSITORY/commits/main" --jq '.sha // ""')" ||
    fail "canonical main is unreadable"
  [[ "$canonical_main" =~ ^[0123456789abcdef]{40}$ ]] || fail "canonical main is malformed"
  if [ "$pinned" != "$canonical_main" ]; then
    relation="$(gh api "repos/$CANONICAL_REPOSITORY/compare/$pinned...$canonical_main" --jq '.status // ""')" ||
      fail "pinned revision reachability is unreadable"
    [ "$relation" = ahead ] || fail "pinned revision $pinned is not reachable from canonical main"
  fi
  printf '%s\n' "$pinned"
}

run_revision() {
  [ "$#" -eq 5 ] || fail "run-revision takes <run-node-id> <run-id> <workflow-id> <workflow-path> <pinned-sha>"
  local node_id="$1" run_id="$2" workflow_id="$3" workflow_path="$4" pinned="$5" response
  [[ "$node_id" =~ ^[A-Za-z0-9_=-]{1,128}$ ]] || fail "run node identity is missing or malformed"
  is_id "$run_id" || fail "malformed run ID"
  is_id "$workflow_id" || fail "malformed workflow ID"
  is_workflow_path "$workflow_path" || fail "malformed workflow path"
  [[ "$pinned" =~ ^[0123456789abcdef]{40}$ ]] || fail "malformed pinned revision"
  [[ "${GITHUB_SERVER_URL:-}" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]] || fail "GITHUB_SERVER_URL is malformed"

  # shellcheck disable=SC2016 # GraphQL variables, not shell expansions
  response="$(gh api graphql \
    -f query='query($id: ID!) { node(id: $id) { ... on WorkflowRun { databaseId workflow { databaseId } file { path repositoryName repositoryFileUrl } } } }' \
    -f id="$node_id")" || fail "workflow run provenance is unreadable"
  # repositoryName is compared ASCII-case-insensitively (ADR 0213: the organization
  # reports its display case `verJSON`); ascii_downcase leaves non-ASCII untouched, so a
  # lookalike never equals the literal. The file URL is then rebuilt from the reported
  # name, the pinned SHA, and the declared path and must match byte for byte.
  json_satisfies '(has("errors") | not) and
    (.data.node | type == "object") and
    .data.node.databaseId == $run_id and
    .data.node.workflow.databaseId == $workflow_id and
    (.data.node.file | type == "object") and
    .data.node.file.path == $path and
    (.data.node.file.repositoryName | type == "string" and ascii_downcase == "verjson/.github") and
    .data.node.file.repositoryFileUrl ==
      ($server + "/" + .data.node.file.repositoryName + "/blob/" + $sha + "/" + $path)' \
    --argjson run_id "$run_id" --argjson workflow_id "$workflow_id" \
    --arg path "$workflow_path" --arg sha "$pinned" --arg server "$GITHUB_SERVER_URL" <<<"$response" ||
    fail "run $run_id did not execute $workflow_path from $CANONICAL_REPOSITORY at $pinned: $response"
}

case "${1-}" in
  pinned-revision) shift; pinned_revision "$@" ;;
  run-revision) shift; run_revision "$@" ;;
  *) fail "usage: required-workflow-provenance.sh pinned-revision|run-revision ..." ;;
esac
