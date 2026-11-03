#!/usr/bin/env python3
"""Fail closed when ADR 0252's Actions-paused exclusion drifts from live state.

Reads only, through explicit paginated GETs. Exit 0 is conformant, 1 is drift,
and 2 means the audit could not establish trustworthy API or policy state.
"""
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent.parent
CHECKED_IN_POLICY = ROOT / "config/actions-paused-drift-policy.json"
# ADR 0252: a paused-only relaxation records results without blocking while
# its repository is paused, and blocks again once the repository is enabled.
RELAXATION_ENFORCEMENT = {"paused": "evaluate", "enabled": "active"}
RULESET_ENFORCEMENTS = {"active", "disabled", "evaluate"}


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


def gh_json_list(path: str):
    items = []
    for index, page in enumerate(gh_json_pages(path)):
        items.extend(require_array(page, f"{path} page {index}"))
    return items


def gh_json_object(path: str):
    pages = gh_json_pages(path)
    if len(pages) != 1:
        raise AuditDataError(f"{path} returned {len(pages)} pages, expected one object")
    return require_mapping(pages[0], path)


def select_policy(arguments: list[str]):
    if not arguments:
        return CHECKED_IN_POLICY
    if len(arguments) == 2 and arguments[0] == "--test-policy" and arguments[1]:
        return Path(arguments[1])
    raise AuditDataError("usage: actions-paused-drift.py [--test-policy PATH]")


def read_policy(policy: Path):
    document = require_mapping(
        load_json(policy.read_text(encoding="utf-8"), "drift policy"), "drift policy"
    )
    require_exact_keys(
        document,
        {"source", "organization", "property", "baseline_ruleset_id", "paused_only_relaxations"},
        "drift policy",
    )
    relaxations = []
    for index, entry in enumerate(
        require_array(document["paused_only_relaxations"], "drift policy.paused_only_relaxations")
    ):
        location = f"drift policy.paused_only_relaxations[{index}]"
        require_exact_keys(
            require_mapping(entry, location), {"ruleset_id", "scope", "repository"}, location
        )
        relaxations.append(
            {
                "ruleset_id": require_positive_integer(entry["ruleset_id"], f"{location}.ruleset_id"),
                "scope": require_string(entry["scope"], f"{location}.scope"),
                "repository": require_string(entry["repository"], f"{location}.repository"),
            }
        )
    return {
        "organization": require_string(document["organization"], "drift policy.organization"),
        "property": require_string(document["property"], "drift policy.property"),
        "baseline_ruleset_id": require_positive_integer(
            document["baseline_ruleset_id"], "drift policy.baseline_ruleset_id"
        ),
        "relaxations": relaxations,
    }


def read_actions_states(organization: str, property_name: str):
    states = {}
    path = f"orgs/{organization}/properties/values?per_page=100"
    for index, entry in enumerate(gh_json_list(path)):
        location = f"{path} item {index}"
        require_mapping(entry, location)
        name = require_string(entry.get("repository_name"), f"{location}.repository_name")
        values = [
            require_mapping(item, f"{location}.properties")
            for item in require_array(entry.get("properties"), f"{location}.properties")
        ]
        matching = [item for item in values if item.get("property_name") == property_name]
        # The API lists an unset property with a null value; such a repository is
        # not excluded from anything, so ADR 0252 does not govern it.
        if matching and matching[0].get("value") is not None:
            value = matching[0]["value"]
            if value not in RELAXATION_ENFORCEMENT:
                raise AuditDataError(
                    f"{name} has {property_name}={value!r}, "
                    f"expected one of {sorted(RELAXATION_ENFORCEMENT)}"
                )
            states[name] = value
    return states


def read_repositories(organization: str):
    repositories = {}
    path = f"orgs/{organization}/repos?type=all&per_page=100"
    for index, entry in enumerate(gh_json_list(path)):
        location = f"{path} item {index}"
        require_mapping(entry, location)
        name = require_string(entry.get("name"), f"{location}.name")
        repositories[name] = {
            "id": require_positive_integer(entry.get("id"), f"{location}.id"),
            "default_branch": require_string(entry.get("default_branch"), f"{location}.default_branch"),
        }
    return repositories


def read_actions_enabled(organization: str, name: str):
    path = f"repos/{organization}/{name}/actions/permissions"
    enabled = gh_json_object(path).get("enabled")
    if not isinstance(enabled, bool):
        raise AuditDataError(f"{path}.enabled must be a boolean")
    return enabled


def read_branch_ruleset_ids(organization: str, name: str, branch: str):
    path = f"repos/{organization}/{name}/rules/branches/{quote(branch, safe='/')}?per_page=100"
    return {
        require_positive_integer(
            require_mapping(rule, f"{path} rule").get("ruleset_id"), f"{path} rule.ruleset_id"
        )
        for rule in gh_json_list(path)
    }


def read_relaxation(organization: str, relaxation: dict, repository_id: int):
    ruleset_id = relaxation["ruleset_id"]
    name = relaxation["repository"]
    if relaxation["scope"] == "organization":
        ruleset = gh_json_object(f"orgs/{organization}/rulesets/{ruleset_id}")
        # A repository-id-targeted org ruleset cannot take the property exclude,
        # which is why ADR 0252 relaxes it; it must still bind only its repository.
        conditions = require_mapping(ruleset.get("conditions"), f"ruleset {ruleset_id}.conditions")
        target = conditions.get("repository_id")
        if not isinstance(target, dict) or target.get("repository_ids") != [repository_id]:
            raise AuditDataError(
                f"organization ruleset {ruleset_id} must target exactly {name} ({repository_id})"
            )
    else:
        ruleset = gh_json_object(f"repos/{organization}/{name}/rulesets/{ruleset_id}")
        # The repository endpoint also returns inherited organization rulesets.
        source = ruleset.get("source")
        if (
            ruleset.get("source_type") != "Repository"
            or not isinstance(source, str)
            or source.casefold() != f"{organization}/{name}".casefold()
        ):
            raise AuditDataError(f"ruleset {ruleset_id} must be a Repository ruleset of {name}")
    if ruleset.get("id") != ruleset_id:
        raise AuditDataError(
            f"ruleset {ruleset_id} response carries id {ruleset.get('id')!r}"
        )
    enforcement = ruleset.get("enforcement")
    if enforcement not in RULESET_ENFORCEMENTS:
        raise AuditDataError(
            f"ruleset {ruleset_id} enforcement {enforcement!r} is not one of "
            f"{sorted(RULESET_ENFORCEMENTS)}"
        )
    return enforcement


def main(arguments: list[str] | None = None) -> int:
    try:
        policy = read_policy(select_policy(sys.argv[1:] if arguments is None else arguments))
        organization = policy["organization"]
        states = read_actions_states(organization, policy["property"])
        repositories = read_repositories(organization)
        findings = []
        for name, state in states.items():
            if state == "paused" and read_actions_enabled(organization, name):
                findings.append(
                    f"{name} is {policy['property']}=paused but its Actions are enabled"
                )
            branch = repositories[name]["default_branch"]
            governed = policy["baseline_ruleset_id"] in read_branch_ruleset_ids(
                organization, name, branch
            )
            if state == "enabled" and governed:
                findings.append(
                    f"{name} is {policy['property']}=enabled but its default branch {branch} "
                    f"is still governed by the paused baseline ruleset {policy['baseline_ruleset_id']}"
                )
            if state == "paused" and not governed:
                findings.append(
                    f"{name} is {policy['property']}=paused but its default branch {branch} "
                    f"is not governed by the paused baseline ruleset {policy['baseline_ruleset_id']}"
                )
        for relaxation in policy["relaxations"]:
            name = relaxation["repository"]
            if name not in states:
                raise AuditDataError(
                    f"declared relaxation {relaxation['ruleset_id']} names {name}, "
                    f"which has no {policy['property']}"
                )
            enforcement = read_relaxation(organization, relaxation, repositories[name]["id"])
            expected = RELAXATION_ENFORCEMENT[states[name]]
            if enforcement != expected:
                findings.append(
                    f"ruleset {relaxation['ruleset_id']} for {name} is {enforcement}, "
                    f"expected {expected} while {policy['property']}={states[name]}"
                )
    except (OSError, AuditDataError, RuntimeError) as error:
        print(f"ERROR: cannot establish Actions-paused state: {error}", file=sys.stderr)
        return 2

    if findings:
        for finding in findings:
            print(f"ERROR: {finding}", file=sys.stderr)
        return 1

    paused = sum(1 for state in states.values() if state == "paused")
    print(
        f"actions-paused-drift=conformant paused={paused} "
        f"enabled={len(states) - paused} relaxations={len(policy['relaxations'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
