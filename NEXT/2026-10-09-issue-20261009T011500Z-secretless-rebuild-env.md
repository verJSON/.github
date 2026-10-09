---
date: 2026-10-09
id: 20261009T011500Z
impact: patch
title: Harden secretless rebuild environment controls
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
untrusted scripts. Consumer scripts cannot access runner command-file paths, and
the compatibility validator uses `/usr/bin/python3` so caller path additions
cannot shadow its checks. GitHub-hosted non-Linux runners fail with an explicit platform error.
Tests cover npm and pnpm, host process scans, and writes to known host
command-file paths. The explicit hosted Actions CI lane runs the inherited-descriptor write probe and consumer sandbox harness against the verified Bubblewrap binary; persistent fastlane groups keep the deterministic command stubs.

Explicit, nested, and default consumer scripts in secretless mode now use a
Bubblewrap filesystem boundary on GitHub-hosted Linux as well. The checkout is
writable for build outputs with `.git` read-only, and runner temp is replaced
by private temporary storage so scripts cannot locate or change GitHub Actions
command files by searching host paths. The source reusable keeps the
caller-selected network policy; the generated protected workflow retains its
service-aware network isolation.

Database and cache service inputs now reject environment keys that can alter
runner process startup, executable resolution, GitHub CLI host selection,
Docker daemon selection, network/TLS routing, Git credentials, or workflow
command files. Regressions prove supplied `BASH_ENV` and `GH_HOST` cannot
capture or redirect a later token-bearing identity check; `DOCKER_HOST` and
other Docker controls are rejected before service-step side effects and before
candidate scripts receive their environment. Case-mixed npm configuration
overrides, carriage-return smuggling, and database endpoint overrides from the
cache service are also rejected before side effects when the database service
is enabled. Compatibility scripts use the same exact-script `requiresServices`
gate and receive configured DB/cache values only when their plan grants access.
Cache-only callers retain `DB_HOST` and `DB_PORT` as ordinary configuration.

The generated protected workflow runs explicit, nested, and default consumer
scripts one at a time inside Bubblewrap. It gives each script a fresh npm cache,
uses a minimal environment without runner command-file paths or `BASH_ENV`, and
keeps `.git` read-only while leaving build outputs writable. A trusted Python
bootstrap closes all inherited mount descriptors before npm starts. Only the
validated, job-scoped Playwright cache is writable for browser installation;
the workflow bounds its files and bytes before saving it. Scripts named
`test`, `test:*`, or `*:test` retain service access by default; custom test
scripts can request it with the strict JSON boolean `requiresServices: true`.
Only those scripts receive configured database/cache variables, and the
workflow rejects service-enabled plans on self-hosted runners. The generated
protected workflow grants network access only to service-enabled scripts; the
source reusable retains the caller-selected runner's normal network policy.
Network access on the permitted GitHub-hosted runner is unrestricted egress,
not a firewall limited to the configured services. Credential-bearing variable
names, nested encoded query/fragment aliases, and credential key/value DSNs are
rejected. Ordinary environment values such as the runner's `PWD`, `EXIT_CODE`,
and `CACHE_KEY` remain available. Npm configuration overrides that could point
candidate scripts at an authenticated `.npmrc` are removed. Candidate npm uses
`/dev/null` for user config and a fresh empty global config file inside the
sandbox; only the generated npm cache setting is retained. URL user information is accepted only for
loopback hosts or the exact database endpoint emitted by the workflow's
`db-service` step; caller-controlled `CACHE_HOST` values cannot authorize a
remote credentialed URL.
Query and DSN fields commonly used for credentials, including `key`, `code`,
and signed-query aliases, are rejected.
The documented `OPENAI_API_KEY=ci-dummy-key` test sentinel remains available;
other credential-bearing variables are rejected. Service values must remain
test-only and must not carry secrets outside that local URL or exact dummy
sentinel exception. The canonical Corepack cache is mounted read-only with
downloads disabled so pnpm scripts use the pinned manager already installed by
dependency restoration. Lifecycle rebuilds keep their separate network-isolated
sandbox. Contract tests cover default-plan execution, hostile `BASH_ENV`, a real
Bubblewrap inherited-FD write probe, signed-query and local-URL service
filtering, self-hosted service-plan rejection, and npm/Corepack-pnpm rebuild
paths.
