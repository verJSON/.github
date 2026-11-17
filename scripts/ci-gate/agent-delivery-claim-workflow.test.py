#!/usr/bin/env python3
"""Contract tests for the agent delivery claim dispatcher workflow (#2191)."""
from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/agent-delivery-claim.yml"


def extract_signal_script(text: str) -> str:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line == "      - name: Detect needs-changelog-context signal":
            start = index
            break
    assert start is not None, "signal step not found"
    body_start = None
    for index in range(start, len(lines)):
        if lines[index] == "        run: |":
            body_start = index + 1
            break
    assert body_start is not None, "signal run body not found"
    body = []
    for line in lines[body_start:]:
        if line.startswith("          "):
            body.append(line[10:])
        elif line == "":
            body.append("")
        else:
            break
    assert body, "signal run body empty"
    return "\n".join(body) + "\n"


class AgentDeliveryClaimWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)
        cls.signal_script = extract_signal_script(cls.text)

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
        group = job["concurrency"]["group"]
        self.assertEqual(
            group,
            "agent-delivery-claim-${{ github.repository }}-${{ github.event.pull_request.number }}",
        )
        self.assertNotIn("head.sha", group)
        self.assertFalse(job["concurrency"]["cancel-in-progress"])

    def test_public_pull_requests_stay_on_ephemeral_hosted_runners(self):
        runs_on = self.doc["jobs"]["claim"]["runs-on"]
        self.assertIn("ubuntu-24.04", runs_on)
        # Reviewed hosted-selector shape (renovate-compatibility-reconcile twin):
        # private → CI_LANE_TRUSTED; else → CI_LANE_UNTRUSTED (ephemeral public).
        self.assertIn("github.event.repository.private == true", runs_on)
        self.assertIn("CI_LANE_UNTRUSTED", runs_on)
        self.assertNotIn("CI_RUNNER_OVERFLOW", runs_on)

    def test_claim_scripts_run_from_base_sha_trusted_checkout(self):
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", self.text)
        self.assertIn("path: .agent-delivery-claim-trusted", self.text)
        self.assertIn("scripts/ci-gate/agent_delivery_claim.py", self.text)
        self.assertIn("scripts/changelog.py", self.text)
        self.assertIn(
            'python3 -I "$GITHUB_WORKSPACE/.agent-delivery-claim-trusted/scripts/changelog.py"',
            self.text,
        )
        self.assertIn(
            'python3 -I "$GITHUB_WORKSPACE/.agent-delivery-claim-trusted/scripts/ci-gate/agent_delivery_claim.py"',
            self.text,
        )
        # Claim invocation must not call the PR-head copy of the claim script.
        claim_step = next(
            step
            for step in self.doc["jobs"]["claim"]["steps"]
            if step.get("name") == "Claim agent delivery job"
        )
        self.assertIn(".agent-delivery-claim-trusted/scripts/ci-gate/agent_delivery_claim.py", claim_step["run"])
        self.assertNotRegex(claim_step["run"], r"(?m)^\s*python3 -I scripts/ci-gate/agent_delivery_claim\.py")
        self.assertIn("PR_TITLE: ${{ github.event.pull_request.title }}", self.text)
        self.assertIn("PR_LABELS: ${{ join(github.event.pull_request.labels.*.name, ',') }}", self.text)
        self.assertNotIn("--pr-title", claim_step["run"])
        # Worker still must not write NEXT/ from the claim job.
        self.assertNotIn("NEXT/", claim_step["run"])
        self.assertNotIn("pull_request_target", self.text)

    def _run_signal(self, *, changelog_rc: int, changelog_stdout: str) -> tuple[int, str, dict[str, str]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted = root / ".agent-delivery-claim-trusted" / "scripts"
            trusted.mkdir(parents=True)
            changelog = trusted / "changelog.py"
            changelog.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                f"sys.stdout.write({changelog_stdout!r})\n"
                f"raise SystemExit({changelog_rc})\n",
                encoding="utf-8",
            )
            changelog.chmod(changelog.stat().st_mode | stat.S_IXUSR)
            script = root / "signal.sh"
            script.write_text(self.signal_script, encoding="utf-8")
            output = root / "github-output"
            summary = root / "summary.md"
            env = os.environ.copy()
            env.update(
                {
                    "GITHUB_WORKSPACE": str(root),
                    "GITHUB_OUTPUT": str(output),
                    "GITHUB_STEP_SUMMARY": str(summary),
                    "BASE_SHA": "a" * 40,
                    "HEAD_SHA": "b" * 40,
                    "PR_AUTHOR": "renovate[bot]",
                    "PATH": env.get("PATH", ""),
                }
            )
            completed = subprocess.run(
                ["bash", str(script)],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                cwd=str(root),
            )
            outputs = {}
            if output.exists():
                for line in output.read_text(encoding="utf-8").splitlines():
                    if "=" in line:
                        key, value = line.split("=", 1)
                        outputs[key] = value
            return completed.returncode, completed.stdout + completed.stderr, outputs

    def test_signal_clear_on_changelog_exit_0(self):
        rc, _out, outputs = self._run_signal(changelog_rc=0, changelog_stdout="")
        self.assertEqual(rc, 0)
        self.assertEqual(outputs.get("signal"), "clear")

    def test_signal_claim_on_needs_changelog_context_exit_1(self):
        rc, _out, outputs = self._run_signal(
            changelog_rc=1,
            changelog_stdout="needs-changelog-context: package.json changed",
        )
        self.assertEqual(rc, 0)
        self.assertEqual(outputs.get("signal"), "claim")

    def test_signal_fails_closed_on_changelog_exit_2(self):
        rc, out, outputs = self._run_signal(changelog_rc=2, changelog_stdout="boom")
        self.assertNotEqual(rc, 0)
        self.assertNotEqual(outputs.get("signal"), "claim")
        self.assertIn("::error title=needs-changelog-context::", out)


if __name__ == "__main__":
    unittest.main()
