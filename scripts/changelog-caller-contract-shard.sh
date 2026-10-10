#!/usr/bin/env bash
# Partition scripts/ci-gate/changelog-caller-contract.test.sh across actions-ci
# cells. A selected mutation runs the complete generated contract; never split
# generated assertions by section because one mutation may be rejected later.
CHANGELOG_CALLER_CONTRACT_SHARD_WIDTH=233

changelog_caller_shard_name_ok() {
  local shard="${1:-all}"
  [ "$shard" = all ] && return 0
  [[ "$shard" =~ ^[1-9][0-9]{0,2}$ ]] || return 1
  [ "$shard" -le "$CHANGELOG_CALLER_CONTRACT_SHARD_WIDTH" ]
}

changelog_caller_case_selected() {
  caller_case_count=$((caller_case_count + 1))
  local shard="${CHANGELOG_CALLER_CONTRACT_SHARD:-all}"
  local width="$CHANGELOG_CALLER_CONTRACT_SHARD_WIDTH"
  local regular_width=$((width - 3))
  local slot
  local adopter_cases_seen="${caller_adopter_cases_seen:-0}"
  [ "$shard" = all ] && { caller_case_ran=$((caller_case_ran + 1)); return 0; }
  if [ "$1" = adopter ]; then
    adopter_cases_seen=$((adopter_cases_seen + 1))
    caller_adopter_cases_seen="$adopter_cases_seen"
    slot=$((width - 3 + adopter_cases_seen))
    caller_case_reserved_count=$((caller_case_reserved_count + 1))
  elif [ "$1" = snapshot-capture-path-override ]; then
    slot="$width"
    caller_case_reserved_count=$((caller_case_reserved_count + 1))
  else
    slot=$(( (caller_case_count - caller_case_reserved_count - 1) % regular_width + 1 ))
  fi
  if [ "$slot" = "$shard" ]; then
    caller_case_ran=$((caller_case_ran + 1))
    return 0
  fi
  return 1
}
