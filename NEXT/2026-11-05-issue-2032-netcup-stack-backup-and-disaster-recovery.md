---
date: 2026-11-05
issue: 2032
impact: patch
title: Propose backup and disaster recovery for the Netcup Git/CI stack
---

Proposes ADR 0256, the backup and disaster recovery plan for the self-hosted Netcup
GitLab, Nexus and MinIO stack, and adds a runbook for backing up and restoring it. The
plan follows the 2026-10-19 outage in #2032, when the stack had no backup.

**What gets backed up.** The ADR covers:

- GitLab, through `gitlab-backup STRATEGY=copy`. `gitlab-secrets.json` and `gitlab.rb`
  are stored as a separate artifact.
- Every MinIO bucket, plus `.minio.sys`.
- Nexus's database and blobs, captured together.
- Encrypted cluster manifests and Secrets.
- A digest-pinned OCI archive of each runtime image.

`gitlab-logs` is excluded.

**How it is captured and encrypted.**

- Captures are taken with the services stopped.
- Each artifact is an independent tar archive, encrypted with `age` to at least two
  recipients and covered by sha256 manifests.
- The host can encrypt but cannot decrypt.
- restic and kopia were rejected because they need a symmetric repository key on the
  host that writes the backups.

**Where the copies go.** The plan uses a 3-2-1 layout: a root-owned copy on the host,
Cloudflare R2, and an independent third copy.

**Recovery targets.**

| Scenario | Target |
| --- | --- |
| Registry restore, host intact | 4 hours |
| GitLab restore, host intact | 8 hours |
| Full host loss | 1 business day (not yet measured) |

The drill restored the registry tier in about 6 minutes of machine time on local disk.
That figure excludes download time and human time.

**R2 setup.**

- The backup target is set by configuration, with a provider profile.
- R2 is the current target, in the existing Cloudflare account, with a 30-day bucket
  lock.
- Each run tries to delete a lock-protected probe object up to 28 days old and checks
  that R2 refuses with exactly 409 `ObjectLockedByBucketPolicy`. This catches a lock
  that was removed or shortened, or a prefix that was narrowed.
- An off-host monitor checks that every run inside the lock window is still complete.
  This catches a lock that was removed and later re-added.
- The ADR records an accepted risk: anyone with bucket-configuration rights in that
  account can remove the lock. That includes account API tokens.

**Operations.**

- The ADR sets safety requirements for the scheduled job, and recipient-integrity checks
  for the encryption keys.
- At least two holders keep the keys, off the host and outside R2.
- Restore drills run monthly, with a full host-loss drill quarterly.
- A freshness monitor runs outside the stack, with a dead-man's-switch heartbeat.

**Runbook.** It holds the procedures as they were executed on 2026-11-05, with the
review fixes. It adds an unexecuted production-restore procedure for when the host is
intact. It uses placeholders and contains no secrets.

**Waiting on the owner:**

- the R2 bucket and tokens;
- an inventory of account tokens that can edit bucket configuration;
- naming the key holders;
- choosing the third-copy location;
- rotating the Nexus admin password.

Part of #2032.
