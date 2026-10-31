---
date: 2026-10-31
id: 20261031T060043Z
impact: patch
title: Observe a supervisor child pidfd handoff
---

Add an experimental one-shot supervisor-to-controller pidfd handoff reader
with bounded validation and descriptor cleanup. Part of #1966.
