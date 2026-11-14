# 0263 — Pulumi private packages use scriptless, step-scoped acquisition

- **Date:** 2026-11-13
- **Issue:** [Verjson/.github#2082](https://github.com/Verjson/.github/issues/2082)
- **Category:** reusable-workflow package credentials and execution boundary (secrets)

## Context

ADR 0027 separates caller-controlled Pulumi validation from live preview
credentials. Its credential-free validation path cannot install private
`@verjson/*` dependencies, so scaffolded infrastructure repositories fail with
registry authentication errors before build or test commands run. Passing a
package token to `install-command` would expose it to repository-controlled npm
lifecycle scripts. This ADR refines ADR 0027's private-package consequence
only; its preview admission, runner, and cloud-credential decisions remain in
force.

## Decision

1. Callers may set `private-node-packages` to a newline-separated allowlist of
   exact `@verjson/*` package names. A validator downloaded from the immutable
   reusable-workflow SHA requires every allowlisted package to appear in the
   caller's npm lockfile and every private lock entry to be listed. Private
   entries must resolve to the matching HTTPS GitHub Packages archive and carry
   SHA-512 integrity. Unsupported lockfile versions, repository `.npmrc` files,
   extra private packages, and mismatched names or URLs fail closed.
2. Private acquisition runs only for a same-repository pull request, `push`, or
   explicit `workflow_dispatch`. A fixed workflow-owned `npm cache add` step
   fetches only lock-validated private tarballs with `node-auth-token` scoped to
   the `@verjson` registry. It checks each blob against SHA-512 integrity,
   rejects extra cache content, and removes the npm index that can contain
   signed redirect URLs. No caller command, package lifecycle script, cloud
   token, Git token, or OIDC credential runs in this step.
3. The user config is cleared after acquisition. The existing caller-owned
   `install-command` and `validate-command` run later with known credentials
   unset and an empty npm user config. npm prefers the verified private blobs
   while public packages remain fetchable without credentials. This keeps
   package lifecycle and caller code outside the credentialed step.
4. With an empty allowlist, the existing public-only validation path is
   unchanged. The private path supports npm lockfile versions 2 and 3 and does
   not accept repository-controlled registry configuration.

## Consequences

- Same-repository callers can validate against private packages without putting
  package credentials in their install or test scripts. Fork pull requests do
  not receive package credentials and cannot use the private acquisition path.
- The workflow downloads the exact locked private package blobs without running
  package scripts; caller installation then uses those verified blobs while it
  fetches public dependencies without credentials.
- The allowlist and lockfile must be updated together when a private package is
  added, removed, or renamed. Non-npm installers and lockfile versions 1 are
  outside this path.
