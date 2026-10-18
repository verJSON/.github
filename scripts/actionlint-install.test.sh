#!/usr/bin/env bash
# Tests the exact actionlint installer run block with stubbed download/extraction
# commands. A checksum mismatch must stop before an archive is extracted or a
# downloaded binary executes (Verjson/.github#83).
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
wf="$(cd "$here/.." && pwd)/.github/workflows/actionlint.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

script="$tmp/install.sh"
awk '
  $0 == "      - name: Install actionlint (pinned release binary)" { seen = 1 }
  seen && $0 == "        run: |" { capture = 1; next }
  capture {
    if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
    if ($0 ~ /^[ \t]*$/) { print ""; next }
    exit
  }
' "$wf" >"$script"
if ! grep -q 'sha256sum --check --strict' "$script" || ! grep -q 'tar -xzf' "$script"; then
  echo "FAIL - could not extract checksum-verifying installer from $wf"
  exit 1
fi

pinned="$(awk '$1 == "ACTIONLINT_SHA256:" {gsub(/['\''\"]/, "", $2); print $2; exit}' "$wf")"
[[ "$pinned" =~ ^[0-9a-f]{64}$ ]] \
  && pass "workflow pins a full SHA-256 digest" \
  || fail "workflow checksum is missing or malformed"

mkdir -p "$tmp/bin"
cat >"$tmp/bin/curl" <<'SH'
#!/usr/bin/env bash
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; break; fi
  shift
done
printf '%s' "$DOWNLOAD_CONTENT" >"$out"
SH
cat >"$tmp/bin/tar" <<'SH'
#!/usr/bin/env bash
echo TAR >>"$ACTIONLOG"
cat > actionlint <<'BIN'
#!/usr/bin/env bash
echo EXEC >>"$ACTIONLOG"
BIN
chmod +x actionlint
SH
chmod +x "$tmp/bin/curl" "$tmp/bin/tar"

run_case() {
  local checksum="$1" case_dir
  case_dir="$(mktemp -d "$tmp/case.XXXXXX")"
  export PATH="$tmp/bin:$PATH"
  export ACTIONLINT_VERSION=1.7.7 ACTIONLINT_SHA256="$checksum"
  export DOWNLOAD_CONTENT='fixture archive bytes'
  export ACTIONLOG="$case_dir/actions.log"
  : >"$ACTIONLOG"
  (cd "$case_dir" && bash "$script") >"$case_dir/out.txt" 2>&1
  RUN_RC=$?
}

good="$(printf '%s' 'fixture archive bytes' | sha256sum | awk '{print $1}')"
run_case "$good"; rc="$RUN_RC"
{ [ "$rc" -eq 0 ] && grep -q TAR "$ACTIONLOG" && grep -q EXEC "$ACTIONLOG"; } \
  && pass "matching checksum permits extraction and execution" \
  || fail "matching checksum did not complete installer"

bad="0${good:1}"
[ "${good:0:1}" = 0 ] && bad="1${good:1}"
run_case "$bad"; rc="$RUN_RC"
{ [ "$rc" -ne 0 ] && ! grep -q TAR "$ACTIONLOG" && ! grep -q EXEC "$ACTIONLOG"; } \
  && pass "checksum mismatch fails before extraction or execution (#83)" \
  || fail "checksum mismatch was not fail-closed (#83)"

# ShellCheck is pinned the same way (ADR 0026): the hosted image's 0.9.0 and the
# pool image's newer build disagree, so the lint verdict must not depend on the
# route. The installer verifies the digest before extracting and puts only the
# pinned binary on the job PATH.
sc_script="$tmp/install-shellcheck.sh"
awk '
  $0 == "      - name: Install ShellCheck (pinned release binary)" { seen = 1 }
  seen && $0 == "        run: |" { capture = 1; next }
  capture {
    if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
    if ($0 ~ /^[ \t]*$/) { print ""; next }
    exit
  }
' "$wf" >"$sc_script"
sc_version="$(awk '$1 == "SHELLCHECK_VERSION:" {gsub(/['\''\"]/, "", $2); print $2; exit}' "$wf")"
sc_pinned="$(awk '$1 == "SHELLCHECK_SHA256:" {gsub(/['\''\"]/, "", $2); print $2; exit}' "$wf")"
if grep -q 'sha256sum --check --strict' "$sc_script" && grep -q 'tar -xzf' "$sc_script" \
  && [[ "$sc_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] && [[ "$sc_pinned" =~ ^[0-9a-f]{64}$ ]]; then
  pass "workflow pins a ShellCheck version and a full SHA-256 digest"
else
  fail "ShellCheck installer, version, or digest is missing or malformed"
fi

mkdir -p "$tmp/scbin"
cp "$tmp/bin/curl" "$tmp/scbin/curl"
cat >"$tmp/scbin/tar" <<'SH'
#!/usr/bin/env bash
echo TAR >>"$ACTIONLOG"
mkdir -p "shellcheck-v$SHELLCHECK_VERSION"
cat >"shellcheck-v$SHELLCHECK_VERSION/shellcheck" <<'BIN'
#!/usr/bin/env bash
echo EXEC >>"$ACTIONLOG"
printf 'version: %s\n' "$SHELLCHECK_VERSION"
BIN
chmod +x "shellcheck-v$SHELLCHECK_VERSION/shellcheck"
SH
chmod +x "$tmp/scbin/curl" "$tmp/scbin/tar"

run_shellcheck_case() {
  local checksum="$1"
  SC_DIR="$(mktemp -d "$tmp/sc.XXXXXX")"
  ACTIONLOG="$SC_DIR/actions.log"
  : >"$ACTIONLOG"
  : >"$SC_DIR/github_path"
  (cd "$SC_DIR" && PATH="$tmp/scbin:$PATH" SHELLCHECK_VERSION="$sc_version" SHELLCHECK_SHA256="$checksum" \
    GITHUB_PATH="$SC_DIR/github_path" ACTIONLOG="$ACTIONLOG" DOWNLOAD_CONTENT='fixture archive bytes' \
    bash "$sc_script") >"$SC_DIR/out.txt" 2>&1
  RUN_RC=$?
}

run_shellcheck_case "$good"
{ [ "$RUN_RC" -eq 0 ] && grep -q TAR "$ACTIONLOG" && grep -q EXEC "$ACTIONLOG" \
  && [ -x "$SC_DIR/.verjson-shellcheck/shellcheck" ] \
  && [ "$(cat "$SC_DIR/github_path")" = "$SC_DIR/.verjson-shellcheck" ]; } \
  && pass "a matching ShellCheck digest installs only the pinned binary on the job PATH" \
  || fail "a matching ShellCheck digest did not install the pinned binary: $(cat "$SC_DIR/out.txt")"

run_shellcheck_case "$bad"
{ [ "$RUN_RC" -ne 0 ] && ! grep -q TAR "$ACTIONLOG" && ! grep -q EXEC "$ACTIONLOG" \
  && [ ! -s "$SC_DIR/github_path" ]; } \
  && pass "a ShellCheck digest mismatch fails before extraction, execution, or PATH changes" \
  || fail "a ShellCheck digest mismatch was not fail-closed"

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
