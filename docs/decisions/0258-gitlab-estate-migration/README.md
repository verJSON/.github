# 0258 — Migrate the repository estate to GitLab CE in owner-approved waves behind a verified one-way mirror from GitHub

- **Date:** 2026-11-05
- **Status:** Proposed. The owner approved the design and a rehearsal of 2–3 repositories.
  Nothing in this record has been executed. The rehearsal runs only after ARB review and
  an explicit owner go-ahead.
- **Issue:** [verJSON/.github#1264](https://github.com/verJSON/.github/issues/1264)
- **Related:** [#2032](https://github.com/verJSON/.github/issues/2032),
  [verjson-ci#30](https://github.com/verJSON/verjson-ci/issues/30),
  [verjson-git-runners#208](https://github.com/verJSON/verjson-git-runners/issues/208),
  [ADR 0160](../0160-self-host-paid-package-distribution-on-nexus/README.md),
  [ADR 0161](../0161-migrate-ci-to-gitlab-with-measured-parity/README.md),
  [ADR 0162](../0162-unify-portable-ci-engine-and-forge-adapters/README.md),
  [ADR 0197](../0197-pulumi-gitlab-mirror/README.md),
  [ADR 0252](../0252-exclude-actions-paused-repos-from-required-checks/README.md),
  [ADR 0256](../0256-netcup-stack-backup-and-disaster-recovery/README.md)
- **Rehearsal plan:** [GitLab estate migration: wave 0 rehearsal](../../infrastructure/gitlab-rehearsal-wave0.md)
- **Category:** repository authority, access control and credential custody (sensitive
  class: deploy keys, a new GitHub App key, protected branches)

## Context

The owner asked to move the broader verJSON Git and runner environment onto the existing
self-hosted Netcup stack, mainly to cut hosting and CI cost. On 2026-11-05 the owner
approved a design and a rehearsal on 2–3 repositories. GitHub stays authoritative. There
is no Git authority switch and no production deployment.

[ADR 0161](../0161-migrate-ci-to-gitlab-with-measured-parity/README.md) and
[ADR 0162](../0162-unify-portable-ci-engine-and-forge-adapters/README.md) decide how CI
*workloads* move: by workload class, with measured parity and exact-head binding, through
the canonical `verjson-ci` engine. Both assume a GitLab copy of each repository exists.
Nothing yet decides how the *repository estate* gets there. That covers Git data, issues
and pull requests, packages and releases, access, secrets and integrations. This record
decides it. [#1264](https://github.com/verJSON/.github/issues/1264) tracks adoption of
`verjson-ci` on GitLab CE.

### Prerequisites already recorded

- **#2032.** The stack was down from 2026-10-19 to 2026-11-05. MinIO, Nexus and both
  runner managers were restored on 2026-11-05 under owner approval, and the restore drill
  passed. [ADR 0256](../0256-netcup-stack-backup-and-disaster-recovery/README.md)
  proposes the backup and recovery contract. Its scheduled job is not built yet.
- **verjson-ci#30.** The `verjson-ci` GitLab adapter, its Nexus targets and its release
  path exist as code. Live signed publication and hosted-runner acceptance are still
  open, and #30 blocks #1264.
- **verjson-git-runners#208.** The runner isolation evidence that #30 needs is not
  delivered. The 2026-10-06 diagnostics showed the default seccomp and AppArmor profiles
  deny the user namespaces a job sandbox needs, and that the host has no KVM for
  disposable VMs. #2032's operators therefore ran no CI job after the restore.

### Facts established read-only on 2026-11-05

**GitHub.** Only the properties this decision depends on are recorded here. The
inventory itself is regenerated at every wave boundary (§3).

- Most active repositories are private, and a few are public. A GitHub repository's
  visibility caps its GitLab project's (§3).
- The estate is small: under 1 GB of Git data in total as GitHub reports it, and no
  single repository is large. No repository uses Git LFS.
- Most repositories carry workflow files, but Actions is paused on most of them by owner
  directive (#1869,
  [ADR 0252](../0252-exclude-actions-paused-repos-from-required-checks/README.md)). It is
  also off on many repositories without that property, including two of the three
  wave 0 candidates.
- Some repositories use environments, repository Actions secrets, GitHub Releases or
  GitHub Packages. Wave 2 takes them (§3).
- Issues and pull requests are in active use across the estate, which is why they stay
  on GitHub (§4).
- The `verjson-stack` custom property is unset on some repositories, so the
  control-plane rule also matches repository names (§3).

**GitLab.** The instance's current inventory (version, projects, runners and settings) is
kept in the private operational record, `docs/operations/netcup-nuremberg.md` in
`verJSON/verjson-cli`, not here. Two facts matter to this design:

- The instance is CE (`enterprise: false`).
- It already holds earlier, non-mirrored copies of `verjson-ci` and `verjson-git-runners`
  that back live runners (§3).

**Cost.** No measured cost ledger exists. ADR 0161 and the
[GitLab CI migration plan](../../gitlab-ci-migration.md) define how to measure cost, but
record no figures. The only measured Actions spend on record is in #1869: one private
adopter used 55,609 minutes ($315.65 net) from 2026-10-01 to 2026-10-21, after the
included minutes ran out on 2026-10-02. No DigitalOcean figures are on record. Hosting
the Git data itself costs almost nothing: about 0.8 GB of repositories, a small fraction
of the host's free disk (#2032). The savings ADR 0161 targets come from
CI execution, and that stays behind ADR 0161's gates.

### What GitLab CE can and cannot do

The instance is CE, which is the Free tier. Tier claims below come from the current
GitLab documentation, checked on 2026-11-05. Two Free capabilities this record depends
on are recent: deploy keys in protected-branch and protected-tag rules moved from
Premium to Free in GitLab 18.10, and service accounts became generally available on
Free in GitLab 18.11. On an older CE release both are unavailable.

| Capability | Tier | Consequence here | Source |
| --- | --- | --- | --- |
| Pull mirroring (GitLab pulls from GitHub) | **Premium, Ultimate** | Not available | [Pull mirroring](https://docs.gitlab.com/user/project/repository/mirror/pull/) |
| CI/CD for external repositories (built on pull mirroring) | **Premium, Ultimate** | Not available | [CI/CD for external repositories](https://docs.gitlab.com/ci/ci_cd_for_external_repos/) |
| Bidirectional mirroring | **Premium, Ultimate** | Not available, and not wanted | [Bidirectional mirroring](https://docs.gitlab.com/user/project/repository/mirror/bidirectional/) |
| Push mirroring (GitLab pushes to another host) | Free | Points the wrong way while GitHub is authoritative | [Push mirroring](https://docs.gitlab.com/user/project/repository/mirror/push/) |
| Mirroring only branches that match a regex | **Premium, Ultimate** | Not available | [Repository mirroring](https://docs.gitlab.com/user/project/repository/mirror/) |
| GitHub importer | Free. An administrator must enable the import source. | A one-time copy, not a sync | [Import from GitHub](https://docs.gitlab.com/user/project/import/github/), [import sources](https://docs.gitlab.com/administration/settings/import_and_export_settings/) |
| Protected branches on one project, with deploy keys in *Allowed to push* | Free from GitLab 18.10; **Premium, Ultimate** before | Usable at 18.11 or later (§2) | [Protected branches](https://docs.gitlab.com/user/project/repository/branches/protected/), [Protected branches API](https://docs.gitlab.com/api/protected_branches/) |
| Protected branches for all projects in a group; groups or users in *Allowed to push*; code owner approval; controlling who can unprotect | **Premium, Ultimate** | Every rule is set per project | [Protected branches](https://docs.gitlab.com/user/project/repository/branches/protected/) |
| Overlapping branch rules | The most permissive matching rule applies | Use exact names; avoid overlapping wildcards | [Branch rules](https://docs.gitlab.com/user/project/repository/branches/protection_rules/) |
| Protected tags | Free | Usable | [Protected tags](https://docs.gitlab.com/user/project/protected_tags/) |
| Deploy keys in a protected tag's *Allowed to create* | Free from GitLab 18.10; **Premium, Ultimate** before | Usable at 18.11 or later (§2) | [Protected tags API](https://docs.gitlab.com/api/protected_tags/) |
| Users, groups or roles in a protected tag's *Allowed to create* | **Premium, Ultimate** | Only *No one* plus the deploy key | [Protected tags API](https://docs.gitlab.com/api/protected_tags/) |
| Code Owners | **Premium, Ultimate** | `CODEOWNERS` mirrors as a file and enforces nothing | [Code Owners](https://docs.gitlab.com/user/project/codeowners/) |
| Deploy keys | Free | Usable | [Deploy keys](https://docs.gitlab.com/user/project/deploy_keys/) |
| Service accounts, including group service accounts | Free from GitLab 18.10 behind a feature flag that is off by default; generally available on Free from 18.11. Free self-managed instances may hold at most 100. | The mirror identity, at 18.11 or later (§2) | [Service accounts](https://docs.gitlab.com/user/profile/service_accounts/), [Service accounts API](https://docs.gitlab.com/api/service_accounts/) |
| Group-level CI/CD variables | Free | Usable | [CI/CD variables](https://docs.gitlab.com/ci/variables/) |
| Environment scope on group-level variables | **Premium, Ultimate** | Environment-scoped values stay on projects | [CI/CD variables](https://docs.gitlab.com/ci/variables/) |
| CI/CD configuration file at a custom path or in another project | Free | Usable | [Customize pipeline configuration](https://docs.gitlab.com/ci/pipelines/settings/) |
| Project webhooks / group webhooks | Free / **Premium, Ultimate** | Not used during transition | [Webhooks](https://docs.gitlab.com/user/project/integrations/webhooks/) |
| CI/CD ID tokens (OIDC) | Free | Usable, per ADR 0162 | [ID token authentication](https://docs.gitlab.com/ci/secrets/id_token_authentication/) |
| Releases | Free | Not created during transition | [Releases](https://docs.gitlab.com/user/project/releases/) |
| Immediate removal of a project already marked for deletion | Free since GitLab 18.0 | Rehearsal teardown can finish immediately | [Projects API](https://docs.gitlab.com/api/projects/) |

## Decision

### 1. Authority during the transition

GitHub stays the only authority. That covers Git refs, pull requests, issues, required
checks, releases, packages, secrets and integrations. Each GitLab project holds a
**read-only, one-way mirror** of its GitHub repository. Nothing flows from GitLab back to
GitHub.

This record decides no Git authority switch. Moving authority for any repository needs
its own ADR and its own owner approval.

### 2. The mirror: a sync worker pushes from GitHub into GitLab

GitLab's own pull mirror would be the natural mechanism, but CE does not have it. So the
mirror pushes into GitLab from outside. GitLab never holds a GitHub credential.

| Option | Verdict |
| --- | --- |
| GitLab pull mirror | Not in CE (Premium). |
| GitLab CI/CD for external repositories | Not in CE (Premium), and it depends on the pull mirror. |
| GitLab push mirror | Free, but it pushes *from* GitLab. That would make GitLab the source. |
| A mirror workflow in each GitHub repository | Rejected. Actions is paused on most repositories by owner directive (ADR 0252), so the mirror would stop wherever Actions is off. Each repository would need its own GitLab write secret, and private repositories would pay for Actions minutes on every push. |
| A scheduled GitLab CI job that pulls | Rejected. It is CI on the Netcup runners before #208 (§5), and it puts a GitHub credential into a job environment. |
| GitHub importer | Rejected as a mirror. It copies once and does not sync (§4). |
| **A sync worker that fetches from GitHub and pushes to GitLab** | **Chosen.** |

**The worker.**

- It runs outside GitLab Runner and is not a CI job.
- It never executes repository content. It runs `git fetch` and `git push` only, with
  hooks disabled and no checkout. That is why it is not gated on #208.
- It is built by extending the canonical `verjson-ci` GitLab mirror kit (ADR 0162,
  ADR 0197, and `verjson-ci` ADRs
  [0042](https://github.com/verJSON/verjson-ci/blob/main/docs/decisions/0042-exact-gitlab-tag-mirror/README.md)
  and
  [0056](https://github.com/verJSON/verjson-ci/blob/main/docs/decisions/0056-pulumi-release-only-gitlab-mirror/README.md)).
  That kit already preserves exact objects, refuses rewrites, protects destination tags
  and provisions with Pulumi. Its current ref policy is release-only. This decision adds
  an *estate* ref policy beside it. No sync tool is hand-written in this repository.
- Wave 0 runs the same policy by hand from the rehearsal plan, attended by an operator.
  That proves the semantics the kit must encode before anyone builds it.
- From wave 1 on, the worker is a host workload deployed by the `verjson-cli` generator.
  Deployment implementation belongs there
  ([infrastructure README](../../infrastructure/README.md)).

**Ref policy.**

| Ref | Policy |
| --- | --- |
| `refs/heads/*` other than the default branch | Mirror creates, fast-forwards, force updates and deletions. |
| The default branch | Mirror creates and fast-forwards. A non-fast-forward update is **refused and alerted**. GitHub's own rules already block force-pushes there. |
| `refs/tags/*` | Mirror creation. If a tag moves or disappears on GitHub, the sync is **refused and alerted**. It is never forced. |
| `refs/pull/*` and every other namespace | Never mirrored. Pull requests stay on GitHub. |

A sync that hits a refusal stops for that repository and changes nothing there.

**Credentials.**

- **Reading GitHub.**
  - In wave 0, the attended operator's own read access is used, and nothing is stored.
  - From wave 1, a dedicated GitHub App with only `Contents: read` and `Metadata: read`
    is used. It is installed on **exactly the repositories in active waves**.
  - Installation tokens are minted per run and expire after one hour
    ([GitHub docs](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)).
  - Because the installation equals the mirrored set, compromising the host yields no
    repository content GitLab does not already hold.
  - **Custody (sensitive class):** the App private key lives only in the worker's secret
    store on the host. ADR 0256 captures it there only inside the encrypted Secrets
    export. It is never a GitHub organization or repository secret, and never a GitLab
    variable. Creating the App is an owner action.
- **Writing GitLab.**
  - Each project gets one SSH deploy key with write access, used only by the worker.
  - The key is the only identity allowed to push the protected default branch and to
    create protected tags (§4).
  - Keys are created by a dedicated, non-administrator **group service account** in the
    mirror group. GitLab gives a deploy key's creator access to the protected branch the
    key may push ([Deploy keys](https://docs.gitlab.com/user/project/deploy_keys/)), so
    that grant lands on the service account, not on a person or on `root`. A service
    account cannot sign in through the UI, has no password and authenticates only with a
    token.
  - Keys are created through a one-day personal access token of the service account. The
    token is minted, used and revoked in one step, and is held only in process memory,
    never in a file (rehearsal plan §5).
  - Private halves stay with the worker. Rotate every 90 days, on any custody change, and
    at teardown.

**Requirement: GitLab 18.11 or later.** Deploy keys in protected-branch and
protected-tag rules are Free only from GitLab 18.10, and service accounts are generally
available on Free only from 18.11. On an older release the protections above cannot be
expressed and the mirror identity does not exist. The instance must therefore run
GitLab 18.11 or later. Every wave's preflight reads the version and refuses anything
older or unparseable (§4).

**Freshness.**

- The worker reconciles on a schedule. The target is every 5 minutes.
- It alerts when a repository's GitLab default branch trails GitHub by more than
  30 minutes outside ADR 0256's nightly quiesce window.
- A GitHub webhook trigger could cut latency later. That is an optimization, not part of
  this decision.
- ADR 0161's status bridge already fails closed on a stale mirror.

### 3. Migration unit and waves

**The unit is one repository.** Each repository becomes one GitLab project with the same
name. Production waves go into the existing `Verjson` group, so the project paths the
`verjson-ci` identity and component contracts already use stay stable. Wave 0 uses a
dedicated, disposable rehearsal group.

Every mirror project starts with these settings:

- visibility **private**, and never wider than the GitHub repository's;
- issues, merge requests, wiki, snippets, package registry, container registry, LFS and
  CI/CD **disabled**;
- shared runners off;
- no webhooks or integrations.

Each feature is turned on only by the gate that governs it.

**Waves.** Assignment follows rules. It is not a frozen list: the inventory is
regenerated at every wave boundary, as the migration plan already requires.

*Control-plane* means `.github`, `verjson-ci`, `verjson-git-runners`, `verjson-agents`,
`renovate-config`, `server-setup`, `alz-bootstrap`, the `verjson-cli*` family, any name
ending in `-infra` or `-iac` or containing `k8s`, and any repository whose
`verjson-stack` is `actions`, `helm` or `pulumi`.

| Wave | Rule | Gate to start |
| --- | --- | --- |
| 0, rehearsal | The three repositories below, in a disposable group, torn down afterwards | ARB review and owner go-ahead |
| 1 | Not control-plane. No packages, releases, environments or repository Actions secrets. Includes the wave 0 three. | Wave 0 receipt; worker and App deployed |
| 2 | Not control-plane, and has at least one of: packages, releases, environments, repository Actions secrets (ADR 0161's trusted class) | Wave 1 `mirrored`; Nexus targets accepted under #1264 and verjson-ci#30 |
| 3 | Control-plane | Wave 2 `mirrored` |
| Owner decides | Empty repositories with no Git data | Owner decision |
| Excluded | Archived repositories and forks | — |

Wave 3 also takes in the two existing GitLab projects for `verjson-ci` and
`verjson-git-runners`. They become verified mirrors without losing their runner
assignments. Their current divergent heads are replaced by the verified mirror, which is
safe because GitHub holds every commit. Until then, no wave touches them.

**Wave 0 candidates.** These were chosen for low risk: small, no LFS, no CI secrets, and
few dependents.

| Repository | Visibility | Why |
| --- | --- | --- |
| `agile-ai-manifesto` | Public | 10 KB in 10 files. One branch and no tags. No workflows (Actions off), packages, releases, repository secrets, environments, webhooks, open issues or pull requests. Nothing in this repository references it. It proves the minimal path and the visibility rule: a public GitHub repository becomes a *private* GitLab project. Its website deploys from GitHub through Cloudflare. The rehearsal never changes the repository, and it proves no integration follows the mirror. |
| `demo-repository` | Private | 18 KB in 13 files. Two branches. No repository secrets, environments, webhooks, packages or LFS. Actions off. It is the **CI-leg candidate**: it has one portable, credentialless workload (`proof-html`) next to five merge-gate workflows that stay on GitHub under ADR 0161's privileged class, so the rehearsal exercises workload classification too. The only references to it here are historical captures (ADR 0091 and a 2026-09-27 merge-gate fixture) that never read its live state. |
| `required-reviewers-rehearsal` | Private | 5 KB. Four branches. No workflows. A `CODEOWNERS` file. It is a throwaway created for #1816 and marked safe to delete. It proves multi-branch ref equality and that `CODEOWNERS` is inert on CE. The incremental-sync probes (branch and tag create, move and delete) write only here; they need its default branch and a second branch at a different commit. If it has been deleted by rehearsal time, the rehearsal stops until the owner names a replacement and this record and the plan are amended. |

Not chosen:

- `verjson-tsconfig` is small, but it is a published npm package that Node repositories
  depend on.
- The `verjson-*-template` repositories validate with `secrets.SUBMODULES_TOKEN`.
- `catalog-helm` runs a kind cluster on a self-hosted Docker lane.
- The two existing GitLab projects back live runners.

### 4. What migrates, and how each part is verified

| Part | Decision | Verification |
| --- | --- | --- |
| **Git history, refs and tags** | All of `refs/heads/*` and `refs/tags/*`, under §2's ref policy. Annotated and signed objects keep their exact bytes. | 1. The `ls-remote` output for heads and tags, including peeled tags, is identical on both sides.<br>2. In fresh bare fetches from each side, the count and the SHA-256 of the sorted `git rev-list --objects` output are identical.<br>3. `git fsck --strict` gives the same result on both sides.<br>4. GitLab has no head or tag that GitHub lacks.<br>The worker repeats check 1 every cycle and checks 2 to 4 daily and at every wave gate. If a GitHub repository fails `fsck`, it is held for a decision, because GitLab may reject the push. |
| **Issues and pull requests** | **They stay on GitHub.** Issues and merge requests are disabled on mirror projects, so discussion cannot split. The GitHub importer is not used: it copies once and then diverges, it needs an administrator to enable the import source and a broad GitHub token, and it creates new projects instead of filling mirrors. Open issues and pull requests stay where their links, automation and history live. Revisit this only in an authority-switch ADR. | Read back `issues_access_level` and `merge_requests_access_level` as `disabled`. |
| **CI configuration** | The `verjson-ci` GitLab adapter's caller is **generated** by the canonical `verjson-ci` generator at one immutable release, never hand-written. That is the GitLab equivalent of the canonical contract, per verjson-ci#30. The caller is committed to the **GitHub** repository through a reviewed pull request, as generated GitHub callers are today, and the mirror carries it. It includes the `verjson-ci` component pinned by version and the image by digest. No repository gets a GitLab caller before that generator exists. | The generator's contract test passes, and regeneration is byte-identical. ADR 0161's parity gates apply. The status bridge checks the digest of the configuration that actually ran. |
| **Packages and releases** | GitHub Packages and GitHub Releases stay the channels. The self-hosted package target is **Nexus**, under ADR 0160, #1264 and verjson-ci#30, not GitLab's package registry. GitLab's package and container registries stay disabled. Tags mirror. GitLab Release objects are not created during the transition. Releases stay `workflow_dispatch` on GitHub under the changelog contract. | Tag sets are equal (row 1). Registry features read back as disabled. |
| **Access and RBAC** | **Group Owner:** at most two named organization administrators, as break-glass. **Other organization members:** Reporter, read-only, on production groups. **Outside collaborators:** not provisioned unless the owner approves each person. **Mirror service account:** Maintainer on mirror projects only. No human holds Developer or Maintainer during the transition, so only the mirror writes. **Default branch:** exact-name rule, *Allowed to push* = No one plus the project's deploy key, *Allowed to merge* = No one, force-push off. **Tags:** `*` protected, *Allowed to create* = No one plus the deploy key. The deploy-key entries are Free from GitLab 18.10 (§2's version requirement). Group-wide rules and user, group or role allowlists are Premium, so Pulumi sets these per project. | Read back members and access levels, protected branches and tags, and visibility. Negative tests: a non-fast-forward push of the default branch with the deploy key is rejected, as is a tag deletion with the deploy key and a push with a read-only key. |
| **Instance preconditions**, checked before every wave | The instance runs GitLab 18.11 or later (§2). No GitLab project is more visible than its GitHub repository. Self-registration is closed or needs administrator approval. Human Owners and administrators use two-factor authentication. Legacy runner registration tokens are off. Any project with no GitHub counterpart and no owner-approved purpose is inventoried. | The rehearsal plan's preflight reads these with GET requests and **stops** on any failure. |
| **Secrets** | **Never copied, exported or read back from GitHub.** When a migrated workload's credential design is approved (ADR 0161's trusted class), its secrets are re-provisioned by role through the existing custody model ([ADR 0088](../0088-auditable-organization-secret-scope/README.md), [ADR 0166](../0166-environment-only-app-private-keys/README.md), [ADR 0180](../0180-no-approved-organization-custody-for-environment-only-app-keys/README.md), and `verjson-ci`'s variable trust boundary). They become protected, masked GitLab variables. Group variables are allowed where a role really spans the group. Environment-scoped values stay on projects, because group environment scope is Premium. Wave 0 provisions **none**. | Variable names only: wave 0 has none; later waves match the approved role map exactly. |
| **Webhooks and integrations** | They stay on GitHub: installed GitHub Apps, repository and organization webhooks, Renovate, Cloudflare and `notify-umbrella`. Mirror projects get no webhooks, integrations or bots during the transition. Each wave inventories repository webhooks and App access. Reading organization webhooks needs the `admin:org_hook` scope, so the owner does that read. | Each project's webhook list is empty and no integration is active. |

### 5. Runner isolation comes first (verjson-git-runners#208)

**No pipeline runs on the Netcup runners for any migrated project** until #208 has
posted isolation evidence and the owner has accepted it. The evidence must show:

- credential-free checkout and signed dependency acquisition before any repository code
  runs;
- a job sandbox that creates user, PID, network, IPC and UTS namespaces, and hides runner
  scripts, ancestor environments, acquisition credentials, helper and upload tokens, and
  host mounts;
- unprivileged jobs with no service-account token and no Docker socket;
- image admission and a variable-override policy enforced before the script runs;
- receipts for cleanup, failure, expiry and rollback.

Until then:

- CI/CD is disabled on every mirror project;
- no runner is assigned to a mirror group;
- shared runners are off.

**Wave 0 exception (owner decision, 2026-11-05).** #208 is split for the rehearsal. The
wave 0 CI leg (rehearsal plan §9) may run plain, credential-free test jobs, with no
image or candidate builds, once a new dedicated group runner has posted plain-job
admission evidence and the owner has accepted it. That runner is locked to the rehearsal
group, unprivileged, protected-refs-only and on narrow egress. The full #208 gate above
still governs every image, candidate or OCI build job, and the `ci-shadow` stage (§7) for
every later wave. The wave 0 CI leg is also blocked on the generator in §4. Everything
else in wave 0 can run without either.

### 6. Rollback for each wave

GitLab holds no authoritative data, so no rollback can lose any. To roll back a wave:

1. Remove the wave's repositories from the App installation and the worker configuration.
2. Revoke their deploy keys.
3. Disable CI on their projects.
4. Archive the projects, or delete them if the owner decides so. A deletion first
   checks that the group holds only what the wave created. Someone with Owner or
   administrator rights could still move content in between that check and the delete,
   so the post-delete readback checks again. Group deletion is delayed, so a group found
   non-empty is restored and investigated before the instance's retention ends.
5. If a CI workload had moved, restore its GitHub route under ADR 0161's rollback.
6. Revert any generated-caller pull request on GitHub normally.

Any of these triggers rollback:

- a ref or object mismatch that the next sync does not clear;
- a lag alert lasting more than 6 cycles;
- an unexpected write to GitLab;
- visibility drift;
- a breach of deploy-key or App-key custody;
- a failed backup drill for the wave's projects.

### 7. Promotion between stages needs owner approval for each wave

"Cutover" in this record means promoting a wave between the stages below. It never means
switching Git authority. Each promotion is an owner approval recorded on #1264, with a
receipt that holds no secrets, endpoints or host details.

| Stage | Entry criteria |
| --- | --- |
| `mirrored` | Seven consecutive days of passing ref checks, and passing object checks at the start and end. Lag within threshold. No unexpected writes. The projects are in an ADR 0256 backup run, and that run passed a drill. |
| `ci-shadow` | #208 evidence accepted, the generator released, and ADR 0161 phase 2 entered |
| `canary-required` | ADR 0161 phase 3 gates |

A new wave starts only after the previous wave has reached `mirrored`.

### 8. Interaction with backup and recovery (ADR 0256)

**Scope grows automatically.** Every mirror project falls inside ADR 0256 §1 with no
change to the capture: `gitlab-backup` and the `gitlab-data` tar carry the repositories
and their settings. The worker's deploy keys and the App key live in cluster Secrets,
which only the encrypted Secrets export carries, under ADR 0256 §7's key custody.

**Size.** The whole estate adds about 0.8 GB per copy of the repositories. GitHub's disk
estimate is the basis. Each run holds that data twice, once in the backup and once in
the tar. At ADR 0256's retention of 35 daily and about 13 monthly runs, that is roughly
80 GB in R2, about $1.20 a month at $0.015/GB-month. This is an estimate, to be replaced
by measured archive sizes after wave 1.

**Rehearsal residue.** Teardown does not purge backups. Backups taken while wave 0 exists
keep those projects for up to 400 days under `monthly/`. That is acceptable because the
wave 0 repositories are low-sensitivity: one is public, one is a demo, and one is a
throwaway. It is one more reason they were chosen.

**Restore.** A GitLab restore brings the mirrors back as of the run. The worker then
fast-forwards them from GitHub. Two additions to ADR 0256 §6's drill checks:

- the number of mirror projects matches the backup manifest;
- after a re-sync, every project passes the ref check.

**Quiesce window.** Pushes fail during the nightly window and are retried on the next
cycle. The lag alert excludes the window.

**The 2026-11-05 restore did not exercise this.** No backup job exists yet. Promotion to
`mirrored` (§7) therefore waits for ADR 0256's first scheduled run and monthly drill.

### 9. Decided and pending

**Decided** by this record:

- GitHub stays authoritative; GitLab holds a one-way mirror;
- the sync worker, its ref policy and its credential model;
- the repository as the unit, the wave rules and the wave 0 candidates;
- what migrates and how each part is verified;
- the #208 gate;
- rollback and per-wave promotion;
- the BC/DR interaction.

**Pending owner action.** None of this has been done.

- [ ] Keep the instance on GitLab 18.11 or later (§2). The preflight of every wave,
      the rehearsal's included, refuses anything older.
- [ ] Approve running wave 0 under the rehearsal plan after ARB review. That covers the
      rehearsal group, the mirror service account, the deploy keys and the probe writes
      in `required-reviewers-rehearsal`.
- [ ] Decide whether to enable Actions on `demo-repository` for the CI leg's comparison
      window. Actions is off by owner directive (#1869).
- [ ] Accept or reject #208's isolation evidence once it is posted.
- [ ] Create the read-only GitHub App and approve its key custody on the host. This is
      needed before wave 1.
- [ ] Decide the production group's visibility. The recommendation is private.
- [ ] Decide what happens to the empty repositories, and whether any outside
      collaborator gets GitLab access.
- [ ] Supply DigitalOcean cost figures for ADR 0161's ledger.

**Pending engineering:**

- the estate ref policy in the `verjson-ci` mirror kit, its Pulumi resources and its
  mocked contract tests;
- the GitLab caller generator in `verjson-ci` (verjson-ci#30);
- the worker deployment in the `verjson-cli` generator;
- the drill additions in ADR 0256's runbook.

## Consequences

- **Positive:**
  - The estate reaches GitLab without anyone giving up GitHub. Every stage can be undone
    without data loss.
  - GitLab never holds a GitHub credential. The read credential reaches no repository
    GitLab does not already mirror.
  - Verification compares refs and object sets, not repository names or green
    pipelines.
  - Each CE limit is designed around, not discovered during a wave.
- **Negative:**
  - The sync worker is a new host workload with two credentials to look after.
  - The design depends on Free features from GitLab 18.10 and 18.11, so the instance must
    stay on 18.11 or later; restoring an older release stops every wave's preflight.
  - Mirror latency is minutes, not seconds.
  - GitLab users cannot open issues or merge requests during the transition.
  - Every protection rule is per project, because group rules are Premium.
  - Wave 0's CI leg cannot run until #208 and the generator land.
- **Neutral:**
  - ADR 0161's CI migration is unchanged; this record supplies the repositories it
    assumed.
  - Moving to GitLab Premium later would replace the worker with native pull mirroring.
    The verification and wave model would not change.

## Considered options

- **GitLab Premium for native pull mirroring.** Not chosen. It is a licensing and spend
  decision for the owner. The worker meets the need on CE, and the verification contract
  is the same either way.
- **Moving issues and pull requests with the GitHub importer now.** Rejected. While
  GitHub is authoritative, a one-time copy only creates a second, stale tracker (§4).
- **A mirror workflow in each repository.** Rejected (§2).
- **Migrating all repositories at once.** Rejected. It gives no rollback granularity, and
  it would put control-plane repositories through an unproven path first.
- **Running the worker on a GitHub-hosted runner.** Rejected for production. Private
  repositories pay for minutes, and a public host repository would publish logs about
  private repositories. Running it on the host keeps the GitHub credential scoped to what
  GitLab already holds.
