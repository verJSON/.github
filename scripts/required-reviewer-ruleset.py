#!/usr/bin/env python3
"""Keep sensitive-path required-reviewer org rulesets equal to reviewed config (#1816)."""
import argparse
import json
from pathlib import Path
import sys

# `python3 -I` does not put the script directory on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from github_identity import same_organization  # noqa: E402
from org_ruleset_api import (  # noqa: E402
    ContractError, drifted_fields, gh_json, gh_json_input, list_organization_rulesets,
    load_json, require,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/required-reviewer-rulesets.json"
ORGANIZATION = "Verjson"
ISSUE = 1816
APPLY_ACKNOWLEDGEMENT = f"APPLY-REQUIRED-REVIEWERS-{ISSUE}"
NAME_SUFFIX = "-sensitive-path-admin-review"
ADMINS_TEAM = {"type": "Team", "id": 17813010}
# ADR 0210 (2026-10-19 amendment): only the release-authorization App may bypass. The
# merge App, Renovate, and organization owners stay subject to the admin approval.
RELEASE_BYPASS = [{"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"}]
RULESET_KEYS = {"name", "target", "enforcement", "conditions", "bypass_actors", "rules"}
# Everything but the reviewer requirement is neutral, so the ruleset adds the admin
# approval and changes no other review behavior `main-protection` already sets.
NEUTRAL_PARAMETERS = {
    "allowed_merge_methods": ["squash"],
    "dismiss_stale_reviews_on_push": True,
    "require_code_owner_review": False,
    "require_last_push_approval": False,
    "require_extra_approval_for_unattributed_changes": False,
    "required_approving_review_count": 0,
    "required_review_thread_resolution": False,
}


def is_positive_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_file_patterns(patterns, location):
    require(
        isinstance(patterns, list) and patterns
        and all(isinstance(pattern, str) and pattern.strip() == pattern and pattern
                and not pattern.startswith("/") for pattern in patterns)
        and len(set(patterns)) == len(patterns),
        f"{location}: file_patterns must be a non-empty list of distinct "
        "repository-relative patterns",
    )


def validate_ruleset(ruleset, location):
    require(isinstance(ruleset, dict) and set(ruleset) == RULESET_KEYS,
            f"{location}: ruleset keys drifted")
    require(ruleset["target"] == "branch" and ruleset["enforcement"] == "active",
            f"{location}: must be an active branch ruleset")
    repository_name = ruleset["conditions"].get("repository_name") \
        if isinstance(ruleset["conditions"], dict) else None
    repositories = repository_name.get("include") if isinstance(repository_name, dict) else None
    repository = repositories[0] if isinstance(repositories, list) and len(repositories) == 1 else None
    require(
        isinstance(repository, str) and ruleset["conditions"] == {
            "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
            "repository_name": {"include": [repository], "exclude": [], "protected": True},
        },
        f"{location}: conditions must target exactly one protected repository's default branch",
    )
    require(ruleset["name"] == f"{repository}{NAME_SUFFIX}",
            f"{location}: name must be <repository>{NAME_SUFFIX}")
    require(ruleset["bypass_actors"] == RELEASE_BYPASS,
            f"{location}: bypass actors must be exactly the release-authorization App")
    rules = ruleset["rules"]
    require(isinstance(rules, list) and len(rules) == 1 and isinstance(rules[0], dict)
            and set(rules[0]) == {"type", "parameters"} and rules[0]["type"] == "pull_request",
            f"{location}: must hold exactly one pull_request rule")
    parameters = rules[0]["parameters"]
    require(
        isinstance(parameters, dict)
        and set(parameters) == set(NEUTRAL_PARAMETERS) | {"required_reviewers"}
        and all(type(parameters[key]) is type(value) and parameters[key] == value
                for key, value in NEUTRAL_PARAMETERS.items()),
        f"{location}: pull_request parameters must stay neutral",
    )
    reviewers = parameters["required_reviewers"]
    require(isinstance(reviewers, list) and len(reviewers) == 1
            and isinstance(reviewers[0], dict)
            and set(reviewers[0]) == {"reviewer", "minimum_approvals", "file_patterns"},
            f"{location}: must declare exactly one required reviewer")
    reviewer = reviewers[0]
    require(reviewer["reviewer"] == ADMINS_TEAM
            and type(reviewer["reviewer"].get("id")) is int,
            f"{location}: reviewer must be the admins team")
    require(is_positive_integer(reviewer["minimum_approvals"]),
            f"{location}: minimum_approvals must be a positive integer")
    validate_file_patterns(reviewer["file_patterns"], location)
    return repository


def read_config(path=CONFIG):
    config = load_json(path.read_text(encoding="utf-8"), str(path))
    require(isinstance(config, dict) and set(config) == {
        "schema_version", "organization", "issue", "apply_acknowledgement", "rulesets",
    }, "config top-level keys drifted")
    require(config["schema_version"] == 1 and config["organization"] == ORGANIZATION
            and config["issue"] == ISSUE, "config identity drifted")
    require(config["apply_acknowledgement"] == APPLY_ACKNOWLEDGEMENT,
            "apply acknowledgement drifted")
    rulesets = config["rulesets"]
    require(isinstance(rulesets, list) and rulesets,
            "config must declare at least one ruleset")
    repositories = set()
    for index, ruleset in enumerate(rulesets):
        repository = validate_ruleset(ruleset, f"rulesets[{index}]")
        require(repository not in repositories,
                f"rulesets[{index}]: duplicate ruleset for {repository}")
        repositories.add(repository)
    return config


def live_ruleset(ruleset_id):
    live = gh_json(f"orgs/{ORGANIZATION}/rulesets/{ruleset_id}")
    require(isinstance(live, dict) and live.get("id") == ruleset_id,
            f"ruleset {ruleset_id} detail identity drifted")
    require(live.get("source_type") == "Organization"
            and same_organization(live.get("source"), ORGANIZATION),
            f"ruleset {ruleset_id} is not an organization ruleset")
    return live


def check(config):
    entries = list_organization_rulesets(ORGANIZATION)
    findings = []
    for expected in config["rulesets"]:
        named = [entry for entry in entries if entry.get("name") == expected["name"]]
        if not named:
            findings.append(f"{expected['name']}: absent; apply the reviewed config")
            continue
        if len(named) > 1:
            findings.append(f"{expected['name']}: {len(named)} live rulesets share the name")
            continue
        live = live_ruleset(named[0]["id"])
        drifted = drifted_fields(live, expected)
        if drifted:
            findings.append(f"{expected['name']} ({live['id']}): differs from the reviewed "
                            f"config in {', '.join(drifted)}")
        else:
            print(f"verified: {expected['name']} ({live['id']})")
    declared = {ruleset["name"] for ruleset in config["rulesets"]}
    for entry in entries:
        name = entry.get("name")
        if isinstance(name, str) and name.endswith(NAME_SUFFIX) and name not in declared:
            findings.append(f"{name} ({entry.get('id')}): not declared in the reviewed config")
    for finding in findings:
        print(f"ERROR: {finding}", file=sys.stderr)
    return 1 if findings else 0


def require_live_image(ruleset_id, expected, failure):
    drifted = drifted_fields(live_ruleset(ruleset_id), expected)
    require(not drifted, f"{expected['name']} ({ruleset_id}): {failure} ({', '.join(drifted)})")


def create(expected):
    # Created disabled and verified before it can block a merge, so a payload GitHub
    # reads back differently never enforces an unreviewed image.
    staged = dict(expected, enforcement="disabled")
    created = gh_json_input("POST", f"orgs/{ORGANIZATION}/rulesets", staged)
    ruleset_id = created.get("id") if isinstance(created, dict) else None
    require(is_positive_integer(ruleset_id),
            f"{expected['name']}: create returned no positive ruleset id")
    require_live_image(ruleset_id, staged, "created ruleset reads back differently; left disabled")
    gh_json_input("PUT", f"orgs/{ORGANIZATION}/rulesets/{ruleset_id}", expected)
    require_live_image(ruleset_id, expected, "activated ruleset reads back differently")
    print(f"created: {expected['name']} ({ruleset_id})")


def apply(config):
    entries = list_organization_rulesets(ORGANIZATION)
    for expected in config["rulesets"]:
        named = [entry for entry in entries if entry.get("name") == expected["name"]]
        require(len(named) <= 1, f"{expected['name']}: {len(named)} live rulesets share the name")
        if not named:
            create(expected)
            continue
        ruleset_id = named[0].get("id")
        if drifted_fields(live_ruleset(ruleset_id), expected):
            gh_json_input("PUT", f"orgs/{ORGANIZATION}/rulesets/{ruleset_id}", expected)
            require_live_image(ruleset_id, expected, "updated ruleset reads back differently")
            print(f"updated: {expected['name']} ({ruleset_id})")
    return 0


def parse_arguments(arguments):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("render", "check", "apply"))
    parser.add_argument("--ack", default="")
    return parser.parse_args(arguments)


def main(arguments=None):
    args = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    config = read_config()
    if args.mode == "check":
        return check(config)
    if args.mode == "apply":
        require(args.ack == config["apply_acknowledgement"],
                f"explicit acknowledgement {config['apply_acknowledgement']} is required")
        return apply(config)
    print(json.dumps(config["rulesets"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
