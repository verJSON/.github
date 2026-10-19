#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

recipe_for() {
  awk -v target="$1:" '
    $0 == target { found = 1; next }
    found && /^\t/ { sub(/^\t/, ""); print; exit }
    found { exit }
  ' "$root/Makefile"
}

if [ "$(recipe_for lint)" = '$(ACTIONLINT) -config-file .github/actionlint.yaml -shellcheck=shellcheck -color' ]; then
  pass "lint delegates to the repository actionlint policy"
else
  fail "lint did not preserve the actionlint command"
fi

if [ "$(recipe_for test)" = '$(BASH) scripts/repo-hygiene.test.sh' ]; then
  pass "test delegates to the documented behavioral smoke test"
else
  fail "test did not preserve the documented test command"
fi

if [ "$(recipe_for render)" = '$(BASH) scripts/render-next.sh' ]; then
  pass "render delegates to the canonical NEXT renderer"
else
  fail "render did not preserve the canonical renderer command"
fi

if [ "$(recipe_for adr-index)" = '$(BASH) scripts/gen-adr-index.sh --check' ]; then
  pass "adr-index checks the generated index without rewriting it"
else
  fail "adr-index did not preserve the generated-index check"
fi

actions_ci="$root/scripts/actions-ci-groups.tsv"
if grep -q $'\tbash scripts/gen-adr-index.sh --check$' "$actions_ci"; then
  pass "actions-ci consumes the portable ADR-index command without requiring make"
else
  fail "actions-ci bypasses the portable ADR-index target"
fi
# The row wraps the renderer so an emptied post-release NEXT/ passes; it must
# still execute the same portable script the `render` target runs. Run the row
# against a stub renderer that records its execution, so a reference that only
# appears in a comment cannot satisfy the check.
render_fixture="$tmp/render-row"
mkdir -p "$render_fixture/scripts" "$render_fixture/NEXT"
cp "$root/scripts/render-next-check.sh" "$render_fixture/scripts/"
printf '%s\n' '#!/usr/bin/env bash' ': >"$(dirname "$0")/../render-next.ran"' \
  > "$render_fixture/scripts/render-next.sh"
if grep -q $'\tbash scripts/render-next-check.sh$' "$actions_ci" \
  && (cd "$render_fixture" && bash -o pipefail -c 'bash scripts/render-next-check.sh') >/dev/null 2>&1 \
  && [ -e "$render_fixture/render-next.ran" ]; then
  pass "actions-ci consumes the portable render command without requiring make"
else
  fail "actions-ci bypasses the portable render target"
fi

for control_plane in ai-review-merge.yml ai-privileged-merge.yml; do
  if grep -qE '^[[:space:]]+run: make([[:space:]]|$)' "$root/.github/workflows/$control_plane"; then
    fail "$control_plane was pulled into the portability layer"
  else
    pass "$control_plane remains outside the portability layer"
  fi
done

if awk -F '\t' '$2 ~ /^make([[:space:]]|$)/ { found = 1 } END { exit !found }' "$actions_ci"; then
  fail "actions-ci still requires an undeclared make binary"
else
  pass "actions-ci manifest runs without make on minimal routed runners"
fi

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi

echo "$fails test(s) failed."
exit 1
