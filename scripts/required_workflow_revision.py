#!/usr/bin/env python3
"""Admit an organization required-workflow ruleset's live revision as a predecessor.

A rotation has to recognize the revision it replaces, both to accept the live
preimage and to restore it after a failed write. A committed record of that
revision goes stale at every rotation, because a rotation's target is always the
merge commit of a later change (#1824). The live ruleset is the only record that
cannot lag, so the predecessor is read from it and admitted only when it is a
canonical revision (ADR 0153 2026-10-15 amendment).
"""

import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

import yaml


SHA = re.compile(r"[0-9a-f]{40}")
FORWARD = frozenset({"ahead", "identical"})
PROTECTED_LANE = ".github/workflows/node-ci-protected.yml"
CANONICAL_PROTECTED_LANE = f"Verjson/.github/{PROTECTED_LANE}@"
# #1759, which contains #1750: the protected lane's inline interpreters start isolated
# from the candidate checkout. A required workflow must never call an earlier revision.
NODE_CI_SECURITY_FLOOR = "21eb48e713bd8304951fb7b26a900d08359af900"


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


def protected_lane_pins(workflow_text, require):
    try:
        document = yaml.safe_load(workflow_text)
    except yaml.YAMLError:
        document = None
    jobs = document.get("jobs") if isinstance(document, dict) else None
    require(isinstance(jobs, dict), "required workflow jobs are malformed")
    pins = set()
    for job in jobs.values():
        require(isinstance(job, dict), "required workflow job is malformed")
        uses = job.get("uses")
        if uses is None:
            continue
        require(isinstance(uses, str), "required workflow job uses is malformed")
        if PROTECTED_LANE not in uses:
            continue
        pin = uses.removeprefix(CANONICAL_PROTECTED_LANE)
        require(pin != uses and SHA.fullmatch(pin) is not None,
                f"node-ci-protected call is not canonical and immutable: {uses!r}")
        pins.add(pin)
    require(pins, "required workflow calls no node-ci-protected revision")
    return sorted(pins)


def require_security_floor(workflow_text, *, compare, require):
    for pin in protected_lane_pins(workflow_text, require):
        require(compare(NODE_CI_SECURITY_FLOOR, pin) in FORWARD,
                f"node-ci-protected pin {pin} predates the protected-lane security floor")


def admit_predecessor(
        previous_sha, target_sha, *, compare, read_file, regenerate, workflow_path, require):
    require(compare(previous_sha, target_sha) in FORWARD,
            "live required-workflow revision is not an ancestor of the rotation target")
    workflow = read_file(workflow_path, previous_sha)
    require(regenerate(previous_sha, workflow) == workflow,
            "live required-workflow revision is not its generator's output")


def run_generator(files, argv, require):
    """Run a historical generator from its own bytes, with no inherited environment.

    The bytes come from a revision already proven to be on protected main, but the
    caller may hold an organization-admin token, so none of its environment, user
    site, or working tree reaches the generator.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for relative, content in files.items():
            parts = PurePosixPath(relative).parts
            require(bool(parts) and not PurePosixPath(relative).is_absolute()
                    and ".." not in parts,
                    f"generator input escapes its sandbox: {relative!r}")
            path = root.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        try:
            result = subprocess.run(
                [sys.executable, "-E", "-s", *argv],
                cwd=root, env={"PATH": os.defpath},
                capture_output=True, check=False, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
    require(result is not None and result.returncode == 0, "historical generator failed")
    return result.stdout
