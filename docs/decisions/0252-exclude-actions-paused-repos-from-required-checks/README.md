# 0252 — Exclude Actions-paused repositories from required-check rulesets

- **Date:** 2026-11-03
- **Status:** Accepted (owner decision, 2026-11-03)
- **Issue:** [verJSON/.github#1985](https://github.com/verJSON/.github/issues/1985)
- **Category:** repository rulesets and branch protection — **sensitive class**
- **Related:** [verJSON/.github#1962](https://github.com/verJSON/.github/issues/1962), [#1869](https://github.com/verJSON/.github/issues/1869), [#1945](https://github.com/verJSON/.github/issues/1945)

## Context

GitHub Actions is paused for most repositories by an owner directive (#1869, 2026-10-28). Of the 33 repositories governed by `core-checks-node` (20515817), `changelog-contract-required` (20513599) and `ai-authorization-arm-required` (20722935), 28 have Actions off. Their required checks can never report, so every pull request is `BLOCKED` and merges only through the OrganizationAdmin bypass (#1962). #1945 and #1956 retire that bypass, which would leave those repositories with no merge path at all.

## Decision

While a repository's Actions are paused, it is excluded from the three required-check rulesets. Membership is explicit, not inferred:

- **Property:** a new organization custom property, `verjson-actions-state` (single select: `enabled`, `paused`), records the state. Existing properties keep their meaning.
- **Rulesets:** each of the three rulesets gains `repository_property.exclude: [{name: verjson-actions-state, property_values: [paused]}]`. Their include conditions, bypass actors and rules are unchanged.
- **Set:** `paused` is set only on governed repositories whose Actions are disabled at the time of the change. The 28 recorded in #1985 are AiB, renovate-config, and verjson-agent-observer, ai, ai-gguf, authn, browser-agent, ci, cli, cli-cloud, cli-projects, cloud-storage, compliance, compliance-schema, customer-lifecycle, document-contracts, editor, email, eslint-config, graphql-conventions, identity-lifecycle, infra, leads, object-storage, observability, payments, temporal-kit and video-forge. The five with Actions on (.github, verjson-agents, verjson-git-runners, verjson-identity-contracts, verjson-oidc-claims-middleware) are set to `enabled` and stay fully protected.
- **Baseline protection:** a stand-in org ruleset, `actions-paused-baseline-protection` (26466984), includes exactly the `paused` repositories on their default branch. It requires a pull request (no approvals, as before), blocks force-pushes and blocks deletion, with the same bypass actors. Without it, the exclusion removed every remaining rule on those branches, because `main-protection` (18098028) is disabled, and write collaborators could push directly to, force-push or delete `main`.
- **Re-enabling:** in order, (1) regenerate the repository's generated contract at one SHA, (2) the owner enables Actions, (3) set its property to `enabled`. Step 3 restores the required-check rulesets and drops it from the stand-in. Canary order: verjson-editor, verjson-ci, verjson-cli.
- **Other Actions-only rulesets (owner decision, extended):** 13 of the 28 were also governed by other rulesets requiring checks that cannot report without Actions.
  - Org ruleset 24056850 `core-checks-node-floor` gains the same paused exclude.
  - Org rulesets 21750617 (authn) and 21830367 (cli-projects) target by repository id and cannot take a property condition, so their enforcement is `evaluate`.
  - The repository rulesets on verjson-ai, ai-gguf, customer-lifecycle, eslint-config, graphql-conventions, identity-lifecycle, infra, leads, observability and temporal-kit are also set to `evaluate`. Results are still recorded, but nothing is blocked.
  - Each is restored to `active` when its repository is re-enabled.

## Consequences

- **No CI evidence while paused.** Excluded repositories merge through a pull request but without required CI evidence. The compensating control is process, not platform: the PM skill's Actions-disabled policy (verjson-agents `skills/pm-orchestrator`, Charter C) requires a head-bound local reproduction of the canonical lanes, with a receipt, before merging. On `paused` repositories that receipt replaces `scripts/assert-mergeable-head.sh`, which cannot pass there (Gate A finds no required-check rule). This is weaker than a platform check and is accepted only for the pause.
- **No bypass is needed.** All 28 merge normally through a pull request under baseline protection, so #1945/#1956 can retire the bypass without stranding them.
- **Reversible:** remove the exclude condition from the four org rulesets that carry it (20515817, 20513599, 20722935 and 24056850; their prior JSON is in #1985), delete ruleset 26466984, restore `enforcement: active` on the 12 single-repository rulesets, then delete the `verjson-actions-state` schema and its 33 values. That restores the pre-change state exactly (all rollback JSON is in #1985).
- **Explicit set, fail-closed drift check:** once no repository is `paused`, remove the exclude condition and ruleset 26466984, and delete the schema. Until then, a scheduled conformance check in `.github` must flag any `paused` repository that has Actions on (#1987, which blocks the first canary re-enable), because one left `paused` after re-enabling would silently stay without required checks.

## Amendment (2026-11-04, #1985) — the conformance audits derive the paused image

The decision above is unchanged. Applying it left the scheduled `Organization ruleset conformance` workflow red from its first run afterwards (41810784481), because four audits still compared the live rulesets with their `active` reviewed images. The drift was in the audits, not in the rulesets. Following [ADR 0254](../0254-audit-recognizes-the-main-protection-suspension/README.md), each audit now derives a reviewed paused image instead of being told to skip anything:

- **One table.** `config/actions-paused-drift-policy.json` remains the only record of the 12 paused-only relaxations and of the stand-in 26466984. `org-ruleset-conformance.py`, `package-type-surface-ruleset.py audit`, `cli-projects-package-surface-ruleset.py audit` and `authn-type-surface-ruleset.py audit` all read it through `accept_paused_evaluate` in `scripts/actions-paused-drift.py`. That function reads the property the same way the drift check does.
- **`evaluate` is the only difference allowed.** It is accepted only on a recorded relaxation, and only while the one repository the ruleset targets reads `verjson-actions-state=paused`. That repository must match by ID, not just by name. Rules, conditions and bypass actors must still match the reviewed image exactly. An unrecorded ruleset, or a targeted repository that is `enabled`, unset or unreadable, is an error naming that repository: restore the ruleset to `active`. Each audit prints the acceptance it relied on.
- **The stand-in has a derived contract.** 26466984 carries no bypass list of its own. Its bypass actors must exactly equal the reviewed `bypass_actor_contracts` entry named by `baseline_bypass_contract_ruleset_id`. That entry is `core-checks-node` (20515817), so the actors are OrganizationAdmin, Integration 4583107 and Integration 4693283, each `always`. Its rules must be one pull request requiring 0 approvals, plus `non_fast_forward` and `deletion`. It must target `~DEFAULT_BRANCH` of `verjson-actions-state=paused` repositories only, and its enforcement must be `active`. Anything else is an error.
- **Why `core-checks-node`, and not `main-protection`.** "The same bypass actors" in the decision refers to the required-check rulesets these repositories are excluded from. The live stand-in matches `core-checks-node` and `changelog-contract-required` exactly. `main-protection` (18098028) also carries Integration 2740, which the stand-in deliberately omits. Comparing the stand-in with `main-protection` would either fail against the applied state or call for adding a bypass actor.

Once no repository is `paused`, the removal steps above also remove the stand-in's ID, its reference and the relaxations from that table.
