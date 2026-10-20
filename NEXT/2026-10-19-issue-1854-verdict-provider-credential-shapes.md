---
date: 2026-10-19
issue: 1854
impact: patch
title: Refuse AI review verdicts that carry OpenAI or DeepSeek key shapes
---

The gate's verdict validator already refused a verdict containing the exact model credentials or a GitHub- or Anthropic-shaped token, but the verdict steps also hold the OpenAI and DeepSeek keys, and their formats were not recognized. `scripts/ci-gate/review-verdict.py` now also refuses an `sk-` token with 32 or more alphanumerics (DeepSeek and legacy OpenAI keys), an OpenAI `proj`, `svcacct`, `admin`, or `service` key, or an OpenAI-marked key with any other prefix. A key in Markdown emphasis or glued after `_` or a leading `-` is refused too. A bare prefix, a commit SHA, an integrity digest, or an identifier such as `task-sk-…` stays usable, and bounded bodies keep the scan linear on adversarial text. ADR 0237 decision 10 records the amendment.
