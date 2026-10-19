---
date: 2026-10-19
id: c0a7d1e9
impact: patch
title: Run actions-ci for every data input its registered tests read
---

`actions-ci` now runs for pull requests and pushes that change `contracts/**`. The container deployment contract test reads the pinned deployment CLI package there, but a Renovate bump of that package never triggered the suite, so it could neither run the test nor report the required `shell-tests` check. The same gap existed for other tracked inputs that registered tests read: `config/`, the root `package.json`, `package-lock.json` and `bin/`, `renovate/`, `.github/required-check-contract.json`, `.github/CODEOWNERS`, and `.telemetry/`. A change to any of them alone was refused at admission for the same missing check. All are now path triggers. `trigger-scope.test.sh` fails if any tracked file under them is uncovered.
