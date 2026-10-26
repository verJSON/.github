"""Pure validation for normalized Node CI script-plan entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import script_plan

ScriptPlanError = script_plan.ScriptPlanError


@dataclass(frozen=True)
class NormalizedEntry:
    manifest_path: str | None
    manifest_index: int | None
    script: str
    unset_env: tuple[str, ...]
    requires_services: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "manifestPath": self.manifest_path,
            "manifestIndex": self.manifest_index,
            "script": self.script,
            "unsetEnv": list(self.unset_env),
            "requiresServices": self.requires_services,
        }


@dataclass(frozen=True)
class ValidatedNormalizedPlan:
    entries: tuple[NormalizedEntry, ...]
    nested_manifests: tuple[script_plan.NestedScriptPlan, ...]


def validate_normalized_entries(
    value: Any,
    nested_source: str,
) -> ValidatedNormalizedPlan:
    if not isinstance(value, list):
        raise script_plan.ScriptPlanError("normalized script-plan entries must be a JSON array")

    nested_manifests = script_plan.parse_nested_manifests(nested_source)
    grouped_entries: dict[int | None, list[dict[str, Any]]] = {None: []}
    normalized_metadata: list[tuple[str | None, int | None]] = []
    last_manifest_index = -1
    nested_entries_seen = False
    for entry in value:
        if not isinstance(entry, dict):
            raise script_plan.ScriptPlanError("normalized script-plan entries must be objects")

        manifest_path = entry.get("manifestPath")
        manifest_index = entry.get("manifestIndex")
        if manifest_index is None:
            if manifest_path is not None:
                raise script_plan.ScriptPlanError(
                    "root script-plan entries cannot declare a manifest path"
                )
            if nested_entries_seen:
                raise script_plan.ScriptPlanError(
                    "normalized root script-plan entries must precede nested entries"
                )
        else:
            if (
                type(manifest_index) is not int
                or manifest_index < 0
                or manifest_index >= len(nested_manifests)
                or manifest_path != nested_manifests[manifest_index].path
            ):
                raise script_plan.ScriptPlanError(
                    "normalized nested script-plan entry has an invalid manifest"
                )
            if manifest_index < last_manifest_index:
                raise script_plan.ScriptPlanError(
                    "normalized nested script-plan entries must preserve manifest order"
                )
            last_manifest_index = manifest_index
            nested_entries_seen = True

        script = entry.get("script")
        unset_env = entry.get("unsetEnv")
        requires_services = entry.get("requiresServices")
        if (
            not isinstance(script, str)
            or not isinstance(unset_env, list)
            or type(requires_services) is not bool
        ):
            raise script_plan.ScriptPlanError("normalized script-plan entry has invalid fields")

        parsed_entry = {
            "script": script,
            "unsetEnv": unset_env,
            "requiresServices": requires_services,
        }
        grouped_entries.setdefault(manifest_index, []).append(parsed_entry)
        normalized_metadata.append((manifest_path, manifest_index))

    root_entries = script_plan.parse_plan_value(
        grouped_entries[None],
        "normalized root script-plan entries",
        allow_empty=True,
    )
    nested_entry_groups = []
    for manifest in nested_manifests:
        entries = script_plan.parse_plan_value(
            grouped_entries.get(manifest.index, []),
            f"{manifest.path} normalized script-plan entries",
            allow_empty=True,
            max_entries=8,
        )
        if entries != manifest.entries:
            raise script_plan.ScriptPlanError(
                "normalized nested script-plan entries differ from manifest order"
            )
        nested_entry_groups.append(entries)

    normalized_entries = []
    root_iterator = iter(root_entries)
    nested_iterators = [iter(entries) for entries in nested_entry_groups]
    for manifest_path, manifest_index in normalized_metadata:
        if manifest_index is None:
            parsed_entry = next(root_iterator)
        else:
            parsed_entry = next(nested_iterators[manifest_index])
        normalized_entries.append(
            NormalizedEntry(
                manifest_path=manifest_path,
                manifest_index=manifest_index,
                script=parsed_entry.script,
                unset_env=parsed_entry.unset_env,
                requires_services=parsed_entry.requires_services,
            )
        )

    return ValidatedNormalizedPlan(tuple(normalized_entries), nested_manifests)
