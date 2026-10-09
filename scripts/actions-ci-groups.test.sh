#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
workflow="$root/.github/workflows/actions-ci.yml"
runner="$root/scripts/actions-ci-group.sh"
manifest="$root/scripts/actions-ci-groups.tsv"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

if python3 - "$workflow" "$manifest" <<'PY'
import copy
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    document = yaml.safe_load(stream)
manifest_text = open(sys.argv[2], encoding="utf-8").read()
jobs = document["jobs"]
assert set(jobs) == {
    "change-scope",
    "shell-test-groups",
    "docs-contracts",
    "hosted-compatibility-tests",
    "adr-number-collision",
    "shell-tests",
}
assert jobs["shell-test-groups"]["needs"] == "change-scope"
assert jobs["shell-test-groups"]["if"] == "needs.change-scope.outputs.heavy == 'true'"
assert jobs["hosted-compatibility-tests"]["needs"] == "change-scope"
assert jobs["hosted-compatibility-tests"]["if"] == "needs.change-scope.outputs.heavy == 'true'"
assert jobs["docs-contracts"]["timeout-minutes"] == 10
assert jobs["change-scope"]["outputs"]["heavy"] == "${{ steps.scope.outputs.heavy }}"

groups = jobs["shell-test-groups"]
assert groups["timeout-minutes"] == 30
assert groups["strategy"] == {
    "fail-fast": False,
    "max-parallel": 3,
    "matrix": {"group": ["platform", "merge-gate", "changelog-release"]},
}
checkout = next(step for step in groups["steps"] if "uses" in step)
assert checkout["with"]["path"] == (
    ".actions-ci-source-${{ github.run_id }}-"
    "${{ github.run_attempt }}-${{ matrix.group }}"
)
setup_python = next(
    step
    for step in groups["steps"]
    if step.get("name") == "Provision actions CI Python"
)
assert setup_python == {
    "name": "Provision actions CI Python",
    "uses": "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
    "with": {"python-version": "3.12.14"},
}
install_python_dependencies = next(
    step
    for step in groups["steps"]
    if step.get("name") == "Install actions CI Python dependencies"
)
assert install_python_dependencies == {
    "name": "Install actions CI Python dependencies",
    "working-directory": (
        ".actions-ci-source-${{ github.run_id }}-"
        "${{ github.run_attempt }}-${{ matrix.group }}"
    ),
    "run": (
        "python3 -m pip install \\\n"
        "  --disable-pip-version-check \\\n"
        "  --no-input \\\n"
        "  --quiet \\\n"
        "  --no-deps \\\n"
        "  --only-binary=:all: \\\n"
        "  --require-hashes \\\n"
        "  --requirement scripts/actions-ci-python.requirements.txt\n"
    ),
}
assert groups["steps"].index(install_python_dependencies) == (
    groups["steps"].index(setup_python) + 1
)
group_step = next(
    step for step in groups["steps"]
    if step.get("name") == "Run ${{ matrix.group }} shell contracts without hiding sibling failures"
)
assert group_step["env"] == {"RUNNER_LABELS": ""}
assert group_step["run"] == (
    'source_root="$GITHUB_WORKSPACE/.actions-ci-source-${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.group }}"\n'
    'group_root="$(mktemp -d "$RUNNER_TEMP/actions-ci-${{ matrix.group }}.XXXXXX")"\n'
    'trap \'rm -rf "$source_root" "$group_root"\' EXIT\n'
    'cp -a "$source_root/." "$group_root/"\n'
    'rm -rf "$source_root"\n'
    'cd "$group_root"\n'
    'bash scripts/actions-ci-group.sh "${{ matrix.group }}"\n'
)

compatibility_commands = (
    "bash scripts/ci-gate/node-ci-secretless-compatibility.test.sh",
    "scripts/ci-gate/node-ci-secretless-compatibility-absent.test.py",
    "python3 scripts/ci-gate/node-ci-required-identity.test.py",
    "python3 scripts/container_release_reconcile.test.py",
)

def validate_hosted_compatibility(candidate, candidate_manifest):
    candidate_jobs = candidate["jobs"]
    if "hosted-compatibility-tests" not in candidate_jobs:
        raise AssertionError("missing hosted-compatibility-tests job")
    hosted = candidate_jobs["hosted-compatibility-tests"]
    assert hosted["runs-on"] == "ubuntu-24.04"
    assert hosted["timeout-minutes"] == 8
    assert hosted["steps"][0]["uses"] == (
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    assert hosted["steps"][1] == {
        "name": "Provision actions CI Python",
        "uses": "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        "with": {"python-version": "3.12.14"},
    }
    execution = next(
        step for step in hosted["steps"]
        if step.get("name") == "Run namespace-bound compatibility contracts"
    )
    assert execution["env"] == {"RUNNER_LABELS": ""}
    assert execution["run"] == "\n".join((*compatibility_commands, ""))
    for command in compatibility_commands:
        assert execution["run"].splitlines().count(command) == 1
        assert not any(
            line.endswith("\t" + command)
            for line in candidate_manifest.splitlines()
        )
    aggregate = candidate_jobs["shell-tests"]
    assert "hosted-compatibility-tests" in aggregate["needs"]
    assert aggregate["steps"][0]["env"]["COMPATIBILITY_RESULT"] == (
        "${{ needs.hosted-compatibility-tests.result }}"
    )
    assert 'if [ "$COMPATIBILITY_RESULT" != "success" ]; then' in (
        aggregate["steps"][0]["run"]
    )

validate_hosted_compatibility(document, manifest_text)

def validate_shellcheck_trigger_scope(candidate):
    events = candidate.get("on", candidate.get(True))
    assert isinstance(events, dict)
    for event in ("pull_request", "push"):
        paths = events[event]["paths"]
        assert paths.count("**/*.sh") == 1

validate_shellcheck_trigger_scope(document)
for event in ("pull_request", "push"):
    mutant = copy.deepcopy(document)
    events = mutant.get("on", mutant.get(True))
    events[event]["paths"].remove("**/*.sh")
    try:
        validate_shellcheck_trigger_scope(mutant)
    except (AssertionError, KeyError, TypeError, ValueError):
        print(f"ok - {event} shellcheck path removal fails closed")
    else:
        raise AssertionError(f"{event} shellcheck path removal passed")

missing_job_mutant = copy.deepcopy(document)
del missing_job_mutant["jobs"]["hosted-compatibility-tests"]
try:
    validate_hosted_compatibility(missing_job_mutant, manifest_text)
except AssertionError as error:
    assert str(error) == "missing hosted-compatibility-tests job"
    print("ok - entire hosted compatibility job removal fails for missing job")
else:
    raise AssertionError("missing hosted compatibility job passed")

for command in compatibility_commands:
    mutant = copy.deepcopy(document)
    execution = next(
        step for step in mutant["jobs"]["hosted-compatibility-tests"]["steps"]
        if step.get("name") == "Run namespace-bound compatibility contracts"
    )
    execution["run"] = execution["run"].replace(command + "\n", "", 1)
    try:
        validate_hosted_compatibility(mutant, manifest_text)
    except (AssertionError, KeyError, StopIteration):
        print(f"ok - hosted compatibility removal fails closed: {command}")
    else:
        raise AssertionError(f"hosted compatibility removal passed: {command}")

    try:
        validate_hosted_compatibility(
            document,
            manifest_text + f"platform\t{command}\n",
        )
    except (AssertionError, KeyError, StopIteration):
        print(f"ok - persistent platform reassignment fails closed: {command}")
    else:
        raise AssertionError(f"persistent platform reassignment passed: {command}")

runner_mutant = copy.deepcopy(document)
runner_mutant["jobs"]["hosted-compatibility-tests"]["runs-on"] = (
    document["jobs"]["shell-test-groups"]["runs-on"]
)
try:
    validate_hosted_compatibility(runner_mutant, manifest_text)
except (AssertionError, KeyError, StopIteration):
    print("ok - hosted compatibility runner drift fails closed")
else:
    raise AssertionError("hosted compatibility runner drift passed")

aggregate_mutant = copy.deepcopy(document)
aggregate_mutant["jobs"]["shell-tests"]["needs"].remove(
    "hosted-compatibility-tests"
)
try:
    validate_hosted_compatibility(aggregate_mutant, manifest_text)
except (AssertionError, KeyError, StopIteration):
    print("ok - hosted compatibility aggregate removal fails closed")
else:
    raise AssertionError("hosted compatibility aggregate removal passed")

required = jobs["shell-tests"]
assert required["needs"] == [
    "change-scope",
    "shell-test-groups",
    "docs-contracts",
    "hosted-compatibility-tests",
    "adr-number-collision",
]
assert required["if"] == "${{ always() }}"
assert required["timeout-minutes"] == 2
assert "strategy" not in required
assert required["steps"][0]["env"] == {
    "SCOPE_RESULT": "${{ needs.change-scope.result }}",
    "HEAVY": "${{ needs.change-scope.outputs.heavy }}",
    "GROUP_RESULT": "${{ needs.shell-test-groups.result }}",
    "DOCS_RESULT": "${{ needs.docs-contracts.result }}",
    "COMPATIBILITY_RESULT": "${{ needs.hosted-compatibility-tests.result }}",
    "COLLISION_RESULT": "${{ needs.adr-number-collision.result }}",
}
assert required["steps"][0]["run"] == (
    'if [ "$SCOPE_RESULT" != "success" ]; then\n'
    '  echo "::error::changed-path classification failed"\n'
    "  exit 1\n"
    "fi\n"
    'if [ "$DOCS_RESULT" != "success" ]; then\n'
    '  echo "::error::documentation shell contracts failed"\n'
    "  exit 1\n"
    "fi\n"
    'if [ "$HEAVY" = true ]; then\n'
    '  if [ "$GROUP_RESULT" != "success" ]; then\n'
    '    echo "::error::one or more shell-test groups failed"\n'
    "    exit 1\n"
    "  fi\n"
    'elif [ "$HEAVY" = false ]; then\n'
    '  if [ "$GROUP_RESULT" != "skipped" ]; then\n'
    '    echo "::error::documentation diff still ran the heavy shell-test groups"\n'
    "    exit 1\n"
    "  fi\n"
    "else\n"
    '  echo "::error::changed-path classification did not report heavy=true or heavy=false"\n'
    "  exit 1\n"
    "fi\n"
    'if [ "$HEAVY" = true ]; then\n'
    '  if [ "$COMPATIBILITY_RESULT" != "success" ]; then\n'
    '    echo "::error::hosted compatibility contracts failed"\n'
    "    exit 1\n"
    "  fi\n"
    'elif [ "$COMPATIBILITY_RESULT" != "skipped" ]; then\n'
    '  echo "::error::documentation diff still ran hosted compatibility contracts"\n'
    "  exit 1\n"
    "fi\n"
    '# adr-number-collision only runs on pull_request (needs live PR\n'
    "# state), so 'skipped' -- e.g. on a push to main -- is not a failure.\n"
    'if [ "$COLLISION_RESULT" != "success" ] && [ "$COLLISION_RESULT" != "skipped" ]; then\n'
    '  echo "::error::ADR number collision check failed"\n'
    "  exit 1\n"
    "fi\n"
)

adr_collision = jobs["adr-number-collision"]
assert adr_collision["if"] == "github.event_name == 'pull_request'"
assert groups["runs-on"] == required["runs-on"]
PY
then
  pass "bounded groups, hosted compatibility, and required shell-tests aggregation stay bound"
else
  fail "bounded fan-out, hosted compatibility, or required aggregation is missing"
fi

cat >"$tmp/python-without-pip" <<'SH'
#!/usr/bin/env bash
if [ "${1-}" = -m ] && [ "${2-}" = pip ]; then
  exit 1
fi
exec python3 "$@"
SH
chmod +x "$tmp/python-without-pip"

if CHANGELOG_SCHEMA_TEST_PYTHON="$tmp/python-without-pip" \
  bash "$root/scripts/changelog-fragment-schema.test.sh" \
  >"$tmp/schema-without-pip.out" 2>&1; then
  fail "schema validator runner accepted an interpreter without pip"
elif grep -qF 'pip is required; actions-ci must provision it with setup-python' \
  "$tmp/schema-without-pip.out"; then
  pass "schema validator runner fails clearly when setup-python acquisition is absent"
else
  fail "schema validator runner failed without actionable missing-pip evidence"
fi

mkdir -p "$tmp/workspace/.actions-ci-source-42-1-platform/scripts" \
  "$tmp/workspace/.actions-ci-source-42-1-merge-gate/scripts" \
  "$tmp/runner/actions-ci-platform.stale"
printf 'stale\n' > "$tmp/runner/actions-ci-platform.stale/stale-fixture"
for group in platform merge-gate; do
  printf 'original\n' > "$tmp/workspace/.actions-ci-source-42-1-$group/shared-fixture"
done
cat > "$tmp/group-runner" <<'SH'
#!/usr/bin/env bash
[ ! -e stale-fixture ]
printf 'mutated\n' > shared-fixture
pwd > "$ISOLATION_PROBE"
[ -x scripts/actions-ci-group.sh ]
SH
chmod +x "$tmp/group-runner"
cp "$tmp/group-runner" "$tmp/workspace/.actions-ci-source-42-1-platform/scripts/actions-ci-group.sh"
cp "$tmp/group-runner" "$tmp/workspace/.actions-ci-source-42-1-merge-gate/scripts/actions-ci-group.sh"
mkdir -p "$tmp/shared-source/scripts" "$tmp/legacy-copy"
cp "$tmp/group-runner" "$tmp/shared-source/scripts/actions-ci-group.sh"
rm -rf "$tmp/shared-source/scripts"
cp -a "$tmp/shared-source/." "$tmp/legacy-copy/"
if [ ! -e "$tmp/legacy-copy/scripts/actions-ci-group.sh" ]; then
  pass "shared-source cleanup reproduces the missing actions-ci group runner from issue 675"
else
  fail "the issue 675 shared-source failure fixture did not reproduce"
fi
python3 - "$workflow" "$tmp/run-platform.sh" platform <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    document = yaml.safe_load(stream)
step = next(
    step for step in document["jobs"]["shell-test-groups"]["steps"]
    if step.get("name") == "Run ${{ matrix.group }} shell contracts without hiding sibling failures"
)
with open(sys.argv[2], "w", encoding="utf-8") as stream:
    stream.write(
        step["run"]
        .replace("${{ github.run_id }}", "42")
        .replace("${{ github.run_attempt }}", "1")
        .replace("${{ matrix.group }}", sys.argv[3])
    )
PY
sed 's/platform/merge-gate/g' \
  "$tmp/run-platform.sh" > "$tmp/run-merge-gate.sh"
if GITHUB_WORKSPACE="$tmp/workspace" RUNNER_TEMP="$tmp/runner" \
  ISOLATION_PROBE="$tmp/probe-one" bash "$tmp/run-platform.sh" &
then platform_pid=$!; else platform_pid=''; fi
if GITHUB_WORKSPACE="$tmp/workspace" RUNNER_TEMP="$tmp/runner" \
  ISOLATION_PROBE="$tmp/probe-two" bash "$tmp/run-merge-gate.sh" &
then merge_gate_pid=$!; else merge_gate_pid=''; fi
if [ -n "$platform_pid" ] && [ -n "$merge_gate_pid" ] \
  && wait "$platform_pid" && wait "$merge_gate_pid" \
  && [ "$(cat "$tmp/probe-one")" != "$(cat "$tmp/probe-two")" ] \
  && [ ! -e "$(cat "$tmp/probe-one")" ] \
  && [ ! -e "$(cat "$tmp/probe-two")" ] \
  && [ ! -e "$tmp/workspace/.actions-ci-source-42-1-platform" ] \
  && [ ! -e "$tmp/workspace/.actions-ci-source-42-1-merge-gate" ] \
  && [ "$(cat "$tmp/runner/actions-ci-platform.stale/stale-fixture")" = stale ]; then
  pass "concurrent matrix groups snapshot unique sources before cleanup and cannot mutate siblings"
else
  fail "matrix groups can lose their runner during setup, collide, retain stale files, or mutate siblings"
fi

validate_manifest() {
  local candidate="$1"

  awk -F '\t' '
    BEGIN { valid = 1 }
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    NF != 2 { valid = 0; next }
    $1 !~ /^(platform|merge-gate|changelog-release|docs)$/ { valid = 0; next }
    seen[$2]++ { valid = 0 }
    { groups[$1]++; total++ }
    END {
      if (!(total >= 60 &&
        groups["platform"] > 0 &&
        groups["merge-gate"] > 0 &&
        groups["changelog-release"] > 0 &&
        groups["docs"] > 0)) {
        valid = 0
      }
      exit valid ? 0 : 1
    }
  ' "$candidate" || return 1

  while IFS=$'\t' read -r expected_group command; do
    [ "$(awk -F '\t' -v expected_group="$expected_group" -v command="$command" '
      $1 == expected_group && $2 == command { count++ }
      END { print count + 0 }
    ' "$candidate")" -eq 1 ] || return 1
  done <<'LOAD_BEARING_COMMANDS'
merge-gate	python3 scripts/ci-gate/event-driven-authorization.test.py
merge-gate	bash scripts/ci-gate/arm-receipt.test.sh
merge-gate	bash scripts/ci-gate/gate-hold-disable.test.sh
merge-gate	bash scripts/ci-gate/native-automerge.test.sh
merge-gate	bash scripts/ci-gate/privileged-merge-pin.test.sh
changelog-release	bash scripts/ci-gate/changelog-caller-contract.test.sh
platform	bash scripts/runner-selector-health.test.sh
docs	python3 scripts/changelog.py validate --repo-root .
docs	bash scripts/changelog-fragment-schema.test.sh
changelog-release	python3 scripts/v1-readiness-contract.test.py
platform	bash scripts/actions-ci-python-dependencies.test.sh
platform	bash scripts/shellcheck-tracked.test.sh
platform	bash scripts/shellcheck-tracked.sh
platform	bash scripts/ci-gate/hub-changelog-validate.sh
LOAD_BEARING_COMMANDS
}

if validate_manifest "$manifest"; then
  pass "manifest assigns every command once across four non-empty cohesive groups"
else
  fail "manifest is missing, malformed, duplicated, or incompletely grouped"
fi

# A gate that is registered nowhere does not run in Actions, and nothing says so:
# it keeps passing locally, keeps looking like coverage in the tree, and silently
# rots. Nine tests did (#1320) -- the topology they extracted was removed by ADR
# 0079/0081, and the deregistration that removed them from this manifest left the
# files behind for a month before anyone ran them.
#
# THE RULE (#1450). Until now this check found its candidates by the `*.test.sh`
# suffix, so `hub-changelog-validate.sh` -- a gate that is not a test -- was
# invisible to it and had to pin its own registration from inside its test file.
# That does not generalize: the next non-test gate added without its own pin is
# unprotected again and nothing signals it. So the candidate set is now stated,
# not inferred from a filename:
#
#   Every tracked `*.sh` or `*.py` file under `scripts/ci-gate/` is a gate script
#   and must be reachable in Actions, UNLESS it is declared below as a library
#   module. "Reachable in Actions" means named as a command argument in
#   `scripts/actions-ci-groups.tsv`, or in the hosted-compatibility job's run
#   step, or named anywhere in a tracked workflow or composite action under
#   `.github/` -- each of which is an execution path Actions actually takes.
#
# Adding a file under `scripts/ci-gate/` therefore has exactly two honest
# outcomes: register it somewhere Actions runs it, or declare it here as a
# library with a reason. Nothing is exempt by virtue of what it is called.
#
# What the declaration list can and cannot do: a declaration that goes stale
# reddens below, so the list cannot decay silently. It cannot tell a genuine
# library from a real gate parked here to silence it -- that is not a computable
# property. Declaring a gate here passes. Reviewing a diff to this list is the
# control for that, which is why the list is here and not in a data file.
#
# The hosted-compatibility route is not new policy: that job is already the
# authoritative second execution path, and this file already asserts that its
# three commands are absent from the manifest.
#
# Deliberate residual, stated rather than papered over: this proves a gate is
# *named* on an Actions execution path, not that the path executes. A script
# named only by a workflow whose triggers never fire, or guarded by an `if:` that
# is never true, still counts as reachable here -- as does one named only by a
# `sparse-checkout:` entry or an `env:` value while its invocation is deleted,
# since the name is matched anywhere in the workflow text and those shapes are
# real in this tree. Reachability of workflows themselves is a separate
# invariant and a separate check.
if python3 - "$root" "$manifest" "$workflow" <<'PY'
import ast
import pathlib
import shlex
import subprocess
import sys

import yaml

root = pathlib.Path(sys.argv[1])
manifest_text = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
document = yaml.safe_load(pathlib.Path(sys.argv[3]).read_text(encoding="utf-8"))

# Declared library modules: imported by a ci-gate script, never invoked as a
# command of their own, so no manifest row could run them. Each entry carries the
# reason it is not a gate; adding one is a reviewable act, not a naming accident.
NON_GATE_MODULES = {
    "scripts/ci-gate/conformance/adopter.py": (
        "adopter fixture loader imported by conformance.test.py"
    ),
    "scripts/ci-gate/conformance/contract_steps.py": (
        "contract step library imported by conformance.test.py"
    ),
    "scripts/ci-gate/conformance/expressions.py": (
        "expression evaluator imported by the conformance modules"
    ),
    "scripts/ci-gate/conformance/model.py": (
        "workflow model imported by the conformance modules"
    ),
}


def tracked(*pathspecs):
    """The index is what Actions checks out, so enumerate it -- not the filesystem."""
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", *pathspecs],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "git ls-files failed, so the gate-script inventory could not be read: "
            + result.stderr.strip()
        )
    return sorted(entry for entry in result.stdout.split("\0") if entry)


hosted_run = next(
    (
        step["run"]
        for step in document["jobs"]["hosted-compatibility-tests"]["steps"]
        if step.get("name") == "Run namespace-bound compatibility contracts"
    ),
    None,
)
if hosted_run is None:
    raise SystemExit(
        "the hosted-compatibility execution step is missing, so the exempt second "
        "execution path cannot be read"
    )

gate_scripts = [
    path
    for path in tracked("scripts/ci-gate")
    if path.endswith((".sh", ".py"))
]
if not gate_scripts:
    raise SystemExit(
        "discovered no ci-gate scripts; the pathspec or repository root is wrong"
    )


def referenced(text):
    """Tracked ci-gate paths mentioned by shell tokens on an execution path."""
    logical_text = text.replace("\\\r\n", "").replace("\\\n", "")
    lexer = shlex.shlex(logical_text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError as error:
        mentioned = sorted(path for path in gate_scripts if path in logical_text)
        if not mentioned:
            return set()
        evidence = ", ".join(mentioned)
        raise SystemExit(
            f"shell source could not be parsed while checking ci-gate paths; "
            f"mentioned {evidence}: {error}"
        ) from error

    seen = set()
    for token in tokens:
        # Exact, ./, trusted-checkout, workspace-variable, punctuation, and
        # unsupported wrapper forms all retain the repository-relative path.
        # Counting every such mention fails closed instead of letting shell
        # spelling turn a declared library into an unregistered gate.
        seen.update(path for path in gate_scripts if path in token)
    return seen


def workflow_source(path):
    """A tracked path missing from the worktree is a stated failure, not a traceback."""
    try:
        return (root / path).read_text(encoding="utf-8")
    except OSError as error:
        raise SystemExit(
            f"{path} is tracked but could not be read, so gate-script reachability "
            f"could not be established: {error}"
        ) from error


workflow_sources = {
    path: workflow_source(path)
    for path in tracked(".github/workflows", ".github/actions")
    if path.endswith((".yml", ".yaml"))
}
workflow_text = "\n".join(workflow_sources.values())


exact_commands = referenced(manifest_text) | referenced(hosted_run)


def run_blocks(value):
    """Yield only shell execution blocks from workflow and action documents."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "run" and isinstance(child, str):
                yield child
            else:
                yield from run_blocks(child)
    elif isinstance(value, list):
        for child in value:
            yield from run_blocks(child)


def invocation_paths(sources):
    invoked = set(exact_commands)
    for source in sources.values():
        for run_block in run_blocks(yaml.safe_load(source)):
            invoked.update(referenced(run_block))
    return invoked


invoked_paths = invocation_paths(workflow_sources)


CI_GATE_ROOT = pathlib.PurePosixPath("scripts/ci-gate")
python_sources = {
    path: workflow_source(path)
    for path in gate_scripts
    if path.endswith(".py")
}


def import_roots(importer, node):
    """Resolve an import from the ci-gate root or the importer's package."""
    importer_parent = pathlib.PurePosixPath(importer).parent
    if isinstance(node, ast.ImportFrom) and node.level:
        package = importer_parent
        for _ in range(node.level - 1):
            package = package.parent
        return (package,)
    return (CI_GATE_ROOT, importer_parent)


def module_path(root_path, module):
    if not module:
        return root_path
    return root_path.joinpath(*module.split("."))


def resolve_import_reference(importer, reference, roots, tracked_python):
    candidates = set()
    for root_path in roots:
        base = module_path(root_path, reference)
        candidates.add(str(base.with_suffix(".py")))
        candidates.add(str(base / "__init__.py"))
    matches = sorted(candidates & tracked_python)
    if len(matches) > 1:
        raise SystemExit(
            f"{importer} import {reference!r} ambiguously resolves to tracked "
            f"ci-gate paths: {', '.join(matches)}"
        )
    return matches


def namespace_roots(reference, roots, tracked_python):
    packages = []
    for root_path in roots:
        base = module_path(root_path, reference)
        prefix = f"{base}/"
        if any(path.startswith(prefix) for path in tracked_python):
            packages.append(root_path)
    return packages


def imported_paths(importer, sources):
    """Return exact tracked Python paths statically imported by one script."""
    try:
        tree = ast.parse(sources[importer], filename=importer)
    except (KeyError, SyntaxError) as error:
        raise SystemExit(
            f"{importer} could not be parsed while checking non-gate imports: {error}"
        ) from error

    tracked_python = set(sources)
    resolved = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = import_roots(importer, node)
            for alias in node.names:
                resolved.update(
                    resolve_import_reference(
                        importer, alias.name, roots, tracked_python
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            roots = import_roots(importer, node)
            base_matches = (
                resolve_import_reference(importer, module, roots, tracked_python)
                if module
                else []
            )
            resolved.update(base_matches)

            # `from module import Name` reads an attribute when module.py is
            # the resolved base. Alias submodules are possible only from a
            # regular package (__init__.py), namespace package, or explicit
            # relative package.
            if base_matches and not all(
                match.endswith("/__init__.py") for match in base_matches
            ):
                continue
            package_roots = (
                list(roots)
                if not module
                else namespace_roots(module, roots, tracked_python)
            )
            for alias in node.names:
                if alias.name == "*":
                    continue
                reference = ".".join(
                    part for part in (module, alias.name) if part
                )
                resolved.update(
                    resolve_import_reference(
                        importer, reference, package_roots, tracked_python
                    )
                )
        else:
            continue
    return resolved


def importers_of(path, sources=python_sources):
    return [
        importer
        for importer in sources
        if importer != path and path in imported_paths(importer, sources)
    ]


def is_reachable(path):
    # The manifest and the hosted step are executed verbatim, so match their
    # command arguments exactly. A workflow may interpolate a path into a longer
    # expression, so accept it being named anywhere in one.
    return path in exact_commands or path in workflow_text


def unaccounted(candidate_paths):
    return [
        path
        for path in candidate_paths
        if path not in NON_GATE_MODULES and not is_reachable(path)
    ]


orphaned = unaccounted(gate_scripts)
if orphaned:
    raise SystemExit(
        "ci-gate scripts run nowhere in Actions -- they are named in neither the "
        "actions-ci manifest, the hosted-compatibility job, nor any workflow, and "
        "are not declared library modules. See ADR 0193 and NON_GATE_MODULES in "
        "scripts/actions-ci-groups.test.sh:\n  "
        + "\n  ".join(orphaned)
    )

DECLARATION_GUIDANCE = (
    "See ADR 0193 and NON_GATE_MODULES in scripts/actions-ci-groups.test.sh."
)


def declaration_error(path, reason, *, importers, invoked, tracked_paths=gate_scripts):
    if path not in tracked_paths:
        return (
            f"declared non-gate module is not a tracked ci-gate script: {path} "
            f"({reason}). {DECLARATION_GUIDANCE}"
        )
    if not importers:
        return (
            f"declared non-gate module is not imported by any tracked ci-gate "
            f"script: {path} ({reason}). {DECLARATION_GUIDANCE}"
        )
    if invoked:
        return (
            f"declared non-gate module is invoked as a command on a tracked "
            f"execution path: {path} ({reason}) -- delete the declaration or the "
            f"invocation. {DECLARATION_GUIDANCE}"
        )
    return None


# A declaration is a checkable claim: the path remains tracked, another tracked
# ci-gate script imports it, and no tracked Actions execution block invokes it.
def declaration_errors(
    *,
    workflows=workflow_sources,
    sources=python_sources,
    declarations=NON_GATE_MODULES,
    tracked_paths=gate_scripts,
):
    invoked = invocation_paths(workflows)
    errors = {}
    for path, reason in sorted(declarations.items()):
        error = declaration_error(
            path,
            reason,
            importers=importers_of(path, sources),
            invoked=path in invoked,
            tracked_paths=tracked_paths,
        )
        if error:
            errors[path] = error
    return errors


current_errors = declaration_errors()
if current_errors:
    raise SystemExit(next(iter(current_errors.values())))


def assert_mutation_rejected(
    name,
    *,
    mutation_module="scripts/ci-gate/conformance/adopter.py",
    workflows=workflow_sources,
    sources=python_sources,
    declarations=NON_GATE_MODULES,
    tracked_paths=gate_scripts,
):
    try:
        error = declaration_errors(
            workflows=workflows,
            sources=sources,
            declarations=declarations,
            tracked_paths=tracked_paths,
        ).get(mutation_module)
    except SystemExit as failure:
        error = str(failure)
    if not error or mutation_module not in error:
        raise SystemExit(
            f"{name} mutation did not fail closed naming {mutation_module}"
        )


# End-to-end source mutations prove discovery, parsing, normalization, and the
# declaration decision fail closed together. Replacing the real sibling import
# with a same-basename unrelated module also controls the collision that a
# suffix-only resolver would incorrectly accept.
mutation_importer = "scripts/ci-gate/conformance/conformance.test.py"
real_import = "from adopter import ADOPTERS, AdopterContractMismatch, bind_inputs, callers_for"
collision_import = real_import.replace("from adopter", "from unrelated.adopter")
if real_import not in python_sources[mutation_importer]:
    raise SystemExit("non-gate import mutation fixture no longer matches its source")
unimported_sources = dict(python_sources)
unimported_sources[mutation_importer] = python_sources[mutation_importer].replace(
    real_import, collision_import, 1
)
assert_mutation_rejected("declared-but-unimported-basename-collision", sources=unimported_sources)

ambiguous_sources = dict(python_sources)
ambiguous_sources["scripts/ci-gate/adopter.py"] = "# duplicate-basename mutation fixture\n"
assert_mutation_rejected("ambiguous-basename", sources=ambiguous_sources)

relative_collision = "scripts/ci-gate/conformance.py"
relative_sources = dict(python_sources)
relative_sources[relative_collision] = "# relative-import collision fixture\n"
relative_sources["scripts/ci-gate/conformance/relative-import.test.py"] = (
    "from . import adopter\n"
)
assert_mutation_rejected(
    "relative-package-file-collision",
    mutation_module=relative_collision,
    sources=relative_sources,
    declarations={relative_collision: "relative import collision mutation"},
    tracked_paths=[*gate_scripts, relative_collision],
)

attribute_collision = "scripts/ci-gate/collision_model/Scenario.py"
attribute_sources = dict(python_sources)
attribute_sources["scripts/ci-gate/collision_model.py"] = "Scenario = object()\n"
attribute_sources[attribute_collision] = "# attribute collision fixture\n"
attribute_sources["scripts/ci-gate/attribute-import.test.py"] = (
    "from collision_model import Scenario\n"
)
assert_mutation_rejected(
    "module-attribute-submodule-collision",
    mutation_module=attribute_collision,
    sources=attribute_sources,
    declarations={attribute_collision: "module attribute collision mutation"},
    tracked_paths=[
        *gate_scripts,
        "scripts/ci-gate/collision_model.py",
        attribute_collision,
        "scripts/ci-gate/attribute-import.test.py",
    ],
)

mutation_module = "scripts/ci-gate/conformance/adopter.py"
invocation_forms = {
    "relative-path": f"python3 ./{mutation_module};",
    "workspace-variable": f'python3 "$GITHUB_WORKSPACE/{mutation_module}"',
    "trusted-checkout": f"python3 .gate-trust/{mutation_module}",
    "unsupported-wrapper": f'python3 "${{TOOL:-{mutation_module}}}"',
    "line-continuation": "python3 scripts/ci-gate/conformance/adop\\\nter.py",
    "parse-error": f'python3 "{mutation_module}',
}
for name, command in invocation_forms.items():
    mutated_workflows = dict(workflow_sources)
    mutated_workflows[f".github/workflows/non-gate-{name}.yml"] = yaml.safe_dump(
        {"jobs": {"mutation": {"steps": [{"run": command}]}}}
    )
    assert_mutation_rejected(name, workflows=mutated_workflows)

# Negative controls: an empty result must mean "nothing runs nowhere", never "the
# detector cannot see it". Feed it an unregistered test and an unregistered
# non-test gate -- the #1450 case the suffix rule missed -- and require both.
probes = [
    "scripts/ci-gate/never-registered-probe.test.sh",
    "scripts/ci-gate/never-registered-probe.sh",
    "scripts/ci-gate/never-registered-probe.py",
]
if unaccounted([*gate_scripts, *probes]) != probes:
    raise SystemExit(
        "the detector does not flag every known-unregistered gate script; it must "
        "not depend on the `.test.` suffix"
    )
PY
then
  pass "every ci-gate script runs in the actions-ci manifest, the hosted compatibility job, or a workflow"
else
  fail "a ci-gate script violates ADR 0193; inspect NON_GATE_MODULES in scripts/actions-ci-groups.test.sh"
fi

for command_id in schema readiness; do
  if [ "$command_id" = schema ]; then
    command='bash scripts/changelog-fragment-schema.test.sh'
  else
    command='python3 scripts/v1-readiness-contract.test.py'
  fi

  for mutation in deletion duplication reassignment; do
    mutant="$tmp/manifest-$command_id-$mutation.tsv"
    case "$mutation" in
      deletion)
        awk -F '\t' -v command="$command" '$2 != command' "$manifest" >"$mutant"
        ;;
      duplication)
        cp "$manifest" "$mutant"
        printf 'changelog-release\t%s\n' "$command" >>"$mutant"
        ;;
      reassignment)
        awk -F '\t' -v command="$command" '
          BEGIN { OFS = "\t" }
          { $1 = ($2 == command ? "platform" : $1); print }
        ' "$manifest" >"$mutant"
        ;;
    esac

    if validate_manifest "$mutant"; then
      fail "$command_id manifest $mutation mutation passed"
    else
      pass "$command_id manifest $mutation mutation fails closed"
    fi
  done
done

cat >"$tmp/manifest.tsv" <<EOF
platform	printf 'first\n' >>'$tmp/seen'
platform	false
platform	printf 'last\n' >>'$tmp/seen'
EOF

if ACTIONS_CI_GROUP_MANIFEST="$tmp/manifest.tsv" bash "$runner" platform >"$tmp/out" 2>&1; then
  fail "group runner reported green after a member failed"
elif [ "$(cat "$tmp/seen")" = $'first\nlast' ] \
  && grep -q '1 command(s) failed' "$tmp/out"; then
  pass "group runner reports failure after executing every independent command"
else
  fail "group runner stopped early or hid its aggregate failure"
fi

if ACTIONS_CI_GROUP_MANIFEST="$tmp/manifest.tsv" bash "$runner" forged >"$tmp/invalid-out" 2>&1; then
  fail "group runner accepted an undeclared group"
elif grep -q 'unknown actions-ci group' "$tmp/invalid-out"; then
  pass "group runner rejects undeclared matrix values"
else
  fail "invalid group failed without actionable evidence"
fi

if grep -q $'^platform\tbash scripts/actions-ci-groups.test.sh$' "$manifest"; then
  pass "grouping contract runs in actions CI"
else
  fail "grouping contract is not wired into actions CI"
fi

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."
exit 1
