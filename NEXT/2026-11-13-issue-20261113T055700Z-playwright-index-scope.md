---
date: 2026-11-13
id: 20261113T055700Z
impact: minor
title: Scope Playwright signed-index checks to used packages
---
Hosted Playwright setup reads signed Ubuntu index records only for its pinned package closure and the installed `needrestart` hook when present. Unrelated index conflicts no longer block setup; exact package pins and downloaded archive hashes remain checked. Part of #1875.
