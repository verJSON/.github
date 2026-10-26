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
exclusive_commands = {
    line
    for line in manifest_text.splitlines()
    if "\t@exclusive " in line
}
assert exclusive_commands == {
    "platform\t@exclusive @budget=180 bash scripts/actions-ci-groups.test.sh",
    "platform\t@exclusive @budget=120 python3 scripts/ci-gate/conformance/conformance.test.py",
    "platform\t@exclusive @budget=120 bash scripts/required-checks-audit.test.sh",
    "merge-gate\t@exclusive @budget=120 bash scripts/ci-gate/privileged-merge-conformance.test.sh",
}
jobs = document["jobs"]
assert document[True]["pull_request"]["types"] == [
    "opened", "reopened", "synchronize", "ready_for_review", "converted_to_draft",
]
assert set(jobs) == {
    "change-scope",
    "shell-test-groups",
    "docs-contracts",
    "hosted-compatibility-tests",
    "adr-number-collision",
    "runner-isolation",
    "shell-tests",
}
assert jobs["shell-test-groups"]["needs"] == ["change-scope", "docs-contracts", "runner-isolation"]
assert jobs["shell-test-groups"]["if"] == (
    "${{ !cancelled() && needs.change-scope.result == 'success' && "
    "needs.change-scope.outputs.heavy == 'true' && "
    "(github.event_name != 'pull_request' || (!github.event.pull_request.draft && "
    "needs.docs-contracts.result == 'success' && "
    "needs.runner-isolation.result == 'success')) }}"
)
assert jobs["hosted-compatibility-tests"]["needs"] == ["change-scope", "docs-contracts", "runner-isolation"]
assert jobs["hosted-compatibility-tests"]["if"] == (
    "${{ !cancelled() && needs.change-scope.result == 'success' && "
    "needs.change-scope.outputs.heavy == 'true' && "
    "(github.event_name != 'pull_request' || (!github.event.pull_request.draft && "
    "needs.docs-contracts.result == 'success' && "
    "needs.runner-isolation.result == 'success')) }}"
)
assert "if" not in jobs["change-scope"]
assert "if" not in jobs["docs-contracts"]
assert "if" not in jobs["runner-isolation"]
assert jobs["adr-number-collision"]["if"] == "github.event_name == 'pull_request'"
assert jobs["shell-tests"]["if"] == (
    "${{ always() && (github.event_name != 'pull_request' "
    "|| !github.event.pull_request.draft) }}"
)
assert jobs["docs-contracts"]["timeout-minutes"] == 10
docs_group_step = next(
    step
    for step in jobs["docs-contracts"]["steps"]
    if step.get("name") == "Run documentation shell contracts"
)
assert docs_group_step["env"] == {
    "RUNNER_LABELS": "",
    "ACTIONS_CI_COMMAND_BUDGET_SECONDS": (
        "${{ github.event_name == 'pull_request' && '60' || '' }}"
    ),
}
assert jobs["change-scope"]["outputs"]["heavy"] == "${{ steps.scope.outputs.heavy }}"
scope_run = next(
    step["run"]
    for step in jobs["change-scope"]["steps"]
    if step.get("id") == "scope"
)
assert "rm -rf \"$source_root\"" in scope_run
assert (
    ".actions-ci-source-${{ github.run_id }}-"
    "${{ github.run_attempt }}-change-scope"
) in scope_run
assert any(
    "VERJSON_CHANGELOG_TOOL_CACHE=" in step.get("run", "")
    for step in jobs["docs-contracts"]["steps"]
)

groups = jobs["shell-test-groups"]
assert groups["timeout-minutes"] == 30
assert groups["strategy"]["fail-fast"] == "${{ github.event_name == 'pull_request' }}"
assert groups["strategy"]["max-parallel"] == 6
assert groups["strategy"]["matrix"]["group"] == [
    "platform",
    "merge-gate",
    "changelog-release-1",
    "changelog-release-2",
    "changelog-release-3",
    "changelog-release-4",
]
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
        "python3 -I -m pip install \\\n"
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
assert group_step["env"] == {
    "RUNNER_LABELS": "",
    "ACTIONS_CI_SHELLCHECK_BASE_SHA": (
        "${{ github.event_name == 'pull_request' "
        "&& github.event.pull_request.base.sha || '' }}"
    ),
    "ACTIONS_CI_SHELLCHECK_HEAD_SHA": (
        "${{ github.event_name == 'pull_request' "
        "&& github.sha || '' }}"
    ),
    "ACTIONS_CI_COMMAND_BUDGET_SECONDS": (
        "${{ github.event_name == 'pull_request' && '60' || '' }}"
    ),
    # Outside the group's checkout copy, which the step removes on exit.
    "ACTIONS_CI_RUNTIME_RECORDS": (
        "${{ runner.temp }}/actions-ci-runtime-records-${{ github.run_id }}-"
        "${{ github.run_attempt }}/${{ matrix.group }}.jsonl"
    ),
}
# Runtime records (verjson-observability#271) upload from every cell even when
# its rows failed or the job was cancelled, named per cell and attempt.
records_upload = groups["steps"][groups["steps"].index(group_step) + 1]
assert records_upload == {
    "name": "Upload ${{ matrix.group }} runtime records",
    "if": "${{ always() }}",
    "uses": "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9",
    "with": {
        "name": (
            "actions-ci-runtime-records-${{ github.run_id }}-"
            "${{ github.run_attempt }}-${{ matrix.group }}"
        ),
        "path": (
            "${{ runner.temp }}/actions-ci-runtime-records-${{ github.run_id }}-"
            "${{ github.run_attempt }}/${{ matrix.group }}.jsonl"
        ),
        "retention-days": 7,
        "if-no-files-found": "warn",
    },
}, records_upload
assert groups["steps"][-1] is records_upload
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
    "python3 -I scripts/ci-gate/node-ci-secretless-compatibility-absent.test.py",
    "python3 -I scripts/ci-gate/node-ci-required-identity.test.py",
    "VERJSON_TEST_REAL_BWRAP=1 python3 -I scripts/ci-gate/ci-entry-receipts.test.py",
    "VERJSON_TEST_REAL_BWRAP=1 python3 -I scripts/ci-gate/node-ci-secretless-rebuild-env.test.py",
    "bash scripts/ci-gate/node-ci-secretless-consumer.test.sh",
    "bash scripts/ci-gate/node-ci-secretless-nested-manifests.test.sh",
    "python3 -I scripts/container_release_reconcile.test.py",
)

def validate_hosted_compatibility(candidate, candidate_manifest):
    candidate_jobs = candidate["jobs"]
    if "hosted-compatibility-tests" not in candidate_jobs:
        raise AssertionError("missing hosted-compatibility-tests job")
    hosted = candidate_jobs["hosted-compatibility-tests"]
    # continue-on-error in any spelling (true, an expression) would let the
    # bubblewrap and locale-collation contracts fail without failing the job.
    if "continue-on-error" in hosted:
        raise AssertionError("hosted-compatibility-tests job may continue on error")
    for step in hosted["steps"]:
        if "continue-on-error" in step:
            raise AssertionError(
                "hosted-compatibility-tests step may continue on error: "
                f"{step.get('name', step.get('uses'))}"
            )
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

skippable_job = copy.deepcopy(document)
skippable_job["jobs"]["hosted-compatibility-tests"]["continue-on-error"] = True
try:
    validate_hosted_compatibility(skippable_job, manifest_text)
except AssertionError:
    print("ok - continue-on-error hosted compatibility job fails closed")
else:
    raise AssertionError("continue-on-error hosted compatibility job passed")
for index, step in enumerate(document["jobs"]["hosted-compatibility-tests"]["steps"]):
    skippable_step = copy.deepcopy(document)
    skippable_step["jobs"]["hosted-compatibility-tests"]["steps"][index][
        "continue-on-error"
    ] = True
    label = step.get("name", step.get("uses", index))
    try:
        validate_hosted_compatibility(skippable_step, manifest_text)
    except AssertionError:
        print(f"ok - continue-on-error hosted compatibility step fails closed: {label}")
    else:
        raise AssertionError(f"continue-on-error hosted compatibility step passed: {label}")

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

# A manifest group that no job runs is registered coverage that never executes,
# and an en_US.UTF-8 contract on a fleet image fails closed for want of locale
# data. So every manifest group must be named by exactly one runner, and every
# contract that imports the locale helper runs in the hosted locale-collation
# group, which holds nothing else.
#
# Membership is derived from the importers, never from the rows: every tracked
# Python file a row could run (anywhere in the repository) that imports
# ascii_digit_locales -- `import`, `from ... import`, `__import__(...)` or
# `importlib.import_module(...)` -- must be exactly the set of scripts the
# locale-collation rows run. Deriving it from the rows instead (asking "does
# this row's script import the helper?") passes vacuously when a row is deleted
# or an import is respelled. A file that names the helper in a way this scan
# cannot classify fails closed. Each row's scripts are resolved as bash would
# run them -- quoted, absolute, or relative after `cd` -- and a row whose
# executed script cannot be resolved to a scanned, tracked file fails.
#
# Rows themselves are strict: a `$`, backtick, or glob anywhere in a row fails
# it. What "fails closed" does not cover is a script or module name that a
# reached file never writes out whole:
#   - a name assembled at run time from pieces --
#     `importlib.import_module("ascii_digit" + "_locales")`, `"$dir/$prefix.py"`,
#     or a name read from a file or the environment;
#   - a glob (`for test in scripts/ci-gate/*.test.py`), `find ... -exec`, or a
#     command substitution `$(...)` in a wrapper body that yields the script;
#   - JavaScript: `.js`, `.mjs`, and `.cjs` files are scanned for the module
#     name but not followed into what they run;
#   - a wrapper launched from Python: a reached Python file is checked for an
#     importer it names, but not followed into the shell scripts it runs.
# No static scan can classify these, and flagging name fragments would only
# move the gap to the next spelling. The backstop is at run time: guard_locales
# raises under CI=true
# when en_US.UTF-8 cannot be reproduced (pinned by the missing-hazard-locale
# test in settable-value-guards.test.py), so such a test on a fleet image fails
# red, not green.
import ast
from functools import lru_cache
import pathlib
import posixpath
import re
import subprocess

LOCALE_GROUP = "locale-collation"
LOCALE_STEP = "Run locale-collation shell contracts"
LOCALE_MODULE = "ascii_digit_locales"
LOCALE_HELPER = "scripts/ci-gate/ascii_digit_locales.py"
# Tracked files that name the helper without importing it.
LOCALE_NON_IMPORTERS = {
    LOCALE_HELPER,
    "scripts/actions-ci-groups.tsv",
    "scripts/actions-ci-groups.test.sh",
}
repository_root = pathlib.Path(sys.argv[2]).resolve().parent.parent
sys.path.insert(0, str(repository_root / "scripts"))
import shell_words  # noqa: E402

# A row can run any tracked file, so the scan covers every tracked source a row
# could execute -- not only scripts/ -- plus every other file under scripts/.
# A row whose script lies outside these roots fails rather than going unscanned.
SCANNED_SUFFIXES = (".py", ".sh", ".bash", ".js", ".mjs", ".cjs")


def scanned(path):
    return path.startswith("scripts/") or path.endswith(SCANNED_SUFFIXES)


def tracked_script_sources():
    listing = subprocess.run(
        ["git", "-C", str(repository_root), "ls-files", "-z"],
        capture_output=True,
        text=True,
    )
    if listing.returncode != 0:
        raise SystemExit(
            "git ls-files failed, so the locale-helper importers could not be read: "
            + listing.stderr.strip()
        )
    paths = sorted(entry for entry in listing.stdout.split("\0") if entry and scanned(entry))
    if LOCALE_HELPER not in paths:
        raise SystemExit(f"{LOCALE_HELPER} is not tracked; the scan root is wrong")
    return {
        path: (repository_root / path).read_bytes().decode("utf-8", "replace")
        for path in paths
    }


script_sources = tracked_script_sources()


def manifest_rows(text):
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        group, command = line.split("\t", 1)
        yield group, command


def names_locale_module(dotted):
    return isinstance(dotted, str) and LOCALE_MODULE in dotted.split(".")


def imports_locale_module(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(names_locale_module(alias.name) for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if names_locale_module(node.module) or any(
                alias.name == LOCALE_MODULE for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Call) and node.args:
            function = node.func
            name = (
                function.id if isinstance(function, ast.Name)
                else function.attr if isinstance(function, ast.Attribute)
                else None
            )
            argument = node.args[0]
            if (
                name in ("__import__", "import_module")
                and isinstance(argument, ast.Constant)
                and names_locale_module(argument.value)
            ):
                return True
    return False


def locale_importers(sources):
    importers = set()
    for path, text in sources.items():
        if LOCALE_MODULE not in text or path in LOCALE_NON_IMPORTERS:
            continue
        if not path.endswith(".py"):
            raise AssertionError(
                f"{path} names {LOCALE_MODULE} but is not Python, so whether it "
                "imports the locale helper cannot be classified"
            )
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError as error:
            raise AssertionError(f"{path} could not be parsed: {error}") from error
        if not imports_locale_module(tree):
            raise AssertionError(
                f"{path} names {LOCALE_MODULE} without an import this contract "
                "recognizes; spell it as an import or list it in LOCALE_NON_IMPORTERS"
            )
        importers.add(path)
    return importers


# A fleet row reaches what its script names, not only the script itself: a
# shell wrapper can exec an importer, and a Python test can run one as a
# subprocess. Shell scripts are followed through their words (comments
# excluded) into every tracked script they name, by repository path or by a
# name beside them, up to this many levels; a chain still naming new shell
# scripts there fails. A reached Python file is checked for an importer named
# in its string constants but not followed further: Python tests name most of
# the repository as data, and following them would turn the bound into a guess
# at the diameter of that graph rather than a limit on wrapper nesting.
# Shell tests name generators that name others, so real chains already run
# several levels deep; the bound leaves room for that, and termination does
# not depend on it (each file is visited once).
SHELL_FOLLOW_DEPTH = 16
# This contract names the importers as fixtures; it runs none of them.
LOCALE_REFERENCE_EXEMPT = {"scripts/actions-ci-groups.test.sh"}
PATH_FRAGMENT = re.compile(r"[A-Za-z0-9_./-]+\.(?:py|sh|bash)\b")


@lru_cache(maxsize=None)
def path_fragments(path, text):
    """Path-like fragments in the text a file could run something by."""
    return tuple(
        sorted({fragment for chunk in named_chunks(path, text) for fragment in PATH_FRAGMENT.findall(chunk)})
    )


def named_chunks(path, text):
    """Text a file could run something by: shell words, or Python string constants."""
    if path.endswith(".py"):
        try:
            tree = ast.parse(text, filename=path)
        except SyntaxError:
            return [text]
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
    try:
        return shell_words.words(text)
    except ValueError:
        return [text]


def named_sources(path, text, runnable_by_name, importers):
    """Tracked sources `path` names: by repository path, beside it, or any importer by name."""
    return sources_named_by(path, path_fragments(path, text), runnable_by_name, importers)


def sources_named_by(path, fragments, runnable_by_name, importers):
    """Tracked sources that path-like `fragments` written in `path` resolve to."""
    parent = posixpath.dirname(path)
    named = set()
    for fragment in fragments:
        for candidate in runnable_by_name.get(posixpath.basename(fragment), ()):
            if (
                candidate in importers
                or fragment == candidate
                or fragment.endswith("/" + candidate)
                or posixpath.dirname(candidate) == parent
            ):
                named.add(candidate)
    return named


def without_whole_line_comments(text):
    """Text minus lines that are only a comment.

    A line whose first word is `#` is a comment to bash, and to Python or bash
    reading it as heredoc or string data, whatever quote state the comment
    stripper believes it is in. Any other `#` the stripper drops is only as
    sound as its quote tracking, which does not model heredocs or `$'...'`.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def fleet_reached_importers(scripts, sources, importers):
    """Importers a fleet row runs directly or through what its scripts name."""
    runnable_by_name = {}
    for source in sources:
        if source.endswith((".py", ".sh", ".bash")):
            runnable_by_name.setdefault(posixpath.basename(source), []).append(source)
    reached = set()
    frontier = sorted(scripts)
    for depth in range(1, SHELL_FOLLOW_DEPTH + 2):
        following = set()
        for path in frontier:
            if path in reached:
                continue
            reached.add(path)
            if path in importers or path in LOCALE_REFERENCE_EXEMPT:
                continue
            if path.endswith((".sh", ".bash")):
                named = named_sources(path, sources[path], runnable_by_name, importers)
                dropped = sources_named_by(
                    path,
                    PATH_FRAGMENT.findall(without_whole_line_comments(sources[path])),
                    runnable_by_name,
                    importers,
                ) - named
                if dropped:
                    raise AssertionError(
                        f"{path} names {sorted(dropped)} outside a whole-line comment, "
                        "but the lexer dropped them, so what it runs cannot be classified"
                    )
                following |= named
            elif path.endswith(".py"):
                following |= importers & named_sources(
                    path, sources[path], runnable_by_name, importers
                )
        frontier = sorted(following - reached)
        if not frontier:
            return reached & importers
    raise AssertionError(
        f"following scripts {sorted(scripts)} stopped after {depth} levels, "
        f"{frontier} are still unvisited, and the bound is {SHELL_FOLLOW_DEPTH} "
        "nested wrappers, so whether they run a locale-helper importer cannot be classified"
    )


CONTROL_OPERATORS = {"&&", "||", ";", "|", "|&", "&", "(", ")"}
REDIRECTION = re.compile(r"^[0-9]*[<>&]*[<>][<>&|]*$")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
UNRESOLVABLE_CHARACTERS = set("$`*?[]{}~")
# Quoting is gone once a row is split into words, so a `$` or backtick in any
# word -- argument, assignment, or redirection target -- may run code.
RUN_TIME_EXPANSION = set("$`")
# A row runs under `bash -o pipefail -c` without `-e`: after `||`, `&`, or `;`
# a failed script no longer decides the row's exit status.
STATUS_HIDING_OPERATORS = {"||", "&", ";"}
# Interpreter options a row may pass; anything else (`-c`, `-m`, `-e`) runs code
# that is not a tracked script, so the row cannot be classified.
INTERPRETER_OPTIONS = {
    "bash": set(),
    "python3": {"-I", "-u", "-B", "-E", "-s", "-S"},
    "node": {"--test"},
}


def simple_commands(tokens):
    """Split a row's words at control operators, dropping redirections."""
    current = []
    skip_target = False
    for token in tokens:
        if skip_target:
            skip_target = False
        elif token in CONTROL_OPERATORS:
            yield current
            current = []
        elif REDIRECTION.match(token):
            skip_target = True
        else:
            current.append(token)
    yield current


def resolve_row_path(directory, word, command):
    """Repository-relative path of `word` as bash would open it from `directory`."""
    if not word or UNRESOLVABLE_CHARACTERS & set(word):
        raise AssertionError(f"row path {word!r} cannot be resolved statically: {command}")
    path = pathlib.PurePosixPath(word)
    if path.is_absolute():
        try:
            path = path.relative_to(repository_root)
        except ValueError:
            raise AssertionError(
                f"row path {word!r} lies outside the repository: {command}"
            ) from None
    else:
        path = directory / path
    normalized = posixpath.normpath(str(path))
    if normalized == ".." or normalized.startswith("../"):
        raise AssertionError(f"row path {word!r} lies outside the repository: {command}")
    return normalized


def row_scripts(command, sources):
    """Every tracked script a manifest row executes, resolved as bash would.

    Each simple command must be a `cd` or a recognized interpreter running one
    script, so a row whose executed script cannot be named is unclassifiable
    and fails rather than passing for want of a match.
    """
    body = command
    while body.startswith("@"):
        _directive, _separator, body = body.partition(" ")
    try:
        tokens = shell_words.words(body)
    except ValueError as error:
        raise AssertionError(f"row cannot be parsed: {command}: {error}") from error
    hiding = sorted(STATUS_HIDING_OPERATORS.intersection(tokens))
    if hiding:
        raise AssertionError(
            f"row operator {hiding} hides the exit status of a script it runs: {command}"
        )
    for token in tokens:
        if RUN_TIME_EXPANSION & set(token):
            raise AssertionError(
                f"row word {token!r} expands at run time, so what the row runs "
                f"cannot be named: {command}"
            )
    directory = pathlib.PurePosixPath(".")
    scripts = set()
    for words in simple_commands(tokens):
        while words and ASSIGNMENT.match(words[0]):
            words = words[1:]
        if not words:
            raise AssertionError(f"row has an empty command: {command}")
        head, arguments = words[0], words[1:]
        if head == "cd":
            if len(arguments) != 1:
                raise AssertionError(f"row cd has no single target: {command}")
            directory = pathlib.PurePosixPath(
                resolve_row_path(directory, arguments[0], command)
            )
            continue
        if head in INTERPRETER_OPTIONS:
            options = INTERPRETER_OPTIONS[head]
            while arguments and arguments[0] in options:
                arguments = arguments[1:]
            if not arguments or arguments[0].startswith("-"):
                raise AssertionError(
                    f"row runs {head} without a script this contract can name: {command}"
                )
            word = arguments[0]
        elif "/" in head:
            word = head
        else:
            raise AssertionError(
                f"row runs {head!r}, which this contract cannot resolve to a script: {command}"
            )
        script = resolve_row_path(directory, word, command)
        if script not in sources:
            raise AssertionError(
                f"row script {script} is not a tracked file in the scanned roots: {command}"
            )
        scripts.add(script)
    if not scripts:
        raise AssertionError(f"row names no script: {command}")
    return scripts


def executed_groups(candidate):
    runners = []
    for job_name, job in candidate["jobs"].items():
        matrix = job.get("strategy", {}).get("matrix", {}).get("group")
        for step in job.get("steps", []):
            run = step.get("run", "")
            if 'bash scripts/actions-ci-group.sh "${{ matrix.group }}"' in run:
                runners.extend((job_name, group) for group in matrix or [])
            runners.extend(
                (job_name, group)
                for group in re.findall(
                    r"^\s*bash scripts/actions-ci-group\.sh ([a-z0-9-]+)\s*$",
                    run,
                    re.MULTILINE,
                )
            )
    return runners


def validate_locale_collation(candidate, candidate_manifest, sources=script_sources):
    runners = executed_groups(candidate)
    named = [group for _job, group in runners]
    manifest_groups = {group for group, _command in manifest_rows(candidate_manifest)}
    if sorted(set(named)) != sorted(named):
        raise AssertionError("a manifest group is run by more than one job")
    if set(named) != manifest_groups:
        raise AssertionError(
            f"manifest groups {sorted(manifest_groups)} differ from executed groups {sorted(named)}"
        )
    if (("hosted-compatibility-tests", LOCALE_GROUP)) not in runners:
        raise AssertionError("locale-collation is not run on the hosted image")
    hosted = candidate["jobs"]["hosted-compatibility-tests"]
    if hosted["runs-on"] != "ubuntu-24.04":
        raise AssertionError("locale-collation host is not the hosted image")
    steps = [step for step in hosted["steps"] if step.get("name") == LOCALE_STEP]
    if len(steps) != 1 or steps[0] != {
        "name": LOCALE_STEP,
        "if": "${{ !cancelled() }}",
        "env": {
            "RUNNER_LABELS": "",
            "ACTIONS_CI_COMMAND_BUDGET_SECONDS": (
                "${{ github.event_name == 'pull_request' && '60' || '' }}"
            ),
        },
        "run": f"bash scripts/actions-ci-group.sh {LOCALE_GROUP}",
    }:
        raise AssertionError("hosted locale-collation step drifted")
    importers = locale_importers(sources)
    if not importers:
        raise AssertionError(f"no tracked script imports {LOCALE_MODULE}")
    locale_scripts = set()
    for group, command in manifest_rows(candidate_manifest):
        scripts = row_scripts(command, sources)
        if group == LOCALE_GROUP:
            locale_scripts |= scripts
        else:
            escaped = fleet_reached_importers(scripts, sources, importers)
            if escaped:
                raise AssertionError(
                    f"locale-helper importer {sorted(escaped)} runs outside "
                    f"locale-collation: {group}\t{command}"
                )
    if locale_scripts != importers:
        raise AssertionError(
            "locale-collation membership drifted: rows run "
            f"{sorted(locale_scripts)} but {LOCALE_MODULE} importers are "
            f"{sorted(importers)}"
        )


validate_locale_collation(document, manifest_text)
print("ok - every manifest group has one runner; en_US.UTF-8 contracts run hosted")


def expect_locale_failure(
    label, candidate, candidate_manifest, sources=script_sources, reason=None
):
    """`reason`, when given, must appear in the error, so an unrelated failure is not credit."""
    try:
        validate_locale_collation(candidate, candidate_manifest, sources)
    except (AssertionError, KeyError, ValueError) as error:
        if reason is not None and reason not in str(error):
            raise AssertionError(f"{label} failed for another reason: {error}") from error
        print(f"ok - {label} fails closed")
    else:
        raise AssertionError(f"{label} passed")


removed_step = copy.deepcopy(document)
removed_step["jobs"]["hosted-compatibility-tests"]["steps"] = [
    step for step in removed_step["jobs"]["hosted-compatibility-tests"]["steps"]
    if step.get("name") != LOCALE_STEP
]
expect_locale_failure("hosted locale-collation step removal", removed_step, manifest_text)
fleet_runner = copy.deepcopy(document)
fleet_runner["jobs"]["hosted-compatibility-tests"]["runs-on"] = (
    document["jobs"]["shell-test-groups"]["runs-on"]
)
expect_locale_failure("locale-collation fleet routing", fleet_runner, manifest_text)
wrong_group = copy.deepcopy(document)
next(
    step for step in wrong_group["jobs"]["hosted-compatibility-tests"]["steps"]
    if step.get("name") == LOCALE_STEP
)["run"] = "bash scripts/actions-ci-group.sh docs"
expect_locale_failure("locale-collation step running another group", wrong_group, manifest_text)
skippable = copy.deepcopy(document)
next(
    step for step in skippable["jobs"]["hosted-compatibility-tests"]["steps"]
    if step.get("name") == LOCALE_STEP
)["continue-on-error"] = True
expect_locale_failure("continue-on-error locale-collation step", skippable, manifest_text)
expect_locale_failure(
    "locale contract moved to a fleet group",
    document,
    manifest_text.replace(
        "locale-collation\tpython3 scripts/ci-gate/settable-value-guards.test.py",
        "merge-gate\tpython3 scripts/ci-gate/settable-value-guards.test.py",
    ),
)
expect_locale_failure(
    "non-locale contract added to locale-collation",
    document,
    manifest_text + "locale-collation\tbash scripts/actions-ci-group-output.test.sh\n",
)
expect_locale_failure(
    "manifest group without a runner",
    document,
    manifest_text + "orphan\tbash scripts/actions-ci-group-output.test.sh\n",
)

retention_script = "scripts/ci-gate/release-verify-retention-keep.test.py"
retention_row = f"{LOCALE_GROUP}\tpython3 {retention_script}\n"
retention_import = "import ascii_digit_locales  # noqa: E402\n"
if retention_row not in manifest_text or retention_import not in script_sources[retention_script]:
    raise AssertionError("locale membership mutation fixtures no longer match their sources")
expect_locale_failure(
    "locale contract row deleted",
    document,
    manifest_text.replace(retention_row, "", 1),
)
platform_manifest = manifest_text.replace(
    retention_row, f"platform\tpython3 {retention_script}\n", 1
)
respellings = {
    "from-import": "from ascii_digit_locales import guard_locales  # noqa: E402\n",
    "dunder-import": 'ascii_digit_locales = __import__("ascii_digit_locales")\n',
    "importlib": (
        "import importlib\n"
        'ascii_digit_locales = importlib.import_module("ascii_digit_locales")\n'
    ),
    "package-from-import": "from ci_gate import ascii_digit_locales\n",
}
for spelling, replacement in respellings.items():
    respelled = dict(script_sources)
    respelled[retention_script] = respelled[retention_script].replace(
        retention_import, replacement, 1
    )
    # Positive control: the respelled import is still recognized when its row
    # stays in locale-collation, so the failure below is the move, not the spelling.
    validate_locale_collation(document, manifest_text, respelled)
    expect_locale_failure(
        f"{spelling} importer moved to a fleet group", document, platform_manifest, respelled
    )
unclassifiable = dict(script_sources)
unclassifiable[retention_script] = unclassifiable[retention_script].replace(
    retention_import,
    'ascii_digit_locales = getattr(importlib, "import_module")("ascii_digit_locales")\n',
    1,
)
expect_locale_failure(
    "unclassifiable locale-helper reference", document, manifest_text, unclassifiable
)
shell_mention = dict(script_sources)
shell_mention["scripts/ci-gate/locale-probe.test.sh"] = (
    "python3 - <<'EOF'\nimport ascii_digit_locales\nEOF\n"
)
expect_locale_failure("shell-embedded locale-helper import", document, manifest_text, shell_mention)

# A row runs whatever script bash resolves, however the path is spelled. Each
# spelling is a positive control in locale-collation and a failure in a fleet
# group, so the failure is the move, not an unresolvable spelling.
retention_parent, retention_name = retention_script.rsplit("/", 1)
for spelling, command in {
    "quoted": f'python3 "{retention_script}"',
    "single-quoted": f"python3 '{retention_script}'",
    "dot-relative": f"python3 ./{retention_script}",
    "absolute": f"python3 {repository_root}/{retention_script}",
    "relative after cd": f"cd {retention_parent} && python3 {retention_name}",
    "relative after quoted cd": f'cd "{retention_parent}" && python3 ./{retention_name}',
    "flagged interpreter": f"python3 -I {retention_script} >/dev/null 2>&1",
}.items():
    validate_locale_collation(
        document,
        manifest_text.replace(retention_row, f"{LOCALE_GROUP}\t{command}\n", 1),
    )
    expect_locale_failure(
        f"{spelling} importer path moved to a fleet group",
        document,
        manifest_text.replace(retention_row, f"{LOCALE_GROUP}\tpython3 {retention_script}\nplatform\t{command}\n", 1),
        reason="runs outside locale-collation",
    )
for spelling, command in {
    "variable script path": 'python3 "$SCRIPT"',
    "inline python": "python3 -c 'import sys'",
    "module invocation": "python3 -m unittest",
    "unknown command": "make test",
    "glob script path": "bash scripts/ci-gate/*.test.sh",
    "cd to a variable": 'cd "$HOME" && bash scripts/actions-ci-group-output.test.sh',
    "no command": "@budget=60",
    "unknown command beside a script": "make check && bash scripts/actions-ci-group-output.test.sh",
}.items():
    expect_locale_failure(
        f"fleet row with an unresolvable {spelling}",
        document,
        manifest_text + f"platform\t{command}\n",
    )

# A `$` or backtick anywhere in a row -- an argument, an assignment, or a
# redirection target, not only the script word -- can run code at run time.
benign_row = "bash scripts/actions-ci-group-output.test.sh"
for spelling, command in {
    "a command-substitution argument": f'{benign_row} "$(python3 {retention_script})"',
    "a backtick argument": f"{benign_row} `python3 {retention_script}`",
    "an expanding assignment": f'PROBE="$(python3 {retention_script})" {benign_row}',
    "an expanding redirection target": f'{benign_row} >"$(python3 {retention_script})"',
    "a variable argument": f'{benign_row} "$PROBE"',
}.items():
    expect_locale_failure(
        f"fleet row with {spelling}",
        document,
        manifest_text + f"platform\t{command}\n",
        reason="expands at run time",
    )

# Rows run under `bash -o pipefail -c` without `-e`, so `||`, `&`, and `;` each
# let a script fail while the row still exits 0. Only `&&` and `|` chain.
for spelling, command in {
    "`||`": f"{benign_row} || {benign_row}",
    "a mid-row `&`": f"{benign_row} & {benign_row}",
    "a trailing `&`": f"{benign_row} &",
    "`;`": f"{benign_row}; {benign_row}",
}.items():
    expect_locale_failure(
        f"fleet row joined by {spelling}",
        document,
        manifest_text + f"platform\t{command}\n",
        reason="hides the exit status",
    )

# Importers outside scripts/ are scanned too, and a row may not run a script
# the scan does not cover.
# Fixture files enter the sources only where tracked_script_sources would scan them.
def with_tracked(extra):
    return {**script_sources, **{path: text for path, text in extra.items() if scanned(path)}}


action_probe = ".github/actions/zzprobe/zz.test.py"
action_importer = with_tracked({
    action_probe: (
        "import pathlib, sys\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / 'scripts/ci-gate'))\n"
        "import ascii_digit_locales\n"
    ),
})
expect_locale_failure(
    "importer outside scripts/ in a fleet group",
    document,
    manifest_text + f"platform\tpython3 {action_probe}\n",
    action_importer,
    reason="runs outside locale-collation",
)
expect_locale_failure(
    "unregistered importer outside scripts/",
    document,
    manifest_text,
    action_importer,
    reason="membership drifted",
)

# A fleet shell row reaches every script its body runs, transitively, so a
# wrapper cannot carry an importer out of locale-collation.
wrapper = "scripts/ci-gate/zz-probe.test.sh"
for spelling, body in {
    "sibling exec": f'exec python3 "$(dirname "$0")/{retention_name}"\n',
    "repository path": f'python3 "$root/{retention_script}" "$@"\n',
    "assigned name": f'name={retention_name}\nexec python3 "$here/$name"\n',
}.items():
    wrapped = dict(script_sources)
    wrapped[wrapper] = f"#!/usr/bin/env bash\n{body}"
    expect_locale_failure(
        f"fleet shell wrapper running an importer by {spelling}",
        document,
        manifest_text + f"platform\tbash {wrapper}\n",
        wrapped,
        reason="runs outside locale-collation",
    )
nested = dict(script_sources)
nested[wrapper] = 'exec bash "$(dirname "$0")/zz-inner.sh"\n'
nested["scripts/ci-gate/zz-inner.sh"] = f"exec python3 {retention_script}\n"
expect_locale_failure(
    "nested fleet shell wrapper running an importer",
    document,
    manifest_text + f"platform\tbash {wrapper}\n",
    nested,
    reason="runs outside locale-collation",
)
# The comment stripper does not model heredocs or `\'` inside `$'...'`, so a
# stray quote can shift its quote tracking until a later `#` swallows real code.
# A runnable name that survives in the raw text (whole-line comments aside) but
# not in the lexed words makes the wrapper unclassifiable, directly or through
# a further wrapper whose name was the one dropped.
for spelling, body in {
    "heredoc apostrophe": (
        "cat <<EOF\ndon't\nEOF\n"
        f"x='a #b'; python3 \"$root/{retention_script}\"\n"
    ),
    "ANSI-C escaped quote": f"echo $'it\\'s # x'; python3 \"$root/{retention_script}\"\n",
    "dropped inner wrapper": (
        "echo $'it\\'s # x'; exec bash \"$(dirname \"$0\")/zz-inner.sh\"\n"
    ),
}.items():
    misread = dict(script_sources)
    misread[wrapper] = f"#!/usr/bin/env bash\n{body}"
    misread["scripts/ci-gate/zz-inner.sh"] = f"exec python3 {retention_script}\n"
    expect_locale_failure(
        f"fleet shell wrapper whose {spelling} hides a run from the lexer",
        document,
        manifest_text + f"platform\tbash {wrapper}\n",
        misread,
        reason="the lexer dropped",
    )
subprocess_runner = dict(script_sources)
subprocess_runner["scripts/ci-gate/zz-sub.test.py"] = (
    f'import subprocess\nsubprocess.run(["python3", "{retention_script}"], check=True)\n'
)
expect_locale_failure(
    "fleet Python running an importer as a subprocess",
    document,
    manifest_text + "platform\tpython3 scripts/ci-gate/zz-sub.test.py\n",
    subprocess_runner,
    reason="runs outside locale-collation",
)
too_deep = dict(script_sources)
for level in range(SHELL_FOLLOW_DEPTH + 2):
    too_deep[f"scripts/ci-gate/zz-level{level}.sh"] = (
        f'exec bash "$(dirname "$0")/zz-level{level + 1}.sh"\n'
    )
too_deep[f"scripts/ci-gate/zz-level{SHELL_FOLLOW_DEPTH + 2}.sh"] = "true\n"
# The error names where following stopped -- the depth reached and the files
# still unvisited there -- not only the bound.
expect_locale_failure(
    "fleet shell chain deeper than the follow bound",
    document,
    manifest_text + "platform\tbash scripts/ci-gate/zz-level0.sh\n",
    too_deep,
    reason=(
        f"after {SHELL_FOLLOW_DEPTH + 1} levels, "
        f"['scripts/ci-gate/zz-level{SHELL_FOLLOW_DEPTH + 1}.sh'] are still unvisited"
    ),
)
# Positive controls: a comment that names an importer, and a wrapper that runs
# only a non-importer, stay classifiable in a fleet group.
benign = dict(script_sources)
benign[wrapper] = (
    f"# unlike {retention_script}, this needs no hazard locale\n"
    "exec bash scripts/actions-ci-group-output.test.sh\n"
)
validate_locale_collation(document, manifest_text + f"platform\tbash {wrapper}\n", benign)
print("ok - a fleet wrapper naming an importer only in a comment stays classifiable")

for location, reason in {
    "/tmp/zz.test.py": "lies outside the repository",
    "../zz.test.py": "lies outside the repository",
    "scripts/ci-gate/zz-untracked.test.py": "is not a tracked file in the scanned roots",
}.items():
    expect_locale_failure(
        f"fleet row script {location} outside the scanned roots",
        document,
        manifest_text + f"platform\tpython3 {location}\n",
        reason=reason,
    )

required = jobs["shell-tests"]
assert required["needs"] == [
    "change-scope",
    "shell-test-groups",
    "docs-contracts",
    "hosted-compatibility-tests",
    "adr-number-collision",
    "runner-isolation",
]
assert required["if"] == (
    "${{ always() && (github.event_name != 'pull_request' "
    "|| !github.event.pull_request.draft) }}"
)
assert required["timeout-minutes"] == 2
assert "strategy" not in required
assert required["steps"][0]["env"] == {
    "SCOPE_RESULT": "${{ needs.change-scope.result }}",
    "HEAVY": "${{ needs.change-scope.outputs.heavy }}",
    "GROUP_RESULT": "${{ needs.shell-test-groups.result }}",
    "DOCS_RESULT": "${{ needs.docs-contracts.result }}",
    "COMPATIBILITY_RESULT": "${{ needs.hosted-compatibility-tests.result }}",
    "COLLISION_RESULT": "${{ needs.adr-number-collision.result }}",
    "ISOLATION_RESULT": "${{ needs.runner-isolation.result }}",
}
assert required["steps"][0]["run"] == (
    'if [ "$SCOPE_RESULT" != "success" ]; then\n'
    '  echo "::error::changed-path classification failed"\n'
    "  exit 1\n"
    "fi\n"
    "# ADR 0235: the runner-isolation contract runs on every event, so\n"
    "# anything but success, including skipped, fails the required context.\n"
    'if [ "$ISOLATION_RESULT" != "success" ]; then\n'
    '  echo "::error::runner-isolation contract failed (ADR 0235)"\n'
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
    $1 !~ /^(platform|merge-gate|changelog-release-[1-4]|docs|locale-collation)$/ { valid = 0; next }
    seen[$2]++ { valid = 0 }
    { groups[$1]++; total++ }
    END {
      if (!(total >= 60 &&
        groups["platform"] > 0 &&
        groups["merge-gate"] > 0 &&
        groups["changelog-release-1"] > 0 &&
        groups["changelog-release-2"] > 0 &&
        groups["changelog-release-3"] > 0 &&
        groups["changelog-release-4"] > 0 &&
        groups["docs"] > 0 &&
        groups["locale-collation"] > 0)) {
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
merge-gate	@budget=90 bash scripts/ci-gate/arm-receipt.test.sh
merge-gate	bash scripts/ci-gate/gate-hold-disable.test.sh
merge-gate	@budget=90 bash scripts/ci-gate/native-automerge.test.sh
merge-gate	bash scripts/ci-gate/privileged-merge-pin.test.sh
changelog-release-1	@budget=600 CHANGELOG_CALLER_CONTRACT_SHARD=1 bash scripts/ci-gate/changelog-caller-contract.test.sh
changelog-release-2	@budget=600 CHANGELOG_CALLER_CONTRACT_SHARD=2 bash scripts/ci-gate/changelog-caller-contract.test.sh
changelog-release-3	@budget=600 CHANGELOG_CALLER_CONTRACT_SHARD=3 bash scripts/ci-gate/changelog-caller-contract.test.sh
changelog-release-4	@budget=600 CHANGELOG_CALLER_CONTRACT_SHARD=4 bash scripts/ci-gate/changelog-caller-contract.test.sh
changelog-release-1	@budget=120 CHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY=1 bash scripts/ci-gate/changelog-caller-contract.test.sh
platform	bash scripts/runner-selector-health.test.sh
docs	python3 scripts/changelog.py validate --repo-root .
docs	bash scripts/changelog-fragment-schema.test.sh
changelog-release-2	python3 scripts/v1-readiness-contract.test.py
platform	bash scripts/actions-ci-python-dependencies.test.sh
platform	bash scripts/shellcheck-tracked.test.sh
platform	@budget=210 bash scripts/shellcheck-tracked.sh
platform	bash scripts/ci-gate/hub-changelog-validate.sh
LOAD_BEARING_COMMANDS
}

if validate_manifest "$manifest"; then
  pass "manifest assigns every command once across the platform, merge-gate, docs, changelog-release, and locale-collation cells"
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
#   step, or named in a parsed key or value of a tracked workflow or composite
#   action under `.github/` -- each of which is an execution path Actions
#   actually takes. A comment is not one: a mention in a YAML comment, or in a
#   shell comment inside a `run` block, never counts.
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
# since the name is matched anywhere in the parsed workflow values and those shapes are
# real in this tree. Reachability of workflows themselves is a separate
# invariant and a separate check.
if python3 - "$root" "$manifest" "$workflow" <<'PY'
import ast
from functools import lru_cache
import pathlib
import subprocess
import sys

import yaml

root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
import shell_words  # noqa: E402
manifest_text = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
document = yaml.safe_load(pathlib.Path(sys.argv[3]).read_text(encoding="utf-8"))

# Declared library modules: imported by a ci-gate script, never invoked as a
# command of their own, so no manifest row could run them. Each entry carries the
# reason it is not a gate; adding one is a reviewable act, not a naming accident.
NON_GATE_MODULES = {
    "scripts/ci-gate/ascii_digit_locales.py": (
        "locale-matrix helper imported by the digit-guard tests"
    ),
    "scripts/ci-gate/settable_value_guards.py": (
        "shell guard scanner imported by settable-value-guards.test.py"
    ),
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
    "scripts/ci-gate/conformance/yaml_documents.py": (
        "cached workflow loader imported by the conformance modules"
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


# Every mutation re-lexes the same unchanged run blocks; a failed lex raises and
# is never cached, so a malformed block still fails each time it is read.
@lru_cache(maxsize=None)
def shell_tokens(text):
    return tuple(shell_words.words(text))


def referenced(text, candidates=None):
    """Tracked ci-gate paths mentioned by shell tokens on an execution path."""
    candidates = gate_scripts if candidates is None else candidates
    try:
        tokens = shell_tokens(text)
    except ValueError as error:
        mentioned = sorted(path for path in candidates if path in text)
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
        seen.update(path for path in candidates if path in token)
    return seen


# Bash starts a comment only at a word-initial `#`; inside a word (`${#arr[@]}`,
# a URL fragment) it is data, and dropping the rest of that line would lose a
# real reference and report a registered gate as unreachable.
COMMENT_PROBE = "scripts/ci-gate/x.test.sh"
for source, expected in (
    (f"n=${{#arr[@]}}; bash {COMMENT_PROBE}", {COMMENT_PROBE}),
    (f"curl https://h/p#frag && bash {COMMENT_PROBE}", {COMMENT_PROBE}),
    (f"printf '%s' '# kept' && bash {COMMENT_PROBE}", {COMMENT_PROBE}),
    (f"echo ok # bash {COMMENT_PROBE}", set()),
    (f"echo ok;# bash {COMMENT_PROBE}", set()),
    (f"# bash {COMMENT_PROBE}\necho ok", set()),
):
    found = referenced(source, [COMMENT_PROBE])
    if found != expected:
        raise AssertionError(f"shell comment handling: {source!r} found {found}")
print("ok - only a word-initial # starts a shell comment")


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


@lru_cache(maxsize=None)
def parse_workflow(source):
    return yaml.safe_load(source)


def invocation_paths(sources, candidates=None):
    invoked = set(exact_commands)
    for source in sources.values():
        for run_block in run_blocks(parse_workflow(source)):
            invoked.update(referenced(run_block, candidates))
    return invoked


def yaml_strings(value):
    """String keys and values a parsed document carries, except shell `run` blocks.

    Parsing drops YAML comments; `run` blocks are read by `referenced`, whose
    shell lexer drops shell comments, so neither kind of comment is a mention.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                yield key
            if key == "run" and isinstance(child, str):
                continue
            yield from yaml_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from yaml_strings(child)
    elif isinstance(value, str):
        yield value


def reachability(sources, candidates=None):
    """A comment is not an execution path: a test file named only in a comment
    beside the step it documents must not count as registered."""
    invoked = invocation_paths(sources, candidates)
    mentions = "\n".join(
        text
        for source in sources.values()
        for text in yaml_strings(parse_workflow(source))
    )
    return lambda path: path in invoked or path in mentions


workflow_reachable = reachability(workflow_sources)


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
        text = sources[importer]
    except KeyError as error:
        raise SystemExit(
            f"{importer} could not be parsed while checking non-gate imports: {error}"
        ) from error
    return parsed_imports(importer, text, frozenset(sources))


# `importers_of` asks every script for its imports once per declared module and
# once more per mutation, so an uncached walk re-parses each script thousands of
# times and pushed this row past its pull-request budget on a loaded runner.
# The key is everything the answer depends on: the script's own text and the
# tracked set its imports resolve against, so a mutated source still re-parses.
@lru_cache(maxsize=None)
def parsed_imports(importer, text, tracked_python):
    try:
        tree = ast.parse(text, filename=importer)
    except SyntaxError as error:
        raise SystemExit(
            f"{importer} could not be parsed while checking non-gate imports: {error}"
        ) from error

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
    return frozenset(resolved)


def importers_of(path, sources=python_sources):
    return [
        importer
        for importer in sources
        if importer != path and path in imported_paths(importer, sources)
    ]


def unaccounted(candidate_paths, reachable=workflow_reachable):
    # The manifest and the hosted step are executed verbatim, so their command
    # arguments match exactly (`invocation_paths` starts from them), as do the
    # shell tokens of every `run` block. A workflow may interpolate a path into a
    # longer expression, so any other parsed key or value may also name it.
    return [
        path
        for path in candidate_paths
        if path not in NON_GATE_MODULES and not reachable(path)
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

comment_probe = probes[0]
comment_forms = {
    "YAML comment": f"# runs {comment_probe}\njobs:\n  probe:\n    steps:\n      - run: true\n",
    "trailing YAML comment": (
        f"jobs:\n  probe:\n    steps:\n      - run: true  # {comment_probe}\n"
    ),
    "shell comment in a run block": (
        "jobs:\n  probe:\n    steps:\n      - run: |\n"
        f"          # guard proven by {comment_probe}\n          true\n"
    ),
}
for form, source in comment_forms.items():
    commented = dict(workflow_sources)
    commented[".github/workflows/comment-probe.yml"] = source
    if unaccounted([comment_probe], reachability(commented, probes)) != [comment_probe]:
        raise SystemExit(f"a ci-gate path named only in a {form} counted as reachable")
named_forms = {
    "run command": f"jobs:\n  probe:\n    steps:\n      - run: bash {comment_probe}\n",
    "step input": (
        "jobs:\n  probe:\n    steps:\n      - uses: ./x\n"
        f"        with:\n          script: {comment_probe}\n"
    ),
}
for form, source in named_forms.items():
    named = dict(workflow_sources)
    named[".github/workflows/value-probe.yml"] = source
    if unaccounted([comment_probe], reachability(named, probes)):
        raise SystemExit(f"a ci-gate path named in a workflow {form} did not count as reachable")
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
platform	printf 'first\n' >'$tmp/first-ran'
platform	false
platform	printf 'last\n' >'$tmp/last-ran'
EOF

if ACTIONS_CI_GROUP_MANIFEST="$tmp/manifest.tsv" bash "$runner" platform >"$tmp/out" 2>&1; then
  fail "group runner reported green after a member failed"
elif [ "$(cat "$tmp/first-ran")" = first ] \
  && [ "$(cat "$tmp/last-ran")" = last ] \
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

if grep -qE $'^platform\t@exclusive (@budget=[0-9]+ )?bash scripts/actions-ci-groups.test.sh$' "$manifest"; then
  pass "grouping contract runs alone in actions CI"
else
  fail "grouping contract is not registered exclusively in actions CI"
fi

# Pull requests hold every row to 60 seconds unless the row declares its own
# budget (ADR 0225). The default leaves at least 2x margin over rows measured at
# 30s or less on the 2-vCPU general runners; a row measured above that declares
# twice its slowest CI time, rounded up to 30s. Declarations are pinned so that
# raising a budget is a reviewed change, and none may approach the job timeout.
if python3 - "$manifest" <<'PY'
import re
import sys

declared = {}
for line in open(sys.argv[1], encoding="utf-8").read().splitlines():
    if not line or line.startswith("#"):
        continue
    group, command = line.split("\t", 1)
    directives = []
    while command.startswith("@"):
        directive, _, command = command.partition(" ")
        directives.append(directive)
    budgets = [d for d in directives if d.startswith("@budget=")]
    if directives not in ([], ["@exclusive"], budgets, ["@exclusive", *budgets]) or len(budgets) > 1:
        raise SystemExit(f"directives must be [@exclusive] [@budget=N] in that order: {line}")
    if budgets:
        match = re.fullmatch(r"@budget=([1-9][0-9]*)", budgets[0])
        if not match or not 60 < int(match.group(1)) <= 600 or int(match.group(1)) % 30:
            raise SystemExit(f"declared budget must be a multiple of 30 in 90..600 seconds: {line}")
        declared[f"{group}\t{command}"] = int(match.group(1))

shards = {
    f"changelog-release-{shard}\tCHANGELOG_CALLER_CONTRACT_SHARD={shard} "
    "bash scripts/ci-gate/changelog-caller-contract.test.sh": 600
    for shard in range(1, 5)
}
expected = shards | {
    "changelog-release-1\tCHANGELOG_CALLER_CONTRACT_GENERATOR_ONLY=1 "
    "bash scripts/ci-gate/changelog-caller-contract.test.sh": 120,
    "changelog-release-3\tbash scripts/component-changelog-contract.test.sh": 90,
    "platform\tbash scripts/actions-ci-groups.test.sh": 180,
    "platform\tbash scripts/ci-gate/runner-admission-reconcile.test.sh": 120,
    "platform\tpython3 scripts/ci-gate/app-key-environment.test.py": 120,
    "platform\tbash scripts/required-checks-audit.test.sh": 120,
    "platform\tpython3 scripts/ci-gate/conformance/conformance.test.py": 120,
    "platform\tpython3 scripts/ci-gate/compatibility-sandbox-provisioning.test.py": 90,
    "platform\tbash scripts/shellcheck-tracked.sh": 210,
    "merge-gate\tbash scripts/ci-gate/privileged-merge-conformance.test.sh": 120,
    "merge-gate\tbash scripts/ci-gate/default-branch-uri-encoding.test.sh": 90,
    "merge-gate\tbash scripts/ci-gate/native-automerge.test.sh": 90,
    "merge-gate\tbash scripts/ci-gate/arm-receipt.test.sh": 90,
    "merge-gate\tbash scripts/ci-gate/zero-provider-recovery.test.sh": 90,
    # 173s serially on main run 38513400637.
    "merge-gate\tpython3 scripts/ci-gate/review-caller-bootstrap.test.py": 360,
    # 31s serially on main run 38513400637.
    "platform\tpython3 scripts/ci-gate/hosted-selector-policy.test.py": 90,
    # The runner-isolation test measured 122s whole inside the platform group
    # at #1827's round 13, so it runs as two parts: 62s (core) and 69s
    # (probes) inside the platform group, each about a third of 180s.
    "platform\tRUNNER_ISOLATION_TEST_PART=core python3 scripts/ci-gate/public-pr-runner-isolation.test.py": 180,
    "platform\tRUNNER_ISOLATION_TEST_PART=probes python3 scripts/ci-gate/public-pr-runner-isolation.test.py": 180,
    # 51s on main run 38799238936, before #1822 added 15 assertions to it.
    "merge-gate\tbash scripts/assert-mergeable-head.test.sh": 120,
}
if declared != expected:
    drift = sorted(
        f"{row} declared={declared.get(row)} expected={expected.get(row)}"
        for row in declared.keys() | expected.keys()
        if declared.get(row) != expected.get(row)
    )
    raise SystemExit("declared row budgets drifted:\n  " + "\n  ".join(drift))
PY
then
  pass "declared row budgets are pinned, ordered after @exclusive, and stay within 600s"
else
  fail "declared row budgets are malformed or drifted from their measured basis"
fi

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."
exit 1
