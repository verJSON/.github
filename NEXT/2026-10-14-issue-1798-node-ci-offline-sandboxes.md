---
date: 2026-10-14
issue: 1798
impact: patch
title: Restore native rebuilds and offline reinstall rehearsals in Node CI sandboxes
---
Two adopter paths broke once #1729 moved Node CI's credentialless steps into Bubblewrap sandboxes. Native addons such as `better-sqlite3-multiple-ciphers` could not build in the network-denied lifecycle rebuild because node-gyp tried to download Node headers. The rebuild now points node-gyp at the selected toolchain's own `include/node` headers, and network access stays denied. Consumer scripts that rehearse `npm ci --offline` against the cache promised by `secretless-runtime-public-cache` found that cache empty. The canonical consumer plan now gives each script a disposable copy of the verified runtime cache, as the protected workflow already did. Network policy and credential isolation are unchanged. See ADR 0086.
