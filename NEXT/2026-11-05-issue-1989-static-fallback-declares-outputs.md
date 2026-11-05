---
date: 2026-11-05
issue: 1989
impact: patch
title: The static-fallback artifact caller declares every preflight output it reads
---

Generated build-artifact callers on the static per-lane fallback now pass actionlint: their `artifact-preflight` job declares the six sharing-selection outputs (`build-artifact-producer-plan`, `-consumer-plans`, `-profile`, `-export-paths`, `-runner-image`, `-package-manager-version`) that participant, producer, and aggregate jobs read, and writes its outputs through one redirect (ShellCheck SC2129).

Each newly declared output is empty, the exact value the formerly undeclared reads resolved to, so runtime behavior is unchanged; sharing is never selected on that path. Declaring them was chosen over omitting the reads because omitted `node-ci.yml` inputs take non-empty defaults (`'{}'`, `'[]'`), which would have changed the values participants receive. The share-eligible and level-only renders are byte-identical. A test pins that every `needs.<job>.outputs.<name>` read in the generated artifact goldens names an output its job declares (#1989).
