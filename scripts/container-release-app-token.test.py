#!/usr/bin/env python3
import copy
import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/container-release.yml"
ACTION = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"


def load_workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def named_step(job, name):
    return next(step for step in job["steps"] if step.get("name") == name)


def validate(workflow):
    errors = []
    call = workflow.get(True, {}).get("workflow_call", {})
    inputs = call.get("inputs", {})
    secrets = call.get("secrets", {})
    if set(inputs) < {"release_app_client_id"}:
        errors.append("missing release App client ID input")
    if secrets or not inputs.get("release_environment", {}).get("required"):
        errors.append("release must select an environment without forwarded keys")

    promote = workflow["jobs"]["promote"]
    permissions = promote.get("permissions", {})
    expected = {
        "actions": "read",
        "contents": "read",
        "id-token": "write",
        "packages": "write",
    }
    if permissions != expected:
        errors.append("promote permissions changed")

    mint = named_step(promote, "Mint exact-repository release App token")
    if mint.get("uses") != ACTION:
        errors.append("mint action is not immutable")
    expected_with = {
        "client-id": "${{ inputs.release_app_client_id }}",
        "private-key": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-contents": "write",
    }
    if mint.get("with") != expected_with:
        errors.append("mint scope or permission changed")
    if mint.get("continue-on-error") is not None:
        errors.append("mint failure is tolerated")

    client_guard = named_step(promote, "Require the release App client ID")
    guard_script = client_guard.get("run", "")
    if "-z \"$RELEASE_APP_CLIENT_ID\"" not in guard_script or "^[0-9]+$" not in guard_script:
        errors.append("client ID guard does not reject missing or numeric IDs")

    mint_index = promote["steps"].index(mint)
    promotion_index = next(
        index
        for index, step in enumerate(promote["steps"])
        if step.get("name") == "Promote exact digests without rebuilding"
    )
    if mint_index >= promotion_index:
        errors.append("credential failure occurs after package mutation")

    output = named_step(promote, "Canonical changelog, Git tag, release and machine output")
    token_expression = "${{ steps.release-app-token.outputs.token }}"
    if output.get("env", {}).get("GH_TOKEN") != token_expression:
        errors.append("terminal release does not receive App token")
    for step in promote["steps"]:
        if step is not output and token_expression in repr(step):
            errors.append("App token escaped terminal release step")

    checkout = next(step for step in promote["steps"] if str(step.get("uses", "")).startswith("actions/checkout@"))
    if checkout.get("with", {}).get("persist-credentials") is not False or "token" in checkout.get("with", {}):
        errors.append("checkout persists a write credential")

    login_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("docker/login-action@")
    ]
    ghcr_logins = [step for step in login_steps if step.get("with", {}).get("registry") == "ghcr.io"]
    gar_logins = [
        step for step in login_steps
        if step.get("with", {}).get("registry") == "${{ steps.candidate.outputs.registry_host }}"
    ]
    if (
        not ghcr_logins
        or any(step.get("with", {}).get("password") != "${{ github.token }}" for step in ghcr_logins)
        or len(gar_logins) != 1
        or gar_logins[0].get("with", {}).get("username") != "oauth2accesstoken"
        or gar_logins[0].get("with", {}).get("password") != "${{ steps.gar-auth.outputs.auth_token }}"
    ):
        errors.append("registry login is not isolated to job credentials")

    rendered = repr(workflow)
    legacy_release_token = "RELEASE_" + "TOKEN"
    legacy_org_release_token = "VERJSON_RELEASE_" + "TOKEN"
    if legacy_release_token in rendered or legacy_org_release_token in rendered or "release-token" in rendered:
        errors.append("legacy release token remains")
    return errors


class ContainerReleaseAppTokenContractTest(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow()

    def test_exact_contract_is_accepted(self):
        self.assertEqual([], validate(self.workflow))

    def test_rejects_repository_scope_widening(self):
        mutant = copy.deepcopy(self.workflow)
        named_step(mutant["jobs"]["promote"], "Mint exact-repository release App token")["with"]["repositories"] = "${{ github.repository_owner }}"
        self.assertIn("mint scope or permission changed", validate(mutant))

    def test_rejects_app_permission_widening(self):
        mutant = copy.deepcopy(self.workflow)
        named_step(mutant["jobs"]["promote"], "Mint exact-repository release App token")["with"]["permission-packages"] = "write"
        self.assertIn("mint scope or permission changed", validate(mutant))

    def test_rejects_mutable_action(self):
        mutant = copy.deepcopy(self.workflow)
        named_step(mutant["jobs"]["promote"], "Mint exact-repository release App token")["uses"] = "actions/create-github-app-token@v3"
        self.assertIn("mint action is not immutable", validate(mutant))

    def test_rejects_tolerated_mint_failure(self):
        mutant = copy.deepcopy(self.workflow)
        named_step(mutant["jobs"]["promote"], "Mint exact-repository release App token")["continue-on-error"] = True
        self.assertIn("mint failure is tolerated", validate(mutant))

    def test_rejects_removed_malformed_client_id_guard(self):
        mutant = copy.deepcopy(self.workflow)
        named_step(mutant["jobs"]["promote"], "Require the release App client ID")["run"] = "true"
        self.assertIn("client ID guard does not reject missing or numeric IDs", validate(mutant))

    def test_rejects_app_token_delivery_to_registry(self):
        mutant = copy.deepcopy(self.workflow)
        login = next(step for step in mutant["jobs"]["promote"]["steps"] if str(step.get("uses", "")).startswith("docker/login-action@"))
        login["with"]["password"] = "${{ steps.release-app-token.outputs.token }}"
        errors = validate(mutant)
        self.assertIn("App token escaped terminal release step", errors)
        self.assertIn("registry login is not isolated to job credentials", errors)

    def test_rejects_mint_after_first_mutation(self):
        mutant = copy.deepcopy(self.workflow)
        steps = mutant["jobs"]["promote"]["steps"]
        mint = named_step(mutant["jobs"]["promote"], "Mint exact-repository release App token")
        steps.remove(mint)
        promotion = named_step(mutant["jobs"]["promote"], "Promote exact digests without rebuilding")
        steps.insert(steps.index(promotion) + 1, mint)
        self.assertIn("credential failure occurs after package mutation", validate(mutant))

    def test_rejects_persisted_checkout_token(self):
        mutant = copy.deepcopy(self.workflow)
        checkout = next(step for step in mutant["jobs"]["promote"]["steps"] if str(step.get("uses", "")).startswith("actions/checkout@"))
        checkout["with"]["persist-credentials"] = True
        self.assertIn("checkout persists a write credential", validate(mutant))

    def test_rejects_legacy_pat_contract(self):
        mutant = copy.deepcopy(self.workflow)
        mutant[True]["workflow_call"].setdefault("secrets", {})["release-token"] = {"required": True}
        errors = validate(mutant)
        self.assertIn("release must select an environment without forwarded keys", errors)
        self.assertIn("legacy release token remains", errors)


if __name__ == "__main__":
    unittest.main()
