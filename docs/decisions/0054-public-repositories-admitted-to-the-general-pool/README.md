# 0054 — Admit public repositories to the self-hosted general pool

- **Date:** 2026-08-05
- **Issues:** [Verjson/.github#401](https://github.com/Verjson/.github/issues/401),
  [#395](https://github.com/Verjson/.github/issues/395) (the same admission defect
  reached through `VERJSON_RUNNER_UNTRUSTED`),
  [#391](https://github.com/Verjson/.github/issues/391) (the `general` label
  missing from the fleet on 2026-08-04)
- **Restores:** [ADR 0041](../0041-shared-admission-hosted-and-self-hosted/README.md) —
  the live organization had regressed away from it, so nothing new is decided about
  admission here
- **Refines:** ADR 0033 (visibility routing), ADR 0040 (lanes and admission axes),
  ADR 0031 (admission is an org-settings boundary, not workflow YAML)

## Context

On 2026-08-05 every `[self-hosted, general]` job in this repository queued
indefinitely while five of the ten `general` runners sat idle. The pool had moved
into org runner group `DigitalOcean` with `visibility: selected` and
`allows_public_repositories: false`. `Verjson/.github` is public, so it was not
an eligible target for any runner in that group — and GitHub queues such a job
rather than failing it, so there is no check-run diagnostic and no error to read.

Nothing about that admission posture was decided here or anywhere else. ADR 0041
already decided, deliberately and with the owner's sign-off, that the self-hosted
pool serves public and private repositories alike; it quotes the group as
`visibility: all, allows_public_repositories: true`. The move to a new group
carried restrictive defaults and silently reverted it. This ADR restores a
recorded decision rather than widening anything.

The merge gate wedged behind it: PR #400 held `gate` and `dispatch-merge` green
with `privileged_merge` unable to start. Nothing could merge in this repository.

Two long-standing structures failed at once.

**Admission is an org-settings fact, and the routing assumed a different one.**
`ai-privileged-merge.yml` routes every Verjson-owned target to
`VERJSON_RUNNER_ISOLATED`, which currently resolves to `["self-hosted","general"]`
with no visibility branch. `node-ci.yml` routes public repositories to
`VERJSON_RUNNER_UNTRUSTED`, the same labels. Both are correct designs *if* public
repositories can reach the pool. Group settings decided they could not.

**The monitor for exactly this condition could not run.**
`runner-admission-reconcile` exists to detect "a repository cannot reach the lane
its visibility routes it to", and it models the case correctly — the suite has a
passing case named *public repository denied by group is reported as drift*. It
ran on `[self-hosted, general]`. When public repositories lost the pool, so did
the monitor. Run 30996731678 queued at 10:16 and did not start until 14:22:28 —
seconds after the group was opened — then failed 11s later on the second defect
below. One run demonstrates both: four hours unable to report an admission outage
because it was inside it, followed immediately by `Could not read the org; not
reporting clean.` A watcher that depends on the resource it watches is silent in
precisely the outage it exists to report.

It was also blind for a second reason. The `GCP` → `DigitalOcean` move renamed
the group, and while lane *labels* follow org variables — `node-ci.yml` says so
in its own `runs-on` comment, "a variable flip, not a PR here" — the group *name*
was still in code:

```
UNDETERMINED: runner group 'GCP' (selected by the general lane) does not exist in
Verjson; present groups: GitHub, manish, DigitalOcean, verjson-runner-maintenance
```

That is the #266 failure recurring, resolved by name rather than by id.

## Decision

**Any public Verjson repository can use the self-hosted general pool.** Runner
group `DigitalOcean` is set to `visibility: all` and
`allows_public_repositories: true`.

`visibility: all` rather than adding `.github` to a selected list, because the
requirement is about the class and not this repository: a selected list makes
every newly created public repository wedge on its first job with no diagnostic,
which is the #182/#192 failure mode that ADR 0031's allowlist was retired for.
Admission remains an org-settings boundary and is still not expressed in workflow
YAML.

Two repository-side changes keep the decision observable:

1. The reconciler runs on the fast lane, never on the pool it monitors.
2. `GENERAL_GROUP_NAME` is read from `vars.VERJSON_RUNNER_GENERAL_GROUP` with a
   `DigitalOcean` fallback, so the next pool migration is a variable flip like
   the labels rather than a pull request.

Both are pinned by tests. `runner-admission-reconcile.yml` is deliberately
excluded from the routing contract that keeps repository-local jobs on the
general lane, with a dedicated assertion in its place — otherwise the next tidy-up
puts the monitor back inside the thing it watches.

**Dead labels are undeclared rather than documented.** The same fleet move took
`gce`, `GCP` and `gate` off the runners while workflows still selected them —
steps 5 and 6 of the migration sequence in `docs/runner-routing.md` ran out of
order, which that document warns produces exactly #182's silent failure.
`Verjson/verjson-identity-lifecycle`'s `generated-docs` job reproduced it on
`[self-hosted, GCP]`. `.github/actionlint.yaml` is the organization-wide policy
every consumer sparse-checks, and it declared all three, so lint stayed green
while the routes were unplaceable. They are now undeclared: naming one fails lint
with a file and a line. Every remaining literal selector in this repository is
routed through `vars.VERJSON_RUNNER_*` with a live-label fallback, so the next
relabel is a variable flip rather than a pull request per workflow — ADR 0041's
rule, applied to the repository-local jobs that had been exempt from it.

## Consequences

- Public repositories reach the general pool. Verified rather than reasoned: the
  wedged `privileged_merge` job started on `gha-general-2` within ~40s of the
  group change, and #400 merged.
- The admission monitor now reports during an admission outage instead of
  disappearing into it, and it survives a group rename without a code change.
- `ORG_ADMIN_TOKEN` moves onto a GitHub-hosted runner for this job. That is
  scheduled default-branch code with no fork input, on an ephemeral VM — a
  narrower exposure than a persistent self-hosted host, not a wider one.
- **A consumer still naming `gce`, `GCP` or `gate` now fails actionlint.** That is
  the intent — those repositories are already broken, silently — but the blast
  radius is organization-wide and lands as a red lint job rather than a hung one.
  The failure names the file and line to fix.
- The monitor's independence rests on `VERJSON_RUNNER_FASTLANE` not pointing at
  the general pool. ADR 0047 offers exactly that as its rollback ("set it to
  `["self-hosted","general"]`"), which would put the monitor back inside what it
  watches while both routing assertions still passed. That rollback is not
  available while `runner-admission-reconcile` and `fleet-watchdog` ride the fast
  lane; repoint them first.

### Residual risk, stated plainly

`allows_public_repositories: true` means a workflow in a public Verjson
repository can place jobs on the self-hosted fleet, including one triggered by a
pull request from a fork. This is the risk class GitHub warns about for
self-hosted runners on public repositories, and it is the same surface #350 is
open on.

It is accepted here because the alternative — routing public repositories to
hosted runners — is ruled out by the standing requirement that public Verjson
repositories be able to use the general pool.

What does *not* mitigate it today: `VERJSON_RUNNER_UNTRUSTED` and
`VERJSON_RUNNER_DEFAULT` both resolve to `["self-hosted","general"]`, so the
"untrusted" lane that public and fork work is routed to is the same pool that
runs trusted work. The lane separation ADR 0040 describes exists in the variables
and not in the fleet — there is no second pool to route to, since the `gate`
label came off the runners as well. That is
[#204](https://github.com/Verjson/.github/issues/204), open since the DigitalOcean
migration. This ADR does not claim it is solved, and admitting public
repositories to the one remaining pool makes it more load-bearing, not less.

## Rollback

Set group `DigitalOcean` back to `visibility: selected` /
`allows_public_repositories: false`. Every public Verjson repository then queues
indefinitely again on any `[self-hosted, general]` job, so the rollback is only
safe together with routing public repositories to the hosted fast lane.

## 2026-10-15 amendment — public pull-request code leaves the pool

[ADR 0235](../0235-public-pull-request-code-uses-ephemeral-runners/README.md)
narrows the "Residual risk" accepted above. Public repositories keep their
admission to the general pool for trusted events, but no default path may place
pull-request code there. `scripts/ci-gate/public-pr-runner-isolation.py` checks
that for this repository, and
[#1831](https://github.com/verJSON/.github/issues/1831) tracks the remaining
workflows and the owner's runner-group admission decision.
