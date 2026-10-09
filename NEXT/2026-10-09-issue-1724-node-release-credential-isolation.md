---
date: 2026-10-09
issue: 1724
impact: patch
title: Isolate Node release registry credentials from lifecycle scripts
---

The canonical Node release workflow acquires locked packages with lifecycle scripts disabled in a credentialed Actions step, then restores the locked tree with `npm ci --prefer-offline` in a separate tokenless step. Ending the first step prevents lifecycle code from reading its shell's initial token through `/proc`; an offline contract fixture compares hook counts and per-package order with a normal install, inspects ancestor environments, and covers empty dependency trees.
