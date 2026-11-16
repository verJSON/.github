#!/usr/bin/env bash
# Generate a consumer's merge-admission caller and trigger (ADR 0260 C10 (iv)).
#
# Usage:
#   scripts/gen-merge-admission-caller.sh <40-character-contract-sha> '<workflows-json>' \
#     > .github/workflows/merge-admission-caller.yml
#   scripts/gen-merge-admission-caller.sh --trigger \
#     > .github/workflows/merge-admission-trigger.yml
#
# The SHA is required for the caller because it carries secrets:inherit into the
# producer reusable workflow. A mutable ref would let a later upstream change alter
# privileged behavior in every adopter without a consumer-side review.
#
# <workflows-json> is a non-empty JSON array of workflow names whose completions
# start the producer. It must include "Merge admission trigger". Each consumer
# lists every workflow that reports a check on a pull-request head there, matching
# this repository's hand-authored caller contract (ADR 0260, action 5).
set -euo pipefail

emit_trigger() {
  cat <<'YAML'
# GENERATED FILE — do not edit by hand.
# Regenerate with:
#   scripts/gen-merge-admission-caller.sh --trigger > .github/workflows/merge-admission-trigger.yml
#
# Carries the pull-request and review events of ADR 0260 action 5 to the
# merge-admission producer, which starts on this run's completion (`workflow_run`),
# from the default branch, outside the head's inventories. This run belongs to the
# head, so it holds no credential, reads nothing, and completes in seconds.
name: Merge admission trigger

on:
  pull_request_target:
    types: [opened, synchronize, reopened, ready_for_review, edited, labeled, unlabeled]
  pull_request_review:
    types: [submitted, edited, dismissed]

permissions: {}

jobs:
  signal:
    runs-on: ${{ (github.event_name == 'pull_request' || github.event_name == 'pull_request_review' || github.event_name == 'pull_request_review_comment' || github.event_name == 'merge_group' || github.event_name == 'pull_request_target') && github.event.repository.private != true && 'ubuntu-24.04' || (fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]')) }}
    timeout-minutes: 2
    permissions: {}
    steps:
      - name: Hand the event to the merge-admission producer
        run: echo "merge-admission re-evaluates this head when this run completes"
YAML
}

if [ "${1-}" = "--trigger" ]; then
  [ "$#" -eq 1 ] || {
    echo "usage: gen-merge-admission-caller.sh --trigger" >&2
    exit 2
  }
  emit_trigger
  exit 0
fi

if [ "$#" -ne 2 ]; then
  echo "usage: gen-merge-admission-caller.sh <40-character-contract-sha> '<workflows-json>'" >&2
  echo "       gen-merge-admission-caller.sh --trigger" >&2
  exit 2
fi

contract_sha="$1"
[[ "$contract_sha" =~ ^[0123456789abcdef]{40}$ ]] || {
  echo "contract SHA must be exactly 40 lowercase hexadecimal characters" >&2
  exit 2
}

command -v jq >/dev/null 2>&1 || {
  echo "jq is required to validate the completion-workflow list" >&2
  exit 2
}

workflows="$(jq -ce '
  if type == "array" and length > 0 and
     (unique | length) == length and
     all(.[];
       type == "string" and length > 0 and
       all(explode[];
         . >= 32 and (. < 127 or . > 159) and . != 8232 and . != 8233) and
       (contains("${{") | not)) and
     any(.[]; . == "Merge admission trigger")
  then . else error("unsafe workflows") end
' <<<"$2" 2>/dev/null)" || {
  echo "workflows must be a non-empty unique JSON string array that includes \"Merge admission trigger\"" >&2
  exit 2
}

readonly TARGET="Verjson/.github/.github/workflows/merge-admission.yml@$contract_sha"
printf -v workflows_shell '%q' "$workflows"

emit_caller() {
  cat <<YAML
# GENERATED FILE — do not edit by hand.
# Regenerate with:
#   scripts/gen-merge-admission-caller.sh $contract_sha $workflows_shell > .github/workflows/merge-admission-caller.yml
#
# Thin caller for the immutable merge-admission producer in Verjson/.github
# (ADR 0260 C10 (iv)). Completions of the listed workflows, the 15-minute sweep,
# and a manual dispatch start the producer on the default-branch commit.
name: Merge admission

on:
  workflow_run:
    workflows: $workflows
    types: [completed]
  schedule:
    - cron: '*/15 * * * *'
  workflow_dispatch: {}

concurrency:
  group: merge-admission-caller-\${{ contains(fromJSON('["pull_request","pull_request_target","pull_request_review"]'), github.event.workflow_run.event) && github.event.workflow_run.head_sha || 'sweep' }}
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  admit:
    permissions:
      actions: read
      checks: read
      contents: read
      pull-requests: read
      statuses: read
    uses: $TARGET
    secrets: inherit
    with:
      ai_review_environment: ai-review-app
YAML
}

out="$(emit_caller)"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
  printf '%s\n' "$out" |
    python3 -c 'import sys, yaml; yaml.load(sys.stdin, Loader=yaml.BaseLoader)' 2>/dev/null ||
    { echo "internal error: generated caller is not valid YAML; refusing to emit" >&2; exit 3; }
fi
printf '%s\n' "$out"
