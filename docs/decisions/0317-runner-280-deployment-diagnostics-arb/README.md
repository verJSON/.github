# 0317 — Board review of runner deployment diagnostics for #280

- **Date:** 2026-11-27
- **Status:** Accepted. All five providers accepted S1 and S2 in round two of the [chair record](arb-record.md).
- **Scope:** Part of [runner canary issue #197](https://github.com/verJSON/verjson-git-runners/issues/197).

## Decision and rationale

A protected canary dry-run reported a generic transport error. Runner #280 adopts the diagnostic relay previously approved in ADR 0286. It distinguishes authentication failures and other known causes while restricting what may reach the logs. The chair reproduced all eleven generated files from the protected contract commit `bf9d207c83211685bab2cd3e87eb6f2420d3808b`. [Canary run](https://github.com/verJSON/verjson-git-runners/actions/runs/44253156883) · [Runner PR #280](https://github.com/verJSON/verjson-git-runners/pull/280) · [ADR 0286](../0286-host-export-relay-allowlist-delivery-arb/README.md)

S1 qualifies this source at the Git objects below. The relay retains the upstream limits for plain words and short values inside multi-key secrets. The CLI reserves space for its host-export prefix within the 200-character allowance. The controller adds its own prefix after it checks the extracted detail. Changes to the source, parents, tree or scope require fresh qualification.

| Runner source | Git object |
| --- | --- |
| Base | `ab692ec94ce040a156238c46f82bb79614cb0e71` |
| Head | `124e20e5bc407bc8e1697855a02c0e64873ee760` |
| Native merge candidate | `36a26d7441ac12392bde4240fda470465c0f2361` |
| Head and candidate tree | `b190ff83b5822bd457e57f9c1c2a17575b51d85d` |

S2 keeps the publication hold pending a separately authorized window that can suppress publication, verify the target and queue, restore the workflow and prove zero candidate writes. An ordinary source merge requires the landed archive citation, independent review of the exact head and the applicable protected checks. The protected dry-run and rollback, real runner conformance and GitLab/Nexus runtime acceptance remain open. [Runner #197](https://github.com/verJSON/verjson-git-runners/issues/197) · [Runner #214](https://github.com/verJSON/verjson-git-runners/issues/214)

## Provenance

The chair supplied the original record directly before this archive. Its whole-file SHA-256 is `f9162721fccfe98384e194ce471a1ee40ea0eb66f07a11ab380ca018b546d4eb`. Run `arb-runner-280-canonical-deployment-relay-20261127` has Gate R fence SHA-256 `676069f323e263708c2fc935ac5a04024a4c0d3155f1b3d22e1490af42f867f7`. The archive receives its own independent exact-head review under the standing policy. [ADR 0309](../0309-standing-archive-only-admission-lane/README.md)
