# 0115 — Configurable package retention with rename-deprecation stubs

- **Date:** 2026-08-22
- **Status:** Accepted
- **Issue:** [#987](https://github.com/Verjson/.github/issues/987)
- **Related:** [ADR 0108](../0108-bound-package-retention-to-three-stable-releases/README.md)
- **Category:** Destructive package lifecycle

## Context

ADR 0108 bound `@verjson/*` package retention to exactly three stable
releases and hardcoded that count (`plan_target` raised `RetentionError`
for any `keep != 3`). Issue #987, filed from `tequityapp`, documented that
this hardcoding had already broken two consumers (`tequity-api`,
`tequity-worker`) and one earlier (`verjson-observability#194`), and — in a
later comment on the same issue — that the mechanism is `node-release.yml`'s
per-repository retention job working as ADR 0108 specified, not a defect.
The issue's own final comment (2026-08-2x) concludes the consumer-side drift
(exact-pinned versions sitting still) was the proximate cause of the observed
breakage, and that the org's actual, unresolved gap is narrower: **the
retention count has no config surface**, so a human weighing more storage
cost against a longer safety margin cannot express that decision without
a code change to the pinned contract implementation; and **a package rename
has no required deprecation step**, which is what turned `@verjson/uploads`
into a hard 404 with no forwarding pointer when it became
`@verjson/object-storage`.

This ADR does not reopen or reverse ADR 0108's default of three. Changing
that default is a storage-cost-vs-consumer-breakage tradeoff that requires
GitHub Packages billing visibility this repository's tooling does not have
(`tequityapp` reported hitting a Packages billing limit on 2026-08-16) — it
is called out explicitly below as an open decision for a human, not decided
here.

## Decision

1. **The retention count is a parameter, not a hardcoded literal.**
   `scripts/package_retention.py`'s `plan_target`, `_container_safety`,
   `build_plan`, and `apply_plan` all take a `keep: int` parameter (default
   `3`), and `main()` exposes `--keep` on the CLI (default `3`). The only
   validation is that `keep` is a positive integer — the prior "must equal
   3" assertion is gone, because it existed solely to block exactly the
   configurability this issue asks for.
   `node-release.yml` and `container-release.yml` source `--keep` from the
   org variable `VERJSON_PACKAGE_RETENTION_KEEP`, falling back to `3` when
   unset, with the value validated as a positive integer before use. No org
   variable is set as part of this change, so today's behavior — retain
   exactly three — is unchanged for every repository. A human can raise
   (or further bound) the window later by setting one org variable; that is
   the "simple config change instead of a code change" this issue asked for.

2. **A package rename gets a first-class deprecation step.**
   `node-release.yml` gains an optional `deprecates` input — a JSON array of
   `{"name","message"}` old scoped-package basenames — consumed by a new
   "Deprecate renamed package names" step that runs `npm deprecate` for each
   entry immediately after a successful publish, before retention's cleanup
   job runs. A repository renaming a package supplies this input on the
   first release under the new name, coupling "stop publishing the old name"
   to "point consumers still on the old name somewhere useful" in the same
   release, rather than leaving the old name to age out (or vanish with the
   repository) silently. The step fails closed on malformed or duplicate
   entries before calling `npm` at all, and is a no-op for the default `[]`.

3. **The "never delete a version a known consumer still pins" check is out
   of scope here, and stays out of scope.** Implementing it would require a
   cross-repository inventory of which `@verjson/*` versions which
   consumers currently resolve to — no such inventory exists in
   `Verjson/.github` (there is no lockfile registry, no dependents index,
   and no PM ownership registry that maps packages to pinned consumer
   versions; the closest artifact in this repository, the rework-telemetry
   repo list, tracks CI rework rates, not package consumption). Building one
   is a multi-repository undertaking — a scanner that can read every
   consumer's lockfile, keyed to org membership, refreshed on a cadence
   tight enough to matter before a retention run — and is explicitly not
   attempted in this change per the scoping instructions for #987. Issue
   #987's own final comment reaches the same conclusion independently: the
   retention job "cannot see consumers," and turning that into a real
   pre-delete check "may be out of scope for a publish-side contract."

## Consequences

- Every `@verjson/*` npm and container repository keeps retaining exactly
  three stable releases today; nothing about live behavior changes until a
  human sets `VERJSON_PACKAGE_RETENTION_KEEP`.
- Raising or further bounding the retention window is now a one-variable
  config change available to whoever owns GitHub Packages billing for the
  org, without touching `scripts/package_retention.py` or either release
  workflow.
- A future package rename has a supported, single-release way to leave a
  `npm deprecate` forwarding pointer at the old name; it does not retroactively
  restore `@verjson/uploads`, which is unrecoverable.
- The "stop deleting vs. keep a longer, consumer-aware window" policy
  question from #987 remains open. It is a storage-cost decision for a human
  with GitHub Packages billing visibility, not something this repository's
  tooling can resolve on its own — see the issue for the concrete tradeoff
  as measured (a per-package table of currently-surviving versions, and the
  `tequityapp` billing-limit data point). *Resolved on 2026-10-16: see the
  amendment below.*

## Verification

`scripts/package_retention_test.py` adds coverage for a configurable `keep`
default of 3, a positive-integer boundary check, and `build_plan`/
`apply_plan`/`_container_safety` threading a non-default `keep` through
container reachability. `scripts/ci-gate/release-node-deprecate.test.sh`
extracts the "Deprecate renamed package names" step body and exercises it
against a stubbed `npm`: single- and multi-entry deprecation, a no-op empty
array, and fail-closed behavior on a missing field, a duplicate old name,
and invalid JSON — all before any `npm deprecate` call. Both are registered
in `scripts/actions-ci-groups.tsv` (`platform` and `changelog-release`
respectively) so they run in Actions.

## 2026-10-16 amendment — retention is a policy value: unset, `1`–`999`, or `all`

**Context.** This ADR left open whether retention should ever stop deleting.
The variable has since been renamed `CI_PACKAGE_RETENTION_KEEP`
([ADR 0123](../0123-use-organization-neutral-ci-variables/README.md)), and
[ADR 0220](../0220-confine-release-credentials-to-dependency-acquisition/README.md)'s
2026-10-15 refinement forwards it to the generated release verification
hook. That refinement also capped the window at 999 and spelled its digits
in ASCII: the old `^[1-9][0-9]*$` guard admitted U+0663 under
`en_US.UTF-8`, and an unbounded value wraps to 0 in shell arithmetic.
Before the cap, keeping every version meant setting an arbitrarily large
number. After it, that workaround fails. The owner decided that "keep
everything" must be a stated policy, not a large number.

**Decision.** `CI_PACKAGE_RETENTION_KEEP` is a policy value with one
grammar. Every site that reads it accepts exactly this grammar:

| Value | Meaning |
| --- | --- |
| unset | the organization default, `3` (ADR 0108), through `vars.CI_PACKAGE_RETENTION_KEEP \|\| '3'` |
| `1`–`999`, ASCII digits, no leading zero | keep the newest N stable versions |
| `all`, exact lowercase | retention disabled; nothing is deleted |
| anything else | fail closed with an error naming the accepted forms |

"Anything else" includes `0`, values above 999, non-ASCII digits,
whitespace, and case variants such as `ALL`. The shell guards spell the
grammar as `^(all|[123456789][0123456789]{0,2})$`. `package_retention.py`
parses `--keep` through the same language (`KEEP_POLICY`, `parse_keep`).
The sites are:
- the generated release verify step, in all three modes, which forwards `all` unchanged to the hook;
- the `node-release.yml` and `container-release.yml` retention jobs. With `all`, `package_retention.py` validates its targets, prints `package-retention: disabled by policy (keep=all)`, and returns before any API call. The jobs also write "Package retention disabled by policy" to the run summary.

**Rationale.**
- Verjson's own default stays 3, under ADR 0108's storage bound. Nothing
  changes until someone sets the variable.
- `all` is an explicit per-organization or per-repository choice, not a
  default. The any-organization bootstrap needs it: an organization adopting
  the canonical contract must be able to keep every version without picking
  an arbitrary number that the 999 cap would reject.
- Exactly one spelling. Every consumer compares the value as a string: five
  shell guards, `package_retention.py`, and each adopter's release hook. A
  second accepted spelling would have to be normalized identically in all of
  them. One consumer that forgot would read `ALL` as an invalid number or,
  worse, as a window.

**Release-availability coupling.** The window is now an input to release
availability, not only a storage setting. The generated verify step refuses
an invalid value before the snapshot, so a malformed window blocks every
release before its irreversible tag. `vars` resolves a repository value
before the organization value, so this applies at either level. After
publication, `node-release.yml` retention fails and turns the run red.
`container-release.yml` retention is `continue-on-error`: its run stays
green, older images are not deleted, and the summary records the skip.
A release hook that reads the window, such as verjson-ci#298's, must treat
`all` as "never evicts". A hook that does not recognize `all` blocks every
release.

**Rollout warning.** Setting the organization variable to `all` turns
retention red in every repository still pinned to an older contract. A
variable set at organization level reaches every repository at once, and:
- an older retention guard (`^[1-9][0-9]*$`, or the earlier 999 cap without `all`) rejects `all` after publication. node-release retention goes red, and container-release skips its cleanup;
- an older generated verify step does not forward the variable at all.

Pin the new contract in every repository that reads the variable before
setting it to `all` at organization level. Until then, adopt `all` by
setting it at repository level, on a repository already on this contract.
verjson-ci#298 is the first consumer that must understand `all`.

**Enforcement.** `scripts/ci-gate/release-verify-retention-keep.test.py`
runs a shared corpus through all five shell guards and `parse_keep` under
C.UTF-8 and en_US.UTF-8, and requires one accept-or-refuse decision per
value. It also runs each retention job with `all` against an unreachable
API, and requires no deletion plus the summary line.
`scripts/package_retention_test.py` covers the parser and the no-op.
