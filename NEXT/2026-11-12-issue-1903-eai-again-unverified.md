---
date: 2026-11-12
issue: 1903
impact: patch
title: Mark the Playwright EAI_AGAIN troubleshooting route as not live-verified
---

`docs/node-workflows.md` now states that the `EAI_AGAIN` troubleshooting route
(skip a browser package's install script and provision the browser in an earlier
script-plan entry) has not been verified in a live run, and that it is
experimental until a run exercises it (#1903).

The text it replaces cited the 2026-10-26 adopter run as confirming the route.
That run downloaded Chromium inside its own `test:*` script, through the source
reusable `node-ci.yml`, which keeps the runner's network policy (no script-plan network
isolation was in force), and listed no browser package in
`secretless-skip-install-scripts`. The doc now records it as evidence for that
narrower path only.
