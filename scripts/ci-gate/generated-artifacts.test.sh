#!/usr/bin/env bash
# Verjson/.github#404 — the shared generated-artifact validation workflow.
#
# House method: awk-extract the exact `run:` block from
# .github/workflows/generated-artifacts.yml (single source of truth — no copy of
# the logic lives here) and execute it against fixture repositories.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../.." && pwd)"
wf="$repo_root/.github/workflows/generated-artifacts.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

[ -f "$wf" ] || { echo "FAIL - workflow not found: $wf"; exit 1; }

# The `run:` block of the step carrying `id: <step>`, dedented to column 0.
extract() { # extract <step-id> <destination>
  awk -v want="        id: $1" '
    $0 == want { seen = 1 }
    seen && $0 == "        run: |" { cap = 1; next }
    cap && $0 ~ /^      - name:/ { exit }
    cap {
      if (substr($0, 1, 10) == "          ") { print substr($0, 11); next }
      if ($0 ~ /^[ \t]*$/) { print ""; next }
      exit
    }
  ' "$wf" >"$2"
}

validate="$tmp/validate.sh"
extract validate "$validate"
[ -s "$validate" ] \
  || { echo "FAIL - could not extract the validate run block from $wf"; exit 1; }

pin="$tmp/pin.sh"
extract pin "$pin"
[ -s "$pin" ] \
  || { echo "FAIL - could not extract the pin run block from $wf"; exit 1; }

# A syntactically valid contract pin: 40 lower-case hex. The fixtures never
# fetch it — the contract engine is copied into place by hand — so it only has
# to satisfy the workflow's own immutability rule.
immutable_sha=0123456789abcdef0123456789abcdef01234567

# A consumer repository carrying a real, current ADR index.
make_adr_repo() { # make_adr_repo <path>
  local ws="$1"
  mkdir -p "$ws/scripts" "$ws/docs/decisions/0001-example"
  cp "$repo_root/scripts/gen-adr-index.sh" "$ws/scripts/gen-adr-index.sh"
  cat >"$ws/docs/decisions/0001-example/README.md" <<'ADR'
# 0001 — Example decision

- **Date:** 2026-08-05
ADR
  cat >"$ws/docs/decisions/README.md" <<'INDEX'
# Decisions

<!-- BEGIN ADR INDEX -->
<!-- END ADR INDEX -->
INDEX
  bash "$ws/scripts/gen-adr-index.sh" >/dev/null
}

run_validate() { # run_validate <workspace> [env assignments...]
  local ws="$1"
  shift
  ( cd "$ws" && env GITHUB_WORKSPACE="$ws" GITHUB_STEP_SUMMARY="$ws/summary.md" \
      RUN_ADR_INDEX=false RUN_CHANGELOG=false \
      CONTRACT_REF='' LEGACY_DIR='' BASE_SHA='' HEAD_SHA='' \
      "$@" bash "$validate" 2>&1 )
}

# --------------------------------------------------------------------------
# Clean: the committed artifact matches what the generator produces.
# --------------------------------------------------------------------------
ws="$tmp/clean"
make_adr_repo "$ws"
out="$(run_validate "$ws" RUN_ADR_INDEX=true)"
rc=$?
[ "$rc" -eq 0 ] \
  && pass "a current ADR index passes the adr-index check" \
  || fail "a current ADR index was rejected (rc=$rc): $out"

# --------------------------------------------------------------------------
# Stale: the generator produces something other than what is committed. The
# whole point of centralising this is that the consumer is told WHICH generator
# to re-run, so a failing job is actionable without opening the workflow.
# --------------------------------------------------------------------------
ws="$tmp/stale"
make_adr_repo "$ws"
mkdir -p "$ws/docs/decisions/0002-added-later"
cat >"$ws/docs/decisions/0002-added-later/README.md" <<'ADR'
# 0002 — A decision whose row was never generated

- **Date:** 2026-08-05
ADR
out="$(run_validate "$ws" RUN_ADR_INDEX=true)"
rc=$?
[ "$rc" -ne 0 ] \
  && pass "a stale ADR index fails the adr-index check" \
  || fail "a stale ADR index passed — the check is fail-open"

# Asserted on the workflow's OWN report, not on whatever the generator happened
# to print: `gen-adr-index.sh --check` already names itself on stderr, so a
# looser grep passes without the shared workflow reporting anything at all.
grep -q '::error title=Generated artifact out of date: ADR index::' <<<"$out" \
  && pass "the failure is reported in the workflow's uniform annotation form" \
  || fail "no uniform failure annotation for the ADR index: $out"

grep -qE '^ +bash scripts/gen-adr-index\.sh$' "$ws/summary.md" \
  && pass "the job summary names the generator command to re-run" \
  || fail "the job summary does not name the generator: $(cat "$ws/summary.md")"

# --------------------------------------------------------------------------
# Missing: the caller opted into a check whose generator the repository does not
# have. That is a caller mistake, not a clean repository — it must fail, and it
# must not be reported as "stale", which sends the consumer to regenerate a file
# with a script that is not there.
# --------------------------------------------------------------------------
ws="$tmp/missing"
make_adr_repo "$ws"
rm -f "$ws/scripts/gen-adr-index.sh"
out="$(run_validate "$ws" RUN_ADR_INDEX=true)"
rc=$?
[ "$rc" -ne 0 ] \
  && pass "an absent generator fails the check instead of passing vacuously" \
  || fail "adr-index passed with no scripts/gen-adr-index.sh — the check is fail-open"

grep -q '::error title=Generated artifact check unavailable: ADR index::' <<<"$out" \
  && pass "an absent generator is reported as unavailable, not as stale" \
  || fail "an absent generator is misreported: $out"

grep -q 'scripts/gen-adr-index.sh' "$ws/summary.md" \
  && pass "the report names the script the repository is missing" \
  || fail "the report does not name the missing script: $(cat "$ws/summary.md")"

# --------------------------------------------------------------------------
# Every check off. Booleans default to false, so a caller that mistypes an input
# name — or copies the caller before deciding what to check — gets a workflow
# that validates nothing and reports green. That is the fail-open shape this
# workflow exists to remove, so it is an error rather than a no-op.
# --------------------------------------------------------------------------
ws="$tmp/nothing"
make_adr_repo "$ws"
out="$(run_validate "$ws")"
rc=$?
[ "$rc" -ne 0 ] \
  && pass "a call with every check disabled fails instead of reporting a green no-op" \
  || fail "a call that validates nothing passed green: $out"

grep -q 'no generated-artifact check was enabled' <<<"$out" \
  && pass "the empty-call failure says which input to set" \
  || fail "the empty-call failure is not explained: $out"

# Fixture history. The workflow's pull-request policy check diffs base...head,
# so a fixture without real commits cannot exercise it at all — BASE_SHA and
# HEAD_SHA would stay empty and the branch would never be entered.
fixture_commit() { # fixture_commit <path> <message> -> prints the commit SHA
  local ws="$1"
  git -C "$ws" add -A
  git -C "$ws" -c user.email=ci@example.invalid -c user.name='Fixture CI' \
    commit --quiet --no-gpg-sign --allow-empty -m "$2"
  git -C "$ws" rev-parse HEAD
}

# A consumer repository on the canonical changelog contract, with the contract
# checked out where the workflow's own checkout step puts it. The contract
# checkout and the job summary are job scratch on a real runner, never tracked
# files of the consumer, so the fixture ignores them.
make_changelog_repo() { # make_changelog_repo <path>
  local ws="$1"
  mkdir -p "$ws/NEXT" "$ws/.changelog-contract/scripts"
  cp "$repo_root/scripts/changelog.py" "$ws/.changelog-contract/scripts/changelog.py"
  # The preview lives beside the engine in the contract checkout, because
  # changelog-validate.yml reaches it the same way (#449). Copied rather than
  # stubbed: these assertions are about what an adopter actually sees.
  #
  # Deliberately NOT chmod +x'd. An earlier revision of this fixture did, and it
  # hid the script being committed at mode 644 — the workflow gated on -x, so
  # every adopter would have taken the "unavailable" branch forever while these
  # tests stayed green. The fixture now inherits whatever mode the repository
  # actually carries, so the workflow has to work at that mode.
  cp "$repo_root/scripts/changelog-preview.sh" "$ws/.changelog-contract/scripts/changelog-preview.sh"
  cat >"$ws/NEXT/2026-08-05-issue-404-example.md" <<'FRAGMENT'
---
date: 2026-08-05
issue: 404
title: Example
---

The lead paragraph, which is what a release note needs.

## Why

The argument, which belongs in the running log and not in release notes.
FRAGMENT
  printf '.changelog-contract/\nsummary.md\n' >"$ws/.gitignore"
  git -C "$ws" init --quiet --initial-branch=main
  base_sha="$(fixture_commit "$ws" 'base')"
}

# --------------------------------------------------------------------------
# Clean: unreleased fragments parse and render under the pinned contract.
# --------------------------------------------------------------------------
ws="$tmp/changelog-clean"
make_changelog_repo "$ws"
out="$(run_validate "$ws" RUN_CHANGELOG=true CONTRACT_REF="$immutable_sha")"
rc=$?
[ "$rc" -eq 0 ] \
  && pass "a repository with valid fragments passes the changelog check" \
  || fail "valid fragments were rejected (rc=$rc): $out"

# --------------------------------------------------------------------------
# The release-note preview (#426). A released snapshot keeps only each entry's
# title and lead paragraph and can never be edited afterwards (ADR 0059), so the
# released form is otherwise first seen when it is already permanent. The check
# prints it while the fragments are still editable.
# --------------------------------------------------------------------------
grep -q 'Release notes this would publish' "$ws/summary.md" \
  && pass "a passing changelog check writes the release-note preview to the job summary" \
  || fail "no release-note preview in the job summary: $(cat "$ws/summary.md")"

grep -q 'The lead paragraph, which is what a release note needs.' "$ws/summary.md" \
  && ! grep -q 'The argument, which belongs in the running log' "$ws/summary.md" \
  && pass "the preview shows the RELEASED shape — lead kept, argument dropped" \
  || fail "the preview is not the released shape: $(cat "$ws/summary.md")"

# A job summary is capped. A preview cut off mid-entry that does not say so
# reads as a complete release note that is quietly missing entries.
ws="$tmp/changelog-huge"
make_changelog_repo "$ws"
{
  printf -- '---\ndate: 2026-08-05\nissue: 405\ntitle: Enormous\n---\n\n'
  head -c 70000 /dev/zero | tr '\0' 'x'
  printf '\n'
} >"$ws/NEXT/2026-08-05-issue-405-enormous.md"
out="$(run_validate "$ws" RUN_CHANGELOG=true CONTRACT_REF="$immutable_sha")"
rc=$?
[ "$rc" -eq 0 ] && grep -q 'Truncated for the job summary' "$ws/summary.md" \
  && grep -q '</details>' "$ws/summary.md" \
  && pass "an over-long preview says it was truncated and still closes its block" \
  || fail "an over-long preview was silently cut (rc=$rc): $(head -c 300 "$ws/summary.md")"

# The remedy has to be runnable where it is read. Under ADR 0038 no consumer
# commits the engine, so naming `scripts/render-next.sh` sends every one of them
# to "No such file or directory" — the same defect the failure remedies avoid.
grep -qF 'python3 .changelog-contract/scripts/changelog.py render-next --repo-root . --as-released' \
  "$ws/summary.md" \
  && pass "the truncation note names the pinned contract engine, not a path consumers lack" \
  || fail "the truncation note is not runnable by a consumer: $(grep -A2 Truncated "$ws/summary.md")"

# --------------------------------------------------------------------------
# A repository with nothing unreleased. `render-next` exits non-zero here by
# design, and reporting that as a broken renderer would train consumers to
# ignore the warning — the inverse of #399, where a real renderer failure was
# reported as "no unreleased fragments" and exited 0.
# --------------------------------------------------------------------------
ws="$tmp/changelog-empty"
make_changelog_repo "$ws"
rm -f "$ws/NEXT"/*.md
out="$(run_validate "$ws" RUN_CHANGELOG=true CONTRACT_REF="$immutable_sha")"
rc=$?
[ "$rc" -eq 0 ] && grep -q 'nothing to preview' <<<"$out" \
  && ! grep -q '::warning' <<<"$out" \
  && pass "an empty NEXT/ is reported as nothing to preview, not as a broken renderer" \
  || fail "an empty NEXT/ was misreported (rc=$rc): $out"

# --------------------------------------------------------------------------
# The consumer pins `contract_ref` independently of the ref it calls this
# workflow at, so a repository can legitimately run a NEWER workflow against an
# OLDER engine that has no --as-released. The preview is informational, so that
# must warn and still pass — a preview failure that failed the check would make
# an optional convenience a fleet-wide breaking change.
# --------------------------------------------------------------------------
ws="$tmp/changelog-old-engine"
make_changelog_repo "$ws"
cat >"$ws/.changelog-contract/scripts/changelog.py" <<'OLD'
import sys
if "--as-released" in sys.argv:
    sys.stderr.write("changelog.py render-next: error: unrecognized arguments: --as-released\n")
    raise SystemExit(2)
raise SystemExit(0)
OLD
out="$(run_validate "$ws" RUN_CHANGELOG=true CONTRACT_REF="$immutable_sha")"
rc=$?
[ "$rc" -eq 0 ] \
  && pass "an engine too old for --as-released still passes the changelog check" \
  || fail "a missing preview failed the check (rc=$rc): $out"

grep -q '::warning title=Release-note preview unavailable::' <<<"$out" \
  && grep -q 'unrecognized arguments' <<<"$out" \
  && pass "the unavailable preview is reported with the engine's own reason" \
  || fail "a failed preview was swallowed: $out"

# An annotation shows one line. A multi-line failure — a traceback, an argparse
# usage block — would otherwise arrive with its reason on lines nobody reads.
ws="$tmp/changelog-noisy-engine"
make_changelog_repo "$ws"
cat >"$ws/.changelog-contract/scripts/changelog.py" <<'NOISY'
import sys
if "--as-released" in sys.argv:
    sys.stderr.write("Traceback (most recent call last):\n  File x\nValueError: the real reason\n")
    raise SystemExit(1)
raise SystemExit(0)
NOISY
out="$(run_validate "$ws" RUN_CHANGELOG=true CONTRACT_REF="$immutable_sha")"
rc=$?
[ "$rc" -eq 0 ] \
  && grep -q '::warning title=Release-note preview unavailable::.*ValueError: the real reason' <<<"$out" \
  && pass "a multi-line preview failure is collapsed so the reason reaches the annotation" \
  || fail "the preview failure reason did not survive onto one line (rc=$rc): $out"

grep -q 'PASSED' <<<"$out" \
  && pass "the warning says the check itself passed, so it is not read as a failure" \
  || fail "the preview warning does not distinguish itself from a verdict: $out"

# --------------------------------------------------------------------------
# Stale: a fragment the canonical engine rejects.
# --------------------------------------------------------------------------
ws="$tmp/changelog-stale"
make_changelog_repo "$ws"
printf 'no front matter here\n' >"$ws/NEXT/2026-08-05-issue-404-broken.md"
out="$(run_validate "$ws" RUN_CHANGELOG=true CONTRACT_REF="$immutable_sha")"
rc=$?
[ "$rc" -ne 0 ] \
  && pass "a fragment the contract rejects fails the changelog check" \
  || fail "an invalid fragment passed — the changelog check is fail-open: $out"

grep -q '::error title=Generated artifact out of date: Changelog::' <<<"$out" \
  && pass "the changelog failure uses the same uniform annotation" \
  || fail "the changelog failure is not reported uniformly: $out"

# The remedy has to be the command CI ran. Under ADR 0038 the engine lives only
# in Verjson/.github and consumers never commit scripts/changelog.py, so naming
# that path sends every consumer to "No such file or directory" — the opposite
# of the one-command promise. The engine path is the pinned contract checkout.
grep -qE '^ +python3 -I \.changelog-contract/scripts/changelog\.py validate --repo-root \.$' "$ws/summary.md" \
  && pass "the changelog remedy names the pinned contract engine, not a path consumers lack" \
  || fail "the changelog remedy is not runnable by a consumer: $(cat "$ws/summary.md")"

grep -q "$immutable_sha" "$ws/summary.md" \
  && pass "the changelog remedy says which contract commit to check out" \
  || fail "the changelog remedy does not name the contract pin: $(cat "$ws/summary.md")"

# --------------------------------------------------------------------------
# Missing: the contract checkout did not land the engine. Fails as unavailable
# and names contract_ref, because "your fragments are wrong" would be a lie.
# --------------------------------------------------------------------------
ws="$tmp/changelog-missing"
make_changelog_repo "$ws"
rm -rf "$ws/.changelog-contract"
out="$(run_validate "$ws" RUN_CHANGELOG=true CONTRACT_REF="$immutable_sha")"
rc=$?
[ "$rc" -ne 0 ] \
  && pass "an absent contract engine fails instead of passing vacuously" \
  || fail "the changelog check passed with no contract checkout: $out"

grep -q '::error title=Generated artifact check unavailable: Changelog::' <<<"$out" \
  && grep -q 'contract_ref' <<<"$out" \
  && pass "an absent contract engine is reported as unavailable and names contract_ref" \
  || fail "an absent contract engine is misreported: $out"

# --------------------------------------------------------------------------
# The changelog check is meaningless without a pin, and an unpinned checkout
# would silently validate against whatever Verjson/.github@HEAD happens to be.
# --------------------------------------------------------------------------
ws="$tmp/changelog-unpinned"
make_changelog_repo "$ws"
out="$(run_validate "$ws" RUN_CHANGELOG=true)"
rc=$?
[ "$rc" -ne 0 ] && grep -q 'contract_ref' <<<"$out" \
  && pass "changelog without contract_ref fails and says which input is missing" \
  || fail "changelog ran unpinned (rc=$rc): $out"

# --------------------------------------------------------------------------
# The pin guard (#404 review). `ref:` accepts ANY ref of Verjson/.github, and
# the workflow then executes python3 from that checkout. A branch or tag can
# move under the pin; `refs/pull/<n>/merge` is an unreviewed pull request
# against a PUBLIC repository, so accepting one would let anyone who can open a
# PR run their own contract engine on the Verjson lane. Only a full commit SHA
# names code that cannot change after review, and it is rejected BEFORE either
# checkout so nothing is fetched for a call that is already invalid.
# --------------------------------------------------------------------------
run_pin() { # run_pin <workspace> [env assignments...]
  local ws="$1"
  shift
  mkdir -p "$ws"
  ( cd "$ws" && env GITHUB_STEP_SUMMARY="$ws/summary.md" \
      RUN_CHANGELOG=true CONTRACT_REF='' \
      "$@" bash "$pin" 2>&1 )
}

out="$(run_pin "$tmp/pin-good" CONTRACT_REF="$immutable_sha")"
rc=$?
[ "$rc" -eq 0 ] \
  && pass "a full 40-character commit SHA is accepted as the contract pin" \
  || fail "an immutable contract pin was rejected (rc=$rc): $out"

# A mutable ref points at whatever is there when the job runs, which is not
# what any reviewer approved. `refs/pull/<n>/merge` is the sharp one: this
# repository is public, so that ref is reachable by anyone.
mutable_case() { # mutable_case <ref> <description>
  local ref="$1" description="$2" out rc
  out="$(run_pin "$tmp/pin-bad" CONTRACT_REF="$ref")"
  rc=$?
  [ "$rc" -ne 0 ] && grep -q 'contract_ref' <<<"$out" \
    && pass "$description is rejected and the failure names contract_ref" \
    || fail "$description was accepted as a contract pin (rc=$rc): $out"
}
mutable_case main 'a branch name'
mutable_case v1 'a tag'
mutable_case refs/pull/1/merge 'a pull-request merge ref'
mutable_case feat/404-generated-artifacts-ci 'a feature branch'

# #312: `ref_is_immutable` must require exactly 40 hex characters. An
# abbreviated SHA is ambiguous by construction — git resolves it against
# whatever objects exist at fetch time — so "is a prefix of a SHA" is not the
# test, and neither is "looks hex-ish".
mutable_case "${immutable_sha:0:12}" 'an abbreviated SHA'
mutable_case "${immutable_sha}0" 'an over-long hex string'
mutable_case "${immutable_sha^^}" 'an upper-case SHA'
mutable_case "$immutable_sha "$'\n''main' 'a SHA with a trailing ref smuggled after a newline'

# The guard is only about the changelog contract: a caller that never asks for
# the changelog check has no contract to pin, and must not be forced to invent
# one to run the ADR-index check.
out="$(run_pin "$tmp/pin-off" RUN_CHANGELOG=false)"
rc=$?
[ "$rc" -eq 0 ] \
  && pass "a call that does not request the changelog check needs no pin" \
  || fail "the pin guard fires for a call with changelog: false (rc=$rc): $out"

# The guard has to run before the contract checkout, or the unreviewed code it
# rejects has already been fetched onto the runner by the time it speaks.
pin_line="$(grep -n '^        id: pin$' "$wf" | cut -d: -f1)"
contract_line="$(grep -n '^          repository: Verjson/\.github$' "$wf" | cut -d: -f1)"
[ -n "$pin_line" ] && [ -n "$contract_line" ] && [ "$pin_line" -lt "$contract_line" ] \
  && pass "the pin guard runs before the contract checkout" \
  || fail "the pin guard does not precede the contract checkout (pin=$pin_line contract=$contract_line)"

# --------------------------------------------------------------------------
# The pull-request policy check (#404 review). Every fixture above leaves
# BASE_SHA and HEAD_SHA empty, so `check-pr` never ran once and the whole branch
# was dead: `|| changelog_ok=false` could be `|| true` and the suite stayed
# green. These cases run it against a real two-commit history.
#
# `check-pr` polices what a PR may do to the changelog store under ADR 0038 —
# it forbids editing generated aggregates or released snapshots and forbids
# consuming NEXT/ fragments. It does NOT require a PR to add a fragment, so a
# fixture built on "adds no NEXT/ fragment" would assert a rule the canonical
# engine does not have.
# --------------------------------------------------------------------------
ws="$tmp/check-pr-clean"
make_changelog_repo "$ws"
cat >"$ws/NEXT/2026-08-05-issue-405-added.md" <<'FRAGMENT'
---
date: 2026-08-05
issue: 405
impact: patch
title: Added by this pull request
---

Body text.
FRAGMENT
head_sha="$(fixture_commit "$ws" 'add a fragment')"
out="$(run_validate "$ws" RUN_CHANGELOG=true CONTRACT_REF="$immutable_sha" \
  BASE_SHA="$base_sha" HEAD_SHA="$head_sha")"
rc=$?
[ "$rc" -eq 0 ] \
  && pass "a pull request that adds a NEXT/ fragment passes the policy check" \
  || fail "an ordinary changelog pull request was rejected (rc=$rc): $out"

ws="$tmp/check-pr-aggregate"
make_changelog_repo "$ws"
printf '# Changelog\n\nhand-edited\n' >"$ws/CHANGELOG.md"
head_sha="$(fixture_commit "$ws" 'hand-edit the generated aggregate')"
out="$(run_validate "$ws" RUN_CHANGELOG=true CONTRACT_REF="$immutable_sha" \
  BASE_SHA="$base_sha" HEAD_SHA="$head_sha")"
rc=$?
[ "$rc" -ne 0 ] \
  && pass "a pull request that hand-edits CHANGELOG.md fails the policy check" \
  || fail "check-pr never ran or its result was discarded (rc=$rc): $out"

grep -q '::error title=Generated artifact out of date: Changelog::' <<<"$out" \
  && pass "a policy-check failure is reported in the uniform annotation form" \
  || fail "a policy-check failure is not reported by the workflow: $out"

# The remedy names the subcommand that failed. Sending someone to `validate`
# for a policy failure sends them to a command that passes, which reads as a
# flaky check rather than a verdict about their pull request.
grep -qF "python3 -I .changelog-contract/scripts/changelog.py check-pr --repo-root . --base $base_sha --head $head_sha" \
  "$ws/summary.md" \
  && pass "a policy-check failure names check-pr, with the SHAs it was given" \
  || fail "the policy-check remedy is not the command that failed: $(cat "$ws/summary.md")"

ws="$tmp/check-pr-consumed"
make_changelog_repo "$ws"
rm -f "$ws/NEXT/2026-08-05-issue-404-example.md"
head_sha="$(fixture_commit "$ws" 'consume a fragment outside a release')"
out="$(run_validate "$ws" RUN_CHANGELOG=true CONTRACT_REF="$immutable_sha" \
  BASE_SHA="$base_sha" HEAD_SHA="$head_sha")"
rc=$?
[ "$rc" -ne 0 ] \
  && pass "a pull request that consumes a NEXT/ fragment fails the policy check" \
  || fail "a consumed fragment passed the policy check (rc=$rc): $out"

# --------------------------------------------------------------------------
# Two broken artifacts, one run. Checks accumulate rather than short-circuit, so
# a consumer fixes both in one round instead of learning about the second only
# after paying for another CI run.
# --------------------------------------------------------------------------
ws="$tmp/both-broken"
make_adr_repo "$ws"
make_changelog_repo "$ws"
mkdir -p "$ws/docs/decisions/0002-added-later"
cat >"$ws/docs/decisions/0002-added-later/README.md" <<'ADR'
# 0002 — A decision whose row was never generated

- **Date:** 2026-08-05
ADR
printf 'no front matter here\n' >"$ws/NEXT/2026-08-05-issue-404-broken.md"
out="$(run_validate "$ws" RUN_ADR_INDEX=true RUN_CHANGELOG=true \
  CONTRACT_REF="$immutable_sha")"
rc=$?
[ "$rc" -ne 0 ] \
  && grep -q 'out of date: ADR index' <<<"$out" \
  && grep -q 'out of date: Changelog' <<<"$out" \
  && pass "both failing checks are reported in one run" \
  || fail "the run stops at the first failure (rc=$rc): $out"

# --------------------------------------------------------------------------
# legacy_dir is a pass-through to the contract engine, not decoration: a
# repository mid-migration keeps its fragments elsewhere, and dropping the value
# would validate an empty set and report green.
# --------------------------------------------------------------------------
ws="$tmp/legacy"
make_changelog_repo "$ws"
mkdir -p "$ws/legacy-next"
printf 'no front matter here\n' >"$ws/legacy-next/2026-08-05-issue-404-broken.md"
out="$(run_validate "$ws" RUN_CHANGELOG=true LEGACY_DIR=legacy-next \
  CONTRACT_REF="$immutable_sha")"
rc=$?
[ "$rc" -ne 0 ] \
  && pass "legacy_dir reaches the contract engine, so its fragments are validated too" \
  || fail "legacy_dir was dropped — fragments there are never checked: $out"

grep -qE '^ +python3 -I \.changelog-contract/scripts/changelog\.py validate --repo-root \. --legacy-dir legacy-next$' \
  "$ws/summary.md" \
  && pass "the remedy carries legacy_dir, so re-running it reproduces the verdict" \
  || fail "the remedy drops legacy_dir and would pass locally: $(cat "$ws/summary.md")"

# --------------------------------------------------------------------------
# The boundary itself (#404). Checks are ENUMERATED opt-ins: a caller names a
# supported artifact, never a command. An input that reached the shell as code
# would turn every consumer's workflow file into a remote-execution surface on
# the shared runner pool, which is the one thing this workflow must not become.
# --------------------------------------------------------------------------
declared_inputs="$(awk '
  /^  workflow_call:/ { in_call = 1; next }
  in_call && /^    inputs:/ { in_inputs = 1; next }
  in_inputs && /^[a-z]/ { exit }
  in_inputs && /^      [a-z][a-z_-]*:$/ {
    gsub(/[ :]/, "")
    print
  }
' "$wf")"
expected_inputs="$(printf '%s\n' adr-index changelog contract_ref legacy_dir runner | sort)"
[ "$(printf '%s\n' "$declared_inputs" | sort)" = "$expected_inputs" ] \
  && pass "the input surface is exactly the enumerated set" \
  || fail "input surface drifted: got [$(printf '%s' "$declared_inputs" | tr '\n' ' ')] want [$(printf '%s' "$expected_inputs" | tr '\n' ' ')]"

# The extraction method above runs the `run:` block with env supplied by hand,
# which means the input -> env mapping itself is never executed by any fixture.
# A missing variable fails closed under `set -u`, but a swapped or misspelled one
# fails open and silently: wiring RUN_ADR_INDEX to `inputs.changelog` leaves
# every test here green while `adr-index: true` checks nothing. Pin the mapping
# literally — it is the one part of the step no fixture can reach.
env_block() { # env_block <step-id>
  awk -v want="        id: $1" '
    $0 == want { seen = 1; next }
    seen && $0 == "        env:" { cap = 1; next }
    cap && $0 !~ /^          [A-Z_]+: / { exit }
    cap { print substr($0, 11) }
  ' "$wf"
}

expect_env() { # expect_env <step-id> <expected block on stdin>
  local step="$1" want got
  want="$(sort)"
  got="$(env_block "$step" | sort)"
  [ "$got" = "$want" ] \
    && pass "the $step step's env maps every input to its exact expression" \
    || fail "the $step step's env drifted:
--- want ---
$want
--- got ---
$got"
}

expect_env validate <<'ENV'
RUN_ADR_INDEX: ${{ inputs.adr-index }}
RUN_CHANGELOG: ${{ inputs.changelog }}
CONTRACT_REF: ${{ inputs.contract_ref }}
LEGACY_DIR: ${{ inputs.legacy_dir }}
BASE_SHA: ${{ github.event.pull_request.base.sha }}
HEAD_SHA: ${{ github.event.pull_request.head.sha }}
ENV

# The pin guard reads the same two inputs; mis-wiring CONTRACT_REF there would
# leave it validating a value the checkout never uses.
expect_env pin <<'ENV'
RUN_CHANGELOG: ${{ inputs.changelog }}
CONTRACT_REF: ${{ inputs.contract_ref }}
ENV

# Both artifact selectors are booleans, so `adr-index: rm -rf /` is not even a
# type-valid call — the rejection is GitHub's, before any shell starts.
for boolean in adr-index changelog; do
  awk -v want="      $boolean:" '
    $0 == want { seen = 1; next }
    seen && /^      [a-z]/ { exit }
    seen { print }
  ' "$wf" | grep '^        type: boolean$' >/dev/null \
    && pass "$boolean is typed boolean, so it cannot carry a command" \
    || fail "$boolean is not declared as a boolean input"
done

# No input value is interpolated into the script body, and nothing eval's one.
# Values arrive through `env:`, where the shell treats them as data.
grep -q '\${{' "$validate" \
  && fail "the validate script interpolates an expression into its body: $(grep '\${{' "$validate")" \
  || pass "no workflow expression is interpolated into the script body"

grep -qE '(^|[^[:alnum:]_])eval([^[:alnum:]_]|$)' "$validate" \
  && fail "the validate script eval's a value" \
  || pass "the validate script never eval's an input"

# --------------------------------------------------------------------------
# Folded, not duplicated (#404). Where the generated artifact IS the changelog,
# this runs the canonical engine from the pinned contract — the same commands
# changelog-validate.yml runs — instead of adding a second render pass.
# --------------------------------------------------------------------------
canonical="$repo_root/.github/workflows/changelog-validate.yml"
grep -qF '.changelog-contract/scripts/changelog.py' "$validate" \
  && pass "the changelog check runs the engine from the pinned contract checkout" \
  || fail "the changelog check does not use the pinned contract engine"

for subcommand in validate check-pr; do
  grep -qF "changelog.py $subcommand" "$canonical" && grep -qF "\"\$engine\" $subcommand" "$validate" \
    && pass "the changelog check runs the canonical '$subcommand' subcommand" \
    || fail "the changelog check and changelog-validate.yml disagree on '$subcommand'"
done

# ADR 0055 kept a render out of this check because `validate` already parses
# every fragment, so a second VERDICT-BEARING render pays twice for one answer.
# The #426 preview is not a second verdict: it runs only after the verdict is
# decided, and since #449 it does not even live here — the check shells out to
# the shared preview script, and never reads its exit status. Asserted here
# statically, and behaviourally by the old-engine fixture above where the render
# fails and the check still passes.
renders="$(grep -c '"\$engine" render-next' "$validate")"
[ "$renders" -eq 0 ] \
  && pass "the validate script renders nothing itself — the verdict rests on validate alone" \
  || fail "the engine is asked to render $renders times in the validate script"

# The preview moved out so that changelog-validate.yml can reach it too. Assert
# the shared script is what both get: one render, in the released shape.
shared_preview="$repo_root/scripts/changelog-preview.sh"
[ "$(grep -c 'python3 "\$engine" render-next' "$shared_preview")" -eq 1 ] \
  && grep -q 'render-next "\${args\[@\]}" --as-released' "$shared_preview" \
  && pass "the shared preview script renders the released form exactly once" \
  || fail "the shared preview script does not render the released form exactly once"

grep -E 'preview_rc' "$validate" | grep -E 'changelog_ok|failures|report ' >/dev/null \
  && fail "the preview's exit status feeds the changelog verdict" \
  || pass "no verdict is derived from the preview — renderability is still established by validate alone"

# --------------------------------------------------------------------------
# Plumbing the shared workflow owns so no consumer hand-writes it again.
# --------------------------------------------------------------------------
grep -qxF 'permissions:' "$wf" && grep -qxF '  contents: read' "$wf" \
  && pass "the shared workflow pins read-only permissions" \
  || fail "the shared workflow does not pin contents: read"

grep -qE '^    timeout-minutes: [0-9]+$' "$wf" \
  && pass "the job carries a timeout" \
  || fail "the job has no timeout, so a hung generator holds a runner"

checkouts="$(grep -cE '^        uses: actions/checkout@[0-9a-f]{40} # v[0-9]+$' "$wf")"
[ "$checkouts" -eq "$(grep -cE '^        uses: actions/checkout@' "$wf")" ] \
  && [ "$checkouts" -ge 1 ] \
  && pass "every checkout is pinned to a full commit SHA" \
  || fail "a checkout is not pinned by SHA"

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
else
  echo "$fails test(s) failed."
  exit 1
fi
