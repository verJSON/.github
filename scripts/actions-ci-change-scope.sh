#!/usr/bin/env bash
# Classify a diff for actions-ci. Documentation surfaces do not schedule the
# heavy matrix (#1734). A path is documentation only when it is under docs/,
# NEXT/, or CHANGELOG/, or is Markdown, and it is not under scripts/ or
# .github/ and is not a shell script. Empty input fails closed to heavy=true.
set -euo pipefail

heavy=false
seen=0
while IFS= read -r path || [ -n "$path" ]; do
  [ -n "$path" ] || continue
  seen=1
  case "$path" in
    scripts/* | .github/* | *.sh)
      heavy=true
      ;;
    docs/* | NEXT/* | CHANGELOG/* | *.md)
      ;;
    *)
      heavy=true
      ;;
  esac
done

if [ "$seen" -eq 0 ] || [ "$heavy" = true ]; then
  printf 'heavy=true\n'
else
  printf 'heavy=false\n'
fi
