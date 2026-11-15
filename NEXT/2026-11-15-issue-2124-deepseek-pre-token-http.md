---
date: 2026-11-15
issue: 2124
impact: patch
title: Keep pre-token DeepSeek failures from burning both review passes
---

Surface the non-secret HTTP status family when DeepSeek fails before any completion tokens, and do not reserve the automatic Flash pass for an identical auth, quota, upstream, or transport failure. Model-specific HTTP failures still cascade, real verdicts stay fail-closed, and the authorization check remains a neutral advisory.

Run 43737657166 on #2123 classified an immediate HTTPError as an empty semantic verdict and reserved pass 2. The same pattern burned #2116. Response bodies and exception text stay redacted. ADR 0090 amendment 2026-11-15. The advisory HTTP-status accept list uses explicit digit classes so settable-value-guards stays green under non-C locales.
