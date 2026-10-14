---
date: 2026-10-14
id: 20261014T185454Z
refs: 1696
impact: minor
title: Let a review caller bootstrap move canonical reusable CI pins
---

The authorization arm's stale review caller bootstrap now also admits a consumer's canonical `node-ci.yml` and `actionlint.yml` caller pins moving to the bootstrap's contract revision, together with their contract-test mirrors, when nothing else in those workflow files changes ([ADR 0231](docs/decisions/0231-bootstrap-moves-reusable-ci-pins/README.md), extending ADR 0227).

verjson-cli could not recover otherwise: its stale review caller refuses the CI pin repair (verjson-cli#302), and its stale `node-ci.yml@ab93610d` fails the bootstrap's required `ci / build-test` (verjson-cli#318). The proof is a line-by-line diff. Every changed line must be a job-level allowlisted `Verjson/.github` reusable workflow reference, or a `#`-led line, whose only change is its 40-hex revision moving to the contract. A caller containing any line break other than LF or CRLF is refused, each moved revision must be an ancestor of the contract, and release and deployment workflows stay off the closed allowlist. The lane takes effect only after organization ruleset 20722935 is rotated to the merge SHA.
