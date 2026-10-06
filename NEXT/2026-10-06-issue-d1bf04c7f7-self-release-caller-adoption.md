---
date: 2026-10-06
id: d1bf04c7f7
impact: major
title: Regenerate the release caller at the explicit-version contract
---

The organization's own generated release caller now requires an explicit version and rejects Unicode-whitespace-only input before release work. It pins the immutable contract from #1688 and uses the canonical `verJSON` organization spelling from #1698. The runner routing policy recognizes the generated canonical selector and still rejects unreviewed hosted fallbacks; the contract tools package test checks the regenerated header.
