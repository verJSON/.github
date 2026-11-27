# 0248 — Hold snapshots requiring downstream completion

- **Date:** 2026-10-28
- **Status:** Proposed
- **Issue:** [#1954](https://github.com/Verjson/.github/issues/1954)

## Context

A downstream publisher cannot authenticate the snapshot, source, or tag from the current snapshot caller. A retry can check out a historical tag before running repository hooks, so a hold implemented in a hook on the current branch does not apply reliably.

## Decision

A release-snapshot caller may explicitly select `--require-downstream-completion`. Its generated verify job fails before checking out a release tree, and its publish job independently fails before release lookup or publication. The generated contract binds the selection, the exact unconditional hold and its position, and the complete publish job. The audit regenerates the selected contract and caller. This is an unconditional interim hold: no input, tag, hook, or receipt can clear it.

Callers without this selection retain the existing no-image snapshot behavior. Existing pinned callers and independently triggered publishers must adopt and enforce their own current policy; this change does not alter them or their credentials, rulesets, or events. A generated caller's selection is repository policy that must be reviewed; absence is not proof that no downstream publication exists.

## Consequences

Selected releases cannot snapshot, publish, or successfully complete until a separately reviewed replacement provides authenticated downstream evidence. The design does not claim to implement a receipt, issuer, replay protection, or completion protocol. That later protocol must bind repository, run and attempt, source, tag object, snapshot commit and tree, changelog digest, and authorized publisher; specify retry, concurrency, and recovery; and obtain live nonproduction evidence before production adoption.
