#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cat >"$tmp/gh" <<'SH'
#!/usr/bin/env bash
printf 'called\n' >>"$CALLS"
exit 0
SH
chmod +x "$tmp/gh"
for base in 2222222222222222222222222222222222222222 malformed ''; do
  : >"$tmp/calls"
  if PATH="$tmp:$PATH" CALLS="$tmp/calls" GH_TOKEN=app-token \
    TARGET_REPO=Verjson/example PR_NUMBER=7 \
    EXPECTED_HEAD_SHA=1111111111111111111111111111111111111111 \
    AUTHORIZED_BASE_SHA="$base" DEFAULT_BRANCH=main \
    bash "$root/scripts/ci-gate/terminal-merge.sh" >"$tmp/out" 2>&1; then
    echo "FAIL - retired terminal helper accepted base $base" >&2
    exit 1
  fi
  test ! -s "$tmp/calls"
  grep -q 'disabled' "$tmp/out"
done
echo 'ok - retired terminal helper never contacts GitHub'
