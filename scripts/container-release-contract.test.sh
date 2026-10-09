#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"; tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/contract/scripts" "$tmp/consumer/.github/workflows" "$tmp/consumer/scripts"
cp "$root/scripts/gen-container-release.sh" "$root/scripts/changelog.py" "$root/scripts/container_release_promotion.py" "$root/scripts/container_release_manifest.py" "$root/scripts/container_registry_destinations.py" "$root/scripts/container_artifact_extract.py" "$root/scripts/container_attestation_verify.py" "$root/scripts/container_cosign_provenance.py" "$tmp/contract/scripts/"
git -C "$tmp/contract" init -q; git -C "$tmp/contract" config user.name fixture; git -C "$tmp/contract" config user.email fixture@example.invalid
git -C "$tmp/contract" add scripts; git -C "$tmp/contract" commit -qm fixture; ref="$(git -C "$tmp/contract" rev-parse HEAD)"
generator="$tmp/contract/scripts/gen-container-release.sh"
"$generator" workflow "$ref" >"$tmp/consumer/.github/workflows/container-release.yml"
"$generator" validator "$ref" >"$tmp/consumer/scripts/container_release_promotion.py"
"$generator" manifest-validator "$ref" >"$tmp/consumer/scripts/container_release_manifest.py"
"$generator" destination-helper "$ref" >"$tmp/consumer/scripts/container_registry_destinations.py"
"$generator" artifact-extractor "$ref" >"$tmp/consumer/scripts/container_artifact_extract.py"
"$generator" attestation-verifier "$ref" >"$tmp/consumer/scripts/container_attestation_verify.py"
"$generator" cosign-helper "$ref" >"$tmp/consumer/scripts/container_cosign_provenance.py"
"$generator" contract-test "$ref" >"$tmp/consumer/scripts/container-release-contract.test.sh"
(cd "$tmp/consumer" && bash scripts/container-release-contract.test.sh)
mkdir -p "$tmp/consumer/NEXT"
cat >"$tmp/consumer/NEXT/2026-08-24-issue-1013-clean-adopter.md" <<'EOF'
---
date: 2026-08-24
issue: 1013
impact: major
title: Exercise a clean generated container release adopter
---

Release-path fixture.
EOF
git -C "$tmp/consumer" init -q
git -C "$tmp/consumer" config user.name fixture
git -C "$tmp/consumer" config user.email fixture@example.invalid
git -C "$tmp/consumer" add .
git -C "$tmp/consumer" commit -qm fixture
mkdir -p "$tmp/consumer/.container-release-contract/scripts"
git -C "$tmp/contract" show "$ref:scripts/changelog.py" >"$tmp/consumer/.container-release-contract/scripts/changelog.py"
(cd "$tmp/consumer" && python3 .container-release-contract/scripts/changelog.py release --version v1.0.0)
test -f "$tmp/consumer/CHANGELOG/v1.0.0.md"
test "$(git -C "$tmp/consumer" tag --list)" = v1.0.0
test ! -e "$tmp/consumer/scripts/changelog.py"
cp "$tmp/consumer/.github/workflows/container-release.yml" "$tmp/workflow.clean"
reject_caller_mutation() {
  if (cd "$tmp/consumer" && bash scripts/container-release-contract.test.sh >/dev/null 2>&1); then
    echo "generated contract accepted caller drift: $1" >&2
    exit 1
  fi
  cp "$tmp/workflow.clean" "$tmp/consumer/.github/workflows/container-release.yml"
}
sed -i 's/^  packages: write$/  packages: read/' "$tmp/consumer/.github/workflows/container-release.yml"
reject_caller_mutation 'package permission'
sed -i 's/vars.RELEASE_APP_CLIENT_ID/vars.OTHER_CLIENT_ID/' "$tmp/consumer/.github/workflows/container-release.yml"
reject_caller_mutation 'App client ID mapping'
sed -i 's/release_environment: release-app/release_environment: unguarded/' "$tmp/consumer/.github/workflows/container-release.yml"
reject_caller_mutation 'App environment selection'
printf '\n    secrets: inherit\n' >> "$tmp/consumer/.github/workflows/container-release.yml"
reject_caller_mutation 'inherited secrets'
if "$generator" validator "$(printf 'a%.0s' {1..40})" >/dev/null 2>&1; then
  echo "validator generation resolved a nonexistent pin from local files" >&2; exit 1
fi
if "$generator" workflow "$ref" ../hostile.json >/dev/null 2>&1; then
  echo "generator accepted a config path outside the source commit" >&2; exit 1
fi

# --- opt-in pre-credential reconciliation hook (#1203, ADR 0158) ------------------
# A consumer that does not configure reconciliation must see byte-identical output.
"$generator" workflow "$ref" >"$tmp/workflow.unconfigured"
cmp -s "$tmp/workflow.clean" "$tmp/workflow.unconfigured" \
  || { echo "unconfigured reconciliation changed the generated caller" >&2; exit 1; }
if grep -q 'reconcile-allowlist' "$tmp/workflow.unconfigured"; then
  echo "unconfigured caller declares a reconciliation allowlist" >&2; exit 1
fi

reject_generation() {
  if "$generator" workflow "$ref" container-candidate.json --reconcile-allow "$1" >/dev/null 2>&1; then
    echo "generator accepted an unsafe reconciliation path: $1" >&2; exit 1
  fi
}
reject_generation '../hostile'
reject_generation '/etc/passwd'
reject_generation './Dockerfile'
reject_generation '.github/workflows/container-release.yml'
reject_generation '.gitattributes'
reject_generation 'RELEASES/containers/v1.0.0.json'
reject_generation 'CHANGELOG/v1.0.0.md'
reject_generation 'NEXT/entry.md'
reject_generation 'scripts/release-reconcile.sh'
reject_generation 'scripts/container_release_promotion.py'
reject_generation 'scripts/container_registry_destinations.py'
reject_generation 'Docker file'
reject_generation '$(id)'
if "$generator" workflow "$ref" container-candidate.json \
  --reconcile-allow Dockerfile --reconcile-allow Dockerfile >/dev/null 2>&1; then
  echo "generator accepted a duplicate reconciliation path" >&2; exit 1
fi
if "$generator" validator "$ref" --reconcile-allow Dockerfile >/dev/null 2>&1; then
  echo "generator accepted reconciliation flags on a non-caller artifact" >&2; exit 1
fi

mkdir -p "$tmp/adopter/.github/workflows" "$tmp/adopter/scripts" "$tmp/adopter/deploy"
"$generator" workflow "$ref" container-candidate.json \
  --reconcile-allow Dockerfile --reconcile-allow deploy/values.yaml \
  >"$tmp/adopter/.github/workflows/container-release.yml"
grep -Fqx "      reconcile-allowlist: '[\"Dockerfile\",\"deploy/values.yaml\"]'" \
  "$tmp/adopter/.github/workflows/container-release.yml" \
  || { echo "configured caller does not bake in the reviewed allowlist" >&2; exit 1; }
grep -q 'workflow_dispatch' "$tmp/adopter/.github/workflows/container-release.yml"
if grep -A6 '^    inputs:$' "$tmp/adopter/.github/workflows/container-release.yml" | grep 'reconcile' >/dev/null; then
  echo "reconciliation allowlist is dispatch-controlled" >&2; exit 1
fi
"$generator" validator "$ref" >"$tmp/adopter/scripts/container_release_promotion.py"
"$generator" manifest-validator "$ref" >"$tmp/adopter/scripts/container_release_manifest.py"
"$generator" destination-helper "$ref" >"$tmp/adopter/scripts/container_registry_destinations.py"
"$generator" artifact-extractor "$ref" >"$tmp/adopter/scripts/container_artifact_extract.py"
"$generator" attestation-verifier "$ref" >"$tmp/adopter/scripts/container_attestation_verify.py"
"$generator" cosign-helper "$ref" >"$tmp/adopter/scripts/container_cosign_provenance.py"
"$generator" contract-test "$ref" container-candidate.json \
  --reconcile-allow Dockerfile --reconcile-allow deploy/values.yaml \
  >"$tmp/adopter/scripts/container-release-contract.test.sh"
printf 'FROM ghcr.io/verjson/base:v1.0.0\n' >"$tmp/adopter/Dockerfile"
printf 'tag: v1.0.0\n' >"$tmp/adopter/deploy/values.yaml"
if (cd "$tmp/adopter" && bash scripts/container-release-contract.test.sh >/dev/null 2>&1); then
  echo "configured contract test passed without the reviewed hook present" >&2; exit 1
fi
printf '#!/usr/bin/env bash\nexit 0\n' >"$tmp/adopter/scripts/release-reconcile.sh"
chmod +x "$tmp/adopter/scripts/release-reconcile.sh"
(cd "$tmp/adopter" && bash scripts/container-release-contract.test.sh >/dev/null)
chmod -x "$tmp/adopter/scripts/release-reconcile.sh"
if (cd "$tmp/adopter" && bash scripts/container-release-contract.test.sh >/dev/null 2>&1); then
  echo "configured contract test accepted a non-executable hook" >&2; exit 1
fi
chmod +x "$tmp/adopter/scripts/release-reconcile.sh"
rm "$tmp/adopter/deploy/values.yaml"
ln -s /etc/passwd "$tmp/adopter/deploy/values.yaml"
if (cd "$tmp/adopter" && bash scripts/container-release-contract.test.sh >/dev/null 2>&1); then
  echo "configured contract test accepted a symlinked allowlist target" >&2; exit 1
fi
rm "$tmp/adopter/deploy/values.yaml"
printf 'tag: v1.0.0\n' >"$tmp/adopter/deploy/values.yaml"
sed -i "s/reconcile-allowlist: '\\[\"Dockerfile\",\"deploy\\/values.yaml\"\\]'/reconcile-allowlist: '[\"Dockerfile\",\"docs.md\"]'/" \
  "$tmp/adopter/.github/workflows/container-release.yml"
if (cd "$tmp/adopter" && bash scripts/container-release-contract.test.sh >/dev/null 2>&1); then
  echo "configured contract test accepted a widened allowlist" >&2; exit 1
fi
printf '#!/usr/bin/env bash\nexit 0\n' >"$tmp/consumer/scripts/release-reconcile.sh"
chmod +x "$tmp/consumer/scripts/release-reconcile.sh"
if (cd "$tmp/consumer" && bash scripts/container-release-contract.test.sh >/dev/null 2>&1); then
  echo "unconfigured contract test accepted an undeclared reconciliation hook" >&2; exit 1
fi
rm "$tmp/consumer/scripts/release-reconcile.sh"
(cd "$tmp/consumer" && bash scripts/container-release-contract.test.sh >/dev/null)
# --- end reconciliation hook ------------------------------------------------------
workflow="$root/.github/workflows/container-release.yml"
WORKFLOW="$workflow" python3 - <<'PY'
import copy
import os
import yaml

with open(os.environ["WORKFLOW"], encoding="utf-8") as stream:
    workflow = yaml.safe_load(stream)

def assert_provenance_boundary(document):
    jobs = document["jobs"]
    assert set(jobs) == {"app-key-policy", "promote", "retention"}, "release job graph is not exact"
    assert jobs["promote"]["runs-on"] == "ubuntu-24.04", (
        "release promotion and manifest attestation must use an independently trusted hosted runner"
    )
    assert jobs["promote"].get("env", {}).get("GIT_NO_REPLACE_OBJECTS") == "1", (
        "every Git invocation in release promotion must ignore replacement refs"
    )
    retention = jobs["retention"]
    assert retention["timeout-minutes"] == 30
    assert retention["continue-on-error"] is True
    assert retention["permissions"] == {"contents": "read", "packages": "write"}, (
        "retention must remain the sole non-provenance runner exception"
    )
    steps = jobs["promote"]["steps"]
    sandbox = next(
        step for step in steps
        if step.get("name") == "Prepare the release reconciliation sandbox"
    )
    reconcile = next(
        step for step in steps
        if step.get("name") == "Reconcile derived release inputs before credential minting"
    )
    token = next(step for step in steps if step.get("name") == "Mint exact-repository release App token")
    assert sandbox["if"] == "${{ inputs.reconcile-allowlist != '' }}"
    credentialed_steps = [
        index for index, step in enumerate(steps)
        if "docker/login-action@" in step.get("uses", "")
        or "google-github-actions/auth@" in step.get("uses", "")
        or step.get("name") == "Mint exact-repository release App token"
    ]
    assert credentialed_steps and all(
        steps.index(sandbox) < index for index in credentialed_steps
    ), "sandbox package setup must finish before registry or release credentials are acquired"
    assert steps.index(sandbox) < steps.index(reconcile) < steps.index(token), (
        "the pinned hook must run only after sandbox setup and before release credentials are minted"
    )
    sandbox_run = sandbox["run"]
    for required in (
        "apt-get install --no-install-recommends --yes apparmor apparmor-profiles bubblewrap",
        "verify_package_floor bubblewrap '0.9.0-1build1'",
        "verify_package_floor apparmor '4.0.1really4.0.1-0ubuntu0.24.04.3'",
        "verify_package_floor apparmor-profiles '4.0.1really4.0.1-0ubuntu0.24.04.3'",
        "/usr/share/apparmor/extra-profiles/bwrap-userns-restrict",
        "parser='/usr/sbin/apparmor_parser'",
        '[ "$(dpkg-query -S "$parser")" = "apparmor: $parser" ]',
        'sudo "$parser" --replace "$profile"',
        "--unshare-user --unshare-pid --unshare-net --unshare-ipc --unshare-uts",
        "--disable-userns --cap-drop ALL",
        "-- /usr/bin/true",
    ):
        assert required in sandbox_run, f"sandbox setup is missing {required!r}"
    assert "dpkg-query -S /sbin/apparmor_parser" not in sandbox_run, (
        "package ownership must be checked using Ubuntu's canonical /usr/sbin path"
    )

assert_provenance_boundary(workflow)

extra_job = copy.deepcopy(workflow)
extra_job["jobs"]["resign"] = {
    "runs-on": '${{ fromJSON(vars.CI_LANE_TRUSTED) }}',
    "permissions": {"id-token": "write", "attestations": "write"},
    "steps": [],
}
try:
    assert_provenance_boundary(extra_job)
    raise AssertionError("release contract accepted an extra self-hosted attestation job")
except AssertionError as error:
    assert "job graph is not exact" in str(error)

elevated_retention = copy.deepcopy(workflow)
elevated_retention["jobs"]["retention"]["permissions"]["id-token"] = "write"
elevated_retention["jobs"]["retention"]["permissions"]["attestations"] = "write"
try:
    assert_provenance_boundary(elevated_retention)
    raise AssertionError("release contract accepted provenance authority in retention")
except AssertionError as error:
    assert "sole non-provenance" in str(error)
PY
grep -A8 '^  retention:$' "$workflow" | grep -x '    timeout-minutes: 30' >/dev/null
assert_python3_extractor() {
  local candidate=$1
  [ "$(grep -cE '^          python3 scripts/container_artifact_extract\.py candidate\.zip candidate\.json candidate-manifest\.sigstore\.json$' "$candidate")" -eq 1 ] &&
    ! grep -Eq '^ +python scripts/container_artifact_extract\.py candidate\.zip candidate\.json candidate-manifest\.sigstore\.json$' "$candidate"
}
assert_python3_extractor "$workflow"
cp "$workflow" "$tmp/container-release-python-mutation.yml"
sed -i 's/^          python3 scripts\/container_artifact_extract\.py candidate\.zip candidate\.json candidate-manifest\.sigstore\.json$/          python scripts\/container_artifact_extract.py candidate.zip candidate.json/' \
  "$tmp/container-release-python-mutation.yml"
if assert_python3_extractor "$tmp/container-release-python-mutation.yml"; then
  echo "container release contract accepted a python extractor mutation" >&2
  exit 1
fi
grep -q "github.event_name == 'workflow_dispatch'" "$workflow"
! grep -Eq '^  (push|pull_request):' "$workflow"
grep -q 'imagetools create' "$workflow"
! grep -Eq 'build-push-action|docker build|deploy|verjson-cli-cloud' "$workflow"
grep -q 'Mint exact-repository release App token' "$workflow"
# Atomic, and with repository hooks disabled: `.git/hooks` is untracked, so nothing
# in it was reviewed, and this command holds the release App token (ADR 0158).
grep -q 'git -c core.hooksPath=/dev/null push --atomic' "$workflow"
grep -q 'docker/login-action@' "$workflow"
! grep -q 'secrets.release-token' "$workflow"
legacy_release_token='RELEASE_'"TOKEN"
legacy_org_release_token='VERJSON_RELEASE_'"TOKEN"
! grep -Eq "$legacy_release_token|$legacy_org_release_token" "$workflow"
! grep -Eq 'gh attestation verify|actions/attest-build-provenance@' "$workflow"
grep -q 'cosign verify-blob candidate.json' "$workflow"
grep -q 'google-github-actions/auth@' "$workflow"
grep -q 'oras-project/setup-oras@' "$workflow"
grep -q 'container_cosign_provenance.py' "$workflow"
grep -q -- '--repository-id "$GITHUB_REPOSITORY_ID"' "$workflow"
grep -q -- '--expected-repository "$GITHUB_REPOSITORY"' "$workflow"
grep -q -- '--expected-source-ref refs/heads/main' "$workflow"
grep -q -- '--expected-source-commit "$(jq -er .head_sha candidate-run.json)"' "$workflow"
grep -q -- '--contract-ref "$CONTRACT_REF"' "$workflow"
grep -q -- '--cosign-helper ".container-release-contract/scripts/container_cosign_provenance.py"' "$workflow"
grep -q 'candidate has no reviewed GAR destination' "$workflow"
grep -q 'release-manifest.sigstore.json' "$workflow"
grep -Fq 'mv candidate.zip candidate-manifest.zip' "$workflow"
grep -Fq 'gh release download "v$VERSION" --pattern candidate-manifest.zip --dir "$tmp"' "$workflow"
grep -Fq 'cmp -s "$tmp/candidate-manifest.zip" candidate-manifest.zip' "$workflow"
grep -Fq 'release-manifest.sigstore.json candidate-manifest.zip receipts/*.json' "$workflow"
grep -q 'container_attestation_verify.py' "$workflow"
grep -q 'repository: Verjson/.github' "$workflow"
grep -q 'ref: \${{ inputs.contract-ref }}' "$workflow"
grep -q 'python3 .container-release-contract/scripts/changelog.py release' "$workflow"
grep -Fq 'GIT_NO_REPLACE_OBJECTS=1 git -C .container-release-contract show \' "$workflow"
grep -Fq '"$CONTRACT_REF:scripts/changelog.py" \' "$workflow"
grep -Fq '| cmp -s .container-release-contract/scripts/changelog.py - \' "$workflow"
engine_guard_line="$(grep -n 'GIT_NO_REPLACE_OBJECTS=1 git -C .container-release-contract show' "$workflow" | cut -d: -f1)"
engine_run_line="$(grep -n 'python3 .container-release-contract/scripts/changelog.py release' "$workflow" | cut -d: -f1)"
[ "$engine_guard_line" -lt "$engine_run_line" ]
! grep -Eq 'python3? scripts/changelog.py release' "$workflow"
! grep -Eq 'gh attestation verify|actions/attest-build-provenance@' "$workflow"
! grep -q 'container-release-${{ github.repository }}-${{ inputs.version }}' "$workflow"
grep -q 'group: container-release-${{ github.repository }}' "$workflow"
grep -q 'complete stable alias set diverged' "$workflow"
grep -q 'continue-on-error: true' "$workflow"
grep -q 'package_retention.py' "$workflow"
grep -q 'packages: write' "$workflow"
grep -q 'needs: promote' "$workflow"
grep -q 'retention-targets' "$workflow"
grep -q 'Validate the immutable retention contract ref' "$workflow"
grep -q '\^\[0-9a-f\]{40}\$' "$workflow"
guard_line="$(grep -n 'Validate the immutable retention contract ref' "$workflow" | tail -1 | cut -d: -f1)"
checkout_line="$(grep -n 'path: .package-retention-contract' "$workflow" | cut -d: -f1)"
[ "$guard_line" -lt "$checkout_line" ]
grep -qx '          python3 scripts/container_artifact_extract.py candidate.zip candidate.json candidate-manifest.sigstore.json' "$workflow"
grep -qx '          python3 scripts/container_attestation_verify.py --candidate candidate.json \\' "$workflow"
grep -qx '          python3 scripts/container_release_promotion.py --candidate candidate.json \\' "$workflow"
! grep -Eq '^[[:space:]]+python scripts/container_(artifact_extract|attestation_verify|release_promotion)\.py' "$workflow"
grep -Fq 'candidate_artifact_digest="${BASH_REMATCH[2]}"' "$workflow"
! grep -Fq 'candidate_artifact_digest="${BASH_REMATCH[1]}"' "$workflow"
grep -Fq '[[ "$CANDIDATE_MANIFEST" =~ ^([0-9]+)@(sha256:[0-9a-f]{64})$ ]]' "$workflow"
candidate_identity="9631224154@sha256:70be36dfceee65146fd9942840a227d498506c1edc61af53ed4cb6e642b8d3cd"
[[ "$candidate_identity" =~ ^([0-9]+)@(sha256:[0-9a-f]{64})$ ]]
[ "${BASH_REMATCH[1]}" = "9631224154" ]
[ "${BASH_REMATCH[2]}" = "sha256:70be36dfceee65146fd9942840a227d498506c1edc61af53ed4cb6e642b8d3cd" ]
grep -q 'existing tag records a divergent release manifest' "$workflow"
grep -q 'existing GitHub Release manifest diverges' "$workflow"
bash "$root/scripts/container-contract-coexistence.test.sh"
echo 'container release canonical contract passed'
