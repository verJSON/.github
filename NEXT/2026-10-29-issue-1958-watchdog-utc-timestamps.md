---
date: 2026-10-29
issue: 1958
title: Make runner watchdog deadlines timezone-independent
impact: patch
---

Evaluate runner acquisition timestamps in UTC so watchdog deadlines remain
consistent across runner timezones: jq 1.6 applied the process zone's
daylight-saving offset, delaying the stall bound by up to an hour. Recorded as a
dated amendment to ADR 0230.
