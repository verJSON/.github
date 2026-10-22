#!/usr/bin/env python3
"""Keep sensitive-path required-reviewer org rulesets equal to reviewed config (#1816)."""
import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys

# `python3 -I` does not put the script directory on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from github_identity import same_organization, same_repository  # noqa: E402
from org_ruleset_api import (  # noqa: E402
    ContractError, drifted_fields, gh_json, gh_json_input, is_positive_integer,
    list_organization_rulesets, load_json, require, same_json,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/required-reviewer-rulesets.json"
ORGANIZATION = "Verjson"
ISSUE = 1816
APPLY_ACKNOWLEDGEMENT = f"APPLY-REQUIRED-REVIEWERS-{ISSUE}"
NAME_SUFFIX = "-sensitive-path-admin-review"
# One literal repository: `repository_name` also accepts fnmatch globs and `~ALL`,
# either of which would put the admin requirement on repositories nobody reviewed.
REPOSITORY_NAME = re.compile(r"[A-Za-z0-9._-]+")
# @admins has three members and an author cannot approve their own pull request, so
# requiring two approvals would leave an admin-authored change with exactly one
# possible approver set and stall it whenever a member is unavailable.
MINIMUM_APPROVALS = 1
ADMINS_TEAM = {"type": "Team", "id": 17813010}
# ADR 0210 (2026-10-19 amendment): only the release-authorization App may bypass. The
# merge App, Renovate, and organization owners stay subject to the admin approval.
RELEASE_BYPASS = [{"actor_type": "Integration", "actor_id": 4583107, "bypass_mode": "always"}]
RULESET_KEYS = {"name", "target", "enforcement", "conditions", "bypass_actors", "rules"}
# ADR 0210 (2026-10-22 amendment): an explicit, dated suspension is the only way a
# reviewed ruleset may be live-disabled. The declared `enforcement` stays "active" (the
# state to return to); the declaration makes the audit expect "disabled" meanwhile.
SUSPENSION_KEYS = {"reason", "since", "reenable_when"}
SUSPENSION_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
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


def validate_file_patterns(patterns, location):
    require(
        isinstance(patterns, list) and patterns
        and all(isinstance(pattern, str) and pattern.strip() == pattern and pattern
                and not pattern.startswith("/") for pattern in patterns)
        and len(set(patterns)) == len(patterns),
        f"{location}: file_patterns must be a non-empty list of distinct "
        "repository-relative patterns",
    )


def valid_suspension_date(value):
    if SUSPENSION_DATE.fullmatch(value) is None:
        return False
    try:
        return date.fromisoformat(value) <= date.today()
    except ValueError:
        return False


def validate_suspension(suspension, location):
    require(
        isinstance(suspension, dict) and set(suspension) == SUSPENSION_KEYS
        and all(isinstance(value, str) and value.strip() and value.isprintable()
                for value in suspension.values())
        and valid_suspension_date(suspension["since"]),
        f"{location}: suspended must hold non-blank reason and reenable_when and a "
        "YYYY-MM-DD since",
    )


def expected_image(ruleset):
    """The image GitHub must hold: the declaration minus `suspended`, disabled if suspended."""
    image = {key: value for key, value in ruleset.items() if key != "suspended"}
    if "suspended" in ruleset:
        image["enforcement"] = "disabled"
    return image


def validate_ruleset(ruleset, location):
    require(isinstance(ruleset, dict)
            and set(ruleset) - {"suspended"} == RULESET_KEYS,
            f"{location}: ruleset keys drifted")
    if "suspended" in ruleset:
        validate_suspension(ruleset["suspended"], location)
    require(ruleset["target"] == "branch" and ruleset["enforcement"] == "active",
            f"{location}: must be an active branch ruleset (declare a suspension instead "
            "of disabling)")
    repository_name = ruleset["conditions"].get("repository_name") \
        if isinstance(ruleset["conditions"], dict) else None
    repositories = repository_name.get("include") if isinstance(repository_name, dict) else None
    repository = repositories[0] if isinstance(repositories, list) and len(repositories) == 1 else None
    require(
        isinstance(repository, str) and same_json(ruleset["conditions"], {
            "ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []},
            "repository_name": {"include": [repository], "exclude": [], "protected": True},
        }),
        f"{location}: conditions must target exactly one protected repository's default branch",
    )
    require(REPOSITORY_NAME.fullmatch(repository) is not None,
            f"{location}: repository must be one literal repository name")
    require(ruleset["name"] == f"{repository}{NAME_SUFFIX}",
            f"{location}: name must be <repository>{NAME_SUFFIX}")
    require(same_json(ruleset["bypass_actors"], RELEASE_BYPASS),
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
    require(same_json(reviewer["reviewer"], ADMINS_TEAM),
            f"{location}: reviewer must be the admins team")
    require(same_json(reviewer["minimum_approvals"], MINIMUM_APPROVALS),
            f"{location}: minimum_approvals must be {MINIMUM_APPROVALS}")
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


def target_repository(ruleset):
    return ruleset["conditions"]["repository_name"]["include"][0]


def verify_targets(config):
    """Refuse a target that is not a live repository: a typo would enforce nothing."""
    for ruleset in config["rulesets"]:
        repository = target_repository(ruleset)
        live = gh_json(f"repos/{ORGANIZATION}/{repository}")
        require(isinstance(live, dict)
                and same_repository(live.get("full_name"), f"{ORGANIZATION}/{repository}"),
                f"{ruleset['name']}: repository {repository} identity drifted")
        require(live.get("archived") is False,
                f"{ruleset['name']}: repository {repository} is archived")
        require(live.get("default_branch") == "main",
                f"{ruleset['name']}: repository {repository} default branch is not main")


def report_suspensions(config):
    """Name every declared suspension on every run so a disabled control is not forgotten."""
    for ruleset in config["rulesets"]:
        suspension = ruleset.get("suspended")
        if suspension:
            print(f"SUSPENDED: {ruleset['name']} since {suspension['since']}: "
                  f"{suspension['reason']} Re-enable when: {suspension['reenable_when']}")


def check(config):
    report_suspensions(config)
    verify_targets(config)
    entries = list_organization_rulesets(ORGANIZATION)
    findings = []
    for expected in map(expected_image, config["rulesets"]):
        named = [entry for entry in entries if entry["name"] == expected["name"]]
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
        if entry["name"].endswith(NAME_SUFFIX) and entry["name"] not in declared:
            findings.append(f"{entry['name']} ({entry['id']}): not declared in the reviewed config")
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
    activate_verified(ruleset_id, expected, staged)
    print(f"created: {expected['name']} ({ruleset_id})")


def activate_disabled(ruleset_id, expected):
    """Bring a non-active ruleset (such as a failed create's leftover) to the reviewed image."""
    staged = dict(expected, enforcement="disabled")
    gh_json_input("PUT", f"orgs/{ORGANIZATION}/rulesets/{ruleset_id}", staged)
    require_live_image(ruleset_id, staged, "staged ruleset reads back differently; left disabled")
    activate_verified(ruleset_id, expected, staged)
    print(f"activated: {expected['name']} ({ruleset_id})")


def activate_verified(ruleset_id, expected, staged):
    """Activate a verified disabled ruleset, restoring that image if activation reads back wrong."""
    try:
        gh_json_input("PUT", f"orgs/{ORGANIZATION}/rulesets/{ruleset_id}", expected)
        require_live_image(ruleset_id, expected, "activated ruleset reads back differently")
    except ContractError as error:
        restore_disabled(ruleset_id, staged, error)


def restore_disabled(ruleset_id, staged, cause):
    """Return a ruleset whose activation could not be verified to its verified disabled image."""
    try:
        gh_json_input("PUT", f"orgs/{ORGANIZATION}/rulesets/{ruleset_id}", staged)
        require_live_image(ruleset_id, staged, "disabled image reads back differently")
    except ContractError as error:
        raise ContractError(
            f"{cause}; disable rollback could not be verified ({error}). Ruleset "
            f"{ruleset_id} may still be ACTIVE with an unverified image: run check to see "
            "its live state; re-running apply rewrites it in place as an active drifted ruleset"
        ) from None
    raise ContractError(f"{cause}; ruleset restored and verified disabled")


def plan(config):
    """Resolve every ruleset's action from live state before anything is written."""
    verify_targets(config)
    entries = list_organization_rulesets(ORGANIZATION)
    actions = []
    for expected in map(expected_image, config["rulesets"]):
        named = [entry for entry in entries if entry["name"] == expected["name"]]
        require(len(named) <= 1, f"{expected['name']}: {len(named)} live rulesets share the name")
        if not named:
            actions.append(("create", expected, None))
            continue
        ruleset_id = named[0]["id"]
        live = live_ruleset(ruleset_id)
        if not drifted_fields(live, expected):
            actions.append(("unchanged", expected, ruleset_id))
        elif expected["enforcement"] == "active" and live.get("enforcement") != "active":
            actions.append(("activate", expected, ruleset_id))
        else:
            # Already enforcing: rewritten in place, without rollback, because its
            # prior image is drifted and so not a state worth restoring (ADR 0210).
            actions.append(("update", expected, ruleset_id))
    return actions


def apply(config):
    for action, expected, ruleset_id in plan(config):
        if action == "create":
            create(expected)
        elif action == "activate":
            activate_disabled(ruleset_id, expected)
        elif action == "update":
            gh_json_input("PUT", f"orgs/{ORGANIZATION}/rulesets/{ruleset_id}", expected)
            require_live_image(ruleset_id, expected, "updated ruleset reads back differently")
            print(f"updated: {expected['name']} ({ruleset_id})")
        else:
            print(f"unchanged: {expected['name']} ({ruleset_id})")
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
    print(json.dumps([expected_image(ruleset) for ruleset in config["rulesets"]],
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
