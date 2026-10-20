#!/usr/bin/env python3
"""Shared primitives for tools that manage organization rulesets as reviewed config.

`authn-type-surface-ruleset.py` and `cli-projects-package-surface-ruleset.py`
predate this module and still carry private copies of these helpers.
"""
import json


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
