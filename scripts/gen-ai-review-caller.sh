#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ] || [[ ! "$1" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: gen-ai-review-caller.sh <40-character-contract-sha>" >&2
  exit 2
fi

contract_sha="$1"
cat <<YAML
# GENERATED FILE — do not edit by hand.
# Regenerate with:
#   scripts/gen-ai-review-caller.sh $contract_sha > .github/workflows/ai-review-merge.yml
name: AI review
run-name: AI review authorization \${{ inputs.authorization_check_id }} from arm \${{ inputs.arm_run_id }}.\${{ inputs.arm_run_attempt }}

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
      explicit_rereview:
        required: false
        type: boolean
        default: false
      review_policy:
        required: true
        type: string

permissions:
  actions: write
  checks: write
  contents: read
  issues: write
  pull-requests: write
  statuses: read

jobs:
  review:
    uses: verjson.github/.github/workflows/ai-review-merge.yml@$contract_sha
    secrets: inherit
    with:
      ai_review_environment: ai-review-app
      contract_ref: $contract_sha
      pr_number: \${{ inputs.pr_number }}
      expected_head_sha: \${{ inputs.expected_head_sha }}
      authorization_check_id: \${{ inputs.authorization_check_id }}
      arm_run_id: \${{ inputs.arm_run_id }}
      arm_run_attempt: \${{ inputs.arm_run_attempt }}
      explicit_rereview: \${{ inputs.explicit_rereview }}
      review_policy: \${{ inputs.review_policy }}
YAML
