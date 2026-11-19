---
date: 2026-11-19
issue: 2210
impact: patch
title: Classify Renovate minor and patch titles of v2+ packages as eligible
---

The agent-delivery claim worker no longer fails closed on a minor or patch
Renovate title whose target major is 2 or higher (`to v2.2.0`), which blocked
#2222. A title is a major only when its target is a bare major or `N.0.0`;
`to v1` now fails closed too. Short commit digests (`digest to 1234567`,
`pin … to 11bd719`) are not majors. Labels still win over the title.
