#!/usr/bin/env python3
"""Pure script-plan parsing used by canonical Node CI workflows."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from typing import Any

MAX_PLAN_ENTRIES = 32
MAX_NESTED_MANIFESTS = 8
MAX_UNSET_ENV = 16

SCRIPT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]*\Z")
ENV_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
PATH_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
PROTECTED_ENV = frozenset(
    {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "NODE_AUTH_TOKEN",
        "NPM_TOKEN",
        "NPM_CONFIG_CACHE",
        "NPM_CONFIG_GLOBALCONFIG",
        "NPM_CONFIG_USERCONFIG",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AZURE_CREDENTIALS",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
    }
)


class ScriptPlanError(ValueError):
    """An invalid or over-budget script-plan document."""


@dataclass(frozen=True)
class ScriptPlanEntry:
    script: str
    unset_env: tuple[str, ...]
    requires_services: bool

    def as_json(
        self,
        manifest_path: str | None = None,
        manifest_index: int | None = None,
    ) -> dict[str, Any]:
        return {
            "manifestPath": manifest_path,
            "manifestIndex": manifest_index,
            "script": self.script,
            "unsetEnv": list(self.unset_env),
            "requiresServices": self.requires_services,
        }


@dataclass(frozen=True)
class NestedScriptPlan:
    index: int
    path: str
    entries: tuple[ScriptPlanEntry, ...]


def is_test_script(script: str) -> bool:
    return script == "test" or script.startswith("test:") or script.endswith(":test")


def _validate_utf8(source: str, label: str) -> None:
    try:
        source.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ScriptPlanError(f"{label} must be valid UTF-8") from exc


def _load_json(source: str, label: str) -> Any:
    if not isinstance(source, str):
        raise ScriptPlanError(f"{label} must be a JSON string")
    _validate_utf8(source, label)
    try:
        return json.loads(source)
    except json.JSONDecodeError as exc:
        raise ScriptPlanError(f"{label} must be valid JSON: {exc.msg}") from exc


def parse_plan_value(
    plan: Any,
    label: str = "secretless-ci-script-plan",
    *,
    allow_empty: bool = False,
    max_entries: int = MAX_PLAN_ENTRIES,
) -> tuple[ScriptPlanEntry, ...]:
    if not isinstance(plan, list) or (not allow_empty and not plan) or len(plan) > max_entries:
        minimum = 0 if allow_empty else 1
        raise ScriptPlanError(f"{label} accepts {minimum}-{max_entries} unique exact npm script entries")

    entries: list[ScriptPlanEntry] = []
    for entry in plan:
        if isinstance(entry, str):
            script = entry
            unset_env: Any = []
            requires_services: Any = None
            requires_services_specified = False
        elif (
            isinstance(entry, dict)
            and "script" in entry
            and set(entry).issubset({"script", "unsetEnv", "requiresServices"})
        ):
            script = entry["script"]
            unset_env = entry.get("unsetEnv", [])
            requires_services = entry.get("requiresServices")
            requires_services_specified = "requiresServices" in entry
        else:
            raise ScriptPlanError(
                f"{label} entries must be script names or script objects with supported fields"
            )

        if not isinstance(script, str) or SCRIPT_PATTERN.fullmatch(script) is None:
            raise ScriptPlanError(f"{label} contains an invalid script name")
        if not requires_services_specified:
            requires_services = is_test_script(script)
        elif type(requires_services) is not bool:
            raise ScriptPlanError(f"{label} requiresServices must be a JSON boolean")

        if not isinstance(unset_env, list) or len(unset_env) > MAX_UNSET_ENV:
            raise ScriptPlanError(
                f"{label} unsetEnv accepts up to {MAX_UNSET_ENV} unique non-credential environment names"
            )
        if any(
            not isinstance(name, str)
            or ENV_PATTERN.fullmatch(name) is None
            or name in PROTECTED_ENV
            for name in unset_env
        ):
            raise ScriptPlanError(
                f"{label} unsetEnv accepts up to {MAX_UNSET_ENV} unique non-credential environment names"
            )
        if len(unset_env) != len(set(unset_env)):
            raise ScriptPlanError(
                f"{label} unsetEnv accepts up to {MAX_UNSET_ENV} unique non-credential environment names"
            )

        entries.append(ScriptPlanEntry(script, tuple(unset_env), requires_services))

    scripts = [entry.script for entry in entries]
    if len(scripts) != len(set(scripts)):
        raise ScriptPlanError(f"{label} accepts unique script names")
    return tuple(entries)


def parse_script_plan(
    source: str,
    label: str = "secretless-ci-script-plan",
    *,
    trim_source: bool = True,
) -> tuple[ScriptPlanEntry, ...] | None:
    if not isinstance(source, str):
        raise ScriptPlanError(f"{label} must be a JSON string")
    _validate_utf8(source, label)
    if trim_source:
        source = source.strip()
    elif not source.strip():
        return None
    if not source:
        return None
    return parse_plan_value(_load_json(source, label), label)


def parse_nested_manifests(source: str) -> tuple[NestedScriptPlan, ...]:
    if not isinstance(source, str):
        raise ScriptPlanError("secretless-nested-manifests must be a JSON string")
    _validate_utf8(source, "secretless-nested-manifests")
    source = source.strip()
    if not source:
        return ()
    nested = _load_json(source, "secretless-nested-manifests")
    if not isinstance(nested, list) or not 1 <= len(nested) <= MAX_NESTED_MANIFESTS:
        raise ScriptPlanError(
            f"secretless-nested-manifests accepts 1-{MAX_NESTED_MANIFESTS} nested manifest objects"
        )

    plans: list[NestedScriptPlan] = []
    for index, manifest in enumerate(nested):
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"path", "approvedPackages", "scriptPlan"}
        ):
            raise ScriptPlanError(
                "each nested manifest requires exactly path, approvedPackages, and scriptPlan"
            )
        path = manifest["path"]
        if (
            not isinstance(path, str)
            or not path
            or any(PATH_SEGMENT_PATTERN.fullmatch(part) is None for part in path.split("/"))
        ):
            raise ScriptPlanError(
                "each nested manifest path must be a relative checkout directory"
            )
        entries = parse_plan_value(
            manifest["scriptPlan"],
            f"{path}/secretless-nested-manifests scriptPlan",
            allow_empty=True,
            max_entries=8,
        )
        plans.append(NestedScriptPlan(index, path, entries))
    return tuple(plans)


def requires_services_for_script(
    script_name: str,
    plan_source: str,
) -> bool:
    if not isinstance(script_name, str) or SCRIPT_PATTERN.fullmatch(script_name) is None:
        raise ScriptPlanError("compatibility request requires a valid script name")
    plan = parse_script_plan(plan_source, trim_source=False)
    if plan is None:
        return is_test_script(script_name)
    for entry in plan:
        if entry.script == script_name:
            return entry.requires_services
    return False


def encode_json_output(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    try:
        encoded.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ScriptPlanError("normalized script-plan output must be valid UTF-8") from exc
    return encoded


def write_normalized_entries(entries: list[dict[str, Any]], runner_temp: str) -> str:
    encoded = encode_json_output(entries)
    descriptor, path = tempfile.mkstemp(
        prefix="verjson-script-plan-",
        suffix=".json",
        dir=runner_temp,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(encoded)
    except BaseException:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        raise
    return path


def normalize_outputs(plan_source: str, nested_source: str) -> tuple[list[dict[str, Any]], bool]:
    root_plan = parse_script_plan(plan_source)
    entries = [entry.as_json() for entry in root_plan or ()]
    for nested in parse_nested_manifests(nested_source):
        entries.extend(entry.as_json(nested.path, nested.index) for entry in nested.entries)
    return entries, root_plan is None


def _compatibility_request_script(source: str) -> str:
    request = _load_json(source, "compatibility request")
    if not isinstance(request, dict):
        raise ScriptPlanError("compatibility request must be an object")
    script = request.get("script")
    if not isinstance(script, str) or SCRIPT_PATTERN.fullmatch(script) is None:
        raise ScriptPlanError("compatibility request requires a valid script name")
    return script


def main() -> int:
    mode = os.environ.get("SCRIPT_PLAN_MODE", "")
    plan_source = os.environ.get("SCRIPT_PLAN_SOURCE", "")
    nested_source = os.environ.get("NESTED_MANIFESTS_SOURCE", "")
    try:
        if mode == "normalize":
            entries, use_defaults = normalize_outputs(plan_source, nested_source)
            entries_path = write_normalized_entries(entries, os.environ["RUNNER_TEMP"])
            print(f"entries-file={entries_path}")
            print(f"default-plan={'true' if use_defaults else 'false'}")
            return 0
        if mode == "compatibility":
            request_source = os.environ.get("COMPATIBILITY_REQUEST_SOURCE", "")
            script = _compatibility_request_script(request_source)
            requires_services = requires_services_for_script(script, plan_source)
            print(f"requires-services={'true' if requires_services else 'false'}")
            return 0
        raise ScriptPlanError("mode must be normalize or compatibility")
    except ScriptPlanError as exc:
        print(f"script-plan: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
