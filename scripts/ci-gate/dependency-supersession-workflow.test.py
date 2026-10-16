#!/usr/bin/env python3
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]


class WorkflowContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.observe = (ROOT / ".github/workflows/dependency-supersession-observe.yml").read_text()
        cls.reconcile = (ROOT / ".github/workflows/dependency-supersession-reconcile.yml").read_text()

    def test_observer_is_read_only_and_exact_repository_scoped(self):
        self.assertIn("permission-contents: read", self.observe)
        self.assertIn("permission-pull-requests: read", self.observe)
        self.assertNotIn("permission-pull-requests: write", self.observe)
        self.assertIn("repositories: ${{ inputs.repository }}", self.observe)
        self.assertNotIn("pull_request_target", self.observe)

    def test_write_credential_is_only_terminal_and_gated(self):
        self.assertIn("if: ${{ inputs.enforce }}\n        id: supersession-token", self.reconcile)
        self.assertIn("DEPENDENCY_SUPERSESSION_WRITE_ENABLED", self.reconcile)
        self.assertEqual(self.reconcile.count("DEPENDENCY_SUPERSESSION_APP_PRIVATE_KEY"), 1)
        self.assertEqual(self.reconcile.count("steps.supersession-token.outputs.token"), 1)
        self.assertIn("permission-contents: read", self.reconcile)
        self.assertIn("permission-pull-requests: write", self.reconcile)
        self.assertNotIn("ORG_ADMIN_TOKEN", self.reconcile)
        self.assertNotIn("pull_request_target", self.reconcile)

    def test_proposal_provenance_and_malformed_ids_fail_closed(self):
        for text in ("^[123456789][0123456789]{0,14}$", "^[0-9a-f]{64}$", ".path == \".github/workflows/dependency-supersession-observe.yml\"", ".conclusion == \"success\"", "merge_base_commit.sha == $run_head"):
            self.assertIn(text, self.reconcile)
        self.assertIn("(.repository.full_name | ascii_downcase) == ($repo | ascii_downcase)", self.reconcile)
        self.assertIn("(.head_repository.full_name | ascii_downcase) == ($repo | ascii_downcase)", self.reconcile)
        self.assertIn("repositories: ${{ steps.target.outputs.name }}", self.reconcile)

    def test_run_repository_identity_accepts_case_variants_and_rejects_confusables(self):
        comparison = "(.repository.full_name | ascii_downcase) == ($repo | ascii_downcase)"
        self.assertIn(comparison, self.reconcile)
        query = f"{{repository: {{full_name: $name}}}} | {comparison}"
        accepted = subprocess.run(
            ["jq", "-e", "-n", "--arg", "repo", "Verjson/payments", "--arg", "name", "verJSON/payments", query],
            check=False,
            capture_output=True,
            text=True,
        )
        rejected = subprocess.run(
            ["jq", "-e", "-n", "--arg", "repo", "Verjson/payments", "--arg", "name", "v\u0435rJSON/payments", query],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertNotEqual(rejected.returncode, 0, rejected.stderr)


if __name__ == "__main__":
    unittest.main()
