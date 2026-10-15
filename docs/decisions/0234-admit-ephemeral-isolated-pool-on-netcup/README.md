# 0234 — Admit an ephemeral isolated runner pool on netcup for untrusted CI

- **Date:** 2026-10-15
- **Status:** Accepted
- **Issue:** [verJSON/.github#1818](https://github.com/verJSON/.github/issues/1818)
- **Amends:** [ADR 0147](../0147-route-untrusted-ci-to-ephemeral-hosted-runners/README.md) — per-repository opt-in only; the organization-level untrusted selectors are unchanged

## Context

ADR 0147 routes the untrusted lane to GitHub-hosted `ubuntu-24.04` because hosted
runners are ephemeral per job, while the persistent DigitalOcean pool shares host
state between jobs. Several repositories also hard-code hosted labels for jobs that
hold no credential.

On 2026-10-15 the organization's Actions budget ($250 a month, stop usage enabled) ran
out. Every GitHub-hosted job in a private repository now fails with zero steps and the
annotation "The job was not started because recent account payments have failed or
your spending limit needs to be increased". This includes required checks and
privileged merges. The October rate is about $500 a month.

A per-job classification of this month's hosted minutes found four groups:

- **About 18% needs neither Docker nor root.** Examples are sitenav `crawler`,
  `voice` and `voice-worker`, verjson-editor `ci-node-floor / build-test`, the
  secretless dependency-acquisition jobs, `changelog-contract`, `title-policy` and
  `event-policy`.
- **About 13% needs root only.** Examples are `playwright install --with-deps` in
  sitenav `browser` and verjson-editor `ci / build-test`.
- **About 52% needs Docker.** Examples are sitenav `quality` (a `services:`
  database), marketing-studio `ci / build-test` and its image builds, and the
  verjson-ai, verjson-ci and toquorum `build-test` jobs.
- **About 16% must stay hosted under existing decisions.** These are the
  privileged-merge and app-key gates (ADR 0118), node-release, `deferred-ci`, the
  runner-acquisition watchdog, deploy and promotion jobs that hold environment
  secrets, and macOS and Windows legs.

The netcup k3s cluster (`docs/infrastructure/netcup-nuremberg.md`) runs GitLab CE,
Nexus CE and MinIO. Its node has 16 threads and about 50 GiB of free memory, and the
cluster already supports user namespaces (k3s v1.33.3, containerd 2.0.5).

## Decision

1. **Pool.** The netcup cluster runs an Actions Runner Controller scale set named
   `verjson-isolated`:
   - Controller chart `gha-runner-scale-set-controller` 0.15.0, release `arc` in
     `arc-systems`.
   - Scale set chart 0.15.0 in namespace `verjson-gha-runners`, `minRunners: 0`,
     `maxRunners: 4`.
   - Runner image `ghcr.io/actions/actions-runner` 2.338.0, pinned by digest.
   - Runner group `verjson-isolated`. It is not the Default group, it admits no
     public repositories, and it is visible only to repositories that have opted in.
   - Jobs select the pool with `runs-on: verjson-isolated`, or with the selector
     `["verjson-isolated"]`.
2. **Isolation contract.** These properties are this pool's equivalent of a
   hosted runner being ephemeral:
   - Each job gets its own pod, and ARC deletes the pod when the job ends.
   - The pod runs with `hostUsers: false`, so container uid 0 maps to an
     unprivileged host uid range. `sudo` works inside the pod; it is never root on
     the node.
   - There is no Docker daemon, no container socket and no privileged container.
     The service-account token is not mounted.
   - The namespace enforces Pod Security `baseline`, and a ResourceQuota caps the
     pool.
   - The NetworkPolicy allows DNS to kube-dns, plus TCP 80 and 443 to public
     addresses only. It excludes RFC 1918, loopback, link-local, CGNAT, multicast
     and the node's own public IP, so a job cannot reach GitLab, Nexus, MinIO, the
     k3s API, host services or instance metadata.
   - Changes to any of these properties are made through a new ADR.
3. **Credential.** ARC authenticates with the organization App
   `org-gh-runner-registration`, which is limited to self-hosted runner write. It
   uses a key dedicated to this cluster, generated on 2026-10-15 and stored only as
   the namespace secret `github-app-runner-registration`. The pool holds no other
   secret.
4. **Routing is opt-in per repository.** The six untrusted variables stay at
   `["ubuntu-24.04"]` at organization level, so `runner-admission-reconcile` and
   ADR 0147's exact-value check are unchanged.
   - A repository moves to the pool only when every job its untrusted selector
     routes fits clause 2: no Docker, no `services:`, no `container:`, and no
     `RUNNER_ENVIRONMENT == github-hosted` assertion.
   - To opt in, the repository sets a repository-level `CI_LANE_UNTRUSTED`, plus
     each alias its pinned workflows read, to `["verjson-isolated"]`. A hard-coded
     hosted job moves through a pull request in that repository.
   - The repository is then added to the runner group.
   - Deleting the repository variables restores ADR 0147 routing.
5. **Not covered.** The following stay where they are:
   - Jobs that need Docker or `services:`. Running privileged Docker for untrusted
     code on the cluster that hosts GitLab, Nexus and MinIO needs a separate
     decision, with the cluster owner's approval.
   - The privileged, app-key, release and `deferred-ci` lanes, and the watchdog.

## Evidence

- Canary runs in `demo-repository` passed: run 38756257799 with the base contract
  and run 38763931902 with `sudo`.
  - Inside the jobs, `https://git.159-195-78-163.nip.io`, `https://10.43.0.1:443`
    and `http://169.254.169.254` were blocked, while GitHub and npm were reachable.
  - `sudo apt-get install make` succeeded.
  - `/proc/self/uid_map` mapped container uid 0 to host uid 4222877696.
- As a control, a pod in a namespace without the policy reached the GitLab host and
  the k3s API.

## Consequences

- When a repository opts in, its untrusted jobs stop drawing on the hosted budget.
  If it later opts out, its jobs bill the budget again.
- A repository that opts in depends on netcup's availability for the checks it
  routes there. This is the failure mode sitenav cited for staying hosted, so
  sitenav's maintainers decide whether sitenav opts in. Rolling back is one variable
  change or a revert.
- The gate-rearm `runner-acquisition-watchdog` goes dormant for a repository that
  opts in. This is by design: it runs only while the untrusted lane is exactly
  `["ubuntu-24.04"]`.
- Capacity is four concurrent jobs, with 2 CPU and 4 GiB each. Peak hosted
  concurrency this month averaged about seven, so jobs on the pool queue at peak.
- Untrusted code shares a kernel with GitLab, Nexus and MinIO. User namespaces, the
  missing container socket and the network fence reduce that risk but do not remove
  it. A kernel escape is the remaining threat.
