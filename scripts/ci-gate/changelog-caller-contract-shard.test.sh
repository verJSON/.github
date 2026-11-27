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
  && grep -Fq 'env "${cache_env[@]}" "GITHUB_REPOSITORY_OWNER=${ADOPTER_GITHUB_OWNER:-}" ./scripts/changelog-contract.test.sh' "$caller"; then
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

# Out-of-case reads of a shared adopter tree must name the selection that built
# it. A grep of $quoted after the quoted case was skipped is a silent weaken
# (#1978): the assertion runs, the tree was never built, and the failure is
# either lost to a suspended pass/fail or blamed on the next selected case.
def _skip_heredoc(index, line):
    match = re.search(r"(?<!<)<<(?!<)-?\s*['\"]?([A-Za-z0-9_]+)", line)
    if not match or line[: match.start()].count("'") % 2 == 1:
        return None
    tag = match.group(1)
    cursor = index + 1
    while cursor < len(lines) and lines[cursor] != tag:
        cursor += 1
    return cursor + 1 if cursor < len(lines) else len(lines)


inside_function = set()
cursor = 0
while cursor < len(lines):
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(\) \{$", lines[cursor]):
        depth = 1
        cursor += 1
        while cursor < len(lines) and depth:
            nxt = _skip_heredoc(cursor, lines[cursor])
            if nxt is not None:
                inside_function.update(range(cursor, nxt))
                cursor = nxt
                continue
            inside_function.add(cursor)
            depth += lines[cursor].count("{") - lines[cursor].count("}")
            cursor += 1
        continue
    cursor += 1

owned_lines = set()
prepared_lines = set()
cursor = 0
while cursor < len(lines):
    if cursor in inside_function:
        cursor += 1
        continue
    if re.match(r"\s*fixture_owned_here ", lines[cursor]):
        cursor += 1
        while cursor < len(lines) and lines[cursor] != "FIXTURE_OWNER":
            owned_lines.add(cursor)
            cursor += 1
        cursor += 1
        continue
    indent = re.match(r"(\s*)if caller_case_prepare ", lines[cursor])
    if indent:
        prefix = indent.group(1)
        cursor += 1
        while cursor < len(lines) and not re.match("^" + re.escape(prefix) + r"fi\s*$", lines[cursor]):
            prepared_lines.add(cursor)
            cursor += 1
        cursor += 1
        continue
    cursor += 1

shared_fixtures = (
    "adopter",
    "quoted",
    "artifact_adopter",
    "private_artifact_adopter",
    "upper_scope_adopter",
    "generated_adopter",
    "adr_adopter",
    "refs_adopter",
    "foreign_adopter",
    "nested_adopter",
    "multi_release_adopter",
    "custom_adopter",
)
ensured_by = {
    "ensure_pristine_adopter": "adopter",
    "ensure_released_adopter": "adopter",
    "ensure_artifact_adopter": "artifact_adopter",
    "ensure_private_artifact_adopter": "private_artifact_adopter",
    "ensure_upper_scope_adopter": "upper_scope_adopter",
    "ensure_generated_adopter": "generated_adopter",
    "ensure_adr_adopter": "adr_adopter",
    "ensure_refs_adopter": "refs_adopter",
    "ensure_foreign_adopter": "foreign_adopter",
    "ensure_nested_adopter": "nested_adopter",
    "ensure_multi_release_adopter": "multi_release_adopter",
    "ensure_custom_adopter": "custom_adopter",
}
shared_read = re.compile(
    r"\$\{?(?:%s)\b" % "|".join(sorted(shared_fixtures, key=len, reverse=True))
)


def _previous_code(index):
    index -= 1
    while index >= 0 and (not lines[index].strip() or lines[index].lstrip().startswith("#")):
        index -= 1
    return lines[index].strip() if index >= 0 else ""


untied = []
for index, line in enumerate(lines):
    if index in inside_function or index in owned_lines or index in prepared_lines:
        continue
    if line.lstrip().startswith("#") or not shared_read.search(line):
        continue
    if re.match(r"\s*(if caller_case_prepare|ensure_[A-Za-z0-9_]+)", line):
        continue
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", line) and "pass" not in line and "fail" not in line:
        continue
    if not re.search(r"\b(pass|fail|grep|python3)\b|^\s*\(cd ", line):
        continue
    previous = _previous_code(index)
    if any(
        previous == call and re.search(r"\$\{?%s\b" % variable, line)
        for call, variable in ensured_by.items()
    ):
        continue
    untied.append(f"{index + 1}: {line.strip()}")
if untied:
    raise SystemExit("untied fixture read: " + "; ".join(untied))
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

# A read of a shared tree outside its prepare block and outside fixture_owned_here
# is the #1978 weaken: the assertion runs on a shard that never built the tree.
{
  fixture_header
  printf '# BEGIN generator-only assertions\nif generator_only_assertions; then\n%s\nfi\n' "$early_exit"
  printf '# END generator-only assertions\nquoted="$tmproot/adopter-quoted"\n'
  printf 'grep -q title "$quoted/CHANGELOG.md"\n'
} >"$tmp/leak-untied-read.sh"
{
  fixture_header
  printf '# BEGIN generator-only assertions\nif generator_only_assertions; then\n%s\nfi\n' "$early_exit"
  printf '# END generator-only assertions\nquoted="$tmproot/adopter-quoted"\n'
  printf "fixture_owned_here owner_1 <<'FIXTURE_OWNER'\n"
  printf 'grep -q title "$quoted/CHANGELOG.md"\nFIXTURE_OWNER\n'
} >"$tmp/leak-tied-read.sh"
if ! python3 "$tmp/fixture-leaks.py" "$tmp/leak-untied-read.sh" >/dev/null 2>"$tmp/leak.err" \
  && grep -q 'untied fixture read' "$tmp/leak.err" \
  && python3 "$tmp/fixture-leaks.py" "$tmp/leak-tied-read.sh" >/dev/null 2>"$tmp/leak.err"; then
  pass "an out-of-case fixture read must name the selection that owns it"
else
  cat "$tmp/leak.err" >&2
  fail "the fixture-leak check missed an untied shared-tree read or rejected a tied one"
fi

# Selection order is the sequence of changelog_caller_case_selected calls.
# prepare consumes one, take reuses it, peek does not consume, and a shared
# marker is created once. An owned body does not run for a selection this shard skipped.
shard_behavior="$(
  set -uo pipefail
  # shellcheck source=scripts/changelog-caller-contract-shard.sh
  source "$shard"
  # shellcheck source=scripts/changelog-caller-contract-fixtures.sh
  source "$repo_root/scripts/changelog-caller-contract-fixtures.sh"
  caller_case_count=0
  caller_case_ran=0
  CHANGELOG_CALLER_CONTRACT_SHARD=1
  if ! changelog_caller_case_would_run 0 || [ "$caller_case_count" -ne 0 ]; then
    echo peek-consumed
  fi
  if changelog_caller_case_would_run 1; then
    echo peek-selected-the-other-shard
  fi
  if ! caller_case_prepare alpha owner_a || ! caller_case_take alpha; then
    echo first-case-skipped
  fi
  if [ "$caller_case_count" -ne 1 ]; then
    echo "count-after-take:$caller_case_count"
  fi
  if caller_case_prepare beta || caller_case_take beta; then
    echo second-case-ran
  fi
  root="$tmp/shared-tree"
  builds=0
  ensure_demo() {
    if [ -e "$root.fixture-base" ]; then
      return 0
    fi
    mkdir -p "$root"
    : >"$root.fixture-base"
    builds=$((builds + 1))
    note_fixture_materialization
  }
  ensure_demo
  ensure_demo
  # shellcheck disable=SC2154 # assigned by note_fixture_materialization in the sourced fixtures helper
  if [ "$builds" -ne 1 ] || [ "$caller_fixture_materializations" -ne 1 ]; then
    echo "ensure-once:$builds:$caller_fixture_materializations"
  fi
  owned_out="$(fixture_owned_here owner_a <<'FIXTURE_OWNER'
printf selected
FIXTURE_OWNER
)"
  if [ "$owned_out" != selected ]; then
    echo "owned-selected:$owned_out"
  fi
  CHANGELOG_CALLER_CONTRACT_SHARD=2
  caller_case_count=0
  caller_case_ran=0
  unset caller_case_selected_ordinal
  # shellcheck disable=SC2034 # reset for the fixtures helper; caller_case_prepare writes into it
  declare -A caller_case_selected_ordinal
  caller_case_prepare gamma owner_c || true
  skipped_out="$(fixture_owned_here owner_c <<'FIXTURE_OWNER'
printf selected
FIXTURE_OWNER
)"
  if [ -n "$skipped_out" ]; then
    echo "owned-ran-unselected:$skipped_out"
  fi
)"
mismatch_status=0
(
  set -uo pipefail
  # shellcheck source=scripts/changelog-caller-contract-shard.sh
  source "$shard"
  # shellcheck source=scripts/changelog-caller-contract-fixtures.sh
  source "$repo_root/scripts/changelog-caller-contract-fixtures.sh"
  caller_case_count=0
  # shellcheck disable=SC2034 # read by the sourced shard and fixture helpers
  caller_case_ran=0
  # shellcheck disable=SC2034 # read by the sourced shard helper
  CHANGELOG_CALLER_CONTRACT_SHARD=1
  caller_case_prepare alpha
  caller_case_take beta
) >/dev/null 2>"$tmp/mismatch.err" || mismatch_status=$?
if [ -z "$shard_behavior" ] && [ "$mismatch_status" -eq 1 ] \
  && grep -q 'fixture prepared for alpha but the next case is beta' "$tmp/mismatch.err"; then
  pass "a shard builds a shared fixture once and runs only the reads its cases own"
else
  printf '%s\n' "$shard_behavior" >&2
  cat "$tmp/mismatch.err" >&2
  fail "shard fixture scheduling changed selection order or ran an unselected read"
fi

python3 - "$caller" "$shard" "$manifest" <<'PY' || fails=$((fails + 1))
import re
import subprocess
import sys

caller, shard, manifest = sys.argv[1:]
text = open(caller, encoding="utf-8").read()
expected_calls = 344
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
f"changelog-release-{shard_id}\t"
f"{'@exclusive ' if shard_id == 2 else ''}@budget=600 "
f"CHANGELOG_CALLER_CONTRACT_SHARD={shard_id} "
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
cp "$repo_root/scripts/changelog-caller-contract-fixtures.sh" \
  "$stub_root/scripts/changelog-caller-contract-fixtures.sh"
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

# Every generator call in the caller contract is must-succeed or expected-refusal.
# A must-succeed fixture redirect that ignores status leaves an empty file, and a
# later rejection of that file still reads green because `fail` is suspended
# between cases (#1983). write_generated_fixture / read_generated_fixture exit 1,
# the same abort the nested-only fixtures used (#1974).
cat >"$tmp/fixture-generator-inventory.py" <<'PY'
import re
import sys
from collections import deque

raw = open(sys.argv[1], encoding="utf-8").read().splitlines()
CALLS = (
    ("write", re.compile(r"\bwrite_generated_fixture\b(?!\()")),
    ("read", re.compile(r"\bread_generated_fixture\b(?!\()")),
    ("capture", re.compile(r"\bcapture_mode\b(?!\()")),
    ("refusal", re.compile(r"\bexpect_generator_refusal\b(?!\()")),
    ("raw", re.compile(r"\bbash \"\$gen\"")),
    ("direct", re.compile(r"(?:^|\bif\s+)\"\$gen\"")),
)


def strip_comment(line):
    out = []
    quote = None
    i = 0
    while i < len(line):
        char = line[i]
        if quote:
            out.append(char)
            if char == quote and (i == 0 or line[i - 1] != "\\"):
                quote = None
            i += 1
            continue
        if char in "'\"":
            quote = char
            out.append(char)
        elif char == "#":
            break
        else:
            out.append(char)
        i += 1
    return "".join(out).rstrip()


def heredoc_delimiters(line):
    """Real heredoc openers. `<<` inside quotes is fixture text, not a body."""
    delims = []
    quote = None
    i = 0
    while i < len(line):
        char = line[i]
        if quote:
            if char == quote and line[i - 1] != "\\":
                quote = None
            i += 1
            continue
        if char == "$" and i + 1 < len(line) and line[i + 1] == "'":
            quote = "'"
            i += 2
            continue
        if char in "'\"":
            quote = char
            i += 1
            continue
        if char == "#":
            break
        if (
            char == "<"
            and i + 1 < len(line)
            and line[i + 1] == "<"
            and (i == 0 or line[i - 1] != "<")
            and not (i + 2 < len(line) and line[i + 2] == "<")
        ):
            j = i + 2
            if j < len(line) and line[j] == "-":
                j += 1
            while j < len(line) and line[j] == " ":
                j += 1
            if j < len(line) and line[j] in "'\"":
                end = line.find(line[j], j + 1)
                if end == -1:
                    break
                delims.append(line[j + 1:end])
                i = end + 1
                continue
            end = j
            while end < len(line) and (line[end].isalnum() or line[end] == "_"):
                end += 1
            if end > j:
                delims.append(line[j:end])
            i = max(end, i + 2)
            continue
        i += 1
    return delims


def brace_delta(line):
    delta = 0
    quote = None
    i = 0
    while i < len(line):
        char = line[i]
        if quote:
            if char == quote and line[i - 1] != "\\":
                quote = None
            i += 1
            continue
        if char in "'\"":
            quote = char
        elif char == "{":
            delta += 1
        elif char == "}":
            delta -= 1
        i += 1
    return delta


code = []
pending = deque()
for line in raw:
    if pending:
        if line.strip() == pending[0]:
            pending.popleft()
        code.append("")
        continue
    stripped = strip_comment(line)
    for delim in heredoc_delimiters(line):
        pending.append(delim)
    code.append(stripped)
if pending:
    raise SystemExit("unclosed heredoc while inventorying generator calls: " + pending[0])

spans = {}
index = 0
while index < len(code):
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\) \{$", code[index])
    if not match:
        index += 1
        continue
    name = match.group(1)
    depth = 0
    start = index
    while index < len(code):
        depth += brace_delta(code[index])
        index += 1
        if depth == 0:
            break
    else:
        raise SystemExit(f"function {name} at line {start + 1} never closes")
    spans[name] = (start, index)

for name in (
    "capture_mode",
    "expect_generator_refusal",
    "write_generated_fixture",
    "read_generated_fixture",
):
    if name not in spans:
        raise SystemExit(f"missing {name}()")
    start, end = spans[name]
    if end - start > 80:
        raise SystemExit(f"{name}() span lines {start + 1}-{end} is implausibly large")

bodies = {name: "\n".join(raw[start:end]) for name, (start, end) in spans.items()}
write_body = bodies["write_generated_fixture"]
read_body = bodies["read_generated_fixture"]
if 'bash "$gen" "$@" >"$dest" || status=$?' not in write_body or not re.search(r"(?m)^[ ]+exit 1$", write_body):
    raise SystemExit("write_generated_fixture must run the generator and exit 1 when it fails")
if re.search(r"(?m)^\s*fail\s", write_body):
    raise SystemExit("write_generated_fixture uses fail, which is suspended between cases")
if 'out="$(bash "$gen" "$@" 2>"$err")" || status=$?' not in read_body or not re.search(r"(?m)^[ ]+exit 1$", read_body):
    raise SystemExit("read_generated_fixture must take the generator status and exit 1 on failure")
if re.search(r"(?m)^\s*fail\s", read_body):
    raise SystemExit("read_generated_fixture uses fail, which is suspended between cases")
refusal_body = bodies["expect_generator_refusal"]
if "exit 1" in refusal_body or '|| status=$?' not in refusal_body:
    raise SystemExit("expect_generator_refusal must keep asserting the refusal, not abort the shard")
if '[ "$mode_capture_failures" -eq 0 ] || exit 1' not in "\n".join(raw):
    raise SystemExit("capture_mode failures must still abort the shard")

inside = set()
for name in (
    "capture_mode",
    "expect_generator_refusal",
    "write_generated_fixture",
    "read_generated_fixture",
):
    start, end = spans[name]
    inside.update(range(start, end))

logical = []
index = 0
while index < len(code):
    if index in inside or not code[index].strip():
        index += 1
        continue
    start = index
    parts = []
    while index < len(code):
        piece = code[index]
        if piece.endswith("\\"):
            parts.append(piece[:-1].rstrip())
            index += 1
            continue
        parts.append(piece)
        index += 1
        break
    text = " ".join(part for part in parts if part.strip())
    if text.strip():
        logical.append((start, index - 1, text))


def next_code(end):
    cursor = end + 1
    while cursor < len(code):
        if code[cursor].strip():
            return code[cursor].strip()
        cursor += 1
    return ""


def then_action(text, following):
    match = re.search(r"\bthen\b(.*)$", text)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return following


def fixture_redirect(text):
    for match in re.finditer(r"(?<![0-9&|])>(?!&|>)\s*(\S+)", text):
        if match.group(1) != "/dev/null":
            return True
    return False


def classify(kind, text, following):
    if kind in ("write", "read", "capture"):
        return "must-succeed"
    if kind == "refusal":
        return "expected-refusal"
    if "|| :" in text:
        return "expected-refusal"
    if "refusal_status" in text:
        return "expected-refusal"
    if re.search(r"\bif\s+!", text):
        return "must-succeed"
    if re.search(r"\bif\s+(?:bash \"\$gen\"|\"\$gen\")", text):
        action = then_action(text, following)
        if action.startswith("fail"):
            return "expected-refusal"
        if action.startswith("pass"):
            return "must-succeed"
        return None
    if "||" in text:
        return "must-succeed"
    return None


def if_aborts(start):
    """A must-succeed if must exit the shard; fail() is suspended between cases."""
    depth = 0
    saw_exit = False
    for index in range(start, len(code)):
        line = code[index].strip()
        if not line:
            continue
        if re.match(r"if\b", line):
            depth += 1
        if re.search(r"\bexit 1\b", line):
            saw_exit = True
        if re.match(r"fi\b", line):
            depth -= 1
            if depth == 0:
                return saw_exit
    return False


errors = []
rows = []
for start, end, text in logical:
    found = []
    for kind, pattern in CALLS:
        for match in pattern.finditer(text):
            found.append((match.start(), kind))
    if not found:
        continue
    following = next_code(end)
    for _, kind in sorted(found):
        if kind == "raw" and fixture_redirect(text) and "||" not in text and not re.search(r"\bif\s+", text):
            errors.append(
                "unchecked must-succeed generator redirect at line "
                f"{start + 1}: {' '.join(text.split())[:160]}"
            )
            continue
        if kind == "raw" and '$(bash "$gen"' in text and "||" not in text:
            errors.append(
                "unchecked must-succeed generator capture at line "
                f"{start + 1}: {' '.join(text.split())[:160]}"
            )
            continue
        if (
            kind == "raw"
            and re.search(r"\bif\s+(?:bash \"\$gen\"|\"\$gen\")", text)
            and not re.search(r"\bif\s+!", text)
            and then_action(text, following).startswith("pass")
            and not if_aborts(start)
        ):
            errors.append(
                "must-succeed generator call lacks a status check at line "
                f"{start + 1}: {' '.join(text.split())[:160]}"
            )
            continue
        classified = classify(kind, text, following)
        if classified is None:
            errors.append(
                "unclassified generator call at line "
                f"{start + 1}: {' '.join(text.split())[:160]}"
            )
            continue
        rows.append((classified, start + 1, kind, " ".join(text.split())[:120]))

if errors:
    raise SystemExit("\n".join(errors))
must = sum(1 for row in rows if row[0] == "must-succeed")
refused = sum(1 for row in rows if row[0] == "expected-refusal")
if must < 1 or refused < 1:
    raise SystemExit(f"inventory is missing a class: must-succeed={must} expected-refusal={refused}")
for classified, line, kind, text in rows:
    print(f"{classified}\t{line}\t{kind}\t{text}")
print(f"inventory: {len(rows)} calls, {must} must-succeed, {refused} expected-refusal")
PY
if inventory="$(python3 "$tmp/fixture-generator-inventory.py" "$caller" 2>"$tmp/inventory.err")"; then
  summary="$(tail -n 1 <<<"$inventory")"
  unclassified="$(grep -vE '^(must-succeed|expected-refusal)	' <<<"$inventory" | grep -v '^inventory: ' || true)"
  if [ -n "$unclassified" ] || ! grep -q '^inventory: ' <<<"$summary"; then
    fail "fixture generator inventory is not a complete classification: $summary"
  else
    pass "$summary (#1983)"
  fi
else
  fail "fixture generator inventory rejected the caller contract: $(cat "$tmp/inventory.err")"
fi
cp "$caller" "$tmp/unchecked-caller.sh"
printf '\nbash "$gen" release-node "$sha" >"$tmproot/unchecked-fixture.yml"\n' >>"$tmp/unchecked-caller.sh"
if python3 "$tmp/fixture-generator-inventory.py" "$tmp/unchecked-caller.sh" >"$tmp/unchecked.out" 2>"$tmp/unchecked.err"; then
  fail "a new must-succeed generator redirect without a status check was accepted"
elif grep -q 'unchecked must-succeed generator redirect' "$tmp/unchecked.err"; then
  pass "a new must-succeed generator redirect without a status check is rejected (#1983)"
else
  fail "an unchecked generator redirect failed without the status-check diagnostic: $(cat "$tmp/unchecked.err")"
fi
cp "$caller" "$tmp/added-refusal.sh"
printf '\n%s\n' \
  'expect_generator_refusal '\''new reason'\'' "passes" "accepted" workflow not-a-sha' \
  >>"$tmp/added-refusal.sh"
if python3 "$tmp/fixture-generator-inventory.py" "$tmp/added-refusal.sh" >"$tmp/added-refusal.out" 2>"$tmp/added-refusal.err"; then
  pass "an added expected-refusal probe stays classified as a refusal (#1983)"
else
  fail "an added expected-refusal probe was rejected: $(cat "$tmp/added-refusal.err")"
fi

# Sampled must-succeed site: the default adopter's CODEOWNERS write. A case
# shard builds that adopter before it selects a case, so every shard must abort
# before a rejection can read the empty file as green (#1983). Bash presents
# both a command substitution and a script redirect as a pipe, so stdout type
# cannot tell them apart. The preamble captures codeowners once; the next
# codeowners invocation is that write. Other modes stay non-empty and silent
# so the shard reaches the write, and the retired-mode probe still refuses.
cat >"$tmp/fail-codeowners.sh" <<'EOF'
#!/usr/bin/env bash
set -u
mode=""
for arg in "$@"; do
  case "$arg" in
    -*) continue ;;
  esac
  mode="$arg"
  break
done
if [ "$mode" = codeowners ]; then
  state="${FIXTURE_GENERATOR_STATE:?}"
  mkdir -p "$state"
  count=0
  if [ -f "$state/codeowners" ]; then
    count="$(cat "$state/codeowners")"
  fi
  count=$((count + 1))
  printf '%s\n' "$count" >"$state/codeowners"
  if [ "$count" -ge 2 ]; then
    echo "mutated fixture generator failed" >&2
    exit 7
  fi
fi
if [ "$mode" = renovate-attribution ] || [ -z "$mode" ]; then
  echo "gen-changelog-caller.sh: unknown mode; the usage line lists every mode" >&2
  exit 2
fi
if [ "$mode" = contract-test ]; then
  printf '%s\n' 'EXPECTED_RELEASE_NODE_VERSION="22"'
  exit 0
fi
printf '%s\n' fixture
exit 0
EOF
chmod +x "$tmp/fail-codeowners.sh"
if python3 - "$caller" "$tmp/mutated-caller.sh" "$repo_root" "$tmp/fail-codeowners.sh" <<'PY'
import re
import sys

source, target, root, wrapper = sys.argv[1:]
text = open(source, encoding="utf-8").read()
assignment = re.compile(r'(?m)^repo_root="\$\(cd "\$here/\.\./\.\." && pwd\)"$')
if len(assignment.findall(text)) != 1:
    raise SystemExit(
        f"expected one repo_root assignment, found {len(assignment.findall(text))}"
    )
text = assignment.sub("repo_root=" + repr(root), text, count=1)
generator = 'gen="$repo_root/scripts/gen-changelog-caller.sh"'
if text.count(generator) != 1:
    raise SystemExit(f"expected one gen assignment, found {text.count(generator)}")
text = text.replace(generator, "gen=" + repr(wrapper), 1)
open(target, "w", encoding="utf-8").write(text)
PY
then
  fixture_abort_pids=()
  for fixture_abort_shard in 1 2 3 4; do
    FIXTURE_GENERATOR_STATE="$tmp/state-$fixture_abort_shard" \
      CHANGELOG_CALLER_CONTRACT_SHARD="$fixture_abort_shard" \
      timeout 30 bash "$tmp/mutated-caller.sh" \
      >"$tmp/shard-$fixture_abort_shard.out" 2>"$tmp/shard-$fixture_abort_shard.err" &
    fixture_abort_pids+=("$!")
  done
  fixture_abort_shard=1
  for fixture_abort_pid in "${fixture_abort_pids[@]}"; do
    fixture_abort_status=0
    wait "$fixture_abort_pid" || fixture_abort_status=$?
    fixture_abort_out="$tmp/shard-$fixture_abort_shard.out"
    fixture_abort_err="$tmp/shard-$fixture_abort_shard.err"
    if [ "$fixture_abort_status" -ne 1 ] \
      || ! grep -q 'FAIL - generator failed writing .*\.github/CODEOWNERS (exit 7)' "$fixture_abort_out" \
      || grep -q 'running caller contract case' "$fixture_abort_err" \
      || grep -q 'ok   - emitted suite rejects' "$fixture_abort_out" \
      || grep -q 'All tests passed' "$fixture_abort_out"; then
      fail "shard $fixture_abort_shard did not abort on the failing CODEOWNERS generator (exit $fixture_abort_status): $(tail -n 3 "$fixture_abort_out") $(tail -n 3 "$fixture_abort_err")"
    else
      pass "shard $fixture_abort_shard aborts when CODEOWNERS generation fails (#1983)"
    fi
    fixture_abort_shard=$((fixture_abort_shard + 1))
  done
else
  fail "could not point the mutated caller at the failing generator"
fi

[ "$fails" -eq 0 ] || exit 1
echo "All tests passed."
