#!/usr/bin/env python3
"""Pin ADR 0238's 2026-10-22 amendment: the reusable CI workflows declare no concurrency.

A called workflow cannot tell two calls in one run apart, and an empty group is a
startup failure, so any group it declares is shared by sibling lanes in the same
caller run and would serialize or cancel them. Cancelling superseded pull-request
runs belongs to the caller (docs/node-workflows.md, "Concurrency belongs to the
caller"). Revisit this test only together with that ADR section.
"""
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
REUSABLE_CI = (
    "node-ci.yml",
    "node-ci-protected.yml",
    "helm-ci.yml",
    "pulumi-ci.yml",
    "ui-ci.yml",
)
DOC = ROOT / "docs" / "node-workflows.md"


def load(name):
    return yaml.load((ROOT / ".github" / "workflows" / name).read_text(), Loader=yaml.BaseLoader)


class ReusableCiConcurrencyTest(unittest.TestCase):
    def test_no_workflow_level_group(self):
        for name in REUSABLE_CI:
            with self.subTest(workflow=name):
                self.assertNotIn("concurrency", load(name))

    def test_no_job_level_group(self):
        for name in REUSABLE_CI:
            for job_id, job in load(name)["jobs"].items():
                with self.subTest(workflow=name, job=job_id):
                    self.assertNotIn("concurrency", job)

    def test_documented_caller_snippet_never_cancels_trunk_or_merge_queue(self):
        section = DOC.read_text().split("## Concurrency belongs to the caller", 1)[1]
        snippet = re.search(r"```yaml\n(.*?)```", section, re.S).group(1)
        concurrency = yaml.safe_load(snippet)["concurrency"]
        self.assertEqual(
            concurrency["cancel-in-progress"],
            "${{ github.event_name == 'pull_request' }}",
        )
        self.assertIn("github.event.pull_request.number", concurrency["group"])
        self.assertIn("github.run_id", concurrency["group"])


if __name__ == "__main__":
    unittest.main()
