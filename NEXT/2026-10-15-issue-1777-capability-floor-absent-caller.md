---
date: 2026-10-15
issue: 1777
impact: patch
title: Record a provably absent generated caller instead of aborting capability-floor observation
---

Capability-floor discovery now lists each consumer's `.github/workflows` directory at the resolved source commit and records an allowlisted caller missing from that successful listing as an `allowlisted-caller-absent` finding naming the repository as non-conforming, then continues. A 404 is never read as absence; every API, access, or response failure still fails closed.
