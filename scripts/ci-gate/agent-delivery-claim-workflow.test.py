#!/usr/bin/env python3
"""Contract tests for the agent delivery claim dispatcher workflow (#2191)."""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/agent-delivery-claim.yml"


class AgentDeliveryClaimWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_refuses_pull_request_target_and_privileged_fragment_writers(self):
        self.assertNotIn("pull_request_target", self.text)
        self.assertNotIn("renovate-changelog.yml", self.text)
        # Must not author or push release-context fragments (ADR 0243 / S-2).
        self.assertNotRegex(self.text, r"(?i)git\s+commit")
        self.assertNotRegex(self.text, r"(?i)git\s+push")
        self.assertNotIn("contents: write", self.text)
        triggers = self.doc[True]
        self.assertIn("pull_request", triggers)
        self.assertEqual(
            set(triggers["pull_request"]["types"]),
            {"opened", "reopened", "synchronize"},
        )

    def test_claim_job_is_renovate_gated_and_posts_only_claim_receipts(self):
        job = self.doc["jobs"]["claim"]
        self.assertIn("renovate", job["if"])
        self.assertIn("pull-requests: write", self.text)
        self.assertIn("contents: read", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertIn("scripts/ci-gate/agent_delivery_claim.py", self.text)
        self.assertIn("--store comment", self.text)
        self.assertIn("needs-changelog-context", self.text)
        self.assertIn("agent-delivery-claim-", job["concurrency"]["group"])
        self.assertFalse(job["concurrency"]["cancel-in-progress"])

    def test_public_pull_requests_stay_on_ephemeral_hosted_runners(self):
        runs_on = self.doc["jobs"]["claim"]["runs-on"]
        self.assertIn("ubuntu-24.04", runs_on)
        self.assertIn("github.event.repository.private != true", runs_on)


if __name__ == "__main__":
    unittest.main()
