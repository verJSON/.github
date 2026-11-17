---
date: 2026-11-17
id: 20261117T025200Z
impact: patch
title: Pin and acquire gitleaks for quality-review Evaluate
---

Part of #1820. Download gitleaks 8.30.1 with archive + binary sha256 pins,
verify before use, and pass `subprocess_scanner` into `run_review` so the
canary can obtain evidence instead of failing closed on an absent scanner.
