---
date: 2026-10-16
id: 20261016T014014Z
impact: patch
title: Pin the generator sandbox to the interpreter's runtime library directory and record pending rotation receipts
---

A test now gives the sandboxed historical generator a decoy build-time `LIBDIR`. It
asserts that the runtime prefix (`sys.base_prefix/lib`) is the only library directory
used. Without it, a switch to the build-time path could pass review while it breaks
self-hosted runners.

ADRs 0151 and 0153 now record both #1828 rotations to `c78fcd4e`, their snapshots, and
the exact `verify-run` commands. Whoever sees the first consumer run can then complete
the outstanding live receipts (Part of #1423).
