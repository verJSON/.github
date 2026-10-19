#!/usr/bin/env bash
# Pins the lockfile-elision logic in ai-review-merge.yml's "Prepare bounded
# review context" step (Verjson/.github#110). A generated lockfile in the diff
# (e.g. an 8k-line package-lock) once consumed the whole AI review budget and
# forced a no-verdict, fail-closed run on an otherwise-small PR. The gate now
# filters lockfile hunks out of the payload it hands the model while keeping the
# full diff on disk. This extracts the real filter (LOCK_RE + the two awk
# programs) from the workflow — single source of truth, no drift — and drives it
# against synthetic diffs. Plain bash + awk.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
wf="$repo_root/.github/workflows/ai-review-merge.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$wf" ] || { echo "FAIL - workflow not found: $wf"; exit 1; }

# Dedent the step's run: block, then slice out just the elision fragment
# (LOCK_RE assignment through the elided_lockfiles awk). Running that real slice
# against fixtures is what keeps the test honest — a workflow edit that breaks
# the filter breaks this test.
block="$tmp/block.sh"
awk '
  $0 == "      - name: Prepare bounded review context" { seen = 1 }
  seen && $0 == "        run: |" { cap = 1; next }
  cap {
    if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
    if ($0 ~ /^[ \t]*$/) { print ""; next }
    cap = 0
  }
' "$wf" > "$block"

slice="$tmp/elide.sh"
awk '/^LOCK_RE=/ { c = 1 } c { print } /full\.diff"?\)"/ { if (c) exit }' "$block" > "$slice"
grep -q '^LOCK_RE=' "$slice" || { echo "FAIL - could not extract LOCK_RE (filter moved/renamed?) from $wf"; exit 1; }
grep -q 'elided_lockfiles=' "$slice" || { echo "FAIL - could not extract elided_lockfiles awk from $wf"; exit 1; }

# run_filter <fixture-file>: run the real slice on a synthetic full diff.
# Writes .ai-review/pr.diff and exports elided_lockfiles for assertions.
run_filter() {
  rm -rf "$tmp/work"; mkdir -p "$tmp/work/.ai-review"
  cp "$1" "$tmp/work/.ai-review/pr.full.diff"
  # shellcheck disable=SC1090,SC2154 # The generated fixture defines this value.
  (
    cd "$tmp/work" && review_dir="$tmp/work/.ai-review" && source "$slice" &&
      printf '%s' "$elided_lockfiles" >elided.out
  )
  filtered="$tmp/work/.ai-review/pr.diff"
  elided="$(cat "$tmp/work/elided.out")"
}
kept()    { grep -qF "$1" "$filtered"; }

# --- Fixture: source + package-lock + package.json ---
cat > "$tmp/mixed.diff" <<'DIFF'
diff --git a/src/app.ts b/src/app.ts
index 111..222 100644
--- a/src/app.ts
+++ b/src/app.ts
@@ -1 +1 @@
-const a = 1
+const a = 2
diff --git a/package.json b/package.json
index 333..444 100644
--- a/package.json
+++ b/package.json
@@ -1 +1 @@
-  "dep": "1.0.0"
+  "dep": "2.0.0"
diff --git a/package-lock.json b/package-lock.json
index 555..666 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1 +1 @@
-      "version": "1.0.0"
+      "version": "2.0.0"
DIFF

run_filter "$tmp/mixed.diff"
kept 'diff --git a/src/app.ts'  && pass "keeps source file"        || fail "source file must survive filtering"
kept 'diff --git a/package.json b/package.json' \
  && pass "keeps package.json manifest" || fail "manifest (package.json) must survive"
! kept 'package-lock.json' \
  && pass "drops package-lock.json section entirely" || fail "package-lock.json must be dropped"
! grep -q '"version": "2.0.0"' "$filtered" \
  && pass "drops lockfile hunk body" || fail "lockfile hunk body leaked into review diff"
[ "$elided" = "package-lock.json" ] \
  && pass "reports the elided lockfile" || fail "elided list wrong: '$elided'"

# --- Fixture: multiple lock ecosystems, nested paths ---
cat > "$tmp/multi.diff" <<'DIFF'
diff --git a/svc/pnpm-lock.yaml b/svc/pnpm-lock.yaml
index a..b 100644
--- a/svc/pnpm-lock.yaml
+++ b/svc/pnpm-lock.yaml
@@ -1 +1 @@
-x
+y
diff --git a/Cargo.lock b/Cargo.lock
index a..b 100644
--- a/Cargo.lock
+++ b/Cargo.lock
@@ -1 +1 @@
-x
+y
diff --git a/go.sum b/go.sum
index a..b 100644
--- a/go.sum
+++ b/go.sum
@@ -1 +1 @@
-x
+y
diff --git a/src/main.rs b/src/main.rs
index a..b 100644
--- a/src/main.rs
+++ b/src/main.rs
@@ -1 +1 @@
-fn main() {}
+fn main() { work() }
DIFF

run_filter "$tmp/multi.diff"
kept 'diff --git a/src/main.rs' && pass "keeps source amid many lockfiles" || fail "source dropped among lockfiles"
{ ! kept 'pnpm-lock.yaml' && ! kept 'Cargo.lock' && ! kept 'go.sum'; } \
  && pass "drops nested + multi-ecosystem lockfiles" || fail "a lockfile leaked (pnpm/cargo/go)"
[ "$elided" = "svc/pnpm-lock.yaml, Cargo.lock, go.sum" ] \
  && pass "reports all elided lockfiles in order" || fail "elided list wrong: '$elided'"

# --- Guard: Cargo.toml is a manifest, NOT a lockfile — must be kept ---
cat > "$tmp/manifest.diff" <<'DIFF'
diff --git a/Cargo.toml b/Cargo.toml
index a..b 100644
--- a/Cargo.toml
+++ b/Cargo.toml
@@ -1 +1 @@
-version = "1"
+version = "2"
DIFF
run_filter "$tmp/manifest.diff"
kept 'Cargo.toml' && [ -z "$elided" ] \
  && pass "Cargo.toml manifest is not elided" || fail "Cargo.toml must not be treated as a lockfile"

# --- Guard: a lockfile-only diff yields an empty review diff + full elided list ---
cat > "$tmp/lockonly.diff" <<'DIFF'
diff --git a/yarn.lock b/yarn.lock
index a..b 100644
--- a/yarn.lock
+++ b/yarn.lock
@@ -1 +1 @@
-x
+y
DIFF
run_filter "$tmp/lockonly.diff"
[ ! -s "$filtered" ] && [ "$elided" = "yarn.lock" ] \
  && pass "lockfile-only diff filters to empty payload" || fail "lockfile-only diff not handled"

if [ "$fails" -eq 0 ]; then echo "All tests passed."; exit 0; else echo "$fails test(s) failed."; exit 1; fi
