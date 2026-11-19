---
date: 2026-11-19
id: 20261119T051256Z
impact: patch
title: Archive the delivery ARB for the Renovate bump classifier fix
---

ADR 0288 archives the ARB approval of .github#2255, which stops the
agent-delivery claim worker classifying v2+ minor and patch Renovate titles as
majors, so Gate R can cite it.
