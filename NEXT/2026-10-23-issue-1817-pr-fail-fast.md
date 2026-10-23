---
date: 2026-10-23
issue: 1817
impact: minor
title: Fail fast on pull requests, cheap gates first, cancel job not adopted
---

`pulumi-ci.yml` stack previews and `actions-ci.yml` shell-test groups now stop sibling matrix cells after a failure on `pull_request` only; trunk, schedule, dispatch and merge-group runs keep the full inventory. On a pull request the heavy shell-test and compatibility jobs wait for the `docs-contracts` and `runner-isolation` gates to succeed. ADR 0238 records why the issue's cancel-on-failure job is not adopted, and `scripts/ci_runner_minutes.py` gives a before/after runner-minute estimate method.
