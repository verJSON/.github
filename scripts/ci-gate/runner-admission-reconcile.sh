#!/usr/bin/env bash
# Reconcile ADR 0035 variable-driven lanes against live runner-group admission
# and online capacity. Exit 0 is proven clean, 1 is drift, and 2 means the live
# state could not be determined.
set -uo pipefail

ORG="${ORG:-Verjson}"
# Groups are resolved by NAME against the live listing, never by a pinned id.
# Ids are not stable over an org's lifetime: group 6 (`isolated`) was deleted on
# 2026-07-31 and this job went undetermined on every run afterwards (#266). The
# names stay overridable so a rename is a config change, not a code change.
# `GCP` until 2026-08-05, when the pool moved to DigitalOcean and the group was
# renamed with it. The lane's LABELS survived that (they come from the lane
# variables), but this name did not, so the reconciler resolved no group and went
# undetermined — #266 again, by name rather than by id (#401).
GENERAL_GROUP_NAME="${GENERAL_GROUP_NAME:-DigitalOcean}"
# No default. `isolated` was the old group name, and that group has not existed
# since it was deleted on 2026-07-31. An explicitly classified untrusted lane
# must name its live group; hosted untrusted selectors need no group name.
UNTRUSTED_GROUP_NAME="${UNTRUSTED_GROUP_NAME:-}"
script_dir="$(cd "$(dirname "$0")" && pwd)"
larger_runner_allowlist_file="$script_dir/hosted-larger-runner-allowlist.json"

die_undetermined() {
  printf 'UNDETERMINED: %s\n' "$1" >&2
  exit 2
}

command -v gh >/dev/null 2>&1 || die_undetermined "gh is not available"
command -v jq >/dev/null 2>&1 || die_undetermined "jq is not available"

fetch() {
  local path="$1" jq_expr="$2" out
  if ! out="$(gh api --paginate "$path" --jq "$jq_expr" 2>&1)"; then
    printf 'UNDETERMINED: GET %s failed: %s\n' \
      "$path" "$(head -3 <<<"$out" | tr '\n' ' ')" >&2
    return 2
  fi
  printf '%s\n' "$out"
}

# GitHub-hosted larger runners have administrator-chosen labels, so a workflow
# selector cannot distinguish one from a self-hosted fleet label. Inventory is
# the only authoritative boundary. The allowlist is a repository file on
# purpose: admitting metered capacity must require a reviewed code change, not
# an organization-variable edit beside the setting this check governs.
[ -r "$larger_runner_allowlist_file" ] \
  || die_undetermined "larger-runner allowlist is unreadable: $larger_runner_allowlist_file"
larger_runner_allowlist="$(jq -ce '
  if (type == "array"
    and all(.[];
      type == "object"
      and keys == ["id", "name"]
      and (.id | type) == "number"
      and .id > 0
      and .id == (.id | floor)
      and (.name | type) == "string"
      and (.name | test("\\S")))
    and (map(.id) | length) == (map(.id) | unique | length)
    and (map(.name | ascii_downcase) | length)
      == (map(.name | ascii_downcase) | unique | length))
  then .
  else error("invalid allowlist")
  end
' "$larger_runner_allowlist_file" 2>/dev/null)" \
  || die_undetermined "larger-runner allowlist must contain exact id/name objects with unique ids and names"

# Keep whole page objects until local validation. A server-side `.runners[]`
# projection can erase a missing collection into an empty stream; an unreadable
# inventory must never look like an empty one.
larger_runner_pages="$(fetch "/orgs/$ORG/actions/hosted-runners?per_page=100" '.')" || exit 2
if ! larger_runners="$(jq -cse '
  if length == 0 then error("no response pages")
  elif any(.[];
    type != "object"
    or (.total_count | type) != "number"
    or .total_count < 0
    or .total_count != (.total_count | floor)
    or (.runners | type) != "array")
  then error("malformed response page")
  else
    . as $pages
    | [$pages[].runners[]] as $runners
    | if ($pages | map(.total_count) | unique | length) != 1
      then error("page counts disagree")
      elif ($runners | length) != $pages[0].total_count
      then error("paginated count mismatch")
      elif any($runners[];
        type != "object"
        or (.id | type) != "number"
        or .id <= 0
        or .id != (.id | floor)
        or (.name | type) != "string"
        or (.name | test("\\S") | not))
      then error("malformed runner")
      elif ($runners | map(.id) | unique | length) != ($runners | length)
      then error("duplicate runner id")
      elif ($runners | map(.name | ascii_downcase) | unique | length)
        != ($runners | length)
      then error("duplicate runner name")
      else $runners | map({id, name})
      end
  end
' <<<"$larger_runner_pages" 2>/dev/null)"; then
  die_undetermined "GitHub-hosted larger-runner inventory response was malformed, incomplete, or pagination-inconsistent"
fi

unapproved_larger_runners="$(jq -c --argjson allowed "$larger_runner_allowlist" '
  map(select(. as $runner | ($allowed | index($runner)) == null))
' <<<"$larger_runners")" \
  || die_undetermined "could not compare larger-runner inventory with its allowlist"
stale_larger_runner_identities="$(jq -c --argjson inventory "$larger_runners" '
  map(select(. as $runner | ($inventory | index($runner)) == null))
' <<<"$larger_runner_allowlist")" \
  || die_undetermined "could not compare the larger-runner allowlist with inventory"
renamed_larger_runners="$(jq -c --argjson allowed "$larger_runner_allowlist" '
  map(select(. as $runner |
    ($allowed | map(.id) | index($runner.id)) != null
    and ($allowed | index($runner)) == null))
' <<<"$larger_runners")" \
  || die_undetermined "could not compare larger-runner names with reviewed identities"

repos_raw="$(fetch "/orgs/$ORG/repos?per_page=100" \
  '.[] | select(.archived == false) | "\(.full_name)\t\(.private)"')" || exit 2
[ -n "${repos_raw//[[:space:]]/}" ] \
  || die_undetermined "no active repositories returned for org $ORG"

# An unset lane variable is a NORMAL state — the `runs-on` chain simply falls
# through to the next term. Only a 404 is "not set". Any other failure — 403, 5xx, a network fault — means
# the value could not be READ, which is undetermined and must not be mistaken for
# an unset variable that falls through to the next lane term. Collapsing the two
# is how a permissions regression would look identical to a clean migration.
fetch_optional() {
  local path="$1" out
  if ! out="$(gh api "$path" --jq '{value,visibility}' 2>&1)"; then
    case "$out" in
      *404*|*"Not Found"*) return 0 ;;
      *) printf 'UNDETERMINED: GET %s failed: %s\n' \
           "$path" "$(head -3 <<<"$out" | tr '\n' ' ')" >&2
         return 2 ;;
    esac
  fi
  printf '%s\n' "$out"
}

# Read the variables the WORKFLOWS ACTUALLY ROUTE ON, resolved the same way the
# `runs-on` expression resolves them: the lane, then CI_LANE_FALLBACK.
#
# This used to read the retired runner-default pair. Every `runs-on:` now
# resolves `CI_LANE_*`, and the retired pair is deliberately left set for
# consumers pinned to a pre-migration SHA — so monitoring it would have this job
# validate a variable nothing routes on and report "no drift" while the live lane
# pointed at a group that admits nobody. That is #401's exact silence with one
# variable name changed, inside the file that exists to prevent it (#403 review).
lane_variable() {
  local lane="$1" resolved
  resolved="$(fetch_optional "/orgs/$ORG/actions/variables/CI_LANE_${lane}")" || return 2
  if [ -z "$resolved" ]; then
    resolved="$(fetch_optional "/orgs/$ORG/actions/variables/CI_LANE_FALLBACK")" || return 2
  fi
  [ -n "$resolved" ] || die_undetermined \
    "neither CI_LANE_${lane} nor CI_LANE_FALLBACK is set in $ORG; every runs-on chain resolves to the portable hosted tail"
  printf '%s\n' "$resolved"
}

default_var="$(lane_variable TRUSTED)" || exit 2
# Six names remain live while immutable historical callers cross the two
# runner-variable migrations.  CI_LANE_UNTRUSTED is the only selector used by
# current workflows, but a partial edit would leave an older pin executing on
# different capacity.  Require every alias until inventory-backed retirement
# removes it from this reviewed list.
untrusted_alias_names=(
  # Index zero is the reconciliation anchor, not rollout order. ADR 0147
  # updates the five historical aliases before this canonical value.
  CI_LANE_UNTRUSTED
  CI_RUNNER_UNTRUSTED
  VERJSON_LANE_UNTRUSTED
  VERJSON_RUNNER_UNTRUSTED
  CI_RUNNER_ISOLATED
  VERJSON_RUNNER_ISOLATED
)
untrusted_alias_vars=()
for untrusted_alias_name in "${untrusted_alias_names[@]}"; do
  untrusted_alias_var="$(fetch_optional "/orgs/$ORG/actions/variables/$untrusted_alias_name")" || exit 2
  [ -n "$untrusted_alias_var" ] || die_undetermined \
    "$untrusted_alias_name is unset; cannot prove all immutable untrusted caller aliases resolve one lane"
  untrusted_alias_vars+=("$untrusted_alias_var")
done
untrusted_var="${untrusted_alias_vars[0]}"
# This is the #204 cutover seam, not necessarily the selector of every live
# privileged job. `runner_labels`, FASTLANE, and OVERFLOW can currently win
# before the lane. Validating the seam independently makes the eventual removal
# of those temporary overrides observable before it moves merge authority.
privileged_var="$(lane_variable PRIVILEGED)" || exit 2

# The retired pair is still live for consumers on an old pin. It is not the
# routing source any more, so it is not fatal here — but a legacy variable that
# has drifted away from its lane means those consumers route somewhere this run
# never checked, which is reported rather than assumed away.
legacy_default_var="$(fetch_optional "/orgs/$ORG/actions/variables/VERJSON_RUNNER_DEFAULT")" || exit 2

selector() {
  local name="$1" variable="$2" value visibility
  value="$(jq -er '.value' <<<"$variable")" \
    || die_undetermined "$name has no string value"
  visibility="$(jq -er '.visibility' <<<"$variable")" \
    || die_undetermined "$name has no visibility"
  [ "$visibility" = "all" ] \
    || die_undetermined "$name is not visible to all repositories"
  # `self-hosted` is no longer required. A lane may legitimately point at hosted
  # capacity — that is what CI_LANE_FALLBACK is for, and what the whole
  # fleet would be set to during a provider outage. Such a lane has no runner
  # group and therefore no admission to reconcile; it is skipped below rather
  # than treated as a malformed variable.
  jq -e 'type == "array" and length >= 1
    and all(.[]; type == "string" and length > 0)' <<<"$value" >/dev/null \
    || die_undetermined "$name is not a non-empty JSON array of label strings"
  printf '%s\n' "$value"
}

default_selector="$(selector CI_LANE_TRUSTED "$default_var")" || exit 2
untrusted_selector="$(selector CI_LANE_UNTRUSTED "$untrusted_var")" || exit 2
privileged_selector="$(selector CI_LANE_PRIVILEGED "$privileged_var")" || exit 2

# Once the lane is hosted, only the standard ephemeral image is the reviewed
# destination. Accepting another hosted label could silently admit a larger or
# differently governed runner class.
if jq -e 'index("self-hosted") == null' <<<"$untrusted_selector" >/dev/null \
  && [ "$untrusted_selector" != '["ubuntu-24.04"]' ]; then
  die_undetermined \
    'CI_LANE_UNTRUSTED hosted selector must be exactly ["ubuntu-24.04"]; value redacted'
fi

untrusted_alias_selectors=()
for index in "${!untrusted_alias_names[@]}"; do
  untrusted_alias_selectors+=("$(selector "${untrusted_alias_names[$index]}" "${untrusted_alias_vars[$index]}")") \
    || exit 2
done

group_for_selector() {
  local name="$1" labels="$2"
  if jq -e 'index("self-hosted") == null' <<<"$labels" >/dev/null; then
    # Hosted capacity: GitHub admits every repository, so there is no group and
    # nothing to reconcile. Named explicitly so the resolve loop below stays
    # total and a hosted lane cannot be mistaken for an unhandled one.
    printf 'hosted\n'
  elif jq -e 'index("lane-general") != null or index("general") != null' <<<"$labels" >/dev/null; then
    printf 'general\n'
  elif jq -e 'index("lane-untrusted") != null or index("isolated") != null
    or index("untrusted-pr") != null' <<<"$labels" >/dev/null; then
    printf 'untrusted\n'
  else
    die_undetermined "$name selector has no governed lane label; value redacted"
  fi
}

admitted() {
  local repo="$1" private="$2" group="$3" members="$4"
  local visibility allows_public
  visibility="$(jq -r '.visibility' <<<"$group")"
  allows_public="$(jq -r '.allows_public_repositories' <<<"$group")"
  case "$visibility" in
    all)
      [ "$private" = "true" ] || [ "$allows_public" = "true" ]
      ;;
    selected)
      jq -e --arg repo "$repo" 'index($repo) != null' <<<"$members" >/dev/null \
        && { [ "$private" = "true" ] || [ "$allows_public" = "true" ]; }
      ;;
    *)
      return 1
      ;;
  esac
}

has_capacity() {
  local labels="$1" runners="$2"
  jq -e --argjson required "$labels" '
    any(.[]; . as $runner | .status == "online" and
      ($required | all(. as $required_label |
        ($runner.labels | index($required_label)) != null)))
  ' <<<"$runners" >/dev/null
}

default_lane="$(group_for_selector CI_LANE_TRUSTED "$default_selector")" || exit 2
untrusted_lane="$(group_for_selector CI_LANE_UNTRUSTED "$untrusted_selector")" || exit 2
if [ "$untrusted_lane" = general ]; then
  printf '## Runner admission reconciliation (%s)\n\n' "$ORG"
  printf '### Drift\n\n'
  printf '%s\n' '- `CI_LANE_UNTRUSTED` resolves to the persistent general runner lane, contrary to ADR 0235. Set it to `["ubuntu-24.04"]` or a dedicated lane classified as `untrusted`. Runner-group configuration, per-repository admission, and runner capacity were not inspected; selector and group values are redacted.'
  exit 1
fi
privileged_lane="$(group_for_selector CI_LANE_PRIVILEGED "$privileged_selector")" || exit 2

# One listing, slurped: `--paginate` emits one object per page, so streaming
# `.runner_groups[]` and slurping is the pagination-safe shape (cf. #260).
groups_ndjson="$(fetch "/orgs/$ORG/actions/runner-groups?per_page=100" \
  '.runner_groups[]')" || exit 2
groups="$(jq -sc '.' <<<"$groups_ndjson")" \
  || die_undetermined "runner group listing for $ORG was not JSON"
jq -e 'length > 0' <<<"$groups" >/dev/null \
  || die_undetermined "no runner groups returned for org $ORG"

lane_group_name() {
  case "$1" in
    general) printf '%s\n' "$GENERAL_GROUP_NAME" ;;
    untrusted)
      [ -n "$UNTRUSTED_GROUP_NAME" ] \
        || die_undetermined "no runner group is configured for lane 'untrusted'; set CI_RUNNER_UNTRUSTED_GROUP"
      printf '%s\n' "$UNTRUSTED_GROUP_NAME"
      ;;
    *) die_undetermined "no runner group is configured for lane '$1'" ;;
  esac
}

# Fail closed and name the selected lane, never the configured group-name value.
# This output is copied into a public issue, while both names come from
# organization variables.
resolve_group() {
  local lane="$1" name group
  name="$(lane_group_name "$lane")" || exit 2
  group="$(jq -c --arg name "$name" \
    'map(select(.name == $name)) | .[0] // empty' <<<"$groups")" \
    || die_undetermined "could not resolve the runner group selected by the $lane lane; configured name redacted"
  [ -n "$group" ] || die_undetermined \
    "runner group selected by the $lane lane does not exist in $ORG; configured and live group names redacted"
  printf '%s\n' "$group"
}

# Resolve only the groups a lane actually selects. A group nothing routes to is
# not this job's business and must not be able to take the run down — that is
# precisely how a deleted, unreferenced `isolated` group blinded the monitor.
general_group=""
untrusted_group=""
for lane in "$default_lane" "$untrusted_lane" "$privileged_lane"; do
  case "$lane" in
    general)
      [ -n "$general_group" ] || { general_group="$(resolve_group general)" || exit 2; } ;;
    untrusted)
      [ -n "$untrusted_group" ] || { untrusted_group="$(resolve_group untrusted)" || exit 2; } ;;
    # Hosted capacity has no runner group, so there is no admission to resolve.
    hosted) ;;
    # Total only because group_for_selector is — an invariant 80 lines away.
    # Without this arm a new lane silently resolves NO group, and the omission
    # surfaces later as an unhandled lane in the repository loop. Failing here
    # names the real cause instead of at the point of use.
    *) die_undetermined "unhandled lane '$lane' while resolving runner groups" ;;
  esac
done

# `--paginate` concatenates one response PER PAGE, so a `[...]` collector yields
# one array per page and the `jq -e` in admitted()/has_capacity() would reflect
# only the LAST page — a repository listed on page 1 would read as unadmitted
# and be reported as drift against a healthy org (same class as #260). Stream
# the elements and slurp them into a single array instead.
slurp_strings() { jq -Rsc 'split("\n") | map(select(length > 0))'; }
slurp_objects() { jq -sc '.'; }

group_id_of() {
  local group="$1" identity="$2"
  # `.id // empty` rather than `.id | tostring`: tostring turns a MISSING id into
  # the literal string "null" and exits 0, which builds `/runner-groups/null/...`
  # and only fails closed by accident when the API 404s. `// empty` produces no
  # output, so jq -e exits 4 and the guard below actually fires.
  jq -er '.id // empty' <<<"$group" \
    || die_undetermined "$identity runner group has no readable id; object contents redacted"
}

general_id=""
general_members='[]'
general_runners='[]'
if [ -n "$general_group" ]; then
  general_id="$(group_id_of "$general_group" 'general-lane')" || exit 2
  general_members="$(fetch "/orgs/$ORG/actions/runner-groups/$general_id/repositories?per_page=100" \
    '.repositories[].full_name' | slurp_strings)" || exit 2
  general_runners="$(fetch "/orgs/$ORG/actions/runner-groups/$general_id/runners?per_page=100" \
    '.runners[] | {name,status,labels:[.labels[].name]}' | slurp_objects)" || exit 2
fi

untrusted_id=""
untrusted_members='[]'
untrusted_runners='[]'
if [ -n "$untrusted_group" ]; then
  untrusted_id="$(group_id_of "$untrusted_group" 'untrusted-lane')" || exit 2
  untrusted_members="$(fetch "/orgs/$ORG/actions/runner-groups/$untrusted_id/repositories?per_page=100" \
    '.repositories[].full_name' | slurp_strings)" || exit 2
  untrusted_runners="$(fetch "/orgs/$ORG/actions/runner-groups/$untrusted_id/runners?per_page=100" \
    '.runners[] | {name,status,labels:[.labels[].name]}' | slurp_objects)" || exit 2
fi

drift=""
count=0

if jq -e 'length > 0' <<<"$unapproved_larger_runners" >/dev/null; then
  larger_runner_names="$(jq -r 'map("\(.name | @json) (id \(.id))") | join(", ")' \
    <<<"$unapproved_larger_runners")" \
    || die_undetermined "could not render unapproved larger-runner inventory"
  drift="$drift- GitHub-hosted larger runner(s) exist outside the reviewed allowlist: $larger_runner_names. Remove them, or add their exact id/name identities to \`scripts/ci-gate/hosted-larger-runner-allowlist.json\` through review before use"$'\n'
fi
if jq -e 'length > 0' <<<"$renamed_larger_runners" >/dev/null; then
  drift="$drift- live GitHub-hosted larger-runner identity differs from the reviewed id/name allowlist; rename approval requires a reviewed code change"$'\n'
fi
if jq -e 'length > 0' <<<"$stale_larger_runner_identities" >/dev/null; then
  stale_larger_runner_names="$(jq -r 'map("\(.name | @json) (id \(.id))") | join(", ")' \
    <<<"$stale_larger_runner_identities")" \
    || die_undetermined "could not render stale larger-runner identities"
  drift="$drift- reviewed GitHub-hosted larger-runner identities are absent from live inventory: $stale_larger_runner_names. Remove stale entries through review"$'\n'
fi

while IFS=$'\t' read -r repo private; do
  [ -n "$repo" ] || continue
  count=$((count + 1))
  if [ "$private" = "true" ]; then
    lane="$default_lane"
  else
    lane="$untrusted_lane"
  fi
  case "$lane" in
    general) group="$general_group"; members="$general_members"
             group_label="runner group selected by the general lane (id $general_id)" ;;
    untrusted) group="$untrusted_group"; members="$untrusted_members"
             group_label="runner group selected by the untrusted lane (id $untrusted_id)" ;;
    # GitHub-hosted: every repository is admitted, so there is nothing to check.
    hosted) group="" ;;
    # Without this, an unhandled lane silently reuses the PREVIOUS iteration's
    # group/members and misattributes drift to the wrong group.
    *) die_undetermined "unhandled lane '$lane' for repository $repo" ;;
  esac
  [ "$lane" = hosted ] || admitted "$repo" "$private" "$group" "$members" \
    || drift="$drift- \`$repo\` cannot access runner group $group_label for the selected $lane lane"$'\n'

  case "$privileged_lane" in
    general) privileged_group="$general_group"; privileged_members="$general_members"
             privileged_group_label="runner group selected by the general lane (id $general_id)" ;;
    untrusted) privileged_group="$untrusted_group"; privileged_members="$untrusted_members"
             privileged_group_label="runner group selected by the untrusted lane (id $untrusted_id)" ;;
    hosted) privileged_group="" ;;
    *) die_undetermined "unhandled privileged lane '$privileged_lane' for repository $repo" ;;
  esac
  [ "$privileged_lane" = hosted ] || admitted "$repo" "$private" "$privileged_group" "$privileged_members" \
    || drift="$drift- \`$repo\` cannot access runner group $privileged_group_label for the privileged lane"$'\n'
done <<EOF
$repos_raw
EOF

[ "$count" -gt 0 ] || die_undetermined "parsed zero repositories"

case "$default_lane" in
  general) default_runners="$general_runners" ;;
  untrusted) default_runners="$untrusted_runners" ;;
  hosted) default_runners="" ;;
  *) die_undetermined "unhandled default lane '$default_lane'" ;;
esac
case "$untrusted_lane" in
  general) untrusted_runners_for_selector="$general_runners" ;;
  untrusted) untrusted_runners_for_selector="$untrusted_runners" ;;
  hosted) untrusted_runners_for_selector="" ;;
  *) die_undetermined "unhandled untrusted lane '$untrusted_lane'" ;;
esac
case "$privileged_lane" in
  general) privileged_runners="$general_runners" ;;
  untrusted) privileged_runners="$untrusted_runners" ;;
  hosted) privileged_runners="" ;;
  *) die_undetermined "unhandled privileged lane '$privileged_lane'" ;;
esac

# A hosted lane has no self-hosted capacity to have, so asking would report
# permanent drift against a deliberately hosted configuration.
[ "$default_lane" = hosted ] || has_capacity "$default_selector" "$default_runners" \
  || drift="$drift- CI_LANE_TRUSTED has no matching online runner"$'\n'
[ "$untrusted_lane" = hosted ] || has_capacity "$untrusted_selector" "$untrusted_runners_for_selector" \
  || drift="$drift- CI_LANE_UNTRUSTED has no matching online runner"$'\n'
[ "$privileged_lane" = hosted ] || has_capacity "$privileged_selector" "$privileged_runners" \
  || drift="$drift- CI_LANE_PRIVILEGED has no matching online runner"$'\n'

# The retired pair still routes every consumer pinned to a pre-migration SHA.
# Silent divergence between it and the live lane is the state this migration
# creates, so it is reported rather than assumed harmless (#403 review).
legacy_drift() {
  local name="$1" variable="$2" lane_value="$3" value
  [ -n "$variable" ] || return 0
  value="$(jq -r '.value' <<<"$variable" 2>/dev/null)" || return 0
  [ "$value" = "$lane_value" ] || drift="$drift- \`$name\` differs from the lane that replaced it; values redacted because org-variable contents must not enter public logs or issues; consumers pinned to a pre-migration SHA route somewhere this run did not check"$'\n'
}
legacy_drift VERJSON_RUNNER_DEFAULT "$legacy_default_var" "$default_selector"
for index in "${!untrusted_alias_names[@]}"; do
  [ "$index" = 0 ] && continue
  [ "${untrusted_alias_selectors[$index]}" = "$untrusted_selector" ] \
    || drift="$drift- \`${untrusted_alias_names[$index]}\` differs from CI_LANE_UNTRUSTED; values redacted because org-variable contents must not enter public logs or issues; an immutable caller can still route to the divergent lane"$'\n'
done

# Placement (#275). The two checks above ask whether repositories are admitted
# and whether lanes have capacity. A runner registered WITHOUT `--runnergroup`
# is neither: it lands in GitHub's default group — `visibility: all`,
# `allows_public_repositories: true`, no label discipline — as capacity that no
# lane selects and no policy governs. Nothing detected that before.
#
# Resolved by `.default`, never by the id 1. The id is stable in practice, but
# pinning an id is exactly what took this job down for a week (#266), and the
# flag is what the invariant is actually about.
default_groups="$(jq -c 'map(select(.default == true))' <<<"$groups")" \
  || die_undetermined "could not search runner groups for the default group"
default_group_count="$(jq -r 'length' <<<"$default_groups")"
# Exactly one, asserted in BOTH directions. Taking `.[0]` of a multi-default
# listing would skip every other default group — a stray runner in the second
# one exits 0 with "no runner sits in the default group", which is the fail-open
# this check exists to close. Absence and multiplicity are the same kind of
# surprise and get the same answer.
[ "$default_group_count" = "1" ] || die_undetermined \
  "expected exactly one runner group in $ORG marked default, found $default_group_count; GitHub marks one and a custom group cannot become it (ADR 0003); group names redacted"
default_group="$(jq -c '.[0]' <<<"$default_groups")" \
  || die_undetermined "could not read the default runner group"

default_group_id="$(group_id_of "$default_group" default)" || exit 2
jq -e '(.name | type) == "string" and (.name | length) > 0' <<<"$default_group" >/dev/null \
  || die_undetermined "default runner group has no readable name; object contents redacted"

# Fetched unconditionally, and a failure here exits 2. Treating an unreadable
# group as empty would turn this check into a rubber stamp — the fail-open shape
# #266 left in this file.
stray_ndjson="$(fetch "/orgs/$ORG/actions/runner-groups/$default_group_id/runners?per_page=100" \
  '.runners[] | {name,status}')" || exit 2
stray_runners="$(slurp_objects <<<"$stray_ndjson")" \
  || die_undetermined "default runner group listing for $ORG was not JSON"

# Names are selected HERE rather than in the API-side `--jq`, because the test
# stub returns fixtures verbatim and cannot exercise a server-side filter: a
# mutation adding `select(.status == "online")` to that expression passed the
# whole suite. Anything this check's correctness depends on has to be evaluated
# on data the tests actually control.
#
# And no status filter, deliberately: an offline runner is still registered in
# the wrong group and rejoins the pool on its own.
# A runner object without `.name` would render as an empty string and produce an
# unactionable report ("...: . Register with..."), so name the id instead. Same
# reasoning as `group_id_of`'s `// empty`: never emit a placeholder that reads
# like real data.
strays="$(jq -c 'map(.name // "<unnamed runner id \(.id // "?")>")' <<<"$stray_runners")" \
  || die_undetermined "could not read runner names from the default group listing"

if jq -e 'length > 0' <<<"$strays" >/dev/null; then
  # Derived from the group actually read, not asserted. This text is filed as a
  # GitHub issue and an operator acts on it, so a hardcoded "admits public
  # repositories" would be a false claim about live org config the moment the
  # group is narrowed.
  default_group_traits="visibility \`$(jq -r '.visibility // "unknown"' <<<"$default_group")\`, public repositories $(jq -r 'if .allows_public_repositories == true then "allowed" elif .allows_public_repositories == false then "denied" else "unknown" end' <<<"$default_group")"
  general_destination="the runner group selected by the general lane"
  [ -z "$general_id" ] || general_destination="$general_destination (id $general_id)"
  drift="$drift- runner(s) sit in the default runner group (id $default_group_id; $default_group_traits), which no lane selects and no label discipline governs: $(jq -r 'join(", ")' <<<"$strays"). Move them into $general_destination now, and register future runners with \`--runnergroup\` — provisioning lives in verjson-cli-cloud"$'\n'
fi

printf '## Runner admission reconciliation (%s)\n\n' "$ORG"
printf 'Checked **%d** active repositories using variable-selected trusted, untrusted, and privileged lanes.\n\n' "$count"

if [ -n "$drift" ]; then
  printf '### Drift\n\n%s\n' "$drift"
  exit 1
fi

larger_runner_count="$(jq -r 'length' <<<"$larger_runners")"
printf 'No drift: variables are valid, every repository is admitted, all three lanes have online capacity, no runner sits in the default runner group (id %s), and %d reviewed GitHub-hosted larger runner(s) exactly match the repository allowlist.\n' \
  "$default_group_id" "$larger_runner_count"
