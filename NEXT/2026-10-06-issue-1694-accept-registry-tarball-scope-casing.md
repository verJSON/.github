---
date: 2026-10-06
issue: 1694
title: Accept registry tarball scope casing
impact: patch
---

Accept GitHub Packages tarball URLs whose ASCII scope or package casing differs from the exact approved lowercase lock identity. Keep the registry-issued URL and integrity intact while preserving approval, URL structure, lock identity, and digest checks.
