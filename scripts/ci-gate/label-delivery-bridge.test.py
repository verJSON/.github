#!/usr/bin/env python3
from pathlib import Path
import os
import subprocess
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[2]
ARM = ROOT / ".github/workflows/gate-rearm.yml"
CALLER = ROOT / ".github/workflows/ai-review-label-rearm.yml"
GENERATOR = ROOT / "scripts/gen-ai-review-label-rearm-caller.sh"

def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))

def validate_caller(doc, target):
    assert doc[True] == {"pull_request_target": {"types": ["labeled"]}}
    assert doc["permissions"] == {"actions": "read", "contents": "read"}
    assert doc["jobs"] == {"rearm": {
            "permissions": {"actions": "write", "checks": "write", "contents": "read", "issues": "write", "pull-requests": "write"},
        "uses": target,
        "secrets": "inherit",
        "with": {"ai_review_environment": "ai-review-app"},
    }}

def validate_event_admission(workflow):
    script = workflow["jobs"]["event-policy"]["steps"][0]["run"]
    cases = [
        ("body-only edit", {"action": "edited", "changes": {"body": {"from": "old body"}}}, False),
        ("body-only edit on held PR", {"action": "edited", "changes": {"body": {"from": "old body"}}, "pull_request": {"title": "DO NOT MERGE: held"}}, False),
        ("base-only edit on held PR", {"action": "edited", "changes": {"base": {"ref": {"from": "main"}}}, "pull_request": {"title": "DO NOT MERGE: held"}}, False),
        ("missing changes", {"action": "edited", "pull_request": {"title": "DO NOT MERGE: held"}}, False),
        ("null previous title", {"action": "edited", "changes": {"title": {"from": None}}, "pull_request": {"title": "DO NOT MERGE: held"}}, False),
        ("empty previous title", {"action": "edited", "changes": {"title": {"from": ""}}, "pull_request": {"title": "ordinary title"}}, False),
        ("ordinary title edit", {"action": "edited", "changes": {"title": {"from": "Old title"}}, "pull_request": {"title": "New title"}}, False),
        ("embedded prefix does not clear a hold", {"action": "edited", "changes": {"title": {"from": "REDO NOT MERGE: old"}}, "pull_request": {"title": "ordinary title"}}, False),
        ("embedded suffix does not set a hold", {"action": "edited", "changes": {"title": {"from": "ordinary title"}}, "pull_request": {"title": "DO NOT MERGER: new"}}, False),
        ("add title hold", {"action": "edited", "changes": {"title": {"from": "Old title"}}, "pull_request": {"title": "DO NOT MERGE: Old title"}}, True),
        ("add mixed-case title hold", {"action": "edited", "changes": {"title": {"from": "Old title"}}, "pull_request": {"title": "dO nOt mErGe: Old title"}}, True),
        ("remove title hold", {"action": "edited", "changes": {"title": {"from": "do not merge: Old title"}}, "pull_request": {"title": "Old title"}}, True),
        ("retain title hold", {"action": "edited", "changes": {"title": {"from": "do not merge: Old title"}}, "pull_request": {"title": "DO NOT MERGE: new title"}}, False),
        ("unrelated label addition", {"action": "labeled", "label": {"name": "documentation"}}, False),
        ("recognized label addition", {"action": "labeled", "label": {"name": "hold"}}, True),
        ("recognized label removal", {"action": "unlabeled", "label": {"name": "hold"}}, True),
    ]
    cases.extend((action, {"action": action}, True) for action in (
        "opened", "reopened", "synchronize", "ready_for_review", "converted_to_draft",
    ))
    with tempfile.TemporaryDirectory() as temporary_directory:
        output_path = Path(temporary_directory) / "github-output"
        for name, event, expected in cases:
            changes = event.get("changes") or {}
            title_change = changes.get("title") or {}
            title_changed = title_change.get("from") is not None
            pull_request = event.get("pull_request") or {}
            label = event.get("label") or {}
            env = os.environ.copy()
            env.update({
                "EVENT_ACTION": event["action"],
                "EVENT_LABEL": label.get("name", ""),
                "EVENT_TITLE_CHANGED": str(title_changed).lower(),
                "EVENT_OLD_TITLE": title_change.get("from") or "",
                "EVENT_NEW_TITLE": pull_request.get("title") or "",
                "GITHUB_OUTPUT": str(output_path),
            })
            output_path.write_text("", encoding="utf-8")
            completed = subprocess.run(["bash", "-c", script], env=env, check=False, capture_output=True, text=True)
            assert completed.returncode == 0, (name, completed.stderr)
            outputs = dict(line.split("=", 1) for line in output_path.read_text(encoding="utf-8").splitlines())
            assert outputs["run_control_plane"] == str(expected).lower(), name

def main():
    arm = load(ARM)
    validate_event_admission(arm)
    assert "issues" not in arm[True]
    assert "labeled" not in arm[True]["pull_request_target"]["types"]
    validate_caller(load(CALLER), "./.github/workflows/gate-rearm.yml")
    with tempfile.NamedTemporaryFile() as local_generated:
        subprocess.run([str(GENERATOR), "--local"], check=True, stdout=local_generated)
        local_generated.flush()
        validate_caller(load(Path(local_generated.name)), "./.github/workflows/gate-rearm.yml")
    sha = "1" * 40
    with tempfile.NamedTemporaryFile() as generated:
        subprocess.run([str(GENERATOR), sha], check=True, stdout=generated)
        generated.flush()
        validate_caller(load(Path(generated.name)), f"Verjson/.github/.github/workflows/gate-rearm.yml@{sha}")
    for invalid in ("main", "v1", "1" * 39, "1" * 41):
        assert subprocess.run([str(GENERATOR), invalid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
    script = next(step["run"] for step in arm["jobs"]["arm"]["steps"] if step.get("id") == "arm")
    for marker in (
        '[ "$EVENT_NAME" = pull_request_target ] && [ "$EVENT_ACTION" = labeled ]',
        'case "$label_normalized" in ai-review|re-review|hold|do-not-merge)',
        '[ "${GITHUB_RUN_ATTEMPT:-}" = 1 ]',
        '"$TARGET_REPO/.github/workflows/ai-review-label-rearm.yml@refs/heads/$DEFAULT_BRANCH")',
        'local_delivery_path=".github/workflows/ai-review-label-rearm.yml"',
        'local_workflow="ai-review-label-rearm.yml"',
        '.github/workflows/ai-review-label-rearm.yml:labeled)',
        'expected_workflow_ref="$TARGET_REPO/$local_delivery_path@refs/heads/$DEFAULT_BRANCH"',
        '[ "$WORKFLOW_REF" = "$expected_workflow_ref" ]',
        '--arg path "$local_delivery_path"',
        '.event == "pull_request_target"',
        '.path == $path',
        '.head_sha == $head',
        '[ "$head_sha" != "$EVENT_HEAD_SHA" ]',
        '.actor.login == $actor',
        '[[ "$source_workflow_id" =~ ^[123456789][0123456789]*$ ]]',
        '.workflow_id == $workflow_id',
        'source_bound_delivery=true',
        '--arg review_policy "$review_policy" --arg delivery_event "$EVENT_ACTION"',
        '--arg delivery_actor "$REQUEST_ACTOR"',
        '--arg workflow_ref "$WORKFLOW_REF" --arg workflow_sha "$WORKFLOW_SHA"',
        '[ -n "$rereview_label" ]',
        '[ -n "$ai_review_label" ]',
        'schema:(if $source_bound then 2 else 1 end)',
        '. + {delivery_event:$delivery_event,delivery_actor:$delivery_actor,',
        'workflow_ref:$workflow_ref',
        'workflow_sha:$workflow_sha',
    ):
        assert marker in script
    assert "ORG_ADMIN_TOKEN" not in ARM.read_text() and "secrets: inherit" in CALLER.read_text()
    print("PASS: separate protected PR-label caller is exact-source, exact-head, actor-bound and fail-closed")

if __name__ == "__main__":
    main()
