---
date: 2026-11-27
id: 20261127T140519Z
impact: patch
title: Classify structured doctl HTTP errors in host evidence probes
---

Report a fixed HTTP diagnostic when a failed `doctl projects list` probe
returns a valid JSON HTTP error status. This distinguishes rejected
authentication (401) from a refused API request (403) even when DigitalOcean
omits the request ID.

The transport keeps the established missing-token and legacy text diagnostics.
Malformed or ambiguous JSON produces `doctl probe failed`. Part of
[#629](https://github.com/verJSON/.github/issues/629) and
[verjson-git-runners#197](https://github.com/verJSON/verjson-git-runners/issues/197).
See [ADR 0198](../docs/decisions/0198-confine-runner-host-evidence-credentials/README.md).
