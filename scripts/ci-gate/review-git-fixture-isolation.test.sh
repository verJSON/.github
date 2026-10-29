#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/hooks"
cat > "$tmp/hooks/post-commit" <<'HOOK'
#!/bin/sh
printf 'unexpected hook\n' >> "$HOOK_MARKER"
HOOK
chmod +x "$tmp/hooks/post-commit"
printf '[core]\n\thooksPath = %s/hooks\n' "$tmp" > "$tmp/gitconfig"
git init -q "$tmp/control"
HOOK_MARKER="$tmp/hook-called" GIT_CONFIG_GLOBAL="$tmp/gitconfig" \
  git -C "$tmp/control" -c user.name=Test -c user.email=test@example.invalid \
  commit --allow-empty -qm control
test -s "$tmp/hook-called" || { echo 'control hook did not execute' >&2; exit 1; }
rm "$tmp/hook-called"
for test in review-context.test.sh review-verdict.test.py; do
  case "$test" in
    *.sh) command=(bash "$root/scripts/ci-gate/$test") ;;
    *.py) command=(python3 "$root/scripts/ci-gate/$test") ;;
  esac
  HOOK_MARKER="$tmp/hook-called" GIT_CONFIG_GLOBAL="$tmp/gitconfig" \
    "${command[@]}" >"$tmp/$test.log" 2>&1 || { cat "$tmp/$test.log" >&2; exit 1; }
  test ! -e "$tmp/hook-called" || { echo "$test ran the inherited hook" >&2; exit 1; }
done
echo 'ok - review fixture repositories ignore inherited Git hooks'
