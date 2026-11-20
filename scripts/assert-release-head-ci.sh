#!/usr/bin/env bash
# Assert named GitHub Actions check contexts succeeded on an exact commit.
#
# Usage:
#   assert-release-head-ci.sh <owner/repo> <40-hex-sha> <context> [<context>...]
#
# Requires GH_TOKEN (or GITHUB_TOKEN) with checks:read. Exits 0 only when every
# named context has a completed success check run from GitHub Actions
# (app id 15368) on that SHA. Latest numeric check-run id wins when a context
# appears more than once. See Verjson/.github ADR 0291.
set -euo pipefail

GITHUB_ACTIONS_APP_ID=15368

usage() {
  echo "usage: $0 <owner/repo> <40-hex-sha> <context> [<context>...]" >&2
  exit 2
}

[[ $# -ge 3 ]] || usage
repo=$1
sha=$2
shift 2
contexts=("$@")

[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || {
  echo "::error::repository must be owner/name, got '$repo'" >&2
  exit 2
}
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "::error::release head must be a canonical 40-hex SHA, got '$sha'" >&2
  exit 2
}
for context in "${contexts[@]}"; do
  [[ -n "$context" ]] || {
    echo "::error::required CI context names must be non-empty" >&2
    exit 2
  }
done

token=${GH_TOKEN:-${GITHUB_TOKEN:-}}
[[ -n "$token" ]] || {
  echo "::error::GH_TOKEN or GITHUB_TOKEN is required to read check runs" >&2
  exit 1
}

api=${GITHUB_API_URL:-https://api.github.com}
runs_file=$(mktemp)
page_file=$(mktemp)
trap 'rm -f -- "$runs_file" "$page_file"' EXIT
printf '%s\n' '[]' >"$runs_file"

page=1
while true; do
  code=$(curl -sS -o "$page_file" -w '%{http_code}' \
    -H "Authorization: Bearer $token" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$api/repos/$repo/commits/$sha/check-runs?per_page=100&page=$page")
  if [[ "$code" != 200 ]]; then
    echo "::error::Checks API returned HTTP $code for $repo@$sha" >&2
    exit 1
  fi
  page_count=$(python3 - "$runs_file" "$page_file" <<'PY'
import json, sys
runs_path, page_path = sys.argv[1], sys.argv[2]
page = json.load(open(page_path, encoding="utf-8"))
batch = page.get("check_runs")
if not isinstance(batch, list):
    raise SystemExit("check-runs response missing check_runs list")
existing = json.load(open(runs_path, encoding="utf-8"))
existing.extend(batch)
json.dump(existing, open(runs_path, "w", encoding="utf-8"))
print(len(batch))
PY
)
  if [[ "$page_count" -lt 100 ]]; then
    break
  fi
  page=$((page + 1))
  if [[ "$page" -gt 50 ]]; then
    echo "::error::refusing to paginate past 50 check-run pages for $repo@$sha" >&2
    exit 1
  fi
done

python3 - "$runs_file" "$GITHUB_ACTIONS_APP_ID" "${contexts[@]}" <<'PY'
import json, sys

path = sys.argv[1]
actions_app_id = int(sys.argv[2])
contexts = sys.argv[3:]
runs = json.load(open(path, encoding="utf-8"))

by_name = {}
for run in runs:
    name = run.get("name")
    if not isinstance(name, str) or not name:
        continue
    by_name.setdefault(name, []).append(run)

failures = []
for context in contexts:
    candidates = by_name.get(context, [])
    if not candidates:
        failures.append(f"{context}: no check run on this commit")
        continue
    latest = max(
        candidates,
        key=lambda run: int(run["id"]) if isinstance(run.get("id"), int) else -1,
    )
    app = latest.get("app") or {}
    app_id = app.get("id")
    status = (latest.get("status") or "").lower()
    conclusion = (latest.get("conclusion") or "").lower()
    if app_id != actions_app_id:
        failures.append(
            f"{context}: latest check run id={latest.get('id')} is from app {app_id}, not GitHub Actions ({actions_app_id})"
        )
        continue
    if status != "completed":
        failures.append(
            f"{context}: latest check run id={latest.get('id')} status is {status or 'missing'}, not completed"
        )
        continue
    if conclusion != "success":
        failures.append(
            f"{context}: latest check run id={latest.get('id')} conclusion is {conclusion or 'missing'}, not success"
        )
        continue

if failures:
    for line in failures:
        print(f"::error::required CI context failed: {line}", file=sys.stderr)
    raise SystemExit(1)

print("Required CI contexts succeeded on this commit: " + ", ".join(contexts))
PY
