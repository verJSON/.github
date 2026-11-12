---
date: 2026-11-12
issue: 2000
impact: patch
title: Floor the lifecycle re-arm caller at the arm that verifies its receipts
---

The capability-floor audit now reports a lifecycle re-arm caller
(`scripts/gen-ai-review-lifecycle-rearm-caller.sh`) as stale when it pins
Verjson/.github before `010fa39d`. That commit, first released in v4.0.0, made
the arm source-bind lifecycle deliveries. Daily observation now reads
verjson-authn's pinned lifecycle caller. The observe job's sparse checkout
includes the generator, without which the audit would reject the new floor
(#2000).

The lifecycle generator accepts any 40-hex contract SHA since #1993. A caller
pinned before `010fa39d` passes the authorization audit's completeness check,
yet it reaches an arm that binds local delivery only to the label caller's
`WORKFLOW_REF`, and that arm skips the schema-2 receipt bindings: protected
caller, first run attempt, actor, and exact event head. The new
`ai-review-lifecycle-receipt-binding` floor in `config/capability-floors.json`
closes that gap. `backlog-sparse-closure.test.py` now derives the observe job's
generator data from both capability-floor configs, so a future registration
cannot pass while the sparse list lags. verjson-cli-cloud's lifecycle caller
stays out of the discovery catalog because it still uses the `--local` form,
which discovery rejects.
