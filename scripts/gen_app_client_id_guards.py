#!/usr/bin/env python3
"""Generate the shared App client-ID preflight immediately before every token mint."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from app_client_id_contract import CLIENT_ID_GUARD_RUN


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
TOKEN_ACTION = "actions/create-github-app-token@"
GENERATED_ID_PREFIX = "generated_app_client_id_"
STATUS_CHECK_FUNCTIONS = {"success", "always", "failure", "cancelled"}
LEGACY_GUARD_NAMES = {
    "Require the compatibility App client ID",
    "Require the read-only compatibility App client ID",
    "Require the release App client ID",
    "Require the selected App client ID",
    "Validate authorization App client ID",
    "Validate review-reservation App client ID",
    "Validate the observer App client ID",
}


class GuardGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    replacement: str


def _mapping(node: Node | None) -> dict[str, Node]:
    if not isinstance(node, MappingNode):
        return {}
    return {
        key.value: value
        for key, value in node.value
        if isinstance(key, ScalarNode)
    }


def _get(node: Node | None, key: str) -> Node | None:
    return _mapping(node).get(key)


def _value(node: Node | None) -> str | None:
    return node.value if isinstance(node, ScalarNode) else None


def _line_start(text: str, offset: int) -> int:
    newline = text.rfind("\n", 0, offset)
    return newline + 1


def _indent_at(text: str, line_start: int) -> str:
    line_end = text.find("\n", line_start)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    return line[: len(line) - len(line.lstrip(" "))]


def _step_id(step: Node) -> str | None:
    return _value(_get(step, "id"))


def _step_name(step: Node) -> str | None:
    return _value(_get(step, "name"))


def _is_old_guard(step: Node) -> bool:
    return _step_name(step) in LEGACY_GUARD_NAMES


def _template_markers(expression: str) -> tuple[list[int], list[int]]:
    openings: list[int] = []
    closings: list[int] = []
    index = 0
    while index < len(expression):
        if expression[index] == "'":
            index += 1
            while index < len(expression):
                if expression[index] == "'":
                    if index + 1 < len(expression) and expression[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if expression.startswith("${{", index):
            openings.append(index)
            index += 3
            continue
        if expression.startswith("}}", index):
            closings.append(index)
            index += 2
            continue
        index += 1
    return openings, closings


def _single_quoted_literal_value(expression: str) -> str | None:
    expression = expression.strip()
    if len(expression) < 2 or expression[0] != "'" or expression[-1] != "'":
        return None
    value: list[str] = []
    index = 1
    while index < len(expression) - 1:
        if expression[index] == "'":
            if index + 1 < len(expression) - 1 and expression[index + 1] == "'":
                value.append("'")
                index += 2
                continue
            return None
        value.append(expression[index])
        index += 1
    return "".join(value)


def _has_status_check_function(expression: str) -> bool:
    index = 0
    while index < len(expression):
        if expression[index] == "'":
            index += 1
            while index < len(expression):
                if expression[index] == "'":
                    if index + 1 < len(expression) and expression[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if expression[index].isalpha() or expression[index] == "_":
            start = index
            while index < len(expression) and (expression[index].isalnum() or expression[index] in "_-"):
                index += 1
            name = expression[start:index].lower()
            after_name = index
            while after_name < len(expression) and expression[after_name].isspace():
                after_name += 1
            if name in STATUS_CHECK_FUNCTIONS and after_name < len(expression) and expression[after_name] == "(":
                return True
            continue
        index += 1
    return False


def _status_check_problem(expression: str | None) -> str | None:
    if expression is None:
        return None
    normalized = expression.strip()
    if "${{" in normalized and not normalized.startswith("${{"):
        return "token mint condition uses embedded expression templates; only one outer expression is supported"
    openings, closings = _template_markers(normalized)
    if openings or closings:
        if openings != [0] or closings != [len(normalized) - 2]:
            return "token mint condition uses embedded expression templates; only one outer expression is supported"
        expression = normalized[3:-2]
        literal_value = _single_quoted_literal_value(expression)
        if literal_value is not None and _has_status_check_function(literal_value):
            return "token mint condition uses an explicit status-check function; the implicit success gate must remain active"
    if _has_status_check_function(expression):
        return "token mint condition uses an explicit status-check function; the implicit success gate must remain active"
    return None


def _terminal_step_end(text: str, step: Node) -> int:
    end = step.end_mark.index
    if end >= len(text):
        return end
    line_start = _line_start(text, end)
    if not text[line_start:end].strip():
        return line_start
    newline = text.find("\n", end)
    return len(text) if newline == -1 else newline + 1


def _step_starts_on_own_line(text: str, step: Node, steps_node: Node | None = None) -> bool:
    if isinstance(steps_node, SequenceNode) and steps_node.flow_style:
        return False
    if not isinstance(step, MappingNode) or step.flow_style:
        return False
    start = _line_start(text, step.start_mark.index)
    prefix = text[start : step.start_mark.index]
    return re.fullmatch(r" *- +", prefix) is not None


def _guard_id(workflow: Path, job_id: str, ordinal: int) -> str:
    stem = f"{workflow.stem}_{job_id}_{ordinal}"
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", stem)
    return f"{GENERATED_ID_PREFIX}{safe}"


def _render_step(
    indent: str,
    step_id: str,
    client_id_expression: str,
    if_expression: str | None,
) -> str:
    lines = [
        f"{indent}- name: Validate GitHub App client ID",
        f"{indent}  id: {step_id}",
    ]
    if if_expression is not None:
        lines.append(f"{indent}  if: {json.dumps(if_expression)}")
    lines.extend(
        [
            f"{indent}  shell: bash",
            f"{indent}  env:",
            f"{indent}    APP_CLIENT_ID: {json.dumps(client_id_expression)}",
            f"{indent}    LC_ALL: C",
            f"{indent}  run: |",
        ]
    )
    lines.extend(f"{indent}    {line}" for line in CLIENT_ID_GUARD_RUN.splitlines())
    return "\n".join(lines) + "\n"


def _workflow_nodes(path: Path, text: str) -> tuple[Node, list[tuple[str, Node]]]:
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        raise GuardGenerationError(f"{path.relative_to(ROOT)}: invalid YAML: {exc}") from exc
    if root is None:
        raise GuardGenerationError(f"{path.relative_to(ROOT)}: empty workflow")
    jobs_node = _get(root, "jobs")
    if not isinstance(jobs_node, MappingNode):
        return root, []
    jobs: list[tuple[str, Node]] = []
    for key, job in jobs_node.value:
        if isinstance(key, ScalarNode):
            jobs.append((key.value, job))
    return root, jobs


def _collect_edits(
    path: Path,
    text: str,
    *,
    require_editable: bool = False,
) -> tuple[list[Edit], int, list[str]]:
    _, jobs = _workflow_nodes(path, text)
    edits: list[Edit] = []
    errors: list[str] = []
    mint_count = 0
    for job_id, job in jobs:
        steps_node = _get(job, "steps")
        if not isinstance(steps_node, SequenceNode):
            continue
        steps = list(steps_node.value)
        token_indexes = [
            index
            for index, step in enumerate(steps)
            if (_value(_get(step, "uses")) or "").startswith(TOKEN_ACTION)
        ]
        consumed_old_guards: set[int] = set()
        consumed_generated_guards: set[int] = set()
        for ordinal, index in enumerate(token_indexes, start=1):
            mint_count += 1
            step = steps[index]
            if require_editable and not _step_starts_on_own_line(text, step, steps_node):
                errors.append(
                f"{path.relative_to(ROOT)}:{step.start_mark.line + 1}: token mint step layout cannot be rewritten safely"
                )
                continue
            client_id_expression = _value(_get(_get(step, "with"), "client-id"))
            if not client_id_expression:
                errors.append(
                    f"{path.relative_to(ROOT)}:{step.start_mark.line + 1}: token mint lacks string client-id"
                )
                continue
            if_expression = _value(_get(step, "if"))
            condition_problem = _status_check_problem(if_expression)
            if condition_problem:
                errors.append(
                    f"{path.relative_to(ROOT)}:{step.start_mark.line + 1}: {condition_problem}"
                )
                continue
            guard_id = _guard_id(path, job_id, ordinal)
            token_line_start = _line_start(text, step.start_mark.index)
            indent = _indent_at(text, token_line_start)
            replacement = _render_step(indent, guard_id, client_id_expression, if_expression)
            previous_index = index - 1
            previous = steps[previous_index] if previous_index >= 0 else None
            if previous is not None and (
                (_step_id(previous) or "").startswith(GENERATED_ID_PREFIX)
                or _is_old_guard(previous)
            ):
                if require_editable and not _step_starts_on_own_line(text, previous, steps_node):
                    guard_type = (
                        "generated"
                        if (_step_id(previous) or "").startswith(GENERATED_ID_PREFIX)
                        else "legacy"
                    )
                    errors.append(
                        f"{path.relative_to(ROOT)}:{previous.start_mark.line + 1}: "
                        f"{guard_type} guard step layout cannot be rewritten safely"
                    )
                    continue
                previous_line_start = _line_start(text, previous.start_mark.index)
                edits.append(Edit(previous_line_start, token_line_start, replacement))
                if (_step_id(previous) or "").startswith(GENERATED_ID_PREFIX):
                    consumed_generated_guards.add(previous_index)
                else:
                    consumed_old_guards.add(previous_index)
            else:
                edits.append(Edit(token_line_start, token_line_start, replacement))
        for index, step in enumerate(steps):
            if _is_old_guard(step) and index not in consumed_old_guards:
                if require_editable and not _step_starts_on_own_line(text, step, steps_node):
                    errors.append(
                    f"{path.relative_to(ROOT)}:{step.start_mark.line + 1}: legacy guard step layout cannot be rewritten safely"
                    )
                    continue
                if require_editable and index + 1 < len(steps) and not _step_starts_on_own_line(
                    text, steps[index + 1], steps_node
                ):
                    errors.append(
                        f"{path.relative_to(ROOT)}:{step.start_mark.line + 1}: "
                        "following step layout cannot be preserved while removing a legacy guard"
                    )
                    continue
                start = _line_start(text, step.start_mark.index)
                if index + 1 < len(steps):
                    end = _line_start(text, steps[index + 1].start_mark.index)
                else:
                    end = _terminal_step_end(text, step)
                edits.append(Edit(start, end, ""))
            step_id = _step_id(step) or ""
            if step_id.startswith(GENERATED_ID_PREFIX) and index not in consumed_generated_guards:
                if require_editable and not _step_starts_on_own_line(text, step, steps_node):
                    errors.append(
                    f"{path.relative_to(ROOT)}:{step.start_mark.line + 1}: generated guard step layout cannot be rewritten safely"
                    )
                    continue
                if require_editable and index + 1 < len(steps) and not _step_starts_on_own_line(
                    text, steps[index + 1], steps_node
                ):
                    errors.append(
                        f"{path.relative_to(ROOT)}:{step.start_mark.line + 1}: "
                        "following step layout cannot be preserved while removing a generated guard"
                    )
                    continue
                start = _line_start(text, step.start_mark.index)
                if index + 1 < len(steps):
                    end = _line_start(text, steps[index + 1].start_mark.index)
                else:
                    end = _terminal_step_end(text, step)
                edits.append(Edit(start, end, ""))
    return edits, mint_count, errors


def _apply_edits(text: str, edits: list[Edit]) -> str:
    ordered = sorted(edits, key=lambda edit: (edit.start, edit.end), reverse=True)
    previous_start = len(text) + 1
    for edit in ordered:
        if edit.end > previous_start:
            raise GuardGenerationError("overlapping guard generation edits")
        text = text[: edit.start] + edit.replacement + text[edit.end :]
        previous_start = edit.start
    return text


def _guard_problems(path: Path, text: str) -> list[str]:
    _, jobs = _workflow_nodes(path, text)
    problems: list[str] = []
    for job_id, job in jobs:
        steps_node = _get(job, "steps")
        if steps_node is None:
            continue
        if not isinstance(steps_node, SequenceNode):
            problems.append(f"{path.relative_to(ROOT)}: job {job_id} steps is not a sequence")
            continue
        steps = list(steps_node.value)
        for index, step in enumerate(steps):
            if not (_value(_get(step, "uses")) or "").startswith(TOKEN_ACTION):
                continue
            if index == 0:
                problems.append(f"{path.relative_to(ROOT)}:{step.start_mark.line + 1}: mint has no preceding guard")
                continue
            guard = steps[index - 1]
            prefix = f"{path.relative_to(ROOT)}:{step.start_mark.line + 1}"
            condition_problem = _status_check_problem(_value(_get(step, "if")))
            if condition_problem:
                problems.append(f"{prefix}: {condition_problem}")
            if not (_step_id(guard) or "").startswith(GENERATED_ID_PREFIX):
                problems.append(f"{prefix}: mint is not preceded by generated client-ID guard")
                continue
            if _step_name(guard) != "Validate GitHub App client ID":
                problems.append(f"{prefix}: generated guard step name changed")
            guard_fields = _mapping(guard)
            if _value(_get(guard, "shell")) != "bash":
                problems.append(f"{prefix}: generated guard does not use bash")
            if _value(_get(guard, "run")) != CLIENT_ID_GUARD_RUN:
                problems.append(f"{prefix}: generated guard body drifted")
            guard_env = _mapping(_get(guard, "env"))
            mint_with = _mapping(_get(step, "with"))
            if _value(guard_env.get("APP_CLIENT_ID")) != _value(mint_with.get("client-id")):
                problems.append(f"{prefix}: guard and token mint client IDs differ")
            if _value(guard_env.get("LC_ALL")) != "C":
                problems.append(f"{prefix}: generated guard does not force LC_ALL=C")
            if _value(_get(guard, "if")) != _value(_get(step, "if")):
                problems.append(f"{prefix}: guard and token mint conditions differ")
            if "continue-on-error" in guard_fields:
                problems.append(f"{prefix}: generated guard failure is suppressed")
    for _, job in jobs:
        steps_node = _get(job, "steps")
        if not isinstance(steps_node, SequenceNode):
            continue
        for step in steps_node.value:
            if _is_old_guard(step):
                problems.append(
                    f"{path.relative_to(ROOT)}:{step.start_mark.line + 1}: legacy per-workflow guard remains"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite generated guard steps")
    args = parser.parse_args(argv)
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    errors: list[str] = []
    total_mints = 0
    generated_files: dict[Path, str] = {}
    for path in files:
        text = path.read_text(encoding="utf-8")
        try:
            edits, mint_count, generation_errors = _collect_edits(
                path,
                text,
                require_editable=args.write,
            )
            total_mints += mint_count
            errors.extend(generation_errors)
            if generation_errors:
                errors.extend(_guard_problems(path, text))
                continue
            if args.write:
                generated = _apply_edits(text, edits)
                errors.extend(_guard_problems(path, generated))
                if generated != text:
                    generated_files[path] = generated
            else:
                errors.extend(_guard_problems(path, text))
        except GuardGenerationError as exc:
            errors.append(str(exc))
    if errors:
        for error in dict.fromkeys(errors):
            print(f"FAIL - {error}", file=sys.stderr)
        return 1
    if args.write:
        for path, generated in generated_files.items():
            path.write_text(generated, encoding="utf-8")
        print(f"generated guards for {total_mints} GitHub App token mints")
        return 0
    print(f"ok - generated guards cover {total_mints} GitHub App token mints")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
