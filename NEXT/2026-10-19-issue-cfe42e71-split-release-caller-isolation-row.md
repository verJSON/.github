---
date: 2026-10-19
id: cfe42e71
impact: patch
title: Split the release-caller credential-isolation test into three actions-ci rows
---

The release-caller credential-isolation test now runs as three `platform` rows instead of one: the npm-free contract checks, then the npm 10 and npm 11 isolation cases. The single row had reached 56 seconds of its 60-second budget on hosted runners and then timed out. `RELEASE_CALLER_ISOLATION_PART` selects a part; unset, it still runs everything locally, and an unknown part exits 2 and runs nothing. Every part first runs the npm trace recorder guard that the isolation assertions rely on, and fails if the guard did not run or if its run tables and the case space name different cases. A new test, `scripts/release-caller-credential-isolation-parts.test.py`, derives the case space from the test itself, pins its expected checks and labels independently, and fails if the parts miss a case, overlap, or include an empty part, or if the manifest does not run each part exactly once.
