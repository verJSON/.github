"""Typed, ordered CI test levels: parse once, validate once, render once.

A caller declares `ci-levels.json`. `parse_levels` turns it into an immutable
`Plan` or raises `LevelsError` carrying structured `{code, path, message}`
errors. `render_caller` turns a plan into a caller workflow with one `node-ci`
call per level (ADR 0238, 2026-10-22 test-levels amendment).

Cancellation of superseded runs is deliberately absent from the schema: a
reusable workflow cannot declare per-call concurrency, so it stays a caller
workflow-level `concurrency` block, rendered here verbatim.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runner_lane_catalog import HOSTED  # noqa: E402

SCHEMA_VERSION = 1
AGGREGATE_ID = "test-levels"
MAX_LEVELS = 16
MAX_NEEDS = 8
MAX_INPUTS = 64
MAX_INPUT_LENGTH = 4096
DEFAULT_EVENTS = ("pull_request", "push", "workflow_dispatch")
ALL_EVENTS = DEFAULT_EVENTS
CONDITIONS = ("success", "completed")

_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_BRANCH = re.compile(r"^(?!.*((^|/)\.|\.\.|//|\.lock(/|$)|[./]$))[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
# Bare YAML scalars that would not stay strings as job keys, needs entries or env values.
_YAML_SCALAR_IDS = frozenset({"null", "true", "false", "yes", "no", "on", "off", "y", "n"})
MAX_DOCUMENT_BYTES = 1 << 20  # characters
_SECRET = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")

TOP_FIELDS = {"version", "levels", "secrets", "pushBranches"}
LEVEL_FIELDS = {"id", "needs", "failFast", "events", "branches", "inputs"}
NEED_FIELDS = {"level", "condition"}

CONCURRENCY = (
    "concurrency:\n"
    "  group: ci-${{ github.workflow }}-${{ github.event_name == 'pull_request'"
    " && format('pr-{0}', github.event.pull_request.number)"
    " || format('run-{0}', github.run_id) }}\n"
    "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n"
)

AGGREGATE_SCRIPT = """\
set -euo pipefail
failed=0
for ((i = 0; i < LEVEL_COUNT; i++)); do
  id_name="LEVEL_${i}"
  result_name="RESULT_${i}"
  expected_name="EXPECTED_${i}"
  id="${!id_name}"
  result="${!result_name:-}"
  expected="${!expected_name:-}"
  case "${expected}:${result}" in
    true:success | false:skipped) ;;
    *)
      printf '::error::level %s: expected=%s result=%s\\n' "${id}" "${expected}" "${result}"
      failed=1
      ;;
  esac
done
exit "${failed}"
"""


@dataclass(frozen=True)
class PlanError:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


class LevelsError(Exception):
    def __init__(self, errors: list[PlanError]):
        self.errors = tuple(errors)
        super().__init__("; ".join(f"{e.code} at {e.path}: {e.message}" for e in errors))


@dataclass(frozen=True)
class Need:
    level: str
    condition: str


@dataclass(frozen=True)
class Level:
    id: str
    needs: tuple[Need, ...]
    events: tuple[str, ...] | None
    branches: tuple[str, ...] | None
    inputs: tuple[tuple[str, str | bool | int], ...]


@dataclass(frozen=True)
class Plan:
    levels: tuple[Level, ...]
    secrets: tuple[str, ...]
    push_branches: tuple[str, ...]
    explicit: bool


DEFAULT_PLAN = Plan(
    levels=(Level("ci", (), None, None, ()),),
    secrets=(),
    push_branches=("main",),
    explicit=False,
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def _unknown_fields(obj: dict[str, Any], allowed: set[str], path: str, errors: list[PlanError]) -> None:
    for key in sorted(set(obj) - allowed):
        hint = (
            " (cancelling superseded runs is a caller workflow-level concurrency block, not a level field)"
            if key == "cancel"
            else ""
        )
        errors.append(PlanError("unknown_field", f"{path}.{key}", f"field {key!r} is not part of the schema{hint}"))


def _describe(item: Any) -> str:
    return repr(item[:40]) if isinstance(item, str) else f"a {type(item).__name__}"


def _string_list(
    value: Any, path: str, pattern: re.Pattern[str], limit: int, errors: list[PlanError]
) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        errors.append(PlanError("bad_type", path, "expected an array of strings"))
        return None
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not pattern.fullmatch(item):
            errors.append(PlanError("bad_value", f"{path}[{index}]", f"{_describe(item)} does not match {pattern.pattern}"))
        elif item in items:
            errors.append(PlanError("duplicate_value", f"{path}[{index}]", f"duplicate entry {item!r}"))
        else:
            items.append(item)
    if len(value) > limit:
        errors.append(PlanError("too_many", path, f"at most {limit} entries"))
    return tuple(items)


def _matches_declared_type(item: Any, declared: str) -> bool:
    if declared == "boolean":
        return isinstance(item, bool)
    if declared == "number":
        return type(item) is int
    return isinstance(item, str)


def _inert_text(text: str) -> bool:
    if "${{" in text or len(text) > MAX_INPUT_LENGTH:
        return False
    # Control, surrogate, unassigned and line-separator characters are not safe in a YAML double-quoted scalar.
    return not any(
        (unicodedata.category(c) in ("Cc", "Cs", "Cn") and c not in "\t\n") or c in "\u2028\u2029"
        for c in text
    )


def _parse_inputs(value: Any, path: str, known_inputs: dict[str, str], errors: list[PlanError]):
    if not isinstance(value, dict):
        errors.append(PlanError("bad_type", path, "expected an object"))
        return ()
    if len(value) > MAX_INPUTS:
        errors.append(PlanError("too_many", path, f"at most {MAX_INPUTS} inputs"))
    parsed: list[tuple[str, str | bool | int]] = []
    for key, item in value.items():
        item_path = f"{path}.{key}"
        if key not in known_inputs:
            errors.append(PlanError("unknown_input", item_path, f"node-ci.yml declares no input {key!r}"))
        elif not _matches_declared_type(item, known_inputs[key]):
            errors.append(PlanError("bad_input_value", item_path, f"node-ci.yml declares {key!r} as {known_inputs[key]}"))
        elif isinstance(item, str) and not _inert_text(item):
            errors.append(
                PlanError("bad_input_value", item_path, f"no expressions, lone surrogates, or more than {MAX_INPUT_LENGTH} characters")
            )
        else:
            parsed.append((key, item))
    return tuple(parsed)


def _parse_needs(
    value: Any, path: str, preceding: dict[str, Level], declared: set[str], own_id: Any, errors: list[PlanError]
):
    if not isinstance(value, list):
        errors.append(PlanError("bad_type", path, "expected an array"))
        return ()
    if len(value) > MAX_NEEDS:
        errors.append(PlanError("too_many", path, f"at most {MAX_NEEDS} needs"))
    needs: list[Need] = []
    for index, raw in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(raw, dict):
            errors.append(PlanError("bad_type", item_path, "expected an object with level and optional condition"))
            continue
        _unknown_fields(raw, NEED_FIELDS, item_path, errors)
        target = raw.get("level")
        condition = raw.get("condition", "success")
        if condition not in CONDITIONS:
            errors.append(PlanError("bad_value", f"{item_path}.condition", f"expected one of {list(CONDITIONS)}"))
            continue
        if not isinstance(target, str):
            errors.append(PlanError("missing_field", f"{item_path}.level", "level is required"))
        elif target in preceding:
            if any(n.level == target for n in needs):
                errors.append(PlanError("duplicate_need", item_path, f"level {target!r} is already a need"))
            else:
                needs.append(Need(target, condition))
        elif target == own_id:
            errors.append(PlanError("forward_need", item_path, "a level cannot need itself"))
        else:
            errors.append(
                PlanError(
                    "forward_need" if target in declared else "unknown_need",
                    item_path,
                    f"{target!r} must be a level declared before the one that needs it",
                )
            )
    return tuple(needs)


def _parse_level(
    raw: Any, path: str, preceding: dict[str, Level], declared: set[str], known_inputs: dict[str, str], errors: list[PlanError]
):
    if not isinstance(raw, dict):
        errors.append(PlanError("bad_type", path, "expected an object"))
        return None
    _unknown_fields(raw, LEVEL_FIELDS, path, errors)
    level_id = raw.get("id")
    if not isinstance(level_id, str) or not _ID.fullmatch(level_id):
        errors.append(PlanError("bad_value", f"{path}.id", f"id must match {_ID.pattern}"))
        level_id = None
    elif level_id == AGGREGATE_ID or level_id in _YAML_SCALAR_IDS:
        errors.append(PlanError("reserved_id", f"{path}.id", f"{level_id!r} is reserved (aggregate job or YAML scalar)"))
    elif level_id in preceding:
        errors.append(PlanError("duplicate_id", f"{path}.id", f"level {level_id!r} is declared twice"))
    if "failFast" in raw:
        fail_fast = raw["failFast"]
        if not isinstance(fail_fast, bool):
            errors.append(PlanError("bad_type", f"{path}.failFast", "expected a boolean"))
        elif not fail_fast:
            errors.append(
                PlanError("fail_fast_unsupported", f"{path}.failFast", "failFast:false needs in-lane support; not available yet")
            )
    needs = _parse_needs(raw.get("needs", []), f"{path}.needs", preceding, declared, level_id, errors)
    events = branches = None
    if "events" in raw:
        events = _string_list(raw["events"], f"{path}.events", re.compile("^(" + "|".join(ALL_EVENTS) + ")$"), len(ALL_EVENTS), errors)
        if events is not None and not events:
            errors.append(PlanError("empty_work", f"{path}.events", "an empty events list means the level never runs"))
    if "branches" in raw:
        branches = _string_list(raw["branches"], f"{path}.branches", _BRANCH, 32, errors)
        if branches is not None and not branches:
            errors.append(PlanError("empty_work", f"{path}.branches", "an empty branches list means the level never runs"))
    inputs = _parse_inputs(raw.get("inputs", {}), f"{path}.inputs", known_inputs, errors)
    if level_id is None:
        return None
    return Level(level_id, needs, events, branches, inputs)


def _check_input_events(levels: tuple[Level, ...], errors: list[PlanError]) -> None:
    """node-ci.yml fails a secretless mode on the wrong event, so reject the pairing here."""
    for index, level in enumerate(levels):
        inputs = dict(level.inputs)
        path = f"$.levels[{index}].inputs"
        pr, trusted = inputs.get("secretless-pr") is True, inputs.get("secretless-trusted-ref") is True
        events = set(DEFAULT_EVENTS if level.events is None else level.events)
        if pr and trusted:
            errors.append(PlanError("input_event_conflict", path, "enable exactly one secretless event mode"))
        elif pr and events != {"pull_request"}:
            errors.append(PlanError("input_event_conflict", path, "secretless-pr runs only on pull_request; set events to [\"pull_request\"]"))
        elif trusted and not events <= {"push", "workflow_dispatch"}:
            errors.append(
                PlanError("input_event_conflict", path, "secretless-trusted-ref runs only on push or workflow_dispatch; set events")
            )


def _reachable_branches(level: Level, push_branches: tuple[str, ...]) -> tuple[str, ...] | None:
    if level.events != ("push",):
        return level.branches
    if level.branches is None:
        return push_branches
    return tuple(branch for branch in level.branches if branch in push_branches)


def _check_coverage(levels: tuple[Level, ...], push_branches: tuple[str, ...], errors: list[PlanError]) -> None:
    """A level may only need levels that run wherever it runs, or it would skip silently."""
    by_id = {level.id: level for level in levels}
    for index, level in enumerate(levels):
        for need in level.needs:
            upstream = by_id[need.level]
            # A push-only level can only ever see the branches the workflow is triggered on.
            reachable = lambda l: _reachable_branches(l, push_branches)  # noqa: E731
            if not _covers(upstream.events, level.events, DEFAULT_EVENTS) or not _covers(
                reachable(upstream), reachable(level), None
            ):
                errors.append(
                    PlanError(
                        "need_not_covering",
                        f"$.levels[{index}].needs",
                        f"{need.level!r} does not run on every event and branch of {level.id!r}",
                    )
                )


def _check_reachable(levels: tuple[Level, ...], push_branches: tuple[str, ...], errors: list[PlanError]) -> None:
    """A push-only level whose branches never meet the push trigger would skip forever."""
    for index, level in enumerate(levels):
        if level.events == ("push",) and level.branches and not set(level.branches) & set(push_branches):
            errors.append(
                PlanError("unreachable_level", f"$.levels[{index}]", f"none of its branches is in pushBranches {list(push_branches)}")
            )


def _covers(upstream, downstream, default) -> bool:
    upstream = default if upstream is None else upstream
    downstream = default if downstream is None else downstream
    if upstream is None:
        return True
    if downstream is None:
        return False
    return set(downstream) <= set(upstream)


def parse_levels(text: str, known_inputs: dict[str, str], known_secrets: frozenset[str]) -> Plan:
    try:
        return _parse_levels(text, known_inputs, known_secrets)
    except RecursionError as exc:
        raise LevelsError([PlanError("invalid_json", "$", "document nesting is too deep")]) from exc


def _parse_levels(text: str, known_inputs: dict[str, str], known_secrets: frozenset[str]) -> Plan:
    if len(text) > MAX_DOCUMENT_BYTES:
        raise LevelsError([PlanError("too_many", "$", f"at most {MAX_DOCUMENT_BYTES} characters")])
    try:
        document = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, RecursionError) as exc:
        code = "duplicate_key" if "duplicate key" in str(exc) else "invalid_json"
        raise LevelsError([PlanError(code, "$", str(exc))]) from exc
    errors: list[PlanError] = []
    if not isinstance(document, dict):
        raise LevelsError([PlanError("bad_type", "$", "expected an object")])
    _unknown_fields(document, TOP_FIELDS, "$", errors)
    if type(document.get("version")) is not int or document["version"] != SCHEMA_VERSION:
        errors.append(PlanError("bad_value", "$.version", f"expected {SCHEMA_VERSION}"))
    raw_levels = document.get("levels")
    if not isinstance(raw_levels, list):
        errors.append(PlanError("bad_type", "$.levels", "expected an array"))
        raise LevelsError(errors)
    if not raw_levels:
        errors.append(PlanError("empty_work", "$.levels", "declare at least one level"))
    if len(raw_levels) > MAX_LEVELS:
        errors.append(PlanError("too_many", "$.levels", f"at most {MAX_LEVELS} levels"))
    secrets = _string_list(document.get("secrets", []), "$.secrets", _SECRET, 16, errors) or ()
    for index, name in enumerate(secrets):
        if name not in known_secrets:
            errors.append(PlanError("unknown_secret", f"$.secrets[{index}]", f"node-ci.yml declares no secret {name!r}"))
    push_branches = _string_list(document.get("pushBranches", ["main"]), "$.pushBranches", _BRANCH, 32, errors) or ()
    if "pushBranches" in document and not push_branches:
        errors.append(PlanError("empty_work", "$.pushBranches", "omit pushBranches or list at least one branch"))
    declared = {item["id"] for item in raw_levels if isinstance(item, dict) and isinstance(item.get("id"), str)}
    preceding: dict[str, Level] = {}
    for index, raw in enumerate(raw_levels):
        level = _parse_level(raw, f"$.levels[{index}]", preceding, declared, known_inputs, errors)
        if level is not None and level.id not in preceding:
            preceding[level.id] = level
    if not errors:
        _check_input_events(tuple(preceding.values()), errors)
        _check_coverage(tuple(preceding.values()), push_branches, errors)
        _check_reachable(tuple(preceding.values()), push_branches, errors)
    if errors:
        raise LevelsError(errors)
    return Plan(tuple(preceding.values()), secrets, push_branches, explicit=True)


def _event_filter(level: Level) -> str | None:
    if level.events is None:
        return None
    return " || ".join(f"github.event_name == '{event}'" for event in level.events)


def _target_filter(level: Level) -> str | None:
    if level.branches is None:
        return None
    return " || ".join(f"(github.base_ref || github.ref_name) == '{branch}'" for branch in level.branches)


def level_filter(level: Level) -> str | None:
    parts = [part for part in (_event_filter(level), _target_filter(level)) if part]
    if not parts:
        return None
    return " && ".join(f"({part})" for part in parts) if len(parts) > 1 else parts[0]


def _job_condition(level: Level) -> str | None:
    selected = level_filter(level)
    if not level.needs:
        return selected
    terms = ["!cancelled()"]
    for need in level.needs:
        result = f"needs.{need.level}.result"
        if need.condition == "success":
            terms.append(f"{result} == 'success'")
        else:
            terms.append(f"({result} == 'success' || {result} == 'failure')")
    if selected:
        terms.append(f"({selected})")
    return " && ".join(terms)


def _scalar(value: str | bool | int) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False)


def _render_level(level: Level, plan: Plan, sha: str) -> list[str]:
    lines = [f"  {level.id}:"]
    if level.needs:
        lines.append(f"    needs: [{', '.join(n.level for n in level.needs)}]")
    condition = _job_condition(level)
    if condition:
        lines.append(f"    if: ${{{{ {condition} }}}}")
    lines += [
        "    permissions:",
        "      contents: read",
        "      packages: read",
        "      statuses: read",
        f"    uses: Verjson/.github/.github/workflows/node-ci.yml@{sha}",
    ]
    if level.inputs:
        lines.append("    with:")
        lines += [f"      {key}: {_scalar(value)}" for key, value in level.inputs]
    if plan.secrets:
        lines.append("    secrets:")
        lines += [f"      {name}: ${{{{ secrets.{name} }}}}" for name in plan.secrets]
    return lines


def _render_aggregate(plan: Plan) -> list[str]:
    lines = [
        f"  {AGGREGATE_ID}:",
        "    if: ${{ always() }}",
        f"    needs: [{', '.join(level.id for level in plan.levels)}]",
        "    permissions: {}",
        f"    runs-on: {HOSTED}",
        "    timeout-minutes: 5",
        "    steps:",
        "      - name: Require every expected level to succeed",
        "        env:",
        f"          LEVEL_COUNT: {len(plan.levels)}",
    ]
    for index, level in enumerate(plan.levels):
        lines += [
            f"          LEVEL_{index}: {level.id}",
            f"          RESULT_{index}: ${{{{ needs.{level.id}.result }}}}",
            f"          EXPECTED_{index}: ${{{{ {level_filter(level) or 'true'} }}}}",
        ]
    lines.append("        run: |")
    lines += [f"          {line}" if line else "" for line in AGGREGATE_SCRIPT.splitlines()]
    return lines


def _triggers(plan: Plan) -> list[str]:
    events: set[str] = set()
    for level in plan.levels:
        events.update(DEFAULT_EVENTS if level.events is None else level.events)
    lines = ["on:"]
    for event in ALL_EVENTS:
        if event not in events:
            continue
        if event == "push":
            lines += ["  push:", f"    branches: [{', '.join(json.dumps(b) for b in plan.push_branches)}]"]
        else:
            lines.append(f"  {event}:")
    return lines


def render_caller(plan: Plan, sha: str, source: str) -> str:
    header = f"# Generated by scripts/gen-node-ci-caller.py{source} -- do not edit; its pin is the `uses:` lines."
    lines = [header, "name: CI", *_triggers(plan), "", "permissions: {}", ""]
    out = "\n".join(lines) + "\n" + CONCURRENCY + "\njobs:\n"
    blocks = [_render_level(level, plan, sha) for level in plan.levels]
    if plan.explicit:
        blocks.append(_render_aggregate(plan))
    return out + "\n\n".join("\n".join(block) for block in blocks) + "\n"
