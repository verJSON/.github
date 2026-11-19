---
date: 2026-11-19
id: 20261119T054825Z
impact: patch
title: Decide the fleet dispatch credential and its custody
---

ADR 0289 records the ARB decision for #2249: the org fleet dispatcher uses a
dedicated App installed only on verjson-cli-cloud with actions: write, keeps
its key in a main-only .github environment, and dispatches two fixed fleet
workflows through workflow_dispatch. No App or workflow is created yet.
