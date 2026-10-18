#!/usr/bin/env python3
"""The runner-isolation contract is part of the required `shell-tests` context.

ADR 0235's checker ran only in its own `runner-isolation` workflow, which no
ruleset requires, so a red run was advisory. The round-4 review of #1827
asked for it inside the required aggregate. These tests execute the live
`shell-tests` aggregate step with stubbed `needs` results, so a check that
reads the wrong result, or tolerates a skipped or failed isolation run, fails.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ACTIONS_CI = ROOT / ".github" / "workflows" / "actions-ci.yml"
UNTRUSTED_LANE = "${{ fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]') }}"
NEEDS_RESULT = re.compile(r"^\$\{\{ needs\.([a-z-]+)\.result \}\}$")
HEAVY_OUTPUT = "${{ needs.change-scope.outputs.heavy }}"
GREEN = {
    "change-scope": "success",
    "shell-test-groups": "success",
    "docs-contracts": "success",
    "hosted-compatibility-tests": "success",
    "adr-number-collision": "success",
    "runner-isolation": "success",
}


def jobs() -> dict:
    return yaml.load(ACTIONS_CI.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["jobs"]


def run_aggregate(results: dict[str, str], heavy: str = "true") -> subprocess.CompletedProcess:
    """Run the aggregate step with each `needs` expression replaced by a stub.
    An env value this harness cannot bind fails the test rather than passing
    an empty string through."""
    step = jobs()["shell-tests"]["steps"][0]
    env = {"PATH": "/usr/bin:/bin"}
    for name, value in step["env"].items():
        match = NEEDS_RESULT.match(value)
        if value == HEAVY_OUTPUT:
            env[name] = heavy
        elif match and match.group(1) in results:
            env[name] = results[match.group(1)]
        else:
            raise AssertionError(f"shell-tests env {name}={value!r} is not a needs result this test stubs")
    return subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)


class RunnerIsolationRequiredTests(unittest.TestCase):
    def test_aggregate_passes_when_every_need_succeeds(self):
        # Without this, every red case below could pass vacuously.
        self.assertEqual(run_aggregate(GREEN).returncode, 0)

    def test_aggregate_waits_for_and_reads_the_runner_isolation_job(self):
        aggregate = jobs()["shell-tests"]
        self.assertIn("runner-isolation", aggregate["needs"])
        self.assertIn("${{ needs.runner-isolation.result }}", aggregate["steps"][0]["env"].values())

    def test_aggregate_fails_unless_runner_isolation_succeeded(self):
        for heavy in ("true", "false"):
            groups = "success" if heavy == "true" else "skipped"
            for result in ("failure", "cancelled", "skipped", ""):
                with self.subTest(heavy=heavy, result=result):
                    completed = run_aggregate(
                        {
                            **GREEN,
                            "shell-test-groups": groups,
                            "hosted-compatibility-tests": groups,
                            "runner-isolation": result,
                        },
                        heavy,
                    )
                    self.assertEqual(completed.returncode, 1, completed.stdout + completed.stderr)
                    self.assertIn("runner-isolation", completed.stdout + completed.stderr)

    def test_actions_ci_job_runs_the_checker_on_every_run_on_the_untrusted_lane(self):
        job = jobs()["runner-isolation"]
        self.assertNotIn("if", job)
        self.assertNotIn("needs", job)
        self.assertEqual(job["runs-on"], UNTRUSTED_LANE)
        runs = "\n".join(step.get("run", "") for step in job["steps"])
        self.assertIn(
            "python3 -I scripts/ci-gate/public-pr-runner-isolation.py \\\n"
            "  --pending scripts/ci-gate/public-pr-runner-isolation.pending.tsv \\\n"
            "  --reviewed scripts/ci-gate/public-pr-runner-isolation.reviewed.tsv \\\n"
            "  .github/workflows",
            runs,
        )


if __name__ == "__main__":
    unittest.main()
