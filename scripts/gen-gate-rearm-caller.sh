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

# The local arm guard (F-1R, ADR 0255 amendment 2026-11-06): the exact bytes of
# scripts/local-arm-guard.sh, embedded rather than read at generation time so
# this generator stays self-contained wherever an audit materializes it alone.
# scripts/ci-gate/gate-rearm-caller-contract.test.sh fails when the two differ.
guard_script="$(cat <<'LOCAL_ARM_GUARD'
#!/usr/bin/env bash
readonly ORG_ARM_REPOSITORY_ID=1269388380
readonly ORG_ARM_PATH=.github/workflows/gate-rearm.yml

decide() { # local_arm reason
  printf 'local_arm=%s\n' "$1" >>"$GITHUB_OUTPUT"
  printf 'local arm guard: local_arm=%s: %s\n' "$1" "${2//[[:cntrl:]]/ }"
  exit 0
}

case "$EVENT_NAME/$EVENT_ACTION" in
  pull_request_target/opened|pull_request_target/reopened|pull_request_target/synchronize) ;;
  *) decide true "$EVENT_NAME/$EVENT_ACTION is not a head transition the organization arm receives" ;;
esac

base_ref_path="$(jq -rn --arg r "$BASE_REF" '$r | @uri')"
rules_path="repos/$REPOSITORY/rules/branches/$base_ref_path"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
gh api --paginate "$rules_path?per_page=100" </dev/null >"$scratch/rules" 2>"$scratch/error" \
  || decide true "could not read $rules_path: $(head -c 500 "$scratch/error")"
rules="$(jq -s 'add' <"$scratch/rules" 2>/dev/null)" \
  || decide true "the rules response is not JSON"
verdict="$(jq -r --arg path "$ORG_ARM_PATH" --argjson id "$ORG_ARM_REPOSITORY_ID" '
  def readable:
    (.ruleset_id | type) == "number"
    and (.parameters | type) == "object"
    and (.parameters.workflows | type) == "array" and (.parameters.workflows | length) > 0
    and all(.parameters.workflows[]; type == "object" and (.path | type) == "string" and .path != ""
                                     and (.repository_id | type) == "number");
  if type != "array" then "true\tthe rules response is not a JSON array"
  elif any(.[]; type != "object" or (.type == "workflows" and (readable | not))) then
    "true\tthe rules response carries a rule the guard cannot read"
  else [ .[] | select(.type == "workflows") | .parameters.workflows[] | select(.path == $path) ] as $at_path
  | if ($at_path | length) == 0 then "true\tno workflows rule names \($path)"
    elif any($at_path[]; .repository_id != $id) then
      "true\tambiguous: a workflows rule names \($path) from repository \([ $at_path[] | select(.repository_id != $id) | .repository_id ] | map(tostring) | join(", ")), not the organization arm (\($id))"
    else "false\tthe organization arm \($path) from repository \($id) is required on this base ref" end end' <<<"$rules")"
decide "${verdict%%$'\t'*}" "${verdict#*$'\t'}"
LOCAL_ARM_GUARD
)"

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

# No concurrency here or on either job: the arm's group is declared by the reusable
# workflow and engages only when rearm starts, so a skipped rearm displaces nothing.
jobs:
  # Yields the local arm only where the base ref provably requires the organization's
  # arm, whose own run arms the head; any uncertainty keeps the local arm. A liveness
  # decision, never admission evidence: Gate W alone checks the required run (ADR 0255).
  guard:
    runs-on: \${{ fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') }}
    timeout-minutes: 5
    permissions:
      contents: read
    outputs:
      local_arm: \${{ steps.decide.outputs.local_arm == 'false' && 'false' || 'true' }}
    steps:
      - id: decide
        continue-on-error: true
        shell: bash
        env:
          GH_TOKEN: \${{ github.token }}
          EVENT_NAME: \${{ github.event_name }}
          EVENT_ACTION: \${{ github.event.action }}
          REPOSITORY: \${{ github.repository }}
          BASE_REF: \${{ github.event.pull_request.base.ref }}
        run: |
YAML
  printf '%s\n' "$guard_script" | sed '/./s/^/          /'
  cat <<YAML
  rearm:
    needs: guard
    if: \${{ needs.guard.outputs.local_arm == 'true' }}
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
