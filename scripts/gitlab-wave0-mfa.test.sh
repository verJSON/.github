#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# Load the function directly from the Markdown runbook.
# shellcheck source=/dev/null
source <(sed -n '/^wave0_privileged_mfa() {$/,/^}$/p' \
  "$root/docs/infrastructure/gitlab-rehearsal-wave0.md")
declare -F wave0_privileged_mfa >/dev/null

gl() {
  case "$*" in
    user)
      if [[ $scenario == nonadmin ]]; then echo '{"id":1,"is_admin":false}'; else echo '{"id":1,"is_admin":true}'; fi ;;
    '--paginate users?admins=true&per_page=100')
      case $scenario in
        repeated-admin-page) printf '%s\n' '[{"id":1}]' '[{"id":1}]' ;;
        *) printf '%s\n' '[{"id":1}]' '[{"id":2}]' ;;
      esac
      [[ $scenario != truncated ]] ;;
    '--paginate groups?per_page=100&all_available=true')
      if [[ $scenario != empty-group-output ]]; then printf '%s\n' '[{"id":4}]' '[{"id":5}]'; fi ;;
    '--paginate groups/4/members/all?per_page=100')
      echo '[]' ;;
    '--paginate groups/5/members/all?per_page=100')
      if [[ $scenario == malformed-members ]]; then
        printf '%s\n' '[{"id":7,"access_level":30}]' '[{"id":3,"access_level":"50"}]'
      else
        printf '%s\n' '[{"id":7,"access_level":30}]' '[{"id":3,"access_level":50},{"id":6,"access_level":50}]'
      fi ;;
    users/1) echo '{"id":1,"bot":false,"two_factor_enabled":true}' ;;
    users/2)
      if [[ $scenario == admin-no-mfa ]]; then echo '{"id":2,"bot":false,"two_factor_enabled":false}'; else echo '{"id":2,"bot":false,"two_factor_enabled":true}'; fi ;;
    users/3)
      case $scenario in
        owner-no-mfa) echo '{"id":3,"bot":false,"two_factor_enabled":false,"email":"private@example.invalid"}' ;;
        malformed-user) echo '{"id":3,"bot":"true","two_factor_enabled":false}' ;;
        unreadable-user) return 1 ;;
        *) echo '{"id":3,"bot":false,"two_factor_enabled":true}' ;;
      esac ;;
    users/6) echo '{"id":6,"bot":true,"two_factor_enabled":false}' ;;
    *) printf 'Unexpected fake GitLab request: %s\n' "$*" >&2; return 1 ;;
  esac
}

scenario=healthy
wave0_privileged_mfa

for scenario in nonadmin truncated repeated-admin-page empty-group-output malformed-members \
  admin-no-mfa owner-no-mfa malformed-user unreadable-user; do
  case $scenario in
    nonadmin) expected='preflight requires a confirmed GitLab administrator' ;;
    truncated|repeated-admin-page) expected='cannot verify the complete GitLab administrator list' ;;
    empty-group-output) expected='cannot verify the complete GitLab group list' ;;
    malformed-members) expected='cannot verify the complete GitLab group membership list' ;;
    *) expected='missing or unverified two-factor authentication for a GitLab administrator or group Owner' ;;
  esac
  if output=$(wave0_privileged_mfa 2>&1); then
    echo "Expected preflight to fail for $scenario" >&2
    exit 1
  fi
  [[ $output == "STOP: $expected" ]] || {
    echo "Expected a sanitized STOP diagnostic for $scenario" >&2
    exit 1
  }
done
printf '%s\n' 'Wave 0 privileged MFA tests passed'
