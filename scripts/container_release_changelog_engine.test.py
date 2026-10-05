#!/usr/bin/env python3
import copy
import json
import os
import pathlib
import subprocess
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/container-release.yml"
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"


def load_workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def named_step(job, name):
    return next(step for step in job["steps"] if step.get("name") == name)


def validate(workflow):
    errors = []
    promote = workflow["jobs"]["promote"]
    steps = promote["steps"]
    guard = named_step(promote, "Bind the immutable changelog contract to this workflow")
    contract_checkout = named_step(promote, "Check out the immutable provenance and changelog contract")
    mint = named_step(promote, "Mint exact-repository release App token")
    output = named_step(promote, "Canonical changelog, Git tag, release and machine output")

    guard_script = guard.get("run", "")
    expected_guard_env = {
        "CONTRACT_REF": "${{ inputs.contract-ref }}",
        "JOB_CONTEXT": "${{ toJSON(job) }}",
    }
    expected_identity = "Verjson/.github/.github/workflows/container-release.yml@$CONTRACT_REF"
    if guard.get("env") != expected_guard_env or "^[0-9a-f]{40}$" not in guard_script or expected_identity not in guard_script:
        errors.append("contract ref guard is not exact")
    if steps.index(guard) != 0:
        errors.append("contract ref guard does not precede acquisition")

    expected_checkout = {
        "repository": "Verjson/.github",
        "ref": "${{ inputs.contract-ref }}",
        "path": ".container-release-contract",
        "persist-credentials": False,
        # The same pinned checkout carries the reconciliation enforcer (ADR 0158), so
        # the hook's allowlist is enforced by immutable contract code rather than by a
        # consumer-local copy. Widening beyond these two files is drift.
        "sparse-checkout": "scripts/changelog.py\nscripts/container_release_reconcile.py\nscripts/container_cosign_provenance.py\n",
        "sparse-checkout-cone-mode": False,
    }
    if contract_checkout.get("uses") != CHECKOUT or contract_checkout.get("with") != expected_checkout:
        errors.append("changelog engine checkout is not exact")
    if steps.index(contract_checkout) >= steps.index(mint):
        errors.append("changelog acquisition occurs after App-token minting")

    run = output.get("run", "")
    expected_command = "python3 .container-release-contract/scripts/changelog.py release --version \"v$VERSION\""
    if expected_command not in run:
        errors.append("terminal release does not execute the pinned changelog engine")
    if "python scripts/changelog.py release" in run or "python3 scripts/changelog.py release" in run:
        errors.append("terminal release executes a consumer-local changelog engine")
    return errors


def run_guard(workflow_ref, contract_ref):
    workflow = load_workflow()
    guard = named_step(workflow["jobs"]["promote"], "Bind the immutable changelog contract to this workflow")
    env = os.environ.copy()
    env.update({"JOB_CONTEXT": json.dumps({"workflow_ref": workflow_ref}), "CONTRACT_REF": contract_ref})
    return subprocess.run(["bash", "-c", guard["run"]], env=env, capture_output=True, text=True)


class ContainerReleaseChangelogEngineContractTest(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow()

    def test_exact_contract_is_accepted(self):
        self.assertEqual([], validate(self.workflow))

    def test_rejects_mutable_contract_ref(self):
        mutant = copy.deepcopy(self.workflow)
        named_step(mutant["jobs"]["promote"], "Check out the immutable provenance and changelog contract")["with"]["ref"] = "main"
        self.assertIn("changelog engine checkout is not exact", validate(mutant))

    def test_rejects_widened_contract_checkout(self):
        mutant = copy.deepcopy(self.workflow)
        named_step(mutant["jobs"]["promote"], "Check out the immutable provenance and changelog contract")["with"].pop("sparse-checkout")
        self.assertIn("changelog engine checkout is not exact", validate(mutant))

    def test_rejects_persisted_contract_credential(self):
        mutant = copy.deepcopy(self.workflow)
        named_step(mutant["jobs"]["promote"], "Check out the immutable provenance and changelog contract")["with"]["persist-credentials"] = True
        self.assertIn("changelog engine checkout is not exact", validate(mutant))

    def test_rejects_guard_after_acquisition(self):
        mutant = copy.deepcopy(self.workflow)
        steps = mutant["jobs"]["promote"]["steps"]
        guard = named_step(mutant["jobs"]["promote"], "Bind the immutable changelog contract to this workflow")
        steps.remove(guard)
        steps.insert(1, guard)
        self.assertIn("contract ref guard does not precede acquisition", validate(mutant))

    def test_guard_accepts_only_the_exact_loaded_workflow_revision(self):
        contract_ref = "a" * 40
        result = run_guard(
            f"Verjson/.github/.github/workflows/container-release.yml@{contract_ref}",
            contract_ref,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_guard_rejects_a_different_valid_contract_sha(self):
        result = run_guard(
            f"Verjson/.github/.github/workflows/container-release.yml@{'a' * 40}",
            "b" * 40,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("does not match the loaded canonical", result.stdout)

    def test_guard_rejects_a_wrong_workflow_source_or_path(self):
        contract_ref = "a" * 40
        wrong_refs = (
            f"Attacker/.github/.github/workflows/container-release.yml@{contract_ref}",
            f"Verjson/.github/.github/workflows/other.yml@{contract_ref}",
        )
        for workflow_ref in wrong_refs:
            with self.subTest(workflow_ref=workflow_ref):
                result = run_guard(workflow_ref, contract_ref)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("does not match the loaded canonical", result.stdout)

    def test_rejects_consumer_local_engine(self):
        mutant = copy.deepcopy(self.workflow)
        output = named_step(mutant["jobs"]["promote"], "Canonical changelog, Git tag, release and machine output")
        output["run"] = output["run"].replace(".container-release-contract/scripts/changelog.py", "scripts/changelog.py")
        errors = validate(mutant)
        self.assertIn("terminal release does not execute the pinned changelog engine", errors)
        self.assertIn("terminal release executes a consumer-local changelog engine", errors)

    def test_rejects_engine_acquisition_after_token_mint(self):
        mutant = copy.deepcopy(self.workflow)
        steps = mutant["jobs"]["promote"]["steps"]
        checkout = named_step(mutant["jobs"]["promote"], "Check out the immutable provenance and changelog contract")
        steps.remove(checkout)
        mint = named_step(mutant["jobs"]["promote"], "Mint exact-repository release App token")
        steps.insert(steps.index(mint) + 1, checkout)
        self.assertIn("changelog acquisition occurs after App-token minting", validate(mutant))


if __name__ == "__main__":
    unittest.main()
