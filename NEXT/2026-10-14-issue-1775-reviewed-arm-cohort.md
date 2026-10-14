---
date: 2026-10-14
issue: 1775
impact: patch
title: Restore the reviewed authorization-arm cohort after the organization rename
---
The authorization-arm audit now compares repository identities case-insensitively and names the unreviewed and absent repositories when the cohort drifts. The reviewed migration set adds verjson-document-contracts, and the accepted verjson-agents finding is re-recorded under the renamed `verJSON` display case.

The conformance workflow had failed daily since 2026-10-02. On that day the organization login became `verJSON`, so all 29 reviewed `Verjson/...` names stopped matching the API. This masked the one real omission, which the audit did not name. Reviewed entries must now use GitHub's ASCII repository-name charset, and a case-only duplicate is rejected wherever the audit reads repository identities. ADR 0206 is amended with the evidence.
