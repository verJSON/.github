---
date: 2026-10-14
issue: 1798
impact: patch
title: Build native addons offline in the Node CI lifecycle rebuild sandbox
---
Since #1729, the credentialless lifecycle rebuild in the canonical and protected Node CI workflows has run with no network egress. Native addons that fall back to `node-gyp rebuild`, such as `better-sqlite3-multiple-ciphers`, failed because node-gyp tried to download `node-vX-headers.tar.gz` from nodejs.org. The sandbox now points node-gyp at the mounted toolchain's own `include/node` headers, so these addons compile offline against the selected Node runtime. Network access stays denied and every isolation property is unchanged. See ADR 0086.
