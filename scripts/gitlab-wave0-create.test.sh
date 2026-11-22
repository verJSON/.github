#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
runbook=$root/docs/infrastructure/gitlab-rehearsal-wave0.md
scratch=$(mktemp -d)
trap 'rm -r "$scratch"' EXIT
for section in 3 4; do
  awk -v heading="## $section." 'index($0,heading)==1{section=1} section && /^\x60\x60\x60sh$/{block=1;next} block && /^\x60\x60\x60$/{exit} block && !/^\. /{print}' \
    "$runbook" > "$scratch/section-$section"
  [[ -s $scratch/section-$section ]]
done
cat > "$scratch/fake" <<'FAKE'
set -euo pipefail
WORK=$1
scenario=$2
section=$3
GL_GROUP=rehearsal
GL_GROUP_MARKER='fixture marker'
REPOS=demo
pid() { cat "$WORK/$1.project.id"; }
rehearsal_group() { echo '{"id":4}'; }
gl() {
  case "$*" in
    '-X POST groups '*)
      cat >/dev/null
      echo POST >> "$WORK/writes"
      if [[ $scenario == interrupted ]]; then return 1; fi
      echo '{"id":4,"full_path":"rehearsal","visibility":"private","description":"fixture marker"}'
      if [[ $scenario == multiple-responses ]]; then echo '{"id":5}'; fi ;;
    '-X POST projects '*)
      cat >/dev/null
      echo POST >> "$WORK/writes"
      if [[ $scenario == wrong-project ]]; then
        echo '{"id":7,"path_with_namespace":"another/demo","namespace":{"id":99},"description":"unrecognized"}'
      else
        echo '{"id":7,"path_with_namespace":"rehearsal/demo","namespace":{"id":4},"visibility":"private","description":"One-way mirror of GitHub verJSON/demo. ADR 0258 wave 0. Read-only."}'
      fi ;;
    groups/4) echo '{"id":4,"full_path":"rehearsal","visibility":"private","description":"fixture marker"}' ;;
    projects/7) echo '{"id":7,"path_with_namespace":"rehearsal/demo","namespace":{"id":4},"description":"One-way mirror of GitHub verJSON/demo. ADR 0258 wave 0. Read-only.","visibility":"private","lfs_enabled":false,"shared_runners_enabled":false,"issues_access_level":"disabled","merge_requests_access_level":"disabled","wiki_access_level":"disabled","snippets_access_level":"disabled","builds_access_level":"disabled","container_registry_access_level":"disabled","package_registry_access_level":"disabled"}' ;;
    projects/7/hooks) echo '[]' ;;
    *) return 1 ;;
  esac
}
FAKE
sed -n '/^snapshot_ok() {$/,/^}$/p; /^reserve_receipt() {$/,/^}$/p; /^capture_create_id() {$/,/^}$/p' "$runbook" >> "$scratch/fake"
printf '. "$WORK/section-$section"\n' >> "$scratch/fake"
printf '[]\n' > "$scratch/pre-groups.json"
printf '[]\n' > "$scratch/pre-projects.json"
printf '23\n' > "$scratch/group.id"
if bash "$scratch/fake" "$scratch" retry 3 >/dev/null 2>&1; then
  echo 'Existing group receipt did not stop repeat POST' >&2
  exit 1
fi
[[ $(cat "$scratch/group.id") == 23 && ! -e $scratch/writes ]]

for scenario in interrupted multiple-responses preexisting group-ok wrong-project project-ok; do
  work="$scratch/$scenario"
  mkdir "$work"
  printf '[]\n' > "$work/pre-groups.json"
  printf '[]\n' > "$work/pre-projects.json"
  cp "$scratch"/section-{3,4} "$work/"
  section=3
  receipt=group.id
  case $scenario in
    preexisting) printf '[4]\n' > "$work/pre-groups.json" ;;
    wrong-project|project-ok) section=4; receipt=demo.project.id ;;
  esac
  case $scenario in
    group-ok|project-ok)
      bash "$scratch/fake" "$work" "$scenario" "$section"
      if [[ $section == 3 ]]; then expected=4; else expected=7; fi
      [[ $(cat "$work/$receipt") == "$expected" ]] ;;
    *)
      if output=$(bash "$scratch/fake" "$work" "$scenario" "$section" 2>&1); then
        echo "Expected no usable create receipt for $scenario" >&2
        exit 1
      fi
      [[ $output == STOP:*'creation outcome unverified'* && -f $work/$receipt && ! -s $work/$receipt ]]
      if output=$(bash "$scratch/fake" "$work" "$scenario" "$section" 2>&1); then exit 1; fi
      [[ $output == STOP:*'existing or unwritable create receipt'* && $(wc -l < "$work/writes") == 1 ]] ;;
  esac
done
printf 'Wave 0 create receipt tests passed\n'
