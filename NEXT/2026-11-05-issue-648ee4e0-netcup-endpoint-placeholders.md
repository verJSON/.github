---
date: 2026-11-05
id: 648ee4e0
impact: patch
title: Replace the Netcup stack's concrete endpoints with placeholders
---

The public Nuremberg service reference (`docs/infrastructure/netcup-nuremberg.md`) no longer publishes the stack's concrete hostnames or the GitLab SSH port. It uses placeholders and points to the private operational record in `verJSON/verjson-cli` (`docs/operations/netcup-nuremberg.md`), which the stack's renderer lives beside.
