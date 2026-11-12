---
date: 2026-10-28
issue: 1939
impact: minor
title: Derive manually dispatched release versions from fragments
---

Allow an explicitly initiated release to derive the next version from the selected fragment impacts while retaining the verified release plan and existing publication boundaries. A blank-version dispatch must state the exact default-branch commit it authorizes in `expected_head`; the run fails before checkout if the branch has moved, so the derived version can be previewed offline with the pinned `release-plan` before dispatch.
