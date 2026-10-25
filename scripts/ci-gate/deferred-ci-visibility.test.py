#!/usr/bin/env python3
"""A deferred org CI run must not look like an executed, passing run.

`build-test` reports `if: always()` so a Renovate PR held on
`renovate/stability-days` never wedges on a permanently unsatisfied required
context (Verjson/.github#191). The cost is that a deferred run skips every
execution step and still concludes `success` on the required `ci / build-test`
context. `Verjson/verjson-cli#251` is the harm: a repository-local lockfile
guard never ran and a deliberate version hold was violated behind a green
required check.

ADR 0178 keeps the required context satisfying and adds a separate `deferred-ci`
job that exists only on a defer and terminates unsuccessfully, so the deferral is
a first-class non-SUCCESS entry in `statusCheckRollup` rather than an annotation
only an opt-in script reads. These tests pin both halves: the required context
still reports, and the deferral is distinguishable by check conclusion alone.
"""
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ROOT / ".github/workflows/node-ci.yml",
    ROOT / ".github/workflows/node-ci-protected.yml",
)
DEFER_CONDITION = (
    "needs.eligibility.outputs.should-run == 'false' && "
    "(inputs.build-artifact-role == 'level' || inputs.build-artifact-role == 'consumer' "
    "|| inputs.build-artifact-role == 'producer')"
)
EXECUTION_GUARD = "needs.eligibility.outputs.should-run != 'false'"


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class DeferredCiVisibilityTest(unittest.TestCase):
    def test_build_test_still_reports_so_a_held_renovate_pr_never_wedges(self):
        for path in WORKFLOWS:
            with self.subTest(workflow=path.name):
                build_test = load(path)["jobs"]["build-test"]
                self.assertEqual(
                    "always() && (inputs.build-artifact-role == 'level' || "
                    "inputs.build-artifact-role == 'consumer' || inputs.build-artifact-role == 'producer')",
                    build_test["if"],
                )
                self.assertNotIn("if", load(path)["jobs"]["eligibility"])

    def test_a_deferred_run_executes_nothing_but_the_deferral_report(self):
        for path in WORKFLOWS:
            with self.subTest(workflow=path.name):
                steps = load(path)["jobs"]["build-test"]["steps"]
                unguarded = [step for step in steps
                             if EXECUTION_GUARD not in str(step.get("if", ""))]
                self.assertEqual(
                    ["Report deferred CI"],
                    [step.get("name") for step in unguarded],
                    "a deferred build-test must execute nothing but its own notice; "
                    "any other unguarded step changes what vacuity means",
                )

    def test_a_defer_publishes_a_separate_non_success_check(self):
        for path in WORKFLOWS:
            with self.subTest(workflow=path.name):
                jobs = load(path)["jobs"]
                self.assertIn(
                    "deferred-ci", sorted(jobs),
                    "a deferred run must report a separate check, or it is "
                    "indistinguishable from an executed, passing run",
                )
                deferred = jobs["deferred-ci"]
                self.assertEqual(DEFER_CONDITION, deferred["if"])
                self.assertIn("eligibility", str(deferred["needs"]))

    def test_the_separate_check_terminates_unsuccessfully(self):
        for path in WORKFLOWS:
            with self.subTest(workflow=path.name):
                jobs = load(path)["jobs"]
                self.assertIn("deferred-ci", sorted(jobs))
                steps = jobs["deferred-ci"]["steps"]
                self.assertTrue(steps, "the deferral check must execute a step")
                for step in steps:
                    self.assertNotIn(
                        "continue-on-error", step,
                        "continue-on-error would restore the SUCCESS conclusion "
                        "this job exists to withhold",
                    )
                completed = subprocess.run(
                    ["/usr/bin/bash", "-eo", "pipefail", "-c", steps[-1]["run"]],
                    capture_output=True, text=True,
                )
                self.assertNotEqual(
                    0, completed.returncode,
                    "the deferral job must fail so its check conclusion is not "
                    "SUCCESS, NEUTRAL, or SKIPPED",
                )
                self.assertIn("deferred", (completed.stdout + completed.stderr).lower())

    def test_the_deferral_concludes_even_when_the_self_hosted_pool_is_down(self):
        for path in WORKFLOWS:
            with self.subTest(workflow=path.name):
                deferred = load(path)["jobs"]["deferred-ci"]
                self.assertEqual(
                    "ubuntu-24.04", deferred["runs-on"],
                    "a saturated or offline self-hosted lane would leave this "
                    "check PENDING, which a rollup assertion cannot distinguish "
                    "from a queued run; the deferral must always conclude",
                )

    def test_the_deferral_is_named_in_the_rollup_not_only_in_an_annotation(self):
        for path in WORKFLOWS:
            with self.subTest(workflow=path.name):
                jobs = load(path)["jobs"]
                notice = next(step for step in jobs["build-test"]["steps"]
                              if step.get("name") == "Report deferred CI")
                self.assertIn("title=CI deferred", notice["run"])
                self.assertNotEqual(
                    jobs["deferred-ci"]["if"], jobs["build-test"].get("if"),
                    "the separate check must be conditional on the defer alone",
                )


if __name__ == "__main__":
    unittest.main()
