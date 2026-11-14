# 0264 — Node CI submodule credentials end at checkout

- **Date:** 2026-11-14
- **Status:** Accepted
- **Issue:** [Verjson/.github#2067](https://github.com/Verjson/.github/issues/2067)
- **Category:** secrets and CI supply-chain (sensitive class)

## Context

`node-ci.yml` declares the reusable-workflow secret `submodules-token` for private sibling submodules. Repository secret names cannot contain hyphens, but `gen-node-ci-caller.py` previously rendered every declared secret as a same-name lookup. The generated caller therefore referenced `secrets.submodules-token`, which consumers cannot provide as a repository secret. Handwritten callers work around this by mapping the declared input to `SUBMODULES_TOKEN`.

The regular `build-test` checkout also kept its submodule authentication active after checkout. The pinned checkout action stores token configuration under `$RUNNER_TEMP` and references it from Git config; with persistence enabled, later `npm ci`, package lifecycle scripts, and tests run while that reference remains active, so dependency code can invoke Git with the credential. The [pinned checkout implementation](https://github.com/actions/checkout/blob/3d3c42e5aac5ba805825da76410c181273ba90b1/src/git-auth-helper.ts#L321-L379) creates the temporary configuration, and [checkout cleanup](https://github.com/actions/checkout/blob/3d3c42e5aac5ba805825da76410c181273ba90b1/src/git-source-provider.ts#L316-L325) removes it when persistence is disabled.

## Decision

1. `ci-levels.json` keeps the array form as shorthand for same-name mappings and adds an object form mapping declared reusable-workflow secret names to caller repository secret names. The parser validates caller names against `node-ci.yml` and validates repository secret names against GitHub's supported name format. It rejects `GITHUB_` names and refuses a hyphenated caller secret in the same-name shorthand, so the invalid lookup cannot be generated again.
2. `node-ci.yml` continues to pass `submodules-token` to the checkout that fetches private submodules, but sets `persist-credentials: false` for that checkout in every mode. Later install, lifecycle, build, and test steps do not inherit the checkout credential from `.git/config`.

## Consequences

- A generated caller can map `submodules-token` to `SUBMODULES_TOKEN`; existing same-name mappings continue to work.
- The checkout action may use the token to fetch submodules, but subsequent commands cannot use it through persisted Git configuration. Any future operation that needs authenticated Git access must receive an explicit, short-lived credential only for that operation.
- Consumers still own creating and granting the repository secret. The generator validates the name and wiring but cannot verify that the secret exists or can read the sibling repository.
