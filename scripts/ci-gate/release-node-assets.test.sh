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
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
workflow="$root/.github/workflows/node-release.yml"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
awk '/BOUNDED_RELEASE_ASSETS_BEGIN/ { active = 1 } active { sub(/^          /, ""); print } /BOUNDED_RELEASE_ASSETS_END/ { exit }' "$workflow" >"$work/assets.sh"
awk '/RESTART_SAFE_GH_RELEASE_BEGIN/ { active = 1 } active { sub(/^          /, ""); print } /RESTART_SAFE_GH_RELEASE_END/ { exit }' "$workflow" >"$work/publish.sh"
# An empty or unterminated block still parses, and the empty one exits 0, so
# prove each extraction reached its end marker before trusting a verdict on it.
for extracted in assets:BOUNDED_RELEASE_ASSETS_END publish:RESTART_SAFE_GH_RELEASE_END; do
  [ -s "$work/${extracted%%:*}.sh" ] && grep -qF "${extracted#*:}" "$work/${extracted%%:*}.sh" || {
    echo "FAIL - could not extract the ${extracted%%:*} block through ${extracted#*:} from $workflow" >&2
    exit 1
  }
done
bash -n "$work/assets.sh"
bash -n "$work/publish.sh"

mkdir -p "$work/repo/contract" "$work/repo/CHANGELOG" "$work/runner" "$work/state" "$work/bin"
printf '%s\n' 'tagged schema' >"$work/repo/contract/schema.graphql"
printf '%s\n' 'tagged digest' >"$work/repo/contract/schema.sha256"
printf '%s\n' notes >"$work/repo/CHANGELOG/v1.2.3.md"
git -C "$work/repo" init -q
git -C "$work/repo" config user.name test
git -C "$work/repo" config user.email test@example.invalid
git -C "$work/repo" add contract CHANGELOG
git -C "$work/repo" commit -qm fixture

run_assets() {
  : >"$work/output"
  (cd "$work/repo" && RELEASE_ASSETS="$1" ASSET_STATE_ROOT="$work/runner/validation" \
    GITHUB_OUTPUT="$work/output" bash -euo pipefail "$work/assets.sh")
}
manifest() { sed -n 's/^manifest=//p' "$work/output"; }
# A run that crashes, or that stops on a different guard, fails too, so a
# non-zero exit alone would read as every rejection. Each refusal names the
# exit status and the message it must have stopped with.
refused_with() {
  local label="$1" status="$2" expected_status="$3" output="$4" reason="$5"
  if [ "$status" -eq 0 ]; then
    echo "FAIL - accepted $label" >&2
    exit 1
  fi
  # An empty reason is a refusal that prints nothing; its status alone names it.
  if [ "$status" -ne "$expected_status" ] \
    || { [ -n "$reason" ] && ! grep -qF -- "$reason" "$output"; }; then
    echo "FAIL - $label was refused (exit $status, expected $expected_status) without the reason: $reason" >&2
    sed 's/^/diag - /' "$output" >&2
    exit 1
  fi
}
rejects() {
  local name="$1" value="$2" reason="$3" status=0
  run_assets "$value" >"$work/reject.out" 2>&1 || status=$?
  refused_with "$name" "$status" 1 "$work/reject.out" "$reason"
  echo "ok - rejects $name"
}

run_assets '[]'
[ "$(manifest)" = '[]' ]
echo "ok - empty opt-in records an empty immutable manifest"
run_assets '["contract/schema.graphql","contract/schema.sha256"]'
release_manifest="$(manifest)"
tagged_digest() {
  local digest
  digest="$(git -C "$work/repo" show "HEAD:$1" | sha256sum | awk '{print $1}')" || digest=""
  if [ "${#digest}" -ne 64 ]; then
    echo "FAIL - could not digest the tagged $1 fixture" >&2
    exit 1
  fi
  printf '%s\n' "$digest"
}
schema_digest="$(tagged_digest contract/schema.graphql)"
sidecar_digest="$(tagged_digest contract/schema.sha256)"

cat >"$work/bin/gh" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
if [ "$1" = api ]; then
  case "${REMOTE_MODE:-absent}" in
    absent|race) printf '%s\n' '{"assets":[]}' ;;
    matching) printf '{"assets":[{"name":"schema.graphql","digest":"sha256:%s"},{"name":"schema.sha256","digest":"sha256:%s"}]}\n' "$SCHEMA_DIGEST" "$SIDECAR_DIGEST" ;;
    mismatch) printf '%s\n' '{"assets":[{"name":"schema.graphql","digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]}' ;;
    unverifiable) printf '%s\n' '{"assets":[{"name":"schema.graphql","digest":null}]}' ;;
    missing-assets) printf '%s\n' '{}' ;;
    null-assets) printf '%s\n' '{"assets":null}' ;;
    scalar-member) printf '%s\n' '{"assets":["schema.graphql"]}' ;;
    missing-name) printf '%s\n' '{"assets":[{"digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]}' ;;
    nonstring-name) printf '%s\n' '{"assets":[{"name":7,"digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]}' ;;
    malformed-digest) printf '%s\n' '{"assets":[{"name":"schema.graphql","digest":"sha512:not-a-sha256"}]}' ;;
    malformed-json) printf '%s\n' '{"assets":[' ;;
    api-failure) printf '%s\n' '{"assets":['; exit 24 ;;
  esac
  exit 0
fi
[ "$1" = release ]
case "$2" in
  view) [ -e "$TEST_STATE/release" ] || exit 1; printf '%s\n' v1.2.3 ;;
  create) touch "$TEST_STATE/release" ;;
  edit) : ;;
  upload)
    [ "$#" -eq 4 ]
    [ "$4" != --clobber ]
    [ "${REMOTE_MODE:-absent}" != race ] || exit 23
    printf '%s\t' "${4##*/}" >>"$TEST_STATE/uploads"
    sha256sum "$4" | awk '{print $1}' >>"$TEST_STATE/uploads"
    ;;
  *) exit 90 ;;
esac
STUB
chmod +x "$work/bin/gh"

run_publish() {
  (cd "$work/repo" && PATH="$work/bin:/usr/local/bin:/usr/bin:/bin" TEST_STATE="$work/state" \
    ASSET_ROOT="$work/runner/assets" RELEASE_ASSET_MANIFEST="$release_manifest" \
    VERSION=v1.2.3 GH_TOKEN=test GITHUB_SERVER_URL=https://github.com \
    GITHUB_REPOSITORY=Verjson/example SCHEMA_DIGEST="$schema_digest" SIDECAR_DIGEST="$sidecar_digest" \
    REMOTE_MODE="${REMOTE_MODE:-absent}" bash -euo pipefail "$work/publish.sh")
}

printf '%s\n' hostile >"$work/repo/contract/schema.graphql"
mkdir -p "$work/runner/assets"
printf '%s\n' injected >"$work/runner/assets/injected.txt"
: >"$work/state/uploads"
run_publish
grep -qxF $'schema.graphql\t'"$schema_digest" "$work/state/uploads"
grep -qxF $'schema.sha256\t'"$sidecar_digest" "$work/state/uploads"
[ "$(wc -l <"$work/state/uploads")" -eq 2 ]
echo "ok - post-validation mutation cannot change or expand the exact tagged upload manifest"

: >"$work/state/uploads"
REMOTE_MODE=matching run_publish
[ ! -s "$work/state/uploads" ]
echo "ok - matching digest receipts make retry a no-op"
while IFS='|' read -r remote_mode reason; do
  : >"$work/state/uploads"
  status=0
  REMOTE_MODE="$remote_mode" run_publish >"$work/$remote_mode.out" 2>&1 || status=$?
  refused_with "$remote_mode existing asset state" "$status" 1 "$work/$remote_mode.out" "$reason"
  [ ! -s "$work/state/uploads" ]
  echo "ok - $remote_mode existing asset fails closed without replacement"
done <<'EXISTING_ASSET_REFUSALS'
mismatch|::error::Existing release asset 'schema.graphql' has digest 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
unverifiable|::error::Existing release asset 'schema.graphql' has no verifiable SHA-256 digest
EXISTING_ASSET_REFUSALS
: >"$work/state/uploads"
# The colliding upload is refused without a message, so the stub's own exit 23
# is what shows the upload, and not an earlier step, stopped the run.
status=0
REMOTE_MODE=race run_publish >"$work/race.out" 2>&1 || status=$?
refused_with "an inspection/upload collision race" "$status" 23 "$work/race.out" ""
[ ! -s "$work/state/uploads" ]
echo "ok - an asset created after inspection fails instead of being clobbered"
while IFS='|' read -r remote_mode expected_status reason; do
  : >"$work/state/uploads"
  status=0
  REMOTE_MODE="$remote_mode" run_publish >"$work/$remote_mode.out" 2>&1 || status=$?
  refused_with "spoofed GitHub Release API state: $remote_mode" "$status" "$expected_status" \
    "$work/$remote_mode.out" "$reason"
  [ ! -s "$work/state/uploads" ]
  echo "ok - spoofed GitHub Release API state '$remote_mode' fails with zero uploads"
done <<'SPOOFED_API_REFUSALS'
missing-assets|1|GitHub Release inspection did not return an explicit assets list
null-assets|1|GitHub Release inspection did not return an explicit assets list
scalar-member|1|GitHub Release inspection returned an asset without a string name
missing-name|1|GitHub Release inspection returned an asset without a string name
nonstring-name|1|GitHub Release inspection returned an asset without a string name
malformed-digest|1|GitHub Release returned malformed digest for selected asset: schema.graphql
malformed-json|1|json.decoder.JSONDecodeError
api-failure|24|
SPOOFED_API_REFUSALS

git -C "$work/repo" restore contract/schema.graphql
rejects 'malformed JSON' '["contract/schema.graphql"' 'release-assets is not valid JSON:'
rejects 'a traversal path' '["../schema.graphql"]' 'invalid or duplicate release asset path: ../schema.graphql'
rejects 'an absolute path' '["/tmp/schema.graphql"]' 'invalid or duplicate release asset path: /tmp/schema.graphql'
rejects 'a dot segment' '["contract/./schema.graphql"]' 'invalid or duplicate release asset path: contract/./schema.graphql'
rejects 'a duplicate path' '["contract/schema.graphql","contract/schema.graphql"]' \
  'invalid or duplicate release asset path: contract/schema.graphql'
mkdir -p "$work/repo/other"
printf '%s\n' collision >"$work/repo/other/schema.graphql"
git -C "$work/repo" add other/schema.graphql
git -C "$work/repo" commit -qm collision
rejects 'duplicate destination basenames' '["contract/schema.graphql","other/schema.graphql"]' \
  'release asset basename is duplicated: schema.graphql'
rejects 'a directory' '["contract"]' "::error::Release asset 'contract' must be a regular non-symlink file."
printf '%s\n' untracked >"$work/repo/untracked.txt"
rejects 'an untracked file' '["untracked.txt"]' \
  "::error::Release asset 'untracked.txt' is not tracked by the immutable tag."
ln -s contract/schema.graphql "$work/repo/link.graphql"
rejects 'a symlink file' '["link.graphql"]' "::error::Release asset path traverses symlink 'link.graphql'."
ln -s contract "$work/repo/linked-contract"
rejects 'a symlink parent component' '["linked-contract/schema.graphql"]' \
  "::error::Release asset path traverses symlink 'linked-contract'."
# Built as JSON, not spliced: the hand-joined array ended in `]]`, so this case
# was refused as invalid JSON and the 16-path limit was never reached.
many="$(jq -cn '[range(1; 18) | "asset-\(.).txt"]')"
rejects 'more than 16 assets' "$many" 'release-assets must be a JSON array containing at most 16 paths'

real_git="$(command -v git)"
cat >"$work/bin/git" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
if [ "$1" = cat-file ] && [ "$2" = -s ]; then printf '%s\n' "$FAKE_BLOB_SIZE"; else exec "$REAL_GIT" "$@"; fi
STUB
chmod +x "$work/bin/git"
rejects_sized() {
  local name="$1" size="$2" value="$3" reason="$4" status=0
  (cd "$work/repo" && PATH="$work/bin:$PATH" REAL_GIT="$real_git" FAKE_BLOB_SIZE="$size" RELEASE_ASSETS="$value" \
    ASSET_STATE_ROOT="$work/runner/validation" GITHUB_OUTPUT="$work/output" \
    bash -euo pipefail "$work/assets.sh" >"$work/reject.out" 2>&1) || status=$?
  refused_with "$name" "$status" 1 "$work/reject.out" "$reason"
  echo "ok - rejects $name"
}
rejects_sized 'an asset over 100 MiB' 104857601 '["contract/schema.graphql"]' \
  "::error::Release asset 'contract/schema.graphql' exceeds 100 MiB."
# Three distinct basenames: reusing other/schema.graphql made this case stop on
# the duplicate-basename check before any size was totalled.
printf '%s\n' third >"$work/repo/other/third.graphql"
git -C "$work/repo" add other/third.graphql
git -C "$work/repo" commit -qm third
rejects_sized 'assets over 250 MiB total' 94371840 \
  '["contract/schema.graphql","contract/schema.sha256","other/third.graphql"]' \
  '::error::Release assets exceed 250 MiB total.'
echo "All tests passed."
