# 0232 — Renovate never edits generated callers; their pins move in the generators

- **Date:** 2026-10-15
- **Status:** Accepted
- **Issue:** [verJSON/.github#1812](https://github.com/verJSON/.github/issues/1812)
  (downstream: [Verjson/verjson-agents#427](https://github.com/Verjson/verjson-agents/issues/427))
- **Related:** [ADR 0038](../0038-canonical-changelog-contract/README.md) (the generated set),
  [ADR 0185](../0185-org-contract-distribution/README.md) (versioned distribution would
  remove generated artifacts, and this fence with them)
- **Category:** Dependency automation policy (organization Renovate configuration)

## Context

`scripts/gen-changelog-caller.sh` writes adopters' `release.yml` and
`changelog-contract.yml`. Adopters' contract tests compare those files byte for byte with
the output of the generator at their contract pin. The generated release callers pin
third-party actions (`actions/checkout`, `actions/setup-node`, `actions/upload-artifact`,
`actions/download-artifact`, `actions/cache`). The pr-gate caller names a literal
`ubuntu-24.04` runner. Renovate's `github-actions` manager reads both kinds of dependency.

Verjson/verjson-agents#419, #420, and #421 bumped those three actions in
verjson-agents' generated `release.yml`. Each one failed that repository's
byte-faithfulness test. Regenerating removes the edit, and Renovate then reopens it,
because the generator still pinned the old commits. Nothing here moved those commits: this
repository's `renovate.json` had no manager for the generator scripts. The generator also
spelled the checkout and setup-node pins a second time inside the generated contract test,
and it hashed them into an approved pre-credential step digest. A bump made by hand or by
automation therefore had to edit four places.

The hazard is property-shaped, not a fact about these three actions. Any generated path
holding a dependency the manager reads has it, and so does any pin a generator spells
outside the declaration that automation moves.

## Decision

1. **Adopters never renovate generated callers.** `renovate/generated-callers.json`, kept
   in this repository beside the generators, disables the `github-actions` manager for
   `.github/workflows/release.yml` and `.github/workflows/changelog-contract.yml`. The
   organization preset in Verjson/renovate-config extends it as
   `github>Verjson/.github//renovate/generated-callers`. That one change belongs to the
   owning repository (Verjson/renovate-config#59). This repository's own `renovate.json`
   extends the same reference, because its generated `release.yml` is byte-compared by
   `scripts/release-verify.sh` and cannot wait for the organization preset. Until
   renovate-config extends it, the preset protects no adopter.
2. **The fence is derived, not curated.** `scripts/ci-gate/renovate-generated-callers.test.py`
   generates every mode listed in the changelog generator's `usage()`. Every output path
   that holds a dependency the manager would update must be fenced. Every fenced path must
   be a generator output. Every other generator must emit no such dependency, unless the
   test records it as a non-adopter exception with its reason. Renovate cannot match a file
   by its generated header, so the fence matches by path. The narrowest reliable set is the
   two paths whose output is visible to the manager. Generated callers that reference only
   `Verjson/.github` workflows are left out, because the organization preset already
   disables their digest updates. Those paths are also the reusable workflows' own names in
   this repository, where Renovate must keep maintaining them.
3. **Generator pins are Renovate-managed here.** A regex custom manager over
   `scripts/gen-*.sh|py` reads each `release_*='owner/repo@<sha> # <tag>'` declaration. It
   uses the `github-actions` manager's datasource (`github-tags`) and versioning
   (`github-actions`). The existing `github actions digests` group moves the generators and
   this repository's workflows in one PR. Adopters receive a bump only when they regenerate.
4. **One declaration per pin, and checks that follow it.** A generator may not spell a
   third-party pin outside a declaration the manager reads. The test enforces this and
   lists one recorded exception. The generated contract test receives the checkout and
   setup-node declarations through `EXPECTED_RELEASE_CHECKOUT` and
   `EXPECTED_RELEASE_SETUP_NODE`. Its pre-credential step digest records which declared pin
   a step uses, not the pin's value. The step-sequence check already binds each such step
   to its exact declaration. The test applies the manager's own replacement to a copy of
   the generator and requires every release mode to pass the generated release-shape check.
5. **One audited commit per action in this repository.** The generator's pins equal every
   same-action pin in this repository's workflows. There are two exceptions: generated
   callers here, which follow their own contract pin (`scripts/release-verify.sh`), and
   workflows that another repository accepts by exact content digest (`runner-canary.yml`).
   Each exception is fenced from the grouped digest PR as well: the generated callers by
   the preset, and each exact-digest workflow by its own `renovate.json` rule. The test
   asserts both, so no exclusion from the coherence check is also a path Renovate edits.
6. **The binding is tested where the digest stopped carrying it.** Registered rejection
   cases run the generated release-shape check against every release mode, and each one
   asserts its own diagnostic: a verify checkout moved off its declared commit, a
   setup-node step moved off its declared commit, and an empty, unpinned, or wrong-owner
   `EXPECTED_RELEASE_*` value.

## Consequences

- An adopter that writes its own `.github/workflows/release.yml` also loses Renovate
  updates and vulnerability PRs for that file. An organization search on 2026-10-15 found
  54 `release.yml` files. Three carry no generator header: `AiB`, `catalog-ui`, and
  `verjson-platform-template`. `verjson-platform-template` runs semantic-release on push to
  `main`, which the dispatched-release contract treats as a defect to remediate.
  `catalog-ui` builds an image on tag push. `AiB` is a dispatched installer release. Their
  pins go stale until each one adopts a generated caller or renames its workflow. No
  `changelog-contract.yml` is hand-written.
- This repository's own generated `release.yml` is fenced by this repository's own
  `extends`, and also through organization inheritance once Verjson/renovate-config#59
  lands, which is correct: `scripts/release-verify.sh` rejects any byte the pinned
  generator did not emit.
- Organization Renovate policy now depends on a file in this repository. The trust domain
  is unchanged, because a change here already governs every merge gate.
- Tests in this repository that hard-code an action pin still fail on the grouped Renovate
  PR until someone updates them. Examples are `scripts/node-workflow-pins.test.sh` and the
  container contract tests. That is deliberate audit friction that predates this decision,
  not a gap in the fence.
- Versioned contract distribution (ADR 0185) would delete generated artifacts and with
  them this fence; supersede this record then.

## Amendment (2026-10-15, #1812) — the fence covers every release caller name, and goes live

**Context.** On 2026-10-15 `Verjson/renovate-config/default.json` still did not extend the
preset, so decision 1's fence protected no adopter that inherits the organization
configuration. Renovate kept opening digest PRs inside generated callers
(verjson-cli#314, AiB#348, AiB#349), and each one failed `changelog-contract`. The
preset also listed only `release.yml` and `changelog-contract.yml`. The generated
contract test accepts a release caller under any name: it checks every workflow that
calls `changelog-release.yml`, and `--release-caller-package-dirs` declares the extra
ones. verjson-cli's `release-cli-schema.yml` is `release-node` output and was unfenced.

**Why a path, again.** Fencing by generated-file identity is not available. In Renovate
44.65.5 (renovate-config's pin) and 44.164.6 (latest on 2026-10-15), `matchJsonata`
evaluates the per-dependency upgrade object (`dist/util/package-rules/jsonata.js`).
`matchFileNames` sees only `packageFile` and `lockFiles` (`files.js`). The
`github-actions` extractor returns dependencies with no file content
(`modules/manager/github-actions/extract.js`), so no matcher can read the generator
header. Verjson/renovate-config's fence test pins Renovate 44.65.5, but the hosted Renovate app runs a newer release, so re-check these matcher semantics, and the merge order that test exercises, whenever the pin is bumped.

**Decision.**

1. The preset fences `.github/workflows/release-*.yml` as well as `release.yml` and
   `changelog-contract.yml`. Adopters name an extra release caller
   `release-<purpose>.yml`. The pattern stays inside the glob subset that
   Verjson/verjson-agents' resolution model (`unfenced_paths`) understands: no regex,
   negation, brace, or in-segment `**`. That model treats any other pattern as no fence
   at all. `.yaml` is left out because no generator writes it, and the one
   `release.yaml` in the organization (`scv-iac`) is hand-written.
2. A hand-written workflow on a fenced path is reopened by its own repository with a
   rule that matches only `matchManagers: ["github-actions"]`, the file, and
   `matchDepTypes: ["action"]`. The rule precedes every disable in that repository.
   Any broader rule would also undo the organization preset's `uses-with` and
   `Verjson/.github` workflow disables. `enabled: true` also clears the extractor's
   `unversioned-reference` skip, so a reopened file must not contain a bare-SHA
   third-party pin. This repository's `release-app-canary.yml`, `release-dispatch.yml`,
   and `release-propose.yml` are reopened that way. Without that, the grouped digest PR
   would stop moving them and decision 5's coherence check would fail on that PR.
3. `scripts/ci-gate/renovate-generated-callers.test.py` treats the preset entries as
   globs and requires every generated output to be fenced, including a release caller
   placed at each recorded adopter name. Every pattern must reach a generator output.
   Every pattern must stay in the modeled subset. Every fenced hand-written workflow
   here that carries a dependency Renovate reads must be reopened, and each reopen must
   be narrow, ordered first, live, and free of unversioned pins.
4. Verjson/renovate-config#59 adds the extend as the last `extends` entry. Every preset
   in its list resolves before it, and its own `packageRules` follow it. Its test
   resolves the merged configuration with Renovate's own `resolveConfigPresets` and
   `applyPackageRules`, and it requires every fenced generated path to stay disabled.

**Consequences.** An organization survey on 2026-10-15 covered 99 non-archived
repositories. It found 45 `.github/workflows/release*.yml` files. Outside this
repository, every one that the new pattern newly reaches is generated: verjson-cli's
`release-cli-schema.yml` (`release-node`) and verjson-cloud-storage's
`release-propose.yml` (`release-propose`, which has nothing for Renovate to update).
Two hand-written `release.yml` files remain fenced, as they were before: `catalog-ui` and
`verjson-platform-template`. AiB's `release.yml` is now generated (`release-artifact`),
so the first Consequences bullet's list is down to those two. A release caller named
outside `release*.yml`, such as `publish.yml`, would still be unfenced. None exists
today. Closing that gap structurally means the generated contract test must reject a
release caller outside the fenced names. That would change the contract every adopter
regenerates, so it is left as a recorded option under #1812 rather than done here. A
Dependabot vulnerability alert still applies `force: vulnerabilityAlerts`
(`enabled: true`) after every rule, so a vulnerable action in a generated caller can
appear on the dependency dashboard. That entry stays approval-gated and never
automerges (renovate-config's `vulnerabilityAlerts`). The fix is still regeneration.
