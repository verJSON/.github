---
date: 2026-11-15
issue: 1869
impact: patch
title: Fetch secretless compatibility lanes from a pinned helper
---

Node CI no longer inlines the secretless compatibility lane executor. Both the standard and protected workflows fetch `scripts/ci_secretless_compatibility_lanes.py` at `JOB_WORKFLOW_SHA`, verify its SHA-256, and run it. The generator byte bounds drop from 490,000 to 416,000, 4,881 bytes above the 411,119-byte canonical workflow. See the 2026-11-15 amendment to ADR 0238.
