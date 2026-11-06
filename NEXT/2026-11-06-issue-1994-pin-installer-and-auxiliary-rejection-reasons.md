---
date: 2026-11-06
issue: 1994
impact: patch
title: Pin the rejection reason in the installer digest and secretless-transfer negative tests
---
Eight negative tests now assert why they were rejected, not only that they were:
- the actionlint and ShellCheck digest-mismatch cases in `scripts/actionlint-install.test.sh`, which run under `LC_ALL=C` so `sha256sum`'s `FAILED` line is stable;
- six cases in `scripts/ci-gate/node-ci-secretless-transfer.test.sh`: the run, future-attempt, lock, digest and auxiliary-repository identity mismatches, and the corrupt payload.

This exposed one real gap. The digest case appended a byte, so the size check rejected it first, and deleting the secretless cache digest check from `node-ci.yml` left the whole suite green. The case now overwrites a byte in place, keeping the size, and fails without that check. Separately, injecting an unrelated earlier failure ahead of each installer's intact digest check, or ahead of the identity guard, left the old cases passing; the new cases fail and print the actual cause (#1994).
