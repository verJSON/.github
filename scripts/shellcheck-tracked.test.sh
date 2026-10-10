#!/usr/bin/env bash
set -uo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
runner="$root/scripts/shellcheck-tracked.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

run_full_tree_shellcheck() {
  ACTIONS_CI_SHELLCHECK_BASE_SHA='' \
    ACTIONS_CI_SHELLCHECK_HEAD_SHA='' \
    bash "$runner"
}

repo="$tmp/repo"
git init -q "$repo"
mkdir -p "$repo/scripts"

cat >"$repo/scripts/clean.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' clean
SH

cat >"$repo/scripts/name with spaces.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' 'space-safe'
SH

cat >"$repo/scripts/untracked-warning.sh" <<'SH'
#!/usr/bin/env bash
cd "$1"
printf '%s\n' done
SH

git -C "$repo" add -- scripts/clean.sh 'scripts/name with spaces.sh'

if (cd "$repo" && run_full_tree_shellcheck); then
  pass "tracked clean scripts pass while an untracked warning is ignored"
else
  fail "tracked clean scripts failed or the untracked fixture was linted"
fi

git -C "$repo" add -- scripts/untracked-warning.sh
if output="$(cd "$repo" && run_full_tree_shellcheck 2>&1)"; then
  fail "tracked standalone warning passed ShellCheck"
elif grep -qF 'scripts/untracked-warning.sh' <<<"$output" \
  && grep -qF 'SC2164 (warning)' <<<"$output"; then
  pass "tracked standalone warning fails with its ShellCheck diagnostic"
else
  printf '%s\n' "$output" >&2
  fail "tracked standalone warning failed without the expected diagnostic"
fi

empty_repo="$tmp/empty-repo"
git init -q "$empty_repo"
if (cd "$empty_repo" && run_full_tree_shellcheck); then
  pass "a repository without tracked shell scripts is a successful no-op"
else
  fail "empty tracked shell set failed"
fi

diff_repo="$tmp/diff-repo"
git init -q "$diff_repo"
mkdir -p "$diff_repo/scripts"
cat >"$diff_repo/scripts/clean.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' clean
SH
cat >"$diff_repo/scripts/name with spaces.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' clean
SH
cat >"$diff_repo/scripts/unchanged-warning.sh" <<'SH'
#!/usr/bin/env bash
cd "$1"
SH
git -C "$diff_repo" add -- scripts
git -C "$diff_repo" -c user.name=test -c user.email=test@example.invalid \
  commit -qm 'base scripts'
base_sha="$(git -C "$diff_repo" rev-parse HEAD)"
cat >"$diff_repo/scripts/name with spaces.sh" <<'SH'
#!/usr/bin/env bash
cd "$1"
SH
cat >"$diff_repo/scripts/untracked-warning.sh" <<'SH'
#!/usr/bin/env bash
cd "$1"
SH
git -C "$diff_repo" add -- 'scripts/name with spaces.sh'
git -C "$diff_repo" -c user.name=test -c user.email=test@example.invalid \
  commit -qm 'change a space-named script'
head_sha="$(git -C "$diff_repo" rev-parse HEAD)"

if output="$(cd "$diff_repo" && \
  ACTIONS_CI_SHELLCHECK_BASE_SHA="$base_sha" \
  ACTIONS_CI_SHELLCHECK_HEAD_SHA="$head_sha" bash "$runner" 2>&1)"; then
  fail "the pull-request diff did not lint a changed warning"
elif grep -qF 'scripts/name with spaces.sh' <<<"$output" \
  && grep -qF 'SC2164 (warning)' <<<"$output" \
  && ! grep -qF 'unchanged-warning.sh' <<<"$output" \
  && ! grep -qF 'untracked-warning.sh' <<<"$output"; then
  pass "pull requests lint changed tracked scripts with space-safe paths"
else
  printf '%s\n' "$output" >&2
  fail "pull-request ShellCheck scope was not the exact tracked diff"
fi

if output="$(cd "$diff_repo" && run_full_tree_shellcheck 2>&1)"; then
  fail "the full-tree push audit ignored a tracked warning"
elif grep -qF 'unchanged-warning.sh' <<<"$output" \
  && grep -qF 'scripts/name with spaces.sh' <<<"$output" \
  && ! grep -qF 'untracked-warning.sh' <<<"$output"; then
  pass "pushes keep the full tracked-tree audit"
else
  printf '%s\n' "$output" >&2
  fail "the full-tree push scan was not fail-closed"
fi

exit "$fails"
