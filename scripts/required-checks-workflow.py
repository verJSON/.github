#!/usr/bin/env python3
"""Inspect the workflow shapes that can publish required check contexts."""

from __future__ import annotations

import json
import re
import shlex
import sys


class WorkflowSyntaxError(ValueError):
    pass


BLOCK_SCALAR = re.compile(r":\s*[|>](?:[1-9][+-]?|[+-][1-9]?)?\s*$")
MERGE_KEY = re.compile(r"(?:^|[\[{,])\s*(?:-\s*)?<<\s*:")
NODE_PROPERTY = re.compile(
    r"(?:^|[\[{,:])\s*(?:-\s*)?(?:[&*][A-Za-z0-9_-]+|!(?!=)(?:[^\s,\]}]+)?)"
)


def mask_quoted(value: str) -> str:
    masked = list(value)
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(value):
        char = value[index]
        if quote == '"':
            masked[index] = " "
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif quote == "'":
            masked[index] = " "
            if char == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    masked[index + 1] = " "
                    index += 1
                else:
                    quote = None
        elif char in "'\"":
            quote = char
            masked[index] = " "
        index += 1
    return "".join(masked)


def mask_expressions(value: str) -> str:
    return re.sub(r"\$\{\{.*?\}\}", lambda match: " " * len(match.group()), value)


def reject_unsupported_yaml(lines: list[str]) -> None:
    block_scalar_indent: int | None = None
    for line in lines:
        if not line.strip():
            continue
        line_indent = indentation(line)
        if block_scalar_indent is not None and line_indent > block_scalar_indent:
            continue
        block_scalar_indent = None
        structural = mask_expressions(mask_quoted(line))
        if MERGE_KEY.search(structural) or NODE_PROPERTY.search(structural):
            raise WorkflowSyntaxError("YAML anchors, aliases, merge keys, and tags are unsupported")
        if BLOCK_SCALAR.search(structural):
            block_scalar_indent = line_indent


def strip_comment(line: str) -> str:
    out: list[str] = []
    quote: str | None = None
    for index, char in enumerate(line):
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            out.append(char)
            continue
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            break
        out.append(char)
    return "".join(out).rstrip()


def split_top_level(text: str, delimiter: str = ",") -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    stack: list[str] = []
    quote: str | None = None
    pairs = {"[": "]", "{": "}"}
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            continue
        if char in pairs:
            stack.append(pairs[char])
        elif char in "]}":
            if not stack or stack.pop() != char:
                raise WorkflowSyntaxError("unbalanced flow collection")
        elif char == delimiter and not stack:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if quote or stack:
        raise WorkflowSyntaxError("unterminated flow collection")
    if "".join(current).strip():
        parts.append("".join(current).strip())
    return parts


def normalize_key(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def split_mapping_entry(text: str) -> tuple[str, str]:
    stack: list[str] = []
    quote: str | None = None
    pairs = {"[": "]", "{": "}"}
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
        elif char in pairs:
            stack.append(pairs[char])
        elif char in "]}":
            if not stack or stack.pop() != char:
                raise WorkflowSyntaxError("unbalanced flow collection")
        elif char == ":" and not stack:
            return normalize_key(text[:index]), text[index + 1 :].strip()
    raise WorkflowSyntaxError(f"mapping entry has no value separator: {text}")


def scalar(value: str) -> str:
    return normalize_key(value.strip())


def flow_mapping(value: str) -> dict[str, str]:
    value = value.strip()
    if not (value.startswith("{") and value.endswith("}")):
        raise WorkflowSyntaxError("expected a flow mapping")
    body = value[1:-1].strip()
    if not body:
        return {}
    return dict(split_mapping_entry(part) for part in split_top_level(body))


def flow_sequence(value: str) -> list[str]:
    value = value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        raise WorkflowSyntaxError("expected a flow sequence")
    body = value[1:-1].strip()
    return [scalar(part) for part in split_top_level(body)] if body else []


def indentation(line: str) -> int:
    if "\t" in line[: len(line) - len(line.lstrip())]:
        raise WorkflowSyntaxError("tab indentation is ambiguous")
    return len(line) - len(line.lstrip())


def mapping_line(line: str) -> tuple[str, str]:
    return split_mapping_entry(line.strip())


def reject_duplicate_top_level_keys(lines: list[str]) -> None:
    seen: set[str] = set()
    for line in lines:
        if not line.strip() or indentation(line) != 0:
            continue
        key, _ = mapping_line(line)
        canonical_key = "on" if key in {"on", "true"} else key
        if canonical_key in seen:
            raise WorkflowSyntaxError(f"duplicate top-level mapping key: {canonical_key}")
        seen.add(canonical_key)


def top_level(lines: list[str], wanted: str) -> tuple[str, list[str]] | None:
    aliases = {wanted, "true" if wanted == "on" else wanted}
    for index, line in enumerate(lines):
        if not line.strip() or indentation(line) != 0:
            continue
        key, value = mapping_line(line)
        if key not in aliases:
            continue
        end = index + 1
        while end < len(lines) and (not lines[end].strip() or indentation(lines[end]) > 0):
            end += 1
        return value, lines[index + 1 : end]
    return None


def direct_entries(lines: list[str], base_indent: int) -> dict[str, tuple[str, list[str]]]:
    entries: dict[str, tuple[str, list[str]]] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or indentation(line) != base_indent:
            index += 1
            continue
        key, value = mapping_line(line)
        end = index + 1
        while end < len(lines) and (not lines[end].strip() or indentation(lines[end]) > base_indent):
            end += 1
        if key in entries:
            raise WorkflowSyntaxError(f"duplicate mapping key: {key}")
        entries[key] = (value, lines[index + 1 : end])
        index = end
    return entries


def trigger_state(lines: list[str]) -> tuple[bool, bool]:
    trigger = top_level(lines, "on")
    if trigger is None:
        return False, False
    value, children = trigger
    events: dict[str, tuple[str, list[str]]] = {}
    if value:
        if value.startswith("{"):
            events = {key: (config, []) for key, config in flow_mapping(value).items()}
        elif value.startswith("["):
            events = {key: ("", []) for key in flow_sequence(value)}
        else:
            events = {scalar(value): ("", [])}
    elif children:
        significant = [line for line in children if line.strip()]
        base_indent = min(indentation(line) for line in significant)
        if all(line.strip().startswith("-") for line in significant if indentation(line) == base_indent):
            for line in significant:
                if indentation(line) == base_indent:
                    events[scalar(line.strip()[1:].strip())] = ("", [])
        else:
            events = direct_entries(children, base_indent)

    path_filter = False
    for config, nested in events.values():
        if config.startswith("{"):
            path_filter = path_filter or bool({"paths", "paths-ignore"} & flow_mapping(config).keys())
        elif nested:
            nested_lines = [line for line in nested if line.strip()]
            if nested_lines:
                nested_indent = min(indentation(line) for line in nested_lines)
                path_filter = path_filter or bool(
                    {"paths", "paths-ignore"} & direct_entries(nested, nested_indent).keys()
                )

    pull_request = events.get("pull_request")
    if pull_request is None:
        return path_filter, False
    config, nested = pull_request
    unconditional = config in {"", "{}"} and not any(line.strip() for line in nested)
    return path_filter, unconditional


def jobs(lines: list[str]) -> dict[str, dict[str, tuple[str, list[str]]]]:
    block = top_level(lines, "jobs")
    if block is None:
        return {}
    value, children = block
    if value:
        raise WorkflowSyntaxError("jobs must be a block mapping")
    significant = [line for line in children if line.strip()]
    if not significant:
        return {}
    job_indent = min(indentation(line) for line in significant)
    result: dict[str, dict[str, tuple[str, list[str]]]] = {}
    for name, (inline, body) in direct_entries(children, job_indent).items():
        if inline:
            raise WorkflowSyntaxError("job definitions must be block mappings")
        body_lines = [line for line in body if line.strip()]
        if not body_lines:
            result[name] = {}
            continue
        body_indent = min(indentation(line) for line in body_lines)
        if body_indent <= job_indent:
            raise WorkflowSyntaxError("job body must be nested below its key")
        result[name] = direct_entries(body, body_indent)
    return result


GENERATED_TARGET = re.compile(
    r"(?:Verjson|verJSON)/\.github/\.github/workflows/generated-artifacts\.yml@([0-9a-f]{40})"
)
LEGACY_TARGET = re.compile(
    r"(?:(?:Verjson|verJSON)/\.github/\.github/workflows/changelog-validate\.yml@[0-9a-f]{40}|"
    r"\./\.github/workflows/changelog-validate\.yml)"
)


def input_mapping(entry: tuple[str, list[str]]) -> dict[str, str]:
    value, children = entry
    if value:
        return {key: scalar(item) for key, item in flow_mapping(value).items()}
    significant = [line for line in children if line.strip()]
    if not significant:
        return {}
    base_indent = min(indentation(line) for line in significant)
    return {key: scalar(item) for key, (item, _) in direct_entries(children, base_indent).items()}


def generated_changelog_state(
    parsed_jobs: dict[str, dict[str, tuple[str, list[str]]]], expected_job: str
) -> str:
    callers: list[tuple[str, dict[str, tuple[str, list[str]]], str]] = []
    for name, job in parsed_jobs.items():
        uses = scalar(job.get("uses", ("", []))[0])
        if GENERATED_TARGET.fullmatch(uses) or LEGACY_TARGET.fullmatch(uses):
            callers.append((name, job, uses))
    if not callers:
        return "absent"
    if len(callers) != 1:
        return "invalid"
    name, job, uses = callers[0]
    match = GENERATED_TARGET.fullmatch(uses)
    if match is None or set(job) != {"uses", "with"}:
        return "invalid"
    inputs = input_mapping(job["with"])
    required = {"changelog", "contract_ref"}
    valid = (
        name == expected_job
        and set(inputs) in (required, required | {"adr-index"})
        and inputs.get("changelog") == "true"
        and inputs.get("contract_ref") == match.group(1)
        and ("adr-index" not in inputs or inputs["adr-index"] == "true")
    )
    return "valid" if valid else "invalid"


def step_mappings(entry: tuple[str, list[str]]) -> list[dict[str, str]]:
    value, children = entry
    if value:
        raise WorkflowSyntaxError("steps must be a block sequence")
    significant = [line for line in children if line.strip()]
    if not significant:
        return []
    item_indent = min(indentation(line) for line in significant)
    starts = [
        index
        for index, line in enumerate(children)
        if line.strip().startswith("-") and indentation(line) == item_indent
    ]
    steps: list[dict[str, str]] = []
    for offset, start in enumerate(starts):
        end = starts[offset + 1] if offset + 1 < len(starts) else len(children)
        first = children[start].strip()[1:].strip()
        body = children[start + 1 : end]
        values: dict[str, str] = {}
        if first:
            key, item = split_mapping_entry(first)
            values[key] = scalar(item)
        if body:
            body_lines = [line for line in body if line.strip()]
            body_indent = min((indentation(line) for line in body_lines), default=item_indent + 1)
            for key, (item, nested) in direct_entries(body, body_indent).items():
                if item in {"|", "|-", ">", ">-"}:
                    values[key] = "\n".join(line.strip() for line in nested if line.strip()).strip()
                elif not item and nested:
                    mapping = input_mapping((item, nested))
                    values[key] = "&".join(f"{k}={v}" for k, v in sorted(mapping.items()))
                else:
                    values[key] = scalar(item)
        steps.append(values)
    return steps


def is_cache_environment_command(command: str) -> bool:
    variable = r"\$(?:RUNNER_TEMP|\{RUNNER_TEMP\})"
    destination = r'"\$(?:GITHUB_ENV|\{GITHUB_ENV\})"'
    cache_word = rf'"VERJSON_CHANGELOG_TOOL_CACHE={variable}/verjson-changelog-tools"'
    return (
        re.fullmatch(
            rf"[ \t]*echo[ \t]+{cache_word}[ \t]*>>[ \t]*{destination}[ \t]*",
            command,
        )
        is not None
    )


def is_contract_test_command(command: str) -> bool:
    if re.search(r"[\r\n;&|<>]", command):
        return False
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        return False
    return words == ["bash", "scripts/changelog-contract.test.sh"]


def has_canonical_workflow_permissions(entry: tuple[str, list[str]] | None) -> bool:
    if entry is None:
        return False
    try:
        return input_mapping(entry) == {"contents": "read"}
    except WorkflowSyntaxError:
        return False


def changelog_contract_state(
    parsed_jobs: dict[str, dict[str, tuple[str, list[str]]]],
    workflow_permissions: tuple[str, list[str]] | None,
) -> str:
    job = parsed_jobs.get("changelog-contract")
    if job is None:
        return "absent"
    if not has_canonical_workflow_permissions(workflow_permissions):
        return "invalid"
    allowed = {"runs-on", "timeout-minutes", "steps"}
    if not set(job).issubset(allowed) or "runs-on" not in job or "steps" not in job:
        return "invalid"
    runner_value, runner_children = job["runs-on"]
    if runner_value.startswith("["):
        runners = flow_sequence(runner_value)
    elif runner_value:
        runners = [scalar(runner_value)]
    else:
        significant = [line for line in runner_children if line.strip()]
        runner_indent = min((indentation(line) for line in significant), default=0)
        runners = [
            scalar(line.strip()[1:].strip())
            for line in significant
            if indentation(line) == runner_indent and line.strip().startswith("-")
        ]
        if len(runners) != len(significant):
            return "invalid"
    if not runners or not all(runners):
        return "invalid"
    steps = step_mappings(job["steps"])
    if len(steps) != 3:
        return "invalid"
    checkout, prepare_cache, command = steps
    valid = (
        set(checkout).issubset({"uses", "name", "with"})
        and re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout.get("uses", "")) is not None
        and checkout.get("with", "") == "persist-credentials=false"
        and set(prepare_cache).issubset({"run", "name"})
        and is_cache_environment_command(prepare_cache.get("run", ""))
        and set(command).issubset({"run", "name"})
        and is_contract_test_command(command.get("run", ""))
    )
    return "valid" if valid else "invalid"


def main() -> int:
    if len(sys.argv) != 2:
        raise WorkflowSyntaxError("expected canonical changelog job name")
    lines = [strip_comment(line) for line in sys.stdin.read().splitlines()]
    lines = [line for line in lines if line not in {"---", "..."}]
    reject_unsupported_yaml(lines)
    reject_duplicate_top_level_keys(lines)
    path_filter, pull_request = trigger_state(lines)
    parsed_jobs = jobs(lines)
    json.dump(
        {
            "changelog_contract": changelog_contract_state(
                parsed_jobs, top_level(lines, "permissions")
            ),
            "generated_changelog": generated_changelog_state(parsed_jobs, sys.argv[1]),
            "path_filter": path_filter,
            "pull_request": pull_request,
        },
        sys.stdout,
        sort_keys=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkflowSyntaxError as error:
        print(f"workflow syntax unsupported: {error}", file=sys.stderr)
        raise SystemExit(2)
