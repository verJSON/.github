---
date: 2026-10-27
id: b17f30e92a4c
impact: patch
title: Split public runner-isolation probes for CI
---
Actions CI runs the public runner-isolation probes in two bounded shards while
the test keeps its full-run entrypoint. The partition contract checks the
runner's actual selection and requires every check-bearing statement exactly
once. The release verifier's pending digest is refreshed against the scanner.
