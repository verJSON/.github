#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
contract="$tmp/contract"
consumer="$tmp/consumer"
mkdir -p \
  "$contract/.github/workflows" \
  "$contract/contracts/container-deployment-cli" \
  "$contract/scripts" \
  "$contract/docs/decisions/0078-container-release-and-runner-deployment-contract" \
  "$consumer/.github/workflows" \
  "$consumer/scripts"
cp \
  "$root/scripts/gen-container-deployment.sh" \
  "$root/scripts/container_deployment_transport.py" \
  "$root/scripts/container_deployment_controller.py" \
  "$root/scripts/container_deployment_preflight.py" \
  "$root/scripts/container_deployment_review_producer.py" \
  "$root/scripts/validate-container-deployment-cli-lock.py" \
  "$contract/scripts/"
cp \
  "$root/.github/workflows/container-deployment-review-producer.yml" \
  "$contract/.github/workflows/"
cp \
  "$root/docs/decisions/0078-container-release-and-runner-deployment-contract/deployment-receipt.schema.json" \
  "$contract/docs/decisions/0078-container-release-and-runner-deployment-contract/"
cp \
  "$root/contracts/container-deployment-cli/package.json" \
  "$root/contracts/container-deployment-cli/package-lock.json" \
  "$root/contracts/container-deployment-cli/.npmrc" \
  "$contract/contracts/container-deployment-cli/"
git -C "$contract" init -q
git -C "$contract" config user.name fixture
git -C "$contract" config user.email fixture@example.invalid
git -C "$contract" add scripts docs
git -C "$contract" add .github
git -C "$contract" commit -qm fixture
ref="$(git -C "$contract" rev-parse HEAD)"
generator="$contract/scripts/gen-container-deployment.sh"

"$generator" workflow "$ref" >"$consumer/.github/workflows/container-deployment.yml"
"$generator" code-review-workflow "$ref" >"$consumer/.github/workflows/container-deployment-code-review.yml"
"$generator" security-review-workflow "$ref" >"$consumer/.github/workflows/container-deployment-security-review.yml"
"$generator" ai-review-workflow "$ref" >"$consumer/.github/workflows/container-deployment-ai-review.yml"
"$generator" review-producer-workflow "$ref" >"$consumer/.github/workflows/container-deployment-review-producer.yml"
"$generator" review-producer "$ref" >"$consumer/scripts/container_deployment_review_producer.py"
"$generator" transport "$ref" >"$consumer/scripts/container_deployment_transport.py"
"$generator" controller "$ref" >"$consumer/scripts/container_deployment_controller.py"
"$generator" preflight "$ref" >"$consumer/scripts/container_deployment_preflight.py"
"$generator" receipt-schema "$ref" >"$consumer/scripts/deployment-receipt.schema.json"
"$generator" contract-test "$ref" >"$consumer/scripts/container-deployment-contract.test.sh"
cat >"$consumer/container-deployment.json" <<JSON
{
  "schemaVersion": 1,
  "reviewAuthority": {
    "code": {"appId": 201, "installationId": 301, "checkName": "runner-deploy-code-review", "workflowPath": ".github/workflows/container-deployment-code-review.yml"},
    "security": {"appId": 202, "installationId": 302, "checkName": "runner-deploy-security-review", "workflowPath": ".github/workflows/container-deployment-security-review.yml"},
    "ai": {"appId": 203, "installationId": 303, "sourceAppId": 403, "sourceCheckName": "canonical-ai-review", "checkName": "runner-deploy-ai-review", "workflowPath": ".github/workflows/container-deployment-ai-review.yml"}
  },
  "hostEvidenceAuthority": {"appId": 204, "installationId": 304},
  "cliCommand": ["verjson-cloud"],
  "evidenceCommand": ["python3", "scripts/runner-deployment-evidence.py"],
  "probeCommand": ["python3", "scripts/runner-deployment-probe.py"],
  "expectedRelease": {
    "sourceRepository": "Verjson/example",
    "sourceRef": "refs/heads/main",
    "signerWorkflow": "Verjson/.github/.github/workflows/container-release.yml",
    "contractCommit": "$ref",
    "variant": "runner"
  },
  "fleets": {
    "production": {
      "lane": "gate",
      "project": "existing-fleet",
      "hostEvidence": {"doContext": "read-only", "doSshKey": "read-only", "maxAgeSeconds": 300},
      "canary": "gha-gate-1",
      "runners": ["gha-gate-1", "gha-gate-2"],
      "minimumAvailable": 1,
      "drainTimeoutSeconds": 600,
      "probeTimeoutSeconds": 300,
      "observationSeconds": 120,
      "runnerGroup": "trusted",
      "requiredLabels": ["gate", "pwsh"],
      "requiredTools": ["pwsh"]
    }
  }
}
JSON
cat >"$consumer/scripts/runner-deployment-evidence.py" <<'PY'
#!/usr/bin/env python3
PY
cat >"$consumer/scripts/runner-deployment-probe.py" <<'PY'
#!/usr/bin/env python3
PY
chmod +x "$consumer/scripts/"*.py "$consumer/scripts/"*.sh
(cd "$consumer" && bash scripts/container-deployment-contract.test.sh)
cp "$consumer/scripts/container_deployment_transport.py" "$tmp/transport.clean"
printf '\n# drift\n' >> "$consumer/scripts/container_deployment_transport.py"
drift_report="$tmp/transport-drift.log"
if (cd "$consumer" && bash scripts/container-deployment-contract.test.sh >"$drift_report" 2>&1); then
  echo 'generated deployment contract accepted transport byte drift' >&2
  exit 1
fi
# A required contract test that exits 1 saying nothing forces an adopter to
# bisect the generated script by hand (Verjson/verjson-git-runners#207). The
# generated script must name the failing assertion and both sides of it.
observed_transport_digest="$(sha256sum "$consumer/scripts/container_deployment_transport.py" | cut -d' ' -f1)"
expected_transport_digest="$(sha256sum "$tmp/transport.clean" | cut -d' ' -f1)"
for required_fragment in \
  scripts/container_deployment_transport.py \
  "$expected_transport_digest" \
  "$observed_transport_digest"; do
  grep -qF -- "$required_fragment" "$drift_report" || {
    echo "generated contract test failed without reporting $required_fragment" >&2
    cat "$drift_report" >&2
    exit 1
  }
done
cp "$tmp/transport.clean" "$consumer/scripts/container_deployment_transport.py"


cp "$consumer/.github/workflows/container-deployment.yml" "$tmp/caller.clean"
sed -i "s/container-deployment.yml@$ref/container-deployment.yml@main/" \
  "$consumer/.github/workflows/container-deployment.yml"
if (cd "$consumer" && bash scripts/container-deployment-contract.test.sh >/dev/null 2>&1); then
  echo "generated contract accepted a mutable reusable-workflow ref" >&2
  exit 1
fi
mv "$tmp/caller.clean" "$consumer/.github/workflows/container-deployment.yml"

# The deployment-config preconditions are what #207 had to bisect by hand, so
# the generated script must name the offending field, its expectation, and the
# observed value rather than exiting 1 silently.
cp "$consumer/container-deployment.json" "$tmp/config.clean"
jq '.schemaVersion = 2 | .cliCommand = ["wrong-cli"]' "$tmp/config.clean" \
  >"$consumer/container-deployment.json"
config_report="$tmp/config-drift.log"
if (cd "$consumer" && bash scripts/container-deployment-contract.test.sh >"$config_report" 2>&1); then
  echo 'generated deployment contract accepted an invalid deployment config' >&2
  exit 1
fi
for required_fragment in schemaVersion 'observed 2' cliCommand wrong-cli; do
  grep -qF -- "$required_fragment" "$config_report" || {
    echo "generated contract test failed without reporting $required_fragment" >&2
    cat "$config_report" >&2
    exit 1
  }
done
mv "$tmp/config.clean" "$consumer/container-deployment.json"
(cd "$consumer" && bash scripts/container-deployment-contract.test.sh >/dev/null)

# A contract failure must never print an empty reason. The nine deployment-config
# preconditions are the verdict *and* the diagnostic: a single jq program derives
# both, so a condition added later cannot be enforced without also being reported.
# A second hand-maintained predicate would let the two drift back apart and yield
# `container-deployment contract FAILED: ` with nothing after it.
generated_contract_test="$consumer/scripts/container-deployment-contract.test.sh"
for single_sourced_condition in \
  '.schemaVersion == 1' \
  '(.reviewAuthority | keys | sort == ["ai", "code", "security"])' \
  '[.reviewAuthority[].installationId] | all(type == "number" and . > 0)' \
  '(.reviewAuthority.ai.sourceAppId | type == "number" and . > 0)' \
  '(.reviewAuthority.ai.sourceCheckName | type == "string" and length > 0)' \
  '.cliCommand == ["verjson-cloud"]' \
  '(.evidenceCommand | length == 2)' \
  '(.probeCommand | length == 2)' \
  '(.fleets | type == "object" and length > 0)'; do
  occurrences="$(grep -oF -- "$single_sourced_condition" "$generated_contract_test" | wc -l)"
  test "$occurrences" = 1 || {
    echo "deployment-config condition is stated $occurrences times, expected once: $single_sourced_condition" >&2
    exit 1
  }
done

blank_reason="$(bash -c '
  eval "$(sed -n "/^contract_fail() {/,/^}/p" "$1")"
  contract_fail ""
' _ "$generated_contract_test" 2>&1 || true)"
case "$blank_reason" in
  *'FAILED: '[![:space:]]*) ;;
  *)
    echo "generated contract test reported a failure with an empty reason: $blank_reason" >&2
    exit 1
    ;;
esac

# Every deployment-config precondition must reject on its own and name itself, so
# the accept/reject partition stays pinned field by field rather than only in
# aggregate.
cp "$consumer/container-deployment.json" "$tmp/config.clean"
while IFS='|' read -r mutation expected_field; do
  jq "$mutation" "$tmp/config.clean" >"$consumer/container-deployment.json"
  condition_report="$tmp/config-condition.log"
  if (cd "$consumer" && bash scripts/container-deployment-contract.test.sh >"$condition_report" 2>&1); then
    echo "generated deployment contract accepted a config violating $expected_field" >&2
    exit 1
  fi
  grep -qF -- "$expected_field" "$condition_report" || {
    echo "generated contract test rejected $mutation without naming $expected_field" >&2
    cat "$condition_report" >&2
    exit 1
  }
  grep -Eq 'contract FAILED: [^[:space:]]' "$condition_report" || {
    echo "generated contract test rejected $mutation with an empty reason" >&2
    cat "$condition_report" >&2
    exit 1
  }
done <<'MUTATIONS'
.schemaVersion = 2|schemaVersion: expected 1
del(.reviewAuthority.ai)|reviewAuthority: expected keys [ai, code, security]
.reviewAuthority.code.installationId = 0|reviewAuthority[].installationId: expected positive numbers
.reviewAuthority.ai.sourceAppId = "403"|reviewAuthority.ai.sourceAppId: expected a positive number
.reviewAuthority.ai.sourceCheckName = ""|reviewAuthority.ai.sourceCheckName: expected a non-empty string
.cliCommand = ["wrong-cli"]|cliCommand: expected
.evidenceCommand = ["python3"]|evidenceCommand: expected 2 elements
.probeCommand = ["python3"]|probeCommand: expected 2 elements
.fleets = {}|fleets: expected a non-empty object
MUTATIONS

# An empty config yields no jq result at all, rather than an empty violation
# list. That must stay a rejection, and a named one.
: >"$consumer/container-deployment.json"
empty_config_report="$tmp/empty-config.log"
if (cd "$consumer" && bash scripts/container-deployment-contract.test.sh >"$empty_config_report" 2>&1); then
  echo 'generated deployment contract accepted an empty deployment config' >&2
  exit 1
fi
grep -Eq 'contract FAILED: [^[:space:]]' "$empty_config_report" || {
  echo 'generated contract test rejected an empty config with an empty reason' >&2
  cat "$empty_config_report" >&2
  exit 1
}

# The adapter assertions sit behind the config check rather than behind a digest
# pin, so a config naming a bad adapter reaches them.
while IFS='|' read -r adapter_kind expected_adapter_report; do
  jq '.evidenceCommand[1] = "scripts/runner-deployment-absent.py"' "$tmp/config.clean" \
    >"$consumer/container-deployment.json"
  case "$adapter_kind" in
    missing) ;;
    symlink)
      ln -s runner-deployment-evidence.py "$consumer/scripts/runner-deployment-absent.py" ;;
  esac
  adapter_report="$tmp/adapter-$adapter_kind.log"
  if (cd "$consumer" && bash scripts/container-deployment-contract.test.sh >"$adapter_report" 2>&1); then
    echo "generated deployment contract accepted a $adapter_kind adapter" >&2
    exit 1
  fi
  grep -qF -- "$expected_adapter_report" "$adapter_report" || {
    echo "generated contract test rejected a $adapter_kind adapter without naming it" >&2
    cat "$adapter_report" >&2
    exit 1
  }
  rm -f "$consumer/scripts/runner-deployment-absent.py"
done <<'ADAPTERS'
missing|scripts/runner-deployment-absent.py, which is not a regular file
symlink|scripts/runner-deployment-absent.py, which is a symlink
ADAPTERS

mv "$tmp/config.clean" "$consumer/container-deployment.json"
(cd "$consumer" && bash scripts/container-deployment-contract.test.sh >/dev/null)

# The caller-workflow assertions sit behind that file's own digest pin, so no
# mutation of the caller alone can reach them. Re-pin the digest to the mutated
# caller -- exactly what regenerating at a contract whose emitter produced that
# caller would do -- and the later assertions become reachable.
assert_generated_contract_rejects_caller() {
  local label="$1" mutation="$2" expected="$3" caller_digest report
  cp "$consumer/.github/workflows/container-deployment.yml" "$tmp/caller.clean"
  cp "$consumer/scripts/container-deployment-contract.test.sh" "$tmp/contract-test.clean"
  sed -i "$mutation" "$consumer/.github/workflows/container-deployment.yml"
  caller_digest="$(sha256sum "$consumer/.github/workflows/container-deployment.yml" | cut -d' ' -f1)"
  sed -i -E "s|^(assert_digest \.github/workflows/container-deployment\.yml ).*|\1$caller_digest|" \
    "$consumer/scripts/container-deployment-contract.test.sh"
  report="$tmp/caller-$label.log"
  if (cd "$consumer" && bash scripts/container-deployment-contract.test.sh >"$report" 2>&1); then
    echo "generated deployment contract accepted a caller with $label" >&2
    exit 1
  fi
  grep -qF -- "$expected" "$report" || {
    echo "generated contract test rejected $label without reporting it" >&2
    cat "$report" >&2
    exit 1
  }
  mv "$tmp/caller.clean" "$consumer/.github/workflows/container-deployment.yml"
  mv "$tmp/contract-test.clean" "$consumer/scripts/container-deployment-contract.test.sh"
}
assert_generated_contract_rejects_caller \
  'an unpinned reusable-workflow ref' \
  "s|container-deployment.yml@$ref|container-deployment.yml@main|" \
  'does not contain the required pinned text'
assert_generated_contract_rejects_caller \
  'a mutable image tag' \
  '$a# runner image: ghcr.io/verjson/runner:latest' \
  'must not name secrets, environments, or mutable tags'
assert_generated_contract_rejects_caller \
  'a named secret map on the reusable edge' \
  's|^    secrets: inherit$|    secrets:\n      RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY: ${{ secrets.RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY }}|' \
  'must not name secrets, environments, or mutable tags'
assert_generated_contract_rejects_caller \
  'a non-inherit secrets clause' \
  's|^    secrets: inherit$|    secrets: {}|' \
  'may carry only `secrets: inherit`'
assert_generated_contract_rejects_caller \
  'a dropped secrets inheritance' \
  '/^    secrets: inherit$/d' \
  'must inherit the caller secret context'
assert_generated_contract_rejects_caller \
  'a duplicated secrets inheritance' \
  '$a\    secrets: inherit' \
  'must inherit the caller secret context'
assert_generated_contract_rejects_caller \
  'a mis-indented secrets inheritance' \
  's|^    secrets: inherit$|  secrets: inherit|' \
  'may carry only `secrets: inherit`'
assert_generated_contract_rejects_caller \
  'an environment binding' \
  '$a\    environment: production' \
  'must not name secrets, environments, or mutable tags'
assert_generated_contract_rejects_caller \
  'a dropped permission' \
  's|^  checks: read$||' \
  'expected permissions'
(cd "$consumer" && bash scripts/container-deployment-contract.test.sh >/dev/null)

workflow="$root/.github/workflows/container-deployment.yml"
grep -q '^    environment: production$' "$workflow"
grep -q 'if: inputs.dry-run' "$workflow"
# The literal GitHub expression is the contract under test.
# shellcheck disable=SC2016
grep -q 'if: \${{ !inputs.dry-run }}' "$workflow"
grep -q 'cancel-in-progress: false' "$workflow"
grep -q 'container_deployment_preflight.py' "$workflow"
assert_cli_cloud_lock_matches_manifest() {
  local manifest="$1" lock="$2" cli_cloud_version
  cli_cloud_version="$(jq -er '.dependencies["@verjson/cli-cloud"]' "$manifest")" || return 1
  [[ "$cli_cloud_version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || return 1
  test "$(jq -er '.packages[""].dependencies["@verjson/cli-cloud"]' "$lock")" = \
    "$cli_cloud_version" || return 1
  test "$(jq -er '.packages["node_modules/@verjson/cli-cloud"].version' "$lock")" = \
    "$cli_cloud_version" || return 1
}

cli_manifest="$root/contracts/container-deployment-cli/package.json"
cli_lock="$root/contracts/container-deployment-cli/package-lock.json"
assert_cli_cloud_lock_matches_manifest "$cli_manifest" "$cli_lock"

for mutation in root-pin package-version; do
  mutated_lock="$tmp/cli-cloud-$mutation-lock.json"
  case "$mutation" in
    root-pin)
      jq '.packages[""].dependencies["@verjson/cli-cloud"] = "9.9.9"' \
        "$cli_lock" > "$mutated_lock" ;;
    package-version)
      jq '.packages["node_modules/@verjson/cli-cloud"].version = "9.9.9"' \
        "$cli_lock" > "$mutated_lock" ;;
  esac
  if assert_cli_cloud_lock_matches_manifest "$cli_manifest" "$mutated_lock"; then
    echo "container deployment contract accepted cli-cloud $mutation drift" >&2
    exit 1
  fi
done
python3 "$root/scripts/validate-container-deployment-cli-lock.py" \
  "$root/contracts/container-deployment-cli/package-lock.json"
python3 - "$root/contracts/container-deployment-cli/package-lock.json" "$tmp" <<'PY'
import copy
import json
from pathlib import Path
import sys

source = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
destination = Path(sys.argv[2])
package_name = next(name for name in source["packages"] if name)
mutations = {
    "missing-integrity": ("integrity", None),
    "wrong-integrity": ("integrity", "sha256-" + "A" * 44),
    "file-url": ("resolved", "file:///tmp/package.tgz"),
    "git-url": ("resolved", "git+https://github.com/example/package.git"),
    "plain-http": ("resolved", "http://registry.npmjs.org/package/-/package.tgz"),
    "foreign-host": ("resolved", "https://packages.example.invalid/package.tgz"),
}
for name, (field, value) in mutations.items():
    candidate = copy.deepcopy(source)
    if value is None:
        candidate["packages"][package_name].pop(field, None)
    else:
        candidate["packages"][package_name][field] = value
    (destination / f"lock-{name}.json").write_text(
        json.dumps(candidate), encoding="utf-8"
    )
PY
for hostile_lock in "$tmp"/lock-*.json; do
  if python3 "$root/scripts/validate-container-deployment-cli-lock.py" \
      "$hostile_lock" >/dev/null 2>&1; then
    echo "deployment CLI lock validator accepted $(basename "$hostile_lock")" >&2
    exit 1
  fi
done
grep -q 'npm ci --ignore-scripts --no-audit --no-fund' "$workflow"
grep -q 'NPM_CONFIG_CACHE:.*npm-cache-' "$workflow"
grep -q 'validate-container-deployment-cli-lock.py' "$workflow"
grep -q 'repos/Verjson/.github/tarball/\$CONTRACT_REF' "$workflow"
grep -q 'test -x "\$cli_bin/verjson-cloud"' "$workflow"
grep -q 'VERJSON_DEPLOYMENT_CLI_ROOT=' "$workflow"
grep -q 'Retain admitted or reconciled authority' "$workflow"
grep -q 'container_deployment_controller.py reconcile' "$workflow"
test "$(grep -c -- '--authorization github-authorization.json' "$workflow")" = 3
for authority_variable in \
  RUNNER_DEPLOY_CODE_REVIEW_APP_ID RUNNER_DEPLOY_CODE_REVIEW_CHECK RUNNER_DEPLOY_CODE_REVIEW_WORKFLOW \
  RUNNER_DEPLOY_SECURITY_REVIEW_APP_ID RUNNER_DEPLOY_SECURITY_REVIEW_CHECK RUNNER_DEPLOY_SECURITY_REVIEW_WORKFLOW \
  RUNNER_DEPLOY_AI_REVIEW_APP_ID RUNNER_DEPLOY_AI_REVIEW_CHECK RUNNER_DEPLOY_AI_REVIEW_WORKFLOW; do
  ! grep -q "$authority_variable" "$workflow"
done
grep -q 'actions/artifacts/{artifact' "$workflow"
grep -q 'review-receipt.json' "$workflow"
! grep -qF '.github/workflows/adversarial-code-review.yml' "$workflow"
! grep -qF '.github/workflows/adversarial-security-review.yml' "$workflow"
grep -qF '"sha256:" + hashlib.sha256(archive).hexdigest()' "$workflow"
grep -qF 'pulls/{pull[' "$workflow"
# The literal shell variable must never become a path argument.
# shellcheck disable=SC2016
if grep -q -- '--rollback-source "\$ROLLBACK_RECEIPT"' "$workflow"; then
  echo "rollback receipt identity is used as a filesystem path" >&2
  exit 1
fi
grep -q 'verjson-cloud' "$root/scripts/container_deployment_controller.py"
grep -q '"--only"' "$root/scripts/container_deployment_controller.py"
grep -q 'def _validate_runner_admission' "$root/scripts/container_deployment_controller.py"
grep -q 'control=True' "$root/scripts/container_deployment_controller.py"
! grep -q 'SSH_AUTH_SOCK' "$root/scripts/container_deployment_controller.py"
grep -q 'def complete_workflow_runs' "$root/scripts/container_deployment_transport.py"
grep -q 'MAX_RUN_RECORDS' "$root/scripts/container_deployment_transport.py"
if grep -vF 'actions/create-github-app-token@' "$workflow" \
  | grep -E 'doctl|ssh |droplet|--replicas|--standard|resize|create' >/dev/null; then
  echo "reusable workflow contains fleet mechanics or a spend-increasing operation" >&2
  exit 1
fi
if grep -Eq 'doctl|droplet|--replicas|--standard|resize|create' \
    "$root/scripts/container_deployment_controller.py"; then
  echo "deployment controller contains a spend-increasing operation" >&2
  exit 1
fi

python3 - "$workflow" "$consumer" "$ref" <<'PY'
import os
from pathlib import Path
import subprocess
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    workflow = yaml.safe_load(stream)
trigger = workflow.get("on", workflow.get(True))
assert set(trigger) == {"workflow_call"}
jobs = workflow["jobs"]
entry_guard = jobs["verify-default-branch"]
assert entry_guard.get("environment") is None
assert entry_guard.get("permissions") == {}
entry_step = entry_guard["steps"][0]
assert entry_step["env"] == {
    "DEFAULT_BRANCH": "${{ github.event.repository.default_branch }}",
    "ENTRY_REF": "${{ github.ref }}",
}
for entry_ref, expected_status in (
    ("refs/heads/main", 0),
    ("refs/heads/feature", 1),
):
    result = subprocess.run(
        ["bash", "-c", entry_step["run"]],
        env={**os.environ, "DEFAULT_BRANCH": "main", "ENTRY_REF": entry_ref},
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected_status, result.stderr
for job_name in ("dry-run", "deploy"):
    assert jobs[job_name]["needs"] == "verify-default-branch"
admit_step = next(
    step for step in jobs["deploy"]["steps"]
    if step.get("name") == "Admit immutable sequential plan"
)
for expected_argument in (
    '--fleet "$FLEET_SELECTOR"',
    '--action "$ACTION"',
    '--contract-ref "$CONTRACT_REF"',
):
    assert expected_argument in admit_step["run"]
assert jobs["deploy"]["environment"] == "production"
assert jobs["dry-run"]["environment"] == "production"
preview_step = next(
    step for step in jobs["dry-run"]["steps"]
    if step.get("name") == "Produce exact mutation-free host plan"
)
assert preview_step["run"].count("--preview") == 2
assert all("--preview" not in step.get("run", "") for step in jobs["deploy"]["steps"])
assert workflow["concurrency"]["cancel-in-progress"] is False
host_secrets = {
    "RUNNER_HOST_EVIDENCE_APP_PRIVATE_KEY",
    "RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY",
    "RUNNER_HOST_EVIDENCE_DOCTL_CONFIG",
    "RUNNER_HOST_EVIDENCE_KNOWN_HOSTS",
}
for job_name in ("dry-run", "deploy"):
    for step in jobs[job_name]["steps"]:
        run = step.get("run", "")
        if ("container_deployment_controller.py collect-evidence" in run
                or "container_deployment_controller.py execute" in run):
            assert host_secrets <= set(step.get("env", {})), (
                f"{job_name}: host-export secrets must be step-scoped to controller operations"
            )
assert set(workflow["permissions"]) == {
    "actions", "attestations", "checks", "contents", "packages", "pull-requests"
}
for job_name in ("dry-run", "deploy"):
    steps = jobs[job_name]["steps"]
    setup = [step for step in steps if step.get("uses", "").startswith("actions/setup-node@")]
    assert len(setup) == 1
    assert setup[0]["uses"] == "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020"
    assert setup[0]["with"]["node-version"] == "24.19.0"
    binding = [step for step in steps if step.get("name") == "Verify caller binds this immutable contract"]
    assert len(binding) == 1
    acquisition = [step for step in steps if step.get("name") == "Acquire immutable deployment CLI"]
    assert len(acquisition) == 1
    assert "NPM_CONFIG_CACHE" in acquisition[0]["env"]
    cleanup = [step for step in steps if step.get("name") == "Remove deployment acquisition state"]
    assert len(cleanup) == 1
    assert cleanup[0]["if"] == "${{ always() }}"
    assert "deployment-contract.tar.gz" in cleanup[0]["run"]
    assert "deployment-contract" in cleanup[0]["run"]
    assert "NPM_CONFIG_CACHE" in cleanup[0]["run"]

consumer = Path(sys.argv[2])
contract = sys.argv[3]
binding_script = next(
    step["run"] for step in jobs["dry-run"]["steps"]
    if step.get("name") == "Verify caller binds this immutable contract"
)
environment = os.environ | {
    "CONTRACT_REF": contract,
    "GITHUB_REPOSITORY": "fixture/consumer",
    "GITHUB_WORKSPACE": str(consumer),
    "WORKFLOW_REF": "fixture/consumer/.github/workflows/container-deployment.yml@refs/heads/main",
}
subprocess.run(["bash", "-c", binding_script], check=True, env=environment, cwd=consumer)
caller = consumer / ".github/workflows/container-deployment.yml"
original = caller.read_text(encoding="utf-8")
caller.write_text(original.replace(f"contract-ref: {contract}", "contract-ref: " + "0" * 40), encoding="utf-8")
rejected = subprocess.run(
    ["bash", "-c", binding_script], env=environment, cwd=consumer,
    capture_output=True, text=True,
)
assert rejected.returncode != 0
caller.write_text(original, encoding="utf-8")
mutation_steps = [
    step for step in jobs["deploy"]["steps"]
    if step.get("name", "").startswith("Advance ")
]
assert len(mutation_steps) == 3
expected_mutation_env = {
    "CONFIG_PATH": "${{ inputs.config-path }}",
    "GH_RUNNER_CONTROL_TOKEN": "${{ steps.runner-app-token.outputs.token }}",
    "DIGITALOCEAN_RUNNER_FLEET_TOKEN": "${{ secrets.DIGITALOCEAN_RUNNER_FLEET_TOKEN }}",
    "RUNNER_HOST_EVIDENCE_APP_PRIVATE_KEY": "${{ secrets.RUNNER_HOST_EVIDENCE_APP_PRIVATE_KEY }}",
    "RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY": "${{ secrets.RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY }}",
    "RUNNER_HOST_EVIDENCE_DOCTL_CONFIG": "${{ secrets.RUNNER_HOST_EVIDENCE_DOCTL_CONFIG }}",
    "RUNNER_HOST_EVIDENCE_KNOWN_HOSTS": "${{ secrets.RUNNER_HOST_EVIDENCE_KNOWN_HOSTS }}",
}
assert all(step["env"] == expected_mutation_env for step in mutation_steps)
mint = next(step for step in jobs["deploy"]["steps"] if step.get("id") == "runner-app-token")
assert mint["uses"] == "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
assert mint["with"] == {
    "client-id": "${{ vars.GH_RUNNER_REGISTRATION_APP_CLIENT_ID }}",
    "private-key": "${{ secrets.GH_RUNNER_REGISTRATION_APP_PRIVATE_KEY }}",
    "owner": "${{ github.repository_owner }}",
    "repositories": "${{ github.event.repository.name }}",
    "permission-organization-self-hosted-runners": "write",
}
verify_installation = next(
    step for step in jobs["deploy"]["steps"]
    if step.get("name") == "Verify runner App installation identity"
)
assert "outputs.installation-id" in str(verify_installation)
assert "GH_RUNNER_REGISTRATION_APP_INSTALLATION_ID" in str(verify_installation)
dry_uploads = [
    step for step in jobs["dry-run"]["steps"]
    if step.get("uses", "").startswith("actions/upload-artifact@")
]
assert len(dry_uploads) == 1
assert dry_uploads[0]["with"]["path"] == "deployment-plan.json"
for step in jobs["deploy"]["steps"]:
    if step not in mutation_steps:
        assert "DIGITALOCEAN_RUNNER_FLEET_TOKEN" not in str(step)
        assert "GH_RUNNER_CONTROL_TOKEN" not in str(step)
PY

echo "container deployment generated contract and protected reusable workflow passed"
