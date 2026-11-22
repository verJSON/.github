#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
runbook=$root/docs/infrastructure/gitlab-rehearsal-wave0.md
source <(sed -n '/^gitlab_probe_refmap() {$/,/^}$/p; /^assert_probe_refmap() {$/,/^}$/p; /^captured_id() {$/,/^}$/p; /^assert_target_fixture() {$/,/^}$/p; /^use_source_writer() {$/,/^}$/p' "$runbook")
GL_SSH=fixture
GL_GROUP=rehearsal
ssh_with() { printf 'fixture-read-only'; }
git() {
  [[ $GIT_SSH_COMMAND == fixture-read-only ]]
  [[ $* == 'ls-remote --heads --tags fixture/rehearsal/demo.git' ]]
  case $scenario in
    unchanged) printf 'base\trefs/heads/main\nother\trefs/heads/unrelated\ntag\trefs/tags/probe\npeeled\trefs/tags/probe^{}\n' ;;
    changed) printf 'base\trefs/heads/main\nnew\trefs/heads/unrelated\ntag\trefs/tags/probe\npeeled\trefs/tags/probe^{}\n' ;;
    unreadable) return 1 ;;
  esac
}
scenario=unchanged
before=$(gitlab_probe_refmap demo)
assert_probe_refmap demo "$before"
scenario=changed
if output=$(assert_probe_refmap demo "$before" 2>&1); then
  echo 'Expected unrelated target ref drift to fail the negative probe' >&2
  exit 1
fi
[[ $output == 'FAIL: GitLab branch/tag map changed during a refused probe' ]]
scenario=unreadable
if output=$(assert_probe_refmap demo "$before" 2>&1); then exit 1; fi
[[ $output == 'FAIL: cannot read GitLab branch/tag map after probe' ]]
printf 'Wave 0 probe ref-map tests passed\n'

scratch=$(mktemp -d)
trap 'rm -r "$scratch"' EXIT
WORK=$scratch/work
mkdir "$WORK"
GH_ORG=fixture
WAVE0_RUNBOOK_COMMIT=0123456789abcdef0123456789abcdef01234567
mkdir "$scratch/writer" "$scratch/reader"
export GH_CONFIG_DIR="$scratch/reader" GH_TOKEN=fixture-fallback
printf '7\n' > "$WORK/demo.github.id"
printf '8\n' > "$WORK/demo.project.id"
printf '4\n' > "$WORK/group.id"
jq -n --arg config "$scratch/writer" --arg pin "$WAVE0_RUNBOOK_COMMIT" \
  '{repository:"fixture/demo", github_id:7, gitlab_id:8, runbook_commit:$pin,
    login:"fixture-writer", gh_config_dir:$config}' > "$WORK/demo.source-writer.json"
gh() {
  [[ $GH_CONFIG_DIR == "$scratch/writer" && ! ${GH_TOKEN+x} ]] || return 1
  case "$*" in
    'api user') echo '{"login":"fixture-writer"}' ;;
    'api repos/fixture/demo')
      if [[ $scenario == wrong-source ]]; then echo '{"id":99,"private":true}'; else echo '{"id":7,"private":true}'; fi ;;
    *) return 1 ;;
  esac
}
rehearsal_group() { echo '{"id":4}'; }
gl_get() {
  [[ $1 == projects/8 ]] || return 1
  if [[ $scenario == wrong-target ]]; then echo '{"id":8,"namespace":{"id":4},"path_with_namespace":"other/demo","visibility":"private","description":"One-way mirror of GitHub verJSON/demo. ADR 0258 wave 0. Read-only."}'
  else echo '{"id":8,"namespace":{"id":4},"path_with_namespace":"rehearsal/demo","visibility":"private","description":"One-way mirror of GitHub verJSON/demo. ADR 0258 wave 0. Read-only."}'; fi
}
scenario=valid
(use_source_writer demo)
for scenario in wrong-source wrong-target; do
  if output=$(use_source_writer demo 2>&1); then exit 1; fi
  [[ $output == "STOP: $([[ $scenario == wrong-source ]] && echo source || echo target) fixture identity changed" ]]
done
printf 'Wave 0 scoped source writer tests passed\n'

awk '/^## 9\./{section=1} section && /^\x60\x60\x60sh$/{block=1;next} block && /^\x60\x60\x60$/{exit} block && !/^\. /{print}' "$runbook" > "$scratch/ci-push"
[[ -s $scratch/ci-push ]]
cat > "$scratch/ci-fake" <<'FAKE'
set -euo pipefail
WORK=$1; scenario=$2; shift 2
GH_ORG=fixture
use_source_writer() { [[ $1 == demo-repository ]]; }
git() {
  case $* in
    '-C checkout rev-parse --verify HEAD^{commit}') echo fixture-commit ;;
    '-C checkout push --force-with-lease=refs/heads/rehearsal/'*)
      ref=$4
      [[ $ref == --force-with-lease=refs/heads/rehearsal/*: && $5 == https://github.com/fixture/demo-repository.git ]]
      expected=${ref#--force-with-lease=}; expected=${expected%:}
      [[ $6 == "fixture-commit:$expected" ]]
      printf '%s\n' "$expected" >> "$WORK/source-writes"
      [[ $scenario != lost-response ]] ;;
    *) return 1 ;;
  esac
}
FAKE
sed -n '/^reserve_receipt() {$/,/^}$/p' "$runbook" >> "$scratch/ci-fake"
printf '. "%s/ci-push"\n' "$scratch" >> "$scratch/ci-fake"
bash "$scratch/ci-fake" "$WORK" accepted rehearsal/ci-parity checkout
[[ $(cat "$WORK/ci-parity.source.oid") == fixture-commit ]]
if bash "$scratch/ci-fake" "$WORK" lost-response rehearsal/ci-inject-failure checkout; then exit 1; fi
[[ -f $WORK/ci-inject-failure.source.oid && ! -s $WORK/ci-inject-failure.source.oid ]]
if bash "$scratch/ci-fake" "$WORK" retry rehearsal/ci-inject-failure checkout 2>/dev/null; then exit 1; fi
diff -u <(printf '%s\n' refs/heads/rehearsal/ci-parity refs/heads/rehearsal/ci-inject-failure) "$WORK/source-writes"
printf 'Wave 0 optional CI source receipt tests passed\n'

awk '/^### 8\.1 /{section=1} section && /^\x60\x60\x60sh$/{block=1;next} block && /^\x60\x60\x60$/{exit} block && !/^\. /{print}' "$runbook" > "$scratch/target-branch"
awk '/^# b\. A tag is created on GitHub/{block=1} /^# c\. Move/{exit} block{print}' "$runbook" > "$scratch/target-tag"
[[ -s $scratch/target-branch && -s $scratch/target-tag ]]
printf 'main\n' > "$WORK/demo.default"
printf ':\n' > "$WORK/sync.sh"
printf ':\n' > "$WORK/verify.sh"
cat > "$scratch/target-fake" <<'FAKE'
set -euo pipefail
WORK=$1; scenario=$2; snippet=$3
REPOS=demo
R=demo
GL_GROUP=rehearsal
GL_SSH=fixture
api=fixture-api
base=1111111111111111111111111111111111111111
branch_oid=2222222222222222222222222222222222222222
ssh_with() { printf 'fixture-%s' "$1"; }
rehearsal_group() { echo '{"id":4}'; }
gl_get() {
  [[ $1 == projects/8 ]] || return 1
  if [[ $scenario == wrong-target ]]; then
    echo '{"id":8,"namespace":{"id":99},"path_with_namespace":"rehearsal/demo","visibility":"private","description":"different"}'
  else
    echo '{"id":8,"namespace":{"id":4},"path_with_namespace":"rehearsal/demo","visibility":"private","description":"One-way mirror of GitHub verJSON/demo. ADR 0258 wave 0. Read-only."}'
  fi
}
gh() {
  [[ $snippet == *target-tag ]] || return 99
  printf '{"ref":"refs/tags/wave0-probe","object":{"sha":"%s"}}\n' "$base"
}
git() {
  case $* in
    'ls-remote --heads --tags fixture/rehearsal/demo.git')
      if [[ $scenario == malformed-oid ]]; then printf 'bad\trefs/heads/main\n'
      else printf '%s\trefs/heads/main\n' "$branch_oid"; fi
      if [[ $scenario == occupied ]]; then printf '%s\trefs/heads/wave0-negative\n' "$base"; fi
      if [[ $scenario == wrong-tag ]]; then printf '%s\trefs/tags/wave0-probe\n' "$branch_oid"
      else printf '%s\trefs/tags/wave0-probe\n' "$base"; fi ;;
    *' commit-tree '*) printf '%s\n' "$base" ;;
    *' push '*)
      printf '%s\n' "$*" >> "$WORK/target-writes"
      case $* in
        *:refs/tags/wave0-probe*) echo 'protected tag' >&2 ;;
        *:refs/heads/wave0-negative*) echo 'write access denied' >&2 ;;
        *) echo 'protected branch' >&2 ;;
      esac
      return 1 ;;
    *) return 99 ;;
  esac
}
FAKE
sed -n '/^captured_id() {$/,/^}$/p; /^gitlab_probe_refmap() {$/,/^}$/p; /^assert_probe_refmap() {$/,/^}$/p; /^assert_target_fixture() {$/,/^}$/p; /^probe_ref_oid() {$/,/^}$/p' "$runbook" >> "$scratch/target-fake"
printf '. "$snippet"\n' >> "$scratch/target-fake"
for scenario in wrong-target malformed-oid occupied wrong-tag; do
  snippet=$scratch/target-branch
  if [[ $scenario == wrong-tag ]]; then snippet=$scratch/target-tag; fi
  if output=$(bash "$scratch/target-fake" "$WORK" "$scenario" "$snippet" 2>&1); then
    echo "Unsafe accepted target probe for $scenario" >&2
    exit 1
  fi
  [[ ! -e $WORK/target-writes && $output == STOP:* ]]
done
bash "$scratch/target-fake" "$WORK" denied "$scratch/target-branch"
bash "$scratch/target-fake" "$WORK" denied "$scratch/target-tag"
grep -q -- '--force-with-lease=refs/heads/main:2222222222222222222222222222222222222222 ' "$WORK/target-writes"
grep -q -- '--force-with-lease=refs/heads/wave0-negative: ' "$WORK/target-writes"
grep -q -- '--force-with-lease=refs/tags/wave0-probe:1111111111111111111111111111111111111111 ' "$WORK/target-writes"
[[ $(wc -l < "$WORK/target-writes") == 3 ]]
printf 'Wave 0 target checks and negative probe lease tests passed\n'
