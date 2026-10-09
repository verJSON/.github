---
date: 2026-10-09
issue: 1725
impact: patch
title: Separate npm config files in generated release verification
---

Generated Node release callers now give npm's user and global configuration namespaces distinct, empty files under a private temporary home. The verification contract test runs real npm through the emitted verification command in release-node, release-artifact, and release-snapshot modes, proving a package build runs without loading `/dev/null` twice. (#1725; ADR 0220)
