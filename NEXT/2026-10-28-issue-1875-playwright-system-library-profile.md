---
date: 2026-10-28
issue: 1875
impact: minor
title: Add a fixed Playwright system-library profile
summary: Keep newer runner-image packages and use a digest-verified root-owned helper and APT cache for privileged provisioning.
---
Node CI can opt into a versioned Playwright 1.63.0 Chromium system-library profile on GitHub-hosted Ubuntu 24.04 amd64. Trusted setup installs offline only the closure packages the runner image lacks or ships below their reviewed pins, each at its signed, hash-pinned version; image packages already at or above their pins are kept, so it never downgrades a patched image library. It rejects any actual plan that would mutate `dpkg`, supervises every trusted subprocess, and verifies the receipt of the complete installed closure before launching the untrusted candidate. The exact needrestart hook is suppressed only after its installed fragment matches the authenticated package archive; existing package scripts may also run during upgrades. Cancellation or incomplete cleanup produces no receipt. Receipt cleanup is bound to invocation-owned filesystem identities, and workflow-side identity checks use explicit ASCII digits to stay locale-independent. Provisioning failures expose bounded, sanitized metadata for diagnosis. Part of #1875.

The Ubuntu archive signing key is pinned by SHA256 and snapshotted into the private invocation root before APT runs; any key change fails closed until runner-image and package provenance are reviewed. The key's parent directories are not mode-checked, because the hosted image ships `/usr/share` as `0777` and only the pinned bytes are ever admitted.

The protected Node CI generator's source and output byte tripwires rise from 480,000 to 490,000 bytes so that the merged canonical workflow (483,271 bytes) fits; ADR 0238's 2026-11-12 amendment records this as a labeled stopgap until inline candidate-execution code moves into the pinned helper modules.

The same-repository hosted acceptance workflow is Tier 2 work: it skips draft pull requests, starts on `ready_for_review`, and cancels a superseded run. Its completion also re-evaluates merge admission, as ADR 0260 requires of every workflow that reports on a pull-request head.

The generated protected workflow and runner-isolation digest are rebuilt against the merged baseline-recovery contract.

The protected workflow records the SHA-256 of the helper downloaded from its pinned workflow revision. Provisioning and receipt cleanup copy the helper into a root-owned private directory, verify the copy, and invoke only that copy. The provisioner recursively transfers every archive-cache entry, including nested entries under `partial/`, to root before digest validation; any ownership-transfer failure aborts before archive verification or installation. The catalog advances `libc6` to `2.39-0ubuntu8.12` and `libglib2.0-0t64` to `2.80.0-6ubuntu3.10`, with companion entries for `libc-bin` and `libglib2.0-data` at the matching revisions so apt can build a fully pinned plan. All package sizes and SHA-256 values were verified against authenticated Noble indexes and downloaded archives. Higher versions already installed on the runner remain in place. Part of #1875.
