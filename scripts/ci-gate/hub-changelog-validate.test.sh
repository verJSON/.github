#!/usr/bin/env bash
# The hub publishes the changelog contract but sits in the `core-checks-actions`
# ruleset cohort, not `changelog-contract-required`, so `changelog / validate`
# never runs on its own pull requests (Verjson/.github#1425). A fragment missing
# `impact:` merged green on #1418 for exactly that reason, and the snapshot it
# would have been consumed into is immutable.
#
# The remedy is that the producer exercises its own contract at the source
# rather than adopting its own consumer packaging: this wrapper runs
# `scripts/changelog.py validate --base <base> --head HEAD` from the hub's own
# `platform` CI group. These assertions are what keep that wrapper honest.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
gate="$here/hub-changelog-validate.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
# GitHub exports the PR's base branch; fixtures use their own `main` refs.
unset GITHUB_BASE_REF
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

# A fixture repository with a `main` holding one complete fragment, so every
# case below differs from its base only in what the branch adds. The contract
# engine under test is the hub's own `scripts/changelog.py`, which the wrapper
# resolves from its own location — the fixture never carries a copy.
write_fragment() {
  local dir="$1" file="$2" identity="$3" title="$4" impact="${5:-}"
  mkdir -p "$dir/NEXT"
  {
    echo "---"
    echo "date: 2026-09-18"
    echo "issue: $identity"
    [ -z "$impact" ] || echo "impact: $impact"
    echo "title: $title"
    echo "---"
    echo
    echo "$title release note."
  } >"$dir/NEXT/$file"
}

new_fixture() {
  local dir="$1"
  mkdir -p "$dir"
  git -C "$dir" init -q -b main
  git -C "$dir" config user.name test
  git -C "$dir" config user.email test@example.com
  write_fragment "$dir" 2026-09-18-issue-1000-base.md 1000 "Base entry" patch
  git -C "$dir" add -A
  git -C "$dir" commit -qm base
  # The wrapper resolves `origin/main` by default; a bare remote gives the
  # fixture a real one rather than a local alias that would not exercise it.
  git -C "$dir" remote add origin "$dir"
  git -C "$dir" update-ref refs/remotes/origin/main refs/heads/main
  git -C "$dir" switch -q -c feature
}

commit_branch() {
  git -C "$1" add -A
  git -C "$1" commit -qm branch
}

# (a) The failure that #1418 shipped: a fragment the branch adds with no
# `impact:` at all. `check-pr` exits 0 on this, which is why the hub saw green.
missing="$tmp/missing-impact"
new_fixture "$missing"
write_fragment "$missing" 2026-09-18-issue-1001-no-impact.md 1001 "No impact"
commit_branch "$missing"

status=0
output="$(cd "$missing" && bash "$gate" . 2>&1)" || status=$?
if [ "$status" -ne 0 ] && printf '%s\n' "$output" | grep 'impact is required' >/dev/null; then
  pass "a branch-added fragment without impact reddens the hub gate"
else
  fail "the gate exited $status for a fragment missing impact; output: $output"
fi

# (b) The shape actions-ci actually runs in. `actions-ci.yml` checks out with
# `fetch-depth: 1`, so the PR ref is the only history present and
# `refs/remotes/origin/main` does not exist at all. A gate that resolved its base
# only from a local ref would be unable to run in the one place it must, which is
# how this whole class of gap is introduced. The base is named by
# `GITHUB_BASE_REF`, exactly as the generated consumer caller names it from the
# pull-request event, and the gate fetches it.
shallow_origin="$tmp/shallow-origin"
new_fixture "$shallow_origin"
write_fragment "$shallow_origin" 2026-09-18-issue-1002-no-impact.md 1002 "Shallow no impact"
commit_branch "$shallow_origin"
branch_sha="$(git -C "$shallow_origin" rev-parse HEAD)"

shallow="$tmp/shallow"
mkdir -p "$shallow"
git -C "$shallow" init -q -b main
git -C "$shallow" config user.name test
git -C "$shallow" config user.email test@example.com
git -C "$shallow" remote add origin "$shallow_origin"
git -C "$shallow" fetch -q --depth 1 origin "$branch_sha"
git -C "$shallow" checkout -q --detach "$branch_sha"

if git -C "$shallow" rev-parse --verify -q origin/main >/dev/null; then
  fail "the shallow fixture already resolves origin/main; it no longer reproduces actions-ci"
else
  pass "the shallow fixture reproduces the fetch-depth 1 checkout actions-ci performs"
fi

status=0
output="$(cd "$shallow" && GITHUB_BASE_REF=main bash "$gate" . 2>&1)" || status=$?
if [ "$status" -ne 0 ] && printf '%s\n' "$output" | grep 'impact is required' >/dev/null; then
  pass "the gate resolves its base under a fetch-depth 1 checkout and still reddens"
else
  fail "the gate exited $status in the shallow checkout; output: $output"
fi

# (b2) The unshallow need not leave the base ref behind: with no configured
# fetch refspec it deepens history into FETCH_HEAD only, and `actions/checkout`
# does not promise one. The gate must then fetch the base branch by name rather
# than give up on a base it could have obtained.
unmapped="$tmp/shallow-unmapped"
mkdir -p "$unmapped"
git -C "$unmapped" init -q -b main
git -C "$unmapped" remote add origin "$shallow_origin"
git -C "$unmapped" config --unset-all remote.origin.fetch
git -C "$unmapped" fetch -q --depth 1 origin "$branch_sha"
git -C "$unmapped" checkout -q --detach "$branch_sha"

status=0
output="$(cd "$unmapped" && GITHUB_BASE_REF=main bash "$gate" . 2>&1)" || status=$?
if [ "$status" -ne 0 ] && printf '%s\n' "$output" | grep 'impact is required' >/dev/null; then
  pass "the gate fetches its base branch when the unshallow leaves no base ref"
else
  fail "the gate exited $status when the unshallow left no base ref; output: $output"
fi

# (c) A second fragment claiming an identity another fragment already holds.
# `check-pr` exits 0 on this — it only covers the dependency-manifest/fragment
# pairing — so on a hub pull request it was previously invisible, and two
# fragments for one issue signal overlapping ownership that `NEXT/README.md`
# asks to be consolidated rather than renumbered.
duplicate="$tmp/duplicate-identity"
new_fixture "$duplicate"
write_fragment "$duplicate" 2026-09-18-issue-1000-second.md 1000 "Second entry" patch
commit_branch "$duplicate"

status=0
output="$(cd "$duplicate" && bash "$gate" . 2>&1)" || status=$?
if [ "$status" -ne 0 ] && printf '%s\n' "$output" | grep 'duplicate identity issue:1000' >/dev/null; then
  pass "a second fragment claiming a held identity reddens the hub gate"
else
  fail "the gate exited $status for a duplicate identity; output: $output"
fi

# `check-pr` is the gate that already ran on hub pull requests. Pin that it is
# silent here, so the reason this wrapper exists stays visible in the suite
# rather than becoming folklore: if `check-pr` ever grew this coverage, this
# assertion is where that is noticed.
status=0
(cd "$duplicate" && python3 "$repo_root/scripts/changelog.py" check-pr \
  --repo-root . --base origin/main --head HEAD) >/dev/null 2>&1 || status=$?
if [ "$status" -eq 0 ]; then
  pass "check-pr still exits 0 on a duplicate identity, so it cannot replace this gate"
else
  fail "check-pr now exits $status on a duplicate identity; revisit why this gate is separate"
fi

# (d) A complete fragment must pass. Without this, every assertion above is
# satisfied by a gate that reddens unconditionally, which would be worse than
# the gap it closes: a required check that reddens for a reason the diff cannot
# cause teaches reviewers to re-run rather than read (ADR 0185).
clean="$tmp/clean"
new_fixture "$clean"
write_fragment "$clean" 2026-09-18-issue-1003-clean.md 1003 "Clean entry" patch
commit_branch "$clean"

status=0
output="$(cd "$clean" && bash "$gate" . 2>&1)" || status=$?
if [ "$status" -eq 0 ]; then
  pass "a complete branch-added fragment passes the hub gate"
else
  fail "the gate exited $status on a clean branch; output: $output"
fi

# (e) A branch that adds no fragment at all is not this gate's business — the
# dependency-manifest/fragment pairing is `check-pr`'s gate, and a wrapper that
# also demanded a fragment would be a second, undeclared policy.
no_fragment="$tmp/no-fragment"
new_fixture "$no_fragment"
printf 'unrelated\n' >"$no_fragment/unrelated.txt"
commit_branch "$no_fragment"

status=0
output="$(cd "$no_fragment" && bash "$gate" . 2>&1)" || status=$?
if [ "$status" -eq 0 ]; then
  pass "a branch that adds no fragment is left to check-pr"
else
  fail "the gate exited $status on a branch with no fragment; output: $output"
fi

# (f) Failure modes must be loud. The whole defect class this closes is a gate
# that reports success without having looked, so an unresolvable base must never
# be indistinguishable from a clean pull request.
unreachable="$tmp/unreachable-base"
new_fixture "$unreachable"
write_fragment "$unreachable" 2026-09-18-issue-1004-no-impact.md 1004 "Orphan base"
commit_branch "$unreachable"
git -C "$unreachable" remote remove origin
git -C "$unreachable" update-ref -d refs/remotes/origin/main

status=0
output="$(cd "$unreachable" && bash "$gate" . 2>&1)" || status=$?
if [ "$status" -ne 0 ] && printf '%s\n' "$output" | grep 'cannot resolve the base revision' >/dev/null; then
  pass "an unresolvable base fails loudly instead of validating nothing"
else
  fail "the gate exited $status with no base; output: $output"
fi

# (g) Two histories with no common ancestor make `base...HEAD` an empty diff, so
# the contract would report success having compared nothing. That is the exact
# shape of the original gap and must be refused by name.
orphan="$tmp/orphan-history"
new_fixture "$orphan"
write_fragment "$orphan" 2026-09-18-issue-1005-no-impact.md 1005 "No ancestor"
commit_branch "$orphan"
# HEAD stays on the branch under test; only the BASE is moved to a history that
# shares no root with it. Pointing both at the orphan would make merge-base
# succeed trivially and prove nothing.
git -C "$orphan" checkout -q --orphan unrelated
git -C "$orphan" commit -q --allow-empty -m unrelated
git -C "$orphan" update-ref refs/remotes/origin/main refs/heads/unrelated
git -C "$orphan" switch -q feature

status=0
output="$(cd "$orphan" && bash "$gate" . 2>&1)" || status=$?
if [ "$status" -ne 0 ] && printf '%s\n' "$output" | grep 'share no common ancestor' >/dev/null; then
  pass "a base with no common ancestor is refused rather than silently diffing nothing"
else
  fail "the gate exited $status with an unrelated base; output: $output"
fi

# (i) A tag or branch literally named `origin/main` shadows the real base under
# git's short-name disambiguation, which prefers `refs/tags/` and `refs/heads/`
# over `refs/remotes/`. `actions-ci.yml` checks out with `fetch-tags: true`, so
# such a tag is present in the job. Resolved short, every check in the gate
# succeeds against the wrong object and the contract validates an empty diff at
# exit 0 -- compared-nothing reading as clean, which is the whole defect this
# gate exists to remove. The gate uses the full refname; assert that it still
# reddens with each shadowing ref present.
for shadow_kind in tag branch; do
  shadowed="$tmp/shadowed-$shadow_kind"
  new_fixture "$shadowed"
  write_fragment "$shadowed" 2026-09-18-issue-1009-no-impact.md 1009 "No impact"
  commit_branch "$shadowed"
  if [ "$shadow_kind" = tag ]; then
    git -C "$shadowed" tag origin/main HEAD
  else
    git -C "$shadowed" branch origin/main HEAD
  fi

  status=0
  output="$(cd "$shadowed" && bash "$gate" . 2>&1)" || status=$?
  if [ "$status" -ne 0 ] && printf '%s\n' "$output" | grep 'impact is required' >/dev/null; then
    pass "a $shadow_kind named origin/main cannot shadow the base and mute the gate"
  else
    fail "a $shadow_kind named origin/main muted the gate: exit $status; output: $output"
  fi
done

# (h) This gate is not a `*.test.sh`, and until #1450 the actions-ci orphan
# detector found its candidates by that suffix, so it could not see this file:
# deregistering it would have left every assertion above passing locally while
# nothing ran in Actions. The registration was therefore pinned here.
#
# It is no longer pinned here. `actions-ci-groups.test.sh` now requires every
# tracked `*.sh`/`*.py` under `scripts/ci-gate/` to be reachable in Actions or
# declared a library module, so this gate is covered by the same rule as every
# other one -- and so is the next non-test gate, which is the part a per-file pin
# could never give. The one thing the pin also asserted, that the registration
# sits in the `platform` group, moved to that file's load-bearing command list,
# which is where group assignment is owned.

if [ "$fails" -ne 0 ]; then
  printf '%d test(s) failed.\n' "$fails" >&2
  exit 1
fi
