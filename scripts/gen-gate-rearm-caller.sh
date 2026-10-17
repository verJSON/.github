#!/usr/bin/env bash
# Generate a consumer's thin gate re-arm caller.
#
# Usage:
#   scripts/gen-gate-rearm-caller.sh <verjson-github-contract-sha> \
#     > .github/workflows/gate-rearm.yml
#
# The SHA is required because this pull_request_target workflow carries
# actions:write. A mutable ref would let a later upstream change alter privileged
# behavior in every adopter without a consumer-side review.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: gen-gate-rearm-caller.sh <40-character-contract-sha>" >&2
  exit 2
fi

contract_sha="$1"
[[ "$contract_sha" =~ ^[0123456789abcdef]{40}$ ]] || {
  echo "contract SHA must be exactly 40 lowercase hexadecimal characters" >&2
  exit 2
}

readonly TARGET="Verjson/.github/.github/workflows/gate-rearm.yml@$contract_sha"

emit() {
  cat <<YAML
# GENERATED FILE — do not edit by hand.
# Regenerate with:
#   scripts/gen-gate-rearm-caller.sh $contract_sha > .github/workflows/gate-rearm.yml
#
# Thin caller for the immutable gate re-arm bridge in Verjson/.github.
name: gate re-arm

on:
  workflow_call:
    inputs:
      ai_review_environment:
        description: Main-only environment containing the AI review App private key
        required: true
        type: string
  pull_request_target:
    types: [opened, reopened, synchronize]
permissions:
  contents: read

jobs:
  rearm:
    permissions:
      contents: read
      actions: write
      checks: write
      issues: write
      pull-requests: write
    uses: $TARGET
    secrets: inherit
    with:
      ai_review_environment: ai-review-app
YAML
}

out="$(emit)"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
  printf '%s\n' "$out" |
    python3 -c 'import sys, yaml; yaml.load(sys.stdin, Loader=yaml.BaseLoader)' 2>/dev/null ||
    { echo "internal error: generated caller is not valid YAML; refusing to emit" >&2; exit 3; }
fi
printf '%s\n' "$out"
