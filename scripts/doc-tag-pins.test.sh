#!/usr/bin/env bash
# Unit tests for scripts/doc-tag-pins.sh (Verjson/.github#287).
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/.." && pwd)"
script="$repo_root/scripts/doc-tag-pins.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$script" ] || { echo "FAIL - script not found: $script"; exit 1; }

# pin <workflow> <ref> -> a caller line pinning that ref. Assembled at runtime on
# purpose: a literal `<name>.yml@vX` here would be a tracked pin like any other, and
# the self-check below scans every tracked file — fixtures must not become findings.
pin() { printf 'uses: Verjson/.github/.github/workflows/%s.yml@%s\n' "$1" "$2"; }

# fixture <name> <tag>... -> path to a committed git repo carrying $DOC (below).
fixture() {
  local name="$1"; shift
  local dir="$tmp/$name"
  mkdir -p "$dir"
  printf '%s\n' "$DOC" >"$dir/README.md"
  git init -q "$dir"
  git -C "$dir" config user.name test
  git -C "$dir" config user.email test@example.com
  git -C "$dir" add README.md
  git -C "$dir" commit -qm fixture
  local tag
  for tag in "$@"; do git -C "$dir" tag -a "$tag" -m release; done
  printf '%s\n' "$dir"
}

DOC="$(pin node-ci v2.1.0)"
existing="$(fixture existing v2.1.0)"
DOC_TAG_PINS_ROOT="$existing" bash "$script" >"$tmp/existing.out" 2>&1
rc=$?
[ "$rc" -eq 0 ] \
  && pass "example pinned to an existing tag is accepted" \
  || { fail "existing-tag pin rejected (rc=$rc)"; sed 's/^/diag - /' "$tmp/existing.out"; }

# The #287 regression: docs advertised `@v2.1.1` while tags stopped at `v2.1.0`.
DOC="$(pin node-ci v2.1.1)"
stale="$(fixture stale v2.1.0)"
DOC_TAG_PINS_ROOT="$stale" bash "$script" >"$tmp/stale.out" 2>&1
rc=$?
{ [ "$rc" -ne 0 ] && grep -q 'v2\.1\.1' "$tmp/stale.out"; } \
  && pass "example pinned to a nonexistent tag is rejected and named (#287)" \
  || { fail "nonexistent-tag pin accepted or unreported (rc=$rc)"; sed 's/^/diag - /' "$tmp/stale.out"; }

# Fail closed on a broken lookup. A checkout without tags (the CI default) makes
# every pin look nonexistent; that must read as "the lookup broke", never as a
# docs verdict — and never, on any path, as a pass.
DOC="$(pin node-ci v2.1.0)"
tagless="$(fixture tagless)"
DOC_TAG_PINS_ROOT="$tagless" bash "$script" >"$tmp/tagless.out" 2>&1
rc=$?
{ [ "$rc" -ne 0 ] && grep -qi 'no tags' "$tmp/tagless.out"; } \
  && pass "a tag lookup that yields nothing fails closed as a lookup fault" \
  || { fail "tagless checkout did not fail closed as a lookup fault (rc=$rc)"; sed 's/^/diag - /' "$tmp/tagless.out"; }

# The documented moving major alias is a real tag, so it must stay accepted.
DOC="$(pin helm-ci v2)"
moving="$(fixture moving v2 v2.1.0)"
DOC_TAG_PINS_ROOT="$moving" bash "$script" >"$tmp/moving.out" 2>&1
rc=$?
[ "$rc" -eq 0 ] \
  && pass "the moving major alias is accepted like any other tag" \
  || { fail "moving major alias rejected (rc=$rc)"; sed 's/^/diag - /' "$tmp/moving.out"; }

# `v2.1` is a prefix of `v2.1.0`, not a tag: a substring match would pass it.
DOC="$(pin helm-ci v2.1)"
prefix="$(fixture prefix v2.1.0)"
DOC_TAG_PINS_ROOT="$prefix" bash "$script" >"$tmp/prefix.out" 2>&1
rc=$?
[ "$rc" -ne 0 ] \
  && pass "a ref that is only a prefix of a real tag is rejected" \
  || { fail "prefix of an existing tag accepted as a tag (rc=$rc)"; sed 's/^/diag - /' "$tmp/prefix.out"; }

notrepo="$tmp/not-a-repo"
mkdir -p "$notrepo"
printf '%s\n' "$DOC" >"$notrepo/README.md"
DOC_TAG_PINS_ROOT="$notrepo" bash "$script" >"$tmp/notrepo.out" 2>&1
rc=$?
[ "$rc" -ne 0 ] \
  && pass "an unreadable checkout fails closed instead of finding no pins" \
  || { fail "non-repository root passed (rc=$rc)"; sed 's/^/diag - /' "$tmp/notrepo.out"; }

# `@main` and full-SHA refs name no tag, so they are out of scope — not failures.
DOC="$(pin helm-ci main)$(pin actionlint bfecdd0111582d0ddada558e6b4d0cadd9b488bd)"
unpinned="$(fixture unpinned v2.1.0)"
DOC_TAG_PINS_ROOT="$unpinned" bash "$script" >"$tmp/unpinned.out" 2>&1
rc=$?
[ "$rc" -eq 0 ] \
  && pass "@main and full-SHA refs are not read as tag pins" \
  || { fail "a non-tag ref was checked against the tag list (rc=$rc)"; sed 's/^/diag - /' "$tmp/unpinned.out"; }

# The contract itself: this repository's own examples must resolve today.
bash "$script" >"$tmp/self.out" 2>&1
rc=$?
[ "$rc" -eq 0 ] \
  && pass "every tag pin documented in this repository resolves (#287)" \
  || { fail "this repository documents a nonexistent tag (rc=$rc)"; sed 's/^/diag - /' "$tmp/self.out"; }

# The lookup above is only as good as the checkout that feeds it. Bounding
# actions-ci's history to depth 1 (#234) also drops tags unless they are asked
# for explicitly, which would turn every documented pin into a lookup fault.
actions_ci="$repo_root/.github/workflows/actions-ci.yml"
# The pin lookup runs in docs-contracts. An earlier checkout, such as
# changed-path classification, does not feed it and must not satisfy this.
checkout_with="$(awk '
  /^  docs-contracts:/ { in_job = 1 }
  in_job && /uses: actions\/checkout@/ { in_checkout = 1; next }
  in_checkout && /^[[:space:]]*-[[:space:]]/ { exit }
  in_checkout { print }
' "$actions_ci")"
{ printf '%s\n' "$checkout_with" | grep -E '^[[:space:]]*fetch-tags:[[:space:]]*true[[:space:]]*$' >/dev/null \
  || printf '%s\n' "$checkout_with" | grep -E '^[[:space:]]*fetch-depth:[[:space:]]*0[[:space:]]*$' >/dev/null; } \
  && pass "actions-ci checks out the tags this lookup reads (#234)" \
  || fail "actions-ci checks out without tags, so every documented pin would fail closed"

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
