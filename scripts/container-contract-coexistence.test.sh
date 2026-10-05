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
  "$root/scripts/gen-container-candidate.sh" \
  "$root/scripts/gen-container-release.sh" \
  "$root/scripts/gen-container-deployment.sh" \
  "$root/scripts/container_release_promotion.py" \
  "$root/scripts/container_release_manifest.py" \
  "$root/scripts/container_private_dependencies.py" \
  "$root/scripts/container_dependency_transfer.py" \
  "$root/scripts/container_candidate_retry.py" \
  "$root/scripts/container_registry_destinations.py" \
  "$root/scripts/container_artifact_extract.py" \
  "$root/scripts/container_attestation_verify.py" \
  "$root/scripts/container_cosign_provenance.py" \
  "$root/scripts/container_deployment_controller.py" \
  "$root/scripts/container_deployment_transport.py" \
  "$root/scripts/container_deployment_preflight.py" \
  "$root/scripts/container_deployment_review_producer.py" \
  "$root/scripts/validate-container-deployment-cli-lock.py" \
  "$contract/scripts/"
cp \
  "$root/.github/workflows/container-deployment-review-producer.yml" \
  "$contract/.github/workflows/"
cp "$root/docs/decisions/0078-container-release-and-runner-deployment-contract/deployment-receipt.schema.json" \
  "$contract/docs/decisions/0078-container-release-and-runner-deployment-contract/"
cp \
  "$root/contracts/container-deployment-cli/package.json" \
  "$root/contracts/container-deployment-cli/package-lock.json" \
  "$root/contracts/container-deployment-cli/.npmrc" \
  "$contract/contracts/container-deployment-cli/"
git -C "$contract" init -q
git -C "$contract" config user.name fixture
git -C "$contract" config user.email fixture@example.invalid
git -C "$contract" add .github contracts scripts docs
git -C "$contract" commit -qm fixture
ref="$(git -C "$contract" rev-parse HEAD)"
cp "$root/scripts/fixtures/container-candidate/single.json" "$consumer/container-candidate.json"

candidate="$contract/scripts/gen-container-candidate.sh"
release="$contract/scripts/gen-container-release.sh"
deployment="$contract/scripts/gen-container-deployment.sh"
(cd "$consumer" && "$candidate" workflow "$ref" container-candidate.json) >"$consumer/.github/workflows/container-candidate.yml"
"$candidate" validator "$ref" container-candidate.json >"$consumer/scripts/container_release_manifest.py"
"$candidate" destination-helper "$ref" container-candidate.json >"$consumer/scripts/container_registry_destinations.py"
candidate_validator_digest="$(sha256sum "$consumer/scripts/container_release_manifest.py")"
(cd "$consumer" && "$candidate" contract-test "$ref" container-candidate.json) >"$consumer/scripts/container-candidate-contract.test.sh"

"$release" workflow "$ref" container-candidate.json >"$consumer/.github/workflows/container-release.yml"
"$release" validator "$ref" container-candidate.json >"$consumer/scripts/container_release_promotion.py"
"$release" manifest-validator "$ref" container-candidate.json >"$consumer/scripts/container_release_manifest.py"
"$release" destination-helper "$ref" container-candidate.json >"$consumer/scripts/container_registry_destinations.py"
release_validator_digest="$(sha256sum "$consumer/scripts/container_release_manifest.py")"
"$release" artifact-extractor "$ref" container-candidate.json >"$consumer/scripts/container_artifact_extract.py"
"$release" attestation-verifier "$ref" container-candidate.json >"$consumer/scripts/container_attestation_verify.py"
"$release" cosign-helper "$ref" container-candidate.json >"$consumer/scripts/container_cosign_provenance.py"
"$release" contract-test "$ref" container-candidate.json >"$consumer/scripts/container-release-contract.test.sh"

"$deployment" workflow "$ref" container-deployment.json >"$consumer/.github/workflows/container-deployment.yml"
"$deployment" code-review-workflow "$ref" >"$consumer/.github/workflows/container-deployment-code-review.yml"
"$deployment" security-review-workflow "$ref" >"$consumer/.github/workflows/container-deployment-security-review.yml"
"$deployment" ai-review-workflow "$ref" >"$consumer/.github/workflows/container-deployment-ai-review.yml"
"$deployment" review-producer-workflow "$ref" >"$consumer/.github/workflows/container-deployment-review-producer.yml"
"$deployment" review-producer "$ref" >"$consumer/scripts/container_deployment_review_producer.py"
"$deployment" controller "$ref" >"$consumer/scripts/container_deployment_controller.py"
"$deployment" transport "$ref" >"$consumer/scripts/container_deployment_transport.py"
"$deployment" preflight "$ref" >"$consumer/scripts/container_deployment_preflight.py"
"$deployment" receipt-schema "$ref" >"$consumer/scripts/deployment-receipt.schema.json"
"$deployment" contract-test "$ref" container-deployment.json >"$consumer/scripts/container-deployment-contract.test.sh"
cat >"$consumer/container-deployment.json" <<JSON
 {"schemaVersion":1,"reviewAuthority":{"code":{"appId":201,"installationId":301,"checkName":"runner-deploy-code-review","workflowPath":".github/workflows/container-deployment-code-review.yml"},"security":{"appId":202,"installationId":302,"checkName":"runner-deploy-security-review","workflowPath":".github/workflows/container-deployment-security-review.yml"},"ai":{"appId":203,"installationId":303,"sourceAppId":403,"sourceCheckName":"canonical-ai-review","checkName":"runner-deploy-ai-review","workflowPath":".github/workflows/container-deployment-ai-review.yml"}},"hostEvidenceAuthority":{"appId":204,"installationId":304},"cliCommand":["verjson-cloud"],"evidenceCommand":["python3","scripts/evidence.py"],"probeCommand":["python3","scripts/probe.py"],"expectedRelease":{"repository":"ghcr.io/verjson/example","sourceRepository":"Verjson/example","sourceRef":"refs/heads/main","signerWorkflow":"Verjson/.github/.github/workflows/container-release.yml","contractCommit":"$ref","variant":"runner"},"fleets":{"production":{"lane":"gate","project":"existing","hostEvidence":{"doContext":"read-only","doSshKey":"read-only","maxAgeSeconds":300},"canary":"gha-gate-1","runners":["gha-gate-1","gha-gate-2"],"minimumAvailable":1,"drainTimeoutSeconds":600,"probeTimeoutSeconds":300,"observationSeconds":120,"runnerGroup":"trusted","requiredLabels":["gate"],"requiredTools":["pwsh"]}}}
JSON
printf '#!/usr/bin/env python3\n' >"$consumer/scripts/evidence.py"
printf '#!/usr/bin/env python3\n' >"$consumer/scripts/probe.py"

[ "$candidate_validator_digest" = "$release_validator_digest" ] || {
  echo "candidate and release manifest validators differ" >&2
  exit 1
}
chmod +x "$consumer/scripts/"*.py "$consumer/scripts/"*.sh
(
  cd "$consumer"
  bash scripts/container-candidate-contract.test.sh
  bash scripts/container-release-contract.test.sh
  bash scripts/container-deployment-contract.test.sh
)

echo "container candidate, release, and deployment generated contracts coexist"
