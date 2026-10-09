---
date: 2026-10-09
id: 20261009T011500Z
impact: patch
title: Allow ONNX Runtime binary skip in secretless rebuilds
---
The reusable Node CI workflow allows only `ONNXRUNTIME_NODE_INSTALL=skip` when
`onnxruntime-node` is explicitly approved for credentialless lifecycle rebuild.
On GitHub-hosted runners, the workflow provisions and verifies its
bubblewrap/AppArmor boundary before the rebuild; other runner types fail closed.
It starts from a temporary filesystem root, isolates network access, and mounts
only system tools, the selected Node toolchain, required configuration, the
checkout, and (for pnpm) its Corepack cache. Directory descriptors hold the
checkout and tool sources through Bubblewrap setup, then a trusted bootstrap closes
all inherited descriptors before any package-manager lifecycle code runs. The checkout stays
read-only and only `node_modules` is writable. The package manager gets no
credentials, raw caller input, or GitHub command-file paths. Secretless pnpm
installs ignore repository pnpmfile hooks, and the rebuild mounts only the
runner's canonical Corepack cache. Protected identity checks run before
untrusted scripts so command-file changes cannot inject code into a later token
step. GitHub-hosted non-Linux runners fail with an explicit platform error.
Tests cover npm and pnpm, host process scans, and writes to known host
command-file paths. The explicit hosted Actions CI lane runs the inherited-descriptor write probe and consumer sandbox harness against the verified Bubblewrap binary; persistent fastlane groups keep the deterministic command stubs.

Database and cache service inputs now reject environment keys that can alter
runner process startup, executable resolution, GitHub CLI host selection,
network/TLS routing, Git credentials, or workflow command files. Regressions
prove supplied `BASH_ENV` and `GH_HOST` cannot capture or redirect a later
token-bearing identity check; case-mixed npm configuration overrides, carriage-return
smuggling, and database endpoint overrides from the cache service are rejected
before side effects when the database service is enabled. Cache-only callers
retain `DB_HOST` and `DB_PORT` as ordinary configuration.
