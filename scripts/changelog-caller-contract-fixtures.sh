#!/usr/bin/env bash
# Fixture scheduling for scripts/ci-gate/changelog-caller-contract.test.sh.
#
# A case fixture is built only after that case is selected. Shared trees are
# built on first selected use. Out-of-case reads name the selection that owns
# them so skipping a build cannot weaken an assertion (#1978). Selection order
# stays the sequence of changelog_caller_case_selected calls: caller_case_prepare
# consumes the selection, and the matching caller_case_take does not consume it
# again.
# shellcheck disable=SC2034,SC2154
# SC2154: caller_case_count is assigned by changelog_caller_case_selected in the
# sourced shard helper before these helpers run.

declare -A caller_case_selected_ordinal
caller_case_holding=
caller_case_held_run=0
caller_case_last_ordinal=
caller_fixture_materializations=0

caller_case_prepare() {
  # caller_case_prepare <case-id> [ordinal-variable]
  local label="$1" ordinal_var="${2:-}"
  if ! changelog_caller_case_selected "$label"; then
    caller_assertions_suspended=1
    caller_case_held_run=0
  else
    caller_assertions_suspended=0
    caller_case_held_run=1
    caller_case_selected_ordinal[$caller_case_count]=1
  fi
  caller_case_holding="$label"
  caller_case_last_ordinal=$caller_case_count
  if [ -n "$ordinal_var" ]; then
    printf -v "$ordinal_var" '%s' "$caller_case_count"
  fi
  [ "$caller_case_held_run" = 1 ]
}

caller_case_take() {
  # caller_case_take <case-id> [announce]
  # Returns 0 when this case should run. A prepared case is consumed exactly
  # once; any other call selects, in the same order as before preparation.
  local case_id="$1" announce="${2:-}"
  if [ -n "${caller_case_holding:-}" ]; then
    if [ "$caller_case_holding" != "$case_id" ]; then
      printf 'FAIL - fixture prepared for %s but the next case is %s\n' \
        "$caller_case_holding" "$case_id" >&2
      exit 1
    fi
    caller_case_holding=
    if [ "${caller_case_held_run:-0}" != 1 ]; then
      return 1
    fi
    [ "$announce" = announce ] && printf 'running caller contract case: %s\n' "$case_id" >&2
    return 0
  fi
  if ! changelog_caller_case_selected "$case_id"; then
    caller_assertions_suspended=1
    caller_case_last_ordinal=$caller_case_count
    return 1
  fi
  caller_assertions_suspended=0
  caller_case_selected_ordinal[$caller_case_count]=1
  caller_case_last_ordinal=$caller_case_count
  [ "$announce" = announce ] && printf 'running caller contract case: %s\n' "$case_id" >&2
  return 0
}

# Run <body> only when the selection recorded in <ordinal-variable> ran.
# Suspension outside the body is restored: a tied read must not silence or
# revive the following case.
fixture_owned_here() {
  local ordinal_var="$1" body ordinal saved
  body="$(cat)"
  ordinal="${!ordinal_var:-}"
  if [ -z "$ordinal" ] || [ "${caller_case_selected_ordinal[$ordinal]:-}" != 1 ]; then
    return 0
  fi
  saved="${caller_assertions_suspended:-0}"
  caller_assertions_suspended=0
  eval "$body"
  caller_assertions_suspended="$saved"
}

note_fixture_materialization() {
  caller_fixture_materializations=$((caller_fixture_materializations + 1))
}
