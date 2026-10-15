#!/usr/bin/env python3
"""Admit an organization required-workflow ruleset's live revision as a predecessor.

A rotation has to recognize the revision it replaces, both to accept the live
preimage and to restore it after a failed write. A committed record of that
revision goes stale at every rotation, because a rotation's target is always the
merge commit of a later change (#1824). The live ruleset is the only record that
cannot lag, so the predecessor is read from it and admitted only when it is a
canonical revision (ADR 0153 2026-10-15 amendment).
"""

FORWARD = frozenset({"ahead", "identical"})


def admit_predecessor(
        previous_sha, target_sha, *, compare, read_file, regenerate, workflow_path, require):
    require(compare(previous_sha, target_sha) in FORWARD,
            "live required-workflow revision is not an ancestor of the rotation target")
