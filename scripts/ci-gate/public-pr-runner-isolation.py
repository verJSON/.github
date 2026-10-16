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

pull_request_target jobs that materialize the head (a checkout whose ref or
repository is not base-only, `gh pr checkout`, or a git command in a job naming
a head-derived value) are held to the same rule. Remote callees under
pull_request_target are reported as NOT FOLLOWED; workflow_run and
issue_comment are out of scope (ADR 0235).

Verjson/.github ADR 0235.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
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
    selector_digest: str = ""

    def __str__(self) -> str:
        return f"{self.path}: job '{self.job}': {self.detail}"


def selector_digest(runs_on) -> str:
    """SHA-256 of a job's runs-on value as canonical JSON, keying pending rows
    to the exact reviewed selector rather than to a job name alone."""
    canonical = json.dumps(runs_on, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
            return [self.event]
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
    r"|\$\{\{\s*inputs\.",
    re.I,
)
GIT_MATERIALIZE = re.compile(
    r"\bgh\s+pr\s+checkout\b"
    r"|\bgit\b[^\n]*?\b(?:fetch|checkout|switch|worktree|pull|reset|merge|cherry-pick|clone|am|apply)\b"
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


def materializes_head(job: dict) -> bool:
    """Whether a pull_request_target job can put head-controlled bytes on disk.

    Over-approximates: a checkout whose ref or repository is anything but a
    base-only value, `gh pr checkout`, or a git command in a job that names a
    head-derived value. Head metadata used only for API calls does not count.
    """
    job_text = json.dumps(job, sort_keys=True, default=str)
    names_head = bool(HEAD_TOKEN.search(job_text))
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            return True
        uses = str(step.get("uses", ""))
        if uses.split("@")[0].lower() == "actions/checkout":
            options = step.get("with") or {}
            if not is_base_only(options.get("ref")) or not is_base_only(options.get("repository")):
                return True
        run = step.get("run")
        if isinstance(run, str) and GIT_MATERIALIZE.search(run):
            if "gh pr checkout" in run or names_head:
                return True
    return False


class Scan:
    """Traversal state for one triggering event across local reusable calls."""

    def __init__(self, directory: str, repository: str, event: str) -> None:
        self.directory = directory
        self.repository = repository
        self.event = event
        self.notes: list[str] = []


def check_call(scan: Scan, path: str, name: str, job: dict, depth: int) -> list[Violation]:
    uses = str(job["uses"])
    local = LOCAL_REUSABLE.match(uses)
    if not local:
        if scan.event == "pull_request_target":
            # The base workflow chose this pin; ADR 0235 scopes remote callees
            # under pull_request_target out of the checker, visibly.
            scan.notes.append(f"{path}: job '{name}' calls {uses}; remote callee not followed")
            return []
        raise Undetermined(f"{path}: job '{name}' calls {uses}, whose placement this checker cannot read")
    if depth >= MAX_CALL_DEPTH:
        raise Undetermined(f"{path}: job '{name}' exceeds the reusable-call depth of {MAX_CALL_DEPTH}")
    callee = local.group(1)
    document = load_workflow(os.path.join(scan.directory, callee))
    return check_workflow(
        scan,
        f"{path}:{name} -> {callee}",
        document,
        callee_bindings(job, document),
        depth + 1,
    )


def check_workflow(
    scan: Scan,
    path: str,
    document: dict,
    bindings: dict[str, tuple] | None = None,
    depth: int = 0,
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
            violations.extend(check_call(scan, path, name, job, depth))
        elif scan.event == "pull_request_target" and not materializes_head(job):
            continue
        else:
            violations.extend(
                check_job(
                    path, name, job, scan.repository, bindings, frozenset(unreachable), scan.event
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
                        selector_digest(runs_on),
                    )
                )
                return violations
    return violations


CHECKED_EVENTS = ("pull_request", "pull_request_target")


def check_tree(directory: str, repository: str, notes: list[str] | None = None) -> list[Violation]:
    violations: list[Violation] = []
    names = sorted(
        name for name in os.listdir(directory) if name.endswith((".yml", ".yaml"))
    )
    if not names:
        raise Undetermined(f"{directory}: no workflow files to scan")
    for name in names:
        document = load_workflow(os.path.join(directory, name))
        for event in CHECKED_EVENTS:
            if event not in triggers(document):
                continue
            scan = Scan(directory, repository, event)
            label = name if event == "pull_request" else f"{name} [{event}]"
            violations.extend(check_workflow(scan, label, document))
            if notes is not None:
                notes.extend(scan.notes)
    return violations


PENDING_FORMAT = "path<TAB>job<TAB>selector digest<TAB>reason"


def load_pending(path: str | None) -> dict[tuple[str, str], tuple[str, str]]:
    """Reviewed rows awaiting a recorded follow-up, keyed to the exact selector."""
    if path is None:
        return {}
    pending: dict[tuple[str, str], tuple[str, str]] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            rows = handle.read().splitlines()
    except OSError as error:
        raise Undetermined(f"cannot read pending file: {error}") from error
    for number, row in enumerate(rows, 1):
        if not row.strip() or row.startswith("#"):
            continue
        fields = row.split("\t")
        if len(fields) != 4 or not all(field.strip() for field in fields):
            raise Undetermined(f"{path}:{number}: expected {PENDING_FORMAT}")
        if not DIGEST.fullmatch(fields[2]):
            raise Undetermined(f"{path}:{number}: selector digest must be sha256:<64 hex>")
        pending[(fields[0], fields[1])] = (fields[2], fields[3])
    return pending


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repository", default="Verjson/.github")
    parser.add_argument("--pending", help=f"reviewed {PENDING_FORMAT} exceptions")
    parser.add_argument("workflow_dir")
    arguments = parser.parse_args(argv)
    try:
        pending = load_pending(arguments.pending)
        notes: list[str] = []
        found = check_tree(arguments.workflow_dir, arguments.repository, notes)
    except Undetermined as error:
        print(f"UNDETERMINED: {error}", file=sys.stderr)
        return 2
    violations = []
    for violation in found:
        row = pending.get((violation.path, violation.job))
        if row is None:
            violations.append(violation)
        elif row[0] != violation.selector_digest:
            violations.append(
                violation._replace(
                    detail=f"{violation.detail}; pending row no longer applies: selector digest "
                    f"changed from {row[0]} to {violation.selector_digest}"
                )
            )
        else:
            print(f"PENDING: {violation} [{row[1]}]")
    for note in notes:
        print(f"NOT FOLLOWED: {note}")
    for violation in violations:
        print(f"VIOLATION: {violation}")
    reported = {(violation.path, violation.job) for violation in found}
    stale = sorted(set(pending) - reported)
    for path, job in stale:
        print(f"STALE PENDING: {path}: job '{job}' no longer violates; remove it from the pending file")
    if violations or stale:
        return 1
    print(
        f"clean: no pull_request job, and no pull_request_target job that materializes the "
        f"head, in {arguments.workflow_dir} can reach a persistent runner"
        + (f" beyond {len(pending)} reviewed pending job(s)" if pending else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
