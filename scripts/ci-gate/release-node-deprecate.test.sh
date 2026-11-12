#!/usr/bin/env bash
set -euo pipefail

# Behavioral coverage for node-release.yml's "Deprecate renamed package names"
# step (verjson-agents#987): a package rename must npm-deprecate the old
# scoped name before its versions can silently age out under retention with
# no forwarding pointer (the @verjson/uploads -> @verjson/object-storage
# failure the issue documents). This extracts the exact step body between its
# BEGIN/END markers and exercises it against a stubbed npm, the same pattern
# scripts/ci-gate/release-node-notes.test.sh uses for the release-notes step.

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
workflow="$repo_root/.github/workflows/node-release.yml"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

awk '
  /DEPRECATE_RENAMED_PACKAGES_BEGIN/ { active = 1 }
  active { sub(/^          /, ""); print }
  /DEPRECATE_RENAMED_PACKAGES_END/ { exit }
' "$workflow" >"$work/deprecate.sh"
bash -n "$work/deprecate.sh"

mkdir -p "$work/bin" "$work/state" "$work/tmp"
cat >"$work/bin/npm" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf 'npm %s\n' "$*" >> "$TEST_STATE/npm-calls"
STUB
chmod +x "$work/bin/npm"

node_bin="$(dirname "$(command -v node)")"

run_deprecate() {
  env -i \
    PATH="$work/bin:$node_bin:/usr/bin:/bin" \
    TEST_STATE="$work/state" \
    RUNNER_TEMP="$work/tmp" \
    SCOPE='@verjson' \
    DEPRECATES="$1" \
    bash "$work/deprecate.sh"
}

# Every refusal is an Error the step's node parser throws, so its stderr line is
# what names the guard; a crash or a different guard is nonzero too.
refuses_with() { # refuses_with <reason> <deprecates JSON>
  local reason="$1" status=0
  run_deprecate "$2" 2>"$work/state/stderr" || status=$?
  if [ "$status" -eq 0 ]; then
    return 1
  fi
  if ! grep -qF -- "Error: $reason" "$work/state/stderr"; then
    echo "FAIL - refused (exit $status) without the reason: $reason" >&2
    sed 's/^/diag - /' "$work/state/stderr" >&2
    exit 1
  fi
}

rm -rf "$work/state"; mkdir -p "$work/state"
run_deprecate '[{"name":"uploads","message":"renamed to @verjson/object-storage; see https://github.com/Verjson/verjson-object-storage"}]'
[ -f "$work/state/npm-calls" ] || { echo "FAIL - a single rename entry did not call npm deprecate"; exit 1; }
grep -qF 'npm deprecate @verjson/uploads@* renamed to @verjson/object-storage; see https://github.com/Verjson/verjson-object-storage --registry=https://npm.pkg.github.com' \
  "$work/state/npm-calls" || { echo "FAIL - npm deprecate was not called with the expected scoped name, message, and registry"; exit 1; }
echo "ok - a rename entry deprecates the old scoped package name with its forwarding message"

rm -rf "$work/state"; mkdir -p "$work/state"
run_deprecate '[{"name":"uploads","message":"m1"},{"name":"legacy-schema","message":"m2"}]'
[ "$(wc -l <"$work/state/npm-calls")" -eq 2 ]
grep -qF '@verjson/uploads@*' "$work/state/npm-calls"
grep -qF '@verjson/legacy-schema@*' "$work/state/npm-calls"
echo "ok - multiple rename entries each deprecate their own old name"

rm -rf "$work/state"; mkdir -p "$work/state"
run_deprecate '[]'
[ -f "$work/state/npm-calls" ] && { echo "FAIL - an empty deprecates array must not call npm"; exit 1; }
echo "ok - an empty deprecates array is a no-op"

rm -rf "$work/state"; mkdir -p "$work/state"
if ! refuses_with "each deprecates entry needs a non-empty basename 'name' and 'message'" '[{"name":"uploads"}]'; then
  echo "FAIL - an entry missing 'message' must fail closed"; exit 1
fi
[ ! -f "$work/state/npm-calls" ] || { echo "FAIL - npm was called despite a malformed entry"; exit 1; }
echo "ok - an entry missing a required field fails closed before any npm deprecate call"

rm -rf "$work/state"; mkdir -p "$work/state"
if ! refuses_with 'duplicate deprecates entry: uploads' '[{"name":"uploads","message":"a"},{"name":"uploads","message":"b"}]'; then
  echo "FAIL - a duplicate old-name entry must fail closed"; exit 1
fi
[ ! -f "$work/state/npm-calls" ] || { echo "FAIL - npm was called despite a duplicate entry"; exit 1; }
echo "ok - a duplicate old-name entry fails closed instead of deprecating twice"

rm -rf "$work/state"; mkdir -p "$work/state"
if ! refuses_with 'deprecates is not valid JSON: ' 'not-json'; then
  echo "FAIL - invalid JSON must fail closed"; exit 1
fi
echo "ok - invalid JSON input fails closed"

rm -rf "$work/state"; mkdir -p "$work/state"
if ! refuses_with "deprecates entry 'uploads' has a message starting with '-'" '[{"name":"uploads","message":"--force renamed"}]'; then
  echo "FAIL - a message starting with '-' must fail closed"; exit 1
fi
[ ! -f "$work/state/npm-calls" ] || { echo "FAIL - npm was called despite a message starting with '-'"; exit 1; }
echo "ok - a message starting with '-' fails closed before any npm deprecate call"

echo "All tests passed."
