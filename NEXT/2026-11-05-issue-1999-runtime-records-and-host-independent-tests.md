---
date: 2026-11-05
issue: 1999
impact: patch
title: Every actions-ci group writes runtime records, and two tests no longer depend on the host
---

The `docs` and `locale-collation` actions-ci groups now write and upload runtime records,
like the matrix groups do. So every pull-request run uploads at least the `docs` records,
including runs for drafts. The release-caller allowlist sweep runs as its own row with a
90-second budget, so its cost is recorded on its own. The watchdog and release-node
credential-boundary tests now give the same verdict whatever the host's time zone or npm
version.

- **Runtime records.** `docs-contracts` and the hosted `locale-collation` step set
  `ACTIONS_CI_RUNTIME_RECORDS` and upload `actions-ci-runtime-records-<run>-<attempt>-<group>`
  with `if: always()`. `scripts/actions-ci-groups.test.sh` now checks this for every step
  that runs the group runner, not only the matrix job. The pull-request runs that
  uploaded no records were all runs where the heavy matrix was skipped; none skipped an
  upload after its rows had run.
- **Per-case cost.** The record schema stays at version 1, because the
  verjson-observability collector rejects unknown fields. A case known to dominate its row
  runs as its own row instead. The release-caller `allowlist sweep` took 12.3 of the
  contract part's 13.7 seconds locally. It now runs as
  `RELEASE_CALLER_ISOLATION_PART=allowlist-sweep`, and the part partition test pins it.
- **Budget.** The sweep row declares `@budget=90`. That is twice 32 seconds, the slowest
  of 11 hosted runs of the contract part that used to carry it, rounded up to a multiple of
  30 seconds. The rest of the contract part takes about 1.3 seconds and keeps the 60-second
  default.
- **Watchdog time zone.** `arm-runner-acquisition-watchdog.test.py` runs the job's steps
  in UTC, the hosted runner's zone, unless the job sets its own `TZ`. Under
  `TZ=America/New_York`, jq 1.6's `fromdateiso8601` had shifted every job's age by an
  hour. Four of 24 behaviors failed and one hung until its 120-second timeout. A new
  behavior runs the stall scenario from a New York caller.
- **npm major.** npm 12 refuses `npm cache add` for a remote tarball URL (`EALLOWREMOTE`),
  so credentialed acquisition does not work there. The acquisition guard already refuses
  npm outside 10 and 11. On such an npm, `release-node-credential-boundary.test.py` now
  checks that the guard refused before any credentialed request and that nothing was
  installed. It then skips and names the npm version. In GitHub Actions the test fails
  instead of skipping, so a runner image that moves off npm 10 or 11 cannot silently drop
  this boundary from CI.

ADR 0225 has an amendment for the records and per-case decisions. Part of #1999.
