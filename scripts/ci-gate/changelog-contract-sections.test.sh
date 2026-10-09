#!/usr/bin/env bash
# The generated changelog contract can run one section, and the caller-contract
# map must name every section a mutation can affect. A missing map entry runs
# every section and fails this test, so it cannot silently drop assertions.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
map_file="$here/changelog-contract-sections.sh"
caller="$here/changelog-caller-contract.test.sh"
gen="$repo_root/scripts/gen-changelog-caller.sh"
manifest="$repo_root/scripts/actions-ci-groups.tsv"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

# shellcheck source=changelog-contract-sections.sh
source "$map_file"

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

python3 - "$caller" "$map_file" "$manifest" <<'PY' || fails=$((fails + 1))
import re
import subprocess
import sys

caller, map_file, manifest = sys.argv[1:]
text = open(caller, encoding="utf-8").read()
assigns = {}
for match in re.finditer(
    r'^([A-Za-z_][A-Za-z0-9_]*)=(?:"\$tmproot/([^"]+)"|\$tmproot/(\S+))',
    text,
    re.M,
):
    path = match.group(2) or match.group(3)
    assigns[match.group(1)] = path.rstrip('"').split("/")[-1]

calls = []
for match in re.finditer(r'^schedule_adopter_script "([^"]+)"', text, re.M):
    raw = match.group(1)
    if raw.startswith("$tmproot/"):
        ident = raw.split("/")[-1]
    elif raw.startswith("$"):
        ident = assigns[raw[1:]]
    else:
        raise SystemExit(f"odd schedule arg {raw}")
    calls.append(ident)
label_patterns = (
    r'^[ \t]*expect_rejection "((?:[^"\\]|\\.)*)"',
    r'^[ \t]*expect_release_mode_rejection [^\n]*\n[ \t]*"((?:[^"\\]|\\.)*)"',
    r'^[ \t]*expect_unestablished_pin "((?:[^"\\]|\\.)*)"',
)

def expand_call_label(label, start):
    """A loop interpolates the label before the helper sees it."""
    names = re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", label)
    if not names:
        return [label]
    expanded = [label]
    for name in names:
        for_loop = None
        for candidate in re.finditer(rf"\bfor {name} in ([^\n]+); do\s*$", text, re.M):
            if candidate.end() <= start:
                for_loop = candidate
        while_loop = None
        for candidate in re.finditer(
            rf"\bwhile\b[^\n]*\bread -r {name}\b[^\n]*; do\s*$",
            text,
            re.M,
        ):
            if candidate.end() <= start:
                while_loop = candidate
        if for_loop and (while_loop is None or for_loop.end() > while_loop.end()):
            values = []
            for word in for_loop.group(1).split():
                if len(word) >= 2 and word[0] == "'" and word[-1] == "'":
                    values.append(word[1:-1])
                else:
                    values.append(word)
        elif while_loop:
            body = re.search(r"done <<'EOF'\n(.*?)\nEOF", text[while_loop.end() :], re.S)
            if body is None:
                raise SystemExit(f"caller-contract label ${name} has no heredoc values: {label}")
            values = [line for line in body.group(1).splitlines() if line]
        else:
            raise SystemExit(f"caller-contract label interpolates ${name} outside a visible loop: {label}")
        expanded = [item.replace(f"${name}", value) for item in expanded for value in values]
    return expanded

for pattern in label_patterns:
    for match in re.finditer(pattern, text, re.M):
        calls.extend(expand_call_label(match.group(1), match.start()))
for match in re.finditer(r'^(?:if )?run_adopter "([^"]+)"', text, re.M):
    raw = match.group(1)
    ident = assigns[raw[1:]] if raw.startswith("$") else raw
    calls.append(ident)

if len(calls) < 100:
    raise SystemExit(f"parser saw only {len(calls)} caller-contract cases")

sections = {}
unmapped = []
for ident in dict.fromkeys(calls):
    probe = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; changelog_contract_sections_for "$2"',
            "bash",
            map_file,
            ident,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        unmapped.append(ident)
        continue
    sections[ident] = probe.stdout.strip()

if unmapped:
    raise SystemExit(
        "mutation-to-section map does not name: " + ", ".join(unmapped[:12])
    )

names = "generated-set workflow-callers release-workflows renderer fixtures".split()
covered = set()
for value in sections.values():
    covered.update(names if value == "all" else value.split())
missing = [name for name in names if name not in covered]
if missing:
    raise SystemExit("section map covers no mutation for: " + ", ".join(missing))
unknown = covered.difference(names)
if unknown:
    raise SystemExit("section map names unknown sections: " + ", ".join(sorted(unknown)))

# Every call index belongs to exactly one shard. Re-run the selector itself.
width = 4
owners = {index: [] for index in range(1, len(calls) + 1)}
for shard in range(1, width + 1):
    script = f'''
source "$MAP"
caller_case_count=0
caller_case_ran=0
CHANGELOG_CALLER_CONTRACT_SHARD={shard}
selected=""
for _ in $(seq 1 {len(calls)}); do
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
        env={"MAP": map_file, "PATH": "/usr/bin:/bin"},
    )
    if probe.returncode != 0:
        raise SystemExit(probe.stderr)
    for token in probe.stdout.split():
        owners[int(token)].append(shard)
bad = [str(index) for index, found in owners.items() if found != [((index - 1) % width) + 1]]
if bad:
    raise SystemExit("shard map is not a partition: " + ", ".join(bad[:8]))

manifest_text = open(manifest, encoding="utf-8").read().splitlines()
expected = {
    f"changelog-release-{shard}\tCHANGELOG_CALLER_CONTRACT_SHARD={shard} bash scripts/ci-gate/changelog-caller-contract.test.sh"
    for shard in range(1, 5)
}
if not expected.issubset(manifest_text):
    raise SystemExit("manifest is missing a caller-contract shard command")
if any(
    line.endswith("\tbash scripts/ci-gate/changelog-caller-contract.test.sh")
    for line in manifest_text
):
    raise SystemExit("manifest still runs the caller contract without a shard")

print(f"ok   - section map covers {len(sections)} mutations and {len(calls)} cases")
PY

sha="$(git -C "$repo_root" rev-parse HEAD)"
bash "$gen" contract-test "$sha" >"$tmp/suite.sh"
python3 - "$tmp/suite.sh" <<'PY' || fails=$((fails + 1))
import sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
section = None
ok_outside = []
completed = set()
opened = set()
for line in lines:
    if line.startswith("if contract_section_selected "):
        name = line.split()[2]
        if not name.endswith(";"):
            raise SystemExit(f"malformed section gate: {line}")
        section = name.removesuffix(";")
        opened.add(section)
        continue
    if line.startswith("contract_section_complete "):
        completed.add(line.split()[1])
        section = None
        continue
    if 'echo "ok -' in line and section is None:
        ok_outside.append(line.strip())
if ok_outside:
    raise SystemExit("assertions sit outside a contract section: " + ok_outside[0])
if opened != completed:
    raise SystemExit(f"section gates {sorted(opened)} do not match completions {sorted(completed)}")
required = {"generated-set", "workflow-callers", "release-workflows", "renderer", "fixtures"}
if opened != required:
    raise SystemExit(f"generated sections are {sorted(opened)}")
print("ok   - every generated assertion is inside a named section")
PY

selector_block() {
  sed -n '/# BEGIN CHANGELOG_CONTRACT_SECTIONS/,/# END CHANGELOG_CONTRACT_SECTIONS/p' "$tmp/suite.sh" >"$tmp/selector.sh"
}

selector_block
run_selector() {
  bash -c '
    fail() { printf "FAIL - %s\n" "$1" >&2; exit 1; }
    source "$1"
    shift
    "$@"
  ' bash "$tmp/selector.sh" "$@"
}

if run_selector true; then
  pass "default section selection sources with every section enabled"
else
  fail "default section selection rejected an unset CHANGELOG_CONTRACT_SECTIONS"
fi

default_ok=0
if CHANGELOG_CONTRACT_SECTIONS=generated-set run_selector contract_section_selected generated-set \
  && CHANGELOG_CONTRACT_SECTIONS=workflow-callers run_selector contract_section_selected workflow-callers \
  && CHANGELOG_CONTRACT_SECTIONS=release-workflows run_selector contract_section_selected release-workflows \
  && CHANGELOG_CONTRACT_SECTIONS=renderer run_selector contract_section_selected renderer \
  && CHANGELOG_CONTRACT_SECTIONS=fixtures run_selector contract_section_selected fixtures \
  && ! CHANGELOG_CONTRACT_SECTIONS=generated-set run_selector contract_section_selected fixtures; then
  default_ok=1
fi
if [ "$default_ok" -eq 1 ]; then
  pass "each contract section can be selected on its own"
else
  fail "a named contract section did not select itself or selected another section"
fi

if CHANGELOG_CONTRACT_SECTIONS=not-a-section run_selector true >"$tmp/invalid.out" 2>&1; then
  fail "an unknown contract section was accepted"
elif grep -q 'unknown changelog contract section: not-a-section' "$tmp/invalid.out"; then
  pass "an unknown contract section fails before assertions"
else
  fail "an unknown contract section failed without naming itself"
fi

if CHANGELOG_CONTRACT_SECTIONS='' run_selector true >"$tmp/empty.out" 2>&1; then
  fail "an empty contract section list was accepted"
elif grep -q 'CHANGELOG_CONTRACT_SECTIONS is empty' "$tmp/empty.out"; then
  pass "an empty contract section list fails closed"
else
  fail "an empty contract section list failed without the empty-list diagnostic"
fi

if run_selector contract_section_selected not-a-marker >"$tmp/marker.out" 2>&1; then
  fail "an unknown section marker was accepted"
elif grep -q 'unknown changelog contract section marker: not-a-marker' "$tmp/marker.out"; then
  pass "an unknown section marker fails closed"
else
  fail "an unknown section marker failed without naming itself"
fi

if CHANGELOG_CALLER_CONTRACT_SHARD=9 bash "$caller" >"$tmp/shard.out" 2>&1; then
  fail "the caller contract accepted shard 9"
elif grep -q 'CHANGELOG_CALLER_CONTRACT_SHARD must be all or 1..4' "$tmp/shard.out"; then
  pass "the caller contract rejects an unknown shard before it runs cases"
else
  fail "the caller contract rejected shard 9 without the shard diagnostic"
fi

if bash -c 'source "$1"; changelog_contract_sections_for "not-a-real-mutation"' bash "$map_file" >"$tmp/unmap.out" 2>&1; then
  fail "an unmapped mutation received a section list"
else
  pass "an unmapped mutation is rejected by the section map"
fi

[ "$fails" -eq 0 ] || exit 1
echo "All tests passed."
