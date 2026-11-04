#!/usr/bin/env bash
set -Eeuo pipefail
# `set -e` ends the run on a failed bare assertion without saying which one, so
# name it. `-E` carries the trap into functions; a subshell keeps its own
# errexit, and its failure is reported where the parent sees it.
report_failed_assertion() {
  local status=$?
  if [ "$BASH_SUBSHELL" -eq 0 ]; then
    printf 'FAIL - %s line %s: %s (exit %s)\n' "${0##*/}" "$1" "$2" "$status" >&2
    exit "$status"
  fi
}
trap 'report_failed_assertion "$LINENO" "$BASH_COMMAND"' ERR

root="$(cd "$(dirname "$0")/.." && pwd)"
generator="$root/scripts/gen-changelog-caller.sh"
ref="$(git -C "$root" rev-parse HEAD)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# `!` exempts a command from `set -e`, so a bare `! grep` can never fail the
# run; and a grep that cannot read its input exits 2, which a negation would
# take for absence.
assert_absent() {
  local search_status=0
  grep "$@" || search_status=$?
  [ "$search_status" -eq 1 ] && return 0
  if [ "$search_status" -eq 0 ]; then
    printf 'FAIL - %s: found what must be absent: grep %s\n' "${0##*/}" "$*" >&2
  else
    printf 'FAIL - %s: could not search (grep exit %s): grep %s\n' "${0##*/}" "$search_status" "$*" >&2
  fi
  exit 1
}

"$generator" pr-gate "$ref" >"$tmp/changelog-contract.yml"

grep -q '^  changelog-contract:$' "$tmp/changelog-contract.yml"
# #935: pull_request checks out PR-authored code, so the default must be an
# ephemeral hosted runner, never a persistent self-hosted one.
grep -q 'runs-on: ubuntu-24.04$' "$tmp/changelog-contract.yml"
assert_absent -q 'runs-on:.*self-hosted' "$tmp/changelog-contract.yml"
for runner in ubuntu-24.04 ubuntu-26.04; do
  "$generator" pr-gate "$ref" --untrusted-runner "$runner" >"$tmp/versioned-runner.yml"
  grep -F -q "runs-on: [$runner]" "$tmp/versioned-runner.yml"
done
echo "ok - --untrusted-runner accepts versioned Ubuntu labels"
grep -q 'VERJSON_CHANGELOG_TOOL_CACHE=\$RUNNER_TEMP/verjson-changelog-tools' \
  "$tmp/changelog-contract.yml"
grep -q 'bash scripts/changelog-contract.test.sh' "$tmp/changelog-contract.yml"
assert_absent -q '/opt/verjson/changelog-tools' "$tmp/changelog-contract.yml"

cache_line="$(grep -n 'VERJSON_CHANGELOG_TOOL_CACHE=' "$tmp/changelog-contract.yml" | cut -d: -f1 || true)"
test_line="$(grep -n 'bash scripts/changelog-contract.test.sh' "$tmp/changelog-contract.yml" | cut -d: -f1 || true)"
[ -n "$cache_line" ] && [ -n "$test_line" ] && [ "$cache_line" -lt "$test_line" ] || {
  echo "FAIL - generated PR gate does not set the changelog cache (line '${cache_line}') before validation (line '${test_line}')" >&2
  exit 1
}

echo "ok - generated PR gate uses a job-writable changelog cache before validation"
echo "ok - generated PR gate defaults to an ephemeral hosted runner (#935)"

# #959. Adopters cannot hand-edit generated output, so a step this file emits
# without a `name:` sits in the blind spot of every downstream policy scanner
# that keys sections off the `- name:` marker — the reporting adopter's
# credential-hygiene check reported "valid" while never reading this job's
# checkout at all. Naming every emitted step costs nothing and keeps the
# generated gate visible to those scans.
python3 - "$tmp/changelog-contract.yml" <<'PY'
import sys

import yaml

workflow = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
unnamed = [
    f"{job_name}[{index}]"
    for job_name, job in (workflow.get("jobs") or {}).items()
    for index, step in enumerate(job.get("steps") or [])
    if not step.get("name")
]
if unnamed:
    raise SystemExit("FAIL - generated PR gate emits un-named steps: " + ", ".join(unnamed))
PY
echo "ok - every generated PR gate step carries a name downstream scanners can key on (#959)"

"$generator" pr-gate "$ref" --untrusted-runner self-hosted,untrusted-pr,ephemeral \
  >"$tmp/changelog-contract-override.yml"
grep -q 'runs-on: \[self-hosted, untrusted-pr, ephemeral\]' \
  "$tmp/changelog-contract-override.yml"
echo "ok - --untrusted-runner lets an adopter opt into an isolated self-hosted lane"

for bad in 'self-hosted; rm -rf /' 'ubuntu-26.04/evil' '' 'self-hosted,' ',ephemeral'; do
  if "$generator" pr-gate "$ref" --untrusted-runner "$bad" >/dev/null 2>&1; then
    echo "FAIL - accepted invalid --untrusted-runner value: '$bad'" >&2
    exit 1
  fi
done
if "$generator" generated-artifacts "$ref" --untrusted-runner self-hosted >/dev/null 2>&1; then
  echo "FAIL - --untrusted-runner accepted outside pr-gate mode" >&2
  exit 1
fi
echo "ok - --untrusted-runner rejects malformed values and non-pr-gate modes"
