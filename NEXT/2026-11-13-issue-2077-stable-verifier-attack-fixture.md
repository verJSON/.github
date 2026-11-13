---
date: 2026-11-13
issue: 2077
impact: patch
title: Coordinate verifier child attack fixture observation
---
The same-UID compatibility sandbox attack fixture waits for its attacker thread to observe the exact verifier child before completing, replacing a short scheduling window that intermittently failed unrelated platform CI runs. The attacker still mutates verifier paths and attempts the child stdin injection. Fixes #2077.
