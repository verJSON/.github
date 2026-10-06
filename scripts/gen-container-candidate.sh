#!/usr/bin/env bash
# Generate one immutable container-candidate adopter artifact.
set -euo pipefail

mode="${1-}"
ref="${2-}"
config_path="${3-container-candidate.json}"
root="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
  echo "usage: $(basename "$0") {workflow|validator|destination-helper|contract-test} <40-hex-contract-ref> [config-path]" >&2
  exit 2
}

[[ "$ref" =~ ^[0-9a-f]{40}$ ]] || usage
[[ "$config_path" =~ ^([A-Za-z0-9][A-Za-z0-9._-]*/)*[A-Za-z0-9][A-Za-z0-9._-]*\.json$ ]] || {
  echo "config-path must be a repository-relative JSON path" >&2
  exit 2
}

private_package_mode() {
  local config="$PWD/$config_path"
  [ -f "$config" ] || { echo "config not found: $config_path" >&2; exit 2; }
  jq -r '
    if ((.privateNodePackages // []) | type) != "array" then
      error("privateNodePackages must be an array")
    elif ((.packageManager // "npm") | . != "npm" and . != "pnpm") then
      error("packageManager must be npm or pnpm")
    else
      ((.privateNodePackages // []) | length > 0)
    end
  ' "$config"
}

case "$mode" in
workflow)
  acquisition_sha256="$(git -C "$root" show "$ref:scripts/container_private_dependencies.py" | sha256sum | cut -d' ' -f1)"
  transfer_sha256="$(git -C "$root" show "$ref:scripts/container_dependency_transfer.py" | sha256sum | cut -d' ' -f1)"
  retry_sha256="$(git -C "$root" show "$ref:scripts/container_candidate_retry.py" | sha256sum | cut -d' ' -f1)"
  provenance_sha256="$(git -C "$root" show "$ref:scripts/container_cosign_provenance.py" | sha256sum | cut -d' ' -f1)"
  private_packages="$(private_package_mode)"
  cat <<YAML
# GENERATED FILE — do not edit by hand.
# Contract: $ref
# Regenerate with:
#   scripts/gen-container-candidate.sh workflow $ref $config_path > .github/workflows/container-candidate.yml
name: immutable container candidate

on:
  push:
    branches: [main]
  pull_request:

jobs:
  validate:
    if: github.event_name == 'pull_request'
    permissions:
      actions: read
      contents: read
    uses: Verjson/.github/.github/workflows/container-candidate.yml@$ref
    with:
      config-path: $config_path
      contract-ref: $ref
      retry-sha256: $retry_sha256
  publish:
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    permissions:
      actions: read
      contents: read
      packages: write
      id-token: write
    uses: Verjson/.github/.github/workflows/container-candidate-publish.yml@$ref
    with:
      config-path: $config_path
      contract-ref: $ref
      acquisition-sha256: $acquisition_sha256
      transfer-sha256: $transfer_sha256
      retry-sha256: $retry_sha256
      provenance-sha256: $provenance_sha256
$(if [ "$private_packages" = true ]; then printf '%s\n' '    secrets:' '      NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}'; fi)
YAML
  ;;
validator)
  cat <<HEADER
#!/usr/bin/env python3
# GENERATED FILE — do not edit by hand.
# Contract: $ref
# Source: Verjson/.github/scripts/container_release_manifest.py@$ref
HEADER
  git -C "$root" show "$ref:scripts/container_release_manifest.py" \
    | sed '1{/^#!\/usr\/bin\/env python3$/d;}'
  ;;
destination-helper)
  cat <<HEADER
#!/usr/bin/env python3
# GENERATED FILE — do not edit by hand.
# Contract: $ref
# Source: Verjson/.github/scripts/container_registry_destinations.py@$ref
HEADER
  git -C "$root" show "$ref:scripts/container_registry_destinations.py" \
    | sed '1{/^#!\/usr\/bin\/env python3$/d;}'
  ;;
contract-test)
  acquisition_sha256="$(git -C "$root" show "$ref:scripts/container_private_dependencies.py" | sha256sum | cut -d' ' -f1)"
  transfer_sha256="$(git -C "$root" show "$ref:scripts/container_dependency_transfer.py" | sha256sum | cut -d' ' -f1)"
  retry_sha256="$(git -C "$root" show "$ref:scripts/container_candidate_retry.py" | sha256sum | cut -d' ' -f1)"
  provenance_sha256="$(git -C "$root" show "$ref:scripts/container_cosign_provenance.py" | sha256sum | cut -d' ' -f1)"
  private_packages="$(private_package_mode)"
  workflow_digest="$("$0" workflow "$ref" "$config_path" | sha256sum | cut -d' ' -f1)"
  validator_digest="$("$0" validator "$ref" "$config_path" | sha256sum | cut -d' ' -f1)"
  destination_helper_digest="$("$0" destination-helper "$ref" "$config_path" | sha256sum | cut -d' ' -f1)"
  cat <<TEST
#!/usr/bin/env bash
# GENERATED FILE — do not edit by hand.
# Contract: $ref
set -euo pipefail
root="\$(cd "\$(dirname "\$0")/.." && pwd)"
caller="\$root/.github/workflows/container-candidate.yml"
validator="\$root/scripts/container_release_manifest.py"
destination_helper="\$root/scripts/container_registry_destinations.py"
fail() { echo "ERROR: \$*" >&2; exit 1; }

[ -f "\$caller" ] || fail "generated caller is missing"
[ -f "\$validator" ] || fail "generated validator is missing"
[ -f "\$destination_helper" ] || fail "generated destination helper is missing"
grep -qx '# Contract: $ref' "\$caller" || fail "caller contract pin differs"
grep -qx '# Contract: $ref' "\$validator" || fail "validator contract pin differs"
grep -qx '# Contract: $ref' "\$destination_helper" || fail "destination helper contract pin differs"
[ "\$(grep -c 'uses: Verjson/.github/.github/workflows/container-candidate.yml@$ref' "\$caller")" -eq 1 ] || fail "validation does not use the pinned read-only reusable workflow"
[ "\$(grep -c 'uses: Verjson/.github/.github/workflows/container-candidate-publish.yml@$ref' "\$caller")" -eq 1 ] || fail "publication does not use the pinned publication reusable workflow"
[ "\$(grep -c 'contract-ref: $ref' "\$caller")" -eq 2 ] || fail "caller does not pass the shared pin to both event paths"
[ "\$(grep -c 'acquisition-sha256: $acquisition_sha256' "\$caller")" -eq 1 ] || fail "only trusted publication may pin the acquisition implementation digest"
[ "\$(grep -c 'transfer-sha256: $transfer_sha256' "\$caller")" -eq 1 ] || fail "trusted publication does not pin the dependency transfer implementation digest"
[ "\$(grep -c 'retry-sha256: $retry_sha256' "\$caller")" -eq 2 ] || fail "both event paths do not pin the retry verifier digest"
[ "\$(grep -c '^      actions: read$' "\$caller")" -eq 2 ] || fail "both event paths require Actions reads"
[ "\$(grep -c '^      contents: read$' "\$caller")" -eq 2 ] || fail "both event paths require source reads"
[ "\$(grep -c 'provenance-sha256: $provenance_sha256' "\$caller")" -eq 1 ] || fail "publication does not pin provenance helper"
[ "\$(grep -c 'promotionEligible:false' "\$caller")" -eq 0 ] || fail "caller may not assert candidate eligibility"
! grep -q '^      attestations: write$' "\$caller" || fail "caller may not request GitHub Artifact Attestations"
[ "\$(grep -c '^      packages: write$' "\$caller")" -eq 1 ] || fail "only publication may write candidate images"
[ "\$(grep -c '^      id-token: write$' "\$caller")" -eq 1 ] || fail "only publication may mint attestation identity"
grep -q "if: github.event_name == 'pull_request'" "\$caller" || fail "validation is not restricted to pull requests"
grep -q "if: github.event_name == 'push' && github.ref == 'refs/heads/main'" "\$caller" || fail "publication is not restricted to trusted main pushes"
[ "\$(sha256sum "\$caller" | cut -d' ' -f1)" = "$workflow_digest" ] || fail "generated caller was edited"
[ "\$(sha256sum "\$validator" | cut -d' ' -f1)" = "$validator_digest" ] || fail "generated validator was edited"
[ "\$(sha256sum "\$destination_helper" | cut -d' ' -f1)" = "$destination_helper_digest" ] || fail "generated destination helper was edited"
if grep -Eq 'secrets: inherit|registry-namespace:|environment:' "\$caller"; then
  fail "caller may not inherit credentials or inject registry namespaces or environments"
fi
if [ "$private_packages" = true ]; then
  [ "\$(grep -cF 'NODE_AUTH_TOKEN: \${{ secrets.NODE_AUTH_TOKEN }}' "\$caller")" -eq 1 ] || fail "private-package caller must route its acquisition token only to trusted publication"
validation_job="\$(mktemp)"
awk '/^  validate:/{copy=1} /^  publish:/{copy=0} copy' "\$caller" > "\$validation_job"
if grep -Eq 'packages: read|secrets:|NODE_AUTH_TOKEN' "\$validation_job"; then
    rm -f "\$validation_job"
    fail "private-package pull-request validation exposes package credentials or contents"
  fi
  rm -f "\$validation_job"
else
  ! grep -q 'NODE_AUTH_TOKEN\|packages: read' "\$caller" || fail "public-only caller exposes package credentials or read authority"
fi
python3 "\$validator" --help >/dev/null
echo "container candidate generated contract passed"
TEST
  ;;
*)
  usage
  ;;
esac
