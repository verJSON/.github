# GitLab estate migration: wave 0 rehearsal

This plan rehearses wave 0 of
[ADR 0258](../decisions/0258-gitlab-estate-migration/README.md). It mirrors three
low-risk repositories from GitHub into a disposable GitLab CE group, verifies refs and
history, runs CI only once the runner-isolation gate has passed, compares the results
with GitHub Actions, and then tears everything down. GitHub stays authoritative
throughout.

**Status: not executed.** No group, project, user, key, runner or mirror described here
exists yet.

- Steps marked **Requires owner-approved rehearsal** write to GitLab or GitHub. Do not
  run them until the owner has approved the rehearsal on
  [#1264](https://github.com/verJSON/.github/issues/1264), after ARB review.
- Every other step is read-only.
- **If any check fails, stop.** Never repair a failed check in place and carry on.
  - A failure in §0 to §2 ends the rehearsal there. Nothing has been created yet, so
    there is nothing to tear down: do **not** run §11.
  - From §3 on, go to §11 and tear down.

## Public-repository rules

This repository is public ([README](README.md)), so every command uses placeholders.
Never commit real values for:

- hostnames, IP addresses or SSH ports;
- tokens, keys, or where they are stored;
- host access methods;
- workstation paths;
- GitLab numeric IDs.

The real values live in the private operational record, next to the escrowed keys of
ADR 0256 §7.

| Placeholder | Meaning |
| --- | --- |
| `<gitlab-host>` | The GitLab web and API hostname, from the private service record |
| `<gitlab-ssh-host>`, `<gitlab-ssh-port>` | The GitLab SSH clone host and port |
| `<ssh-host-key-fingerprint>` | The `SHA256:` fingerprint of the GitLab SSH host's Ed25519 key, from the private service record |
| `<rehearsal-group>` | The path of the disposable top-level group. Pick a path that does not exist. |
| `<work>` | The operator's scratch directory. It must be outside every checkout, contain no whitespace, and is created with mode `0700`. Prefer a memory-backed filesystem: `shred` cannot guarantee erasure on journaling or copy-on-write filesystems, or under WSL. |
| `<tomorrow>` | Tomorrow's UTC date, `YYYY-MM-DD` |
| `<rehearsal-runner-tag>` | The tag of the rehearsal runner (§9) |
| `<verjson-ci-generator-command>`, `<verjson-ci-normalize-command>` | The caller generator and result normalizer of the pinned `verjson-ci` release (§9, §10). Neither exists yet. |
| `<github-caller-workflow>`, `<envelope-artifact>`, `<envelope-path>` | Names the generated callers define (§9, §10) |
| `<sha>`, `<run-id>`, `<job-id>` | The commit and the runs being compared (§9, §10) |

`glab` must already be logged in to `<gitlab-host>` as an administrator. For read-only
inventory and verification, `gh` must be logged in to GitHub with one of these:

- a fine-grained token with *Metadata: read*, *Contents: read*, *Webhooks: read*,
  *Secrets: read* and the read permissions named in §2.2, on the three repositories;
- or a classic token with the `repo` scope, held by a user with the admin role on the
  three repositories, used only for read operations.

Source mutations in §8.2 and §9 require a separate fine-grained writer profile for the
single approved fixture, using an owner-verified identity and a separate `GH_CONFIG_DIR`
outside `<work>`. The three-repository profile and classic fallback cannot authorize
those writes. Grant *Contents: write* for that fixture. For §9, grant *Workflows: write*
only for the reviewed caller and *Actions: write* only for the approved dispatch.
Any change to repository Actions settings stays with the source owner.

§2.2 stops with the missing permission named when a listing is refused. Neither token is
ever printed, written into `<work>`, or passed on a command line.

**Run every block as a script.** Save it to a file and run it with `bash block.sh`.
Never paste a block into an interactive shell: its `set -e` would end your session, and
its settings would stay in that shell afterwards. Every block after §1's first starts by
sourcing `<work>/env.sh` (§1).

## 0. Entry criteria

- [ ] ARB has approved ADR 0258 and this plan.
- [ ] The owner's go-ahead is recorded on #1264. It names the operator and the window,
      and says whether the CI leg (§9) is in scope.
- [ ] Run every block from the same immutable runbook commit that both forge owners
      reviewed for this window. Record the commit and verify the checkout before
      extracting or running any block.
- [ ] The window does not overlap ADR 0256's nightly quiesce window.
- [ ] The instance runs GitLab 18.11 or later (ADR 0258 §2). §2.1 checks it and stops
      otherwise.
- [ ] `required-reviewers-rehearsal` still exists. If it does not, stop: the owner names
      a replacement and ADR 0258 §3 and this plan are amended first (§8.2 needs its
      default branch and a second branch at a different commit).

No fresh backup is needed for the isolated rehearsal. GitLab writes stay inside its
new group and resources. GitHub writes, if approved, are limited to the named source
refs in §8.2 and §9, within the owner-approved window.

## 1. Operator environment (read-only)

Create the scratch directory first, so that nothing is ever written to it before it is
private:

```sh
umask 077
mkdir -p '<work>' && chmod 700 '<work>'
```

Then create `<work>/env.sh` inside it. It holds no secrets.

```sh
# <work>/env.sh: sourced by every block in this plan.
set -euo pipefail
umask 077
GL_HOST='<gitlab-host>'
GL_SSH='ssh://git@<gitlab-ssh-host>:<gitlab-ssh-port>'
GL_GROUP='<rehearsal-group>'
GH_ORG='verJSON'
WORK='<work>'
REPOS='agile-ai-manifesto demo-repository required-reviewers-rehearsal'
WAVE0_RUNBOOK_COMMIT='<reviewed-immutable-runbook-commit>'
# ssh reads UserKnownHostsFile as a space-separated list, so no quoting makes a
# whitespace path safe there.
case $WORK in *[[:space:]]*) echo 'STOP: <work> must not contain whitespace' >&2; exit 1 ;; esac
# Isolated Git: no system or user configuration, no hooks, no prompts.
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$WORK/gitconfig" GIT_TERMINAL_PROMPT=0
GL_GROUP_MARKER='ADR 0258 wave 0 rehearsal. Disposable.'
gl() { glab api --hostname "$GL_HOST" "$@"; }
pid() { cat "$WORK/$1.project.id"; }
# gl_get <path>: print the body. Exit 0 if found, 4 only on HTTP 404, 1 on anything else.
gl_get() {
  local out
  if out=$(gl "$1" 2>"$WORK/gl_get.err"); then printf '%s\n' "$out"; return 0; fi
  grep -q '(HTTP 404)' "$WORK/gl_get.err" && return 4
  cat "$WORK/gl_get.err" >&2; return 1
}
# snapshot_ok <name>: §2.1 wrote <work>/pre-<name>.json as a JSON array of ids.
snapshot_ok() {
  jq -se 'length == 1 and (.[0] | type == "array"
    and all(.[]; type == "number" and . > 0 and . == floor)
    and length == (unique | length))' "$WORK/pre-$1.json" >/dev/null 2>&1
}
# rehearsal_group_lookup: the group by §3's id when the file holds one and it answers,
# else by path. Same exit codes as gl_get.
rehearsal_group_lookup() {
  local id rc=0 g
  id=$(grep -sx '[0-9][0-9]*' "$WORK/group.id" || true)
  if [ -n "$id" ]; then
    g=$(gl_get "groups/$id") || rc=$?
    case $rc in 0) printf '%s\n' "$g"; return 0 ;; 4) ;; *) return 1 ;; esac
  fi
  gl_get "groups/$(jq -rn --arg g "$GL_GROUP" '$g | @uri')"
}
# Reserve before POST. An empty receipt records an ambiguous attempt.
reserve_receipt() {
  (set -o noclobber; : > "$1") 2>/dev/null || {
    echo 'STOP: existing or unwritable create receipt; reconcile with the owner' >&2
    return 1
  }
}
# Validate the response and exclude IDs already in the preflight snapshot.
capture_create_id() {
  local kind=$1 check=$2; shift 2
  jq -ser --slurpfile pre "$WORK/pre-$kind.json" "$@" '
    if length == 1 and (.[0] | type == "object"
      and (.id | type == "number" and . > 0 and . == floor))
      and (.[0].id as $id | $pre[0] | index($id) == null)
      and (.[0] | '"$check"')
    then .[0].id else error("unverified create response") end' 2>/dev/null
}
# A create receipt must contain one positive numeric ID. Exit 4 only if absent.
captured_id() {
  local id
  [ -e "$1" ] || return 4
  [ -f "$1" ] || return 1
  id=$(cat "$1") || return 1
  [[ $id =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "$id"
}
# Path and marker corroborate the create receipt. Neither replaces it.
rehearsal_group() {
  local g rc=0 id capture_rc=0
  snapshot_ok groups || return 1
  id=$(captured_id "$WORK/group.id") || capture_rc=$?
  [ "$capture_rc" != 1 ] || return 1
  g=$(rehearsal_group_lookup) || rc=$?
  [ "$rc" = 0 ] || return "$rc"
  [ "$capture_rc" = 0 ] || return 1
  jq -se --argjson id "$id" --arg p "$GL_GROUP" --arg m "$GL_GROUP_MARKER" \
    --slurpfile pre "$WORK/pre-groups.json" \
    'length == 1 and (.[0] | .id == $id and .full_path == $p and .description == $m)
     and ($pre[0] | index($id) == null)' <<<"$g" >/dev/null || return 1
  printf '%s\n' "$g"
}
gitlab_probe_refmap() {
  GIT_SSH_COMMAND=$(ssh_with "$1.ro.key") git ls-remote --heads --tags "$GL_SSH/$GL_GROUP/$1.git" \
    | LC_ALL=C sort -k2,2
}
assert_probe_refmap() {
  local current
  current=$(gitlab_probe_refmap "$1") || {
    echo 'FAIL: cannot read GitLab branch/tag map after probe' >&2; return 1;
  }
  [ "$current" = "$2" ] || {
    echo 'FAIL: GitLab branch/tag map changed during a refused probe' >&2; return 1;
  }
}
# Require one valid OID for the exact ref, excluding any peeled companion.
probe_ref_oid() {
  local oid
  oid=$(awk -v ref="$2" '$2 == ref { print $1 }' <<<"$1") || return 1
  [[ $oid =~ ^([0-9a-f]{40}|[0-9a-f]{64})$ ]] || return 1
  printf '%s\n' "$oid"
}
assert_target_fixture() {
  local repo=$1 project_id group group_id
  project_id=$(captured_id "$WORK/$repo.project.id") || return 1
  group=$(rehearsal_group) || return 1
  group_id=$(jq -er .id <<<"$group") || return 1
  gl_get "projects/$project_id" | jq -se --argjson id "$project_id" --argjson gid "$group_id" \
    --arg path "$GL_GROUP/$repo" \
    --arg marker "One-way mirror of GitHub verJSON/$repo. ADR 0258 wave 0. Read-only." '
    length == 1 and (.[0] | .id == $id and .namespace.id == $gid
      and .path_with_namespace == $path and .visibility == "private" and .description == $marker)' >/dev/null
}
# Switch only a source mutation script to the separate owner-approved writer.
use_source_writer() {
  local repo=$1 source_id project_id record config reader login
  [[ $WAVE0_RUNBOOK_COMMIT =~ ^[0-9a-f]{40}$ ]] || return 1
  source_id=$(captured_id "$WORK/$repo.github.id") || return 1
  project_id=$(captured_id "$WORK/$repo.project.id") || return 1
  record="$WORK/$repo.source-writer.json"
  jq -se --arg repo "$GH_ORG/$repo" --arg pin "$WAVE0_RUNBOOK_COMMIT" \
    --argjson github "$source_id" --argjson gitlab "$project_id" '
    length == 1 and (.[0] | .repository == $repo and .runbook_commit == $pin
      and .github_id == $github and .gitlab_id == $gitlab
      and (.login | type == "string" and length > 0)
      and (.gh_config_dir | type == "string" and startswith("/")))' "$record" >/dev/null \
    || { echo 'STOP: source writer record does not match approved fixture and runbook' >&2; return 1; }
  config=$(realpath -e -- "$(jq -r .gh_config_dir "$record")") || return 1
  reader=$(realpath -m -- "${GH_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/gh}") || return 1
  [ -d "$config" ] && [ "$config" != "$reader" ] || return 1
  case "$config/" in "$WORK/"*) return 1 ;; esac
  login=$(jq -r .login "$record") || return 1
  unset GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN
  export GH_CONFIG_DIR="$config"
  gh api user | jq -se --arg login "$login" 'length == 1 and .[0].login == $login' >/dev/null \
    || { echo 'STOP: source writer login does not match owner record' >&2; return 1; }
  gh api "repos/$GH_ORG/$repo" | jq -se --argjson id "$source_id" \
    'length == 1 and (.[0] | .id == $id and .private == true)' >/dev/null \
    || { echo 'STOP: source fixture identity changed' >&2; return 1; }
  assert_target_fixture "$repo" \
    || { echo 'STOP: target fixture identity changed' >&2; return 1; }
}
ssh_with() {
  printf 'ssh -F none -i %q -o IdentitiesOnly=yes -o IdentityAgent=none -o UserKnownHostsFile=%q -o StrictHostKeyChecking=yes' \
    "$WORK/$1" "$WORK/known_hosts"
}
```

Then set up the isolated Git configuration and pin the SSH host key:

```sh
. '<work>/env.sh'
git config --file "$WORK/gitconfig" core.hooksPath /dev/null
git config --file "$WORK/gitconfig" credential.https://github.com.helper '!gh auth git-credential'
git config --file "$WORK/gitconfig" user.name 'wave0 rehearsal'
git config --file "$WORK/gitconfig" user.email 'wave0@invalid.example'
# Pin exactly one Ed25519 host key, and only if it matches the private record.
ssh-keyscan -t ed25519 -p '<gitlab-ssh-port>' '<gitlab-ssh-host>' 2>/dev/null \
  | grep -v '^#' > "$WORK/known_hosts.candidate" || true
[ "$(wc -l < "$WORK/known_hosts.candidate")" = 1 ] \
  || { echo 'STOP: expected exactly one Ed25519 host key'; exit 1; }
fp=$(ssh-keygen -lf "$WORK/known_hosts.candidate" | awk '{print $2}')
[ "$fp" = '<ssh-host-key-fingerprint>' ] \
  || { echo 'STOP: the host key does not match the private record'; exit 1; }
mv "$WORK/known_hosts.candidate" "$WORK/known_hosts"
```

**Check:** exit status 0. If the private record holds the full `known_hosts` line, write
`$WORK/known_hosts` from it instead and skip the scan.

## 2. Preflight (read-only)

### 2.1 Instance preconditions (ADR 0258 §4)

```sh
. '<work>/env.sh'
fail=0
gl version | jq -e '.enterprise == false' >/dev/null || { echo 'STOP: not GitLab CE'; fail=1; }
# ADR 0258 §2: 18.11 or later. A missing or unparseable version fails closed.
gl version | jq -e '.version | capture("^(?<maj>[0-9]+)\\.(?<min>[0-9]+)\\.")
  | (.maj | tonumber) as $maj | (.min | tonumber) as $min
  | $maj > 18 or ($maj == 18 and $min >= 11)' >/dev/null 2>&1 \
  || { echo 'STOP: GitLab is older than 18.11, or its version is unreadable'; fail=1; }
gl application/settings | jq -e '
  (.signup_enabled == false or .require_admin_approval_after_user_signup == true)
  and (.allow_runner_registration_token == false)' >/dev/null \
  || { echo 'STOP: self-registration open or legacy runner registration tokens on'; fail=1; }
# Compare effective Owner access (50) with the administrator-filtered user list.
# Only print generic diagnostics. User records can include personal data.
wave0_privileged_mfa() {
  local admin_id admins groups group owners privileged id
  local id_check='def valid_id: type == "number" and . > 0 and . == floor;'
  local pages="$id_check"'
    def valid_pages:
      length > 0 and all(.[]; type == "array")
      and (add | all(.[]; type == "object" and (.id | valid_id))
        and (map(.id) | length == (unique | length)));'
  admin_id=$(gl user 2>/dev/null | jq -ser "$id_check"'
    if length == 1 and .[0].is_admin == true and (.[0].id | valid_id)
    then .[0].id else error("unconfirmed administrator") end' 2>/dev/null) \
    || { echo 'STOP: preflight requires a confirmed GitLab administrator' >&2; return 1; }
  admins=$(gl --paginate 'users?admins=true&per_page=100' 2>/dev/null \
    | jq -ser --argjson admin "$admin_id" "$pages"'
      if valid_pages and (add | any(.[]; .id == $admin))
      then add[].id else error("invalid administrator list") end' 2>/dev/null) \
    || { echo 'STOP: cannot verify the complete GitLab administrator list' >&2; return 1; }
  groups=$(gl --paginate 'groups?per_page=100&all_available=true' 2>/dev/null \
    | jq -sr "$pages"'
      if valid_pages then add[].id else error("invalid group list") end' 2>/dev/null) \
    || { echo 'STOP: cannot verify the complete GitLab group list' >&2; return 1; }
  privileged=$admins
  for group in $groups; do
    owners=$(gl --paginate "groups/$group/members/all?per_page=100" 2>/dev/null \
      | jq -sr "$pages"'
        if valid_pages and (add | all(.[]; .access_level as $access
          | [0, 5, 10, 15, 20, 25, 30, 40, 50] | index($access) != null))
        then add[] | select(.access_level == 50) | .id
        else error("invalid group membership list") end' 2>/dev/null) \
      || { echo 'STOP: cannot verify the complete GitLab group membership list' >&2; return 1; }
    privileged="$privileged $owners"
  done
  privileged=$(printf '%s\n' $privileged | sort -nu) || return 1
  for id in $privileged; do
    gl "users/$id" 2>/dev/null | jq -se --argjson id "$id" '
      length == 1 and (.[0] | .id == $id
        and (.bot == true or (.bot == false and .two_factor_enabled == true)))
    ' >/dev/null 2>&1 \
      || { echo 'STOP: missing or unverified two-factor authentication for a GitLab administrator or group Owner' >&2; return 1; }
  done
}
wave0_privileged_mfa || fail=1
gl --paginate 'groups?per_page=100&all_available=true' | jq -s -e --arg g "$GL_GROUP" \
  'add | all(.[]; .full_path != $g)' >/dev/null || { echo "STOP: $GL_GROUP already exists"; fail=1; }
# Visibility: no GitLab project may be more visible than its GitHub repository.
gl --paginate 'projects?per_page=100' | jq -r '.[] | [.path_with_namespace, .visibility] | @tsv' \
  > "$WORK/pre-gl-visibility.tsv"
while IFS=$'\t' read -r path vis; do
  name=${path##*/}
  ghvis=$(gh repo view "$GH_ORG/$name" --json visibility --jq '.visibility | ascii_downcase' 2>/dev/null || echo none)
  case "$vis:$ghvis" in
    private:none) echo "NOTE: $path has no GitHub counterpart; record its owner and purpose" ;;
    private:*|internal:public|public:public) ;;
    *) echo "STOP: $path is $vis on GitLab but $ghvis on GitHub"; fail=1 ;;
  esac
done < "$WORK/pre-gl-visibility.tsv"
# Snapshots that teardown is verified against (IDs only, no secrets).
gl --paginate 'users?per_page=100' | jq -s 'add | [.[].id] | sort' > "$WORK/pre-users.json"
gl --paginate 'runners/all?per_page=100' | jq -s 'add | [.[].id] | sort' > "$WORK/pre-runners.json"
gl --paginate 'projects?per_page=100' | jq -s 'add | [.[].id] | sort' > "$WORK/pre-projects.json"
gl --paginate 'deploy_keys?per_page=100' | jq -s 'add | [.[].id] | sort' > "$WORK/pre-deploy_keys.json"
gl --paginate 'groups?per_page=100&all_available=true' | jq -s 'add | [.[].id] | sort' > "$WORK/pre-groups.json"
exit "$fail"
```

**Check:** exit status 0. A `STOP` line is an owner action on the instance. It is not
something to work around in the rehearsal.

### 2.2 The three GitHub repositories

```sh
. '<work>/env.sh'
fail=0
# none <what> <permission> <endpoint> <jq>: passes only on a readable count of 0. A refused
# or unreadable listing stops with the permission it needs, never as "has <what>".
none() {
  local n
  n=$(gh api "$3" --jq "$4" 2>/dev/null) || { echo "STOP: cannot list $1 of $r; gh needs $2 (or admin)"; return 1; }
  case $n in
    0) ;;
    ''|*[!0-9]*) echo "STOP: unreadable $1 count for $r"; return 1 ;;
    *) echo "STOP: $r has $n $1"; return 1 ;;
  esac
}
for r in $REPOS; do
  gh api "repos/$GH_ORG/$r" --jq '[.full_name, .visibility, .archived, .default_branch] | @tsv'
  gh api "repos/$GH_ORG/$r" --jq .default_branch > "$WORK/$r.default"
  gh api "repos/$GH_ORG/$r" | jq -er '.id | select(type == "number" and . > 0 and . == floor)' \
    > "$WORK/$r.github.id"
  [ "$(gh api "repos/$GH_ORG/$r" --jq .archived)" = false ] || { echo "STOP: $r archived"; fail=1; }
  none 'webhooks' 'Webhooks: read' "repos/$GH_ORG/$r/hooks" length || fail=1
  none 'Actions secrets' 'Secrets: read' "repos/$GH_ORG/$r/actions/secrets" .total_count || fail=1
  none 'environments' 'Actions: read' "repos/$GH_ORG/$r/environments" .total_count || fail=1
  if gh api "repos/$GH_ORG/$r/contents/.gitattributes" --jq .content 2>/dev/null | base64 -d | grep -q 'filter=lfs'; then
    echo "STOP: $r uses Git LFS"; fail=1
  fi
  git ls-remote "https://github.com/$GH_ORG/$r.git" | awk '$2 ~ /^refs\/(heads|tags)\//' | sort -k2 > "$WORK/$r.pre.refs"
  echo "$r: $(wc -l < "$WORK/$r.pre.refs") head and tag refs"
done
exit "$fail"
```

**Check:** exit status 0. Keep `<repo>.pre.refs`: §11 uses it for readback.

## 3. Create the rehearsal group

**Requires owner-approved rehearsal.**

```sh
. '<work>/env.sh'
snapshot_ok groups || { echo 'STOP: invalid group snapshot'; exit 1; }
reserve_receipt "$WORK/group.id"
group_id=$(jq -n --arg g "$GL_GROUP" --arg m "$GL_GROUP_MARKER" '{name: $g, path: $g, visibility: "private",
  description: $m,
  project_creation_level: "maintainer", subgroup_creation_level: "owner",
  request_access_enabled: false}' \
  | gl -X POST groups -H 'Content-Type: application/json' --input - \
  | capture_create_id groups '.full_path == $path and .description == $marker and .visibility == "private"' \
      --arg path "$GL_GROUP" --arg marker "$GL_GROUP_MARKER") \
  || { echo 'STOP: group creation outcome unverified; preserve the empty receipt'; exit 1; }
printf '%s\n' "$group_id" > "$WORK/group.id"
rehearsal_group >/dev/null
```

## 4. Create the mirror projects

**Requires owner-approved rehearsal.** Every feature starts disabled (ADR 0258 §3).

```sh
. '<work>/env.sh'
snapshot_ok projects || { echo 'STOP: invalid project snapshot'; exit 1; }
g=$(rehearsal_group) || { echo 'STOP: cannot prove rehearsal group ownership'; exit 1; }
gid=$(jq -er .id <<<"$g")
for r in $REPOS; do
  reserve_receipt "$WORK/$r.project.id"
  project_id=$(jq -n --arg r "$r" --argjson ns "$gid" '{
    name: $r, path: $r, namespace_id: $ns, visibility: "private",
    description: "One-way mirror of GitHub verJSON/\($r). ADR 0258 wave 0. Read-only.",
    initialize_with_readme: false, lfs_enabled: false, shared_runners_enabled: false,
    issues_access_level: "disabled", merge_requests_access_level: "disabled",
    wiki_access_level: "disabled", snippets_access_level: "disabled",
    builds_access_level: "disabled", container_registry_access_level: "disabled",
    package_registry_access_level: "disabled", pages_access_level: "disabled",
    releases_access_level: "disabled", environments_access_level: "disabled"}' \
  | gl -X POST projects -H 'Content-Type: application/json' --input - \
  | capture_create_id projects '.namespace.id == $gid and .path_with_namespace == $path
      and .description == $marker and .visibility == "private"' --argjson gid "$gid" \
      --arg path "$GL_GROUP/$r" --arg marker "One-way mirror of GitHub verJSON/$r. ADR 0258 wave 0. Read-only.") \
    || { echo 'STOP: project creation outcome unverified; preserve the empty receipt'; exit 1; }
  printf '%s\n' "$project_id" > "$WORK/$r.project.id"
  gl "projects/$project_id" | jq -se --argjson id "$project_id" --argjson gid "$gid" \
    --arg path "$GL_GROUP/$r" --arg marker "One-way mirror of GitHub verJSON/$r. ADR 0258 wave 0. Read-only." '
    length == 1 and (.[0] | .id == $id and .namespace.id == $gid
    and .path_with_namespace == $path and .description == $marker
    and .visibility == "private" and .lfs_enabled == false and .shared_runners_enabled == false
    and ([.issues_access_level, .merge_requests_access_level, .wiki_access_level,
          .snippets_access_level, .builds_access_level, .container_registry_access_level,
          .package_registry_access_level] | all(. == "disabled")))' >/dev/null \
    || { echo "STOP: $r settings did not read back as requested"; exit 1; }
  [ "$(gl "projects/$(pid "$r")/hooks" | jq length)" = 0 ] || { echo "STOP: $r has hooks"; exit 1; }
done
```

## 5. Mirror service account, deploy keys and protections

**Requires owner-approved rehearsal.** GitLab gives a deploy key's creator access to the
protected branch the key may push. So the write keys are created by a dedicated,
non-administrator group service account in the rehearsal group (ADR 0258 §2), which
cannot sign in through the UI and has no password. It gets a one-day personal access
token for this one step. The token lives only in a shell variable: it is never written
to a file, printed, or passed on a command line. An `EXIT` trap, installed before the
token is minted, revokes every active token of the service account however the block
ends. Never run this block with `set -x`. glab's debug output (`GLAB_DEBUG`,
`GLAB_DEBUG_HTTP`, or `debug` in its configuration) would print the mint response, token
included; the block switches both variables off, and the configuration must be off too.

Deploy keys in `allowed_to_push` and `allowed_to_create` (§5.5) are Free only from
GitLab 18.10, and service accounts only from 18.11; §2.1 has already refused an older
instance.

```sh
. '<work>/env.sh'
gid=$(cat "$WORK/group.id")
# 5.1 The service account: no password, no UI sign-in, Maintainer on the three projects only.
jq -n '{name: "verJSON mirror (wave 0 rehearsal)"}' \
| gl -X POST "groups/$gid/service_accounts" -H 'Content-Type: application/json' --input - \
| jq -er '.id' > "$WORK/sa.id"
sa=$(cat "$WORK/sa.id")
sa_tokens="groups/$gid/service_accounts/$sa/personal_access_tokens"
for r in $REPOS; do
  gl -X POST "projects/$(pid "$r")/members" -F user_id="$sa" -F access_level=40 >/dev/null
done

# 5.2 Key pairs: one write key and one read-only key per project.
for r in $REPOS; do
  ssh-keygen -q -t ed25519 -N '' -C "wave0-mirror-$r" -f "$WORK/$r.key"
  ssh-keygen -q -t ed25519 -N '' -C "wave0-readonly-$r" -f "$WORK/$r.ro.key"
done

# 5.3 Write keys, created as the service account. No glab debug output from here on.
export GLAB_DEBUG=false GLAB_DEBUG_HTTP=false
hdr=''
revoke_sa_tokens() {
  hdr=''
  gl "$sa_tokens" | jq -r '.[] | select(.active) | .id' \
  | while read -r t; do gl -X DELETE "$sa_tokens/$t" >/dev/null; done
}
# Installed before the token exists, so no failure below can leave it active.
trap 'revoke_sa_tokens || echo "WARN: revoke the service account tokens by hand (§11)" >&2' EXIT
hdr=$(jq -n --arg d '<tomorrow>' '{name: "wave0-deploy-keys", scopes: ["api"], expires_at: $d}' \
  | gl -X POST "$sa_tokens" -H 'Content-Type: application/json' --input - \
  | jq -er '.token | select(type == "string" and length > 0) | "PRIVATE-TOKEN: " + .')
for r in $REPOS; do
  body=$(jq -n --arg t "wave0-mirror-$r" --rawfile k "$WORK/$r.key.pub" \
    '{title: $t, key: ($k | rtrimstr("\n")), can_push: true}')
  # The header reaches curl on a pipe from a shell builtin, never in argv or a file.
  printf '%s\n' "$hdr" \
  | curl -q --fail-with-body -sS -H @- -H 'Content-Type: application/json' \
      -X POST --data "$body" "https://$GL_HOST/api/v4/projects/$(pid "$r")/deploy_keys" \
  | jq -er .id > "$WORK/$r.key.id"
done
revoke_sa_tokens
trap - EXIT

# 5.4 Read-only keys, for verification (§7) and the negative test (§8.1).
for r in $REPOS; do
  jq -n --arg t "wave0-readonly-$r" --rawfile k "$WORK/$r.ro.key.pub" \
    '{title: $t, key: ($k | rtrimstr("\n")), can_push: false}' \
  | gl -X POST "projects/$(pid "$r")/deploy_keys" -H 'Content-Type: application/json' --input - \
  | jq -er .id > "$WORK/$r.ro.key.id"
done

# 5.5 Protections, created before the first push. Exact default-branch name only, so no
#     wildcard can widen it (most-permissive-rule semantics).
for r in $REPOS; do
  kid=$(cat "$WORK/$r.key.id"); def=$(cat "$WORK/$r.default")
  jq -n --arg n "$def" --argjson k "$kid" '{name: $n, push_access_level: 0,
    merge_access_level: 0, allow_force_push: false, allowed_to_push: [{deploy_key_id: $k}]}' \
  | gl -X POST "projects/$(pid "$r")/protected_branches" -H 'Content-Type: application/json' --input - >/dev/null
  jq -n --argjson k "$kid" '{name: "*", create_access_level: 0, allowed_to_create: [{deploy_key_id: $k}]}' \
  | gl -X POST "projects/$(pid "$r")/protected_tags" -H 'Content-Type: application/json' --input - >/dev/null
done
```

**Read back** (read-only):

```sh
. '<work>/env.sh'
gid=$(cat "$WORK/group.id"); sa=$(cat "$WORK/sa.id")
gl "users/$sa" | jq -e '.is_admin == false' >/dev/null
gl "groups/$gid/service_accounts/$sa/personal_access_tokens" | jq -e '[.[] | select(.active)] | length == 0' >/dev/null \
  || { echo 'STOP: the service account still has an active token'; exit 1; }
for r in $REPOS; do
  p=$(pid "$r"); kid=$(cat "$WORK/$r.key.id"); def=$(cat "$WORK/$r.default")
  gl "projects/$p/members/all" | jq -e --argjson b "$sa" \
    'all(.[]; .id == $b or .access_level >= 50 or .access_level <= 20)' >/dev/null \
    || { echo "STOP: $r has a Developer or Maintainer other than the service account"; exit 1; }
  gl "projects/$p/deploy_keys" | jq -e --argjson k "$kid" \
    'length == 2 and ([.[] | select(.can_push)] | length == 1 and .[0].id == $k)' >/dev/null
  gl "projects/$p/protected_branches" | jq -e --arg n "$def" --argjson k "$kid" '
    length == 1 and .[0].name == $n and .[0].allow_force_push == false
    and all(.[0].merge_access_levels[]; .access_level == 0)
    and all(.[0].push_access_levels[]; .deploy_key_id == $k or (.deploy_key_id == null and .access_level == 0))
    and any(.[0].push_access_levels[]; .deploy_key_id == $k)' >/dev/null
  gl "projects/$p/protected_tags" | jq -e --argjson k "$kid" '
    length == 1 and .[0].name == "*"
    and all(.[0].create_access_levels[]; .deploy_key_id == $k or (.deploy_key_id == null and .access_level == 0))
    and any(.[0].create_access_levels[]; .deploy_key_id == $k)' >/dev/null
done
```

Group Owners count as 50 and are the break-glass administrators. Reporters are 20.

## 6. Initial sync

**Requires owner-approved rehearsal.** Save this as `<work>/sync.sh`. It is ADR 0258
§2's ref policy, run by an attended operator. From wave 1 on, the policy lives in the
canonical `verjson-ci` mirror kit, never in a copy of this script.

```sh
#!/usr/bin/env bash
# Usage: bash <work>/sync.sh <repository>. Exit 3 means the policy refused the sync.
. '<work>/env.sh'
r=$1
def=$(cat "$WORK/$r.default")
bare="$WORK/$r.git"
gh_url="https://github.com/$GH_ORG/$r.git"
gl_url="$GL_SSH/$GL_GROUP/$r.git"
export GIT_SSH_COMMAND; GIT_SSH_COMMAND=$(ssh_with "$r.key")
[ -d "$bare" ] || git init --bare -q "$bare"
# What GitHub has now, staged outside the mirrored namespaces. Never refs/pull/*.
git -C "$bare" fetch -q --prune --no-tags "$gh_url" \
  '+refs/heads/*:refs/incoming/heads/*' '+refs/tags/*:refs/incoming/tags/*'
# What GitLab has now.
git -C "$bare" fetch -q --prune --no-tags "$gl_url" \
  '+refs/heads/*:refs/gitlab/heads/*' '+refs/tags/*:refs/gitlab/tags/*'
# Policy: tags never move or disappear; the default branch only fast-forwards.
fail=0
while read -r oid ref; do
  t=${ref#refs/gitlab/tags/}
  now=$(git -C "$bare" rev-parse -q --verify "refs/incoming/tags/$t" || true)
  [ "$now" = "$oid" ] || { echo "REFUSE $r: tag $t moved or was deleted on GitHub" >&2; fail=1; }
done < <(git -C "$bare" for-each-ref --format='%(objectname) %(refname)' refs/gitlab/tags/)
new=$(git -C "$bare" rev-parse -q --verify "refs/incoming/heads/$def") \
  || { echo "REFUSE $r: default branch $def is missing on GitHub" >&2; exit 3; }
old=$(git -C "$bare" rev-parse -q --verify "refs/gitlab/heads/$def" || true)
if [ -n "$old" ] && ! git -C "$bare" merge-base --is-ancestor "$old" "$new"; then
  echo "REFUSE $r: $def on GitHub is not a fast-forward of GitLab's" >&2; fail=1
fi
[ "$fail" = 0 ] || exit 3
# Branches, including deletions, in one atomic push. Then tags: never forced or pruned.
git -C "$bare" push -q --atomic --prune "$gl_url" '+refs/incoming/heads/*:refs/heads/*'
if [ -n "$(git -C "$bare" for-each-ref refs/incoming/tags/)" ]; then
  git -C "$bare" push -q --atomic "$gl_url" 'refs/incoming/tags/*:refs/tags/*'
fi
echo "SYNCED $r $def=$new"
```

If GitLab answers that it does not support atomic pushes, stop and record it. Do not drop
`--atomic`.

Run it for each repository, then set and check the default branch:

```sh
. '<work>/env.sh'
for r in $REPOS; do
  bash "$WORK/sync.sh" "$r"
  gl -X PUT "projects/$(pid "$r")" -f default_branch="$(cat "$WORK/$r.default")" >/dev/null
  gl "projects/$(pid "$r")" | jq -e --arg d "$(cat "$WORK/$r.default")" '.default_branch == $d' >/dev/null
  gl "projects/$(pid "$r")/protected_branches" | jq -e 'length == 1' >/dev/null \
    || { echo "STOP: $r gained an extra branch rule on first push"; exit 1; }
done
```

## 7. Verify refs and history (read-only)

Save this as `<work>/verify.sh`. It reads GitLab with the read-only key.

```sh
#!/usr/bin/env bash
# Usage: bash <work>/verify.sh <repository>. Exit 0 only if every check passes.
. '<work>/env.sh'
r=$1; v="$WORK/verify-$r"; rm -rf "$v"; mkdir -p "$v"
export GIT_SSH_COMMAND; GIT_SSH_COMMAND=$(ssh_with "$r.ro.key")
declare -A url=([github]="https://github.com/$GH_ORG/$r.git" [gitlab]="$GL_SSH/$GL_GROUP/$r.git")
for side in github gitlab; do
  git ls-remote "${url[$side]}" | awk '$2 ~ /^refs\/(heads|tags)\//' | sort -k2 > "$v/$side.refs"
  git init --bare -q "$v/$side.git"
  git -C "$v/$side.git" fetch -q --no-tags "${url[$side]}" '+refs/heads/*:refs/heads/*' '+refs/tags/*:refs/tags/*'
  git -C "$v/$side.git" rev-list --objects --all | cut -d' ' -f1 | sort > "$v/$side.objects"
  # The verdict is compared, not the messages: their order follows each side's packing.
  if git -C "$v/$side.git" fsck --strict --no-dangling > "$v/$side.fsck.log" 2>&1; then
    echo pass > "$v/$side.fsck"; else echo fail > "$v/$side.fsck"; fi
done
fail=0
diff -u "$v/github.refs" "$v/gitlab.refs" || { echo "FAIL $r: refs differ"; fail=1; }
cmp -s "$v/github.objects" "$v/gitlab.objects" || { echo "FAIL $r: object sets differ"; fail=1; }
cmp -s "$v/github.fsck" "$v/gitlab.fsck" || { echo "FAIL $r: fsck verdicts differ"; fail=1; }
[ "$(cat "$v/github.fsck")" = pass ] || { echo "HOLD $r: fsck fails on GitHub's copy (ADR 0258 §4)"; fail=1; }
printf '%s refs=%s objects=%s objects-sha256=%s fsck=%s\n' "$r" \
  "$(wc -l < "$v/github.refs")" "$(wc -l < "$v/github.objects")" \
  "$(sha256sum < "$v/github.objects" | cut -d' ' -f1)" "$(cat "$v/github.fsck")"
exit "$fail"
```

```sh
. '<work>/env.sh'
for r in $REPOS; do bash "$WORK/verify.sh" "$r"; done
```

**Pass:**

- no `FAIL` or `HOLD` lines;
- the ref diff is empty, including peeled `^{}` tag lines;
- the object lists are byte-identical;
- `git fsck --strict` passes on both sides.

A `HOLD` means GitHub's own copy fails `fsck`. That is not a pass even when GitLab
matches. Hold that repository for an owner decision (ADR 0258 §4). Record each printed
line in the receipt (§12).

## 8. Negative tests and incremental sync

Before any probe, both forge owners must approve one attended writer window and record
the exact isolated source repository and target project IDs in the private rehearsal
record. Keep other writers and automation out of both repositories for that window.
Confirm that source ref changes cannot run an unintended workflow, integration or webhook,
or publish artifacts or notifications. If either owner cannot establish isolation, skip
the probes and record the hold. Verify and reconcile all residue before ending the window.

### 8.1 Writes that must be rejected

**Requires owner-approved rehearsal.** These attempt writes, and each must fail for the
stated reason.

```sh
. '<work>/env.sh'
for r in $REPOS; do
  bare="$WORK/$r.git"; def=$(cat "$WORK/$r.default"); gl_url="$GL_SSH/$GL_GROUP/$r.git"
  assert_target_fixture "$r" || { echo 'STOP: target fixture identity changed'; exit 1; }
  before=$(gitlab_probe_refmap "$r")
  default_ref="refs/heads/$def"
  default_oid=$(probe_ref_oid "$before" "$default_ref") \
    || { echo 'STOP: target default branch OID is missing or invalid'; exit 1; }
  if awk '$2 == "refs/heads/wave0-negative" { found = 1 } END { exit !found }' <<<"$before"; then
    echo 'STOP: target negative-test ref already exists'; exit 1
  fi
  # A root commit is never a fast-forward of the default branch.
  stray=$(git -C "$bare" commit-tree -m 'wave0 negative test' "refs/incoming/heads/$def^{tree}")
  out=$(GIT_SSH_COMMAND=$(ssh_with "$r.key") git -C "$bare" push \
    --force-with-lease="$default_ref:$default_oid" "$gl_url" "$stray:$default_ref" 2>&1) \
    && { echo "FAIL $r: non-fast-forward push to $def was accepted"; exit 1; }
  grep -qi 'protected branch' <<<"$out" || { echo "FAIL $r: rejected for another reason: $out"; exit 1; }
  assert_probe_refmap "$r" "$before"
  assert_target_fixture "$r" || { echo 'STOP: target fixture identity changed'; exit 1; }
  out=$(GIT_SSH_COMMAND=$(ssh_with "$r.ro.key") git -C "$bare" push \
    --force-with-lease=refs/heads/wave0-negative: "$gl_url" "$stray:refs/heads/wave0-negative" 2>&1) \
    && { echo "FAIL $r: read-only key pushed"; exit 1; }
  grep -qi 'write access' <<<"$out" || { echo "FAIL $r: rejected for another reason: $out"; exit 1; }
  assert_probe_refmap "$r" "$before"
  echo "PASS $r: both writes rejected for the expected reason"
done
```

None of the three repositories has a tag before §8.2. The tag-deletion test therefore
runs there.

### 8.2 Incremental sync probes in `required-reviewers-rehearsal`

**Requires explicit source-owner and target-owner approval.** The source operator needs
repository-scoped GitHub Contents write permission for exactly the approved private
throwaway fixture. The allowed source mutations are create and delete of
`refs/heads/wave0-probe` and create, move and delete of `refs/tags/wave0-probe`.
Use the fixture's approved operator identity, not a broader automation credential.
Record the owner-authorized writer after verifying both fixture identities. The private
`$WORK/<repository>.source-writer.json` holds `repository` (owner/name), `github_id`
and `gitlab_id` (numbers), `runbook_commit`, `login` and the absolute `gh_config_dir`
of the separate writer profile. The owner records the profile's exact allowed scopes
and confirms its authentication independently. The file carries no credential values.
Creation refuses an occupied name. Subsequent Git pushes use the exact previous OID
as a lease. A missing response or failed lease stops the probe for owner reconciliation.

```sh
. '<work>/env.sh'
# The probe repository and its two branches. Change these only together with ADR 0258 §3.
R=required-reviewers-rehearsal
use_source_writer "$R" || { echo 'STOP: approved source writer unavailable'; exit 1; }
base_branch=$(cat "$WORK/$R.default")   # the default branch, from §2.2
second_branch=t5-readme                 # any other branch whose head differs
source="repos/$GH_ORG/$R"
api="$source/git"
base=$(gh api "$api/ref/heads/$base_branch" --jq .object.sha)
other=$(git ls-remote "https://github.com/$GH_ORG/$R.git" "refs/heads/$second_branch" | cut -f1)
[ -n "$other" ] && [ "$other" != "$base" ] || { echo 'STOP: no second commit to probe with'; exit 1; }

# Each sync and each verification is its own command, so set -e stops on either.
# a. A branch is created on GitHub, then deleted. GitLab follows both.
gh api -X POST "$api/refs" -f ref=refs/heads/wave0-probe -f sha="$base" \
  | jq -er --arg oid "$base" 'select(.ref == "refs/heads/wave0-probe" and .object.sha == $oid) | .object.sha' \
  > "$WORK/wave0-probe.branch.oid"
bash "$WORK/sync.sh" "$R"
bash "$WORK/verify.sh" "$R"
git -C "$WORK/$R.git" push --force-with-lease="refs/heads/wave0-probe:$base" \
  "https://github.com/$GH_ORG/$R.git" :refs/heads/wave0-probe
bash "$WORK/sync.sh" "$R"
bash "$WORK/verify.sh" "$R"

# b. A tag is created on GitHub. GitLab gets it, and it is protected there.
gh api -X POST "$api/refs" -f ref=refs/tags/wave0-probe -f sha="$base" \
  | jq -er --arg oid "$base" 'select(.ref == "refs/tags/wave0-probe" and .object.sha == $oid) | .object.sha' \
  > "$WORK/wave0-probe.tag.oid"
bash "$WORK/sync.sh" "$R"
bash "$WORK/verify.sh" "$R"
before=$(gitlab_probe_refmap "$R")
target_tag_oid=$(probe_ref_oid "$before" refs/tags/wave0-probe) \
  || { echo 'STOP: target probe tag OID is missing or invalid'; exit 1; }
[ "$target_tag_oid" = "$base" ] || { echo 'STOP: target probe tag OID changed'; exit 1; }
assert_target_fixture "$R" || { echo 'STOP: target fixture identity changed'; exit 1; }
out=$(GIT_SSH_COMMAND=$(ssh_with "$R.key") git -C "$WORK/$R.git" push \
  --force-with-lease="refs/tags/wave0-probe:$target_tag_oid" "$GL_SSH/$GL_GROUP/$R.git" :refs/tags/wave0-probe 2>&1) \
  && { echo 'FAIL: the deploy key deleted a protected tag'; exit 1; }
grep -qi 'protected' <<<"$out" || { echo "FAIL: rejected for another reason: $out"; exit 1; }

assert_probe_refmap "$R" "$before"

# c. Move, then delete the source tag. Each attempt must leave every target ref intact.
git -C "$WORK/$R.git" push --force-with-lease="refs/tags/wave0-probe:$base" \
  "https://github.com/$GH_ORG/$R.git" "$other:refs/tags/wave0-probe"
printf '%s\n' "$other" > "$WORK/wave0-probe.tag.oid"
rc=0; bash "$WORK/sync.sh" "$R" || rc=$?
[ "$rc" = 3 ] || { echo "FAIL: expected moved-tag refusal (exit 3), got exit $rc"; exit 1; }
assert_probe_refmap "$R" "$before"

git -C "$WORK/$R.git" push --force-with-lease="refs/tags/wave0-probe:$other" \
  "https://github.com/$GH_ORG/$R.git" :refs/tags/wave0-probe
rc=0; bash "$WORK/sync.sh" "$R" || rc=$?
[ "$rc" = 3 ] || { echo "FAIL: expected missing-tag refusal (exit 3), got exit $rc"; exit 1; }
assert_probe_refmap "$R" "$before"
echo 'PASS: branch create/delete followed; tag protected; moved and missing tags refused'
```

The wave 0 sync is attended, so it measures how long a sync takes, not mirror lag. Lag
is first measured in wave 1, against the scheduled worker.

## 9. CI leg: blocked until both gates pass

**Requires owner-approved rehearsal.** Do not start until every box is ticked:

- [ ] The dedicated wave 0 runner has posted plain-job admission evidence, and the
      owner has accepted it (ADR 0258 §5, wave 0 exception). Only plain, credential-free
      test jobs run; any image, candidate or OCI build job still needs the full
      verjson-git-runners#208 evidence.
- [ ] `verjson-ci` has released, at an immutable version, the generator for the GitLab
      caller (verjson-ci#30). Until it exists, this section cannot run, and no caller is
      written by hand.
- [ ] The owner has decided whether Actions is enabled on `demo-repository` for the
      comparison window (#1869).
- [ ] Both forge owners approved the optional leg, its exact fixture IDs, isolated
      writer profile, branch names and intended workflow effects under §8. The
      approvals name the reviewed runbook commit and apply to this window.
- [ ] The runner owner (verjson-git-runners) has registered a runner for
      `<rehearsal-group>` only. It runs protected refs only, carries the tag
      `<rehearsal-runner-tag>`, is unprivileged with narrow egress, and is the runner whose admission evidence the
      first box accepted. Registration goes
      through `POST /user/runners`, never a legacy registration token. Before calling it,
      the rehearsal operator runs `reserve_receipt "$WORK/runner.id"`. The runner owner
      records the exact validated ID from the response, bound to the approved group and
      manager deployment in the private record. An ambiguous response leaves the receipt
      empty and blocks teardown until the owner reconciles the attempt.

Save this source push block as `<work>/ci-source-push.sh`. Pass the exact branch name
and the approved local checkout of the prepared commit. Do not bypass this block
with a direct push. The private source-writer record follows §8.2 for
`demo-repository`.

```sh
#!/usr/bin/env bash
. '<work>/env.sh'
branch=$1
case $branch in rehearsal/ci-parity|rehearsal/ci-inject-failure) ;; *) exit 1 ;; esac
R=demo-repository
use_source_writer "$R" || { echo 'STOP: approved source writer unavailable'; exit 1; }
ref="refs/heads/$branch"
oid=$(git -C "$2" rev-parse --verify 'HEAD^{commit}')
receipt="$WORK/${branch##*/}.source.oid"
reserve_receipt "$receipt"
git -C "$2" push --force-with-lease="$ref:" "https://github.com/$GH_ORG/$R.git" "$oid:$ref"
printf '%s\n' "$oid" > "$receipt"
```

**Steps:**

1. **Generate the callers.** On a new GitHub branch `rehearsal/ci-parity` of
   `demo-repository`, run `<verjson-ci-generator-command>` from that pinned release for
   the GitHub and GitLab callers of the repository's portable, credentialless workload
   (`proof-html`). Push with the block above. Do not merge it.
2. **Admit the branch on GitLab.** Protect `rehearsal/*` on the GitLab project, pushable
   only by the deploy key, as in §5.5, so the protected-only runner admits it. Set
   `builds_access_level` to `enabled`. If the generator writes the caller somewhere other
   than `.gitlab-ci.yml`, set `ci_config_path` to that path.
3. **Run the GitLab leg.** Run `sync.sh demo-repository`. The mirror push creates the
   pipeline. Record it with
   `gl "projects/$(pid demo-repository)/pipelines?sha=<sha>"`.
4. **Run the GitHub leg.** In a dedicated script that sources `env.sh` and successfully
   calls `use_source_writer demo-repository`, run
   `gh workflow run <github-caller-workflow> --repo verJSON/demo-repository --ref rehearsal/ci-parity`,
   then `gh run watch`.
5. **Inject a failure.** Use the same source push block for
   `rehearsal/ci-inject-failure`, with one commit that breaks the declared workload,
   such as a broken link in `index.html`.
   Repeat steps 3 and 4 for it.

## 10. Compare GitLab with GitHub Actions (read-only)

Apply ADR 0162's conformance contract to each pair of runs:

- **Same commit.** GitHub's `gh run view <run-id> --json headSha` equals the GitLab
  pipeline's `sha`.
- **Same image.** Both result envelopes name the same OCI digest.
- **Same outcome.** Fetch both envelopes:
  - `gh run download <run-id> -n <envelope-artifact> -D "$WORK/ci/github"`;
  - `gl "projects/$(pid demo-repository)/jobs/<job-id>/artifacts/<envelope-path>"`.

  Normalize them with the `verjson-ci` release's own normalizer
  (`<verjson-ci-normalize-command>`), never a hand-written one. The normalized envelopes
  must be identical.
- **No silent skips.** Every expected job reached a terminal state on both sides.
- **The injected failure fails on both sides** with the same semantic outcome.
- **Record without gating:** duration and queue time on each side. These feed ADR 0161's
  cost ledger.

Wave 0 does not test the status bridge's forgery defenses, because no bridge exists yet.
ADR 0161 phase 2 owns that.

## 11. Teardown

**Requires owner-approved rehearsal.** Run only after §3 has started and keep the group
quiescent under the owner's control. Every deletion requires the exact ID captured from
§3, §4 or §5 and matching current metadata. Snapshots exclude pre-existing IDs. An empty
or malformed receipt, an unrecognized occupant or an unreadable inventory stops teardown.
Do not repair a missing receipt by copying an ID from the current inventory.

Teardown reports GitHub probe refs without deleting them. Reconcile residue with the
owner using the successful operation's expected OID and an explicit Git lease. Leave a
ref alone if either ownership or the expected OID is uncertain.

Group deletion is delayed. A writer outside this rehearsal could add an object after
the final inventory. If readback finds that the scheduled group contains any project,
service account, group runner or descendant, restore it (`POST /groups/:id/restore`)
and investigate.

```sh
. '<work>/env.sh'
stop() { echo "STOP: $*" >&2; exit 1; }
# Accept a genuine empty page. Reject missing pages, malformed or duplicate IDs, and
# failing requests (env.sh enables pipefail).
teardown_list() {
  gl --paginate "$1" 2>/dev/null | jq -sc '
    if length > 0 and all(.[]; type == "array")
      and (add | all(.[]; type == "object" and (.id | type == "number" and . > 0 and . == floor))
        and (map(.id) | length == (unique | length)))
    then add else error("unreadable inventory") end' 2>/dev/null
}
receipt() {
  local id rc=0
  id=$(captured_id "$WORK/$1") || rc=$?
  case $rc in
    0) jq -e --argjson id "$id" 'index($id) == null' "$WORK/pre-$2.json" >/dev/null \
         || stop 'create receipt conflicts with preflight'
       printf '%s\n' "$id" ;;
    4) ;;
    *) stop 'create receipt is empty or invalid' ;;
  esac
}
# A group listing can omit a moved or pending-deletion project. Confirm absent
# captured projects by ID rather than assuming the parent listing was sufficient.
confirm_omitted_projects() {
  local repo id rc
  for repo in $REPOS; do
    id=$(receipt "$repo.project.id" projects) || exit 1
    [ -n "$id" ] || continue
    if jq -e --argjson id "$id" 'any(.[]; .id == $id)' <<<"$1" >/dev/null; then continue; fi
    rc=0; gl_get "projects/$id" >/dev/null 2>&1 || rc=$?
    [ "$rc" = 4 ] || stop 'captured project omitted from inventory is not confirmed absent'
  done
}
project_matches() {
  jq -se --argjson p "$2" --argjson g "$gid" --arg path "$GL_GROUP/$1" \
    --arg marker "One-way mirror of GitHub verJSON/$1. ADR 0258 wave 0. Read-only." '
    length == 1 and (.[0] | .id == $p and .namespace.id == $g
      and .path_with_namespace == $path and .description == $marker)' >/dev/null
}
confirm_captured_account_absent() {
  local rc=0
  [ -n "$sa" ] || return 0
  gl_get "users/$sa" >/dev/null 2>&1 || rc=$?
  [ "$rc" = 4 ] || stop 'captured service account is not confirmed absent'
}
gl user | jq -e '.is_admin == true' >/dev/null || stop 'GitLab administrator required'
for snap in groups projects users deploy_keys runners; do
  snapshot_ok "$snap" || stop "missing or invalid pre-$snap.json"
done
for r in $REPOS; do
  receipt "$r.project.id" projects >/dev/null || exit 1
  receipt "$r.key.id" deploy_keys >/dev/null || exit 1
  receipt "$r.ro.key.id" deploy_keys >/dev/null || exit 1
done
sa=$(receipt sa.id users) || exit 1
runner_id=$(receipt runner.id runners) || exit 1
if [ -n "$runner_id" ]; then
  removed_id=$(captured_id "$WORK/runner.removed.id") \
    || stop 'recorded runner requires attested owner cleanup before teardown'
  [ "$removed_id" = "$runner_id" ] || stop 'owner cleanup receipt names a different runner'
  rc=0; gl_get "runners/$runner_id" >/dev/null 2>&1 || rc=$?
  [ "$rc" = 4 ] || stop 'recorded runner requires attested owner cleanup before teardown'
fi
rc=0; g=$(rehearsal_group) || rc=$?
case $rc in
  0) gid=$(jq -r .id <<<"$g") ;;
  4) gid=''; confirm_captured_account_absent; echo 'NOTE: no GitLab rehearsal group to remove' ;;
  *) stop 'cannot prove the group matches its create receipt and marker' ;;
esac
if [ -n "$gid" ]; then
  projects_path="groups/$gid/projects?with_shared=true&include_subgroups=true&per_page=100"
  descendants_path="groups/$gid/descendant_groups?all_available=true&per_page=100"
  accounts_path="groups/$gid/service_accounts?per_page=100"
  runners_path="groups/$gid/runners?type=group_type&per_page=100"
  runners=$(teardown_list "$runners_path") || stop 'cannot enumerate group runners'
  [ "$runners" = '[]' ] || stop 'group runner requires owner cleanup'
  projects=$(teardown_list "$projects_path") || stop 'cannot enumerate projects'
  descendants=$(teardown_list "$descendants_path") || stop 'cannot enumerate descendants'
  accounts=$(teardown_list "$accounts_path") || stop 'cannot enumerate service accounts'
  [ "$descendants" = '[]' ] || stop 'rehearsal group has descendants'
  confirm_omitted_projects "$projects"
  jq -e --arg sa "$sa" 'all(.[]; (.id | tostring) == $sa
    and .name == "verJSON mirror (wave 0 rehearsal)")' <<<"$accounts" >/dev/null \
    || stop 'unrecognized service account'
  if [ "$accounts" = '[]' ]; then confirm_captured_account_absent; fi
  project_ids=() key_routes=() project_names=()
  for p in $(jq -r '.[].id' <<<"$projects"); do
    repo=''
    for r in $REPOS; do
      [ "$(receipt "$r.project.id" projects)" != "$p" ] || { repo=$r; break; }
    done
    [ -n "$repo" ] || stop 'project has no matching create receipt'
    jq -c --argjson p "$p" '.[] | select(.id == $p)' <<<"$projects" \
      | project_matches "$repo" "$p" || stop 'project path, namespace or marker changed'
    project_runners=$(teardown_list "projects/$p/runners?type=project_type&per_page=100") \
      || stop 'cannot enumerate project runners'
    [ "$project_runners" = '[]' ] || stop 'project runner requires owner cleanup'
    project_accounts=$(teardown_list "projects/$p/service_accounts?per_page=100") \
      || stop 'cannot enumerate project service accounts'
    [ "$project_accounts" = '[]' ] || stop 'project has unrecognized service accounts'
    keys=$(teardown_list "projects/$p/deploy_keys?per_page=100") || stop 'cannot enumerate deploy keys'
    for k in $(jq -r '.[].id' <<<"$keys"); do
      if [ "$(receipt "$repo.key.id" deploy_keys)" = "$k" ]; then
        key_file=$repo.key.pub; title=wave0-mirror-$repo; push=true
      elif [ "$(receipt "$repo.ro.key.id" deploy_keys)" = "$k" ]; then
        key_file=$repo.ro.key.pub; title=wave0-readonly-$repo; push=false
      else
        stop 'deploy key has no matching create receipt'
      fi
      material=$(awk 'NR == 1 && NF >= 2 { print $1 " " $2 }' "$WORK/$key_file") \
        || stop 'cannot read the recorded public deploy key'
      [ -n "$material" ] || stop 'missing recorded public deploy key'
      jq -e --argjson k "$k" --arg t "$title" --arg m "$material" --argjson push "$push" '
        any(.[]; .id == $k and .title == $t and .can_push == $push
          and (.key | split(" ") | .[:2] | join(" ")) == $m)' <<<"$keys" >/dev/null \
        || stop 'deploy key metadata or public key changed'
      key_routes+=("projects/$p/deploy_keys/$k")
    done
    project_ids+=("$p"); project_names+=("$repo")
  done

  # Authorize the complete inventory before the first write.
  for route in "${key_routes[@]}"; do gl -X DELETE "$route" >/dev/null || stop 'deploy key removal failed'; done
  for i in "${!project_ids[@]}"; do
    p=${project_ids[i]}; r=${project_names[i]}
    gl -X DELETE "projects/$p" >/dev/null || stop 'project removal failed'
    rc=0; info=$(gl_get "projects/$p" 2>/dev/null) || rc=$?
    case $rc in
      4) ;;
      0) project_matches "$r" "$p" <<<"$info" || stop 'project identity changed during removal'
         if jq -e '(.marked_for_deletion_on // .marked_for_deletion_at) != null' <<<"$info" >/dev/null; then
           path=$(jq -er '.path_with_namespace | @uri' <<<"$info") || stop 'project path is unreadable'
           gl -X DELETE "projects/$p?permanently_remove=true&full_path=$path" >/dev/null \
             || stop 'permanent project removal failed'
         fi ;;
      *) stop 'cannot determine project deletion outcome' ;;
    esac
  done
  if [ "$accounts" != '[]' ]; then
    gl -X DELETE "groups/$gid/service_accounts/$sa?hard_delete=true" >/dev/null \
      || stop 'service account removal failed'
  fi
  rehearsal_group >/dev/null || stop 'group ownership changed during cleanup'
  for endpoint in "$projects_path" "$descendants_path" "$accounts_path" "$runners_path"; do
    remaining=$(teardown_list "$endpoint") || stop 'cannot verify the group is empty'
    [ "$remaining" = '[]' ] || stop 'the group is not empty and was not deleted'
  done
  confirm_omitted_projects '[]'
  confirm_captured_account_absent
  gl -X DELETE "groups/$gid" >/dev/null || stop 'group removal failed'
fi

refs=$(git ls-remote --refs "https://github.com/$GH_ORG/required-reviewers-rehearsal.git" \
  refs/heads/wave0-probe refs/tags/wave0-probe) || stop 'cannot inspect GitHub probe refs'
[ -z "$refs" ] || stop 'GitHub probe refs left untouched for attended reconciliation'
```

Before automated teardown, if a runner was registered, the runner owner removes only
the attested runner and manager deployment after checking the recorded ID, scope and
identity against the approved receipt. Confirm the ID was absent from
`$WORK/pre-runners.json`. Confirm absence through the runner API and the
host manager readback. The owner records the result privately and the operator puts
that exact ID in `$WORK/runner.removed.id` only after both checks succeed. An unknown
runner or missing receipt needs owner reconciliation. Teardown never deletes a runner.

After teardown, by hand:

- If §9 ran:
  - reconcile only the exact source branches created by §9, using each recorded
    `<branch-basename>.source.oid` as an explicit deletion lease and the same
    approved writer profile and fixture ID. Leave unrecorded, empty or changed
    refs alone and report their state;
  - put `demo-repository`'s Actions setting back to its preflight state.
- Remove the local material:
  `find '<work>' -type f -name '*.key' -exec shred -u {} +`, then `rm -rf '<work>'`.
  Do this only after the readback below has passed. `shred` is best effort: it cannot
  guarantee erasure on journaling or copy-on-write filesystems, or under WSL, which is
  why `<work>` should be memory-backed. The keys are revoked on GitLab either way.

**Read back** (read-only). Run it before removing `<work>`.

```sh
. '<work>/env.sh'
fail=0
for r in $REPOS; do
  git ls-remote "https://github.com/$GH_ORG/$r.git" | awk '$2 ~ /^refs\/(heads|tags)\//' | sort -k2 \
    | diff -u "$WORK/$r.pre.refs" - || { echo "FAIL: GitHub refs of $r differ from preflight"; fail=1; }
done
for kind in users runners/all projects deploy_keys; do
  f=$WORK/pre-${kind%%/*}.json
  gl --paginate "$kind?per_page=100" | jq -s 'add | [.[].id] | sort' | diff -u "$f" - \
    || { echo "FAIL: $kind differ from preflight"; fail=1; }
done
# The group, looked up as teardown does, must be gone (HTTP 404 only), or be scheduled
# for deletion and empty: no project, service account, group runner or descendant group. GitLab
# documents `marked_for_deletion_on` on groups for Premium and Ultimate only, so a group
# that the `active=true` listing ("not archived and not marked for deletion") omits, and
# that is not archived, also counts as scheduled. Unverified on CE: which signal it
# returns. A non-empty scheduled group means the §11 race happened: restore and
# investigate.
rc=0; g=$(rehearsal_group) || rc=$?
case $rc in
  4) ;;
  0) gid=$(jq -r .id <<<"$g")
     active=$(gl --paginate 'groups?active=true&all_available=true&per_page=100' | jq -s '[(add // [])[].id]') \
       || { echo 'FAIL: cannot list active groups'; fail=1; active=''; }
     archived=$(gl --paginate 'groups?archived=true&all_available=true&per_page=100' | jq -s '[(add // [])[].id]') \
       || { echo 'FAIL: cannot list archived groups'; fail=1; archived=''; }
     [ -z "$active" ] || [ -z "$archived" ] || jq -e --argjson a "$active" --argjson z "$archived" '
       .marked_for_deletion_on != null
       or (.archived != true and (.id as $i | ($a | index($i) | not) and ($z | index($i) | not)))' \
       <<<"$g" >/dev/null \
       || { echo "FAIL: $GL_GROUP exists and is not scheduled for deletion (or is only archived)"; fail=1; }
     n=$(gl --paginate "groups/$gid/projects?with_shared=true&include_subgroups=true&per_page=100" \
       | jq -se 'if length > 0 and all(.[]; type == "array") then add | length else error("missing pages") end') && [ "$n" = 0 ] \
       || { echo "FAIL: $GL_GROUP still holds projects, or they cannot be listed"; fail=1; }
     n=$(gl --paginate "groups/$gid/runners?type=group_type&per_page=100" \
       | jq -se 'if length > 0 and all(.[]; type == "array") then add | length else error("missing pages") end') \
       && [ "$n" = 0 ] \
       || { echo "FAIL: $GL_GROUP still holds runners, or they cannot be listed"; fail=1; }
     n=$(gl --paginate "groups/$gid/service_accounts?per_page=100" \
       | jq -se 'if length > 0 and all(.[]; type == "array") then add | length else error("missing pages") end') \
       && [ "$n" = 0 ] \
       || { echo "FAIL: $GL_GROUP still holds service accounts, or they cannot be listed"; fail=1; }
     n=$(gl --paginate "groups/$gid/descendant_groups?all_available=true&per_page=100" \
       | jq -se 'if length > 0 and all(.[]; type == "array") then add | length else error("missing pages") end') && [ "$n" = 0 ] \
       || { echo "FAIL: $GL_GROUP has descendant groups, or they cannot be listed"; fail=1; } ;;
  *) echo "FAIL: cannot read whether $GL_GROUP is gone"; fail=1 ;;
esac
exit "$fail"
```

GitLab deletes users in a background job. If only the service account still shows in
`users`, wait a minute and run the readback again.

Teardown does not remove the rehearsal projects from backups already taken. Under
ADR 0256 they persist for up to 400 days (ADR 0258 §8).

## 12. Receipt

Post this on #1264. Leave out hostnames, IDs, keys, fingerprints and paths.

```text
ADR 0258 wave 0 rehearsal: <PASS | FAIL | PARTIAL>
Date (UTC): <date>   Operator: <github-login>   ADR 0258 commit: <sha>
Owner approval: <link>   CI leg: <ran | blocked: #208 | blocked: generator>
Per repository (from §7): <repo> refs=<n> objects=<n> objects-sha256=<hash> fsck=<pass|fail>
Negative tests (§8.1): <pass | fail with reason>
Probes (§8.2): branch create/delete <pass>; tag protected <pass>; moved and missing tags refused <pass>
CI comparison (§10): <same commit, digest and normalized outcome; injected failure | n/a>
Teardown readback (§11): <pass | fail>
Deviations from this plan: <none | list>
```
