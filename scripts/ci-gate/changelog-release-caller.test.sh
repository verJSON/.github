#!/usr/bin/env bash
# Contract tests for the generated release caller
# (`scripts/gen-changelog-caller.sh release-node`, Verjson/.github#463/#464/#465).
#
# The release caller was the one adopter file the generator did not emit, so
# every adopter hand-copied it from a sibling and every defect in the copied
# shape reached ~21 repositories by inheritance. All three defects fire only on a
# real dispatch, and the first symptom of each is a burned version number: a
# tagged, changelog-bearing commit on ruleset-protected `main` with nothing
# published, recoverable by neither re-dispatch.
#
# Two kinds of assertion, because neither alone is evidence:
#
#   1. `check_shape` reads the emitted YAML structurally and is re-run against a
#      table of mutations, each of which breaks exactly one invariant. A guard
#      that no mutation kills is a guard that cannot fail.
#   2. The verify job's guards are EXECUTED — extracted from the emitted YAML and
#      run against stubs — because a release guard that is only grepped for has
#      never been observed to say "no".
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
gen="$root/scripts/gen-changelog-caller.sh"
sha="$(git -C "$root" rev-parse HEAD)"
fails=0

pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

python3 -c 'import yaml' 2>/dev/null \
  || { echo "FAIL - PyYAML is required to check the emitted release caller"; exit 1; }

release="$tmp/release.yml"
if bash "$gen" release-node "$sha" >"$release" 2>"$tmp/gen.err"; then
  pass "release-node mode emits a file"
else
  fail "release-node mode failed: $(cat "$tmp/gen.err")"
  echo "$fails test(s) failed."
  exit 1
fi

# --- the shape checker, and the mutation table that proves it can fail --------

cat >"$tmp/check_shape.py" <<'PY'
"""Assert the invariants #463/#464/#465 exist to establish.

Structural, not textual: a grep for `needs: verify` passes on a file where the
key sits under the wrong job, and that is precisely the mistake the hand-copied
shape made with `runner:`.
"""
import re
import sys

import yaml

path = sys.argv[1]
raw = open(path, encoding="utf-8").read()
doc = yaml.safe_load(raw)
problems = []


def bad(message):
    problems.append(message)


# `on` is the YAML 1.1 boolean True once parsed, which is a footgun worth
# spelling out rather than tripping over.
triggers = doc.get("on", doc.get(True))
if not isinstance(triggers, dict):
    bad("the workflow declares no mapping of triggers")
    triggers = {}
if set(triggers) != {"workflow_dispatch"}:
    bad(
        "triggers are %r; a release states the version it cuts and is never "
        "derived from a merge (ADR 0038, ADR 0060)" % sorted(triggers)
    )
inputs = (triggers.get("workflow_dispatch") or {}).get("inputs") or {}
version_input = inputs.get("version") or {}
if version_input.get("required") is not True or "default" in version_input:
    bad("workflow_dispatch must require `version` without a default")
if (inputs.get("prefix") or {}).get("default") != "v":
    bad("workflow_dispatch does not default the independent release prefix to `v`")
for receipt_input in ("expected_head", "selector_digest"):
    if (inputs.get(receipt_input) or {}).get("default") != "":
        bad("workflow_dispatch does not expose empty-default `%s`" % receipt_input)
if (inputs.get("component") or {}).get("default") != "":
    bad("workflow_dispatch does not expose an empty-default `component` input")

jobs = doc.get("jobs") or {}
for name in ("verify", "snapshot", "publish"):
    if name not in jobs:
        bad("no `%s` job" % name)
if problems:
    print("\n".join(problems))
    sys.exit(1)

verify, snapshot, publish = jobs["verify"], jobs["snapshot"], jobs["publish"]

header, trigger_separator, _ = raw.partition("\non:")
header_warning = """# The verification suite runs after package.json has been stamped to the
# dispatched version. Its expected version must be read dynamically from
# package.json; never assert a hardcoded version literal. This order is
# intentional: the suite verifies the exact package metadata that will ship."""
if not trigger_separator or header_warning not in header:
    bad("the stamped-version warning is not inside the generated header before `on:` (#862)")


def needs_of(job):
    value = job.get("needs") or []
    return [value] if isinstance(value, str) else list(value)


# #463/#464. The snapshot push is atomic and irreversible; anything that can say
# "no" has to have said it already.
if "verify" not in needs_of(snapshot):
    bad("`snapshot` does not declare `needs: verify`, so the irreversible "
        "snapshot can run before anything has verified the tree (#463, #464)")
if set(needs_of(publish)) != {"verify", "snapshot"}:
    bad("`publish` must depend on both verify and snapshot for restart-safe publication")
if "always()" not in str(publish.get("if") or ""):
    bad("`publish` does not run after a deliberately skipped existing snapshot")
if str(snapshot.get("if") or "") != "needs.verify.outputs.selected == 'true' && needs.verify.outputs.snapshot-exists != 'true'":
    bad("`snapshot` does not skip empty selections and verified existing immutable snapshots")
verify_outputs = verify.get("outputs") or {}
if verify_outputs.get("selected") != "${{ steps.release-version.outputs.selected }}":
    bad("`verify` does not expose the resolved selection state")
if verify_outputs.get("version") != "${{ steps.release-version.outputs.version }}":
    bad("`verify` does not expose the resolved release version")
if verify_outputs.get("selection-digest") != "${{ steps.release-version.outputs.selection-digest }}":
    bad("`verify` does not expose the resolved selection digest")
if verify_outputs.get("snapshot-exists") != "${{ steps.release-state.outputs.snapshot-exists }}":
    bad("`verify` does not expose the restart-safe snapshot state")
resume_checkouts = [
    step for step in verify.get("steps") or []
    if step.get("uses", "").startswith("actions/checkout@")
    and (step.get("with") or {}).get("ref") == "${{ steps.release-version.outputs.version }}"
]
if len(resume_checkouts) != 1 or str(resume_checkouts[0].get("if") or "") != "steps.release-version.outputs.selected == 'true' && steps.release-state.outputs.snapshot-exists == 'true'":
    bad("`verify` does not switch to the existing tagged snapshot before resumed verification")

uses = snapshot.get("uses", "")
match = re.fullmatch(
    r"verJSON/\.github/\.github/workflows/changelog-release\.yml@([0-9a-f]{40})", uses
)
if not match:
    bad("`snapshot` does not call changelog-release.yml at an immutable "
        "40-character commit; it uses %r" % uses)
else:
    with_ = snapshot.get("with") or {}
    if with_.get("contract_ref") != match.group(1):
        bad("`snapshot` passes contract_ref %r but calls the workflow at %s"
            % (with_.get("contract_ref"), match.group(1)))
    if with_.get("component") != "${{ inputs.component }}":
        bad("`snapshot` does not pass the selected component stream")
    if with_.get("prefix") != "${{ inputs.prefix }}":
        bad("`snapshot` does not pass the selected release namespace")
    if with_.get("selection_digest") != "${{ needs.verify.outputs.selection-digest }}":
        bad("`snapshot` does not pass the resolved selection digest")

# #465(2). One release, one pool. The expression is compared, not merely
# required: two different expressions that happen to resolve identically today
# are the drift that hid this defect until a lane migration moved one half.
with_ = snapshot.get("with") or {}
runner = str(with_.get("runner") or "").strip()
if not runner:
    bad("`snapshot` passes no explicit `runner:`, so changelog-release.yml "
        "routes it by its own default while this caller's jobs route by "
        "another — on a private repository the snapshot half then queues "
        "silently on hosted runners (#465)")
else:
    expr = re.fullmatch(r"\$\{\{(.*)\}\}", runner, re.S)
    snapshot_pool = expr.group(1).strip() if expr else None
    if snapshot_pool is None:
        bad("`snapshot` runner %r is not a single expression" % runner)
    for name, job in (("verify", verify),):
        runs_on = str(job.get("runs-on") or "").strip()
        inner = re.fullmatch(r"\$\{\{\s*fromJSON\((.*)\)\s*\}\}", runs_on, re.S)
        pool = inner.group(1).strip() if inner else None
        if pool != snapshot_pool:
            bad("`%s` routes on %r but `snapshot` routes on %r; the two halves "
                "of one release must share one pool (#465)" % (name, pool, snapshot_pool))

GITHUB_TOKEN = re.compile(
    r"\$\{\{\s*(secrets\.GITHUB_TOKEN|github\.token)\s*\}\}", re.I
)


def steps_of(job):
    return job.get("steps") or []


cache_steps = [
    step for step in steps_of(verify)
    if step.get("name") == "Prepare job-scoped changelog tool cache"
]
if (
    len(cache_steps) != 1
    or len(steps_of(verify)) < 2
    or steps_of(verify)[0].get("name") != "Require an explicit release version"
    or "if" in steps_of(verify)[0]
    or "continue-on-error" in steps_of(verify)[0]
    or steps_of(verify)[1] is not cache_steps[0]
    or cache_steps[0].get("run") != 'echo "VERJSON_CHANGELOG_TOOL_CACHE=$RUNNER_TEMP/verjson-changelog-tools" >> "$GITHUB_ENV"'
):
    bad("`verify` does not export a job-writable runner.temp cache before repository steps (#630)")

head_receipts = [
    step for step in steps_of(verify)
    if step.get("name") == "Bind a proposer dispatch to its exact derived head"
]
if len(head_receipts) != 1:
    bad("`verify` does not contain exactly one exact-head proposer guard")
else:
    head_receipt = head_receipts[0]
    receipt_env = head_receipt.get("env") or {}
    receipt_run = str(head_receipt.get("run") or "")
    if receipt_env.get("EXPECTED_HEAD") != "${{ inputs.expected_head }}":
        bad("the proposer guard does not read expected_head")
    if receipt_env.get("SELECTOR_DIGEST") != "${{ inputs.selector_digest }}":
        bad("the proposer guard does not bind selector_digest with expected_head")
    if '"$GITHUB_SHA" != "$EXPECTED_HEAD"' not in receipt_run:
        bad("the proposer guard does not fail a mutable-branch TOCTOU head change")

selector_receipts = [
    step for step in steps_of(verify)
    if step.get("name") == "Resolve the release selection and version"
]
if len(selector_receipts) != 1:
    bad("`verify` does not contain exactly one canonical release-plan step")
else:
    selector_run = str(selector_receipts[0].get("run") or "")
    selector_env = selector_receipts[0].get("env") or {}
    if "release-plan" not in selector_run or "EXPECTED_SELECTOR_DIGEST" not in selector_run:
        bad("the release-plan step does not resolve and bind the canonical selection digest")
    if "GITHUB_STEP_SUMMARY" not in selector_run or "GITHUB_SHA" not in selector_run:
        bad("the release-plan step does not publish the operator-facing resolution summary")
    if selector_env.get("INPUT_VERSION") != "${{ inputs.version }}":
        bad("the release-plan step does not read the required version input")


# #465(1). A repository-scoped GITHUB_TOKEN cannot read a private GitHub
# Packages package owned by another repository, so an install wired to it 401s
# for every adopter with a private @verjson dependency.
installs = 0
for name, job in jobs.items():
    for step in steps_of(job):
        run = step.get("run") or ""
        if not re.search(r"\bnpm ci\b", run):
            continue
        installs += 1
        token = str((step.get("env") or {}).get("NODE_AUTH_TOKEN") or "")
        if not token:
            bad("the `npm ci` step in `%s` passes no NODE_AUTH_TOKEN" % name)
        elif GITHUB_TOKEN.search(token):
            bad("the `npm ci` step in `%s` installs with GITHUB_TOKEN, which "
                "cannot read a private @verjson package owned by another "
                "repository (#465)" % name)
if installs != 1:
    bad("expected one install step in `verify`, found %d" % installs)

# Publication is centralized in the publish-only reusable workflow at the same
# immutable contract pin. It consumes the version chosen by the snapshot job.
publish_uses = str(publish.get("uses") or "")
expected_publish = (
    "verJSON/.github/.github/workflows/node-release.yml@%s" % match.group(1)
    if match else ""
)
if not expected_publish or publish_uses != expected_publish:
    bad("`publish` does not call node-release.yml at the contract pin")
publish_with = publish.get("with") or {}
if str(publish_with.get("version") or "").strip() != "${{ needs.verify.outputs.version }}":
    bad("`publish` does not pass the resolved contract-selected version")
if str(publish_with.get("prefix") or "").strip() != "${{ inputs.prefix }}":
    bad("`publish` does not pass the independent release namespace")
if str(publish_with.get("node-version") or "") != "${{ '24' }}":
    bad("`publish` does not pass the generated Node version to node-release.yml")
if str(publish_with.get("scope") or "") != "@verjson":
    bad("`publish` does not pass the generated npm scope to node-release.yml")
publish_secret = str((publish.get("secrets") or {}).get("NODE_AUTH_TOKEN") or "")
if not publish_secret or GITHUB_TOKEN.search(publish_secret):
    bad("`publish` does not pass the private-dependency read token")
publish_runner = str(publish_with.get("runner") or "").strip()
publish_expr = re.fullmatch(r"\$\{\{(.*)\}\}", publish_runner, re.S)
if not publish_expr or publish_expr.group(1).strip() != snapshot_pool:
    bad("`publish` and `snapshot` do not route on the same pool")

# ADR 0099: the reusable workflow mints the ruleset-bypassing token, while the
# generated caller passes only the dedicated App identity material.
snapshot_with = snapshot.get("with") or {}
if snapshot_with.get("release_app_client_id") != "${{ vars.RELEASE_APP_CLIENT_ID }}":
    bad("`snapshot` does not pass vars.RELEASE_APP_CLIENT_ID")
snapshot_secrets = snapshot.get("secrets")
if snapshot_secrets != "inherit" or snapshot_with.get("release_environment") != "release-app":
    bad("`snapshot` must inherit context and select release-app")
if "ORG_ADMIN_TOKEN" in raw or "push_token:" in raw:
    bad("release caller retains the temporary broad push credential")

# verify must verify the content the snapshot will take, and must actually run
# a suite rather than merely existing as a gate-shaped no-op.
checkouts = [
    step for step in steps_of(verify) if str(step.get("uses", "")).startswith("actions/checkout@")
]
if not checkouts:
    bad("`verify` checks nothing out")
elif "${{ github.sha }}" not in str((checkouts[0].get("with") or {}).get("ref") or ""):
    bad("`verify` does not check out github.sha, so it proves nothing about "
        "the tree the snapshot will tag")
# The branch guard's shell logic is executed further down, but a guard is only
# as good as what it is handed: fed `github.ref_name` as DEFAULT_BRANCH it
# compares the dispatch ref to itself, passes for every branch, and no executed
# test can see it, because the harness supplies the env directly (#466).
branch_guard_env = {}
for step in steps_of(verify):
    if "refs/heads/$DEFAULT_BRANCH" in (step.get("run") or ""):
        branch_guard_env = step.get("env") or {}
        break
if not branch_guard_env:
    bad("`verify` carries no default-branch guard, so a release can be "
        "dispatched from any ref (#466)")
else:
    if "github.event.repository.default_branch" not in str(branch_guard_env.get("DEFAULT_BRANCH", "")):
        bad("the default-branch guard does not read DEFAULT_BRANCH from "
            "github.event.repository.default_branch, so it cannot tell the "
            "default branch from the ref it was dispatched on (#466)")
    if str(branch_guard_env.get("DISPATCH_REF", "")).strip() != "${{ github.ref }}":
        bad("the default-branch guard does not read DISPATCH_REF from "
            "github.ref, so it does not check the ref the release runs on (#466)")

suite = "\n".join(step.get("run") or "" for step in steps_of(verify))
if "npm test" not in suite:
    bad("`verify` never runs the repository's suite")
if "scripts/release-verify.sh" not in suite:
    bad("`verify` offers no scripts/release-verify.sh hook, so an adopter whose "
        "suite is not `npm test` must edit a generated artifact")

# #1712. The verification hook runs repository-controlled code after private
# dependencies have been acquired, so it must explicitly clear the package token.
suite_steps = [
    step for step in steps_of(verify)
    if step.get("name") == "Run the release verification suite"
]
if len(suite_steps) != 1:
    bad("`verify` must contain exactly one named release verification suite step")
else:
    suite_step = suite_steps[0]
    suite_env = suite_step.get("env") or {}
    if suite_env.get("NODE_AUTH_TOKEN") != "":
        bad("the release verification hook/default suite must explicitly "
            "clear NODE_AUTH_TOKEN (#1712)")
    if str(suite_env.get("PACKAGE_VERSION") or "").strip() != "${{ steps.release-version.outputs.package-version }}":
        bad("the release verification suite cannot diagnose the stamped dispatch version (#862)")
    if str(suite_env.get("VERJSON_CHANGELOG_TOOL_CACHE") or "").strip() != "${{ steps.release-verification-runtime.outputs.cache }}":
        bad("the release verification suite does not use its captured job-scoped cache (#1717)")
    diagnostic_command = (
        'echo "::error::Release verification failed against stamped dispatch version '
        '$PACKAGE_VERSION. Check for the hardcoded-version footgun: version assertions '
        'can pass in pull requests and local runs, then fail only here; read the expected '
        'version dynamically from package.json."'
    )
    if not any(
        line.strip() == diagnostic_command
        for line in str(suite_step.get("run") or "").splitlines()
    ):
        bad("the release verification step does not execute the canonical "
            "stamped-version failure diagnostic (#862)")
# Used to reject the private credential anywhere outside dependency acquisition.
private_token = "${{ secrets.NODE_AUTH_TOKEN }}"
for job_name, job in jobs.items():
    job_token = str((job.get("env") or {}).get("NODE_AUTH_TOKEN") or "")
    if job_token == private_token:
        bad("`%s` exposes the private-package token at job scope instead of "
            "only to commands that need it (#569)" % job_name)
    for step in steps_of(job):
        token = str((step.get("env") or {}).get("NODE_AUTH_TOKEN") or "")
        run = step.get("run") or ""
        if token == private_token and not (
            re.search(r"\bnpm ci\b", run)
            or step.get("name") == "Run the release verification suite"
        ):
            bad("an unrelated step in `%s` receives the private-package token "
                "(#569)" % job_name)

# Provenance, so an adopter's own contract test can tell a regenerated caller
# from a hand-copied one.
if not re.search(r"gen-changelog-caller\.sh release-node [0-9a-f]{40}", raw):
    bad("the emitted caller carries no generator provenance marker")

if problems:
    print("\n".join(problems))
    sys.exit(1)
print("shape ok")
PY

check_shape() { python3 "$tmp/check_shape.py" "$1" >"$tmp/shape.out" 2>&1; }

if check_shape "$release"; then
  pass "the generated release caller satisfies every #463/#464/#465 invariant"
else
  fail "the generated release caller fails its own shape check: $(cat "$tmp/shape.out")"
fi

# Each mutation breaks exactly one invariant in a copy of the generated file.
# A mutation the checker survives is a guard that cannot fail.
mutate_seq=0
expect_shape_rejection() {
  # expect_shape_rejection <label> <sed-or-fn>
  local label="$1" mutator="$2" copy
  mutate_seq=$((mutate_seq + 1))
  copy="$tmp/mutant-$mutate_seq.yml"
  cp "$release" "$copy"
  "$mutator" "$copy"
  if cmp -s "$release" "$copy"; then
    fail "mutation '$label' changed nothing; the case is vacuous"
    return
  fi
  if check_shape "$copy"; then
    fail "shape check accepted $label"
  else
    pass "shape check rejects $label"
  fi
}

drop_needs_verify() { sed -i '/^    needs: verify$/d' "$1"; }
drop_needs_snapshot() { sed -i 's/^    needs: \[verify, snapshot\]$/    needs: verify/' "$1"; }
drop_snapshot_restart_condition() { sed -i "/^    if: needs.verify.outputs.selected == 'true' && needs.verify.outputs.snapshot-exists != 'true'$/d" "$1"; }
drop_publish_restart_condition() { sed -i "/^    if: always() && needs.verify.result == 'success'/d" "$1"; }
drop_snapshot_state_output() { sed -i '/^      snapshot-exists: .*steps.release-state.outputs.snapshot-exists/d' "$1"; }
drop_resume_snapshot_checkout() {
  sed -i "/^      - name: Check out the existing snapshot for resumed verification$/,+6d" "$1"
}
install_with_github_token() {
  # Only the install wiring, never the publish wiring: publishing with
  # GITHUB_TOKEN is correct and must stay accepted.
  sed -i 's|NODE_AUTH_TOKEN: ${{ secrets.NODE_AUTH_TOKEN }}|NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}|g' "$1"
}
drop_explicit_runner() { sed -i '/^      runner: /d' "$1"; }
diverge_runner_pool() {
  sed -i "/^  publish:/,\$ s@^      runner: .*@      runner: \${{ vars.CI_RUNNER_OVERFLOW || '[\"ubuntu-24.04\"]' }}@" "$1"
}
add_push_trigger() { sed -i 's|^on:$|on:\n  push:\n    branches: [main]|' "$1"; }
unpin_reusable_ref() {
  sed -i "s|changelog-release.yml@$sha|changelog-release.yml@main|" "$1"
}
skew_contract_ref() {
  sed -i "s|^      contract_ref: $sha|      contract_ref: deadbeefdeadbeefdeadbeefdeadbeefdeadbeef|" "$1"
}
drop_component_selection() {
  sed -i '/^      component: /d' "$1"
}
drop_release_app_client_id() { sed -i '/^      release_app_client_id: /d' "$1"; }
drop_release_environment() { sed -i '/^      release_environment: /d' "$1"; }
drop_snapshot_context() { sed -i '/^    secrets: inherit$/d' "$1"; }
restore_org_admin_token() {
  sed -i '/^      release_environment: /c\      push_token: ${{ secrets.ORG_ADMIN_TOKEN }}' "$1"
}
verify_a_different_ref() {
  sed -i 's|          ref: ${{ github.sha }}|          ref: ${{ github.ref }}|' "$1"
}
hollow_out_the_suite() {
  sed -i '/npm test || verification_status=/c\              echo skipping || verification_status=$?' "$1"
}
drop_verify_hook() { sed -i '/scripts\/release-verify.sh/d' "$1"; }
drop_verify_suite_token_blank() {
  sed -i '/^      - name: Run the release verification suite$/,/^      - name:/ {/NODE_AUTH_TOKEN:/d;}' "$1"
}
shadow_stamped_version_header() {
  python3 - "$1" <<'PY'
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
warning = """# The verification suite runs after package.json has been stamped to the
# dispatched version. Its expected version must be read dynamically from
# package.json; never assert a hardcoded version literal. This order is
# intentional: the suite verifies the exact package metadata that will ship.
"""
if text.count(warning) != 1:
    raise SystemExit("stamped-version header fixture no longer matches generated output")
open(path, "w", encoding="utf-8").write(text.replace(warning, "", 1) + "\n" + warning)
PY
}
noop_stamped_version_diagnostic() {
  sed -i 's|echo "::error::Release verification failed against stamped dispatch version|: "::error::Release verification failed against stamped dispatch version|' "$1"
}
strip_provenance() { sed -i '/gen-changelog-caller.sh release-node/d' "$1"; }
drop_publish_token() { sed -i '/^      NODE_AUTH_TOKEN: /d' "$1"; }
drop_publish_node_version() {
  sed -i '/^  publish:/,$ { /^      node-version: /d; }' "$1"
}
drop_publish_scope() {
  sed -i '/^  publish:/,$ { /^      scope: /d; }' "$1"
}
tautological_branch_guard() {
  # The guard's shell logic survives this untouched — only its inputs change, so
  # it compares github.ref against itself and can never say "no". Executing the
  # extracted script proves nothing here, because the harness supplies the env
  # the workflow would have got wrong (#466).
  sed -i 's|          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}|          DEFAULT_BRANCH: ${{ github.ref_name }}|' "$1"
}
tautological_exact_head_guard() {
  sed -i 's/"$GITHUB_SHA" != "$EXPECTED_HEAD"/"$EXPECTED_HEAD" != "$EXPECTED_HEAD"/' "$1"
}
drift_selector_receipt() {
  sed -i '/^      - name: Resolve the release selection and version$/,/^      - name: Resolve restart-safe release state$/ s/release-plan/next-version/' "$1"
}
drop_publish_prefix() {
  sed -i '/^  publish:/,$ { /^      prefix: /d; }' "$1"
}

expect_shape_rejection "snapshot without needs: verify (#463, #464)" drop_needs_verify
expect_shape_rejection "publish without needs: snapshot" drop_needs_snapshot
expect_shape_rejection "snapshot without restart condition (#588)" drop_snapshot_restart_condition
expect_shape_rejection "publish skipped after a reused snapshot (#588)" drop_publish_restart_condition
expect_shape_rejection "verify without snapshot state output (#588)" drop_snapshot_state_output
expect_shape_rejection "resumed verification without the tagged snapshot checkout (#591)" drop_resume_snapshot_checkout
expect_shape_rejection "npm ci installing with GITHUB_TOKEN (#465)" install_with_github_token
expect_shape_rejection "snapshot without an explicit runner (#465)" drop_explicit_runner
expect_shape_rejection "snapshot and publish on different pools (#465)" diverge_runner_pool
expect_shape_rejection "a push: trigger that derives a release from a merge" add_push_trigger
expect_shape_rejection "an unpinned reusable ref" unpin_reusable_ref
expect_shape_rejection "a contract_ref that disagrees with the uses: pin" skew_contract_ref
expect_shape_rejection "a snapshot that drops the selected component" drop_component_selection
expect_shape_rejection "snapshot without RELEASE_APP_CLIENT_ID (ADR 0099)" drop_release_app_client_id
expect_shape_rejection "snapshot without release environment (ADR 0166)" drop_release_environment
expect_shape_rejection "snapshot without inherited environment context (ADR 0171)" drop_snapshot_context
expect_shape_rejection "the temporary ORG_ADMIN_TOKEN release credential" restore_org_admin_token
expect_shape_rejection "verifying a ref other than github.sha" verify_a_different_ref
expect_shape_rejection "a verify job that runs no suite" hollow_out_the_suite
expect_shape_rejection "a verify job with no release-verify.sh hook" drop_verify_hook
expect_shape_rejection "a release verification suite without an explicit credential blank (#1712)" drop_verify_suite_token_blank
expect_shape_rejection "stamped-version warning text shadowed outside the generated header (#862)" shadow_stamped_version_header
grep -qF 'the stamped-version warning is not inside the generated header before `on:` (#862)' "$tmp/shape.out" \
  && pass "the shape validator rejects the shadowed warning for leaving the header" \
  || fail "the shadowed warning failed shape validation for another reason: $(cat "$tmp/shape.out")"
expect_shape_rejection "a no-op stamped-version failure diagnostic (#862)" noop_stamped_version_diagnostic
grep -qF 'does not execute the canonical stamped-version failure diagnostic (#862)' "$tmp/shape.out" \
  && pass "the shape validator rejects the no-op diagnostic as non-executable" \
  || fail "the no-op diagnostic failed shape validation for another reason: $(cat "$tmp/shape.out")"
expect_shape_rejection "a caller carrying no generator provenance" strip_provenance
expect_shape_rejection "a publish job without its private dependency token" drop_publish_token
expect_shape_rejection "a publish job without its generated Node version" drop_publish_node_version
expect_shape_rejection "a publish job without its generated npm scope" drop_publish_scope
expect_shape_rejection "a branch guard that compares github.ref to itself" tautological_branch_guard
expect_shape_rejection "an exact-head guard made tautological across the dispatch race" tautological_exact_head_guard
expect_shape_rejection "a selector receipt not recomputed by the canonical digest" drift_selector_receipt
expect_shape_rejection "a publish job without the release namespace" drop_publish_prefix

# The hand-copied shape every adopter carries today must be rejected, otherwise
# regenerating changes nothing observable.
legacy="$tmp/legacy-release.yml"
cat >"$legacy" <<YAML
name: Release
on:
  workflow_dispatch:
    inputs:
      version:
        required: true
        type: string
permissions:
  contents: write
  packages: write
jobs:
  snapshot:
    uses: Verjson/.github/.github/workflows/changelog-release.yml@$sha
    with:
      contract_ref: $sha
      version: \${{ inputs.version }}
    secrets:
      push_token: \${{ secrets.ORG_ADMIN_TOKEN }}
  publish:
    needs: snapshot
    runs-on: \${{ fromJSON(vars.CI_RUNNER_DEFAULT || '["self-hosted","general"]') }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          ref: \${{ inputs.version }}
      - run: npm ci
        env:
          NODE_AUTH_TOKEN: \${{ secrets.GITHUB_TOKEN }}
      - run: npm test
      - name: Publish
        env:
          NODE_AUTH_TOKEN: \${{ secrets.GITHUB_TOKEN }}
          VERSION: \${{ inputs.version }}
        run: npm publish
YAML
if check_shape "$legacy"; then
  fail "shape check accepts the hand-copied verjson-payments shape"
else
  pass "shape check rejects the hand-copied verjson-payments shape"
fi
# Rejected for the stated reason, not incidentally: the legacy shape's defining
# defect is that nothing verifies anything before the snapshot.
# Matched on the whole phrase, not the bare word: `verify` is a substring of
# several unrelated rejection messages, so a loose grep would report this case
# green no matter which defect actually fired.
if grep -qF 'no `verify` job' "$tmp/shape.out"; then
  pass "the legacy shape is rejected for having no verify job"
else
  fail "the legacy rejection never mentions verify: $(cat "$tmp/shape.out")"
fi

# --- the verify guards, executed ---------------------------------------------

extract_run() {
  # extract_run <job> <step-name-substring>
  RELEASE_YML="$release" JOB="$1" STEP="$2" python3 - <<'PY'
import os
import sys

import yaml

doc = yaml.safe_load(open(os.environ["RELEASE_YML"], encoding="utf-8"))
for step in doc["jobs"][os.environ["JOB"]]["steps"]:
    if os.environ["STEP"] in str(step.get("name", "")):
        sys.stdout.write(step["run"])
        break
else:
    sys.exit("no such step")
PY
}

branch_guard="$tmp/branch-guard.sh"
extract_run verify "default branch" >"$branch_guard" || fail "cannot extract the branch guard"
head_guard="$tmp/head-guard.sh"
extract_run verify "exact derived head" >"$head_guard" || fail "cannot extract the exact-head guard"
version_guard="$tmp/version-guard.sh"
extract_run verify "selection and version" >"$version_guard" || fail "cannot extract the selection and version plan"
tag_guard="$tmp/tag-guard.sh"
extract_run verify "restart-safe release state" >"$tag_guard" || fail "cannot extract the tag guard"
runtime_step="$tmp/runtime-step.sh"
extract_run verify "Capture trusted release verification runtime" >"$runtime_step" \
  || fail "cannot extract trusted release runtime capture"
suite_step="$tmp/suite.sh"
extract_run verify "verification suite" >"$suite_step" || fail "cannot extract the suite step"

run_guard() { # run_guard <script> [env assignments...]
  # `bash -e`, because that is what Actions runs a `run:` block under
  # (`shell: bash` expands to `bash --noprofile --norc -eo pipefail {0}`).
  # Plain `bash` hides every regression in a non-final command: a suite step
  # whose build breaks would still exit on its last line and report green here
  # while failing in CI — or, worse, the reverse.
  ( cd "$tmp/sandbox" && env \
      GITHUB_WORKSPACE="$tmp/sandbox" \
      RUNNER_TEMP="$tmp/runner" \
      GITHUB_STEP_SUMMARY="$tmp/summary" \
      GITHUB_SHA="$(printf 'c%.0s' {1..40})" \
      COMPONENT= \
      FRAGMENTS= \
      EXPECTED_SELECTOR_DIGEST= \
      "${@:2}" bash -eo pipefail "$1" ) >"$tmp/guard.out" 2>&1
}

mkdir -p "$tmp/sandbox/CHANGELOG" "$tmp/sandbox/NEXT" "$tmp/sandbox/.changelog-contract/scripts" "$tmp/runner" "$tmp/bin"
cp "$root/scripts/changelog.py" "$tmp/sandbox/.changelog-contract/scripts/changelog.py"
printf 'previous\n' >"$tmp/sandbox/CHANGELOG/v1.2.2.md"
cat >"$tmp/sandbox/NEXT/2026-07-30-issue-249-plan.md" <<'EOF'
---
date: 2026-07-30
issue: 249
impact: patch
title: Plan
---

Plan preview.
EOF
git init -q --bare "$tmp/release-remote.git"
git -C "$tmp/sandbox" init -q --initial-branch=main
git -C "$tmp/sandbox" config user.name 'Changelog caller test'
git -C "$tmp/sandbox" config user.email 'changelog-caller-test@example.invalid'
git -C "$tmp/sandbox" add CHANGELOG NEXT .changelog-contract
git -C "$tmp/sandbox" commit -qm 'Set up release-state fixture'
git -C "$tmp/sandbox" remote add origin "$tmp/release-remote.git"
git -C "$tmp/sandbox" push -qu origin main

if run_guard "$branch_guard" DISPATCH_REF=refs/heads/main DEFAULT_BRANCH=main; then
  pass "the branch guard admits a dispatch from the default branch"
else
  fail "the branch guard rejected the default branch: $(cat "$tmp/guard.out")"
fi
for ref in refs/heads/topic refs/tags/v1.0.0 refs/heads/mainline; do
  if run_guard "$branch_guard" "DISPATCH_REF=$ref" DEFAULT_BRANCH=main; then
    fail "the branch guard admitted a dispatch from $ref"
  else
    pass "the branch guard rejects a dispatch from $ref"
  fi
done

expected_head="$(printf 'a%.0s' {1..40})"
selector_digest="$(printf 'b%.0s' {1..64})"
if run_guard "$head_guard" EXPECTED_HEAD= SELECTOR_DIGEST= GITHUB_SHA="$(printf 'c%.0s' {1..40})"; then
  pass "the exact-head guard preserves explicit manual dispatch"
else
  fail "the exact-head guard rejected a manual dispatch: $(cat "$tmp/guard.out")"
fi
if run_guard "$head_guard" "EXPECTED_HEAD=$expected_head" "SELECTOR_DIGEST=$selector_digest" "GITHUB_SHA=$expected_head"; then
  pass "the exact-head guard accepts the derived default-branch head"
else
  fail "the exact-head guard rejected its derived head: $(cat "$tmp/guard.out")"
fi
if run_guard "$head_guard" "EXPECTED_HEAD=$expected_head" "SELECTOR_DIGEST=$selector_digest" "GITHUB_SHA=$(printf 'd%.0s' {1..40})"; then
  fail "the exact-head guard admitted a newer branch head after proposer derivation"
else
  pass "the exact-head guard rejects the mutable-branch TOCTOU race before verification"
fi
if run_guard "$head_guard" "EXPECTED_HEAD=$expected_head" SELECTOR_DIGEST= "GITHUB_SHA=$expected_head"; then
  fail "the exact-head guard admitted a partial proposer receipt"
else
  pass "the exact-head guard rejects partial proposer receipts"
fi

while IFS=' ' read -r prefix good; do
  : >"$tmp/version-output"
  if run_guard "$version_guard" "PREFIX=$prefix" "INPUT_VERSION=$good" "GITHUB_OUTPUT=$tmp/version-output"; then
    pass "the version guard accepts $good in namespace $prefix"
  else
    fail "the version guard rejected $good in namespace $prefix: $(cat "$tmp/guard.out")"
  fi
done <<'GOOD'
v v1.2.3
v v1.2.3-rc.1
v v1.2.3+build.5
python-v python-v1.2.3
worker_2-v worker_2-v3.4.5
GOOD
# `1.2.3` is the case #464 names: the engine strips a leading v, so a bare
# version cuts a tag that sorts apart from every v-prefixed sibling.
while IFS= read -r bad; do
  if run_guard "$version_guard" PREFIX=v "INPUT_VERSION=$bad" "GITHUB_OUTPUT=$tmp/version-output"; then
    fail "the version guard accepted '$bad'"
  else
    pass "the version guard rejects '${bad//$'\n'/\\n}'"
  fi
done <<'BAD'
1.2.3
v1.2
v1.2.3.4
v01.2.3
vlatest
V1.2.3
v1.2.3-
v.1.2.3
BAD

# Anchored against the whole string, not per line: a version carrying a second
# line would otherwise be admitted on the strength of its first.
if run_guard "$version_guard" PREFIX=v "$(printf 'INPUT_VERSION=v1.0.0\nevil')" "GITHUB_OUTPUT=$tmp/version-output"; then
  fail "the version guard accepted a multi-line version"
else
  pass "the version guard rejects a multi-line version"
fi

while IFS=' ' read -r prefix version; do
  if run_guard "$version_guard" "PREFIX=$prefix" "INPUT_VERSION=$version" "GITHUB_OUTPUT=$tmp/version-output"; then
    fail "the version guard accepted namespace '$prefix' for '$version'"
  else
    pass "the version guard rejects namespace '$prefix' for '$version'"
  fi
done <<'BAD_NAMESPACE'
python-v v1.2.3
v python-v1.2.3
Python-v Python-v1.2.3
python python1.2.3
BAD_NAMESPACE

: >"$tmp/version-output"
: >"$tmp/summary"
if run_guard "$version_guard" PREFIX=v INPUT_VERSION= GITHUB_OUTPUT="$tmp/version-output"; then
  grep -q '^version=v1.2.3$' "$tmp/version-output" \
    && pass "the blank version derives the canonical next version" \
    || fail "the blank version did not expose the canonical derived version"
  grep -q 'Assembled release notes' "$tmp/summary" \
    && pass "the blank version writes the operator-facing release preview" \
    || fail "the blank version did not write the operator-facing release preview"
else
  fail "the blank version failed to derive the canonical next version: $(cat "$tmp/guard.out")"
fi
: >"$tmp/version-output"
if run_guard "$version_guard" PREFIX=v INPUT_VERSION= COMPONENT=python GITHUB_OUTPUT="$tmp/version-output"; then
  grep -q '^selected=false$' "$tmp/version-output" \
    && pass "an empty component stream resolves to a no-release plan" \
    || fail "an empty component stream did not expose a no-release plan"
  grep -q '^version=$' "$tmp/version-output" \
    && pass "an empty component stream exposes no release version" \
    || fail "an empty component stream exposed a release version"
  grep -q 'Source commit' "$tmp/summary" \
    && pass "an empty component stream still records its source commit" \
    || fail "an empty component stream omitted its source commit"
else
  fail "an empty component stream failed instead of producing a no-release plan: $(cat "$tmp/guard.out")"
fi

printf 'immutable\n' >"$tmp/sandbox/CHANGELOG/v1.2.3.md"
git -C "$tmp/sandbox" add CHANGELOG/v1.2.3.md
git -C "$tmp/sandbox" commit -qm 'Add tagged immutable release snapshot'
git -C "$tmp/sandbox" tag v1.2.3
git -C "$tmp/sandbox" push -qu origin main
git -C "$tmp/sandbox" push -qu origin refs/tags/v1.2.3
: >"$tmp/release-state"
if run_guard "$tag_guard" VERSION=v1.2.3 GITHUB_OUTPUT="$tmp/release-state"; then
  grep -q '^snapshot-exists=true$' "$tmp/release-state" \
    && pass "the release-state guard resumes a matching existing snapshot" \
    || fail "the release-state guard did not expose an existing snapshot"
else
  fail "the release-state guard rejected a matching existing snapshot: $(cat "$tmp/guard.out")"
fi
printf 'changed after release\n' >"$tmp/sandbox/CHANGELOG/v1.2.3.md"
git -C "$tmp/sandbox" add CHANGELOG/v1.2.3.md
git -C "$tmp/sandbox" commit -qm 'Change released snapshot on default branch'
git -C "$tmp/sandbox" push -qu origin main
: >"$tmp/release-state"
if run_guard "$tag_guard" VERSION=v1.2.3 GITHUB_OUTPUT="$tmp/release-state"; then
  fail "the release-state guard admitted a conflicting existing tag"
else
  pass "the release-state guard rejects a conflicting existing tag"
fi
: >"$tmp/release-state"
if run_guard "$tag_guard" VERSION=v1.2.4 GITHUB_OUTPUT="$tmp/release-state"; then
  grep -q '^snapshot-exists=false$' "$tmp/release-state" \
    && pass "the release-state guard admits an unused version" \
    || fail "the release-state guard did not expose unused release state"
else
  fail "the release-state guard rejected an unused version: $(cat "$tmp/guard.out")"
fi
: >"$tmp/release-state"
git -C "$tmp/sandbox" remote remove origin
if run_guard "$tag_guard" VERSION=v1.2.4 GITHUB_OUTPUT="$tmp/release-state"; then
  fail "the release-state guard admitted a version when remote tag state was unavailable"
else
  pass "the release-state guard fails closed when remote tag state is unavailable"
fi
git -C "$tmp/sandbox" remote add origin "$tmp/release-remote.git"
printf 'immutable\n' >"$tmp/sandbox/CHANGELOG/v1.2.4.md"
: >"$tmp/release-state"
if run_guard "$tag_guard" VERSION=v1.2.4 GITHUB_OUTPUT="$tmp/release-state"; then
  fail "the release-state guard admitted an untagged released snapshot"
else
  pass "the release-state guard rejects an untagged released snapshot"
fi
runtime_temp="$tmp/runtime-runner"
runtime_output="$tmp/runtime-output"
mkdir -p "$runtime_temp"
if run_guard "$runtime_step" "RUNNER_TEMP=$runtime_temp" \
  "VERJSON_CHANGELOG_TOOL_CACHE=$runtime_temp/verjson-changelog-tools" \
  "GITHUB_OUTPUT=$runtime_output"; then
  grep -Fx "cache=$runtime_temp/verjson-changelog-tools" "$runtime_output" \
    && pass "the runtime capture pins the job-scoped changelog cache before verification" \
    || fail "the runtime capture omitted the trusted changelog cache output"
else
  fail "the runtime capture rejected its canonical cache path: $(cat "$tmp/guard.out")"
fi
: >"$runtime_output"
if run_guard "$runtime_step" "RUNNER_TEMP=$runtime_temp" \
  "VERJSON_CHANGELOG_TOOL_CACHE=$tmp/attacker-cache" \
  "GITHUB_OUTPUT=$runtime_output"; then
  fail "the runtime capture accepted a cache outside runner.temp"
else
  grep -qF "release verification cache must be job-scoped" "$tmp/guard.out" \
    && pass "the runtime capture rejects a cache redirected through GITHUB_ENV" \
    || fail "the runtime capture rejected an attacker cache for another reason: $(cat "$tmp/guard.out")"
fi
rm -rf "$tmp/sandbox/CHANGELOG"

# The escape hatch that keeps the generated file un-edited: an adopter whose
# suite is not `npm test` commits scripts/release-verify.sh instead.
printf '%s\n' '#!/usr/bin/env bash' 'echo "npm $*"' >"$tmp/bin/npm"
chmod +x "$tmp/bin/npm"
if run_guard "$suite_step" "PATH=$tmp/bin:$PATH" "RELEASE_VERIFICATION_PATH=$tmp/bin:$PATH"; then
  pass "the default suite runs when no release-verify.sh is present"
else
  fail "the default suite failed: $(cat "$tmp/guard.out")"
fi
grep -q '^npm test$' "$tmp/guard.out" \
  && pass "the default suite runs npm test" \
  || fail "the default suite never ran npm test: $(cat "$tmp/guard.out")"

printf '%s\n' '#!/usr/bin/env bash' \
  'echo "npm $*"' \
  'if [ "$1 $2" = "run typecheck" ]; then exit 4; fi' >"$tmp/bin/npm"
if run_guard "$suite_step" "PATH=$tmp/bin:$PATH" "RELEASE_VERIFICATION_PATH=$tmp/bin:$PATH" PACKAGE_VERSION=4.5.6; then
  fail "a failing default suite still passed the verify job"
else
  suite_rc=$?
  [ "$suite_rc" -eq 4 ] \
    && pass "a failing default suite preserves its exit status" \
    || fail "a failing default suite exited $suite_rc instead of 4"
  grep -qF 'Release verification failed against stamped dispatch version 4.5.6. Check for the hardcoded-version footgun:' "$tmp/guard.out" \
    && pass "a default-suite failure receives the stamped-version diagnostic (#862)" \
    || fail "a default-suite failure omits the stamped-version diagnostic: $(cat "$tmp/guard.out")"
  ! grep -qE '^npm run lint|^npm test$' "$tmp/guard.out" \
    && pass "the default suite stops at its first failing command" \
    || fail "the default suite continued after typecheck failed: $(cat "$tmp/guard.out")"
fi
printf '%s\n' '#!/usr/bin/env bash' 'echo "npm $*"' >"$tmp/bin/npm"
chmod +x "$tmp/bin/npm"

mkdir -p "$tmp/sandbox/scripts"
cat >"$tmp/sandbox/scripts/release-verify.sh" <<EOF
#!/usr/bin/env bash
set -e
test "\$VERJSON_CHANGELOG_TOOL_CACHE" = "\$RUNNER_TEMP/verjson-changelog-tools"
mkdir -p "\$VERJSON_CHANGELOG_TOOL_CACHE/$sha"
printf verified >"\$VERJSON_CHANGELOG_TOOL_CACHE/$sha/changelog.py"
echo REPO_HOOK_RAN
EOF
chmod +x "$tmp/sandbox/scripts/release-verify.sh"
release_runner_temp="$tmp/release-runner-temp"
mkdir -p "$release_runner_temp"
ambient_release_cache="/proc/verjson-persistent-changelog-cache"
export VERJSON_CHANGELOG_TOOL_CACHE="$ambient_release_cache"
verify_cache_step="$tmp/verify-cache-step.sh"
python3 - "$release" >"$verify_cache_step" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for step in doc["jobs"]["verify"]["steps"]:
    if step.get("name") == "Prepare job-scoped changelog tool cache":
        print(step["run"])
PY
verify_github_env="$tmp/verify-github-env"
RUNNER_TEMP="$release_runner_temp" GITHUB_ENV="$verify_github_env" \
  bash -eo pipefail "$verify_cache_step"
release_cache="$(sed -n 's/^VERJSON_CHANGELOG_TOOL_CACHE=//p' "$verify_github_env")"
if run_guard "$suite_step" "PATH=$tmp/bin:$PATH" "RELEASE_VERIFICATION_PATH=$tmp/bin:$PATH" \
  "RUNNER_TEMP=$release_runner_temp" "VERJSON_CHANGELOG_TOOL_CACHE=$release_cache"; then
  pass "the suite step runs an adopter's release-verify.sh"
else
  fail "the suite step failed with a hook present: $(cat "$tmp/guard.out")"
fi
{ grep -q '^REPO_HOOK_RAN$' "$tmp/guard.out" && ! grep -q '^npm test$' "$tmp/guard.out"; } \
  && pass "the hook replaces the default suite instead of running alongside it" \
  || fail "the hook did not replace the default suite: $(cat "$tmp/guard.out")"
[ "$(cat "$release_cache/$sha/changelog.py" 2>/dev/null)" = verified ] \
  && [ "$VERJSON_CHANGELOG_TOOL_CACHE" = "$ambient_release_cache" ] \
  && [ ! -e "$ambient_release_cache" ] \
  && pass "the verify hook overrides a hostile persistent cache and populates a cold SHA beneath runner.temp (#630)" \
  || fail "the verify hook cannot populate its job-scoped changelog cache"

# Removing the generated job override must reproduce the adopter failure: the
# repository hook inherits the hostile host cache and cannot create its cold SHA.
verify_without_cache="$tmp/release-without-verify-cache.yml"
cp "$release" "$verify_without_cache"
sed -i '/^      - name: Prepare job-scoped changelog tool cache$/,+1d' "$verify_without_cache"
missing_cache_step="$tmp/missing-verify-cache-step.sh"
python3 - "$verify_without_cache" >"$missing_cache_step" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for step in doc["jobs"]["verify"]["steps"]:
    if step.get("name") == "Prepare job-scoped changelog tool cache":
        print(step["run"])
PY
[ ! -s "$missing_cache_step" ] || fail "the verify-cache removal mutation left an override step"
if run_guard "$suite_step" "PATH=$tmp/bin:$PATH" "RELEASE_VERIFICATION_PATH=$tmp/bin:$PATH" \
  "VERJSON_CHANGELOG_TOOL_CACHE=$ambient_release_cache"; then
  fail "the verify hook survived removal of the runner.temp cache override"
else
  pass "removing the verify override reproduces the hostile persistent-cache failure (#630)"
fi
unset VERJSON_CHANGELOG_TOOL_CACHE

# A failing hook must fail the job, or the verify gate is decorative.
printf '%s\n' '#!/usr/bin/env bash' 'exit 3' >"$tmp/sandbox/scripts/release-verify.sh"
if run_guard "$suite_step" "PATH=$tmp/bin:$PATH" "RELEASE_VERIFICATION_PATH=$tmp/bin:$PATH" PACKAGE_VERSION=9.8.7 NODE_AUTH_TOKEN=do-not-print-this; then
  fail "a failing release-verify.sh still passed the verify job"
else
  suite_rc=$?
  [ "$suite_rc" -eq 3 ] \
    && pass "a failing release-verify.sh preserves its exit status" \
    || fail "a failing release-verify.sh exited $suite_rc instead of 3"
  grep -qF 'Release verification failed against stamped dispatch version 9.8.7. Check for the hardcoded-version footgun:' "$tmp/guard.out" \
    && pass "a suite failure diagnoses the stamped version and hardcoded-version footgun (#862)" \
    || fail "a suite failure omits the stamped-version diagnostic: $(cat "$tmp/guard.out")"
  ! grep -qF 'do-not-print-this' "$tmp/guard.out" \
    && pass "the stamped-version diagnostic does not expose the verification credential" \
    || fail "the stamped-version diagnostic leaked the verification credential"
fi

# Committing the hook without the executable bit is the ordinary way to get this
# wrong, and an existence test that only asks `-x` reads it as "no hook here" and
# silently runs `npm test` instead. The adopter then watches a green release
# verified by a suite they deliberately replaced.
printf '%s\n' '#!/usr/bin/env bash' 'echo REPO_HOOK_RAN' >"$tmp/sandbox/scripts/release-verify.sh"
chmod 644 "$tmp/sandbox/scripts/release-verify.sh"
if run_guard "$suite_step" "PATH=$tmp/bin:$PATH" "RELEASE_VERIFICATION_PATH=$tmp/bin:$PATH"; then
  fail "a non-executable release-verify.sh was ignored and the job passed on the wrong suite"
else
  pass "a non-executable release-verify.sh fails the job instead of falling back"
fi
if grep -q 'chmod' "$tmp/guard.out"; then
  pass "the failure tells the adopter to chmod +x the hook"
else
  fail "the non-executable failure does not name the fix: $(cat "$tmp/guard.out")"
fi
if grep -q '^npm test$' "$tmp/guard.out"; then
  fail "the default suite ran anyway, which is the fallback this case exists to reject"
else
  pass "the default suite does not run behind an unusable hook"
fi
rm -rf "$tmp/sandbox/scripts"

# The generated caller is dispatch-only, and no repository workflow triggered
# by pull_request may acquire the private release credential. Comments showing
# caller examples do not count; executable YAML does.
while IFS= read -r workflow; do
  if grep -qE '^  pull_request:|^  pull_request_target:' "$workflow" \
    && grep -qF 'secrets.NODE_AUTH_TOKEN' "$workflow"; then
    fail "an untrusted pull-request workflow exposes secrets.NODE_AUTH_TOKEN: $workflow"
  fi
done < <(find "$root/.github/workflows" -maxdepth 1 -type f -name '*.yml' -print)
pass "pull-request workflows do not receive the private release credential"

# --- generator hygiene, matching the sibling modes ---------------------------

for bad in 'main' "$(printf 'main\n    if: false')" '../../evil' "${sha^^}" "${sha}0" ''; do
  if bash "$gen" release-node "$bad" >/dev/null 2>&1; then
    fail "release-node accepted a non-commit ref: '$bad'"
  else
    pass "release-node rejects a non-commit ref: '${bad//$'\n'/\\n}'"
  fi
done

if bash "$gen" release "$sha" >/dev/null 2>&1; then
  fail "a bare 'release' mode emitted output; the stack must be named explicitly"
else
  pass "a bare 'release' mode is refused so no stack is chosen implicitly"
fi

[ "$fails" -eq 0 ] || { echo "$fails test(s) failed."; exit 1; }
echo "All tests passed."
