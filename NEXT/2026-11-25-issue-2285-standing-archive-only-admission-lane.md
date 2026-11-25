---
date: 2026-11-25
issue: 2285
impact: minor
title: Admit archive-only verJSON/.github pull requests without a citation
---

Gate R's arb-consensus citation step now has a standing archive-only admission
lane (ADR 0309, pending owner ratification). A verJSON/.github pull request
whose exact head only adds ADR chair records with their READMEs, regenerates the
decision index and adds NEXT fragments passes without a citation once each added
record parses as a cited archive would, and a notice names the lane. Any other
pull request still cites. An unreadable file list or record leaves the step
unanswered. The exact-head independent-review receipt is still required.
