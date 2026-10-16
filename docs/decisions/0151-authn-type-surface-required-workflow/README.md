# 0151 — Bind authn type-surface enforcement to a required workflow

- **Date:** 2026-08-28
- **Status:** Accepted
- **Issue:** [#1154](https://github.com/Verjson/.github/issues/1154)

## Context

`Verjson/verjson-authn` ruleset `21522093` requires a GitHub Actions check named
`type-surface-contract`. That check is authored in the pull request's own workflow, so a
pull request can replace the aggregator with an inert successful job carrying the same
name. App attribution plus a context string does not prove that protected code ran.

The type-surface contract also needs private `@verjson/*` packages and an immutable base
`NEXT/` snapshot. Giving those credentials to pull-request-authored build or contract
steps would repair the provenance defect by reopening a credential-execution defect.

## Decision

Create `.github/workflows/authn-type-surface-required.yml` as an organization-owned
required workflow. It runs only for `pull_request`, refuses every repository except the
numeric-rule-selected `Verjson/verjson-authn`, and calls protected canonical `node-ci.yml`
with `secretless-pr: true`. The acquisition job alone receives the consumer-scoped
`GITHUB_TOKEN` with contents, packages, and statuses read permissions. The reusable
workflow call is pinned to the reviewed immutable `c973a841` revision. The canonical
lane transfers only lock- and policy-bound package bytes and the exact auxiliary source;
checkout, install, build, and `test:type-surface-compatibility` execute credentiallessly
on the fixed ephemeral hosted lane established by ADR 0147.

Create a dedicated active organization branch ruleset named
`authn-type-surface-required-workflow`. Its scope is the exact immutable repository ID
`1302124584` and `~DEFAULT_BRANCH`; it has no bypass actors. Its sole rule selects the
canonical repository ID `1269388380`, exact workflow path, `refs/heads/main`, and the
merged canonical SHA supplied at rollout. A repository-local workflow or check bearing
the same display name has neither the required-workflow URL nor the protected path and
cannot satisfy this rule.

The organization-wide ruleset conformance audit normally requires the release App's
bypass on every default-branch-token ruleset. Its policy now carries one narrow exception
for this exact ruleset. The exception matches the ruleset name, numeric consumer scope,
canonical workflow repository/path/ref, immutable 40-hex SHA, disabled-on-create flag,
active enforcement, and empty bypass list; any widened or malformed variant remains a
conformance failure. This is an explicit no-bypass decision, not a generic exemption.

The checked-in rollout tool is read-only by default. Apply is allowed only after its
workflow, contract, and tool bytes are present at a SHA reachable from protected `main`,
the consumer identity and repository-ruleset preimage still match exactly — numeric ID,
repository source, default-branch scope, active branch target, empty bypass list, and the
sole GitHub-Actions-bound `type-surface-contract` required check — and the explicit
human acknowledgement is supplied. It first creates the organization rule disabled,
reads back its exact scope, path, SHA, and empty bypass set, then activates it and requires
exact equality again. An unreadable or mismatched active postimage always attempts to
restore the new rule to disabled and verifies that rollback; diagnostics expose only the
fixed rollback outcome, never API response detail. It does not remove repository ruleset
`21522093`.

After creation, use the tool's `snapshot` mode immediately before triggering a fresh
authn pull-request run, then verify it with the checked-in tool. The accepted receipt must
have an ID greater than the pre-trigger maximum and a creation time after the live
ruleset's activation timestamp, be completed/success, use event `pull_request`, bind the
exact candidate head, carry the canonical path, and use the consumer-scoped
`actions/required_workflows/<positive-id>` URL while the live organization rule retains
the exact workflow SHA. Only then may the authn-owned follow-up update its ADR and remove
ruleset `21522093`. A failure preserves both enforcement mechanisms and stops rollout.

## Consequences

- Pull-request-authored names and workflow rewrites no longer establish type-surface
  provenance.
- Private acquisition stays in protected code and PR-authored execution remains
  credentialless on disposable hosted capacity.
- The policy affects only `Verjson/verjson-authn`; no property assignment or name pattern
  can broaden it implicitly.
- Rollout is intentionally two-phase: merge protected bytes first, then apply and verify
  the organization rule under a human gate, then hand off consumer-owned retirement.

## 2026-10-15 amendment: repository identity is case-insensitive

The "repository source" this ADR requires to match exactly is a GitHub identity. Since the
organization's 2026-10-02 display-case rename it is reported as `verJSON/verjson-authn`.
It, the organization ruleset `source`, and the required-run `workflow_url` repository
segment now compare case-insensitively over GitHub's ASCII name charset through
`scripts/github_identity.py`. A different owner or repository still fails closed. Rationale
and evidence: ADR 0213's 2026-10-15 amendment ([#1779](https://github.com/Verjson/.github/issues/1779)).

## 2026-10-15 amendment: repin past #1750 and rotate from the admitted live predecessor

The generated required workflow now calls `node-ci-protected.yml` at
`576707fe0be0dabe43ea57c504575f8cda88b0d2`, the v4.0.0 release commit, which contains
#1750 and #1759. The rationale matches
[ADR 0153's 2026-10-15 predecessor amendment](../0153-cli-projects-package-surface-required-workflow/README.md).

The rollout tool previously could only create the organization ruleset. The earlier
activation therefore used a one-off procedure (ADR 0167), and the only predecessor the tool
knew was a hard-coded `f2f425e9` for `stage-release-bypass` (ADR 0163). It now uses the
same model as ADR 0153 through the shared `scripts/required_workflow_revision.py`:

- `dry-run` and `apply` read the bound revision from the live ruleset of 21750617.
- The tool accepts that revision only when the live image is that revision's reviewed
  image with the target's enforcement and the sole release-App bypass, when the revision
  is an ancestor of the target, and when its workflow is exactly the output of its own
  generator for the pin its header names.
- It writes the target image and verifies it. If the write fails, it restores the
  observed image and verifies that instead.
- `apply` makes no change when the exact target is already active.
- `snapshot` and `verify-run` still accept only the exact target image.
- The same `node-ci-protected` floor check runs before any mutation.
- A read-only `audit` mode backs the scheduled `required-workflow-audit` job.

`config/package-type-surface-rulesets.json` held another pinned copy of this
ruleset's revision (`dc6b2942`) for the package release-authorization audit. That copy
would have gone stale at the first rotation, in the same way the CLI projects record did.
Its `sha` is now `null`, which means the revision is delegated: the package audit still
governs the ruleset's shape and release-only bypass. Delegation is bounded: an entry must
name its owner in `delegated_to`, and `null` is accepted only for a ruleset registered in
`DELEGATED_REVISION_OWNERS` (today only 21750617, owned by
`scripts/authn-type-surface-ruleset.py`). A test requires every registered owner to
expose an `audit` mode that `org-ruleset-conformance.yml` runs. It also requires the live binding
to be an immutable SHA, but it accepts whichever revision the rotation tool installed.
`package-type-surface-ruleset.py apply` never writes a ruleset with a delegated
revision. The unused preimage is dropped.

`stage-release-bypass` is retired. The live ruleset is active with the release-App
bypass, so the evaluate-only transition it performed is complete and its predecessor can
never match again. Any later change to that bypass is a reviewed contract change that
this rotation path applies.
