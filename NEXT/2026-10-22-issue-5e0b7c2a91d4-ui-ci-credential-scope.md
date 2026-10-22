---
date: 2026-10-22
id: 5e0b7c2a91d4
impact: minor
title: Scope ui-ci package and Git tokens to the install steps outside trusted refs
---

`ui-ci` no longer exports its package and Git tokens job-wide on pull requests and non-default-branch events: the tokens are step-scoped to the install steps, the checkout no longer persists its token, and the new `credentialless-untrusted` input withholds every token (always so for forks). Push and `workflow_dispatch` on the default branch keep the previous job-wide behavior. This tightens prior behavior for lint, typecheck, test, and build commands that read the token; install commands and lifecycle scripts still see it. Recorded in ADR 0239; part of #1869.
