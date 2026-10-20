#!/usr/bin/env python3
"""Shared primitives for tools that manage organization rulesets as reviewed config.

`authn-type-surface-ruleset.py` and `cli-projects-package-surface-ruleset.py`
predate this module and still carry private copies of these helpers.
"""
import json
import subprocess


class ContractError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def load_json(text, source):
    """Parse JSON, rejecting a duplicated key instead of silently keeping the last."""
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, f"{source} contains duplicate key {key}")
            value[key] = item
        return value

    try:
        return json.loads(text, object_pairs_hook=unique_object)
    except json.JSONDecodeError as error:
        raise ContractError(f"{source} is invalid JSON: {error}") from None


def gh_json(*arguments):
    result = subprocess.run(
        ["gh", "api", "--hostname", "github.com", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ContractError(f"GitHub API request failed: {' '.join(arguments)}")
    return load_json(result.stdout, f"GitHub API response for {' '.join(arguments)}")


MUTABLE_FIELDS = ("name", "target", "enforcement", "bypass_actors", "conditions", "rules")


def mutable_ruleset(value):
    """The fields a ruleset create or update writes, as the API reads them back."""
    require(isinstance(value, dict) and set(MUTABLE_FIELDS) <= set(value),
            "live ruleset is missing required fields")
    return {key: value[key] for key in MUTABLE_FIELDS}


def list_organization_rulesets(organization):
    listing = gh_json("--paginate", "--slurp", f"orgs/{organization}/rulesets?per_page=100")
    require(isinstance(listing, list) and listing
            and all(isinstance(page, list) for page in listing),
            "organization ruleset listing is malformed")
    entries = [entry for page in listing for entry in page]
    require(entries and all(isinstance(entry, dict) for entry in entries),
            "organization ruleset listing is empty or malformed")
    return entries
