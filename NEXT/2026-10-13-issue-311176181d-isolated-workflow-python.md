---
date: 2026-10-13
id: 311176181d
impact: patch
title: Start every workflow's inline Python interpreters isolated
---

Every workflow, and every workflow the generators emit for adopters, now starts its inline Python interpreters with `-I`, so helpers resolve only the modules they name explicitly. Scripts that import a sibling add their own directory, the hosted-selector policy takes its pinned parser directory as an argument, and the release version guard requests UTF-8 on the command line.

`scripts/ci-gate/workflow-isolated-python.test.py` (renamed from the Node CI contract) enforces this across all workflows and rendered generator output, requires every generator to be classified, and checks that each script a workflow starts isolated still imports. This completes #1752 (the existing #1752 fragment records the index sync, so this entry carries its own identity). ADR 0228 records the decision; adopters pick it up on their next canonical repin, and this repository's pinned release caller does at its own.
