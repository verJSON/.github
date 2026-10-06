---
date: 2026-10-06
issue: 1688
impact: major
title: Require an explicit version in generated release dispatches
---

Generated package, artifact, and snapshot release callers now require an exact version at dispatch and reject blank input before release work. The verified release plan continues to validate that version against the selected changelog fragments. ADR 0216 records the reversal of optional direct-dispatch versions.
