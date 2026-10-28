---
date: 2026-10-27
id: 20261027T120747Z
impact: patch
title: Preserve zero-provider recovery through the dispatch wrapper
---
`verify-zero-provider-recovery.sh` recognizes the exact `review / ` job-name prefix returned by the canonical dispatch wrapper and keeps direct job names for adopter workflows. Contract tests cover both paths. Part of #1831.
