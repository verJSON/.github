#!/usr/bin/env python3
"""Prove pull-request code in a public repository never reaches a persistent runner.

    public-pr-runner-isolation.py [--repository OWNER/NAME] [--pending FILE] <workflow-dir>

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

Under pull_request_target, workflow_run and issue_comment, which run the base
workflow with base privileges and can be triggered by fork activity, any job
that can resolve to a persistent runner fails unless `--reviewed` lists it by
path, job, and a digest of its full content, inherited workflow settings, and
calling jobs. The checker over-approximates what such a job puts on disk: any
fetch, runtime tree change, non-default shell, unbounded checkout, or head
detection that finds a materializing or undeterminable pattern leaves the job
unresolved, and its entry must carry a `dynamic:` field naming every one of
those reason categories (ADR 0235).

Verjson/.github ADR 0235.
"""

from __future__ import annotations

import argparse
import functools
import glob
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
from typing import NamedTuple

import yaml

# ADR 0147 fixes these six names to the ephemeral `["ubuntu-24.04"]` selector and
# ADR 0235 withdraws its rollback to the persistent pool. They are the only
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
# A caller-supplied input set from an expression is attacker-shaped in a public
# repository: a fork can edit the calling workflow. Each such input is tried as
# every value that changes routing.
ADVERSARIAL_INPUT_VALUES = ("", PERSISTENT_SELECTOR, EPHEMERAL_SELECTOR, True, False)
# Standard GitHub-hosted images only, versions shaped like #1826's guard. A
# self-hosted runner may carry any custom label, so a family prefix such as
# `ubuntu-gpu-selfhosted` proves nothing; larger runners (`-large`, `-xlarge`)
# are billed and not reviewed here. ASCII digits only: `\d` would accept
# fullwidth digits.
HOSTED_LABEL = re.compile(
    r"^(?:"
    r"ubuntu-(?:latest|[0-9]{2}\.[0-9]{2})(?:-arm)?"
    r"|windows-(?:latest|[0-9]{4}|11-arm)"
    r"|macos-(?:latest|[0-9]{2}(?:-intel)?)"
    r")$"
)


class Violation(NamedTuple):
    path: str
    job: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: job '{self.job}': {self.detail}"


DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


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
    r"|(?P<operator>&&|\|\||==|!=|<=|>=|<|>|!|\(|\)|\[|\]|,|\.|\*)"
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
                if self.at("*"):
                    # An object filter (`labels.*.name`) yields an array this
                    # checker never needs to know; it resolves as unknown.
                    self.index += 1
                    path.append("*")
                else:
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


ENUMERATED_SCOPES = ("vars", "inputs")


def enumerated_references(node) -> set[tuple[str, str]]:
    """The free `vars.*` / `inputs.*` names an expression reads."""
    return {
        (path[0].lower(), path[1])
        for path in references(node)
        if path[0].lower() in ENUMERATED_SCOPES and len(path) == 2
    } | {
        (path[0].lower(), ".".join(path[1:]))
        for path in references(node)
        if path[0].lower() in ENUMERATED_SCOPES and len(path) != 2
    }


def exact_comparison(node):
    """`(scope, name, literal)` when a comparison is a bare free reference
    against a literal under `==` / `!=`, which enumeration decides exactly by
    trying the reference as that literal; otherwise None."""
    if node[0] != "compare" or node[1] not in ("==", "!="):
        return None
    for reference, other in ((node[2], node[3]), (node[3], node[2])):
        if (
            reference[0] == "ref"
            and len(reference[1]) == 2
            and reference[1][0].lower() in ENUMERATED_SCOPES
            and other[0] == "literal"
        ):
            return reference[1][0].lower(), reference[1][1], other[1]
    return None


def compared_literals(node) -> dict[tuple[str, str], list]:
    found: dict[tuple[str, str], list] = {}
    kind = node[0]
    if kind == "compare":
        exact = exact_comparison(node)
        if exact is not None:
            found.setdefault(exact[:2], []).append(exact[2])
        children = [node[2], node[3]]
    elif kind == "call":
        children = list(node[2])
    elif kind in ("literal", "ref"):
        children = []
    else:
        children = list(node[1:])
    for child in children:
        for key, values in compared_literals(child).items():
            found.setdefault(key, []).extend(values)
    return found


def as_variable_text(value) -> str:
    """Organization variables are strings; render a compared literal as one."""
    if value is None:
        return ""
    if value is True or value is False:
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


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
    """Actions semantics: case-insensitive strings, numeric coercion otherwise."""
    if isinstance(left, tuple) or isinstance(right, tuple):
        # Arrays and objects compare by identity, which no selector relies on.
        identical = left is right
        return identical if operator == "==" else not identical if operator == "!=" else False
    if isinstance(left, str) and isinstance(right, str):
        left, right = left.lower(), right.lower()
    elif type(left) is not type(right):
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


STATUS_FUNCTIONS = frozenset({"success", "failure", "cancelled", "always"})


def evaluate(node, resolve, call=None) -> list:
    """Every value the expression can produce, given a reference resolver."""
    kind = node[0]
    if kind == "literal":
        return [node[1]]
    if kind == "ref":
        return resolve(node[1])
    if kind == "not":
        return _unique([not truth for value in evaluate(node[1], resolve, call) for truth in truthiness(value)])
    if kind in ("and", "or"):
        results: list = []
        for value in evaluate(node[1], resolve, call):
            for truth in truthiness(value):
                short_circuits = (not truth) if kind == "and" else truth
                if short_circuits:
                    results.append(value)
                else:
                    results.extend(evaluate(node[2], resolve, call))
        return _unique(results)
    if kind == "compare":
        if enumerated_references(node) and exact_comparison(node) is None:
            # The candidates for a free value cannot be chosen to decide this
            # comparison (another reference, a function result, an ordering),
            # so both outcomes are possible.
            return [True, False]
        results = []
        for left in evaluate(node[2], resolve, call):
            for right in evaluate(node[3], resolve, call):
                if left is UNKNOWN or right is UNKNOWN:
                    results.extend([True, False])
                else:
                    results.append(compare(node[1], left, right))
        return _unique(results)
    if kind == "call":
        name, arguments = node[1], node[2]
        if name == "fromjson" and len(arguments) == 1:
            return _unique([from_json(value) for value in evaluate(arguments[0], resolve, call)])
        if name in STATUS_FUNCTIONS and call is not None:
            return call(name)
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
    def __init__(
        self,
        repository: str,
        values: dict[tuple[str, str], object],
        needs: tuple[str, ...] = (),
        unreachable: frozenset[str] = frozenset(),
        event: str = "pull_request",
    ) -> None:
        self.repository = repository
        self.event = event
        self.values = values
        self.needs = needs
        self.unreachable = unreachable

    def status(self, name: str) -> list:
        if name == "always":
            return [True]
        if name == "success" and any(need in self.unreachable for need in self.needs):
            return [False]
        return [True, False]

    def resolve(self, path: tuple[str, ...]) -> list:
        lowered = tuple(part.lower() for part in path)
        if lowered[0] == "needs" and len(path) >= 3 and path[1] in self.unreachable:
            return ["skipped"] if lowered[2] == "result" else [""]
        if lowered == ("github", "event_name"):
            # A called workflow sees its caller's event, which a standalone
            # workflow_call scan cannot know: try every event a caller can have.
            return list(CALLER_EVENTS) if self.event == "workflow_call" else [self.event]
        if lowered == ("github", "repository"):
            return [self.repository]
        if lowered == ("github", "repository_owner"):
            return [self.repository.split("/")[0]]
        if lowered == ("github", "event", "repository", "private"):
            return [False]
        if lowered == ("github", "event", "repository", "visibility"):
            return ["public"]
        if lowered[0] == "vars" and len(path) == 2:
            return [self.values.get(("vars", path[1]), "")]
        if lowered[0] == "inputs" and len(path) == 2:
            return [self.values.get(("inputs", path[1]))]
        return [UNKNOWN]


def assignments(nodes, bindings: dict[str, tuple]):
    """Every combination of the variables and inputs the expressions read.

    Enumerated jointly rather than per reference, so `vars.X != '' &&
    fromJSON(vars.X)` sees one value of X, as Actions does.
    """
    present = [node for node in nodes if node is not None]
    names = sorted(
        (scope, name)
        for node in present
        for scope, name in enumerated_references(node)
        if "." not in name
    )
    names = sorted(set(names))
    literals: dict[tuple[str, str], list] = {}
    for node in present:
        for key, values in compared_literals(node).items():
            literals.setdefault(key, []).extend(values)
    choices = []
    for scope, name in names:
        if scope == "vars":
            base = ["", EPHEMERAL_SELECTOR if name in EPHEMERAL_LANE_VARIABLES else PERSISTENT_SELECTOR]
            extra = [as_variable_text(value) for value in literals.get((scope, name), [])]
        else:
            base = list(bindings.get(name, (None,)))
            # A fixed binding is exact; only attacker-shaped inputs take every
            # literal their consumer compares them against.
            adversarial = bindings.get(name) is ADVERSARIAL_INPUT_VALUES
            extra = literals.get((scope, name), []) if adversarial else []
        choices.append(tuple(_unique(base + extra)))
    for values in itertools.product(*choices):
        yield dict(zip(names, values))


def describe(assignment: dict[tuple[str, str], object]) -> str:
    return ", ".join(
        f"{scope}.{name}={'<unset>' if value in ('', None) else value}"
        for (scope, name), value in assignment.items()
    ) or "no variables or inputs"


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
    if len(labels) == 1 and isinstance(labels[0], str) and HOSTED_LABEL.fullmatch(labels[0]):
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


def calls(node) -> set[str]:
    kind = node[0]
    if kind == "call":
        return {node[1]}.union(*(calls(argument) for argument in node[2]))
    if kind in ("literal", "ref"):
        return set()
    if kind == "compare":
        return calls(node[2]) | calls(node[3])
    return set().union(*(calls(child) for child in node[1:]))


def condition_node(condition):
    """Parse a job `if:` and apply Actions' implicit `success() &&`."""
    implicit_success = ("call", "success", [])
    if condition is None:
        return implicit_success
    if isinstance(condition, bool):
        node = ("literal", condition)
    elif isinstance(condition, str):
        match = FULL_EXPRESSION.match(condition)
        node = Parser(match.group(1) if match else condition).parse()
    else:
        raise Undetermined(f"unsupported if: shape {condition!r}")
    if calls(node) & STATUS_FUNCTIONS:
        return node
    return ("and", implicit_success, node)


def selector_node(runs_on):
    match = FULL_EXPRESSION.match(runs_on) if isinstance(runs_on, str) else None
    return Parser(match.group(1)).parse() if match else None


def can_run(condition, context: Context) -> bool:
    return any(
        truth
        for value in evaluate(condition, context.resolve, context.status)
        for truth in truthiness(value)
    )


def job_needs(job: dict) -> tuple[str, ...]:
    needs = job.get("needs") or ()
    return (needs,) if isinstance(needs, str) else tuple(str(need) for need in needs)


def dependency_order(path: str, jobs: dict) -> list[Violation]:
    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in ordered:
            return
        if name in visiting or name not in jobs:
            raise Undetermined(f"{path}: job graph is cyclic or names a missing job '{name}'")
        visiting.add(name)
        for need in job_needs(jobs[name]):
            visit(need)
        visiting.discard(name)
        ordered.append(name)

    for name in jobs:
        visit(name)
    return ordered


def is_reachable(
    job: dict,
    repository: str,
    bindings: dict[str, tuple],
    unreachable: frozenset[str],
    event: str,
) -> bool:
    condition = condition_node(job.get("if"))
    return any(
        can_run(condition, Context(repository, assignment, job_needs(job), unreachable, event))
        for assignment in assignments((condition,), bindings)
    )


def workflow_call_inputs(document: dict) -> dict:
    trigger = document.get("on", document.get(True))
    if not isinstance(trigger, dict) or not isinstance(trigger.get("workflow_call"), dict):
        return {}
    return trigger["workflow_call"].get("inputs") or {}


def callee_bindings(job: dict, callee: dict) -> dict[str, tuple]:
    supplied = job.get("with") or {}
    bindings: dict[str, tuple] = {}
    for name, declaration in workflow_call_inputs(callee).items():
        if name in supplied:
            value = supplied[name]
            if isinstance(value, str) and "${{" in value:
                bindings[name] = ADVERSARIAL_INPUT_VALUES
            else:
                bindings[name] = (value,)
        elif isinstance(declaration, dict) and "default" in declaration:
            bindings[name] = (declaration["default"],)
        else:
            bindings[name] = (None,)
    return bindings


LOCAL_REUSABLE = re.compile(r"^\./\.github/workflows/([A-Za-z0-9._-]+\.ya?ml)$")
MAX_CALL_DEPTH = 4


# Expression references whose value is fixed by the base repository, never by
# the pull request. A pull_request_target checkout pinned to these runs base
# code; any other expression is treated as able to name the head.
BASE_ONLY_REFERENCE = re.compile(
    r"^(?:github\.(?:sha|ref|ref_name|base_ref|repository|workflow_sha|run_id|run_attempt"
    r"|event\.pull_request\.base\.[a-z_.]+|event\.repository\.[a-z_.]+)"
    r"|job\.workflow_(?:sha|repository))$"
)
HEAD_TOKEN = re.compile(
    r"pull_request\.head\.|github\.head_ref|refs/pull/|pull/\$\{\{|merge_commit_sha"
    r"|github\.event\.number|pull_request\.number|\bgh\s+pr\s+(?:diff|checkout)\b"
    r"|\$\{\{\s*inputs\.",
    re.I,
)
# git global options before the subcommand: `-C dir`, `-c k=v`, `--git-dir=x`,
# `--work-tree x`, `--no-pager`.
GIT_OPTIONS = r"(?:-[\w-]+(?:=\S*|[ \t]+(?!-)\S+)?[ \t]+)*"
# Commands that put head-controlled bytes on disk when given a head value.
PR_MATERIALIZE = re.compile(r"\bgh\s+pr\s+(?:checkout|diff)\b")
GIT_MATERIALIZE = re.compile(
    rf"(?:^|[\s;&|(])git[ \t]+{GIT_OPTIONS}"
    r"(?:fetch|checkout|switch|worktree|pull|reset|merge|cherry-pick|clone|am|apply)\b"
)
DOWNLOAD = re.compile(
    r"\bcurl\b|\bwget\b|\bgh\s+api\b[^\n]*\b(?:tarball|zipball|archive)\b"
)
# `gh run download` is deliberately absent: workflow artifacts are not head
# detection; FETCH below leaves any job that downloads them unresolved.
# The same operations written as an argv list or a library call, for program
# text the workflow authors inline (a heredoc into $RUNNER_TEMP).
ARGV_PR_MATERIALIZE = re.compile(r"""["']gh["']\s*,\s*["']pr["']\s*,\s*["'](?:checkout|diff)["']""")
ARGV_MATERIALIZE = re.compile(
    r"""["']git["']\s*,\s*(?:["']-[Cc]["']\s*,\s*[^,\]]+,\s*)*"""
    r"""["'](?:fetch|checkout|switch|worktree|pull|reset|merge|cherry-pick|clone|am|apply)["']"""
    r"""|\burlopen\s*\(|\brequests\.get\s*\("""
)
HEREDOC = re.compile(r"<<-?\s*['\"]?[A-Za-z_][A-Za-z0-9_]*['\"]?")


def materializes(text: str, names_head: bool) -> bool:
    if PR_MATERIALIZE.search(text) or ARGV_PR_MATERIALIZE.search(text):
        return True
    return names_head and bool(
        GIT_MATERIALIZE.search(text) or DOWNLOAD.search(text) or ARGV_MATERIALIZE.search(text)
    )


# Programs whose behavior the checker cannot read from the step text.
OPAQUE_PIPE = re.compile(
    r"\|\s*(?:sudo\s+)?(?:sh|bash|zsh|python3?|node|perl|ruby)\b|\beval\b|\bsource\s+<\("
)


def is_base_only(value) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    if "refs/pull/" in value:
        return False
    for expression in re.findall(r"\$\{\{(.*?)\}\}", value, re.S):
        try:
            node = Parser(expression).parse()
        except Undetermined:
            return False
        for path in references(node):
            if not BASE_ONLY_REFERENCE.match(".".join(path).lower()):
                return False
    return True


INTERPRETER_CALL = re.compile(
    r"(?:^|[\s;&|(])(?:sudo[ \t]+)?(?:bash|sh|zsh|python3?|node|pwsh|perl|ruby)"
    r"((?:[ \t]+-[A-Za-z]+)*)[ \t]+(?!-)(\"[^\"\n]+\"|'[^'\n]+'|[^\s;&|)<>]+)",
    re.M,
)
# A command position: a line start, after `;`, `&`, `|`, `(`, `$(`, a
# backtick, or a compound keyword, past any `NAME=value` prefixes.
COMMAND_POSITION = r"(?:^|[;&|(`]|\$\(|\b(?:then|do|else|exec|time)\b)[ \t]*(?:[A-Za-z_]\w*=\S*[ \t]+)*"
DIRECT_EXEC = re.compile(COMMAND_POSITION + r"(\.\.?/[^\s;&|)<>`]+)", re.M)
# A bare relative command word (`scripts/x.sh`): followed only when it names a
# file in the scanned tree, so `node_modules/.bin/tool` is not a program here.
BARE_EXEC = re.compile(COMMAND_POSITION + r"([A-Za-z0-9_][\w.-]*/[^\s;&|)<>`$\"']+)", re.M)


def programs(run: str, root: str | None = None) -> list[str]:
    """Files a run block executes: `<interpreter> [flags] <path>`, `./path`,
    or, given the scanned root, a bare relative path to a file in it.

    Deliberately a pattern match rather than a shell parser: it only has to
    find candidate programs, and an unreadable one matters only when the same
    step names a head value. `-c`/`-e` inline code is already in the step text;
    `-m module` is opaque.
    """
    joined = run.replace("\\\n", " ")
    found = []
    for match in INTERPRETER_CALL.finditer(joined):
        flags = match.group(1).split()
        if any(flag in ("-c", "-e") or (flag.startswith("-") and "c" in flag[1:] and len(flag) <= 3) for flag in flags):
            continue
        if "-m" in flags:
            found.append("<module>")
            continue
        found.append(match.group(2).strip("\"'"))
    found.extend(match.group(1) for match in DIRECT_EXEC.finditer(joined))
    if root is not None:
        found.extend(
            match.group(1) for match in BARE_EXEC.finditer(joined) if read_program(root, match.group(1)) is not None
        )
    # `program="$RUNNER_TEMP/tool.py"; python3 "$program"`: follow one level of
    # plain assignment so the program's file name can still be matched.
    assigned = {
        match.group(1): match.group(2).strip("\"'")
        for match in re.finditer(r"^\s*([A-Za-z_]\w*)=(\"[^\"\n]*\"|'[^'\n]*'|\S+)\s*$", joined, re.M)
    }
    resolved = []
    for program in found:
        variable = re.fullmatch(r"\$\{?([A-Za-z_]\w*)\}?", program)
        resolved.append(assigned.get(variable.group(1), program) if variable else program)
    return resolved


def inline_source(job: dict, program: str) -> str | None:
    """The run block that writes `program` from a heredoc, if this job has one.

    A program the workflow authors inline is as readable as the step text; the
    match is on the program's file name inside a run block that holds a heredoc.
    """
    name = os.path.basename(program.strip("\"'"))
    if not name or "$" in name:
        return None
    for step in job.get("steps") or []:
        run = step.get("run") if isinstance(step, dict) else None
        if isinstance(run, str) and name in run and HEREDOC.search(run):
            return run
    return None


def read_program(root: str, program: str) -> str | None:
    if "$" in program or program.startswith(("/", "<")):
        return None
    candidate = os.path.normpath(os.path.join(root, program))
    if not candidate.startswith(os.path.normpath(root) + os.sep) or not os.path.isfile(candidate):
        return None
    with open(candidate, encoding="utf-8", errors="replace") as handle:
        return handle.read()


SHELL_SHEBANG = re.compile(r"#![ \t]*(?:/usr)?/bin/(?:env[ \t]+)?(?:ba|z|da)?sh\b")


def is_shell_script(path: str, content: str) -> bool:
    return path.endswith(".sh") or bool(SHELL_SHEBANG.match(content))


def executed_programs(job: dict, root: str, excluded: frozenset[str] = frozenset()) -> dict[str, str]:
    """SHA-256 of every scanned-tree program a job's steps run, and of every
    program those run in turn (to MAX_CLOSURE_DEPTH), keyed by path. Binds
    what a full-checkout job executes without binding the whole tree."""
    found: dict[str, str] = {}
    queue = [
        (without_comments(step["run"]), 0)
        for step in job.get("steps") or []
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    ]
    while queue:
        text, depth = queue.pop()
        for program in programs(text, root):
            content = read_program(root, program)
            relative = os.path.relpath(os.path.normpath(os.path.join(root, program)), root)
            if content is None or relative in found or relative in excluded:
                continue
            found[relative] = hashlib.sha256(content.encode("utf-8", "surrogateescape")).hexdigest()
            # Only a shell script is read with shell grammar; another
            # interpreter's source would match command positions in its
            # string literals and bind most of the tree.
            if depth + 1 < MAX_CLOSURE_DEPTH and is_shell_script(relative, content):
                queue.append((without_comments(content), depth + 1))
    return dict(sorted(found.items()))


def materializes_head(job: dict, workflow_env, root: str, label: str) -> bool:
    """Whether a pull_request_target job can put head-controlled bytes on disk.

    Over-approximates. Head-capable: a checkout whose ref or repository is not
    base-only; `gh pr checkout|diff`; a git fetch/checkout-class command or a
    download (`curl`, `wget`, `gh api …tarball|zipball|archive`) in a job whose
    workflow env, job env, or steps name a head value; a non-checkout action
    whose `with:` names one; or a repository script or inline-authored program
    (heredoc) doing the same. Undetermined: a step that names a head value,
    directly or through a head-valued env name, and hands it to a program the
    checker can read neither from the repository nor from the workflow. Head
    metadata used only for API calls does not count, and neither does a program
    that reads an inherited head-valued env var without its step naming it.
    """
    environments = [workflow_env or {}, job.get("env") or {}]
    head_names = {
        str(name)
        for environment in environments
        if isinstance(environment, dict)
        for name, value in environment.items()
        if HEAD_TOKEN.search(str(value))
    }
    job_text = json.dumps([workflow_env, job], sort_keys=True, default=str)
    names_head = bool(HEAD_TOKEN.search(job_text))

    def step_names_head(step: dict) -> bool:
        text = json.dumps(step, sort_keys=True, default=str)
        if HEAD_TOKEN.search(text):
            return True
        step_env = step.get("env") if isinstance(step.get("env"), dict) else {}
        local = head_names | {str(n) for n, v in step_env.items() if HEAD_TOKEN.search(str(v))}
        return any(
            re.search(rf"\$\{{?{re.escape(name)}\b|env\.{re.escape(name)}\b", text) for name in local
        )

    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            raise Undetermined(f"{label}: unsupported step shape {step!r}")
        uses = str(step.get("uses", ""))
        if uses:
            options = step.get("with") or {}
            if uses.split("@")[0].lower() == "actions/checkout":
                if not is_base_only(options.get("ref")) or not is_base_only(options.get("repository")):
                    return True
            elif step_names_head({"with": options}):
                return True
        run = step.get("run")
        if not isinstance(run, str):
            continue
        if materializes(run, names_head):
            return True
        local_head = step_names_head(step)
        if local_head and OPAQUE_PIPE.search(run):
            raise Undetermined(f"{label}: a step pipes a head value into a program the checker cannot read")
        for program in programs(run, root):
            content = read_program(root, program) or inline_source(job, program)
            if content is None:
                if local_head:
                    raise Undetermined(
                        f"{label}: a step hands a head value to {program}, which the checker cannot read"
                    )
                continue
            if materializes(content, names_head or bool(HEAD_TOKEN.search(content))):
                return True
    return False


# Events that run the BASE workflow with base privileges while fork activity
# can trigger them, or whose payload carries text or a repository an outside
# account chooses: a fork's own repository (`fork`), an issue or discussion
# title and body. Head-content detection cannot be complete for these (event
# payload files, GITHUB_ENV/GITHUB_OUTPUT indirection, API clients), so every
# job that can land on a persistent runner needs a reviewed entry (ADR 0235).
PRIVILEGED_EVENTS = (
    "pull_request_target",
    "workflow_run",
    "issue_comment",
    "fork",
    "issues",
    "discussion",
    "discussion_comment",
)
# Relays: a workflow another workflow or account starts with values it chooses.
# gate-rearm.yml's pull_request_target run dispatches ai-review-merge.yml with
# a pull request number and head SHA, and a caller passes any input it likes,
# so a relayed job can be handed a fork's head. Each is scanned like a
# privileged event, and a job that materializes the head, or whose head
# detection cannot decide, may not run on a persistent runner at all.
RELAY_EVENTS = ("workflow_dispatch", "workflow_call", "repository_dispatch")
# Events that run the pull request's own code with pull_request semantics: a
# review, a review comment, and a merge queue all run the head or merge commit.
PULL_REQUEST_EVENTS = ("pull_request", "pull_request_review", "pull_request_review_comment", "merge_group")
CHECKED_EVENTS = PULL_REQUEST_EVENTS + PRIVILEGED_EVENTS + RELAY_EVENTS
# Documented events whose runs carry no fork-controlled ref or code. Any event
# outside this set and CHECKED_EVENTS is undetermined: check_run, check_suite,
# status, deployment, and deployment_status can name a fork head SHA, and an
# unknown name may be a new pull-request event.
UNSCANNED_EVENTS = frozenset(
    {
        "branch_protection_rule",
        "create",
        "delete",
        "gollum",
        "label",
        "milestone",
        "page_build",
        "project",
        "project_card",
        "project_column",
        "public",
        "push",
        "registry_package",
        "release",
        "schedule",
        "watch",
    }
)
# What `github.event_name` can be inside a called workflow: any event a caller
# can be triggered by. workflow_call itself is never an event name.
CALLER_EVENTS = tuple(sorted((set(CHECKED_EVENTS) | UNSCANNED_EVENTS) - {"workflow_call"}))
INHERITED_KEYS = ("env", "permissions", "defaults")


REVIEW_REQUIRED = "requires a reviewed entry"


class Finding(NamedTuple):
    """A privileged-event job that can resolve to a persistent runner."""

    path: str
    job: str
    review_digest: str
    diagnostic: str
    # (reason, detail) pairs for what the job puts on disk that the scanned
    # tree does not bound; an entry's `dynamic:` must name every reason.
    unresolved: tuple = ()
    # (kind, reason) pairs no entry can accept: an unpinned remote callee
    # or image, an unbounded runtime checkout, a relayed head.
    refusals: tuple = ()
    # Reached through a relay event (workflow_dispatch, workflow_call,
    # repository_dispatch); only these may be ratcheted by a pending row.
    relay: bool = False
    # For a relay finding, the backlog digest a pending row keys to: the job's
    # full content, its callers, its inherited settings, the bytes of every
    # local action it runs, and its unresolved reason names, so any edit to
    # the job stops the row applying.
    backlog_digest: str = ""


RELAY_HEAD_REFUSAL = (
    "a relayed run can be handed a pull request head, and head detection finds this job "
    "materializes it or cannot rule that out ({diagnostic}); it must run on an ephemeral "
    "runner, so no reviewed entry can accept it (ADR 0235)"
)

# The relay backlog, frozen (round-4 review of #1827): the 44 relayed jobs
# #1831 moves, as they stood when ADR 0235 began scanning relays. They are the
# only keys a pending row may name; any other key, including every
# pull_request job, is a format error, so the list cannot grow without
# editing this set, and public-pr-runner-isolation.test.py pins it against a
# snapshot it may only shrink from. Both live in this repository, so a change
# can edit both: growth is a visible, reviewed code change, not impossible. Each key also names the refusal kinds it carried then. Refusals are
# decided before any row is read: a kind outside that set, or any kind once the
# key's pending row is gone, fails whatever the lists say, so no row can waive
# a new refusal; a kind the job no longer carries fails as STALE BACKLOG until
# it is removed here.
RELAY_BACKLOG: dict[tuple[str, str], frozenset[str]] = {
    ('actionlint.yml [workflow_call]', 'actionlint'): frozenset({'relay-head'}),
    ('ai-promotion-retry.yml [workflow_call]', 'resolve'): frozenset({'relay-head'}),
    ('capability-floor-observe.yml [workflow_dispatch]', 'observe'): frozenset(),
    ('changelog-release.yml [workflow_call]', 'release'): frozenset({'relay-head', 'runtime-checkout'}),
    ('changelog-validate.yml [workflow_call]', 'validate'): frozenset({'relay-head', 'runtime-checkout'}),
    ('container-candidate.yml [workflow_call]', 'prepare'): frozenset({'relay-head'}),
    ('container-candidate.yml [workflow_call]', 'pull-request-build'): frozenset(),
    ('container-candidate.yml [workflow_call]', 'skip-private-node-build'): frozenset(),
    ('container-deployment-review-producer.yml [workflow_call]', 'analyze'):
        frozenset({'relay-head', 'runtime-checkout'}),
    ('container-deployment-review-producer.yml [workflow_call]', 'environment-preflight'): frozenset(),
    ('container-deployment-review-producer.yml [workflow_call]', 'publish-ai'):
        frozenset({'relay-head', 'runtime-checkout'}),
    ('container-deployment-review-producer.yml [workflow_call]', 'publish-code'):
        frozenset({'relay-head', 'runtime-checkout'}),
    ('container-deployment-review-producer.yml [workflow_call]', 'publish-security'):
        frozenset({'relay-head', 'runtime-checkout'}),
    ('container-deployment.yml [workflow_call]', 'deploy'): frozenset({'relay-head'}),
    ('container-deployment.yml [workflow_call]', 'dry-run'): frozenset({'relay-head'}),
    ('container-release-workflow-ref-canary.yml [workflow_dispatch]:probe -> container-release.yml', 'retention'):
        frozenset({'relay-head', 'runtime-checkout'}),
    ('container-release.yml [workflow_call]', 'retention'): frozenset({'relay-head', 'runtime-checkout'}),
    ('dependency-supersession-observe.yml [workflow_dispatch]', 'observe'): frozenset({'relay-head'}),
    ('dependency-supersession-reconcile.yml [workflow_dispatch]', 'reconcile'): frozenset(),
    ('generated-artifacts.yml [workflow_call]', 'validate'): frozenset({'relay-head', 'runtime-checkout'}),
    ('helm-ci.yml [workflow_call]', 'lint-template'): frozenset({'relay-head'}),
    ('node-ci-protected.yml [workflow_call]', 'build-test'): frozenset({'relay-head', 'runtime-checkout'}),
    ('node-ci-protected.yml [workflow_call]', 'eligibility'): frozenset(),
    ('node-ci.yml [workflow_call]', 'build-test'): frozenset({'relay-head'}),
    ('node-ci.yml [workflow_call]', 'eligibility'): frozenset(),
    ('notify-umbrella.yml [workflow_call]', 'dispatch'): frozenset(),
    ('pulumi-ci.yml [workflow_call]', 'preview'): frozenset({'relay-head'}),
    ('pulumi-ci.yml [workflow_call]', 'preview-admission'): frozenset(),
    ('pulumi-ci.yml [workflow_call]', 'validate'): frozenset({'relay-head'}),
    ('release-app-canary.yml [workflow_dispatch]', 'canary'): frozenset(),
    ('release-dispatch.yml [workflow_call]', 'release-dispatch'): frozenset({'relay-head', 'runtime-checkout'}),
    ('release-propose.yml [workflow_call]', 'release-propose'): frozenset({'relay-head', 'runtime-checkout'}),
    ('release.yml [workflow_dispatch]', 'publish'): frozenset(),
    ('release.yml [workflow_dispatch]', 'snapshot'): frozenset(),
    ('release.yml [workflow_dispatch]', 'verify'): frozenset({'relay-head', 'runtime-checkout'}),
    ('renovate-changelog.yml [workflow_call]', 'attribute'): frozenset({'relay-head', 'runtime-checkout'}),
    ('renovate-compatibility-canary.yml [workflow_call]', 'receipt'): frozenset(),
    ('renovate-compatibility-reconcile.yml [workflow_dispatch]', 'observe'): frozenset(),
    ('renovate-grouping-plan.yml [workflow_dispatch]', 'plan'): frozenset({'relay-head', 'runtime-checkout'}),
    ('repo-hygiene.yml [workflow_call]', 'hygiene'): frozenset({'relay-head', 'runtime-checkout'}),
    ('rework-reconcile.yml [workflow_dispatch]', 'reconcile'): frozenset(),
    ('runner-canary.yml [workflow_dispatch]', 'canary'): frozenset({'relay-head'}),
    ('ui-ci.yml [workflow_call]', 'build-test'): frozenset({'relay-head'}),
    ('verjson-ci.yml [workflow_call]', 'ci'): frozenset(),
}


def _normalize(value):
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def inherited_settings(document: dict) -> dict:
    """Workflow-level settings a job inherits, including the whole trigger block:
    a job's safety can depend on `types:`, `branches:`, or `paths:`."""
    settings = {key: document.get(key) for key in INHERITED_KEYS}
    settings["on"] = document.get("on", document.get(True))
    return settings


def review_digest(chain: tuple, job: dict, inherited: dict, closure: dict | None = None) -> str:
    """SHA-256 over the job, its inherited workflow settings, every caller job
    (with its own inherited settings) on the path that reaches it, and the
    bytes of every repository file the job checks out, executes, or uses as a
    local action."""
    canonical = json.dumps(
        _normalize({"chain": list(chain), "job": job, "inherited": inherited, "closure": closure or {}}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


FULL_SHA_PIN = re.compile(r"@[0-9a-f]{40}$")
def _is_reference(value: str, path: tuple[str, ...]) -> bool:
    """Whether `value` is exactly the expression `${{ <path> }}`, however spaced."""
    match = FULL_EXPRESSION.match(value)
    if not match:
        return False
    try:
        node = Parser(match.group(1)).parse()
    except Undetermined:
        return False
    return node[0] == "ref" and tuple(part.lower() for part in node[1]) == path


def is_tree_ref(ref: str) -> bool:
    """Whether a checkout ref resolves to the tree this scan reads: the default
    ref or `github.sha`, however it is spaced. Anything else (a step output, a
    branch name, `pull_request.base.sha`, which is not refreshed when the base
    branch advances) is selected at runtime."""
    return ref == "" or _is_reference(ref, ("github", "sha"))


ACTION_PATH = re.compile(r"\$\{\{\s*github\.action_path\s*\}\}")
MAX_CLOSURE_DEPTH = 4


class Closure:
    """The scanned-tree bytes a job puts on disk, and what cannot be bounded.

    Digests what a checkout materializes rather than what commands name:
    `source`, bare paths, imports, `require`, `make`, `npm run`, and data
    files read by scripts are all covered because the bytes they reach are on
    disk. Command parsing remains only for local actions, which come from the
    scanned tree without their own checkout step.
    """

    def __init__(self, root: str, excluded: frozenset[str] = frozenset()) -> None:
        self.root = os.path.normpath(root)
        self.excluded = excluded
        self.files: dict[str, str] = {}
        # The subset of `files` a local action contributes: the job's own
        # steps, as opposed to bytes a checkout puts on disk.
        self.action_files: set[str] = set()
        self.unresolved: dict[str, set[str]] = {}
        # Digest of the whole tracked tree, set when a checkout materializes it.
        self.tree: str | None = None
        # Why no reviewed entry can accept this job, if anything.
        self.refusals: set[tuple[str, str]] = set()

    def flag(self, reason: str, detail: str) -> None:
        assert reason in REASONS, reason
        self.unresolved.setdefault(reason, set()).add(detail)

    def pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((reason, detail) for reason, details in self.unresolved.items() for detail in details))

    def flag_fetches(self, text: str, where: str) -> None:
        for match in itertools.chain(FETCH.finditer(text), ARGV_FETCH.finditer(text)):
            self.flag("fetch", f"`{' '.join(match.group(0).split())}` in {where}")

    def flag_shell(self, shell, where: str) -> None:
        if shell is not None and str(shell).strip() != DEFAULT_SHELL:
            self.flag("shell", f"`shell: {shell}` in {where}")

    def add_file(self, repo_path: str, why: str) -> bool:
        """Bind what a checkout of `repo_path` puts on disk. Symlinks are never
        followed: a link binds as its target text, and a link whose target
        leaves the scanned tree, or a path reached through a linked
        directory, is an unscanned path, because its bytes are the runner's
        rather than the repository's."""
        candidate = os.path.normpath(os.path.join(self.root, repo_path))
        if candidate != self.root and not candidate.startswith(self.root + os.sep):
            self.flag("unscanned-path", f"{repo_path} ({why}: outside the scanned tree)")
            return False
        relative = os.path.relpath(candidate, self.root)
        if relative in self.excluded:
            return True  # the pending and reviewed lists: reviewed as the change that edits them
        parts = [] if relative == os.curdir else relative.split(os.sep)
        for depth in range(1, len(parts)):
            ancestor = os.path.join(self.root, *parts[:depth])
            if os.path.islink(ancestor):
                self.flag(
                    "unscanned-path",
                    f"{repo_path} ({why}: reached through the symlink {os.path.join(*parts[:depth])}, "
                    "which a checkout does not descend)",
                )
                return False
        if os.path.islink(candidate):
            target = os.readlink(candidate)
            self.files.setdefault(relative, "link " + target)
            if why.startswith(("local action", "run by local action")):
                self.action_files.add(relative)
            resolved = os.path.normpath(os.path.join(os.path.dirname(candidate), target))
            if os.path.isabs(target) or (resolved != self.root and not resolved.startswith(self.root + os.sep)):
                self.flag("unscanned-path", f"{repo_path} ({why}: symlink to {target}, outside the scanned tree)")
            return True
        if os.path.isdir(candidate):
            for base, directories, names in os.walk(candidate):
                links = sorted(d for d in directories if os.path.islink(os.path.join(base, d)))
                directories[:] = sorted(d for d in directories if d != ".git" and d not in links)
                for name in sorted(names + links):
                    self.add_file(os.path.relpath(os.path.join(base, name), self.root), why)
            return True
        if not os.path.isfile(candidate):
            self.flag("unscanned-path", f"{repo_path} ({why}: not in the scanned tree)")
            return False
        if relative not in self.files:
            with open(candidate, "rb") as handle:
                self.files[relative] = hashlib.sha256(handle.read()).hexdigest()
        if why.startswith(("local action", "run by local action")):
            self.action_files.add(relative)
        return True

    def add_direct_files(self, directory: str, why: str) -> None:
        """Files directly in a directory, as cone mode materializes for parents."""
        absolute = os.path.normpath(os.path.join(self.root, directory))
        if not os.path.isdir(absolute) or os.path.islink(absolute):
            return
        for name in sorted(os.listdir(absolute)):
            path = os.path.join(absolute, name)
            if os.path.islink(path) or os.path.isfile(path):
                self.add_file(os.path.relpath(path, self.root), why)

    def add_checkout(self, options: dict) -> None:
        if str(options.get("submodules", "false")).strip().lower() in ("true", "recursive"):
            self.flag(
                "submodules",
                f"checkout submodules: {options.get('submodules')} materializes repositories outside the scanned tree"
            )
        repository = str(options.get("repository") or "").strip()
        ref = " ".join(str(options.get("ref") or "").split())
        sparse = [line.strip() for line in str(options.get("sparse-checkout") or "").splitlines() if line.strip()]
        cone = str(options.get("sparse-checkout-cone-mode", "true")).strip().lower() != "false"
        this_repository = repository == "" or _is_reference(repository, ("github", "repository"))
        if not (this_repository and is_tree_ref(ref)):
            self.flag(
                "runtime-checkout",
                f"checkout of {repository or 'this repository'}@{ref or 'default'} "
                "selected at runtime, not the scanned tree"
            )
            if not sparse:
                self.refusals.add((
                    "runtime-checkout",
                    f"checkout of {repository or 'this repository'}@{ref or 'default'} is selected at runtime "
                    "and has no sparse-checkout list, so no review can bind the bytes it puts on disk; "
                    "add a sparse-checkout list naming only the paths the job needs",
                ))
            # The scanned bytes of the listed paths still bind the review.
            for path in sparse:
                self.add_sparse_path(path, cone, "runtime checkout, scanned bytes")
            return
        if not sparse:
            self.flag("full-checkout", "full checkout of the scanned tree (no sparse-checkout list bounds it)")
            self.tree = tree_digest(self.root, self.excluded)
            return
        if cone:
            self.add_direct_files("", "cone-mode root")
        for path in sparse:
            self.add_sparse_path(path, cone, "sparse-checkout")

    def add_sparse_path(self, path: str, cone: bool, why: str) -> None:
        unprovable = (CONE_UNPROVABLE if cone else PATTERN_UNPROVABLE).search(path) or not path.strip("/")
        if unprovable:
            self.refusals.add((
                "sparse-pattern",
                f"{'cone-mode' if cone else 'non-cone'} sparse-checkout pattern {path!r} is not one this checker "
                "evaluates exactly, so no review can bind what it puts on disk and no entry can accept it; "
                "list literal paths (non-cone patterns may use * and ?)",
            ))
            return
        if not cone:
            self.add_sparse_pattern(path, why)
            return
        path = path.strip("/")
        parts = path.split("/")
        for depth in range(1, len(parts)):
            self.add_direct_files("/".join(parts[:depth]), f"cone-mode parent of {path}")
        self.add_file(path, why)

    def add_sparse_pattern(self, pattern: str, why: str) -> None:
        """Bind what a non-cone (gitignore-syntax) pattern can put on disk.

        A pattern with a slash before its last character is anchored to the
        root; any other matches at every depth. `*` matches a leading dot. A
        negation only removes bytes, so the union of the positive patterns
        over-approximates the checkout.
        """
        if pattern.startswith("!"):
            return
        anchored = "/" in pattern.rstrip("/")
        body = pattern.strip("/")
        found = glob.glob(os.path.join(self.root, body if anchored else "**/" + body), recursive=True,
                          include_hidden=True)
        matches = sorted(match for match in found if ".git" not in os.path.relpath(match, self.root).split(os.sep))
        if not matches:
            self.flag("unscanned-path", f"{body} ({why}: matches nothing in the scanned tree)")
        for match in matches:
            self.add_file(os.path.relpath(match, self.root), why)

    def add_steps(self, steps, depth: int, action_dir: str | None = None) -> None:
        for number, step in enumerate(steps or [], 1):
            if not isinstance(step, dict):
                continue
            where = f"step {number}" + (f" of local action {action_dir}" if action_dir else "")
            uses = str(step.get("uses", ""))
            name = uses.split("@")[0].lower()
            if name == "actions/checkout":
                self.add_checkout(step.get("with") or {})
            elif name in FETCH_ACTIONS:
                self.flag("fetch", f"{uses.split('@')[0]} at {where}")
            elif uses.startswith("docker://"):
                self.flag("fetch", f"{uses} at {where} pulls an image")
                self.require_pinned(f"{where} uses:", uses[len("docker://"):])
            elif uses.startswith("./"):
                self.add_action(uses, depth)
            run = step.get("run")
            if isinstance(run, str):
                self.flag_shell(step.get("shell"), where)
                for change in RUNTIME_TREE_CHANGE.finditer(without_comments(run)):
                    self.flag(
                        "tree-change",
                        f"`{' '.join(change.group(0).strip(' ;&|(').split())}` changes the checked-out tree at runtime",
                    )
                if action_dir is not None:
                    for program in programs(run):
                        if ACTION_PATH.search(program):
                            self.add_file(os.path.normpath(ACTION_PATH.sub(action_dir, program.strip("\"'"))),
                                          f"run by local action {action_dir}")
            for text in step_strings(step):
                self.flag_fetches(text, where)

    def add_job_images(self, job: dict) -> None:
        """A job `container:` or `services:` image is pulled when the job
        starts. It is a fetch, and only a digest pin lets a review bind it."""
        images = []
        if "container" in job:
            container = job["container"]
            images.append(("container", container.get("image") if isinstance(container, dict) else container))
        services = job.get("services")
        if isinstance(services, dict):
            for name, service in sorted(services.items(), key=lambda item: str(item[0])):
                images.append((f"services.{name}", service.get("image") if isinstance(service, dict) else service))
        elif services is not None:
            images.append(("services", services))
        for where, image in images:
            self.flag("fetch", f"`{where}` image {image} is pulled when the job starts")
            self.require_pinned(f"`{where}`", image)

    def require_pinned(self, where: str, image) -> None:
        """Only a digest pin lets a review bind the image a job runs."""
        if not (isinstance(image, str) and PINNED_IMAGE.fullmatch(image.strip())):
            self.refusals.add((
                "image",
                f"{where} image {image!r} must be pinned by an @sha256: digest; "
                "a tag or expression can change after review, so no entry can accept it",
            ))

    def require_pinned_build(self, action_dir: str, dockerfile: str, uses: str) -> None:
        """A local Docker action built from a Dockerfile pulls every `FROM`
        base, every `COPY --from` and `RUN --mount=...,from=` source that is
        not an earlier stage, and any `# syntax=` frontend; each must be
        digest-pinned. An `escape` directive changes how the file parses, so
        nothing here can vet it."""
        path = os.path.normpath(os.path.join(self.root, action_dir, dockerfile))
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            self.flag("local-action", f"{uses} (Dockerfile {dockerfile} not in the scanned tree)")
            return
        where = f"local docker action {uses}"
        # Directives are honored only at the top; matching them anywhere
        # over-approximates.
        for name, value in DOCKERFILE_DIRECTIVE.findall(text):
            if name.lower() == "syntax":
                self.require_pinned(f"{where} `# syntax` frontend", value.strip())
            elif name.lower() == "escape" and value.strip() != "\\":
                self.refusals.add((
                    "image",
                    f"{where} sets the parser directive escape={value.strip()}, so this checker cannot read "
                    "which images it pulls and no entry can accept it",
                ))
        lines = [line for line in text.replace("\\\n", " ").splitlines() if not line.lstrip().startswith("#")]
        stages: list[str] = []
        found = False
        for line in lines:
            match = DOCKERFILE_FROM.match(line)
            if match:
                found = True
                image, stage = match.group(1), match.group(2)
                if image.lower() != "scratch" and image.lower() not in stages:
                    self.require_pinned(f"{where} `FROM`", image)
                stages.append(stage.lower() if stage else "")
                continue
            instruction = DOCKERFILE_INSTRUCTION.match(line)
            if not instruction:
                continue
            flags = []
            for token in instruction.group(2).split():
                if not token.startswith("--"):
                    break  # flags precede the arguments; later text is the command
                flags.extend(DOCKERFILE_FLAG.findall(token))
            for flag, value in flags:
                if flag.lower() == "from":
                    sources = [(f"`{instruction.group(1).upper()} --from`", value)]
                elif flag.lower() == "mount":
                    sources = [
                        (f"`{instruction.group(1).upper()} --mount`", option.split("=", 1)[1])
                        for option in value.split(",")
                        if option.split("=", 1)[0].strip().lower() == "from" and "=" in option
                    ]
                else:
                    continue
                for label, source in sources:
                    source = source.strip("\"'")
                    earlier = source.lower() in stages or (source.isdigit() and int(source) < len(stages))
                    if not earlier:
                        self.require_pinned(f"{where} {label}", source)
        if not found:
            self.require_pinned(f"{where} `FROM`", None)

    def add_scanned_fetches(self) -> None:
        """A repository file the job puts on disk can fetch when it runs."""
        for relative in sorted(self.files):
            path = os.path.join(self.root, relative)
            if os.path.islink(path):
                continue  # bound by its target text; never read through it
            with open(path, encoding="utf-8", errors="replace") as handle:
                self.flag_fetches(handle.read(), relative)

    def add_action(self, uses: str, depth: int) -> None:
        action_dir = os.path.normpath(uses)
        if depth >= MAX_CLOSURE_DEPTH:
            self.flag("local-action", f"{uses} (local action nested beyond {MAX_CLOSURE_DEPTH})")
            return
        if not any(os.path.isfile(os.path.join(self.root, action_dir, name)) for name in ("action.yml", "action.yaml")):
            self.flag("local-action", f"{uses} (local action not in the scanned tree)")
            return
        # Everything the action directory holds is on disk when it runs.
        self.add_file(action_dir, f"local action {uses}")
        manifest = next(
            os.path.join(action_dir, name)
            for name in ("action.yml", "action.yaml")
            if os.path.isfile(os.path.join(self.root, action_dir, name))
        )
        try:
            with open(os.path.join(self.root, manifest), encoding="utf-8") as handle:
                action = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError):
            self.flag("local-action", f"{uses} (local action manifest unreadable)")
            return
        runs = action.get("runs") if isinstance(action, dict) else None
        if not isinstance(runs, dict):
            self.flag("local-action", f"{uses} (local action has no runs block)")
            return
        if runs.get("using") == "composite":
            self.add_steps(runs.get("steps"), depth + 1, action_dir)
            return
        if runs.get("using") == "docker":
            self.flag("fetch", f"local docker action {uses} builds or pulls an image at runtime")
            image = runs.get("image")
            if isinstance(image, str) and image.startswith("docker://"):
                self.require_pinned(f"local docker action {uses}", image[len("docker://"):])
            elif isinstance(image, str) and image.strip():
                self.require_pinned_build(action_dir, image.strip(), uses)
            else:
                self.require_pinned(f"local docker action {uses}", image)
        for key in ("main", "pre", "post", "image"):
            value = runs.get(key)
            if isinstance(value, str) and not value.startswith(("docker://", "http")):
                self.add_file(os.path.join(action_dir, value), f"local action {uses} entry point")


# Sparse-checkout syntax add_sparse_path does not evaluate exactly: in a
# non-cone (gitignore) pattern a character class or backslash escape differs
# from Python's glob, and a cone-mode entry is a directory, never a pattern.
PATTERN_UNPROVABLE = re.compile(r"[\[\]\\]")
CONE_UNPROVABLE = re.compile(r"[*?\[\]\\]|^!")
# A container image reference fixed by content: no expression, no whitespace.
PINNED_IMAGE = re.compile(r"[^\s@${}]+@sha256:[0-9a-f]{64}")
# `FROM [--platform=…] <image> [AS <stage>]` in a Dockerfile.
DOCKERFILE_FROM = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?\s*$", re.I)
# Any other instruction and its flags, and the flags that name an image.
DOCKERFILE_INSTRUCTION = re.compile(r"^\s*([A-Za-z]+)\s+(.*)$")
DOCKERFILE_FLAG = re.compile(r"^--([A-Za-z-]+)=(\S+)$")
# A `# name=value` parser directive (syntax, escape, check).
DOCKERFILE_DIRECTIVE = re.compile(r"(?m)^[ \t]*#[ \t]*([A-Za-z]+)[ \t]*=[ \t]*(\S*)[ \t]*$")


def tracked_files(root: str) -> list[tuple[str, str | None, str | None]]:
    """The entries a full checkout of the scanned tree materializes, as
    (path, index mode, index object ID): git's index in a work tree (never
    untracked residue such as __pycache__), otherwise every file under the
    root except .git, with no mode or object ID."""
    if os.path.exists(os.path.join(root, ".git")):
        try:
            listing = subprocess.run(
                ["git", "-C", root, "ls-files", "-s", "-z"], capture_output=True, check=True
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            raise Undetermined(f"{root}: cannot list the tracked tree a full checkout puts on disk: {error}") from error
        entries = {}
        for record in listing.decode("utf-8", "surrogateescape").split("\0"):
            if not record:
                continue
            # `<mode> <object> <stage>\t<path>`; a conflicted path lists each stage.
            header, _, path = record.partition("\t")
            mode, oid, stage = header.split(" ")
            entries.setdefault(path, []).append((stage, mode, oid))
        return sorted(
            (
                path,
                " ".join(mode for _, mode, _ in sorted(stages)),
                " ".join(oid for _, _, oid in sorted(stages)),
            )
            for path, stages in entries.items()
        )
    found = []
    for base, directories, names in os.walk(root):
        links = [name for name in directories if os.path.islink(os.path.join(base, name))]
        directories[:] = sorted(name for name in directories if name != ".git" and name not in links)
        found.extend(os.path.relpath(os.path.join(base, name), root) for name in names + links)
    return [(path, None, None) for path in sorted(found)]


GITLINK_MODE = "160000"


@functools.lru_cache(maxsize=None)
def tree_digest(root: str, excluded: frozenset[str] = frozenset()) -> str:
    """SHA-256 over every tracked entry, in path order, so an edit, an added
    file, a deleted file, a mode change, and a moved submodule all change it.

    A file contributes its index mode and the SHA-256 of its bytes on disk, so
    an unstaged edit counts; a symlink its target, never followed; a submodule
    gitlink its mode and commit ID, which is what `submodules: true` would
    check out; and a tracked path missing from disk the index object a
    checkout would put there. Each entry is one JSON array per line, which
    escapes every newline and control character, so no path or link target can
    forge an entry boundary. The excluded paths are the pending and reviewed
    lists themselves: an entry cannot contain a digest over its own bytes."""
    digest = hashlib.sha256()
    for relative, mode, oid in tracked_files(root):
        if relative in excluded:
            continue
        path = os.path.join(root, relative)
        if mode == GITLINK_MODE:
            entry = [relative, mode, "gitlink", oid]
        elif os.path.islink(path):
            entry = [relative, mode, "link", os.readlink(path)]
        elif os.path.isfile(path):
            with open(path, "rb") as handle:
                entry = [relative, mode, "file", hashlib.sha256(handle.read()).hexdigest()]
        else:
            entry = [relative, mode, "missing", oid]
        digest.update(json.dumps(entry, ensure_ascii=True).encode("ascii") + b"\n")
    return "sha256:" + digest.hexdigest()


RUNTIME_TREE_CHANGE = re.compile(
    rf"(?:^|[\s;&|(])git[ \t]+{GIT_OPTIONS}"
    r"(?:sparse-checkout|checkout|switch|restore|reset|pull|merge|rebase|worktree|apply|am|cherry-pick"
    r"|read-tree|checkout-index)\b"
    r"|(?:^|[\s;&|(])git[ \t][^\n]*?--(?:git-dir|work-tree)\b"
    r"|\bgh[ \t]+pr[ \t]+checkout\b"
    r"|(?:^|[;&|(]|&&|\|\|)[ \t]*(?:sudo[ \t]+)?patch(?=[ \t]|$)",
    re.M,
)
# Content a job fetches at runtime, by whatever means the step text or a
# scanned file names: network clients, the GitHub API and CLI downloads, git
# transport, package managers and container images, and in-language HTTP
# clients. A vocabulary, so incomplete by construction (ADR 0235 decision 6).
FETCH = re.compile(
    r"\b(?:curl|wget|aria2c|rsync|scp|sftp)\b"
    rf"|\bgit[ \t]+{GIT_OPTIONS}(?:clone|fetch|pull|submodule|ls-remote|remote[ \t]+update)\b"
    r"|\bgh[ \t]+(?:api|release[ \t]+download|run[ \t]+download|pr[ \t]+(?:checkout|diff)"
    r"|repo[ \t]+clone|gist[ \t]+clone|attestation[ \t]+download)\b"
    r"|\b(?:npx|pnpx|bunx|uvx|pipx)\b|\b(?:npm|pnpm|yarn|bun)[ \t]+(?:install|i|ci|add|exec|dlx|x|create|update)\b"
    r"|\bpip3?[ \t]+(?:install|download)\b|\bpython3?[ \t]+-m[ \t]+pip\b|\buv[ \t]+(?:pip|run|tool|sync|add)\b"
    r"|\b(?:docker|podman)[ \t]+(?:run|pull|create|build|buildx|compose)\b"
    r"|\bgo[ \t]+(?:install|get|run)\b|\bcargo[ \t]+(?:install|fetch|build)\b|\bgem[ \t]+install\b"
    r"|\b(?:apt|apt-get|yum|dnf|apk|brew)[ \t]+(?:install|add)\b"
    r"|(?i:\b(?:invoke-webrequest|invoke-restmethod|iwr|irm|start-bitstransfer)\b|\bnet\.webclient\b)"
    r"|\burllib\b|\burlopen\b|\brequests\.\w|\bhttpx\b|\bhttp\.client\b|\bfetch\(|\bhttps?\.(?:get|request)\(|\baxios\b"
    r"""|\brequire\(\s*["'](?:node:)?https?["']\s*\)|\bfrom\s+["'](?:node:)?https?["']"""
)
# gh and git subcommands as an argv list in a program the job runs (a quoted
# "curl" or "wget" already matches FETCH).
ARGV_FETCH = re.compile(
    r"""["']gh["']\s*,\s*["'](?:api|release|run|pr|repo|gist)["']"""
    r"""|["']git["']\s*,\s*(?:["']-[^"']*["']\s*,\s*(?:[^,\]]+,\s*)?)*["'](?:clone|fetch|pull|submodule)["']"""
)
FETCH_ACTIONS = ("actions/download-artifact", "actions/cache", "actions/cache/restore", "actions/github-script")
# The runner's default for `run:` on Linux; any other shell is its own program.
DEFAULT_SHELL = "bash"
REASONS = (
    "fetch",
    "head-materialization",
    "tree-change",
    "shell",
    "full-checkout",
    "runtime-checkout",
    "submodules",
    "unscanned-path",
    "local-action",
)


def step_strings(step) -> list[str]:
    """Every string a step carries, with full-line comments dropped from `run`."""
    found = []
    stack = [step]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                stack.append(without_comments(item) if key == "run" and isinstance(item, str) else item)
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str):
            found.append(value)
    return found


def without_comments(run: str) -> str:
    """Drop full-line shell comments, which name commands without running them."""
    return "\n".join(line for line in run.split("\n") if not line.lstrip().startswith("#"))


def job_closure(job: dict, root: str, workflow_defaults=None, excluded: frozenset[str] = frozenset()) -> Closure:
    closure = Closure(root, excluded)
    for scope, defaults in (("workflow", workflow_defaults), ("job", job.get("defaults"))):
        run_defaults = defaults.get("run") if isinstance(defaults, dict) else None
        if isinstance(run_defaults, dict):
            closure.flag_shell(run_defaults.get("shell"), f"{scope} defaults.run")
    closure.add_job_images(job)
    closure.add_steps(job.get("steps"), 0)
    closure.add_scanned_fetches()
    return closure


class Scan:
    """Traversal state for one triggering event across local reusable calls."""

    def __init__(self, directory: str, repository: str, event: str, lists: tuple[str, ...] = ()) -> None:
        self.directory = directory
        # Workflows live at <root>/.github/workflows; scripts resolve from <root>.
        self.root = os.path.dirname(os.path.dirname(os.path.abspath(directory)))
        # The pending and reviewed lists, relative to the root, kept out of
        # the tree digest.
        self.excluded = frozenset(
            os.path.relpath(os.path.abspath(path), self.root) for path in lists if path is not None
        )
        self.repository = repository
        self.event = event
        self.findings: list[Finding] = []

    @property
    def relay(self) -> bool:
        return self.event in RELAY_EVENTS

    @property
    def privileged(self) -> bool:
        return self.event in PRIVILEGED_EVENTS or self.relay


def head_diagnostic(job: dict, document: dict, root: str, label: str) -> tuple[str, bool]:
    """Head detection's finding, and whether it leaves the job unresolved.

    "No known pattern" is not proof of safety, but anything else (the job
    materializes the head, or detection cannot decide) needs `dynamic:`.
    """
    try:
        if materializes_head(job, document.get("env"), root, label):
            return "head detection: materializes the pull request head", True
        return "head detection: no known head-materializing pattern (not proof of safety)", False
    except Undetermined as error:
        return f"head detection: undetermined ({error})", True


def check_call(
    scan: Scan, path: str, name: str, job: dict, depth: int, chain: tuple, document: dict
) -> list[Violation]:
    uses = str(job["uses"])
    local = LOCAL_REUSABLE.match(uses)
    if not local:
        if scan.privileged:
            scan.findings.append(
                Finding(
                    path,
                    name,
                    review_digest(chain, job, inherited_settings(document)),
                    f"remote callee {uses}: its placement cannot be read",
                    refusals=()
                    if FULL_SHA_PIN.search(uses)
                    else ((
                        "remote-callee",
                        f"remote callee {uses} must be pinned to a full 40-hex commit SHA; "
                        "a branch or tag can change after review, so no entry can accept it",
                    ),),
                    relay=scan.relay,
                    backlog_digest=review_digest(chain, job, inherited_settings(document)),
                )
            )
            return []
        raise Undetermined(f"{path}: job '{name}' calls {uses}, whose placement this checker cannot read")
    if depth >= MAX_CALL_DEPTH:
        raise Undetermined(f"{path}: job '{name}' exceeds the reusable-call depth of {MAX_CALL_DEPTH}")
    callee = local.group(1)
    callee_document = load_workflow(os.path.join(scan.directory, callee))
    return check_workflow(
        scan,
        f"{path}:{name} -> {callee}",
        callee_document,
        callee_bindings(job, callee_document),
        depth + 1,
        chain + ({"job": job, "inherited": inherited_settings(document)},),
    )


def check_workflow(
    scan: Scan,
    path: str,
    document: dict,
    bindings: dict[str, tuple] | None = None,
    depth: int = 0,
    chain: tuple = (),
) -> list[Violation]:
    bindings = bindings or {}
    jobs = {str(name): job for name, job in (document.get("jobs") or {}).items()}
    unreachable: set[str] = set()
    violations: list[Violation] = []
    for name in dependency_order(path, jobs):
        job = jobs[name]
        if not is_reachable(job, scan.repository, bindings, frozenset(unreachable), scan.event):
            unreachable.add(name)
            continue
        if "uses" in job:
            violations.extend(check_call(scan, path, name, job, depth, chain, document))
            continue
        try:
            found = check_job(
                path, name, job, scan.repository, bindings, frozenset(unreachable), scan.event
            )
        except Undetermined as error:
            if not scan.privileged:
                raise
            # A privileged job whose placement cannot be proven hosted needs review.
            found = [Violation(path, name, f"placement undetermined: {error}")]
        if not scan.privileged:
            violations.extend(found)
        elif found:
            closure = job_closure(job, scan.root, document.get("defaults"), scan.excluded)
            diagnostic, head_unresolved = head_diagnostic(job, document, scan.root, f"{path}: job '{name}'")
            if head_unresolved:
                closure.flag("head-materialization", diagnostic)
                if scan.relay:
                    closure.refusals.add(("relay-head", RELAY_HEAD_REFUSAL.format(diagnostic=diagnostic)))
            pairs = closure.pairs()
            bound = {"files": closure.files, "unresolved": [f"{reason}: {detail}" for reason, detail in pairs]}
            # The backlog digest binds what the job is and what it runs: the
            # bytes its sparse checkouts and local actions put on disk (the
            # pending and reviewed lists excluded, or no row could match
            # itself) and every scanned-tree program its steps execute. A full
            # checkout's whole-tree digest is left out, because every commit
            # would restale the row; the programs it runs stand in for it.
            backlog_digest = review_digest(
                chain,
                job,
                inherited_settings(document),
                {
                    "files": closure.files,
                    "programs": executed_programs(job, scan.root, scan.excluded),
                    "reasons": sorted({reason for reason, _ in pairs}),
                },
            )
            if closure.tree is not None:
                bound["tree"] = closure.tree
            scan.findings.append(
                Finding(
                    path,
                    name,
                    review_digest(chain, job, inherited_settings(document), bound),
                    diagnostic,
                    pairs,
                    refusals=tuple(sorted(closure.refusals)),
                    relay=scan.relay,
                    backlog_digest=backlog_digest,
                )
            )
    return violations


def check_job(
    path: str,
    name: str,
    job: dict,
    repository: str,
    bindings: dict[str, tuple],
    unreachable: frozenset[str],
    event: str,
) -> list[Violation]:
    runs_on = job.get("runs-on")
    if runs_on is None:
        raise Undetermined(f"{path}: job '{name}' has no runs-on")
    condition = condition_node(job.get("if"))
    violations = []
    for assignment in assignments((condition, selector_node(runs_on)), bindings):
        context = Context(repository, assignment, job_needs(job), unreachable, event)
        if not can_run(condition, context):
            continue
        for outcome in selector_outcomes(runs_on, context):
            try:
                verdict = classify_labels(outcome)
            except Undetermined as error:
                raise Undetermined(f"{path}: job '{name}': {error}") from error
            if verdict == "persistent":
                violations.append(
                    Violation(
                        path,
                        name,
                        f"{event} in a public repository can resolve to self-hosted "
                        f"or non-ephemeral runner labels {list(outcome)!r} ({describe(assignment)})",
                    )
                )
                return violations
    return violations


def relay_bindings(document: dict, event: str) -> dict[str, tuple]:
    """The values a relay's own inputs can take: whatever its dispatcher or
    caller chooses. A `choice` takes only its options and a `boolean` only
    true or false; every other input is tried as an adversarial value."""
    if event not in ("workflow_dispatch", "workflow_call"):
        return {}
    trigger = document.get("on", document.get(True))
    declared = trigger.get(event) if isinstance(trigger, dict) else None
    inputs = declared.get("inputs") if isinstance(declared, dict) else None
    bindings: dict[str, tuple] = {}
    for name, declaration in (inputs or {}).items():
        declaration = declaration if isinstance(declaration, dict) else {}
        kind = declaration.get("type", "string")
        if kind == "choice" and isinstance(declaration.get("options"), list) and declaration["options"]:
            bindings[str(name)] = tuple(declaration["options"])
        elif kind == "boolean":
            bindings[str(name)] = (True, False)
        else:
            bindings[str(name)] = ADVERSARIAL_INPUT_VALUES
    return bindings


def check_tree(
    directory: str, repository: str, findings: list[Finding] | None = None, lists: tuple[str, ...] = ()
) -> list[Violation]:
    violations: list[Violation] = []
    names = sorted(
        name for name in os.listdir(directory) if name.endswith((".yml", ".yaml"))
    )
    if not names:
        raise Undetermined(f"{directory}: no workflow files to scan")
    for name in names:
        document = load_workflow(os.path.join(directory, name))
        events = triggers(document)
        unclassified = sorted(events - set(CHECKED_EVENTS) - UNSCANNED_EVENTS)
        if unclassified:
            raise Undetermined(
                f"{name}: event '{unclassified[0]}' is not classified by this checker; it may run pull-request code "
                "or carry a fork-controlled ref, so classify it in ADR 0235 before a workflow uses it"
            )
        for event in CHECKED_EVENTS:
            if event not in events:
                continue
            scan = Scan(directory, repository, event, lists)
            label = name if event == "pull_request" else f"{name} [{event}]"
            violations.extend(check_workflow(scan, label, document, relay_bindings(document, event)))
            if findings is not None:
                findings.extend(scan.findings)
    return violations


PENDING_FORMAT = "path<TAB>job<TAB>backlog digest<TAB>reason"


REVIEWED_FORMAT = (
    "path<TAB>job<TAB>review digest<TAB>justification"
    "[<TAB>dynamic: <reason>[, <reason>]: <justification>]"
)
DYNAMIC_FIELD = re.compile(r"dynamic: ([a-z][a-z-]*(?:, [a-z][a-z-]*)*): (.*)", re.S)


class Entry(NamedTuple):
    digest: str
    justification: str
    dynamic: str = ""
    reasons: frozenset = frozenset()


def parse_dynamic(field: str, where: str, row_format: str) -> frozenset:
    """`dynamic: fetch, shell: <why>` -> {"fetch", "shell"}; every tag must be
    a reason the checker computes, named once, with a non-empty justification."""
    match = DYNAMIC_FIELD.fullmatch(field)
    if not match:
        raise Undetermined(f"{where}: expected {row_format}")
    tags = match.group(1).split(", ")
    for tag in tags:
        if tag not in REASONS:
            raise Undetermined(f"{where}: unknown dynamic: reason '{tag}' (known: {', '.join(REASONS)})")
    if len(set(tags)) != len(tags):
        raise Undetermined(f"{where}: dynamic: repeats a reason ({match.group(1)})")
    if not match.group(2).strip():
        raise Undetermined(f"{where}: dynamic: has an empty justification")
    return frozenset(tags)


def load_pending(path: str | None, row_format: str = PENDING_FORMAT) -> dict[tuple[str, str], Entry]:
    """Reviewed `path<TAB>job<TAB>sha256:<hex><TAB>reason` rows, keyed to a digest."""
    if path is None:
        return {}
    pending: dict[tuple[str, str], Entry] = {}
    first_line: dict[tuple[str, str], int] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            rows = handle.read().splitlines()
    except OSError as error:
        raise Undetermined(f"cannot read pending file: {error}") from error
    for number, row in enumerate(rows, 1):
        if not row.strip() or row.startswith("#"):
            continue
        fields = row.split("\t")
        optional_dynamic = (
            row_format == REVIEWED_FORMAT and len(fields) == 5 and fields[4].startswith("dynamic:")
        )
        if (len(fields) != 4 and not optional_dynamic) or not all(field.strip() for field in fields):
            raise Undetermined(f"{path}:{number}: expected {row_format}")
        if not DIGEST.fullmatch(fields[2]):
            raise Undetermined(f"{path}:{number}: {row_format.split('<TAB>')[2]} must be sha256:<64 hex>")
        reasons = parse_dynamic(fields[4], f"{path}:{number}", row_format) if optional_dynamic else frozenset()
        key = (fields[0], fields[1])
        if key in first_line:
            raise Undetermined(
                f"{path}:{number}: duplicate entry for {fields[0]} job '{fields[1]}' "
                f"(first at line {first_line[key]}); a later row must not silently replace an earlier one"
            )
        first_line[key] = number
        pending[key] = Entry(
            fields[2], fields[3], fields[4] if optional_dynamic else "", reasons
        )
    return pending


def require_frozen_backlog(pending: dict[tuple[str, str], Entry], path: str | None) -> None:
    """The pending list may only shrink: every row names a frozen backlog key."""
    for path_label, job in sorted(set(pending) - set(RELAY_BACKLOG)):
        raise Undetermined(
            f"{path}: {path_label} job '{job}' is not in the frozen backlog (ADR 0235); the pending list may only "
            "shrink, so move the job to an ephemeral runner or give it a reviewed entry instead"
        )


def describe_reasons(pairs, reasons) -> str:
    """`fetch (detail; detail), shell (detail)` for the named reasons."""
    return ", ".join(
        f"{reason} ({'; '.join(detail for name, detail in pairs if name == reason)})"
        for reason in REASONS
        if reason in reasons
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repository", default="Verjson/.github")
    parser.add_argument("--pending", help=f"reviewed {PENDING_FORMAT} exceptions")
    parser.add_argument(
        "--reviewed",
        help=f"reviewed {REVIEWED_FORMAT} privileged-event jobs allowed on persistent runners",
    )
    parser.add_argument("workflow_dir")
    arguments = parser.parse_args(argv)
    try:
        pending = load_pending(arguments.pending)
        require_frozen_backlog(pending, arguments.pending)
        reviewed = load_pending(arguments.reviewed, REVIEWED_FORMAT)
        findings: list[Finding] = []
        found = check_tree(
            arguments.workflow_dir, arguments.repository, findings, (arguments.pending, arguments.reviewed)
        )
    except Undetermined as error:
        print(f"UNDETERMINED: {error}", file=sys.stderr)
        return 2
    # No pull_request job is pending: every one must be ephemeral.
    violations = list(found)
    stale_reviews = []
    ratcheted: set[tuple[str, str]] = set()
    for finding in findings:
        label = f"{finding.path}: job '{finding.job}'"
        print(f"DIAGNOSTIC: {label}: {finding.diagnostic}")
        row = reviewed.get((finding.path, finding.job))
        reasons = {reason for reason, _ in finding.unresolved}
        key = (finding.path, finding.job)
        pending_row = pending.get(key) if finding.relay else None
        if pending_row is not None:
            ratcheted.add(key)
            if row is not None:
                print(f"UNDETERMINED: {label} is in both the pending and the reviewed list; keep one", file=sys.stderr)
                return 2
        # Refusals first: no row, pending or reviewed, is read until they pass.
        # Only a frozen backlog key with a pending row tolerates the refusal
        # kinds it carried when the backlog was frozen.
        tolerated = RELAY_BACKLOG.get(key, frozenset()) if pending_row is not None else frozenset()
        refused = [reason for kind, reason in finding.refusals if kind not in tolerated]
        if refused:
            violations.append(
                Violation(
                    finding.path,
                    finding.job,
                    "; ".join(refused)
                    + (f" (backlog digest {finding.backlog_digest})" if finding.relay else "")
                    + ("; a pending row cannot waive a refusal" if pending_row is not None else ""),
                )
            )
            continue
        if pending_row is not None:
            # The relay backlog (#1831): a known relayed job, an exact ratchet,
            # never accepted by review.
            carried = {kind for kind, _ in finding.refusals}
            for kind in sorted(tolerated - carried):
                stale_reviews.append(
                    f"STALE BACKLOG: {label} no longer carries refusal kind {kind}; "
                    "remove it from RELAY_BACKLOG and the test snapshot"
                )
            if pending_row.digest == finding.backlog_digest:
                print(f"PENDING: {label}: relayed job can resolve to a persistent runner [{pending_row.justification}]")
            else:
                violations.append(
                    Violation(
                        finding.path,
                        finding.job,
                        f"relayed job can resolve to a persistent runner; pending row no longer applies: "
                        f"backlog digest changed from {pending_row.digest} to {finding.backlog_digest}"
                        + (f" (unresolved: {describe_reasons(finding.unresolved, reasons)})" if reasons else ""),
                    )
                )
            continue
        if row is None:
            needed = ", ".join(reason for reason in REASONS if reason in reasons)
            violations.append(
                Violation(
                    finding.path,
                    finding.job,
                    ("relayed" if finding.relay else "privileged-event")
                    + " job can resolve to a persistent runner and "
                    f"{REVIEW_REQUIRED} (review digest {finding.review_digest}"
                    + (f", backlog digest {finding.backlog_digest}" if finding.relay else "")
                    + ")"
                    + (
                        f"; its entry also needs `dynamic: {needed}: <justification>` for: "
                        + describe_reasons(finding.unresolved, reasons)
                        if reasons
                        else ""
                    ),
                )
            )
            continue
        missing = reasons - row.reasons
        extra = [reason for reason in REASONS if reason in row.reasons - reasons]
        if row.digest != finding.review_digest:
            stale_reviews.append(
                f"STALE REVIEWED: {label}: content changed since review "
                f"(reviewed {row.digest}, now {finding.review_digest})"
                + (
                    "; newly unresolved reasons dynamic: does not name: "
                    + describe_reasons(finding.unresolved, missing)
                    if missing
                    else ""
                )
                + (f"; dynamic: names reasons the job no longer has: {', '.join(extra)}" if extra else "")
                + "; re-review before relisting"
            )
        elif missing:
            violations.append(
                Violation(
                    finding.path,
                    finding.job,
                    "unclassifiable: unresolved reasons the entry's dynamic: does not name: "
                    + describe_reasons(finding.unresolved, missing)
                    + "; the dynamic: field must name each with a justification that covers it",
                )
            )
        elif extra:
            stale_reviews.append(
                f"STALE REVIEWED: {label}: dynamic: names reasons the job no longer has: "
                f"{', '.join(extra)}; remove them"
            )
        else:
            print(f"REVIEWED: {label} [{row.justification}]" + (f" [{row.dynamic}]" if row.dynamic else ""))
    for violation in violations:
        print(f"VIOLATION: {violation}")
    reported = {(violation.path, violation.job) for violation in found} | ratcheted
    stale = sorted(set(pending) - reported)
    for path, job in stale:
        print(f"STALE PENDING: {path}: job '{job}' no longer violates; remove it from the pending file")
    present = {(finding.path, finding.job) for finding in findings}
    for path, job in sorted(set(reviewed) - present):
        stale_reviews.append(
            f"STALE REVIEWED: {path}: job '{job}' no longer needs review "
            "(removed, renamed, unreachable, or hosted-only); remove its entry"
        )
    for line in stale_reviews:
        print(line)
    if violations or stale or stale_reviews:
        return 1
    print(
        f"clean: no pull_request job in {arguments.workflow_dir} can reach a persistent runner"
        + (f", {len(pending)} relayed job(s) are in the frozen backlog" if pending else "")
        + f", and every privileged-event job that can is one of {len(reviewed)} reviewed entries"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
