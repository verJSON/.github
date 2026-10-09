#!/usr/bin/env bash
# The caller contract is partitioned by case. The generated suite has no
# section switch: a shard that owns a mutation runs every assertion.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
shard="$repo_root/scripts/changelog-caller-contract-shard.sh"
caller="$here/changelog-caller-contract.test.sh"
gen="$repo_root/scripts/gen-changelog-caller.sh"
manifest="$repo_root/scripts/actions-ci-groups.tsv"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

# shellcheck source=scripts/changelog-caller-contract-shard.sh
source "$repo_root/scripts/changelog-caller-contract-shard.sh"

if changelog_caller_shard_name_ok all \
  && changelog_caller_shard_name_ok 1 \
  && changelog_caller_shard_name_ok 4 \
  && ! changelog_caller_shard_name_ok 0 \
  && ! changelog_caller_shard_name_ok 5 \
  && ! changelog_caller_shard_name_ok changelog; then
  pass "caller-contract shard names are all and 1..4"
else
  fail "caller-contract shard names accepted an unknown value or rejected a valid one"
fi

if grep -q 'contract_section_selected\|CHANGELOG_CONTRACT_SECTIONS' "$caller" "$gen"; then
  fail "the caller contract or its generator can still skip a generated section"
else
  pass "a caller-contract case cannot skip a generated section"
fi

python3 - "$caller" "$shard" "$manifest" <<'PY' || fails=$((fails + 1))
import re
import subprocess
import sys

caller, shard, manifest = sys.argv[1:]
text = open(caller, encoding="utf-8").read()
expected_calls = 215
calls = len(re.findall(r"^schedule_adopter_script ", text, re.M))
calls += len(re.findall(r"^[ \t]*expect_rejection ", text, re.M))
calls += len(re.findall(r"^expect_release_mode_rejection ", text, re.M))
calls += len(re.findall(r"^[ \t]*expect_unestablished_pin ", text, re.M))
calls += len(re.findall(r"^(?:if )?run_adopter ", text, re.M))
if calls != expected_calls:
    raise SystemExit(
        f"parser saw {calls} caller-contract cases; expected {expected_calls}; "
        "review and update the case inventory when cases change"
    )

width = 4
owners = {index: [] for index in range(1, calls + 1)}
for shard_id in range(1, width + 1):
    script = f'''
source "$SHARD"
caller_case_count=0
caller_case_ran=0
CHANGELOG_CALLER_CONTRACT_SHARD={shard_id}
selected=""
for _ in $(seq 1 {calls}); do
  if changelog_caller_case_selected ignored; then
    selected="$selected $caller_case_count"
  fi
done
printf '%s\\n' "$selected"
'''
    probe = subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env={"SHARD": shard, "PATH": "/usr/bin:/bin"},
    )
    if probe.returncode != 0:
        raise SystemExit(probe.stderr)
    for token in probe.stdout.split():
        owners[int(token)].append(shard_id)
bad = [str(index) for index, found in owners.items() if found != [((index - 1) % width) + 1]]
if bad:
    raise SystemExit("shard map is not a partition: " + ", ".join(bad[:8]))

manifest_text = open(manifest, encoding="utf-8").read().splitlines()
expected = {
    f"changelog-release-{shard_id}\tCHANGELOG_CALLER_CONTRACT_SHARD={shard_id} bash scripts/ci-gate/changelog-caller-contract.test.sh"
    for shard_id in range(1, 5)
}
if not expected.issubset(manifest_text):
    raise SystemExit("manifest is missing a caller-contract shard command")
if any(
    line.endswith("\tbash scripts/ci-gate/changelog-caller-contract.test.sh")
    for line in manifest_text
):
    raise SystemExit("manifest still runs the caller contract without a shard")
print(f"ok   - caller-contract cases partition across {width} shards ({calls} cases)")
PY

sha="$(git -C "$repo_root" rev-parse HEAD)"
bash "$gen" contract-test "$sha" >"$tmp/suite.sh"
if grep -q 'contract_section_selected\|CHANGELOG_CONTRACT_SECTIONS' "$tmp/suite.sh"; then
  fail "the generated contract suite can skip a section"
else
  pass "the generated contract suite runs every section"
fi

if CHANGELOG_CALLER_CONTRACT_SHARD=9 bash "$caller" >"$tmp/shard.out" 2>&1; then
  fail "the caller contract accepted shard 9"
elif grep -q 'CHANGELOG_CALLER_CONTRACT_SHARD must be all or 1..4' "$tmp/shard.out"; then
  pass "the caller contract rejects an unknown shard before it runs cases"
else
  fail "the caller contract rejected shard 9 without the shard diagnostic"
fi

[ "$fails" -eq 0 ] || exit 1
echo "All tests passed."
