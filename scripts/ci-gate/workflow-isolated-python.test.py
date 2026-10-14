#!/usr/bin/env python3
"""Every inline Python interpreter in every workflow starts isolated.

Workflow steps run Python helpers with a checkout, or directories it can
write, as the working directory. ``-I`` keeps that directory, a script's
directory, the user site directory, and ``PYTHON*`` variables out of module
resolution, so a helper resolves only the modules it names explicitly
(ADR 0228, extending ADR 0086).

This contract parses every ``run:`` block in every workflow under
``.github/workflows/`` and in the workflows each ``scripts/gen-*`` generator
emits for adopters, and fails on any interpreter invocation without ``-I``
(combined short flags such as ``-Ic`` count when ``I`` precedes an option that
takes an argument). After joining line continuations it checks every
occurrence on a line, so a later call after ``;``, ``&&``, ``||``, or ``|``
is checked on its own, as is a bare ``| python3``. An interpreter in command
position, quoted or not, is an invocation whatever follows it, and leading
redirections (``python3 2>&1 -c ...``) are skipped before its options are
read. It also covers calls nested in ``bash -c '...'``, interpreters held in a
shell variable (any variable whose name contains ``py``, and any variable
command reading ``-``, ``-c``, or ``-m`` or running a ``.py`` file), argument
lists embedded in Python (``("/usr/bin/python3", "-I", ...)`` and
``sys.executable``), a ``.py`` file started through its own shebang (including
a path built from quoted variables), and a step, job, or workflow ``shell:``
naming Python. Every repository script a
workflow starts isolated must still import from an unrelated directory under
``-I`` (a script that imports a sibling adds its own directory explicitly), and
a behavioral regression runs a representative extracted Node CI validator in a
fixture checkout carrying same-named modules.

Documented limitations: an interpreter path computed inside Python (for
example from ``shutil.which`` or a variable passed to ``subprocess``), or
reached through shell indirection such as ``eval``, command substitution
(``"$(command -v python3)" -``), functions, or aliases, is not detected
lexically, nor is an interpreter word outside command position followed by a
bare word (read as prose). Review those forms by hand.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIRECTORY = ROOT / ".github/workflows"
NODE_CI_WORKFLOWS = (
    WORKFLOW_DIRECTORY / "node-ci.yml",
    WORKFLOW_DIRECTORY / "node-ci-protected.yml",
)
CONTRACT_REF = "{contract_ref}"
REQUIRED_CHECKS = (
    '[{{"name":"shell-tests","app_id":15368,"workflow_id":315894159,'
    '"workflow_path":".github/workflows/actions-ci.yml"}}]'
)
# Every workflow a generator emits, rendered from the working tree. A generator
# whose output is not a workflow is listed in NON_WORKFLOW_GENERATORS instead;
# an unclassified generator fails this contract until someone decides which.
RENDERED_WORKFLOWS = {
    "scripts/gen-changelog-caller.sh": (
        *(
            (mode, CONTRACT_REF)
            for mode in (
                "workflow",
                "generated-artifacts",
                "generated-artifacts-with-adr-index",
                "renovate-attribution",
                "pr-gate",
                "release-node",
                "release-snapshot",
            )
        ),
        ("release-artifact", CONTRACT_REF, "--build-runner", "ubuntu-24.04"),
        ("release-propose", CONTRACT_REF, "--autonomy", "propose"),
        ("release-propose", CONTRACT_REF, "--autonomy", "dispatch"),
    ),
    "scripts/gen-node-required-workflow.py": (
        ("config/cli-projects-required-node-ci.json",),
        ("--consumer", "config/cli-projects-required-node-ci.json"),
    ),
    "scripts/gen-node-ci-protected.py": ((),),
    "scripts/gen-authn-type-surface-required-workflow.py": ((CONTRACT_REF,),),
    "scripts/gen-container-candidate.sh": (("workflow", CONTRACT_REF),),
    "scripts/gen-container-release.sh": (("workflow", CONTRACT_REF),),
    "scripts/gen-container-deployment.sh": tuple(
        (kind, CONTRACT_REF)
        for kind in (
            "workflow",
            "code-review-workflow",
            "security-review-workflow",
            "ai-review-workflow",
            # review-producer-workflow copies the committed
            # container-deployment-review-producer.yml at the ref, which is scanned directly.
        )
    ),
    "scripts/gen-privileged-merge-caller.sh": (
        (CONTRACT_REF, REQUIRED_CHECKS),
        (CONTRACT_REF, "--retry", '["CI"]', REQUIRED_CHECKS),
    ),
    "scripts/gen-gate-rearm-caller.sh": ((CONTRACT_REF,),),
    "scripts/gen-ai-review-caller.sh": ((CONTRACT_REF,),),
    "scripts/gen-ai-review-label-rearm-caller.sh": ((CONTRACT_REF,), ("--local",)),
    "scripts/gen-ai-review-lifecycle-rearm-caller.sh": (("--local",),),
    "scripts/gen-renovate-compatibility-caller.sh": (
        (CONTRACT_REF, "@verjson/example", "1.2.3", "node"),
    ),
}
NON_WORKFLOW_GENERATORS = {
    "scripts/gen-adr-index.sh": "renders the decision index Markdown",
    "scripts/gen-ai-review-caller-bootstrap-fragment.sh": "renders a NEXT/ changelog fragment",
    "scripts/gen-conformance-regression-fixture.py": (
        "renders a conformance counter-example derived from node-ci.yml, which this "
        "contract scans; the fixture is test input and never runs as a workflow"
    ),
    "scripts/gen-type-surface-contract.sh": "renders a packing helper and a shell contract test",
}
# Reviewed exceptions: (source, step name, invocation text) -> written reason.
# Keep it rare: an interpreter that cannot run isolated must be fixed, not
# listed. An entry that no longer matches a finding fails, so it cannot linger.
SELF_RELEASE_CALLER_PIN = (
    "this repository's own release caller is gen-changelog-caller.sh output pinned to "
    "contract d1bf04c7f73c04f37cc942d8b0a7a03e173579f8 and is never hand-edited; the "
    "generator emits these steps isolated, so the next canonical repin regenerates "
    "them isolated and this entry then fails as stale"
)
ALLOWED_NON_ISOLATED = {
    (".github/workflows/release.yml", step, invocation): SELF_RELEASE_CALLER_PIN
    for step, invocation in (
        ("Require an explicit release version", "python3 - <<'PY'"),
        (
            "Resolve the release selection and version",
            'python3 .changelog-contract/scripts/changelog.py "${args[@]}" >"$plan_file"',
        ),
        (
            "Resolve the release selection and version",
            'actual_selector_digest="$(python3 - "$plan_file" <<\'PY\'',
        ),
        (
            "Resolve the release selection and version",
            'python3 - "$plan_file" "$GITHUB_OUTPUT" "$GITHUB_STEP_SUMMARY" "$GITHUB_SHA" <<\'PY\'',
        ),
    )
}

# Any directory prefix counts: toolcache and venv interpreters are still Python.
INTERPRETER = r"(?:[^\s\"'`=;|&()$]*/)?python(?:3(?:\.\d+)?)?"
INTERPRETER_TOKEN = re.compile(r"(?<![\w.$/-])" + INTERPRETER + r"(?![\w./-])")
LIST_INVOCATION = re.compile(
    r"""(?:["']""" + INTERPRETER + r"""["']|sys\.executable)\s*,\s*(?P<next>[^,\])\n]*)"""
)
SEGMENT_SEPARATOR = re.compile(r"\|\||&&|;|\||\n")
VARIABLE_COMMAND = re.compile(r"""^["']?\$\{?(?P<name>[A-Za-z_]\w*)\}?["']?$""")
# Shell keywords that leave the next word in command position.
SHELL_KEYWORDS = frozenset(("if", "then", "elif", "else", "do", "while", "until", "!", "{"))
# Wrapper commands that run their operand as a command: the options that take
# a separate value, and how many positional words precede the command.
WRAPPER_COMMANDS = {
    "exec": (frozenset(("-a",)), 0),
    "command": (frozenset(), 0),
    "time": (frozenset(("-f", "--format", "-o", "--output")), 0),
    "nohup": (frozenset(), 0),
    "env": (frozenset(("-u", "--unset", "-C", "--chdir", "-S", "--split-string")), 0),
    "sudo": (frozenset((
        "-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt",
        "-C", "--close-from", "-D", "--chdir", "-R", "--chroot", "-T", "--command-timeout",
        "-U", "--other-user", "-r", "--role", "-t", "--type",
    )), 0),
    "timeout": (frozenset(("-s", "--signal", "-k", "--kill-after")), 1),
    "nice": (frozenset(("-n", "--adjustment")), 0),
    "stdbuf": (frozenset(("-i", "--input", "-o", "--output", "-e", "--error")), 0),
    "xargs": (frozenset((
        "-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "-L", "--max-lines",
        "-n", "--max-args", "-P", "--max-procs", "-s", "--max-chars",
    )), 0),
}
# Python options whose value is the following word.
PYTHON_OPTIONS_WITH_VALUE = frozenset(("-W", "-X"))
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
# A brace opens a group only as its own word, so xargs -I {} is not a boundary.
COMMAND_BOUNDARY = re.compile(r"\|\||&&|;|\||\n|\(|`|(?:^|(?<=\s))\{(?=\s)")
REDIRECTION_OPERATOR = re.compile(r"^(?:\d*|&)(?:>>?|<<?<?|>&|<&)$")
ATTACHED_REDIRECTION = re.compile(r"^(?:\d*|&)(?:>>?|<<?<?)\S+$")
# A path built from literal and shell-variable parts, after its quotes are
# removed, naming a .py file. A Python string literal such as
# f"{ref}/scripts/x.py" keeps a bare brace and is not a command word.
SCRIPT_PATH = re.compile(r"""(?:\$\{?\w+\}?|[\w.~/-])(?:[\w.~/-]|\$\{?\w+\}?)*\.py""")
# Python options that consume the rest of their short-flag cluster as an argument.
OPTIONS_WITH_ARGUMENT = frozenset("cmWXQ")


def flags_are_isolated(tokens):
    """True when the interpreter's leading options include -I before any program."""
    for token in tokens:
        token = token.strip("'\"")
        if not token.startswith("-") or token == "-" or token.startswith("--"):
            return False
        for flag in token[1:]:
            if flag == "I":
                return True
            if flag in OPTIONS_WITH_ARGUMENT:
                return False
    return False


def without_command_prefix(tokens):
    """Tokens from the command word on.

    Drops shell keywords, assignments, and wrapper commands together with the
    wrapper's own options, option values, and positional words (``timeout
    60``), so a wrapped interpreter is still read in command position.
    """
    tokens = list(tokens)
    while tokens:
        word = tokens[0]
        if word in SHELL_KEYWORDS or ASSIGNMENT.match(word):
            tokens.pop(0)
            continue
        if word not in WRAPPER_COMMANDS:
            break
        options_with_value, positional_words = WRAPPER_COMMANDS[tokens.pop(0)]
        while tokens and tokens[0].startswith("-") and tokens[0] != "-":
            option = tokens.pop(0)
            if option == "--":
                break
            if option in options_with_value and tokens:
                tokens.pop(0)
        del tokens[:positional_words]
    return tokens


def python_program(arguments):
    """What an interpreter runs: ``-``, ``-c``, ``-m``, a script word, or None."""
    remaining = iter(arguments)
    for token in remaining:
        token = token.strip("'\"")
        if token == "-" or not token.startswith("-") or token.startswith("--"):
            return token
        if token in PYTHON_OPTIONS_WITH_VALUE:
            next(remaining, None)
            continue
        for flag in token[1:]:
            if flag in "cm":
                return f"-{flag}"
            if flag in OPTIONS_WITH_ARGUMENT:
                break
    return None


def in_command_position(before):
    """True when the text before a word leaves that word as the command."""
    boundaries = list(COMMAND_BOUNDARY.finditer(before))
    preceding = before[boundaries[-1].end():] if boundaries else before
    return not without_command_prefix(preceding.split())


def without_redirections(tokens):
    """Arguments with shell redirections removed, so they cannot read as the program."""
    remaining = []
    skip_target = False
    for token in tokens:
        if skip_target:
            skip_target = False
        elif REDIRECTION_OPERATOR.match(token):
            skip_target = True
        elif not ATTACHED_REDIRECTION.match(token):
            remaining.append(token)
    return remaining


def is_script_path(token):
    return SCRIPT_PATH.fullmatch(re.sub(r"[\"']", "", token)) is not None


def segment_after(text, position):
    """Arguments that follow an interpreter, up to the next shell command boundary."""
    rest = text[position:]
    boundary = SEGMENT_SEPARATOR.search(rest)
    return rest[:boundary.start()] if boundary else rest


def literal_interpreter_findings(line):
    findings = []
    for match in INTERPRETER_TOKEN.finditer(line):
        before = line[:match.start()]
        after = line[match.end():]
        end = match.end()
        if before.endswith(("'", '"')) and after[:1] == before[-1]:
            # A quoted interpreter is a command only in command position; in a
            # list or call (``"python3", ...``) LIST_INVOCATION checks it instead.
            if after[1:].lstrip()[:1] in (",", "]", ")") or not in_command_position(before[:-1]):
                continue
            before, end = before[:-1], end + 1
        if re.search(r"(?:command\s+-v|which|type|install)\s+(?:\S+\s+)*$", before):
            continue
        tokens = without_redirections(segment_after(line, end).split())
        cleaned = [token.strip("'\")") for token in tokens]
        if (
            not in_command_position(before)
            and cleaned
            and cleaned[0]
            and not re.match(r"""[-"'$/.<>&{]|\S+\.py\b""", tokens[0])
        ):
            continue  # prose such as "the runner's python3 parser"
        if not flags_are_isolated(cleaned):
            findings.append(match.group(0))
    return findings


def variable_interpreter_findings(line):
    findings = []
    for segment in SEGMENT_SEPARATOR.split(line):
        tokens = without_command_prefix(segment.split())
        if not tokens:
            continue
        variable = VARIABLE_COMMAND.match(tokens[0])
        if variable is None:
            continue
        arguments = without_redirections(tokens[1:])
        program = python_program(arguments)
        names_python = re.search(r"py", variable.group("name"), re.IGNORECASE) is not None
        runs_python = program in ("-", "-c", "-m") or (program is not None and is_script_path(program))
        if (names_python or runs_python) and not flags_are_isolated(arguments):
            findings.append(tokens[0])
    return findings


def direct_script_findings(line):
    """A ``.py`` command word runs through its shebang, which never carries -I."""
    findings = []
    for segment in SEGMENT_SEPARATOR.split(line):
        tokens = without_command_prefix(segment.split())
        if tokens and is_script_path(tokens[0]):
            findings.append(tokens[0])
    return findings


def list_interpreter_findings(lines, index):
    findings = []
    for match in LIST_INVOCATION.finditer(lines[index]):
        following = match.group("next").strip()
        if not following:
            # A multi-line argument tuple names the interpreter on its own line.
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        first_argument = following.split(",", 1)[0].strip()
        if not flags_are_isolated([first_argument]):
            findings.append(match.group(0).strip())
    return findings


def non_isolated_invocations(script):
    joined = re.sub(r"\\\n\s*", " ", script)
    lines = joined.splitlines()
    findings = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if (
            literal_interpreter_findings(line)
            or variable_interpreter_findings(line)
            or direct_script_findings(line)
            or list_interpreter_findings(lines, index)
        ):
            findings.append(stripped)
    return findings


def step_shell_findings(step, job, document):
    """A run step whose shell is itself a Python interpreter executes in-process."""
    shell = (
        step.get("shell")
        or ((job.get("defaults") or {}).get("run") or {}).get("shell")
        or ((document.get("defaults") or {}).get("run") or {}).get("shell")
    )
    if not isinstance(shell, str):
        return []
    tokens = shell.split()
    if not tokens or not re.fullmatch(INTERPRETER, tokens[0]):
        return []
    return [] if flags_are_isolated(tokens[1:]) else [f"shell: {shell}"]


def workflow_findings(source, text, allowed_used):
    """Non-isolated invocations in one workflow document, minus reviewed exceptions."""
    document = yaml.safe_load(text)
    assert isinstance(document, dict), f"{source}: workflow is not a mapping"
    findings = []
    for job_name, job in (document.get("jobs") or {}).items():
        for step in job.get("steps", []) or []:
            script = step.get("run")
            if not isinstance(script, str):
                continue
            step_name = step.get("name", step.get("id", "<unnamed>"))
            invocations = non_isolated_invocations(script) + step_shell_findings(step, job, document)
            for invocation in invocations:
                key = (source, step_name, invocation)
                if key in ALLOWED_NON_ISOLATED:
                    allowed_used.add(key)
                else:
                    findings.append(f"{source}: {job_name} / {step_name}: {invocation}")
    return findings


def committed_workflows():
    workflows = sorted(
        path for pattern in ("*.yml", "*.yaml") for path in WORKFLOW_DIRECTORY.glob(pattern)
    )
    assert workflows, "no workflows found under .github/workflows"
    return workflows


def render_generator(generator, arguments, scratch):
    """One generator output, rendered from the working tree."""
    path = ROOT / generator
    if generator == "scripts/gen-node-ci-protected.py":
        # Its entry point writes the committed workflow in place; render in memory.
        spec = importlib.util.spec_from_file_location("gen_node_ci_protected", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.render()
    command = [sys.executable, str(path)] if path.suffix == ".py" else ["bash", str(path)]
    working_directory = ROOT
    if generator == "scripts/gen-container-candidate.sh":
        # It reads the adopter's candidate configuration from its working directory.
        working_directory = scratch
        (scratch / "container-candidate.json").write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        command + list(arguments),
        cwd=working_directory,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and result.stdout.strip(), (
        f"{generator} {' '.join(arguments)} did not render a workflow: {result.stderr}"
    )
    return result.stdout


def generator_classification_findings():
    generators = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "scripts").glob("gen-*")
        if path.is_file() and ".test." not in path.name
    }
    classified = set(RENDERED_WORKFLOWS) | set(NON_WORKFLOW_GENERATORS)
    return [
        *(f"{name}: unclassified generator; render its workflows or record why it emits none"
          for name in sorted(generators - classified)),
        *(f"{name}: classified generator no longer exists" for name in sorted(classified - generators)),
    ]


def workflow_sources():
    """(source label, workflow text) for every committed and generator-emitted workflow."""
    sources = [
        (workflow.relative_to(ROOT).as_posix(), workflow.read_text(encoding="utf-8"))
        for workflow in committed_workflows()
    ]
    contract_ref = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    with tempfile.TemporaryDirectory() as temporary:
        scratch = Path(temporary)
        for generator, invocations in RENDERED_WORKFLOWS.items():
            for arguments in invocations:
                arguments = tuple(argument.format(contract_ref=contract_ref) for argument in arguments)
                source = " ".join((generator, *arguments)).replace(contract_ref, "<contract-ref>")
                sources.append((source, render_generator(generator, arguments, scratch)))
    return sources


def all_findings(sources):
    allowed_used = set()
    findings = generator_classification_findings()
    for source, text in sources:
        findings += workflow_findings(source, text, allowed_used)
    findings += [
        f"{source}: {step} / {invocation}: stale exception; remove it ({reason})"
        for (source, step, invocation), reason in ALLOWED_NON_ISOLATED.items()
        if (source, step, invocation) not in allowed_used
    ]
    return findings


def assert_scanner_recognizes_invocation_forms():
    non_isolated = (
        "python3 - <<'PY'",
        "exec python3 - <<'PY'",
        "exec /usr/bin/python3 - <<'PY'",
        'NAME="$VALUE" python3 - "$payload" <<\'PY\'',
        "python3 - > \"$output\" <<'PY'",
        'python3 "$SERVER" "$DIRECTORY" &',
        "python -c 'print(1)'",
        "python3.12 -u - <<'PY'",
        # Any interpreter directory, not only /usr/bin.
        "/opt/hostedtoolcache/Python/3.12.7/x64/bin/python3 - <<'PY'",
        "/bin/python3 -c pass",
        "./venv/bin/python -c pass",
        '"/usr/bin/python3", "-c", source,',
        "subprocess.run([sys.executable, script])",
        "subprocess.run([\"python3\", \"-c\", source])",
        # A second call after an isolated one on the same line.
        "python3 -I - <<'PY'; python3 - <<'PY'",
        "python3 -I -c pass && python3 -c pass",
        "python3 -I -c pass || python3 -c pass",
        "python3 -I -c pass | python3 -c pass",
        # A line continuation between the interpreter and its arguments.
        "python3 \\\n  - <<'PY'",
        "exec /usr/bin/python3 \\\n  -c \"$source\"",
        # Stdin from a pipe with no arguments.
        "printf '%s' \"$source\" | python3",
        "cat script.py | python3 | tee out",
        # A nested shell.
        "bash -c 'python3 -'",
        "sh -c \"python3 -c pass\"",
        # An interpreter held in a variable, or any variable reading stdin.
        "\"$python\" - <<'PY'",
        "$py -c pass",
        "${PYTHON_BIN} script.py",
        "exec \"$interpreter\" - <<'PY'",
        "NAME=value \"$tool\" - <<'PY'",
        # A script started through its own shebang line.
        "scripts/ci-gate/example.test.py",
        "./scripts/tool.py --flag",
        "VAR=1 \"$RUNNER_TEMP/helper.py\" run",
        "set -e; scripts/tool.py",
        # A quoted interpreter in command position.
        '"/usr/bin/python3" scripts/x.py',
        "'python3' -c pass",
        'out="$("python3" - <<\'PY\'',
        # Redirections before the program, and a program without a .py suffix.
        "python3 2>&1 -c pass",
        "python3 >/dev/null scripts/tool",
        "python3 tool",
        "exec python3 runner",
        # A script whose path starts with a quoted variable.
        '"$GITHUB_WORKSPACE"/scripts/x.py',
        "\"$ROOT\"/scripts/x.py --flag",
        # A variable command whose first argument is a Python script.
        '"$interpreter" scripts/x.py',
        "$tool \"$RUNNER_TEMP/helper.py\" run",
        # Wrapper words, and their own arguments, before a quoted interpreter.
        'timeout 60 "python3" scripts/x.py',
        'sudo -E "python3" x.py',
        # A replacement-string argument is not a brace group.
        'xargs -I {} "python3" scripts/x.py',
        'xargs -I {} python3 tool',
        'xargs -I{} python3 tool',
        'sudo -u runner -E "python3" -c pass',
        'nice -n 10 "python3" -c pass',
        'stdbuf -oL "python3" scripts/x.py',
        'xargs -0 -r "python3" scripts/x.py',
        'env -u GH_TOKEN "python3" scripts/x.py',
        # A variable command's options before the script it runs.
        '"$interp" -u scripts/x.py',
        '"$interp" -X utf8 scripts/x.py',
    )
    isolated = (
        "python3 -I - <<'PY'",
        "exec /usr/bin/python3 -I - load \"$profile\"",
        "python3 -u -I -c 'print(1)'",
        '"/usr/bin/python3", "-I", "-c", source,',
        "subprocess.run([sys.executable, \"-I\", script])",
        "command -v python3 >/dev/null",
        "# python3 - <<'PY' in a comment",
        "apt-get install python3-yaml",
        "sys.exit(\"requires the runner's python3 parser\")",
        "python3 -Ic 'print(1)'",
        "python3 -IS - <<'PY'",
        "python3 -I - <<'PY'; python3 -I -c pass && python3 -Ic pass | python3 -I -",
        "python3 \\\n  -I - <<'PY'",
        "printf '%s' \"$source\" | python3 -I",
        "bash -c 'python3 -I -'",
        "\"$python\" -I - <<'PY'",
        "$py -Ic pass",
        "\"$tool\" --version",
        "echo \"$python_version\"",
        "python3 -I scripts/tool.py",
        "curl -fsSL \"$url\" -o \"$RUNNER_TEMP/helper.py\"",
        "sha256sum scripts/tool.py",
        "install -m 0555 scripts/a.py \"$RUNNER_TEMP/a.py\"",
        'f"{contract_ref}/scripts/changelog.py"',
        # A quoted interpreter inside an argument list or call is a list element.
        '["python3", "-I", script]',
        'shutil.which("python3")',
        "print('python3')",
        '"$interpreter" -I scripts/x.py',
        '"$GITHUB_WORKSPACE"/scripts/x.sh',
        'timeout 60 "python3" -I scripts/x.py',
        'sudo -E "python3" -I x.py',
        '"$interp" -u -I scripts/x.py',
        'timeout 60 "$tool" --config x.json',
    )
    for sample in non_isolated:
        assert non_isolated_invocations(sample), f"scanner missed a non-isolated invocation: {sample!r}"
    for sample in isolated:
        assert not non_isolated_invocations(sample), f"scanner flagged an isolated or non-invocation: {sample!r}"
    multi_line = '(\n    "/usr/bin/python3",\n    "-c",\n    source,\n)'
    assert non_isolated_invocations(multi_line), "scanner missed a multi-line argument tuple"
    assert not non_isolated_invocations(multi_line.replace('"-c"', '"-I",\n    "-c"'))
    assert not non_isolated_invocations('"/usr/bin/python3", "-Ic", source,')
    assert non_isolated_invocations('"/usr/bin/python3", "-cI", source,'), "-cI runs the command 'I'"
    for shell in ("python {0}", "python3 {0}", "/usr/bin/python3 -u {0}"):
        assert step_shell_findings({"shell": shell}, {}, {}), f"scanner missed a Python step shell: {shell!r}"
    assert not step_shell_findings({"shell": "python3 -I {0}"}, {}, {})
    assert step_shell_findings({}, {"defaults": {"run": {"shell": "python {0}"}}}, {})
    assert step_shell_findings({}, {}, {"defaults": {"run": {"shell": "python {0}"}}})
    assert not step_shell_findings({"shell": "bash"}, {}, {})


# An isolated start of a script file: the interpreter, its options (``-X`` takes
# a value), then the script path.
ISOLATED_SCRIPT_START = re.compile(
    r"""(?<![\w.$/-])(?:[^\s"'`=;|&()$]*/)?python3?(?:\.\d+)?"""
    r"""(?:\s+(?:-X\s+\S+|-[A-Za-z]+))*\s+["']?(?P<path>[^\s"']+?(?:\.py\b|(?=["'\s])))"""
)
SCRIPT_ASSIGNMENT = re.compile(r"""(?m)^\s*([A-Za-z_]\w*)=["']?([^\s"';]+\.py)["']?\s*$""")
VARIABLE_REFERENCE = re.compile(r"^\$\{?(?P<name>[A-Za-z_]\w*)\}?$")
IMPORT_PROBE = (
    "import runpy, sys; "
    "runpy.run_path(sys.argv[1], run_name='__isolated_import_probe__')"
)


def repository_script(started_path):
    """The repository file a workflow's script path names, if it names one."""
    if "scripts/" in started_path:
        candidate = ROOT / ("scripts/" + started_path.rsplit("scripts/", 1)[1])
        return candidate if candidate.is_file() else None
    if started_path.startswith(("$RUNNER_TEMP/", "${RUNNER_TEMP}/")) and started_path.count("/") == 1:
        name = started_path.split("/", 1)[1]
        for directory in ("scripts", "scripts/ci-gate"):
            if (ROOT / directory / name).is_file():
                return ROOT / directory / name
    return None


def isolated_script_starts(sources):
    """Repository scripts that some workflow starts with an isolated interpreter."""
    started = set()
    for _, text in sources:
        document = yaml.safe_load(text)
        for job in (document.get("jobs") or {}).values():
            for step in job.get("steps", []) or []:
                script = step.get("run")
                if not isinstance(script, str):
                    continue
                joined = re.sub(r"\\\n\s*", " ", script)
                # A script path is often held in a step variable or a shell assignment.
                variables = {
                    name: str(value) for name, value in (step.get("env") or {}).items()
                }
                variables.update(SCRIPT_ASSIGNMENT.findall(joined))
                for match in ISOLATED_SCRIPT_START.finditer(joined):
                    started_path = VARIABLE_REFERENCE.sub(
                        lambda reference: variables.get(reference.group("name"), reference.group(0)),
                        match.group("path"),
                    )
                    path = repository_script(started_path)
                    if path is not None:
                        started.add(path)
    return sorted(started)


def import_probe_failures(scripts):
    """Scripts whose module-level imports fail when Python starts isolated.

    ``-I`` leaves a script's own directory off the module path, so a script that
    imports a sibling must add that directory itself. The probe runs each
    script's module body, not its ``__main__`` block, from an unrelated
    directory.
    """
    failures = []
    with tempfile.TemporaryDirectory() as unrelated:
        for script in scripts:
            environment = {
                name: value for name, value in os.environ.items() if not name.startswith("PYTHON")
            }
            result = subprocess.run(
                [sys.executable, "-I", "-c", IMPORT_PROBE, str(script)],
                cwd=unrelated,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 0:
                reason = (result.stderr.strip().splitlines() or ["no diagnostic"])[-1]
                failures.append(f"{script.relative_to(ROOT)}: {reason}")
    return failures


def extracted_step(workflow, step_name):
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    matches = [
        step
        for job in document["jobs"].values()
        for step in job.get("steps", []) or []
        if step.get("name") == step_name
    ]
    assert matches, f"{workflow.name} has no step named {step_name!r}"
    return matches[0]


def assert_dependency_lock_validator_ignores_checkout_modules(workflow):
    step = extracted_step(workflow, "Validate approved internal dependency lock")
    with tempfile.TemporaryDirectory(dir=str(Path.home())) as temporary:
        workspace = Path(temporary) / "workspace"
        workspace.mkdir()
        marker_directory = Path(temporary) / "markers"
        marker_directory.mkdir()
        (workspace / "package.json").write_text(
            '{"name":"consumer","version":"1.0.0"}\n', encoding="utf-8"
        )
        (workspace / "package-lock.json").write_text(
            json.dumps({
                "name": "consumer",
                "version": "1.0.0",
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "consumer", "version": "1.0.0"},
                    "node_modules/@verjson/identity-contracts": {
                        "version": "0.2.2",
                        "resolved": "https://npm.pkg.github.com/download/@verjson/identity-contracts/0.2.2/"
                        + "0" * 40,
                        "integrity": "sha512-"
                        + base64.b64encode(hashlib.sha512(b"identity-contracts").digest()).decode(),
                    },
                },
            }),
            encoding="utf-8",
        )
        planted = ("json", "base64", "re", "pathlib", "urllib")
        for name in planted:
            (workspace / f"{name}.py").write_text(
                f"open({str(marker_directory / name)!r}, 'w').close()\n",
                encoding="utf-8",
            )
        environment = {
            name: value for name, value in os.environ.items()
            if not name.startswith("PYTHON") and name != "BASH_ENV"
        }
        for name, value in (step.get("env") or {}).items():
            environment[name] = "" if "${{" in str(value) else str(value)
        environment.update({
            "APPROVED_INTERNAL_PACKAGES": "@verjson/identity-contracts",
            "APPROVED_INTERNAL_SCOPES": "@verjson",
            "COMPATIBILITY_RANGES": "",
            "NESTED_MANIFESTS": "",
            "PRIVATE_CACHE_ENTRIES": str(Path(temporary) / "private-entries"),
            "TRUSTED_PACKAGE_POLICY": "",
            "PACKAGE_MANAGER": "npm",
        })
        result = subprocess.run(
            ["bash", "-e", "-c", step["run"]],
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        executed = sorted(path.name for path in marker_directory.iterdir())
        assert not executed, (
            f"{workflow.name}: the dependency lock validator imported checkout modules {executed!r}"
        )
        assert result.returncode == 0, (
            f"{workflow.name}: the dependency lock validator rejected a benign lock: {result.stderr}"
        )


def main():
    assert_scanner_recognizes_invocation_forms()
    sources = workflow_sources()
    findings = all_findings(sources)
    assert not findings, (
        "inline Python interpreters must run isolated with -I:\n  " + "\n  ".join(findings)
    )
    started = isolated_script_starts(sources)
    assert len(started) >= 30, f"expected the workflows to start many repository scripts, found {len(started)}"
    failures = import_probe_failures(started)
    assert not failures, (
        "scripts started with -I must import isolated:\n  " + "\n  ".join(failures)
    )
    for workflow in NODE_CI_WORKFLOWS:
        assert_dependency_lock_validator_ignores_checkout_modules(workflow)
    print("every workflow's inline Python interpreters run isolated")


if __name__ == "__main__":
    main()
