#!/usr/bin/env python3
"""The reconciliation hook's position and bounds inside the reusable release workflow."""

import copy
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/container-release.yml"
STEP = "Reconcile derived release inputs before credential minting"
RECONCILER = ".container-release-contract/scripts/container_release_reconcile.py"


def load_workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def named_step(job, name):
    return next(step for step in job["steps"] if step.get("name") == name)


def validate(workflow):
    errors = []
    inputs = workflow.get(True, {}).get("workflow_call", {}).get("inputs", {})
    allowlist_input = inputs.get("reconcile-allowlist")
    if allowlist_input != {"required": False, "type": "string", "default": ""}:
        errors.append("reconciliation allowlist input is not an optional bounded string")

    promote = workflow["jobs"]["promote"]
    steps = promote["steps"]
    try:
        reconcile = named_step(promote, STEP)
    except StopIteration:
        return errors + ["reconciliation step is absent"]
    plan = named_step(promote, "Fail-closed preflight and immutable plan")
    contract = named_step(promote, "Check out the immutable provenance and changelog contract")
    mint = named_step(promote, "Mint exact-repository release App token")
    output = named_step(promote, "Canonical changelog, Git tag, release and machine output")

    if reconcile.get("if") != "${{ inputs.reconcile-allowlist != '' }}":
        errors.append("reconciliation is not opt-in on the reviewed allowlist")
    if steps.index(reconcile) <= steps.index(plan):
        errors.append("reconciliation runs before the provenance-verified release manifest exists")
    if steps.index(reconcile) <= steps.index(contract):
        errors.append("reconciliation runs before the pinned enforcer is checked out")
    if steps.index(reconcile) >= steps.index(mint):
        errors.append("reconciliation runs after the release App credential is minted")

    expected_env = {
        "VERSION": "${{ inputs.version }}",
        "CONTRACT_REF": "${{ inputs.contract-ref }}",
        "RECONCILE_ALLOWLIST": "${{ inputs.reconcile-allowlist }}",
    }
    if reconcile.get("env") != expected_env:
        errors.append("reconciliation step receives more than bounded non-secret release metadata")
    run = reconcile.get("run", "")
    if f"python3 {RECONCILER}" not in run:
        errors.append("reconciliation does not execute the pinned contract enforcer")
    if "python3 scripts/container_release_reconcile.py" in run:
        errors.append("reconciliation executes a consumer-local enforcer")

    release_run = output.get("run", "")
    if "git -C .container-release-contract rev-parse HEAD" not in release_run:
        errors.append("the release step does not rebind the pinned engine before executing it")
    if "release commit stages paths outside" not in release_run:
        errors.append("the release commit does not assert its exact staged path set")
    if output.get("env", {}).get("CONTRACT_REF") != "${{ inputs.contract-ref }}":
        errors.append("the release step cannot rebind the pinned engine without the contract ref")
    if output.get("env", {}).get("RECONCILE_ALLOWLIST") != "${{ inputs.reconcile-allowlist }}":
        errors.append("the release step cannot tell a configured release from an unconfigured one")
    if '[ -n "$RECONCILE_ALLOWLIST" ] && [ -f reconciled-paths.txt ]' not in release_run:
        errors.append("an unconfigured release reads back a tracked reconciled-paths.txt")
    # `.git/hooks` is untracked, so nothing in it was ever reviewed; it must not run
    # in the two workflow-authored commands that hold the release App token.
    # scripts/changelog.py's own internal git commit/tag calls during release() are a
    # separate path, covered instead by scripts/changelog.test.py (see its shared git()
    # helper, which applies the same core.hooksPath=/dev/null guard universally).
    for command in ("commit", "push"):
        if f"git -c core.hooksPath=/dev/null {command}" not in release_run:
            errors.append(f"git {command} runs repository hooks with the release App token")

    sparse = contract.get("with", {}).get("sparse-checkout", "")
    if any(path not in sparse for path in (
        "scripts/container_release_reconcile.py",
        "scripts/changelog.py",
        "scripts/container_cosign_provenance.py",
    )):
        errors.append("the pinned checkout does not carry the engine, enforcer, and provenance verifier")
    return errors


class ReconcileHookWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow()

    def test_exact_contract_is_accepted(self):
        self.assertEqual([], validate(self.workflow))

    def test_every_pinned_release_helper_starts_from_the_sparse_checkout(self):
        contract = named_step(
            self.workflow["jobs"]["promote"],
            "Check out the immutable provenance and changelog contract",
        )
        paths = contract["with"]["sparse-checkout"].splitlines()
        with tempfile.TemporaryDirectory() as directory:
            checkout = pathlib.Path(directory)
            for path in paths:
                source = ROOT / path
                destination = checkout / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            for helper in (
                "scripts/changelog.py",
                "scripts/container_release_reconcile.py",
                "scripts/container_cosign_provenance.py",
            ):
                with self.subTest(helper=helper):
                    result = subprocess.run(
                        [sys.executable, str(checkout / helper), "--help"],
                        cwd=checkout,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_reconciliation_after_credential_minting(self):
        mutant = copy.deepcopy(self.workflow)
        steps = mutant["jobs"]["promote"]["steps"]
        reconcile = named_step(mutant["jobs"]["promote"], STEP)
        steps.remove(reconcile)
        mint = named_step(mutant["jobs"]["promote"], "Mint exact-repository release App token")
        steps.insert(steps.index(mint) + 1, reconcile)
        self.assertIn(
            "reconciliation runs after the release App credential is minted", validate(mutant)
        )

    def test_rejects_reconciliation_before_the_verified_manifest(self):
        mutant = copy.deepcopy(self.workflow)
        steps = mutant["jobs"]["promote"]["steps"]
        reconcile = named_step(mutant["jobs"]["promote"], STEP)
        steps.remove(reconcile)
        steps.insert(0, reconcile)
        self.assertIn(
            "reconciliation runs before the provenance-verified release manifest exists",
            validate(mutant),
        )

    def test_rejects_an_always_on_hook(self):
        mutant = copy.deepcopy(self.workflow)
        named_step(mutant["jobs"]["promote"], STEP).pop("if")
        self.assertIn("reconciliation is not opt-in on the reviewed allowlist", validate(mutant))

    def test_rejects_a_secret_reaching_the_hook_step(self):
        mutant = copy.deepcopy(self.workflow)
        named_step(mutant["jobs"]["promote"], STEP)["env"]["GH_TOKEN"] = "${{ github.token }}"
        self.assertIn(
            "reconciliation step receives more than bounded non-secret release metadata",
            validate(mutant),
        )

    def test_rejects_a_consumer_local_enforcer(self):
        mutant = copy.deepcopy(self.workflow)
        step = named_step(mutant["jobs"]["promote"], STEP)
        step["run"] = step["run"].replace(f"{RECONCILER}", "scripts/container_release_reconcile.py")
        errors = validate(mutant)
        self.assertIn("reconciliation does not execute the pinned contract enforcer", errors)
        self.assertIn("reconciliation executes a consumer-local enforcer", errors)

    def test_rejects_a_runtime_dispatch_controlled_allowlist(self):
        mutant = copy.deepcopy(self.workflow)
        mutant[True]["workflow_call"]["inputs"]["reconcile-allowlist"] = {
            "required": True, "type": "string",
        }
        self.assertIn(
            "reconciliation allowlist input is not an optional bounded string", validate(mutant)
        )

    def test_rejects_dropping_the_release_commit_staging_assertion(self):
        mutant = copy.deepcopy(self.workflow)
        output = named_step(
            mutant["jobs"]["promote"], "Canonical changelog, Git tag, release and machine output"
        )
        output["run"] = output["run"].replace("release commit stages paths outside", "unchecked")
        self.assertIn("the release commit does not assert its exact staged path set", validate(mutant))

    def test_rejects_dropping_the_pinned_engine_rebind(self):
        mutant = copy.deepcopy(self.workflow)
        output = named_step(
            mutant["jobs"]["promote"], "Canonical changelog, Git tag, release and machine output"
        )
        output["run"] = output["run"].replace(
            "git -C .container-release-contract rev-parse HEAD", "true"
        )
        self.assertIn(
            "the release step does not rebind the pinned engine before executing it", validate(mutant)
        )

    def test_rejects_running_repository_hooks_with_the_release_token(self):
        mutant = copy.deepcopy(self.workflow)
        output = named_step(
            mutant["jobs"]["promote"], "Canonical changelog, Git tag, release and machine output"
        )
        output["run"] = output["run"].replace("git -c core.hooksPath=/dev/null commit", "git commit")
        self.assertIn(
            "git commit runs repository hooks with the release App token", validate(mutant)
        )

    def test_rejects_reading_the_reconciled_path_list_without_a_configured_allowlist(self):
        mutant = copy.deepcopy(self.workflow)
        output = named_step(
            mutant["jobs"]["promote"], "Canonical changelog, Git tag, release and machine output"
        )
        output["run"] = output["run"].replace(
            '[ -n "$RECONCILE_ALLOWLIST" ] && [ -f reconciled-paths.txt ]',
            "[ -f reconciled-paths.txt ]",
        )
        self.assertIn(
            "an unconfigured release reads back a tracked reconciled-paths.txt", validate(mutant)
        )


if __name__ == "__main__":
    unittest.main()
