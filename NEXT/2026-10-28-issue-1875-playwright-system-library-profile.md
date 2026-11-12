---
date: 2026-10-28
issue: 1875
impact: minor
title: Add a fixed Playwright system-library profile
---
Node CI can opt into a versioned Playwright 1.63.0 Chromium system-library profile on GitHub-hosted Ubuntu 24.04 amd64. Trusted setup validates and installs the complete signed, hash-pinned package closure offline, rejects any actual plan that would mutate `dpkg`, supervises every trusted subprocess, and verifies the complete installed-package receipt before launching the untrusted candidate. The exact needrestart hook is suppressed only after its installed fragment matches the authenticated package archive; existing package scripts may also run during upgrades. Cancellation or incomplete cleanup produces no receipt. Receipt cleanup is bound to invocation-owned filesystem identities, and workflow-side identity checks use explicit ASCII digits to stay locale-independent. Provisioning failures expose bounded, sanitized metadata for diagnosis. Part of #1875.

The Ubuntu archive signing key is pinned by SHA256 and snapshotted into the private invocation root before APT runs; any key change fails closed until runner-image and package provenance are reviewed. Parent-check failures report only the fixed parent path, numeric uid, and octal mode.

The protected Node CI generator's source and output byte tripwires rise from 480,000 to 490,000 bytes so that the merged canonical workflow (483,271 bytes) fits; ADR 0238's 2026-11-12 amendment records this as a labeled stopgap until inline candidate-execution code moves into the pinned helper modules.

The same-repository hosted acceptance workflow is Tier 2 work: it skips draft pull requests, starts on `ready_for_review`, and cancels a superseded run.
