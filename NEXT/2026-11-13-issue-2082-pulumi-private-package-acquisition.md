---
date: 2026-11-13
issue: 2082
impact: minor
title: Enable private npm acquisition for Pulumi validation
summary: Pulumi validation can acquire approved @verjson packages without exposing the package token to lifecycle or caller code.
---

The reusable Pulumi workflow adds an optional `private-node-packages` allowlist. It checks the lockfile's exact private package names, downloads only approved integrity-verified tarballs with a scoped token, and removes npm's signed-redirect index before caller code runs. Caller install and validation commands run later without credentials and prefer the verified private blobs while public dependencies remain fetchable. Part of #2082; see ADR 0263.
