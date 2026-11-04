---
date: 2026-11-04
issue: 2012
impact: patch
title: Zero-provider recovery admits every live review run status and names each failed predicate
---

`scripts/ci-gate/verify-zero-provider-recovery.sh` now treats a review run whose
status is `waiting`, `pending` or `requested` as the current live run, alongside
`queued` and `in_progress`. These are the non-terminal run statuses GitHub documents
in the REST *List workflow runs for a repository* `status` parameter. Before this
change, a run held while a job waited on environment protection was refused before
any provider ran. `completed` is still refused as terminal, and any status outside
that set is still refused as unrecognized. The identity predicates are unchanged.

A refusal still begins with `::error::zero-provider recovery run identity mismatch`.
Each predicate that failed now follows on its own line, with its observed and
expected values: `id`, `run_attempt`, `event`, `path`, `display_title`,
`head_branch`, the head repository, the repository, and `status`. The values are
JSON-encoded, non-ASCII characters are escaped, and `%` is written as `%25`, so a
lookalike shows up and a value cannot inject a workflow command or fake an annotation
line. Only run-record fields are printed. If the record cannot be evaluated, that is
reported too, even after partial predicate lines.

An empty or unparseable run record is refused, as before. This change also tightens
multi-document handling: the old `jq -e` check judged only the last document, so two
valid copies passed. A run record must now be exactly one JSON object.

`gate-rearm.yml`'s owned-review check already accepted the same live set, so it is
unchanged. This is the preflight refusal seen while gating #2011; follows #1393.
