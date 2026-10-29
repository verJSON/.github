---
date: 2026-10-28
issue: 1949
impact: patch
title: Reject container candidates from a different release source
---

Require new container promotion plans to use a candidate built from the release dispatch source commit. Historical retries whose recorded sources differ also stop before promotion; investigate them under the separate recovery contract.
