# 0265 — Node CI selects the package read token explicitly

- **Date:** 2026-11-14
- **Status:** Accepted
- **Issue:** [Verjson/.github#2069](https://github.com/Verjson/.github/issues/2069)
- **Category:** secrets and CI supply chain (sensitive class)

## Context

Generated Node CI callers pass the reusable workflow's `NODE_AUTH_TOKEN` from a
repository secret. Some consumers, including `verjson-authz`, intentionally use
the workflow's per-run `GITHUB_TOKEN` with only `packages: read` instead of an
organization package token. The general secret-alias map must continue to reject
`GITHUB_*` names and arbitrary expressions: those values are not repository
secrets and should not become a general credential-forwarding path.

## Decision

Add the opt-in `package-token: "github-token"` value to `ci-levels.json`. It
selects the fixed `GITHUB_TOKEN` source for the reusable `NODE_AUTH_TOKEN` input
and emits that mapping for every generated `node-ci.yml` invocation, including
`buildArtifact` jobs. Every ordinary level must select one of `secretless-pr`
or `secretless-trusted-ref`; the default credentialed install path is rejected.
`buildArtifact` already supplies those isolated acquisition modes. The generated
caller continues to grant `packages: read`, and the `buildArtifact`
authorization-plan v1 shape remains unchanged.

This option accepts no other value. A caller cannot map `NODE_AUTH_TOKEN` in the
`secrets` object at the same time. The general `secrets` array and object forms
continue to validate repository secret names and reject every `GITHUB_` name;
they never accept expressions. Other reusable-workflow secrets remain explicit
repository-secret mappings.

The reusable workflow remains responsible for keeping package credentials
inside its acquisition boundary. The selected token must not reach install
scripts, builds, or tests.

## Consequences

- Consumers can read approved internal packages with the least-privilege
  per-run token and do not need an organization package token.
- The new fixed option narrowly selects one existing GitHub token. It does not
  change the generic secret alias contract recorded in ADR 0264.
- Package access still depends on the caller's repository permissions and the
  package's configured repository access.

## Rollback

Remove `package-token` from the consumer configuration and restore an explicit
repository-secret mapping only after the consumer has an approved credential
with the required package-read scope. Do not widen the generic alias validator
or pass arbitrary expressions to the reusable workflow.
