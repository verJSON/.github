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
  && changelog_caller_shard_name_ok 233 \
  && ! changelog_caller_shard_name_ok 0 \
  && ! changelog_caller_shard_name_ok 234 \
  && ! changelog_caller_shard_name_ok 001 \
  && ! changelog_caller_shard_name_ok changelog; then
  pass "caller-contract shard names are all and 1..233"
else
  fail "caller-contract shard names accepted an unknown value or rejected a valid one"
fi

if ! grep -qE 'CHANGELOG_CONTRACT_SECTIONS|contract_section_selected|contract_sections_finish' "$gen" \
  && ! grep -qE 'CHANGELOG_CONTRACT_SECTIONS|changelog_contract_run_for|contract_section_selected' "$caller" \
  && grep -Fq 'env "${cache_env[@]}" ./scripts/changelog-contract.test.sh' "$caller"; then
  pass "each selected caller mutation runs the complete generated contract"
else
  fail "caller mutations can skip assertions in the generated contract"
fi

python3 - "$caller" "$shard" "$manifest" <<'PY' || fails=$((fails + 1))
import re
import subprocess
import sys

caller, shard, manifest = sys.argv[1:]
text = open(caller, encoding="utf-8").read()
expected_calls = 223
calls = len(re.findall(r"^schedule_adopter_script ", text, re.M))
calls += len(re.findall(r"^[ \t]*expect_rejection ", text, re.M))
calls += len(re.findall(r"^expect_release_mode_rejection ", text, re.M))
calls += len(re.findall(r"^[ \t]*expect_unestablished_pin ", text, re.M))
calls += len(re.findall(r"^(?:if )?run_adopter ", text, re.M))
calls += len(
    re.findall(
        r"^assert_(?:mutable_verification_path|release_path_mutation)_rejected\s+",
        text,
        re.M,
    )
)
if calls != expected_calls:
    raise SystemExit(
        f"parser saw {calls} caller-contract cases; expected {expected_calls}; "
        "review and update the case inventory when cases change"
    )

width = 233
# Some declarations expand into multiple executions. Pin the runtime inventory
# too; the caller suite requires exactly one selected case in every shard.
expanded_cases = 233
owners = {index: [] for index in range(1, expanded_cases + 1)}
for shard_id in range(1, width + 1):
    script = f'''
source "$SHARD"
caller_case_count=0
caller_case_ran=0
caller_case_reserved_count=0
caller_adopter_cases_seen=0
CHANGELOG_CALLER_CONTRACT_SHARD={shard_id}
selected=""
for index in $(seq 1 {expanded_cases}); do
  case_id=ignored
  case "$index" in
    1|2) case_id=adopter ;;
    3) case_id=snapshot-capture-path-override ;;
  esac
  if changelog_caller_case_selected "$case_id"; then
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
def expected_owner(index):
    if index == 1:
        return width - 2
    if index == 2:
        return width - 1
    if index == 3:
        return width
    return ((index - 4) % (width - 3)) + 1

bad = [str(index) for index, found in owners.items() if found != [expected_owner(index)]]
if bad:
    raise SystemExit("shard map is not a partition: " + ", ".join(bad[:8]))
empty_shards = [
    shard_id
    for shard_id in range(1, width + 1)
    if not any(found == [shard_id] for found in owners.values())
]
if empty_shards:
    raise SystemExit("caller-contract shards own no case: " + ", ".join(map(str, empty_shards)))

manifest_text = open(manifest, encoding="utf-8").read().splitlines()
expected = {
    f"changelog-release-{(shard_id - 1) % 4 + 1}\tADOPTER_SLOTS=1 CHANGELOG_CALLER_CONTRACT_SHARD={shard_id} bash scripts/ci-gate/changelog-caller-contract.test.sh"
    for shard_id in range(1, width + 1)
}
actual_shards = [
    line
    for line in manifest_text
    if "\tADOPTER_SLOTS=1 CHANGELOG_CALLER_CONTRACT_SHARD=" in line
]
if len(actual_shards) != width or set(actual_shards) != expected:
    raise SystemExit("manifest caller-contract shard commands are not an exact one-to-one inventory")
generator_commands = {
    "changelog-release-1\tCHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY=1 bash scripts/ci-gate/changelog-caller-contract.test.sh"
}
if not generator_commands.issubset(manifest_text):
    raise SystemExit("manifest is missing the per-cell generator contract command")
if any(
    line.endswith("\tbash scripts/ci-gate/changelog-caller-contract.test.sh")
    and "CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY=1" not in line
    for line in manifest_text
):
    raise SystemExit("manifest still runs the caller contract without a shard or generator-only mode")
print(
    f"ok   - {calls} caller-contract declarations expand to {expanded_cases} "
    f"single-case executions across {width} shards"
)
PY

sha="$(git -C "$repo_root" rev-parse HEAD)"
bash "$gen" contract-test "$sha" >"$tmp/suite.sh"
if grep -qE 'CHANGELOG_CONTRACT_SECTIONS|contract_section_selected|contract_sections_finish' "$tmp/suite.sh"; then
  fail "the generated contract suite exposes a section selector"
else
  pass "the generated contract suite has no section selector"
fi

if CHANGELOG_CALLER_CONTRACT_SHARD=234 bash "$caller" >"$tmp/shard.out" 2>&1; then
  fail "the caller contract accepted shard 234"
elif grep -q 'CHANGELOG_CALLER_CONTRACT_SHARD must be all or 1..233' "$tmp/shard.out"; then
  pass "the caller contract rejects an unknown shard before it runs cases"
else
  fail "the caller contract rejected shard 234 without the shard diagnostic"
fi

cache_file="$tmp/not-a-directory"
: >"$cache_file"
if CHANGELOG_CALLER_CONTRACT_CACHE="$cache_file" CHANGELOG_CALLER_CONTRACT_SHARD=1 \
  bash "$caller" >"$tmp/cache.out" 2>&1; then
  fail "the caller contract accepted a cache path that is a file"
elif grep -q "capture_mode could not create the cached output directory for generator mode 'contract-test'" "$tmp/cache.out"; then
  pass "cache setup failures name the actual failed operation"
else
  fail "a cache setup failure was misreported as a generator failure"
fi

[ "$fails" -eq 0 ] || exit 1
echo "All tests passed."
