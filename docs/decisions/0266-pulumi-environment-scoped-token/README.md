# 0266 — Pulumi preview can read an environment-scoped token

- **Date:** 2026-11-15
- **Issue:** [Verjson/.github#2085](https://github.com/Verjson/.github/issues/2085)
- **Category:** reusable-workflow secrets and environment binding (secrets)
- **Extends:** [ADR 0027](../0027-pulumi-preview-credential-boundary/README.md), [ADR 0239](../0239-unified-untrusted-execution-boundary/README.md)
- **Related:** [ADR 0166](../0166-environment-only-app-private-keys/README.md), [ADR 0171](../0171-inherited-reusable-environment-context/README.md)

## Context

A job that calls `pulumi-ci.yml` cannot set `environment:` itself, so only the
reusable preview job can bind a GitHub environment. Without that binding, an
adopter has to store `PULUMI_ACCESS_TOKEN` as a repository secret. GitHub passes
repository secrets to same-repository `pull_request` runs, and a writer can read
the token by editing the caller workflow. The owner-delegated decision on
[verjson-cli#367](https://github.com/verJSON/verjson-cli/issues/367) (D-2c)
accepts the xops-infra CI OIDC grant only if `PULUMI_ACCESS_TOKEN` is never
repository-scoped. Binding a job to an environment does not confine a
repository or organization secret that still exists
([#1285](https://github.com/Verjson/.github/issues/1285), ADR 0166).

ADR 0239's `preview-environment` is an approval gate for same-repository pull
requests. It is not a place to store the default-branch token: a pull request
must not select the environment that holds it. Hyphenated reusable-workflow
secret aliases cannot be environment secret names.

## Decision

1. Callers may set optional `secrets-environment`. Empty means the preview
   job's environment expression and the mapped `pulumi-access-token` path are
   unchanged.
2. The preview job's `environment` is derived from the event and the inputs,
   never from a step output. A `push` or `workflow_dispatch` whose ref is
   `refs/heads/<default branch>` binds `secrets-environment` when it is
   non-empty. A `pull_request` binds `preview-environment` only. Every other
   event binds no environment from this input.
3. `validate` and `preview-admission` do not set `environment` and do not read
   `secrets.PULUMI_ACCESS_TOKEN`, `secrets.pulumi-access-token`, or
   `secrets.NODE_AUTH_TOKEN`. Validation's package acquisition keeps the
   explicit `node-auth-token` grant from ADR 0263.
4. Trusted-ref admission treats a charset-valid `secrets-environment` as
   satisfying the Pulumi-token presence check and does not read the token.
   GCP workload-identity secrets are still required. The reviewed pull-request
   route still requires the mapped token. A name outside
   `[A-Za-z0-9_][A-Za-z0-9._-]{0,63}` does not satisfy the check.
5. On that trusted ref, preview reads `secrets.PULUMI_ACCESS_TOKEN` through the
   named grant `PULUMI_ACCESS_TOKEN: ${{ secrets.PULUMI_ACCESS_TOKEN }}`. A
   fixed step fails closed before cloud authentication when that value is
   empty, so the mapped alias is not a substitute once the caller opts in.
   The preview install may read `secrets.NODE_AUTH_TOKEN` from the same
   environment and otherwise keeps `secrets.node-auth-token`. The workflow
   does not use `secrets: inherit`.
6. The caller stores `PULUMI_ACCESS_TOKEN` only as a secret of the named
   environment. That environment uses a custom deployment branch policy that
   names only `main`, with no tag rule and no wildcard. The caller does not
   also keep a repository or organization copy.

## Consequences

- Adopters who leave `secrets-environment` empty keep today's mapped secrets
  and the ADR 0239 reviewer gate.
- A pull request cannot aim the preview job at the secrets environment. The
  expression ignores that input on `pull_request`, and admission still refuses
  a reviewed pull request that lacks the mapped token.
- Native deployment-branch policy is the caller's control on who may enter the
  environment. This workflow checks the ref before binding and does not
  re-read the branch policy. A caller who points the input at an environment
  with a wider policy still receives the token only on a default-branch
  trusted ref.
- A repository or organization copy of `PULUMI_ACCESS_TOKEN` stays readable by
  any workflow that references it. Opting in does not confine that copy.
  Operators delete it. Runtime expressions cannot tell a secret's provenance
  while the copy remains.
- The named grant is the transport ADR 0171's 2026-09-27 amendment proved for
  an environment-bound job. `secrets: inherit` would deliver every caller
  secret into the reusable workflow and is rejected here.
