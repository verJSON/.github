# 0174 — Declare the organization development team as code owner

- **Date:** 2026-09-10
- **Issue:** [#1272](https://github.com/Verjson/.github/issues/1272)
- **Category:** repository ownership and review authorization

## Context

The organization `main-protection` rule requires code-owner review, but a repository
without CODEOWNERS has no matching owner. The user explicitly selected the existing
`@Verjson/devs` team as owner of every repository. That selection determines the
owner declaration; it does not authorize granting new team access or changing
ruleset bypasses. Consumer implementation remains with each repository's PM.

## Decision

Generate `.github/CODEOWNERS` through the canonical
`scripts/gen-changelog-caller.sh codeowners <contract-sha>` entrypoint, using a
reviewed immutable contract checkout. One wildcard rule assigns every path,
including CODEOWNERS itself, to `@Verjson/devs`. The generated header identifies
its source and regeneration command. No path-specific override is implied.

`scripts/codeowners.py` is the single source of the exact artifact and its offline
conformance check. The generator delegates rendering to that helper. The check
rejects missing or byte-drifted ownership, additional overriding rules, symlinked
ownership files/directories, and competing root or docs CODEOWNERS files. Existing
consumer ownership intent must be reviewed before replacing competing files; a
conformance failure is not permission to delete unreviewed work. The organization
repository adopts the generated file and runs the check in Actions. Consumers
must use the matching helper from the same reviewed contract checkout; do not
copy a permissive substitute or change shared caller pins incidentally.

This extends the existing review requirement without changing its value, minimum
approval count, bypass actors, or merge authority. A visible team must have explicit
write access to a repository to be eligible as a code owner. Team membership or an
organization-wide owner selection is not proof of that access. Code conformance
only verifies the declaration; it never reports live eligibility.

## Eligibility and rollout

Read-only observation on 2026-09-10: `devs` is a visible (`closed`) team with six
members and a default `pull` permission. Successful team-repository and repository-team
listings did not associate `devs` with `Verjson/.github`. A direct permission query
returned 404 with an additional token-scope hint; that response alone cannot identify
the cause. The successful lists establish that explicit team write access has not
been verified for this adoption. No permission mutation was made.

Before declaring review effective, an authorized owner must resolve and verify the
team's actual repository write access. Do not silently grant organization-wide
write permissions. Then inspect GitHub's CODEOWNERS errors on the merged base branch
and demonstrate the expected team review request and eligible approval on a real
PR. The declaration applies from the base branch, so an introducing PR alone does
not prove runtime enforcement. Maintain the existing review requirement throughout.

For each consumer, its PM must inspect existing ownership, team access and normal
merge paths, generate and check the artifact, and preserve its own validation and
running log. Record missing access as a rollout blocker. A narrower set of eligible
reviewers can block non-bypass merges; do not assume existing approval requirements
or bypass actors make adoption incapable of blocking a merge. Neither this PR nor
an offline check establishes organization-wide coverage or closes #1272's rollout.

## Verification and use

```bash
# Run from the reviewed immutable Verjson/.github contract checkout.
bash scripts/gen-changelog-caller.sh codeowners "$PIN" > "$CONSUMER/.github/CODEOWNERS"
python3 scripts/codeowners.py check --repo-root "$CONSUMER"
python3 scripts/codeowners_test.py
```

Tests cover the exact wildcard owner, missing/wrong owner, overriding rules,
generated-header drift, alternate locations and symlink boundaries. The actual
canonical generator and self-adopted bytes are compared. A disposable exact-pin
release rehearsal checks that the added artifact mode preserves the existing
changelog contract and release path. No test grants permissions or weakens review.

[GitHub CODEOWNERS requirements](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
provide the platform eligibility and base-branch semantics; live effectiveness
requires the separate receipts above.

## 2026-09-10 — Check canonical entry spelling on every filesystem

[Issue #1312](https://github.com/Verjson/.github/issues/1312) reproduced a local
conformance false positive: case-insensitive lookup accepted generated content at
`.Github/CodeOwners` without the canonical `.github/CODEOWNERS` spelling. Inspect
the exact entry names in the repository root and `.github` directory as well as
the existing byte and symlink checks. No recursive search is needed: arbitrary
other directories are outside GitHub's supported ownership locations. Existing
fallback lookups already reject mixed-case aliases on case-insensitive filesystems.
A bounded case-insensitive adapter reproduces the old false positive and verifies
canonical success, capitalization refusal and unchanged fallback behavior. This
restores portable local conformance; no live enforcement bypass is claimed.

## 2026-10-19 — Sensitive-path approval stays out of CODEOWNERS

[Issue #1816](https://github.com/Verjson/.github/issues/1816) asked whether the
generated file should carry path-scoped `@verJSON/admins` routes after adopters lost
theirs on regeneration. It does not: the owner decided that CODEOWNERS states default
ownership only, and that additional admin approval of sensitive paths is a native
organization `required_reviewers` ruleset managed in
`config/required-reviewer-rulesets.json`. The decision, its scope and its rollout are
recorded in the 2026-10-19 amendment to
[ADR 0210](../0210-code-owner-review-is-real-through-a-generated-codeowners-member/README.md).
