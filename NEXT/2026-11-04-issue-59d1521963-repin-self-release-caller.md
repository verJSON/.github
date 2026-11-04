---
date: 2026-11-04
id: 59d1521963
impact: patch
title: Repin this repository's release caller at the verjson.com release identity
---
This repository's own `release.yml` is regenerated at contract
`3619ce18de265db603b12e7a97df7c36c41d1cac`, so its next release commits and
tags as `release@verjson.com`. The repin also brings the caller's `verify`
job under the adopters' current shape: a reserved-path refusal before each
contract checkout, private registry packages acquired through the pinned
`npm-private-acquisition.sh` before a credential-free `npm ci`, and the
`CI_PACKAGE_RETENTION_KEEP` value validated before release verification.
The caller's two exemptions are retired because it now meets their rules:
`fixed-path-checkout-refusal.test.py` no longer exempts its
`.changelog-contract` checkout, and `settable-value-guards.test.py` no
longer exempts it from the locale rule, since its digest guards now
enumerate their characters. That test's reviewed inventory grows by the new
retention guard, to 516.

The runner-isolation scanner no longer misreads a bash `=~` regex
operand: the `|` and parentheses of its regex were read as a pipeline and
a subshell, so the generated retention guard was refused as an unresolved
program. In bash text only, the operand after an unquoted, unescaped `=~`
now folds into one word when it holds parentheses; every separator around
it, including a quoted or escaped `[[` and a pipeline after `]]`, is read
as before, and command and process substitutions in it are still traced. The three `release.yml` relay-backlog digests are refreshed;
their recorded reasons are unchanged.
