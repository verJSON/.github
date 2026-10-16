#!/usr/bin/env bash
# Exercises the token authority of the reusable changelog release workflow
# (Verjson/.github#295, Verjson/.github#784).
#
# The release job once pushed with GITHUB_TOKEN-derived credentials, so it had
# to grant that token contents-write (#295). The dedicated release App now owns
# the push credential (#329); the job token only reads the canonical contract.
# The assertions below keep both workflow and generated caller GITHUB_TOKENs
# read-only while the short-lived App token alone receives contents-write.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
workflow="$root/.github/workflows/changelog-release.yml"
fails=0

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

# The release job's header: everything from `  release:` up to its `steps:`.
# Scoping matters — a file-wide read check would not prove that this job did not
# override the workflow default with write authority.
job_header="$(awk '
  /^  release:/ { inside = 1; next }
  inside && /^    steps:/ { exit }
  inside && /^  [^ ]/ { exit }
  inside { print }
' "$workflow")"

grep -qE '^      contents: read$' <<<"$job_header" \
  && pass "the release job keeps GITHUB_TOKEN contents-read-only" \
  || fail "the release job grants GITHUB_TOKEN more than contents-read"

grep -qE '^    permissions:$' <<<"$job_header" \
  && pass "the release job declares its read-only token boundary" \
  || fail "the release job declares no permissions block of its own"

# Least privilege is the reason the explicit workflow and job boundaries exist:
# anything this workflow gains later must ask for it explicitly.
workflow_default="$(awk '
  /^permissions:/ { inside = 1; next }
  inside && /^[^ ]/ { exit }
  inside { print }
' "$workflow")"

[ "$(grep -c . <<<"$workflow_default")" -eq 2 ] && grep -qE '^  contents: read$' <<<"$workflow_default" && grep -qE '^  actions: read$' <<<"$workflow_default" \
  && pass "the workflow-level default grants only contents and environment-policy reads" \
  || fail "the workflow-level default differs from contents/actions read"

# The read-only boundary is safe because the push uses an independent App token.
# If this wiring changes, the token grants need re-justifying rather than
# inheriting.
python3 - "$workflow" <<'PY'
import sys

import yaml

path = sys.argv[1]
doc = yaml.safe_load(open(path, encoding="utf-8"))
call = doc.get("on", doc.get(True))["workflow_call"]
inputs = call.get("inputs") or {}
secrets = call.get("secrets") or {}
release = doc["jobs"]["release"]
steps = release.get("steps") or []
mint = next((step for step in steps if step.get("id") == "release-app-token"), {})
checkout = next(
    (step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")),
    {},
)
problems = []
if not (inputs.get("release_app_client_id") or {}).get("required"):
    problems.append("workflow_call does not require release_app_client_id")
if secrets or not (inputs.get("release_environment") or {}).get("required"):
    problems.append("release must require an environment instead of forwarded App keys")
if mint.get("uses") != "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1":
    problems.append("release token action is not at the audited immutable pin")
expected = {
    "client-id": "${{ inputs.release_app_client_id }}",
    "private-key": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
    "owner": "${{ github.repository_owner }}",
    "repositories": "${{ github.event.repository.name }}",
    "permission-contents": "write",
}
if mint.get("with") != expected:
    problems.append("release token is not constrained to owner, current repository, and contents-write")
if (checkout.get("with") or {}).get("token") != "${{ steps.release-app-token.outputs.token }}":
    problems.append("release checkout does not persist the short-lived App token")
raw = open(path, encoding="utf-8").read()
if "secrets.push_token" in raw or "ORG_ADMIN_TOKEN" in raw:
    problems.append("release workflow retains the temporary broad push credential")
if problems:
    raise SystemExit("\n".join(problems))
PY
if [ "$?" -eq 0 ]; then
  pass "the release push uses only a current-repository contents-write App token"
else
  fail "the release App token contract is not least-privilege"
fi

client_id_guard="$(awk '
  /^      - name: Require the release App client ID$/ { found = 1; next }
  found && /^      - name:/ { exit }
  found { print }
' "$workflow")"
grep -qF '[[ -z "$RELEASE_APP_CLIENT_ID" || "$RELEASE_APP_CLIENT_ID" =~ ^[0123456789]+$ ]]' <<<"$client_id_guard" \
  && pass "the release workflow rejects only empty and numeric legacy App IDs locally" \
  || fail "the release workflow does not defer full client-ID validation to the pinned action"

client_id_run="$(awk '
  /^      - name: Require the release App client ID$/ { found = 1; next }
  found && /^        run: \|$/ { run = 1; next }
  run && /^      - name:/ { exit }
  run { sub(/^          /, ""); print }
' "$workflow")"
for valid_client_id in Iv23liIrniWY27YJKYDP Iv1.0123456789abcdef; do
  if RELEASE_APP_CLIENT_ID="$valid_client_id" bash -c "$client_id_run" >/dev/null; then
    pass "the release workflow delegates supported client ID '$valid_client_id' to the pinned action"
  else
    fail "the release workflow rejects supported client ID '$valid_client_id'"
  fi
done
for invalid_client_id in '' 4583107; do
  if RELEASE_APP_CLIENT_ID="$invalid_client_id" bash -c "$client_id_run" >/dev/null 2>&1; then
    fail "the release workflow accepts an empty or numeric legacy App ID"
  else
    pass "the release workflow rejects legacy App ID '${invalid_client_id:-<empty>}'"
  fi
done

grep -qF 'git push --atomic origin \' "$workflow" \
  && grep -qF '"$release_commit:refs/heads/$DEFAULT_BRANCH" \' "$workflow" \
  && grep -qF '"refs/tags/$VERSION"' "$workflow" \
  && pass "the release still publishes commit and exact tag atomically" \
  || fail "the atomic commit+tag push shape changed"

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
