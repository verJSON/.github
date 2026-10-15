#!/usr/bin/env python3
"""Admit an organization required-workflow ruleset's live revision as a predecessor.

A rotation has to recognize the revision it replaces, both to accept the live
preimage and to restore it after a failed write. A committed record of that
revision goes stale at every rotation, because a rotation's target is always the
merge commit of a later change (#1824). The live ruleset is the only record that
cannot lag, so the predecessor is read from it and admitted only when it is a
canonical revision (ADR 0153 2026-10-15 amendment).
"""

import re


SHA = re.compile(r"[0-9a-f]{40}")
FORWARD = frozenset({"ahead", "identical"})


def bound_workflow_sha(ruleset, require):
    rules = ruleset.get("rules") if isinstance(ruleset, dict) else None
    require(isinstance(rules, list), "live ruleset rules are malformed")
    bindings = [
        rule for rule in rules
        if isinstance(rule, dict) and rule.get("type") == "workflows"
    ]
    require(len(bindings) == 1, "live ruleset must have exactly one workflows rule")
    parameters = bindings[0].get("parameters")
    workflows = parameters.get("workflows") if isinstance(parameters, dict) else None
    require(isinstance(workflows, list) and len(workflows) == 1
            and isinstance(workflows[0], dict),
            "live ruleset must bind exactly one required workflow")
    sha = workflows[0].get("sha")
    require(isinstance(sha, str) and SHA.fullmatch(sha) is not None,
            "live ruleset binds a mutable required-workflow revision")
    return sha


def admit_predecessor(
        previous_sha, target_sha, *, compare, read_file, regenerate, workflow_path, require):
    require(compare(previous_sha, target_sha) in FORWARD,
            "live required-workflow revision is not an ancestor of the rotation target")
    workflow = read_file(workflow_path, previous_sha)
    require(regenerate(previous_sha, workflow) == workflow,
            "live required-workflow revision is not its generator's output")
