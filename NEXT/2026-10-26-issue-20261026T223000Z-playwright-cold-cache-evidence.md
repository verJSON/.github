---
date: 2026-10-26
id: 20261026T223000Z
impact: patch
title: Record live Playwright cold-cache evidence and its network limit
---
`docs/node-workflows.md` records that an external adopter's `e2e-full / build-test` passed after a Playwright browser-cache miss and downloaded browsers from the CDN. It states that one successful run does not guarantee CDN availability, so transient DNS failures can still fail a cold browser install.
