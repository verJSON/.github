---
date: 2026-10-19
id: 5af3c1d2
impact: patch
title: Raise the review-caller bootstrap floor to the agent-config isolation fix
---

The authorization arm's review-caller bootstrap now refuses to move a stale caller to any revision before `4d95c2ee` (#1850), the change that stops the review from loading pull-request agent configuration, confines its reads, and fails closed on an incomplete diff. The previous floor was `12098a0e`. It takes effect once organization ruleset 20722935 is rotated to this change's merge commit (ADR 0227 amendment of 2026-10-19).
