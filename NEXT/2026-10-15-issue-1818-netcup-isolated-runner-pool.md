---
date: 2026-10-15
issue: 1818
impact: patch
title: Untrusted CI can opt in to an ephemeral isolated runner pool on netcup
---

ADR 0234 admits `verjson-isolated`, an Actions Runner Controller scale set on the netcup k3s cluster, as a bridge until CI moves to GitLab CE (ADR 0161), so that untrusted jobs needing neither Docker nor a GitHub-hosted environment stop drawing on the exhausted hosted budget. Each job gets its own pod with `hostUsers: false`, so container root, reachable through `sudo`, maps to an unprivileged host uid. The pod has no container socket and no service-account token. A NetworkPolicy allows only DNS and TCP 80/443 to public addresses, excluding the node's own IP, so jobs cannot reach GitLab, Nexus, MinIO or the k3s API.

Routing is opt-in per repository: a repository-level `CI_LANE_UNTRUSTED` (plus the aliases its pins read) set to `["verjson-isolated"]`, or a pull request for a hard-coded hosted job. The organization-level untrusted selectors stay at ADR 0147's `["ubuntu-24.04"]`, so `runner-admission-reconcile` is unchanged. Jobs that need Docker or `services:` are out of scope.
