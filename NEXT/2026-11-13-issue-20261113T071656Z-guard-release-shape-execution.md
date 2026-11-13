---
date: 2026-11-13
id: 20261113T071656Z
impact: patch
title: Guard release shape execution
---

The generated release contract test now runs its package-less fixture only after the caller's runtime capture and verification bodies match approved, mode-specific digests and its verification environment matches the approved contract (#1906). A modified caller body is rejected without executing that body. The fixture subprocess also excludes inherited credentials and shell startup variables.
