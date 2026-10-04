#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
helper="$root/scripts/ci-gate/terminal-merge.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"

head_sha=1111111111111111111111111111111111111111
base_sha=2222222222222222222222222222222222222222
cat >"$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$CALLS"
if [ "$1" = api ] && [[ "$2" == */pulls/* ]]; then
  printf '{"state":"%s","draft":%s,"title":%s,"labels":%s,"head":{"sha":"%s"},"base":{"ref":"%s","sha":"%s"}}\n' \
    "${PR_STATE:-open}" "${PR_DRAFT:-false}" "${PR_TITLE:-\"change\"}" \
    "${PR_LABELS:-[]}" "${ACTUAL_HEAD:-$EXPECTED_HEAD_SHA}" \
    "${ACTUAL_BRANCH:-$DEFAULT_BRANCH}" "${ACTUAL_PR_BASE:-$AUTHORIZED_BASE_SHA}"
elif [ "$1" = api ] && [[ "$2" == */git/ref/heads/* ]]; then
  printf '%s\n' "${ACTUAL_REF_BASE:-$AUTHORIZED_BASE_SHA}"
elif [ "$1 $2 $3" = "pr merge $PR_NUMBER" ]; then
  exit "${MERGE_EXIT:-0}"
else
  exit 90
fi
SH
chmod +x "$tmp/bin/gh"

run_case() {
  : >"$tmp/calls"
  PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" GH_TOKEN=app-token \
    TARGET_REPO=Verjson/example PR_NUMBER=7 EXPECTED_HEAD_SHA="$head_sha" \
    AUTHORIZED_BASE_SHA="$base_sha" DEFAULT_BRANCH=main "$helper"
}

run_case
grep -q 'api repos/Verjson/example/pulls/7' "$tmp/calls"
grep -q 'api repos/Verjson/example/git/ref/heads/main' "$tmp/calls"
grep -q "pr merge 7 --repo Verjson/example --admin --squash --match-head-commit $head_sha" "$tmp/calls"
echo 'ok - exact authorized base reaches the head-CAS merge'

for mutation in \
  'ACTUAL_HEAD=3333333333333333333333333333333333333333' \
  'ACTUAL_PR_BASE=3333333333333333333333333333333333333333' \
  'ACTUAL_REF_BASE=3333333333333333333333333333333333333333' \
  'ACTUAL_REF_BASE=malformed' \
  'ACTUAL_BRANCH=release' \
  'PR_DRAFT=true' \
  'PR_LABELS=[{"name":"hold"}]' \
  'PR_LABELS=[{"name":"Do__Not--Merge"}]' \
  'PR_TITLE="chore: DO NOT MERGE until QA"' \
  'PR_STATE=closed'; do
  : >"$tmp/calls"
  if env "$mutation" PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" GH_TOKEN=app-token \
      TARGET_REPO=Verjson/example PR_NUMBER=7 EXPECTED_HEAD_SHA="$head_sha" \
      AUTHORIZED_BASE_SHA="$base_sha" DEFAULT_BRANCH=main "$helper" >/dev/null 2>&1; then
    echo "not ok - accepted $mutation" >&2
    exit 1
  fi
  ! grep -q '^pr merge ' "$tmp/calls"
  echo "ok - rejects $mutation before merge"
done

if PATH="$tmp/bin:$PATH" CALLS="$tmp/calls" GH_TOKEN=app-token TARGET_REPO=Verjson/example \
    PR_NUMBER=7 EXPECTED_HEAD_SHA="$head_sha" AUTHORIZED_BASE_SHA=not-an-oid \
    DEFAULT_BRANCH=main "$helper" >/dev/null 2>&1; then
  echo 'not ok - accepted malformed authorized base' >&2
  exit 1
fi
echo 'ok - rejects malformed authorization before API access'
