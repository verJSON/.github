#!/usr/bin/env python3
"""The org-required arm never skips or yields to a local caller (ADR 0255 §3, F-3).

Ruleset 20722935 requires `gate-rearm.yml` itself. A required run that skipped or
yielded because the repository-local `gate re-arm` caller covered the same event would
satisfy the `workflows` rule without an organization-controlled arm, and the local
run's claim is repository-controlled. So the required invocation must take exactly the
path a caller's head run takes: no condition reads who invoked the workflow, and the
arm's invocation classification recognizes only the label and lifecycle callers.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
ARM = ROOT / ".github/workflows/gate-rearm.yml"
HEAD_ACTIONS = ("opened", "reopened", "synchronize")
REQUIRED_PATH_JOBS = ("event-policy", "app-key-policy", "validate-ai-review-key", "arm")
# Contexts that identify the invocation rather than the event: a condition on any of
# them can make the required run behave differently from a caller's run.
INVOCATION_IDENTITY = re.compile(r"\binputs\.|\bgithub\.workflow|\bgithub\.job_workflow|\bjob\.workflow")


def load():
    return yaml.safe_load(ARM.read_text(encoding="utf-8"))


def conditions(workflow):
    for job_name, job in workflow["jobs"].items():
        if "if" in job:
            yield job_name, str(job["if"])
        for step in job.get("steps") or []:
            if "if" in step:
                yield f"{job_name} / {step.get('name', step.get('id'))}", str(step["if"])


def test_every_head_action_reaches_the_required_workflow():
    types = load()[True]["pull_request_target"]["types"]
    assert set(HEAD_ACTIONS) <= set(types), types


def test_no_condition_reads_the_invocation_identity():
    offending = [(where, condition) for where, condition in conditions(load())
                 if INVOCATION_IDENTITY.search(condition)]
    assert offending == [], offending


def test_required_path_jobs_are_gated_only_by_event_admission():
    jobs = load()["jobs"]
    assert "if" not in jobs["event-policy"]
    assert jobs["app-key-policy"]["if"] == "${{ needs.event-policy.outputs.run_control_plane == 'true' }}"
    assert "if" not in jobs["validate-ai-review-key"]
    # always(): a failed key validation still reaches the arm, which completes the
    # authorization check as failed instead of the run skipping it.
    assert jobs["arm"]["if"] == "${{ always() && needs.event-policy.outputs.run_control_plane == 'true' }}"
    assert set(REQUIRED_PATH_JOBS) <= set(jobs), sorted(jobs)


def test_head_actions_are_admitted_whatever_the_label_or_title():
    script = load()["jobs"]["event-policy"]["steps"][0]["run"]
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "github-output"
        for action in HEAD_ACTIONS:
            for label, title in (("", ""), ("hold", "DO NOT MERGE: held"), ("ai-review", "ordinary")):
                output.write_text("", encoding="utf-8")
                env = dict(os.environ, EVENT_ACTION=action, EVENT_LABEL=label, EVENT_TITLE_CHANGED="true",
                           EVENT_OLD_TITLE=title, EVENT_NEW_TITLE=title, GITHUB_OUTPUT=str(output))
                completed = subprocess.run(["bash", "-c", script], env=env, check=False,
                                           capture_output=True, text=True)
                assert completed.returncode == 0, (action, completed.stderr)
                outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
                assert outputs["run_control_plane"] == "true", (action, label, title)


def test_the_arm_classifies_only_the_label_and_lifecycle_callers():
    arm = load()["jobs"]["arm"]
    script = next(step["run"] for step in arm["steps"] if step.get("id") == "arm")
    block = re.search(r'case "\$WORKFLOW_REF" in\n(.*?)\n\s*esac', script, re.S)
    assert block, "the arm no longer classifies its invocation by WORKFLOW_REF"
    patterns = re.findall(r'^\s*("[^\n]*?)\)\s*$', block[1], re.M)
    assert patterns == [
        '"$TARGET_REPO/.github/workflows/ai-review-label-rearm.yml@refs/heads/$DEFAULT_BRANCH"',
        '"$TARGET_REPO/.github/workflows/ai-review-lifecycle-rearm.yml@refs/heads/$DEFAULT_BRANCH"',
    ], patterns


def test_a_running_required_arm_is_never_cancelled_by_a_later_family_run():
    concurrency = load()["concurrency"]
    assert concurrency == {"group": "ai-review-arm-${{ github.event.pull_request.number }}",
                           "cancel-in-progress": False}, concurrency


def main():
    tests = [(name, value) for name, value in globals().items() if name.startswith("test_") and callable(value)]
    failures = 0
    for name, test in tests:
        try:
            test()
        except Exception as error:  # noqa: BLE001 - report every failing behavior, not only the first
            failures += 1
            print(f"FAIL {name}: {type(error).__name__}: {error}")
        else:
            print(f"ok   {name}")
    if failures:
        raise SystemExit(f"{failures} of {len(tests)} required-arm behaviors failed")
    print(f"all {len(tests)} required-arm behaviors passed")


if __name__ == "__main__":
    main()
