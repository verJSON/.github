#!/usr/bin/env bash
# Verjson/.github#173/#174: keep this published workflow package portable
# without allowing Verjson-owned jobs to drift back to GitHub-hosted runners.
set -uo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflows="$root/.github/workflows"
# Both suffixes. Every sweep below used to glob `*.yml` alone, so a workflow
# named `.yaml` — which GitHub runs identically — evaded all of them (#401 review).
shopt -s nullglob
workflow_files=("$workflows"/*.yml "$workflows"/*.yaml)
shopt -u nullglob
# An empty array would make every `grep … "${workflow_files[@]}"` below read
# STDIN and hang forever instead of failing — a sweep that scans nothing must
# not look like a sweep that found nothing.
[ "${#workflow_files[@]}" -gt 0 ] || { echo "FAIL - no workflow files found under $workflows"; exit 1; }
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

if python3 - "$root/renovate.json" <<'PY'
import json
import sys

rules = json.load(open(sys.argv[1], encoding="utf-8")).get("packageRules", [])
runner_rules = [
    rule for rule in rules
    if rule.get("matchManagers") == ["github-actions"]
    and rule.get("matchDepTypes") == ["github-runner"]
]
digest_rules = [
    rule for rule in rules
    if rule.get("groupName") == "github actions digests"
]
if len(runner_rules) != 1 or runner_rules[0].get("enabled") is not False:
    raise SystemExit("github-runner dependencies must be disabled for Renovate")
if (
    len(digest_rules) != 1
    or digest_rules[0].get("matchManagers") != ["github-actions", "custom.regex"]
    or digest_rules[0].get("matchPackageNames") != ["!ubuntu"]
):
    raise SystemExit("the action digest group must keep its manager scope and exclude the ubuntu runner package")
PY
then
  pass "Renovate leaves hosted runner labels under the coordinated baseline"
else
  fail "Renovate can propose an uncoordinated hosted runner migration"
fi

# A literal hosted selector is allowed only where fixed hosted placement is a
# reviewed security boundary. Inventory file AND job identity so another job in
# an allowed workflow cannot inherit the exception by proximity.
literal_hosted_job_sites() {
  local workflow
  for workflow in "$@"; do
    awk -v file="$(basename "$workflow")" '
      /^  [A-Za-z0-9_-]+:$/ {
        job = $1
        sub(/:$/, "", job)
      }
      /^    runs-on:[[:space:]]+(\[)?ubuntu-(24\.04|latest)([][:space:],]|$)/ {
        print file ":" job ":" $0
      }
    ' "$workflow"
  done | sort
}

literal_hosted_sites="$(literal_hosted_job_sites "${workflow_files[@]}")"
expected_literal_hosted_sites=$'actions-ci.yml:hosted-compatibility-tests:    runs-on: ubuntu-24.04\ncontainer-deployment.yml:verify-default-branch:    runs-on: ubuntu-24.04\nai-privileged-merge.yml:cleanup_arm_receipt:    runs-on: ubuntu-24.04\nai-privileged-merge.yml:invalid_verjson_route:    runs-on: ubuntu-24.04\nai-privileged-merge.yml:validate_privileged_lane:    runs-on: ubuntu-24.04\napp-key-environment.yml:validate:    runs-on: ubuntu-24.04\ncontainer-candidate-publish.yml:acquire-private-node-dependencies:    runs-on: ubuntu-24.04\ncontainer-candidate-publish.yml:attest-sbom:    runs-on: ubuntu-24.04\ncontainer-candidate-publish.yml:candidate-manifest:    runs-on: ubuntu-24.04\ncontainer-candidate-publish.yml:mirror-gar:    runs-on: ubuntu-24.04\ncontainer-candidate-publish.yml:prepare:    runs-on: ubuntu-24.04\ncontainer-candidate-publish.yml:publish-base:    runs-on: ubuntu-24.04\ncontainer-candidate-publish.yml:publish-derived:    runs-on: ubuntu-24.04\ncontainer-release.yml:promote:    runs-on: ubuntu-24.04\nprivileged-merge-conformance.yml:audit:    runs-on: ubuntu-24.04'
expected_literal_hosted_sites="$(printf '%s\n' \
  "$expected_literal_hosted_sites" \
  $'changelog-contract-fleet-report.yml:report:    runs-on: ubuntu-24.04' \
  $'cli-projects-package-surface-required.yml:admission:    runs-on: ubuntu-24.04\ncli-projects-package-surface-required.yml:package-surface:    runs-on: ubuntu-24.04' \
  $'node-ci.yml:deferred-ci:    runs-on: ubuntu-24.04\nnode-ci-protected.yml:deferred-ci:    runs-on: ubuntu-24.04' \
  | sort)"

validate_literal_hosted_inventory() {
  local sites="$1"
  [ "$sites" = "$expected_literal_hosted_sites" ] || {
    printf 'fixed hosted selector inventory drifted: %s\n' "$sites" >&2
    return 1
  }
}
if inventory_error="$(validate_literal_hosted_inventory "$literal_hosted_sites" 2>&1)"; then
  pass "reviewed security-boundary jobs use exact fixed hosted selectors"
else
  fail "$inventory_error"
fi

if python3 - "$root/.github/workflows/actions-ci.yml" <<'PY'
import copy
import sys

import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    document = yaml.safe_load(stream)


def validate_hosted_compatibility_job(candidate):
    jobs = candidate["jobs"]
    target = "hosted-compatibility-tests"
    unexpected = sorted(
        job_id
        for job_id, job in jobs.items()
        if job_id != target
        and isinstance(job, dict)
        and job.get("runs-on") == "ubuntu-24.04"
    )
    if unexpected:
        raise AssertionError(
            "literal hosted selector escaped compatibility job: "
            + ",".join(unexpected)
        )
    if jobs.get(target, {}).get("runs-on") != "ubuntu-24.04":
        raise AssertionError(f"{target} must own literal hosted selector")


validate_hosted_compatibility_job(document)
mutant = copy.deepcopy(document)
mutant["jobs"]["hosted-compatibility-tests"]["runs-on"] = (
    document["jobs"]["shell-tests"]["runs-on"]
)
mutant["jobs"]["shell-tests"]["runs-on"] = "ubuntu-24.04"
assert sum(
    isinstance(job, dict) and job.get("runs-on") == "ubuntu-24.04"
    for job in mutant["jobs"].values()
) == 1
try:
    validate_hosted_compatibility_job(mutant)
except AssertionError as error:
    assert str(error) == (
        "literal hosted selector escaped compatibility job: shell-tests"
    )
    print("ok   - another actions-ci job cannot occupy the hosted exception")
else:
    raise AssertionError("another actions-ci job occupied the hosted exception")
PY
then
  pass "actions-ci hosted exception is bound to the compatibility job identity"
else
  fail "actions-ci hosted exception escaped the compatibility job identity"
fi

literal_hosted="$(
  awk 'NR == FNR { reviewed[$0] = 1; next } !($0 in reviewed)' \
    <(printf '%s\n' "$expected_literal_hosted_sites") \
    <(printf '%s\n' "$literal_hosted_sites")
)"
[ -z "$literal_hosted" ] \
  && pass "Verjson-local jobs contain no unreviewed literal GitHub-hosted runs-on selector" \
  || fail "unreviewed literal GitHub-hosted selectors found: $literal_hosted"

# A hosted fallback may only ever be reached by a caller OUTSIDE Verjson. A
# Verjson-owned job that resolves to `ubuntu-24.04` is not a soft fallback — it
# is a guaranteed failure, because GitHub-hosted minutes are unfunded for this
# org (#189). Every Verjson route must therefore land on self-hosted capacity
# the caller is actually admitted to (ADR 0033).
# The premise above is FALSE as stated and is corrected by ADR 0047/0048:
# hosted minutes are free for PUBLIC repositories (billing measured 2026-08-01);
# private ones are capped by a spending limit, which is a budget knob rather than
# an impossibility. The invariant that still holds is narrower — a Verjson job
# must not land on hosted *by accident*. Deliberate, reviewed routes are allowed.
#
# Sanctioned exceptions:
#  * actionlint's `github-hosted-runner` input (ADR 0026) — explicit per-caller
#    opt-in, never a default a Verjson caller falls into.
#  * `CI_RUNNER_FASTLANE` (ADR 0047/0048) — the fast lane. Selected by an
#    org variable, so it is repointable at self-hosted capacity without editing
#    a workflow, and it always carries a fallback.
#  * `fleet-watchdog.yml` — polices the self-hosted fleet, so gating it on that
#    fleet would leave it queued behind the jam it exists to clear. It runs only
#    in this repository, which is public, so its minutes are free.
#  * `privileged-merge-conformance.yml` — holds the privileged audit token, so
#    its exact fixed hosted selector prevents mutable variables or a persistent
#    worker from choosing the token's execution environment (ADR 0089).
#  * `ai-privileged-merge.yml`'s one-minute `cleanup_arm_receipt` job — carries
#    no secret or environment, receives only a validated artifact ID after the
#    exact merged head is confirmed, and uses fixed hosted capacity to keep the
#    deletion cleanup independent of persistent fleet state (ADR 0207).
#  * `ai-privileged-merge.yml`'s invalid-route guard — deliberately carries no
#    credential and uses fixed hosted capacity so unavailable visibility or an
#    unregistered route produces an observable failure without trusting a
#    persistent worker or mutable placement variable (ADR 0089).
#  * container candidate publication and release promotion — every job that
#    contributes deployable bytes or attestations uses fixed hosted capacity so
#    the managed fleet cannot act as its own provenance root (ADR 0148).
#  * `actions-ci.yml`'s compatibility-contract job — the contract requires
#    bubblewrap, which the persistent platform image does not provide. Its
#    literal hosted image is pinned by ADR 0146 and aggregated into the existing
#    required context rather than falling back or passing conditionally.
#  * the tail of a lane chain, `vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]'`
#    — ADR 0040's portability contract. It is only reached when an organization
#    has no lane variable set at all, and hosted is the one landing that works
#    without knowing anything about its fleet. The tail it replaces named a
#    Verjson fleet label, which put a fleet rename inside a file that every
#    consumer would have had to edit (#401).
#  * node-ci's secretless acquisition job (ADR 0086) — it carries a package
#    credential while reading a PR-controlled lockfile, so it may use only the
#    isolated untrusted lane or a fresh hosted runner, never the trusted fallback.
#  * node-ci's `deferred-ci` job (ADR 0178) — it exists only on a defer, checks
#    nothing out, declares `permissions: {}`, and runs one `echo` plus `exit 1`.
#    Its entire purpose is to CONCLUDE non-SUCCESS, and a saturated or offline
#    self-hosted lane would leave it PENDING instead — indistinguishable from a
#    queued run to the rollup assertion that reads it. It consumes none of the
#    compute or credential reach this policy protects, so fixed hosted placement
#    is the reviewed route. Job identity is bound by the exact inventory above.
# Exact raw hosted selectors are governed by the file/job inventory above;
# this expression sweep handles portable fallbacks and other embedded routes.
# The generated release callers' selector (gen-changelog-caller.sh
# release_runner_expr) is a Verjson-guarded route whose hosted fallback is
# reachable only outside Verjson; it is governed by
# generated-changelog-selector-policy.test.py and appears here since this
# repository adopted its own generated release-snapshot caller (#1374).
unsafe_portable="$(
  grep -HnE "^    runs-on:.*ubuntu-(24\\.04|latest)" "${workflow_files[@]}" \
    | grep -v "github.repository_owner != 'Verjson' && 'ubuntu-24.04'" \
    | grep -v "github.repository_owner == 'Verjson'.*|| 'ubuntu-24.04'" \
    | grep -vF "github.repository_owner == 'Verjson' && (vars.CI_RUNNER_DEFAULT || '[\"self-hosted\",\"general\"]') || '[\"ubuntu-24.04\"]'" \
    | grep -vE '/ai-privileged-merge\.yml:[0-9]+:.*github\.event\.repository\.visibility == '\''public'\'' && '\''ubuntu-24\.04'\''.*self-hosted.*general' \
    | grep -vE ':[0-9]+:[[:space:]]+runs-on: ubuntu-24\.04$' \
    | grep -v "inputs.github-hosted-runner" \
    | grep -v "vars.CI_RUNNER_FASTLANE" \
    | grep -v "vars.CI_LANE_UNTRUSTED || '\[\"ubuntu-24.04\"\]'" \
    | sed "s/vars\.CI_LANE_FALLBACK || '\\[\"ubuntu-24\.04\"\\]'//" \
    | grep -E "ubuntu-(24\\.04|latest)" \
    || true
)"

# Every self-hosted lane selector must end at CI_LANE_FALLBACK. A chain that
# stops at its own lane variable leaves an org that set only the fallback with an
# empty `runs-on`, and one that stops before the hosted tail leaves an org with no
# lane variables unplaceable — the queue-forever failure, one level up from the
# literal labels this replaced.
#
# The OS-scoped lanes of ADR 0103 (#814) are the one exception, and it runs the
# other way: `CI_LANE_TRUSTED_MACOS` and `_WINDOWS` must carry NO fallback,
# because degrading a macOS leg to Linux produces a non-installable artifact
# behind a green check. The trailing `([^A-Z_]|$)` is what encodes that — without
# it, `CI_LANE_TRUSTED_MACOS` matches the `TRUSTED` alternative and this
# rule would demand exactly the fallback ADR 0103 forbids. The exception is
# encoded here rather than left for the OS lane to evade.
lane_selector_pattern="vars\\.CI_LANE_(TRUSTED|UNTRUSTED|PRIVILEGED)([^A-Z_]|\$)"
lane_without_fallback="$(
  grep -HnE "^    runs-on:.*$lane_selector_pattern" "${workflow_files[@]}" \
    | grep -v "vars.CI_LANE_FALLBACK" \
    | grep -v "vars.CI_LANE_UNTRUSTED || '\[\"ubuntu-24.04\"\]'" || true
)"

# The exception, asserted directly rather than inferred from a green sweep. This
# repository has no OS-lane workflow — R5 forbids one — so the narrowing above
# is unobservable against the live tree, and a revert to the unanchored pattern
# would leave every assertion in this file passing. Both polarities, because
# over-narrowing (dropping the ordinary lanes) fails just as silently.
grep -qE "$lane_selector_pattern" \
  <<<"    runs-on: \${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK) }}" \
  && ! grep -qE "$lane_selector_pattern" \
    <<<"    runs-on: \${{ fromJSON(vars.CI_LANE_TRUSTED_MACOS) }}" \
  && pass "the fallback rule binds the ordinary lanes and exempts the OS-scoped lanes (ADR 0103)" \
  || fail "the fallback rule's OS-lane exception drifted: it must match CI_LANE_TRUSTED and not CI_LANE_TRUSTED_MACOS"
[ -z "$lane_without_fallback" ] \
  && pass "every lane selector falls through to CI_LANE_FALLBACK" \
  || fail "lane selector that cannot degrade: $lane_without_fallback"

# No `runs-on` may name a fleet label. The lane variables exist so that a relabel,
# a provider move, or a pool rename is an org-variable edit — never a pull request
# in this repository, and never one in each of ~90 consumers (ADR 0041).
# Keyed on the LITERAL ARRAY, not on the word `self-hosted`: `runs-on: [general]`
# is just as unplaceable after a relabel, and keying on the word let it through
# (#401 review). Hosted literals are caught separately by `literal_hosted`.
fleet_label="$(
  grep -HnE "^    runs-on:[[:space:]]*\[" "${workflow_files[@]}" \
    | grep -vE '/ai-privileged-merge\.yml:[0-9]+:[[:space:]]+runs-on: \[self-hosted, general\]$' \
    | grep -vE "/runner-canary\\.yml:[0-9]+:[[:space:]]+runs-on: \\[self-hosted, '\\\$\\{\\{ inputs\\.runner_label \\}\\}'\\]$" \
    || true
)"
[ -z "$fleet_label" ] \
  && pass "no runs-on names a fleet label; lanes are selected by intent" \
  || fail "runs-on names a fleet label instead of a lane: $fleet_label"

# The fast lane must keep a fallback: a bare `fromJSON(vars.X)` with no `||`
# breaks every consumer the moment the variable is unset.
fastlane_no_fallback="$(
  grep -HnE "^    runs-on:.*CI_RUNNER_FASTLANE" "${workflow_files[@]}" \
    | grep -v "CI_RUNNER_FASTLANE ||" || true
)"
[ -z "$fastlane_no_fallback" ] \
  && pass "every fast-lane selector keeps a fallback for an unset variable" \
  || fail "fast-lane selector without a fallback: $fastlane_no_fallback"
[ -z "$unsafe_portable" ] \
  && pass "hosted fallbacks are reachable only by callers outside Verjson" \
  || fail "hosted fallback reachable by a Verjson caller: $unsafe_portable"

grep -qF "runs-on: \${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') }}" "$workflows/gate-rearm.yml" \
  && pass "the required authorization arm routes through the trusted organization lane" \
  || fail "gate-rearm required-workflow lane routing drifted"

grep -qF "github.repository_owner != 'Verjson'" "$workflows/actionlint.yml" \
  && grep -qF 'vars.CI_LANE_FALLBACK' "$workflows/actionlint.yml" \
  && pass "actionlint preserves its bounded external hosted compatibility path" \
  || fail "actionlint runner contract drifted"

# ADR 0047 moves this suite off the shared pool. The contract is that it routes
# through a VARIABLE with a fallback chain, never a hardcoded label: the lane has
# to be repointable at a self-hosted pool from org settings alone, and an unset
# variable must degrade to the ADR 0034 general pool rather than to nothing.
grep -qF 'vars.CI_RUNNER_FASTLANE || vars.CI_LANE_TRUSTED' "$workflows/actions-ci.yml" \
  && grep -qF 'vars.CI_LANE_FALLBACK' "$workflows/actions-ci.yml" \
  && ! grep -qF 'runs-on: [self-hosted, general]' "$workflows/actions-ci.yml" \
  && pass "repository shell validation routes through the fast-lane variable with a general fallback" \
  || fail "actions-ci fast-lane routing drifted (ADR 0047)"

# These used to be pinned to the literal `[self-hosted, general]`. A literal label
# is a rename away from an unplaceable job, and GitHub does not fail an
# unplaceable job — it queues it with no diagnostic. That is not hypothetical:
# the fleet moved GCP→DigitalOcean and `Verjson/verjson-identity-lifecycle`'s
# `generated-docs` job asked for `[self-hosted, GCP]`, a label no runner carries
# any more, until `35c1efa1` (#401). Repository-local jobs now select the lane the same way
# every reusable workflow does — through the variable, with a fallback — so a
# relabel is an org-variable flip rather than a pull request per workflow, which
# is what ADR 0041 requires.
#
# runner-admission-reconcile.yml is absent because it must observe the general
# pool from outside that pool (#401, ADR 0054).
for local_workflow in rework-reconcile.yml; do
  off_lane="$(
    grep -E '^    runs-on:' "$workflows/$local_workflow" \
      | grep -vF "runs-on: \${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') }}" \
      || true
  )"
  if [ -n "$off_lane" ]; then
    fail "$local_workflow has a repository-local job that does not select the trusted lane: $off_lane"
  else
    pass "$local_workflow selects the trusted lane by name"
  fi
done

# Inverted AND pinned to the whole line. "No line says [self-hosted, general]"
# would stay green if a second job were appended on any other literal; a prefix
# match on `fromJSON(vars.CI_RUNNER_FASTLANE` would stay green if the
# selector were tidied into the chained form actions-ci.yml uses, which ends at
# the general pool — putting the monitor back inside what it watches (#401 review).
off_fastlane="$(
  grep -E '^    runs-on:' "$workflows/runner-admission-reconcile.yml" \
    | grep -vF "runs-on: \${{ fromJSON(vars.CI_RUNNER_FASTLANE || '[\"ubuntu-24.04\"]') }}" \
    || true
)"
if [ -n "$off_fastlane" ]; then
  fail "runner-admission-reconcile.yml has a job off the fast lane, watching the pool from inside it (#401): $off_fastlane"
else
  pass "the admission monitor is routed off the pool it watches"
fi


# --------------------------------------------------------------------------
# Runner routing policy (ADR 0033, supersedes the ADR 0031 allowlist).
#
# Three tiers, resolved in order:
#   1. an explicit `runner` input                      — per-caller override
#   2. caller outside Verjson                          — 'ubuntu-24.04'
#   3. private Verjson repository                     — default org variable
#   4. public or unresolved Verjson repository        — untrusted org variable
#
# Every case below evaluates the REAL `runs-on:` expression awk-extracted from
# the workflow (single source of truth — no copy of any expression lives here).
# --------------------------------------------------------------------------

# With no lane variable set at all, the defined landing is the portable hosted
# default — the trailing `'["ubuntu-24.04"]'` in every lane chain. That is the
# portability contract of ADR 0040, not a safety net: it exists so an org with
# none of these variables lands somewhere sane. It replaces a hardcoded
# `["self-hosted","general"]` tail, which named a fleet label from inside a file
# a rename cannot reach (#401).
UNSET_LANDING='["ubuntu-24.04"]'

# Extract the `runs-on:` value of one job block, verbatim.
extract_runs_on() {
  awk -v job="  $2:" '
    $0 == job { in_job = 1; next }
    in_job && /^  [^[:space:]#]/ { in_job = 0 }
    in_job && /^    runs-on:/ {
      sub(/^    runs-on:[[:space:]]*/, "")
      print
      exit
    }
  ' "$1"
}

evaluator="$(mktemp)"
mutated_workflow="$(mktemp)"
trap 'rm -f "$evaluator" "$mutated_workflow"' EXIT
cat >"$evaluator" <<'JS'
// Evaluate the GitHub Actions expression subset used by `runs-on:`. The
// operators (&&, ||, ==, !=), single-quoted strings and value-returning
// short-circuit semantics are shared with JavaScript, so the extracted
// expression is evaluated as-is rather than reimplemented.
//
// `private` arrives as 'true' | 'false' | '' — the empty case models an event
// payload that carries no repository visibility, which tier 4 must catch.
//
// Actions compares mixed types by coercing to number (null -> 0, false -> 0,
// true -> 1), so `null == false` is TRUE there while JS `null == false` is
// false. Modelling unresolved visibility as 0 rather than undefined/null makes
// JS's own loose `==` agree with Actions on BOTH polarities: 0 == true is
// false, 0 == false is true. That matters because ai-review-merge.yml already
// makes this same decision with `== false`; a future flip here must be
// validated against real semantics, not against a model that only happens to
// agree with the polarity in use today.
const vm = require('node:vm');
const [, , raw, repository, runnerInput, priv, varDefault, varUntrusted, varFastlane, runnerLabelsInput, varOverflow] = process.argv;
// `inputs.github-hosted-runner` is a legal Actions reference and an illegal JS
// one — bare, it parses as `inputs.github - hosted - runner`. Rewriting it to a
// bracket access is what lets actionlint.yml be EVALUATED here rather than only
// grepped, which is how its visibility polarity went untested until ADR 0050.
const body = raw.trim().replace(/^\$\{\{/, '').replace(/\}\}$/, '')
  .replace(/inputs\.github-hosted-runner/g, "inputs['github-hosted-runner']")
  .replace(/inputs\.secretless-pr/g, "inputs['secretless-pr']");
const github = {
  repository,
  repository_owner: repository.split('/')[0],
  event: {
    repository: {
      private: priv === '' ? 0 : priv === 'true',
      // A STRING, and the reason ADR 0050 routes on it. `private` is a boolean
      // that Actions coerces to 0 when the payload omits it, so `private ==
      // false` is TRUE for an unreadable repository as well as a public one —
      // a fail-OPEN that would spend hosted minutes on a repo it could not
      // read. `visibility` is '' in that case, and '' == 'public' is false.
      visibility: priv === '' ? '' : priv === 'true' ? 'private' : 'public',
    },
  },
};
// `github-hosted-runner` is modelled as false throughout: it is the per-caller
// opt-in of ADR 0026, so every case here is the default one where a Verjson
// caller has NOT opted in.
//
// `runner_labels` is the merge workflows' equivalent override and is modelled
// as '' when omitted, which is what Actions passes for an optional string input
// with no default. Modelling it as undefined would agree by accident: both are
// falsy, so the omitted-input cases would pass while nothing proved a SUPPLIED
// value is honoured — the assertion that fails when it is not modelled at all.
const inputs = {
  runner: runnerInput,
  runner_labels: runnerLabelsInput === undefined ? '' : runnerLabelsInput,
  privileged_lane: varDefault,
  'github-hosted-runner': false,
  'secretless-pr': false,
};
// preflight resolves the TARGET repository's visibility and publishes it as a
// STRING ('true' | 'false' | '' when unreadable). gate and dispatch-merge route
// on that rather than on `github.event.repository`, because on the dispatch path
// the event repository is the dispatcher, not the target.
  const needs = {
    preflight: { outputs: { target_private: priv } },
    resolve_privileged_route: { outputs: { selector: '' } },
  };
// An unset Actions variable is the empty string, not undefined.
const vars = {
  // Lane variables name what the work IS (ADR 0040). The pool variables they
  // replace are kept in the model, not because any workflow still reads them,
  // but because an org mid-migration still has both set and a stray reference
  // would otherwise resolve to undefined here and pass.
  CI_LANE_TRUSTED: varDefault,
  CI_LANE_UNTRUSTED: varUntrusted,
  // Privileged and fallback share the private-lane fixture, following the same
  // reasoning the isolated lane used: call sites supply one self-hosted value,
  // and the preference ORDER is pinned by the structural assertions below
  // rather than by these values. A case that sets varDefault to '' therefore
  // models "this org has no lane variables at all", whose defined landing is
  // the portable hosted default.
  CI_LANE_PRIVILEGED: varDefault,
  CI_LANE_FALLBACK: varDefault,
  CI_RUNNER_DEFAULT: varDefault,
  CI_RUNNER_UNTRUSTED: varUntrusted,
  CI_RUNNER_ISOLATED: varDefault,
  CI_RUNNER_FASTLANE: varFastlane,
  // ADR 0053's overflow lane, and the one variable that MUST come from its own
  // fixture rather than being left off the model. It is the FIRST term of the
  // tail in all three ai-review-merge.yml chains, and the organization sets it
  // org-wide today, so omitting it from `vars` resolves it to `undefined` here
  // and every case silently asserts a route production cannot currently
  // produce. Unset is '' (an unset Actions variable is the empty string), so a
  // case that leaves it out still models "this org has not enabled overflow" —
  // deliberately, rather than by accident.
  CI_RUNNER_OVERFLOW: varOverflow === undefined ? '' : varOverflow,
};
const fromJSON = (value) => JSON.parse(value);
// GitHub Actions string equality and contains() are case-insensitive; JS === is not.
// This evaluator is therefore stricter than production. `contains` is kept even
// though the routing expressions no longer use it (the ADR 0031 allowlist is
// retired) because ai-review-merge.yml still does, and this evaluator is the
// obvious thing to reach for the next time a runs-on expression needs checking.
const contains = (haystack, needle) =>
  Array.isArray(haystack)
    ? haystack.some((item) => item === needle)
    : String(haystack).includes(needle);

// Evaluate in a fresh vm context rather than `new Function` (#187). The input is
// awk-extracted from workflow files in this repository, so it is not untrusted
// today — anyone who can edit those can already edit this .test.sh, which bash
// executes outright. The change is about the pattern, not this call site: a
// context built from a null-prototype object exposes no `process`, `require`,
// `fetch`, or module loader, so the same helper stays safe if it is ever pointed
// at a workflow from a fork or another org.
//
// This is hardening, not a security boundary — Node documents `vm` as not a
// sandbox against hostile code. What it does buy: ambient authority is gone by
// construction, and the timeout bounds a pathological expression instead of
// hanging CI.
const context = vm.createContext(
  Object.assign(Object.create(null), { github, inputs, needs, vars, fromJSON, contains }),
);
const resolved = vm.runInContext(`(${body})`, context, {
  timeout: 5000,
  filename: 'runs-on-expression',
});
process.stdout.write(
  typeof resolved === 'string' ? resolved : JSON.stringify(resolved),
);
JS

# A missing evaluator must fail the suite, never silently skip these cases.
if ! command -v node >/dev/null 2>&1; then
  fail "node is required to evaluate the extracted runs-on expression"
  exit 1
fi

# The evaluator must carry no ambient authority (#187). These probes are what
# stops a future revert to `new Function` for ambient globals such as `process`;
# the `require` probe separately documents that neither evaluator exposes the
# CommonJS wrapper's module-local loader.
assert_no_ambient() {
  local expression="$1" symbol="$2"
  local out
  out="$(node "$evaluator" "$expression" Verjson/example '' true '' '' 2>&1)" && {
    fail "evaluator resolved '$symbol' instead of rejecting it: $out"
    return
  }
  case "$out" in
    *"$symbol is not defined"*) pass "evaluator context exposes no $symbol" ;;
    *) fail "evaluator rejected '$symbol' for the wrong reason: $out" ;;
  esac
}

assert_no_ambient '${{ process.env.HOME }}' process
assert_no_ambient '${{ require("node:fs") }}' require

# assert_route <workflow> <job> <repo> <runner-input> <private> <var-default> <var-untrusted> <expected> <label> [var-fastlane] [runner-labels-input] [var-overflow]
#
# `runner-labels-input` is separate from `runner-input` rather than folded into
# it: they are different inputs on different workflows (`inputs.runner` on the
# reusable CI workflows, `inputs.runner_labels` on the two merge workflows), and
# one fixture feeding both would make a case pass for the wrong reason the first
# time a workflow reads both.
assert_route() {
  local workflow="$1" job="$2" repository="$3" runner_input="$4" priv="$5"
  local var_default="$6" var_untrusted="$7" expected="$8" label="$9"
  local var_fastlane="${10-}" runner_labels="${11-}" var_overflow="${12-}"
  local resolved
  resolved="$(resolve_route "$workflow" "$job" "$repository" "$runner_input" \
    "$priv" "$var_default" "$var_untrusted" "$var_fastlane" "$runner_labels" \
    "$var_overflow")" || {
    fail "$label — $resolved"
    return
  }
  [ "$resolved" = "$expected" ] \
    && pass "$label" \
    || fail "$label (expected '$expected', got '$resolved')"
}

resolve_route() {
  local workflow="$1" job="$2" repository="$3" runner_input="$4" priv="$5"
  local var_default="$6" var_untrusted="$7" var_fastlane="$8"
  local runner_labels="$9" var_overflow="${10-}"
  local expression
  expression="$(extract_runs_on "$workflow" "$job")"
  if [ -z "$expression" ]; then
    echo "could not extract runs-on for job '$job' from $(basename "$workflow")"
    return 1
  fi
  if [ "$expression" = "ubuntu-24.04" ]; then
    printf '%s' "$expression"
    return 0
  fi
  node "$evaluator" "$expression" "$repository" "$runner_input" \
    "$priv" "$var_default" "$var_untrusted" "$var_fastlane" "$runner_labels" \
    "$var_overflow" 2>&1
}

mutate_job_expression() {
  local source="$1" job="$2" old="$3" new="$4" destination="$5"
  awk -v job="  $job:" -v old="$old" -v new="$new" '
    $0 == job { in_job = 1 }
    in_job && /^    runs-on:/ {
      at = index($0, old)
      if (at > 0) {
        $0 = substr($0, 1, at - 1) new substr($0, at + length(old))
      }
      in_job = 0
    }
    { print }
  ' "$source" >"$destination"
}

fixed_hosted_provenance_targets() {
  cat <<'TARGETS'
ai-privileged-merge.yml cleanup_arm_receipt
container-candidate-publish.yml prepare
container-candidate-publish.yml acquire-private-node-dependencies
container-candidate-publish.yml publish-base
container-candidate-publish.yml publish-derived
container-candidate-publish.yml attest-sbom
container-candidate-publish.yml candidate-manifest
container-candidate-publish.yml mirror-gar
container-release.yml promote
TARGETS
}

validate_fixed_hosted_provenance_job() {
  local workflow="$1" job="$2" expression
  expression="$(extract_runs_on "$workflow" "$job")"
  [ "$expression" = "ubuntu-24.04" ] || {
    printf "hosted-provenance job %s/%s must use exact hosted selector 'ubuntu-24.04' (got '%s')\n" \
      "$(basename "$workflow")" "$job" "$expression" >&2
    return 1
  }
}

while read -r provenance_workflow provenance_job; do
  provenance_path="$workflows/$provenance_workflow"
  if provenance_error="$(validate_fixed_hosted_provenance_job \
    "$provenance_path" "$provenance_job" 2>&1)"; then
    pass "$provenance_workflow/$provenance_job uses exact hosted provenance"
  else
    fail "$provenance_error"
  fi

  for provenance_mutation in trusted-lane ubuntu-latest wrong-version; do
    case "$provenance_mutation" in
      trusted-lane)
        provenance_replacement='${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK) }}'
        ;;
      ubuntu-latest)
        provenance_replacement='ubuntu-latest'
        ;;
      wrong-version)
        provenance_replacement='ubuntu-22.04'
        ;;
    esac
    mutate_job_expression "$provenance_path" "$provenance_job" \
      'ubuntu-24.04' "$provenance_replacement" "$mutated_workflow"
    if provenance_error="$(validate_fixed_hosted_provenance_job \
      "$mutated_workflow" "$provenance_job" 2>&1)"; then
      fail "$provenance_workflow/$provenance_job $provenance_mutation mutation survived"
    else
      case "$provenance_error" in
        *"must use exact hosted selector 'ubuntu-24.04'"*)
          pass "$provenance_workflow/$provenance_job rejects $provenance_mutation for exact hosted-provenance reason"
          ;;
        *)
          fail "$provenance_workflow/$provenance_job $provenance_mutation failed for wrong reason: $provenance_error"
          ;;
      esac
    fi
  done
done < <(fixed_hosted_provenance_targets)

retention_expression="$(extract_runs_on "$workflows/container-release.yml" retention)"
mutate_job_expression "$workflows/container-release.yml" retention \
  "$retention_expression" 'ubuntu-24.04' "$mutated_workflow"
mutated_release_sites="$(
  literal_hosted_job_sites "$mutated_workflow" \
    | sed "s#^$(basename "$mutated_workflow"):#container-release.yml:#"
)"
mutated_literal_hosted_sites="$(
  {
    printf '%s\n' "$literal_hosted_sites" | grep -v '^container-release\.yml:'
    printf '%s\n' "$mutated_release_sites"
  } | sort
)"
if inventory_error="$(validate_literal_hosted_inventory \
  "$mutated_literal_hosted_sites" 2>&1)"; then
  fail "unallowlisted container-release retention literal-hosted mutation survived"
else
  case "$inventory_error" in
    *"fixed hosted selector inventory drifted:"*)
      pass "unallowlisted literal-hosted job fails the exact inventory"
      ;;
    *)
      fail "unallowlisted literal-hosted mutation failed for wrong reason: $inventory_error"
      ;;
  esac
fi

# GitHub context selects terminal capacity directly; no runner-produced output
# is trusted to place the secret-bearing job. The exact admission guard is
# covered by the semantic privileged-caller contract, while this sweep executes
# the hosted, persistent, and external routing expressions.
# shellcheck disable=SC2043 # Keep this loop aligned with the privileged workflow set.
for privileged_workflow in ai-privileged-merge.yml; do
  privileged_path="$workflows/$privileged_workflow"
  privileged_runs_on="$(extract_runs_on "$privileged_path" privileged_merge)"
  ! grep -qE 'needs\..*outputs|resolve_privileged_route' <<<"$privileged_runs_on" \
    && pass "$privileged_workflow terminal route trusts no runner-produced selector" \
    || fail "$privileged_workflow terminal route trusts runner-produced placement data"

  mutate_job_expression "$privileged_path" privileged_merge \
    "$privileged_runs_on" '${{ fromJSON(needs.route.outputs.selector) }}' \
    "$mutated_workflow"
  mutated_privileged_runs_on="$(extract_runs_on "$mutated_workflow" privileged_merge)"
  grep -qE 'needs\..*outputs|resolve_privileged_route' <<<"$mutated_privileged_runs_on" \
    && pass "$privileged_workflow rejects runner-produced secret-bearing selector" \
    || fail "$privileged_workflow runner-produced selector mutation escaped"

  assert_route "$privileged_path" privileged_merge Verjson/.github '' false \
    '[]' '[]' \
    'ubuntu-24.04' \
    "$privileged_workflow — exact public canonical consumer takes fixed hosted capacity"

  assert_route "$privileged_path" privileged_merge Verjson/private-consumer '' true \
    '["ubuntu-24.04"]' '[]' \
    '["ubuntu-24.04"]' \
    "$privileged_workflow — private Verjson consumer takes the exact hosted lane"

  assert_route "$privileged_path" privileged_merge Acme/widgets '' true \
    '["self-hosted","isolated-canary"]' '["self-hosted","untrusted-canary"]' \
    'ubuntu-24.04' \
    "$privileged_workflow — external privileged caller retains hosted portability"

  # The escape hatch, exercised rather than assumed: a self-hosted consumer
  # OUTSIDE Verjson has no lane variables, so the input must still beat the
  # hosted portability route. Keeping the input is the reason #405 makes it
  # optional instead of deleting it.
  assert_route "$privileged_path" privileged_merge Acme/widgets '' true \
    '' '' \
    '["self-hosted","acme-fleet"]' \
    "$privileged_workflow — an off-Verjson fleet still wins via runner_labels" \
    '' '["self-hosted","acme-fleet"]'

  # #676 cutover: compare the input-backed expression with its exact admitted
  # hosted literal. This proves every current caller shape resolves as intended
  # without allowing a fallback or runner-produced selector.
  legacy_workflow="$mutated_workflow"
  mutate_job_expression "$privileged_path" privileged_merge \
    'fromJSON(inputs.privileged_lane)' \
    'fromJSON('\''["ubuntu-24.04"]'\'')' "$legacy_workflow"

  assert_same_existing_route() {
    local label="$1" repository="$2" private="$3" lane="$4" runner_labels="$5"
    local before after
    before="$(resolve_route "$legacy_workflow" privileged_merge "$repository" '' \
      "$private" "$lane" '' '' "$runner_labels" '')" || {
      fail "$label — legacy resolution failed: $before"
      return
    }
    after="$(resolve_route "$privileged_path" privileged_merge "$repository" '' \
      "$private" "$lane" '' '' "$runner_labels" '')" || {
      fail "$label — input-backed resolution failed: $after"
      return
    }
    [ "$after" = "$before" ] \
      && pass "$label resolves byte-identically before and after" \
      || fail "$label changed from '$before' to '$after'"
  }

  assert_same_existing_route "public Verjson caller without privileged_lane" \
    Verjson/.github false '' ''
  assert_same_existing_route "private Verjson generated caller with the admitted hosted literal" \
    Verjson/private-consumer true '["ubuntu-24.04"]' ''
  assert_same_existing_route "external hosted caller without privileged_lane" \
    Acme/widgets true '' ''
  assert_same_existing_route "external self-hosted caller without privileged_lane" \
    Acme/widgets true '' '["self-hosted","acme-fleet"]'
done

# #487 / ADR 0064. The requirement travels with the JOB, not with the file, so it
# is DISCOVERED rather than listed. #487 existed because ADR 0042 split
# `privileged_merge` into its own file and the overflow term did not follow it; a
# hardcoded pair list here would have had exactly the same blind spot, since
# nobody updates a list they do not know exists. Discovery means the next split
# fails this assertion instead of shipping silently.
#
# The predicate is deliberately NARROWER than "has a sleep loop". What ADR 0053
# is about is a job that holds a pool runner while waiting on OTHER WORK THAT
# ALSO NEEDS A POOL RUNNER — that circularity is the deadlock. So both halves are
# required: a `sleep` (it parks) AND a query against GitHub for the state of
# checks it does not itself run (what it parks ON).
#
# `sleep` alone is a false positive, and there is a live one: `node-ci.yml`'s
# `build-test` sleeps waiting for its own Docker Postgres to accept connections.
# That wait needs no runner and starves nothing, and routing the fleet's CI onto
# hosted overflow would be a far larger decision than #487 — reached by accident,
# through a loose regex, on a job nobody was looking at.
polling_jobs="$(
  for wf in "${workflow_files[@]}"; do
    awk -v file="$wf" '
      /^jobs:[[:space:]]*$/ { in_jobs = 1; next }
      !in_jobs { next }
      /^[^[:space:]#]/ { in_jobs = 0; next }
      # A job key: exactly two spaces of indent, inside `jobs:`. The optional
      # trailing comment is not cosmetic — without it, `  gate:  # note` is not
      # recognised as a key, so the job body that follows is attributed to the
      # PREVIOUS job. That misattribution is a false NEGATIVE in the direction
      # that matters: a poller inheriting a neighbour that already has overflow
      # passes while being unrouted.
      /^  [^[:space:]#][^:]*:[[:space:]]*(#.*)?$/ {
        if (job != "") print file "\t" job "\t" (parks && waits_on_checks && !delegates) "\t" runs_on
        job = $0
        sub(/^  /, "", job); sub(/:[[:space:]]*(#.*)?$/, "", job)
        parks = 0; waits_on_checks = 0; delegates = 0; runs_on = ""
        next
      }
      job == "" { next }
      /^    runs-on:/ && runs_on == "" {
        runs_on = $0
        sub(/^    runs-on:[[:space:]]*/, "", runs_on)
      }
      # A job-level `uses:` calls a reusable workflow and declares no runner of
      # its own; the requirement belongs to the CALLED workflow, which this sweep
      # scans on its own if it lives here. Without this, such a job reports an
      # empty `runs-on` and fails for a reason that is not #487.
      /^    uses:/ { delegates = 1 }
      /[^[:alnum:]_]sleep[[:space:]]+[0-9]/ { parks = 1 }
      /statusCheckRollup|check-runs|commits\/[^ ]*\/status/ { waits_on_checks = 1 }
      END { if (job != "") print file "\t" job "\t" (parks && waits_on_checks && !delegates) "\t" runs_on }
    ' "$wf"
  done | awk -F'\t' '$3 == 1'
)"
# ADR 0079's desired state is no runner-held merge-gate polling job.
[ -z "$polling_jobs" ] \
  && pass "no merge-gate job parks a runner while waiting on checks" \
  || fail "a merge-gate polling job remains after the event-driven redesign"
while IFS=$'\t' read -r poll_file poll_job _ poll_runs_on; do
  [ -n "$poll_file" ] || continue
  case "$poll_runs_on" in
    *'vars.CI_RUNNER_OVERFLOW'*)
      pass "$(basename "$poll_file") — polling job '$poll_job' can reach the overflow lane" ;;
    *)
      fail "$(basename "$poll_file") — polling job '$poll_job' holds a pool runner while polling but cannot reach vars.CI_RUNNER_OVERFLOW (#487, ADR 0064 — add it at the head of the LANE tail, after inputs.runner_labels, never before): $poll_runs_on" ;;
  esac
done <<<"$polling_jobs"

dispatch_workflow="$workflows/ai-review-merge.yml"
grep -qF 'CI_LANE_PRIVILEGED || vars.CI_LANE_FALLBACK' "$dispatch_workflow" \
  && pass "dispatch-merge prefers the privileged lane, then the fallback" \
  || fail "dispatch-merge lost its privileged/fallback preference"
# ADR 0048's complete visibility matrix. These assertions evaluate each exact
# job expression independently; a repository-wide substring check could stay
# green when one job inverted its polarity and another retained the right text.
assert_route "$dispatch_workflow" preflight Verjson/.github '' false \
  '["trusted-canary"]' '["untrusted-canary"]' '["fastlane-canary"]' \
  "preflight — public Verjson event takes the fast lane" '["fastlane-canary"]'
assert_route "$dispatch_workflow" preflight Verjson/.github '' true \
  '["trusted-canary"]' '["untrusted-canary"]' '["untrusted-canary"]' \
  "preflight — private Verjson event stays on the untrusted lane" '["fastlane-canary"]'
# preflight has not resolved TARGET_REPO yet. This empty event-field case pins
# Actions' loose boolean coercion exactly; gate and dispatch-merge below carry
# the fail-closed target-visibility contract.
assert_route "$dispatch_workflow" preflight Verjson/.github '' '' \
  '["trusted-canary"]' '["untrusted-canary"]' '["fastlane-canary"]' \
  "preflight — unresolved event visibility follows Actions boolean coercion" '["fastlane-canary"]'
assert_route "$dispatch_workflow" preflight Acme/widgets '' true '' '' \
  'ubuntu-24.04' "preflight — external callers retain hosted portability"

assert_route "$dispatch_workflow" gate Verjson/.github '' false \
  '["trusted-canary"]' '["untrusted-canary"]' '["fastlane-canary"]' \
  "gate — public target takes the fast lane" '["fastlane-canary"]'
assert_route "$dispatch_workflow" gate Verjson/.github '' true \
  '["trusted-canary"]' '["untrusted-canary"]' '["trusted-canary"]' \
  "gate — private target stays on the trusted lane" '["fastlane-canary"]'
assert_route "$dispatch_workflow" gate Verjson/.github '' '' \
  '["trusted-canary"]' '["untrusted-canary"]' '["trusted-canary"]' \
  "gate — unresolved target visibility fails closed" '["fastlane-canary"]'
assert_route "$dispatch_workflow" gate Acme/widgets '' true '' '' \
  'ubuntu-24.04' "gate — external callers retain hosted portability"

assert_route "$dispatch_workflow" dispatch-merge Verjson/.github '' false \
  '["privileged-canary"]' '["untrusted-canary"]' '["fastlane-canary"]' \
  "dispatch-merge — public target takes the fast lane" '["fastlane-canary"]'
assert_route "$dispatch_workflow" dispatch-merge Verjson/.github '' true \
  '["privileged-canary"]' '["untrusted-canary"]' '["privileged-canary"]' \
  "dispatch-merge — private target stays on the privileged lane" '["fastlane-canary"]'
assert_route "$dispatch_workflow" dispatch-merge Verjson/.github '' '' \
  '["privileged-canary"]' '["untrusted-canary"]' '["privileged-canary"]' \
  "dispatch-merge — unresolved target visibility fails closed" '["fastlane-canary"]'
assert_route "$dispatch_workflow" dispatch-merge Acme/widgets '' true '' '' \
  'ubuntu-24.04' "dispatch-merge — external callers retain hosted portability"

# #411. A supplied runner_labels value is explicit consumer fleet authority and
# must beat every inferred route on dispatch-merge just as it does on preflight
# and gate. Four visibility/owner polarities keep a future rewrite from applying
# the input only to the originally reported off-Verjson case.
for dispatch_override_case in \
  'Verjson/.github|false|public Verjson target' \
  'Verjson/verjson-authn|true|private Verjson target' \
  'Verjson/.github||unresolved Verjson target' \
  'Acme/widgets|true|external target'; do
  IFS='|' read -r override_repo override_private override_label <<<"$dispatch_override_case"
  assert_route "$dispatch_workflow" dispatch-merge "$override_repo" '' "$override_private" \
    '["privileged-canary"]' '["untrusted-canary"]' '["consumer-fleet"]' \
    "dispatch-merge — runner_labels overrides the $override_label" \
    '["fastlane-canary"]' '["consumer-fleet"]' '["overflow-canary"]'
done

# Mutation fixtures prove that every matrix row is bound to its own job. The
# inverted public predicate must send a public target to that job's non-fast
# lane; if a mutation is not applied or the evaluator reads a different job,
# these assertions fail.
for mutation in \
  'preflight|github.event.repository.private == false|github.event.repository.private == true|["untrusted-canary"]' \
  "gate|needs.preflight.outputs.target_private == 'false'|needs.preflight.outputs.target_private == 'true'|[\"trusted-canary\"]" \
  "dispatch-merge|needs.preflight.outputs.target_private == 'false'|needs.preflight.outputs.target_private == 'true'|[\"privileged-canary\"]"; do
  IFS='|' read -r mutation_job old_predicate new_predicate mutation_expected <<<"$mutation"
  mutate_job_expression "$dispatch_workflow" "$mutation_job" \
    "$old_predicate" "$new_predicate" "$mutated_workflow"
  assert_route "$mutated_workflow" "$mutation_job" Verjson/.github '' false \
    "$mutation_expected" '["untrusted-canary"]' "$mutation_expected" \
    "$mutation_job — inverted-polarity mutation changes its own semantic route" \
    '["fastlane-canary"]'
done

# The same two #405 polarities for the gate job, the one a cross-org consumer
# actually calls: a Verjson caller that omits the input must still be lane-routed,
# and an off-Verjson self-hosted fleet must still be able to name itself.
#
# BOTH overflow polarities, because `CI_RUNNER_OVERFLOW` precedes the lane
# variables in this chain and the organization has it SET. Asserting only the
# unset case would claim a route production does not take: with overflow set,
# "not by label" still holds, but the lane the caller lands on is the overflow
# lane, not CI_LANE_TRUSTED. That is ADR 0053 working as designed — it is
# recorded here so the next reader does not take the lane-routing claim to mean
# the trusted lane specifically.
assert_route "$dispatch_workflow" gate Verjson/verjson-authn '' true \
  '["self-hosted","trusted-canary"]' '["self-hosted","untrusted-canary"]' \
  '["self-hosted","trusted-canary"]' \
  "gate — omitting runner_labels routes by lane, not by label (overflow unset)" \
  '' '' ''

assert_route "$dispatch_workflow" gate Verjson/verjson-authn '' true \
  '["self-hosted","trusted-canary"]' '["self-hosted","untrusted-canary"]' \
  '["ubuntu-24.04"]' \
  "gate — with the overflow lane set, as in production, the omitted input lands there" \
  '' '' '["ubuntu-24.04"]'

assert_route "$dispatch_workflow" gate Acme/widgets '' true '' '' \
  '["self-hosted","acme-fleet"]' \
  "gate — an off-Verjson fleet still wins via runner_labels" \
  '' '["self-hosted","acme-fleet"]'

# The un-regenerated consumer, which is most of them until the #365 sweep: a
# caller still passing the input must keep beating the overflow lane. This is the
# precedence ADR 0053's exclusion paragraph rests on, so it is asserted rather
# than assumed to survive #405.
assert_route "$dispatch_workflow" gate Verjson/verjson-authn '' true \
  '["self-hosted","trusted-canary"]' '["self-hosted","untrusted-canary"]' \
  '["self-hosted","general"]' \
  "gate — a caller generated before #405 still overrides the overflow lane" \
  '' '["self-hosted","general"]' '["ubuntu-24.04"]'

# ADR 0050. actionlint is a short, secretless CPU job, so on a PUBLIC target it
# takes the fast lane instead of contending for the fixed self-hosted pool with
# the merge-gate poll loops it can starve. Four polarities, because every
# interesting failure here is in a fallback rather than the happy path.
assert_route "$workflows/actionlint.yml" actionlint Verjson/.github '' false \
  '["self-hosted","d"]' '["self-hosted","u"]' '["ubuntu-24.04"]' \
  "actionlint — a public Verjson repo takes the fast lane (ADR 0050)" '["ubuntu-24.04"]'

assert_route "$workflows/actionlint.yml" actionlint Verjson/verjson-authn '' true \
  '["self-hosted","d"]' '["self-hosted","u"]' '["self-hosted","d"]' \
  "actionlint — a private Verjson repo stays on CI_RUNNER_DEFAULT" '["ubuntu-24.04"]'

# The load-bearing one. The test is `== false`, never `!= true`, so a visibility
# that fails to resolve falls through to self-hosted instead of silently
# spending hosted minutes on an unreadable repository (ADR 0048's polarity rule).
assert_route "$workflows/actionlint.yml" actionlint Verjson/.github '' '' \
  '["self-hosted","d"]' '["self-hosted","u"]' '["self-hosted","u"]' \
  "actionlint — unresolved visibility still fails safe to CI_RUNNER_UNTRUSTED" '["ubuntu-24.04"]'

assert_route "$workflows/actionlint.yml" actionlint Acme/widgets '' true \
  '["self-hosted","d"]' '["self-hosted","u"]' 'ubuntu-24.04' \
  "actionlint — external callers retain hosted portability" '["ubuntu-24.04"]'

assert_route "$workflows/actionlint.yml" actionlint Verjson/.github '' false \
  '["self-hosted","d"]' '["self-hosted","u"]' '["self-hosted","u"]' \
  "actionlint — an unset fast lane degrades to CI_RUNNER_UNTRUSTED" ''

# Mutation check. The obvious way to write the public branch —
# `private == false` — is FAIL-OPEN: Actions coerces a missing `private` to 0,
# and 0 == false, so an unreadable repository would reach the fast lane. That
# form passed the three positive cases above and failed only the unresolved one,
# which is exactly how it would have shipped. Pin its absence directly, so the
# regression cannot return through a rewrite that keeps the tests passing.
grep -qF "github.event.repository.visibility == 'public'" "$workflows/actionlint.yml" \
  && ! grep -qF 'github.event.repository.private == false' "$workflows/actionlint.yml" \
  && pass "actionlint routes public on the visibility STRING, not the coercible private boolean" \
  || fail "actionlint uses the fail-open 'private == false' form (ADR 0050)"

# Every job that carries the policy, across every reusable workflow. A job
# missing from this list is caught by the no-owner-wide-route sweep below.
policy_jobs() {
  cat <<'TARGETS'
node-ci.yml eligibility
node-ci.yml build-test
node-release.yml release
notify-umbrella.yml dispatch
helm-ci.yml lint-template
ui-ci.yml build-test
pulumi-ci.yml validate
pulumi-ci.yml preview-admission
pulumi-ci.yml preview
changelog-validate.yml validate
generated-artifacts.yml validate
changelog-release.yml release
container-candidate.yml prepare
container-candidate.yml pull-request-build
container-candidate.yml skip-private-node-build
TARGETS
}

while read -r wf_name job; do
  wf="$workflows/$wf_name"
  name="${wf_name%.yml} $job"

  # Both live variables temporarily select general, but each visibility class
  # must consult its own variable so #204 can harden public work with one flip.
  assert_route "$wf" "$job" Verjson/verjson-authn '' true \
    '["self-hosted","private-canary"]' '["self-hosted","public-canary"]' \
    '["self-hosted","private-canary"]' "$name — private Verjson repo uses CI_RUNNER_DEFAULT"

  assert_route "$wf" "$job" Verjson/.github '' false \
    '["self-hosted","private-canary"]' '["self-hosted","public-canary"]' \
    '["self-hosted","public-canary"]' "$name — public Verjson repo uses CI_RUNNER_UNTRUSTED"

  assert_route "$wf" "$job" Verjson/verjson-authn '' '' \
    '["self-hosted","private-canary"]' '["self-hosted","public-canary"]' \
    '["self-hosted","public-canary"]' "$name — unresolved visibility fails safe to CI_RUNNER_UNTRUSTED"

  # Tier 2 — the published package stays usable outside the org.
  assert_route "$wf" "$job" Acme/widgets '' true '' '' \
    'ubuntu-24.04' "$name — caller outside Verjson stays portable on hosted"

  assert_route "$wf" "$job" Verjson/verjson-authn '' true '' '' \
    "$UNSET_LANDING" "$name — no lane variable set lands on the portable hosted default"

  assert_route "$wf" "$job" Verjson/.github '' false '["self-hosted","default-only"]' '' \
    '["self-hosted","default-only"]' "$name — untrusted falls back to default during migration"

  # A configured pool must never leak to an external caller's job.
  assert_route "$wf" "$job" Acme/widgets '' true '["self-hosted","do"]' '["self-hosted","do-isolated"]' \
    'ubuntu-24.04' "$name — configured pools do not leak to callers outside Verjson"
done < <(policy_jobs)

# The `runner` input is the per-caller override, but only on the jobs that
# expose it. pulumi-ci's `validate` and `preview-admission` deliberately do not:
# they sit on the credential boundary in ADR 0027/0029, so a caller must not be
# able to redirect them to a pool of its choosing.
while read -r wf_name job; do
  wf="$workflows/$wf_name"
  name="${wf_name%.yml} $job"
  assert_route "$wf" "$job" Verjson/verjson-authn '["self-hosted","manish"]' true '' '' \
    '["self-hosted","manish"]' "$name — explicit runner input wins"
done <<'TARGETS'
node-ci.yml eligibility
node-ci.yml build-test
node-release.yml release
notify-umbrella.yml dispatch
helm-ci.yml lint-template
ui-ci.yml build-test
pulumi-ci.yml preview
changelog-validate.yml validate
generated-artifacts.yml validate
changelog-release.yml release
container-candidate.yml prepare
container-candidate.yml pull-request-build
TARGETS

for job in publish-base publish-derived candidate-manifest; do
  assert_route "$workflows/container-candidate-publish.yml" "$job" Verjson/.github '' false \
    '["self-hosted","trusted-canary"]' '["self-hosted","untrusted-canary"]' \
    'ubuntu-24.04' "container-candidate $job — Verjson publication uses fixed hosted provenance"
  assert_route "$workflows/container-candidate-publish.yml" "$job" Acme/widgets '' false '' '' \
    'ubuntu-24.04' "container-candidate $job — external publication uses fixed hosted provenance"
  expression="$(extract_runs_on "$workflows/container-candidate-publish.yml" "$job")"
  case "$expression" in
    *inputs.runner*) fail "container-candidate $job must not accept the build-only runner override" ;;
    *) pass "container-candidate $job rejects the build-only runner override" ;;
  esac
done

assert_route "$workflows/container-release.yml" promote Verjson/.github '' false \
  '["self-hosted","trusted-canary"]' '["self-hosted","untrusted-canary"]' \
  'ubuntu-24.04' "container-release promote — Verjson release uses fixed hosted provenance"
assert_route "$workflows/container-release.yml" promote Acme/widgets '' false '' '' \
  'ubuntu-24.04' "container-release promote — external release uses fixed hosted provenance"

for job in dry-run deploy; do
  assert_route "$workflows/container-deployment.yml" "$job" Verjson/.github '' false \
    '["self-hosted","trusted-canary"]' '["self-hosted","untrusted-canary"]' \
    '["self-hosted","trusted-canary"]' \
    "container-deployment $job — protected orchestration stays on the trusted lane"
  assert_route "$workflows/container-deployment.yml" "$job" Acme/widgets '' false '' '' \
    'ubuntu-24.04' "container-deployment $job — external callers retain hosted portability"
  expression="$(extract_runs_on "$workflows/container-deployment.yml" "$job")"
  case "$expression" in
    *inputs.runner*) fail "container-deployment $job must not accept a caller runner override" ;;
    *) pass "container-deployment $job rejects caller-selected runner routing" ;;
  esac
done

for job in validate preview-admission; do
  expr_no_override="$(extract_runs_on "$workflows/pulumi-ci.yml" "$job")"
  case "$expr_no_override" in
    *inputs.runner*)
      fail "pulumi-ci $job must not accept a caller runner override (ADR 0027/0029)" ;;
    '')
      fail "could not extract runs-on for pulumi-ci $job" ;;
    *)
      pass "pulumi-ci $job keeps its credential-boundary runner fixed" ;;
  esac
done

# --------------------------------------------------------------------------
# Exhaustive, not enumerated. Every check above names specific (file, job)
# pairs, so a NEW job added to any of these files escapes all of them. Review
# demonstrated that: appending a job with an INVERTED policy — public repos to
# the persistent pool — left the suite fully green. That is the exact invariant
# ADR 0033 is built on, so the sweep has to bind every `runs-on:` line that
# exists, not a list someone has to remember to update.
#
# Prefixes legitimately differ, but every routed job must name both lanes by
# intent and degrade through CI_LANE_FALLBACK to the portable hosted tail.
# --------------------------------------------------------------------------
policy_files="node-ci.yml node-release.yml notify-umbrella.yml helm-ci.yml ui-ci.yml pulumi-ci.yml actionlint.yml changelog-validate.yml changelog-release.yml generated-artifacts.yml"
deviant=""
job_count=0
for name in $policy_files; do
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    job_count=$((job_count + 1))
    value="${line#*runs-on:}"
    value="${value# }"
    if [ "$name" = node-ci.yml ] \
        && [ "$value" = '${{ fromJSON(vars.CI_LANE_UNTRUSTED || '\''["ubuntu-24.04"]'\'') }}' ]; then
      continue
    fi
    # ADR 0178's deferral check. Keyed on the exact literal value, which alone
    # would excuse any node-ci job that adopted it — the exact-site inventory at
    # the top of this file is what binds it to `deferred-ci` specifically, so
    # moving the literal to another job fails there instead of passing silently.
    if [ "$name" = node-ci.yml ] && [ "$value" = 'ubuntu-24.04' ]; then
      continue
    fi
    if ! grep -qF 'CI_LANE_TRUSTED' <<<"$value" \
        || ! grep -qF 'CI_LANE_UNTRUSTED' <<<"$value" \
        || ! grep -qF 'CI_LANE_FALLBACK' <<<"$value"; then
      deviant="$deviant$name: $line"$'\n'
    fi
  done <<EOF
$(grep -nE "^    runs-on:" "$workflows/$name")
EOF
done

[ "$job_count" -ge 10 ] \
  && pass "sweep covered $job_count routed jobs across the reusable workflows" \
  || fail "sweep found only $job_count routed jobs — extraction is broken"

[ -z "$deviant" ] \
  && pass "every routed job names both lanes and degrades through CI_LANE_FALLBACK" \
  || fail "job(s) deviate from the routing policy:"$'\n'"$deviant"

# --------------------------------------------------------------------------
# Guidance is routing too. helm-ci.yml told callers to pin their kind smoke
# tests to `[self-hosted, docker]` long after the Docker lane stopped existing;
# a caller who followed it got a job that queued forever with no check run
# (#271, #182's silent mode). Nothing caught it because the pin lived in a
# COMMENT, and every check above reads `runs-on:` lines only.
#
# So bind every self-hosted label named in a bracketed selector list — in code
# or in a comment — to the declared set in .github/actionlint.yaml. That file is
# the organization-wide actionlint policy, i.e. the list of labels a Verjson
# workflow may name at all, which makes it the right authority: a label absent
# from it is one nothing may route on, so recommending it in prose is the same
# defect as routing on it.
#
# SCOPE, stated precisely because a guard trusted past its reach is worse than
# none. This matches the bracketed forms — `["self-hosted","general"]` and
# `[self-hosted, docker]` — on a single line. It does NOT match the YAML
# block-sequence spelling, nor a bare mention like "the self-hosted docker
# lane" in prose. Those remain unguarded; widening to bare words would need a
# deny-list of retired labels rather than this allow-list shape.
#
# The surface includes `.github/actions/**` docs, not just workflows: the same
# defect shipped a second time in setup-verjson-node's README, where a consumer
# copies the usage block verbatim.
#
# Also unguarded, and known: prose ATTRIBUTIONS in actionlint.yaml's own comments
# ("gate — carried today by the gha-general-* runners"). Those name runners
# rather than labels, so no selector-shaped check can see them going stale. If
# that class recurs, it needs a live-fleet check, not a wider regex here.
# --------------------------------------------------------------------------
declared="$(sed -n '/^self-hosted-runner:/,/^[^ #]/p' "$root/.github/actionlint.yaml" \
  | sed -n 's/^    - \([A-Za-z0-9._-]*\).*/\1/p')"
[ -n "$declared" ] \
  && pass "read $(printf '%s\n' "$declared" | wc -l | tr -d ' ') declared runner label(s) from actionlint.yaml" \
  || fail "could not read declared labels from .github/actionlint.yaml — extraction is broken"

# actionlint.yml's self-test feeds actionlint a workflow pinned to this label to
# prove the central policy actually rejects an unknown one. It is a NEGATIVE
# CONTROL, so it must stay undeclared — declaring it would leave the self-test
# passing while proving nothing.
control_label="retired-runner-label"
grep -qxF "$control_label" <<<"$declared" \
  && fail "$control_label is declared in actionlint.yaml — actionlint.yml's negative control no longer proves anything" \
  || pass "the actionlint self-test's negative control label stays undeclared"

# Both halves, or this asserts something about a control that no longer exists:
# deleting the invalid-runner fixture would leave the check above green while
# nothing proves the policy rejects an unknown label at all.
grep -qF "$control_label" "$workflows/actionlint.yml" \
  && pass "actionlint.yml still exercises the undeclared-runner negative control" \
  || fail "actionlint.yml's undeclared-runner negative control is missing — nothing proves the policy rejects unknown labels"

undeclared=""
for f in "${workflow_files[@]}" "$root"/.github/actions/*/README.md "$root"/.github/actions/*/action.yml; do
  [ -f "$f" ] || continue
  # The exemption is scoped to the file that owns the fixture. Repo-wide, it
  # would silently excuse a genuine `runs-on: [self-hosted, retired-runner-label]`
  # anywhere else — the exact "exemption hides a real label" hole.
  exempt="self-hosted"
  [ "$(basename "$f")" = "actionlint.yml" ] && exempt="self-hosted|$control_label"
  # The dispatch-only canonical canary's exact dynamic selector is validated by
  # hosted-selector-policy.test.py. Keep this duplicate inventory scan from
  # treating that expression as a literal fleet label without widening policy
  # for another workflow or symlink.
  if [ "$f" = "$root/.github/workflows/runner-canary.yml" ] && [ ! -L "$f" ]; then
    exempt='self-hosted|\$\{\{ inputs\.runner_label \}\}'
  fi
  # `tr -d "\"'"` strips BOTH quote styles: `['self-hosted', 'general']` is legal
  # YAML, and stripping only double quotes made it fail as the bogus label
  # `'general'`. Blank lines are dropped with a second grep rather than an empty
  # ERE alternation, which is undefined in POSIX and errors outright on non-GNU
  # grep — leaving the scan silently empty and passing forever.
  mentioned="$(grep -oE '\[[^][]*self-hosted[^][]*\]' "$f" \
    | tr -d "\"'" | tr ',[]' '\n\n\n' | sed 's/^ *//; s/ *$//' \
    | grep -vxE "$exempt" | grep -v '^$' | sort -u)"
  # Quoted: an unquoted expansion word-splits a prose label containing a space.
  while IFS= read -r label; do
    [ -n "$label" ] || continue
    grep -qxF "$label" <<<"$declared" \
      || undeclared="$undeclared$(basename "$f"): $label"$'\n'
  done <<<"$mentioned"
done

[ -z "$undeclared" ] \
  && pass "every self-hosted label in a bracketed selector — workflows and action docs — is declared" \
  || fail "workflow(s) name a runner label absent from .github/actionlint.yaml:"$'\n'"$undeclared"

# The declared set is BOUNDED, not just a superset of what is used. Undeclaring
# `gce`/`GCP`/`gate`/`meta`/`isolated` is the whole detection mechanism — a
# consumer naming one gets a lint failure instead of a job that queues forever —
# and re-adding one passed every other assertion in this suite (#401 review).
# Adding a label here is therefore a reviewed edit to this list, which is the
# point: it should cost a pull request, because it silences a detector.
declared_labels="$(
  awk '/^self-hosted-runner:/{f=1;next} f&&/^[a-z]/{f=0} f&&/^ *- /{sub(/#.*/,"");gsub(/[ \t-]/,"");if($0!="")print}' \
    "$root/.github/actionlint.yaml" | sort
)"
expected_labels="$(printf '%s\n' general | sort)"
[ "$declared_labels" = "$expected_labels" ] \
  && pass "the declared runner-label set is exactly the lanes the fleet serves" \
  || fail "declared runner labels drifted from the bounded set:"$'\n'"got: $(printf '%s' "$declared_labels" | tr '\n' ' ')"$'\n'"want: $(printf '%s' "$expected_labels" | tr '\n' ' ')"

# --------------------------------------------------------------------------
# The metered hosted SKUs (#814, ADR 0103). Every literal-selector assertion
# above keys on `ubuntu-(24\.04|latest)`, so `runs-on: macos-latest` passed this
# entire file — the two families at 10x and 2x multipliers were the two it could
# not see. With the organization raising its Actions spending limit for AiB's
# installer legs (#810), that check is the only remaining bound on hosted spend.
#
# The rules live in a separate, parameterized script rather than in more
# assertions here, for two reasons. This repository has no macOS or Windows
# selector and no OS-scoped lane, so an assertion against the live tree could
# never fail and would prove nothing — the negative path is provable only
# against fixtures, which `hosted-selector-policy.test.py` owns. And #815 has to
# point the same rules at a consumer's checkout, which a check hardcoded to
# `$root/.github/workflows` cannot do.
#
# It is Python, and that is a correctness decision rather than a preference. The
# first implementation matched `runs-on:` with shell patterns like the ones
# above, and two review passes found five parser-level false negatives in shapes
# GitHub accepts — a flow-style job body, a flow-mapping `jobs:`, a space before
# the colon, a matrix indirection, and a TAB/IFS field collapse — each returning
# exit 0 on a genuinely metered job. `yaml.safe_load` understands the language
# instead of recognising shapes, and refuses what it cannot resolve.
#
# `--visibility public` is the measured fact about THIS repository, not a
# default: `Verjson/.github` is public, so its hosted minutes are free and the
# Tier B literal-Linux rule does not fire here. The stricter standard `.github`
# is actually held to is the closed, exact-site ADR 0089 inventory above, which
# this change does not touch.
hosted_selector_policy="$root/scripts/ci-gate/hosted-selector-policy.py"
if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 is required to run the hosted-selector policy"
else
  hosted_selector_out="$(cd -- "$root" && python3 "$hosted_selector_policy" --visibility public "$workflows" 2>&1)"
  hosted_selector_rc=$?
  case "$hosted_selector_rc" in
    0) pass "no workflow names a metered hosted runner family or an unbounded OS lane" ;;
    1) fail "hosted-selector policy violation: $hosted_selector_out" ;;
    # Exit 2 is reported separately from exit 1 on purpose: "the sweep could not
    # decide" and "the sweep found a violation" are different problems, and
    # collapsing them is how a check that scanned nothing gets read as a pass.
    2) fail "hosted-selector policy could not run: $hosted_selector_out" ;;
    # Anything else is the script failing to execute at all — a missing
    # interpreter or an import error. Left in the catch-all it would have been
    # reported as a policy violation, which sends the reader to the workflows.
    *) fail "hosted-selector policy exited $hosted_selector_rc (it did not run): $hosted_selector_out" ;;
  esac
fi

if [ "$fails" -eq 0 ]; then
  echo "All tests passed."
  exit 0
fi
echo "$fails test(s) failed."
exit 1
