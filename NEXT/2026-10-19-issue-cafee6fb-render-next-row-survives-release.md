---
date: 2026-10-19
id: cafee6fb
impact: patch
title: Keep actions-ci green on main after a release empties NEXT/
---

The `docs` actions-ci row that smoke-renders `NEXT/` now passes when a dispatched release has consumed every fragment, instead of failing every release commit on `main` since v3.0.1 with `changelog: no unreleased fragments` and masking unrelated failures behind it. Every other renderer failure still fails the row with the renderer's stderr.

`scripts/render-next-check.sh` excuses a non-zero exit only when `NEXT/` is a directory holding no fragment besides `README.md` and `0000-archive.md` and the renderer printed exactly that diagnostic with the status `changelog.py` returns for it (1), the tree-decided distinction the generated adopter contract test already draws (#399). A missing `NEXT/`, a malformed or component-only fragment, a renderer crash before or after the diagnostic, and any other exit status all still fail, keeping the renderer's status; `scripts/ci-gate/render-next-check.test.sh` executes the registered row against each. `changelog.py render-next` semantics are unchanged.
