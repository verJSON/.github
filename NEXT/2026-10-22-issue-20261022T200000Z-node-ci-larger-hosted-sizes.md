---
date: 2026-10-22
id: 20261022T200000Z
impact: minor
title: Admit vetted larger hosted runner sizes in the node-ci secretless lane guard
---

The secretless hosted-lane guard now accepts `ubuntu-<latest|YY.MM>-<4|8|16|32|64>-cores` in `CI_LANE_UNTRUSTED`, for callers outside Verjson, so an adopter can trade per-minute rate for wall time. Arm, gpu, and other suffixes stay refused. Recorded in ADR 0238, which also records what node-ci leaves caller-side. Part of #1869.
