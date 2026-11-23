---
date: 2026-11-23
issue: 1264
impact: minor
title: Stage verified Alpine packages for the CLI candidate image
---

The standalone Linux amd64 CLI candidate can opt into a public Alpine input manifest shared with the portable builder. The reusable candidate workflows verify both APKs before staging them and build the opted-in image with offline Dockerfile instructions.

The staging boundary and adopter publication requirements are recorded in ADR 0078. The source repository tracks the consumer rollout in [verjson-ci #30](https://github.com/VerJSON/verjson-ci/issues/30).
