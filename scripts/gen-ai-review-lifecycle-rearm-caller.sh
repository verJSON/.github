#!/usr/bin/env bash
set -euo pipefail

[ "$#" -eq 1 ] || { echo "usage: gen-ai-review-lifecycle-rearm-caller.sh <40-character-contract-sha|--local>" >&2; exit 2; }
contract_ref="$1"
if [ "$contract_ref" = --local ]; then
  workflow_ref="./.github/workflows/gate-rearm.yml"
else
  workflow_ref="Verjson/.github/.github/workflows/gate-rearm.yml@$contract_ref"
fi

cat <<YAML
# GENERATED FILE — do not edit by hand.
# Regenerate with:
# scripts/gen-ai-review-lifecycle-rearm-caller.sh $contract_ref > .github/workflows/ai-review-lifecycle-rearm.yml
name: AI review lifecycle re-arm

on:
  pull_request_target:
    types: [ready_for_review, converted_to_draft, edited, unlabeled]

permissions:
  actions: read
  contents: read

jobs:
  rearm:
    permissions:
      actions: write
      checks: write
      contents: read
      issues: write
      pull-requests: write
    uses: $workflow_ref
    secrets: inherit
    with:
      ai_review_environment: ai-review-app
YAML
