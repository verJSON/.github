#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"

legacy_prefix="VERJSON_"
contract_paths=(.github scripts docs)

scan_pattern() {
  local pattern=$1
  shift
  local output status
  output="$(mktemp)"
  if grep -rInE "$pattern" "$@" >"$output"; then
    status=0
  else
    status=$?
  fi
  if [ "$status" -gt 1 ]; then
    rm -f "$output"
    return "$status"
  fi
  grep -v '^docs/decisions/' "$output" || true
  rm -f "$output"
}

find_legacy_context_refs() {
  scan_pattern "(vars|secrets)\\.${legacy_prefix}[A-Z0-9_]+" "$@" || return $?
  scan_pattern "(vars|secrets)\\\\\\.${legacy_prefix}[A-Z0-9_]+" "$@" || return $?
  scan_pattern "(vars|secrets)\\[[[:space:]]*['\"]${legacy_prefix}[A-Z0-9_]+['\"][[:space:]]*\\]" "$@" || return $?
}

# The snapshot is byte-verified against its immutable upstream commit. The
# active Verjson-only candidate gate keeps its matching source flag: ADR 0240
# treats that canonical repository's feature switch as contract identity, not
# installation policy. The adapter contract test binds its scope and callee.
legacy_candidate_context="vars.${legacy_prefix}CANDIDATE_WORKERS_ENABLED"
legacy_candidate_gate="    if: \${{ inputs.profile == 'approved-candidate-v1' && ${legacy_candidate_context} == 'true' && github.repository == 'Verjson/verjson-ci' && github.ref == 'refs/heads/main' && github.event.repository.private == true }}"
legacy_candidate_gate_match="$(grep -nFx "$legacy_candidate_gate" .github/workflows/verjson-ci.yml)"
if [ "$(grep -Fxc "$legacy_candidate_gate" .github/workflows/verjson-ci.yml)" -ne 1 ]; then
  printf 'candidate gate exception must match exactly once in the canonical adapter\n' >&2
  exit 1
fi
legacy_candidate_record=".github/workflows/verjson-ci.yml:${legacy_candidate_gate_match%%:*}:${legacy_candidate_gate}"
legacy_candidate_rejection_line="      ${legacy_candidate_context} != 'true' ||"
legacy_candidate_rejection_match="$(grep -nFx "$legacy_candidate_rejection_line" .github/workflows/verjson-ci.yml)"
if [ "$(grep -Fxc "$legacy_candidate_rejection_line" .github/workflows/verjson-ci.yml)" -ne 1 ]; then
  printf 'candidate rejection exception must match exactly once in the canonical adapter\n' >&2
  exit 1
fi
legacy_candidate_rejection_record=".github/workflows/verjson-ci.yml:${legacy_candidate_rejection_match%%:*}:${legacy_candidate_rejection_line}"
legacy_candidate_contract_assertion="    assert \"${legacy_candidate_context}\" in candidate_if"
legacy_candidate_contract_match="$(grep -nFx "$legacy_candidate_contract_assertion" scripts/ci-gate/verjson-ci-canonical-contract.test.sh)"
if [ "$(grep -Fxc "$legacy_candidate_contract_assertion" scripts/ci-gate/verjson-ci-canonical-contract.test.sh)" -ne 1 ]; then
  printf 'candidate contract exception must match exactly once in the canonical contract test\n' >&2
  exit 1
fi
legacy_candidate_contract_record="scripts/ci-gate/verjson-ci-canonical-contract.test.sh:${legacy_candidate_contract_match%%:*}:${legacy_candidate_contract_assertion}"
legacy_candidate_rejection_assertion="        \"${legacy_candidate_context} != 'true' || \""
legacy_candidate_rejection_assertion_match="$(grep -nFx "$legacy_candidate_rejection_assertion" scripts/ci-gate/verjson-ci-canonical-contract.test.sh)"
if [ "$(grep -Fxc "$legacy_candidate_rejection_assertion" scripts/ci-gate/verjson-ci-canonical-contract.test.sh)" -ne 1 ]; then
  printf 'candidate rejection assertion must match exactly once in the canonical contract test\n' >&2
  exit 1
fi
legacy_candidate_rejection_assertion_record="scripts/ci-gate/verjson-ci-canonical-contract.test.sh:${legacy_candidate_rejection_assertion_match%%:*}:${legacy_candidate_rejection_assertion}"
legacy_context_refs="$(find_legacy_context_refs "${contract_paths[@]}" \
  | grep -v '^scripts/ci-gate/fixtures/verjson-ci-reusable-c9084daca387849c09f1d97bccf8ac311ff11615\.yml:' \
  | grep -vFx "$legacy_candidate_record" \
  | grep -vFx "$legacy_candidate_rejection_record" \
  | grep -vFx "$legacy_candidate_contract_record" \
  | grep -vFx "$legacy_candidate_rejection_assertion_record" || true)"
candidate_gate_refs="$(find_legacy_context_refs .github/workflows/verjson-ci.yml \
  | grep -F "$legacy_candidate_context == 'true'" || true)"
[ "$(printf '%s\n' "$candidate_gate_refs" | sed '/^$/d' | wc -l)" -eq 1 ] || {
  echo 'the canonical candidate contract must contain exactly one scoped Verjson feature gate' >&2
  exit 1
}
[ -z "$legacy_context_refs" ] || {
  printf 'organization-branded GitHub configuration remains:\n%s\n' "$legacy_context_refs" >&2
  exit 1
}

fixture_dir="$(mktemp -d)"
fixture="$fixture_dir/bracket-forms.yml"
spoof_fixture="$fixture_dir/unrelated-comment.yml"
printf 'vars.%sUNRELATED_POLICY: true # %s\n' "$legacy_prefix" "$legacy_candidate_context" > "$spoof_fixture"
spoof_refs="$(find_legacy_context_refs "$spoof_fixture")"
if [ -z "$spoof_refs" ]; then
  printf 'candidate flag comment masked an unrelated legacy variable reference\n' >&2
  exit 1
fi
trap 'rm -rf "$fixture_dir"' EXIT
printf '%s\n' \
  "vars['${legacy_prefix}LANE_TRUSTED']" \
  "vars[\"${legacy_prefix}LANE_TRUSTED\"]" \
  "secrets['${legacy_prefix}RELEASE_TOKEN']" \
  "secrets[\"${legacy_prefix}RELEASE_TOKEN\"]" >"$fixture"
[ "$(find_legacy_context_refs "$fixture" | wc -l)" -eq 4 ] || {
  echo 'bracket-form neutrality mutations were not all rejected' >&2
  exit 1
}
mkdir -p "$fixture_dir/scripts/decisions"
printf '%s\n' "vars.${legacy_prefix}LANE_TRUSTED" > "$fixture_dir/scripts/decisions/active.yml"
[ "$(find_legacy_context_refs "$fixture_dir/scripts" | wc -l)" -eq 1 ] || {
  echo 'an active nested decisions directory bypassed the neutrality guard' >&2
  exit 1
}
if find_legacy_context_refs "$fixture_dir/missing" >/dev/null 2>&1; then
  echo 'a missing scan root was reported as clean' >&2
  exit 1
fi

for legacy_secret in "${legacy_prefix}RUNNER_DEPLOY_TOKEN" "${legacy_prefix}RELEASE_TOKEN"; do
  refs="$(
    scan_pattern "$legacy_secret" "${contract_paths[@]}"
  )"
  [ -z "$refs" ] || {
    printf 'organization-branded secret contract remains:\n%s\n' "$refs" >&2
    exit 1
  }
done

grep -qF 'vars.CI_LANE_PRIVILEGED' scripts/gen-privileged-merge-caller.sh
grep -qF 'vars.CI_RUNNER_DEFAULT' scripts/gen-changelog-caller.sh
grep -qF 'release_environment: release-app' scripts/gen-container-release.sh
! grep -qF 'secrets.RELEASE_APP_PRIVATE_KEY' scripts/gen-container-release.sh
! grep -qF 'secrets.RUNNER_DEPLOY_TOKEN' .github/workflows/container-deployment.yml
grep -qF 'secrets.DIGITALOCEAN_RUNNER_FLEET_TOKEN' .github/workflows/container-deployment.yml
grep -qF 'secrets.GH_RUNNER_REGISTRATION_APP_PRIVATE_KEY' .github/workflows/container-deployment.yml

printf 'organization-neutral canonical CI variable contract: PASS\n'
