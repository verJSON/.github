---
date: 2026-10-14
issue: 1767
impact: patch
title: Restore default SIGINT and SIGQUIT for actions-ci rows
---
The actions-ci group runner now execs every manifest row through `env --default-signal=INT,QUIT`, so a row can trap SIGINT and SIGQUIT even though the runner starts it asynchronously and a non-interactive bash hands asynchronous commands those signals ignored. Budgeted pull-request runs got the defaults back from `timeout`; unbudgeted push runs on `main` did not, which failed `runner-canary-contract.test.py` after #1746 merged. The runner refuses to start without `env --default-signal` (GNU coreutils 8.31 or later). See #1767 and ADR 0225.
