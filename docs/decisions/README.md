# Decision log

Reverse-chronological index of org-level decisions.

> The table below is **generated** from each `NNNN-*/README.md` by
> `scripts/gen-adr-index.sh` (each row is the ADR's `# NNNN — Title` H1 + its
> `**Date:**`). Do not hand-edit it — add your ADR directory and run the script;
> CI (`actions-ci`) fails if the committed table is stale. This keeps concurrent
> ADR PRs from conflicting on a shared table.

<!-- BEGIN ADR INDEX -->
<!-- prettier-ignore -->
| # | Date | Decision |
|---|------|----------|
| [0224](0224-shard-changelog-release-actions-ci/README.md) | 2026-10-09 | Shard the changelog-release actions-ci group |
| [0222](0222-documentation-diffs-skip-heavy-actions-ci/README.md) | 2026-10-09 | Documentation diffs skip the heavy actions-ci matrix |
| [0221](0221-separate-node-release-runner-boundaries/README.md) | 2026-10-09 | Isolate Node release jobs on fresh hosted runners |
| [0220](0220-confine-release-credentials-to-dependency-acquisition/README.md) | 2026-10-07 | Confine release credentials to dependency acquisition |
| [0219](0219-retire-moving-major-tags-for-contract-releases/README.md) | 2026-10-06 | Retire moving major tags for contract releases |
| [0218](0218-require-explicit-release-dispatch-version/README.md) | 2026-10-06 | Require an explicit version for release dispatch |
| [0217](0217-bound-gar-canary-publishing-to-one-github-workflow/README.md) | 2026-10-05 | Bound GAR canary publishing to one GitHub workflow |
| [0216](0216-keyless-container-provenance-is-verified-per-registry/README.md) | 2026-10-05 | Keyless container provenance is verified per registry |
| [0215](0215-verified-oci-candidate-registry-destinations/README.md) | 2026-10-02 | Verify OCI candidate registry destinations |
| [0214](0214-private-candidate-builds-skip-untrusted-pull-requests/README.md) | 2026-10-02 | Keep private package contents out of pull-request candidate builds |
| [0213](0213-package-type-surface-release-authorization/README.md) | 2026-10-01 | Package type-surface rulesets use the release App only |
| [0212](0212-cli-projects-admission-accepts-only-generated-caller/README.md) | 2026-09-29 | CLI Projects admission accepts only the generated CI caller |
| [0211](0211-signed-merge-gate-provenance-binds-the-canonical-required-workflow/README.md) | 2026-09-27 | Signed merge-gate provenance binds the canonical required workflow through captured claim predicates |
| [0210](0210-code-owner-review-is-real-through-a-generated-codeowners-member/README.md) | 2026-09-27 | Code-owner review is real: the generated CODEOWNERS is a required member of the adopter contract |
| [0209](0209-hosted-tool-cache-trust-is-a-tree-property/README.md) | 2026-09-26 | Hosted tool-cache trust is a property of the admitted tree, not its root component |
| [0208](0208-independent-review-eligibility-is-collaborator-permission-not-association/README.md) | 2026-09-26 | Independent-review eligibility is the live collaborator permission, not `author_association` |
| [0207](0207-withhold-issue-mutation-from-terminal-merge-app/README.md) | 2026-09-23 | Withhold issue mutation from the terminal merge App |
| [0206](0206-ai-authorization-property-is-independent/README.md) | 2026-09-23 | Enroll the AI authorization arm with an independent property |
| [0205](0205-protected-consumer-type-surface-baseline/README.md) | 2026-09-23 | Resolve protected consumer type-surface baselines from the PR base |
| [0204](0204-retire-duplicate-cli-projects-repository-rule/README.md) | 2026-09-23 | Retire duplicate cli-projects repository rule |
| [0203](0203-retire-review-app-authorization-check-ownership/README.md) | 2026-09-23 | Retire review-App authorization-check ownership |
| [0202](0202-bind-autonomous-merge-to-independent-review-receipt/README.md) | 2026-09-23 | Bind autonomous merge to an independent-review receipt |
| [0201](0201-secret-bearing-fleet-report-uses-default-branch/README.md) | 2026-09-22 | Keep the full fleet report in a private workflow run |
| [0200](0200-temporary-legacy-authorization-check-write/README.md) | 2026-09-22 | Temporarily authorize exact legacy review-App checks |
| [0199](0199-authorization-checks-fail-without-review-app-credentials/README.md) | 2026-09-21 | Authorization checks fail without review App credentials |
| [0198](0198-confine-runner-host-evidence-credentials/README.md) | 2026-09-21 | Confine runner host-evidence credentials to the protected deployment environment |
| [0197](0197-pulumi-gitlab-mirror/README.md) | 2026-09-20 | Use Pulumi for the GitLab mirror artifact |
| [0196](0196-decline-unclassified-command-position-constructs/README.md) | 2026-09-18 | The guard-liveness event stream declines what it cannot classify |
| [0195](0195-an-indeterminate-authorization-answer-is-not-a-permissive-one/README.md) | 2026-09-18 | An indeterminate authorization answer is never decodable as a permissive one |
| [0194](0194-a-ref-scan-anchored-on-two-shapes-reads-green-over-the-shapes-it-cannot-see/README.md) | 2026-09-18 | A ref-encoding scan anchored on two shapes reads green over every shape it cannot see |
| [0193](0193-every-ci-gate-script-is-a-gate-unless-declared-a-library/README.md) | 2026-09-18 | Every `scripts/ci-gate/` script is a gate unless it is declared a library |
| [0192](0192-encode-adopter-ref-names-before-they-reach-a-url/README.md) | 2026-09-18 | Encode every adopter-controlled ref name before it reaches a `gh api` URL |
| [0191](0191-contract-releases-are-the-unit-of-adoption/README.md) | 2026-09-17 | A contract version is a release, declared once and read back from disk |
| [0190](0190-pin-the-producer-app-of-every-required-status-check/README.md) | 2026-09-17 | Pin the producer App of every required status check |
| [0189](0189-scheduled-audits-adjudicate-findings-against-a-reviewed-expectation/README.md) | 2026-09-17 | Scheduled audits adjudicate findings against a reviewed expectation |
| [0188](0188-contract-audits-assert-fields-against-a-growing-vendor-schema/README.md) | 2026-09-17 | Contract audits assert fields, and a growing vendor schema is not drift |
| [0187](0187-provision-the-review-environment-before-the-caller/README.md) | 2026-09-17 | Provision the review environment before the review caller |
| [0186](0186-required-checks-bind-through-repository-properties/README.md) | 2026-09-17 | Required status checks bind through repository properties, not per-repository rulesets |
| [0185](0185-org-contract-distribution/README.md) | 2026-09-17 | Distribute the organization contract as a version, not a commit |
| [0184](0184-merge-gates-assert-execution-not-absence-of-red/README.md) | 2026-09-17 | A merge gate asserts that verification executed, not that nothing is red |
| [0183](0183-release-plan-resolves-optional-version/README.md) | 2026-09-15 | Resolve an optional release version in the verified release plan |
| [0182](0182-package-backed-portable-ci-adapter/README.md) | 2026-09-14 | Package-backed portable CI adapter |
| [0181](0181-adr-coverage-claims-follow-their-tests/README.md) | 2026-09-14 | An ADR's coverage claims are checked against the tests that exist |
| [0180](0180-no-approved-organization-custody-for-environment-only-app-keys/README.md) | 2026-09-14 | No approved organization custody for environment-only App keys |
| [0179](0179-exhaustive-app-key-confinement/README.md) | 2026-09-14 | Confine every App private key, not only the three canonical roles |
| [0178](0178-publish-deferred-ci-as-its-own-check/README.md) | 2026-09-14 | Publish a deferred CI run as its own non-success check |
| [0177](0177-owner-approved-provisioning-delegation/README.md) | 2026-09-14 | Owner-approved delegation for agent-operated provisioning |
| [0176](0176-declare-conditional-node-floor-lane/README.md) | 2026-09-14 | Declare the conditional Node-floor lane in the canonical required-check contract |
| [0175](0175-enforce-nexus-ce-subscriber-access-at-gateway/README.md) | 2026-09-11 | Enforce Nexus CE subscriber access at the gateway |
| [0174](0174-declared-organization-codeowners/README.md) | 2026-09-10 | Declare the organization development team as code owner |
| [0173](0173-bind-core-check-producer-apps/README.md) | 2026-09-10 | Prepare exact App bindings for organization core checks |
| [0172](0172-opt-in-node-floor-preparation/README.md) | 2026-09-10 | Prepare an opt-in Node22 required-check lane |
| [0171](0171-inherited-reusable-environment-context/README.md) | 2026-09-10 | Inherit reusable environment secret context |
| [0170](0170-parent-owned-deployment-github-transport/README.md) | 2026-09-10 | Parent-owned deployment GitHub transport |
| [0169](0169-sealed-environment-app-bootstrap/README.md) | 2026-09-10 | Sealed environment App bootstrap |
| [0168](0168-correct-authn-required-workflow-baseline/README.md) | 2026-09-10 | Correct the Authn required workflow baseline after the activation trial |
| [0167](0167-controlled-authn-required-workflow-activation/README.md) | 2026-09-10 | Activate the staged Authn required workflow under explicit owner authority |
| [0166](0166-environment-only-app-private-keys/README.md) | 2026-09-10 | Confine App private keys to main-only environment secrets |
| [0165](0165-preserve-generated-changelog-runner-boundaries/README.md) | 2026-09-05 | Preserve generated changelog runner boundaries in consumer policy |
| [0164](0164-bridge-review-lifecycle-deliveries/README.md) | 2026-09-05 | Bridge review lifecycle deliveries independently of required workflows |
| [0163](0163-preserve-release-authorization-in-authn-required-workflow/README.md) | 2026-09-05 | Preserve release authorization in the authn required workflow |
| [0162](0162-unify-portable-ci-engine-and-forge-adapters/README.md) | 2026-09-03 | Unify the portable CI engine and forge adapters |
| [0161](0161-migrate-ci-to-gitlab-with-measured-parity/README.md) | 2026-09-01 | Migrate CI to GitLab with measured parity and reversible cutovers |
| [0160](0160-self-host-paid-package-distribution-on-nexus/README.md) | 2026-09-01 | Self-host paid package distribution on Sonatype Nexus Repository |
| [0159](0159-graduate-packages-to-v1-by-evidence/README.md) | 2026-09-01 | Graduate packages to `v1` by evidence, not by organization-wide wave |
| [0158](0158-pre-credential-release-reconciliation-hook/README.md) | 2026-09-01 | Container releases reconcile derived inputs before minting the release credential |
| [0157](0157-per-manifest-secretless-acquisition/README.md) | 2026-09-01 | Scope secretless acquisition and authorization per declared manifest |
| [0156](0156-deferred-ci-legible-to-merge-gate/README.md) | 2026-09-01 | Make Renovate-deferred CI legible to the pre-merge assertion |
| [0155](0155-de-escalate-the-authn-type-surface-required-workflow/README.md) | 2026-08-31 | De-escalate the authn type-surface required workflow until its consumer prerequisites land |
| [0154](0154-authenticate-required-workflow-identity-overrides/README.md) | 2026-08-30 | Authenticate required-workflow identity overrides |
| [0153](0153-cli-projects-package-surface-required-workflow/README.md) | 2026-08-29 | Bind cli-projects package-surface enforcement to a required workflow |
| [0152](0152-distribute-paid-packages-through-cloudsmith/README.md) | 2026-08-28 | Distribute paid packages through Cloudsmith entitlements |
| [0151](0151-authn-type-surface-required-workflow/README.md) | 2026-08-28 | Bind authn type-surface enforcement to a required workflow |
| [0150](0150-immutable-exact-runner-canary/README.md) | 2026-08-28 | Publish the exact-runner canary at an immutable ref |
| [0149](0149-authorize-exact-secretless-call-subsets/README.md) | 2026-08-27 | Authorize exact secretless call subsets |
| [0148](0148-hosted-runner-provenance/README.md) | 2026-08-27 | Produce deployable runner provenance on hosted infrastructure |
| [0147](0147-route-untrusted-ci-to-ephemeral-hosted-runners/README.md) | 2026-08-27 | Route untrusted CI to ephemeral hosted runners |
| [0146](0146-restore-absent-secretless-compatibility-targets/README.md) | 2026-08-26 | Restore absent secretless compatibility targets |
| [0145](0145-recover-zero-provider-review-preflight/README.md) | 2026-08-26 | Recover receipt-bound reviews that stop before provider execution |
| [0144](0144-exact-head-runner-deployment-review/README.md) | 2026-08-26 | Bind runner deployment to exact-head independent review |
| [0143](0143-populate-secretless-runtime-cache-from-public-lock-blobs/README.md) | 2026-08-26 | Populate secretless runtime cache from public lock blobs |
| [0142](0142-reserve-deepseek-completion-for-json-verdicts/README.md) | 2026-08-26 | Reserve DeepSeek completion for JSON verdicts |
| [0141](0141-swap-verified-compatibility-packages-without-graph-resolution/README.md) | 2026-08-26 | Swap verified compatibility packages without graph resolution |
| [0140](0140-resolve-package-compatibility-without-consumer-credentials/README.md) | 2026-08-26 | Resolve package compatibility without consumer credentials |
| [0139](0139-recover-orphaned-authorization-checks-across-runs/README.md) | 2026-08-26 | Recover orphaned authorization checks across runs |
| [0138](0138-keep-canonical-app-identities-organization-neutral/README.md) | 2026-08-25 | Keep canonical GitHub App identities organization-neutral |
| [0137](0137-cut-v1-0-0-across-pre-1-0-packages/README.md) | 2026-08-25 | Cut `v1.0.0` across the pre-1.0 `@verjson/*` packages |
| [0136](0136-separate-dependency-supersession-observation-from-mutation/README.md) | 2026-08-25 | Separate dependency-update supersession observation from mutation |
| [0135](0135-observe-live-generated-caller-capability-floors/README.md) | 2026-08-25 | Observe live generated-caller capability floors with a repository-scoped App token |
| [0134](0134-bind-node-release-assets-to-the-immutable-tag/README.md) | 2026-08-25 | Bind Node release assets to the immutable tag |
| [0133](0133-bind-terminal-merge-to-live-default-branch/README.md) | 2026-08-25 | Bind terminal merge to the live default branch |
| [0132](0132-base-authorized-pre-contract-snapshot-moves/README.md) | 2026-08-25 | Base-authorized pre-contract snapshot moves |
| [0131](0131-bind-terminal-caller-read-ceiling-to-callee/README.md) | 2026-08-25 | Bind the terminal caller read ceiling to the callee |
| [0130](0130-separate-explicit-label-caller/README.md) | 2026-08-24 | Separate explicit review labels from the required-workflow trigger |
| [0129](0129-bridge-explicit-review-label-deliveries/README.md) | 2026-08-24 | Bridge explicit review-label deliveries into the authorization arm |
| [0128](0128-separate-generated-contract-from-authorization-arm/README.md) | 2026-08-24 | Separate generated-contract enforcement from the authorization arm |
| [0127](0127-bind-container-private-acquisition-to-reviewed-package-manager/README.md) | 2026-08-24 | Bind container private acquisition to the reviewed package manager |
| [0126](0126-split-container-release-authority/README.md) | 2026-08-24 | Split container release Git and package authority |
| [0125](0125-bootstrap-canonical-ci-adopters-fail-closed/README.md) | 2026-08-24 | Bootstrap canonical CI adopters through a fail-closed manifest |
| [0124](0124-split-container-candidate-authority-entrypoints/README.md) | 2026-08-24 | Split container candidate authority entrypoints |
| [0123](0123-use-organization-neutral-ci-variables/README.md) | 2026-08-24 | Use organization-neutral canonical CI variables |
| [0122](0122-split-release-proposal-authority-entrypoints/README.md) | 2026-08-24 | Split release-proposal authority across reusable entrypoints |
| [0121](0121-consume-persistent-ai-review-authorization/README.md) | 2026-08-24 | Consume persistent AI-review authorization on synchronized heads |
| [0120](0120-bind-terminal-merge-to-repository-app-token/README.md) | 2026-08-23 | Bind terminal merge to a repository-scoped App token |
| [0119](0119-restore-dedicated-app-for-renovate-observation/README.md) | 2026-08-23 | Restore the dedicated App for Renovate observation |
| [0118](0118-route-private-terminal-merge-to-hosted-capacity/README.md) | 2026-08-23 | Route private terminal merge to hosted capacity |
| [0117](0117-route-private-terminal-merge-from-validated-lane-input/README.md) | 2026-08-23 | Route private terminal merge from the validated lane input |
| [0116](0116-node-ci-caller-supplied-health-check/README.md) | 2026-08-22 | node-ci accepts a caller-supplied health check and container port |
| [0115](0115-configurable-package-retention-with-rename-deprecation/README.md) | 2026-08-22 | Configurable package retention with rename-deprecation stubs |
| [0114](0114-generated-caller-capability-floor-audit/README.md) | 2026-08-22 | Detect consumer generated-caller pins that predate an assumed capability |
| [0113](0113-refresh-rework-thresholds-repo-list/README.md) | 2026-08-21 | Refresh the rework-telemetry repo list to activity-ranked repos |
| [0112](0112-arm-authorization-checks-reach-a-terminal-state/README.md) | 2026-08-20 | An armed authorization check always reaches a terminal state |
| [0111](0111-interim-pat-for-renovate-compatibility-control-plane/README.md) | 2026-08-20 | Interim human PAT for the Renovate compatibility control plane |
| [0110](0110-consume-arm-receipt-artifact-on-last-read/README.md) | 2026-08-19 | Delete the arm receipt artifact on its last required read |
| [0109](0109-observe-first-renovate-compatibility-control-plane/README.md) | 2026-08-18 | Observe-first Renovate compatibility control plane |
| [0108](0108-bound-package-retention-to-three-stable-releases/README.md) | 2026-08-18 | Bound package retention to three stable releases |
| [0107](0107-retain-typed-ai-extraction-diagnostics/README.md) | 2026-08-16 | Retain typed AI extraction diagnostics without provider content |
| [0106](0106-generated-changelog-publishes-live-required-context/README.md) | 2026-08-16 | Generated changelog publishes the live required context |
| [0105](0105-preserve-ai-review-across-head-supersession/README.md) | 2026-08-16 | Preserve AI review across head supersession |
| [0104](0104-trusted-renovate-changelog-attribution/README.md) | 2026-08-16 | Trusted automation attributes hosted Renovate changes |
| [0103](0103-os-scoped-hosted-lanes/README.md) | 2026-08-15 | OS-scoped hosted lanes are repository-scoped, bounded, and fail closed |
| [0102](0102-explicit-impact-for-new-changelog-fragments/README.md) | 2026-08-15 | Require explicit impact for new changelog fragments |
| [0101](0101-explicit-release-proposal-autonomy/README.md) | 2026-08-15 | Make release-proposal autonomy explicit and non-releasing |
| [0100](0100-bind-blocking-review-findings-to-exact-head-source/README.md) | 2026-08-13 | Bind blocking review findings to exact-head source |
| [0099](0099-release-app-replaces-admin-pat/README.md) | 2026-08-13 | A dedicated release App replaces the admin PAT |
| [0098](0098-require-bounded-ai-review-for-code/README.md) | 2026-08-12 | Require bounded AI review for code changes |
| [0097](0097-secretless-trusted-ref-node-validation/README.md) | 2026-08-12 | Extend secretless Node validation to trusted refs |
| [0096](0096-isolate-checkouts-on-reusable-runner-workspaces/README.md) | 2026-08-12 | Isolate checkouts on reusable runner workspaces |
| [0095](0095-bounded-secretless-node-cache-transfer/README.md) | 2026-08-12 | Bound secretless Node transfer to an exact-attempt private cache |
| [0094](0094-arm-required-by-its-own-property-scoped-ruleset/README.md) | 2026-08-11 | Require the authorization arm from its own property-scoped ruleset |
| [0093](0093-authorization-arm-uses-trusted-runner-lane/README.md) | 2026-08-11 | Route the authorization arm through the trusted runner lane |
| [0092](0092-stage-generated-changelog-contract-requirement/README.md) | 2026-08-10 | Stage the generated changelog contract requirement behind fleet conformance |
| [0091](0091-ruleset-requires-authorization-arm/README.md) | 2026-08-10 | Require the authorization arm, not the dispatched AI review |
| [0090](0090-human-first-opt-in-ai-review/README.md) | 2026-08-10 | Keep human approval available when AI review is opted in |
| [0089](0089-caller-supplied-privileged-routing/README.md) | 2026-08-10 | Pass privileged routing through the trusted caller |
| [0088](0088-auditable-organization-secret-scope/README.md) | 2026-08-09 | Make organization secret scope auditable before mutation |
| [0087](0087-runner-free-event-driven-ai-gates/README.md) | 2026-08-09 | AI gates never hold runners waiting for external CI |
| [0086](0086-secretless-node-pr-validation/README.md) | 2026-08-09 | Separate Node dependency acquisition from secretless PR execution |
| [0085](0085-immutable-privileged-caller-contract/README.md) | 2026-08-09 | Pin privileged callers to immutable contract revisions |
| [0084](0084-programmatic-privileged-routing/README.md) | 2026-08-09 | Resolve privileged routing programmatically |
| [0083](0083-pin-ai-review-action-and-source-bot-logins/README.md) | 2026-08-09 | Pin the AI review action and name source bot logins |
| [0082](0082-receipt-bound-provider-budget-policy/README.md) | 2026-08-08 | Receipt-bound provider and budget policy |
| [0081](0081-event-driven-terminal-ai-promotion/README.md) | 2026-08-08 | Event-driven AI authorization ends in terminal privileged promotion |
| [0080](0080-one-automatic-paid-ai-review-per-head/README.md) | 2026-08-08 | Allow one automatic paid AI review per head |
| [0079](0079-head-bound-ai-authorization-and-native-auto-merge/README.md) | 2026-08-08 | Head-bound AI authorization and native auto-merge |
| [0078](0078-container-release-and-runner-deployment-contract/README.md) | 2026-08-08 | Container releases promote immutable candidates before protected deployment |
| [0077](0077-private-repository-gate-restoration/README.md) | 2026-08-07 | Restore private-repository gates without speculative signing |
| [0076](0076-bounded-actions-ci-shell-test-groups/README.md) | 2026-08-07 | Bound `actions-ci` shell tests into three parallel groups |
| [0075](0075-generated-artifacts-is-the-changelog-check-prefix/README.md) | 2026-08-07 | Generated artifacts is the changelog check prefix |
| [0074](0074-signed-gate-attestation-provenance/README.md) | 2026-08-07 | Privileged merge trusts signed gate provenance |
| [0073](0073-required-node-ci-context-always-reports/README.md) | 2026-08-07 | The required Node CI context always reports |
| [0072](0072-watchdog-cadence-is-observed-not-guaranteed/README.md) | 2026-08-07 | Watchdog cadence is observed, not guaranteed |
| [0071](0071-changelog-impact-governs-version-bumps/README.md) | 2026-08-07 | Changelog impact governs version bumps |
| [0070](0070-component-scoped-changelog-streams/README.md) | 2026-08-07 | Changelog components are explicit release streams |
| [0069](0069-node-publication-consumes-contract-version/README.md) | 2026-08-07 | Node publication consumes the changelog contract's version |
| [0068](0068-dispatch-honors-caller-runner-labels/README.md) | 2026-08-07 | The merge dispatcher honors the caller’s explicit runner fleet |
| [0067](0067-portable-ci-command-boundary/README.md) | 2026-08-07 | Portable CI commands stop at the GitHub control plane |
| [0065](0065-verified-changelog-tool-cache/README.md) | 2026-08-07 | Changelog tooling uses a verified runner cache |
| [0064](0064-privileged-merge-reaches-the-overflow-lane/README.md) | 2026-08-07 | The privileged merge poller reaches the overflow lane |
| [0063](0063-required-workflow-events-are-bridged/README.md) | 2026-08-06 | A required workflow only sees three activity types, so the rest are bridged |
| [0062](0062-release-verifies-before-it-tags/README.md) | 2026-08-06 | The release caller is generated, and it verifies before it tags |
| [0061](0061-changelog-check-is-required-for-adopters/README.md) | 2026-08-06 | The changelog check is a required status check, scoped by a repository property |
| [0060](0060-node-release-retired/README.md) | 2026-08-06 | `node-release.yml` is retired: a release is dispatched, never derived from a merge |
| [0059](0059-released-snapshots-carry-the-release-note/README.md) | 2026-08-05 | Released snapshots carry the release note; the running log keeps the argument |
| [0058](0058-github-waits-for-checks-not-the-gate/README.md) | 2026-08-05 | GitHub waits for required checks; the gate stops being an orchestrator |
| [0057](0057-runner-labels-optional-lane-routed-callers/README.md) | 2026-08-05 | `runner_labels` is optional again, so generated callers route by lane |
| [0056](0056-fleet-watchdog-retained-and-retargeted/README.md) | 2026-08-05 | Keep the fleet watchdog, retargeted at the poll job the overflow lane cannot reach |
| [0055](0055-shared-generated-artifact-checks/README.md) | 2026-08-05 | Generated-artifact validation is a shared workflow with enumerated checks |
| [0054](0054-public-repositories-admitted-to-the-general-pool/README.md) | 2026-08-05 | Admit public repositories to the self-hosted general pool |
| [0053](0053-overflow-lane-for-polling-gate-jobs/README.md) | 2026-08-05 | One reversible overflow lane for jobs that poll the pool they wait on |
| [0052](0052-release-push-token-satisfies-branch-rules/README.md) | 2026-08-04 | The changelog release push needs a credential the branch ruleset bypasses |
| [0051](0051-ai-review-pr-head-evidence-boundary/README.md) | 2026-08-03 | AI review does not infer base state from the PR checkout |
| [0050](0050-actionlint-fast-lane-for-public-targets/README.md) | 2026-08-03 | actionlint takes the fast lane on public targets |
| [0049](0049-fleet-watchdog-preempts-poll-jobs/README.md) | 2026-08-03 | A watchdog preempts merge-gate poll jobs on the self-hosted fleet |
| [0048](0048-merge-gate-fast-lane-by-visibility/README.md) | 2026-08-03 | Merge-gate jobs take the fast lane when the target is public |
| [0047](0047-fast-lane-runner-variable/README.md) | 2026-08-03 | A fast lane for short CI jobs, selected by variable |
| [0046](0046-baseline-repository-hygiene/README.md) | 2026-08-02 | Baseline repository hygiene: a root README that answers three questions |
| [0045](0045-pin-validation-fetches-by-sha/README.md) | 2026-08-02 | Pin validation fetches by SHA, so it proves immutability but not reachability |
| [0044](0044-gate-provenance-bound-to-entry-workflow/README.md) | 2026-08-02 | Gate provenance is bound to the run's entry workflow |
| [0043](0043-privileged-merge-verifies-its-own-revision/README.md) | 2026-08-01 | Privileged merge verifies which revision of itself is executing |
| [0042](0042-privileged-merge-reusable-split/README.md) | 2026-08-01 | Privileged merge becomes a reusable workflow with a two-sided name contract |
| [0041](0041-shared-admission-hosted-and-self-hosted/README.md) | 2026-08-01 | Both hosted and self-hosted serve both public and private repositories |
| [0040](0040-runner-lanes-and-admission-axes/README.md) | 2026-08-01 | Lanes name the work; groups enforce admission |
| [0039](0039-required-workflow-gate-provenance/README.md) | 2026-07-31 | Organization required-workflow runs are trusted gate provenance |
| [0038](0038-canonical-changelog-contract/README.md) | 2026-07-30 | Canonical changelog fragments and immutable release snapshots |
| [0037](0037-isolate-actions-write-dispatch/README.md) | 2026-07-30 | Isolate Actions write permission in a metadata-only dispatcher |
| [0036](0036-separate-pr-review-from-privileged-merge/README.md) | 2026-07-30 | Separate PR review from privileged merge authority |
| [0035](0035-variable-driven-runner-lanes/README.md) | 2026-07-30 | Variable-driven runner lanes with a temporary permissive lane |
| [0034](0034-temporary-general-merge-gate/README.md) | 2026-07-29 | Temporarily route Verjson merge gates through general runners |
| [0033](0033-self-hosted-runner-policy-by-visibility/README.md) | 2026-07-29 | Route runners by repository visibility, on configurable self-hosted pools |
| [0032](0032-gate-budget-exceeded-outcome/README.md) | 2026-07-29 | Size the merge-gate review budget to the diff, and make budget exhaustion an explicit blocking outcome |
| [0031](0031-node-ci-isolated-pool-allowlist/README.md) | 2026-07-29 | Route node-ci on isolated-pool admission, not organization ownership |
| [0030](0030-portable-reusable-runner-policy/README.md) | 2026-07-28 | Separate Verjson runner policy from reusable-workflow portability |
| [0029](0029-repurpose-meta-runners-private-gate/README.md) | 2026-07-28 | Repurpose retired meta runners as private merge-gate capacity |
| [0028](0028-runner-security-tiers-cache-boundary/README.md) | 2026-07-28 | CI security tiers and runner-aware npm cache boundaries |
| [0027](0027-pulumi-preview-credential-boundary/README.md) | 2026-07-27 | Pulumi validation and live preview use separate credential boundaries |
| [0026](0026-actionlint-reusable-governed-runners/README.md) | 2026-07-27 | Reusable actionlint offers only governed runner choices |
| [0025](0025-release-tooling-audit-allowlist/README.md) | 2026-07-25 | Release-tooling audit accepts dated, per-advisory exceptions |
| [0024](0024-absent-checks-fail-closed/README.md) | 2026-07-25 | Absent CI checks fail the merge gate closed |
| [0023](0023-skip-ci-while-stability-days-pending/README.md) | 2026-07-24 | Skip org CI while a PR is held by renovate/stability-days |
| [0022](0022-gate-reusable-cross-org/README.md) | 2026-07-23 | Distribute the merge gate as a pinned cross-org reusable workflow |
| [0021](0021-node-ci-caller-supplied-db-image/README.md) | 2026-07-22 | node-ci runs a caller-supplied DB image on the shared self-hosted pool |
| [0020](0020-gate-constrains-dispatch-target-to-org/README.md) | 2026-07-22 | Merge gate constrains its dispatch target to this org |
| [0019](0019-gate-skips-rereview-on-unchanged-diff/README.md) | 2026-07-22 | Merge gate skips the paid re-review on a base-merge-only re-fire |
| [0018](0018-gate-elides-lockfiles-from-review/README.md) | 2026-07-22 | Merge gate elides generated lockfiles from the AI review payload |
| [0017](0017-two-stage-ai-merge-gate/README.md) | 2026-07-21 | AI merge gate uses two runner assignments and one long CI wait |
| [0016](0016-self-gate-runner-redundancy/README.md) | 2026-07-20 | Self-gate runner lane must be redundant (second dedicated `meta` runner) |
| [0015](0015-gate-retry-structured-output-flake/README.md) | 2026-07-20 | Merge gate retries a third time on a transient structured-output flake |
| [0014](0014-reusable-workflow-versioning/README.md) | 2026-07-20 | Version & pin the org reusable workflows (moving major tag) |
| [0013](0013-gate-auto-update-on-conflict/README.md) | 2026-07-20 | Merge gate tries update-branch before holding on a conflict |
| [0012](0012-gate-honors-do-not-merge-label/README.md) | 2026-07-20 | Merge gate honors a `DO NOT MERGE` label as a terminal hold |
| [0011](0011-hostinger-runner-labels-capability-accurate/README.md) | 2026-07-19 | Runner labels describe capability: drop `GCP` from the `hostinger` runner |
| [0010](0010-platform-templates-consume-reusable-workflows/README.md) | 2026-07-19 | Platform-template service repos consume org reusable workflows |
| [0009](0009-gate-files-followup-issues/README.md) | 2026-07-18 | Merge gate files tracking issues for substantive non-blocking findings |
| [0008](0008-gate-auto-update-stale-branches/README.md) | 2026-07-18 | Merge gate auto-updates stale branches before review/merge |
| [0007](0007-adaptive-verification-blast-radius/README.md) | 2026-07-18 | Adaptive verification: scale review to blast radius, escalate on error rise |
| [0006](0006-ai-rework-telemetry-observe-and-report/README.md) | 2026-07-18 | AI-work rework telemetry: observe-and-report, human holds the dial |
| [0005](0005-defer-renovate-release-age-prs/README.md) | 2026-07-15 | Defer AI review of Renovate PRs whose release-age gate is still pending |
| [0004](0004-ci-telemetry-via-observability-action/README.md) | 2026-07-15 | Merge-gate CI telemetry via the verjson-observability action |
| [0003](0003-runner-groups-gcp-github-manish/README.md) | 2026-07-15 | Runner groups: GCP / GitHub (last resort) / manish |
| [0002](0002-ai-review-graceful-budget-escalation/README.md) | 2026-07-15 | AI merge gate: escalate on budget exhaustion instead of failing |
| [0001](0001-renovate-automerge-ai-review/README.md) | 2026-07-13 | Renovate auto-merge + org-wide advisory AI review |

<!-- END ADR INDEX -->

## When to write an ADR (vs a GitHub issue)

An **issue** tracks *work* — transient, open→closed, "what needs doing / is it done".
An **ADR** records a *decision* — durable, "why is it this way, what did we rule out".
An issue's value ends when it closes; an ADR's begins there — so decision rationale must
not live only in issue comments (they rot and aren't versioned with the code).

Default flow:

1. **An issue is the front door** — bugs, tasks, and proposals start as issues (triage,
   discussion, backlog).
2. **Write an ADR only when the resolution locks in an architecturally-significant or
   hard-to-reverse decision** (auth/RBAC, rulesets/branch protection, IAM/OIDC, secrets,
   runner topology, anything destructive — always). A bug fix, dependency bump, or "adopt
   the existing pattern" needs no ADR.
3. **Wire them both ways** — the ADR's Context cites the issue #; the issue links the ADR;
   the implementing PR links both (*what* = issue, *why* = ADR).
4. **Close the issue on merge; the ADR persists.**
5. **Never edit a decided ADR to reverse it** — add a new ADR that supersedes it and link
   both.

Format: one directory per decision, `NNNN-kebab-title/README.md` (next zero-padded number),
added to the table above (newest first).
