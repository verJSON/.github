---
date: 2026-11-12
id: 20261112T182000Z
refs: 2031
impact: minor
title: Bind merge-admission to the AI review App in the ruleset conformance policy
---

The organization ruleset conformance policy now binds the `merge-admission` required
context to the AI review App (4528902) while every other context stays bound to GitHub
Actions (15368), and it carries the reviewed bypass contract for the not-yet-created
`merge-admission-required` ruleset: OrganizationAdmin, 4693283, 4583107, and Renovate
2740, all `always`, never 4528902.

`scripts/org-ruleset-conformance.py` fails when `merge-admission` is unbound or bound to
any other App, refuses a policy that binds it to the declared producer 15368, and refuses
a policy whose `merge-admission-required` contract is missing or lists the producer App
(ARB-2031-2026-11-12 C6 and C10 (i); ADR 0260 Decision 2).

The same audit now issues the two C10 receipts. Before the ruleset exists it fails unless
the organization variable `AI_REVIEW_APP_ID` names the bound App and prints a
`pre-creation` receipt that reports the ruleset as absent; once the ruleset exists, even
disabled, it also requires the ruleset to require `merge-admission` bound to that App and
prints a `full` receipt naming the ruleset id and enforcement. The pre-creation receipt
gates creation; the full receipt gates activation and each enrollment edit. The variable's
value is compared and never printed.
