#!/usr/bin/env python3

import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
PUBLISH_WORKFLOW = ROOT / ".github/workflows/container-candidate-publish.yml"
GENERATOR = ROOT / "scripts/gen-container-candidate.sh"


class CosignWorkflowContractTests(unittest.TestCase):
    def test_reusable_publisher_does_not_accept_caller_asserted_eligibility(self):
        workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
        workflow_call = workflow.get("on", workflow.get(True))["workflow_call"]

        self.assertNotIn("candidate-eligibility-enabled", workflow_call["inputs"])

    def test_container_candidate_flow_has_no_github_artifact_attestation_dependency(self):
        workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

        self.assertNotRegex(workflow, r"actions/attest(?:-build-provenance)?@")
        self.assertNotRegex(workflow, r"(?m)^\s*attestations:\s*write\s*$")
        self.assertNotIn("gh attestation verify", workflow)

    def test_candidate_manifest_records_the_default_off_eligibility_input(self):
        workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("promotionEligible:false", workflow)

    def test_generated_callers_pin_provenance_helper_and_disable_eligibility(self):
        generator = GENERATOR.read_text(encoding="utf-8")

        self.assertRegex(generator, r"provenance_sha256=.*container_cosign_provenance\.py")
        self.assertIn("provenance-sha256: $provenance_sha256", generator)
        self.assertNotIn("candidate-eligibility-enabled: false", generator)

    def test_publisher_pins_cosign_and_oras_installers(self):
        workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

        self.assertRegex(
            workflow,
            r"uses: sigstore/cosign-installer@[0-9a-f]{40} # v4\.1\.2",
        )
        self.assertRegex(
            workflow,
            r"uses: oras-project/setup-oras@[0-9a-f]{40} # v2\.0\.2",
        )
        self.assertGreaterEqual(workflow.count("cosign-release: v3.1.3"), 1)

    def test_every_cosign_job_downloads_its_verified_sibling_before_execution(self):
        workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
        for name in ("publish-base", "publish-derived", "attest-sbom", "mirror-gar", "candidate-manifest"):
            with self.subTest(job=name):
                steps = workflow["jobs"][name]["steps"]
                download = next(
                    index for index, step in enumerate(steps)
                    if "scripts/container_cosign_provenance.py\" -o" in step.get("run", "")
                )
                script = steps[download]["run"]
                self.assertEqual(steps[download]["env"]["RETRY_SHA256"], "${{ inputs.retry-sha256 }}")
                self.assertIn("scripts/container_candidate_retry.py\" -o", script)
                self.assertIn('"$RETRY_SHA256" "$retry" | sha256sum --check --strict', script)
                self.assertLess(
                    script.index("scripts/container_candidate_retry.py\" -o"),
                    script.index("python3 ") if "python3 " in script else len(script),
                )

    def test_gar_mirror_verifies_the_original_attestation_from_gar(self):
        workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
        mirror_job = workflow["jobs"]["mirror-gar"]
        run_scripts = "\n".join(
            step.get("run", "") for step in mirror_job["steps"]
        )

        self.assertIn("setup-oras", "\n".join(step.get("uses", "") for step in mirror_job["steps"]))
        self.assertRegex(run_scripts, r'python3 "\$provenance_helper" verify-image')
        self.assertIn("--image-repository", run_scripts)
        self.assertIn("--registry-repository", run_scripts)
        self.assertIn("--expected-bundle-digest", run_scripts)
        self.assertIn("--expected-referrer-digest", run_scripts)
        self.assertIn('scripts/container_oci_index.py" -o "$RUNNER_TEMP/container_oci_index.py"', run_scripts)


if __name__ == "__main__":
    unittest.main()
