---
date: 2026-10-15
issue: 1779
impact: patch
title: Compare organization and repository identities case-insensitively in ruleset and secret audits
---
The package type-surface, Authn, and CLI projects ruleset audits and the organization secret-scope audit now compare GitHub organization and `owner/name` identities case-insensitively through one shared helper, `scripts/github_identity.py`. A different owner or repository, or a non-ASCII spelling that would case-fold onto a reviewed name, still fails closed. The nightly package type-surface audit passes against the live organization again.

The organization login changed display case from `Verjson` to `verJSON` on 2026-10-02, and the API reports the new case. Exact string comparison against the reviewed `Verjson` values had made the package audit report `source drifted` for all four managed rulesets every night since then. The Authn and CLI projects required-run receipts embed the repository in `workflow_url` and `details_url`; only that segment now compares as an identity. The CLI projects rollout tool now also runs under `python3 -I`. ADRs 0213, 0151, and 0088 carry dated amendments.
