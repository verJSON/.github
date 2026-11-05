---
date: 2026-11-04
id: 20261104T225627Z
refs: 1995
impact: patch
title: Reach the two deferred guards in the generated container contracts
---
The two guards deferred from #1995 are now reached: the credential guard in the contract test that `gen-container-candidate.sh` generates, and the reconciliation-allowlist guard in the one that `gen-container-release.sh` generates. Both sit behind the caller's whole-file digest pin, so no fixture reached them before, and deleting either left every test green. Their fixtures now re-pin that digest to the mutated caller, as regenerating at a contract that emitted that caller would, and assert each guard's own message. New environment and registry-namespace cases reach the credential guard's other two alternatives.

The other guards in those two generated tests that no test reached are now reached too:

- Candidate: the validator and destination-helper digest checks, the private-package token-routing check, each part of the check that pull-request validation gets no package token, secrets, or package reads, both halves of the public-only package check, and the missing-config check.
- Release: the hook's symlink check, the check that each allowlisted target exists, the six pinned-helper digest checks, and the allowlist guard's whole-line match, which a re-indented allowlist now exercises.
