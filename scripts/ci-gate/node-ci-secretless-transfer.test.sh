#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/node-ci.yml"
failures=0
tmp="$(mktemp -d)"
export TRANSFER_HELPER_SOURCE="$root/scripts/container_dependency_transfer.py"
export PACKAGE_BODY_SCRIPT="$tmp/package-body.sh"
export RUNNER_TEMP="$tmp/transfer-runner"
mkdir -p "$RUNNER_TEMP"
trap 'rm -rf "$tmp"' EXIT

pass() { printf 'ok - %s\n' "$1"; }
fail() { printf 'not ok - %s\n' "$1" >&2; failures=$((failures + 1)); }

python3 - "$workflow" <<'PY' \
  && pass "secretless caches preserve run binding and isolate persistent public content" \
  || fail "secretless transfer workflow structure violates its storage or trust contract"
import sys
import yaml

doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
jobs = doc["jobs"]
acquire = jobs["acquire-secretless-dependencies"]
build = jobs["build-test"]
inputs = doc[True]["workflow_call"]["inputs"]

acquisition_guard = next(
    step for step in build["steps"]
    if step.get("name") == "Require completed secretless dependency acquisition"
)
assert "needs.eligibility.outputs.should-run != 'false'" in acquisition_guard["if"]
assert "inputs.secretless-pr || inputs.secretless-trusted-ref" in acquisition_guard["if"]
assert "needs.acquire-secretless-dependencies.result != 'success'" in acquisition_guard["if"]
assert acquisition_guard["env"]["ACQUISITION_RESULT"] == "${{ needs.acquire-secretless-dependencies.result }}"
assert "eligibility likely did not complete" in acquisition_guard["run"]
assert "Re-run the workflow" in acquisition_guard["run"]
assert build["steps"].index(acquisition_guard) < next(
    index for index, step in enumerate(build["steps"])
    if str(step.get("uses", "")).startswith("actions/checkout@")
)

assert inputs["secretless-runtime-public-cache"]["type"] == "boolean"
assert inputs["secretless-runtime-public-cache"]["default"] is False
assert inputs["browser-cache"]["type"] == "boolean"
assert inputs["browser-cache"]["default"] is False

assert acquire["outputs"]["transfer-cache-key"] == "${{ steps.create-secretless-cache-key.outputs.cache-key }}"
assert acquire["outputs"]["transfer-encryption-key"] == "${{ steps.package-secretless-transfer.outputs.encryption-key }}"
assert acquire["outputs"]["transfer-payload-bytes"] == "${{ steps.package-secretless-transfer.outputs.payload-bytes }}"
assert acquire["outputs"]["transfer-payload-sha256"] == "${{ steps.package-secretless-transfer.outputs.payload-sha256 }}"
assert "transfer-artifact-id" not in acquire["outputs"]
assert acquire["outputs"]["auxiliary-content-path"] == "${{ steps.resolve-auxiliary-source.outputs.content-path }}"
create_key = next(step for step in acquire["steps"] if step.get("id") == "create-secretless-cache-key")
assert "openssl rand -hex 32" in create_key["run"]
assert "secretless-npm-transfer-${RUN_ID}-${RUN_ATTEMPT}-${nonce}" in create_key["run"]
save = next(step for step in acquire["steps"] if step.get("id") == "save-secretless-transfer")
assert save["uses"] == "actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
assert save["with"]["key"] == "${{ steps.create-secretless-cache-key.outputs.cache-key }}"
acquire_transfer_download = next(
    step for step in acquire["steps"]
    if step.get("name") == "Download pinned secretless dependency transfer implementation"
)
assert "job)).workflow_sha" in acquire_transfer_download["env"]["JOB_WORKFLOW_SHA"]
assert "JOB_WORKFLOW_SHA" in acquire_transfer_download["run"]
assert "raw.githubusercontent.com/Verjson/.github/" in acquire_transfer_download["run"]
stable_transfer_path = ".verjson-secretless-transfer-${{ github.run_id }}"
assert save["with"]["path"] == stable_transfer_path
assert "runner.temp" not in save["with"]["path"]
assert "node_modules" not in save["with"]["path"]

package = next(step for step in acquire["steps"] if step.get("name") == "Package bounded credential-free npm cache")
assert package["id"] == "package-secretless-transfer"
assert acquire["steps"].index(package) < acquire["steps"].index(save)
assert package["env"]["MAX_PAYLOAD_BYTES"] == "83886080"
assert acquire["steps"].index(acquire_transfer_download) < acquire["steps"].index(package)
assert '[ ! -e "$TRANSFER_DIR" ] && [ ! -L "$TRANSFER_DIR" ]' in package["run"]
assert "reserved secretless transfer path exists before packaging" in package["run"]
assert "_cacache/content-v2" in package["run"]
assert "npm-private-cache.tar" in package["run"]
assert "npm-private-cache.tar.enc" in package["run"]
assert 'rm -f "$TRANSFER_DIR/npm-private-cache.tar"' in package["run"]
assert "container_dependency_transfer.py\" encrypt" in package["run"]
assert "encryption-key=%s" in package["run"]
assert "payload_bytes" in package["run"]
assert "run_attempt=$RUN_ATTEMPT" in package["run"]
assert "lock_sha256=$lock_sha256" in package["run"]
assert "auxiliary_commit=$AUXILIARY_COMMIT" in package["run"]
assert "payload-bytes=%s" in package["run"]
assert "payload-sha256=%s" in package["run"]
assert '>> "$GITHUB_OUTPUT"' in package["run"]
acquire_cleanup = next(step for step in acquire["steps"] if step.get("name") == "Remove local acquisition and transfer state")
assert acquire_cleanup["if"] == "always()"
populate = next(step for step in acquire["steps"] if step.get("id") == "populate-private-cache")
assert package["env"]["CACHE_DIR"] == "${{ steps.populate-private-cache.outputs.cache-dir }}"
assert acquire_cleanup["env"]["CACHE_DIR"] == "${{ steps.populate-private-cache.outputs.cache-dir }}"

stable_workspace_transfer = "${{ github.workspace }}/" + stable_transfer_path
restore_guard = next(step for step in build["steps"] if step.get("name") == "Require an unused secretless restore path")
restore = next(step for step in build["steps"] if step.get("id") == "restore-secretless-transfer")
transfer_download = next(
    step for step in build["steps"]
    if step.get("name") == "Download pinned secretless dependency transfer implementation"
)
transfer_decrypt = next(
    step for step in build["steps"]
    if step.get("name") == "Decrypt run-scoped secretless dependency payload"
)
install = next(step for step in build["steps"] if step.get("name") == "Install from verified secretless npm cache")
assert build["steps"].index(restore_guard) < build["steps"].index(restore)
assert restore_guard["env"]["TRANSFER_DIR"] == stable_workspace_transfer
assert '[ ! -e "$TRANSFER_DIR" ] && [ ! -L "$TRANSFER_DIR" ]' in restore_guard["run"]
assert "reserved secretless transfer path exists before restore" in restore_guard["run"]
assert restore["uses"] == "actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
assert restore["with"]["key"] == "${{ needs.acquire-secretless-dependencies.outputs.transfer-cache-key }}"
assert restore["with"]["path"] == stable_transfer_path
assert restore["with"]["fail-on-cache-miss"] is True
assert "restore-keys" not in restore["with"]
# #1830: a failed restore stays fail-closed and gains an actionable diagnostic.
diagnostic = next(step for step in build["steps"] if step.get("name") == "Explain a failed secretless transfer restore")
assert build["steps"].index(diagnostic) == build["steps"].index(restore) + 1
assert diagnostic["if"] == "failure() && needs.eligibility.outputs.should-run != 'false' && steps.restore-secretless-transfer.outcome == 'failure'"
assert "continue-on-error" not in restore and "continue-on-error" not in diagnostic
assert "env" not in diagnostic and "${{" not in diagnostic["run"]
assert "Re-run all jobs" in diagnostic["run"] and "#1251" in diagnostic["run"] and "6c4c694f" in diagnostic["run"]
assert build["steps"].index(restore) < build["steps"].index(transfer_download)
assert build["steps"].index(transfer_download) < build["steps"].index(transfer_decrypt)
assert build["steps"].index(transfer_decrypt) < build["steps"].index(install)
assert transfer_decrypt["env"]["TRANSFER_KEY"] == "${{ needs.acquire-secretless-dependencies.outputs.transfer-encryption-key }}"
assert 'npm-private-cache.tar.enc' in transfer_decrypt["run"]
assert 'npm-private-cache.tar"' in transfer_decrypt["run"]
assert "container_dependency_transfer.py\" decrypt" in transfer_decrypt["run"]
assert save["with"]["path"] == restore["with"]["path"]

public_restore = next(step for step in build["steps"] if step.get("id") == "restore-secretless-public-cache")
public_save = next(step for step in build["steps"] if step.get("id") == "save-secretless-public-cache")
assert "inputs.cache" in public_restore["if"]
assert "inputs.secretless-pr || inputs.secretless-trusted-ref" in public_restore["if"]
assert "inputs.package-manager == 'npm'" in public_restore["if"]
assert "hashFiles(inputs.cache-dependency-path) != ''" in public_restore["if"]
assert public_restore["with"]["key"] == "secretless-public-npm-${{ runner.os }}-${{ hashFiles(inputs.cache-dependency-path) }}"
assert "restore-keys" not in public_restore["with"]
assert public_restore["with"]["path"].startswith("${{ runner.temp }}/secretless-public-npm-")
assert public_save["with"] == public_restore["with"]
assert "steps.restore-secretless-public-cache.outputs.cache-hit != 'true'" in public_save["if"]

browser_prepare = next(step for step in build["steps"] if step.get("id") == "prepare-playwright-browser-cache")
browser_restore = next(step for step in build["steps"] if step.get("id") == "restore-playwright-browser-cache")
browser_validate = next(step for step in build["steps"] if step.get("name") == "Validate restored Playwright browser cache")
browser_bound = next(step for step in build["steps"] if step.get("id") == "bound-playwright-browser-cache")
browser_save = next(step for step in build["steps"] if step.get("id") == "save-playwright-browser-cache")
browser_cleanup = next(step for step in build["steps"] if step.get("name") == "Remove job-scoped Playwright browser cache")
browser_path = "${{ runner.temp }}/verjson-playwright-browsers-${{ github.run_id }}-${{ github.run_attempt }}"
browser_key = "playwright-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles(inputs.cache-dependency-path) }}"
assert browser_prepare["env"]["BROWSER_CACHE_DIR"] == browser_path
assert "PLAYWRIGHT_BROWSERS_PATH=%s" in browser_prepare["run"]
assert '"$BROWSER_CACHE_DIR" >> "$GITHUB_ENV"' in browser_prepare["run"]
assert '[ ! -e "$BROWSER_CACHE_DIR" ] && [ ! -L "$BROWSER_CACHE_DIR" ]' in browser_prepare["run"]
assert browser_restore["uses"] == "actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
assert browser_restore["with"]["path"] == browser_path
assert browser_restore["with"]["key"] == browser_key
assert "restore-keys" not in browser_restore["with"]
assert browser_validate["env"]["BROWSER_CACHE_DIR"] == browser_path
assert browser_validate["env"]["MAX_BROWSER_CACHE_FILES"] == "10000"
assert browser_validate["env"]["MAX_BROWSER_CACHE_BYTES"] == "1073741824"
for marker in ("BROWSER_CACHE_SYMLINK", "BROWSER_CACHE_FILE_BOUND", "BROWSER_CACHE_BYTE_BOUND"):
    assert marker in browser_validate["run"]
assert browser_bound["env"]["MAX_BROWSER_CACHE_FILES"] == "10000"
assert browser_bound["env"]["MAX_BROWSER_CACHE_BYTES"] == "1073741824"
assert "BROWSER_CACHE_SYMLINK" in browser_bound["run"]
assert "BROWSER_CACHE_FILE_BOUND" in browser_bound["run"]
assert "BROWSER_CACHE_BYTE_BOUND" in browser_bound["run"]
assert browser_save["uses"] == "actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
assert browser_save["with"] == browser_restore["with"]
assert "steps.restore-playwright-browser-cache.outputs.cache-hit != 'true'" in browser_save["if"]
assert "steps.bound-playwright-browser-cache.outputs.should-save == 'true'" in browser_save["if"]
assert browser_cleanup["if"].startswith("always()")
assert browser_cleanup["env"]["BROWSER_CACHE_DIR"] == browser_path
assert 'rm -rf -- "$BROWSER_CACHE_DIR"' in browser_cleanup["run"]
assert build["steps"].index(browser_prepare) < build["steps"].index(browser_restore)
assert build["steps"].index(browser_restore) < build["steps"].index(browser_validate)
assert build["steps"].index(browser_validate) < build["steps"].index(browser_bound)
assert build["steps"].index(browser_bound) < build["steps"].index(browser_save)
assert build["steps"].index(browser_save) < build["steps"].index(browser_cleanup)
assert "~/.cache/ms-playwright" not in str(build)
setup_index = next(index for index, step in enumerate(build["steps"]) if str(step.get("uses", "")).startswith("actions/setup-node@"))
setup = build["steps"][setup_index]
install_index = build["steps"].index(install)
assert setup_index < install_index
assert "!(inputs.secretless-pr || inputs.secretless-trusted-ref)" in setup["with"]["cache"]
for credential in (
    "GH_TOKEN", "GITHUB_TOKEN", "NODE_AUTH_TOKEN", "NPM_TOKEN",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS", "AZURE_CREDENTIALS",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL",
):
    assert install["env"][credential] == ""
assert install["env"]["MAX_PAYLOAD_BYTES"] == package["env"]["MAX_PAYLOAD_BYTES"]
assert install["env"]["EXPECTED_PAYLOAD_BYTES"] == "${{ needs.acquire-secretless-dependencies.outputs.transfer-payload-bytes }}"
assert install["env"]["EXPECTED_PAYLOAD_SHA256"] == "${{ needs.acquire-secretless-dependencies.outputs.transfer-payload-sha256 }}"
assert package["env"]["TRANSFER_DIR"] == stable_workspace_transfer
assert acquire_cleanup["env"]["TRANSFER_DIR"] == stable_workspace_transfer
assert install["env"]["TRANSFER_DIR"] == stable_workspace_transfer
assert install["env"]["NPM_CONFIG_CACHE"].startswith("${{ runner.temp }}/secretless-runtime-cache-")
assert install["env"]["SECRETLESS_RUNTIME_PUBLIC_CACHE"] == "${{ inputs.secretless-runtime-public-cache }}"
assert install["env"]["PERSISTED_PUBLIC_CACHE_DIR"].startswith("${{ runner.temp }}/secretless-public-npm-")
assert install["env"]["RESTORE_PERSISTED_PUBLIC_CACHE"] == "${{ inputs.cache && inputs.package-manager == 'npm' }}"
assert install["env"]["MAX_PUBLIC_RUNTIME_CACHE_BLOBS"] == "4096"
assert install["env"]["MAX_PUBLIC_RUNTIME_CACHE_BYTES"] == "268435456"
assert install["env"]["NPM_CONFIG_GLOBALCONFIG"].startswith("${{ runner.temp }}/")
assert install["env"]["NPM_CONFIG_USERCONFIG"].startswith("${{ runner.temp }}/")
assert "npm ci --ignore-scripts --prefer-offline --no-audit --no-fund" in install["run"]
assert "PUBLIC_RUNTIME_CACHE_REQUIRES_NPM" in install["run"]
assert "manifest_run_attempt" in install["run"]
assert 'manifest_run_attempt" -le "$RUN_ATTEMPT"' in install["run"]
assert "PERSISTED_PUBLIC_CACHE_CORRUPT" in install["run"]
assert "PERSISTED_PUBLIC_CACHE_PRIVATE_COLLISION" in install["run"]
assert "manifest_lock_sha256" in install["run"]
assert "manifest_payload_sha256" in install["run"]
assert '"$manifest_payload_sha256" = "$EXPECTED_PAYLOAD_SHA256"' in install["run"]
assert '"$manifest_payload_bytes" = "$EXPECTED_PAYLOAD_BYTES"' in install["run"]
assert 'tarfile.open(sys.argv[1], "r:")' in install["run"]
assert '-xf "$payload"' in install["run"]
assert "locked private package content is missing or corrupt" in install["run"]
build_cleanup = next(step for step in build["steps"] if step.get("name") == "Remove local secretless transfer state")
assert "always()" in build_cleanup["if"]
assert "needs.eligibility.outputs.should-run != 'false'" in build_cleanup["if"]
assert build_cleanup["env"]["TRANSFER_DIR"] == stable_workspace_transfer
runtime_cleanup = next(step for step in build["steps"] if step.get("name") == "Remove job-scoped secretless runtime cache")
assert "always()" in runtime_cleanup["if"]
assert "inputs.secretless-pr" in runtime_cleanup["if"]

assert "cleanup-secretless-transfer" not in jobs
assert "actions/upload-artifact@" not in str(acquire)
assert "actions/download-artifact@" not in str(build)
assert "actions: write" not in open(sys.argv[1], encoding="utf-8").read()

for command in ("npm run build", "npm run typecheck --if-present", "npm test", "npm run lint --if-present"):
    step = next(step for step in build["steps"] if step.get("run") == command)
    assert "secrets." not in str(step.get("env", {}))
PY

python3 - "$workflow" "$tmp" <<'PY'
import sys
import yaml

doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
root = sys.argv[2]
names = {
    "Populate verified private dependency cache": "populate.sh",
    "Package bounded credential-free npm cache": "package.sh",
    "Install from verified secretless npm cache": "install.sh",
    "Prepare job-scoped Playwright browser cache": "prepare-browser.sh",
    "Bound Playwright browser cache before save": "bound-browser.sh",
    "Remove job-scoped Playwright browser cache": "cleanup-browser.sh",
}
for job in doc["jobs"].values():
    for step in job.get("steps", []):
        name = step.get("name")
        if name in names:
            with open(f"{root}/{names[name]}", "w", encoding="utf-8") as stream:
                stream.write(step["run"])
populate = next(step for step in doc["jobs"]["acquire-secretless-dependencies"]["steps"]
                if step.get("name") == "Populate verified private dependency cache")
assert '${#expected_content[@]}' in populate["run"]
assert 'mktemp -d "$RUNNER_TEMP/secretless-private-npm-cache-${RUN_ID}-${RUN_ATTEMPT}-XXXXXX"' in populate["run"]
assert 'trap cleanup_failed_cache EXIT' in populate["run"]
assert 'Possible causes include package authorization' in populate["run"]
assert '${diagnostic_line//$NODE_AUTH_TOKEN/[REDACTED]}' in populate["run"]
assert 'tail -c 8192 "$npm_diagnostic" | tail -n 20' in populate["run"]
PY

mv "$tmp/package.sh" "$tmp/package-body.sh"
cat > "$tmp/package.sh" <<'BASH'
#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$RUNNER_TEMP"
cp "$TRANSFER_HELPER_SOURCE" "$RUNNER_TEMP/container_dependency_transfer.py"
bash "$PACKAGE_BODY_SCRIPT"
key="$(sed -n 's/^encryption-key=//p' "$GITHUB_OUTPUT")"
cp "$TRANSFER_DIR/npm-private-cache.tar.enc" \
  "$RUNNER_TEMP/cache-ciphertext-${RUN_ID}-${RUN_ATTEMPT}.bin"
TRANSFER_KEY="$key" python3 "$TRANSFER_HELPER_SOURCE" decrypt \
  --source "$TRANSFER_DIR/npm-private-cache.tar.enc" \
  --destination "$TRANSFER_DIR/npm-private-cache.tar"
rm -f "$TRANSFER_DIR/npm-private-cache.tar.enc"
BASH
chmod +x "$tmp/package.sh"

browser_env="$tmp/browser.env"
browser_output="$tmp/browser.output"
browser_dir="$tmp/browser-cache"
if BROWSER_CACHE_DIR="$browser_dir" GITHUB_ENV="$browser_env" GITHUB_OUTPUT="$browser_output" \
    bash "$tmp/prepare-browser.sh" \
    && [ "$(stat -c %a "$browser_dir")" = 700 ] \
    && grep -qFx "PLAYWRIGHT_BROWSERS_PATH=$browser_dir" "$browser_env" \
    && grep -qFx 'owned=true' "$browser_output"; then
  pass "Playwright uses a fresh mode-0700 job-scoped path exported to consumer scripts"
else
  fail "Playwright did not prepare and export its isolated cache path"
fi

mkdir -p "$tmp/browser-occupied"
printf 'preserve\n' > "$tmp/browser-occupied/sentinel"
if occupied_output="$(BROWSER_CACHE_DIR="$tmp/browser-occupied" \
      GITHUB_ENV="$tmp/occupied.env" GITHUB_OUTPUT="$tmp/occupied.output" \
      bash "$tmp/prepare-browser.sh" 2>&1)" \
    || ! grep -qF 'BROWSER_CACHE_DESTINATION_OCCUPIED' <<< "$occupied_output" \
    || ! grep -qFx 'preserve' "$tmp/browser-occupied/sentinel"; then
  fail "an occupied Playwright cache destination was modified or accepted"
else
  pass "an occupied Playwright cache destination fails closed without modification"
fi

mkdir -p "$tmp/browser-symlink-target"
ln -s "$tmp/browser-symlink-target" "$tmp/browser-symlink"
if symlink_destination_output="$(BROWSER_CACHE_DIR="$tmp/browser-symlink" \
      GITHUB_ENV="$tmp/symlink.env" GITHUB_OUTPUT="$tmp/symlink.output" \
      bash "$tmp/prepare-browser.sh" 2>&1)" \
    || ! grep -qF 'BROWSER_CACHE_DESTINATION_OCCUPIED' <<< "$symlink_destination_output" \
    || [ ! -d "$tmp/browser-symlink-target" ]; then
  fail "a symlinked Playwright cache destination was followed or accepted"
else
  pass "a symlinked Playwright cache destination fails closed without following it"
fi

printf 'browser\n' > "$browser_dir/chromium"
: > "$tmp/browser-bound.output"
if BROWSER_CACHE_DIR="$browser_dir" MAX_BROWSER_CACHE_FILES=1 \
    MAX_BROWSER_CACHE_BYTES=8 GITHUB_OUTPUT="$tmp/browser-bound.output" \
    bash "$tmp/bound-browser.sh" \
    && grep -qFx 'should-save=true' "$tmp/browser-bound.output"; then
  pass "a bounded regular-file Playwright cache is admitted for save"
else
  fail "a bounded Playwright cache was not admitted for save"
fi

printf 'second\n' > "$browser_dir/firefox"
if file_bound_output="$(BROWSER_CACHE_DIR="$browser_dir" MAX_BROWSER_CACHE_FILES=1 \
      MAX_BROWSER_CACHE_BYTES=1024 GITHUB_OUTPUT="$tmp/file-bound.output" \
      bash "$tmp/bound-browser.sh" 2>&1)" \
    || ! grep -qF 'BROWSER_CACHE_FILE_BOUND:2' <<< "$file_bound_output"; then
  fail "an over-count Playwright cache did not fail before save"
else
  pass "an over-count Playwright cache fails before save"
fi
rm "$browser_dir/firefox"

if byte_bound_output="$(BROWSER_CACHE_DIR="$browser_dir" MAX_BROWSER_CACHE_FILES=1 \
      MAX_BROWSER_CACHE_BYTES=1 GITHUB_OUTPUT="$tmp/byte-bound.output" \
      bash "$tmp/bound-browser.sh" 2>&1)" \
    || ! grep -qF 'BROWSER_CACHE_BYTE_BOUND:8' <<< "$byte_bound_output"; then
  fail "an oversized Playwright cache did not fail before save"
else
  pass "an oversized Playwright cache fails before save"
fi

ln -s "$tmp/browser-symlink-target" "$browser_dir/link"
if browser_entry_output="$(BROWSER_CACHE_DIR="$browser_dir" MAX_BROWSER_CACHE_FILES=10 \
      MAX_BROWSER_CACHE_BYTES=1024 GITHUB_OUTPUT="$tmp/browser-entry.output" \
      bash "$tmp/bound-browser.sh" 2>&1)" \
    || ! grep -qF 'BROWSER_CACHE_SYMLINK:' <<< "$browser_entry_output"; then
  fail "a symlink inside the Playwright cache did not fail before save"
else
  pass "a symlink inside the Playwright cache fails before save"
fi

rm "$browser_dir/link"
if BROWSER_CACHE_DIR="$browser_dir" bash "$tmp/cleanup-browser.sh" \
    && [ ! -e "$browser_dir" ] && [ ! -L "$browser_dir" ]; then
  pass "Playwright browser cache cleanup removes the exact job-scoped directory"
else
  fail "Playwright browser cache cleanup left job-scoped state"
fi

mkdir -p "$tmp/cleanup-browser-target"
printf 'preserve\n' > "$tmp/cleanup-browser-target/sentinel"
ln -s "$tmp/cleanup-browser-target" "$tmp/cleanup-browser-link"
if BROWSER_CACHE_DIR="$tmp/cleanup-browser-link" bash "$tmp/cleanup-browser.sh" \
    && [ ! -e "$tmp/cleanup-browser-link" ] && [ ! -L "$tmp/cleanup-browser-link" ] \
    && grep -qFx 'preserve' "$tmp/cleanup-browser-target/sentinel"; then
  pass "Playwright cleanup removes a substituted link without following it"
else
  fail "Playwright cleanup followed a substituted link or left it behind"
fi

mkdir -p "$tmp/bin" "$tmp/package-cache/_cacache/content-v2/sha512" "$tmp/acquire/node_modules"
printf 'cached private package bytes\n' > "$tmp/private-package.tgz"
private_digest="$(sha512sum "$tmp/private-package.tgz" | cut -d' ' -f1)"
private_integrity="sha512-$(openssl dgst -sha512 -binary "$tmp/private-package.tgz" | base64 -w0)"
private_content="$tmp/package-cache/_cacache/content-v2/sha512/${private_digest:0:2}/${private_digest:2:2}/${private_digest:4}"
mkdir -p "$(dirname "$private_content")"
cp "$tmp/private-package.tgz" "$private_content"
printf '%s\n' "{\"name\":\"fixture\",\"version\":\"1.0.0\",\"lockfileVersion\":3,\"packages\":{\"\":{\"name\":\"fixture\",\"version\":\"1.0.0\"},\"node_modules/@verjson/private-fixture\":{\"name\":\"@verjson/private-fixture\",\"version\":\"1.0.0\",\"resolved\":\"https://npm.pkg.github.com/download/@verjson/private-fixture/1.0.0/abc\",\"integrity\":\"$private_integrity\"}}}" \
  > "$tmp/acquire/package-lock.json"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'if [ "${NPM_STUB_FAIL:-false}" = true ]; then echo "npm ERR! network timeout for token=$NODE_AUTH_TOKEN" >&2; exit 42; fi' \
  'printf '\''%s\n'\'' "$*" >> "$NPM_STUB_LOG"' \
  'if [ "${1:-} ${2:-}" = "cache add" ]; then' \
  '  while [ "$#" -gt 0 ]; do' \
  '    if [ "$1" = --cache ]; then cache_dir="$2"; break; fi' \
  '    shift' \
  '  done' \
  '  content_path="$cache_dir/_cacache/content-v2/sha512/${NPM_STUB_DIGEST:0:2}/${NPM_STUB_DIGEST:2:2}/${NPM_STUB_DIGEST:4}"' \
  '  mkdir -p "$(dirname "$content_path")"' \
  '  cp "$NPM_STUB_CONTENT" "$content_path"' \
  'fi' \
  'if [ "${1:-}" = ci ] && [ -n "${NPM_STUB_PUBLIC_CONTENT:-}" ]; then' \
  '  while [ "$#" -gt 0 ]; do' \
  '    if [ "$1" = --cache ]; then cache_dir="$2"; break; fi' \
  '    shift' \
  '  done' \
  '  content_path="$cache_dir/_cacache/content-v2/sha512/${NPM_STUB_PUBLIC_DIGEST:0:2}/${NPM_STUB_PUBLIC_DIGEST:2:2}/${NPM_STUB_PUBLIC_DIGEST:4}"' \
  '  mkdir -p "$(dirname "$content_path")"' \
  '  if [ "${NPM_STUB_PUBLIC_SYMLINK:-false}" = true ]; then' \
  '    ln -s "$NPM_STUB_PUBLIC_CONTENT" "$content_path"' \
  '  else' \
  '    cp "$NPM_STUB_PUBLIC_CONTENT" "$content_path"' \
  '  fi' \
  '  mkdir -p "$cache_dir/_cacache/index-v5/fixture"' \
  '  printf '\''registry-request-metadata\n'\'' > "$cache_dir/_cacache/index-v5/fixture/entry"' \
  '  package_name="${NPM_STUB_PUBLIC_PACKAGE_NAME:-public-fixture}"' \
  '  mkdir -p "node_modules/$package_name"' \
  '  printf '\''{"name":"%s","version":"1.0.0"}\n'\'' "$package_name" > "node_modules/$package_name/package.json"' \
  'fi' \
  'if [ "${1:-}" = ci ] && [ -n "${NPM_STUB_REQUIRE_DIGEST:-}" ]; then' \
  '  while [ "$#" -gt 0 ]; do' \
  '    if [ "$1" = --cache ]; then cache_dir="$2"; break; fi' \
  '    shift' \
  '  done' \
  '  required="$cache_dir/_cacache/content-v2/sha512/${NPM_STUB_REQUIRE_DIGEST:0:2}/${NPM_STUB_REQUIRE_DIGEST:2:2}/${NPM_STUB_REQUIRE_DIGEST:4}"' \
  '  [ -f "$required" ] || { echo "required persisted blob was not imported" >&2; exit 43; }' \
  'fi' \
  > "$tmp/bin/npm"
chmod +x "$tmp/bin/npm"

mkdir -p "$tmp/empty"
: > "$tmp/empty/private-entries"
if PRIVATE_CACHE_ENTRIES="$tmp/empty/private-entries" \
    APPROVED_INTERNAL_SCOPES=@verjson NODE_AUTH_TOKEN=super-secret-value \
    NPM_CONFIG_GLOBALCONFIG="$tmp/empty/empty-global.npmrc" \
    NPM_CONFIG_USERCONFIG="$tmp/empty/acquisition.npmrc" \
    RUNNER_TEMP="$tmp/empty" RUN_ID=7001 RUN_ATTEMPT=3 GITHUB_OUTPUT="$tmp/empty/output" \
    NPM_STUB_LOG="$tmp/empty/npm.log" PATH="$tmp/bin:$PATH" \
    bash "$tmp/populate.sh" \
    && empty_cache="$(sed -n 's/^cache-dir=//p' "$tmp/empty/output")" \
    && [ -d "$empty_cache/_cacache/content-v2" ] \
    && [ ! -e "$tmp/empty/npm.log" ]; then
  pass "an empty approved package set produces a valid empty private cache without tokened npm requests"
else
  fail "an empty approved package set did not produce a bounded credential-free handoff"
fi

mkdir -p "$tmp/failed-retry"
printf '%s\t%s\n' \
  'https://npm.pkg.github.com/download/@verjson/private-fixture/1.0.0/abc' "$private_digest" \
  > "$tmp/failed-retry/private-entries"
if PRIVATE_CACHE_ENTRIES="$tmp/failed-retry/private-entries" \
    APPROVED_INTERNAL_SCOPES=@verjson NODE_AUTH_TOKEN=super-secret-value \
    NPM_CONFIG_GLOBALCONFIG="$tmp/failed-retry/empty-global.npmrc" \
    NPM_CONFIG_USERCONFIG="$tmp/failed-retry/acquisition.npmrc" \
    RUNNER_TEMP="$tmp/failed-retry" RUN_ID=7001 RUN_ATTEMPT=3 GITHUB_OUTPUT="$tmp/failed-retry/failed-output" \
    NPM_STUB_FAIL=true NPM_STUB_LOG="$tmp/failed-retry/npm.log" PATH="$tmp/bin:$PATH" \
    bash "$tmp/populate.sh" >"$tmp/failed-retry/diagnostic" 2>&1; then
  fail "a failed private-package download unexpectedly succeeded"
elif grep -qF 'npm exit 42' "$tmp/failed-retry/diagnostic" \
    && grep -qF 'network timeout for token=[REDACTED]' "$tmp/failed-retry/diagnostic" \
    && grep -qF 'network or registry availability' "$tmp/failed-retry/diagnostic" \
    && ! grep -qF 'super-secret-value' "$tmp/failed-retry/diagnostic" \
    && [ -z "$(find "$tmp/failed-retry" -maxdepth 1 -type d -name 'secretless-private-npm-cache-*' -print -quit)" ] \
    && PRIVATE_CACHE_ENTRIES="$tmp/failed-retry/private-entries" \
      APPROVED_INTERNAL_SCOPES=@verjson NODE_AUTH_TOKEN=token \
      NPM_CONFIG_GLOBALCONFIG="$tmp/failed-retry/empty-global.npmrc" \
      NPM_CONFIG_USERCONFIG="$tmp/failed-retry/acquisition.npmrc" \
      RUNNER_TEMP="$tmp/failed-retry" RUN_ID=7002 RUN_ATTEMPT=1 GITHUB_OUTPUT="$tmp/failed-retry/retry-output" \
      NPM_STUB_CONTENT="$tmp/private-package.tgz" NPM_STUB_DIGEST="$private_digest" \
      NPM_STUB_LOG="$tmp/failed-retry/retry.log" PATH="$tmp/bin:$PATH" \
      bash "$tmp/populate.sh"; then
  pass "failed acquisition cleans its unique cache, reports safe npm context, and permits a fresh retry"
else
  fail "failed acquisition left partial state, leaked its token, obscured npm context, or poisoned retry"
fi

mkdir -p "$tmp/duplicate-digest"
printf '%s\t%s\n%s\t%s\n' \
  'https://npm.pkg.github.com/download/@verjson/one/1.0.0/abc' "$private_digest" \
  'https://npm.pkg.github.com/download/@verjson/two/1.0.0/def' "$private_digest" \
  > "$tmp/duplicate-digest/private-entries"
if PRIVATE_CACHE_ENTRIES="$tmp/duplicate-digest/private-entries" \
    APPROVED_INTERNAL_SCOPES=@verjson NODE_AUTH_TOKEN=token \
    NPM_CONFIG_GLOBALCONFIG="$tmp/duplicate-digest/empty-global.npmrc" \
    NPM_CONFIG_USERCONFIG="$tmp/duplicate-digest/acquisition.npmrc" \
    RUNNER_TEMP="$tmp/duplicate-digest" RUN_ID=7003 RUN_ATTEMPT=1 GITHUB_OUTPUT="$tmp/duplicate-digest/output" \
    NPM_STUB_CONTENT="$tmp/private-package.tgz" NPM_STUB_DIGEST="$private_digest" \
    NPM_STUB_LOG="$tmp/duplicate-digest/npm.log" PATH="$tmp/bin:$PATH" \
    bash "$tmp/populate.sh"; then
  pass "private URLs sharing one digest count one content-addressed blob"
else
  fail "duplicate private digests were counted as distinct cache content"
fi

if (cd "$tmp/acquire" && PATH="$tmp/bin:$PATH" NPM_STUB_LOG="$tmp/npm.log" \
    CACHE_DIR="$tmp/package-cache" TRANSFER_DIR="$tmp/acquire/transfer" \
    AUXILIARY_COMMIT='' AUXILIARY_CONTENT_PATH='' AUXILIARY_REPOSITORY='' \
    GITHUB_WORKSPACE="$tmp/acquire" \
    GITHUB_OUTPUT="$tmp/acquire/package.outputs" \
    NPM_CONFIG_GLOBALCONFIG="$tmp/acquire/empty-global.npmrc" \
    NPM_CONFIG_USERCONFIG="$tmp/acquire/empty-user.npmrc" \
    MAX_PAYLOAD_BYTES=83886080 RUN_ID=7001 RUN_ATTEMPT=3 bash "$tmp/package.sh") \
    && [ ! -d "$tmp/acquire/node_modules" ] \
    && grep -qFx 'run_id=7001' "$tmp/acquire/transfer/manifest" \
    && grep -qFx 'run_attempt=3' "$tmp/acquire/transfer/manifest" \
    && ! grep -a -qF 'cached private package bytes' "$tmp/transfer-runner/cache-ciphertext-7001-3.bin" \
    && tar -tf "$tmp/acquire/transfer/npm-private-cache.tar" | grep '^_cacache/content-v2/' >/dev/null \
    && ! tar -tf "$tmp/acquire/transfer/npm-private-cache.tar" | grep 'index-v5' >/dev/null; then
  pass "packaging transfers only private content blobs and binds run, attempt, lock, digest, and size"
else
  fail "packaging did not produce the bounded identity-bound cache transfer"
fi

mkdir -p "$tmp/oversize/cache/_cacache/content-v2" "$tmp/oversize/node_modules"
for index in $(seq 1 256); do printf '%s' "$index" | sha256sum; done \
  > "$tmp/oversize/cache/_cacache/content-v2/blob"
cp "$tmp/acquire/package-lock.json" "$tmp/oversize/package-lock.json"
if (cd "$tmp/oversize" && PATH="$tmp/bin:$PATH" NPM_STUB_LOG="$tmp/npm.log" \
    CACHE_DIR="$tmp/oversize/cache" TRANSFER_DIR="$tmp/oversize/transfer" \
    AUXILIARY_COMMIT='' AUXILIARY_CONTENT_PATH='' AUXILIARY_REPOSITORY='' \
    GITHUB_WORKSPACE="$tmp/oversize" \
    GITHUB_OUTPUT="$tmp/oversize/package.outputs" \
    NPM_CONFIG_GLOBALCONFIG="$tmp/oversize/empty-global.npmrc" \
    NPM_CONFIG_USERCONFIG="$tmp/oversize/empty-user.npmrc" \
    MAX_PAYLOAD_BYTES=1024 RUN_ID=7001 RUN_ATTEMPT=3 bash "$tmp/package.sh") >/dev/null 2>&1; then
  fail "a cache over the declared transfer budget was packaged"
else
  pass "a cache over the declared transfer budget fails before save"
fi

run_install() {
  local fixture="$1" run_id="${2:-7001}" run_attempt="${3:-3}"
  local auxiliary_repository="${4:-}" auxiliary_commit="${5:-}" auxiliary_content_path="${6:-}"
  local public_runtime_cache="${7:-false}" runtime_cache="$fixture/runtime-cache"
  local package_manager="${8:-npm}" install_script="${INSTALL_SCRIPT:-$tmp/install.sh}"
  local persisted_public_cache="${9:-$fixture/persisted-public-cache}"
  local restore_persisted_public_cache="${10:-false}"
  local expected_payload_sha256 expected_payload_bytes
  if [ "$public_runtime_cache" = true ] || [ "$restore_persisted_public_cache" = true ]; then
    mkdir -p "$fixture/runner-temp"
    runtime_cache="$fixture/runner-temp/secretless-runtime-cache-$run_id-$run_attempt"
  fi
  expected_payload_sha256="$(sed -n 's/^payload_sha256=//p' "$fixture/transfer/manifest")"
  expected_payload_bytes="$(sed -n 's/^payload_bytes=//p' "$fixture/transfer/manifest")"
  (cd "$fixture" && PATH="$tmp/bin:$PATH" NPM_STUB_LOG="$fixture/npm.log" \
    NPM_STUB_PUBLIC_CONTENT="${NPM_STUB_PUBLIC_CONTENT:-}" \
    NPM_STUB_PUBLIC_DIGEST="${NPM_STUB_PUBLIC_DIGEST:-}" \
    NPM_STUB_PUBLIC_PACKAGE_NAME="${NPM_STUB_PUBLIC_PACKAGE_NAME:-}" \
    NPM_STUB_PUBLIC_SYMLINK="${NPM_STUB_PUBLIC_SYMLINK:-false}" \
    NPM_STUB_REQUIRE_DIGEST="${NPM_STUB_REQUIRE_DIGEST:-}" \
    GITHUB_ENV="$fixture/github.env" NPM_CONFIG_USERCONFIG="$fixture/empty.npmrc" \
    NPM_CONFIG_CACHE="$runtime_cache" \
    NPM_CONFIG_GLOBALCONFIG="$fixture/empty-global.npmrc" \
    APPROVED_INTERNAL_SCOPES=@verjson \
    EXPECTED_AUXILIARY_COMMIT="$auxiliary_commit" \
    EXPECTED_AUXILIARY_CONTENT_PATH="$auxiliary_content_path" \
    EXPECTED_AUXILIARY_REPOSITORY="$auxiliary_repository" GITHUB_WORKSPACE="$fixture" \
    EXPECTED_PAYLOAD_BYTES="$expected_payload_bytes" \
    EXPECTED_PAYLOAD_SHA256="$expected_payload_sha256" \
    MAX_PUBLIC_RUNTIME_CACHE_BLOBS="${MAX_PUBLIC_RUNTIME_CACHE_BLOBS:-4096}" \
    MAX_PUBLIC_RUNTIME_CACHE_BYTES="${MAX_PUBLIC_RUNTIME_CACHE_BYTES:-268435456}" \
    PERSISTED_PUBLIC_CACHE_DIR="$persisted_public_cache" \
    RESTORE_PERSISTED_PUBLIC_CACHE="$restore_persisted_public_cache" \
    SECRETLESS_RUNTIME_PUBLIC_CACHE="$public_runtime_cache" RUNNER_TEMP="$fixture/runner-temp" \
    PACKAGE_MANAGER="$package_manager" \
    SECRETLESS_CACHE_DIR="$fixture/build-cache" TRANSFER_DIR="$fixture/transfer" \
    MAX_PAYLOAD_BYTES=83886080 RUN_ID="$run_id" RUN_ATTEMPT="$run_attempt" \
    bash "$install_script")
}

rebind_payload_manifest() {
  local fixture="$1"
  local payload="$fixture/transfer/npm-private-cache.tar"
  local digest bytes
  digest="$(sha256sum "$payload" | cut -d' ' -f1)"
  bytes="$(stat -c %s "$payload")"
  sed -i "s/^payload_sha256=.*/payload_sha256=$digest/; s/^payload_bytes=.*/payload_bytes=$bytes/" \
    "$fixture/transfer/manifest"
}

printf '%s\n' \
  '{"name":"empty-fixture","version":"1.0.0","lockfileVersion":3,"packages":{"":{"name":"empty-fixture","version":"1.0.0"}}}' \
  > "$tmp/empty/package-lock.json"
if (cd "$tmp/empty" && PATH="$tmp/bin:$PATH" NPM_STUB_LOG="$tmp/empty/npm.log" \
    CACHE_DIR="$empty_cache" TRANSFER_DIR="$tmp/empty/transfer" \
    AUXILIARY_COMMIT='' AUXILIARY_CONTENT_PATH='' AUXILIARY_REPOSITORY='' \
    GITHUB_WORKSPACE="$tmp/empty" GITHUB_OUTPUT="$tmp/empty/package.outputs" \
    NPM_CONFIG_GLOBALCONFIG="$tmp/empty/empty-global.npmrc" \
    NPM_CONFIG_USERCONFIG="$tmp/empty/empty-user.npmrc" \
    MAX_PAYLOAD_BYTES=83886080 RUN_ID=7001 RUN_ATTEMPT=3 bash "$tmp/package.sh") \
    && empty_payload_bytes="$(stat -c %s "$tmp/empty/transfer/npm-private-cache.tar")" \
    && [ "$empty_payload_bytes" = "$(sed -n 's/^payload_bytes=//p' "$tmp/empty/transfer/manifest")" ] \
    && grep -qFx "payload-bytes=$empty_payload_bytes" "$tmp/empty/package.outputs" \
    && [ "$(tar -tf "$tmp/empty/transfer/npm-private-cache.tar" | grep -vc '/$')" -eq 0 ] \
    && run_install "$tmp/empty" \
    && grep -qF 'ci --ignore-scripts --prefer-offline --no-audit --no-fund --cache ' "$tmp/empty/npm.log" \
    && [ ! -e "$tmp/empty/transfer" ] \
    && [ ! -e "$tmp/empty/build-cache" ]; then
  pass "an empty approved package set completes the packaged credential-free handoff"
else
  fail "an empty approved package set failed the packaged credential-free handoff"
fi

mkdir -p "$tmp/build"
cp "$tmp/acquire/package-lock.json" "$tmp/build/package-lock.json"
cp -R "$tmp/acquire/transfer" "$tmp/build/transfer"
if run_install "$tmp/build" \
    && grep -qF 'ci --ignore-scripts --prefer-offline --no-audit --no-fund --cache ' "$tmp/build/npm.log" \
    && [ ! -e "$tmp/build/transfer" ] && [ ! -e "$tmp/build/build-cache" ] \
    && grep -qFx 'NODE_AUTH_TOKEN=' "$tmp/build/github.env" \
    && grep -qFx 'npm_config_cache='"$tmp/build/runtime-cache" "$tmp/build/github.env" \
    && grep -qFx 'ACTIONS_ID_TOKEN_REQUEST_TOKEN=' "$tmp/build/github.env"; then
  pass "a matching private-only transfer installs credentiallessly and scrubs local state"
else
  fail "the credentialless install did not consume and clean a valid private-only transfer"
fi

printf 'cached public package bytes\n' > "$tmp/public-package.tgz"
public_digest="$(sha512sum "$tmp/public-package.tgz" | cut -d' ' -f1)"
public_integrity="sha512-$(openssl dgst -sha512 -binary "$tmp/public-package.tgz" | base64 -w0)"
public_url='https://registry.npmjs.org/public-fixture/-/public-fixture-1.0.0.tgz'

prepare_public_fixture() {
  local fixture="$1" package_name="${2:-public-fixture}"
  mkdir -p "$fixture"
  cp -R "$tmp/acquire/transfer" "$fixture/transfer"
  node - "$tmp/acquire/package-lock.json" "$fixture/package-lock.json" \
    "$package_name" "$public_url" "$public_integrity" <<'NODE'
const fs = require('node:fs');
const [source, target, packageName, resolved, integrity] = process.argv.slice(2);
const lock = JSON.parse(fs.readFileSync(source, 'utf8'));
lock.packages[`node_modules/${packageName}`] = {
  name: packageName,
  version: '1.0.0',
  resolved,
  integrity,
};
fs.writeFileSync(target, `${JSON.stringify(lock)}\n`);
NODE
  local lock_digest
  lock_digest="$(sha256sum "$fixture/package-lock.json" | cut -d' ' -f1)"
  sed -i "s/^lock_sha256=.*/lock_sha256=$lock_digest/" "$fixture/transfer/manifest"
}

rebind_lock_manifest() {
  local fixture="$1" lock_file="${2:-package-lock.json}" lock_digest
  lock_digest="$(sha256sum "$fixture/$lock_file" | cut -d' ' -f1)"
  sed -i "s/^lock_sha256=.*/lock_sha256=$lock_digest/" "$fixture/transfer/manifest"
}

prepare_persisted_public_fixture() {
  local fixture="$1"
  prepare_public_fixture "$fixture"
  local cache="$fixture/persisted-public-cache"
  local content="$cache/_cacache/content-v2/sha512/${public_digest:0:2}/${public_digest:2:2}/${public_digest:4}"
  mkdir -p "$(dirname "$content")"
  cp "$tmp/public-package.tgz" "$content"
}

prepare_persisted_public_fixture "$tmp/persisted-public"
if NPM_STUB_REQUIRE_DIGEST="$public_digest" \
    run_install "$tmp/persisted-public" 7001 3 '' '' '' false npm \
      "$tmp/persisted-public/persisted-public-cache" true; then
  pass "a lock-matched verified public blob is imported before credentialless npm install"
else
  fail "a valid persisted public blob was not reusable by the credentialless install"
fi

prepare_persisted_public_fixture "$tmp/corrupt-persisted-public"
printf 'corrupt\n' >> "$tmp/corrupt-persisted-public/persisted-public-cache/_cacache/content-v2/sha512/${public_digest:0:2}/${public_digest:2:2}/${public_digest:4}"
if corrupt_persisted_output="$(run_install "$tmp/corrupt-persisted-public" 7001 3 '' '' '' false npm \
      "$tmp/corrupt-persisted-public/persisted-public-cache" true 2>&1)" \
    || ! grep -qF 'PERSISTED_PUBLIC_CACHE_CORRUPT' <<< "$corrupt_persisted_output"; then
  fail "a corrupt persisted public blob did not fail closed before npm"
else
  pass "a corrupt persisted public blob fails closed before npm"
fi

prepare_persisted_public_fixture "$tmp/private-persisted-public"
private_persisted="$tmp/private-persisted-public/persisted-public-cache/_cacache/content-v2/sha512/${private_digest:0:2}/${private_digest:2:2}/${private_digest:4}"
mkdir -p "$(dirname "$private_persisted")"
cp "$tmp/private-package.tgz" "$private_persisted"
if private_persisted_output="$(run_install "$tmp/private-persisted-public" 7001 3 '' '' '' false npm \
      "$tmp/private-persisted-public/persisted-public-cache" true 2>&1)" \
    || ! grep -qF 'PERSISTED_PUBLIC_CACHE_PRIVATE_COLLISION' <<< "$private_persisted_output"; then
  fail "a private-package blob crossed the persistent public-cache boundary"
else
  pass "a private-package blob is rejected from the persistent public cache"
fi

prepare_persisted_public_fixture "$tmp/metadata-persisted-public"
mkdir -p "$tmp/metadata-persisted-public/persisted-public-cache/_cacache/index-v5"
printf 'metadata\n' > "$tmp/metadata-persisted-public/persisted-public-cache/_cacache/index-v5/entry"
if metadata_persisted_output="$(run_install "$tmp/metadata-persisted-public" 7001 3 '' '' '' false npm \
      "$tmp/metadata-persisted-public/persisted-public-cache" true 2>&1)" \
    || ! grep -qF 'PERSISTED_PUBLIC_CACHE_UNEXPECTED_CONTENT' <<< "$metadata_persisted_output"; then
  fail "npm request metadata crossed the persistent public-cache boundary"
else
  pass "npm request metadata is rejected from the persistent public cache"
fi

prepare_public_fixture "$tmp/public-runtime"
public_runtime_content="$tmp/public-runtime/runner-temp/secretless-runtime-cache-7001-3/_cacache/content-v2/sha512/${public_digest:0:2}/${public_digest:2:2}/${public_digest:4}"
private_runtime_content="$tmp/public-runtime/runner-temp/secretless-runtime-cache-7001-3/_cacache/content-v2/sha512/${private_digest:0:2}/${private_digest:2:2}/${private_digest:4}"
if NPM_STUB_PUBLIC_CONTENT="$tmp/public-package.tgz" NPM_STUB_PUBLIC_DIGEST="$public_digest" \
    run_install "$tmp/public-runtime" 7001 3 '' '' '' true \
    && cmp -s "$tmp/public-package.tgz" "$public_runtime_content" \
    && [ ! -e "$private_runtime_content" ] \
    && [ ! -e "$tmp/public-runtime/runner-temp/secretless-runtime-cache-7001-3/_cacache/index-v5" ] \
    && [ "$(find "$tmp/public-runtime/runner-temp/secretless-runtime-cache-7001-3" -type f | wc -l)" -eq 1 ]; then
  pass "opt-in runtime cache copies only complete lock-verified public content blobs"
else
  fail "opt-in runtime cache did not preserve the public-only content boundary"
fi

prepare_public_fixture "$tmp/absent-platform-public"
if run_install "$tmp/absent-platform-public" 7001 3 '' '' '' true \
    && [ "$(find "$tmp/absent-platform-public/runner-temp/secretless-runtime-cache-7001-3" -type f | wc -l)" -eq 0 ]; then
  pass "lock-only platform package absent from the installed tree is not required"
else
  fail "runtime cache treated an uninstalled platform package as installed content"
fi

for destination_mutation in occupied symlinked; do
  prepare_public_fixture "$tmp/destination-$destination_mutation"
  destination_root="$tmp/destination-$destination_mutation/runner-temp"
  destination_cache="$destination_root/secretless-runtime-cache-7001-3"
  mkdir -p "$destination_root"
  if [ "$destination_mutation" = occupied ]; then
    mkdir -p "$destination_cache"
  else
    mkdir -p "$tmp/destination-symlink-target"
    ln -s "$tmp/destination-symlink-target" "$destination_cache"
  fi
  if destination_output="$(run_install "$tmp/destination-$destination_mutation" \
      7001 3 '' '' '' true 2>&1)" \
      || ! grep -qF 'PUBLIC_RUNTIME_CACHE_PATH' <<< "$destination_output"; then
    fail "$destination_mutation runtime-cache destination did not fail for its path reason"
  else
    pass "$destination_mutation runtime-cache destination fails for its path reason"
  fi
done

prepare_public_fixture "$tmp/noncanonical-public-url"
noncanonical_public_url='https://registry.npmjs.org:443/public-fixture/-/public-fixture-1.0.0.tgz'
node - "$tmp/noncanonical-public-url/package-lock.json" "$noncanonical_public_url" <<'NODE'
const fs = require('node:fs');
const [target, resolved] = process.argv.slice(2);
const lock = JSON.parse(fs.readFileSync(target, 'utf8'));
lock.packages['node_modules/public-fixture'].resolved = resolved;
fs.writeFileSync(target, `${JSON.stringify(lock)}\n`);
NODE
rebind_lock_manifest "$tmp/noncanonical-public-url"
if noncanonical_url_output="$(run_install "$tmp/noncanonical-public-url" \
    7001 3 '' '' '' true 2>&1)" \
    || ! grep -qF "PUBLIC_RUNTIME_CACHE_URL:$noncanonical_public_url" \
      <<< "$noncanonical_url_output"; then
  fail "a noncanonical public registry URL did not fail for the exact URL and reason"
else
  pass "a noncanonical public registry URL fails for the exact URL and reason"
fi

prepare_public_fixture "$tmp/installed-symlink-public"
mkdir -p "$tmp/installed-symlink-target" "$tmp/installed-symlink-public/node_modules"
printf '%s\n' '{"name":"public-fixture","version":"1.0.0"}' \
  > "$tmp/installed-symlink-target/package.json"
ln -s "$tmp/installed-symlink-target" \
  "$tmp/installed-symlink-public/node_modules/public-fixture"
if installed_symlink_output="$(run_install "$tmp/installed-symlink-public" \
    7001 3 '' '' '' true 2>&1)" \
    || ! grep -qF 'PUBLIC_RUNTIME_CACHE_INSTALLED_TYPE:node_modules/public-fixture' \
      <<< "$installed_symlink_output"; then
  fail "a symlinked installed package did not fail for the exact path and reason"
else
  pass "a symlinked installed package fails for the exact path and reason"
fi

prepare_public_fixture "$tmp/installed-identity-public"
mkdir -p "$tmp/installed-identity-public/node_modules/public-fixture"
printf '%s\n' '{"name":"public-fixture","version":"2.0.0"}' \
  > "$tmp/installed-identity-public/node_modules/public-fixture/package.json"
if installed_identity_output="$(run_install "$tmp/installed-identity-public" \
    7001 3 '' '' '' true 2>&1)" \
    || ! grep -qF 'PUBLIC_RUNTIME_CACHE_INSTALLED_IDENTITY:node_modules/public-fixture' \
      <<< "$installed_identity_output"; then
  fail "installed package identity drift did not fail for the exact path and reason"
else
  pass "installed package identity drift fails for the exact path and reason"
fi

prepare_public_fixture "$tmp/missing-public"
mkdir -p "$tmp/missing-public/node_modules/public-fixture"
printf '%s\n' '{"name":"public-fixture","version":"1.0.0"}' \
  > "$tmp/missing-public/node_modules/public-fixture/package.json"
if missing_public_output="$(run_install "$tmp/missing-public" 7001 3 '' '' '' true 2>&1)" \
    || ! grep -qF "PUBLIC_RUNTIME_CACHE_MISSING:$public_url" <<< "$missing_public_output"; then
  fail "missing locked public content did not fail for the exact URL and reason"
else
  pass "missing locked public content fails for the exact URL and reason"
fi

printf 'corrupt public package bytes\n' > "$tmp/corrupt-public-package.tgz"
prepare_public_fixture "$tmp/corrupt-public"
if corrupt_public_output="$(NPM_STUB_PUBLIC_CONTENT="$tmp/corrupt-public-package.tgz" \
    NPM_STUB_PUBLIC_DIGEST="$public_digest" \
    run_install "$tmp/corrupt-public" 7001 3 '' '' '' true 2>&1)" \
    || ! grep -qF "PUBLIC_RUNTIME_CACHE_CORRUPT:$public_url" <<< "$corrupt_public_output"; then
  fail "corrupt locked public content did not fail for the exact URL and reason"
else
  pass "corrupt locked public content fails for the exact URL and reason"
fi

prepare_public_fixture "$tmp/symlink-public"
if symlink_public_output="$(NPM_STUB_PUBLIC_CONTENT="$tmp/public-package.tgz" \
    NPM_STUB_PUBLIC_DIGEST="$public_digest" NPM_STUB_PUBLIC_SYMLINK=true \
    run_install "$tmp/symlink-public" 7001 3 '' '' '' true 2>&1)" \
    || ! grep -qF "PUBLIC_RUNTIME_CACHE_SOURCE_TYPE:$public_url" <<< "$symlink_public_output"; then
  fail "symlinked locked public content did not fail for the exact URL and reason"
else
  pass "symlinked locked public content fails for the exact URL and reason"
fi

prepare_public_fixture "$tmp/oversize-public"
if oversize_public_output="$(MAX_PUBLIC_RUNTIME_CACHE_BYTES=1 \
    NPM_STUB_PUBLIC_CONTENT="$tmp/public-package.tgz" NPM_STUB_PUBLIC_DIGEST="$public_digest" \
    run_install "$tmp/oversize-public" 7001 3 '' '' '' true 2>&1)" \
    || ! grep -qF 'PUBLIC_RUNTIME_CACHE_SIZE_BOUND' <<< "$oversize_public_output"; then
  fail "public runtime content over the byte bound did not fail closed"
else
  pass "public runtime content over the byte bound fails closed"
fi

printf 'second cached public package bytes\n' > "$tmp/second-public-package.tgz"
second_public_integrity="sha512-$(openssl dgst -sha512 -binary "$tmp/second-public-package.tgz" | base64 -w0)"
prepare_public_fixture "$tmp/overcount-public"
node - "$tmp/overcount-public/package-lock.json" "$second_public_integrity" <<'NODE'
const fs = require('node:fs');
const [target, integrity] = process.argv.slice(2);
const lock = JSON.parse(fs.readFileSync(target, 'utf8'));
lock.packages['node_modules/second-public-fixture'] = {
  name: 'second-public-fixture',
  version: '1.0.0',
  resolved: 'https://registry.npmjs.org/second-public-fixture/-/second-public-fixture-1.0.0.tgz',
  integrity,
};
fs.writeFileSync(target, `${JSON.stringify(lock)}\n`);
NODE
overcount_lock_digest="$(sha256sum "$tmp/overcount-public/package-lock.json" | cut -d' ' -f1)"
sed -i "s/^lock_sha256=.*/lock_sha256=$overcount_lock_digest/" \
  "$tmp/overcount-public/transfer/manifest"
mkdir -p "$tmp/overcount-public/node_modules/second-public-fixture"
printf '%s\n' '{"name":"second-public-fixture","version":"1.0.0"}' \
  > "$tmp/overcount-public/node_modules/second-public-fixture/package.json"
if overcount_public_output="$(MAX_PUBLIC_RUNTIME_CACHE_BLOBS=1 \
    NPM_STUB_PUBLIC_CONTENT="$tmp/public-package.tgz" NPM_STUB_PUBLIC_DIGEST="$public_digest" \
    run_install "$tmp/overcount-public" 7001 3 '' '' '' true 2>&1)" \
    || ! grep -qF 'PUBLIC_RUNTIME_CACHE_COUNT_BOUND' <<< "$overcount_public_output"; then
  fail "public runtime content over the blob-count bound did not fail closed"
else
  pass "public runtime content over the blob-count bound fails closed"
fi

prepare_public_fixture "$tmp/private-masquerade" '@verjson/private-masquerade'
if private_masquerade_output="$(NPM_STUB_PUBLIC_CONTENT="$tmp/public-package.tgz" \
    NPM_STUB_PUBLIC_DIGEST="$public_digest" \
    run_install "$tmp/private-masquerade" 7001 3 '' '' '' true 2>&1)" \
    || ! grep -qF 'PUBLIC_RUNTIME_CACHE_PRIVATE_PACKAGE:@verjson/private-masquerade' \
      <<< "$private_masquerade_output"; then
  fail "an internal package on the public host did not fail before runtime-cache transfer"
else
  pass "an internal package cannot cross through the public runtime-cache opt-in"
fi

mkdir -p "$tmp/private-digest-collision/cache/_cacache/content-v2/sha512/${public_digest:0:2}/${public_digest:2:2}"
cp "$tmp/public-package.tgz" \
  "$tmp/private-digest-collision/cache/_cacache/content-v2/sha512/${public_digest:0:2}/${public_digest:2:2}/${public_digest:4}"
node - "$tmp/acquire/package-lock.json" "$tmp/private-digest-collision/package-lock.json" \
  "$public_url" "$public_integrity" <<'NODE'
const fs = require('node:fs');
const [source, target, resolved, integrity] = process.argv.slice(2);
const lock = JSON.parse(fs.readFileSync(source, 'utf8'));
lock.packages['node_modules/@verjson/private-fixture'].integrity = integrity;
lock.packages['node_modules/public-fixture'] = {
  name: 'public-fixture', version: '1.0.0', resolved, integrity,
};
fs.writeFileSync(target, `${JSON.stringify(lock)}\n`);
NODE
(cd "$tmp/private-digest-collision" && \
  CACHE_DIR="$tmp/private-digest-collision/cache" \
  TRANSFER_DIR="$tmp/private-digest-collision/transfer" \
  AUXILIARY_COMMIT='' AUXILIARY_CONTENT_PATH='' AUXILIARY_REPOSITORY='' \
  GITHUB_WORKSPACE="$tmp/private-digest-collision" \
  GITHUB_OUTPUT="$tmp/private-digest-collision/package.outputs" \
  NPM_CONFIG_GLOBALCONFIG="$tmp/private-digest-collision/empty-global.npmrc" \
  NPM_CONFIG_USERCONFIG="$tmp/private-digest-collision/empty-user.npmrc" \
  MAX_PAYLOAD_BYTES=83886080 RUN_ID=7001 RUN_ATTEMPT=3 bash "$tmp/package.sh")
if private_collision_output="$(NPM_STUB_PUBLIC_CONTENT="$tmp/public-package.tgz" \
    NPM_STUB_PUBLIC_DIGEST="$public_digest" \
    run_install "$tmp/private-digest-collision" 7001 3 '' '' '' true 2>&1)" \
    || ! grep -qF 'PUBLIC_RUNTIME_CACHE_PRIVATE_COLLISION' <<< "$private_collision_output"; then
  fail "one digest carrying public and private identities did not fail closed"
else
  pass "one digest carrying public and private identities fails closed"
fi

python3 - "$tmp/install.sh" "$tmp/install-unexpected-content.sh" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding="utf-8")
needle = "actual_files = set()\n"
if source.count(needle) != 1:
    raise SystemExit("unexpected-content mutation target drift")
mutated = source.replace(
    needle,
    '(runtime_cache / "unexpected-content").write_bytes(b"unexpected\\n")\n' + needle,
)
Path(sys.argv[2]).write_text(mutated, encoding="utf-8")
PY
prepare_public_fixture "$tmp/unexpected-runtime-content"
if unexpected_content_output="$(INSTALL_SCRIPT="$tmp/install-unexpected-content.sh" \
    NPM_STUB_PUBLIC_CONTENT="$tmp/public-package.tgz" NPM_STUB_PUBLIC_DIGEST="$public_digest" \
    run_install "$tmp/unexpected-runtime-content" 7001 3 '' '' '' true 2>&1)" \
    || ! grep -qF 'PUBLIC_RUNTIME_CACHE_UNEXPECTED_CONTENT' <<< "$unexpected_content_output"; then
  fail "unexpected runtime-cache content did not fail its final completeness check"
else
  pass "unexpected runtime-cache content fails its final completeness check"
fi

mkdir -p "$tmp/pnpm-public-opt-in"
cp -R "$tmp/acquire/transfer" "$tmp/pnpm-public-opt-in/transfer"
cat > "$tmp/pnpm-public-opt-in/pnpm-lock.yaml" <<EOF
lockfileVersion: '9.0'
packages:
  '@verjson/private-fixture@1.0.0':
    resolution:
      tarball: https://npm.pkg.github.com/download/@verjson/private-fixture/1.0.0/abc
      integrity: $private_integrity
EOF
sed -i 's/^package_manager=.*/package_manager=pnpm/' \
  "$tmp/pnpm-public-opt-in/transfer/manifest"
rebind_lock_manifest "$tmp/pnpm-public-opt-in" pnpm-lock.yaml
if pnpm_opt_in_output="$(run_install "$tmp/pnpm-public-opt-in" \
    7001 3 '' '' '' true pnpm 2>&1)" \
    || ! grep -qF 'PUBLIC_RUNTIME_CACHE_REQUIRES_NPM' <<< "$pnpm_opt_in_output"; then
  fail "pnpm runtime-cache opt-in did not fail for its package-manager reason"
else
  pass "pnpm runtime-cache opt-in fails for its package-manager reason"
fi

mkdir -p "$tmp/missing-private"
cp "$tmp/acquire/package-lock.json" "$tmp/missing-private/package-lock.json"
cp -R "$tmp/acquire/transfer" "$tmp/missing-private/transfer"
missing_payload="$tmp/missing-private/transfer/npm-private-cache.tar"
python3 - "$missing_payload" <<'PY'
import sys
import tarfile
import tempfile
from pathlib import Path

payload = Path(sys.argv[1])
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    with tarfile.open(payload, "r:") as archive:
        archive.extractall(root, filter="data")
    for content in (root / "_cacache" / "content-v2" / "sha512").glob("*/*/*"):
        content.unlink()
        break
    with tarfile.open(payload, "w:") as archive:
        archive.add(root / "_cacache", arcname="_cacache")
PY
rebind_payload_manifest "$tmp/missing-private"
if missing_output="$(run_install "$tmp/missing-private" 2>&1)" \
    || [ -e "$tmp/missing-private/npm.log" ] \
    || ! grep -qF 'locked private package content is missing or corrupt' <<< "$missing_output"; then
  fail "missing private content was not rejected before npm"
else
  pass "missing private content fails closed before npm or lifecycle scripts"
fi

for mutation in poisoned-private extra-private; do
  mkdir -p "$tmp/$mutation"
  cp "$tmp/acquire/package-lock.json" "$tmp/$mutation/package-lock.json"
  cp -R "$tmp/acquire/transfer" "$tmp/$mutation/transfer"
  python3 - "$tmp/$mutation/transfer/npm-private-cache.tar" "$mutation" <<'PY'
import sys
import tarfile
import tempfile
from pathlib import Path

payload = Path(sys.argv[1])
mutation = sys.argv[2]
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    with tarfile.open(payload, "r:") as archive:
        archive.extractall(root, filter="data")
    content_root = root / "_cacache" / "content-v2" / "sha512"
    if mutation == "poisoned-private":
        next(content_root.glob("*/*/*")).write_bytes(b"poisoned\n")
    else:
        extra = content_root / "00" / "00" / ("0" * 124)
        extra.parent.mkdir(parents=True)
        extra.write_bytes(b"extra\n")
    with tarfile.open(payload, "w:") as archive:
        archive.add(root / "_cacache", arcname="_cacache")
PY
  rebind_payload_manifest "$tmp/$mutation"
done
if poisoned_output="$(run_install "$tmp/poisoned-private" 2>&1)" \
    || [ -e "$tmp/poisoned-private/npm.log" ] \
    || ! grep -qF 'locked private package content is missing or corrupt' <<< "$poisoned_output"; then
  fail "poisoned private content was not rejected before npm"
else
  pass "poisoned private content fails closed before npm or lifecycle scripts"
fi
if extra_output="$(run_install "$tmp/extra-private" 2>&1)" \
    || [ -e "$tmp/extra-private/npm.log" ] \
    || ! grep -qF 'private cache contains content outside the locked package set' <<< "$extra_output"; then
  fail "extra private cache content was not rejected before npm"
else
  pass "content outside the locked private package set fails before npm"
fi

auxiliary_commit='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
mkdir -p "$tmp/aux-acquire/cache/_cacache/content-v2/sha512/${private_digest:0:2}/${private_digest:2:2}" \
  "$tmp/aux-acquire/.worker-schema/migrations"
cp "$tmp/private-package.tgz" \
  "$tmp/aux-acquire/cache/_cacache/content-v2/sha512/${private_digest:0:2}/${private_digest:2:2}/${private_digest:4}"
printf 'create table fixture();\n' > "$tmp/aux-acquire/.worker-schema/migrations/102_storage.sql"
cp "$tmp/acquire/package-lock.json" "$tmp/aux-acquire/package-lock.json"
if (cd "$tmp/aux-acquire" && PATH="$tmp/bin:$PATH" NPM_STUB_LOG="$tmp/npm.log" \
    AUXILIARY_REPOSITORY=tequityapp/tequity-worker AUXILIARY_COMMIT="$auxiliary_commit" \
    AUXILIARY_CONTENT_PATH=.worker-schema/migrations GITHUB_WORKSPACE="$tmp/aux-acquire" \
    GITHUB_OUTPUT="$tmp/aux-acquire/package.outputs" \
    CACHE_DIR="$tmp/aux-acquire/cache" TRANSFER_DIR="$tmp/aux-acquire/transfer" \
    NPM_CONFIG_GLOBALCONFIG="$tmp/aux-acquire/empty-global.npmrc" \
    NPM_CONFIG_USERCONFIG="$tmp/aux-acquire/empty-user.npmrc" \
    MAX_PAYLOAD_BYTES=83886080 RUN_ID=7001 RUN_ATTEMPT=3 bash "$tmp/package.sh") \
    && grep -qFx 'auxiliary_repository=tequityapp/tequity-worker' "$tmp/aux-acquire/transfer/manifest" \
    && grep -qFx "auxiliary_commit=$auxiliary_commit" "$tmp/aux-acquire/transfer/manifest"; then
  pass "the immutable auxiliary sparse content shares the bounded identity-bound payload"
else
  fail "auxiliary sparse content was not bound into the transfer"
fi

mkdir -p "$tmp/aux-build"
cp "$tmp/aux-acquire/package-lock.json" "$tmp/aux-build/package-lock.json"
cp -R "$tmp/aux-acquire/transfer" "$tmp/aux-build/transfer"
if run_install "$tmp/aux-build" 7001 3 tequityapp/tequity-worker "$auxiliary_commit" .worker-schema/migrations \
    && grep -qFx 'create table fixture();' "$tmp/aux-build/.worker-schema/migrations/102_storage.sql"; then
  pass "a matching auxiliary source is restored only after full manifest verification"
else
  fail "a valid auxiliary source was not restored for credentialless execution"
fi

mkdir -p "$tmp/aux-wrong"
cp "$tmp/aux-acquire/package-lock.json" "$tmp/aux-wrong/package-lock.json"
cp -R "$tmp/aux-acquire/transfer" "$tmp/aux-wrong/transfer"
if run_install "$tmp/aux-wrong" 7001 3 attacker/other "$auxiliary_commit" .worker-schema/migrations >/dev/null 2>&1 \
    || [ -e "$tmp/aux-wrong/npm.log" ]; then
  fail "a mismatched auxiliary repository reached npm"
else
  pass "a mismatched auxiliary repository fails before npm"
fi

mkdir -p "$tmp/earlier-attempt"
cp "$tmp/acquire/package-lock.json" "$tmp/earlier-attempt/package-lock.json"
cp -R "$tmp/acquire/transfer" "$tmp/earlier-attempt/transfer"
if run_install "$tmp/earlier-attempt" 7001 4 >/dev/null 2>&1 \
    && [ -e "$tmp/earlier-attempt/npm.log" ]; then
  pass "an immutable transfer from an earlier attempt remains consumable in the same run"
else
  fail "an earlier-attempt transfer could not be reused within its bound run"
fi

for mutation in run future-attempt lock digest; do
  fixture="$tmp/reject-$mutation"
  mkdir -p "$fixture"
  cp "$tmp/acquire/package-lock.json" "$fixture/package-lock.json"
  cp -R "$tmp/acquire/transfer" "$fixture/transfer"
  case "$mutation" in
    run) run_id=7002; run_attempt=3 ;;
    future-attempt) run_id=7001; run_attempt=2 ;;
    lock) printf '\n' >> "$fixture/package-lock.json"; run_id=7001; run_attempt=3 ;;
    digest) printf 'tamper\n' >> "$fixture/transfer/npm-private-cache.tar"; run_id=7001; run_attempt=3 ;;
  esac
  if run_install "$fixture" "$run_id" "$run_attempt" >/dev/null 2>&1 || [ -e "$fixture/npm.log" ]; then
    fail "a transfer with a mismatched $mutation identity reached npm"
  else
    pass "a transfer with a mismatched $mutation identity fails before npm"
  fi
done

mkdir -p "$tmp/corrupt"
cp "$tmp/acquire/package-lock.json" "$tmp/corrupt/package-lock.json"
cp -R "$tmp/acquire/transfer" "$tmp/corrupt/transfer"
printf 'not a tar archive\n' > "$tmp/corrupt/transfer/npm-private-cache.tar"
if ! rebind_payload_manifest "$tmp/corrupt"; then
  fail "could not prepare the corrupt private payload fixture"
elif run_install "$tmp/corrupt" >/dev/null 2>&1 || [ -e "$tmp/corrupt/npm.log" ]; then
  fail "a corrupt private payload reached npm"
else
  pass "a corrupt private payload fails before npm"
fi

mkdir -p "$tmp/traversal"
cp "$tmp/acquire/package-lock.json" "$tmp/traversal/package-lock.json"
cp -R "$tmp/acquire/transfer" "$tmp/traversal/transfer"
python3 - "$tmp/traversal/transfer/npm-private-cache.tar" <<'PY'
import io
import sys
import tarfile

with tarfile.open(sys.argv[1], "w:") as archive:
    cache = tarfile.TarInfo("_cacache")
    cache.type = tarfile.DIRTYPE
    archive.addfile(cache)
    content = tarfile.TarInfo("_cacache/content-v2")
    content.type = tarfile.DIRTYPE
    archive.addfile(content)
    content = b"escape\n"
    member = tarfile.TarInfo("../escape")
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))
PY
if ! rebind_payload_manifest "$tmp/traversal"; then
  fail "could not prepare the traversal private payload fixture"
elif traversal_output="$(run_install "$tmp/traversal" 2>&1)"; then
  fail "a traversal member escaped private payload validation"
elif [ -e "$tmp/traversal/npm.log" ] || [ -e "$tmp/traversal/escape" ] \
    || ! grep -qF 'unsafe secretless transfer path: ../escape' <<< "$traversal_output"; then
  fail "a traversal member escaped private payload validation"
else
  pass "a traversal member in a private payload fails before extraction and npm"
fi

# #1830: both generated workflows carry the same fail-closed restore and diagnostic, and
# the diagnostic only explains: it prints one error, touches nothing, and never rescues the job.
python3 - "$root/.github/workflows/node-ci.yml" "$root/.github/workflows/node-ci-protected.yml" "$tmp" <<'PY' \
  && pass "a failed secretless transfer restore stays fail-closed and explains itself in both workflows" \
  || fail "the secretless transfer restore diagnostic is missing, unguarded, or differs in the protected twin"
import subprocess
import sys
import yaml

for path in sys.argv[1:3]:
    build = yaml.safe_load(open(path, encoding="utf-8"))["jobs"]["build-test"]
    steps = build["steps"]
    restore = next(step for step in steps if step.get("id") == "restore-secretless-transfer")
    diagnostic = steps[steps.index(restore) + 1]
    assert restore["with"]["fail-on-cache-miss"] is True, path
    assert diagnostic["name"] == "Explain a failed secretless transfer restore", path
    assert diagnostic["if"] == "failure() && needs.eligibility.outputs.should-run != 'false' && steps.restore-secretless-transfer.outcome == 'failure'", path
    cleanup = next(step for step in steps if step.get("name") == "Remove local secretless transfer state")
    assert cleanup["if"].startswith("always()") and steps.index(cleanup) > steps.index(diagnostic), path
    done = subprocess.run(["bash", "-c", diagnostic["run"]], capture_output=True, text=True, cwd=sys.argv[3])
    assert done.returncode == 0 and done.stdout.count("::error::") == 1, path
    assert "secretless-npm-transfer" not in done.stdout and "key" not in done.stdout.lower().replace("keyed", ""), path
PY

exit "$failures"
