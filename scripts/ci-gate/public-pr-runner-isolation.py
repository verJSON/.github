#!/usr/bin/env python3
"""Prove pull-request code in a public repository never reaches a persistent runner.

    public-pr-runner-isolation.py [--repository OWNER/NAME] <workflow-dir>

Exit 0 is "scanned and clean", 1 is "policy violation", 2 is "undetermined" —
the same three-way contract as hosted-selector-policy.py, so a sweep that could
not decide never reads as a sweep that found nothing.

Every job in a `pull_request` workflow has its `runs-on` evaluated as GitHub
Actions would for a pull request in a public repository. Organization variables
are not known statically, so every variable the selector names is enumerated as
both unset and set: the untrusted-lane family to its ADR 0147 ephemeral hosted
value, every other variable to a persistent self-hosted selector. A chain such as
`vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]'` is
therefore a violation: with the untrusted lane unset it lands on the fallback.

Verjson/.github ADR 0231.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import sys

import yaml

# ADR 0147 fixes these six names to the ephemeral `["ubuntu-24.04"]` selector and
# ADR 0231 withdraws its rollback to the persistent pool. They are the only
# variables this policy may assume land on ephemeral capacity.
EPHEMERAL_LANE_VARIABLES = frozenset(
    {
        "CI_LANE_UNTRUSTED",
        "CI_RUNNER_UNTRUSTED",
        "CI_RUNNER_ISOLATED",
        "VERJSON_LANE_UNTRUSTED",
        "VERJSON_RUNNER_UNTRUSTED",
        "VERJSON_RUNNER_ISOLATED",
    }
)
EPHEMERAL_SELECTOR = '["ubuntu-24.04"]'
PERSISTENT_SELECTOR = '["self-hosted","general"]'
HOSTED_LABEL = re.compile(r"^(?:ubuntu|windows|macos)-[A-Za-z0-9._-]+$")


class Undetermined(Exception):
    """The checker could not decide. Never rendered as clean."""


class _Unknown:
    def __repr__(self) -> str:
        return "<unknown>"


UNKNOWN = _Unknown()


class _EvaluationError:
    def __repr__(self) -> str:
        return "<error>"


# A runs-on expression that fails to evaluate fails the job before any step,
# so no pull-request code runs on that branch.
EVALUATION_ERROR = _EvaluationError()


# ---------------------------------------------------------------------------
# Expression language
# ---------------------------------------------------------------------------

TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<string>'(?:''|[^'])*')"
    r"|(?P<number>-?\d+(?:\.\d+)?)"
    r"|(?P<operator>&&|\|\||==|!=|<=|>=|<|>|!|\(|\)|\[|\]|,|\.)"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_-]*)"
    r")"
)


def tokenize(text: str) -> list[tuple[str, object]]:
    tokens: list[tuple[str, object]] = []
    position = 0
    text = text.rstrip()
    while position < len(text):
        match = TOKEN.match(text, position)
        if not match or match.end() == position:
            raise Undetermined(f"cannot tokenize expression near {text[position:position + 20]!r}")
        position = match.end()
        if match.group("string") is not None:
            tokens.append(("literal", match.group("string")[1:-1].replace("''", "'")))
        elif match.group("number") is not None:
            tokens.append(("literal", float(match.group("number"))))
        elif match.group("operator") is not None:
            tokens.append(("op", match.group("operator")))
        else:
            name = match.group("name")
            if name in ("true", "false"):
                tokens.append(("literal", name == "true"))
            elif name == "null":
                tokens.append(("literal", None))
            else:
                tokens.append(("name", name))
    return tokens


class Parser:
    def __init__(self, text: str) -> None:
        self.tokens = tokenize(text)
        self.index = 0

    def peek(self) -> tuple[str, object] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self, kind: str, value: object = None) -> tuple[str, object]:
        token = self.peek()
        if token is None or token[0] != kind or (value is not None and token[1] != value):
            raise Undetermined(f"unexpected token {token!r}; expected {value or kind}")
        self.index += 1
        return token

    def at(self, value: str) -> bool:
        token = self.peek()
        return token is not None and token[0] == "op" and token[1] == value

    def parse(self):
        node = self.disjunction()
        if self.peek() is not None:
            raise Undetermined(f"trailing token {self.peek()!r}")
        return node

    def disjunction(self):
        node = self.conjunction()
        while self.at("||"):
            self.index += 1
            node = ("or", node, self.conjunction())
        return node

    def conjunction(self):
        node = self.comparison()
        while self.at("&&"):
            self.index += 1
            node = ("and", node, self.comparison())
        return node

    def comparison(self):
        node = self.unary()
        while self.peek() is not None and self.peek()[0] == "op" and self.peek()[1] in (
            "==", "!=", "<", "<=", ">", ">=",
        ):
            operator = self.take("op")[1]
            node = ("compare", operator, node, self.unary())
        return node

    def unary(self):
        if self.at("!"):
            self.index += 1
            return ("not", self.unary())
        return self.primary()

    def primary(self):
        token = self.peek()
        if token is None:
            raise Undetermined("unexpected end of expression")
        if token[0] == "literal":
            self.index += 1
            return ("literal", token[1])
        if self.at("("):
            self.index += 1
            node = self.disjunction()
            self.take("op", ")")
            return node
        name = self.take("name")[1]
        if self.at("("):
            self.index += 1
            arguments = []
            if not self.at(")"):
                arguments.append(self.disjunction())
                while self.at(","):
                    self.index += 1
                    arguments.append(self.disjunction())
            self.take("op", ")")
            return ("call", name.lower(), arguments)
        path = [name]
        while True:
            if self.at("."):
                self.index += 1
                path.append(self.take("name")[1])
            elif self.at("["):
                self.index += 1
                key = self.take("literal")[1]
                self.take("op", "]")
                path.append(str(key))
            else:
                return ("ref", tuple(path))


def references(node) -> set[tuple[str, ...]]:
    kind = node[0]
    if kind == "ref":
        return {node[1]}
    if kind == "literal":
        return set()
    if kind == "call":
        return set().union(*(references(argument) for argument in node[2])) if node[2] else set()
    if kind == "compare":
        return references(node[2]) | references(node[3])
    return set().union(*(references(child) for child in node[1:]))


def _unique(values: list) -> list:
    seen: list = []
    for value in values:
        if not any(value is other or (type(value) is type(other) and value == other) for other in seen):
            seen.append(value)
    return seen


def truthiness(value) -> list[bool]:
    if value is UNKNOWN:
        return [True, False]
    if value is EVALUATION_ERROR:
        return [False]
    if value is None or value is False or value == "":
        return [False]
    if isinstance(value, float) and (value == 0 or value != value):
        return [False]
    return [True]


def _number(value) -> float:
    if value is None or value is False:
        return 0.0
    if value is True:
        return 1.0
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return 0.0
        try:
            return float(stripped)
        except ValueError:
            return float("nan")
    return float("nan")


def compare(operator: str, left, right) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        left, right = left.lower(), right.lower()
    elif type(left) is not type(right) or isinstance(left, (tuple, dict)):
        if isinstance(left, (tuple, dict)) and left is right:
            pass
        else:
            left, right = _number(left), _number(right)
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    try:
        return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[operator]
    except TypeError:
        return False


def _freeze(value):
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    return value


def from_json(value):
    if value is UNKNOWN:
        return UNKNOWN
    if not isinstance(value, str):
        return EVALUATION_ERROR
    try:
        return _freeze(json.loads(value))
    except ValueError:
        return EVALUATION_ERROR


def evaluate(node, resolve) -> list:
    """Every value the expression can produce, given a reference resolver."""
    kind = node[0]
    if kind == "literal":
        return [node[1]]
    if kind == "ref":
        return resolve(node[1])
    if kind == "not":
        return _unique([not truth for value in evaluate(node[1], resolve) for truth in truthiness(value)])
    if kind in ("and", "or"):
        results: list = []
        for value in evaluate(node[1], resolve):
            for truth in truthiness(value):
                short_circuits = (not truth) if kind == "and" else truth
                if short_circuits:
                    results.append(value)
                else:
                    results.extend(evaluate(node[2], resolve))
        return _unique(results)
    if kind == "compare":
        results = []
        for left in evaluate(node[2], resolve):
            for right in evaluate(node[3], resolve):
                if left is UNKNOWN or right is UNKNOWN:
                    results.extend([True, False])
                else:
                    results.append(compare(node[1], left, right))
        return _unique(results)
    if kind == "call":
        name, arguments = node[1], node[2]
        if name == "fromjson" and len(arguments) == 1:
            return _unique([from_json(value) for value in evaluate(arguments[0], resolve)])
        return [UNKNOWN]
    raise Undetermined(f"unknown expression node {kind}")


FULL_EXPRESSION = re.compile(r"^\s*\$\{\{(.*)\}\}\s*$", re.S)


# ---------------------------------------------------------------------------
# Workflow model
# ---------------------------------------------------------------------------


def load_workflow(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as error:
        raise Undetermined(f"{path}: cannot parse workflow: {error}") from error
    if not isinstance(document, dict):
        raise Undetermined(f"{path}: workflow is not a mapping")
    return document


def triggers(document: dict) -> set[str]:
    trigger = document.get("on", document.get(True))
    if isinstance(trigger, str):
        return {trigger}
    if isinstance(trigger, list):
        return {str(item) for item in trigger}
    if isinstance(trigger, dict):
        return {str(key) for key in trigger}
    return set()


class Context:
    def __init__(self, repository: str, variables: dict[str, str]) -> None:
        self.repository = repository
        self.variables = variables

    def resolve(self, path: tuple[str, ...]) -> list:
        lowered = tuple(part.lower() for part in path)
        if lowered == ("github", "event_name"):
            return ["pull_request"]
        if lowered == ("github", "repository"):
            return [self.repository]
        if lowered == ("github", "repository_owner"):
            return [self.repository.split("/")[0]]
        if lowered == ("github", "event", "repository", "private"):
            return [False]
        if lowered == ("github", "event", "repository", "visibility"):
            return ["public"]
        if lowered[0] == "vars" and len(path) == 2:
            return [self.variables.get(path[1], "")]
        return [UNKNOWN]


def variable_assignments(names: set[str]):
    ordered = sorted(names)
    choices = [
        ("", EPHEMERAL_SELECTOR if name in EPHEMERAL_LANE_VARIABLES else PERSISTENT_SELECTOR)
        for name in ordered
    ]
    for values in itertools.product(*choices):
        yield dict(zip(ordered, values))


def classify_labels(value) -> str:
    """'ephemeral', 'persistent', 'error', or raise Undetermined."""
    if value is EVALUATION_ERROR:
        return "error"
    if value is UNKNOWN:
        raise Undetermined("runner selector depends on a value this checker cannot resolve")
    labels = [value] if isinstance(value, str) else value
    if not isinstance(labels, tuple) and not isinstance(labels, list):
        raise Undetermined(f"runner selector resolved to a non-label value {value!r}")
    if any(label is UNKNOWN for label in labels):
        raise Undetermined("runner selector depends on a value this checker cannot resolve")
    if len(labels) == 1 and isinstance(labels[0], str) and HOSTED_LABEL.match(labels[0]):
        return "ephemeral"
    return "persistent"


def selector_outcomes(runs_on, context: Context) -> list:
    if isinstance(runs_on, str):
        match = FULL_EXPRESSION.match(runs_on)
        if match:
            return evaluate(Parser(match.group(1)).parse(), context.resolve)
        if "${{" in runs_on:
            raise Undetermined("runner selector interpolates an expression into a string")
        return [runs_on]
    if isinstance(runs_on, list):
        labels = []
        for item in runs_on:
            if not isinstance(item, str) or "${{" in item:
                if isinstance(item, str) and item.strip() == "self-hosted":
                    labels.append(item)
                    continue
                labels.append(UNKNOWN)
            else:
                labels.append(item)
        if "self-hosted" in labels:
            return [("self-hosted",)]
        return [tuple(labels)]
    if isinstance(runs_on, dict):
        return [("group", str(runs_on.get("group", "")))]
    raise Undetermined(f"unsupported runs-on shape {runs_on!r}")


def selector_variables(runs_on) -> set[str]:
    text = runs_on if isinstance(runs_on, str) else ""
    match = FULL_EXPRESSION.match(text)
    if not match:
        return set()
    return {path[1] for path in references(Parser(match.group(1)).parse())
            if len(path) == 2 and path[0].lower() == "vars"}


def check_job(path: str, name: str, job: dict, repository: str) -> list[str]:
    runs_on = job.get("runs-on")
    if runs_on is None:
        raise Undetermined(f"{path}: job '{name}' has no runs-on")
    violations = []
    for assignment in variable_assignments(selector_variables(runs_on)):
        context = Context(repository, assignment)
        for outcome in selector_outcomes(runs_on, context):
            try:
                verdict = classify_labels(outcome)
            except Undetermined as error:
                raise Undetermined(f"{path}: job '{name}': {error}") from error
            if verdict == "persistent":
                shown = {key: value or "<unset>" for key, value in assignment.items()}
                violations.append(
                    f"{path}: job '{name}': pull_request in a public repository can resolve "
                    f"to self-hosted or non-ephemeral runner labels {list(outcome)!r} "
                    f"(variables {shown})"
                )
                return violations
    return violations


def check_tree(directory: str, repository: str) -> list[str]:
    violations: list[str] = []
    names = sorted(
        name for name in os.listdir(directory) if name.endswith((".yml", ".yaml"))
    )
    if not names:
        raise Undetermined(f"{directory}: no workflow files to scan")
    for name in names:
        path = os.path.join(directory, name)
        document = load_workflow(path)
        if "pull_request" not in triggers(document):
            continue
        for job_name, job in (document.get("jobs") or {}).items():
            violations.extend(check_job(name, str(job_name), job, repository))
    return violations


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repository", default="Verjson/.github")
    parser.add_argument("workflow_dir")
    arguments = parser.parse_args(argv)
    try:
        violations = check_tree(arguments.workflow_dir, arguments.repository)
    except Undetermined as error:
        print(f"UNDETERMINED: {error}", file=sys.stderr)
        return 2
    for violation in violations:
        print(f"VIOLATION: {violation}")
    if violations:
        return 1
    print(f"clean: no pull_request job in {arguments.workflow_dir} can reach a persistent runner")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
