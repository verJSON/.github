---
date: 2026-11-16
issue: 2141
impact: minor
title: Add opt-in post-publish OCI smoke to container-candidate-publish
---

`container-candidate-publish.yml` now accepts a required `post-publish-smoke` boolean that must match an optional reviewed `postPublishSmoke: { variant, command }` object, and runs a package-credential-free smoke against the published digest before the candidate manifest (#2141; unblocks verjson-ci#309).

The generator forwards the opt-in only on trusted publication; pull-request validation stays credential-free and never receives Docker smoke. Configured smoke fails closed when the digest or command is missing, and a failure blocks the candidate manifest. Adopters regenerate at the new contract pin and keep the smoke command in reviewed config.
