---
date: 2026-10-14
issue: 1776
impact: patch
title: Accept the withdrawn organization merge key in privileged merge conformance
---

The scheduled privileged merge fleet conformance audit accepts a withdrawn organization copy of `MERGE_APP_PRIVATE_KEY` as the conforming custody state and then requires each privileged merge consumer to hold the key in its own `merge-app` environment; that environment's main-only admission stays the job of `app-key-environment.yml`. Any other read failure, including an organization inventory that cannot corroborate the absence, still fails closed (#1776, ADR 0166).

A `Not Found` for the organization secret counts as withdrawal only when the same token reads a complete, well-formed organization secret inventory that omits the name, because GitHub also answers 404 for a resource the caller cannot see. With the organization copy withdrawn, a missing environment key names the `merge-app` environment rather than asking for the organization copy that ADR 0166 removed.

`config/org-actions-secret-policy.json` and `config/app-role-custody-inventory.json` now record the review and merge organization copies as withdrawn, as they already did for the release copy. That clears the secret-scope audit's `absent_live` drift for both keys; its remaining live findings are unrelated owner decisions tracked in #1385.
