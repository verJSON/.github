#!/usr/bin/env bash
set -euo pipefail

readonly ORG="${PRIVILEGED_MERGE_ORG:-Verjson}"
readonly SECRET_NAME="${PRIVILEGED_MERGE_SECRET_NAME:-MERGE_APP_PRIVATE_KEY}"
readonly MERGE_ENVIRONMENT="merge-app"
readonly CALLER_PATH=".github/workflows/ai-privileged-merge.yml"
readonly RETRY_PATH=".github/workflows/ai-promotion-retry.yml"
readonly GENERATOR="scripts/gen-privileged-merge-caller.sh"
readonly CANONICAL_REPOSITORY="$ORG/.github"
readonly AUDIT_SHA="${PRIVILEGED_MERGE_AUDIT_SHA:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT
readonly LOCAL_CANONICAL_WORKFLOW="$REPO_ROOT/$CALLER_PATH"
readonly LOCAL_CANONICAL_RETRY="$REPO_ROOT/$RETRY_PATH"
readonly EVIDENCE_PAGE_SIZE=100
readonly EVIDENCE_MAX_PAGES=5
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if [[ ! "$AUDIT_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "::error title=Invalid privileged merge audit SHA::PRIVILEGED_MERGE_AUDIT_SHA must be a canonical lowercase 40-character commit SHA."
  exit 1
fi
if [ ! -r "$LOCAL_CANONICAL_WORKFLOW" ] || [ ! -r "$LOCAL_CANONICAL_RETRY" ]; then
  echo "::error title=Missing local canonical privileged merge workflow::paths='$CALLER_PATH $RETRY_PATH'"
  exit 1
fi
if [ -z "${GH_TOKEN:-}" ]; then
  echo "::error title=Missing ORG_ADMIN_TOKEN::Fleet conformance cannot verify privileged merge callers or organization-secret access."
  exit 1
fi
command -v gh >/dev/null 2>&1 || {
  echo "::error title=Missing gh::Fleet conformance requires the GitHub CLI."
  exit 1
}
command -v base64 >/dev/null 2>&1 || {
  echo "::error title=Missing base64::Fleet conformance requires base64 to inspect caller content."
  exit 1
}
command -v jq >/dev/null 2>&1 || {
  echo "::error title=Missing jq::Fleet conformance requires jq to validate reviewed required-check policies."
  exit 1
}

extract_canonical_pins() { # extract_canonical_pins <canonical-workflow-basename>  (body on stdin)
  # The privileged-merge caller and the promotion-retry caller carry the same `uses:`
  # shape and differ only in which canonical workflow they name. Holding a copy of the
  # expression at each site is what let them drift: the retry copy kept the narrow
  # `([0-9a-f]{40})` capture, and the fail-open that came with it, after the caller's was
  # widened. One expression, one argument, so a change cannot reach one site only.
  #
  # The capture is deliberately wide. A narrower one silently turns a present-but-mutable
  # pin into zero captures, which the count arm then explains as a missing or duplicated
  # `uses:` line -- the wrong diagnosis for the most likely wrong pin. The 40-hex guard
  # downstream still judges whatever ref is captured. The optional trailing group is a
  # YAML comment, which requires whitespace before the `#`; `@<sha>#v1` is a single ref
  # and stays refused as a non-SHA pin.
  #
  # The basename is the only interpolated value, and it is expression text rather than
  # input: a `%` would close the `s` delimiter, a `|` would shift the capture, and `ai-.*`
  # would still match. Both call sites pass a bare literal, so that is latent rather than
  # live -- close it by construction instead of by asserting it in a comment. Adopter text
  # only ever reaches sed on stdin, so no caller body can inject expression text.
  local wf="${1:?extract_canonical_pins requires a canonical workflow basename}"
  case "$wf" in
    ai-privileged-merge | ai-promotion-retry) ;;
    *)
      echo "::error title=Unknown canonical workflow basename::extract_canonical_pins refuses '$wf'" >&2
      return 1
      ;;
  esac
  sed -nE "s%^[[:space:]]+uses: Verjson/\.github/\.github/workflows/$wf\.yml@([^[:space:]]+)([[:space:]]+#.*)?[[:space:]]*\$%\1%p"
}

json_satisfies() { # json_satisfies <jq-predicate> [jq-options...]  (one JSON document on stdin)
  # Every decision this audit takes on a JSON body goes through here. `jq -e <pred>` is
  # not a safe gate: jq 1.6 exits 0 on empty or whitespace-only input, so an empty body
  # with a successful status satisfied any predicate. `input | <pred>` closes that but
  # judges only the first of several documents and ignores trailing garbage. Requiring
  # exactly one well-formed document is stricter than both.
  local predicate="${1:?json_satisfies requires a jq predicate}"
  shift
  jq -en "$@" "[inputs] | length == 1 and (.[0] | $predicate)" >/dev/null
}

find_latest_completed_run() { # find_latest_completed_run <repository> <workflow-id>
  local repository="$1" workflow_id="$2" page response page_count exhausted=true
  local candidates='[]'
  COMPLETED_RUN_IDENTITY=""
  for ((page = 1; page <= EVIDENCE_MAX_PAGES; page++)); do
    if ! response="$(gh api "repos/$repository/actions/workflows/$workflow_id/runs?event=pull_request&per_page=$EVIDENCE_PAGE_SIZE&page=$page")" ||
      ! json_satisfies '.workflow_runs | type == "array"' <<<"$response"; then
      echo "::error title=Unreadable required-check workflow evidence::repository=$repository workflow_id=$workflow_id page=$page" >&2
      return 1
    fi
    page_count="$(jq '.workflow_runs | length' <<<"$response")"
    candidates="$(jq -cn --argjson prior "$candidates" --argjson page "$response" \
      '$prior + [$page.workflow_runs[] | select(.status == "completed")]')"
    if [ "$page_count" -lt "$EVIDENCE_PAGE_SIZE" ]; then
      exhausted=false
      break
    fi
  done
  if [ "$exhausted" = true ]; then
    echo "::error title=Required-check workflow evidence exceeded bound::repository=$repository workflow_id=$workflow_id pages=$EVIDENCE_MAX_PAGES page_size=$EVIDENCE_PAGE_SIZE reason='newer run noise exhausted the bounded complete search'" >&2
    return 1
  fi
  COMPLETED_RUN_IDENTITY="$(jq -r 'sort_by(.id) | last // {} | [.id // "", .head_sha // ""] | @tsv' <<<"$candidates")"
}

validate_retry_workflow_names() { # validate_retry_workflow_names <repository> <names-json> <policy-json>
  local repository="$1" names="$2" policy="$3" workflow_id metadata workflow_name
  local expected_names='[]'
  while IFS= read -r workflow_id; do
    if ! metadata="$(gh api "repos/$repository/actions/workflows/$workflow_id")" ||
      ! json_satisfies '.name | type == "string" and length > 0' <<<"$metadata" ||
      ! workflow_name="$(jq -r '.name' <<<"$metadata")"; then
      echo "::error title=Unreadable retry workflow identity::repository=$repository workflow_id=$workflow_id"
      return 1
    fi
    expected_names="$(jq -cn --argjson prior "$expected_names" --arg name "$workflow_name" '$prior + [$name]')"
  done < <(jq -r '[.[].workflow_id] | unique[]' <<<"$policy")
  expected_names="$(jq -c 'sort' <<<"$expected_names")"
  names="$(jq -c 'sort' <<<"$names")"
  if [ "$expected_names" != "$names" ]; then
    echo "::error title=Retry workflow names do not match required-check workflow IDs::repository=$repository expected=$expected_names reviewed=$names"
    return 1
  fi
}

validate_required_check_policy() { # validate_required_check_policy <repository> <default-branch> <policy-json>
  local repository="$1" default_branch="$2" policy="$3"
  local branch_uri rules rule_checks policy_checks required metadata workflow_id workflow_path check_name app_id
  local run_identity run_id jobs latest_job check_run_url check_run
  branch_uri="$(jq -rn --arg branch "$default_branch" '$branch | @uri')"
  if ! rules="$(gh api --paginate "repos/$repository/rules/branches/$branch_uri" | jq -s '[.[][]]')"; then
    echo "::error title=Unreadable effective required checks::repository=$repository branch=$default_branch"
    return 1
  fi
  rule_checks="$(jq -c '[
    .[] | select(.type == "required_status_checks") |
    .parameters.required_status_checks[]? |
    select(.context | test("^(AI review authorization|AI terminal merge promotion|AI terminal promotion retry)$") | not) |
    {name: .context, app_id: .integration_id}
  ]' <<<"$rules")" || return 1
  if ! json_satisfies 'all(.[]; (.name | type == "string" and length > 0) and
      (.app_id | type == "number" and . > 0 and floor == .))' <<<"$rule_checks"; then
    echo "::error title=Required-check rule lacks an exact App binding::repository=$repository branch=$default_branch required=$rule_checks"
    return 1
  fi
  if ! json_satisfies 'sort_by(.name) | group_by(.name) | all(.[]; length == 1)' <<<"$rule_checks"; then
    echo "::error title=Ambiguous required-check rule identity::repository=$repository branch=$default_branch required=$rule_checks reason='same-name required contexts cannot be selected unambiguously at runtime'"
    return 1
  fi
  rule_checks="$(jq -c 'sort_by(.name, .app_id)' <<<"$rule_checks")"
  policy_checks="$(jq -c '[.[] | {name, app_id}] | sort_by(.name, .app_id)' <<<"$policy")" || return 1
  if [ "$rule_checks" != "$policy_checks" ]; then
    echo "::error title=Required-check policy does not match effective branch protection::repository=$repository branch=$default_branch required=$rule_checks reviewed=$policy_checks"
    return 1
  fi

  while IFS= read -r required; do
    workflow_id="$(jq -r .workflow_id <<<"$required")"
    workflow_path="$(jq -r .workflow_path <<<"$required")"
    check_name="$(jq -r .name <<<"$required")"
    app_id="$(jq -r .app_id <<<"$required")"
    # shellcheck disable=SC2016 # jq program text, expanded by jq rather than the shell
    if ! metadata="$(gh api "repos/$repository/actions/workflows/$workflow_id")" ||
      ! json_satisfies '.id == $id and .path == $path and .state == "active"' \
        --arg path "$workflow_path" --argjson id "$workflow_id" <<<"$metadata"; then
      echo "::error title=Invalid required-check workflow identity::repository=$repository check=$check_name workflow_id=$workflow_id path=$workflow_path reason='workflow is missing, renamed, inactive, or mismatched'"
      return 1
    fi
    if ! find_latest_completed_run "$repository" "$workflow_id"; then
      return 1
    fi
    run_identity="$COMPLETED_RUN_IDENTITY"
    IFS=$'\t' read -r run_id _ <<<"$run_identity"
    if ! [[ "$run_id" =~ ^[1-9][0-9]*$ ]]; then
      echo "::error title=Missing required-check workflow evidence::repository=$repository check=$check_name workflow_id=$workflow_id reason='no completed pull-request run can prove the reviewed identity'"
      return 1
    fi
    if ! jobs="$(gh api --paginate "repos/$repository/actions/runs/$run_id/jobs?per_page=100" | jq -s '[.[].jobs[]]')"; then
      echo "::error title=Unreadable required-check job evidence::repository=$repository check=$check_name run_id=$run_id"
      return 1
    fi
    latest_job="$(jq -c --arg name "$check_name" '[.[] | select(.name == $name)] | sort_by(.id) | last // {}' <<<"$jobs")"
    check_run_url="$(jq -r '.check_run_url // ""' <<<"$latest_job")"
    if [[ "$check_run_url" != "https://api.github.com/repos/$repository/check-runs/"* ]]; then
      echo "::error title=Missing or renamed required check::repository=$repository check=$check_name workflow_id=$workflow_id reason='latest completed pull-request workflow did not publish the reviewed check name'"
      return 1
    fi
    # shellcheck disable=SC2016 # jq program text, expanded by jq rather than the shell
    if ! check_run="$(gh api "$check_run_url")" ||
      ! json_satisfies '.name == $name and .app.id == $app_id' \
        --arg name "$check_name" --argjson app_id "$app_id" <<<"$check_run"; then
      echo "::error title=Wrong required-check App identity::repository=$repository check=$check_name app_id=$app_id workflow_id=$workflow_id"
      return 1
    fi
  done < <(jq -c '.[]' <<<"$policy")
}
# A "Not Found" for the secret is withdrawal evidence only when the same token reads a
# complete, well-formed organization secret inventory that also omits the name: GitHub
# answers 404 for a resource the caller may not see, and a missing, zero-page or
# truncated listing would otherwise prove absence by omission. A complete listing with
# total_count 0 is genuine evidence and is accepted. An empty or whitespace-only body is
# not a listing at all; json_satisfies rejects it, as it does any body that is not
# exactly one JSON document.
# shellcheck disable=SC2016 # jq program text, expanded by jq rather than the shell
readonly ORGANIZATION_SECRET_ABSENT='. as $pages
  | ($pages | type == "array" and length > 0)
  and all($pages[]; type == "object" and (.secrets | type == "array") and (.total_count | type == "number"))
  and ([$pages[].secrets[]] as $secrets
    | all($secrets[]; type == "object" and (.name | type == "string" and length > 0))
    and all($pages[]; .total_count == ($secrets | length))
    and ([$secrets[].name] | length == (unique | length))
    and (any($secrets[]; .name == $name) | not))'
if visibility="$(
  gh api "orgs/$ORG/actions/secrets/$SECRET_NAME" --jq .visibility 2>"$tmp/organization-secret.err"
)"; then
  case "$visibility" in
    all|private|selected) ;;
    *)
      echo "::error title=Invalid organization secret visibility::$SECRET_NAME returned unsupported visibility '$visibility'."
      exit 1
      ;;
  esac
else
  cat "$tmp/organization-secret.err" >&2
  if grep -Eq '\(HTTP 404\)$' "$tmp/organization-secret.err" &&
    organization_secrets="$(gh api --paginate --slurp "orgs/$ORG/actions/secrets?per_page=100")" &&
    json_satisfies "$ORGANIZATION_SECRET_ABSENT" --arg name "$SECRET_NAME" <<<"$organization_secrets"; then
    visibility=withdrawn
  else
    echo "::error title=Unreadable organization secret::Cannot verify $SECRET_NAME visibility for $ORG."
    exit 1
  fi
fi

if [ -n "${VERJSON_MANAGED_REPOSITORIES:-}" ]; then
  repositories="$VERJSON_MANAGED_REPOSITORIES"
else
  repositories="$(
    gh api --paginate "orgs/$ORG/repos?type=all&per_page=100" \
      --jq '.[] | select((.archived or .fork or .is_template) | not) | .full_name'
  )" || {
    echo "::error title=Unreadable repository fleet::Cannot enumerate active $ORG repositories."
    exit 1
  }
fi

declare -A selected_repositories=()
if [ "$visibility" = selected ]; then
  selected="$(
    gh api --paginate "orgs/$ORG/actions/secrets/$SECRET_NAME/repositories" \
      --jq '.repositories[].full_name'
  )" || {
    echo "::error title=Unreadable organization secret access::Cannot enumerate repositories selected for $SECRET_NAME."
    exit 1
  }
  while IFS= read -r repository; do
    [ -n "$repository" ] && selected_repositories["$repository"]=1
  done <<<"$selected"
fi

failures=0
repositories_scanned=0
consumers=0
while IFS= read -r repository; do
  [ -n "$repository" ] || continue
  repositories_scanned=$((repositories_scanned + 1))
  # Reset every loop-scoped variable at the top of the iteration, not the bottom: several
  # branches below leave the iteration early, and a reset placed after them would carry one
  # repository's state into the next repository's verdict.
  unset metadata default_branch visibility_type has_secret \
    caller_response caller_content caller_error caller_pins caller_contract_sha canonical_caller \
    caller_available caller_file content_ref direct_consumer relation historical_generator \
    historical_workflow generator_response workflow_response workflow_call_block required_check_lines \
    caller_required_checks caller_required_checks_shell canonical_policy_lines canonical_required_checks \
    retry_response retry_file retry_available retry_content retry_pins retry_policy_lines retry_workflow_lines \
    retry_required_checks retry_workflow_names retry_workflow_names_shell canonical_retry \
    historical_retry_workflow retry_workflow_response environment_key_response
  metadata="$(gh api "repos/$repository" --jq '[.default_branch,.visibility] | @tsv')" || {
    echo "::error title=Unreadable repository metadata::repository=$repository"
    failures=$((failures + 1))
    continue
  }
  IFS=$'\t' read -r default_branch visibility_type <<<"$metadata"
  if [ -z "$default_branch" ] || [ -z "$visibility_type" ]; then
    echo "::error title=Invalid repository metadata::repository=$repository"
    failures=$((failures + 1))
    continue
  fi

  direct_consumer=false
  # $default_branch is adopter-controlled text reaching a query VALUE below, where the API
  # wants "/" literal; percent-encode everything else.
  content_ref="$(jq -rn --arg branch "$default_branch" '$branch | @uri | gsub("%2F"; "/")')"
  if [ "$repository" = "$CANONICAL_REPOSITORY" ]; then
    direct_consumer=true
    content_ref="$AUDIT_SHA"
    consumers=$((consumers + 1))
  fi

  caller_response=""
  caller_error=""
  caller_available=false
  caller_file="$tmp/caller-$repositories_scanned.yml"
  if ! caller_response="$(gh api "repos/$repository/contents/$CALLER_PATH?ref=$content_ref" \
    --jq .content 2>&1)"; then
    caller_error="$caller_response"
    if grep -q 'HTTP 404' <<<"$caller_error"; then
      if [ "$direct_consumer" = true ]; then
        echo "::error title=Missing canonical privileged merge workflow::repository=$repository path=$CALLER_PATH audit_sha=$AUDIT_SHA"
        failures=$((failures + 1))
      else
        # Secret visibility is not consumer registration: this credential is
        # organization-wide, while only repositories with a caller can invoke
        # the reusable terminal workflow.
        continue
      fi
    else
      if [ "$direct_consumer" = true ]; then
        echo "::error title=Unreadable canonical privileged merge workflow::repository=$repository path=$CALLER_PATH audit_sha=$AUDIT_SHA"
      else
        echo "::error title=Unreadable privileged merge caller::repository=$repository path=$CALLER_PATH"
      fi
      failures=$((failures + 1))
      [ "$direct_consumer" = true ] || continue
    fi
  elif ! printf '%s' "$caller_response" | base64 --decode >"$caller_file" 2>/dev/null; then
    if [ "$direct_consumer" = true ]; then
      echo "::error title=Unreadable canonical privileged merge workflow::repository=$repository path=$CALLER_PATH reason='invalid base64 content'"
    else
      echo "::error title=Unreadable privileged merge caller::repository=$repository path=$CALLER_PATH reason='invalid base64 content'"
    fi
    failures=$((failures + 1))
    [ "$direct_consumer" = true ] || continue
  elif [ -z "$(tr -d "[:space:]" <"$caller_file")" ]; then
    # `base64 --decode` succeeds on an empty stream, so a fetch that returned nothing
    # decodes to an artifact with no content that is then read as content. Left
    # unguarded that is not a missing signal but a wrong one: the audit states a fact
    # about the adopter's caller that it never established. Test the content rather
    # than the byte count -- a lone newline is a non-empty file carrying nothing, and
    # the read below strips it to the empty string all the same.
    if [ "$direct_consumer" = true ]; then
      echo "::error title=Empty canonical privileged merge workflow::repository=$repository path=$CALLER_PATH audit_sha=$AUDIT_SHA reason='fetched artifact decoded to no content, which is a failed read rather than an absent file'"
    else
      echo "::error title=Empty privileged merge caller::repository=$repository path=$CALLER_PATH reason='fetched artifact decoded to no content, which is a failed read rather than an absent file'"
    fi
    failures=$((failures + 1))
    [ "$direct_consumer" = true ] || continue
  else
    caller_available=true
    caller_content="$(<"$caller_file")"
  fi

  retry_available=false
  retry_response=""
  retry_file="$tmp/retry-$repositories_scanned.yml"
  if ! retry_response="$(gh api "repos/$repository/contents/$RETRY_PATH?ref=$content_ref" --jq .content 2>&1)"; then
    if [ "$direct_consumer" = true ]; then
      echo "::error title=Missing or unreadable canonical promotion retry::repository=$repository path=$RETRY_PATH audit_sha=$AUDIT_SHA"
    else
      echo "::error title=Missing or unreadable generated promotion retry::repository=$repository path=$RETRY_PATH"
    fi
    failures=$((failures + 1))
  elif ! printf '%s' "$retry_response" | base64 --decode >"$retry_file" 2>/dev/null; then
    echo "::error title=Unreadable promotion retry::repository=$repository path=$RETRY_PATH reason='invalid base64 content'"
    failures=$((failures + 1))
  elif [ -z "$(tr -d "[:space:]" <"$retry_file")" ]; then
    # Same construction, same hazard: an empty decode leaves $retry_available false so no
    # downstream check reads it as evidence, and the failed read is reported on its own.
    echo "::error title=Empty promotion retry::repository=$repository path=$RETRY_PATH reason='fetched artifact decoded to no content, which is a failed read rather than an absent file'"
    failures=$((failures + 1))
  else
    retry_available=true
    retry_content="$(<"$retry_file")"
  fi

  if [ "$direct_consumer" = true ]; then
    if [ "$caller_available" = true ] && ! cmp -s "$LOCAL_CANONICAL_WORKFLOW" "$caller_file"; then
      echo "::error title=Mismatched canonical privileged merge workflow::repository=$repository path=$CALLER_PATH audit_sha=$AUDIT_SHA reason='remote bytes differ from the checked-out audit revision'"
      failures=$((failures + 1))
    fi
    if [ "$retry_available" = true ] && ! cmp -s "$LOCAL_CANONICAL_RETRY" "$retry_file"; then
      echo "::error title=Mismatched canonical promotion retry::repository=$repository path=$RETRY_PATH audit_sha=$AUDIT_SHA reason='remote bytes differ from the checked-out audit revision'"
      failures=$((failures + 1))
    fi
    mapfile -t canonical_policy_lines < <(
      sed -nE "s/^      REQUIRED_CHECK_POLICY: .*&& '(\[\{.*\}\])' \|\|.*$/\1/p" "$LOCAL_CANONICAL_WORKFLOW"
    )
    if [ "${#canonical_policy_lines[@]}" -ne 1 ]; then
      echo "::error title=Missing canonical required-check policy::repository=$repository path=$CALLER_PATH reason='workflow_dispatch must bind one reviewed non-overridable policy'"
      failures=$((failures + 1))
    else
      canonical_required_checks="${canonical_policy_lines[0]//\'\'/\'}"
      if ! validate_required_check_policy "$repository" "$default_branch" "$canonical_required_checks"; then
        failures=$((failures + 1))
      fi
    fi
  elif [ "$caller_available" = true ]; then
    consumers=$((consumers + 1))
    mapfile -t caller_pins < <(extract_canonical_pins ai-privileged-merge <<<"$caller_content")
    caller_contract_sha="${caller_pins[0]-}"
    # The rejected pin must not reach the queries below, but abandoning the iteration would
    # also abandon this repository's remaining evidence. Make the pin-dependent work
    # conditional instead, so every repository is judged on evidence of its own.
    if [ "${#caller_pins[@]}" -ne 1 ]; then
      echo "::error title=Invalid privileged merge caller pin::repository=$repository path=$CALLER_PATH reason='expected exactly one immutable canonical workflow pin'"
      failures=$((failures + 1))
    elif ! [[ "$caller_contract_sha" =~ ^[0-9a-f]{40}$ ]]; then
      echo "::error title=Invalid privileged merge caller pin::repository=$repository reason='pin is not a 40-hex commit SHA'"
      failures=$((failures + 1))
    else
      relation="$(gh api "repos/$CANONICAL_REPOSITORY/compare/$caller_contract_sha...main" --jq .status)" || {
        echo "::error title=Untrusted privileged merge caller pin::repository=$repository contract_sha=$caller_contract_sha reason='pin is absent from canonical main history'"
        failures=$((failures + 1))
        relation=""
      }
      case "$relation" in
        ahead|identical) ;;
        "") ;;
        *)
          echo "::error title=Untrusted privileged merge caller pin::repository=$repository contract_sha=$caller_contract_sha relation=$relation reason='pin is not reachable from canonical main'"
          failures=$((failures + 1))
          relation=""
          ;;
      esac

      historical_generator=""
      historical_workflow=""
      historical_retry_workflow=""
      if [ -n "$relation" ]; then
        if ! generator_response="$(gh api "repos/$CANONICAL_REPOSITORY/contents/$GENERATOR?ref=$caller_contract_sha" --jq .content)" ||
          ! historical_generator="$(printf '%s' "$generator_response" | base64 --decode 2>/dev/null)"; then
          echo "::error title=Unreadable historical caller generator::repository=$repository contract_sha=$caller_contract_sha"
          failures=$((failures + 1))
        fi
        if ! workflow_response="$(gh api "repos/$CANONICAL_REPOSITORY/contents/$CALLER_PATH?ref=$caller_contract_sha" --jq .content)" ||
          ! historical_workflow="$(printf '%s' "$workflow_response" | base64 --decode 2>/dev/null)"; then
          echo "::error title=Unreadable historical privileged workflow::repository=$repository contract_sha=$caller_contract_sha"
          failures=$((failures + 1))
        fi
        if ! retry_workflow_response="$(gh api "repos/$CANONICAL_REPOSITORY/contents/$RETRY_PATH?ref=$caller_contract_sha" --jq .content)" ||
          ! historical_retry_workflow="$(printf '%s' "$retry_workflow_response" | base64 --decode 2>/dev/null)"; then
          echo "::error title=Unreadable historical promotion retry workflow::repository=$repository contract_sha=$caller_contract_sha"
          failures=$((failures + 1))
        fi
      fi

      workflow_call_block="$(awk '
        $0 == "  workflow_call:" { capture=1 }
        capture && /^[^ ]/ { exit }
        capture { print }
      ' <<<"$historical_workflow")"
      if [ -n "$relation" ] && {
        [ -z "$historical_generator" ] ||
        ! grep -q '^      required_checks:$' <<<"$workflow_call_block" ||
        ! grep -q '^      privileged_lane:$' <<<"$workflow_call_block" ||
        ! grep -q '^  privileged_merge:$' <<<"$historical_workflow" ||
        ! grep -q '^      required_checks:$' <<<"$historical_retry_workflow";
      }; then
        echo "::error title=Incompatible privileged merge contract::repository=$repository contract_sha=$caller_contract_sha reason='historical generator or reusable interface is incomplete'"
        failures=$((failures + 1))
      elif [ -n "$relation" ] && [ -n "$historical_generator" ]; then
        mapfile -t required_check_lines < <(
          sed -nE "s/^      required_checks: '(.*)'$/\1/p" <<<"$caller_content"
        )
        if [ "${#required_check_lines[@]}" -ne 1 ]; then
          echo "::error title=Missing reviewed required-check policy::repository=$repository path=$CALLER_PATH reason='generated caller must supply exactly one repository-specific required_checks input'"
          failures=$((failures + 1))
          caller_required_checks=""
        else
          caller_required_checks="${required_check_lines[0]//\'\'/\'}"
        fi
        if [ -n "$caller_required_checks" ] && ! json_satisfies '
          type == "array" and length > 0 and
          ([.[].name] | unique | length) == length and all(.[];
            type == "object" and
            (keys | sort) == ["app_id", "name", "workflow_id", "workflow_path"] and
            (.name | type == "string" and length > 0) and
            (.app_id | type == "number" and . > 0 and floor == .) and
            (.workflow_id | type == "number" and . > 0 and floor == .) and
            (.workflow_path | type == "string" and test("^\\.github/workflows/[A-Za-z0-9._-]+\\.ya?ml$"))
          )' <<<"$caller_required_checks"; then
          echo "::error title=Invalid reviewed required-check policy::repository=$repository path=$CALLER_PATH reason='required_checks must name exact check, App, workflow ID, and workflow path identities'"
          failures=$((failures + 1))
          caller_required_checks=""
        fi
        if [ -n "$caller_required_checks" ] &&
          ! validate_required_check_policy "$repository" "$default_branch" "$caller_required_checks"; then
          failures=$((failures + 1))
        fi
        printf '%s\n' "$historical_generator" >"$tmp/historical-generator.sh"
        canonical_caller=""
        if [ -n "$caller_required_checks" ]; then
          canonical_caller="$(env -u GH_TOKEN bash "$tmp/historical-generator.sh" "$caller_contract_sha" "$caller_required_checks")" || {
            echo "::error title=Caller generation failed::repository=$repository contract_sha=$caller_contract_sha"
            failures=$((failures + 1))
            canonical_caller=""
          }
        fi
        if [ -z "$caller_required_checks" ]; then
          :
        elif [ -z "$canonical_caller" ]; then
          :
        elif [ "$caller_content" != "$canonical_caller" ]; then
          printf -v caller_required_checks_shell '%q' "$caller_required_checks"
          echo "::error title=Non-canonical privileged merge caller::repository=$repository path=$CALLER_PATH remediation='scripts/gen-privileged-merge-caller.sh $caller_contract_sha $caller_required_checks_shell > $CALLER_PATH'"
          failures=$((failures + 1))
        fi

        if [ "$retry_available" = true ]; then
          mapfile -t retry_pins < <(extract_canonical_pins ai-promotion-retry <<<"$retry_content")
          if [ "${#retry_pins[@]}" -ne 1 ] || [ "${retry_pins[0]:-}" != "$caller_contract_sha" ]; then
            echo "::error title=Invalid promotion retry caller pin::repository=$repository path=$RETRY_PATH reason='retry must pin the same immutable contract SHA as privileged merge'"
            failures=$((failures + 1))
          fi
          mapfile -t retry_policy_lines < <(
            sed -nE "s/^      required_checks: '(.*)'$/\1/p" <<<"$retry_content"
          )
          mapfile -t retry_workflow_lines < <(
            sed -nE 's/^    workflows: (\[.*\])$/\1/p' <<<"$retry_content"
          )
          retry_required_checks=""
          retry_workflow_names=""
          if [ "${#retry_policy_lines[@]}" -eq 1 ]; then
            retry_required_checks="${retry_policy_lines[0]//\'\'/\'}"
          fi
          if [ "${#retry_workflow_lines[@]}" -eq 1 ]; then
            retry_workflow_names="${retry_workflow_lines[0]}"
          fi
          if [ -z "$caller_required_checks" ] || [ -z "$retry_required_checks" ] ||
            ! json_satisfies 'type == "array" and length > 0 and (unique | length) == length and
              all(.[]; type == "string" and length > 0)' <<<"$retry_workflow_names" 2>/dev/null ||
            ! json_satisfies '.' <<<"$retry_required_checks" 2>/dev/null; then
            echo "::error title=Invalid reviewed promotion retry policy::repository=$repository path=$RETRY_PATH reason='retry must declare unique workflow names and one required_checks input'"
            failures=$((failures + 1))
          elif [ "$(jq -cS . <<<"$retry_required_checks")" != "$(jq -cS . <<<"$caller_required_checks")" ]; then
            echo "::error title=Promotion retry required-check policy drift::repository=$repository path=$RETRY_PATH reason='retry and privileged caller policies must be identical'"
            failures=$((failures + 1))
          else
            if ! validate_retry_workflow_names "$repository" "$retry_workflow_names" "$caller_required_checks"; then
              failures=$((failures + 1))
            fi
            canonical_retry="$(env -u GH_TOKEN bash "$tmp/historical-generator.sh" "$caller_contract_sha" --retry "$retry_workflow_names" "$caller_required_checks")" || {
              echo "::error title=Promotion retry generation failed::repository=$repository contract_sha=$caller_contract_sha"
              failures=$((failures + 1))
              canonical_retry=""
            }
            if [ -n "$canonical_retry" ] && [ "$retry_content" != "$canonical_retry" ]; then
              printf -v retry_workflow_names_shell '%q' "$retry_workflow_names"
              printf -v caller_required_checks_shell '%q' "$caller_required_checks"
              echo "::error title=Non-canonical promotion retry caller::repository=$repository path=$RETRY_PATH remediation='scripts/gen-privileged-merge-caller.sh $caller_contract_sha --retry $retry_workflow_names_shell $caller_required_checks_shell > $RETRY_PATH'"
              failures=$((failures + 1))
            fi
          fi
        fi
      fi
    fi
  fi
  has_secret=false
  case "$visibility" in
    all) has_secret=true ;;
    private)
      [ "$visibility_type" = private ] && has_secret=true
      ;;
    selected)
      [ "${selected_repositories[$repository]:-}" = 1 ] && has_secret=true
      ;;
    withdrawn)
      # Metadata only: the environment-secret endpoint never returns the key value.
      if environment_key_response="$(gh api "repos/$repository/environments/$MERGE_ENVIRONMENT/secrets/$SECRET_NAME" \
        2>"$tmp/environment-key.err")"; then
        # shellcheck disable=SC2016 # jq program text, expanded by jq rather than the shell
        if json_satisfies '.name == $name' --arg name "$SECRET_NAME" <<<"$environment_key_response" 2>/dev/null; then
          has_secret=true
        else
          echo "::error title=Unreadable merge-app environment key::repository=$repository environment=$MERGE_ENVIRONMENT secret=$SECRET_NAME reason='response does not identify the key'"
          failures=$((failures + 1))
          continue
        fi
      elif ! grep -Eq '\(HTTP 404\)$' "$tmp/environment-key.err"; then
        cat "$tmp/environment-key.err" >&2
        echo "::error title=Unreadable merge-app environment key::repository=$repository environment=$MERGE_ENVIRONMENT secret=$SECRET_NAME"
        failures=$((failures + 1))
        continue
      fi
      ;;
  esac
  if [ "$has_secret" != true ]; then
    if [ "$visibility" = withdrawn ]; then
      echo "::error title=Missing privileged merge App key access::repository=$repository secret=$SECRET_NAME environment=$MERGE_ENVIRONMENT remediation='provision the key in the main-only $MERGE_ENVIRONMENT environment (ADR 0166)'"
    else
      echo "::error title=Missing privileged merge App key access::repository=$repository secret=$SECRET_NAME remediation='grant repository access to the organization secret'"
    fi
    failures=$((failures + 1))
  fi
done <<<"$repositories"

if [ "$repositories_scanned" -eq 0 ]; then
  echo "::error title=Empty managed fleet::No active $ORG repositories were available for conformance verification."
  exit 1
fi
if [ "$consumers" -eq 0 ]; then
  echo "::error title=Empty privileged consumer inventory::No direct or generated privileged merge consumers were found across $repositories_scanned active repositories."
  exit 1
fi
if [ "$failures" -ne 0 ]; then
  echo "result=nonconformant repositories_scanned=$repositories_scanned consumers=$consumers failures=$failures"
  exit 1
fi

echo "result=conformant repositories_scanned=$repositories_scanned consumers=$consumers"
