#!/usr/bin/env bash
# Guards the immutable nested dependencies in every Node workflow/setup surface
# (Verjson/.github#89, #152, #162): audited action SHAs, the complete live
# node-ci dependency graph and Renovate maintenance.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
ci="$root/.github/workflows/node-ci.yml"
release="$root/.github/workflows/node-release.yml"
composite="$root/.github/actions/setup-verjson-node/action.yml"
actions_ci="$root/.github/workflows/actions-ci.yml"
renovate="$root/renovate.json"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

checkout='actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7'
setup_node='actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7'
for wf in "$ci" "$release" "$actions_ci"; do
  name="$(basename "$wf")"
  expected_checkouts=1
  expected_setups=0
  if [ "$wf" = "$ci" ]; then
    expected_checkouts=3
    expected_setups=2
  elif [ "$wf" = "$release" ]; then
    # Prepare checks out the release; publication checks out the release and
    # contract; retention checks out its contract.
    expected_checkouts=4
    expected_setups=2
  elif [ "$wf" = "$actions_ci" ]; then
    # shell-test-groups, adr-number-collision (#983), hosted compatibility
    # (#1114), changed-path classification, and the documentation contracts.
    expected_checkouts=5
  fi
  if [ "$wf" != "$actions_ci" ]; then
    [ "$wf" = "$ci" ] || expected_setups=2
  fi
  pinned_checkouts="$(grep -cF "uses: $checkout" "$wf")"
  all_checkouts="$(grep -cE 'uses: actions/checkout@' "$wf")"
  { [ "$pinned_checkouts" -eq "$expected_checkouts" ] && [ "$all_checkouts" -eq "$expected_checkouts" ]; } \
    && pass "$name pins every checkout use to the audited v7 commit" \
    || fail "$name checkout is not pinned to the audited v7 commit"
  pinned_setups="$(grep -cF "uses: $setup_node" "$wf")"
  all_setups="$(grep -cE 'uses: actions/setup-node@' "$wf")"
  { [ "$pinned_setups" -eq "$expected_setups" ] && [ "$all_setups" -eq "$expected_setups" ]; } \
    && pass "$name pins every setup-node use to the audited v7 commit" \
    || fail "$name setup-node is not pinned to the audited v7 commit"
  grep -Eq 'uses: actions/(checkout|setup-node)@v[0-9]+' "$wf" \
    && fail "$name still contains a mutable nested action tag" \
    || pass "$name contains no mutable checkout/setup-node tag"
done

{ [ "$(grep -cF "uses: $setup_node" "$composite")" -eq 1 ] \
  && [ "$(grep -cE 'uses: actions/setup-node@' "$composite")" -eq 1 ]; } \
  && pass "setup-verjson-node pins setup-node to the audited v7 commit" \
  || fail "setup-verjson-node does not pin setup-node to the audited v7 commit"
grep -Eq 'uses: actions/setup-node@v[0-9]+' "$composite" \
  && fail "setup-verjson-node still contains a mutable setup-node tag" \
  || pass "setup-verjson-node contains no mutable setup-node tag"

grep -qF 'semantic-release' "$release" \
  && fail "node-release still derives a version through semantic-release" \
  || pass "node-release contains no semantic-release decision path"
{ grep -qF 'ref: ${{ inputs.version }}' "$release" \
  && grep -qF 'persist-credentials: false' "$release" \
  && grep -qF 'git describe --tags --exact-match HEAD' "$release"; } \
  && pass "node-release checks out and verifies only the caller-selected tag" \
  || fail "node-release is not bound to the caller-selected tag"

jq -e '
  any(.packageRules[];
    .pinDigests == true and
    (.matchManagers | index("github-actions")) != null and
    (.matchFileNames | index(".github/actions/setup-verjson-node/action.yml")) != null and
    (.matchFileNames | index(".github/workflows/actions-ci.yml")) != null and
    (.matchFileNames | index(".github/workflows/node-ci.yml")) != null and
    (.matchFileNames | index(".github/workflows/node-release.yml")) != null)
' "$renovate" >/dev/null \
  && pass "Renovate maintains every Node workflow/setup digest pin" \
  || fail "Renovate digest-pin maintenance does not cover every Node surface"

# node-ci inlines eligibility rather than calling a separately-versioned copy of
# this repository. This removes the manually-maintained self-pin from #162 and
# prevents repo-wide releases from creating a self-update/release loop (#164).
grep -qE '^[[:space:]]*uses: Verjson/\.github/\.github/actions/ci-eligibility@' "$ci" \
  && fail "node-ci still has a remote ci-eligibility self-dependency" \
  || pass "node-ci has no remote ci-eligibility self-dependency"

# Walk the dependency graph that node-ci actually executes. Every remote ref
# must be a full SHA. Any self-reference is resolved with git-show at that SHA
# and recursively scanned, so mutable code cannot hide behind an immutable
# top-level node-ci reference.
declare -A graph_seen=()
graph_error=''
ref_is_immutable() { [[ "$1" =~ ^[0-9a-f]{40}$ ]]; }

# CI checks out bounded history (#234), so a pin older than the tip is simply not
# in the object store. Fetch that one commit on demand — depth 1, no tags, no
# history walk. A non-zero return is a hard failure for the caller, never a skip:
# an object we cannot obtain is exactly what a rewritten or fabricated pin looks
# like, and the two must not be distinguishable by outcome.
fetch_pinned_commit() {
  local ref="$1"
  git -C "$root" cat-file -e "$ref^{commit}" 2>/dev/null && return 0
  git -C "$root" fetch --quiet --no-tags --depth 1 origin "$ref" >/dev/null 2>&1 || return 1
  git -C "$root" cat-file -e "$ref^{commit}" 2>/dev/null
}

resolve_self_source() {
  local ref="$1" path="$2" destination="$3" candidate object_type
  resolved_self_path=''

  fetch_pinned_commit "$ref" || return 1

  # Reusable workflows reference a file directly; composite actions reference
  # their directory, which GitHub resolves to action.yml or action.yaml.
  for candidate in "$path" "$path/action.yml" "$path/action.yaml"; do
    object_type="$(git -C "$root" cat-file -t "$ref:$candidate" 2>/dev/null || true)"
    [ "$object_type" = blob ] || continue
    if git -C "$root" show "$ref:$candidate" >"$destination" 2>/dev/null; then
      resolved_self_path="$candidate"
      return 0
    fi
  done
  return 1
}

walk_uses_graph() {
  local source="$1" identity="$2" use dependency ref nested path nested_identity
  [ -n "${graph_seen[$identity]:-}" ] && return 0
  graph_seen["$identity"]=1

  while IFS= read -r use; do
    dependency="${use%@*}"
    ref="${use##*@}"
    if [ "$dependency" = "$use" ] || ! ref_is_immutable "$ref"; then
      graph_error="$identity has mutable or missing ref: $use"
      return 1
    fi

    case "$dependency" in
      Verjson/.github/*)
        path="${dependency#Verjson/.github/}"
        nested="$(mktemp)"
        if ! resolve_self_source "$ref" "$path" "$nested"; then
          rm -f "$nested"
          graph_error="$identity cannot resolve self-reference $use"
          return 1
        fi
        nested_identity="$ref:$resolved_self_path"
        if ! walk_uses_graph "$nested" "$nested_identity"; then
          rm -f "$nested"
          return 1
        fi
        rm -f "$nested"
        ;;
    esac
  done < <(awk '
    /^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*/ {
      line = $0
      sub(/^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*/, "", line)
      sub(/[[:space:]#].*$/, "", line)
      print line
    }
  ' "$source")
}

if walk_uses_graph "$ci" "node-ci.yml"; then
  pass "node-ci's complete live dependency graph uses immutable full SHAs"
else
  fail "node-ci dependency graph is not transitively immutable: $graph_error"
fi

# Exercise the graph walker itself, not just its ref predicate. These fixtures
# pin both accepted YAML forms and prove directory-form composite actions resolve
# to their implementation file before recursion.
graph_fixtures="$(mktemp -d)"
trap 'rm -rf "$graph_fixtures"' EXIT
cat >"$graph_fixtures/list-mutable.yml" <<'YAML'
steps:
  - uses: actions/checkout@main
YAML
graph_seen=()
graph_error=''
if walk_uses_graph "$graph_fixtures/list-mutable.yml" "list-mutable.yml"; then
  fail "graph walker accepts a mutable list-form - uses dependency"
elif [[ "$graph_error" == *"actions/checkout@main"* ]]; then
  pass "graph walker rejects mutable list-form - uses dependency"
else
  fail "list-form mutable fixture failed for the wrong reason: $graph_error"
fi

cat >"$graph_fixtures/direct-mutable.yml" <<'YAML'
jobs:
  reusable:
    uses: Verjson/.github/.github/workflows/node-ci.yml@v2
YAML
graph_seen=()
graph_error=''
if walk_uses_graph "$graph_fixtures/direct-mutable.yml" "direct-mutable.yml"; then
  fail "graph walker accepts a mutable direct uses dependency"
elif [[ "$graph_error" == *"node-ci.yml@v2"* ]]; then
  pass "graph walker rejects mutable direct uses dependency"
else
  fail "direct mutable fixture failed for the wrong reason: $graph_error"
fi

self_fixture_ref="$(git -C "$root" rev-parse HEAD)"
cat >"$graph_fixtures/directory-action.yml" <<YAML
steps:
  - uses: Verjson/.github/.github/actions/setup-verjson-node@$self_fixture_ref
YAML
graph_seen=()
graph_error=''
resolved_action="$self_fixture_ref:.github/actions/setup-verjson-node/action.yml"
if walk_uses_graph "$graph_fixtures/directory-action.yml" "directory-action.yml" \
  && [ -n "${graph_seen[$resolved_action]:-}" ]; then
  pass "graph walker resolves directory-form composite action to action.yml"
else
  fail "graph walker did not scan the directory action implementation: $graph_error"
fi

# --- pins resolve from a bounded checkout (#234) ------------------------------
# actions-ci checks out at depth 1, so a pin older than the tip is not in the
# object store. Exercise the walker against a real shallow clone of a real
# origin, because the interesting cases are all git-server behaviour: an old but
# reachable commit can still be fetched by SHA, a rewritten one cannot, and the
# difference must be the difference between a pass and a FAILURE.
pins_fixture="$(mktemp -d)"
trap 'rm -rf "$graph_fixtures" "$pins_fixture"' EXIT
origin_repo="$pins_fixture/origin"
git init -q "$origin_repo"
git -C "$origin_repo" config user.name test
git -C "$origin_repo" config user.email test@example.com
# github.com serves fetch-by-SHA rather than only advertised tips; without this
# the fixture would fail every pin for a reason production does not have.
git -C "$origin_repo" config uploadpack.allowReachableSHA1InWant true
mkdir -p "$origin_repo/.github/actions/setup-verjson-node"
printf 'runs:\n  using: composite\n' \
  >"$origin_repo/.github/actions/setup-verjson-node/action.yml"
git -C "$origin_repo" add -A
git -C "$origin_repo" commit -qm 'old but valid pin target'
old_pin="$(git -C "$origin_repo" rev-parse HEAD)"
git -C "$origin_repo" commit -q --allow-empty -m 'target of a pin that was later rewritten'
rewritten_pin="$(git -C "$origin_repo" rev-parse HEAD)"
# Rewrite it away for real. Dropping the ref is not enough: a server will still
# serve an object it physically holds, so an unreferenced-but-present commit is
# resolvable and legitimately validates. The case that must fail closed is the
# pin whose object the origin can no longer produce at all.
git -C "$origin_repo" reset -q --hard HEAD~1
rm -f "$origin_repo/.git/ORIG_HEAD"
git -C "$origin_repo" reflog expire --expire=now --all
git -C "$origin_repo" gc -q --prune=now
for _ in 1 2 3; do git -C "$origin_repo" commit -q --allow-empty -m advance; done
shallow_clone="$pins_fixture/shallow"
git clone -q --depth 1 --no-tags "file://$origin_repo" "$shallow_clone"

# Point the walker at the shallow clone for one call, exactly as CI would run it.
walk_pin_in_shallow_clone() {
  local ref="$1" label="$2" saved_root="$root" fixture rc
  fixture="$pins_fixture/pin-$label.yml"
  printf 'steps:\n  - uses: Verjson/.github/.github/actions/setup-verjson-node@%s\n' \
    "$ref" >"$fixture"
  graph_seen=()
  graph_error=''
  root="$shallow_clone"
  walk_uses_graph "$fixture" "pin-$label.yml"
  rc=$?
  root="$saved_root"
  return "$rc"
}

# Preconditions, asserted rather than assumed. If the clone were not really
# shallow, or the old pin happened to be in it already, every assertion below
# would pass for the wrong reason and this whole section would prove nothing.
[ -f "$shallow_clone/.git/shallow" ] \
  && pass "the fixture checkout is genuinely shallow" \
  || fail "the fixture checkout is not shallow, so it cannot test bounded history"
git -C "$shallow_clone" cat-file -e "$old_pin^{commit}" 2>/dev/null \
  && fail "the old pin is already in the shallow checkout; the fetch path is untested" \
  || pass "the old pin is absent from the shallow checkout before resolution"

walk_pin_in_shallow_clone "$old_pin" old-but-valid \
  && pass "an old but valid pin resolves from a depth-1 checkout" \
  || fail "an old but valid pin is unresolvable at depth 1: $graph_error"

# Resolving a pin must not quietly buy back the history the bound removed.
[ -f "$shallow_clone/.git/shallow" ] \
  && pass "resolving a pin keeps the checkout bounded" \
  || fail "resolving a pin deepened the checkout to full history"

# A SHA that names nothing must fail. This is the shape of a fabricated or
# typo'd pin, and "the object is not there" is the only evidence there can be.
missing_pin=0123456789abcdef0123456789abcdef01234567
if walk_pin_in_shallow_clone "$missing_pin" missing-object; then
  fail "a pin naming a nonexistent object passed validation"
elif [[ "$graph_error" == *"cannot resolve self-reference"*"$missing_pin"* ]]; then
  pass "a pin naming a nonexistent object fails closed"
else
  fail "missing-object pin failed for the wrong reason: $graph_error"
fi

# A pin whose commit was rewritten out of the origin: well-formed, once real, now
# unobtainable. Full history made this look identical to a valid pin only because
# the objects happened to be local; from a bounded checkout it must fail closed.
if walk_pin_in_shallow_clone "$rewritten_pin" rewritten; then
  fail "a pin at a rewritten, unobtainable commit passed validation"
elif [[ "$graph_error" == *"cannot resolve self-reference"*"$rewritten_pin"* ]]; then
  pass "a pin at a rewritten, unobtainable commit fails closed"
else
  fail "rewritten pin failed for the wrong reason: $graph_error"
fi

# An offline or credential-less runner cannot fetch anything. That is a fault,
# not a verdict, and a fault must not read as a pass — the security property the
# bound is allowed to cost is none.
#
# Asserting this with $rewritten_pin would prove nothing: that pin already fails
# WITH origin present, three cases above, so deleting the remote cannot be what
# made it fail. Use objects the origin can still serve, and pin both halves —
# one resolves while origin is reachable, an equivalent one fails once it is not.
git -C "$origin_repo" commit -q --allow-empty -m 'reachable only by fetching origin'
online_pin="$(git -C "$origin_repo" rev-parse HEAD)"
git -C "$origin_repo" commit -q --allow-empty -m 'never fetched before origin disappears'
offline_pin="$(git -C "$origin_repo" rev-parse HEAD)"

for probe in "$online_pin" "$offline_pin"; do
  git -C "$shallow_clone" cat-file -e "$probe^{commit}" 2>/dev/null     && fail "an offline probe object is already local; the fetch path is untested"
done

walk_pin_in_shallow_clone "$online_pin" fetchable-with-origin   && pass "a pin absent locally resolves while origin is reachable"   || fail "the fetch path cannot obtain a live object at all: $graph_error"

git -C "$shallow_clone" remote remove origin
if walk_pin_in_shallow_clone "$offline_pin" unfetchable; then
  fail "a pin the runner could not fetch passed validation"
elif [[ "$graph_error" == *"cannot resolve self-reference"*"$offline_pin"* ]]; then
  pass "a checkout that cannot fetch fails closed instead of skipping the check"
else
  fail "unfetchable pin failed for the wrong reason: $graph_error"
fi
git -C "$shallow_clone" remote add origin "file://$origin_repo"

jq -e '
  all(.packageRules[]; .pinDigests != false)
' "$renovate" >/dev/null \
  && pass "Renovate has no exception that permits mutable Node dependencies" \
  || fail "renovate.json still contains a pinDigests:false exception"

# --- bounded checkout history (#234) -----------------------------------------
# Full history was only ever a proxy for "the pinned objects are present". The
# walker resolves co-located refs at exact SHAs, and a shallow checkout can fetch
# those SHAs on demand, so the whole graph is not the price of validating a pin.
# Assert the bound here so a future edit cannot quietly restore fetch-depth: 0.
checkout_depth="$(awk '
  /uses: actions\/checkout@/ { in_checkout = 1; next }
  in_checkout && /^[[:space:]]*-[[:space:]]/ { exit }
  in_checkout && /fetch-depth:/ {
    sub(/^.*fetch-depth:[[:space:]]*/, "")
    sub(/[[:space:]#].*$/, "")
    print
    exit
  }
' "$actions_ci")"
checkout_depth="${checkout_depth%\"}"; checkout_depth="${checkout_depth#\"}"
checkout_depth="${checkout_depth%\'}"; checkout_depth="${checkout_depth#\'}"
{ [ -n "$checkout_depth" ] && [ "$checkout_depth" != 0 ]; } \
  && pass "actions-ci checks out bounded history (fetch-depth: $checkout_depth)" \
  || fail "actions-ci checks out unbounded history (fetch-depth: ${checkout_depth:-unset})"

# --- privileged merge uses the same immutable contract rule (ADR 0085) -------
caller_gen="$root/scripts/gen-privileged-merge-caller.sh"
if [ -f "$caller_gen" ]; then
  contract_sha=848c49fd4dac307f26180acd420760a27ceff0ba
  required_checks='[{"name":"shell-tests","app_id":15368,"workflow_id":315894159,"workflow_path":".github/workflows/actions-ci.yml"}]'
  generated="$(bash "$caller_gen" "$contract_sha" "$required_checks" '["ubuntu-24.04"]' 2>/dev/null)"
  caller_ref="$(printf '%s\n' "$generated" \
    | sed -n 's/^[[:space:]]*uses:[[:space:]]*\(Verjson\/\.github\/\.github\/workflows\/ai-privileged-merge\.yml@.*\)$/\1/p')"

  [ "$caller_ref" = "Verjson/.github/.github/workflows/ai-privileged-merge.yml@$contract_sha" ] \
    && pass "privileged-merge caller pins the selected immutable contract SHA" \
    || fail "privileged-merge caller lost its canonical immutable pin: '$caller_ref'"

  printf '%s\n' "$caller_ref" | grep -E '@[0-9a-f]{40}$' >/dev/null \
    && pass "privileged-merge caller is SHA-pinned" \
    || fail "privileged-merge caller is not pinned to immutable content"

  [ -f "$root/docs/decisions/0085-immutable-privileged-caller-contract/README.md" ] \
    && pass "the privileged immutable pin is backed by a decision record" \
    || fail "ADR 0085 is missing — the privileged pin policy is unexplained"
else
  fail "scripts/gen-privileged-merge-caller.sh is missing; the caller would be hand-written"
fi

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
