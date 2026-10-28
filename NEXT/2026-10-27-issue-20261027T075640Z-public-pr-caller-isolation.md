---
date: 2026-10-27
id: 20261027T075640Z
refs: 1831
impact: patch
title: Route public reusable calls through hosted runners and caller concurrency
---
`pull_request` and `pull_request_target` are now both part of the strict public reusable-workflow isolation scan. Public callers are routed to GitHub-hosted runners before caller-selected lanes, and denied reusable workflows must fail through an explicit sentinel with job-level guards. The AI review worker remains on its stable reusable path without concurrency; a generated local dispatch caller owns run cancellation.
