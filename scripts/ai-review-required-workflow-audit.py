#!/usr/bin/env python3
import argparse
import base64
import copy
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
CONTRACT = Path(
    os.environ.get(
        "AI_REVIEW_RULESET_CONTRACT",
        ROOT / "config/ai-review-required-workflow-rollout.json",
    )
)
RULESET_FIELDS = ("name", "target", "enforcement", "bypass_actors", "conditions", "rules")
# The only events GitHub will start a ruleset-required workflow on. A selected
# workflow declaring none of them is inert, however valid its YAML is.
RULESET_ELIGIBLE_TRIGGERS = frozenset({"pull_request", "pull_request_target", "merge_group"})
# What the rulesets API returns beside the mutation payload: identity,
# provenance, and timestamps. Any other top-level key is a candidate policy
# field the contract does not pin, so it belongs in the review report.
RULESET_METADATA_FIELDS = frozenset({
    "id", "node_id", "source", "source_type", "created_at", "updated_at",
    "_links", "links", "current_user_can_bypass",
})


class AuditError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def selected_workflow(payload: dict) -> dict:
    workflow_rules = [rule for rule in payload["rules"] if rule.get("type") == "workflows"]
    require(len(workflow_rules) == 1, "ruleset image must contain exactly one workflows rule")
    parameters = workflow_rules[0].get("parameters", {})
    require(parameters.get("do_not_enforce_on_create") is True, "repository-creation bypass drifted")
    workflows = parameters.get("workflows")
    require(isinstance(workflows, list) and len(workflows) == 1, "workflows rule must select one workflow")
    selected = workflows[0]
    require(isinstance(selected.get("path"), str), "required workflow path is invalid")
    return selected


def workflow_path(payload: dict) -> str:
    return selected_workflow(payload)["path"]


def read_contract(path: Path = CONTRACT) -> dict:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AuditError(f"cannot read rollout contract: {error}") from None
    require(isinstance(contract, dict), "rollout contract must be an object")
    require(contract.get("schema_version") == 4, "unsupported rollout contract schema")
    organization = contract.get("organization")
    require(
        isinstance(organization, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", organization) is not None,
        "contract organization is invalid",
    )
    ruleset_id = contract.get("ruleset_id")
    require(
        isinstance(ruleset_id, int) and not isinstance(ruleset_id, bool) and ruleset_id > 0,
        "ruleset ID must be a positive integer",
    )
    for name in ("retired_path", "replacement_path"):
        value = contract.get(name)
        require(
            isinstance(value, str)
            and re.fullmatch(r"\.github/workflows/[A-Za-z0-9._-]+\.ya?ml", value) is not None,
            f"contract {name} is invalid",
        )
    require(contract["retired_path"] != contract["replacement_path"], "workflow paths must differ")

    for name in ("core_checks_property", "core_checks_value"):
        require(
            isinstance(contract.get(name), str) and contract[name],
            f"contract {name} is invalid",
        )
    arm_property = contract.get("arm_property")
    require(isinstance(arm_property, dict), "arm property contract is missing")
    require(
        set(arm_property) == {"name", "value", "definition"},
        "arm property contract is incomplete",
    )
    require(
        isinstance(arm_property["name"], str)
        and re.fullmatch(r"[a-z][a-z0-9-]{0,74}", arm_property["name"]) is not None,
        "arm property name is invalid",
    )
    require(
        arm_property["name"] != contract["core_checks_property"],
        "arm property must be independent of the deterministic-CI property",
    )
    require(
        isinstance(arm_property["value"], str) and arm_property["value"],
        "arm property value is invalid",
    )
    definition = arm_property["definition"]
    require(
        isinstance(definition, dict)
        and set(definition)
        == {
            "value_type",
            "required",
            "default_value",
            "description",
            "allowed_values",
            "values_editable_by",
            "require_explicit_values",
        },
        "arm property definition is incomplete",
    )
    require(
        definition["value_type"] == "single_select"
        and definition["required"] is False
        and definition["default_value"] is None
        and isinstance(definition["description"], str)
        and definition["description"]
        and isinstance(definition["allowed_values"], list)
        and definition["allowed_values"]
        and all(isinstance(value, str) and value for value in definition["allowed_values"])
        and len(definition["allowed_values"]) == len(set(definition["allowed_values"]))
        and arm_property["value"] in definition["allowed_values"]
        and definition["values_editable_by"] == "org_actors"
        and definition["require_explicit_values"] is False,
        "arm property definition is invalid",
    )
    migration = contract.get("arm_property_migration")
    require(
        isinstance(migration, dict)
        and set(migration) == {"repositories"},
        "arm property migration is incomplete",
    )
    repositories = migration["repositories"]
    require(
        isinstance(repositories, list)
        and repositories
        and repositories == sorted(set(repositories))
        and all(
            isinstance(repository, str)
            # GitHub's repository-name charset. Case-folded identity comparison
            # is only sound over ASCII: U+212A KELVIN SIGN folds to `k`.
            and re.fullmatch(rf"{re.escape(organization)}/[A-Za-z0-9._-]+", repository) is not None
            for repository in repositories
        )
        and len({repository_identity(repository) for repository in repositories}) == len(repositories),
        "arm property migration repositories are invalid",
    )

    images = {}
    for name in (
        "preimage",
        "postimage",
        "rollback_payload",
        "legacy_arm_ruleset",
        "arm_ruleset",
    ):
        image = contract.get(name)
        require(isinstance(image, dict), f"contract {name} is missing")
        require(set(image) == set(RULESET_FIELDS), f"contract {name} is not a complete mutation payload")
        images[name] = image
    require(images["rollback_payload"] == images["preimage"], "rollback payload must equal the verified preimage")
    require(workflow_path(images["preimage"]) == contract["retired_path"], "preimage does not select the retired workflow")
    require(
        not [rule for rule in images["postimage"]["rules"] if rule.get("type") == "workflows"],
        "postimage must not retain a workflows rule",
    )
    # The split moves one rule and changes nothing else about branch protection.
    # Deriving the postimage here is what makes that reviewable rather than
    # asserted: `deletion`, `non_fast_forward`, `required_linear_history`, and
    # `pull_request` stay at `~ALL` for every governed repository.
    derived = copy.deepcopy(images["preimage"])
    derived["rules"] = [rule for rule in derived["rules"] if rule.get("type") != "workflows"]
    require(derived == images["postimage"], "postimage changes fields beyond removing the workflows rule")

    arm_name = contract.get("arm_ruleset_name")
    require(isinstance(arm_name, str) and arm_name, "arm ruleset name is invalid")
    require(images["arm_ruleset"]["name"] == arm_name, "arm ruleset payload name disagrees with the contract")
    require(
        images["legacy_arm_ruleset"]["name"] == arm_name,
        "legacy arm ruleset payload name disagrees with the contract",
    )
    require(images["arm_ruleset"]["enforcement"] == "active", "arm ruleset must be active")
    require(images["legacy_arm_ruleset"]["enforcement"] == "active", "legacy arm ruleset must be active")
    require(workflow_path(images["arm_ruleset"]) == contract["replacement_path"], "arm ruleset does not select the replacement workflow")
    require(
        workflow_path(images["legacy_arm_ruleset"]) == contract["replacement_path"],
        "legacy arm ruleset does not select the replacement workflow",
    )
    # The moved rule must be the SAME rule, not a lookalike: same parameters,
    # same creation bypass, differing only in the selected path.
    moved = copy.deepcopy(next(rule for rule in images["preimage"]["rules"] if rule.get("type") == "workflows"))
    moved["parameters"]["workflows"][0]["path"] = contract["replacement_path"]
    require(images["arm_ruleset"]["rules"] == [moved], "arm ruleset rule is not the relocated workflows rule")
    require(
        images["legacy_arm_ruleset"]["rules"] == [moved],
        "legacy arm ruleset rule is not the relocated workflows rule",
    )
    require(
        images["arm_ruleset"]["bypass_actors"] == images["preimage"]["bypass_actors"],
        "arm ruleset bypass actors diverge from the protection ruleset",
    )
    require(
        images["legacy_arm_ruleset"]["bypass_actors"] == images["arm_ruleset"]["bypass_actors"],
        "legacy arm ruleset bypass actors diverge from the target image",
    )
    # Authorization-arm enrollment is independent from deterministic-CI
    # enrollment. The audit below still requires both on every armed repository.
    conditions = images["arm_ruleset"]["conditions"]
    require(
        conditions.get("ref_name") == images["preimage"]["conditions"]["ref_name"],
        "arm ruleset targets different refs than the protection ruleset",
    )
    require(
        images["legacy_arm_ruleset"]["conditions"].get("ref_name") == conditions["ref_name"],
        "legacy arm ruleset targets different refs than the target image",
    )
    require(
        "repository_name" not in conditions,
        "arm ruleset must be scoped by repository property, not by name",
    )
    require(
        conditions.get("repository_property")
        == {
            "exclude": [],
            "include": [{
                "name": contract["arm_property"]["name"],
                "property_values": [contract["arm_property"]["value"]],
                "source": "custom",
            }],
        },
        "arm ruleset is not scoped to the dedicated authorization property",
    )
    require(
        images["legacy_arm_ruleset"]["conditions"].get("repository_property")
        == {
            "exclude": [],
            "include": [{
                "name": contract["core_checks_property"],
                "property_values": [contract["core_checks_value"]],
                "source": "custom",
            }],
        },
        "legacy arm ruleset is not scoped to the deterministic-CI property",
    )

    for name in ("forbidden_required_status_contexts", "deterministic_required_status_contexts"):
        values = contract.get(name)
        require(
            isinstance(values, list)
            and values
            and all(isinstance(value, str) and value for value in values)
            and len(values) == len(set(values)),
            f"contract {name} must contain unique non-empty contexts",
        )
    require(
        not set(contract["forbidden_required_status_contexts"])
        & set(contract["deterministic_required_status_contexts"]),
        "forbidden and deterministic contexts overlap",
    )
    deterministic_rulesets = contract.get("deterministic_rulesets")
    require(isinstance(deterministic_rulesets, list) and deterministic_rulesets, "deterministic rulesets are missing")
    ids = []
    stacks = []
    allowed_contexts = set(contract["deterministic_required_status_contexts"])
    for index, declaration in enumerate(deterministic_rulesets):
        require(isinstance(declaration, dict), f"deterministic ruleset {index} is invalid")
        ruleset_id = declaration.get("id")
        stack = declaration.get("stack")
        image = declaration.get("image")
        require(
            isinstance(ruleset_id, int) and not isinstance(ruleset_id, bool) and ruleset_id > 0,
            f"deterministic ruleset {index} ID is invalid",
        )
        require(isinstance(stack, str) and stack, f"deterministic ruleset {index} stack is invalid")
        require(
            isinstance(image, dict) and set(image) == set(RULESET_FIELDS),
            f"deterministic ruleset {index} image is incomplete",
        )
        contexts = {
            check.get("context")
            for rule in image["rules"]
            if rule.get("type") == "required_status_checks"
            for check in rule.get("parameters", {}).get("required_status_checks", [])
        }
        require(contexts and contexts <= allowed_contexts, f"deterministic ruleset {index} contexts are invalid")
        ids.append(ruleset_id)
        stacks.append(stack)
    require(len(ids) == len(set(ids)), "deterministic ruleset IDs must be unique")
    require(len(stacks) == len(set(stacks)), "deterministic stack declarations must be unique")

    authorization = contract.get("authorization")
    require(isinstance(authorization, dict), "authorization contract is missing")
    secret = authorization.get("private_key_secret")
    variables = authorization.get("variables")
    require(secret == "AI_REVIEW_APP_PRIVATE_KEY", "private-key secret is invalid")
    require(
        isinstance(variables, list)
        and variables
        and all(
            isinstance(name, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", name) is not None
            for name in variables
        )
        and len(variables) == len(set(variables)),
        "authorization variables are invalid",
    )
    require(
        variables == ["AI_REVIEW_APP_ID", "AI_REVIEW_APP_SLUG", "AI_REVIEW_CLIENT_ID"],
        "authorization variable contract drifted",
    )
    permissions = authorization.get("app_permissions")
    require(
        permissions
        == {
            "checks": "write",
            "contents": "read",
            "metadata": "read",
            "pull_requests": "write",
        },
        "authorization App permission contract drifted",
    )
    require(authorization.get("app_events") == [], "authorization App event contract drifted")

    conformance = contract.get("adopter_conformance")
    require(isinstance(conformance, dict), "adopter conformance contract is missing")
    callers = conformance.get("caller_workflows")
    require(
        isinstance(callers, dict) and set(callers) == {"admission", "completeness"},
        "adopter caller workflows must declare an admission and a completeness set",
    )
    declared = []
    for kind in ("admission", "completeness"):
        values = callers[kind]
        require(
            isinstance(values, list)
            and values
            and all(
                isinstance(value, str)
                and re.fullmatch(r"\.github/workflows/[A-Za-z0-9._-]+\.ya?ml", value) is not None
                for value in values
            )
            and len(values) == len(set(values)),
            f"adopter {kind} caller workflows are invalid",
        )
        declared.extend(values)
    require(len(declared) == len(set(declared)), "an adopter caller is declared in both sets")
    # Admission is not a severity label chosen here: it is exactly the file
    # `gate-rearm.yml` reads out of the target repository and treats as fatal,
    # which is the same path this contract already pins as `retired_path`.
    require(
        callers["admission"] == [contract["retired_path"]],
        "adopter admission set must be exactly the caller the arm hard-requires",
    )
    require(
        conformance.get("environment") == "ai-review-app",
        "adopter review environment contract drifted",
    )
    require(
        conformance.get("environment_deployment_branch_policy")
        == {"protected_branches": False, "custom_branch_policies": True},
        "adopter review environment branch-policy contract drifted",
    )
    return contract


def gh_pages(path: str, allow_missing: bool = False) -> list:
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        # A per-adopter probe asks a question whose "no" is a finding, not an
        # infrastructure failure. Only a 404 means that; any other status is
        # still an unreadable API and must stop the audit, because reporting
        # "absent" for an expired token would turn the whole fleet green-to-red
        # on a credential problem and vice versa.
        if allow_missing and "(HTTP 404)" in result.stderr:
            return []
        raise AuditError(f"GitHub API read failed for {path}")
    try:
        pages = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AuditError(f"GitHub API returned invalid JSON for {path}: {error}") from None
    require(isinstance(pages, list) and pages, f"GitHub API returned no data for {path}")
    return pages


def single_object(read, path: str) -> dict:
    pages = read(path)
    require(len(pages) == 1 and isinstance(pages[0], dict), f"unexpected response shape for {path}")
    return pages[0]


def paginated_items(read, path: str, field: str | None = None, allow_missing: bool = False) -> list[dict]:
    items = []
    for index, page in enumerate(read(path, allow_missing=True) if allow_missing else read(path)):
        values = page.get(field) if field and isinstance(page, dict) else page
        require(isinstance(values, list), f"unexpected page {index} shape for {path}")
        require(all(isinstance(value, dict) for value in values), f"invalid item in {path}")
        items.extend(values)
    return items


def normalize_ruleset(live: dict) -> dict:
    require(all(field in live for field in RULESET_FIELDS), "live ruleset omits mutation fields")
    return {field: live[field] for field in RULESET_FIELDS}


def image_mismatches(live, asserted, path: str = "") -> list[str]:
    """Report every field the reviewed image asserts that the live object fails.

    Keys GitHub adds to its own schema are tolerated; a key the contract asserts
    must exist and carry exactly the asserted value. That asymmetry is the whole
    control. Whole-object equality rotted the moment GitHub shipped
    `require_extra_approval_for_unattributed_changes` (#1404): the audit died at
    its first precondition with a message indistinguishable from real drift.
    Tolerating an unknown key must never become tolerating an unexpected value,
    so list members are matched position-wise and a differing length is drift —
    an added bypass actor or required status check is still a finding.
    """
    where = path or "<root>"
    if isinstance(asserted, dict):
        if not isinstance(live, dict):
            return [f"{where}: expected an object"]
        mismatches = []
        for key, value in asserted.items():
            field = f"{path}.{key}" if path else key
            if key not in live:
                mismatches.append(f"{field}: absent")
            else:
                mismatches.extend(image_mismatches(live[key], value, field))
        return mismatches
    if isinstance(asserted, list):
        if not isinstance(live, list) or len(live) != len(asserted):
            return [f"{where}: expected {len(asserted)} entries"]
        mismatches = []
        for index, value in enumerate(asserted):
            mismatches.extend(image_mismatches(live[index], value, f"{path}[{index}]"))
        return mismatches
    if isinstance(asserted, bool) != isinstance(live, bool) or live != asserted:
        return [f"{where}: {live!r} is not {asserted!r}"]
    return []


def unpinned_keys(live, asserted, path: str = "") -> list[str]:
    """Name every live key the reviewed image does not assert.

    ADR 0188 tolerates keys GitHub adds, so the audit survives a schema that
    grows, and accepts as residual that such a key is invisible by construction
    — including one that weakens protection. Its stated mitigation is a periodic
    review that pins newly security-relevant fields, which is a review task, not
    a property the code can assert about itself. What the code can do is hand
    that review its candidate set instead of asking a human to re-read a vendor
    schema by eye. So an unpinned key is reported and never fails the audit:
    deciding whether it matters stays the reviewer's judgment (#1410).
    """
    if isinstance(asserted, dict) and isinstance(live, dict):
        keys = []
        for key, value in live.items():
            field = f"{path}.{key}" if path else key
            if key in asserted:
                keys.extend(unpinned_keys(value, asserted[key], field))
            else:
                keys.append(field)
        return keys
    if isinstance(asserted, list) and isinstance(live, list):
        keys = []
        for index, (value, pinned) in enumerate(zip(live, asserted)):
            keys.extend(unpinned_keys(value, pinned, f"{path}[{index}]"))
        return keys
    return []


def matches_image(live, image) -> bool:
    return not image_mismatches(live, image)


def concise_mismatches(mismatches: list[str]) -> str:
    sample = mismatches[:4]
    suffix = f" (plus {len(mismatches) - len(sample)} more)" if len(mismatches) > len(sample) else ""
    return f"{'; '.join(sample)}{suffix}"


def concise_findings(name: str, findings: list[str]) -> str:
    sample = findings[:8]
    suffix = f" (plus {len(findings) - len(sample)} more)" if len(findings) > len(sample) else ""
    return f"{name}: missing={len(findings)} sample={sample}{suffix}"


def concise_missing(name: str, missing: list[str]) -> None:
    require(not missing, concise_findings(name, missing))


def selected_repositories(read, organization: str, kind: str, name: str) -> set[str]:
    path = f"orgs/{organization}/actions/{kind}/{name}/repositories"
    repositories = paginated_items(read, path, "repositories")
    names = [repository.get("full_name") for repository in repositories]
    require(all(isinstance(value, str) and value for value in names), f"invalid repository grant for {name}")
    require(len(names) == len(set(names)), f"duplicate repository grant for {name}")
    return set(names)


def require_scope(read, organization: str, kind: str, name: str, governed: dict[str, bool]) -> dict:
    item = single_object(read, f"orgs/{organization}/actions/{kind}/{name}")
    visibility = item.get("visibility")
    if visibility == "all":
        return item
    if visibility == "private":
        missing = sorted(repository for repository, private in governed.items() if not private)
    elif visibility == "selected":
        missing = sorted(set(governed) - selected_repositories(read, organization, kind, name))
    else:
        raise AuditError(f"{name} has unsupported visibility {visibility!r}")
    concise_missing(f"{name} cannot reach governed repositories", missing)
    return item


def verify_ruleset_state(contract: dict, read, expected: str | None = None) -> tuple[str, dict]:
    path = f"orgs/{contract['organization']}/rulesets/{contract['ruleset_id']}"
    live = normalize_ruleset(single_object(read, path))
    if matches_image(live, contract["preimage"]):
        state = "ready"
    elif matches_image(live, contract["postimage"]):
        state = "split"
    else:
        # Name the nearest image and the fields that disagree. "Differs from both"
        # alone sent a reader off to diff two forty-line objects by eye, which is
        # how a key GitHub added looked exactly like a real regression (#1404).
        nearest, mismatches = min(
            (
                (name, image_mismatches(live, contract[name]))
                for name in ("preimage", "postimage")
            ),
            key=lambda candidate: len(candidate[1]),
        )
        raise AuditError(
            "main-protection matches neither reviewed image on the fields the contract "
            "asserts: "
            f"nearest {nearest}: {concise_mismatches(mismatches)}"
        )
    require(expected is None or state == expected, f"ruleset state is {state}, expected {expected}")
    return state, live


def verify_ruleset_exclusivity(
    contract: dict,
    read,
    state: str,
) -> tuple[dict[int, dict], str]:
    organization = contract["organization"]
    recognized = []
    conflicts = []
    candidates = {}
    for summary in paginated_items(read, f"orgs/{organization}/rulesets"):
        candidate = single_object(read, f"orgs/{organization}/rulesets/{summary.get('id')}")
        candidate_id = candidate.get("id")
        require(isinstance(candidate_id, int) and candidate_id not in candidates, "organization ruleset identity is invalid")
        candidates[candidate_id] = candidate
        for rule in candidate.get("rules", []):
            parameters = rule.get("parameters", {})
            if rule.get("type") == "workflows":
                for workflow in parameters.get("workflows", []):
                    if workflow.get("path") in {contract["retired_path"], contract["replacement_path"]}:
                        recognized.append((candidate.get("id"), workflow.get("path")))
            if rule.get("type") == "required_status_checks":
                for check in parameters.get("required_status_checks", []):
                    if check.get("context") in contract["forbidden_required_status_contexts"]:
                        conflicts.append((candidate.get("id"), check.get("context")))
    named = [
        candidate_id
        for candidate_id, candidate in candidates.items()
        if candidate.get("name") == contract["arm_ruleset_name"]
    ]
    if state == "ready":
        # Exactly one selector, on the protection ruleset, still on the retired
        # path — and the replacement ruleset must not exist yet.
        require(not named, f"arm ruleset already exists before the split: {named}")
        require(
            recognized == [(contract["ruleset_id"], contract["retired_path"])],
            f"retired and replacement workflow identities are not exclusive: {recognized}",
        )
        selector = "legacy"
    else:
        require(len(named) == 1, f"split state must have exactly one arm ruleset, found {named}")
        require(
            recognized == [(named[0], contract["replacement_path"])],
            f"retired and replacement workflow identities are not exclusive: {recognized}",
        )
        live_arm = normalize_ruleset(candidates[named[0]])
        target_mismatches = image_mismatches(live_arm, contract["arm_ruleset"])
        legacy_mismatches = image_mismatches(live_arm, contract["legacy_arm_ruleset"])
        if not target_mismatches:
            selector = "target"
        elif not legacy_mismatches:
            selector = "legacy"
        else:
            nearest, mismatches = min(
                (("target", target_mismatches), ("legacy", legacy_mismatches)),
                key=lambda candidate: len(candidate[1]),
            )
            raise AuditError(
                "arm ruleset drifted from both reviewed selector images: "
                f"nearest {nearest}: {concise_mismatches(mismatches)}"
            )
    require(not conflicts, f"retired or App authorization status is also required: {conflicts}")
    return candidates, selector


def verify_arm_property_schema(contract: dict, read, required: bool) -> bool:
    property_schema = paginated_items(
        read,
        f"orgs/{contract['organization']}/properties/schema",
    )
    require(
        all(isinstance(item, dict) for item in property_schema),
        "organization property schema inventory is invalid",
    )
    arm_property = contract["arm_property"]
    schema_matches = [
        item for item in property_schema
        if item.get("property_name") == arm_property["name"]
    ]
    require(len(schema_matches) <= 1, "dedicated authorization property schema is ambiguous")
    if required:
        require(schema_matches, "dedicated authorization property schema is missing")
    if not schema_matches:
        return False
    live_definition = {
        key: schema_matches[0].get(key)
        for key in arm_property["definition"]
    }
    require(
        schema_matches[0].get("source_type") == "organization"
        and live_definition == arm_property["definition"],
        "dedicated authorization property schema drifted",
    )
    return True


def read_repository_properties(contract: dict, read) -> dict[str, dict[str, object]]:
    property_rows = paginated_items(
        read,
        f"orgs/{contract['organization']}/properties/values?per_page=100",
    )
    properties_by_repository = {}
    identities = set()
    for row in property_rows:
        require(isinstance(row, dict), "organization property inventory is invalid")
        full_name = row.get("repository_full_name")
        values = row.get("properties")
        require(
            isinstance(full_name, str)
            and repository_identity(full_name) not in identities
            and isinstance(values, list),
            "organization property inventory is invalid",
        )
        identities.add(repository_identity(full_name))
        parsed = {}
        for item in values:
            require(
                isinstance(item, dict)
                and isinstance(item.get("property_name"), str)
                and item["property_name"]
                and item["property_name"] not in parsed,
                f"organization property values are invalid for {full_name}",
            )
            parsed[item["property_name"]] = item.get("value")
        properties_by_repository[full_name] = parsed
    return properties_by_repository


def target_arm_cohort(
    contract: dict,
    properties_by_repository: dict[str, dict[str, object]],
) -> list[str]:
    arm_property = contract["arm_property"]
    return sorted(
        full_name
        for full_name, values in properties_by_repository.items()
        if values.get(arm_property["name"]) == arm_property["value"]
    )


def repository_identity(full_name: str) -> str:
    # GitHub owner and repository names are case-insensitive; the API reports the
    # current display case, which an organization rename changes (#1775).
    return full_name.casefold()


def require_reviewed_cohort(contract: dict, cohort: list[str], name: str) -> None:
    reviewed = contract["arm_property_migration"]["repositories"]
    reviewed_identities = {repository_identity(repository) for repository in reviewed}
    cohort_identities = {repository_identity(repository) for repository in cohort}
    require(len(cohort_identities) == len(cohort), f"{name} lists a repository identity twice")
    unreviewed = sorted(
        repository for repository in cohort
        if repository_identity(repository) not in reviewed_identities
    )
    absent = sorted(
        repository for repository in reviewed
        if repository_identity(repository) not in cohort_identities
    )
    require(
        not unreviewed and not absent,
        f"{name} differs from the reviewed migration set: unreviewed={unreviewed} absent={absent}",
    )


def verify_target_arm_cohort(
    contract: dict,
    properties_by_repository: dict[str, dict[str, object]],
) -> list[str]:
    cohort = target_arm_cohort(contract, properties_by_repository)
    require_reviewed_cohort(contract, cohort, "authorization property cohort")
    return cohort


def verify_deterministic_ci(
    contract: dict,
    read,
    repositories: list[dict],
    candidates: dict[int, dict],
    selector: str,
) -> list[str]:
    declarations = {}
    for declaration in contract["deterministic_rulesets"]:
        ruleset_id = declaration["id"]
        require(ruleset_id in candidates, f"deterministic ruleset {ruleset_id} is absent")
        mismatches = image_mismatches(normalize_ruleset(candidates[ruleset_id]), declaration["image"])
        require(
            not mismatches,
            f"deterministic ruleset {ruleset_id} drifted from the fields its reviewed image asserts: "
            f"{concise_mismatches(mismatches)}",
        )
        declarations[declaration["stack"]] = declaration
    arm_property = contract["arm_property"]
    verify_arm_property_schema(contract, read, required=selector == "target")
    properties_by_repository = read_repository_properties(contract, read)
    # The arm selector and the deterministic-CI selector have distinct meanings.
    # Every armed repository must still opt into canonical deterministic CI, but
    # a core-check adopter is not armed merely because it adopted that contract.
    covered = []
    missing = []
    for repository in repositories:
        full_name = repository["full_name"]
        values = properties_by_repository.get(full_name, {})
        selector_name = (
            arm_property["name"] if selector == "target" else contract["core_checks_property"]
        )
        selector_value = (
            arm_property["value"] if selector == "target" else contract["core_checks_value"]
        )
        if values.get(selector_name) != selector_value:
            continue
        covered.append(full_name)
        if (
            values.get(contract["core_checks_property"]) != contract["core_checks_value"]
            or values.get("verjson-stack") not in declarations
        ):
            missing.append(full_name)
    require(covered, "no repository carries the active arm selector; the arm would govern nothing")
    covered = sorted(covered)
    if selector == "target":
        verify_target_arm_cohort(contract, properties_by_repository)
    else:
        require_reviewed_cohort(contract, covered, "legacy arm cohort")
    concise_missing("armed default branches without canonical deterministic required CI", sorted(missing))
    return covered


def verify_no_repository_shadowing(contract: dict, read, repositories: list[dict]) -> None:
    authorization = contract["authorization"]
    protected_secret = authorization["private_key_secret"]
    protected_variables = set(authorization["variables"])
    shadows = []
    for repository in repositories:
        full_name = repository["full_name"]
        secrets = paginated_items(read, f"repos/{full_name}/actions/secrets?per_page=100", "secrets")
        variables = paginated_items(read, f"repos/{full_name}/actions/variables?per_page=100", "variables")
        if protected_secret in {item.get("name") for item in secrets}:
            shadows.append(f"{full_name}:secret:{protected_secret}")
        for name in sorted(protected_variables & {item.get("name") for item in variables}):
            shadows.append(f"{full_name}:variable:{name}")
    concise_missing("repository-level authorization credential shadowing", shadows)


def verify_authorization(contract: dict, read, governed: dict[str, bool]) -> None:
    organization = contract["organization"]
    authorization = contract["authorization"]
    require_scope(read, organization, "secrets", authorization["private_key_secret"], governed)
    variables = {
        name: require_scope(read, organization, "variables", name, governed)
        for name in authorization["variables"]
    }
    app_id = variables["AI_REVIEW_APP_ID"].get("value")
    app_slug = variables["AI_REVIEW_APP_SLUG"].get("value")
    client_id = variables["AI_REVIEW_CLIENT_ID"].get("value")
    require(isinstance(app_id, str) and app_id.isdigit() and int(app_id) > 0, "AI_REVIEW_APP_ID is invalid")
    require(isinstance(app_slug, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]*", app_slug), "AI_REVIEW_APP_SLUG is invalid")
    require(isinstance(client_id, str) and re.fullmatch(r"Iv[A-Za-z0-9.]{8,126}", client_id), "AI_REVIEW_CLIENT_ID is invalid")
    installations = paginated_items(read, f"orgs/{organization}/installations", "installations")
    matching = [
        installation
        for installation in installations
        if installation.get("app_id") == int(app_id) and installation.get("app_slug") == app_slug
    ]
    require(len(matching) == 1, "dedicated authorization App installation is missing or ambiguous")
    installation = matching[0]
    require(installation.get("suspended_at") is None, "authorization App installation is suspended")
    require(installation.get("repository_selection") == "all", "authorization App installation is not ~ALL")
    require(installation.get("permissions") == authorization["app_permissions"], "authorization App permission map drifted")
    require(installation.get("events") == authorization["app_events"], "authorization App event subscriptions drifted")


def verify_adopter_conformance(contract: dict, read, repositories: list[dict], covered: list[str]) -> None:
    """Report armed adopters that cannot satisfy the arm they are required to pass.

    Enrollment is granted by a repository property and admission requires a
    repository-local caller the property says nothing about, so the two can
    diverge silently until an adopter's next pull request discovers it (#1401).
    The whole generated family is asserted, not any one member: `verjson-agents`
    carries the merge lane without the review caller `gate-rearm.yml`
    hard-requires, which satisfies a presence check while the arm still cannot
    run at all (ADR 0187). The family is reported in two classes because its
    members fail differently. Absence of the admission caller is what makes
    every pull request in a repository fail; absence of the rest degrades the
    lifecycle. Measured on 2026-09-17 that is 4 adopters against 19, so one
    bucket would deliver the finding #1401 needs as a minority of a backlog.

    The adopter's workflow listing is adopter-controlled text. It is used only to
    test membership against the paths this contract declares — never parsed,
    interpolated, or executed.
    """
    conformance = contract["adopter_conformance"]
    default_branches = {
        repository["full_name"]: repository.get("default_branch") for repository in repositories
    }
    environment = conformance["environment"]
    blocked = []
    findings = []
    environment_findings = []
    for full_name in covered:
        branch = default_branches.get(full_name)
        require(isinstance(branch, str) and branch, f"{full_name} reports no default branch")
        # Git permits `#` and `&` in a ref name. Raw in a query string the first
        # truncates it and the second starts another parameter, so an unencoded
        # probe reads a branch nobody asked about and reports what it finds
        # there as this adopter's state.
        reference = urllib.parse.quote(branch, safe="/")
        entries = paginated_items(
            read,
            f"repos/{full_name}/contents/.github/workflows?ref={reference}",
            allow_missing=True,
        )
        present = {entry.get("path") for entry in entries if entry.get("type") == "file"}
        findings.extend(
            f"{full_name}:{caller}"
            for caller in conformance["caller_workflows"]["completeness"]
            if caller not in present
        )
        for caller in conformance["caller_workflows"]["admission"]:
            if caller not in present:
                blocked.append(f"{full_name}:{caller}")
                continue
            # Present is not dispatchable. `gate-rearm.yml` dispatches the review
            # lane into the adopter, and GitHub refuses to start a workflow
            # disabled manually or for inactivity — the arm then fails for the
            # same reason as an absent caller. The state is read from the Actions
            # API, so no adopter-controlled workflow text becomes an input here;
            # the file name comes from this contract.
            pages = read(
                f"repos/{full_name}/actions/workflows/{caller.rsplit('/', 1)[-1]}",
                allow_missing=True,
            )
            state = pages[0].get("state") if pages and isinstance(pages[0], dict) else "unregistered"
            if state != "active":
                blocked.append(f"{full_name}:{caller}:{state}")
        label = f"{full_name}:{environment}"
        pages = read(f"repos/{full_name}/environments/{environment}", allow_missing=True)
        if not pages:
            environment_findings.append(f"{label}:absent")
            continue
        require(
            len(pages) == 1 and isinstance(pages[0], dict),
            f"unexpected environment response shape for {label}",
        )
        live = pages[0]
        mismatches = image_mismatches(
            live.get("deployment_branch_policy"),
            conformance["environment_deployment_branch_policy"],
            "deployment_branch_policy",
        )
        if mismatches:
            environment_findings.extend(f"{label}:{mismatch}" for mismatch in mismatches)
            continue
        # `custom_branch_policies: true` says the policy is custom, not what it
        # admits. The App key is confined to where the review lane actually runs
        # only if the policy names this adopter's own default branch — which is
        # not always `main`, and is not checkable by mirroring this repository's
        # policy field by field.
        policies = paginated_items(
            read,
            f"repos/{full_name}/environments/{environment}/deployment-branch-policies",
            "branch_policies",
            allow_missing=True,
        )
        # A policy whose `name` is absent would make this set mix `None` with
        # strings and `sorted` would raise `TypeError` out of a hub-privileged
        # control, so an unusable policy is reported as one instead.
        admitted = sorted({str(policy.get("name")) for policy in policies})
        if admitted != [branch]:
            environment_findings.append(
                f"{label}:branch_policies {admitted} do not admit only {branch!r}"
            )
    # Both classes are reported together. The audit runs daily, so surfacing one
    # class at a time would make the fleet take as many scheduled days to become
    # visible as there are classes — the same "the control could not tell you"
    # shape this audit exists to remove.
    reported = [
        concise_findings(name, items)
        for name, items in (
            (
                "armed adopters that cannot satisfy the arm: the admission caller is absent or undispatchable",
                sorted(blocked),
            ),
            ("armed adopters with an incomplete generated adopter caller set", sorted(findings)),
            ("armed adopters without a conforming review environment", sorted(environment_findings)),
        )
        if items
    ]
    require(not reported, " | ".join(reported))


def read_workflow(read, selected: dict, label: str) -> dict:
    # The ruleset names this ref; a ref name is not a constant, and raw in a query string
    # `#` truncates it while `&` starts another parameter. "/" stays literal: a query value
    # is where the API wants a nested branch name spelled out.
    reference = urllib.parse.quote(selected["ref"], safe="/")
    source = single_object(
        read,
        f"repositories/{selected['repository_id']}/contents/{selected['path']}?ref={reference}",
    )
    try:
        workflow = yaml.load(base64.b64decode(source["content"]), Loader=yaml.BaseLoader)
    except (KeyError, ValueError, yaml.YAMLError) as error:
        raise AuditError(f"{label} workflow is unreadable: {error}") from None
    require(isinstance(workflow, dict), f"{label} workflow is not a YAML object")
    return workflow


def verify_selected_workflow_is_schedulable(carrier: dict, read) -> None:
    """Fail while the ruleset selects a workflow GitHub can never schedule.

    A ruleset workflow runs only on `pull_request`, `pull_request_target`, or
    `merge_group`. Selecting a reusable-only workflow does not disable the rule
    — it renders on every governed pull request as "Workflow configuration
    invalid" and produces no run, no check, and no receipt (#728). Nothing
    verified this, so removing the `pull_request` trigger from the selected
    workflow took the organization gate down silently for four days.
    """
    selected = selected_workflow(carrier)
    triggers = read_workflow(read, selected, "selected").get("on")
    require(isinstance(triggers, dict) and triggers, "selected workflow declares no event triggers")
    require(
        set(triggers) & RULESET_ELIGIBLE_TRIGGERS,
        f"selected required workflow {selected['path']}@{selected['ref']} declares only "
        f"{', '.join(sorted(triggers))}; a ruleset workflow runs only on "
        f"{', '.join(sorted(RULESET_ELIGIBLE_TRIGGERS))}, so no governed repository can "
        "produce a required-workflow run",
    )


def verify_replacement_workflow(contract: dict, read) -> None:
    selected = selected_workflow(contract["arm_ruleset"])
    workflow = read_workflow(read, selected, "replacement")
    triggers = workflow.get("on")
    require(isinstance(triggers, dict) and "pull_request_target" in triggers, "replacement lacks pull_request_target")
    arm = workflow.get("jobs", {}).get("arm", {})
    require(arm.get("continue-on-error") == "true", "replacement can veto ADR 0090 human merges")
    expected_runner = (
        "${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK "
        "|| '[\"ubuntu-24.04\"]') }}"
    )
    require(arm.get("runs-on") == expected_runner, "required arm is not routed through the trusted lane")


def unpinned_ruleset_fields(ruleset: dict, image: dict) -> list[str]:
    beside_the_payload = [
        key for key in ruleset
        if key not in RULESET_FIELDS and key not in RULESET_METADATA_FIELDS
    ]
    return unpinned_keys(normalize_ruleset(ruleset), image) + beside_the_payload


def candidate_by_id(candidates: dict[int, dict], ruleset_id: int) -> dict:
    require(
        ruleset_id in candidates,
        f"contracted ruleset {ruleset_id} is absent from the organization listing, "
        "so its unpinned fields cannot be reported",
    )
    return candidates[ruleset_id]


def candidate_by_name(candidates: dict[int, dict], name: str) -> dict:
    named = [candidate for candidate in candidates.values() if candidate.get("name") == name]
    require(
        len(named) == 1,
        f"expected exactly one organization ruleset named {name}, found {len(named)}",
    )
    return named[0]


def unpinned_live_fields(
    contract: dict,
    live: dict,
    candidates: dict[int, dict],
    state: str,
    selector: str,
) -> list[str]:
    """Report, per contracted ruleset, the live fields no reviewed image pins.

    Every contracted ruleset carries the same residual, so a review fed only
    main-protection would leave the arm and both core-checks rulesets exactly as
    unreviewable as before. Top-level keys are read from the unnormalized
    ruleset: `normalize_ruleset` keeps the mutation payload, so a policy field
    GitHub adds beside it would otherwise be invisible to this report as well as
    to the comparison. Identity, provenance, and timestamps are not candidates.
    """
    matched = contract["preimage" if state == "ready" else "postimage"]
    # Never fall back to `live`: it is normalized, so it carries no top-level
    # key outside RULESET_FIELDS and its residual is empty by construction. A
    # contracted ruleset absent from the listing would then report as pinning
    # everything, precisely when it could not be read -- the silence ADR 0188
    # rules out. An unreadable surface is a failure, not an empty one.
    surfaces = [(matched["name"], candidate_by_id(candidates, contract["ruleset_id"]), matched)]
    if state != "ready":
        surfaces.append((
            contract["arm_ruleset_name"],
            candidate_by_name(candidates, contract["arm_ruleset_name"]),
            contract["arm_ruleset" if selector == "target" else "legacy_arm_ruleset"],
        ))
    for declaration in contract["deterministic_rulesets"]:
        surfaces.append((
            declaration["image"]["name"],
            candidate_by_id(candidates, declaration["id"]),
            declaration["image"],
        ))
    # Sorted so the review diff this feeds is stable between runs: an unordered
    # list would show every field as moved whenever one is added.
    return sorted(
        f"{label}.{field}"
        for label, ruleset, image in surfaces
        for field in unpinned_ruleset_fields(ruleset, image)
    )


def audit(contract: dict, read=gh_pages, allow_unschedulable_selection: bool = False) -> dict:
    state, live = verify_ruleset_state(contract, read)
    candidates, selector = verify_ruleset_exclusivity(contract, read, state)
    # Whichever ruleset carries the workflows rule right now is the one whose
    # selection has to be startable.
    carrier = live if state == "ready" else next(
        normalize_ruleset(candidate)
        for candidate in candidates.values()
        if candidate.get("name") == contract["arm_ruleset_name"]
    )
    current_path = workflow_path(carrier)
    # Before any rollout precondition: a precondition explains why the split
    # cannot land yet, which is a different and lesser fact than the currently
    # selected workflow being unable to run at all.
    # `allow_unschedulable_selection` is ONLY for rendering the split payloads,
    # because the split is this defect's remedy: refusing to render it while the
    # selection is dead would make the audit guard the outage in place. It never
    # suppresses the finding for a plain audit, and it never applies once split —
    # a dead selection then is a live regression, not a state being repaired.
    if not (allow_unschedulable_selection and state == "ready"):
        verify_selected_workflow_is_schedulable(carrier, read)
    repositories = paginated_items(
        read,
        f"orgs/{contract['organization']}/repos?per_page=100&type=all",
    )
    require(repositories, "organization has no governed repositories")
    governed = {}
    for repository in repositories:
        full_name = repository.get("full_name")
        require(isinstance(full_name, str) and full_name, "organization repository has no full_name")
        require(full_name not in governed, f"duplicate organization repository {full_name}")
        governed[full_name] = bool(repository.get("private"))
    covered = verify_deterministic_ci(contract, read, repositories, candidates, selector)
    verify_no_repository_shadowing(contract, read, repositories)
    # The App credential only has to reach where the arm actually runs. Demanding
    # organization-wide reach for a rule that governs a subset is what made the
    # credential scope look like a blocker rather than a decision.
    verify_authorization(contract, read, {name: governed[name] for name in covered})
    verify_adopter_conformance(contract, read, repositories, covered)
    verify_replacement_workflow(contract, read)
    return {
        "unpinned_live_fields": unpinned_live_fields(
            contract,
            live,
            candidates,
            state,
            selector,
        ),
        "organization": contract["organization"],
        "ruleset_id": contract["ruleset_id"],
        "current_path": current_path,
        "replacement_path": contract["replacement_path"],
        "governed_repositories": len(governed),
        "armed_repositories": len(covered),
        "state": "split-legacy-selector" if state == "split" and selector == "legacy" else state,
    }


SPLIT_MODES = {"split": "postimage", "arm-ruleset": "arm_ruleset"}


def render_payload(contract: dict, mode: str, read=gh_pages) -> dict:
    if mode in SPLIT_MODES:
        report = audit(contract, read, allow_unschedulable_selection=True)
        require(report["state"] == "ready", f"{mode} payload requires ready preflight")
        return contract[SPLIT_MODES[mode]]
    verify_ruleset_state(contract, read, "split")
    return contract["rollback_payload"]


def render_property_migration_payload(contract: dict, mode: str, read=gh_pages) -> dict:
    if mode == "rollback":
        state, _ = verify_ruleset_state(contract, read, "split")
        _, selector = verify_ruleset_exclusivity(contract, read, state)
        require(selector == "target", "property rollback requires the target selector")
        return contract["legacy_arm_ruleset"]
    if mode == "unset-values":
        state, _ = verify_ruleset_state(contract, read, "split")
        _, selector = verify_ruleset_exclusivity(contract, read, state)
        require(selector == "legacy", "property cleanup requires the restored legacy selector")
        verify_arm_property_schema(contract, read, required=True)
        cohort = target_arm_cohort(contract, read_repository_properties(contract, read))
        reviewed = {
            repository_identity(repository)
            for repository in contract["arm_property_migration"]["repositories"]
        }
        require(
            {repository_identity(repository) for repository in cohort}.issubset(reviewed),
            "property cleanup cohort contains a repository outside the reviewed migration set",
        )
        return {
            "repository_names": [
                repository.split("/", 1)[1]
                for repository in contract["arm_property_migration"]["repositories"]
            ],
            "properties": [{
                "property_name": contract["arm_property"]["name"],
                "value": None,
            }],
        }
    report = audit(contract, read)
    require(
        report["state"] == "split-legacy-selector",
        f"property migration requires the reviewed legacy selector, found {report['state']}",
    )
    if mode == "schema":
        return contract["arm_property"]["definition"]
    verify_arm_property_schema(contract, read, required=True)
    if mode == "values":
        return {
            "repository_names": [
                repository.split("/", 1)[1]
                for repository in contract["arm_property_migration"]["repositories"]
            ],
            "properties": [{
                "property_name": contract["arm_property"]["name"],
                "value": contract["arm_property"]["value"],
            }],
        }
    require(mode == "ruleset", f"unsupported property migration mode {mode}")
    verify_target_arm_cohort(contract, read_repository_properties(contract, read))
    return contract["arm_ruleset"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the AI authorization-arm ruleset rollout")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--render-split-payload", action="store_true")
    modes.add_argument("--render-arm-ruleset-payload", action="store_true")
    modes.add_argument("--render-rollback-payload", action="store_true")
    modes.add_argument("--render-arm-property-schema-payload", action="store_true")
    modes.add_argument("--render-arm-property-values-payload", action="store_true")
    modes.add_argument("--render-arm-property-ruleset-payload", action="store_true")
    modes.add_argument("--render-arm-property-rollback-payload", action="store_true")
    modes.add_argument("--render-arm-property-unset-values-payload", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        contract = read_contract()
        if args.render_split_payload:
            result = render_payload(contract, "split")
        elif args.render_arm_ruleset_payload:
            result = render_payload(contract, "arm-ruleset")
        elif args.render_rollback_payload:
            result = render_payload(contract, "rollback")
        elif args.render_arm_property_schema_payload:
            result = render_property_migration_payload(contract, "schema")
        elif args.render_arm_property_values_payload:
            result = render_property_migration_payload(contract, "values")
        elif args.render_arm_property_ruleset_payload:
            result = render_property_migration_payload(contract, "ruleset")
        elif args.render_arm_property_rollback_payload:
            result = render_property_migration_payload(contract, "rollback")
        elif args.render_arm_property_unset_values_payload:
            result = render_property_migration_payload(contract, "unset-values")
        else:
            result = audit(contract)
    except AuditError as error:
        print(f"ERROR: authorization-arm-rollout-not-ready: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
