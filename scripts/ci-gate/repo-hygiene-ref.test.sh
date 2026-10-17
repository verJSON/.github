#!/usr/bin/env bash
# Exercise the reusable hygiene workflow's immutable-ref and merged-ref guards.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
workflow="$repo_root/.github/workflows/repo-hygiene.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

extract_step() {
  local name="$1" destination="$2"
  awk -v step="      - name: $name" '
    $0 == step { seen = 1 }
    seen && $0 == "        run: |" { capture = 1; next }
    capture {
      if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
      if ($0 ~ /^[ \t]*$/) { print ""; next }
      exit
    }
  ' "$workflow" >"$destination"
}

extract_definition() {
  local name="$1" destination="$2"
  awk -v step="      - name: $name" '
    $0 == step { capture = 1 }
    capture {
      if ($0 != step && $0 ~ /^      - name:/) { exit }
      print
    }
  ' "$workflow" >"$destination"
}

validate="$tmp/validate.sh"
verify="$tmp/verify.sh"
extract_step "Validate the immutable policy ref" "$validate"
extract_step "Verify the policy ref was reviewed and merged" "$verify"
extract_definition "Validate the immutable policy ref" "$tmp/validate-step.yml"
extract_definition "Check out the central hygiene policy" "$tmp/checkout-step.yml"

if ! grep -qF '^[0123456789abcdef]{40}$' "$validate"; then
  echo "FAIL - could not extract an immutable full-SHA guard from $workflow"
  exit 1
fi
if ! grep -qF 'compare/main...$resolved' "$verify"; then
  echo "FAIL - could not extract the merged-ref guard from $workflow"
  exit 1
fi
if ! grep -qF 'HYGIENE_REF: ${{ inputs.hygiene_ref }}' "$tmp/validate-step.yml" \
  || ! grep -qF 'repository: Verjson/.github' "$tmp/checkout-step.yml" \
  || ! grep -qF 'ref: ${{ inputs.hygiene_ref }}' "$tmp/checkout-step.yml"; then
  echo "FAIL - hygiene_ref is not bound through validation and central checkout"
  exit 1
fi

run_validate() {
  HYGIENE_REF="$1" bash -eo pipefail "$validate" >"$tmp/validate.out" 2>&1
}

run_validate main
rc=$?
[ "$rc" -eq 2 ] \
  && pass "rejects the mutable main branch before checkout" \
  || fail "accepted mutable branch main (rc=$rc)"

run_validate v2
rc=$?
[ "$rc" -eq 2 ] \
  && pass "rejects a mutable release tag before checkout" \
  || fail "accepted mutable tag v2 (rc=$rc)"

run_validate 0123456
rc=$?
[ "$rc" -eq 2 ] \
  && pass "rejects an abbreviated commit SHA before checkout" \
  || fail "accepted an abbreviated commit SHA (rc=$rc)"

merged_sha=0123456789abcdef0123456789abcdef01234567
run_validate "$merged_sha"
rc=$?
[ "$rc" -eq 0 ] \
  && pass "accepts a full lowercase commit SHA for reachability verification" \
  || fail "rejected a full commit SHA before reachability verification (rc=$rc)"

mkdir -p "$tmp/bin"
cat >"$tmp/bin/git" <<'SH'
#!/usr/bin/env bash
[ "$*" = "-C .repo-hygiene rev-parse HEAD" ] || exit 99
printf '%s\n' "$RESOLVED_SHA"
SH
cat >"$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
[[ "$*" == *"repos/Verjson/.github/compare/main...$RESOLVED_SHA"* ]] || exit 99
printf '%s\n' "$COMPARE_STATUS"
SH
chmod +x "$tmp/bin/git" "$tmp/bin/gh"

run_verify() {
  PATH="$tmp/bin:$PATH" HYGIENE_REF="$merged_sha" RESOLVED_SHA="$merged_sha" \
    COMPARE_STATUS="$1" bash -eo pipefail "$verify" >"$tmp/verify.out" 2>&1
}

run_verify ahead
rc=$?
[ "$rc" -eq 2 ] \
  && pass "rejects an unmerged full SHA" \
  || fail "accepted an unmerged full SHA (rc=$rc)"

run_verify behind
rc=$?
[ "$rc" -eq 0 ] \
  && pass "accepts a merged full SHA" \
  || fail "rejected a merged full SHA (rc=$rc)"

validate_line="$(grep -nF 'name: Validate the immutable policy ref' "$workflow" | cut -d: -f1)"
checkout_line="$(awk '/uses: actions\/checkout@/ { print NR; exit }' "$workflow")"
if [ -n "$validate_line" ] && [ -n "$checkout_line" ] && [ "$validate_line" -lt "$checkout_line" ]; then
  pass "validates the policy ref before any checkout"
else
  fail "policy ref validation does not precede checkout"
fi

[ "$fails" -eq 0 ] && { echo "All tests passed."; exit 0; }
echo "$fails test(s) failed."
exit 1
