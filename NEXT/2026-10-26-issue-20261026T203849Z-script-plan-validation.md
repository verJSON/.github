---
date: 2026-10-26
id: 20261026T203849Z
refs: 1869
impact: patch
title: Unify normalized CI script-plan validation
---
Part of #1869. Add one pure normalized-entry validator used by both canonical Node CI executors. Both executors load it and the existing parser from the immutable workflow SHA into runner temporary storage. The composite action pin and its byte-hash check stay unchanged; filesystem containment, package-script checks, authorization, runner admission, sandbox setup, and receipts remain in the trusted executors. Regenerate the protected workflow and its runner-isolation digests. Add golden and executor-path coverage for valid and malformed shapes, nested binding and order, strict service booleans, and level-wrapper rejection. Backfill ADR 0210 with #1921 merge metadata; the retained PR record does not prove its pre-merge assertion result.
