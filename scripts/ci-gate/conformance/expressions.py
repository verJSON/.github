#!/usr/bin/env python3
"""A restricted evaluator for the GitHub expression subset used in `if:` guards.

The point of this module is what it *refuses* to do. A conformance harness that
guesses `false` for an expression it does not understand reports every guarded
step as skipped, and a suite built on that asserts nothing while looking
thorough — the same shape of failure as `Verjson/verjson-ci#184`, where a job
reported success having executed nothing. So every construct is either
implemented exactly or raises `UnsupportedExpression`, and the caller is
expected to let that propagate.
"""

from __future__ import annotations

import ast
import json
import math
import re


class UnsupportedExpression(Exception):
    """A guard uses a construct this evaluator does not implement.

    Raised rather than approximated: an approximated guard silently changes
    which steps the harness believes ran.
    """


class UnknownContext(Exception):
    """A guard reads a context value the scenario does not bind.

    A scenario that forgets to bind an input must fail loudly, not inherit a
    default that happens to make the assertion pass.
    """


_FUNCTION_CALL = re.compile(r'\b(?P<name>[a-zA-Z][a-zA-Z0-9_-]*)\s*\(')
_CONTEXT_REF = re.compile(
    r'\b(?:needs|inputs|steps|github|runner|env|vars|matrix|secrets)\b'
    r'(?:\.[A-Za-z0-9_-]+)+')

_SUPPORTED_FUNCTIONS = {'always', 'success', 'failure', 'cancelled', 'hashFiles', 'fromJSON', 'toJSON'}
_JSON_NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z')


def _from_json(value: object) -> object:
    if not isinstance(value, str):
        raise UnsupportedExpression("fromJSON requires a JSON string")

    def reject_nonstandard_constant(token: str) -> object:
        raise ValueError(f"nonstandard JSON constant {token}")

    try:
        parsed = json.loads(value, parse_constant=reject_nonstandard_constant)
    except ValueError as error:
        raise UnsupportedExpression("fromJSON input is invalid JSON") from error
    if _contains_non_finite_number(parsed):
        raise UnsupportedExpression("fromJSON number is outside the supported range")
    return _JsonValue(parsed)


def _contains_non_finite_number(value: object) -> bool:
    if type(value) is float:
        return not math.isfinite(value)
    if isinstance(value, list):
        return any(_contains_non_finite_number(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_non_finite_number(item) for item in value.values())
    return False


class _JsonValue:
    __slots__ = ("value",)

    def __init__(self, value: object) -> None:
        self.value = value

    def __bool__(self) -> bool:
        if self.value is None:
            return False
        if type(self.value) is bool:
            return self.value
        if type(self.value) in (int, float):
            return self.value != 0
        if type(self.value) is str:
            return self.value != ""
        if isinstance(self.value, (list, dict)):
            return True
        raise UnsupportedExpression("fromJSON value has unsupported truthiness")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _JsonValue):
            other = other.value
        return _github_loose_equal(self.value, other)


def _github_number(value: object) -> int | float:
    if value is None:
        return 0
    if type(value) is bool:
        return int(value)
    if type(value) in (int, float):
        try:
            number = float(value)
        except OverflowError as error:
            raise UnsupportedExpression("fromJSON number is outside the supported range") from error
        if not math.isfinite(number):
            raise UnsupportedExpression("fromJSON number is outside the supported range")
        return number
    if type(value) is str:
        if value == "":
            return 0
        if not _JSON_NUMBER.fullmatch(value):
            raise UnsupportedExpression("mixed fromJSON comparison uses an unsupported string")
        try:
            number = float(value)
        except ValueError as error:
            raise UnsupportedExpression("mixed fromJSON comparison uses an unsupported string") from error
        if not math.isfinite(number):
            raise UnsupportedExpression("mixed fromJSON comparison uses a non-finite number")
        return number
    raise UnsupportedExpression("mixed fromJSON comparison uses an unsupported JSON type")


def _github_loose_equal(left: object, right: object) -> bool:
    if isinstance(left, _JsonValue):
        left = left.value
    if isinstance(right, _JsonValue):
        right = right.value
    if isinstance(left, (list, dict)) or isinstance(right, (list, dict)):
        raise UnsupportedExpression("fromJSON object and array equality is unsupported")
    if left is None and right is None:
        return True
    if type(left) is type(right) and type(left) in (bool, str):
        if type(left) is bool:
            return left == right
        if not left.isascii() or not right.isascii():
            raise UnsupportedExpression("non-ASCII string equality is unsupported")
        return left.casefold() == right.casefold()
    if type(left) in (int, float) and type(right) in (int, float):
        return _github_number(left) == _github_number(right)
    return _github_number(left) == _github_number(right)


def _to_json(value: object) -> _JsonValue:
    if isinstance(value, _JsonValue):
        value = value.value
    modeled_value = value is None or type(value) is bool or (
        type(value) is str and value.isascii()
    )
    if not modeled_value:
        raise UnsupportedExpression("toJSON value serialization is unsupported")
    return _JsonValue(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _read_expression_string(expression: str, start: int) -> tuple[str, int]:
    value: list[str] = []
    index = start + 1
    while index < len(expression):
        if expression[index] == "'":
            if expression.startswith("''", index):
                value.append("'")
                index += 2
                continue
            return ''.join(value), index + 1
        value.append(expression[index])
        index += 1
    raise UnsupportedExpression("unterminated expression string literal")


def _rewrite_operators(expression: str, placeholders: dict[str, str]) -> str:
    pieces: list[str] = []
    placeholder_names = sorted(placeholders, key=len, reverse=True)
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "'":
            value, index = _read_expression_string(expression, index)
            pieces.append(repr(value))
            continue
        if char == '"':
            raise UnsupportedExpression("double-quoted expression strings are unsupported")

        replaced_context = False
        for name in placeholder_names:
            if not expression.startswith(name, index):
                continue
            before_ok = index == 0 or not (
                expression[index - 1].isalnum() or expression[index - 1] in "_.-"
            )
            after = index + len(name)
            after_ok = after == len(expression) or not (
                expression[after].isalnum() or expression[after] in "_.-"
            )
            if before_ok and after_ok:
                pieces.append(placeholders[name])
                index = after
                replaced_context = True
                break
        if replaced_context:
            continue

        if expression.startswith('!==', index):
            raise UnsupportedExpression("unsupported operator !==")
        if expression.startswith('&&', index):
            pieces.append(' and ')
            index += 2
            continue
        if expression.startswith('||', index):
            pieces.append(' or ')
            index += 2
            continue
        if char == '!' and not expression.startswith('!=', index):
            pieces.append(' not ')
            index += 1
            continue
        pieces.append(char)
        index += 1
    return ''.join(pieces)


def _python_source(expression: str, placeholders: dict[str, str]) -> str:
    return _rewrite_operators(expression, placeholders)


class _GitHubComparisonRewriter(ast.NodeTransformer):
    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise UnsupportedExpression("chained comparisons are unsupported")
        operator = node.ops[0]
        if not isinstance(operator, (ast.Eq, ast.NotEq)):
            return node
        equality = ast.Call(
            func=ast.Name(id="_github_loose_equal", ctx=ast.Load()),
            args=[node.left, node.comparators[0]],
            keywords=[],
        )
        if isinstance(operator, ast.NotEq):
            equality = ast.UnaryOp(op=ast.Not(), operand=equality)
        return ast.copy_location(equality, node)


class Evaluator:
    """Evaluates one workflow's guards against one scenario's bindings.

    ASCII string equality is case-insensitive, matching GitHub. Non-ASCII
    string equality raises `UnsupportedExpression` rather than approximating
    GitHub's ordinal-ignore-case comparison.
    """

    def __init__(self, bindings: dict[str, object], *, functions: dict[str, object] | None = None):
        self.bindings = bindings
        self.functions = functions or {}

    def evaluate(self, expression: str | bool | None) -> bool:
        if expression is None:
            return True
        if isinstance(expression, bool):
            return expression
        expression = str(expression).strip()
        if expression.startswith('${{') and expression.endswith('}}'):
            expression = expression[3:-2].strip()

        placeholders: dict[str, str] = {}
        values: dict[str, object] = {}

        for match in _FUNCTION_CALL.finditer(expression):
            name = match.group('name')
            if name not in _SUPPORTED_FUNCTIONS:
                raise UnsupportedExpression(f'unsupported function {name}()')

        for index, name in enumerate(sorted(set(_CONTEXT_REF.findall(expression)),
                                            key=len, reverse=True)):
            if name not in self.bindings:
                raise UnknownContext(name)
            placeholder = f'_ctx{index}'
            placeholders[name] = placeholder
            values[placeholder] = self.bindings[name]

        source = _python_source(expression, placeholders)
        for name in _SUPPORTED_FUNCTIONS:
            if name in self.functions:
                values[name] = self.functions[name]
        if 'fromJSON' not in values:
            values['fromJSON'] = _from_json
        if 'toJSON' not in values:
            values['toJSON'] = _to_json
        # GitHub's boolean and null literals are lowercase, so they reach the
        # parser as names rather than as Python constants. Plain assignment,
        # not `setdefault`: nothing can legitimately shadow these — placeholders
        # are `_ctxN` and function names come from a fixed set — so a collision
        # is a defect that should be loud rather than silently deferred to.
        values['true'] = True
        values['false'] = False
        values['null'] = None
        values['_github_loose_equal'] = _github_loose_equal

        try:
            tree = ast.parse(source, mode='eval')
        except SyntaxError as error:
            raise UnsupportedExpression(f'{expression!r} -> {source!r}: {error}') from error

        tree = _GitHubComparisonRewriter().visit(tree)
        ast.fix_missing_locations(tree)
        self._reject_unsupported_nodes(tree, expression, values)
        return bool(eval(compile(tree, '<guard>', 'eval'), {'__builtins__': {}}, values))

    @staticmethod
    def _reject_unsupported_nodes(tree: ast.Expression, expression: str,
                                  values: dict[str, object]) -> None:
        allowed = (
            ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
            ast.Compare, ast.Eq, ast.NotEq, ast.Name, ast.Load, ast.Constant,
            ast.Call,
        )
        for node in ast.walk(tree):
            if not isinstance(node, allowed):
                raise UnsupportedExpression(
                    f'{expression!r} contains unsupported {type(node).__name__}')
            # An unbound name would reach `eval` and raise a bare NameError,
            # which is outside this module's typed-error contract and reads to
            # a caller as a harness crash rather than as an unmodelled guard.
            if isinstance(node, ast.Name) and node.id not in values:
                raise UnsupportedExpression(
                    f'{expression!r} reads {node.id!r}, which nothing bound')
