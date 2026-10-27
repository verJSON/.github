#!/usr/bin/env bash
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
script="$here/runner-admission-reconcile.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

# The stub ignores `--jq` and returns already-shaped fixtures, so it cannot
# verify that the script's jq filters frame their output the way the callers
# assume (raw one-string-per-line for `.repositories[].full_name`, one JSON
# object per line for `.runners[] | {...}`). That framing is load-bearing for
# slurp_strings/slurp_objects. It is inherent to stubbing rather than a new
# risk — the pre-existing repos fetch relies on the same guarantee — but a
# change to those filters will not be caught here.
mkdir -p "$tmp/bin"
cat >"$tmp/bin/gh" <<'GH'
#!/usr/bin/env bash
path=""
for arg in "$@"; do
  case "$arg" in /orgs/*) path="$arg";; esac
done
if [ -n "${GH_API_LOG:-}" ]; then
  printf '%s\n' "$path" >> "$GH_API_LOG"
fi
if [ -n "${FAIL_PATH:-}" ] && [[ "$path" == *"$FAIL_PATH"* ]]; then
  echo "HTTP 403: Resource not accessible" >&2
  exit 1
fi
if [ "${HOSTED_RUNNERS_404:-0}" = "1" ] && [[ "$path" == */actions/hosted-runners* ]]; then
  echo '{"message":"Not Found","status":"404"}' >&2
  echo "gh: Not Found (HTTP 404)" >&2
  exit 1
fi
# A deleted group is gone from BOTH surfaces: the id 404s and it is absent from
# the listing. Modelling only the 404 would let a test pass for the wrong reason.
deleted() {
  case " ${DELETED_GROUPS:-} " in *" $1 "*) return 0;; *) return 1;; esac
}
for id in 4 6; do
  if deleted "$id" && [[ "$path" == */runner-groups/"$id" || "$path" == */runner-groups/"$id"/* ]]; then
    echo '{"message":"Not Found","status":"404"}' >&2
    echo "gh: Not Found (HTTP 404)" >&2
    exit 1
  fi
done
case "$path" in
  */actions/hosted-runners*) printf '%s\n' "$HOSTED_RUNNERS_PAGES" ;;
  # The LANE variables are what every `runs-on:` resolves, so they are what the
  # reconciler reads. The RUNNER pair is still served because it is still set
  # org-wide for consumers pinned to a pre-migration SHA, and divergence between
  # the two is itself reported as drift.
  */actions/variables/CI_LANE_TRUSTED) printf '%s\n' "$DEFAULT_VAR" ;;
  */actions/variables/CI_LANE_UNTRUSTED) printf '%s\n' "$UNTRUSTED_VAR" ;;
  */actions/variables/CI_RUNNER_UNTRUSTED) printf '%s\n' "${CI_RUNNER_UNTRUSTED_VAR-$UNTRUSTED_VAR}" ;;
  */actions/variables/VERJSON_LANE_UNTRUSTED) printf '%s\n' "${VERJSON_LANE_UNTRUSTED_VAR-$UNTRUSTED_VAR}" ;;
  */actions/variables/CI_LANE_PRIVILEGED) printf '%s\n' "$PRIVILEGED_VAR" ;;
  */actions/variables/CI_LANE_FALLBACK) printf '%s\n' "$FALLBACK_VAR" ;;
  */actions/variables/VERJSON_RUNNER_DEFAULT) printf '%s\n' "$LEGACY_DEFAULT_VAR" ;;
  */actions/variables/VERJSON_RUNNER_UNTRUSTED) printf '%s\n' "${VERJSON_RUNNER_UNTRUSTED_VAR-$UNTRUSTED_VAR}" ;;
  */actions/variables/CI_RUNNER_ISOLATED) printf '%s\n' "${CI_RUNNER_ISOLATED_VAR-$UNTRUSTED_VAR}" ;;
  */actions/variables/VERJSON_RUNNER_ISOLATED) printf '%s\n' "${VERJSON_RUNNER_ISOLATED_VAR-$UNTRUSTED_VAR}" ;;
  # Both ids serve the default group's runners, so a test can move the default
  # group off id 1 and prove resolution follows `.default`, not a pinned id.
  */runner-groups/1/runners*|*/runner-groups/9/runners*) printf '%s\n' "$G1_RUNNERS" ;;
  */runner-groups/4/repositories*) printf '%s\n' "$G4_MEMBERS" ;;
  */runner-groups/6/repositories*) printf '%s\n' "$G6_MEMBERS" ;;
  */runner-groups/4/runners*) printf '%s\n' "$G4_RUNNERS" ;;
  */runner-groups/6/runners*) printf '%s\n' "$G6_RUNNERS" ;;
  */runner-groups/4) printf '%s\n' "$G4_GROUP" ;;
  */runner-groups/6) printf '%s\n' "$G6_GROUP" ;;
  # The group listing, which is how the reconciler resolves a name to an id.
  # '?' is a glob wildcard that also matches '/', so the query separator is
  # escaped: an unescaped '*/actions/runner-groups?*' would swallow EVERY
  # unknown '/runner-groups/<id>/...' path and answer it with the listing,
  # instead of letting it fall through to the guard below. A stub that answers
  # the wrong question is worse than one that errors.
  */actions/runner-groups|*/actions/runner-groups\?*)
    [ -z "$G1_GROUP" ] || printf '%s\n' "$G1_GROUP"
    deleted 4 || printf '%s\n' "$G4_GROUP"
    deleted 6 || printf '%s\n' "$G6_GROUP"
    ;;
  */orgs/*/repos*) printf '%s\n' "$REPOS_FIXTURE" ;;
  *) echo "unexpected path: $path" >&2; exit 1 ;;
esac
GH
chmod +x "$tmp/bin/gh"
export PATH="$tmp/bin:$PATH"
export GH_API_LOG="$tmp/gh-api.log"

export DEFAULT_VAR='{"value":"[\"self-hosted\",\"general\"]","visibility":"all"}'
# Unset by default: the lane variables above resolve, so the fallback term is
# never reached. A case that empties one of them exercises the fall-through.
export FALLBACK_VAR=''
# The retired pair, still set org-wide during the migration. Kept equal to the
# lanes here so the divergence case below is the only one that reports it.
export LEGACY_DEFAULT_VAR='{"value":"[\"self-hosted\",\"general\"]","visibility":"all"}'
export UNTRUSTED_VAR='{"value":"[\"ubuntu-24.04\"]","visibility":"all"}'
export PRIVILEGED_VAR='{"value":"[\"self-hosted\",\"general\"]","visibility":"all"}'
# GitHub's own default group. A custom group cannot be made default (ADR 0003),
# so this one is always present in a real org — which is why its ABSENCE below
# has to be an undetermined result rather than a clean one.
export G1_GROUP='{"id":1,"name":"GitHub","visibility":"all","allows_public_repositories":true,"default":true}'
export G1_RUNNERS=''
export G4_GROUP='{"id":4,"name":"DigitalOcean","visibility":"all","allows_public_repositories":true,"default":false}'
export G6_GROUP='{"id":6,"name":"isolated","visibility":"selected","allows_public_repositories":true}'
# Member/runner fixtures are what `gh api --paginate --jq` emits: one element
# per line, concatenated across pages. Multiple lines therefore ARE the
# multi-page case, which is the shape the collector has to survive.
export G4_MEMBERS=''
export G6_MEMBERS=''
export G4_RUNNERS='{"name":"general-1","status":"online","labels":["self-hosted","Linux","X64","general"]}'
export G6_RUNNERS=''
export REPOS_FIXTURE=$'Verjson/private-lib\ttrue\nVerjson/public-app\tfalse'
export FAIL_PATH=''
export DELETED_GROUPS=''
export HOSTED_RUNNERS_PAGES='{"total_count":0,"runners":[]}'
export HOSTED_RUNNERS_404='0'
# Supplied the way the workflow supplies it, because the script no longer ships a
# default for this one: the name it used to default to (`isolated`) has named a
# deleted group since 2026-07-31. The general lane keeps a default and the
# fixtures use it, so a stale default there fails this suite; the untrusted lane
# has no live group to name, so it fails closed instead. The case immediately
# after the namespaced-lane one proves that.
export UNTRUSTED_GROUP_NAME='isolated'

run_case() { : > "$GH_API_LOG"; bash "$script" 2>&1; }
code_of() { run_case >/dev/null 2>&1; printf '%s' "$?"; }

out="$(run_case)"
[ "$(code_of)" = "0" ] \
  && grep -qF 'No drift' <<<"$out" \
  && pass "organization-wide permissive group admits new private and public repositories" \
  || fail "clean permissive policy did not reconcile: $out"

# GitHub-hosted larger runners have arbitrary administrator-chosen labels. A
# static runs-on scan cannot distinguish one named `general` from the
# self-hosted fleet label, so the org-admin reconciler must inspect inventory.
HOSTED_RUNNERS_PAGES='{"total_count":1,"runners":[{"id":42,"name":"general"}]}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'GitHub-hosted larger runner' <<<"$out" \
  && grep -qF '"general" (id 42)' <<<"$out" \
  && pass "an arbitrary-label larger runner is actionable drift" \
  || fail "larger hosted runner inventory was not reported: $out"

# The allowlist is a reviewed repository file, not an org variable or an
# environment override. Exercise a reviewed entry against a disposable copy so
# the production file can remain empty.
allowlisted="$tmp/allowlisted"
mkdir -p "$allowlisted"
cp "$script" "$allowlisted/runner-admission-reconcile.sh"
printf '[{"id":42,"name":"general"}]\n' \
  >"$allowlisted/hosted-larger-runner-allowlist.json"
out="$(bash "$allowlisted/runner-admission-reconcile.sh" 2>&1)"
code=$?
[ "$code" = "0" ] \
  && grep -qF '1 reviewed GitHub-hosted larger runner' <<<"$out" \
  && pass "a larger runner covered by the reviewed ID/name allowlist reconciles cleanly" \
  || fail "reviewed larger-runner allowlist was not honored: $out"

HOSTED_RUNNERS_PAGES='{"total_count":1,"runners":[{"id":42,"name":"general","runner_group_id":7,"state":"ready","maximum_runners":4}]}'
out="$(bash "$allowlisted/runner-admission-reconcile.sh" 2>&1)"
code=$?
[ "$code" = "0" ] \
  && grep -qF '1 reviewed GitHub-hosted larger runner' <<<"$out" \
  && pass "mutable hosted-runner API fields do not defeat exact ID/name approval" \
  || fail "full hosted-runner API object was compared to the identity allowlist: $out"
HOSTED_RUNNERS_PAGES='{"total_count":1,"runners":[{"id":42,"name":"general"}]}'

printf '[{"id":42,"name":"general-old"}]\n' \
  >"$allowlisted/hosted-larger-runner-allowlist.json"
out="$(bash "$allowlisted/runner-admission-reconcile.sh" 2>&1)"
code=$?
[ "$code" = "1" ] \
  && grep -qF 'identity differs' <<<"$out" \
  && pass "renaming an allowlisted larger runner requires reviewed identity update" \
  || fail "renamed larger runner retained ID-only approval: $out"

printf '[{"id":42,"name":"general"},{"id":99,"name":"retired-xl"}]\n' \
  >"$allowlisted/hosted-larger-runner-allowlist.json"
out="$(bash "$allowlisted/runner-admission-reconcile.sh" 2>&1)"
code=$?
[ "$code" = "1" ] \
  && grep -qF 'absent from live inventory' <<<"$out" \
  && pass "a stale reviewed larger-runner identity is drift" \
  || fail "stale larger-runner approval was treated as clean: $out"

for malformed_allowlist in \
  '[{"id":42,"name":"general"},{"id":42,"name":"other"}]' \
  '[{"id":42,"name":"general"},{"id":43,"name":"general"}]' \
  '[{"id":42,"name":""}]' \
  '[42]'; do
  printf '%s\n' "$malformed_allowlist" \
    >"$allowlisted/hosted-larger-runner-allowlist.json"
  out="$(bash "$allowlisted/runner-admission-reconcile.sh" 2>&1)"
  code=$?
  [ "$code" = "2" ] \
    && pass "malformed or duplicate reviewed larger-runner identity is undetermined" \
    || fail "invalid larger-runner allowlist was accepted: $malformed_allowlist: $out"
done

# Two response objects model two pages. Both runners must survive collection;
# keeping only the final page would omit `general` and weaken the report.
HOSTED_RUNNERS_PAGES=$'{"total_count":2,"runners":[{"id":42,"name":"general"}]}\n{"total_count":2,"runners":[{"id":43,"name":"release-xl"}]}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF '"general" (id 42)' <<<"$out" \
  && grep -qF '"release-xl" (id 43)' <<<"$out" \
  && pass "larger-runner inventory is collected across every page" \
  || fail "paginated larger-runner inventory was under-reported: $out"

HOSTED_RUNNERS_PAGES='{"total_count":1,"runners":{}}'
[ "$(code_of)" = "2" ] \
  && pass "a malformed larger-runner response is undetermined, never clean" \
  || fail "malformed larger-runner response did not fail closed: $(code_of)"

HOSTED_RUNNERS_PAGES='{"total_count":2,"runners":[{"id":42,"name":"general"}]}'
[ "$(code_of)" = "2" ] \
  && pass "an incomplete larger-runner page count is undetermined" \
  || fail "incomplete larger-runner pagination did not fail closed: $(code_of)"

HOSTED_RUNNERS_PAGES='{"total_count":1,"runners":[{"name":"general"}]}'
[ "$(code_of)" = "2" ] \
  && pass "a larger-runner object without an id is undetermined" \
  || fail "malformed larger-runner object did not fail closed: $(code_of)"

for malformed_inventory in \
  '{"total_count":1,"runners":[{"id":42,"name":""}]}' \
  '{"total_count":2,"runners":[{"id":42,"name":"general"},{"id":42,"name":"other"}]}' \
  '{"total_count":2,"runners":[{"id":42,"name":"general"},{"id":43,"name":"general"}]}' \
  $'{"total_count":2,"runners":[{"id":42,"name":"general"}]}\n{"total_count":3,"runners":[{"id":43,"name":"other"}]}'; do
  HOSTED_RUNNERS_PAGES="$malformed_inventory"
  [ "$(code_of)" = "2" ] \
    && pass "malformed, duplicate, or count-inconsistent live identity is undetermined" \
    || fail "invalid larger-runner inventory was accepted: $malformed_inventory"
done

HOSTED_RUNNERS_PAGES='{"total_count":0,"runners":[]}'
HOSTED_RUNNERS_404='1'
[ "$(code_of)" = "2" ] \
  && pass "a 404 reading larger-runner inventory is undetermined" \
  || fail "larger-runner 404 did not fail closed: $(code_of)"
HOSTED_RUNNERS_404='0'

FAIL_PATH='/actions/hosted-runners'
[ "$(code_of)" = "2" ] \
  && pass "a larger-runner API failure is undetermined" \
  || fail "larger-runner API failure did not fail closed: $(code_of)"
FAIL_PATH=''
HOSTED_RUNNERS_PAGES='{"total_count":0,"runners":[]}'

G4_GROUP='{"id":4,"name":"DigitalOcean","visibility":"all","allows_public_repositories":false}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'Verjson/public-app' <<<"$out" \
  && pass "public repository denied by group is reported as drift" \
  || fail "public admission drift not reported: $out"

G4_GROUP='{"id":4,"name":"DigitalOcean","visibility":"selected","allows_public_repositories":true}'
# Two lines == two pages: the repo under test sits on the FIRST page, which is
# precisely what a per-page collector would drop.
G4_MEMBERS=$'Verjson/public-app\nVerjson/some-other-repo'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'Verjson/private-lib' <<<"$out" \
  && pass "new private repository missing from selected group is reported" \
  || fail "selected-group new repository gap not reported: $out"

G4_GROUP='{"id":4,"name":"DigitalOcean","visibility":"all","allows_public_repositories":true}'
G4_MEMBERS=''
G4_RUNNERS='{"name":"general-1","status":"offline","labels":["self-hosted","general"]}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'has no matching online runner' <<<"$out" \
  && pass "selector with no online capacity is reported" \
  || fail "capacity drift not reported: $out"

G4_RUNNERS='{"name":"general-1","status":"online","labels":["self-hosted","general"]}'
DEFAULT_VAR='{"value":"[\"ubuntu-24.04\"]","visibility":"all"}'
UNTRUSTED_VAR='{"value":"[\"ubuntu-24.04\"]","visibility":"all"}'
LEGACY_DEFAULT_VAR=''
LEGACY_UNTRUSTED_VAR=''
G4_GROUP='{"id":4,"name":"DigitalOcean","visibility":"all","allows_public_repositories":false,"default":false}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'Verjson/public-app' <<<"$out" \
  && grep -qF 'privileged lane' <<<"$out" \
  && pass "privileged group admission is checked independently of hosted CI lanes" \
  || fail "privileged admission drift was not reported independently: $out"

DEFAULT_VAR='{"value":"[\"self-hosted\",\"general\"]","visibility":"all"}'
UNTRUSTED_VAR='{"value":"[\"ubuntu-24.04\"]","visibility":"all"}'
LEGACY_DEFAULT_VAR='{"value":"[\"self-hosted\",\"general\"]","visibility":"all"}'
LEGACY_UNTRUSTED_VAR='{"value":"[\"self-hosted\",\"general\"]","visibility":"all"}'
G4_GROUP='{"id":4,"name":"DigitalOcean","visibility":"all","allows_public_repositories":true,"default":false}'
PRIVILEGED_VAR='{"value":"[\"self-hosted\",\"privileged-only\"]","visibility":"all"}'
out="$(run_case)"
[ "$(code_of)" = "2" ] \
  && grep -qF 'CI_LANE_PRIVILEGED' <<<"$out" \
  && pass "an ungoverned privileged selector is undetermined, never silently skipped" \
  || fail "privileged selector was not reconciled: $out"

PRIVILEGED_VAR='{"value":"[\"self-hosted\",\"general\"]","visibility":"all"}'
G4_RUNNERS='{"name":"general-1","status":"offline","labels":["self-hosted","general"]}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'CI_LANE_PRIVILEGED has no matching online runner' <<<"$out" \
  && pass "privileged selector with no online capacity is reported" \
  || fail "privileged capacity drift not reported: $out"

G4_RUNNERS='{"name":"general-1","status":"online","labels":["self-hosted","general"]}'
UNTRUSTED_VAR='{"value":"not-json","visibility":"all"}'
[ "$(code_of)" = "2" ] \
  && pass "malformed variable is undetermined, never clean" \
  || fail "malformed variable did not return exit 2"

UNTRUSTED_VAR='{"value":"[\"self-hosted\",\"general\"]","visibility":"selected"}'
[ "$(code_of)" = "2" ] \
  && pass "variable hidden from new repositories is undetermined" \
  || fail "non-global variable did not return exit 2"

UNTRUSTED_VAR='{"value":"[\"self-hosted\",\"unknown-lane\"]","visibility":"all"}'
out="$(run_case)"
[ "$(code_of)" = "2" ] \
  && ! grep -qF 'unknown-lane' <<<"$out" \
  && pass "ungoverned lane selector is undetermined" \
  || fail "ungoverned lane did not return exit 2 without leaking its value: $out"

UNTRUSTED_VAR='{"value":"[\"ubuntu-24.04\"]","visibility":"all"}'
FAIL_PATH='/actions/variables/VERJSON_RUNNER_DEFAULT'
[ "$(code_of)" = "2" ] \
  && pass "org API failure is undetermined, never clean" \
  || fail "API failure did not return exit 2"

FAIL_PATH=''
# Migration complete: the retired pair is gone, so these cases exercise lane
# resolution alone rather than also tripping the divergence report below.
LEGACY_DEFAULT_VAR=''
LEGACY_UNTRUSTED_VAR=''
DEFAULT_VAR='{"value":"[\"self-hosted\",\"lane-general\"]","visibility":"all"}'
UNTRUSTED_VAR='{"value":"[\"self-hosted\",\"lane-untrusted\"]","visibility":"all"}'
PRIVILEGED_VAR='{"value":"[\"self-hosted\",\"lane-untrusted\"]","visibility":"all"}'
G6_GROUP='{"id":6,"name":"isolated","visibility":"all","allows_public_repositories":true}'
G4_RUNNERS='{"name":"general-1","status":"online","labels":["self-hosted","lane-general"]}'
G6_RUNNERS='{"name":"untrusted-1","status":"online","labels":["self-hosted","lane-untrusted"]}'
out="$(run_case)"
[ "$(code_of)" = "0" ] \
  && grep -qF 'No drift' <<<"$out" \
  && pass "namespaced ADR 0035 lane labels map without reconciler changes" \
  || fail "namespaced lane labels did not reconcile: $out"

# Keep the dedicated untrusted-group path observable while the general lane is
# rejected as a rollback. These fixture lines model records from paginated API
# output, as the mock contract above documents.
PRIVILEGED_VAR='{"value":"[\"self-hosted\",\"lane-general\"]","visibility":"all"}'
G6_GROUP='{"id":6,"name":"isolated","visibility":"all","allows_public_repositories":false}'
G6_MEMBERS=''
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'Verjson/public-app' <<<"$out" \
  && grep -qF 'untrusted lane' <<<"$out" \
  && ! grep -qF 'isolated' <<<"$out" \
  && pass "dedicated untrusted group public-admission denial is reported" \
  || fail "dedicated untrusted group public-admission denial was not reported: $out"

G6_GROUP='{"id":6,"name":"isolated","visibility":"selected","allows_public_repositories":true}'
# The required public repository is on page 1 so a last-page-only result fails.
G6_MEMBERS=$'Verjson/public-app\nVerjson/private-lib'
out="$(run_case)"
[ "$(code_of)" = "0" ] \
  && grep -qF 'No drift' <<<"$out" \
  && pass "selected untrusted-group membership spanning API pages is admitted" \
  || fail "selected untrusted-group members were not reconciled: $out"

G6_MEMBERS='Verjson/private-lib'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'Verjson/public-app' <<<"$out" \
  && grep -qF 'untrusted lane' <<<"$out" \
  && ! grep -qF 'isolated' <<<"$out" \
  && pass "selected untrusted-group membership denial is reported" \
  || fail "selected untrusted-group membership denial was not reported: $out"

G6_MEMBERS=$'Verjson/private-lib\nVerjson/public-app'
G6_RUNNERS='{"name":"untrusted-1","status":"offline","labels":["self-hosted","lane-untrusted"]}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'CI_LANE_UNTRUSTED has no matching online runner' <<<"$out" \
  && pass "offline dedicated untrusted capacity is reported" \
  || fail "offline dedicated untrusted capacity was not reported: $out"

G6_RUNNERS='{"name":"untrusted-1","status":"online","labels":["self-hosted","lane-mismatch"]}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'CI_LANE_UNTRUSTED has no matching online runner' <<<"$out" \
  && pass "mismatched dedicated untrusted labels do not count as capacity" \
  || fail "mismatched dedicated untrusted labels counted as capacity: $out"

G6_GROUP='{"id":6,"name":"isolated","visibility":"all","allows_public_repositories":true}'
G6_MEMBERS=''
G6_RUNNERS='{"name":"untrusted-1","status":"online","labels":["self-hosted","lane-untrusted"]}'

# Same fixture, with the untrusted group unconfigured. Reaching a lane whose
# group nobody has named must be undetermined and say so — the alternative, the
# shipped `isolated` default this replaces, reported a group deleted on
# 2026-07-31 as missing and read like fleet drift rather than stale code (#401).
UNTRUSTED_GROUP_NAME='' out="$(run_case)"
UNTRUSTED_GROUP_NAME='' code="$(code_of)"
[ "$code" = "2" ] \
  && grep -qF "no runner group is configured for lane 'untrusted'" <<<"$out" \
  && pass "an unconfigured untrusted group is undetermined, and names what to set" \
  || fail "unconfigured untrusted group did not fail closed: $out"
UNTRUSTED_GROUP_NAME='isolated'

# #266. Group 6 (`isolated`) was deleted on 2026-07-31 and the reconciler, which
# pinned the id, went undetermined on every run. Two distinct behaviours have to
# hold, and the second is the one that was actually broken.

# 1. A group a lane genuinely needs, gone: still fail closed and name the lane,
#    while keeping the org-variable-backed group name out of public output.
DELETED_GROUPS='6'
out="$(run_case)"
[ "$(code_of)" = "2" ] \
  && grep -qF 'untrusted lane' <<<"$out" \
  && ! grep -qF 'isolated' <<<"$out" \
  && pass "a missing lane-selected group fails closed without disclosing its configured name" \
  || fail "missing selected group was not safely reported by lane: $out"

# 2. ADR 0235 withdrew this selector as an untrusted-lane rollback. It is known
#    policy drift even when the untrusted group is currently unselected.
UNTRUSTED_VAR='{"value":"[\"self-hosted\",\"lane-general\"]","visibility":"all"}'
PRIVILEGED_VAR='{"value":"[\"self-hosted\",\"lane-general\"]","visibility":"all"}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'resolves to the persistent general runner lane' <<<"$out" \
  && ! grep -qF 'isolated' <<<"$out" \
  && ! grep -qF '/actions/runner-groups' "$GH_API_LOG" \
  && pass "a persistent untrusted selector is reported before group lookup" \
  || fail "persistent untrusted selector was not safely reported: $out"

# 3. The general lane is the one every private repository rides. Its group going
#    missing is the highest-blast-radius case, and it had no coverage.
UNTRUSTED_VAR='{"value":"[\"ubuntu-24.04\"]","visibility":"all"}'
DELETED_GROUPS='4'
DEFAULT_VAR='{"value":"[\"self-hosted\",\"lane-general\"]","visibility":"all"}'
out="$(run_case)"
[ "$(code_of)" = "2" ] \
  && grep -qF 'general lane' <<<"$out" \
  && ! grep -qF 'DigitalOcean' <<<"$out" \
  && pass "the general lane's group going missing fails closed without disclosing its configured name" \
  || fail "missing general group was not safely reported by lane: $out"

# The fixtures above use the shipped default group name deliberately, so a stale
# default is a failing suite rather than a monitor that resolves nothing in
# production. That is not enough on its own: the name it defaults to is a live
# org fact, and on 2026-08-05 the pool was renamed GCP→DigitalOcean while the
# labels moved by variable and this stayed in code (#401). So the workflow must
# also be able to follow a rename without a pull request.
reconcile_workflow="$(cd "$(dirname "$0")/../.." && pwd)/.github/workflows/runner-admission-reconcile.yml"
grep -qF "GENERAL_GROUP_NAME: \${{ vars.CI_RUNNER_GENERAL_GROUP || 'DigitalOcean' }}" "$reconcile_workflow" \
  && pass "the general group name is repointable from org variables, with a fallback" \
  || fail "runner-admission-reconcile.yml does not source GENERAL_GROUP_NAME from a variable with a fallback"

# Treat organization-variable values as secret-shaped payloads even though the
# GitHub setting is not a secret store: the report is copied into a public issue.
export GENERAL_GROUP_NAME='org-variable-general-canary'
export UNTRUSTED_GROUP_NAME='org-variable-untrusted-canary'
G4_GROUP='{"id":4,"name":"org-variable-general-canary","visibility":"all","allows_public_repositories":true,"default":false}'
G6_GROUP='{"id":6,"name":"org-variable-untrusted-canary","visibility":"all","allows_public_repositories":true,"default":false}'
DELETED_GROUPS=''
out="$(run_case)"
[ "$(code_of)" = "0" ] \
  && ! grep -qF 'org-variable-general-canary' <<<"$out" \
  && ! grep -qF 'org-variable-untrusted-canary' <<<"$out" \
  && pass "clean output discloses no org-variable-backed runner-group name" \
  || fail "clean output disclosed an org-variable-backed group name: $out"

G4_GROUP='{"id":4,"name":"org-variable-general-canary","visibility":"selected","allows_public_repositories":true,"default":false}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'general lane' <<<"$out" \
  && grep -qF 'id 4' <<<"$out" \
  && ! grep -qF 'org-variable-general-canary' <<<"$out" \
  && ! grep -qF 'org-variable-untrusted-canary' <<<"$out" \
  && pass "admission drift uses a lane and numeric id without org-variable payloads" \
  || fail "admission drift disclosed an org-variable-backed group name: $out"

export GENERAL_GROUP_NAME='DigitalOcean'
export UNTRUSTED_GROUP_NAME='isolated'
G4_GROUP='{"id":4,"name":"DigitalOcean","visibility":"all","allows_public_repositories":true,"default":false}'
G6_GROUP='{"id":6,"name":"isolated","visibility":"all","allows_public_repositories":true,"default":false}'

# The matching invariant — this job must not ride the pool it monitors — lives in
# runner-routing-policy.test.sh with every other routing assertion, and is
# written there as "every runs-on is the fast-lane selector" rather than "no
# runs-on is the general lane", so appending a second job cannot slip past it.
# Kept in one place on purpose: the same rule half-asserted in two suites is how
# a weakened version reads as covered.

# 4. If the listing itself cannot be read, no group can be resolved at all. That
#    is the definition of undetermined and must never read as clean.
DELETED_GROUPS=''
FAIL_PATH='/actions/runner-groups'
[ "$(code_of)" = "2" ] \
  && pass "an unreadable runner-group listing is undetermined, never clean" \
  || fail "group listing failure did not return exit 2"

FAIL_PATH=''
# Migration complete: the retired pair is gone, so these cases exercise lane
# resolution alone rather than also tripping the divergence report below.
LEGACY_DEFAULT_VAR=''
LEGACY_UNTRUSTED_VAR=''
DEFAULT_VAR='{"value":"[\"self-hosted\",\"lane-general\"]","visibility":"all"}'
UNTRUSTED_VAR='{"value":"[\"self-hosted\",\"lane-untrusted\"]","visibility":"all"}'

# Structural: `--paginate` emits one response per page, so a `[...]` collector
# yields one array per page and downstream `jq -e` sees only the last. The
# behavioural multi-page case above locks in the new shape; this catches a
# regression back to the old one, which no fixture can express.
# Two bad shapes, not one. `'[.runners[]...'` is the per-page array collector.
# `'.runners'` streams the whole array per page instead of its elements, which
# slurps to a list-of-lists: `map(.name)` then yields [null] and a CLEAN org
# reports drift every day. Only element streaming (`'.runners[]`) is correct.
! grep -qE "'\[\.(repositories|runners)\[\]" "$script" \
  && ! grep -qE "'\.(repositories|runners)'" "$script" \
  && pass "group member/runner fetches use the pagination-safe streaming collector" \
  || fail "a per-page or whole-array collector reappeared in the group fetches"

grep -qF 'select(.archived == false)' "$script" \
  && pass "archived repositories remain excluded" \
  || fail "archived repository filter missing"

workflow="$here/../../.github/workflows/runner-admission-reconcile.yml"
[ -f "$workflow" ] \
  && grep -qF 'runner-admission-reconcile.sh' "$workflow" \
  && grep -qF 'ORG_ADMIN_TOKEN' "$workflow" \
  && pass "daily workflow keeps org-scoped reconciliation wiring" \
  || fail "runner admission workflow wiring drifted"

# The wrapper's exit-code contract, executed rather than grepped. House method
# (gate-hold-disable.test.sh, ci-wait-fail-closed.test.sh): awk-extract the exact `run:` block
# so the test cannot drift from the shipped logic, then drive it with a stub.
extract_reconcile_step() {
  awk '
    $0 == "        id: reconcile" { seen = 1 }
    seen && $0 == "        run: |" { cap = 1; next }
    cap && $0 ~ /^      - name:/ { exit }
    cap {
      if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
      if ($0 ~ /^[ \t]*$/) { print ""; next }
      exit
    }
  ' "$workflow"
}

wrapper="$tmp/reconcile-step.sh"
extract_reconcile_step >"$wrapper"
[ -s "$wrapper" ] \
  && pass "reconcile step body extracts from the workflow" \
  || fail "could not extract the reconcile step body"

# Drive the extracted block with a stub standing in for the reconciler, so the
# only thing under test is how the wrapper maps an exit code to a verdict.
# Emits "<wrapper-exit> <code-output-VALUE>". Asserting the value, not merely
# that a `code=` line exists: a count-based assertion stays green when the
# wrapper hardcodes `code=0`, which would suppress every drift issue AND fire
# the close step on every run, silently closing live drift. A test that cannot
# distinguish that is the same fail-open it is supposed to be guarding.
run_wrapper() { # run_wrapper <exit-code>
  local stub_code="$1" work="$tmp/wrap.$1" rc
  rm -rf "$work"; mkdir -p "$work/scripts/ci-gate"
  printf '#!/usr/bin/env bash\necho "stub report"\nexit %s\n' "$stub_code" \
    >"$work/scripts/ci-gate/runner-admission-reconcile.sh"
  chmod +x "$work/scripts/ci-gate/runner-admission-reconcile.sh"
  : >"$work/summary"; : >"$work/output"
  ( cd "$work" && GH_TOKEN=stub-token \
      GITHUB_STEP_SUMMARY="$work/summary" GITHUB_OUTPUT="$work/output" \
      bash "$wrapper" >/dev/null 2>&1 )
  rc=$?
  printf '%s %s' "$rc" "$(sed -n 's/^code=//p' "$work/output" | tr -d '\n')"
}

[ "$(run_wrapper 0)" = "0 0" ] \
  && pass "wrapper passes a clean reconciliation through as success" \
  || fail "clean run did not exit 0 with code=0: $(run_wrapper 0)"

[ "$(run_wrapper 1)" = "0 1" ] \
  && pass "wrapper keeps drift green so the issue step can file it" \
  || fail "drift run did not exit 0 with code=1: $(run_wrapper 1)"

[ "$(run_wrapper 2)" = "1 2" ] \
  && pass "wrapper fails the job when the org could not be read" \
  || fail "undetermined run did not fail the job: $(run_wrapper 2)"

# The gap: 127 is `command not found`, and a `set -u` abort surfaces as 1-or-worse.
# Before this, anything outside 0/1/2 fell through to the unconditional `exit 0`
# — job green, drift step skipped, nothing filed, org never actually examined.
[ "$(run_wrapper 127)" = "1 127" ] \
  && pass "wrapper treats an off-contract exit code as undetermined, not clean" \
  || fail "crash exit code was reported as clean: $(run_wrapper 127)"

# The wrapper's exit code and its `code` output only mean anything together with
# the conditions on the downstream steps. Without these, flipping the drift step
# to `== '2'` leaves the whole suite green while drift is never filed again.
grep -qF "steps.reconcile.outputs.code == '1'" "$workflow" \
  && pass "the drift-issue step is still gated on the drift code" \
  || fail "drift-issue step condition drifted from code == '1'"

grep -qF "steps.reconcile.outputs.code == '0'" "$workflow" \
  && pass "the issue-closing step is still gated on the clean code" \
  || fail "issue-closing step condition drifted from code == '0'"

grep -qF "DRIFT_ISSUE: '820'" "$workflow" \
  && ! grep -qF 'gh issue create' "$workflow" \
  && grep -qF 'gh issue reopen "$DRIFT_ISSUE"' "$workflow" \
  && ! grep -qF 'gh issue edit "$DRIFT_ISSUE"' "$workflow" \
  && grep -qF '/issues/comments/$comment_id' "$workflow" \
  && pass "drift reuses issue 820 and cannot create a new tracker" \
  || fail "runner admission can still create a new issue instead of reusing #820"

# Execute the exact durable-report step. A public issue lets anyone copy the
# marker, so ownership must be established from GitHub's immutable bot actor id
# before a comment can become the PATCH target.
extract_drift_report_step() {
  awk '
    $0 == "      - name: Reopen or update the durable drift issue" { seen = 1 }
    seen && $0 == "        run: |" { cap = 1; next }
    cap && $0 ~ /^      - name:/ { exit }
    cap {
      if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
      if ($0 ~ /^[ \t]*$/) { print ""; next }
      exit
    }
  ' "$workflow"
}

comment_step="$tmp/comment-step.sh"
extract_drift_report_step >"$comment_step"
comment_bin="$tmp/comment-bin"
mkdir -p "$comment_bin"
cat >"$comment_bin/gh" <<'GH'
#!/usr/bin/env bash
set -eu
if [ "$1" = issue ] && [ "$2" = view ]; then
  printf 'OPEN\n'
  exit 0
fi
if [ "$1" = issue ] && [ "$2" = reopen ]; then
  printf 'REOPEN\n' >>"$COMMENT_ACTIONS"
  exit 0
fi
if [ "$1" = api ]; then
  method=GET
  path=''
  shift
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --method) method="$2"; shift 2 ;;
      /repos/*) path="$1"; shift ;;
      *) shift ;;
    esac
  done
  if [ "$method" = GET ] && [[ "$path" == *'/comments?per_page=100' ]]; then
    printf '%s\n' "$COMMENT_FIXTURE"
    exit 0
  fi
  printf '%s %s\n' "$method" "$path" >>"$COMMENT_ACTIONS"
  exit 0
fi
printf 'unexpected gh call: %s\n' "$*" >&2
exit 1
GH
chmod +x "$comment_bin/gh"

run_comment_case() {
  local fixture="$1" case_dir="$tmp/comment-$2" rc
  mkdir -p "$case_dir"
  : >"$case_dir/actions"
  COMMENT_FIXTURE="$fixture" COMMENT_ACTIONS="$case_dir/actions" \
    PATH="$comment_bin:$PATH" GH_TOKEN=fixture REPORT='fixture report' \
    MARKER='<!-- runner-admission-drift -->' DRIFT_ISSUE=820 \
    REPORT_ACTOR_ID=41898282 \
    GITHUB_REPOSITORY='Verjson/.github' bash "$comment_step" \
    >"$case_dir/output" 2>&1
  rc=$?
  printf '%s %s\n' "$rc" "$case_dir"
}

read -r comment_rc comment_case < <(run_comment_case '' zero)
[ "$comment_rc" = 0 ] \
  && grep -qF 'POST /repos/Verjson/.github/issues/820/comments' "$comment_case/actions" \
  && pass "zero trusted report comments creates exactly one bot-owned report comment" \
  || fail "zero-comment report path did not create its durable comment"

foreign='{"id":900,"body":"<!-- runner-admission-drift -->","author_id":12345}'
read -r comment_rc comment_case < <(run_comment_case "$foreign" foreign)
[ "$comment_rc" = 0 ] \
  && grep -qF 'POST /repos/Verjson/.github/issues/820/comments' "$comment_case/actions" \
  && ! grep -qF '/issues/comments/900' "$comment_case/actions" \
  && pass "a foreign marker comment is ignored and never patched" \
  || fail "a foreign marker captured the report target"

one_trusted=$'{"id":100,"body":"<!-- runner-admission-drift -->","author_id":41898282}\n{"id":900,"body":"<!-- runner-admission-drift -->","author_id":12345}'
read -r comment_rc comment_case < <(run_comment_case "$one_trusted" one)
[ "$comment_rc" = 0 ] \
  && grep -qF 'PATCH /repos/Verjson/.github/issues/comments/100' "$comment_case/actions" \
  && ! grep -qF '/issues/comments/900' "$comment_case/actions" \
  && ! grep -qF 'POST ' "$comment_case/actions" \
  && pass "one trusted bot report is the only update target" \
  || fail "one trusted report did not update exactly its bot-owned comment"

two_trusted=$'{"id":100,"body":"<!-- runner-admission-drift -->","author_id":41898282}\n{"id":101,"body":"<!-- runner-admission-drift -->","author_id":41898282}'
read -r comment_rc comment_case < <(run_comment_case "$two_trusted" multiple)
[ "$comment_rc" != 0 ] \
  && [ ! -s "$comment_case/actions" ] \
  && pass "multiple trusted reports fail closed before comment mutation" \
  || fail "ambiguous trusted reports mutated an arbitrary comment"

# --------------------------------------------------------------------------
# Runner placement (#275).
#
# A runner registered without `--runnergroup` lands in GitHub's default group,
# which is `visibility: all` / `allows_public_repositories: true` and has no
# label discipline. Nothing detected that: the two checks above ask whether
# repositories are ADMITTED and whether lanes have CAPACITY, and a stray runner
# is neither — it is capacity no lane selects and no policy governs.
#
# Detection only. The fix is `--runnergroup` at provisioning time, which lives
# in verjson-cli-cloud, outside this repository.
# --------------------------------------------------------------------------
export G1_RUNNERS='{"name":"gha-stray-1","status":"online","labels":["self-hosted","Linux","X64"]}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'gha-stray-1' <<<"$out" \
  && pass "a runner in the default group is drift, and the runner is named" \
  || fail "stray runner in the default group was not reported: $out"

# Offline is not absolution: the runner is still registered there and comes back
# online on its own. Reusing the online check's shape would have missed it.
export G1_RUNNERS='{"name":"gha-stray-2","status":"offline","labels":["self-hosted"]}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'gha-stray-2' <<<"$out" \
  && pass "an offline runner in the default group is still drift" \
  || fail "offline stray runner was not reported: $out"

export GENERAL_GROUP_NAME='org-variable-remediation-canary'
G4_GROUP='{"id":4,"name":"org-variable-remediation-canary","visibility":"all","allows_public_repositories":true,"default":false}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'general lane' <<<"$out" \
  && grep -qF 'id 4' <<<"$out" \
  && ! grep -qF 'org-variable-remediation-canary' <<<"$out" \
  && pass "default-group remediation redacts the configured destination name" \
  || fail "default-group remediation disclosed its org-variable-backed destination: $out"
export GENERAL_GROUP_NAME='DigitalOcean'
G4_GROUP='{"id":4,"name":"DigitalOcean","visibility":"all","allows_public_repositories":true,"default":false}'

# The report is filed as an issue and an operator acts on it. Both halves of the
# remedy have to survive: --runnergroup fixes the NEXT registration and does
# nothing for the runner just named, so a message carrying only that leaves the
# drift in place and the issue re-filing daily.
grep -qF -- '--runnergroup' <<<"$out" \
  && grep -qiF 'move them' <<<"$out" \
  && pass "the drift report names both the remediation and the prevention" \
  || fail "drift report lost part of its remedy: $out"

# Traits are read from the group, not asserted about it: this fixture is
# `visibility: all` / public allowed, and a narrowed default group must not be
# described with the same sentence.
grep -qF 'visibility `all`' <<<"$out" \
  && pass "the drift report describes the group it actually read" \
  || fail "drift report does not derive the group's traits: $out"

# Multi-line fixtures are the multi-page case: a `[...]` collector would keep
# only the last page and silently under-report (the #260 class).
export G1_RUNNERS=$'{"name":"gha-stray-3","status":"online","labels":["self-hosted"]}\n{"name":"gha-stray-4","status":"online","labels":["self-hosted"]}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'gha-stray-3' <<<"$out" && grep -qF 'gha-stray-4' <<<"$out" \
  && pass "every stray runner is named, across pages" \
  || fail "paginated stray runners were under-reported: $out"

# A runner object with no `.name` must not render as an empty string — the drift
# line would read "...: . Move them..." and name nothing to act on.
export G1_RUNNERS='{"id":77,"status":"online","labels":["self-hosted"]}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'unnamed runner id 77' <<<"$out" \
  && pass "a runner with no name is reported by its id, not as blank" \
  || fail "nameless stray runner produced an unactionable report: $out"

export G1_RUNNERS=''
out="$(run_case)"
[ "$(code_of)" = "0" ] \
  && grep -qF 'No drift' <<<"$out" \
  && pass "an empty default group is clean" \
  || fail "empty default group was reported as drift: $out"

# The default group's own name is read with `// empty` + die, not `jq -r`, which
# renders a missing name as the literal "null" and exits 0 — the same trap
# group_id_of documents.
export G1_GROUP='{"id":1,"visibility":"all","allows_public_repositories":true,"default":true}'
[ "$(code_of)" = "2" ] \
  && pass "a default group with no name is undetermined" \
  || fail "nameless default group did not fail closed: $(code_of)"
export G1_GROUP='{"id":1,"name":"GitHub","visibility":"all","allows_public_repositories":true,"default":true}'

# Fail CLOSED, both directions. A 403 on the placement fetch must not read as
# "no runners there" — that is the shape that turns a security check into a
# rubber stamp, and it is the exact defect #266 left in this file.
export FAIL_PATH='/runner-groups/1/runners'
[ "$(code_of)" = "2" ] \
  && pass "an unreadable default group is undetermined, not clean" \
  || fail "unreadable default group did not fail closed: $(code_of)"
export FAIL_PATH=''

# The default group is found by its `.default` flag, never by the id 1. The id
# is stable in practice, but pinning one is what took this job down for a week
# (#266) — and with the fixture sitting at id 1, an id-pinned implementation
# would satisfy every other case here.
export G1_GROUP='{"id":9,"name":"Renamed Default","visibility":"all","allows_public_repositories":true,"default":true}'
export G1_RUNNERS='{"name":"gha-stray-5","status":"online","labels":["self-hosted"]}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'gha-stray-5' <<<"$out" \
  && grep -qF 'id 9' <<<"$out" \
  && ! grep -qF 'Renamed Default' <<<"$out" \
  && pass "the default group is resolved by its flag and reported by id, not name" \
  || fail "default group resolution is pinned to an id: $out"
export G1_RUNNERS=''

# Multiplicity is the same surprise as absence, and taking `.[0]` of it is a
# live fail-open: a stray in the SECOND default-flagged group is never fetched,
# so the job exits 0 saying no runner sits in the default group. An
# enterprise-shared Default alongside the org's own is the plausible route.
export G1_GROUP=$'{"id":1,"name":"GitHub","visibility":"all","allows_public_repositories":true,"default":true}\n{"id":9,"name":"Enterprise Default","visibility":"all","allows_public_repositories":true,"default":true}'
export G1_RUNNERS='{"name":"gha-stray-6","status":"online","labels":["self-hosted"]}'
[ "$(code_of)" = "2" ] \
  && pass "more than one default group is undetermined, not silently first-wins" \
  || fail "multiple default groups did not fail closed: $(code_of)"
export G1_RUNNERS=''

# GitHub always marks exactly one group default and a custom group cannot become
# one, so no default group in the listing means the listing is not what we think
# it is. Guessing an id here is how the pinned-id outage (#266) happened.
export G1_GROUP=''
[ "$(code_of)" = "2" ] \
  && pass "a listing with no default group is undetermined" \
  || fail "missing default group did not fail closed: $(code_of)"
export G1_GROUP='{"id":1,"name":"GitHub","visibility":"all","allows_public_repositories":true,"default":true}'

# The migration's own hazard: the retired pair stays set for consumers pinned to
# a pre-migration SHA, so a lane edit that leaves it behind sends those consumers
# somewhere this run never examined. Silence there is what made #401 invisible.
DEFAULT_VAR='{"value":"[\"self-hosted\",\"general\"]","visibility":"all"}'
UNTRUSTED_VAR='{"value":"[\"ubuntu-24.04\"]","visibility":"all"}'
LEGACY_DEFAULT_VAR='{"value":"[\"self-hosted\",\"gone\"]","visibility":"all"}'
LEGACY_UNTRUSTED_VAR='{"value":"[\"self-hosted\",\"general\"]","visibility":"all"}'
out="$(run_case)"
[ "$(code_of)" = "1" ] \
  && grep -qF 'VERJSON_RUNNER_DEFAULT' <<<"$out" \
  && ! grep -qF 'gone' <<<"$out" \
  && pass "a retired variable that has drifted from its lane is reported" \
  || fail "legacy/lane divergence was not safely reported: $out"
LEGACY_DEFAULT_VAR='{"value":"[\"self-hosted\",\"general\"]","visibility":"all"}'

# A lane pointed at hosted capacity has no runner group and no self-hosted
# runners; asking for either would report permanent drift against a deliberate
# configuration — the shape a provider outage would force.
DEFAULT_VAR='{"value":"[\"ubuntu-24.04\"]","visibility":"all"}'
UNTRUSTED_VAR='{"value":"[\"ubuntu-24.04\"]","visibility":"all"}'
PRIVILEGED_VAR='{"value":"[\"ubuntu-24.04\"]","visibility":"all"}'
LEGACY_DEFAULT_VAR=''
export LEGACY_UNTRUSTED_VAR=''
out="$(run_case)"
[ "$(code_of)" = "0" ] \
  && grep -qF 'No drift' <<<"$out" \
  && pass "a hosted lane reconciles clean instead of demanding a runner group" \
  || fail "hosted lane did not reconcile: $out"

# #1115 cuts every historical untrusted selector together. Current workflows
# give CI_LANE_UNTRUSTED resolver precedence; immutable older pins still read
# the other five names, so a partial update is a security boundary failure
# rather than a harmless migration delay. ADR 0147's attended update order is
# deliberately legacy-first and canonical-last.
hosted_record='{"value":"[\"ubuntu-24.04\"]","visibility":"all"}'
persistent_record='{"value":"[\"self-hosted\",\"general\"]","visibility":"all"}'
malformed_record='{"value":"not-json","visibility":"all"}'
wrong_hosted_record='{"value":"[\"ubuntu-latest\"]","visibility":"all"}'
widened_hosted_record='{"value":"[\"ubuntu-24.04\",\"extra\"]","visibility":"all"}'
noncanonical_hosted_record='{"value":"[ \"ubuntu-24.04\" ]","visibility":"all"}'

untrusted_alias_names=(
  CI_LANE_UNTRUSTED
  CI_RUNNER_UNTRUSTED
  VERJSON_LANE_UNTRUSTED
  VERJSON_RUNNER_UNTRUSTED
  CI_RUNNER_ISOLATED
  VERJSON_RUNNER_ISOLATED
)
untrusted_alias_envs=(
  UNTRUSTED_VAR
  CI_RUNNER_UNTRUSTED_VAR
  VERJSON_LANE_UNTRUSTED_VAR
  VERJSON_RUNNER_UNTRUSTED_VAR
  CI_RUNNER_ISOLATED_VAR
  VERJSON_RUNNER_ISOLATED_VAR
)

set_all_untrusted_aliases() {
  local record="$1" name
  for name in "${untrusted_alias_envs[@]}"; do
    printf -v "$name" '%s' "$record"
    # shellcheck disable=SC2163 # The fixture names the variable to export.
    export "$name"
  done
}

DEFAULT_VAR="$persistent_record"
FALLBACK_VAR="$persistent_record"
PRIVILEGED_VAR="$persistent_record"
LEGACY_DEFAULT_VAR="$persistent_record"
G4_GROUP='{"id":4,"name":"DigitalOcean","visibility":"all","allows_public_repositories":true,"default":false}'
G4_RUNNERS='{"name":"general-1","status":"online","labels":["self-hosted","general"]}'
set_all_untrusted_aliases "$hosted_record"
out="$(run_case)"
[ "$(code_of)" = "0" ] \
  && grep -qF 'No drift' <<<"$out" \
  && pass "the hosted cut preserves trusted, fallback, privileged, and default selectors" \
  || fail "hosted cut changed a preserved lane: $out"

for index in "${!untrusted_alias_names[@]}"; do
  set_all_untrusted_aliases "$hosted_record"
  printf -v "${untrusted_alias_envs[$index]}" '%s' "$persistent_record"
  export "${untrusted_alias_envs[$index]}"
  out="$(run_case)"
  if [ "$index" = "0" ]; then
    [ "$(code_of)" = "1" ] \
      && grep -qF 'resolves to the persistent general runner lane' <<<"$out" \
      && ! grep -qF 'isolated' <<<"$out" \
      && ! grep -qF '/actions/runner-groups' "$GH_API_LOG" \
      && pass "the canonical persistent selector is rejected before alias comparison" \
      || fail "the canonical persistent selector was not safely rejected: $out"
  else
    [ "$(code_of)" = "1" ] \
      && grep -qF 'differs from CI_LANE_UNTRUSTED' <<<"$out" \
      && grep -qF 'CI_LANE_UNTRUSTED' <<<"$out" \
      && pass "partial cut at ${untrusted_alias_names[$index]} is reported as alias divergence" \
      || fail "partial cut at ${untrusted_alias_names[$index]} survived or failed for the wrong reason: $out"
  fi
done

for index in "${!untrusted_alias_names[@]}"; do
  set_all_untrusted_aliases "$hosted_record"
  printf -v "${untrusted_alias_envs[$index]}" '%s' "$malformed_record"
  export "${untrusted_alias_envs[$index]}"
  out="$(run_case)"
  [ "$(code_of)" = "2" ] \
    && grep -qF "${untrusted_alias_names[$index]}" <<<"$out" \
    && grep -qF 'not a non-empty JSON array of label strings' <<<"$out" \
    && pass "malformed ${untrusted_alias_names[$index]} fails closed at selector validation" \
    || fail "malformed ${untrusted_alias_names[$index]} survived or failed for the wrong reason: $out"
done

for index in "${!untrusted_alias_names[@]}"; do
  set_all_untrusted_aliases "$hosted_record"
  printf -v "${untrusted_alias_envs[$index]}" '%s' ''
  export "${untrusted_alias_envs[$index]}"
  out="$(run_case)"
  [ "$(code_of)" = "2" ] \
    && grep -qF "${untrusted_alias_names[$index]} is unset" <<<"$out" \
    && pass "missing ${untrusted_alias_names[$index]} fails closed as a partial configuration" \
    || fail "missing ${untrusted_alias_names[$index]} survived or failed for the wrong reason: $out"
done

set_all_untrusted_aliases "$wrong_hosted_record"
out="$(run_case)"
[ "$(code_of)" = "2" ] \
  && grep -qF 'hosted selector must be exactly ["ubuntu-24.04"]' <<<"$out" \
  && pass "a different hosted label cannot satisfy the cutover contract" \
  || fail "a different hosted label survived or failed for the wrong reason: $out"

set_all_untrusted_aliases "$widened_hosted_record"
out="$(run_case)"
[ "$(code_of)" = "2" ] \
  && grep -qF 'hosted selector must be exactly ["ubuntu-24.04"]' <<<"$out" \
  && pass "a widened hosted selector cannot satisfy the exact-label contract" \
  || fail "a widened hosted selector survived or failed for the wrong reason: $out"

set_all_untrusted_aliases "$noncanonical_hosted_record"
out="$(run_case)"
[ "$(code_of)" = "2" ] \
  && grep -qF 'hosted selector must be exactly ["ubuntu-24.04"]' <<<"$out" \
  && pass "JSON-equivalent hosted bytes cannot widen the canonical output" \
  || fail "noncanonical hosted bytes survived or failed for the wrong reason: $out"

set_all_untrusted_aliases "$hosted_record"
FALLBACK_VAR="$persistent_record"
out="$(run_case)"
[ "$(code_of)" = "0" ] \
  && grep -qF 'No drift' <<<"$out" \
  && pass "canonical untrusted selector takes precedence over persistent fallback" \
  || fail "persistent fallback overrode canonical hosted selector: $out"

UNTRUSTED_VAR=''
FALLBACK_VAR="$hosted_record"
out="$(run_case)"
[ "$(code_of)" = "2" ] \
  && grep -qF 'CI_LANE_UNTRUSTED is unset' <<<"$out" \
  && pass "hosted fallback cannot conceal a missing canonical untrusted alias" \
  || fail "fallback concealed missing CI_LANE_UNTRUSTED: $out"

# ADR 0235 withdrew the untrusted lane's persistent general-pool rollback.
# This known selector drift must be reported before the monitor reads any runner
# group, and its public report must not contain configured or selector values.
DEFAULT_VAR="$persistent_record"
FALLBACK_VAR="$persistent_record"
PRIVILEGED_VAR="$persistent_record"
LEGACY_DEFAULT_VAR=''
LEGACY_UNTRUSTED_VAR=''
FAIL_PATH=''
DELETED_GROUPS=''
GENERAL_GROUP_NAME='general-group-selector-canary'
UNTRUSTED_GROUP_NAME='untrusted-group-selector-canary'
set_all_untrusted_aliases "$hosted_record"

expected_general_drift="$(cat <<'EXPECTED'
## Runner admission reconciliation (Verjson)

### Drift

- `CI_LANE_UNTRUSTED` resolves to the persistent general runner lane, contrary to ADR 0235. Set it to `["ubuntu-24.04"]` or a dedicated lane classified as `untrusted`. Runner-group configuration, per-repository admission, and runner capacity were not inspected; selector and group values are redacted.
EXPECTED
)"
for value in \
  '{"value":"[\"self-hosted\",\"general\"]","visibility":"all"}' \
  '{"value":"[\"self-hosted\",\"lane-general\"]","visibility":"all"}' \
  '{"value":"[\"self-hosted\",\"general\",\"lane-untrusted\",\"selector-canary\"]","visibility":"all"}' \
  '{"value":"[\"self-hosted\",\"lane-untrusted\",\"general\",\"selector-canary\"]","visibility":"all"}'; do
  UNTRUSTED_VAR="$value"
  code=0
  out="$(run_case)" || code=$?
  [ "$code" = "1" ] \
    && [ "$out" = "$expected_general_drift" ] \
    && ! grep -qF '/actions/runner-groups' "$GH_API_LOG" \
    && ! grep -qF 'selector-canary' <<<"$out" \
    && ! grep -qF 'group-selector-canary' <<<"$out" \
    && pass "persistent untrusted selector is reported before runner-group lookup" \
    || fail "persistent untrusted selector was not safely rejected: code=$code report=$out api=$(cat "$GH_API_LOG")"
done

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
