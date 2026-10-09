#!/usr/bin/env bash
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
release_workflow="$repo_root/.github/workflows/node-release.yml"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

extract_block() {
  local begin="$1" end="$2" output="$3"
  awk -v begin="$begin" -v end="$end" '
    index($0, begin) { active = 1 }
    active { sub(/^          /, ""); print }
    index($0, end) { exit }
  ' "$release_workflow" >"$output"
  grep -qF "$begin" "$output" && grep -qF "$end" "$output" || {
    echo "FAIL - could not extract bounded $begin/$end workflow block" >&2
    exit 1
  }
  bash -n "$output"
}

extract_run_body() {
  local step_name="$1" output="$2"
  awk -v step_name="$step_name" '
    $0 == "      - name: " step_name { selected = 1; next }
    selected && $0 == "        run: |" { active = 1; next }
    active && /^      - / { exit }
    active { sub(/^          /, ""); print }
  ' "$release_workflow" >"$output"
  [ -s "$output" ] || { echo "FAIL - could not extract run body for $step_name" >&2; exit 1; }
}

extract_run_body "Publish validated package archives" "$work/publish-step.sh"
[ "$(awk 'NF { print; exit }' "$work/publish-step.sh")" = "# RESTART_SAFE_NPM_PUBLISH_BEGIN" ] \
  || { echo "FAIL - publisher shell was added before the tested restart-safe block" >&2; exit 1; }
[ "$(awk 'NF { last = $0 } END { print last }' "$work/publish-step.sh")" = "# RESTART_SAFE_NPM_PUBLISH_END" ] \
  || { echo "FAIL - publisher shell was added after the tested restart-safe block" >&2; exit 1; }
extract_block RESTART_SAFE_NPM_PUBLISH_BEGIN RESTART_SAFE_NPM_PUBLISH_END "$work/publish.sh"
extract_block RESTART_SAFE_GH_RELEASE_BEGIN RESTART_SAFE_GH_RELEASE_END "$work/release-notes.sh"
extract_block RELEASE_PREPARE_PACKAGES_BEGIN RELEASE_PREPARE_PACKAGES_END "$work/prepare.sh"

mkdir -p "$work/bin" "$work/repo/CHANGELOG" "$work/repo/compat" "$work/repo/scripts" "$work/repo/artifacts" "$work/state"
mkdir -p "$work/tmp"
printf '%s\n' notes >"$work/repo/CHANGELOG/v1.2.3.md"
printf '%s\n' root-archive >"$work/repo/artifacts/acme-pkg-1.2.3.tgz"
printf '%s\n' compat-archive >"$work/repo/artifacts/acme-compat-1.2.3.tgz"
cat >"$work/repo/validated-packages.json" <<'JSON'
[{"name":"@acme/pkg","version":"1.2.3","integrity":"sha512-expected","filename":"acme-pkg-1.2.3.tgz"},{"name":"@acme/compat","version":"1.2.3","integrity":"sha512-compat","filename":"acme-compat-1.2.3.tgz"}]
JSON
cat >"$work/repo/scripts/release-prepare-packages.sh" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
[ "$1" = 1.2.3 ]
[ -z "${NODE_AUTH_TOKEN:-}" ]
touch "$TEST_STATE/prepared"
HOOK
chmod +x "$work/repo/scripts/release-prepare-packages.sh"

cat >"$work/bin/npm" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
command="$1"
shift
case "$command" in
  version) ;;
  publish)
    [ "${PUBLISH_FAIL:-0}" != 1 ] || exit 1
    case "$1" in
      *compat*) state="$TEST_STATE/registry-compat" ;;
      *) state="$TEST_STATE/registry-root" ;;
    esac
    if [ -e "$state" ]; then exit 1; fi
    touch "$state"
    ;;
  view)
    if [ "${AUTH_FAIL:-0}" = 1 ]; then
      echo "npm error code E401" >&2
      echo "npm error 401 Unauthorized" >&2
      exit 1
    fi
    if [ "${NETWORK_FAIL:-0}" = 1 ]; then
      echo "npm error code ENOTFOUND" >&2
      echo "npm error network request failed" >&2
      exit 1
    fi
    case "${VIEW_MODE:-matching}:$1" in
      matching:*compat*) printf '%s\n' '{"name":"@acme/compat","version":"1.2.3","dist":{"integrity":"sha512-compat"}}' ;;
      matching:*) printf '%s\n' '{"name":"@acme/pkg","version":"1.2.3","dist":{"integrity":"sha512-expected"}}' ;;
      mismatch:*) printf '%s\n' '{"name":"@acme/pkg","version":"1.2.3","dist":{"integrity":"sha512-other"}}' ;;
      spoof:*) printf '%s\n' '{"name":"@attacker/pkg","version":"1.2.3","dist":{"integrity":"sha512-expected"}}' ;;
      missing:*)
        echo "npm error code E404" >&2
        echo "npm error 404 Not Found - GET https://npm.pkg.github.com/@acme%2fpkg" >&2
        exit 1
        ;;
    esac
    ;;
  *) exit 90 ;;
esac
STUB

cat >"$work/bin/gh" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
[ "$1" = release ]
case "$2" in
  view)
    [ -e "$TEST_STATE/github-release" ] || exit 1
    printf '%s\n' v1.2.3
    ;;
  create)
    [ "${GH_CREATE_FAIL:-0}" != 1 ] || exit 1
    touch "$TEST_STATE/github-release"
    ;;
  edit) [ -e "$TEST_STATE/github-release" ] ;;
  *) exit 90 ;;
esac
STUB
chmod +x "$work/bin/npm" "$work/bin/gh"

run_publish() {
  ( cd "$work/repo" && PATH="$work/bin:$PATH" TEST_STATE="$work/state" RUNNER_TEMP="$work/tmp" PACKAGE_VERSION=1.2.3 \
      bash -euo pipefail "$work/prepare.sh" && \
    PATH="$work/bin:$PATH" TEST_STATE="$work/state" RUNNER_TEMP="$work/tmp" \
      ARTIFACT_DIR="$work/repo/artifacts" VALIDATED_MANIFEST="$work/repo/validated-packages.json" \
      REQUESTED_TAG=v1.2.3 PACKAGE_VERSION=1.2.3 NODE_AUTH_TOKEN=test SCOPE=@acme \
      VIEW_MODE="${VIEW_MODE:-}" AUTH_FAIL="${AUTH_FAIL:-0}" NETWORK_FAIL="${NETWORK_FAIL:-0}" \
      PUBLISH_FAIL="${PUBLISH_FAIL:-0}" \
      bash -euo pipefail "$work/publish.sh" )
}
run_notes() {
  ( cd "$work/repo" && PATH="$work/bin:$PATH" TEST_STATE="$work/state" \
      VERSION=v1.2.3 GH_TOKEN=test ASSET_ROOT="$work/assets" RELEASE_ASSET_MANIFEST='[]' \
      "$@" bash -euo pipefail "$work/release-notes.sh" )
}

run_publish
if run_notes env GH_CREATE_FAIL=1; then
  echo "FAIL - simulated GitHub Release failure unexpectedly succeeded" >&2
  exit 1
fi
run_publish
run_notes env GH_CREATE_FAIL=0
for expected_state in registry-root registry-compat prepared github-release; do
  [ -e "$work/state/$expected_state" ] || {
    echo "FAIL - partial-success rerun did not create $expected_state" >&2
    exit 1
  }
done
echo "ok - npm success plus GitHub Release failure completes safely on rerun"
run_publish
run_notes env GH_CREATE_FAIL=0
echo "ok - a fully completed release rerun reconciles without rewriting package or tag"

for mode in mismatch spoof; do
  rm -rf "$work/state"; mkdir -p "$work/state"; touch "$work/state/registry-root"
  if VIEW_MODE="$mode" run_publish >/dev/null 2>&1; then
    echo "FAIL - rerun accepted $mode registry metadata" >&2
    exit 1
  fi
  echo "ok - rerun rejects $mode registry metadata"
done

rm -rf "$work/state"; mkdir -p "$work/state"; touch "$work/state/registry-root"
auth_out="$work/auth-fail.out"
if AUTH_FAIL=1 run_publish >"$auth_out" 2>&1; then
  echo "FAIL - rerun accepted unproven registry authorization" >&2
  exit 1
fi
if grep -qF 'could not confirm either way' "$auth_out" && ! grep -qF 'authorization gap' "$auth_out"; then
  echo "ok - rerun fails closed when registry authorization cannot be proven, without claiming it confirmed the version missing (#924)"
else
  echo "FAIL - an unproven-authorization failure did not distinguish itself from a confirmed-missing version" >&2
  cat "$auth_out" >&2
  exit 1
fi
network_out="$work/network-fail.out"
if NETWORK_FAIL=1 run_publish >"$network_out" 2>&1; then
  echo "FAIL - rerun accepted unavailable registry state" >&2
  exit 1
fi
if grep -qF 'could not confirm either way' "$network_out" && ! grep -qF 'authorization gap' "$network_out"; then
  echo "ok - rerun fails closed when registry metadata is unavailable, without claiming it confirmed the version missing (#924)"
else
  echo "FAIL - an unavailable-registry failure did not distinguish itself from a confirmed-missing version" >&2
  cat "$network_out" >&2
  exit 1
fi

rm -rf "$work/state"; mkdir -p "$work/state"
publish_out="$work/publish-fail.out"
if PUBLISH_FAIL=1 VIEW_MODE=missing run_publish >"$publish_out" 2>&1; then
  echo "FAIL - a genuine first-attempt publish failure unexpectedly succeeded" >&2
  exit 1
fi
[ ! -e "$work/state/registry-root" ] && [ ! -e "$work/state/registry-compat" ] || {
  echo "FAIL - a genuine publish failure left partial registry state" >&2
  exit 1
}
if grep -qF 'allegedly existing' "$publish_out"; then
  echo "FAIL - the genuine-failure path still emits the misleading 'allegedly existing' message (Verjson/.github#921)" >&2
  exit 1
fi
if grep -qF 'expected-recoverable state, not a wedged release' "$publish_out" \
    && grep -qF 're-dispatching this exact same version is safe' "$publish_out" \
    && grep -qF 'authorization gap' "$publish_out"; then
  echo "ok - a genuine publish failure names its safe remedy without overclaiming the version is confirmed missing, since GitHub Packages 404s a private package the token can't read too (#921, #924, #929)"
else
  echo "FAIL - a genuine publish failure did not explain that re-dispatching the same version is safe" >&2
  cat "$publish_out" >&2
  exit 1
fi
