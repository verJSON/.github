#!/usr/bin/env python3
"""Keep public reusable-workflow calls rejected before any privileged job."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_EVENTS = (
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "merge_group",
    "pull_request_target",
)
EVENTS_JSON = "[\"" + "\",\"".join(PUBLIC_EVENTS) + "\"]"
PUBLIC_CALL = (
    f"contains(fromJSON('{EVENTS_JSON}'), github.event_name) "
    "&& github.event.repository.private != true"
)
JOB_ALLOW = "needs.public-call-guard.result == 'success'"
WORKFLOWS = {
    ".github/workflows/node-release.yml": {
        "prepare": set(),
        "release": {"prepare"},
        "retention": {"release"},
    },
    ".github/workflows/container-candidate-publish.yml": {
        "prepare": set(),
        "acquire-private-node-dependencies": {"prepare"},
        "publish-base": {"prepare", "acquire-private-node-dependencies"},
        "publish-derived": {
            "prepare",
            "acquire-private-node-dependencies",
            "publish-base",
        },
        "evict-node-modules-transfer": {
            "acquire-private-node-dependencies",
            "publish-base",
            "publish-derived",
        },
        "attest-sbom": {"prepare", "publish-base", "publish-derived"},
        "mirror-gar": {"prepare", "publish-base", "publish-derived", "attest-sbom"},
        "candidate-manifest": {
            "prepare",
            "publish-base",
            "publish-derived",
            "attest-sbom",
            "mirror-gar",
        },
    },
}


def load_workflow(relative_path: str) -> dict:
    workflow = yaml.safe_load((ROOT / relative_path).read_text())
    triggers = workflow.get("on", workflow.get(True, {}))
    assert "workflow_call" in triggers, f"{relative_path} must remain reusable"
    return workflow


def test_public_reusable_calls_fail_before_existing_jobs() -> None:
    for relative_path, expected_needs in WORKFLOWS.items():
        workflow = load_workflow(relative_path)
        jobs = workflow["jobs"]
        guard = jobs.get("public-call-guard")
        assert guard is not None, f"{relative_path} lacks the public-call sentinel"
        assert guard["runs-on"] == "ubuntu-24.04"
        assert guard.get("permissions", {}) == {}
        assert "needs" not in guard
        assert set(jobs) == set(expected_needs) | {"public-call-guard"}

        steps = guard.get("steps", [])
        assert len(steps) == 1, f"{relative_path} sentinel must stay dependency-free"
        step = steps[0]
        assert "uses" not in step, f"{relative_path} sentinel must not checkout or install"
        assert step.get("if", "").strip() == f"${{{{ {PUBLIC_CALL} }}}}"
        assert "exit 1" in step.get("run", "")

        for job_name, original_needs in expected_needs.items():
            job = jobs[job_name]
            needs = job.get("needs", [])
            if isinstance(needs, str):
                needs = [needs]
            assert set(needs) == original_needs | {"public-call-guard"}, (
                f"{relative_path}:{job_name} changed its job dependencies"
            )
            condition = job.get("if", "")
            assert JOB_ALLOW in condition, (
                f"{relative_path}:{job_name} lacks an explicit sentinel-result guard"
            )

        if relative_path.endswith("node-release.yml"):
            assert "needs.release.outputs.new-release-published == 'true'" in jobs[
                "retention"
            ]["if"]
        else:
            assert all("always()" in jobs[name]["if"] for name in set(expected_needs) - {"prepare"}), (
                f"{relative_path} must preserve its always()-based publication gates"
            )
            assert all(
                "github.event_name == 'push'" in jobs[name]["if"]
                and "github.event_name == 'workflow_dispatch'" in jobs[name]["if"]
                for name in set(expected_needs) - {"prepare", "evict-node-modules-transfer"}
            ), f"{relative_path} must preserve its trusted-event publication gates"
            assert "needs.acquire-private-node-dependencies.result == 'success'" in jobs[
                "evict-node-modules-transfer"
            ]["if"]


if __name__ == "__main__":
    test_public_reusable_calls_fail_before_existing_jobs()
    print("public reusable workflow guard contract: PASS")
