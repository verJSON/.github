# 0163 — Preserve release authorization in the authn required workflow

- **Date:** 2026-09-05
- **Status:** Accepted
- **Issue:** [#1258](https://github.com/Verjson/.github/issues/1258), migration [#1154](https://github.com/Verjson/.github/issues/1154)
- **Category:** rulesets, release authorization — sensitive class
- **Supersedes:** the empty-bypass decision in [ADR 0151](../0151-authn-type-surface-required-workflow/README.md)

## Context

Authn [PR #267](https://github.com/Verjson/verjson-authn/pull/267) and
[ADR 0048](https://github.com/Verjson/verjson-authn/blob/main/docs/decisions/0048-grant-the-release-app-the-sole-type-surface-bypass/README.md)
intentionally granted `release-authorization` App `4583107` the sole bypass on
repository ruleset `21522093`. The dispatched v2.0.0 release had passed its verify
job but its atomic snapshot/tag push failed with GH013 because a direct push
cannot carry the pull-request-only `type-surface-contract` check. This was an
accepted release requirement, not consumer drift.

The canonical migration still expected no consumer bypass and proposed the same
empty bypass on organization ruleset `21750617`. Its protected workflow runs on
`pull_request` only, so activating that replacement would restore the release
blocker. ADR 0155 currently leaves it in `evaluate` until a fresh required-workflow
receipt exists. Consumer CI success is not that receipt.

## Decision

Both the retired consumer preimage and replacement organization rule admit exactly:

```json
[{"actor_id":4583107,"actor_type":"Integration","bypass_mode":"always"}]
```

No organization administrator, extra App, or `merge-authorization` App `4693283`
receives this bypass. Pull-request-only bypass mode cannot authorize the dispatched
direct push. Exact matching rejects both an empty list and any additional actor.
The repository/ref scope and exact protected workflow binding remain unchanged.

The release App is a trusted exception, not evidence that the bypassed type-surface
workflow ran. The inspected authn adopter pins its snapshot caller to
`changelog-release.yml@a350cc807499a01f30065fec690466f658f0dd01`; that canonical path
writes release snapshots and tags after verification. GitHub does not constrain an
App bypass to a changelog-only diff: this bound depends on the trusted pinned
release implementation and protected App credentials. Compromise of that App can
bypass this gate for other writes. Moving the release pin must preserve that trust
boundary; the merge App needs no exception because its PR head can run the check.

`stage-release-bypass` accepts only the known ruleset `21750617`, in `evaluate`,
with its exact previous `f2f425e93af60417f7193abd686007d463171d1d` workflow binding
and empty bypass. It validates merged canonical bytes and the approved consumer
preimage, requires the explicit apply acknowledgement, re-reads the old image,
then writes the release-only bypass and one immutable merged contract SHA.
It verifies the complete postimage and never activates the rule. An exact
postimage is an idempotent no-op; any other preimage fails closed. GitHub offers no
conditional compare-and-swap here, so the immediate re-read narrows rather than
eliminates concurrent administrator races. A failed postimage check requires live
inspection; the tool does not blindly overwrite unexpected state with a rollback.

Read-only `--evaluate` snapshot/verification modes allow the authn owner to produce
and verify the receipt before activation. The default active verification remains
strict. ADR 0155 activation and follow-up mergeability conditions still apply;
this change neither activates the org rule nor removes consumer ruleset `21522093`.

## Verification and rollout

Behavioral tests cover release-only payloads, both bypass drift directions, wrong
App/mode/scope/pin, acknowledgement, concurrent preimage drift, postimage drift,
idempotency, and read-only evaluation. Existing spoofed-check, exact-head, and
required-workflow-origin receipt controls remain mandatory.

After this commit merges, run the merged tool with `stage-release-bypass`, its
merged 40-character SHA, and `--ack APPLY-AUTHN-TYPE-SURFACE-1154`; then run
`dry-run --evaluate` at that SHA. Retain the non-secret pre/postimage and correction
on #1154. Return the exact-head receipt work to
[authn #314](https://github.com/Verjson/verjson-authn/issues/314). Do not infer a
successful live release or activate from mocked verification alone.

## Rollback

Before activation, the previously reviewed evaluate-only image can be restored
with exact pre/postimage checks; it grants no new enforced bypass. Never remove
the consumer release bypass merely to fit the obsolete migration preimage. After
activation, a rollback must preserve the release App exception or return the org
rule to `evaluate`; otherwise it recreates the demonstrated release outage.

## 2026-10-15 amendment: `stage-release-bypass` retired

The staging transition is complete: ruleset 21750617 is active with the release-App
bypass. The mode and its hard-coded `f2f425e9` predecessor are removed. The bypass
requirement above is unchanged and is now enforced by the general rotation path in
[ADR 0151's 2026-10-15 amendment](../0151-authn-type-surface-required-workflow/README.md).
