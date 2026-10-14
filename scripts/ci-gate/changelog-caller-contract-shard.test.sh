#!/usr/bin/env bash
# The caller contract is partitioned by case across the four changelog-release
# cells. The generated suite has no section switch: a shard that owns a mutation
# runs every assertion (ADR 0224, ADR 0225).
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
  && ! changelog_caller_shard_name_ok 01 \
  && ! changelog_caller_shard_name_ok changelog; then
  pass "caller-contract shard names are all and 1..4"
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

# A case shard skips the generator-only assertions. Anything those assertions
# assign therefore does not exist in a shard, and a case that reads it builds a
# different fixture there than in the full run. `custom_contract` did exactly
# that: shards wrote an empty contract suite, which exits 0, so every positive
# custom-release case passed vacuously and every rejection case was misreported.
# The section is delimited by explicit BEGIN/END marker comments rather than by
# parsing if/fi, which a quoted `<<'PY'` inside a string once broke. Empty
# initializers do not count as definitions -- one is what hid that leak.
# Upper-case names are environment prefixes on single commands, not fixtures.
cat >"$tmp/fixture-leaks.py" <<'LEAKS'
import re
import subprocess
import sys

lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
begins = [i for i, line in enumerate(lines) if line.startswith("# BEGIN generator-only assertions")]
ends = [i for i, line in enumerate(lines) if line.startswith("# END generator-only assertions")]
if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
    raise SystemExit(
        f"expected one generator-only section, found {len(begins)} BEGIN and {len(ends)} END markers"
    )
start, end = begins[0], ends[0]
# The gate is a named predicate with a pinned body. Exactly one `if` calls it,
# directly below BEGIN; the generator-only row's early exit is the one call of
# its own predicate and sits inside the section. No other line may test the
# mode, so assertions cannot be gated by a copy of the condition elsewhere.
predicates = [
    "generator_only_row() {",
    '  [ "${CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY:-0}" = 1 ]',
    "}",
    "generator_only_assertions() {",
    '  generator_only_row || [ "${CHANGELOG_CALLER_CONTRACT_SHARD:-all}" = all ]',
    "}",
]
validation = {
    'case "${CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY:-0}" in',
    "  *) printf 'FAIL - CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY must be 0 or 1\\n' >&2; exit 1 ;;",
}
shard_validation = {
    'if ! changelog_caller_shard_name_ok "${CHANGELOG_CALLER_CONTRACT_SHARD:-all}"; then',
    "  printf 'FAIL - CHANGELOG_CALLER_CONTRACT_SHARD must be all or 1..4\\n' >&2",
}
blocks = [i for i in range(len(lines)) if lines[i:i + len(predicates)] == predicates]
if len(blocks) != 1:
    raise SystemExit(f"expected the pinned gate predicates exactly once, found {len(blocks)}")
predicate_lines = set(range(blocks[0], blocks[0] + len(predicates)))
outside = [i for i in range(len(lines)) if i not in predicate_lines]
if any(re.match(r"\s*(?:function\s+)?generator_only_(?:row|assertions)\b\s*(?:\(\))?\s*\{?\s*$", lines[i])
       and "()" in lines[i] for i in outside):
    raise SystemExit("a gate predicate is redefined outside its pinned definition")
for i in outside:
    if "CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY" in lines[i] and lines[i] not in validation:
        raise SystemExit(f"line {i + 1} tests the generator-only mode outside the gate predicates")
    # Any other read of the shard selector could reselect the unsharded run,
    # by comparison or by case pattern; only its validation may name it.
    if "CHANGELOG_CALLER_CONTRACT_SHARD" in lines[i] and lines[i] not in shard_validation:
        raise SystemExit(f"line {i + 1} tests for the unsharded run outside the gate predicates")
gate_calls = [i for i in outside if re.search(r"\bgenerator_only_assertions\b", lines[i])]
if gate_calls != [start + 1] or lines[start + 1] != "if generator_only_assertions; then":
    raise SystemExit(
        "BEGIN marker must directly precede the one `if generator_only_assertions; then` gate "
        f"(gate calls on lines {[i + 1 for i in gate_calls]})"
    )
row_calls = [i for i in outside if re.search(r"\bgenerator_only_row\b", lines[i])]
if len(row_calls) != 1 or not start < row_calls[0] < end:
    raise SystemExit("the generator-only row's early exit must be the one gate call inside the section")
if lines[end - 1] != "fi":
    raise SystemExit("END marker must directly follow the generator-only gate's closing `fi`")
# Bash decides which `fi` closes the gate: the first column-0 `fi` at which the
# span from the gate parses as complete must be the one directly above END.
for index in range(start + 1, end):
    if lines[index] != "fi":
        continue
    span = "\n".join(lines[start + 1:index + 1]) + "\n"
    parsed = subprocess.run(["bash", "-n"], input=span, text=True, capture_output=True)
    if parsed.returncode == 0:
        if index != end - 1:
            raise SystemExit(
                f"END marker must directly follow the gate's closing `fi` (line {index + 1}), "
                f"not line {end}"
            )
        break
else:
    raise SystemExit("END marker does not close the generator-only gate")
name = r"([a-z_][a-z0-9_]*)"
variable_patterns = (
    re.compile(r"^\s*(?:export\s+|local\s+|declare\s+(?:-\w+\s+)?)?" + name + r"\+?=(?!''|\"\"|\s|$)"),
    re.compile(r"\bcapture_mode\s+" + name),
    re.compile(r"\bprintf\s+-v\s+" + name),
    re.compile(r"\bfor\s+" + name + r"\s+in\b"),
    re.compile(r"\b(?:mapfile|readarray)\b(?:\s+-\S+(?:\s+(?!-)\S+)?)*?\s+" + name + r"\s*(?:<|$)"),
)
read_pattern = re.compile(r"\bread\b((?:\s+-[a-zA-Z]+(?:\s+(?![-$])\S+)?)*)((?:\s+[a-z_][a-z0-9_]*)+)")
function_pattern = re.compile(r"^\s*" + name + r"\s*\(\)\s*\{")


def defined(indices, functions=False):
    names = set()
    for index in indices:
        line = lines[index]
        if functions:
            names.update(function_pattern.findall(line))
            continue
        for pattern in variable_patterns:
            names.update(pattern.findall(line))
        for options, targets in read_pattern.findall(line):
            names.update(targets.split())
            names.update(re.findall(r"-a\s+" + name, options))
    return names


section = range(start + 1, end)
elsewhere = [*range(0, start), *range(end, len(lines))]
references = {
    variable: re.compile(r"\$\{?%s\b" % variable)
    for variable in defined(section) - defined(elsewhere)
}
references.update(
    (function, re.compile(r"(?:^|[\s;&|(])%s(?:\s|$)" % function))
    for function in defined(section, True) - defined(elsewhere, True)
)
leaks = []
for variable, reference in sorted(references.items()):
    for index in range(end + 1, len(lines)):
        if reference.search(lines[index]):
            leaks.append(f"{variable} (line {index + 1})")
            break
if leaks:
    raise SystemExit("case fixtures defined only by generator-only assertions: " + ", ".join(leaks))
LEAKS
if python3 "$tmp/fixture-leaks.py" "$caller"; then
  pass "every case fixture is defined outside the generator-only assertions"
else
  fail "case shards read fixtures that only the generator-only assertions define"
fi

# The checker must see every way bash binds a name, and a quoted heredoc token
# inside the section must not hide the section from it.
fixture_header() {
  printf '#!/usr/bin/env bash\n'
  printf '%s\n' 'case "${CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY:-0}" in' '  0|1) ;;' \
    "  *) printf 'FAIL - CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY must be 0 or 1\\n' >&2; exit 1 ;;" 'esac' \
    'generator_only_row() {' '  [ "${CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY:-0}" = 1 ]' '}' \
    'generator_only_assertions() {' \
    '  generator_only_row || [ "${CHANGELOG_CALLER_CONTRACT_SHARD:-all}" = all ]' '}'
}
early_exit='if generator_only_row; then exit 0; fi'
leak_fixture() { # <name> <definition line>
  {
    fixture_header
    printf '# BEGIN generator-only assertions\n'
    printf '%s\n' 'if generator_only_assertions; then' "grep -qF \"python3 -I - <<'PY'\" <<<\"\$x\"" \
      "$2" "$early_exit" 'fi'
    printf '# END generator-only assertions\n'
    printf 'printf %%s "${%s}"\n' "$1"
  } >"$tmp/leak-$1.sh"
}
leak_fixture assigned 'assigned="value"'
leak_fixture captured 'capture_mode captured workflow "$sha"'
leak_fixture printed "printf -v printed '%s' x"
leak_fixture read_line 'IFS= read -r read_line <"$file"'
leak_fixture read_pair 'IFS=: read -r -d "" read_first read_pair <"$file"'
leak_fixture read_array 'read -r -a read_array <<<"$x"'
leak_fixture mapped 'mapfile -t mapped <"$file"'
leak_fixture array_read 'readarray -d "" -t array_read < <(printf x)'
leak_fixture looped 'for looped in a b; do :; done'
fixture_misses=()
for fixture in assigned captured printed read_line read_pair read_array mapped array_read looped; do
  if python3 "$tmp/fixture-leaks.py" "$tmp/leak-$fixture.sh" >/dev/null 2>"$tmp/leak.err" \
    || ! grep -q "$fixture (line" "$tmp/leak.err"; then
    fixture_misses+=("$fixture")
  fi
done
printf '#!/usr/bin/env bash\nx=1\n' >"$tmp/leak-unmarked.sh"
# Markers bind to the gate: BEGIN directly precedes the gate's `if`, and END
# directly follows the `fi` that closes it. Otherwise a capture moved back into
# the gate can sit outside the marked span and pass unseen.
{
  fixture_header
  printf '# BEGIN generator-only assertions\nif generator_only_assertions; then\n%s\n' "$early_exit"
  printf 'if true; then\n  :\nfi\n# END generator-only assertions\n'
  printf 'capture_mode leaked workflow "$sha"\nfi\nprintf %%s "$leaked"\n'
} >"$tmp/leak-end-early.sh"
{
  fixture_header
  printf 'if generator_only_assertions; then\ncapture_mode leaked workflow "$sha"\n'
  printf '# BEGIN generator-only assertions\n%s\nfi\n# END generator-only assertions\n' "$early_exit"
  printf 'printf %%s "$leaked"\n'
} >"$tmp/leak-begin-late.sh"
# Markers around the early-exit `if` while the capture sits in the real gate.
{
  fixture_header
  printf 'if generator_only_assertions; then\ncapture_mode leaked workflow "$sha"\nfi\n'
  printf '# BEGIN generator-only assertions\n%s\n# END generator-only assertions\n' \
    'if generator_only_row; then exit 0; fi'
  printf 'printf %%s "$leaked"\n'
} >"$tmp/leak-wrong-gate.sh"
# The real gate rewritten onto one line with the condition inline, and the
# markers around a dummy copy of the gate: text matching alone accepts this.
{
  fixture_header
  printf '%s\n' 'if generator_only_row || [ "${CHANGELOG_CALLER_CONTRACT_SHARD:-all}" = all ]; then'
  printf 'capture_mode leaked workflow "$sha"\nfi\n'
  printf '# BEGIN generator-only assertions\nif generator_only_assertions; then\n%s\nfi\n' "$early_exit"
  printf '# END generator-only assertions\nprintf %%s "$leaked"\n'
} >"$tmp/leak-dummy-gate.sh"
# The same with the predicate called twice instead of inlined.
{
  fixture_header
  printf 'if generator_only_assertions; then\ncapture_mode leaked workflow "$sha"\nfi\n'
  printf '# BEGIN generator-only assertions\nif generator_only_assertions; then\n%s\nfi\n' "$early_exit"
  printf '# END generator-only assertions\nprintf %%s "$leaked"\n'
} >"$tmp/leak-second-call.sh"
# The unsharded run selected by a case pattern instead of a comparison.
{
  fixture_header
  printf '%s\n' 'case "${CHANGELOG_CALLER_CONTRACT_SHARD:-all}" in' '  all) capture_mode leaked workflow "$sha" ;;' 'esac'
  printf '# BEGIN generator-only assertions\nif generator_only_assertions; then\n%s\nfi\n' "$early_exit"
  printf '# END generator-only assertions\nprintf %%s "$leaked"\n'
} >"$tmp/leak-shard-case.sh"
misplaced_ok=1
for misplaced in end-early begin-late wrong-gate dummy-gate second-call shard-case; do
  if python3 "$tmp/fixture-leaks.py" "$tmp/leak-$misplaced.sh" >/dev/null 2>"$tmp/leak.err" \
    || ! grep -qE 'marker|gate' "$tmp/leak.err"; then
    misplaced_ok=0
    fixture_misses+=("$misplaced")
  fi
done
if [ "${#fixture_misses[@]}" -eq 0 ] && [ "$misplaced_ok" -eq 1 ] \
  && ! python3 "$tmp/fixture-leaks.py" "$tmp/leak-unmarked.sh" 2>"$tmp/leak.err" \
  && grep -q 'expected one generator-only section' "$tmp/leak.err"; then
  pass "the fixture-leak check sees every binding form and rejects markers not bound to the gate"
else
  fail "the fixture-leak check missed a binding or an unmarked section: ${fixture_misses[*]:-unmarked}"
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

width = 4
# Declarations expand into more selections at runtime (loops, nested cases).
# The caller itself fails unless a run makes exactly the pinned number, so this
# only checks that the helper's map partitions that many selections evenly.
pinned = subprocess.run(
    ["bash", "-c", 'source "$SHARD"; printf "%s" "$CHANGELOG_CALLER_CONTRACT_CASES"'],
    check=True, capture_output=True, text=True, env={"SHARD": shard, "PATH": "/usr/bin:/bin"},
).stdout
if not pinned.isdigit():
    raise SystemExit("the shard helper does not pin CHANGELOG_CALLER_CONTRACT_CASES")
expanded_cases = int(pinned)
owners = {index: [] for index in range(1, expanded_cases + 1)}
for shard_id in range(1, width + 1):
    script = f'''
source "$SHARD"
caller_case_count=0
caller_case_ran=0
CHANGELOG_CALLER_CONTRACT_SHARD={shard_id}
selected=""
for index in $(seq 1 {expanded_cases}); do
  if changelog_caller_case_selected "case-$index"; then
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
bad = [str(index) for index, found in owners.items() if len(found) != 1]
if bad:
    raise SystemExit("shard map is not a partition: " + ", ".join(bad[:8]))
loads = [sum(found == [shard_id] for found in owners.values()) for shard_id in range(1, width + 1)]
if max(loads) - min(loads) > 1:
    raise SystemExit(f"caller-contract shards are unbalanced: {loads}")

# One shard per cell. A shard schedules its cases across the runner's CPUs, so
# fewer, heavier rows pay the fixture walk four times instead of once per case;
# 233 single-case rows multiplied it 233 times and ran the cells into their
# 30-minute timeout (run 38014446043). The declared budget is twice the slowest
# measured shard (5m00s on run 37973049643, #1743).
manifest_text = open(manifest, encoding="utf-8").read().splitlines()
caller_rows = [line for line in manifest_text if "scripts/ci-gate/changelog-caller-contract.test.sh" in line]
expected = {
    f"changelog-release-{shard_id}\t@budget=600 CHANGELOG_CALLER_CONTRACT_SHARD={shard_id} "
    "bash scripts/ci-gate/changelog-caller-contract.test.sh"
    for shard_id in range(1, width + 1)
} | {
    "changelog-release-1\t@budget=120 CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY=1 "
    "bash scripts/ci-gate/changelog-caller-contract.test.sh"
}
if len(caller_rows) != len(expected) or set(caller_rows) != expected:
    raise SystemExit(
        "manifest caller-contract rows are not exactly one shard per cell plus the "
        "generator-only row: " + "; ".join(sorted(set(caller_rows) ^ expected))
    )
print(
    f"ok   - {calls} caller-contract declarations; {expanded_cases} pinned case "
    f"selections partition across {width} shards {loads}"
)
PY

sha="$(git -C "$repo_root" rev-parse HEAD)"
bash "$gen" contract-test "$sha" >"$tmp/suite.sh"
if grep -qE 'CHANGELOG_CONTRACT_SECTIONS|contract_section_selected|contract_sections_finish' "$tmp/suite.sh"; then
  fail "the generated contract suite exposes a section selector"
else
  pass "the generated contract suite has no section selector"
fi

# A generator mode that exits 0 with no output is a defect, not a fixture: an
# empty contract suite exits 0 and passes every positive case. Run the caller
# against a stub generator that prints nothing and require it to say so.
stub_root="$tmp/empty-generator"
mkdir -p "$stub_root/scripts/ci-gate"
cp "$caller" "$stub_root/scripts/ci-gate/changelog-caller-contract.test.sh"
cp "$shard" "$stub_root/scripts/changelog-caller-contract-shard.sh"
printf '#!/usr/bin/env bash\nexit 0\n' >"$stub_root/scripts/gen-changelog-caller.sh"
chmod +x "$stub_root/scripts/gen-changelog-caller.sh"
git -C "$stub_root" init -q
git -C "$stub_root" -c user.name=t -c user.email=t@t commit -q --allow-empty -m stub
if CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY=1 \
  bash "$stub_root/scripts/ci-gate/changelog-caller-contract.test.sh" >"$tmp/empty-generator.out" 2>&1; then
  fail "the caller contract accepted a generator mode that emitted nothing"
elif grep -q "generator mode 'workflow' exited 0 but emitted nothing" "$tmp/empty-generator.out"; then
  pass "a generator mode that emits nothing fails capture instead of becoming an empty fixture"
else
  cat "$tmp/empty-generator.out" >&2
  fail "an empty generator capture failed without naming the empty mode"
fi

if CHANGELOG_CALLER_CONTRACT_SHARD=5 bash "$caller" >"$tmp/shard.out" 2>&1; then
  fail "the caller contract accepted shard 5"
elif grep -q 'CHANGELOG_CALLER_CONTRACT_SHARD must be all or 1..4' "$tmp/shard.out"; then
  pass "the caller contract rejects an unknown shard before it runs cases"
else
  fail "the caller contract rejected shard 5 without the shard diagnostic"
fi


[ "$fails" -eq 0 ] || exit 1
echo "All tests passed."
