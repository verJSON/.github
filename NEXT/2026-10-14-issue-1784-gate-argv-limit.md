---
date: 2026-10-14
issue: 1784
impact: patch
title: Keep admission-gate payloads out of jq's argument list
---
`scripts/assert-mergeable-head.sh` no longer passes API payloads to `jq` as arguments. Responses and the values derived from them now go through a scratch directory that the script's single EXIT trap removes, and `jq` reads them with `--slurpfile` or on stdin. Run objects are kept whole: once every payload travels by file, a field allowlist would buy nothing and would read any later-used field as `null`. On verJSON/AiB#347, eleven runs exceeded Linux's 128 KiB per-argument limit, so the gate exited `1` with `Argument list too long`. With this change it evaluates the head and prints `true`.

Gate rules, conclusion sets, and exit codes do not change. All 131 existing fixtures produce byte-identical output. A new large-payload test checks that oversized inputs reach the same verdicts as their small equivalents. ADR 0229 is amended with this evidence.
