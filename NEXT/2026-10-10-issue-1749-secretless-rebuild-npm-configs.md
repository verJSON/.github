---
date: 2026-10-10
issue: 1749
impact: patch
title: ci(node): separate npm config files in secretless lifecycle rebuild
---
The secretless lifecycle rebuild creates separate mode-0600 empty npm user and global config files inside its `/tmp` sandbox before starting npm. This preserves the no-credentials boundary and avoids npm 11 rejecting one path used for both config roles. The registered rebuild environment regression checks that both paths are private to the sandbox and distinct. (#1749)
