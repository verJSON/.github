#!/usr/bin/env bash
# The emitted contract's dependency and executable-YAML loops each check every
# path with its own check-pr. Building a repository per path repeated the same
# base commit (#1977 A-7). One base repository and a branch per path must keep
# those rejections, and the executable-YAML cases must stay inside the
# is_release_relevant_change re-pin guard.
#
# #1977 A-3: scan_retired_callers must run once up front (after validate, before
# generated-set / engine-path fixtures), not at the end of the suite.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
generator="$root/scripts/gen-changelog-caller.sh"
contract="$root/scripts/changelog.py"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

ref="$(git -C "$root" rev-parse HEAD)"
if bash "$generator" contract-test "$ref" >"$tmp/contract-test.sh" 2>"$tmp/gen.err"; then
  pass "contract-test mode emits the engine-path suite"
else
  fail "contract-test mode failed: $(tr '\n' ' ' <"$tmp/gen.err" | cut -c1-500)"
  echo "$fails test(s) failed."
  exit 1
fi

if python3 - "$tmp/contract-test.sh" "$tmp" "$contract" >"$tmp/structure.out" 2>"$tmp/structure.err" <<'PY'
import pathlib
import shlex
import sys

emitted_path, out_dir, contract = sys.argv[1:]
lines = pathlib.Path(emitted_path).read_text(encoding="utf-8").splitlines()
out_dir = pathlib.Path(out_dir)

DEPENDENCY_PATHS = [
    "package.json",
    "apps/api/package-lock.json",
    "pnpm-lock.yaml",
    "web/yarn.lock",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "uv.lock",
    "services/worker/go.mod",
    "services/worker/go.sum",
    "crates/core/Cargo.toml",
    "Cargo.lock",
    "infra/.terraform.lock.hcl",
]
EXECUTABLE_PATHS = [
    ".github/workflows/ci.test.yml",
    ".github/workflows/release.spec.yaml",
    ".github/actions/test/action.yml",
    "tests/action.yml",
    "packages/foo/tests/action.yaml",
]
DEPENDENCY_REJECTION = (
    "changelog: dependency manifests or lockfiles require a new NEXT fragment: "
    "$dependency_path"
)
EXECUTABLE_REJECTION = (
    "changelog: release-relevant changes require a new NEXT fragment "
    "(all paths except changelog stores and test-only files): $executable_path"
)
GUARD = "if grep -q '^def is_release_relevant_change' \"$contract\"; then"


def die(message):
    raise SystemExit(message)


def marker_span(name):
    begin = f"# BEGIN {name}"
    end = f"# END {name}"
    begins = [index for index, line in enumerate(lines) if line == begin]
    ends = [index for index, line in enumerate(lines) if line == end]
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        die(f"{name} markers missing or unordered")
    return begins[0], ends[0], "\n".join(lines[begins[0] + 1 : ends[0]]) + "\n"


def extract_function(name):
    prefix = f"{name}() {{"
    starts = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    if len(starts) != 1:
        die(f"expected one {name} function, found {len(starts)}")
    depth = 0
    chunk = []
    for line in lines[starts[0] :]:
        chunk.append(line)
        depth += line.count("{") - line.count("}")
        if depth == 0:
            return "\n".join(chunk) + "\n"
    die(f"unbalanced function {name}")


def header_paths(section, variable):
    try:
        header = section.split(f"for {variable} in", 1)[1].split("; do", 1)[0]
    except IndexError:
        die(f"missing for {variable}")
    return header.replace("\\", " ").split()


def loop_body(section, variable):
    try:
        after = section.split(f"for {variable} in", 1)[1]
    except IndexError:
        die(f"missing for {variable}")
    _, separator, rest = after.partition("; do\n")
    if not separator:
        die(f"{variable} loop has no do")
    body, separator, _ = rest.partition("\ndone\n")
    if not separator:
        die(f"{variable} loop has no done")
    return body


def call_count(text, name):
    return sum(1 for line in text.splitlines() if line.strip() == name)


def require_shared_base(section, variable, base, paths, rejection, accepted):
    if header_paths(section, variable) != paths:
        die(f"{variable} paths changed: {header_paths(section, variable)}")
    if len(paths) != len(set(paths)):
        die(f"{variable} paths are not unique")
    body = loop_body(section, variable)
    preamble = section.split(f"for {variable} in", 1)[0]
    if call_count(preamble, "new_fixture") != 1 or call_count(preamble, "init_fixture_repo") != 1:
        die(f"{variable} setup is not one shared repository")
    if "check-pr" in preamble:
        die(f"{variable} setup runs check-pr before the per-path loop")
    if call_count(body, "new_fixture") or call_count(body, "init_fixture_repo"):
        die(f"{variable} loop still builds a repository per path")
    if any("git" in line and "init" in line for line in body.splitlines()):
        die(f"{variable} loop still runs git init")
    if body.count("check-pr") != 1:
        die(f"{variable} loop must run exactly one check-pr")
    if "continue" in body or "return" in body:
        die(f"{variable} loop can skip a path")
    checkout = {
        "dependency_path": (
            'git -C "$fixture_root/case" checkout -q -B '
            '"dependency-path-$dependency_path_index" "$dependency_base"'
        ),
        "executable_path": (
            'git -C "$fixture_root/case" checkout -q -B '
            '"executable-path-$executable_path_index" "$executable_base"'
        ),
    }[variable]
    if checkout not in body:
        die(f"{variable} loop does not check out a branch of ${base}")
    if f'--base "${base}"' not in body or '--base "$base"' in body:
        die(f"{variable} check-pr does not use the shared base")
    if "--head HEAD" not in body:
        die(f"{variable} check-pr does not use HEAD")
    if f'grep -qxF "{rejection}"' not in body:
        die(f"{variable} rejection is not the single-path diagnostic")
    if accepted not in body:
        die(f"{variable} acceptance failure no longer names the path")


def matching_fi(start):
    depth = 0
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("if "):
            depth += 1
        elif stripped == "fi":
            depth -= 1
            if depth == 0:
                return index
    die("re-pin guard has no closing fi")


def next_code(index):
    for line in lines[index + 1 :]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


early_begin, early_end, early = marker_span("early-retired-caller-scan")
if early.count("scan_retired_callers") != 1:
    die("early-retired-caller-scan must invoke scan_retired_callers exactly once")
call_sites = [
    index
    for index, line in enumerate(lines)
    if line.strip() == "scan_retired_callers"
]
if len(call_sites) != 1 or not (early_begin < call_sites[0] < early_end):
    die("scan_retired_callers must be called only inside early-retired-caller-scan")

VALIDATE_OK = 'echo "ok - canonical validation accepts this repository"'
validate_oks = [index for index, line in enumerate(lines) if line == VALIDATE_OK]
if len(validate_oks) != 1:
    die("expected one canonical-validation ok line before the early scan")
validate_ok = validate_oks[0]
if not (validate_ok < early_begin):
    die("early retired-caller scan must follow canonical validation")
between_validate = [
    line
    for line in lines[validate_ok + 1 : early_begin]
    if line.strip() and not line.lstrip().startswith("#")
]
if between_validate:
    die("early retired-caller scan must be immediately after canonical validation")

GENERATED_SET_PIN = "generated_set_header_pin="
generated_set_pins = [
    index for index, line in enumerate(lines) if line.startswith(GENERATED_SET_PIN)
]
if len(generated_set_pins) != 1:
    die("expected one generated_set_header_pin assignment")
generated_set_pin = generated_set_pins[0]
if not (early_end < generated_set_pin):
    die("early retired-caller scan must precede the generated-set body")

dependency_begin, dependency_end, dependency = marker_span(
    "engine-path-dependency-fixtures"
)
executable_begin, executable_end, executable = marker_span(
    "engine-path-executable-fixtures"
)
if not (generated_set_pin < dependency_begin < executable_begin):
    die("generated-set must precede engine-path fixtures")
require_shared_base(
    dependency,
    "dependency_path",
    "dependency_base",
    DEPENDENCY_PATHS,
    DEPENDENCY_REJECTION,
    'fail "dependency change without a fragment was accepted: $dependency_path"',
)
require_shared_base(
    executable,
    "executable_path",
    "executable_base",
    EXECUTABLE_PATHS,
    EXECUTABLE_REJECTION,
    'fail "executable GitHub YAML escaped release context: $executable_path"',
)

guards = [index for index, line in enumerate(lines) if line == GUARD]
if not guards:
    die("re-pin guard is missing")
guard_start = guards[0]
guard_end = matching_fi(guard_start)
if not (dependency_end < guard_start):
    die("dependency fixtures moved inside the re-pin guard")
if not (guard_start < executable_begin < executable_end < guard_end):
    die("executable YAML fixtures left the re-pin guard")
if next_code(executable_end) != "fi":
    die("executable YAML fixtures are not the last body of the re-pin guard")
if lines[guard_end] != "fi":
    die("re-pin guard does not close with fi")

functions = "".join(
    extract_function(name) for name in ("fail", "new_fixture", "init_fixture_repo")
)
contract_quoted = shlex.quote(contract)
for name, section in (
    ("dependency", dependency),
    ("executable", executable),
):
    runner = out_dir / f"{name}-fixtures.sh"
    runner.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"{functions}"
        f"fixture_root={shlex.quote(str(out_dir / name))}\n"
        'mkdir -p "$fixture_root"\n'
        'trap \'rm -rf "$fixture_root"\' EXIT\n'
        f"contract={contract_quoted}\n"
        f"{section}",
        encoding="utf-8",
    )
    runner.chmod(0o755)
print("ok structure")
PY
then
  pass "emitted loops share one base, early retired-caller scan, and the re-pin guard"
else
  fail "emitted engine-path fixture shape: $(tr '\n' ' ' <"$tmp/structure.err" | cut -c1-500)"
fi

run_fixtures() {
  local name="$1" ok_line="$2"
  local runner="$tmp/$name-fixtures.sh"
  if [ ! -x "$runner" ]; then
    fail "$name fixtures were not extracted"
    return
  fi
  if bash "$runner" >"$tmp/$name.out" 2>"$tmp/$name.err" \
    && grep -qxF "$ok_line" "$tmp/$name.out" \
    && [ ! -s "$tmp/$name.err" ]; then
    pass "$name fixtures reject each path alone from one base repository"
  else
    fail "$name fixtures failed: $(tr '\n' ' ' <"$tmp/$name.err" | cut -c1-400) $(tr '\n' ' ' <"$tmp/$name.out" | cut -c1-200)"
  fi
}

# The structural check writes these runners only when the emitted shape matches.
# A pre-change suite has no markers, so there is nothing faithful to execute.
if [ "$fails" -eq 0 ]; then
  run_fixtures dependency \
    "ok - dependency manifests and lockfiles require a new fragment for every actor (#524)"
  run_fixtures executable \
    "ok - executable GitHub YAML takes precedence over test exemptions"
fi

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
