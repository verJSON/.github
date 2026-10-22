---
date: 2026-10-22
id: 4d8e1b7c6a30
impact: patch
title: Record why concurrency stays caller-side and pin it
---

A probe established that a reusable workflow cannot declare a per-call concurrency group: an empty group is a startup failure and nothing distinguishes two calls in one run, so sibling lanes would cancel each other. `docs/node-workflows.md` now carries the caller snippet that cancels only superseded `pull_request` runs, ADR 0238 is amended, and `reusable-ci-no-concurrency.test.py` pins that the reusable CI workflows declare none. No workflow behavior changes. Part of #1869.
