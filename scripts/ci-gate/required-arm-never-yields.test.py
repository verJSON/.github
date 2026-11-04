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
# them can make the required run behave differently from a caller's run. Property
# access is matched in both the dot and the index form (`github['workflow_ref']`).
PROPERTY = r"""(?:\.|\[['"])"""
INVOCATION_IDENTITY = re.compile(
    rf"\binputs{PROPERTY}|\bgithub{PROPERTY}(?:job_)?workflow|\bjob{PROPERTY}workflow")


def load():
    return yaml.safe_load(ARM.read_text(encoding="utf-8"))


ENV_READ = re.compile(rf"\benv{PROPERTY}([A-Za-z_][A-Za-z0-9_]*)")


def identity_env_names(*env_maps):
    """Names an env map binds to an invocation-identity context."""
    return {name for env in env_maps for name, value in (env or {}).items()
            if INVOCATION_IDENTITY.search(str(value))}


def conditions(workflow):
    """(where, condition, env names in scope that carry invocation identity)."""
    workflow_identity = identity_env_names(workflow.get("env"))
    for job_name, job in workflow["jobs"].items():
        job_identity = workflow_identity | identity_env_names(job.get("env"))
        if "if" in job:
            yield job_name, str(job["if"]), job_identity
        for step in job.get("steps") or []:
            if "if" in step:
                yield (f"{job_name} / {step.get('name', step.get('id'))}", str(step["if"]),
                       job_identity | identity_env_names(step.get("env")))


def reads_invocation_identity(condition, identity_names):
    return bool(INVOCATION_IDENTITY.search(condition)) or any(
        name in identity_names for name in ENV_READ.findall(condition))


def test_an_env_read_counts_as_identity_only_when_its_name_is_bound_to_identity():
    assert reads_invocation_identity("${{ env.WORKFLOW_REF != '' }}", {"WORKFLOW_REF"}) is True
    assert reads_invocation_identity("${{ env.EVENT_ACTION != '' }}", {"WORKFLOW_REF"}) is False


def test_index_syntax_property_access_counts_as_identity():
    assert reads_invocation_identity("${{ env['WORKFLOW_REF'] != '' }}", {"WORKFLOW_REF"}) is True
    assert reads_invocation_identity("${{ github['workflow_ref'] != '' }}", set()) is True
    assert reads_invocation_identity('${{ inputs["ai_review_environment"] == \'x\' }}', set()) is True


def test_every_head_action_reaches_the_required_workflow():
    types = load()[True]["pull_request_target"]["types"]
    assert set(HEAD_ACTIONS) <= set(types), types


def test_no_condition_reads_the_invocation_identity():
    offending = [(where, condition) for where, condition, identity in conditions(load())
                 if reads_invocation_identity(condition, identity)]
    assert offending == [], offending


def test_the_identity_scan_sees_env_names_bound_to_identity():
    # Guards the scan itself: the arm binds both kinds of identity into env, so a
    # condition reading either name would be a yield even without a direct context.
    workflow = load()
    arm_identity = identity_env_names(workflow.get("env"), workflow["jobs"]["arm"].get("env"))
    assert {"WORKFLOW_REF", "AI_REVIEW_ENVIRONMENT"} <= arm_identity, sorted(arm_identity)
    watchdog_identity = identity_env_names(workflow["jobs"]["runner-acquisition-watchdog"].get("env"))
    assert {"ARM_WORKFLOW_REF", "ARM_CALLER_ENVIRONMENT"} <= watchdog_identity, sorted(watchdog_identity)


def test_required_path_jobs_are_gated_only_by_event_admission():
    jobs = load()["jobs"]
    assert "if" not in jobs["event-policy"]
    assert jobs["app-key-policy"]["if"] == "${{ needs.event-policy.outputs.run_control_plane == 'true' }}"
    assert "if" not in jobs["validate-ai-review-key"]
    # always(): a failed key validation still reaches the arm, which completes the
    # authorization check as failed instead of the run skipping it.
    assert jobs["arm"]["if"] == "${{ always() && needs.event-policy.outputs.run_control_plane == 'true' }}"
    assert set(REQUIRED_PATH_JOBS) <= set(jobs), sorted(jobs)


def test_event_policy_admits_head_actions_whatever_the_event_label_or_title_fields():
    # Scope: the permissionless event-policy classifier only. The arm's own held-PR exit
    # (draft, hold label, DO NOT MERGE title, read from live PR state) applies to every
    # invocation alike and is not exercised here.
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
