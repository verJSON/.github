#!/usr/bin/env python3
"""Execute node-ci's secretless hosted-lane routing decision against stubbed inputs.

Both secretless modes run a lifecycle rebuild and candidate script sandbox that
exit unless the runner is GitHub-hosted, so `build-test` takes the hosted
untrusted lane ahead of a caller's `runner` input. `eligibility` keeps the
caller's (self-hosted) route and must refuse a secretless call early, before
dependency acquisition, only when that hosted lane is not actually hosted.

Each scenario evaluates every eligibility guard step's `if:` and `env:` with the
same expression subset GitHub Actions uses, then runs the step's `run:` script,
so the verdict is the workflow's own, not a re-implementation of it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ROOT / ".github/workflows/node-ci.yml",
    ROOT / ".github/workflows/node-ci-protected.yml",
)

EVALUATOR = r"""
const vm = require('node:vm');
const [, , raw, contextJson] = process.argv;
const context = JSON.parse(contextJson);
const body = raw.trim().replace(/^\$\{\{/, '').replace(/\}\}$/, '')
  .replace(/inputs\.([A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)+)/g, "inputs['$1']");
const sandbox = vm.createContext(Object.assign(Object.create(null), context, {
  fromJSON: (value) => JSON.parse(value),
}));
const resolved = vm.runInContext(`(${body})`, sandbox, { timeout: 5000 });
process.stdout.write(JSON.stringify(resolved === undefined ? null : resolved));
"""


def evaluate(expression: str, context: dict) -> object:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(EVALUATOR)
        script = handle.name
    try:
        result = subprocess.run(
            ["node", script, expression, json.dumps(context)],
            capture_output=True, text=True, check=True,
        )
    finally:
        os.unlink(script)
    return json.loads(result.stdout)


def interpolate(value: object, context: dict) -> str:
    text = str(value)
    if text.startswith("${{") and text.endswith("}}"):
        resolved = evaluate(text, context)
        return "" if resolved is None else str(resolved)
    return text


def eligibility_guards(workflow: Path) -> list[dict]:
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    steps = document["jobs"]["eligibility"]["steps"]
    # The stability-days `check` step calls the GitHub API; every step before
    # it is a local guard that must decide from inputs and variables alone.
    guards = []
    for step in steps:
        if step.get("id") == "check":
            break
        guards.append(step)
    return guards


def run_eligibility_guards(workflow: Path, scenario: dict) -> tuple[int, str]:
    context = {
        "inputs": {
            "secretless-pr": scenario["mode"] == "pr",
            "secretless-trusted-ref": scenario["mode"] == "trusted-ref",
            "secretless-compatibility-ranges": scenario.get("ranges", ""),
        },
        "runner": {"environment": scenario["eligibility_runner"]},
        "vars": {"CI_LANE_UNTRUSTED": scenario["untrusted_lane"]},
        "github": {"repository_owner": "Verjson"},
    }
    for step in eligibility_guards(workflow):
        condition = step.get("if")
        if condition is not None and not evaluate(str(condition), context):
            continue
        env = {"PATH": os.environ["PATH"]}
        for name, value in (step.get("env") or {}).items():
            env[name] = interpolate(value, context)
        result = subprocess.run(
            ["bash", "-c", step["run"]], env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return result.returncode, result.stdout + result.stderr
    return 0, ""


COMPAT_RANGES = '{"package":"@verjson/cli-projects","ranges":[">=1.0.0 <2.0.0"],"script":"compat"}'

SCENARIOS = (
    {
        "label": "trusted-ref compatibility on a self-hosted eligibility runner proceeds when the build lane is hosted",
        "mode": "trusted-ref", "ranges": COMPAT_RANGES,
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04"]',
        "admitted": True,
    },
)


def main() -> int:
    failures = 0
    for workflow in WORKFLOWS:
        for scenario in SCENARIOS:
            status, output = run_eligibility_guards(workflow, scenario)
            label = f"{workflow.name}: {scenario['label']}"
            if (status == 0) == scenario["admitted"]:
                print(f"ok - {label}")
            else:
                failures += 1
                print(f"not ok - {label} (exit {status}: {output.strip()})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
