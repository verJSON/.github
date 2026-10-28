---
date: 2026-10-28
id: 20261028T043832Z
impact: patch
title: Use Node 26 in deployment workflows
---
The candidate publish, deployment, and Renovate compatibility workflows now use Node 26; the deployment contract assertion and two runner-isolation receipts match the updated workflow contents.

This changes no runner selector or pending-risk reason. The deployment jobs remain part of the persistent-runner backlog tracked by #1831.
