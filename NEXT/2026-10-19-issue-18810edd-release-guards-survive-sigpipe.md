---
date: 2026-10-19
id: 18810edd
impact: patch
title: Close the generated release credential guards for large job blocks and every context spelling
---

The generated adopter contract test now rejects a release caller whose snapshot job forwards a credential, or whose `release-artifact` build job references a secret, however large that job block is. The snapshot job is also scanned for every spelling of the token and secrets contexts. Both guards used to pipe the job block from `sed` into `grep -q` under `pipefail`. When the forbidden reference came early in a block bigger than a pipe, `grep` exited on its first match, `sed` died of SIGPIPE, and the negated pipeline read status 141 as "no match". So the snapshot caller passed outright. The build job was caught only by the later Python `#1712` scan, not by its own guard (ADR 0099's 2026-10-19 amendment).

Each guard now captures the comment-stripped block first and greps a here-string, so no writer can be killed. `scripts/ci-gate/changelog-caller-contract.test.sh` adds one case per guard. Each case puts the reference before a MiB of ordinary YAML and asserts the guard's own message. Both cases failed against the previous generator. Adopters pick the fix up when they regenerate at a contract SHA that includes it.

The snapshot guard's own pattern was a list of names, independent of size. It missed index-syntax context access, a token expression after a `#` inside a quoted value, and any secret name the list did not include. Every job that calls `changelog-release.yml` now goes through the same quote-aware expression scan as the build job, and that scan rejects any read of the token or secrets context. A `#` inside a quoted non-credential value stays legal. New cases cover each spelling and that acceptance.
