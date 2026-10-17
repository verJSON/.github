#!/usr/bin/env bash
# Executes actionlint.yml's "Run actionlint over all workflows" step against
# stub ShellCheck binaries. The step must run actionlint only with exactly the
# pinned ShellCheck version (ADR 0026, 2026-10-17 amendment): any other
# version, a binary that reports none, or a missing pin fails before actionlint
# starts, and the error names what was expected and what was found.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
wf="$root/.github/workflows/actionlint.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

step="$tmp/lint.sh"
awk '
  $0 == "      - name: Run actionlint over all workflows" { seen = 1 }
  seen && $0 == "        run: |" { capture = 1; next }
  capture {
    if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
    if ($0 ~ /^[[:space:]]*$/) { print ""; next }
    exit
  }
' "$wf" >"$step"
grep -q 'actionlint' "$step" || { echo "FAIL - could not extract the lint step from $wf"; exit 1; }

pinned="$(awk '$1 == "SHELLCHECK_VERSION:" {gsub(/['\''"]/, "", $2); print $2; exit}' "$wf")"
[[ "$pinned" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  && pass "the workflow pins ShellCheck $pinned" \
  || fail "the workflow does not pin a ShellCheck version: '${pinned}'"

mkdir -p "$tmp/work"
cat >"$tmp/work/actionlint" <<'SH'
#!/bin/bash
printf '%s\n' "$*" >"$ACTIONARGS"
SH
chmod +x "$tmp/work/actionlint"

# run_lint <version line or NONE> <SHELLCHECK_VERSION value or UNSET>
run_lint() {
  local reported="$1" pin="$2" bin
  bin="$(mktemp -d "$tmp/bin.XXXXXX")"
  if [ "$reported" = NONE ]; then
    printf '#!/bin/bash\necho "ShellCheck - shell script analysis tool"\n' >"$bin/shellcheck"
  else
    printf '#!/bin/bash\necho "ShellCheck - shell script analysis tool"\necho "version: %s"\n' "$reported" \
      >"$bin/shellcheck"
  fi
  chmod +x "$bin/shellcheck"
  ACTIONARGS="$tmp/args"
  rm -f "$ACTIONARGS"
  (
    cd "$tmp/work" || exit 1
    export ACTIONARGS ACTIONLINT_CONFIG_FILE=/dev/null PATH="$bin:/usr/bin:/bin"
    if [ "$pin" = UNSET ]; then unset SHELLCHECK_VERSION; else export SHELLCHECK_VERSION="$pin"; fi
    /bin/bash "$step"
  ) >"$tmp/out" 2>&1
  RC=$?
}

run_lint "$pinned" "$pinned"
[ "$RC" -eq 0 ] && [ -s "$tmp/args" ] \
  && pass "the pinned ShellCheck $pinned runs actionlint" \
  || fail "the pinned ShellCheck did not run actionlint (rc=$RC): $(cat "$tmp/out")"

for wrong in 0.9.0 0.11.1 "1$pinned" "$pinned-rc1" "$pinned extra" NONE; do
  run_lint "$wrong" "$pinned"
  if [ "$RC" -ne 0 ] && [ ! -e "$tmp/args" ] \
    && grep -qF "needs ShellCheck $pinned" "$tmp/out"; then
    pass "ShellCheck reporting '${wrong}' fails before actionlint runs"
  else
    fail "ShellCheck reporting '${wrong}' was accepted (rc=$RC): $(cat "$tmp/out")"
  fi
done

# A step whose pin is missing must not compare an empty version to an empty one.
for pin in UNSET ""; do
  run_lint NONE "$pin"
  [ "$RC" -ne 0 ] && [ ! -e "$tmp/args" ] \
    && pass "a missing ShellCheck pin ('${pin}') fails closed even when ShellCheck reports no version" \
    || fail "a missing ShellCheck pin ('${pin}') let actionlint run (rc=$RC): $(cat "$tmp/out")"
done

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
