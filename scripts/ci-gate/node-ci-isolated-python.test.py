#!/usr/bin/env python3
"""Every inline Python interpreter in the Node CI workflows runs isolated.

The secretless Node workflows run their own Python helpers with the
pull-request checkout, or directories it can write, as the working directory.
``-I`` keeps that directory, a script's directory, the user site directory,
and ``PYTHON*`` variables out of module resolution.

This contract parses every ``run:`` block in the canonical and generated
protected workflows and fails on any interpreter invocation without ``-I``
(combined short flags such as ``-Ic`` count when ``I`` precedes an option that
takes an argument). After joining line continuations it checks every
occurrence on a line, so a later call after ``;``, ``&&``, ``||``, or ``|``
is checked on its own, as is a bare ``| python3``. It also covers calls nested
in ``bash -c '...'``, interpreters held in a shell variable (any variable whose
name contains ``py``, and any variable command reading ``-``, ``-c``, or
``-m``), argument lists embedded in Python (``("/usr/bin/python3", "-I", ...)``
and ``sys.executable``), and a step, job, or workflow ``shell:`` naming Python.
A behavioral regression then runs a representative extracted validator in a
fixture checkout carrying same-named modules.

Documented limitations: an interpreter path computed inside Python (for
example from ``shutil.which`` or a variable passed to ``subprocess``), or
reached through shell indirection such as ``eval``, command substitution
(``"$(command -v python3)" -``), functions, or aliases,
is not detected lexically. Review those forms by hand.
"""
import base64
import hashlib
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
WORKFLOWS = (
    ROOT / ".github/workflows/node-ci.yml",
    ROOT / ".github/workflows/node-ci-protected.yml",
)
# Reviewed exceptions as (workflow name, step name, invocation text). Keep it
# empty: an interpreter that cannot run isolated must be fixed, not listed.
ALLOWED_NON_ISOLATED = frozenset()

# Any directory prefix counts: toolcache and venv interpreters are still Python.
INTERPRETER = r"(?:[^\s\"'`=;|&()$]*/)?python(?:3(?:\.\d+)?)?"
INTERPRETER_TOKEN = re.compile(r"(?<![\w.$/-])" + INTERPRETER + r"(?![\w./-])")
LIST_INVOCATION = re.compile(
    r"""(?:["']""" + INTERPRETER + r"""["']|sys\.executable)\s*,\s*(?P<next>[^,\])\n]*)"""
)
SEGMENT_SEPARATOR = re.compile(r"\|\||&&|;|\||\n")
VARIABLE_COMMAND = re.compile(r"""^["']?\$\{?(?P<name>[A-Za-z_]\w*)\}?["']?$""")
SHELL_PREFIX_WORDS = frozenset(("exec", "command", "env", "time", "nohup"))
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
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
        if before.endswith(("'", '"')) and after[:1] == before[-1]:
            continue  # a quoted string literal; argument lists are checked separately
        if re.search(r"(?:command\s+-v|which|type|install)\s+(?:\S+\s+)*$", before):
            continue
        tokens = segment_after(line, match.end()).split()
        cleaned = [token.strip("'\")") for token in tokens]
        if cleaned and cleaned[0] and not re.match(r"""[-"'$/.<>&{]|\S+\.py\b""", tokens[0]):
            continue  # prose such as "the runner's python3 parser"
        if not flags_are_isolated(cleaned):
            findings.append(match.group(0))
    return findings


def variable_interpreter_findings(line):
    findings = []
    for segment in SEGMENT_SEPARATOR.split(line):
        tokens = segment.split()
        while tokens and (tokens[0] in SHELL_PREFIX_WORDS or ASSIGNMENT.match(tokens[0])):
            tokens.pop(0)
        if not tokens:
            continue
        variable = VARIABLE_COMMAND.match(tokens[0])
        if variable is None:
            continue
        arguments = tokens[1:]
        reads_stdin = bool(arguments) and arguments[0] == "-"
        names_python = re.search(r"py", variable.group("name"), re.IGNORECASE) is not None
        if (names_python or reads_stdin or (arguments and arguments[0] in ("-c", "-m"))) \
                and not flags_are_isolated(arguments):
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


def workflow_findings(path):
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    findings = []
    for job_name, job in document.get("jobs", {}).items():
        for step in job.get("steps", []) or []:
            script = step.get("run")
            if not isinstance(script, str):
                continue
            step_name = step.get("name", step.get("id", "<unnamed>"))
            invocations = non_isolated_invocations(script) + step_shell_findings(step, job, document)
            for invocation in invocations:
                key = (path.name, step_name, invocation)
                if key not in ALLOWED_NON_ISOLATED:
                    findings.append(f"{path.name}: {job_name} / {step_name}: {invocation}")
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
    findings = [finding for workflow in WORKFLOWS for finding in workflow_findings(workflow)]
    assert not findings, (
        "inline Python interpreters must run isolated with -I:\n  " + "\n  ".join(findings)
    )
    for workflow in WORKFLOWS:
        assert_dependency_lock_validator_ignores_checkout_modules(workflow)
    print("node-ci inline Python interpreters run isolated")


if __name__ == "__main__":
    main()
