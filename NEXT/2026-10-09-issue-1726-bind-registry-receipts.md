---
date: 2026-10-09
issue: 1726
impact: major
title: Bind candidate receipts to observed provenance evidence
---

Candidate manifest v4 binds GAR index provenance and per-platform SBOM referrer records to registry readback, and validates each destination timestamp independently. Historical v2 and v3 candidates remain readable; v3 rejects v4-only fields, so candidates must be rebuilt before promotion. Promotion preserves the candidate manifest digest and evidence records, retains the exact signed candidate archive on the GitHub Release, and verifies that archive on resume.

The optional pre-credential reconciliation hook now has a complete release-runner sandbox contract. When an allowlist is configured, the reusable workflow installs Bubblewrap and AppArmor at verified package floors, checks package ownership using Ubuntu's canonical `/usr/sbin/apparmor_parser` path, loads the packaged restricted profile, and probes the namespace before running the hook. Setup runs before registry login, so package installation does not share runner state with stored registry credentials. The hook cannot create nested user namespaces and receives no capabilities. Reconciliation rejects pre-existing or hook-created Git replacement refs; its Git commands and every later release Git command ignore replacements. After the hook exits, it compares the `.git` pointer, configuration, history controls, merge state, and hooks directly before running any Git probe, preventing a changed `core.fsmonitor` from executing outside Bubblewrap or a changed `MERGE_HEAD` from altering release commit ancestry. It also verifies the pinned changelog checkout, rejects out-of-allowlist changes, and requires a second hook run to be idempotent before the release App token is minted.

[Issue #1726](https://github.com/Verjson/.github/issues/1726) records the TTP consumer evidence case in [self-publish-ai-app#1352](https://github.com/terptechpub/self-publish-ai-app/issues/1352). [ADR 0215](../docs/decisions/0215-verified-oci-candidate-registry-destinations/README.md) records the registry receipt contract; [ADR 0158](../docs/decisions/0158-pre-credential-release-reconciliation-hook/README.md) records the reconciliation boundary.
