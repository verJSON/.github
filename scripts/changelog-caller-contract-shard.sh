#!/usr/bin/env bash
# Partition scripts/ci-gate/changelog-caller-contract.test.sh across actions-ci
# cells. A selected case runs the whole generated contract. Skipping a section
# of that contract is not a shard: run 37970631888 accepted the same mutations
# in every cell because the section that would reject them never ran. One shard
# per cell keeps the per-process fixture walk to four runs (ADR 0225).
CHANGELOG_CALLER_CONTRACT_SHARD_WIDTH=4
# Every run of the caller contract makes exactly this many case selections, in
# every shard; the caller fails if the count drifts, so a case added or lost
# cannot silently change which shard owns what. Update it with the case list.
# shellcheck disable=SC2034 # Read by the caller contract that sources this file.
CHANGELOG_CALLER_CONTRACT_CASES=268

changelog_caller_shard_name_ok() {
  case "${1:-all}" in
    all|1|2|3|4) return 0 ;;
    *) return 1 ;;
  esac
}

changelog_caller_case_selected() {
  caller_case_count=$((caller_case_count + 1))
  local shard="${CHANGELOG_CALLER_CONTRACT_SHARD:-all}"
  local width="$CHANGELOG_CALLER_CONTRACT_SHARD_WIDTH"
  local slot
  [ "$shard" = all ] && { caller_case_ran=$((caller_case_ran + 1)); return 0; }
  slot=$(( (caller_case_count - 1) % width + 1 ))
  if [ "$slot" = "$shard" ]; then
    caller_case_ran=$((caller_case_ran + 1))
    return 0
  fi
  return 1
}
