#!/usr/bin/env python3

import hashlib
import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
PUBLISH_WORKFLOW = ROOT / ".github/workflows/container-candidate-publish.yml"
GENERATOR = ROOT / "scripts/gen-container-candidate.sh"
CANARY = ROOT / ".github/workflows/container-candidate-reusable-contract.yml"


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

    def test_reusable_canary_pins_current_provenance_and_retry_helpers(self):
        canary = yaml.safe_load(CANARY.read_text(encoding="utf-8"))
        expected = {
            "provenance-sha256": "container_cosign_provenance.py",
            "retry-sha256": "container_candidate_retry.py",
        }
        for input_name, filename in expected.items():
            digest = hashlib.sha256((ROOT / "scripts" / filename).read_bytes()).hexdigest()
            with self.subTest(input=input_name):
                self.assertEqual(canary["jobs"]["publish"]["with"][input_name], digest)
        self.assertEqual(
            canary["jobs"]["validate"]["with"]["retry-sha256"],
            canary["jobs"]["publish"]["with"]["retry-sha256"],
        )

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

    def test_new_builds_check_registry_index_before_requesting_oidc_token(self):
        workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
        for job_name in ("publish-base", "publish-derived"):
            with self.subTest(job=job_name):
                sign = next(
                    step for step in workflow["jobs"][job_name]["steps"]
                    if step.get("id") == "provenance"
                )
                script = sign["run"]
                self.assertEqual(sign["env"]["CONTRACT_REF"], "${{ inputs.contract-ref }}")
                self.assertLess(
                    script.index('docker buildx imagetools inspect "$REPOSITORY@$DIGEST" --raw'),
                    script.index('python3 "$oci" index --index "$raw" --reviewed-platforms "$reviewed"'),
                )
                self.assertLess(
                    script.index('python3 "$oci" index --index "$raw" --reviewed-platforms "$reviewed"'),
                    script.index("request-oidc-token"),
                )


    def test_sbom_export_uses_validated_oci_inventory(self):
        workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
        steps = workflow["jobs"]["attest-sbom"]["steps"]
        subject = next(step for step in steps if step.get("id") == "sbom-subject")
        script = subject["run"]

        self.assertIn(
            'python3 "$helper" index --index "$raw_index" '
            '--reviewed-platforms "$reviewed_platforms" > "$inventory"',
            script,
        )
        self.assertIn(
            '--platform "$platform_key" --inventory "$inventory" > sbom.spdx.json',
            script,
        )

    def test_gar_login_uses_the_generated_service_account_access_token(self):
        workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
        steps = workflow["jobs"]["mirror-gar"]["steps"]
        auth = next(step for step in steps if step.get("id") == "gar-auth")
        login = next(
            step for step in steps
            if step.get("with", {}).get("registry") == "${{ matrix.gar.registryHost }}"
        )

        self.assertEqual(auth["with"]["token_format"], "access_token")
        self.assertEqual(
            login["with"]["password"], "${{ steps.gar-auth.outputs.access_token }}"
        )


if __name__ == "__main__":
    unittest.main()
