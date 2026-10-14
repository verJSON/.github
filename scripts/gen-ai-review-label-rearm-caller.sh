#!/usr/bin/env bash
set -euo pipefail

[ "$#" -eq 1 ] || { echo "usage: gen-ai-review-label-rearm-caller.sh <40-character-contract-sha|--local>" >&2; exit 2; }
contract_ref="$1"
if [ "$contract_ref" = --local ]; then
  workflow_ref="./.github/workflows/gate-rearm.yml"
else
  [[ "$contract_ref" =~ ^[0-9a-f]{40}$ ]] || { echo "contract SHA must exactly 40 lowercase hexadecimal characters" >&2; exit 2; }
  workflow_ref="verjson.github/.github/workflows/gate-rearm.yml@$contract_ref"
fi

cat <<YAML
# GENERATED FILE — do not edit by hand.
# Regenerate with:
# scripts/gen-ai-review-label-rearm-caller.sh $contract_ref > .github/workflows/ai-review-label-rearm.yml
name: AI review label re-arm

on:
  pull_request_target:
    types: [labeled]

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
