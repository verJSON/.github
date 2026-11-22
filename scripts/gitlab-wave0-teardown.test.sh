#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
runbook=$root/docs/infrastructure/gitlab-rehearsal-wave0.md
scratch=$(mktemp -d)
trap 'rm -r "$scratch"' EXIT
awk '/^## 11\. Teardown/{section=1} section && /^\x60\x60\x60sh$/{block=1; next} block && /^\x60\x60\x60$/{exit} block && !/^\. / {print}' "$runbook" > "$scratch/teardown"
[[ -s $scratch/teardown ]]
for snap in groups projects users deploy_keys runners; do printf '[]\n' > "$scratch/pre-$snap.json"; done
printf '4\n' > "$scratch/group.id"
printf '7\n' > "$scratch/demo.project.id"
printf '8\n' > "$scratch/demo.key.id"
printf '9\n' > "$scratch/sa.id"
printf 'ssh-ed25519 ABC fixture\n' > "$scratch/demo.key.pub"
cat > "$scratch/fake" <<'FAKE'
set -euo pipefail
WORK=$1
scenario=$2
GL_GROUP=rehearsal
GL_GROUP_MARKER=fixture-marker
GH_ORG=fixture
REPOS=demo
snapshot_ok() { jq -e 'type == "array"' "$WORK/pre-$1.json" >/dev/null; }
rehearsal_group_lookup() {
  if [[ $scenario == missing-group-live-account ]]; then return 4; fi
  gl_get groups/4
}
gl_get() {
  case $1 in
    groups/4)
      if [[ $scenario == mismatched-group ]]; then
        echo '{"id":99,"full_path":"rehearsal","description":"fixture-marker"}'
      else echo '{"id":4,"full_path":"rehearsal","description":"fixture-marker"}'; fi ;;
    projects/7)
      if [[ $scenario == omitted-project ]]; then
        echo '{"id":7,"namespace":{"id":4},"path_with_namespace":"rehearsal/demo","description":"One-way mirror of GitHub verJSON/demo. ADR 0258 wave 0. Read-only."}'
      else return 4; fi ;;
    runners/88)
      if [[ $scenario == recorded-runner-present ]]; then echo '{"id":88}'; else return 4; fi ;;
    users/9)
      case $scenario in
        omitted-account|missing-group-live-account|account-still-present) echo '{"id":9}' ;;
        unreadable-account) return 1 ;;
        *) return 4 ;;
      esac ;;
    *) return 4 ;;
  esac
}
gl() {
  if [[ $1 == -X ]]; then
    printf '%s\n' "$3" >> "$WORK/writes"
    if [[ $scenario == interrupted ]]; then return 1; fi
    return 0
  fi
  case "$*" in
    user) echo '{"is_admin":true}' ;;
    '--paginate groups/4/projects?'*)
      if [[ -f $WORK/writes && $scenario != remaining-project ]]; then echo '[]'; return; fi
      case $scenario in
        omitted-project) echo '[]' ;;
        unknown-project) echo '[{"id":99,"namespace":{"id":4},"path_with_namespace":"rehearsal/demo","description":"One-way mirror of GitHub verJSON/demo. ADR 0258 wave 0. Read-only."}]' ;;
        bad-project-marker) echo '[{"id":7,"namespace":{"id":4},"path_with_namespace":"rehearsal/demo","description":"different"}]' ;;
        *) echo '[{"id":7,"namespace":{"id":4},"path_with_namespace":"rehearsal/demo","description":"One-way mirror of GitHub verJSON/demo. ADR 0258 wave 0. Read-only."}]' ;;
      esac ;;
    '--paginate projects/'*'/deploy_keys?'*)
      if [[ $scenario == unknown-key ]]; then echo '[{"id":99,"title":"wave0-mirror-demo","key":"ssh-ed25519 ABC","can_push":true}]'
      elif [[ $scenario != failed-key-list ]]; then echo '[{"id":8,"title":"wave0-mirror-demo","key":"ssh-ed25519 ABC","can_push":true}]'
      else echo '[]'; return 1; fi ;;
    '--paginate groups/4/runners?'*)
      if [[ $scenario == unknown-group-runner ]]; then echo '[{"id":88}]'; else echo '[]'; fi ;;
    '--paginate projects/7/runners?'*)
      if [[ $scenario == unknown-project-runner ]]; then echo '[{"id":88}]'; else echo '[]'; fi ;;
    '--paginate projects/7/service_accounts?'*)
      if [[ $scenario == project-account ]]; then echo '[{"id":51}]'; else echo '[]'; fi ;;
    '--paginate groups/4/service_accounts?'*)
      if [[ $scenario == omitted-account || $scenario == unreadable-account ]]; then echo '[]'
      elif [[ $scenario == unknown-account ]]; then echo '[{"id":99,"name":"verJSON mirror (wave 0 rehearsal)"}]'
      elif grep -q '^groups/4/service_accounts/' "$WORK/writes" 2>/dev/null; then echo '[]'
      else echo '[{"id":9,"name":"verJSON mirror (wave 0 rehearsal)"}]'; fi ;;
    '--paginate groups/4/descendant_groups?'*)
      if [[ $scenario == descendant ]]; then echo '[{"id":10}]'; else echo '[]'; fi ;;
    *) return 1 ;;
  esac
}
gh() { echo 'Unexpected GitHub API call' >&2; return 99; }
git() {
  [[ $1 == ls-remote ]] || { echo 'Unexpected Git write' >&2; return 99; }
  if [[ $scenario == probe-ref ]]; then printf 'fixtureoid\trefs/heads/wave0-probe\n'; fi
}
FAKE
sed -n '/^captured_id() {$/,/^}$/p; /^rehearsal_group() {$/,/^}$/p' "$runbook" >> "$scratch/fake"
printf '. "$WORK/teardown"\n' >> "$scratch/fake"

for scenario in unknown-project bad-project-marker unknown-key failed-key-list unknown-account \
  descendant project-account omitted-project mismatched-group unknown-group-runner unknown-project-runner \
  omitted-account unreadable-account missing-group-live-account \
  empty-receipt recorded-runner-unattested recorded-runner-present; do
  case $scenario in
    unknown-project) expected='project has no matching create receipt' ;;
    bad-project-marker) expected='project path, namespace or marker changed' ;;
    unknown-key) expected='deploy key has no matching create receipt' ;;
    failed-key-list) expected='cannot enumerate deploy keys' ;;
    unknown-account) expected='unrecognized service account' ;;
    descendant) expected='rehearsal group has descendants' ;;
    project-account) expected='project has unrecognized service accounts' ;;
    omitted-project) expected='captured project omitted from inventory is not confirmed absent' ;;
    mismatched-group) expected='cannot prove the group matches its create receipt and marker' ;;
    unknown-group-runner) expected='group runner requires owner cleanup' ;;
    unknown-project-runner) expected='project runner requires owner cleanup' ;;
    omitted-account|unreadable-account|missing-group-live-account)
      expected='captured service account is not confirmed absent' ;;
    empty-receipt) : > "$scratch/demo.project.id"; expected='create receipt is empty or invalid' ;;
    recorded-runner-unattested|recorded-runner-present)
      printf '88\n' > "$scratch/runner.id"
      if [[ $scenario == recorded-runner-present ]]; then printf '88\n' > "$scratch/runner.removed.id"; fi
      expected='recorded runner requires attested owner cleanup before teardown' ;;
  esac
  if output=$(bash "$scratch/fake" "$scratch" "$scenario" 2>&1); then
    echo "Expected teardown to stop for $scenario" >&2
    exit 1
  fi
  if [[ -s $scratch/writes ]]; then
    echo "Unsafe delete attempted for $scenario" >&2
    exit 1
  fi
  [[ $output == "STOP: $expected" ]]
  printf '7\n' > "$scratch/demo.project.id"
done
if output=$(bash "$scratch/fake" "$scratch" interrupted 2>&1); then exit 1; fi
[[ $output == 'STOP: deploy key removal failed' ]]
diff -u <(printf '%s\n' projects/7/deploy_keys/8) "$scratch/writes"
mv "$scratch/writes" "$scratch/interrupted-writes"
if output=$(bash "$scratch/fake" "$scratch" account-still-present 2>&1); then exit 1; fi
[[ $output == 'STOP: captured service account is not confirmed absent' ]]
diff -u <(printf '%s\n' projects/7/deploy_keys/8 projects/7 'groups/4/service_accounts/9?hard_delete=true') "$scratch/writes"
mv "$scratch/writes" "$scratch/account-still-present-writes"
if output=$(bash "$scratch/fake" "$scratch" remaining-project 2>&1); then exit 1; fi
[[ $output == 'STOP: the group is not empty and was not deleted' ]]
! grep -qx 'groups/4' "$scratch/writes"
mv "$scratch/writes" "$scratch/partial-writes"
if output=$(bash "$scratch/fake" "$scratch" probe-ref 2>&1); then exit 1; fi
[[ $output == 'STOP: GitHub probe refs left untouched for attended reconciliation' ]]
diff -u <(printf '%s\n' projects/7/deploy_keys/8 projects/7 'groups/4/service_accounts/9?hard_delete=true' groups/4) "$scratch/writes"
mv "$scratch/writes" "$scratch/probe-writes"
bash "$scratch/fake" "$scratch" recorded-runner-removed
diff -u "$scratch/probe-writes" "$scratch/writes"
printf 'Wave 0 teardown ownership tests passed\n'
