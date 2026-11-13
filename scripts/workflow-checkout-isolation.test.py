#!/usr/bin/env python3
"""Conformance tests for checkout isolation on reusable runner workspaces."""

from copy import deepcopy
from pathlib import Path
import re
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
UNIQUE_SUFFIX = "${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}"


class ContractError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def workflow_on(document: dict) -> object:
    keys = [key for key in document if key is True or key == "on"]
    require(len(keys) == 1, "workflow must have exactly one on key")
    return document[keys[0]]


def checkout_steps(job: object) -> list[dict]:
    if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
        return []
    return [
        step
        for step in job["steps"]
        if isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/checkout@")
    ]


def validate_cleanup(job: dict, checkout_path: str, location: str) -> None:
    steps = job.get("steps")
    require(isinstance(steps, list) and steps, f"{location} must have steps")
    cleanup = steps[-1]
    require(isinstance(cleanup, dict), f"{location} cleanup must be a mapping")
    require(cleanup.get("if") == "${{ always() }}", f"{location} cleanup must always run")
    require(
        cleanup.get("working-directory") == "${{ github.workspace }}",
        f"{location} cleanup must run from the workspace root",
    )
    require(
        cleanup.get("run") == f'rm -rf "{checkout_path}"',
        f"{location} cleanup must remove only its isolated checkout",
    )


def validate_run_step_directories(job: dict, checkout_path: str, location: str) -> None:
    steps = job.get("steps")
    require(isinstance(steps, list) and steps, f"{location} must have steps")
    for index, step in enumerate(steps[:-1]):
        if not isinstance(step, dict) or "run" not in step:
            continue
        working_directory = step.get("working-directory")
        require(
            working_directory in (None, checkout_path),
            f"{location} business run step {index} must stay in the isolated checkout",
        )


def validate_sparse_checkouts(documents: dict[Path, dict]) -> None:
    for path, document in documents.items():
        for job_name, job in document.get("jobs", {}).items():
            for step in checkout_steps(job):
                checkout_with = step.get("with")
                if isinstance(checkout_with, dict) and "sparse-checkout" in checkout_with:
                    checkout_path = checkout_with.get("path")
                    require(
                        isinstance(checkout_path, str) and checkout_path.strip(),
                        f"{path.name}: jobs.{job_name} sparse checkout must set path",
                    )
                    if checkout_path.endswith(UNIQUE_SUFFIX):
                        validate_cleanup(
                            job,
                            checkout_path,
                            f"{path.name}: jobs.{job_name}",
                        )
                        validate_run_step_directories(
                            job,
                            checkout_path,
                            f"{path.name}: jobs.{job_name}",
                        )


def step_strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from step_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from step_strings(item)


def sparse_patterns(checkout_with: dict, location: str) -> list[str]:
    listed = checkout_with.get("sparse-checkout")
    require(isinstance(listed, str), f"{location} sparse-checkout must be a literal list")
    patterns = [line.strip() for line in listed.splitlines() if line.strip()]
    require(patterns, f"{location} sparse-checkout lists nothing")
    for pattern in patterns:
        require(
            not re.search(r"[*?\[\]!]", pattern),
            f"{location} sparse-checkout pattern {pattern!r} is not a literal path this check can resolve",
        )
    return patterns


def validate_sparse_trust_references(documents: dict[Path, dict]) -> int:
    """A file a job reads from a fixed-path sparse checkout must be in that checkout.

    A fixed path (no expression) marks a trusted-revision tree that later steps execute
    from, such as `.gate-trust`. A file missing from its sparse list is simply absent at
    run time, so the step fails on every run while every stubbed suite stays green.
    Returns how many references were checked, so a caller can prove the check is not
    vacuous.
    """
    def fixed_sparse_roots(job: object, location: str) -> dict[str, list[str] | None]:
        roots: dict[str, list[str] | None] = {}
        for step in checkout_steps(job):
            checkout_with = step.get("with")
            root = checkout_with.get("path") if isinstance(checkout_with, dict) else None
            if not isinstance(root, str) or "${{" in root:
                continue
            require(root not in roots, f"{location} checks out {root} more than once")
            # A full checkout of the root holds every file; None marks it unrestricted.
            roots[root] = (
                sparse_patterns(checkout_with, location) if "sparse-checkout" in checkout_with else None
            )
        return roots

    trust_roots = {
        root
        for path, document in documents.items()
        for job_name, job in document.get("jobs", {}).items()
        for root, patterns in fixed_sparse_roots(job, f"{path.name}: jobs.{job_name}").items()
        if patterns is not None
    }
    checked = 0
    for path, document in documents.items():
        for job_name, job in document.get("jobs", {}).items():
            if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
                continue
            location = f"{path.name}: jobs.{job_name}"
            checked_out = fixed_sparse_roots(job, location)
            for step in job["steps"]:
                if not isinstance(step, dict) or "uses" in step and step["uses"].startswith("actions/checkout@"):
                    continue
                for text in step_strings({key: step.get(key) for key in ("run", "env", "with")}):
                    for root in trust_roots:
                        for match in re.finditer(rf"(?<![\w.-]){re.escape(root)}/([\w./-]+)", text):
                            reference = match.group(1).rstrip(".")
                            checked += 1
                            require(
                                root in checked_out,
                                f"{location} reads {root}/{reference} without checking {root} out",
                            )
                            patterns = checked_out[root]
                            require(
                                patterns is None
                                or any(
                                    reference == pattern or reference.startswith(pattern.rstrip("/") + "/")
                                    for pattern in patterns
                                ),
                                f"{location} reads {root}/{reference}, which its sparse checkout does not include",
                            )
    return checked


def validate_scheduled_checkouts(documents: dict[Path, dict]) -> None:
    scheduled_count = 0
    for path, document in documents.items():
        triggers = workflow_on(document)
        if not isinstance(triggers, dict) or "schedule" not in triggers:
            continue
        scheduled_count += 1
        for job_name, job in document.get("jobs", {}).items():
            checkouts = checkout_steps(job)
            if not checkouts:
                continue
            strategy = job.get("strategy") if isinstance(job, dict) else None
            require(
                not isinstance(strategy, dict) or "matrix" not in strategy,
                f"{path.name}: jobs.{job_name} matrix children would share one checkout path",
            )
            require(
                len(checkouts) == 1,
                f"{path.name}: jobs.{job_name} must have one unambiguous checkout root",
            )
            checkout_with = checkouts[0].get("with")
            require(
                isinstance(checkout_with, dict),
                f"{path.name}: jobs.{job_name} checkout must configure path",
            )
            checkout_path = checkout_with.get("path")
            require(
                isinstance(checkout_path, str) and checkout_path.endswith(UNIQUE_SUFFIX),
                f"{path.name}: jobs.{job_name} checkout path must be unique per run, attempt, and job",
            )
            working_directory = (
                job.get("defaults", {}).get("run", {}).get("working-directory")
                if isinstance(job, dict)
                else None
            )
            require(
                working_directory == checkout_path,
                f"{path.name}: jobs.{job_name} run steps must use the isolated checkout path",
            )
            validate_cleanup(job, checkout_path, f"{path.name}: jobs.{job_name}")
            validate_run_step_directories(job, checkout_path, f"{path.name}: jobs.{job_name}")
    require(scheduled_count > 0, "no scheduled workflows were examined")


def load_workflows() -> dict[Path, dict]:
    documents = {}
    for path in sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml"))):
        document = yaml.safe_load(path.read_text())
        require(isinstance(document, dict), f"{path.name}: workflow root must be a mapping")
        documents[path] = document
    return documents


def expect_invalid(label: str, validator, documents: dict[Path, dict]) -> int:
    try:
        validator(documents)
    except ContractError:
        print(f"PASS - {label}")
        return 0
    print(f"FAIL - {label}")
    return 1


def main() -> int:
    documents = load_workflows()
    try:
        validate_sparse_checkouts(documents)
        validate_scheduled_checkouts(documents)
        trust_references = validate_sparse_trust_references(
            {WORKFLOWS / "ai-privileged-merge.yml": documents[WORKFLOWS / "ai-privileged-merge.yml"]}
        )
        require(trust_references >= 4, "ai-privileged-merge.yml trust references were not examined")
        validate_sparse_trust_references(documents)
    except ContractError as error:
        print(f"FAIL - repository checkout isolation: {error}")
        return 1

    failures = 0
    trust_mutation = deepcopy(documents)
    privileged_merge = trust_mutation[WORKFLOWS / "ai-privileged-merge.yml"]
    trust_checkout = checkout_steps(privileged_merge["jobs"]["privileged_merge"])[0]["with"]
    trust_checkout["sparse-checkout"] = trust_checkout["sparse-checkout"].replace(
        "scripts/ci-gate/independent-review-receipt.sh\n", ""
    )
    failures += expect_invalid(
        "a trust file read from outside its sparse checkout is rejected",
        validate_sparse_trust_references,
        trust_mutation,
    )

    sparse_mutation = deepcopy(documents)
    post_merge = sparse_mutation[WORKFLOWS / "ai-post-merge.yml"]
    del checkout_steps(post_merge["jobs"]["reconcile"])[0]["with"]["path"]
    failures += expect_invalid(
        "sparse checkout without path is rejected",
        validate_sparse_checkouts,
        sparse_mutation,
    )

    scheduled_mutation = deepcopy(documents)
    watchdog = scheduled_mutation[WORKFLOWS / "fleet-watchdog.yml"]
    checkout_steps(watchdog["jobs"]["watchdog"])[0]["with"]["path"] = ".shared-source"
    failures += expect_invalid(
        "scheduled checkout without a unique path is rejected",
        validate_scheduled_checkouts,
        scheduled_mutation,
    )

    matrix_mutation = deepcopy(documents)
    watchdog = matrix_mutation[WORKFLOWS / "fleet-watchdog.yml"]
    watchdog["jobs"]["watchdog"]["strategy"] = {"matrix": {"shard": [1, 2]}}
    failures += expect_invalid(
        "scheduled checkout matrix without a child discriminator is rejected",
        validate_scheduled_checkouts,
        matrix_mutation,
    )

    working_directory_mutation = deepcopy(documents)
    post_merge = working_directory_mutation[WORKFLOWS / "ai-post-merge.yml"]
    business_step = next(step for step in post_merge["jobs"]["reconcile"]["steps"] if "run" in step)
    business_step["working-directory"] = "${{ github.workspace }}"
    failures += expect_invalid(
        "business run step outside its isolated checkout is rejected",
        validate_sparse_checkouts,
        working_directory_mutation,
    )

    if failures:
        return 1
    print("PASS - repository checkout isolation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
