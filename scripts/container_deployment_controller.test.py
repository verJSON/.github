#!/usr/bin/env python3

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("container_deployment_controller.py")
SPEC = importlib.util.spec_from_file_location("container_deployment_controller", MODULE_PATH)
assert SPEC and SPEC.loader
controller = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = controller
SPEC.loader.exec_module(controller)
TEST_NOW = datetime.now(timezone.utc)


def release(version: str, digit: str) -> dict:
    return {
        "releaseVersion": version,
        "manifestDigest": "sha256:" + digit * 64,
    }


def release_manifest(version: str, image_digit: str) -> dict:
    return {
        "schemaVersion": 1,
        "releaseVersion": version,
        "source": {"repository": "Verjson/verjson-github-runner", "commit": "c" * 40},
        "release": {
            "workflow": {
                "path": ".github/workflows/container-release.yml",
                "contractCommit": "a" * 40,
            }
        },
        "images": [
            {
                "variant": "runner",
                "repository": "ghcr.io/verjson/verjson-github-runner",
                "indexDigest": "sha256:" + image_digit * 64,
            }
        ],
    }


def manifest_release(manifest: dict) -> dict:
    digest = "sha256:" + hashlib.sha256(
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    return {"releaseVersion": manifest["releaseVersion"], "manifestDigest": digest}


def bind_manifest_bytes(candidate: dict, raw: str) -> None:
    candidate["manifestBytes"] = raw
    candidate["manifestIdentity"] = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    candidate["attestation"]["subjectDigest"] = candidate["manifestIdentity"]
    if "hostExport" in candidate:
        refresh_host_export_binding(candidate)


def published_format_evidence() -> dict:
    candidate = evidence()
    bind_manifest_bytes(candidate, json.dumps(candidate["manifest"], indent=2, sort_keys=True) + "\n")
    baseline = release_manifest("1.0.0", "1")
    raw = json.dumps(baseline, indent=2, sort_keys=True) + "\n"
    digest = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    for runner in candidate["fleet"]["runners"]:
        runner["release"]["manifestDigest"] = digest
        runner["manifestIdentity"] = digest
        runner["releaseManifest"] = copy.deepcopy(baseline)
        runner["releaseManifestBytes"] = raw
    refresh_host_export_binding(candidate)
    return candidate


def configuration() -> dict:
    return {
        "schemaVersion": 1,
        "reviewAuthority": {
            "code": {"appId": 201, "installationId": 301, "checkName": "runner-deploy-code-review", "workflowPath": ".github/workflows/container-deployment-code-review.yml"},
            "security": {"appId": 202, "installationId": 302, "checkName": "runner-deploy-security-review", "workflowPath": ".github/workflows/container-deployment-security-review.yml"},
            "ai": {"appId": 203, "installationId": 303, "sourceAppId": 403, "sourceCheckName": "canonical-ai-review", "checkName": "runner-deploy-ai-review", "workflowPath": ".github/workflows/container-deployment-ai-review.yml"},
        },
        "cliCommand": ["verjson-cloud"],
        "evidenceCommand": ["python3", "scripts/runner-deployment-evidence.py"],
        "probeCommand": ["python3", "scripts/runner-deployment-probe.py"],
        "expectedRelease": {
            "sourceRepository": "Verjson/verjson-github-runner",
            "sourceRef": "refs/heads/main",
            "signerWorkflow": "Verjson/.github/.github/workflows/container-release.yml",
            "contractCommit": "a" * 40,
            "variant": "runner",
        },
        "hostEvidenceAuthority": {"appId": 204, "installationId": 304},
        "fleets": {
            "production": {
                "lane": "gate",
                "project": "runner-project",
                "hostEvidence": {
                    "doContext": "readonly",
                    "doSshKey": "runner-key",
                    "maxAgeSeconds": 300,
                },
                "canary": "gha-gate-1",
                "runners": ["gha-gate-1", "gha-gate-2", "gha-gate-3"],
                "minimumAvailable": 2,
                "drainTimeoutSeconds": 600,
                "probeTimeoutSeconds": 300,
                "observationSeconds": 120,
                "runnerGroup": "trusted-production",
                "requiredLabels": ["gate", "pwsh"],
                "requiredTools": ["pwsh"],
            }
        },
    }


def evidence() -> dict:
    now = TEST_NOW
    manifest = release_manifest("2.0.0", "3")
    selected = manifest_release(manifest)
    baseline_manifest = release_manifest("1.0.0", "1")
    baseline = manifest_release(baseline_manifest)
    candidate = {
        "manifestIdentity": selected["manifestDigest"],
        "manifest": manifest,
        "attestation": {
            "verified": True,
            "repository": "Verjson/verjson-github-runner",
            "sourceRef": "refs/heads/main",
            "signerWorkflow": "Verjson/.github/.github/workflows/container-release.yml",
            "contractCommit": "a" * 40,
            "subjectDigest": selected["manifestDigest"],
            "expiresAt": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        },
        "requestedAt": now.isoformat().replace("+00:00", "Z"),
        "activeDeploymentCount": 0,
        "headCommit": "c" * 40,
        "headTree": "d" * 40,
        "authorization": {
            "source": "github-api",
            "repositoryId": 42,
            "defaultBranch": "main",
        "ref": "refs/heads/main",
        "deployedCommit": "c" * 40,
        "deployedTree": "d" * 40,
            "environment": "production",
            "deploymentBranchPolicy": {"protectedBranches": True, "customBranchPolicies": False},
            "requiredReviewers": [],
            "preventSelfReview": False,
            "canAdminsBypass": True,
            "dispatcher": "release-operator",
            "dispatcherId": 104,
            "triggeringActor": "release-trigger",
            "triggeringActorId": 105,
            "environmentBypassed": False,
            "bypassBasis": "branch-policy-only",
            "workflowRunId": 9001,
            "workflowRunAttempt": 1,
            "repository": "Verjson/verjson-github-runner",
            "pullRequest": 174,
            "reviewedHead": "c" * 40,
            "reviewedTree": "d" * 40,
            "patchDigest": "sha256:" + "9" * 64,
            "reviewGates": [
                {
                    "kind": kind,
                    "principalId": principal,
                    "appId": app_id,
                    "issuer": f"org-{kind}-review",
                    "checkRunId": 1000 + app_id,
                    "workflowRunId": 2000 + app_id,
                    "workflowRunAttempt": 1,
                    "workflowPath": f".github/workflows/{kind}-review.yml",
                    "workflowRef": f"Verjson/verjson-github-runner/.github/workflows/{kind}-review.yml@refs/heads/main",
                    "artifactId": 3000 + app_id,
                    "artifactDigest": "sha256:" + digit * 64,
                    "evidenceDigest": "sha256:" + "7" * 64,
                    "repositoryId": 42,
                    "repository": "Verjson/verjson-github-runner",
                    "pullRequest": 174,
                    "headCommit": "c" * 40,
                    "headTree": "d" * 40,
                    "patchDigest": "sha256:" + "9" * 64,
                    "conclusion": "success",
                    "completedAt": "2026-08-26T12:00:00Z",
                }
                for kind, principal, digit, app_id in (
                    ("code", 101, "4", 201),
                    ("security", 102, "5", 202),
                    ("ai", 103, "6", 203),
                )
            ],
        },
        "fleet": {
            "runners": [
                {
                    "name": name,
                    "release": copy.deepcopy(baseline),
                    "manifestIdentity": baseline["manifestDigest"],
                    "deployedDigest": "sha256:" + "1" * 64,
                    "releaseManifest": copy.deepcopy(baseline_manifest),
                    "online": True,
                    "admitted": True,
                    "busy": False,
                    "runnerGroup": "trusted-production",
                    "labels": ["gate", "pwsh"],
                    "tools": ["pwsh"],
                }
                for name in ("gha-gate-1", "gha-gate-2", "gha-gate-3")
            ]
        },
    }


    target_bytes = json.dumps(candidate["manifest"], indent=2, sort_keys=True) + "\n"
    target_identity = "sha256:" + hashlib.sha256(target_bytes.encode()).hexdigest()
    candidate["manifestBytes"] = target_bytes
    candidate["manifestIdentity"] = target_identity
    candidate["attestation"]["subjectDigest"] = target_identity
    candidate["releaseAssetId"] = 404
    baseline_bytes = json.dumps(baseline_manifest, indent=2, sort_keys=True) + "\n"
    baseline_identity = "sha256:" + hashlib.sha256(baseline_bytes.encode()).hexdigest()
    attestations = []
    for runner in candidate["fleet"]["runners"]:
        runner["releaseManifestBytes"] = baseline_bytes
        runner["manifestIdentity"] = baseline_identity
        runner["release"]["manifestDigest"] = baseline_identity
        attestations.append({
            "runnerName": runner["name"],
            "manifestIdentity": baseline_identity,
            "repository": "Verjson/verjson-github-runner",
            "sourceRef": "refs/heads/main",
            "signerWorkflow": "Verjson/.github/.github/workflows/container-release.yml",
            "signerCommit": "a" * 40,
            "verified": True,
        })
    host_request = {
        "schemaVersion": 1,
        "operation": "host-export",
        "attemptId": "9001.1",
        "fleetSelector": "production",
        "lane": "gate",
        "issuedAt": "2026-09-21T00:00:00Z",
        "expiresAt": "2026-09-21T00:15:00Z",
        "deploymentContractCommit": "a" * 40,
        "configDigest": controller.canonical_digest(configuration()),
        "planDigest": None,
        "action": "deploy",
        "rollbackOfAttempt": None,
        "github": {
            "repository": "Verjson/verjson-github-runner",
            "repositoryId": 42,
            "appId": 204,
            "installationId": 304,
        },
        "release": {
            "repository": "Verjson/verjson-github-runner",
            "assetId": 404,
            "manifestDigest": target_identity,
            "variant": "runner",
            "imageDigest": "sha256:" + "3" * 64,
            "sourceCommit": "c" * 40,
            "sourceRef": "refs/heads/main",
            "signerWorkflow": "Verjson/.github/.github/workflows/container-release.yml",
            "signerCommit": "a" * 40,
        },
        "hostExport": {
            "project": "runner-project",
            "doContext": "readonly",
            "doSshKey": "runner-key",
            "runnerNames": [runner["name"] for runner in candidate["fleet"]["runners"]],
            "maxAgeSeconds": 300,
            "purpose": "baseline",
            "runnerName": None,
        },
    }
    candidate["hostExportRequest"] = host_request
    candidate["hostExport"] = {
        "schemaVersion": 1,
        "requestDigest": controller.canonical_digest(host_request),
        "outcome": "passed",
        "hostEvidence": {
            "operation": "host-export",
            "manifestIdentity": target_identity,
            "manifestBytes": target_bytes,
            "manifest": copy.deepcopy(candidate["manifest"]),
            "fleet": copy.deepcopy(candidate["fleet"]),
        },
        "baselineAttestations": attestations,
    }
    return candidate

def invoke_admission(plan: dict, candidate: dict) -> tuple[int, str, list[str]]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        plan_path = root / "plan.json"
        config_path = root / "config.json"
        evidence_path = root / "evidence.json"
        receipt_dir = root / "receipts"
        for path, value in (
            (plan_path, plan),
            (config_path, configuration()),
            (evidence_path, candidate),
        ):
            path.write_text(json.dumps(value), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "admit",
                "--plan",
                str(plan_path),
                "--config",
                str(config_path),
                "--evidence",
                str(evidence_path),
                "--receipt-dir",
                str(receipt_dir),
                "--fleet",
                "production",
                "--action",
                "deploy",
                "--contract-ref",
                "0" * 40,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        receipts = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(receipt_dir.glob("revision-*.json"))
        ]
        return result.returncode, result.stderr, receipts


def refresh_host_export_binding(candidate: dict, config: dict | None = None) -> None:
    request = candidate["hostExportRequest"]
    config = config or configuration()
    expected = config["expectedRelease"]
    request["configDigest"] = controller.canonical_digest(config)
    manifest = candidate["manifest"]
    source = manifest["source"]
    workflow = manifest["release"]["workflow"]
    target_images = manifest["images"]
    target_variant = expected["variant"]
    if not any(item["variant"] == target_variant for item in target_images):
        target_variant = target_images[0]["variant"]
    image = next(item for item in target_images if item["variant"] == target_variant)
    request["release"].update({
        "repository": expected["sourceRepository"],
        "assetId": candidate["releaseAssetId"],
        "manifestDigest": candidate["manifestIdentity"],
        "variant": target_variant,
        "imageDigest": image["indexDigest"],
        "sourceCommit": source["commit"],
        "sourceRef": expected["sourceRef"],
        "signerWorkflow": expected["signerWorkflow"],
        "signerCommit": workflow["contractCommit"],
    })
    request["hostExport"]["runnerNames"] = [
        runner["name"] for runner in candidate["fleet"]["runners"]
    ]
    for runner in candidate["fleet"]["runners"]:
        raw = json.dumps(runner["releaseManifest"], indent=2, sort_keys=True) + "\n"
        identity = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
        runner["releaseManifestBytes"] = raw
        runner["manifestIdentity"] = identity
        runner["release"]["manifestDigest"] = identity
        runner_images = runner["releaseManifest"]["images"]
        runner_variant = target_variant
        if not any(item["variant"] == runner_variant for item in runner_images):
            runner_variant = runner_images[0]["variant"]
        runner_image = next(item for item in runner_images if item["variant"] == runner_variant)
        runner["deployedDigest"] = runner_image["indexDigest"]
    candidate["hostExport"]["requestDigest"] = controller.canonical_digest(request)
    candidate["hostExport"]["hostEvidence"].update({
        "manifestIdentity": candidate["manifestIdentity"],
        "manifestBytes": candidate["manifestBytes"],
        "manifest": copy.deepcopy(manifest),
        "fleet": copy.deepcopy(candidate["fleet"]),
    })
    candidate["hostExport"]["baselineAttestations"] = [
        {
            "runnerName": runner["name"],
            "manifestIdentity": runner["manifestIdentity"],
            "repository": expected["sourceRepository"],
            "sourceRef": expected["sourceRef"],
            "signerWorkflow": expected["signerWorkflow"],
            "signerCommit": runner["releaseManifest"]["release"]["workflow"]["contractCommit"],
            "verified": True,
        }
        for runner in candidate["fleet"]["runners"]
    ]


class HostExportRequestTests(unittest.TestCase):
    def test_host_export_requests_bind_the_manifest_fleet_and_admitted_plan(self):
        config = configuration()
        candidate = evidence()
        contract_ref = "b" * 40
        plan = controller.build_plan(
            config,
            candidate,
            "production",
            deployment_contract_ref=contract_ref,
        )

        with mock.patch.dict(
            controller.os.environ,
            {"VERJSON_DEPLOYMENT_CONTRACT_REF": contract_ref},
            clear=False,
        ):
            baseline = controller._build_host_export_request(
                config, candidate, "production"
            )
        post_update = controller._build_host_export_request(
            config,
            candidate,
            "production",
            purpose="post-update",
            runner_name="gha-gate-1",
            plan=plan,
        )
        capacity = controller._build_host_export_request(
            config, candidate, "production", purpose="capacity", plan=plan
        )

        self.assertEqual("host-export", baseline["operation"])
        self.assertEqual(candidate["manifestIdentity"], baseline["release"]["manifestDigest"])
        self.assertEqual(["gha-gate-1", "gha-gate-2", "gha-gate-3"], baseline["hostExport"]["runnerNames"])
        self.assertEqual("baseline", baseline["hostExport"]["purpose"])
        self.assertEqual("gha-gate-1", post_update["hostExport"]["runnerName"])
        self.assertEqual(controller.canonical_digest(plan), post_update["planDigest"])
        self.assertEqual("capacity", capacity["hostExport"]["purpose"])
        self.assertIsNone(capacity["hostExport"]["runnerName"])
        self.assertEqual(contract_ref, baseline["deploymentContractCommit"])
        self.assertNotIn("ssh-private-key-material", json.dumps(baseline))

    def test_host_export_rejects_request_not_bound_to_admitted_plan(self):
        config = configuration()
        candidate = evidence()
        plan = controller.build_plan(config, candidate, "production")
        adapter = controller.ProcessAdapter(config, {}, candidate, plan)
        request = {"planDigest": "sha256:" + "0" * 64}

        with mock.patch.object(controller, "_build_host_export_request", return_value=request), \
                mock.patch.object(controller, "_run_host_export_transport") as run:
            with self.assertRaisesRegex(
                controller.DeploymentError, "does not bind admitted plan"
            ):
                adapter._host_export("post-update", "gha-gate-1")

        run.assert_not_called()

    def test_host_export_transport_forwards_runner_temp_to_child(self):
        request = {"operation": "host-export"}
        receipt = {
            "schemaVersion": 1,
            "requestDigest": controller.canonical_digest(request),
            "outcome": "passed",
        }
        secrets = {name: "credential" for name in controller.HOST_EXPORT_SECRET_ENV}

        with tempfile.TemporaryDirectory() as runner_temp:
            def run(command, **kwargs):
                self.assertEqual(runner_temp, kwargs["env"]["RUNNER_TEMP"])
                output = Path(command[command.index("--output") + 1])
                output.write_text(json.dumps(receipt), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            environment = {"PATH": "/usr/bin", "RUNNER_TEMP": runner_temp, **secrets}
            with mock.patch.dict(controller.os.environ, environment, clear=True), \
                    mock.patch.object(controller.subprocess, "run", side_effect=run):
                result = controller._run_host_export_transport(request)

        self.assertEqual(receipt, result)

    def test_host_export_collection_stops_before_legacy_evidence_when_authority_is_missing(self):
        config = configuration()
        candidate = evidence()
        with mock.patch.dict(controller.os.environ, {}, clear=True):
            with mock.patch.object(controller.ProcessAdapter, "_run") as run:
                with self.assertRaisesRegex(
                    controller.DeploymentError,
                    "read-only host observation authority not provisioned",
                ):
                    controller._collect_evidence(
                        config, candidate["manifestIdentity"], "production"
                    )
        run.assert_not_called()

    def test_preview_collects_a_run_bound_plan_without_deployment_authorization(self):
        config = configuration()
        candidate = evidence()
        candidate.pop("authorization")
        environment = {
            **{name: "credential" for name in controller.HOST_EXPORT_SECRET_ENV},
            "GITHUB_REPOSITORY": "Verjson/verjson-github-runner",
            "GITHUB_REPOSITORY_ID": "42",
            "GITHUB_RUN_ID": "9001",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": candidate["headCommit"],
            "GITHUB_HEAD_TREE": candidate["headTree"],
            "VERJSON_DEPLOYMENT_CONTRACT_REF": "a" * 40,
        }

        def export(request):
            result = copy.deepcopy(candidate["hostExport"])
            result["requestDigest"] = controller.canonical_digest(request)
            result["releaseManifest"] = {
                "manifestIdentity": candidate["manifestIdentity"],
                "manifestBytes": candidate["manifestBytes"],
                "manifest": candidate["manifest"],
                "attestation": candidate["attestation"],
            }
            return result

        with mock.patch.dict(controller.os.environ, environment, clear=True), \
                mock.patch.object(controller.ProcessAdapter, "_run", return_value=candidate), \
                mock.patch.object(controller, "_run_host_export_transport", side_effect=export):
            collected = controller._collect_evidence(
                config, candidate["manifestIdentity"], "production", preview=True
            )
        plan = controller.build_plan(
            config, collected, "production", preview=True,
            deployment_contract_ref="a" * 40,
        )

        self.assertNotIn("authorization", collected)
        self.assertEqual(42, collected["observationAuthority"]["repositoryId"])
        self.assertEqual("9001.1", plan["attemptId"])
        self.assertIs(plan["preview"], True)
        with self.assertRaisesRegex(controller.DeploymentError, "preview plan cannot be admitted"):
            controller.validate_deployment_plan(
                plan, config, collected, TEST_NOW,
                fleet_selector="production", action="deploy",
                deployment_contract_ref="a" * 40,
            )
        with self.assertRaisesRegex(controller.DeploymentError, "preview evidence cannot authorize deployment"):
            controller.build_plan(config, collected, "production")
        with self.assertRaisesRegex(controller.DeploymentError, "preview plan cannot be executed"):
            controller.execute_plan(plan, config, collected, mock.Mock(), mock.Mock())

    def test_preview_rejects_deployment_authorization_and_missing_run_identity(self):
        candidate = evidence()
        environment = {
            **{name: "credential" for name in controller.HOST_EXPORT_SECRET_ENV},
            "GITHUB_REPOSITORY": "Verjson/verjson-github-runner",
            "GITHUB_REPOSITORY_ID": "42",
            "GITHUB_RUN_ID": "9001",
            "GITHUB_RUN_ATTEMPT": "1",
        }
        with mock.patch.dict(controller.os.environ, environment, clear=True), \
                mock.patch.object(controller.ProcessAdapter, "_run", return_value=candidate), \
                mock.patch.object(controller, "_run_host_export_transport") as export:
            with self.assertRaisesRegex(controller.DeploymentError, "must omit deployment authorization"):
                controller._collect_evidence(
                    configuration(), candidate["manifestIdentity"], "production", preview=True
                )
        export.assert_not_called()

        candidate.pop("authorization")
        environment.pop("GITHUB_RUN_ATTEMPT")
        with mock.patch.dict(controller.os.environ, environment, clear=True), \
                mock.patch.object(controller.ProcessAdapter, "_run", return_value=candidate), \
                mock.patch.object(controller, "_run_host_export_transport") as export:
            with self.assertRaisesRegex(controller.DeploymentError, "preview workflow identity is unavailable"):
                controller._collect_evidence(
                    configuration(), candidate["manifestIdentity"], "production", preview=True
                )
        export.assert_not_called()

    def test_mutating_collection_still_requires_run_bound_authorization(self):
        candidate = evidence()
        candidate.pop("authorization")
        environment = {
            **{name: "credential" for name in controller.HOST_EXPORT_SECRET_ENV},
            "GITHUB_RUN_ID": "9001",
        }
        with mock.patch.dict(controller.os.environ, environment, clear=True), \
                mock.patch.object(controller.ProcessAdapter, "_run", return_value=candidate), \
                mock.patch.object(controller, "_run_host_export_transport") as export:
            with self.assertRaisesRegex(
                controller.DeploymentError, "evidence workflow run differs from workflow authority"
            ):
                controller._collect_evidence(
                    configuration(), candidate["manifestIdentity"], "production"
                )
        export.assert_not_called()


class FakeAdapter:
    def __init__(
        self,
        failing_probe: str | None = None,
        result_overrides: dict | None = None,
        fail_update: str | None = None,
        interrupt_update: str | None = None,
    ):
        self.calls = []
        self.failing_probe = failing_probe
        self.result_overrides = result_overrides or {}
        self.fail_update = fail_update
        self.interrupt_update = interrupt_update

    def update_runner(self, runner, manifest_identity, variant, timeout_seconds):
        self.calls.append(("update", runner, manifest_identity, variant, timeout_seconds))
        if runner == self.fail_update:
            raise controller.DeploymentError("bounded drain timed out")
        if runner == self.interrupt_update:
            raise controller.DeploymentInterrupted("runner state is not terminal")
        result = {
            "beforeDigest": "sha256:" + "1" * 64,
            "afterDigest": "sha256:" + "3" * 64,
            "drained": True,
            "online": True,
            "admitted": True,
            "labels": ["gate", "pwsh"],
            "tools": ["pwsh"],
            "runnerGroup": "trusted-production",
            "healthy": True,
            "transactionLocked": False,
            "manifestIdentity": manifest_identity,
            "availableCapacity": 3,
        }
        result.update(self.result_overrides)
        return result

    def available_capacity(self):
        self.calls.append(("capacity",))
        return 3

    def probe_runner(self, runner, timeout_seconds):
        self.calls.append(("probe", runner, timeout_seconds))
        return {
            "outcome": "failed" if runner == self.failing_probe else "passed",
            "routedRunner": runner,
        }

    def observe(self, seconds):
        self.calls.append(("observe", seconds))


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 8, 14, tzinfo=timezone.utc)

    def now(self):
        current = self.value
        self.value = current.replace(second=current.second + 1)
        return current


class DeploymentPlannerTests(unittest.TestCase):
    def test_admits_exact_published_release_asset_with_mocked_verified_attestation(self):
        path = MODULE_PATH.parent / "fixtures/container-deployment/release-manifest-v0.2.1.json"
        raw = path.read_bytes().decode("utf-8")
        candidate = evidence()
        candidate["manifest"] = json.loads(raw)
        bind_manifest_bytes(candidate, raw)
        self.assertEqual(
            "sha256:4f5bb96e1fe07f7b56cfe124206ed85c4e59b9715b3b4e18b3054d890dd1ad32",
            candidate["manifestIdentity"],
        )
        self.assertNotEqual(controller.canonical_digest(candidate["manifest"]), candidate["manifestIdentity"])
        config = configuration()
        contract = candidate["manifest"]["release"]["workflow"]["contractCommit"]
        config["expectedRelease"]["contractCommit"] = contract
        config["expectedRelease"]["variant"] = "base"
        candidate["attestation"]["contractCommit"] = contract
        refresh_host_export_binding(candidate, config)

        plan = controller.build_plan(config, candidate, "production")

        self.assertEqual(candidate["manifestIdentity"], plan["selectedRelease"]["manifestDigest"])
        self.assertEqual("0.2.1", plan["selectedRelease"]["releaseVersion"])
        self.assertEqual(candidate["manifest"]["images"][0]["indexDigest"], plan["targetDigest"])

    def test_exact_unicode_and_line_endings_are_part_of_release_identity(self):
        candidate = evidence()
        candidate["manifest"]["description"] = "déploiement"
        raw = json.dumps(candidate["manifest"], indent=2, ensure_ascii=False) + "\n"
        for serialized in (raw, raw.replace("\n", "\r\n"), raw.rstrip("\n")):
            with self.subTest(serialized=repr(serialized[-4:])):
                bind_manifest_bytes(candidate, serialized)
                plan = controller.build_plan(configuration(), candidate, "production")
                self.assertEqual(candidate["manifestIdentity"], plan["selectedRelease"]["manifestDigest"])
                candidate["manifestBytes"] = serialized + " "
                with self.assertRaisesRegex(controller.DeploymentError, "bytes differ from release identity"):
                    controller.build_plan(configuration(), candidate, "production")
        bind_manifest_bytes(candidate, raw)
        candidate["manifestBytes"] = raw.replace("é", "e")
        with self.assertRaisesRegex(controller.DeploymentError, "bytes differ from release identity"):
            controller.build_plan(configuration(), candidate, "production")

    def test_raw_manifest_cannot_be_substituted_by_a_different_structured_object(self):
        for replacement in (True, 1.0, 2):
            with self.subTest(replacement=replacement):
                candidate = published_format_evidence()
                candidate["manifest"]["schemaVersion"] = replacement
                with self.assertRaisesRegex(controller.DeploymentError, "bytes differ from structured manifest"):
                    controller.build_plan(configuration(), candidate, "production")

    def test_rejects_ambiguous_or_malformed_attested_json(self):
        for raw, expected in (
            ('{"schemaVersion": 1, "schemaVersion": 1}', "duplicate JSON keys"),
            ('{"value": NaN}', "non-finite"),
            ('{"value": Infinity}', "non-finite"),
            ('{"value": -Infinity}', "non-finite"),
            ('{"value": 1e999}', "non-finite"),
            ('{', "not valid UTF-8 JSON"),
            ('{"value":' + '9' * 5000 + '}', "not valid UTF-8 JSON"),
            ('[]', "must be an object"),
            ('{"value": "\\ud800"}', "not valid UTF-8 JSON"),
        ):
            with self.subTest(raw=raw[:80]):
                candidate = evidence()
                bind_manifest_bytes(candidate, raw)
                with self.assertRaisesRegex(controller.DeploymentError, expected):
                    controller.build_plan(configuration(), candidate, "production")

    def test_rejects_invalid_or_oversized_raw_text_before_admission(self):
        for raw in (None, 42, {}, "\ud800", "x" * (controller.MAX_MANIFEST_BYTES + 1),
                    "é" * (controller.MAX_MANIFEST_BYTES // 2 + 1)):
            with self.subTest(raw_type=type(raw).__name__):
                candidate = evidence()
                candidate["manifestBytes"] = raw
                with self.assertRaises(controller.DeploymentError):
                    controller.build_plan(configuration(), candidate, "production")

    def test_pretty_asset_requires_exact_bytes_and_matching_attestation(self):
        for mutation, expected in (
            (lambda value: value.pop("manifestBytes"), "canonical bytes differ"),
            (lambda value: value["attestation"].update(subjectDigest=controller.canonical_digest(value["manifest"])), "subject digest"),
            (lambda value: value["attestation"].update(verified=False), "not verified"),
            (lambda value: value["attestation"].update(repository="Attacker/repo"), "source repository"),
            (lambda value: value["attestation"].update(sourceRef="refs/heads/feature"), "source ref"),
            (lambda value: value["attestation"].update(signerWorkflow="Attacker/release.yml"), "signer"),
            (lambda value: value["attestation"].update(contractCommit="b" * 40), "contract pin"),
        ):
            with self.subTest(expected=expected):
                candidate = published_format_evidence()
                mutation(candidate)
                with self.assertRaisesRegex(controller.DeploymentError, expected):
                    controller.build_plan(configuration(), candidate, "production")

    def test_builds_immutable_canary_first_sequential_plan(self):
        candidate = evidence()
        candidate["requestedAt"] = "2026-08-14T00:00:00Z"
        candidate["attestation"]["expiresAt"] = "2026-08-15T00:00:00Z"
        plan = controller.build_plan(
            configuration(),
            candidate,
            "production",
            now=datetime(2026, 8, 14, tzinfo=timezone.utc),
        )

        self.assertEqual(
            ["gha-gate-1", "gha-gate-2", "gha-gate-3"],
            [step["runner"] for step in plan["steps"]],
        )
        self.assertEqual(["canary", "rollout", "rollout"], [s["phase"] for s in plan["steps"]])
        self.assertEqual("sequential", plan["rolloutMode"])

    def test_admission_rejects_a_tampered_rollout_plan(self):
        candidate = evidence()
        plan = controller.build_plan(
            configuration(), candidate, "production", now=TEST_NOW
        )
        plan["steps"][0]["runner"] = plan["steps"][1]["runner"]
        code, error, receipts = invoke_admission(plan, candidate)
        self.assertEqual(1, code, error)
        self.assertIn("differs from reviewed configuration", error)
        self.assertEqual([], receipts)

    def test_admission_rejects_incomplete_review_authority(self):
        candidate = evidence()
        plan = controller.build_plan(
            configuration(), candidate, "production", now=TEST_NOW
        )
        candidate["authorization"]["reviewGates"].pop()
        code, error, receipts = invoke_admission(plan, candidate)
        self.assertEqual(1, code, error)
        self.assertIn("exactly three review gates", error)
        self.assertEqual([], receipts)

    def test_admission_rejects_plan_selectors_that_differ_from_dispatch(self):
        for field, value in (
            ("fleetSelector", "staging"),
            ("action", "rollback"),
            ("deploymentContractCommit", "a" * 40),
        ):
            with self.subTest(field=field):
                candidate = evidence()
                plan = controller.build_plan(
                    configuration(), candidate, "production", now=TEST_NOW
                )
                plan[field] = value
                code, error, receipts = invoke_admission(plan, candidate)
                self.assertEqual(1, code, error)
                self.assertIn("selectors differ from the reviewed request", error)
                self.assertEqual([], receipts)

    def test_admission_persists_receipt_for_a_valid_plan(self):
        candidate = evidence()
        plan = controller.build_plan(
            configuration(), candidate, "production", now=TEST_NOW
        )

        code, error, receipts = invoke_admission(plan, candidate)

        self.assertEqual(0, code, error)
        self.assertEqual(1, len(receipts))
        self.assertEqual("admitted", receipts[0]["outcome"])
        self.assertEqual(plan["attemptId"], receipts[0]["attemptId"])

    def test_retained_plan_admission_validates_authority_and_persists_receipt(self):
        config = configuration()
        candidate = evidence()
        plan = controller.build_plan(
            config, candidate, "production", now=TEST_NOW
        )
        admitted = controller.admitted_receipt(plan, config, candidate, TEST_NOW)
        pending = controller._next_revision(
            admitted,
            outcome="in_progress",
            runners=[
                {
                    "name": "gha-gate-1",
                    "beforeDigest": "sha256:" + "1" * 64,
                    "afterDigest": "sha256:" + "3" * 64,
                    "afterRelease": copy.deepcopy(plan["selectedRelease"]),
                    "state": "updated",
                    "probe": "not_run",
                    "observation": "pending",
                    "completedAt": None,
                }
            ],
            completed_at=None,
        )
        candidate["fleet"]["runners"][0]["release"] = copy.deepcopy(
            plan["selectedRelease"]
        )
        candidate["retainedPlan"] = copy.deepcopy(plan)
        candidate["retainedRevisions"] = [admitted, pending]
        candidate["retainedReceiptAuthority"] = (
            f"{pending['attemptId']}/42@"
            f"{controller.retained_authority_digest(plan, [admitted, pending])}"
        )

        code, error, receipts = invoke_admission(plan, candidate)

        self.assertEqual(0, code, error)
        self.assertEqual(2, len(receipts))
        self.assertEqual("admitted", receipts[0]["outcome"])
        self.assertEqual("in_progress", receipts[1]["outcome"])

        candidate["authorization"]["reviewGates"].pop()
        code, error, receipts = invoke_admission(plan, candidate)
        self.assertEqual(1, code, error)
        self.assertIn("exactly three review gates", error)
        self.assertEqual([], receipts)


    def test_rejects_mutable_manifest_tag(self):
        candidate = evidence()
        candidate["manifestIdentity"] = (
            "ghcr.io/verjson/verjson-github-runner-release:stable"
        )

        with self.assertRaisesRegex(controller.DeploymentError, "immutable digest"):
            controller.build_plan(configuration(), candidate, "production")

    def test_rejects_legacy_registry_qualified_manifest_identity(self):
        candidate = evidence()
        candidate["manifestIdentity"] = (
            "ghcr.io/verjson/verjson-github-runner-release@"
            + candidate["manifestIdentity"]
        )

        with self.assertRaisesRegex(controller.DeploymentError, "immutable digest"):
            controller.build_plan(configuration(), candidate, "production")

    def test_rejects_substituted_signer_source_or_contract_pin(self):
        for field, value, expected in (
            ("signerWorkflow", "Attacker/repo/.github/workflows/release.yml", "signer"),
            ("sourceRef", "refs/heads/feature", "source ref"),
            ("contractCommit", "b" * 40, "contract pin"),
        ):
            with self.subTest(field=field):
                candidate = evidence()
                candidate["attestation"][field] = value
                with self.assertRaisesRegex(controller.DeploymentError, expected):
                    controller.build_plan(configuration(), candidate, "production")

    def test_rejects_tampered_or_unverified_manifest_attestation(self):
        for field, value, expected in (
            ("subjectDigest", "sha256:" + "9" * 64, "subject digest"),
            ("verified", False, "not verified"),
        ):
            with self.subTest(field=field):
                candidate = evidence()
                candidate["attestation"][field] = value
                with self.assertRaisesRegex(controller.DeploymentError, expected):
                    controller.build_plan(configuration(), candidate, "production")

    def test_rejects_rollout_digest_mutation_without_new_manifest_identity(self):
        candidate = evidence()
        candidate["manifest"]["images"][0]["indexDigest"] = "sha256:" + "4" * 64

        with self.assertRaisesRegex(controller.DeploymentError, "canonical bytes differ"):
            controller.build_plan(configuration(), candidate, "production")

    def test_rejects_option_shaped_dynamic_cli_tokens(self):
        mutations = (
            ("lane", "--help"),
            ("project", "-danger"),
            ("runnerGroup", "--group"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                candidate = configuration()
                candidate["fleets"]["production"][field] = value
                with self.assertRaisesRegex(controller.DeploymentError, "safe token"):
                    controller.build_plan(candidate, evidence(), "production")

        candidate = configuration()
        candidate["expectedRelease"]["variant"] = "--variant"
        with self.assertRaisesRegex(controller.DeploymentError, "safe token"):
            controller.build_plan(candidate, evidence(), "production")

    def test_rejects_fleet_timing_without_fifteen_minute_job_margin(self):
        candidate = configuration()
        fleet = candidate["fleets"]["production"]
        fleet["drainTimeoutSeconds"] = 1_000
        fleet["probeTimeoutSeconds"] = 900
        fleet["observationSeconds"] = 900
        fixture = evidence()
        refresh_host_export_binding(fixture, candidate)

        with self.assertRaisesRegex(controller.DeploymentError, "job margin"):
            controller.build_plan(candidate, fixture, "production")

    def test_rejects_inventory_drift_and_insufficient_capacity(self):
        missing = evidence()
        missing["fleet"]["runners"].pop()
        with self.assertRaisesRegex(controller.DeploymentError, "inventory"):
            controller.build_plan(configuration(), missing, "production")

        insufficient = configuration()
        insufficient["fleets"]["production"]["minimumAvailable"] = 3
        insufficient_evidence = evidence()
        refresh_host_export_binding(insufficient_evidence, insufficient)
        with self.assertRaisesRegex(controller.DeploymentError, "capacity"):
            controller.build_plan(insufficient, insufficient_evidence, "production")

        boolean_policy = configuration()
        boolean_policy["fleets"]["production"]["minimumAvailable"] = True
        boolean_evidence = evidence()
        refresh_host_export_binding(boolean_evidence, boolean_policy)
        with self.assertRaisesRegex(controller.DeploymentError, "minimum fleet capacity"):
            controller.build_plan(boolean_policy, boolean_evidence, "production")

    def test_rejects_unexpected_fleet_baseline(self):
        candidate = evidence()
        candidate["fleet"]["runners"][2]["release"] = release("0.9.0", "9")
        with self.assertRaisesRegex(controller.DeploymentError, "baseline"):
            controller.build_plan(configuration(), candidate, "production")

    def test_rejects_expired_attestation_stale_request_or_concurrent_deployment(self):
        fixtures = []
        expired = evidence()
        expired["attestation"]["expiresAt"] = "2026-08-13T23:59:59Z"
        expired["requestedAt"] = "2026-08-14T00:00:00Z"
        fixtures.append((expired, "expired"))
        stale = evidence()
        stale["requestedAt"] = "2026-08-13T22:00:00Z"
        fixtures.append((stale, "stale"))
        concurrent = evidence()
        concurrent["requestedAt"] = "2026-08-14T00:00:00Z"
        concurrent["activeDeploymentCount"] = 1
        fixtures.append((concurrent, "concurrent"))

        for candidate, expected in fixtures:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(controller.DeploymentError, expected):
                    controller.build_plan(
                        configuration(),
                        candidate,
                        "production",
                        now=datetime(2026, 8, 14, 0, 30, tzinfo=timezone.utc),
                    )


class DeploymentExecutionTests(unittest.TestCase):
    def test_pretty_asset_reconciliation_preserves_exact_selected_and_baseline_identity(self):
        for selected in (False, True):
            with self.subTest(selected=selected):
                candidate = published_format_evidence()
                plan = controller.build_plan(configuration(), candidate, "production")
                interrupted = controller.execute_plan(
                    plan, configuration(), candidate,
                    FakeAdapter(interrupt_update="gha-gate-1"), lambda _receipt: None,
                    clock=FakeClock(),
                )
                live = copy.deepcopy(candidate)
                runner = live["fleet"]["runners"][0]
                runner["deployedDigest"] = "sha256:" + "1" * 64
                if selected:
                    runner["release"] = plan["selectedRelease"]
                    runner["manifestIdentity"] = plan["manifestIdentity"]
                    runner["deployedDigest"] = plan["targetDigest"]
                reconciled = controller.reconcile_unknown_state(plan, interrupted, live, configuration())
                self.assertEqual(runner["release"], reconciled["finalFleet"][0]["release"])
                if selected:
                    live["manifestBytes"] += " "
                else:
                    runner["releaseManifestBytes"] += " "
                with self.assertRaisesRegex(controller.DeploymentError, "bytes differ from release identity"):
                    controller.reconcile_unknown_state(plan, interrupted, live, configuration())

    def test_rollback_uses_exact_published_baseline_bytes(self):
        candidate = published_format_evidence()
        plan = controller.build_plan(configuration(), candidate, "production")
        source = controller.admitted_receipt(plan, configuration(), candidate, FakeClock().now())
        source["outcome"] = "failed"
        source["completedAt"] = "2026-08-14T00:01:00Z"
        rollback = copy.deepcopy(candidate)
        rollback["rollbackSource"] = source
        baseline = rollback["fleet"]["runners"][0]
        rollback["manifest"] = baseline["releaseManifest"]
        bind_manifest_bytes(rollback, baseline["releaseManifestBytes"])

        rollback_plan = controller.build_plan(
            configuration(), rollback, "production", action="rollback", rollback_source=source
        )

        self.assertEqual(source["observedDeployedRelease"], rollback_plan["selectedRelease"])
        adapter = FakeAdapter()
        controller.execute_plan(rollback_plan, configuration(), rollback, adapter,
                                lambda _receipt: None, clock=FakeClock())
        self.assertTrue(all(call[2] == baseline["manifestIdentity"]
                            for call in adapter.calls if call[0] == "update"))

    def test_accepts_realistic_protected_branch_environment_payload(self):
        controller.validate_environment_policy(
            {
                "deployment_branch_policy": {
                    "protected_branches": True,
                    "custom_branch_policies": False,
                },
                "protection_rules": [
                    {"id": 901, "node_id": "EPR_kwDO", "type": "branch_policy"}
                ],
                "can_admins_bypass": True,
            }
        )

    def test_rejects_bypassable_environment_protection_rule(self):
        for extra in (
            {"type": "required_reviewers", "reviewers": []},
            {"type": "wait_timer", "wait_timer": 5},
            {"type": "custom_protection_rule", "app": {"id": 44}},
        ):
            with self.subTest(extra=extra), self.assertRaisesRegex(
                controller.DeploymentError, "branch-policy rule"
            ):
                controller.validate_environment_policy(
                    {
                        "deployment_branch_policy": {
                            "protected_branches": True,
                            "custom_branch_policies": False,
                        },
                        "protection_rules": [{"type": "branch_policy"}, extra],
                        "can_admins_bypass": True,
                    }
                )
    def test_persists_admission_before_any_mutation_and_rolls_out_sequentially(self):
        adapter = FakeAdapter()
        persisted = []
        plan = controller.build_plan(configuration(), evidence(), "production")

        final = controller.execute_plan(
            plan,
            configuration(),
            evidence(),
            adapter,
            lambda receipt: persisted.append(copy.deepcopy(receipt)),
            clock=FakeClock(),
        )

        self.assertEqual("admitted", persisted[0]["outcome"])
        self.assertEqual([], persisted[0]["runners"])
        self.assertEqual("succeeded", final["outcome"])
        controller.validate_receipt(final)
        self.assertEqual(
            ["gha-gate-1", "gha-gate-2", "gha-gate-3"],
            [entry["name"] for entry in final["runners"]],
        )
        self.assertEqual(
            [
                "capacity",
                "update",
                "probe",
                "observe",
                "capacity",
                "update",
                "probe",
                "capacity",
                "update",
                "probe",
            ],
            [call[0] for call in adapter.calls],
        )
        self.assertTrue(
            all(
                receipt["previousReceiptDigest"]
                == controller.receipt_digest(previous)
                for previous, receipt in zip(persisted, persisted[1:])
            )
        )

    def test_canary_failure_stops_before_observation_or_rollout(self):
        adapter = FakeAdapter(failing_probe="gha-gate-1")
        persisted = []

        final = controller.execute_plan(
            controller.build_plan(configuration(), evidence(), "production"),
            configuration(),
            evidence(),
            adapter,
            lambda receipt: persisted.append(copy.deepcopy(receipt)),
            clock=FakeClock(),
        )

        self.assertEqual("failed", final["outcome"])
        self.assertEqual(
            ["capacity", "update", "probe"], [call[0] for call in adapter.calls]
        )
        self.assertEqual("failed", final["runners"][0]["probe"])
        self.assertEqual(final["selectedRelease"], final["finalFleet"][0]["release"])
        self.assertEqual("verified", final["finalFleet"][0]["state"])
        self.assertEqual("not_run", persisted[-2]["runners"][0]["probe"])
        self.assertEqual(final["selectedRelease"], persisted[-2]["finalFleet"][0]["release"])

    def test_observation_interruption_resumes_observation_without_starting_rollout(self):
        plan = controller.build_plan(configuration(), evidence(), "production")
        crashing = FakeAdapter()
        persisted = []

        def crash_during_observation(_seconds):
            crashing.calls.append(("observe",))
            raise KeyboardInterrupt("simulated cancellation")

        crashing.observe = crash_during_observation
        with self.assertRaises(KeyboardInterrupt):
            controller.execute_plan(
                plan,
                configuration(),
                evidence(),
                crashing,
                lambda receipt: persisted.append(copy.deepcopy(receipt)),
                max_hosts=1,
                clock=FakeClock(),
            )

        retained = persisted[-1]
        self.assertEqual("pending", retained["runners"][0]["observation"])
        self.assertIsNone(retained["runners"][0]["completedAt"])
        live = evidence()
        live["fleet"]["runners"][0]["release"] = copy.deepcopy(plan["selectedRelease"])
        live["fleet"]["runners"][0]["manifestIdentity"] = plan["manifestIdentity"]
        live["fleet"]["runners"][0]["deployedDigest"] = plan["targetDigest"]
        resumed = FakeAdapter()
        final = controller.execute_plan(
            plan,
            configuration(),
            live,
            resumed,
            lambda _receipt: None,
            previous_receipt=retained,
            max_hosts=1,
            clock=FakeClock(),
        )

        self.assertEqual(["observe"], [call[0] for call in resumed.calls])
        self.assertEqual("passed", final["runners"][0]["observation"])

    def test_rejects_failed_admission_evidence_before_touching_next_host(self):
        cases = (
            ({"drained": False}, "drain"),
            ({"online": False}, "online"),
            ({"labels": ["gate"]}, "labels"),
            ({"tools": []}, "tools"),
            ({"runnerGroup": "wrong"}, "runner group"),
            ({"healthy": False}, "health"),
            ({"transactionLocked": True}, "transaction lock"),
            ({"afterDigest": "sha256:" + "4" * 64}, "digest"),
            ({"availableCapacity": True}, "available capacity"),
        )
        for overrides, expected in cases:
            with self.subTest(expected=expected):
                adapter = FakeAdapter(result_overrides=overrides)
                persisted = []
                final = controller.execute_plan(
                    controller.build_plan(configuration(), evidence(), "production"),
                    configuration(),
                    evidence(),
                    adapter,
                    lambda receipt: persisted.append(copy.deepcopy(receipt)),
                    clock=FakeClock(),
                )
                self.assertEqual("failed", final["outcome"])
                self.assertRegex(final["failure"], expected)
                self.assertEqual(
                    ["gha-gate-1"],
                    [call[1] for call in adapter.calls if call[0] == "update"],
                )

    def test_rejects_probe_routed_to_another_host(self):
        adapter = FakeAdapter()

        def routed_elsewhere(runner, timeout_seconds):
            adapter.calls.append(("probe", runner, timeout_seconds))
            return {"outcome": "passed", "routedRunner": "gha-gate-2"}

        adapter.probe_runner = routed_elsewhere
        final = controller.execute_plan(
            controller.build_plan(configuration(), evidence(), "production"),
            configuration(),
            evidence(),
            adapter,
            lambda _receipt: None,
            clock=FakeClock(),
        )
        self.assertEqual("failed", final["outcome"])
        self.assertRegex(final["failure"], "probe routing")

    def test_mid_fleet_interruption_retains_progress_and_stops(self):
        adapter = FakeAdapter(interrupt_update="gha-gate-2")
        persisted = []
        final = controller.execute_plan(
            controller.build_plan(configuration(), evidence(), "production"),
            configuration(),
            evidence(),
            adapter,
            lambda receipt: persisted.append(copy.deepcopy(receipt)),
            clock=FakeClock(),
        )
        self.assertEqual("interrupted", final["outcome"])
        self.assertEqual(
            ["gha-gate-1", "gha-gate-2"],
            [r["name"] for r in final["runners"]],
        )
        self.assertEqual("unknown", final["runners"][1]["state"])
        self.assertIsNone(final["finalFleet"][1]["release"])
        self.assertNotIn("gha-gate-3", [call[1] for call in adapter.calls if call[0] == "update"])

    def test_capacity_drop_stops_before_next_runner_mutation(self):
        adapter = FakeAdapter()
        capacities = iter((3, 2))

        def capacity():
            adapter.calls.append(("capacity",))
            return next(capacities)

        adapter.available_capacity = capacity
        final = controller.execute_plan(
            controller.build_plan(configuration(), evidence(), "production"),
            configuration(),
            evidence(),
            adapter,
            lambda _receipt: None,
            clock=FakeClock(),
        )
        self.assertEqual("failed", final["outcome"])
        self.assertRegex(final["failure"], "capacity")
        self.assertEqual(
            ["gha-gate-1"], [call[1] for call in adapter.calls if call[0] == "update"]
        )

    def test_dry_run_has_no_receipt_or_runner_side_effect(self):
        adapter = FakeAdapter()
        persisted = []
        plan = controller.build_plan(configuration(), evidence(), "production")

        result = controller.execute_plan(
            plan,
            configuration(),
            evidence(),
            adapter,
            persisted.append,
            dry_run=True,
        )

        self.assertIs(result, plan)
        self.assertEqual([], adapter.calls)
        self.assertEqual([], persisted)

    def test_failed_admission_persistence_prevents_mutation(self):
        adapter = FakeAdapter()

        def fail_persist(_receipt):
            raise OSError("retention unavailable")

        with self.assertRaisesRegex(OSError, "retention unavailable"):
            controller.execute_plan(
                controller.build_plan(configuration(), evidence(), "production"),
                configuration(),
                evidence(),
                adapter,
                fail_persist,
            )

        self.assertEqual([], adapter.calls)

    def test_execution_rechecks_runner_admission_before_capacity_or_update(self):
        plan = controller.build_plan(configuration(), evidence(), "production")
        mutations = (
            ("online", False, "online"),
            ("busy", True, "idle"),
            ("admitted", False, "admitted"),
            ("runnerGroup", "untrusted", "group"),
            ("labels", ["gate"], "labels"),
            ("tools", [], "tools"),
        )
        for field, value, expected in mutations:
            candidate = evidence()
            candidate["fleet"]["runners"][0][field] = value
            adapter = FakeAdapter()
            with self.subTest(field=field), self.assertRaisesRegex(
                controller.DeploymentError, expected
            ):
                controller.execute_plan(
                    plan,
                    configuration(),
                    candidate,
                    adapter,
                    lambda _receipt: self.fail("admission receipt was persisted"),
                    clock=FakeClock(),
                )
            self.assertEqual([], adapter.calls)

    def test_second_host_refresh_rejects_stale_manifest_identity_before_mutation(self):
        candidate = published_format_evidence()
        plan = controller.build_plan(configuration(), candidate, "production")
        persisted = []
        controller.execute_plan(
            plan,
            configuration(),
            candidate,
            FakeAdapter(),
            lambda receipt: persisted.append(copy.deepcopy(receipt)),
            max_hosts=1,
            clock=FakeClock(),
        )
        refreshed = copy.deepcopy(candidate)
        refreshed["fleet"]["runners"][0]["release"] = copy.deepcopy(
            plan["selectedRelease"]
        )
        refreshed["fleet"]["runners"][0]["manifestIdentity"] = plan[
            "manifestIdentity"
        ]
        refreshed["fleet"]["runners"][0]["deployedDigest"] = plan["targetDigest"]
        refreshed["fleet"]["runners"][1]["manifestIdentity"] = plan[
            "manifestIdentity"
        ]
        adapter = FakeAdapter()

        with self.assertRaisesRegex(
            controller.DeploymentError, "refreshed manifest identity for gha-gate-2"
        ):
            controller.execute_plan(
                plan,
                configuration(),
                refreshed,
                adapter,
                lambda _receipt: self.fail("invalid refresh was persisted"),
                previous_receipt=persisted[-1],
                max_hosts=1,
                clock=FakeClock(),
            )

        self.assertEqual([], adapter.calls)

    def test_second_host_refresh_rejects_dishonest_deployed_digest_before_mutation(self):
        candidate = published_format_evidence()
        plan = controller.build_plan(configuration(), candidate, "production")
        persisted = []
        controller.execute_plan(
            plan,
            configuration(),
            candidate,
            FakeAdapter(),
            lambda receipt: persisted.append(copy.deepcopy(receipt)),
            max_hosts=1,
            clock=FakeClock(),
        )
        refreshed = copy.deepcopy(candidate)
        refreshed["fleet"]["runners"][0]["release"] = copy.deepcopy(
            plan["selectedRelease"]
        )
        refreshed["fleet"]["runners"][0]["manifestIdentity"] = plan[
            "manifestIdentity"
        ]
        refreshed["fleet"]["runners"][0]["deployedDigest"] = plan["targetDigest"]
        refreshed["fleet"]["runners"][1]["deployedDigest"] = plan["targetDigest"]
        adapter = FakeAdapter()

        with self.assertRaisesRegex(
            controller.DeploymentError, "refreshed deployed digest for gha-gate-2"
        ):
            controller.execute_plan(
                plan,
                configuration(),
                refreshed,
                adapter,
                lambda _receipt: self.fail("invalid refresh was persisted"),
                previous_receipt=persisted[-1],
                max_hosts=1,
                clock=FakeClock(),
            )

        self.assertEqual([], adapter.calls)

    def test_rollback_must_bind_failed_attempt_baseline(self):
        source = controller.admitted_receipt(
            controller.build_plan(configuration(), evidence(), "production"),
            configuration(),
            evidence(),
            FakeClock().now(),
        )
        source["outcome"] = "failed"
        source["completedAt"] = "2026-08-14T00:01:00Z"
        rollback_evidence = evidence()
        rollback_evidence["rollbackSource"] = source
        rollback_evidence["manifestIdentity"] = rollback_evidence["fleet"]["runners"][0][
            "manifestIdentity"
        ]
        rollback_evidence["manifest"] = release_manifest("1.0.0", "1")
        rollback_evidence["attestation"]["subjectDigest"] = rollback_evidence[
            "fleet"
        ]["runners"][0]["release"]["manifestDigest"]
        bind_manifest_bytes(
            rollback_evidence,
            json.dumps(rollback_evidence["manifest"], indent=2, sort_keys=True) + "\n",
        )

        plan = controller.build_plan(
            configuration(),
            rollback_evidence,
            "production",
            action="rollback",
            rollback_source=source,
        )

        self.assertEqual("rollback", plan["action"])
        self.assertEqual(source["attemptId"], plan["rollbackOfAttempt"]["attemptId"])

        adapter = FakeAdapter()
        controller.execute_plan(
            plan,
            configuration(),
            rollback_evidence,
            adapter,
            lambda _receipt: None,
            clock=FakeClock(),
        )
        self.assertTrue(
            all(
                call[2] == rollback_evidence["manifestIdentity"]
                for call in adapter.calls
                if call[0] == "update"
            )
        )

    def test_rollback_failure_stops_before_remaining_hosts(self):
        source_plan = controller.build_plan(configuration(), evidence(), "production")
        source = controller.admitted_receipt(
            source_plan, configuration(), evidence(), FakeClock().now()
        )
        source["outcome"] = "failed"
        source["completedAt"] = "2026-08-14T00:01:00Z"
        source["failure"] = "fixture failure"
        rollback_evidence = evidence()
        rollback_evidence["rollbackSource"] = source
        rollback_evidence["manifestIdentity"] = rollback_evidence["fleet"]["runners"][0][
            "manifestIdentity"
        ]
        rollback_evidence["manifest"] = release_manifest("1.0.0", "1")
        rollback_evidence["attestation"]["subjectDigest"] = rollback_evidence[
            "fleet"
        ]["runners"][0]["release"]["manifestDigest"]
        bind_manifest_bytes(
            rollback_evidence,
            json.dumps(rollback_evidence["manifest"], indent=2, sort_keys=True) + "\n",
        )
        plan = controller.build_plan(
            configuration(),
            rollback_evidence,
            "production",
            action="rollback",
            rollback_source=source,
        )
        adapter = FakeAdapter(interrupt_update="gha-gate-1")
        final = controller.execute_plan(
            plan,
            configuration(),
            rollback_evidence,
            adapter,
            lambda _receipt: None,
            clock=FakeClock(),
        )
        self.assertEqual("interrupted", final["outcome"])
        self.assertEqual(
            ["gha-gate-1"], [call[1] for call in adapter.calls if call[0] == "update"]
        )

    def test_partial_failure_rollback_accepts_only_recorded_mixed_fleet(self):
        source_plan = controller.build_plan(configuration(), evidence(), "production")
        source = controller.admitted_receipt(
            source_plan, configuration(), evidence(), FakeClock().now()
        )
        source["outcome"] = "failed"
        source["completedAt"] = "2026-08-14T00:01:00Z"
        source["failure"] = "probe failed"
        source["previousReceiptDigest"] = "sha256:" + "0" * 64
        source["finalFleet"][0]["release"] = copy.deepcopy(source["selectedRelease"])

        rollback_evidence = evidence()
        rollback_evidence["fleet"]["runners"][0]["release"] = copy.deepcopy(
            source["selectedRelease"]
        )
        rollback_evidence["fleet"]["runners"][0]["releaseManifest"] = release_manifest(
            source["selectedRelease"]["releaseVersion"], "3"
        )
        rollback_evidence["fleet"]["runners"][0]["manifestIdentity"] = source_plan[
            "manifestIdentity"
        ]
        rollback_evidence["manifestIdentity"] = rollback_evidence["fleet"]["runners"][1][
            "manifestIdentity"
        ]
        rollback_evidence["manifest"] = release_manifest("1.0.0", "1")
        rollback_evidence["attestation"]["subjectDigest"] = rollback_evidence[
            "fleet"
        ]["runners"][1]["release"]["manifestDigest"]
        bind_manifest_bytes(
            rollback_evidence,
            json.dumps(rollback_evidence["manifest"], indent=2, sort_keys=True) + "\n",
        )
        refresh_host_export_binding(rollback_evidence)

        plan = controller.build_plan(
            configuration(),
            rollback_evidence,
            "production",
            action="rollback",
            rollback_source=source,
        )
        self.assertEqual(source["observedDeployedRelease"], plan["selectedRelease"])

        rollback_evidence["fleet"]["runners"][2]["release"] = release("0.9.0", "9")
        with self.assertRaisesRegex(controller.DeploymentError, "source final state"):
            controller.build_plan(
                configuration(),
                rollback_evidence,
                "production",
                action="rollback",
                rollback_source=source,
            )

    def test_idempotent_resume_skips_recorded_completed_runners(self):
        adapter = FakeAdapter()
        persisted = []
        plan = controller.build_plan(configuration(), evidence(), "production")
        previous = controller.admitted_receipt(
            plan, configuration(), evidence(), FakeClock().now()
        )
        previous["outcome"] = "in_progress"
        previous["revision"] = 1
        previous["previousReceiptDigest"] = "sha256:" + "0" * 64
        previous["runners"] = [
            {
                "name": "gha-gate-1",
                "beforeDigest": "sha256:" + "1" * 64,
                "afterDigest": "sha256:" + "3" * 64,
                "afterRelease": copy.deepcopy(previous["selectedRelease"]),
                "state": "updated",
                "probe": "passed",
                "observation": "passed",
                "completedAt": "2026-08-14T00:00:01Z",
            }
        ]
        previous["finalFleet"][0]["release"] = copy.deepcopy(previous["selectedRelease"])

        resumed_evidence = evidence()
        resumed_evidence["fleet"]["runners"][0]["release"] = copy.deepcopy(
            previous["selectedRelease"]
        )
        resumed_evidence["fleet"]["runners"][0]["manifestIdentity"] = plan[
            "manifestIdentity"
        ]
        resumed_evidence["fleet"]["runners"][0]["deployedDigest"] = plan[
            "targetDigest"
        ]
        final = controller.execute_plan(
            plan,
            configuration(),
            resumed_evidence,
            adapter,
            lambda receipt: persisted.append(copy.deepcopy(receipt)),
            previous_receipt=previous,
            clock=FakeClock(),
        )

        self.assertEqual("succeeded", final["outcome"])
        self.assertNotIn("gha-gate-1", [call[1] for call in adapter.calls if call[0] == "update"])

    def test_restores_only_an_exact_authorized_receipt_chain(self):
        plan = controller.build_plan(configuration(), evidence(), "production")
        admitted = controller.admitted_receipt(
            plan, configuration(), evidence(), FakeClock().now()
        )
        interrupted = controller._next_revision(
            admitted,
            outcome="interrupted",
            runners=[],
            completed_at="2026-08-14T00:00:02Z",
            failure="operator interruption before mutation",
        )
        retained_evidence = evidence()
        retained_evidence["retainedRevisions"] = [admitted, interrupted]
        retained_evidence["retainedReceiptAuthority"] = (
            f"{interrupted['attemptId']}/42@"
            f"{controller.retained_authority_digest(plan, [admitted, interrupted])}"
        )

        with tempfile.TemporaryDirectory() as directory:
            restored = controller._restore_receipts(
                retained_evidence, plan, Path(directory)
            )
            self.assertEqual(interrupted, restored)
            self.assertEqual(2, len(list(Path(directory).glob("revision-*.json"))))

        retained_evidence["retainedReceiptAuthority"] = (
            f"{interrupted['attemptId']}/42@sha256:" + "0" * 64
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(controller.DeploymentError, "exact chain"):
                controller._restore_receipts(retained_evidence, plan, Path(directory))

    def test_receipt_chain_rejects_a_fabricated_non_admitted_root(self):
        plan = controller.build_plan(configuration(), evidence(), "production")
        fabricated = controller.admitted_receipt(
            plan, configuration(), evidence(), FakeClock().now()
        )
        fabricated["outcome"] = "in_progress"
        fabricated["previousReceiptDigest"] = "sha256:" + "0" * 64

        with self.assertRaisesRegex(ValueError, "root.*admitted"):
            controller.validate_receipt_chain([fabricated])

    def test_resume_probes_retained_post_update_revision_without_redraining(self):
        plan = controller.build_plan(configuration(), evidence(), "production")
        admitted = controller.admitted_receipt(
            plan, configuration(), evidence(), FakeClock().now()
        )
        pending = controller._next_revision(
            admitted,
            outcome="in_progress",
            runners=[
                {
                    "name": "gha-gate-1",
                    "beforeDigest": "sha256:" + "1" * 64,
                    "afterDigest": "sha256:" + "3" * 64,
                    "afterRelease": copy.deepcopy(plan["selectedRelease"]),
                    "state": "updated",
                    "probe": "not_run",
                    "observation": "pending",
                    "completedAt": None,
                }
            ],
            completed_at=None,
        )
        live = evidence()
        live["fleet"]["runners"][0]["release"] = copy.deepcopy(plan["selectedRelease"])
        live["fleet"]["runners"][0]["manifestIdentity"] = plan["manifestIdentity"]
        live["fleet"]["runners"][0]["deployedDigest"] = plan["targetDigest"]
        adapter = FakeAdapter()

        final = controller.execute_plan(
            plan,
            configuration(),
            live,
            adapter,
            lambda _receipt: None,
            previous_receipt=pending,
            max_hosts=1,
            clock=FakeClock(),
        )

        self.assertEqual("in_progress", final["outcome"])
        self.assertEqual("passed", final["runners"][0]["probe"])
        self.assertNotIn("gha-gate-1", [call[1] for call in adapter.calls if call[0] == "update"])
        self.assertEqual(["probe", "observe"], [call[0] for call in adapter.calls])

    def test_resume_uses_exact_published_asset_and_retained_plan_for_a_valid_mixed_fleet(self):
        candidate = published_format_evidence()
        plan = controller.build_plan(configuration(), candidate, "production")
        admitted = controller.admitted_receipt(
            plan, configuration(), candidate, FakeClock().now()
        )
        pending = controller._next_revision(
            admitted,
            outcome="in_progress",
            runners=[
                {
                    "name": "gha-gate-1",
                    "beforeDigest": "sha256:" + "1" * 64,
                    "afterDigest": "sha256:" + "3" * 64,
                    "afterRelease": copy.deepcopy(plan["selectedRelease"]),
                    "state": "updated",
                    "probe": "not_run",
                    "observation": "pending",
                    "completedAt": None,
                }
            ],
            completed_at=None,
        )
        resume_evidence = copy.deepcopy(candidate)
        resume_evidence["fleet"]["runners"][0]["release"] = copy.deepcopy(
            plan["selectedRelease"]
        )
        resume_evidence["retainedPlan"] = copy.deepcopy(plan)
        resume_evidence["retainedRevisions"] = [admitted, pending]
        resume_evidence["retainedReceiptAuthority"] = (
            f"{pending['attemptId']}/42@"
            f"{controller.retained_authority_digest(plan, [admitted, pending])}"
        )

        self.assertEqual(
            plan,
            controller.retained_plan(
                configuration(),
                resume_evidence,
                "production",
                "deploy",
                "0" * 40,
            ),
        )

        resume_evidence["retainedPlan"]["steps"].reverse()
        with self.assertRaisesRegex(controller.DeploymentError, "exact chain"):
            controller.retained_plan(
                configuration(),
                resume_evidence,
                "production",
                "deploy",
                "0" * 40,
            )

    def test_receipt_schema_rejects_malformed_authority_and_self_review(self):
        plan = controller.build_plan(configuration(), evidence(), "production")
        receipt = controller.admitted_receipt(
            plan, configuration(), evidence(), FakeClock().now()
        )
        receipt["authorization"]["workflowRunId"] = "40"
        with self.assertRaisesRegex(ValueError, "integer"):
            controller.validate_receipt(receipt)

        receipt = controller.admitted_receipt(
            plan, configuration(), evidence(), FakeClock().now()
        )
        receipt["authorization"]["reviewGates"][0]["principalId"] = receipt["authorization"]["dispatcherId"]
        with self.assertRaisesRegex(ValueError, "independent review"):
            controller.validate_receipt(receipt)

    def test_unknown_update_state_is_reconciled_from_live_evidence_then_probed(self):
        plan = controller.build_plan(configuration(), evidence(), "production")
        interrupted = controller.execute_plan(
            plan,
            configuration(),
            evidence(),
            FakeAdapter(interrupt_update="gha-gate-1"),
            lambda _receipt: None,
            clock=FakeClock(),
        )
        live = evidence()
        live_runner = live["fleet"]["runners"][0]
        live_runner["release"] = copy.deepcopy(plan["selectedRelease"])
        live_runner["manifestIdentity"] = plan["manifestIdentity"]
        live_runner["deployedDigest"] = plan["targetDigest"]

        reconciled = controller.reconcile_unknown_state(
            plan, interrupted, live, configuration()
        )

        self.assertEqual("in_progress", reconciled["outcome"])
        self.assertEqual("reconciled", reconciled["runners"][0]["state"])
        self.assertEqual("verified", reconciled["finalFleet"][0]["state"])
        adapter = FakeAdapter()
        resumed = controller.execute_plan(
            plan,
            configuration(),
            live,
            adapter,
            lambda _receipt: None,
            previous_receipt=reconciled,
            max_hosts=1,
            clock=FakeClock(),
        )
        self.assertEqual(["probe", "observe"], [call[0] for call in adapter.calls])
        self.assertEqual("passed", resumed["runners"][0]["observation"])

    def test_unknown_update_reconciliation_rejects_unrecognized_live_release(self):
        plan = controller.build_plan(configuration(), evidence(), "production")
        interrupted = controller.execute_plan(
            plan,
            configuration(),
            evidence(),
            FakeAdapter(interrupt_update="gha-gate-1"),
            lambda _receipt: None,
            clock=FakeClock(),
        )
        live = evidence()
        live["fleet"]["runners"][0]["release"] = release("9.0.0", "9")
        live["fleet"]["runners"][0]["manifestIdentity"] = (
            "sha256:" + "9" * 64
        )
        live["fleet"]["runners"][0]["deployedDigest"] = "sha256:" + "9" * 64

        with self.assertRaisesRegex(controller.DeploymentError, "neither selected nor baseline"):
            controller.reconcile_unknown_state(plan, interrupted, live, configuration())

    def test_unknown_update_at_baseline_reconciles_to_a_safe_retry(self):
        plan = controller.build_plan(configuration(), evidence(), "production")
        interrupted = controller.execute_plan(
            plan,
            configuration(),
            evidence(),
            FakeAdapter(interrupt_update="gha-gate-1"),
            lambda _receipt: None,
            clock=FakeClock(),
        )
        live = evidence()
        live["fleet"]["runners"][0]["deployedDigest"] = "sha256:" + "1" * 64
        live["fleet"]["runners"][0]["releaseManifest"] = release_manifest(
            "1.0.0", "1"
        )

        reconciled = controller.reconcile_unknown_state(
            plan, interrupted, live, configuration()
        )

        self.assertEqual([], reconciled["runners"])
        self.assertEqual(
            plan["observedDeployedRelease"], reconciled["finalFleet"][0]["release"]
        )
        adapter = FakeAdapter()
        controller.execute_plan(
            plan,
            configuration(),
            live,
            adapter,
            lambda _receipt: None,
            previous_receipt=reconciled,
            max_hosts=1,
            clock=FakeClock(),
        )
        self.assertEqual(
            ["capacity", "update", "probe", "observe"],
            [call[0] for call in adapter.calls],
        )

    def test_unknown_baseline_reconciliation_rejects_unrelated_deployed_digest(self):
        plan = controller.build_plan(configuration(), evidence(), "production")
        interrupted = controller.execute_plan(
            plan,
            configuration(),
            evidence(),
            FakeAdapter(interrupt_update="gha-gate-1"),
            lambda _receipt: None,
            clock=FakeClock(),
        )
        live = evidence()
        live_runner = live["fleet"]["runners"][0]
        live_runner["deployedDigest"] = "sha256:" + "9" * 64
        live_runner["releaseManifest"] = release_manifest("1.0.0", "1")

        with self.assertRaisesRegex(
            controller.DeploymentError, "baseline live release has the wrong image digest"
        ):
            controller.reconcile_unknown_state(
                plan, interrupted, live, configuration()
            )

    def test_unknown_baseline_reconciliation_requires_release_manifest(self):
        plan = controller.build_plan(configuration(), evidence(), "production")
        interrupted = controller.execute_plan(
            plan,
            configuration(),
            evidence(),
            FakeAdapter(interrupt_update="gha-gate-1"),
            lambda _receipt: None,
            clock=FakeClock(),
        )
        live = evidence()
        live["fleet"]["runners"][0]["deployedDigest"] = "sha256:" + "1" * 64
        live["fleet"]["runners"][0].pop("releaseManifest")

        with self.assertRaisesRegex(
            controller.DeploymentError, "baseline release manifest.*must be an object"
        ):
            controller.reconcile_unknown_state(
                plan, interrupted, live, configuration()
            )

    def test_unknown_baseline_reconciliation_rejects_unbound_release_manifest(self):
        plan = controller.build_plan(configuration(), evidence(), "production")
        interrupted = controller.execute_plan(
            plan,
            configuration(),
            evidence(),
            FakeAdapter(interrupt_update="gha-gate-1"),
            lambda _receipt: None,
            clock=FakeClock(),
        )
        live = evidence()
        live_runner = live["fleet"]["runners"][0]
        live_runner["deployedDigest"] = "sha256:" + "1" * 64
        live_runner["releaseManifest"] = release_manifest("1.0.0", "2")

        with self.assertRaisesRegex(
            controller.DeploymentError, "canonical bytes differ from structured manifest"
        ):
            controller.reconcile_unknown_state(
                plan, interrupted, live, configuration()
            )

    def test_process_adapter_keeps_secret_out_of_arguments_and_never_scales(self):
        completed = mock.Mock()
        completed.stdout = "runner update complete"
        config = configuration()
        fleet = config["fleets"]["production"]
        adapter = controller.ProcessAdapter(config, fleet)
        with tempfile.TemporaryDirectory() as directory:
            cli = Path(directory) / "node_modules/.bin/verjson-cloud"
            cli.parent.mkdir(parents=True)
            cli.write_text("#!/bin/sh\n", encoding="utf-8")
            cli.chmod(0o700)
            with mock.patch.dict(
                controller.os.environ,
                {
                    "VERJSON_DEPLOYMENT_CLI": str(cli),
                    "VERJSON_DEPLOYMENT_CLI_ROOT": directory,
                    "HOME": "/runner-home",
                    "SSH_AUTH_SOCK": "/tmp/agent.sock",
                    "DIGITALOCEAN_RUNNER_FLEET_TOKEN": "provider-fixture",
                    "GH_RUNNER_CONTROL_TOKEN": "github-fixture",
                },
                clear=False,
            ), mock.patch.object(
                controller.subprocess, "run", return_value=completed
            ) as run, mock.patch.object(
                adapter, "_host_export", return_value={"hostEvidence": {}}
            ):
                adapter.update_runner(
                    "gha-gate-1",
                    "sha256:" + "2" * 64,
                    "runner",
                    600,
                )

        command = run.call_args.args[0]
        self.assertIn("--only", command)
        self.assertNotIn("provider-fixture", command)
        self.assertNotIn("github-fixture", command)
        self.assertTrue({"--replicas", "--standard", "create", "resize"}.isdisjoint(command))
        self.assertEqual(
            "provider-fixture",
            run.call_args.kwargs["env"]["DIGITALOCEAN_ACCESS_TOKEN"],
        )
        self.assertEqual("github-fixture", run.call_args.kwargs["env"]["GH_TOKEN"])
        self.assertNotIn("SSH_AUTH_SOCK", run.call_args.kwargs["env"])
        self.assertNotIn("HOME", run.call_args.kwargs["env"])
        self.assertEqual(720, run.call_args.kwargs["timeout"])

    def test_child_process_environment_drops_unreviewed_secrets(self):
        with mock.patch.dict(
            controller.os.environ,
            {
                "PATH": "/usr/bin",
                "RUNNER_TEMP": "/runner/temp",
                "HOME": "/runner-home",
                "SSH_AUTH_SOCK": "/tmp/agent.sock",
                "VERJSON_DEPLOYMENT_CLI": "/runner/bin/verjson-cloud",
                "VERJSON_DEPLOYMENT_CLI_ROOT": "/runner",
                "UNRELATED_SECRET": "must-not-cross-boundary",
                "DIGITALOCEAN_RUNNER_FLEET_TOKEN": "provider-parent",
                "GH_RUNNER_CONTROL_TOKEN": "github-parent",
            },
            clear=True,
        ):
            child = controller._child_environment()
            self.assertEqual({"PATH": "/usr/bin"}, child)

    def test_probe_child_process_receives_no_ssh_agent_or_control_capability(self):
        completed = mock.Mock(
            stdout=json.dumps({"outcome": "passed", "routedRunner": "gha-gate-1"})
        )
        config = configuration()
        with mock.patch.dict(
            controller.os.environ,
            {
                "PATH": "/usr/bin",
                "HOME": "/runner-home",
                "SSH_AUTH_SOCK": "/tmp/agent.sock",
                "VERJSON_DEPLOYMENT_CLI": "/runner/bin/verjson-cloud",
                "VERJSON_DEPLOYMENT_CLI_ROOT": "/runner",
                "DIGITALOCEAN_RUNNER_FLEET_TOKEN": "provider-parent",
                "GH_RUNNER_CONTROL_TOKEN": "github-parent",
            },
            clear=True,
        ), mock.patch.object(
            controller.subprocess, "run", return_value=completed
        ) as run:
            controller.ProcessAdapter(
                config, config["fleets"]["production"]
            ).probe_runner("gha-gate-1", 300)
        self.assertEqual({"PATH": "/usr/bin"}, run.call_args.kwargs["env"])

    def test_refresh_rejects_changed_github_authorization(self):
        admitted = evidence()
        plan = controller.build_plan(configuration(), admitted, "production")
        refreshed = evidence()
        refreshed["authorization"]["reviewGates"][0]["checkRunId"] += 1
        with self.assertRaisesRegex(controller.DeploymentError, "authorization changed"):
            controller.execute_plan(
                plan,
                configuration(),
                refreshed,
                FakeAdapter(),
                lambda _receipt: None,
            )

    def test_v4_controller_rejects_schema_v3_rollback_authority(self):
        source = controller.admitted_receipt(
            controller.build_plan(configuration(), evidence(), "production"),
            configuration(),
            evidence(),
            FakeClock().now(),
        )
        source["outcome"] = "failed"
        source["schemaVersion"] = 3
        with self.assertRaisesRegex(controller.DeploymentError, "schema-v3"):
            controller.build_plan(
                configuration(),
                evidence(),
                "production",
                action="rollback",
                deployment_contract_ref="a" * 40,
                rollback_source=source,
            )

    def test_process_adapter_rejects_boolean_capacity_evidence(self):
        config = configuration()
        adapter = controller.ProcessAdapter(config, config["fleets"]["production"])
        with mock.patch.object(
            adapter,
            "_host_export",
            return_value={"hostEvidence": {"availableCapacity": True}},
        ):
            with self.assertRaisesRegex(controller.DeploymentError, "capacity evidence is malformed"):
                adapter.available_capacity()

    def test_process_adapter_rejects_cli_outside_immutable_acquisition_root(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            cli = Path(outside) / "verjson-cloud"
            cli.write_text("#!/bin/sh\n", encoding="utf-8")
            cli.chmod(0o700)
            with mock.patch.dict(
                controller.os.environ,
                {
                    "VERJSON_DEPLOYMENT_CLI": str(cli),
                    "VERJSON_DEPLOYMENT_CLI_ROOT": root,
                    "DIGITALOCEAN_RUNNER_FLEET_TOKEN": "provider-fixture",
                    "GH_RUNNER_CONTROL_TOKEN": "github-fixture",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(controller.DeploymentError, "escapes"):
                    controller.ProcessAdapter(
                        configuration(), configuration()["fleets"]["production"]
                    ).update_runner(
                        "gha-gate-1", "sha256:" + "2" * 64, "runner", 600
                    )

    def test_collect_evidence_rejects_option_injection_before_adapter_execution(self):
        with mock.patch.object(controller.ProcessAdapter, "_run") as run:
            with self.assertRaisesRegex(controller.DeploymentError, "safe token"):
                controller._collect_evidence(
                    configuration(),
                    "sha256:" + "2" * 64,
                    "--help",
                )
        run.assert_not_called()

    def test_process_adapter_honors_policy_sized_json_command_timeouts(self):
        completed = mock.Mock(stdout="{}")
        with mock.patch.object(
            controller.ProcessAdapter, "_invoke", return_value=completed
        ) as invoke:
            for timeout in (330, 930):
                with self.subTest(timeout=timeout):
                    controller.ProcessAdapter._run(
                        ["python3", "adapter.py"], timeout_seconds=timeout
                    )
                    self.assertEqual(timeout, invoke.call_args.kwargs["timeout_seconds"])

            with self.assertRaisesRegex(controller.DeploymentError, "timeout"):
                controller.ProcessAdapter._run(
                    ["python3", "adapter.py"], timeout_seconds=931
                )

    def test_probe_adapter_maps_reviewed_probe_windows_to_process_timeouts(self):
        adapter = controller.ProcessAdapter(
            configuration(), configuration()["fleets"]["production"]
        )
        with mock.patch.object(
            controller.ProcessAdapter,
            "_run",
            return_value={"outcome": "passed", "routedRunner": "gha-gate-1"},
        ) as run:
            for policy_timeout, process_timeout in ((300, 330), (900, 930)):
                with self.subTest(policy_timeout=policy_timeout):
                    adapter.probe_runner("gha-gate-1", policy_timeout)
                    self.assertEqual(
                        process_timeout, run.call_args.kwargs["timeout_seconds"]
                    )

    def test_probe_process_timeout_maps_to_truthful_timeout_receipt(self):
        adapter = FakeAdapter()

        def timeout(_runner, timeout_seconds):
            raise subprocess.TimeoutExpired("probe", timeout_seconds)

        adapter.probe_runner = timeout
        final = controller.execute_plan(
            controller.build_plan(configuration(), evidence(), "production"),
            configuration(),
            evidence(),
            adapter,
            lambda _receipt: None,
            clock=FakeClock(),
        )
        self.assertEqual("timeout", final["runners"][0]["probe"])
        self.assertRegex(final["failure"], "timed out")


if __name__ == "__main__":
    unittest.main()
