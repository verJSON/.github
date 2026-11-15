# 0089 — Pass privileged routing through the trusted caller

- **Date:** 2026-08-10
- **Issues:** [Verjson/.github#721](https://github.com/Verjson/.github/issues/721),
  [Verjson/.github#676](https://github.com/Verjson/.github/issues/676)
- **Supersedes:** [ADR 0084](../0084-programmatic-privileged-routing/README.md)
- **Extends:** ADR 0036's separation of privileged merge from review execution

## Context

ADR 0084 made the reusable privileged workflow read the organization lane through a
fine-grained PAT. The live `micro-one` canary proved the authorization and exact-head
checks succeeded, then the route read failed with HTTP 403. Terminal repository
administration does not require organization Actions-variable read permission, and
coupling those capabilities made a declarative runner choice a new credential failure
surface.

The generated caller executes only from the target repository's trusted default branch.
It can evaluate the effective `VERJSON_LANE_PRIVILEGED` variable before invoking the
immutable reusable workflow. Repository variables can shadow organization variables, so
the reusable workflow must still treat the supplied value as untrusted routing data.

## Decision

Generated privileged callers pass `vars.VERJSON_LANE_PRIVILEGED` as the
`privileged_lane` workflow input. The canonical checkout-free resolver receives no
credential and accepts exactly `["self-hosted","general"]` for Verjson repositories
before the terminal merge job is scheduled. Missing, malformed, hosted, widened, or
shadowed values fail closed. The resolver's own bootstrap selector remains the same
fixed admitted DigitalOcean lane.

External organizations keep the explicit `runner_labels` escape hatch and do not use
Verjson's lane allowlist. The terminal job alone receives `ORG_ADMIN_TOKEN`; its
maintainer-permission, immutable-workflow, exact-head, arm-receipt, and authorization-App
checks are unchanged.

## Consequences

- Privileged promotion no longer depends on `ACTIONS_VARIABLES_TOKEN` or organization
  variable API permissions.
- A repository-level variable cannot redirect merge authority because only the exact
  admitted selector passes validation.
- A future lane change requires a reviewed canonical allowlist update before callers can
  schedule the terminal job on that lane.
- Existing callers must be regenerated at this contract revision to supply the input.

## 2026-08-14 amendment — repository-bound public hosted stage

The organization-wide lane cannot move yet: current private hosted capacity and budget
evidence does not prove that private privileged continuations remain placeable. The
consumer inventory found 55 active workflow locations: the canonical direct workflow in
`Verjson/.github` and 54 generated callers. Exactly two consuming repositories are
public: `Verjson/.github` and `Verjson/verjson-github-runner`; the other 53 generated
callers are private.

An independent adversarial review rejected a runner-executed resolver: even without a
credential or checkout, a compromised persistent worker could forge its job output and
place the following secret-bearing job back on that worker. Terminal placement therefore
uses GitHub control-plane context directly. The job-level admission expression recognizes
the two exact repository identities only with `public` visibility and the `runs-on`
expression selects exactly `ubuntu-24.04`; no job output participates. Every other
Verjson repository must be an unlisted `private` identity and receives the literal
`["self-hosted","general"]` selector. Unknown public identities, unknown visibility, and
visibility drift cannot schedule the terminal job. A complementary credentialless guard
runs on fixed `ubuntu-24.04` capacity and fails explicitly for those states, so the
workflow cannot conclude successfully merely because the secret-bearing job was skipped.
External organizations retain the existing `runner_labels` portability path.

This is a narrow reversible stage, not the organization-wide cutover. No organization
variable changes, new capacity, or credential changes are authorized by it. The runner
repository must regenerate its caller at the eventual immutable merge revision before
its staged route becomes active, and representative terminal canaries remain required.

Fleet conformance now inventories the canonical direct consumer plus generated caller
files rather than assuming every active repository is a consumer. It verifies secret
scope for both shapes. The direct workflow is required at the audit event SHA and must
match the checked-out canonical bytes exactly; a missing, unreadable, undecodable, or
mismatched direct workflow fails closed. For a generated caller it proves the pin exists
on canonical main, fetches the historical generator and reusable-workflow interface at
that exact pin, and
compares the caller with credentialless historical generation. The audit checkout stays
event-SHA-bound, while an unrelated audit commit no longer makes every unchanged caller
non-canonical. The audit itself is fixed to `ubuntu-24.04`; its privileged read token is
never placed by a repository variable or persistent runner output.

## 2026-08-21 amendment — the promised fail-closed check was never wired up

[Verjson/.github#988](https://github.com/Verjson/.github/issues/988) found that
`ai-privileged-merge.yml` declared `privileged_lane` as a `workflow_call` input,
and every generated caller correctly supplied
`vars.VERJSON_LANE_PRIVILEGED` for it (`scripts/gen-privileged-merge-caller.sh`),
but the reusable workflow itself never read, validated, or otherwise referenced
that input anywhere past its declaration. The private-Verjson branch of
`privileged_merge`'s `runs-on:` hardcoded `fromJSON('["self-hosted","general"]')`
unconditionally. Practical impact was limited — the literal selector cannot
itself be widened by a malicious input it never reads — but the fail-closed
validation this ADR's Decision section promises ("Missing, malformed, hosted,
widened, or shadowed values fail closed") did not exist in code: a
misconfigured, shadowed, or absent `VERJSON_LANE_PRIVILEGED` produced no
diagnostic anywhere.

The fix adds a validation step as the FIRST step of the existing
`privileged_merge` job that exact-matches the effective
`inputs.privileged_lane` against `["self-hosted","general"]` for the
private-Verjson route and fails the run with a `::error::` diagnostic on any
other value — missing, malformed, hosted, widened, or shadowed. `runs-on:` on
`privileged_merge` is byte-identical to before this fix: the step never reads
anything a runner produces and never changes where the job lands, so a
compromised runner still cannot forge its way into selecting the terminal
job's capacity — the same guarantee the rejected 2026-08-14 runner-executed
resolver would have broken.

The first attempt at this fix (PR #989, merged 2026-08-21 by this
organization's own AI-merge authority before the review below was acted on)
used a separate preceding job gated by `needs:`, run unconditionally on fixed
`ubuntu-24.04` capacity. The automated review on that PR correctly flagged
that this forced every caller — including an external self-hosted-only org
using the `runner_labels` escape hatch, which never otherwise needs
GitHub-hosted capacity for this workflow — to also obtain hosted capacity
just to reach a check that is a no-op for their route. A same-day follow-up
(#989's immediate successor) replaced the separate job with the in-job step
described above: it runs on whatever capacity `runs-on:` already resolved to
for that caller, so it adds no new capacity requirement for anyone. A
preceding job was the ADR's naturally-read shape ("before the terminal merge
job is scheduled"), but since `runs-on:` here is a fixed literal rather than
a value derived from any prior resolution step, an early step in the same job
delivers the identical fail-closed guarantee without that regression.

This restores an invariant already decided above; it does not change the
decision, so no new ADR number was minted for it.

## 2026-11-15 amendment — public route names the renamed runner repository

[Verjson/.github#1808](https://github.com/Verjson/.github/issues/1808)
found that `ai-privileged-merge.yml` still compared `github.repository`
against `Verjson/verjson-github-runner` after that repository was renamed
to `Verjson/verjson-git-runners`. The public-hosted stage above still
admits exactly two public Verjson identities; only the runner repository's
string changed. The canonical workflow and its caller-contract pins now
use `Verjson/verjson-git-runners`. Visibility and fail-closed rules are
unchanged.

This restores the intended public allowlist after the rename; it does not
widen the set of admitted repositories, so no new ADR number was minted.
