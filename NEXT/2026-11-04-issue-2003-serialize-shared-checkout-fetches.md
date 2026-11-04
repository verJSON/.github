---
date: 2026-11-04
issue: 2003
impact: patch
title: Serialize every fetch into the shared actions-ci checkout so concurrent rows cannot abort each other
---

Every actions-ci row that deepens or fetches the group's shared shallow checkout
now goes through `scripts/shared_checkout_fetch.py`. The helper holds an exclusive
lock on the repository's git directory for the whole fetch. Before this, the
`platform` shard failed intermittently with `fatal: shallow file has changed since
we read it` when `hub-changelog-validate.sh` deepened the checkout while another
row fetched into it, which turned `shell-tests` red with no code cause.

The routed callers are `hub-changelog-validate.sh`, `shellcheck-tracked.sh`, the
pin fetches in `node-workflow-pins.test.sh` and `contract-pin.test.sh`,
`capability-floor-audit.py`, and `authn-type-surface-ruleset.py`. Under the lock the
helper checks again whether a fetch is still needed, because a writer may have
waited while another one did the work. It does not repeat an unshallow, which git
refuses on a complete repository. It does not refetch a present commit at depth
one, or deepen a complete checkout, which would mark present commits shallow and
truncate the completed history. It also refuses a commit argument that is not a
full object name, so the argument cannot reach `git fetch` as an option.

The checkout stays bounded (ADR 0045). The validator's base-reachability check is
unchanged. `hub-changelog-validate-concurrent-deepen.test.sh` forces the
interleaving against a local origin, and `capability-floor-observe.yml` adds the
helper to its exact sparse checkout.
