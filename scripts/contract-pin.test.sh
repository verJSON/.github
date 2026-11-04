#!/usr/bin/env bash
# Contract check: the pin the migration guide tells consumers to generate against
# must exist in this repository and must contain the generator (Verjson/.github#308).
#
# Consumers were told to pin 1486d44…, which predates gen-changelog-caller.sh. So
# `raw.githubusercontent.com/.../<pin>/scripts/gen-changelog-caller.sh` 404s, and
# an adopter wanting a hermetic contract test had to carry a second ref pointing
# somewhere else — which is two pins for one contract, and the drift #304 was
# filed about wearing a different hat.
#
# This is the #287 lesson applied to the pin instead of to a tag: a documented
# literal that nothing checks will eventually name something that does not exist.
#
# Test seam: CONTRACT_PIN_ROOT selects the repository to check.
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="${CONTRACT_PIN_ROOT:-$(cd "$here/.." && pwd)}"
guide="$root/docs/changelog/migration.md"
readme="$root/docs/changelog/README.md"
fails=0

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

{ [ -f "$guide" ] && [ -f "$readme" ]; } ||
  { echo "FAIL - changelog README or migration guide not found"; exit 1; }

# The README's capability table is the public metadata contract for the
# recommended pin. Read it rather than maintaining a second key list here.
mapfile -t advertised_keys < <(awk '
  /^<!-- contract-pin-metadata:start -->$/ { capture = 1; next }
  /^<!-- contract-pin-metadata:end -->$/   { capture = 0 }
  capture && /^\| `[a-z]+` / {
    key = $2
    gsub(/`/, "", key)
    print key
  }
' "$readme")

if [ "${#advertised_keys[@]}" -eq 0 ]; then
  fail "the README advertises no machine-checkable metadata capabilities"
else
  pass "the README advertises ${#advertised_keys[@]} metadata capabilities"
fi

# Every 40-hex literal the guide presents as a pin to generate against. Read from
# the guide rather than restated here — a copy is the same drift one level up.
mapfile -t pins < <(grep -oE '^   PIN=[0-9a-f]{40}$' "$guide" | cut -d= -f2 | sort -u)
mapfile -t readme_pins < <(
  sed -nE 's/^<!-- recommended-contract-pin: ([0-9a-f]{40}) -->$/\1/p' "$readme" |
    sort -u
)
registry_pin="$(
  jq -r --arg generator 'scripts/gen-changelog-caller.sh' \
    '.recommended_pins[$generator] // empty' \
    "$root/config/capability-floors.json"
)"

if [ "${#pins[@]}" -eq 0 ]; then
  fail "the guide names no pin to generate against — either the check or the guide is wrong"
else
  pass "the guide names ${#pins[@]} pin(s) to generate against"
fi

if [[ ! "$registry_pin" =~ ^[0-9a-f]{40}$ ]]; then
  fail "the capability registry must name one full changelog-generator recommendation"
elif [ "${#readme_pins[@]}" -ne 1 ]; then
  fail "the README must name exactly one machine-checkable recommended pin"
elif [ "${#pins[@]}" -ne 1 ] || [ "${readme_pins[0]}" != "${pins[0]}" ]; then
  fail "the README capability pin and migration guide PIN disagree"
elif [ "$registry_pin" != "${pins[0]}" ]; then
  fail "the README and migration guide disagree with the capability registry recommendation"
else
  pass "the capability registry, README, and migration guide share one immutable pin"
fi

# actions-ci checks out with fetch-depth: 1 (#234, ADR 0045), so the pinned
# commit is normally absent from the local object store. Materialise it the way
# node-workflow-pins.test.sh does rather than assuming full history — assuming it
# is what made the first version of this check pass locally and fail in CI.
fetch_pinned_commit() {
  local ref="$1"
  git -C "$root" cat-file -e "$ref^{commit}" 2>/dev/null && return 0
  # Serialized with every other fetch into this checkout (#2003); see
  # node-workflow-pins.test.sh.
  python3 "$here/shared_checkout_fetch.py" ensure-commit "$root" "$ref" >/dev/null 2>&1
}

read_pinned_generator() {
  local ref="$1" destination="$2"
  git -C "$root" show "$ref:scripts/gen-changelog-caller.sh" >"$destination"
}

generator_supports_mode() {
  local generator="$1" mode="$2"
  grep -qE "^  $mode\)" "$generator"
}

generator_tmp="$(mktemp -d)"
trap 'rm -rf "$generator_tmp"' EXIT

for pin in "${pins[@]}"; do
  if ! fetch_pinned_commit "$pin"; then
    fail "documented pin $pin is not a commit this repository can obtain"
    continue
  fi
  pass "documented pin ${pin:0:8} resolves to a commit"

  # The whole point of #308: the generator has to be readable AT the pin, because
  # that is where a consumer's contract test fetches it from.
  if git -C "$root" cat-file -e "$pin:scripts/gen-changelog-caller.sh" 2>/dev/null; then
    pass "documented pin ${pin:0:8} contains gen-changelog-caller.sh"
  else
    fail "documented pin ${pin:0:8} predates gen-changelog-caller.sh — a consumer fetching it there gets a 404 (#308)"
  fi

  # Read the generator once before checking its modes. Keeping `git show` out of
  # the grep avoids grep's early successful exit closing the producer pipe under
  # pipefail, and preserves an unreadable object as a distinct failure.
  generator="$generator_tmp/generator-$pin.sh"
  if read_pinned_generator "$pin" "$generator" 2>/dev/null; then
    pass "the generator at ${pin:0:8} is readable"
  else
    fail "the generator at ${pin:0:8} cannot be read from the documented pin"
    continue
  fi

  # A pin that cannot generate is a pin that cannot be adopted. Cheap to prove,
  # and it catches a generator whose interface moved after the pin was written.
  #
  # `release-node` belongs in this list even though only publishing adopters run
  # it: the guide documents it under the same `$PIN`, so a pin that predates the
  # mode makes a documented command fail. That was the state until the pin moved
  # to the commit that closed #463/#464/#465 — the guide said `release-node` and
  # the pin could not run it, and nothing caught the contradiction (#463).
  for mode in \
    workflow generated-artifacts renderer contract-test release-node \
    adr-index-generator generated-artifacts-with-adr-index pr-gate; do
    if generator_supports_mode "$generator" "$mode"; then
      pass "the generator at ${pin:0:8} supports '$mode'"
    else
      fail "the generator at ${pin:0:8} has no '$mode' mode, so the documented command fails"
    fi
  done

  # grep exits 1 for a mode that is absent and 2 for a file it cannot read, so
  # only exit 1 is the semantic mode failure, and only on a mutant that was built.
  missing_mode="$generator_tmp/generator-$pin-missing-workflow.sh"
  awk '$0 != "  workflow)"' "$generator" >"$missing_mode"
  mode_status=0
  generator_supports_mode "$missing_mode" workflow 2>/dev/null || mode_status=$?
  if [ "$mode_status" -eq 0 ]; then
    fail "a generator missing the 'workflow' case was accepted"
  elif [ "$mode_status" -eq 1 ] && [ -s "$missing_mode" ] && ! cmp -s "$generator" "$missing_mode"; then
    pass "a missing generator mode remains a semantic mode failure"
  else
    fail "a missing generator mode failed as something other than an absent mode (grep exit $mode_status)"
  fi

  # The destination is created before git runs, so an empty file beside a
  # failed read shows that git, not the redirect, refused the source.
  unreadable="$generator_tmp/unreadable.sh"
  if read_pinned_generator 0000000000000000000000000000000000000000 "$unreadable" 2>/dev/null; then
    fail "an unreadable pinned generator was accepted"
  elif [ -f "$unreadable" ] && [ ! -s "$unreadable" ]; then
    pass "an unreadable pinned generator remains a source-read failure"
  else
    fail "an unreadable pinned generator failed before git could read the source"
  fi

  mapfile -t changelog_floors < <(
    jq -r '.capabilities[] | select(.generators | index("scripts/gen-changelog-caller.sh")) | .introduced_at' \
      "$root/config/capability-floors.json"
  )
  if [ "${#changelog_floors[@]}" -eq 0 ]; then
    fail "capability registry names no changelog-generator floor"
  fi
  for floor in "${changelog_floors[@]}"; do
    if ! fetch_pinned_commit "$floor"; then
      fail "changelog capability floor $floor is not a commit this repository can obtain"
      continue
    fi
    # Depth-one fetches can leave two valid commits as disconnected shallow roots.
    # Deepen only the recommended pin, geometrically and with a hard bound, rather
    # than downloading the repository's unrelated full history in every CI run.
    ancestry=false
    for depth in 64 256 1024 4096; do
      if git -C "$root" merge-base --is-ancestor "$floor" "$pin" 2>/dev/null; then
        ancestry=true
        break
      fi
      python3 "$here/shared_checkout_fetch.py" deepen "$root" "$pin" "$depth" || break
    done
    if [ "$ancestry" = true ] || git -C "$root" merge-base --is-ancestor "$floor" "$pin" 2>/dev/null; then
      pass "recommended pin descends changelog capability floor ${floor:0:8}"
    else
      fail "recommended pin predates changelog capability floor $floor"
    fi
  done

  # Execute the engine AT the recommended pin against fragments that exercise
  # every advertised key. Source inspection alone is insufficient: a parser can
  # name a key and still reject its valid value at the validation boundary.
  fixture="$(mktemp -d)"
  mkdir -p "$fixture/NEXT"
  git -C "$root" show "$pin:scripts/changelog.py" >"$fixture/changelog.py"
  cat >"$fixture/NEXT/2026-08-07-issue-388-issue-form.md" <<'EOF'
---
date: 2026-08-07
issue: 388
title: Exercise issue metadata
summary: Exercise the issue-form capability.
impact: patch
---

Issue-form fixture.
EOF
  cat >"$fixture/NEXT/2026-08-07-issue-20260807T120000Z-id-form.md" <<'EOF'
---
date: 2026-08-07
id: 20260807T120000Z
refs: 388
component: python
title: Exercise reference metadata
summary: Exercise the id-form capability.
---

ID-form fixture.
EOF

  if python3 "$fixture/changelog.py" validate --repo-root "$fixture" \
      >"$fixture/out" 2>"$fixture/err" && [ ! -s "$fixture/err" ]; then
    pass "the engine at ${pin:0:8} accepts fixtures carrying every advertised metadata key"
  else
    fail "the engine at ${pin:0:8} rejects or ignores advertised metadata: $(tr '\n' ' ' <"$fixture/err")"
  fi

  for key in "${advertised_keys[@]}"; do
    if grep -qE "^${key}:" "$fixture"/NEXT/*.md; then
      pass "advertised metadata '$key' is exercised against the pin"
    else
      fail "advertised metadata '$key' has no pin-capability fixture"
    fi
  done
  find "$fixture" -depth -delete
done

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
