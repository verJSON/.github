---
date: 2026-10-22
id: 20261022T230000Z
impact: minor
title: Stop running credentialed Pulumi preview on unreviewed same-repository pull requests
---

`pulumi-ci` now runs the credentialed preview only on a trusted ref (push or `workflow_dispatch` on the default branch) or, for a same-repository pull request, inside the caller's reviewed `preview-environment`; otherwise it runs the credential-free `validate` gate only. This changes prior behavior: `workflow_dispatch` on the default branch now gets a credentialed preview (a widening), and same-repository PRs and non-default-branch pushes no longer get a credentialed preview by default. Validation and preview check out the event's immutable SHA, and the reviewed-PR route needs the new optional `environment-read-token` secret so admission can verify the environment has required reviewers. Recorded in ADR 0239 with migration steps; part of #1869.
