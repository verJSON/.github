#!/usr/bin/env bash
# Generate a consumer's thin `ai-privileged-merge.yml` caller.
#
# Generated, not hand-written, because several details fail SILENTLY when wrong:
# the job key, the `uses:` target, and the values passed in `with:`. The
# reasoning lives in docs/decisions/0042-privileged-merge-reusable-split.
#
# Usage:
#   gen-privileged-merge-caller.sh <40-hex-contract-sha> '<required-checks-json>' ['<runner-labels-json>']
#   gen-privileged-merge-caller.sh <40-hex-contract-sha> --retry '<workflow-names-json>' '<required-checks-json>'
#   scripts/gen-privileged-merge-caller.sh <contract-sha> "$required_checks" > .github/workflows/ai-privileged-merge.yml
#
# The runner-label argument is OPTIONAL and should be omitted by every Verjson consumer
# (#405). It used to be mandatory, which meant the generator hardcoded
# `["self-hosted","general"]` into ~90 repositories: a fleet relabel then needed
# a pull request in each of them, which is precisely the coupling the
# `CI_LANE_*` variables exist to remove (ADR 0041). The canonical
# workflow's `runs-on` already resolves without it, ending at a hosted runner for
# an org with no lane variables at all (ADR 0040).
#
# It is kept, not deleted, for the one caller the lane variables cannot serve: a
# self-hosted consumer OUTSIDE Verjson, whose org has no lane variables and whose
# runners answer to labels only it knows.
set -euo pipefail

contract_sha="${1-}"
[[ "$contract_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "contract_sha must be an immutable lowercase 40-hex commit SHA" >&2
  exit 2
}
readonly TARGET="verjson.github/.github/workflows/ai-privileged-merge.yml@$contract_sha"
readonly RETRY_TARGET="verjson.github/.github/workflows/ai-promotion-retry.yml@$contract_sha"

if [ "${2-}" = --retry ]; then
  [ "$#" -eq 4 ] || { echo "--retry requires workflow-names and required-checks JSON arguments" >&2; exit 2; }
  command -v jq >/dev/null 2>&1 || { echo "jq is required to validate workflow names" >&2; exit 2; }
  # JSON flow collections are valid YAML, so compact JSON serialization admits
  # GitHub's punctuation-rich workflow names without letting their bytes alter
  # the surrounding document. Control characters and expressions are different:
  # they are meaningful after YAML parsing, so reject them at this boundary.
  workflow_names="$(jq -ce '
    if type == "array" and length > 0 and
       (unique | length) == length and
       all(.[];
         type == "string" and length > 0 and
         all(explode[];
           . >= 32 and (. < 127 or . > 159) and . != 8232 and . != 8233) and
         (contains("${{") | not))
    then . else error("unsafe workflow names") end
  ' <<<"$3" 2>/dev/null)" \
    || { echo "workflow names must be a non-empty JSON array without controls or expressions" >&2; exit 2; }
  required_checks_raw="$4"
else
  [ "$#" -ge 2 ] && [ "$#" -le 3 ] || {
    echo "required-checks JSON is mandatory; runner-labels JSON is optional" >&2
    exit 2
  }
  required_checks_raw="$2"
fi

command -v jq >/dev/null 2>&1 || { echo "jq is required to validate required checks" >&2; exit 2; }
required_checks="$(jq -ce '
  if type == "array" and length > 0 and
     ([.[].name] | unique | length) == length and
     all(.[];
       type == "object" and
       (keys | sort) == ["app_id", "name", "workflow_id", "workflow_path"] and
       (.name | type == "string" and length > 0 and
        all(explode[]; . >= 32 and (. < 127 or . > 159) and . != 8232 and . != 8233) and
        (contains("${{") | not)) and
       (.app_id | type == "number" and . > 0 and floor == .) and
       (.workflow_id | type == "number" and . > 0 and floor == .) and
       (.workflow_path | type == "string" and test("^\\.github/workflows/[A-Za-z0-9._-]+\\.ya?ml$")) and
       (.name | test("^(AI review authorization|AI terminal merge promotion|AI terminal promotion retry)$") | not) and
       (.workflow_path | test("/ai-(review-merge|privileged-merge|promotion-retry)\\.yml$") | not))
  then . else error("unsafe required checks") end
' <<<"$required_checks_raw" 2>/dev/null)" || {
  echo "required checks must be a non-empty unique array of exact check/App/workflow identities" >&2
  exit 2
}
printf -v required_checks_shell '%q' "$required_checks"
required_checks_yaml="${required_checks//\'/\'\'}"

if [ "${2-}" = --retry ]; then
  printf -v workflow_names_shell '%q' "$workflow_names"
  cat <<YAML
# GENERATED FILE — do not edit by hand.
# Regenerate with:
#   scripts/gen-privileged-merge-caller.sh $contract_sha --retry $workflow_names_shell $required_checks_shell > .github/workflows/ai-promotion-retry.yml
name: AI terminal promotion retry

on:
  workflow_run:
    workflows: $workflow_names
    types: [completed]

permissions:
  actions: write
  checks: read
  contents: read
  issues: read
  pull-requests: read

jobs:
  retry:
    uses: $RETRY_TARGET
    secrets: inherit
    with:
      merge_environment: merge-app
      required_checks: '$required_checks_yaml'
      privileged_lane: \${{ vars.CI_LANE_PRIVILEGED }}
      merge_app_client_id: \${{ vars.MERGE_APP_CLIENT_ID }}
YAML
  exit 0
fi

# An empty argument is the default case, not an error: `gen … "$LABELS"` with
# LABELS unset must produce the lane-routed caller rather than a diagnostic about
# a value the operator never meant to supply.
runner_labels="${3-}"

if [ -n "$runner_labels" ]; then
  command -v jq >/dev/null 2>&1 || { echo "jq is required to validate runner_labels" >&2; exit 2; }

  # Charset-restricted, not merely well-formed JSON. A label containing a quote is
  # valid JSON, passes a naive type check, then emits YAML GitHub cannot parse —
  # at exit 0, with no warning. A value like ${{ secrets.X }} is likewise valid
  # JSON and would expand a secret into a workflow input.
  jq -e 'type == "array" and length > 0
         and all(.[]; type == "string" and test("^[A-Za-z0-9._-]+$"))' \
    <<<"$runner_labels" >/dev/null 2>&1 \
    || { printf 'runner_labels must be a non-empty JSON array of [A-Za-z0-9._-] strings, got: %s\n' "$runner_labels" >&2; exit 2; }
fi

# Two lines, emitted only for an explicitly requested fleet: the `with:` entry
# and the regenerate command that reproduces it. The regenerate comment matters
# as much as the input — an operator copies that line, so leaving a label in it
# reintroduces the hardcoded fleet on the next regeneration.
regen_arg=""
labels_input=""
if [ -n "$runner_labels" ]; then
  regen_arg=" '$runner_labels'"
  labels_input="
      runner_labels: '${runner_labels}'"
fi

emit() {
  cat <<YAML
# GENERATED FILE — do not edit by hand.
# Regenerate with:
#   scripts/gen-privileged-merge-caller.sh $contract_sha $required_checks_shell$regen_arg > .github/workflows/ai-privileged-merge.yml
#
# Thin caller for the canonical privileged merge (verjson.github). All trust
# logic lives there; nothing here may re-implement it.
name: AI privileged merge

on:
  workflow_dispatch:
    inputs:
      pr_number:
        required: true
        type: string
      expected_head_sha:
        required: true
        type: string
      authorization_check_id:
        required: true
        type: string
      arm_run_id:
        required: true
        type: string
      arm_run_attempt:
        required: true
        type: string
      review_policy:
        required: true
        type: string
      source_run_id:
        required: false
        type: string

permissions:
  actions: write
  checks: read
  contents: read
  issues: read
  pull-requests: read

# Deliberately distinct from the canonical workflow's concurrency group.
concurrency:
  group: ai-native-automerge-call-\${{ inputs.pr_number }}-\${{ inputs.expected_head_sha }}
  cancel-in-progress: false

jobs:
  # The job key is CONTRACTUAL: the published check name is
  # "<caller job> / <callee job>" and both workflows exclude that shape.
  # Renaming it makes the gate wait on its own continuation.
  privileged_merge:
    uses: ${TARGET}
    secrets: inherit
    # The callee reads its caller-owned environment secret directly.
    with:
      merge_environment: merge-app
      pr_number: \${{ inputs.pr_number }}
      expected_head_sha: \${{ inputs.expected_head_sha }}
      authorization_check_id: \${{ inputs.authorization_check_id }}
      arm_run_id: \${{ inputs.arm_run_id }}
      arm_run_attempt: \${{ inputs.arm_run_attempt }}
      review_policy: \${{ inputs.review_policy }}
      source_run_id: \${{ inputs.source_run_id }}
      required_checks: '$required_checks_yaml'
      privileged_lane: \${{ vars.CI_LANE_PRIVILEGED }}${labels_input}
      merge_app_client_id: \${{ vars.MERGE_APP_CLIENT_ID }}
YAML
}

# Never hand an operator a file that does not parse. The premise of generating
# this at all is that they should not be able to receive a silent footgun.
out="$(emit)"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
  printf '%s\n' "$out" | python3 -c 'import sys, yaml; yaml.safe_load(sys.stdin)' 2>/dev/null \
    || { echo "internal error: generated caller is not valid YAML; refusing to emit" >&2; exit 3; }
fi
printf '%s\n' "$out"
