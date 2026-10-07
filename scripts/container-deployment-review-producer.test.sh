#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
producer="$root/scripts/container_deployment_review_producer.py"
python3 -m py_compile "$producer"
PYTHONPATH="$root/scripts" python3 "$root/scripts/container_deployment_review_producer.test.py"

grep -qF 'uses: Verjson/.github/.github/workflows/container-deployment-review-producer.yml@$ref' "$root/scripts/gen-container-deployment.sh"
grep -qF 'contract-ref: $ref' "$root/scripts/gen-container-deployment.sh"

trusted="$root/.github/workflows/container-deployment-review-producer.yml"
download_artifact_sha=9000827ccba6bdab643e8b6fd33ac0654aef8333
download_artifact_pin="uses: actions/download-artifact@$download_artifact_sha # v8.0.2"
test "$(grep -Ec '^[[:space:]]*-[[:space:]]*uses: actions/download-artifact@' "$trusted")" -eq 3
test "$(grep -F -c "$download_artifact_pin" "$trusted")" -eq 3
! grep -qF 'actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1' "$trusted"
grep -qF 'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a' "$trusted"
grep -qF 'actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1' "$trusted"
grep -qF 'permission-checks: write' "$trusted"
grep -qF "ref: '\${{ needs.environment-preflight.outputs.workflow-sha }}'" "$trusted"
grep -qF "repository: '\${{ needs.environment-preflight.outputs.workflow-repository }}'" "$trusted"
grep -qF "JOB_CONTEXT: '\${{ toJSON(job) }}'" "$trusted"
grep -qF 'WORKFLOW_REF:' "$trusted"
grep -qF 'WORKFLOW_SHA:' "$trusted"
grep -qF 'ascii_downcase == "verjson/.github"' "$trusted"
test "$(grep -F -c '"${WORKFLOW_REPOSITORY,,}" == "verjson/.github"' "$trusted")" = 3
! grep -qF 'github.workflow_' "$trusted"
! grep -qF 'inputs.contract-ref' "$trusted"
grep -qF 'environment-preflight:' "$trusted"
grep -qF 'repos/$GITHUB_REPOSITORY/environments/' "$trusted"
! grep -qF 'repos/$JOB_REPOSITORY/environments/' "$trusted"
grep -qF 'needs: [analyze, environment-preflight]' "$trusted"
python3 - "$trusted" <<'PY'
import sys, yaml
with open(sys.argv[1], encoding="utf-8") as stream:
    workflow = yaml.safe_load(stream)
assert workflow["jobs"]["environment-preflight"]["permissions"] == {"actions": "read", "contents": "read"}
for kind in ("code", "security", "ai"):
    job = workflow["jobs"][f"publish-{kind}"]
    mint = next(step for step in job["steps"] if step.get("id") == "publisher")
    assert mint["uses"] == "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
    assert mint["with"] == {
        "client-id": f"${{{{ vars.RUNNER_DEPLOY_{kind.upper()}_REVIEW_APP_CLIENT_ID }}}}",
        "private-key": f"${{{{ secrets.RUNNER_DEPLOY_{kind.upper()}_REVIEW_APP_PRIVATE_KEY }}}}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-checks": "write",
    }
PY
for kind in code security ai; do
  grep -qF "environment: runner-deploy-$kind-review-publisher" "$trusted"
done
test "$(grep -c 'private-key:' "$trusted")" = 3
! sed -n '/^  analyze:/,/^  publish-code:/p' "$trusted" | grep 'private-key:\|APP_PRIVATE_KEY' >/dev/null

! grep -Eq 'RUNNER_DEPLOY_(CODE|SECURITY|AI)_REVIEW_(APP_ID|CHECK|WORKFLOW)' \
  "$root/.github/workflows/container-deployment.yml"
grep -qF 'reviewed_config.get("reviewAuthority")' \
  "$root/.github/workflows/container-deployment.yml"
grep -qF 'validate_environment_policy(environment)' \
  "$root/.github/workflows/container-deployment.yml"

echo "container deployment review producers passed"
