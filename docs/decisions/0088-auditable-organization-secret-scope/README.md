# 0088 — Make organization secret scope auditable before mutation

- **Date:** 2026-08-09
- **Status:** Accepted

## Context

Issue #265 found four organization Actions secrets with `visibility: all`. The live inventory now contains seven secrets, and two (`AI_REVIEW_APP_PRIVATE_KEY` and `OPENAI_API_KEY`) are already selected only for `Verjson/.github`. The original assumption that `ORG_ADMIN_TOKEN` had one consumer is stale: generated privileged-merge and changelog-release callers need it in their caller repository context, alongside trusted scheduled and umbrella workflows.

GitHub code search is not an authority for this boundary. It can omit private or unindexed repositories, generated callers, and indirect reusable-workflow calls. In particular, `secrets: inherit` makes available secrets depend on the caller repository, while organization required workflows also execute in repository contexts across the fleet. A secret removed from a caller resolves to an empty string rather than producing a configuration error, so a visibility edit can fail late or silently degrade behavior.

The current consumers and safe visibility targets are therefore recorded in `config/org-actions-secret-policy.json`:

| Secret | Current consumers | Safe target now |
| --- | --- | --- |
| `ACTIONS_VARIABLES_TOKEN` | generated privileged-merge callers fleet-wide | `all` |
| `AI_REVIEW_APP_PRIVATE_KEY` | canonical gate-rearm and AI review workflows in this repository | `selected: Verjson/.github` |
| `ANTHROPIC_API_KEY` | organization required AI gate and reusable callers using inherited secrets | `all` |
| `NODE_AUTH_TOKEN` | generated Node CI and release callers plus private-package consumers | `all` |
| `OPENAI_API_KEY` | canonical required AI workflow in this repository | `selected: Verjson/.github` |
| `ORG_ADMIN_TOKEN` | generated privileged-merge and release callers plus trusted scheduled and umbrella workflows | `all` |
| `SUBMODULES_TOKEN` | Node/UI private-git CI, generated release/validation callers, catalog/template/umbrella workflows | `all` |

`all` is not a claim that broad access is ideal. It is the narrowest target supported by authoritative evidence today. `ORG_ADMIN_TOKEN`, `ACTIONS_VARIABLES_TOKEN`, and `SUBMODULES_TOKEN` may move to explicit selected sets only after generated callers and private-repository consumers are represented by a complete fleet manifest. `NODE_AUTH_TOKEN` and model-provider credentials may remain fleet-wide while their required-workflow/package roles remain fleet-wide.

## Decision

Keep an exact, checked-in policy for every organization Actions secret. A read-only, main-bound scheduled workflow compares the live secret names, visibility, and exact selected-repository grants against that policy. It fails closed on unreadable API state, an unmanifested secret, incomplete justification, visibility drift, or missing/excess selected grants. Tests replace `gh` entirely, so no test can read or mutate live organization settings. The trusted scheduled job receives `ORG_ADMIN_TOKEN` only as the API read credential; pull-request CI never receives it.

The schedule has no manually selectable ref, serializes overlapping audits without cancelling an established run, and is registered in the semantic privileged-workflow validator. That validator fixes the exact trigger, permissions, runner route, timeout, steps, immutable checkout, command, and token placement. Boundary validation also treats malformed or duplicate policy and API objects as controlled failures rather than allowing interpreter tracebacks to become the operational signal.

No automation in this decision writes secret values or visibility. Every live visibility change remains a separately authorized, one-secret-at-a-time operation after exact-head review and green CI, with before-state rollback evidence and a semantic consumer run. The policy must be updated and reviewed first; weakening it to match an unexplained live change is nonconformant.

## Consequences

- Secret scope and consumer rationale are reviewable without exposing credential values.
- New secrets and silent access drift produce a failing audit instead of becoming undocumented policy.
- This change deliberately performs no live organization mutation and does not close #265; it establishes the prerequisite control for later narrowing.
- Complete private-fleet and generated-caller inventories remain required before replacing any fleet-wide target with `selected`.

## 2026-10-15 amendment: selected grants compare as repository identities

The exact selected-repository grants compare as case-insensitive repository identities over
GitHub's ASCII name charset through `scripts/github_identity.py`, each exactly as often as
reviewed. The API reports `verJSON/<repository>` since the organization's 2026-10-02
display-case rename, while the policy records `Verjson/<repository>`. A missing, excess,
foreign-owner, or non-ASCII grant still fails. Rationale and evidence: ADR 0213's
2026-10-15 amendment ([#1779](https://github.com/Verjson/.github/issues/1779)).

## 2026-10-15 amendment: provider keys follow the caller-selected provider

`OPENAI_API_KEY` and `DEEPSEEK_API_KEY` target visibility `all`, not
`selected: Verjson/.github`. `ai-review-merge.yml` declares both as
`workflow_call` secrets and reads them in the provider steps
(`.github/workflows/ai-review-merge.yml:188-190`, `:1450`, `:1469`, `:1567`).
Callers invoke it from their own repository, so the secrets resolve in the
caller's context, which is the same reason `ANTHROPIC_API_KEY` is `all`. The org
default provider is `deepseek` (`AI_REVIEW_PRIMARY_PROVIDER`). On 2026-10-15 the
owner decided that any repository may switch to another provider without a
per-repository grant. A `selected` target would have left both keys empty in
every other repository's review, so the live visibility was correct and the
policy was not. `JEV_API_KEY` is manifested at its live visibility until
[#1820](https://github.com/Verjson/.github/issues/1820) narrows it to the
repositories authorized for provider disclosure
([#1385](https://github.com/Verjson/.github/issues/1385)).
