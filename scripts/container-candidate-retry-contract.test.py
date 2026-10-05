#!/usr/bin/env python3

import hashlib
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/container-candidate-publish.yml"
CANARY = ROOT / ".github/workflows/container-candidate-reusable-contract.yml"


class RetryWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.text)

    def test_retry_verifier_is_a_required_digest_bound_contract_input(self):
        retry_input = self.workflow[True]["workflow_call"]["inputs"]["retry-sha256"]
        self.assertEqual(
            retry_input,
            {
                "description": "SHA-256 of the canonical exact-SHA retry verifier",
                "required": True,
                "type": "string",
            },
        )
        self.assertEqual(self.text.count('printf \'%s  %s\\n\' "$RETRY_SHA256" "$retry" | sha256sum --check --strict'), 2)
        prepare = next(
            step
            for step in self.workflow["jobs"]["prepare"]["steps"]
            if step.get("id") == "config"
        )
        self.assertEqual(prepare["env"]["RETRY_SHA256"], "${{ inputs.retry-sha256 }}")
        self.assertIn(
            '[[ "$RETRY_SHA256" =~ ^[0-9a-f]{64}$ ]]', prepare["run"]
        )
        helper_digest = hashlib.sha256(
            (ROOT / "scripts/container_candidate_retry.py").read_bytes()
        ).hexdigest()
        canary = yaml.safe_load(CANARY.read_text(encoding="utf-8"))
        self.assertEqual(
            canary["jobs"]["publish"]["with"]["retry-sha256"], helper_digest
        )

    def test_every_builder_verifies_before_reuse_and_skips_only_the_build_and_new_attestation(self):
        for job_name in ("publish-base", "publish-derived"):
            steps = self.workflow["jobs"][job_name]["steps"]
            retry = next(step for step in steps if step.get("id") == "retry")
            build = next(step for step in steps if step.get("id") == "build")
            provenance = next(step for step in steps if step.get("id") == "provenance")
            fresh_record = next(
                step
                for step in steps
                if "commit identity already records a different digest"
                in step.get("run", "")
            )
            self.assertLess(steps.index(retry), steps.index(build))
            self.assertIn("steps.retry.outputs.reused != 'true'", build["if"])
            self.assertIn("steps.retry.outputs.reused != 'true'", provenance["if"])
            self.assertIn(
                "steps.retry.outputs.reused != 'true'", fresh_record["if"]
            )
            command = retry["run"]
            for required in (
                'commit_tag="$REPOSITORY:sha-$GITHUB_SHA"',
                "verify-image \\",
                '--expected-source-repository "$GITHUB_REPOSITORY"',
                '--expected-source-repository-id "$GITHUB_REPOSITORY_ID"',
                '--expected-source-ref "$GITHUB_REF"',
                '--expected-source-commit "$GITHUB_SHA"',
                '--expected-caller-workflow-ref "$CALLER_WORKFLOW_REF"',
                '--expected-caller-workflow-sha "$CALLER_WORKFLOW_SHA"',
                '--expected-publisher-workflow-ref "Verjson/.github/.github/workflows/container-candidate-publish.yml@$CONTRACT_REF"',
                '--expected-contract-sha "$CONTRACT_REF"',
                "python3 \"$oci\" index",
                '--buildkit-provenance "$buildkit"',
            ):
                self.assertIn(required, command)
            self.assertNotIn("|| true", command)
            self.assertLess(command.index("verify-image \\"), command.index("imagetools create -t"))

    def test_missing_or_unreadable_identity_fails_closed_and_immutable_guard_remains(self):
        self.assertEqual(self.text.count("could not prove immutable commit identity state"), 2)
        self.assertEqual(self.text.count("manifest unknown|not found"), 4)
        self.assertEqual(self.text.count("commit identity already records a different digest"), 2)
        self.assertNotRegex(
            self.text,
            re.compile(r'imagetools create -t "\$commit_tag".*\|\|'),
        )

    def test_derived_reuse_binds_the_exact_sha_base_identity(self):
        retry = next(
            step
            for step in self.workflow["jobs"]["publish-derived"]["steps"]
            if step.get("id") == "retry"
        )
        self.assertIn('"$base_repository:sha-$GITHUB_SHA"', retry["run"])
        self.assertIn('echo "base-digest=$base_digest"', retry["run"])
        self.assertIn('--base-repository "$base_repository"', retry["run"])
        self.assertIn('--base-digest "$base_digest"', retry["run"])


    def test_fresh_publication_waits_boundedly_for_exact_digest_visibility(self):
        for job_name in ("publish-base", "publish-derived"):
            steps = self.workflow["jobs"][job_name]["steps"]
            readiness = next(
                step
                for step in steps
                if step.get("name") == "Wait for pushed manifest registry visibility"
            )
            provenance = next(step for step in steps if step.get("id") == "provenance")
            command = readiness["run"]

            self.assertLess(steps.index(readiness), steps.index(provenance))
            self.assertEqual(readiness["if"], provenance["if"])
            self.assertEqual(readiness["env"]["DIGEST"], "${{ steps.build.outputs.digest }}")
            self.assertEqual(readiness["env"]["REPOSITORY"], "${{ matrix.repository }}")
            self.assertIn('^sha256:[0-9a-f]{64}$', command)
            self.assertIn('reference="$REPOSITORY@$DIGEST"', command)
            self.assertIn("for attempt in 1 2 3 4 5 6", command)
            self.assertIn(
                'timeout --kill-after=2s 5s docker buildx imagetools inspect "$reference"',
                command,
            )
            self.assertIn('[ "$observed" = "$DIGEST" ]', command)
            self.assertIn('if [ "$attempt" -eq 6 ]', command)
            self.assertNotIn("${{ secrets.", command)

    def test_registry_visibility_gate_handles_transient_persistent_and_hung_inspection(self):
        steps = self.workflow["jobs"]["publish-base"]["steps"]
        command = next(
            step["run"]
            for step in steps
            if step.get("name") == "Wait for pushed manifest registry visibility"
        )
        digest = "sha256:" + "a" * 64

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (bin_dir / "docker").write_text(
                "#!/bin/sh\n"
                "count=$(cat \"$ATTEMPT_FILE\" 2>/dev/null || echo 0)\n"
                "count=$((count + 1))\n"
                "printf '%s' \"$count\" > \"$ATTEMPT_FILE\"\n"
                "if [ \"$MODE\" = hung ]; then\n"
                "  trap '' TERM\n"
                "  while :; do :; done\n"
                "fi\n"
                "if [ \"$MODE\" = transient ] && [ \"$count\" -ge 3 ]; then\n"
                "  printf '\"%s\"\\n' \"$DIGEST\"\n"
                "elif [ \"$MODE\" = mismatch ]; then\n"
                "  printf '\"sha256:%064d\"\\n' 0\n"
                "else\n"
                "  exit 1\n"
                "fi\n",
                encoding="utf-8",
            )
            for executable in bin_dir.iterdir():
                executable.chmod(0o755)

            for mode, expected_status, expected_attempts in (
                ("transient", 0, "3"),
                ("mismatch", 1, "6"),
                ("hung", 1, "6"),
            ):
                attempt_file = root / f"attempts-{mode}"
                environment = os.environ | {
                    "ATTEMPT_FILE": str(attempt_file),
                    "DIGEST": digest,
                    "MODE": mode,
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "REPOSITORY": "ghcr.io/verjson/example",
                }
                executable_command = command
                if mode == "hung":
                    executable_command = command.replace(
                        "timeout --kill-after=2s 5s",
                        "timeout --kill-after=0.1s 0.1s",
                    )
                started = time.monotonic()
                result = subprocess.run(
                    ["bash", "-c", executable_command],
                    check=False,
                    capture_output=True,
                    env=environment,
                    text=True,
                )
                elapsed = time.monotonic() - started
                self.assertEqual(expected_status, result.returncode, result.stderr)
                self.assertEqual(expected_attempts, attempt_file.read_text(encoding="utf-8"))
                if mode == "hung":
                    self.assertLess(elapsed, 5)


if __name__ == "__main__":
    unittest.main()
