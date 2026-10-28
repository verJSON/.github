---
date: 2026-10-27
id: 20261027T101500Z
refs: 1831
impact: patch
title: Keep public workflow input validation locale-stable
---
The public `verjson-ci` adapter now checks OCI image references with explicit
ASCII character sets while preserving its byte-verified pinned source fixture.
The generated AI review caller accepts only lowercase ASCII hexadecimal
contract pins. Regression tests exercise both boundaries under `en_US.UTF-8`.
The capability-floor observation's pending relay digest is rebound to the
updated generator bytes in its sparse checkout; the row's scope and justification
remain unchanged.
