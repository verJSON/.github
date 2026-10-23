#!/usr/bin/env python3
"""Generate a consumer's Node CI caller workflow.

    gen-node-ci-caller.py <40-hex-contract-sha> [--levels ci-levels.json] > .github/workflows/ci.yml

Without --levels the output is the single-job `ci` caller, so the required
context stays `ci / build-test`. With --levels the caller has one `node-ci` call
per declared level plus the fixed `test-levels` aggregate job (ADR 0238,
2026-10-22 test-levels amendment). Invalid levels exit 1 with one
`code path: message` line per error and write nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ci_levels  # noqa: E402

NODE_CI = ROOT / ".github/workflows/node-ci.yml"


def known_node_ci_inputs() -> dict[str, str]:
    workflow = yaml.safe_load(NODE_CI.read_text())
    return {name: spec["type"] for name, spec in workflow[True]["workflow_call"]["inputs"].items()}


def known_node_ci_secrets() -> frozenset[str]:
    workflow = yaml.safe_load(NODE_CI.read_text())
    return frozenset(workflow[True]["workflow_call"]["secrets"])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sha")
    parser.add_argument("--levels", type=Path)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        parser.error("sha must be a 40-hex commit")
    if args.levels is None:
        sys.stdout.write(ci_levels.render_caller(ci_levels.DEFAULT_PLAN, args.sha, ""))
        return 0
    try:
        text = args.levels.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"unreadable_file $: {exc}", file=sys.stderr)
        return 1
    try:
        plan = ci_levels.parse_levels(text, known_node_ci_inputs(), known_node_ci_secrets())
    except ci_levels.LevelsError as exc:
        for error in exc.errors:
            print(f"{error.code} {error.path}: {error.message}", file=sys.stderr)
        return 1
    sys.stdout.write(ci_levels.render_caller(plan, args.sha, f" --levels {json.dumps(args.levels.name)}"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
