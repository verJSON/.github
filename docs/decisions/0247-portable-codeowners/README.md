# 0247 — Portable generated CODEOWNERS

- **Date:** 2026-10-28
- **Status:** Accepted
- **Issue:** [#1953](https://github.com/Verjson/.github/issues/1953)

## Context

The generated ownership contract assumes the verJSON team, preventing another organization from adopting the canonical changelog contract with its own owner. ADR 0210 requires an exact generated file and rejects competing or symlinked ownership files.

## Decision

The generator accepts an explicit organization and team pair for both CODEOWNERS and its contract test. With no options, the existing verJSON artifact stays byte-identical. Names are constrained to a single GitHub organization and team token; the generated contract records the parameters and hashes the exact emitted file. The audit reconstructs these parameters when regenerating the test. In GitHub Actions, the test refuses an organization different from the repository owner, including the verJSON default when no owner is specified. The organization audit enforces the same binding. Pinning, file location, and shadowing checks remain in force.

The supplied team is an operator selection, not proof that GitHub recognizes it or that it has sufficient access. Installation must independently verify membership, permissions, and review enforcement.

## Consequences

Adopters must generate the owner file and contract test with identical parameters and review ownership changes. Existing default adopters require no change. This does not change live rulesets or permissions.
