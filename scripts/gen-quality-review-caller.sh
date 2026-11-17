#!/usr/bin/env bash
# Generate a consumer's thin quality-review caller (#1820 / ADR 0270).
#
# Usage:
#   scripts/gen-quality-review-caller.sh <40-hex-contract-sha> > .github/workflows/quality-review.yml
#
# The caller pins an immutable Verjson/.github SHA and never checks out PR head
# code in the reusable workflow. Advisory only — not a required check.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: gen-quality-review-caller.sh <40-character-contract-sha>" >&2
  exit 2
fi

contract_sha="$1"
[[ "$contract_sha" =~ ^[0123456789abcdef]{40}$ ]] || {
  echo "contract SHA must be exactly 40 lowercase hexadecimal characters" >&2
  exit 2
}

cat <<YAML
# GENERATED FILE — do not edit by hand.
# Regenerate with:
#   scripts/gen-quality-review-caller.sh ${contract_sha} > .github/workflows/quality-review.yml
#
# Advisory quality review (#1820 / ADR 0270). Never a required check.
name: quality-review

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review, labeled, unlabeled]

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    uses: Verjson/.github/.github/workflows/quality-review.yml@${contract_sha}
    secrets: inherit
    with:
      contract_ref: ${contract_sha}
YAML
