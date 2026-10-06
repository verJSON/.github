---
date: 2026-10-06
issue: 1688
impact: major
title: Require an explicit version in generated release dispatches
---

Generated package, artifact, and snapshot release callers now require an exact version at dispatch and reject blank or Unicode-whitespace-only input before release work, including on C-locale runners. The verified release plan continues to validate that version against the selected changelog fragments. ADR 0218 records the reversal of optional direct-dispatch versions.
