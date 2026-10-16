#!/usr/bin/env python3
from copy import deepcopy
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[2]
review = yaml.safe_load((root / ".github/workflows/ai-review-merge.yml").read_text())
promote = yaml.safe_load((root / ".github/workflows/ai-privileged-merge.yml").read_text())
retry = yaml.safe_load((root / ".github/workflows/ai-promotion-retry.yml").read_text())
rearm_text = (root / ".github/workflows/gate-rearm.yml").read_text()
valid_substitute = "eyJhY3RvciI6InRydXN0ZWQtYXJtIiwiYWN0b3JfcGVybWlzc2lvbiI6ImF1dG9tYXRpb24iLCJidWRnZXRfdXNkIjoiYXV0byIsIm1vZGVsIjoiYXV0byIsInByaWNpbmdfdmVyc2lvbiI6ImFudGhyb3BpYy1uYXRpdmUtdjEiLCJwcm92aWRlciI6ImFudGhyb3BpYyJ9"


def require_contract(review_doc, promote_doc, retry_doc):
    dispatch = promote_doc[True]["workflow_dispatch"]["inputs"]
    called = promote_doc[True]["workflow_call"]["inputs"]
    assert dispatch["review_policy"]["required"] is True
    assert called["review_policy"]["required"] is True
    job = promote_doc["jobs"]["privileged_merge"]
    assert job["env"]["REVIEW_POLICY"] == "${{ inputs.review_policy }}"
    checkout = next(step for step in job["steps"] if step.get("name") == "Check out immutable arm verifier")
    assert "scripts/ci-gate/review-policy-envelope.py" in checkout["with"]["sparse-checkout"]
    promotion_run = next(step for step in job["steps"] if step.get("name") == "Authorize terminal merge from trusted metadata")["run"]
    assert 'review-policy-envelope.py decode "$REVIEW_POLICY"' in promotion_run
    assert '[ "$(jq -r \'.authority // "human"\' <<<"$policy_json")" = ai-merge ]' in promotion_run

    review_job = review_doc["jobs"]["dispatch-merge"]
    assert review_job["env"]["REVIEW_POLICY"] == "${{ inputs.review_policy }}"
    review_dispatch = next(step for step in review_job["steps"] if step.get("name") == "Dispatch trusted terminal promotion")["run"]
    assert '-f review_policy="$REVIEW_POLICY"' in review_dispatch
    review_complete = next(step for step in review_doc["jobs"]["complete-authorization"]["steps"]
                           if step.get("name") == "Complete exact head authorization")["run"]
    authorization_marker = "ai-review-authorized:v1:${AUTHORIZATION_CHECK_ID}:${EXPECTED_AUTHORIZED_HEAD_SHA}:${REVIEW_AUTHORITY}"
    assert authorization_marker in review_complete

    outputs = retry_doc["jobs"]["resolve"]["outputs"]
    assert outputs["review_policy"] == "${{ steps.resolve.outputs.review_policy }}"
    retry_with = retry_doc["jobs"]["promote"]["with"]
    assert retry_with["review_policy"] == "${{ needs.resolve.outputs.review_policy }}"
    retry_run = next(step for step in retry_doc["jobs"]["resolve"]["steps"]
                     if step.get("name") == "Resolve exact-head authorization")["run"]
    retry_checkout = next(step for step in retry_doc["jobs"]["resolve"]["steps"]
                          if step.get("name") == "Check out immutable review-policy decoder")
    assert retry_checkout["with"]["repository"] == "Verjson/.github"
    assert retry_checkout["with"]["ref"] == "${{ steps.trusted-revision.outputs.sha }}"
    assert retry_checkout["with"]["persist-credentials"] is False
    assert "ai-review-arm-$arm_run_id-$arm_run_attempt" in retry_run
    assert 'review_policy="$(jq -er \'.review_policy | select(type == "string")\' "$receipt_dir/receipt.json")"' in retry_run
    assert '^[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-]{1,2048}$' in retry_run
    assert 'review-policy-envelope.py decode "$review_policy"' in retry_run
    assert '!= ai-merge' in retry_run
    assert 'ai-review-authorized:v1:${check_id}:${HEAD_SHA}:ai-merge' in retry_run
    assert ".output.summary | strings" in retry_run


require_contract(review, promote, retry)
mutations = []
for trigger in ("workflow_dispatch", "workflow_call"):
    changed = deepcopy(promote)
    del changed[True][trigger]["inputs"]["review_policy"]
    mutations.append((review, changed, retry))
changed = deepcopy(review); del changed["jobs"]["dispatch-merge"]["env"]["REVIEW_POLICY"]; mutations.append((changed, promote, retry))
changed = deepcopy(retry); del changed["jobs"]["resolve"]["outputs"]["review_policy"]; mutations.append((review, promote, changed))
changed = deepcopy(review)
step = next(item for item in changed["jobs"]["dispatch-merge"]["steps"] if item.get("name") == "Dispatch trusted terminal promotion")
step["run"] = step["run"].replace('-f review_policy="$REVIEW_POLICY"', f'-f review_policy="{valid_substitute}"')
mutations.append((changed, promote, retry))
changed = deepcopy(promote); changed["jobs"]["privileged_merge"]["env"]["REVIEW_POLICY"] = valid_substitute; mutations.append((review, changed, retry))
changed = deepcopy(retry); changed["jobs"]["promote"]["with"]["review_policy"] = valid_substitute; mutations.append((review, promote, changed))
changed = deepcopy(retry)
step = next(item for item in changed["jobs"]["resolve"]["steps"] if item.get("name") == "Resolve exact-head authorization")
step["run"] = step["run"].replace(
    'review_policy="$(jq -er \'.review_policy | select(type == "string")\' "$receipt_dir/receipt.json")"',
    f'review_policy="{valid_substitute}"')
mutations.append((review, promote, changed))
changed = deepcopy(retry)
step = next(item for item in changed["jobs"]["resolve"]["steps"] if item.get("name") == "Resolve exact-head authorization")
step["run"] = step["run"].replace('^[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-]{1,2048}$', '.*')
mutations.append((review, promote, changed))
changed = deepcopy(review)
step = next(item for item in changed["jobs"]["complete-authorization"]["steps"] if item.get("name") == "Complete exact head authorization")
step["run"] = step["run"].replace("ai-review-authorized:v1:${AUTHORIZATION_CHECK_ID}:${EXPECTED_AUTHORIZED_HEAD_SHA}:${REVIEW_AUTHORITY}", "human-path")
mutations.append((changed, promote, retry))
changed = deepcopy(retry)
step = next(item for item in changed["jobs"]["resolve"]["steps"] if item.get("name") == "Resolve exact-head authorization")
step["run"] = step["run"].replace("ai-review-authorized:v1:${check_id}:${HEAD_SHA}:ai-merge", "human-path")
mutations.append((review, promote, changed))
for docs in mutations:
    try:
        require_contract(*docs)
    except (AssertionError, KeyError):
        continue
    raise AssertionError("promotion policy mutation escaped contract")

assert '-f review_policy="$review_policy"' in rearm_text
assert "ai-review-merge.yml" not in (root / ".github/workflows/ai-promotion-retry.yml").read_text()
adr = (root / "docs/decisions/0081-event-driven-terminal-ai-promotion/README.md").read_text()
assert "no-cost recovery" in adr and "exact opaque policy envelope" in adr
print("PASS: terminal promotion binds the exact review-policy envelope on dispatch, callee, and retry")
