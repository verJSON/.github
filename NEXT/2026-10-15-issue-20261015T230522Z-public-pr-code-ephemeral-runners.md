---
date: 2026-10-15
id: 20261015T230522Z
impact: patch
title: Keep public pull-request code off persistent self-hosted runners
---
`actions-ci` now runs every job on the ephemeral untrusted lane
(`CI_LANE_UNTRUSTED` with a hosted tail and no persistent fallback). Before
this change, code from any pull request to this public repository, including
forks, ran on the shared DigitalOcean `general` pool that also runs privileged
jobs. A new contract check, `scripts/ci-gate/public-pr-runner-isolation.py`,
fails if a job that a `pull_request` event can reach could land on a persistent
runner through a variable fallback or a caller-supplied input
([ADR 0235](../docs/decisions/0235-public-pull-request-code-uses-ephemeral-runners/README.md)).

`scripts/ci-gate/public-pr-runner-isolation.pending.tsv` ratchets
`actionlint.yml` and `container-candidate.yml` as pending.
Their selectors are pinned in `hosted-selector-policy.py` and change after #1826;
#1831 tracks them, the adopter reusable workflows, and live lane enforcement.
