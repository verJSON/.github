---
date: 2026-11-02
id: 20261102T034815Z
impact: patch
title: Retain experimental supervisor lifecycle ownership
---

Keep the experimental supervisor pipes, pidfd, and reap result in native-owned
slots, retaining the owner when process-group cleanup is unverified. Part of #1966.
