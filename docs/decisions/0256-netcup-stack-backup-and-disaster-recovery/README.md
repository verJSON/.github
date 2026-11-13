# 0256 — Back up the Netcup Git/CI stack to configured, locked off-site storage and drill every restore path

- **Date:** 2026-11-05
- **Status:** Proposed. The owner decisions below are recorded. ARB review approved this with changes, and those changes are applied here. The items under *Pending owner action* are not done yet.
- **Issue:** [verJSON/.github#2032](https://github.com/verJSON/.github/issues/2032)
- **Related:** [ADR 0160](../0160-self-host-paid-package-distribution-on-nexus/README.md), [ADR 0161](../0161-migrate-ci-to-gitlab-with-measured-parity/README.md), [ADR 0162](../0162-unify-portable-ci-engine-and-forge-adapters/README.md), [#1667](https://github.com/Verjson/.github/issues/1667)
- **Runbook:** [Netcup stack backup and restore](../../infrastructure/netcup-stack-backup-restore.md)
- **Category:** backup, disaster recovery, and secrets handling (sensitive class: storage credentials and key custody)

## Context

The self-hosted Netcup stack is one k3s node that runs GitLab CE 18.3.1, Nexus
Repository CE 3.83.0-08, MinIO, and two GitLab Runner managers
([service record](../../infrastructure/netcup-nuremberg.md)). #2032 records the
2026-10-19 to 2026-11-05 outage and its recovery. The comments on that issue hold the
inventory, the evidence, and the drill receipts that this decision builds on.

**The failure chain.** The node restarted and dropped MinIO's image from its cache.
Upstream MinIO had become source-only, and no public registry still served the pinned
digest anonymously. MinIO stayed in `ImagePullBackOff` for about 103k attempts.
Nexus's S3 blob store, which lives in MinIO, went to `Invalid state: FAILED`. The runner
managers pull their image from Nexus with `pullPolicy: Always`, so they failed too.
GitLab's object storage is in the same MinIO, so artifacts and uploads went down with
it.

**There was no backup.** Every stateful volume (`minio-data`, `nexus-data`,
`gitlab-data`, `gitlab-config`, `gitlab-logs`) was a `local-path` volume with reclaim
policy `Delete`. No backup CronJob existed, nothing wrote to the generated
`verjson-registry-backups` bucket, and no copy existed off the host. The
`verjson-registry-nexus` bucket is the only copy of the images we built, and it is
useless without the Nexus database on `nexus-data`.

**What the owner approved and what was done on 2026-11-05:**

- All five PVs were switched to `Retain`.
- GitLab and Nexus were scaled to 0, which took about 2 minutes of downtime.
- A root helper pod with `imagePullPolicy: Never` then took an offline tar of four
  volumes, about 6.3 GB in total. It used
  `tar --numeric-owner --xattrs --acls --sparse`.
- One copy stays on the host. As a one-off, its files were handed to the unprivileged
  transfer user so they could be copied off the host. A second, encrypted copy is on
  the owner's workstation, and the plaintext sha256 of every archive was verified both
  in transit and after decryption.
- The exact MinIO digest was recovered from an org-controlled GHCR copy and imported
  into the node's containerd. It was also placed in k3s's `agent/images` directory.
- Nexus was restarted and the runner pods were recreated. The stack is serving again.

**The restore drill passed.** It restored the off-host copy into a disposable stack:

- MinIO came back with all 8 buckets and 1,546 objects, with 0 read failures.
- Nexus served the runner manifest, and 44 of 44 nested manifests and blobs verified.
- GitLab had 3 projects, 9 users and matching HEADs, and `gitlab:doctor:secrets`
  reported 0 failures.

It gave this **measured baseline on local disk**. It covers machine time only, with no
download and no human time:

| Step | Time |
| --- | --- |
| Decrypt and restore all four volumes | 245 s (`minio-data` alone: 231 s) |
| MinIO live | 3 s |
| Nexus REST up | 24 s |
| GitLab ready | 86 s |
| Registry tier in total | about 6 min |

The drill also rehearsed a migration to Silo (`pgsty/silo`) and a rollback to the pinned
MinIO, and produced five inputs for this plan. Each one is a requirement below.

**R2 capabilities,** as cited in #2032 and checked against the
[R2 documentation](https://developers.cloudflare.com/r2/) on 2026-11-05:

- **Bucket locks** give retention by age, by date, or indefinitely, per prefix. A lock
  blocks overwrites, deletes, completion of earlier multipart uploads, and lifecycle
  deletion. It does not block creating new objects. These are not S3 Object Lock.
- **Locks are removable.** Anyone who can edit the bucket's configuration can remove
  a lock rule.
- **No versioning.**
- **Tokens.** There is no write-only token preset. `Object Read & Write` can be scoped
  to one bucket and cannot edit bucket configuration. `Admin Read & Write` tokens and
  account tokens with `Workers R2 Storage Write` **can** edit it
  ([API tokens](https://developers.cloudflare.com/r2/api/tokens/)). Temporary
  credentials can narrow a token further.
- **Location.** An `eu` jurisdiction can be chosen when a bucket is created.
- **Cost.** Egress is free.

**Owner decisions:**

- Use Cloudflare R2 as the off-site target, in the **existing Cloudflare account**
  (the one that holds verjson.com).
- Apply a **30-day bucket lock**.

**Organization principle.** Storage is set by configuration, so there is no vendor
lock-in.

## Decision

### 1. Scope

| Item | How it is captured | Notes |
| --- | --- | --- |
| GitLab repositories and database | `gitlab-backup create STRATEGY=copy`, run online before the quiesce window ([GitLab: backup strategy option](https://docs.gitlab.com/administration/backup_restore/backup_gitlab/#backup-strategy-option)) | This is the host-portable logical copy. It restores onto any host, but only into the same GitLab version, so the GitLab OCI archive travels with it (§9). The job moves the archive out of the volume, and the `gitlab-data` tar excludes GitLab's backup directory. `gitlab_rails['backup_keep_time']` is set to one day as a safety net, so archives can never pile up inside the volume. |
| `gitlab-secrets.json` and `gitlab.rb` | `gitlab-config` volume tar, kept as an artifact separate from the GitLab backup | GitLab does not include these in `gitlab-backup`. Without them the database's encrypted columns are unreadable. |
| `gitlab-data` volume | Offline tar in the quiesce window | This is the fast restore path the drill proved. It is about 350 MB, so the duplication is cheap. |
| MinIO: every bucket plus `.minio.sys` | Offline tar of `minio-data` | Covers `format.json`, `pool.bin`, IAM users and policies, and every `verjson-registry-*` bucket. The capture applies equally after the move to Silo, which reuses the same `xl-single` volume. |
| Nexus database **and** blobs | `nexus-data` and `minio-data` tars in the **same** quiesce window | The blob store is useless without the matching database, and the database is useless without the blobs. Never capture one without the other. |
| Cluster state | Namespaced manifests plus Secrets, exported as `kubectl get -o json` lists in one archive. It is client-side encrypted (§3) and never written to disk in plaintext. | Covers Deployments, Services, Ingresses, ConfigMaps, Jobs, PVs and PVCs, and Secrets. This also escrows the GitLab and Nexus S3 keys (§8). The k3s datastore is not snapshotted. The `verjson-cli` generator rebuilds the cluster, and these manifests and Secrets are the state the generator cannot reproduce. |
| Runtime images | Digest-pinned OCI archive of every image the stack needs to start | MinIO or Silo, Nexus, GitLab CE, the runner manager, and the bootstrap helpers (§9). |
| **Excluded: `gitlab-logs`** | Not captured | Logs are diagnostic and can be rebuilt. Losing them loses history, not service. This exclusion is deliberate, not an oversight. |

### 2. Consistent capture

Every scheduled run has two phases:

1. **Online:** run `gitlab-backup create STRATEGY=copy`. GitLab makes this consistent
   with a database dump and repository bundles. `STRATEGY=copy` copies the files
   before archiving them, so files that change during the run do not abort it.
2. **Quiesced:**
   - Scale GitLab, Nexus and MinIO to 0, and wait until their pods are gone.
   - Tar `minio-data`, `nexus-data`, `gitlab-data` and `gitlab-config` as root, with
     `--numeric-owner --xattrs --acls --sparse`. These are the same flags that were
     executed on 2026-11-05.
   - Scale back and wait for readiness.

The 2026-11-05 capture used the same window, which took about 2 minutes. MinIO was
already down that day. The scheduled run also stops MinIO, because a running MinIO
rewrites its usage, bloom and `tmp` files under `.minio.sys` even when no client writes
to it. The drill showed this: starting MinIO alone changed those files. A file-level
copy of a running engine is not a consistent capture.

The window runs once a night, at a fixed low-traffic time. A CI job that hits the window
is retried automatically only if its pipeline sets `retry:` for the failure it gets
(`runner_system_failure`, `stuck_or_timeout_failure`, `api_failure`). Every other job
fails and needs a manual retry. That cost is accepted, because the stack is a
development and CI service, not a customer-facing one. Pipelines that run on the stack
should add that `retry:` block.

### 3. Format and encryption: independent tar archives, `age` to two or more recipients

Every artifact is a plain tar stream, encrypted with
[`age`](https://age-encryption.org/) to the public recipients of at least two key
holders (§7). The host holds only those recipients, so **the host cannot decrypt its
own backups**.

Each run writes a `SHA256SUMS` that covers the plaintext and the ciphertext of every
artifact. It also writes a `MANIFEST.json`, uploaded **last**. A run with no
`MANIFEST.json` is incomplete by definition. The manifest records:

- the start and end times, and the quiesce duration;
- the source volumes and their sizes;
- the runtime image digests in use;
- the tool versions;
- the recipient fingerprints (§7);
- the lock-probe result (§5).

**Why not restic or kopia.** The decisive reason is the key model, not storage
behavior:

- Both tools encrypt the whole repository under a **symmetric** master key.
- Any client that writes to the repository must unlock that key with the repository
  password ([restic: keys, encryption and MAC](https://restic.readthedocs.io/en/stable/100_references.html#keys-encryption-and-mac);
  kopia derives its repository keys from a password in the same way).
- So a backing-up host can also decrypt every snapshot. That breaks the rule that the
  host cannot decrypt.

Bucket-lock compatibility is a secondary concern, and it can be solved:

- `prune` and maintenance delete young index and lock files, which a whole-bucket lock
  refuses.
- Locking only the `data/`, `keys/` and `snapshots/` prefixes, and running
  `restic repair index` when needed, would work.
- But that adds steps, adds a repository format between us and the data, and lets one
  corrupted index affect every snapshot.

Independent archives avoid all of that:

- The writer never deletes or overwrites anything.
- Each run is self-contained and fails in isolation.
- Restore needs only `age` and `tar`. The drill proved that path end to end, using gpg
  for its one-off copy.

The cost is no deduplication. At today's 6.3 GB and the retention in §4, that is about
300 GB, or roughly $4.50 a month. Revisit this choice if a single full capture grows
past 100 GB.

**Why `age` rather than gpg.** `age` handles multiple recipients natively, needs no
keyring and has no trust model to configure. Each recipient can decrypt on their own.
gpg can do the same with public keys, but its keyring configuration is easy to get
wrong. The 2026-11-05 copy used a symmetric gpg passphrase. It stays readable with that
escrowed passphrase (§7), but the scheduled job must not copy that model.

**Where the job runs.** Backup and restore run as a **systemd service and timer on the
host**, installed and pinned by the `verjson-cli` generator. They do not run as an
in-cluster CronJob:

- Backups must still work when the cluster is unhealthy, which is exactly when a
  restore is needed.
- A privileged pod with hostPath access to every volume is a larger standing attack
  surface than a root-owned unit.

The 2026-11-05 helper pods were the right tool for a one-off, given the access available
that day. They are not the model for the scheduled job.

**Safety requirements for the scheduled job.** The job must:

- **Restore replica counts on every exit.** Record each workload's replica count before
  quiescing, and use a trap to restore it on any exit: success, failure, or signal.
- **Abort when the kube API is unavailable.** Check before scaling anything. During the
  quiesce wait, an API error counts as *not quiesced*. It is never counted as zero
  pods.
- **Check free space first.** Before quiescing, `<backup-root>` must have room for one
  more full run, sized from the last run plus a margin. Otherwise abort without
  quiescing.
- **Prune only after success.** Delete older host-local runs only after the new run's
  `MANIFEST.json` is complete and its checksums verify.
- **Pin images before the timer is enabled.** Enable the timer only after every image
  used by a quiesced workload is pinned through `agent/images` (§9). Scaling to 0 every
  night leaves those images unused, which makes them eligible for kubelet image garbage
  collection.
- **Verify ciphertext on the host.** Every `age` process must exit 0. Each ciphertext
  length must fit the plaintext length under age's STREAM framing (a 16-byte nonce,
  then 64 KiB chunks with a 16-byte tag each, after the header), and must equal the
  length of the uploaded object. This catches truncation without decrypting.
- **Require two recipients.** Enable the timer only when at least two recipients are
  configured (§7).

The host cannot decrypt, so **decrypt-verify happens in the monthly drill** (§6), not
in the job.

### 4. Layout, 3-2-1, and recovery targets

| Copy | Location | Medium and domain | Retention | Purpose |
| --- | --- | --- | --- | --- |
| Primary | Live volumes on the node | Node disk | — | Production |
| 1. Host-local | `<backup-root>/<UTC timestamp>/`, outside k3s storage. Root-owned, `0700` for directories and `0600` for files. | Same disk, different path | Latest 2 runs | Fast recovery from operator or k3s mistakes. It does **not** survive losing the host. It holds the plaintext volume tars that were proven on 2026-11-05. The Secrets export exists only encrypted. Unlike the one-off 2026-11-05 transfer, it is never handed to an unprivileged user. |
| 2. Off-site | R2 bucket in the existing Cloudflare account | Object storage, Cloudflare | `daily/` expires after 35 days, `monthly/` after 400 days, `images/` never expires | Survives losing the host. Locked for 30 days (§5). |
| 3. Independent | Pending owner action (§10) | A different administrative domain from both Netcup and the Cloudflare account | At least 8 weekly copies | Survives losing or compromising the Cloudflare account, which is the accepted risk in §5. |

This exceeds 3-2-1. There are three copies besides production, on two media, with two
of them off-site, and one immutable for 30 days.

R2 key layout. Every key is written exactly once:

```text
daily/<UTC-ts>/{gitlab-backup,gitlab-config,gitlab-data,minio-data,nexus-data,cluster}.tar.age
daily/<UTC-ts>/SHA256SUMS
daily/<UTC-ts>/lock-probe
daily/<UTC-ts>/MANIFEST.json          # written last
monthly/<UTC-ts>/...                  # the first run of each month writes here instead of daily/
images/<image>/<sha256-digest>.oci.tar.age
```

**RPO.** The nominal RPO is 24 h, because one nightly run captures everything. After one
missed run it can reach about 48 h. The monitor alerts at 26 h (§6).

**Restore order and RTO targets.** Every restore takes **all volumes from the same
run**. Mixing runs breaks the pairing between the Nexus database and its blobs, and
between GitLab and its object storage. The restore order is:

1. images;
2. cluster manifests and Secrets;
3. MinIO;
4. Nexus;
5. GitLab;
6. runners.

| Tier | Data | Restore position | RTO target, host intact | Measured baseline (2026-11-05, local disk, machine time only) |
| --- | --- | --- | --- | --- |
| 0. Platform | OCI archives; cluster manifests and Secrets | First | Inside tier 1's target | Not measured |
| 1. Registry | `minio-data` (Nexus blobs and `.minio.sys`) and `nexus-data` | After platform | 4 h | About 6 min: the 245 s restore of all four volumes, then MinIO in 3 s and Nexus in 24 s |
| 2. GitLab | `gitlab-data`, `gitlab-config`, GitLab buckets in MinIO | After MinIO and Nexus | 8 h | GitLab ready in 86 s; its volumes are inside the 245 s above |
| — | `gitlab-logs` | Not restored | — | — |

**Losing the host entirely** has a target of **1 business day**: a new host, k3s through
the generator, the R2 download, and tiers 0 to 2. This target is **not measured yet**,
and the first quarterly drill (§6) must measure it.

Git source code stays authoritative on GitHub, and the GitLab projects mirror it. A
GitLab restore therefore recovers GitLab's own state (users, runners, CI history and
settings), not the only copy of any code.

### 5. The backup target is set by configuration; R2 for now

The job reads its target only from configuration:

- an S3-compatible endpoint, plus a jurisdiction-specific endpoint where one applies;
- the bucket and a key prefix;
- a reference to the credential;
- a **provider profile** that declares how the provider refuses a locked delete. For R2
  that is HTTP 409 with `ObjectLockedByBucketPolicy`.

The code depends on no provider API. Uploads use a pinned `rclone` with
`copy --immutable`, which refuses to modify an existing key, and with `no_check_bucket`
set, because a bucket-scoped object token cannot check or create buckets. Pointing the
job at another S3-compatible store means changing the configuration and its provider
profile, not the code.

**R2 configuration** (owner-decided):

- **The bucket lives in the existing Cloudflare account.**
- **One lock rule** with no prefix and a 30-day age condition, so it covers every
  object.
- **Lifecycle rules:** `daily/` expires after 35 days and `monthly/` after 400 days. The
  lock takes precedence over lifecycle, so no object expires inside its first 30 days.
- **Optional `eu` jurisdiction.** It keeps the data in the same jurisdiction as the
  Nuremberg host. It can only be chosen when the bucket is created, and it needs the
  jurisdiction-specific endpoint. The owner decides at creation.
- **No versioning exists,** so any overwrite after the lock expires is unrecoverable.
  The writer therefore uses unique keys and `--immutable`, and never overwrites.

**Credential scope:**

| Credential | Holder | Scope |
| --- | --- | --- |
| Writer | Host only, in a root-owned `0600` file | `Object Read & Write`, scoped to the one bucket. It cannot edit bucket configuration, so it cannot remove the lock. Inside the lock it can only add objects. R2 temporary credentials may narrow it further, but only if they are minted **off the host**. Minting needs the parent token's secret, which must never reach the host. |
| Reader | The freshness monitor and the third-copy puller, both off the host | `Object Read only`, scoped to the one bucket |
| Admin (lock and lifecycle rules) | The owner's interactive Cloudflare login, with 2FA | No `Admin Read & Write` API token is stored anywhere |

**Lock probe.** Every run writes a small `lock-probe` object into its own run prefix.
Then, separately for `daily/` and for `monthly/`, it picks the **oldest eligible**
probe. A probe is eligible when it is at least 1 day old and at most **28 days** old.
Capping it 2 days below the 30-day lock keeps the probe from racing the lock's expiry.
The run sends one **single-object** `DeleteObject` for that probe. In steady state the
`daily/` probe is about 28 days old.

`monthly/` is probed too, and is not dropped in favor of probing only `daily/`. A rule
narrowed to `daily/` would otherwise go unnoticed. The cost is that `monthly/` has no
eligible probe on the 1st of each month and late in a 30- or 31-day month.

Each prefix's result is recorded in `MANIFEST.json`:

| Outcome | Recorded as | Alerts |
| --- | --- | --- |
| Exactly the provider profile's refusal: for R2, HTTP 409 with `ObjectLockedByBucketPolicy` | `refused` | No |
| The delete succeeds | `deleted` | Yes |
| Anything else: 404, 403, 5xx, a timeout, or a different 409 code | `error` | Yes |
| No probe between 1 and 28 days old exists in that prefix | `no-eligible-probe` | No |

`no-eligible-probe` is expected on the first runs and in the `monthly/` gaps above. It
is not an error. A `daily/` prefix that stays in that state means runs are missing, and
the freshness checks in §6 alert on that instead.

The probe never uses the batch `DeleteObjects` call. That call returns 200 even when
keys are locked, so its status shows nothing.

**What the probe detects:**

- a lock rule that was removed;
- a lock whose duration was shortened below the probe's age, which is about 28 days in
  steady state for `daily/`;
- a lock rule whose prefix was narrowed so that it no longer covers `daily/<ts>/` or
  `monthly/<ts>/`.

**What it does not detect:**

- a rule narrowed to exclude other keys inside a run prefix while still covering
  `lock-probe`;
- a lengthened lock;
- **remove, delete, re-add.** Between two probes, someone removes the rule, deletes
  objects, and re-adds the rule. The next probe sees `refused`. The monitor's
  completeness check in §6 catches this case instead.

If the lock is missing, the probe costs only the probe object.

**Accepted risk: anyone who can edit the bucket's configuration can remove the lock.**
The lock protects against the writer credential and against mistakes. It does **not**
protect against anyone who can edit the bucket's configuration. In the existing account
that set **may include, pending the inventory in §10**:

- the owner's account, and any member with R2 admin rights;
- **account API tokens** that carry `Admin Read & Write` or `Workers R2 Storage Write`
  ([R2 API tokens](https://developers.cloudflare.com/r2/api/tokens/)), possibly
  including deploy tokens stored as secrets in other repositories' CI;
- **user API tokens**, which inherit their owner's permissions
  ([R2 API tokens](https://developers.cloudflare.com/r2/api/tokens/)), from any member
  who can edit R2;
- members' **Global API Keys**, a legacy credential with full account access
  ([Global API key](https://developers.cloudflare.com/fundamentals/api/get-started/keys/));
- anyone who takes over one of the above.

Any of them can remove the rule and then delete everything. A separate, dedicated
account would shrink that set. The owner chose the existing account for operational
simplicity and accepted the risk. It is mitigated by:

- keeping R2-admin members to a minimum, with hardware-key 2FA;
- **inventorying every account token, user token and Global API Key that can edit
  bucket configuration, then removing or narrowing the rest** (a pending owner action,
  §10);
- the lock probe;
- the independent third copy (§4), which a Cloudflare compromise cannot reach.

**Residual risk.** Objects older than 30 days, including the `monthly/` keepers, can be
deleted with the writer credential. A compromised host can therefore destroy them, but
it cannot touch the most recent 30 days. The third copy covers that gap.

### 6. Restore drills and freshness monitoring

**Monthly drill.** A key holder restores the newest R2 copy into a disposable
environment off the production host, using the drill in the runbook. This is also where
decrypt-verify happens (§3). A pass requires all of these:

- ciphertext and plaintext checksums match `SHA256SUMS`;
- `format.json` reads `xl-single`;
- every bucket's object count matches, and a full read has 0 failures;
- the Nexus blob store is available, with no `Invalid state: FAILED`;
- the runner manifest and all of its nested blobs verify by digest;
- GitLab's project, user and group counts and its HEADs match;
- `gitlab:doctor:secrets` reports 0 failures.

**Quarterly drill.** The **other** key holder runs it, which proves that both holders
can decrypt independently. It rehearses losing the host: it uses only the generator and
R2, restores the `gitlab-backup` artifact into a fresh GitLab of the pinned version, and
measures the end-to-end RTO against §4.

**Drill receipts.** Each drill posts a receipt to its tracking issue: counts, timings,
pass or fail, and the artifact and image digests. It never includes secrets, endpoints
or host inventory. A failed drill is an incident.

**Freshness monitor.** It runs **outside the stack**, so not on the Netcup host and not
on its runners. The default is a scheduled job on a GitHub-hosted runner, using the
reader credential. It alerts on the configured **alert channel** when any of these
holds:

- the newest `MANIFEST.json` across **both** `daily/` and `monthly/` is older than
  26 h;
- an artifact listed in that manifest is missing or empty;
- **completeness:** a run still inside the 30-day lock window is missing its
  `MANIFEST.json`, or is missing an artifact, or an artifact's length differs from what
  was recorded. The monitor keeps an off-host ledger of every manifest it has seen. It
  checks every ledger entry younger than 30 days on each pass, and alerts on any day
  in the window that has no run. This is the check that catches
  remove-delete-re-add (§5);
- a probe result is `deleted` or `error`. `refused` and `no-eligible-probe` do not
  alert;
- the manifest's recipient fingerprints differ from the expected set (§7);
- the third copy is older than 8 days.

After each check, the monitor pings a configured **dead-man's-switch heartbeat
receiver**. That receiver alerts on its own if the pings stop, so a silent monitor is
noticed. Both endpoints are configuration and appear in no document.

### 7. Key custody

- **At least two named holders,** each with their own `age` identity.
- **Where identities live:** never in R2, on the host, or in this repository. Each
  holder keeps theirs in a password manager and on an offline medium, such as paper or a
  hardware token.
- **Recipient integrity.** The host's recipient list is reviewed configuration in the
  generator, and changes to it are gated by CODEOWNERS. Each run records the
  recipients' fingerprints in `MANIFEST.json`. The monitor compares them against an
  expected set held off the host, and alerts on any unexpected or missing recipient.
- **What that check covers.** It catches misconfiguration and unreviewed configuration
  changes. It does **not** stop a compromised host. Such a host already has the
  plaintext, and the fingerprints are self-reported: an age X25519 recipient stanza
  carries only an ephemeral share, not the recipient's identity, so the ciphertext
  cannot confirm them ([age v1 spec](https://age-encryption.org/v1)).
- **Two-recipient precondition.** The timer is enabled only once at least two
  recipients are configured.
- **Escrow set,** held with the identities under the same custody:
  - the Nexus break-glass credential (§8);
  - the 2026-11-05 gpg passphrase, until that copy is superseded and expired;
  - the access procedures this public repository must not describe.
- **Changing holders.**
  - Adding a holder adds a recipient, and only future runs use it.
  - Removing a holder removes their recipient. Older archives stay readable to the
    removed identity until they expire, at most 400 days later. That exposure is
    accepted. Re-encrypting the archive history was rejected.

### 8. Requirements from the 2026-11-05 drill

1. **Nexus admin.** The `admin` account still uses its first-boot password and is marked
   `changepassword`. Nexus keeps that password in plaintext inside data that every
   backup captures. *Pending owner action, because it is a live change:*
   - rotate the password;
   - confirm the plaintext copy is gone;
   - escrow a break-glass credential with the backup keys (§7).
2. **GitLab S3 key.** It exists only in the cluster Secret, and the encrypted Secrets
   export (§1) now escrows it. The runbook also keeps the fallback the drill proved:
   - start MinIO with a fresh root;
   - create a new user and attach the `verjson-gitlab` policy;
   - point GitLab's object-store connection at that user.
3. **MinIO root.** A restore does not need the old root credential, because any new root
   opens an `xl-single` volume. Production runs with `MINIO_API_ROOT_ACCESS` off.
   - A restore turns it **on only for the restore**, to verify buckets and IAM or to
     create the fallback user.
   - It turns it back off, and confirms it is off, before the stack returns to
     service.
4. **OCI archives travel with every copy.** Every backup copy, whether host-local, R2 or
   third, holds digest-pinned OCI archives of the images needed to restore it: MinIO or
   Silo, Nexus, GitLab CE, the runner manager, and the helper images. A copy without
   them cannot be restored once upstream images disappear.
5. **`gitlab-logs` is excluded** (§1).

### 9. Image availability: a lost node cache must never stop the stack again

- **Keep a local archive of every image.** Every runtime image the stack needs to start
  is kept as a digest-pinned OCI archive in `/var/lib/rancher/k3s/agent/images/`. k3s
  re-imports and pins those images on every start (#2032). So neither a lost cache nor
  a vanished registry can block startup, and a pinned image is not garbage-collected
  while its workload is scaled to 0. The MinIO archive placed there on 2026-11-05 is
  the first; the generator extends this to every image.
- **Keep an org-controlled copy outside the stack.** Each such image also has a copy in
  a registry that does not depend on the stack. Today that is GHCR, by digest.
  - The MinIO copy that saved this recovery sits under `verjson/marketing-studio` only
    by accident. A reviewed mirror workflow moves it to a stack-owned path.
  - Nexus cannot hold these copies, because Nexus itself depends on MinIO.
- **Use `imagePullPolicy: IfNotPresent` for digest-pinned images.** With a digest pin,
  `Always` adds a registry round-trip and no safety. It is what turned Nexus's outage
  into the runners' outage.
- **Recovery path when an image is missing:**
  1. `crane pull --format=oci` by digest.
  2. Verify that the digest header and the hash of the manifest body both equal the
     pin.
  3. `ctr images import`.
  4. Tag the digest reference, and label it `io.cri-containerd.image=managed`.

  The runbook carries the steps as they were executed.

### 10. Decided vs. pending

**Decided** by this ADR, together with the owner decisions it records:

- the scope, including the `gitlab-logs` exclusion;
- the quiesced capture and the job's safety requirements;
- tar plus `age` to two or more recipients, with `SHA256SUMS` and a `MANIFEST.json`
  written last;
- the host-level systemd job;
- the 3-2-1 layout, the restore order and the RTO/RPO targets;
- a target set by configuration, with a provider profile, and R2 as the current target;
- R2 in the existing account with a 30-day lock, and the risk that comes with it;
- the credential scope and the lock probe;
- the drill cadence and the external freshness monitor with its heartbeat;
- key custody and recipient integrity;
- the five drill requirements;
- the image-availability rules.

**Pending owner action.** Nothing below has been done yet.

- [ ] Create the R2 bucket in the existing account, and choose `eu` or not at
      creation. Add the 30-day lock rule with no prefix, and the lifecycle rules.
- [ ] Issue the writer token (bucket-scoped `Object Read & Write`, for the host) and the
      reader token (`Object Read only`, for the monitor and the puller).
- [ ] Inventory everything that can edit R2 bucket configuration, then remove or narrow
      what you find:
  - members with R2 admin rights;
  - account API tokens with `Admin Read & Write` or `Workers R2 Storage Write`,
    including deploy tokens stored in other repositories' secrets;
  - user API tokens, which inherit their owner's permissions;
  - members' Global API Keys.
- [ ] Name at least two key holders. Each generates an `age` identity and shares only
      the public recipient. Record the expected fingerprint set off the host. The
      2026-11-05 gpg passphrase still needs a second holder.
- [ ] Choose the third-copy location. It must be outside both Netcup and the Cloudflare
      account, and it pulls with the reader token.
- [ ] Rotate the Nexus `admin` password and escrow the break-glass credential.
- [ ] Upload the 2026-11-05 copy to R2 once the bucket exists. #2032 calls this copy 3.
- [ ] Approve the nightly quiesce window.

**Pending engineering,** tracked on #2032 until it is split:

- **`verjson-cli` generator changes:**
  - the backup systemd unit and timer, meeting the §3 safety requirements;
  - an OCI archive of every runtime image in `agent/images`;
  - `IfNotPresent` for digest-pinned images;
  - the `MINIO_API_ROOT_ACCESS` toggle used during restore;
  - the CODEOWNERS-gated recipient configuration;
  - `backup_keep_time`, and the `gitlab-data` tar's exclusion of GitLab's backup
    directory;
  - rclone with `--immutable` and `no_check_bucket`.
- `retry:` in the pipelines that run on the stack.
- The stack-owned image mirror.
- The external freshness monitor, with its alert channel, its heartbeat receiver, and
  its off-host completeness ledger.
- The first monthly and quarterly drills.

## Consequences

- **Positive:**
  - Every stateful component has an encrypted, checksummed off-site copy that is locked
    for 30 days, plus an independent third copy.
  - The host cannot decrypt what it writes.
  - Restore is a proven, measured path, not an assumption.
  - The image-cache failure behind #2032 cannot recur silently.
- **Negative:**
  - The nightly quiesce costs about 2 minutes of downtime.
  - There is no deduplication, so storage grows linearly with retention.
  - Two people must keep their keys safe and run drills.
  - Anyone who can edit bucket configuration in the existing account can still remove
    the lock. That risk is accepted and mitigated in §5.
- **Neutral:** Moving the storage engine to Silo, or to any later engine, changes only
  what produces `minio-data`. The capture, encryption and restore contract stays the
  same.

## Considered options

- **A restic or kopia repository on R2.** Rejected. Both need a symmetric repository key
  on the writing host, so the host could decrypt every snapshot (§3). Their conflict
  with the lock is secondary, and it can be worked around.
- **Symmetric gpg, as used for the 2026-11-05 copy.** Rejected for the scheduled job,
  because the host would hold the decryption secret.
- **A dedicated Cloudflare account for backups.** It would shrink the set of identities
  that can remove the lock. The owner declined it for now, and the risk is recorded in
  §5. Because the target is set by configuration, this can be revisited without code
  changes.
- **Netcup server snapshots.** Not counted as a copy. They stay with the same provider
  and administrative domain, and they are not consistent across the stack's writers.
- **An in-cluster backup CronJob.** Rejected in favor of a host-level unit (§3).
- **A k3s datastore snapshot.** Not taken. The generator rebuilds the cluster, and the
  encrypted export of manifests and Secrets carries the state it cannot reproduce (§1).
