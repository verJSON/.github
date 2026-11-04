#!/usr/bin/env bash
set -Eeuo pipefail
# `set -e` ends the run on a failed bare assertion without saying which one, so
# name it. `-E` carries the trap into functions; a subshell keeps its own
# errexit, and its failure is reported where the parent sees it.
report_failed_assertion() {
  local status=$?
  if [ "$BASH_SUBSHELL" -eq 0 ]; then
    printf 'FAIL - %s line %s: %s (exit %s)\n' "${0##*/}" "$1" "$2" "$status" >&2
    exit "$status"
  fi
}
trap 'report_failed_assertion "$LINENO" "$BASH_COMMAND"' ERR
root="$(cd "$(dirname "$0")/.." && pwd)"; tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
# `!` exempts a command from `set -e`, so a bare `! grep` can never fail the
# run; and a grep that cannot read its input exits 2, which a negation would
# take for absence.
assert_absent() {
  local search_status=0
  grep "$@" || search_status=$?
  [ "$search_status" -eq 1 ] && return 0
  if [ "$search_status" -eq 0 ]; then
    printf 'FAIL - %s: found what must be absent: grep %s\n' "${0##*/}" "$*" >&2
  else
    printf 'FAIL - %s: could not search (grep exit %s): grep %s\n' "${0##*/}" "$search_status" "$*" >&2
  fi
  exit 1
}
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
(cd "$tmp/consumer" && python3 -I .container-release-contract/scripts/changelog.py release --version v1.0.0)
test -f "$tmp/consumer/CHANGELOG/v1.0.0.md"
test "$(git -C "$tmp/consumer" tag --list)" = v1.0.0
test ! -e "$tmp/consumer/scripts/changelog.py"
cp "$tmp/consumer/.github/workflows/container-release.yml" "$tmp/workflow.clean"
reject_caller_mutation() {
  # A mutation that matched nothing leaves the clean caller, and its acceptance
  # would be misreported as the contract missing the drift.
  if cmp -s "$tmp/workflow.clean" "$tmp/consumer/.github/workflows/container-release.yml"; then
    echo "caller mutation did not apply, so its verdict proves nothing: $1" >&2
    exit 1
  fi
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
# A generator that crashes, or that stops on a different guard, exits non-zero
# too, so each refusal names the status and the reason it must stop with.
refuses_generation() {
  local label="$1" expected_status="$2" reason="$3" status=0
  shift 3
  "$generator" "$@" >/dev/null 2>"$tmp/generation.err" || status=$?
  if [ "$status" -eq 0 ]; then
    echo "generator accepted $label" >&2; exit 1
  fi
  if [ "$status" -ne "$expected_status" ] || ! grep -qF -- "$reason" "$tmp/generation.err"; then
    echo "generator refused $label (exit $status, expected $expected_status) without the reason: $reason" >&2
    sed 's/^/diag - /' "$tmp/generation.err" >&2
    exit 1
  fi
}
missing_pin="$(printf 'a%.0s' {1..40})"
# Git, not the generator, refuses the lookup, so the pin itself is the reason.
refuses_generation 'a nonexistent validator pin' 128 "$missing_pin" validator "$missing_pin"
refuses_generation 'a config path outside the source commit' 2 \
  'config path must be a repository-relative JSON path' workflow "$ref" ../hostile.json

# --- opt-in pre-credential reconciliation hook (#1203, ADR 0158) ------------------
# A consumer that does not configure reconciliation must see byte-identical output.
"$generator" workflow "$ref" >"$tmp/workflow.unconfigured"
cmp -s "$tmp/workflow.clean" "$tmp/workflow.unconfigured" \
  || { echo "unconfigured reconciliation changed the generated caller" >&2; exit 1; }
if grep -q 'reconcile-allowlist' "$tmp/workflow.unconfigured"; then
  echo "unconfigured caller declares a reconciliation allowlist" >&2; exit 1
fi

reject_generation() {
  refuses_generation "an unsafe reconciliation path: $1" 2 "$2" \
    workflow "$ref" container-candidate.json --reconcile-allow "$1"
}
reject_generation '../hostile' '--reconcile-allow must be a normalized repository-relative path'
reject_generation '/etc/passwd' '--reconcile-allow must be a normalized repository-relative path'
reject_generation './Dockerfile' '--reconcile-allow must be a normalized repository-relative path'
reject_generation '.github/workflows/container-release.yml' '--reconcile-allow may not name a Git or workflow surface'
reject_generation '.gitattributes' '--reconcile-allow may not name a Git or workflow surface'
reject_generation 'RELEASES/containers/v1.0.0.json' '--reconcile-allow may not name a release-engine surface'
reject_generation 'CHANGELOG/v1.0.0.md' '--reconcile-allow may not name a release-engine surface'
reject_generation 'NEXT/entry.md' '--reconcile-allow may not name a release-engine surface'
reject_generation 'scripts/release-reconcile.sh' '--reconcile-allow may not name a release-engine surface'
reject_generation 'scripts/container_release_promotion.py' '--reconcile-allow may not name a release-engine surface'
reject_generation 'scripts/container_registry_destinations.py' '--reconcile-allow may not name a release-engine surface'
reject_generation 'Docker file' '--reconcile-allow must be a normalized repository-relative path'
reject_generation '$(id)' '--reconcile-allow must be a normalized repository-relative path'
refuses_generation 'a duplicate reconciliation path' 2 '--reconcile-allow paths must be unique' \
  workflow "$ref" container-candidate.json --reconcile-allow Dockerfile --reconcile-allow Dockerfile
refuses_generation 'reconciliation flags on a non-caller artifact' 2 \
  '--reconcile-allow is accepted only by workflow and contract-test' \
  validator "$ref" --reconcile-allow Dockerfile

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
cp "$tmp/adopter/.github/workflows/container-release.yml" "$tmp/adopter-workflow.before-widening"
sed -i "s/reconcile-allowlist: '\\[\"Dockerfile\",\"deploy\\/values.yaml\"\\]'/reconcile-allowlist: '[\"Dockerfile\",\"docs.md\"]'/" \
  "$tmp/adopter/.github/workflows/container-release.yml"
if cmp -s "$tmp/adopter-workflow.before-widening" "$tmp/adopter/.github/workflows/container-release.yml"; then
  echo "allowlist-widening mutation did not apply, so its verdict proves nothing" >&2; exit 1
fi
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
    assert set(jobs) == {"reject-public-pull-request-caller", "app-key-policy", "promote", "retention"}, "release job graph is not exact"
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
  [ "$(grep -cE '^          python3 -I scripts/container_artifact_extract\.py candidate\.zip candidate\.json candidate-manifest\.sigstore\.json$' "$candidate")" -eq 1 ] &&
    ! grep -Eq '^ +python scripts/container_artifact_extract\.py candidate\.zip candidate\.json candidate-manifest\.sigstore\.json$' "$candidate"
}
assert_python3_extractor "$workflow"
cp "$workflow" "$tmp/container-release-python-mutation.yml"
sed -i 's/^          python3 -I scripts\/container_artifact_extract\.py candidate\.zip candidate\.json candidate-manifest\.sigstore\.json$/          python scripts\/container_artifact_extract.py candidate.zip candidate.json/' \
  "$tmp/container-release-python-mutation.yml"
if cmp -s "$workflow" "$tmp/container-release-python-mutation.yml"; then
  echo "python extractor mutation did not apply, so its verdict proves nothing" >&2
  exit 1
fi
if assert_python3_extractor "$tmp/container-release-python-mutation.yml"; then
  echo "container release contract accepted a python extractor mutation" >&2
  exit 1
fi
grep -q "github.event_name == 'workflow_dispatch'" "$workflow"
assert_absent -Eq '^  (push|pull_request):' "$workflow"
grep -q 'imagetools create' "$workflow"
# Promotion retags; it never builds. A deny pattern for `docker build` cannot
# keep up with the CLI's spellings (`docker builder build`, `docker --context x
# build`, `docker compose build`, two spaces, a quote or `;` after it), and the
# old one also matched the required `docker buildx imagetools` retag, unnoticed
# only because its `! grep` form could never fail. So the docker invocations are
# allowlisted instead: every `docker` command word, with its next two words, must
# be `docker buildx imagetools`, whether or not it is called by path
# (`/usr/bin/docker`). `docker/login-action` and `-docker.pkg.dev` are not
# command words. Other build tools are named below.
docker_invocations_only_retag() {
  python3 -B - "$1" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read().replace("\\\n", " ")
found = set()
for match in re.finditer(r"(?<![A-Za-z0-9_.-])docker(?![A-Za-z0-9_./-])", text):
    rest = text[match.end():].split("\n", 1)[0]
    rest = re.split(r"[;|&()<>\"'`$]", rest, maxsplit=1)[0]
    found.add(" ".join(["docker", *rest.split()[:2]]))
allowed = {"docker buildx imagetools"}
if found != allowed:
    unexpected = ", ".join(repr(command) for command in sorted(found - allowed))
    raise SystemExit(
        "FAIL - container-release docker invocations must be only 'docker buildx imagetools'; "
        + (f"found {unexpected}" if unexpected else "found no docker invocation at all")
    )
PY
}
# The helper names its own failure; `|| exit 1` keeps the ERR trap from repeating it.
docker_invocations_only_retag "$workflow" || exit 1
assert_absent -Eq 'build-push-action|bake-action|buildah|podman|kaniko|docker-compose|deploy|verjson-cli-cloud' "$workflow"
# Each spelling must reach a verdict on its own, in an otherwise real workflow.
while IFS= read -r build_form; do
  mutant="$tmp/container-release-build-form.yml"
  cp "$workflow" "$mutant"
  printf '          %s\n' "$build_form" >>"$mutant"
  if cmp -s "$workflow" "$mutant"; then
    echo "build-form mutant was not built, so its verdict proves nothing: $build_form" >&2; exit 1
  fi
  if docker_invocations_only_retag "$mutant" 2>/dev/null \
    && (assert_absent -Eq 'build-push-action|bake-action|buildah|podman|kaniko|docker-compose|deploy|verjson-cli-cloud' "$mutant") 2>/dev/null; then
    echo "container release contract accepted a promotion that builds: $build_form" >&2; exit 1
  fi
done <<'BUILD_FORMS'
docker build .
echo "$(docker build -q .)"
run "docker build"
docker build; true
docker  build .
docker builder build .
docker buildx --builder ci build .
docker buildx build --push .
docker buildx bake
docker --context ci build .
docker compose build
docker image build .
docker-compose build
podman build .
buildah bud -t image .
/kaniko/executor --context .
uses: docker/build-push-action@v6
uses: docker/bake-action@v5
/usr/bin/docker build .
BUILD_FORMS
grep -q 'Mint exact-repository release App token' "$workflow"
# Atomic, and with repository hooks disabled: `.git/hooks` is untracked, so nothing
# in it was reviewed, and this command holds the release App token (ADR 0158).
grep -q 'git -c core.hooksPath=/dev/null push --atomic' "$workflow"
grep -q 'docker/login-action@' "$workflow"
assert_absent -q 'secrets.release-token' "$workflow"
legacy_release_token='RELEASE_'"TOKEN"
legacy_org_release_token='VERJSON_RELEASE_'"TOKEN"
assert_absent -Eq "$legacy_release_token|$legacy_org_release_token" "$workflow"
assert_absent -Eq 'gh attestation verify|actions/attest-build-provenance@' "$workflow"
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
grep -q 'python3 -I .container-release-contract/scripts/changelog.py release' "$workflow"
grep -Fq 'GIT_NO_REPLACE_OBJECTS=1 git -C .container-release-contract show \' "$workflow"
grep -Fq '"$CONTRACT_REF:scripts/changelog.py" \' "$workflow"
grep -Fq '| cmp -s .container-release-contract/scripts/changelog.py - \' "$workflow"
engine_guard_line="$(grep -n 'GIT_NO_REPLACE_OBJECTS=1 git -C .container-release-contract show' "$workflow" | cut -d: -f1 || true)"
engine_run_line="$(grep -n 'python3 -I .container-release-contract/scripts/changelog.py release' "$workflow" | cut -d: -f1 || true)"
[ -n "$engine_guard_line" ] && [ -n "$engine_run_line" ] && [ "$engine_guard_line" -lt "$engine_run_line" ] || {
  echo "FAIL - the release engine is not verified (line '${engine_guard_line}') before it runs (line '${engine_run_line}')" >&2
  exit 1
}
assert_absent -Eq 'python3? scripts/changelog.py release' "$workflow"
assert_absent -Eq 'gh attestation verify|actions/attest-build-provenance@' "$workflow"
assert_absent -q 'container-release-${{ github.repository }}-${{ inputs.version }}' "$workflow"
grep -Fq "group: \${{ (github.event_name == 'pull_request' || github.event_name == 'pull_request_review' || github.event_name == 'pull_request_review_comment' || github.event_name == 'merge_group' || github.event_name == 'pull_request_target') && github.event.repository.private != true && format('public-rejected-{0}-{1}-{2}', github.repository, github.run_id, github.run_attempt) || format('container-release-{0}', github.repository) }}" "$workflow"
grep -q 'complete stable alias set diverged' "$workflow"
grep -q 'continue-on-error: true' "$workflow"
grep -q 'package_retention.py' "$workflow"
grep -q 'packages: write' "$workflow"
grep -q 'needs: promote' "$workflow"
grep -q 'retention-targets' "$workflow"
grep -q 'Validate the immutable retention contract ref' "$workflow"
grep -q '\^\[0123456789abcdef\]{40}\$' "$workflow"
guard_line="$(grep -n 'Validate the immutable retention contract ref' "$workflow" | tail -1 | cut -d: -f1 || true)"
checkout_line="$(grep -n 'path: .package-retention-contract' "$workflow" | cut -d: -f1 || true)"
[ -n "$guard_line" ] && [ -n "$checkout_line" ] && [ "$guard_line" -lt "$checkout_line" ] || {
  echo "FAIL - the retention contract ref is not validated (line '${guard_line}') before its checkout (line '${checkout_line}')" >&2
  exit 1
}
grep -qx '          python3 -I scripts/container_artifact_extract.py candidate.zip candidate.json candidate-manifest.sigstore.json' "$workflow"
grep -qx '          python3 -I scripts/container_attestation_verify.py --candidate candidate.json \\' "$workflow"
grep -qx '          python3 -I scripts/container_release_promotion.py --candidate candidate.json \\' "$workflow"
assert_absent -Eq '^[[:space:]]+python scripts/container_(artifact_extract|attestation_verify|release_promotion)\.py' "$workflow"
grep -Fq 'candidate_artifact_digest="${BASH_REMATCH[2]}"' "$workflow"
assert_absent -Fq 'candidate_artifact_digest="${BASH_REMATCH[1]}"' "$workflow"
grep -Fq '[[ "$CANDIDATE_MANIFEST" =~ ^([0123456789]{1,15})@(sha256:[0123456789abcdef]{64})$ ]]' "$workflow"
candidate_identity="9631224154@sha256:70be36dfceee65146fd9942840a227d498506c1edc61af53ed4cb6e642b8d3cd"
[[ "$candidate_identity" =~ ^([0123456789]{1,15})@(sha256:[0123456789abcdef]{64})$ ]]
[ "${BASH_REMATCH[1]}" = "9631224154" ]
[ "${BASH_REMATCH[2]}" = "sha256:70be36dfceee65146fd9942840a227d498506c1edc61af53ed4cb6e642b8d3cd" ]
grep -q 'existing tag records a divergent release manifest' "$workflow"
grep -q 'existing GitHub Release manifest diverges' "$workflow"
bash "$root/scripts/container-contract-coexistence.test.sh"
echo 'container release canonical contract passed'
