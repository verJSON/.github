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
import sysconfig
import tempfile
from urllib.parse import quote

import yaml


CANONICAL_REPOSITORY = "Verjson/.github"
SHA = re.compile(r"[0-9a-f]{40}")
FORWARD = frozenset({"ahead", "identical"})
PROTECTED_LANE = ".github/workflows/node-ci-protected.yml"
CANONICAL_PROTECTED_LANE = f"Verjson/.github/{PROTECTED_LANE}@"
# #1759, which contains #1750: the protected lane's inline interpreters start isolated
# from the candidate checkout. A required workflow must never call an earlier revision.
NODE_CI_SECURITY_FLOOR = "21eb48e713bd8304951fb7b26a900d08359af900"


def canonical_ref(ref, require):
    # Restated at each read because the value goes into a URL: a caller's guard cannot
    # vouch for a read made here.
    require(ref == "main" or (isinstance(ref, str) and SHA.fullmatch(ref) is not None),
            "canonical revision is invalid")
    return ref


def canonical_compare(base, head, *, gh_json, require):
    base = quote(canonical_ref(base, require), safe="")
    head = quote(canonical_ref(head, require), safe="")
    comparison = gh_json(f"repos/{CANONICAL_REPOSITORY}/compare/{base}...{head}")
    status = comparison.get("status") if isinstance(comparison, dict) else None
    return status if isinstance(status, str) else None


def canonical_file(path, ref, require):
    encoded_ref = quote(canonical_ref(ref, require), safe="/")
    result = subprocess.run(
        ["gh", "api", "--hostname", "github.com",
         f"repos/{CANONICAL_REPOSITORY}/contents/{path}?ref={encoded_ref}",
         "-H", "Accept: application/vnd.github.raw+json"],
        capture_output=True, check=False,
    )
    require(result.returncode == 0, f"canonical file unreadable at {ref}: {path}")
    return result.stdout


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
        # A fork-network commit can descend from the floor without ever being reviewed
        # onto protected main, and GitHub still resolves it by SHA.
        require(compare(pin, "main") in FORWARD,
                f"node-ci-protected pin {pin} is not reachable from protected main")


def admit_predecessor(
        previous_sha, target_sha, *, compare, read_file, regenerate, workflow_path, require):
    require(compare(previous_sha, target_sha) in FORWARD,
            "live required-workflow revision is not an ancestor of the rotation target")
    workflow = read_file(workflow_path, previous_sha)
    require(regenerate(previous_sha, workflow) == workflow,
            "live required-workflow revision is not its generator's output")


def audit_live_revision(
        live_sha, *, compare, read_file, workflow_path, runtime_paths, require):
    """Fail unless the live ruleset runs exactly the reviewed bytes on protected main.

    The committed generated workflow (and anything the workflow checks out at its own
    revision) is the record; a rotation is pending exactly when those bytes differ.
    """
    require(compare(live_sha, "main") in FORWARD,
            f"live ruleset binds {live_sha}, a revision outside protected main")
    for path in runtime_paths:
        require(read_file(path, live_sha) == read_file(path, "main"),
                f"rotation pending: live ruleset runs {live_sha}, whose {path} "
                "differs from protected main")
    workflow = read_file(workflow_path, live_sha)
    try:
        text = workflow.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    require_security_floor(text, compare=compare, require=require)


def generator_environment(base_prefix=sys.base_prefix, config=sysconfig.get_config_var):
    """The whole environment a historical generator gets: nothing inherited.

    A shared-libpython interpreter, such as every actions/setup-python build, cannot start
    without its library directory. That directory is derived from the running interpreter's
    own runtime prefix, never copied from the caller's LD_LIBRARY_PATH. The build-time
    LIBDIR names the build host's prefix, not the install location.
    """
    environment = {"PATH": os.defpath}
    soname = config("INSTSONAME")
    if config("Py_ENABLE_SHARED") != 1 or not isinstance(soname, str):
        return environment
    if PurePosixPath(soname).name != soname:
        return environment
    library_directory = Path(base_prefix) / "lib"
    if (library_directory / soname).is_file():
        environment["LD_LIBRARY_PATH"] = str(library_directory)
    return environment


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
                cwd=root, env=generator_environment(),
                capture_output=True, check=False, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
    if result is None or result.returncode != 0:
        detail = "" if result is None else result.stderr.decode("utf-8", "replace").strip()[-400:]
        require(False, f"historical generator failed: {detail}")
    return result.stdout
