---
date: 2026-10-28
id: 795c0c4d73
impact: patch
title: Add a fixed Playwright system-library profile
---
Node CI can opt into a versioned Playwright 1.63.0 Chromium system-library profile on GitHub-hosted Ubuntu 24.04 amd64. Trusted setup validates and installs the complete signed, hash-pinned package closure offline, rejects any actual plan that would mutate `dpkg`, supervises every trusted subprocess, and verifies the complete installed-package receipt before launching the untrusted candidate. The exact needrestart hook is suppressed only after its installed fragment matches the authenticated package archive; existing package scripts may also run during upgrades. Cancellation or incomplete cleanup produces no receipt. Receipt cleanup is bound to invocation-owned filesystem identities, and workflow-side identity checks use explicit ASCII digits to stay locale-independent. Provisioning failures expose bounded, sanitized metadata for diagnosis. Part of #1875.
