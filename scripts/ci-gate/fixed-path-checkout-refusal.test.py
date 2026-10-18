#!/usr/bin/env python3
"""A fixed-path checkout refuses a symlink planted at its path.

actions/checkout at the pinned v7 lists the `path:` directory with readdir and
removes every entry when that directory is not already the requested
repository. readdir follows a symlink at the path itself, so a committed link
makes the checkout empty the link's target and then clone into it. When an
earlier step put caller- or pull-request-authored content in the workspace, or
a persistent runner kept it from an earlier job, that target can be runner
state outside the workspace. Each nested entry is removed with a recursive
`rm` that does not follow links, so a real directory at the path is harmless,
and on a persistent runner it is expected residue from the previous run.

For every protected site this test plants a symlink at the checkout path that
points at a stand-in for runner state. It then executes the step immediately
before the checkout, as that step's own `run:` text, once with the link and
once without. A step counts as the refusal only when it fails with the link and
succeeds without it, and succeeds with a real directory there too. It then
applies Actions' step-condition rules to decide whether the checkout still
runs, and if it does, emulates the checkout's readdir-and-remove. The runner
state must survive.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
RUNNER_STATE = "runner state\n"

# Sites this test must find and prove protected. Listing them guards against a
# scanner that silently finds nothing.
REQUIRED_SITES = (
    ("changelog-validate.yml", "validate", ".changelog-contract"),
)


@dataclass(frozen=True)
class Site:
    workflow: str
    job: str
    index: int
    path: str
    steps: tuple

    @property
    def label(self):
        return f"{self.workflow} {self.job} {self.path}"

    @property
    def checkout(self):
        return self.steps[self.index]


def fixed_path_checkouts():
    """Every actions/checkout step whose path is a literal, by workflow and job."""
    sites = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        document = yaml.safe_load(workflow.read_text())
        for job_name, job in (document.get("jobs") or {}).items():
            steps = tuple((job or {}).get("steps") or ())
            for index, step in enumerate(steps):
                uses = str(step.get("uses", ""))
                path = (step.get("with") or {}).get("path")
                if not uses.startswith("actions/checkout@") or not isinstance(path, str):
                    continue
                if "${{" in path:
                    continue
                sites.append(Site(workflow.name, job_name, index, path, steps))
    return sites


def condition(step):
    text = str(step.get("if", "")).strip()
    match = re.fullmatch(r"\$\{\{(.*)\}\}", text, re.S)
    return " ".join((match.group(1) if match else text).split())


STATUS_FUNCTION = re.compile(r"\b(always|failure|cancelled)\(\)")


def runs_after_failure(step):
    """Whether Actions would still start this step after an earlier step failed."""
    return bool(STATUS_FUNCTION.search(condition(step)))


def requires_success(step, step_id):
    return f"steps.{step_id}.outcome == 'success'" in condition(step)


def refusal_candidate(step, path):
    """A local shell step that names the path; anything else is never executed."""
    run = step.get("run")
    return (
        isinstance(run, str)
        and "uses" not in step
        and "env" not in step
        and "${{" not in run
        and path in run
    )


def run_shell(step, workspace):
    shell = step.get("shell")
    assert shell in (None, "bash"), f"{step.get('name')}: unsupported shell {shell}"
    with tempfile.TemporaryDirectory(prefix="fixed-path-refusal-step-") as script_dir:
        script = Path(script_dir) / "step.sh"
        script.write_text(step["run"])
        command = ["bash", "-e", str(script)] if shell is None else [
            "bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)]
        result = subprocess.run(
            command,
            cwd=workspace,
            env={"PATH": os.environ["PATH"], "GITHUB_WORKSPACE": str(workspace)},
            text=True,
            capture_output=True,
        )
    return result.returncode, result.stdout + result.stderr


def emulate_checkout(workspace, path):
    """actions/checkout v7 prepareExistingDirectory for a non-matching directory."""
    destination = workspace / path
    if destination.is_dir():
        for entry in os.listdir(destination):
            target = destination / entry
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
    else:
        destination.mkdir(parents=True)
    (destination / "checked-out").write_text("canonical\n")


def plant(case, path, kind):
    workspace = case / "workspace"
    workspace.mkdir()
    state = case / "runner-state"
    state.mkdir()
    (state / "keep").write_text(RUNNER_STATE)
    if kind == "symlink":
        (workspace / path).symlink_to(state)
    elif kind == "directory":
        (workspace / path).mkdir()
        (workspace / path / "residue").write_text("previous run\n")
    return workspace, state


def checkout_runs_after_refusal(site, guard):
    checkout = site.checkout
    guard_id = guard.get("id")
    if guard_id and requires_success(checkout, guard_id):
        return False
    return bool(guard.get("continue-on-error")) or runs_after_failure(checkout)


def guard_failures(site, root):
    """Why a planted symlink still reaches this checkout; empty when it cannot."""
    failures = []
    guard = site.steps[site.index - 1] if site.index else None
    is_refusal = False
    if guard is not None and refusal_candidate(guard, site.path):
        outcomes = {}
        for kind in ("symlink", "absent", "directory"):
            case = root / f"{kind}-probe"
            case.mkdir()
            workspace, _ = plant(case, site.path, kind)
            outcomes[kind] = run_shell(guard, workspace)
        is_refusal = outcomes["symlink"][0] != 0 and outcomes["absent"][0] == 0
        if is_refusal and outcomes["directory"][0] != 0:
            failures.append(
                f"{site.label}: the refusal also rejects a real directory, which a "
                f"persistent runner keeps from its previous run: {outcomes['directory'][1]}"
            )
    if not is_refusal:
        guard = None
    checkout_runs = guard is None or checkout_runs_after_refusal(site, guard)
    if guard is not None and condition(guard) != condition(site.checkout).replace(
        f" && steps.{guard.get('id')}.outcome == 'success'", ""
    ):
        failures.append(f"{site.label}: the refusal and the checkout run under different conditions")
    case = root / "symlink-checkout"
    case.mkdir()
    workspace, state = plant(case, site.path, "symlink")
    if checkout_runs:
        emulate_checkout(workspace, site.path)
    if not (state / "keep").exists() or (state / "keep").read_text() != RUNNER_STATE:
        failures.append(
            f"{site.label}: no refusal stops the checkout, which followed a planted "
            "symlink and emptied its target"
        )
    return failures


def main():
    sites = {(site.workflow, site.job, site.path): site for site in fixed_path_checkouts()}
    failures = []
    for key in REQUIRED_SITES:
        site = sites.get(key)
        if site is None:
            failures.append(f"{' '.join(key)}: required fixed-path checkout not found")
            continue
        with tempfile.TemporaryDirectory(prefix="fixed-path-refusal-") as root:
            site_failures = guard_failures(site, Path(root))
        failures += site_failures
        if not site_failures:
            print(f"ok - {site.label} refuses a planted symlink")
    for failure in failures:
        print(f"FAIL - {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
