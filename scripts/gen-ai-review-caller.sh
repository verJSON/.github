#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 1 ] && [ "$1" = "--local" ]; then
  caller_uses="./.github/workflows/ai-review-merge.yml"
  caller_path=".github/workflows/ai-review-merge-dispatch.yml"
  caller_name="AI review + auto-merge"
  contract_ref='${{ github.sha }}'
  environment_expr='${{ inputs.ai_review_environment }}'
  environment_input=$'      ai_review_environment:\n        required: false\n        type: choice\n        options:\n          - ai-review-app\n        default: ai-review-app\n'
  regenerate="scripts/gen-ai-review-caller.sh --local > $caller_path"
elif [ "$#" -eq 1 ] && [[ "$1" =~ ^[0-9a-f]{40}$ ]]; then
  contract_sha="$1"
  caller_uses="Verjson/.github/.github/workflows/ai-review-merge.yml@$contract_sha"
  caller_path=".github/workflows/ai-review-merge.yml"
  caller_name="AI review"
  contract_ref="$contract_sha"
  environment_expr="ai-review-app"
  environment_input=""
  regenerate="scripts/gen-ai-review-caller.sh $contract_sha > $caller_path"
else
  echo "usage: gen-ai-review-caller.sh <40-character-contract-sha> | --local" >&2
  exit 2
fi

cat <<YAML
# GENERATED FILE — do not edit by hand.
# Regenerate with:
# $regenerate
name: $caller_name
run-name: AI review authorization \${{ inputs.authorization_check_id }} from arm \${{ inputs.arm_run_id }}.\${{ inputs.arm_run_attempt }}
on:
  workflow_dispatch:
    inputs:
$environment_input      pr_number:
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
      explicit_rereview:
        required: false
        type: boolean
        default: false
      review_policy:
        required: true
        type: string
      allow_absent_checks:
        required: false
        type: boolean
        default: false

concurrency:
  group: ai-review-\${{ github.workflow }}-\${{ inputs.pr_number }}
  cancel-in-progress: true

permissions:
  actions: write
  checks: write
  contents: read
  issues: write
  pull-requests: write
  statuses: read

jobs:
  review:
    uses: $caller_uses
    secrets: inherit
    with:
      ai_review_environment: $environment_expr
      contract_ref: $contract_ref
      pr_number: \${{ inputs.pr_number }}
      expected_head_sha: \${{ inputs.expected_head_sha }}
      authorization_check_id: \${{ inputs.authorization_check_id }}
      arm_run_id: \${{ inputs.arm_run_id }}
      arm_run_attempt: \${{ inputs.arm_run_attempt }}
      explicit_rereview: \${{ inputs.explicit_rereview }}
      review_policy: \${{ inputs.review_policy }}
      allow_absent_checks: \${{ inputs.allow_absent_checks }}
YAML
