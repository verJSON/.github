#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CHECKED_IN_POLICY = ROOT / "config/org-ruleset-conformance-policy.json"


class AuditDataError(Exception):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AuditDataError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def load_json(text: str, source: str):
    try:
        return json.loads(text, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, AuditDataError) as error:
        raise AuditDataError(f"{source} is not valid JSON: {error}") from None


def require_mapping(value, location: str):
    if not isinstance(value, dict):
        raise AuditDataError(f"{location} must be an object")
    return value


def require_array(value, location: str):
    if not isinstance(value, list):
        raise AuditDataError(f"{location} must be an array")
    return value


def require_string(value, location: str):
    if not isinstance(value, str) or not value.strip():
        raise AuditDataError(f"{location} must be a non-empty string")
    return value


def require_positive_integer(value, location: str):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AuditDataError(f"{location} must be a positive integer")
    return value


def require_exact_keys(value: dict, expected: set[str], location: str):
    actual = set(value)
    if actual != expected:
        raise AuditDataError(
            f"{location} keys are {sorted(actual)}, expected {sorted(expected)}"
        )


def gh_json_pages(path: str):
    result = subprocess.run(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            "GET",
            "--paginate",
            "--slurp",
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"GitHub API read failed for {path}")
    pages = load_json(result.stdout, f"GitHub API response for {path}")
    if not isinstance(pages, list) or not pages:
        raise AuditDataError(f"GitHub API returned no pages for {path}")
    return pages


def select_policy(arguments: list[str]):
    if not arguments:
        return CHECKED_IN_POLICY
    if (
        len(arguments) == 2
        and arguments[0] == "--test-policy"
        and arguments[1]
    ):
        return Path(arguments[1])
    raise AuditDataError(
        "usage: org-ruleset-conformance.py [--test-policy PATH]"
    )


def read_declared_status_check(value, producer_app_id: int, location: str):
    if value is None:
        return None
    check = require_mapping(value, location)
    require_exact_keys(check, {"context", "integration_id", "strict"}, location)
    if not isinstance(check["strict"], bool):
        raise AuditDataError(f"{location}.strict must be a boolean")
    integration_id = require_positive_integer(
        check["integration_id"], f"{location}.integration_id"
    )
    if integration_id != producer_app_id:
        raise AuditDataError(
            f"{location}.integration_id must be the declared producer App {producer_app_id}"
        )
    return {
        "context": require_string(check["context"], f"{location}.context"),
        "integration_id": integration_id,
        "strict": check["strict"],
    }


def read_policy(policy: Path):
    document = require_mapping(
        load_json(policy.read_text(encoding="utf-8"), "ruleset policy"),
        "ruleset policy",
    )
    require_exact_keys(
        document,
        {
            "organization",
            "release_authorization_bypass",
            "required_check_producer_app_id",
            "bypassless_required_workflows",
            "bypass_actor_contracts",
        },
        "ruleset policy",
    )
    organization = require_string(document["organization"], "ruleset policy.organization")
    actor = require_mapping(
        document["release_authorization_bypass"],
        "ruleset policy.release_authorization_bypass",
    )
    require_exact_keys(
        actor,
        {"actor_type", "actor_id", "bypass_mode"},
        "ruleset policy.release_authorization_bypass",
    )
    expected = {
        "actor_type": require_string(
            actor["actor_type"],
            "ruleset policy.release_authorization_bypass.actor_type",
        ),
        "actor_id": require_positive_integer(
            actor["actor_id"],
            "ruleset policy.release_authorization_bypass.actor_id",
        ),
        "bypass_mode": require_string(
            actor["bypass_mode"],
            "ruleset policy.release_authorization_bypass.bypass_mode",
        ),
    }
    if expected["actor_type"] != "Integration" or expected["bypass_mode"] != "always":
        raise AuditDataError(
            "release authorization policy must require an always-bypass Integration"
        )
    producer_app_id = require_positive_integer(
        document["required_check_producer_app_id"],
        "ruleset policy.required_check_producer_app_id",
    )
    exceptions = require_array(
        document["bypassless_required_workflows"],
        "ruleset policy.bypassless_required_workflows",
    )
    parsed_exceptions = []
    names = set()
    for index, value in enumerate(exceptions):
        location = f"ruleset policy.bypassless_required_workflows[{index}]"
        item = require_mapping(value, location)
        require_exact_keys(
            item,
            {
                "name",
                "repository_id",
                "workflow_repository_id",
                "workflow_path",
                "workflow_ref",
                "required_status_check",
            },
            location,
        )
        parsed = {
            "name": require_string(item["name"], f"{location}.name"),
            "repository_id": require_positive_integer(
                item["repository_id"], f"{location}.repository_id"
            ),
            "workflow_repository_id": require_positive_integer(
                item["workflow_repository_id"],
                f"{location}.workflow_repository_id",
            ),
            "workflow_path": require_string(
                item["workflow_path"], f"{location}.workflow_path"
            ),
            "workflow_ref": require_string(
                item["workflow_ref"], f"{location}.workflow_ref"
            ),
            "required_status_check": read_declared_status_check(
                item["required_status_check"],
                producer_app_id,
                f"{location}.required_status_check",
            ),
        }
        if parsed["name"] in names:
            raise AuditDataError(
                f"ruleset policy contains duplicate bypassless ruleset {parsed['name']}"
            )
        names.add(parsed["name"])
        parsed_exceptions.append(parsed)

    contracts = require_array(
        document["bypass_actor_contracts"],
        "ruleset policy.bypass_actor_contracts",
    )
    parsed_contracts = []
    contract_ids = set()
    contract_names = set()
    for index, value in enumerate(contracts):
        location = f"ruleset policy.bypass_actor_contracts[{index}]"
        item = require_mapping(value, location)
        require_exact_keys(item, {"ruleset_id", "name", "bypass_actors"}, location)
        actor_values = require_array(item["bypass_actors"], f"{location}.bypass_actors")
        if not actor_values:
            raise AuditDataError(f"{location}.bypass_actors must not be empty")
        bypass_actors = []
        actor_identities = set()
        for actor_index, actor_value in enumerate(actor_values):
            actor_location = f"{location}.bypass_actors[{actor_index}]"
            actor = require_mapping(actor_value, actor_location)
            require_exact_keys(
                actor,
                {"actor_type", "actor_id", "bypass_mode"},
                actor_location,
            )
            actor_id = actor["actor_id"]
            if actor_id is not None:
                require_positive_integer(actor_id, f"{actor_location}.actor_id")
            parsed_actor = {
                "actor_type": require_string(
                    actor["actor_type"], f"{actor_location}.actor_type"
                ),
                "actor_id": actor_id,
                "bypass_mode": require_string(
                    actor["bypass_mode"], f"{actor_location}.bypass_mode"
                ),
            }
            identity = tuple(parsed_actor.values())
            if identity in actor_identities:
                raise AuditDataError(
                    f"{location} contains duplicate bypass actor {identity!r}"
                )
            actor_identities.add(identity)
            bypass_actors.append(parsed_actor)
        # A null id keys the contract by name: a ruleset applied from reviewed
        # config (#1816) has no id until it is created, and its own check owns
        # its presence and full image.
        parsed = {
            "ruleset_id": None
            if item["ruleset_id"] is None
            else require_positive_integer(item["ruleset_id"], f"{location}.ruleset_id"),
            "name": require_string(item["name"], f"{location}.name"),
            "bypass_actors": bypass_actors,
        }
        if parsed["ruleset_id"] is not None and parsed["ruleset_id"] in contract_ids:
            raise AuditDataError(
                f"ruleset policy contains duplicate bypass contract id {parsed['ruleset_id']}"
            )
        if parsed["name"] in contract_names:
            raise AuditDataError(
                f"ruleset policy contains duplicate bypass contract name {parsed['name']}"
            )
        contract_ids.add(parsed["ruleset_id"])
        contract_names.add(parsed["name"])
        parsed_contracts.append(parsed)
    return organization, expected, producer_app_id, parsed_exceptions, parsed_contracts


def list_ruleset_ids(organization: str):
    pages = gh_json_pages(f"orgs/{organization}/rulesets?per_page=100")
    ruleset_ids = []
    seen = set()
    for page_index, page_value in enumerate(pages):
        page = require_array(page_value, f"ruleset listing page {page_index}")
        for entry_index, entry_value in enumerate(page):
            entry = require_mapping(
                entry_value,
                f"ruleset listing entry {entry_index} on page {page_index}",
            )
            ruleset_id = require_positive_integer(
                entry.get("id"),
                f"ruleset listing entry {entry_index} on page {page_index}.id",
            )
            if ruleset_id in seen:
                raise AuditDataError(f"ruleset listing contains duplicate ruleset id {ruleset_id}")
            seen.add(ruleset_id)
            ruleset_ids.append(ruleset_id)
    if not ruleset_ids:
        raise AuditDataError("ruleset listing contained no rulesets")
    return ruleset_ids


def validate_string_array(value, location: str):
    values = require_array(value, location)
    for index, item in enumerate(values):
        require_string(item, f"{location}[{index}]")
    return values


def read_ruleset(organization: str, ruleset_id: int):
    documents = gh_json_pages(f"orgs/{organization}/rulesets/{ruleset_id}")
    if len(documents) != 1:
        raise AuditDataError(
            f"ruleset {ruleset_id} detail returned {len(documents)} documents"
        )
    ruleset = require_mapping(documents[0], f"ruleset {ruleset_id}")
    detail_id = require_positive_integer(ruleset.get("id"), f"ruleset {ruleset_id}.id")
    if detail_id != ruleset_id:
        raise AuditDataError(
            f"ruleset detail id {detail_id} does not match requested id {ruleset_id}"
        )
    require_string(ruleset.get("name"), f"ruleset {ruleset_id}.name")
    source_type = require_string(
        ruleset.get("source_type"), f"ruleset {ruleset_id}.source_type"
    )
    if source_type != "Organization":
        raise AuditDataError(
            f"ruleset {ruleset_id}.source_type must be 'Organization'"
        )
    target = require_string(ruleset.get("target"), f"ruleset {ruleset_id}.target")
    enforcement = require_string(
        ruleset.get("enforcement"), f"ruleset {ruleset_id}.enforcement"
    )
    if enforcement not in {"active", "evaluate", "disabled"}:
        raise AuditDataError(f"ruleset {ruleset_id}.enforcement is unrecognized")

    conditions = require_mapping(
        ruleset.get("conditions"), f"ruleset {ruleset_id}.conditions"
    )
    if target == "branch" or "ref_name" in conditions:
        ref_name = require_mapping(
            conditions.get("ref_name"), f"ruleset {ruleset_id}.conditions.ref_name"
        )
        validate_string_array(
            ref_name.get("include"),
            f"ruleset {ruleset_id}.conditions.ref_name.include",
        )
        validate_string_array(
            ref_name.get("exclude"),
            f"ruleset {ruleset_id}.conditions.ref_name.exclude",
        )

    actors = require_array(
        ruleset.get("bypass_actors"), f"ruleset {ruleset_id}.bypass_actors"
    )
    for actor_index, actor_value in enumerate(actors):
        actor = require_mapping(
            actor_value, f"ruleset {ruleset_id} bypass actor {actor_index}"
        )
        require_exact_keys(
            actor,
            {"actor_type", "actor_id", "bypass_mode"},
            f"ruleset {ruleset_id} bypass actor {actor_index}",
        )
        require_string(
            actor.get("actor_type"),
            f"ruleset {ruleset_id} bypass actor {actor_index}.actor_type",
        )
        actor_id = actor.get("actor_id")
        if actor_id is not None:
            require_positive_integer(
                actor_id, f"ruleset {ruleset_id} bypass actor {actor_index}.actor_id"
            )
        require_string(
            actor.get("bypass_mode"),
            f"ruleset {ruleset_id} bypass actor {actor_index}.bypass_mode",
        )

    rules = require_array(ruleset.get("rules"), f"ruleset {ruleset_id}.rules")
    for rule_index, rule_value in enumerate(rules):
        rule = require_mapping(rule_value, f"ruleset {ruleset_id} rule {rule_index}")
        rule_location = f"ruleset {ruleset_id} rule {rule_index}"
        if require_string(rule.get("type"), f"{rule_location}.type") != "required_status_checks":
            continue
        parameters = require_mapping(rule.get("parameters"), f"{rule_location}.parameters")
        checks = require_array(
            parameters.get("required_status_checks"),
            f"{rule_location}.parameters.required_status_checks",
        )
        for check_index, check_value in enumerate(checks):
            check_location = f"{rule_location}.parameters.required_status_checks[{check_index}]"
            check = require_mapping(check_value, check_location)
            require_string(check.get("context"), f"{check_location}.context")
            if check.get("integration_id") is not None:
                require_positive_integer(
                    check["integration_id"], f"{check_location}.integration_id"
                )
    return ruleset


def targets_default_branch_token(ruleset: dict):
    return (
        ruleset["target"] == "branch"
        and "~DEFAULT_BRANCH" in ruleset["conditions"]["ref_name"]["include"]
    )


def has_expected_actor(ruleset: dict, expected: dict):
    return any(
        actor.get("actor_type") == expected["actor_type"]
        and actor.get("actor_id") == expected["actor_id"]
        and actor.get("bypass_mode") == expected["bypass_mode"]
        for actor in ruleset["bypass_actors"]
    )


def is_declared_status_check_rule(rule: dict, declared: dict):
    # Identity, not equality: `1 == True` and `0 == False`, so a dict comparison
    # would accept a non-boolean strictness the API never returns.
    parameters = rule.get("parameters")
    if set(rule) != {"type", "parameters"} or not isinstance(parameters, dict):
        return False
    if set(parameters) != {
        "strict_required_status_checks_policy",
        "do_not_enforce_on_create",
        "required_status_checks",
    }:
        return False
    if (
        parameters["strict_required_status_checks_policy"] is not declared["strict"]
        or parameters["do_not_enforce_on_create"] is not False
    ):
        return False
    checks = parameters["required_status_checks"]
    if not isinstance(checks, list) or len(checks) != 1:
        return False
    check = checks[0]
    return (
        isinstance(check, dict)
        and set(check) == {"context", "integration_id"}
        and check["context"] == declared["context"]
        and check["integration_id"] == declared["integration_id"]
    )


def is_exact_bypassless_required_workflow(ruleset: dict, exceptions: list[dict]):
    matching = [item for item in exceptions if item["name"] == ruleset["name"]]
    if len(matching) != 1 or ruleset["bypass_actors"] != []:
        return False
    expected = matching[0]
    conditions = ruleset["conditions"]
    if conditions != {
        "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
        "repository_id": {"repository_ids": [expected["repository_id"]]},
    }:
        return False
    if ruleset["target"] != "branch" or ruleset["enforcement"] != "active":
        return False
    rules_by_type = {}
    for rule in ruleset["rules"]:
        rules_by_type.setdefault(rule["type"], []).append(rule)
    declared_check = expected["required_status_check"]
    expected_types = {"workflows"}
    if declared_check is not None:
        expected_types.add("required_status_checks")
    if set(rules_by_type) != expected_types or any(
        len(rules) != 1 for rules in rules_by_type.values()
    ):
        return False
    if declared_check is not None and not is_declared_status_check_rule(
        rules_by_type["required_status_checks"][0], declared_check
    ):
        return False
    parameters = rules_by_type["workflows"][0].get("parameters")
    if not isinstance(parameters, dict) or set(parameters) != {
        "do_not_enforce_on_create", "workflows",
    } or parameters["do_not_enforce_on_create"] is not False:
        return False
    workflows = parameters["workflows"]
    if not isinstance(workflows, list) or len(workflows) != 1:
        return False
    workflow = workflows[0]
    return (
        isinstance(workflow, dict)
        and set(workflow) == {"path", "repository_id", "ref", "sha"}
        and workflow["path"] == expected["workflow_path"]
        and workflow["repository_id"] == expected["workflow_repository_id"]
        and workflow["ref"] == expected["workflow_ref"]
        and isinstance(workflow["sha"], str)
        and len(workflow["sha"]) == 40
        and all(character in "0123456789abcdef" for character in workflow["sha"])
    )


def producer_binding_findings(ruleset: dict, producer_app_id: int):
    """Required contexts an App other than the declared producer could satisfy."""
    findings = []
    for rule in ruleset["rules"]:
        if rule.get("type") != "required_status_checks":
            continue
        for check in rule["parameters"]["required_status_checks"]:
            binding = check.get("integration_id")
            if binding == producer_app_id:
                continue
            findings.append(
                (
                    check["context"],
                    "is not bound to a producer App"
                    if binding is None
                    else f"is bound to App {binding}, not the declared producer App {producer_app_id}",
                )
            )
    return findings


def bypass_contract_findings(rulesets: list[dict], contracts: list[dict]):
    """Require every non-empty bypass list to match one reviewed actor image."""
    live_by_id = {ruleset["id"]: ruleset for ruleset in rulesets}
    contracts_by_id = {
        contract["ruleset_id"]: contract
        for contract in contracts
        if contract["ruleset_id"] is not None
    }
    name_keyed = {
        contract["name"]: contract for contract in contracts if contract["ruleset_id"] is None
    }
    findings = []

    for contract in contracts:
        label = f"reviewed bypass contract {contract['name']} ({contract['ruleset_id']})"
        if contract["ruleset_id"] is None:
            named = [ruleset for ruleset in rulesets if ruleset["name"] == contract["name"]]
            if not named:
                continue
            if len(named) > 1:
                findings.append(f"{label}: {len(named)} live rulesets share the name")
                continue
            ruleset = named[0]
        else:
            ruleset = live_by_id.get(contract["ruleset_id"])
        if ruleset is None:
            findings.append(f"{label}: ruleset is absent; remove the stale contract")
        elif ruleset["name"] != contract["name"]:
            findings.append(
                f"{label}: live ruleset name is {ruleset['name']!r}; contract identity drifted"
            )
        elif canonical_bypass_actors(ruleset["bypass_actors"]) != canonical_bypass_actors(
            contract["bypass_actors"]
        ):
            findings.append(f"{label}: bypass actors differ from the reviewed contract")

    for ruleset in rulesets:
        if not ruleset["bypass_actors"]:
            continue
        contract = contracts_by_id.get(ruleset["id"]) or name_keyed.get(ruleset["name"])
        if contract is None or contract["name"] != ruleset["name"]:
            findings.append(
                f"{ruleset['name']} ({ruleset['id']}): bypass actors are not covered "
                "by a reviewed contract"
            )
    return findings


def canonical_bypass_actors(actors: list[dict]):
    return sorted(
        [
            (
                actor["actor_type"],
                actor["actor_id"],
                actor["bypass_mode"],
            )
            for actor in actors
        ],
        key=lambda actor: (actor[0], str(actor[1]), actor[2]),
    )


def main(arguments: list[str] | None = None) -> int:
    try:
        policy = select_policy(sys.argv[1:] if arguments is None else arguments)
        (
            organization,
            expected_actor,
            producer_app_id,
            bypassless_exceptions,
            bypass_contracts,
        ) = read_policy(policy)
        ruleset_ids = list_ruleset_ids(organization)
        rulesets = [read_ruleset(organization, ruleset_id) for ruleset_id in ruleset_ids]
    except (OSError, AuditDataError, RuntimeError) as error:
        print(f"ERROR: cannot establish organization ruleset state: {error}", file=sys.stderr)
        return 2

    default_branch_token_rulesets = [
        ruleset for ruleset in rulesets if targets_default_branch_token(ruleset)
    ]
    failures = [
        ruleset
        for ruleset in default_branch_token_rulesets
        if not has_expected_actor(ruleset, expected_actor)
        and not is_exact_bypassless_required_workflow(
            ruleset, bypassless_exceptions
        )
    ]
    unbound = [
        (ruleset, context, diagnostic)
        for ruleset in rulesets
        for context, diagnostic in producer_binding_findings(ruleset, producer_app_id)
    ]
    bypass_findings = bypass_contract_findings(rulesets, bypass_contracts)
    if failures or unbound or bypass_findings:
        for ruleset in failures:
            print(
                f"ERROR: {ruleset['name']} ({ruleset['id']}): "
                "required release authorization bypass is absent",
                file=sys.stderr,
            )
        for ruleset, context, diagnostic in unbound:
            print(
                f"ERROR: {ruleset['name']} ({ruleset['id']}): "
                f"required status check {context!r} {diagnostic}",
                file=sys.stderr,
            )
        for finding in bypass_findings:
            print(f"ERROR: {finding}", file=sys.stderr)
        return 1

    print(
        f"ruleset-policy=conformant organization={organization} "
        f"rulesets={len(rulesets)} "
        f"default_branch_token_rulesets={len(default_branch_token_rulesets)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
