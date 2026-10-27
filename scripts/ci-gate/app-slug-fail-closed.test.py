#!/usr/bin/env python3
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

def workflow(name):
    return yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())

rearm = workflow("gate-rearm")
arm = rearm["jobs"]["arm"]
arm_step = next(step for step in arm["steps"] if step.get("name") == "Create exact-head authorization receipt")
arm_script = arm_step["run"]
assert arm["permissions"].get("checks") == "write"
assert arm_step["env"]["GH_TOKEN"] == "${{ github.token }}"
assert not any(step.get("name") == "Mint dedicated authorization App token" for step in arm["steps"])
assert "--argjson app_id 15368" in arm_script and ".app.id == $app_id" in arm_script
assert "--arg slug github-actions" in arm_script and ".app.slug == $slug" in arm_script
create_check = arm_script.index('gh api --method POST "repos/$TARGET_REPO/check-runs"')
policy_result_check = arm_script.index(
    'if [ "$APP_KEY_POLICY_RESULT" != success ] || [ "$AI_REVIEW_KEY_VALIDATION_RESULT" != success ]; then'
)
dispatch = next(step for step in arm["steps"] if step.get("name") == "Dispatch trusted review after receipt publication")
receipt = next(step for step in arm["steps"] if step.get("name") == "Upload immutable arm receipt")
assert create_check < policy_result_check
assert arm["steps"].index(receipt) < arm["steps"].index(dispatch)

review = workflow("ai-review-merge")
complete = review["jobs"]["complete-authorization"]
app_token = next(step for step in complete["steps"] if step.get("name") == "Mint dedicated authorization App token")
complete_step = next(step for step in complete["steps"] if step.get("name") == "Complete exact head authorization")
assert app_token.get("if") == "${{ needs.app-key-policy.result == 'success' }}"
assert "permission-checks" not in app_token["with"]
assert complete_step["env"]["MINTED_APP_SLUG"] == "${{ steps.app-token.outputs.app-slug }}"
assert '"$MINTED_APP_SLUG" = "$EXPECTED_APP_SLUG"' in complete_step["run"]
assert 'GH_TOKEN="$ACTIONS_TOKEN" gh api --method PATCH "repos/$TARGET_REPO/check-runs/$AUTHORIZATION_CHECK_ID"' in complete_step["run"]
assert "$check_token" not in complete_step["run"]
print("PASS - Actions owns check completion; AI App key is limited to approval authentication")
