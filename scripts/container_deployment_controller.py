#!/usr/bin/env python3

import argparse
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from container_deployment_preflight import (
    PreflightError,
    receipt_digest,
    validate_attempt_revision,
    validate_authorization,
    validate_receipt,
    validate_receipt_chain,
    validate_rollback,
)


DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
MANIFEST_IDENTITY = re.compile(r"(?P<digest>sha256:[0-9a-f]{64})")
MAX_DRAIN_SECONDS = 1_800
MAX_PROBE_SECONDS = 900
MAX_OBSERVATION_SECONDS = 900
MAX_REQUEST_AGE_SECONDS = 3_600
MAX_FLEET_SIZE = 3
MAX_MANIFEST_BYTES = 1_048_576
MISSING_MANIFEST_BYTES = object()
JOB_SECONDS = 5_400
SAFETY_MARGIN_SECONDS = 900
UPDATE_COMMAND_OVERHEAD_SECONDS = 120
POST_UPDATE_EVIDENCE_SECONDS = 120
CAPACITY_EVIDENCE_SECONDS = 120
ADMISSION_EVIDENCE_SECONDS = 120
PROBE_COMMAND_OVERHEAD_SECONDS = 30
MAX_ADAPTER_JSON_SECONDS = MAX_PROBE_SECONDS + PROBE_COMMAND_OVERHEAD_SECONDS
HOST_EXPORT_SECRET_ENV = (
    "RUNNER_HOST_EVIDENCE_APP_PRIVATE_KEY",
    "RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY",
    "RUNNER_HOST_EVIDENCE_DOCTL_CONFIG",
    "RUNNER_HOST_EVIDENCE_KNOWN_HOSTS",
)


class DeploymentError(ValueError):
    pass


class DeploymentInterrupted(DeploymentError):
    pass


def _validate_deployment_authorization(evidence: dict[str, Any]) -> None:
    try:
        validate_authorization(evidence)
    except PreflightError as error:
        raise DeploymentError(f"deployment authorization is invalid: {error}") from error


def validate_environment_policy(environment: dict[str, Any]) -> None:
    branch_policy = environment.get("deployment_branch_policy")
    if not isinstance(branch_policy, dict):
        raise DeploymentError("production deployment branch policy is unavailable")
    if (
        branch_policy.get("protected_branches") is not True
        or branch_policy.get("custom_branch_policies") is not False
    ):
        raise DeploymentError("production environment branch policy differs")
    rules = environment.get("protection_rules")
    if (
        not isinstance(rules, list)
        or len(rules) != 1
        or not isinstance(rules[0], dict)
        or rules[0].get("type") != "branch_policy"
    ):
        raise DeploymentError("production must have only the branch-policy rule")
    if environment.get("can_admins_bypass") is not True:
        raise DeploymentError("production must permit administrator bypass")


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeploymentError(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise DeploymentError(f"{field} must be a non-empty string")
    return value


def _safe_token(value: Any, field: str, pattern: str) -> str:
    token = _text(value, field)
    if token.startswith("-") or re.fullmatch(pattern, token) is None:
        raise DeploymentError(f"{field} must be a non-option safe token")
    return token


def canonical_digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def retained_authority_digest(
    plan: dict[str, Any], receipts: list[dict[str, Any]]
) -> str:
    return canonical_digest({"plan": plan, "revisions": receipts})


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeploymentError(f"cannot read JSON from {path}: {error}") from error
    return _object(value, str(path))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def _command(config: dict[str, Any], field: str) -> list[str]:
    value = config.get(field)
    if (
        not isinstance(value, list)
        or len(value) != 2
        or value[0] != "python3"
        or not isinstance(value[1], str)
        or re.fullmatch(r"scripts/[A-Za-z0-9_.-]+\.py", value[1]) is None
    ):
        raise DeploymentError(f"{field} must be ['python3', 'scripts/<reviewed>.py']")
    return list(value)


def _release(version: Any, manifest_digest: Any, field: str) -> dict[str, str]:
    if not isinstance(version, str) or re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version
    ) is None:
        raise DeploymentError(f"{field} releaseVersion must be stable SemVer")
    if not isinstance(manifest_digest, str) or DIGEST.fullmatch(manifest_digest) is None:
        raise DeploymentError(f"{field} manifestDigest must be an immutable digest")
    return {"releaseVersion": version, "manifestDigest": manifest_digest}


def _manifest_with_identity(
    manifest_value: Any,
    digest: str,
    field: str,
    manifest_bytes: Any = MISSING_MANIFEST_BYTES,
) -> dict[str, Any]:
    manifest = _object(manifest_value, field)
    if manifest_bytes is MISSING_MANIFEST_BYTES:
        if canonical_digest(manifest) != digest:
            raise DeploymentError(f"{field} canonical bytes differ from release identity")
        return manifest

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DeploymentError(f"{field} bytes contain duplicate JSON keys")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise DeploymentError(f"{field} bytes contain non-finite JSON number {value}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            reject_constant(value)
        return parsed

    if not isinstance(manifest_bytes, str) or len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise DeploymentError(f"{field} bytes must be UTF-8 text of at most {MAX_MANIFEST_BYTES} bytes")
    try:
        encoded = manifest_bytes.encode("utf-8")
        if len(encoded) > MAX_MANIFEST_BYTES:
            raise DeploymentError(f"{field} bytes exceed {MAX_MANIFEST_BYTES} bytes")
        if "sha256:" + hashlib.sha256(encoded).hexdigest() != digest:
            raise DeploymentError(f"{field} canonical bytes differ from release identity")
        parsed = _object(json.loads(
            manifest_bytes, object_pairs_hook=unique_object,
            parse_constant=reject_constant, parse_float=finite_float,
        ), field)
        if canonical_digest(parsed) != canonical_digest(manifest):
            raise DeploymentError(f"{field} canonical bytes differ from structured manifest")
    except DeploymentError:
        raise
    except (ValueError, RecursionError) as error:
        raise DeploymentError(f"{field} bytes are not valid UTF-8 JSON") from error
    return parsed


def _validate_release_evidence(
    expected: dict[str, Any],
    evidence: dict[str, Any],
    identity_match: re.Match[str],
    now: datetime,
) -> tuple[dict[str, str], str]:
    attestation = _object(evidence.get("attestation"), "attestation")
    if attestation.get("verified") is not True:
        raise DeploymentError("release attestation is not verified")
    if attestation.get("repository") != expected.get("sourceRepository"):
        raise DeploymentError("release attestation source repository differs")
    if attestation.get("sourceRef") != expected.get("sourceRef"):
        raise DeploymentError("release attestation source ref differs")
    if attestation.get("signerWorkflow") != expected.get("signerWorkflow"):
        raise DeploymentError("release attestation signer differs")
    if attestation.get("contractCommit") != expected.get("contractCommit"):
        raise DeploymentError("release attestation contract pin differs")
    if attestation.get("subjectDigest") != identity_match.group("digest"):
        raise DeploymentError("release attestation subject digest differs")
    expires_at = _date_time(attestation.get("expiresAt"), "attestation.expiresAt")
    if expires_at <= now:
        raise DeploymentError("release attestation is expired")

    manifest = _manifest_with_identity(
        evidence.get("manifest"), identity_match.group("digest"),
        "release manifest", evidence.get("manifestBytes", MISSING_MANIFEST_BYTES),
    )
    source = _object(manifest.get("source"), "manifest.source")
    if source.get("repository") != expected.get("sourceRepository"):
        raise DeploymentError("manifest source repository differs")
    release_evidence = _object(manifest.get("release"), "manifest.release")
    workflow = _object(release_evidence.get("workflow"), "manifest.release.workflow")
    signer_workflow = str(expected.get("signerWorkflow", ""))
    marker = "/.github/workflows/"
    expected_path = (
        ".github/workflows/" + signer_workflow.split(marker, 1)[1]
        if marker in signer_workflow
        else ""
    )
    if workflow.get("path") != expected_path:
        raise DeploymentError("manifest signer workflow differs")
    if workflow.get("contractCommit") != expected.get("contractCommit"):
        raise DeploymentError("manifest release contract pin differs")

    images = manifest.get("images")
    variant = expected.get("variant")
    if not isinstance(images, list) or sum(
        1 for image in images if isinstance(image, dict) and image.get("variant") == variant
    ) != 1:
        raise DeploymentError("manifest must contain exactly one reviewed release variant")
    selected_image = next(
        image for image in images if isinstance(image, dict) and image.get("variant") == variant
    )
    target_digest = selected_image.get("indexDigest")
    if not isinstance(target_digest, str) or DIGEST.fullmatch(target_digest) is None:
        raise DeploymentError("selected release variant has no immutable image digest")
    return (
        _release(
            manifest.get("releaseVersion"),
            identity_match.group("digest"),
            "selectedRelease",
        ),
        target_digest,
    )


def _release_variant_digest(
    manifest_value: Any,
    release: dict[str, Any],
    variant: str,
    field: str,
    manifest_bytes: Any = MISSING_MANIFEST_BYTES,
) -> str:
    manifest = _manifest_with_identity(
        manifest_value, release.get("manifestDigest"), field, manifest_bytes
    )
    if manifest.get("releaseVersion") != release.get("releaseVersion"):
        raise DeploymentError(f"{field} version differs from release identity")
    images = manifest.get("images")
    if not isinstance(images, list) or sum(
        1 for image in images if isinstance(image, dict) and image.get("variant") == variant
    ) != 1:
        raise DeploymentError(f"{field} must contain exactly one reviewed release variant")
    selected_image = next(
        image for image in images if isinstance(image, dict) and image.get("variant") == variant
    )
    digest = selected_image.get("indexDigest")
    if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
        raise DeploymentError(f"{field} release variant has no immutable image digest")
    return digest


def _date_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise DeploymentError(f"{field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DeploymentError(f"{field} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None:
        raise DeploymentError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_policy(fleet: dict[str, Any]) -> None:
    _safe_token(fleet.get("lane"), "lane", r"[a-z][a-z0-9-]{1,29}")
    _safe_token(fleet.get("project"), "project", r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    bounds = (
        ("drainTimeoutSeconds", MAX_DRAIN_SECONDS),
        ("probeTimeoutSeconds", MAX_PROBE_SECONDS),
        ("observationSeconds", MAX_OBSERVATION_SECONDS),
    )
    for field, maximum in bounds:
        value = fleet.get(field)
        if not isinstance(value, int) or value < 1 or value > maximum:
            raise DeploymentError(f"{field} must be between 1 and {maximum}")
    for field in ("requiredLabels", "requiredTools"):
        values = fleet.get(field)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
            or len(set(values)) != len(values)
        ):
            raise DeploymentError(f"{field} must declare unique values")
    _safe_token(
        fleet.get("runnerGroup"),
        "runnerGroup",
        r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}",
    )
    runners = fleet.get("runners")
    if not isinstance(runners, list) or len(runners) > MAX_FLEET_SIZE:
        raise DeploymentError(f"fleet may contain at most {MAX_FLEET_SIZE} runners")
    worst_case = MAX_FLEET_SIZE * ADMISSION_EVIDENCE_SECONDS + len(runners) * (
        CAPACITY_EVIDENCE_SECONDS
        + fleet["drainTimeoutSeconds"]
        + UPDATE_COMMAND_OVERHEAD_SECONDS
        + POST_UPDATE_EVIDENCE_SECONDS
        + fleet["probeTimeoutSeconds"]
        + PROBE_COMMAND_OVERHEAD_SECONDS
    ) + fleet["observationSeconds"]
    if worst_case >= JOB_SECONDS - SAFETY_MARGIN_SECONDS:
        raise DeploymentError("reviewed fleet timing leaves less than the required job margin")


def _validate_commands(config: dict[str, Any]) -> None:
    _command(config, "evidenceCommand")
    _command(config, "probeCommand")
    if config.get("cliCommand") != ["verjson-cloud"]:
        raise DeploymentError("cliCommand must select the contract-acquired verjson-cloud executable")


def _deployment_cli() -> str:
    raw_path = os.environ.get("VERJSON_DEPLOYMENT_CLI")
    raw_root = os.environ.get("VERJSON_DEPLOYMENT_CLI_ROOT")
    if not raw_path or not raw_root:
        raise DeploymentError("contract-acquired verjson-cloud executable is unavailable")
    path = Path(raw_path)
    root = Path(raw_root)
    try:
        resolved_path = path.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
    except (OSError, ValueError) as error:
        raise DeploymentError("deployment CLI escapes its immutable acquisition root") from error
    if not path.is_absolute() or not root.is_absolute() or not os.access(path, os.X_OK):
        raise DeploymentError("contract-acquired verjson-cloud executable is invalid")
    return str(path)


def _validate_runner_admission(fleet: dict[str, Any], inventory: Any) -> None:
    expected_names = fleet.get("runners")
    if not isinstance(inventory, list) or not isinstance(expected_names, list):
        raise DeploymentError("fleet inventory must be an array")
    actual_names = [runner.get("name") for runner in inventory if isinstance(runner, dict)]
    if (
        len(actual_names) != len(inventory)
        or any(not isinstance(name, str) for name in actual_names + expected_names)
        or len(set(actual_names)) != len(actual_names)
        or set(actual_names) != set(expected_names)
    ):
        raise DeploymentError("observed fleet inventory differs from reviewed configuration")
    for runner in inventory:
        if runner.get("online") is not True:
            raise DeploymentError(f"runner {runner.get('name')} is not online")
        if runner.get("busy") is not False:
            raise DeploymentError(f"runner {runner.get('name')} is not idle")
        if runner.get("admitted") is not True:
            raise DeploymentError(f"runner {runner.get('name')} is not admitted")
        if runner.get("runnerGroup") != fleet.get("runnerGroup"):
            raise DeploymentError(
                f"runner {runner.get('name')} group differs from reviewed configuration"
            )
        labels = runner.get("labels")
        if not isinstance(labels, list) or any(
            not isinstance(label, str) for label in labels
        ) or not set(fleet.get("requiredLabels", [])).issubset(labels):
            raise DeploymentError(f"runner {runner.get('name')} labels are incomplete")
        tools = runner.get("tools")
        if not isinstance(tools, list) or any(
            not isinstance(tool, str) for tool in tools
        ) or not set(fleet.get("requiredTools", [])).issubset(tools):
            raise DeploymentError(f"runner {runner.get('name')} tools are incomplete")


def _host_observation_authority(evidence: dict[str, Any]) -> dict[str, Any]:
    name = "observationAuthority" if evidence.get("preview") is True else "authorization"
    return _object(evidence.get(name), name)


def _validate_host_export_binding(
    config: dict[str, Any],
    fleet: dict[str, Any],
    evidence: dict[str, Any],
    inventory: list[dict[str, Any]],
) -> None:
    request = _object(evidence.get("hostExportRequest"), "host export request")
    result = _object(evidence.get("hostExport"), "host export result")
    host_request = _object(request.get("hostExport"), "host export request details")
    authority = _object(config.get("hostEvidenceAuthority"), "hostEvidenceAuthority")
    github = _object(request.get("github"), "host export GitHub authority")
    release_request = _object(request.get("release"), "host export release")
    release_evidence = _object(evidence.get("attestation"), "release attestation")
    if (
        request.get("operation") != "host-export"
        or request.get("lane") != fleet.get("lane")
        or host_request.get("purpose") != "baseline"
        or host_request.get("runnerNames") != fleet.get("runners")
        or request.get("configDigest") != canonical_digest(config)
        or github.get("repository") != release_evidence.get("repository")
        or github.get("repositoryId") != _host_observation_authority(evidence).get("repositoryId")
        or github.get("appId") != authority.get("appId")
        or github.get("installationId") != authority.get("installationId")
        or release_request.get("manifestDigest") != evidence.get("manifestIdentity")
        or release_request.get("repository") != release_evidence.get("repository")
        or release_request.get("sourceRef") != release_evidence.get("sourceRef")
        or release_request.get("signerWorkflow") != release_evidence.get("signerWorkflow")
        or result.get("schemaVersion") != 1
        or result.get("outcome") != "passed"
        or result.get("requestDigest") != canonical_digest(request)
    ):
        raise DeploymentError("host export receipt is not bound to the reviewed fleet request")
    report = _object(result.get("hostEvidence"), "host export report")
    if (
        report.get("fleet") != evidence.get("fleet")
        or report.get("manifestIdentity") != evidence.get("manifestIdentity")
        or report.get("manifestBytes") != evidence.get("manifestBytes")
        or report.get("manifest") != evidence.get("manifest")
    ):
        raise DeploymentError("host export baseline fleet differs retained evidence")
    attestations = result.get("baselineAttestations")
    if not isinstance(attestations, list) or len(attestations) != len(inventory):
        raise DeploymentError("host export baseline attestation set is incomplete")
    by_name = {
        item.get("runnerName"): item
        for item in attestations
        if isinstance(item, dict) and isinstance(item.get("runnerName"), str)
    }
    expected_names = {runner.get("name") for runner in inventory}
    if set(by_name) != expected_names:
        raise DeploymentError("host export baseline attestation identities differ")
    source_attestation = release_evidence
    for runner in inventory:
        name = runner.get("name")
        raw = runner.get("releaseManifestBytes")
        identity = runner.get("manifestIdentity")
        if not isinstance(raw, str) or not isinstance(identity, str):
            raise DeploymentError(f"runner {name} baseline manifest bytes are unavailable")
        identity_match = MANIFEST_IDENTITY.fullmatch(identity)
        if identity_match is None:
            raise DeploymentError(f"runner {name} baseline manifest identity is invalid")
        _manifest_with_identity(
            runner.get("releaseManifest"), identity_match.group("digest"),
            f"runner {name} release manifest", raw,
        )
        manifest = _object(runner.get("releaseManifest"), f"runner {name} release manifest")
        workflow = _object(
            _object(manifest.get("release"), f"runner {name} manifest release").get("workflow"),
            f"runner {name} manifest workflow",
        )
        proof = by_name[name]
        if (
            proof.get("verified") is not True
            or proof.get("manifestIdentity") != identity
            or proof.get("repository") != source_attestation.get("repository")
            or proof.get("sourceRef") != source_attestation.get("sourceRef")
            or proof.get("signerWorkflow") != source_attestation.get("signerWorkflow")
            or proof.get("signerCommit") != workflow.get("contractCommit")
        ):
            raise DeploymentError(f"runner {name} baseline attestation binding differs")


def _validate_inventory(
    config: dict[str, Any],
    fleet: dict[str, Any],
    evidence: dict[str, Any],
    rollback_source: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], str]:
    inventory = _object(evidence.get("fleet"), "fleet evidence").get("runners")
    expected_names = fleet.get("runners")
    if not isinstance(inventory, list) or not isinstance(expected_names, list):
        raise DeploymentError("fleet inventory must be an array")
    _validate_runner_admission(fleet, inventory)

    baseline_values = [runner.get("release") for runner in inventory]
    if rollback_source is None:
        if not baseline_values or any(value != baseline_values[0] for value in baseline_values):
            raise DeploymentError("fleet has an unexpected mixed deployed baseline")
        baseline = _object(baseline_values[0], "fleet baseline")
    else:
        recorded_final = rollback_source.get("finalFleet")
        if not isinstance(recorded_final, list):
            raise DeploymentError("rollback source has no recorded final fleet")
        recorded_by_name = {
            runner.get("name"): runner.get("release")
            for runner in recorded_final
            if isinstance(runner, dict)
        }
        if set(recorded_by_name) != set(expected_names) or any(
            runner.get("release") != recorded_by_name.get(runner.get("name"))
            for runner in inventory
        ):
            raise DeploymentError("live fleet differs from rollback source final state")
        baseline = _object(
            rollback_source.get("observedDeployedRelease"),
            "rollback source observed baseline",
        )
    observed = _release(
        baseline.get("releaseVersion"), baseline.get("manifestDigest"), "fleet baseline"
    )
    for runner in inventory:
        runner_release = _object(runner.get("release"), "runner release")
        runner_identity = _text(runner.get("manifestIdentity"), "runner manifestIdentity")
        match = MANIFEST_IDENTITY.fullmatch(runner_identity)
        if match is None or match.group("digest") != runner_release.get("manifestDigest"):
            raise DeploymentError("fleet runner manifest identity is not immutable")
    if rollback_source is None:
        baseline_identity = inventory[0]["manifestIdentity"]
    else:
        baseline_identity = _text(evidence.get("manifestIdentity"), "rollback manifestIdentity")
    _validate_host_export_binding(config, fleet, evidence, inventory)

    minimum_available = fleet.get("minimumAvailable")
    if (
            type(minimum_available) is not int
        or minimum_available < 1
        or len(inventory) - 1 < minimum_available
    ):
        raise DeploymentError("sequential update would violate minimum fleet capacity")
    return inventory, observed, baseline_identity


def _validate_refreshed_fleet(
    plan: dict[str, Any],
    receipt: dict[str, Any],
    evidence: dict[str, Any],
    config: dict[str, Any],
    now: datetime,
) -> None:
    identity = _text(evidence.get("manifestIdentity"), "refreshed manifestIdentity")
    if identity != plan.get("manifestIdentity"):
        raise DeploymentError("refreshed manifest identity differs from plan")
    identity_match = MANIFEST_IDENTITY.fullmatch(identity)
    if identity_match is None:
        raise DeploymentError("refreshed manifest identity is not immutable")
    expected_release, expected_digest = _validate_release_evidence(
        _object(config.get("expectedRelease"), "expectedRelease"),
        evidence,
        identity_match,
        now,
    )
    if (
        expected_release != plan.get("selectedRelease")
        or expected_digest != plan.get("targetDigest")
    ):
        raise DeploymentError("refreshed manifest differs from selected release")

    fleet = _object(config.get("fleets"), "fleets")[plan["fleetSelector"]]
    inventory = _object(evidence.get("fleet"), "fleet evidence").get("runners")
    _validate_runner_admission(fleet, inventory)
    live_by_name = {
        runner.get("name"): runner
        for runner in inventory
        if isinstance(runner, dict)
    }
    final_by_name = {
        runner.get("name"): runner
        for runner in receipt.get("finalFleet", [])
        if isinstance(runner, dict)
    }
    if set(live_by_name) != set(final_by_name):
        raise DeploymentError("refreshed fleet inventory differs from receipt")

    variant = _text(config["expectedRelease"].get("variant"), "expectedRelease.variant")
    for name, final in final_by_name.items():
        live = live_by_name[name]
        expected_live_release = _object(final.get("release"), f"expected release for {name}")
        if live.get("release") != expected_live_release:
            raise DeploymentError(f"live release for {name} differs from receipt")
        normalized_release = _release(
            expected_live_release.get("releaseVersion"),
            expected_live_release.get("manifestDigest"),
            f"live release for {name}",
        )
        if expected_live_release != normalized_release:
            raise DeploymentError(f"live release for {name} has unrecognized fields")

        if expected_live_release == plan.get("selectedRelease"):
            expected_identity = plan.get("manifestIdentity")
            live_digest = plan.get("targetDigest")
        elif expected_live_release == plan.get("observedDeployedRelease"):
            expected_identity = plan.get("observedManifestIdentity")
            live_digest = _release_variant_digest(
                live.get("releaseManifest"),
                expected_live_release,
                variant,
                f"baseline release manifest for {name}",
                live.get("releaseManifestBytes", MISSING_MANIFEST_BYTES),
            )
        else:
            raise DeploymentError(f"live release for {name} is outside the immutable plan")

        if live.get("manifestIdentity") != expected_identity:
            raise DeploymentError(f"refreshed manifest identity for {name} differs from release")
        deployed_digest = live.get("deployedDigest")
        if not isinstance(deployed_digest, str) or DIGEST.fullmatch(deployed_digest) is None:
            raise DeploymentError(f"refreshed deployed digest for {name} is not immutable")
        if deployed_digest != live_digest:
            raise DeploymentError(f"refreshed deployed digest for {name} differs from release")


def build_plan(
    config: dict[str, Any],
    evidence: dict[str, Any],
    fleet_selector: str,
    *,
    now: datetime | None = None,
    action: str = "deploy",
    rollback_source: dict[str, Any] | None = None,
    deployment_contract_ref: str = "0000000000000000000000000000000000000000",
    preview: bool = False,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if preview:
        if evidence.get("preview") is not True or "authorization" in evidence:
            raise DeploymentError("preview evidence must omit deployment authorization")
    else:
        if "preview" in evidence or "observationAuthority" in evidence:
            raise DeploymentError("preview evidence cannot authorize deployment")
        _validate_deployment_authorization(evidence)
    _validate_commands(config)
    if re.fullmatch(r"[0-9a-f]{40}", deployment_contract_ref) is None:
        raise DeploymentError("deployment contract ref must be an immutable commit")
    expected = _object(config.get("expectedRelease"), "expectedRelease")
    _safe_token(
        expected.get("variant"),
        "expectedRelease.variant",
        r"[a-z0-9][a-z0-9._-]{0,63}",
    )
    _safe_token(fleet_selector, "fleet selector", r"[a-z][a-z0-9_-]{1,31}")
    identity = _text(evidence.get("manifestIdentity"), "manifestIdentity")
    identity_match = MANIFEST_IDENTITY.fullmatch(identity)
    if identity_match is None:
        raise DeploymentError("manifestIdentity must be an immutable digest reference")
    selected_release, target_digest = _validate_release_evidence(
        expected, evidence, identity_match, now
    )

    fleets = _object(config.get("fleets"), "fleets")
    fleet = _object(fleets.get(fleet_selector), "fleet selector")
    runners = fleet.get("runners")
    canary = fleet.get("canary")
    if (
        not isinstance(runners, list)
        or not runners
        or any(
            not isinstance(name, str)
            or name.startswith("-")
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) is None
            for name in runners
        )
        or len(set(runners)) != len(runners)
        or canary not in runners
    ):
        raise DeploymentError("fleet must declare unique runners and one member as canary")
    _validate_policy(fleet)

    requested_at = _date_time(evidence.get("requestedAt"), "requestedAt")
    if requested_at > now or (now - requested_at).total_seconds() > MAX_REQUEST_AGE_SECONDS:
        raise DeploymentError("deployment request is stale")
    if evidence.get("activeDeploymentCount") != 0:
        raise DeploymentError("concurrent deployment is already active")

    _, observed_release, baseline_identity = _validate_inventory(
        config, fleet, evidence, rollback_source if action == "rollback" else None
    )
    authority = _host_observation_authority(evidence)
    head_commit = evidence.get("headCommit")
    if not isinstance(head_commit, str) or re.fullmatch(r"[0-9a-f]{40}", head_commit) is None:
        raise DeploymentError("checked-out head commit evidence is invalid")
    head_tree = evidence.get("headTree")
    if not isinstance(head_tree, str) or re.fullmatch(r"[0-9a-f]{40}", head_tree) is None:
        raise DeploymentError("checked-out head tree evidence is invalid")
    if not preview:
        if authority.get("deployedCommit") != head_commit or authority.get("deployedTree") != head_tree:
            raise DeploymentError("deployment authority differs checked-out default-branch commit/tree")
        if authority.get("reviewedTree") != head_tree:
            raise DeploymentError("reviewed pull-request tree differs deployed default-branch tree")
    run_id = authority.get("workflowRunId")
    run_attempt = authority.get("workflowRunAttempt") if preview else evidence.get("workflowRunAttempt", 1)
    if (
        not isinstance(run_id, int)
        or run_id < 1
        or not isinstance(run_attempt, int)
        or run_attempt < 1
    ):
        raise DeploymentError("workflow run identity is invalid")

    rollback_of = None
    if action == "rollback":
        if rollback_source is None:
            raise DeploymentError("rollback requires a failed or interrupted source attempt")
        if rollback_source.get("schemaVersion") != 4:
            raise DeploymentError(
                "schema-v3 deployment attempts must finish or roll back before v4 cutover"
            )
        candidate = {
            "action": "rollback",
            "selectedRelease": selected_release,
            "rollbackOfAttempt": {
                "attemptId": rollback_source.get("attemptId"),
                "receiptDigest": receipt_digest(rollback_source),
            },
        }
        try:
            validate_rollback(candidate, rollback_source)
        except ValueError as error:
            raise DeploymentError(str(error)) from error
        rollback_of = candidate["rollbackOfAttempt"]
    elif action != "deploy":
        raise DeploymentError("action must be deploy or rollback")

    ordered = [canary, *sorted(name for name in runners if name != canary)]
    return {
        "schemaVersion": 1,
        "fleetSelector": fleet_selector,
        "action": action,
        "attemptId": f"{run_id}.{run_attempt}",
        "deploymentContractCommit": deployment_contract_ref,
        "headCommit": head_commit,
        "headTree": head_tree,
        "authorizationDigest": None if preview else canonical_digest(authority),
        **({"preview": True} if preview else {}),
        "manifestIdentity": identity,
        "selectedRelease": selected_release,
        "targetDigest": target_digest,
        "observedDeployedRelease": observed_release,
        "observedManifestIdentity": baseline_identity,
        "rollbackOfAttempt": rollback_of,
        "rolloutMode": "sequential",
        "plannedAt": now.isoformat().replace("+00:00", "Z"),
        "steps": [
            {"runner": runner, "phase": "canary" if index == 0 else "rollout"}
            for index, runner in enumerate(ordered)
        ],
    }


def validate_deployment_plan(
    plan: dict[str, Any],
    config: dict[str, Any],
    evidence: dict[str, Any],
    now: datetime,
    *,
    fleet_selector: str,
    action: str,
    deployment_contract_ref: str,
) -> None:
    plan = _object(plan, "deployment plan")
    if plan.get("preview") is True:
        raise DeploymentError("preview plan cannot be admitted")
    if (
        plan.get("fleetSelector") != fleet_selector
        or plan.get("action") != action
        or plan.get("deploymentContractCommit") != deployment_contract_ref
    ):
        raise DeploymentError("deployment plan selectors differ from the reviewed request")
    planned_at = _date_time(plan.get("plannedAt"), "plan.plannedAt")
    requested_at = _date_time(evidence.get("requestedAt"), "requestedAt")
    is_retained_plan = evidence.get("retainedPlan") is not None
    if (
        (not is_retained_plan and planned_at > now)
        or (
            not is_retained_plan
            and (now - planned_at).total_seconds() > MAX_REQUEST_AGE_SECONDS
        )
        or requested_at > now
        or (now - requested_at).total_seconds() > MAX_REQUEST_AGE_SECONDS
    ):
        raise DeploymentError("deployment plan or request is stale")

    if is_retained_plan:
        _validate_deployment_authorization(evidence)
        expected = retained_plan(
            config,
            evidence,
            fleet_selector,
            action,
            deployment_contract_ref,
        )
        if expected is None:
            raise DeploymentError("retained plan authority is unavailable")
    else:
        rollback_source = (
            evidence.get("rollbackSource") if plan.get("action") == "rollback" else None
        )
        expected = build_plan(
            config,
            evidence,
            fleet_selector,
            now=planned_at,
            action=action,
            rollback_source=rollback_source,
            deployment_contract_ref=deployment_contract_ref,
        )
    if plan != expected:
        raise DeploymentError("deployment plan differs from reviewed configuration and evidence")


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def admitted_receipt(
    plan: dict[str, Any],
    config: dict[str, Any],
    evidence: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    fleet = _object(config.get("fleets"), "fleets")[plan["fleetSelector"]]
    _validate_runner_admission(
        fleet, _object(evidence.get("fleet"), "fleet evidence").get("runners")
    )
    authorization = _object(evidence.get("authorization"), "authorization")
    required_authorization = {
        field: copy.deepcopy(authorization.get(field))
        for field in (
            "source",
            "repositoryId",
            "defaultBranch",
            "ref",
            "deployedCommit",
            "deployedTree",
            "environment",
            "deploymentBranchPolicy",
            "requiredReviewers",
            "preventSelfReview",
            "canAdminsBypass",
            "dispatcher",
            "dispatcherId",
            "triggeringActor",
            "triggeringActorId",
            "environmentBypassed",
            "bypassBasis",
            "workflowRunId",
            "workflowRunAttempt",
            "repository",
            "pullRequest",
            "reviewedHead",
            "reviewedTree",
            "patchDigest",
            "reviewGates",
        )
    }
    return {
        "schemaVersion": 4,
        "revision": 0,
        "attemptId": plan["attemptId"],
        "action": plan["action"],
        "outcome": "admitted",
        "environment": "production",
        "selectedRelease": copy.deepcopy(plan["selectedRelease"]),
        "observedDeployedRelease": copy.deepcopy(plan["observedDeployedRelease"]),
        "previousReceiptDigest": None,
        "rollbackOfAttempt": copy.deepcopy(plan["rollbackOfAttempt"]),
        "deploymentContractCommit": plan["deploymentContractCommit"],
        "headCommit": plan["headCommit"],
        "headTree": plan["headTree"],
        "planDigest": canonical_digest(plan),
        "manifestIdentity": plan["manifestIdentity"],
        "fleetSelector": plan["fleetSelector"],
        "canaryRunner": plan["steps"][0]["runner"],
        "authorization": required_authorization,
        "runners": [],
        "finalFleet": [
            {
                "name": runner["name"],
                "release": copy.deepcopy(runner["release"]),
                "state": "verified",
            }
            for runner in evidence["fleet"]["runners"]
        ],
        "failure": None,
        "observedAt": _timestamp(now),
        "startedAt": _timestamp(now),
        "completedAt": None,
    }


def retained_plan(
    config: dict[str, Any],
    evidence: dict[str, Any],
    fleet_selector: str,
    action: str,
    deployment_contract_ref: str,
) -> dict[str, Any] | None:
    plan = evidence.get("retainedPlan")
    retained = evidence.get("retainedRevisions")
    authority = evidence.get("retainedReceiptAuthority")
    if plan is None and retained in (None, []) and authority is None:
        return None
    if not isinstance(plan, dict) or not isinstance(retained, list) or not retained:
        raise DeploymentError("resume evidence omits the exact retained plan or receipt chain")
    try:
        validate_receipt_chain(retained)
    except ValueError as error:
        raise DeploymentError(f"retained receipt chain is invalid: {error}") from error
    latest = retained[-1]
    expected_authority = (
        rf"{re.escape(latest['attemptId'])}/[1-9][0-9]*@"
        + re.escape(retained_authority_digest(plan, retained))
    )
    if not isinstance(authority, str) or re.fullmatch(expected_authority, authority) is None:
        raise DeploymentError("retained receipt authority does not bind the exact chain")
    expected_fields = {
        "attemptId": latest["attemptId"],
        "action": action,
        "deploymentContractCommit": deployment_contract_ref,
        "headCommit": evidence.get("headCommit"),
        "manifestIdentity": evidence.get("manifestIdentity"),
        "fleetSelector": fleet_selector,
        "selectedRelease": latest["selectedRelease"],
        "observedDeployedRelease": latest["observedDeployedRelease"],
    }
    if any(plan.get(field) != value for field, value in expected_fields.items()):
        raise DeploymentError("retained plan differs from current immutable authority")
    if latest.get("planDigest") != canonical_digest(plan):
        raise DeploymentError("retained plan bytes differ from receipt authority")
    fleet = _object(_object(config.get("fleets"), "fleets").get(fleet_selector), "fleet")
    _validate_policy(fleet)
    expected_order = [
        fleet["canary"],
        *sorted(name for name in fleet["runners"] if name != fleet["canary"]),
    ]
    if plan.get("steps") != [
        {"runner": runner, "phase": "canary" if index == 0 else "rollout"}
        for index, runner in enumerate(expected_order)
    ]:
        raise DeploymentError("retained plan runner order differs from reviewed configuration")
    identity = _text(evidence.get("manifestIdentity"), "manifestIdentity")
    identity_match = MANIFEST_IDENTITY.fullmatch(identity)
    if identity_match is None:
        raise DeploymentError("manifestIdentity must be an immutable digest reference")
    expected_release, target_digest = _validate_release_evidence(
        _object(config.get("expectedRelease"), "expectedRelease"),
        evidence,
        identity_match,
        datetime.now(timezone.utc),
    )
    if plan.get("selectedRelease") != expected_release or plan.get("targetDigest") != target_digest:
        raise DeploymentError("retained plan release differs from current attested manifest")
    live_runners = {
        runner.get("name"): runner
        for runner in _object(evidence.get("fleet"), "fleet evidence").get("runners", [])
        if isinstance(runner, dict)
    }
    has_unknown = False
    for runner in latest["finalFleet"]:
        live = live_runners.get(runner.get("name"))
        if not isinstance(live, dict):
            raise DeploymentError("live fleet differs from retained resume state")
        if runner.get("state") == "verified":
            if live.get("release") != runner.get("release"):
                raise DeploymentError("live fleet differs from retained resume state")
            continue
        has_unknown = True
        if live.get("release") not in (
            latest.get("selectedRelease"),
            latest.get("observedDeployedRelease"),
        ) or not isinstance(live.get("deployedDigest"), str) or DIGEST.fullmatch(
            live["deployedDigest"]
        ) is None:
            raise DeploymentError("unknown runner requires exact live reconciliation evidence")
    if latest.get("outcome") not in ("admitted", "in_progress", "interrupted") and not (
        latest.get("outcome") == "failed" and has_unknown
    ):
        raise DeploymentError("retained receipt chain is not resumable")
    return plan


def _next_revision(
    previous: dict[str, Any],
    *,
    outcome: str,
    runners: list[dict[str, Any]],
    completed_at: str | None,
    failure: str | None = None,
) -> dict[str, Any]:
    revision = copy.deepcopy(previous)
    revision["revision"] = previous["revision"] + 1
    revision["outcome"] = outcome
    revision["previousReceiptDigest"] = receipt_digest(previous)
    revision["runners"] = copy.deepcopy(runners)
    revision["completedAt"] = completed_at
    revision["failure"] = failure
    transitions = {runner["name"]: runner for runner in runners}
    for runner in revision["finalFleet"]:
        transition = transitions.get(runner["name"])
        if transition is None:
            continue
        if transition["state"] == "unknown":
            runner["release"] = None
            runner["state"] = "unknown"
        else:
            runner["release"] = copy.deepcopy(transition["afterRelease"])
            runner["state"] = "verified"
    try:
        validate_attempt_revision(revision, previous)
    except ValueError as error:
        raise DeploymentError(str(error)) from error
    return revision


def reconcile_unknown_state(
    plan: dict[str, Any],
    previous: dict[str, Any],
    evidence: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    try:
        validate_receipt(previous)
    except ValueError as error:
        raise DeploymentError(f"reconciliation receipt is invalid: {error}") from error
    if previous.get("outcome") not in ("failed", "interrupted"):
        raise DeploymentError("only a failed or interrupted unknown state can be reconciled")
    unknown_names = {
        runner.get("name")
        for runner in previous.get("runners", [])
        if runner.get("state") == "unknown"
    }
    if not unknown_names:
        raise DeploymentError("receipt has no unknown runner state to reconcile")
    for field in (
        "attemptId",
        "action",
        "deploymentContractCommit",
        "headCommit",
        "headTree",
        "manifestIdentity",
        "fleetSelector",
        "selectedRelease",
        "observedDeployedRelease",
    ):
        if previous.get(field) != plan.get(field):
            raise DeploymentError(f"reconciliation changes immutable {field}")
    if previous.get("planDigest") != canonical_digest(plan):
        raise DeploymentError("reconciliation changes immutable plan authority")
    expected_release = _object(config.get("expectedRelease"), "expectedRelease")
    variant = _text(expected_release.get("variant"), "expectedRelease.variant")
    if evidence.get("manifestIdentity") != plan.get("manifestIdentity"):
        raise DeploymentError("reconciliation changes selected manifest identity")
    selected_digest = _release_variant_digest(
        evidence.get("manifest"),
        _object(plan.get("selectedRelease"), "selectedRelease"),
        variant,
        "selected release manifest",
        evidence.get("manifestBytes", MISSING_MANIFEST_BYTES),
    )
    if selected_digest != plan.get("targetDigest"):
        raise DeploymentError("selected release manifest differs from plan image digest")
    live_runners = {
        runner.get("name"): runner
        for runner in _object(evidence.get("fleet"), "fleet evidence").get("runners", [])
        if isinstance(runner, dict)
    }
    final_names = {runner["name"] for runner in previous["finalFleet"]}
    if set(live_runners) != final_names:
        raise DeploymentError("reconciliation live fleet inventory differs")
    for name, live in live_runners.items():
        live_release = _object(live.get("release"), f"live release for {name}")
        normalized_release = _release(
            live_release.get("releaseVersion"),
            live_release.get("manifestDigest"),
            f"live release for {name}",
        )
        if live_release != normalized_release:
            raise DeploymentError(f"live release for {name} has unrecognized fields")
        live_identity = live.get("manifestIdentity")
        identity_match = (
            MANIFEST_IDENTITY.fullmatch(live_identity)
            if isinstance(live_identity, str)
            else None
        )
        if (
            identity_match is None
            or identity_match.group("digest") != live_release["manifestDigest"]
        ):
            raise DeploymentError(f"live manifest identity for {name} is inconsistent")
    transitions = copy.deepcopy(previous["runners"])
    reconciled: list[dict[str, Any]] = []
    for transition in transitions:
        name = transition["name"]
        live = live_runners[name]
        live_release = live.get("release")
        deployed_digest = live.get("deployedDigest")
        if name not in unknown_names:
            recorded = next(
                runner for runner in previous["finalFleet"] if runner["name"] == name
            )
            if recorded.get("state") != "verified" or live_release != recorded.get("release"):
                raise DeploymentError("verified runner changed during reconciliation")
            reconciled.append(transition)
            continue
        if not isinstance(deployed_digest, str) or DIGEST.fullmatch(deployed_digest) is None:
            raise DeploymentError("unknown runner lacks an immutable live image digest")
        if live_release == plan["selectedRelease"]:
            if live.get("manifestIdentity") != plan.get("manifestIdentity"):
                raise DeploymentError("selected live release identity differs from plan")
            if deployed_digest != selected_digest:
                raise DeploymentError("selected live release has the wrong image digest")
            reconciled.append(
                {
                    "name": name,
                    "beforeDigest": None,
                    "afterDigest": deployed_digest,
                    "afterRelease": copy.deepcopy(live_release),
                    "state": "reconciled",
                    "probe": "not_run",
                    "observation": (
                        "pending" if name == previous["canaryRunner"] else "not_required"
                    ),
                    "completedAt": None,
                }
            )
        elif live_release == previous["observedDeployedRelease"]:
            if live.get("manifestIdentity") != plan.get("observedManifestIdentity"):
                raise DeploymentError("baseline live release identity differs from plan")
            baseline_digest = _release_variant_digest(
                live.get("releaseManifest"),
                _object(plan.get("observedDeployedRelease"), "observedDeployedRelease"),
                variant,
                f"baseline release manifest for {name}",
                live.get("releaseManifestBytes", MISSING_MANIFEST_BYTES),
            )
            if deployed_digest != baseline_digest:
                raise DeploymentError("baseline live release has the wrong image digest")
        else:
            raise DeploymentError("unknown runner is neither selected nor baseline release")
    revision = copy.deepcopy(previous)
    revision["revision"] = previous["revision"] + 1
    revision["outcome"] = "in_progress"
    revision["previousReceiptDigest"] = receipt_digest(previous)
    revision["runners"] = reconciled
    revision["finalFleet"] = [
        {
            "name": runner["name"],
            "release": copy.deepcopy(live_runners[runner["name"]]["release"]),
            "state": "verified",
        }
        for runner in previous["finalFleet"]
    ]
    revision["failure"] = None
    revision["completedAt"] = None
    try:
        validate_attempt_revision(revision, previous)
        validate_receipt(revision)
    except ValueError as error:
        raise DeploymentError(f"reconciled receipt is invalid: {error}") from error
    return revision


def execute_plan(
    plan: dict[str, Any],
    config: dict[str, Any],
    evidence: dict[str, Any],
    adapter: Any,
    persist: Any,
    *,
    dry_run: bool = False,
    previous_receipt: dict[str, Any] | None = None,
    max_hosts: int | None = None,
    clock: Any | None = None,
) -> dict[str, Any]:
    if plan.get("preview") is True:
        raise DeploymentError("preview plan cannot be executed")
    if dry_run:
        return plan
    authorization = _object(evidence.get("authorization"), "authorization")
    if canonical_digest(authorization) != plan.get("authorizationDigest"):
        raise DeploymentError("GitHub authorization changed after plan admission")
    if clock is None:
        clock = type("SystemClock", (), {"now": staticmethod(lambda: datetime.now(timezone.utc))})()
    fleet = config["fleets"][plan["fleetSelector"]]
    _validate_runner_admission(
        fleet, _object(evidence.get("fleet"), "fleet evidence").get("runners")
    )

    if previous_receipt is None:
        current = admitted_receipt(plan, config, evidence, clock.now())
        persist(copy.deepcopy(current))
    else:
        current = copy.deepcopy(previous_receipt)
        try:
            validate_receipt(current)
        except ValueError as error:
            raise DeploymentError(f"resume receipt is invalid: {error}") from error
        if current.get("outcome") not in ("admitted", "in_progress", "interrupted"):
            raise DeploymentError("resume receipt is not resumable")
        for field in (
            "attemptId",
            "action",
            "deploymentContractCommit",
            "headCommit",
            "manifestIdentity",
            "fleetSelector",
            "selectedRelease",
            "observedDeployedRelease",
        ):
            expected = plan[field]
            if current.get(field) != expected:
                raise DeploymentError(f"resume changes immutable {field}")
        if current.get("planDigest") != canonical_digest(plan):
            raise DeploymentError("resume changes immutable plan authority")
        live_by_name = {
            runner.get("name"): runner.get("release")
            for runner in _object(evidence.get("fleet"), "fleet evidence").get("runners", [])
            if isinstance(runner, dict)
        }
        for runner in current["finalFleet"]:
            if (
                runner["state"] != "verified"
                or live_by_name.get(runner["name"]) != runner["release"]
            ):
                raise DeploymentError("live fleet differs from retained resume state")
        _validate_refreshed_fleet(plan, current, evidence, config, clock.now())

    completed = copy.deepcopy(current.get("runners", []))
    completed_names = {
        runner.get("name")
        for runner in completed
        if runner.get("probe") == "passed"
        and runner.get("state") in ("updated", "restored", "reconciled")
        and runner.get("afterRelease") == current.get("selectedRelease")
        and runner.get("observation") in ("not_required", "passed")
    }
    target_variant = config["expectedRelease"]["variant"]
    advanced = 0
    for step in plan["steps"]:
        runner = step["runner"]
        if runner in completed_names:
            continue
        if max_hosts is not None and advanced >= max_hosts:
            break
        observation_pending = next(
            (
                transition
                for transition in completed
                if transition.get("name") == runner
                and transition.get("probe") == "passed"
                and transition.get("observation") == "pending"
            ),
            None,
        )
        if observation_pending is not None:
            try:
                adapter.observe(fleet["observationSeconds"])
            except (OSError, RuntimeError) as error:
                failed = _next_revision(
                    current,
                    outcome="interrupted",
                    runners=completed,
                    completed_at=_timestamp(clock.now()),
                    failure=f"canary observation interrupted: {error}",
                )
                persist(copy.deepcopy(failed))
                return failed
            observation_pending["observation"] = "passed"
            observation_pending["completedAt"] = _timestamp(clock.now())
            current = _next_revision(
                current,
                outcome="in_progress",
                runners=completed,
                completed_at=None,
            )
            persist(copy.deepcopy(current))
            advanced += 1
            continue
        pending = next(
            (
                transition
                for transition in completed
                if transition.get("name") == runner
                and transition.get("probe") == "not_run"
                and transition.get("state") in ("updated", "restored", "reconciled")
                and transition.get("afterRelease") == plan["selectedRelease"]
            ),
            None,
        )
        if pending is None:
            try:
                if adapter.available_capacity() - 1 < fleet["minimumAvailable"]:
                    raise DeploymentError("live spare capacity would fall below policy floor")
            except (DeploymentError, OSError, RuntimeError, subprocess.SubprocessError) as error:
                failed = _next_revision(
                    current,
                    outcome="failed",
                    runners=completed,
                    completed_at=_timestamp(clock.now()),
                    failure=str(error),
                )
                persist(copy.deepcopy(failed))
                return failed
            try:
                result = adapter.update_runner(
                    runner,
                    plan["manifestIdentity"],
                    target_variant,
                    fleet["drainTimeoutSeconds"],
                )
                _validate_runner_result(result, runner, plan, fleet)
            except DeploymentInterrupted as error:
                completed.append({
                    "name": runner,
                    "beforeDigest": None,
                    "afterDigest": None,
                    "afterRelease": None,
                    "state": "unknown",
                    "probe": "not_run",
                    "observation": "not_required",
                    "completedAt": None,
                })
                interrupted = _next_revision(
                    current,
                    outcome="interrupted",
                    runners=completed,
                    completed_at=_timestamp(clock.now()),
                    failure=str(error),
                )
                persist(copy.deepcopy(interrupted))
                return interrupted
            except (DeploymentError, OSError, RuntimeError, subprocess.SubprocessError) as error:
                completed.append({
                    "name": runner,
                    "beforeDigest": None,
                    "afterDigest": None,
                    "afterRelease": None,
                    "state": "unknown",
                    "probe": "not_run",
                    "observation": "not_required",
                    "completedAt": None,
                })
                failed = _next_revision(
                    current,
                    outcome="failed",
                    runners=completed,
                    completed_at=_timestamp(clock.now()),
                    failure=str(error),
                )
                persist(copy.deepcopy(failed))
                return failed
            entry = {
                "name": runner,
                "beforeDigest": result["beforeDigest"],
                "afterDigest": result["afterDigest"],
                "afterRelease": copy.deepcopy(plan["selectedRelease"]),
                "state": "restored" if plan["action"] == "rollback" else "updated",
                "probe": "not_run",
                "observation": "pending" if step["phase"] == "canary" else "not_required",
                "completedAt": None,
            }
            completed.append(entry)
            current = _next_revision(
                current,
                outcome="in_progress",
                runners=completed,
                completed_at=None,
            )
            persist(copy.deepcopy(current))
        else:
            entry = pending
        try:
            probe_result = adapter.probe_runner(runner, fleet["probeTimeoutSeconds"])
            probe = _validate_probe(probe_result, runner)
        except (DeploymentError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            probe = "timeout"
            probe_failure = str(error)
        else:
            probe_failure = f"representative probe {probe}"
        entry["probe"] = probe
        if probe != "passed":
            entry["observation"] = "not_required"
            entry["completedAt"] = _timestamp(clock.now())
            failed = _next_revision(
                current,
                outcome="failed",
                runners=completed,
                completed_at=_timestamp(clock.now()),
                failure=probe_failure,
            )
            persist(copy.deepcopy(failed))
            return failed

        if step["phase"] != "canary":
            entry["completedAt"] = _timestamp(clock.now())
        current = _next_revision(
            current,
            outcome="in_progress",
            runners=completed,
            completed_at=None,
        )
        persist(copy.deepcopy(current))
        if step["phase"] == "canary":
            try:
                adapter.observe(fleet["observationSeconds"])
            except (OSError, RuntimeError) as error:
                failed = _next_revision(
                    current,
                    outcome="interrupted",
                    runners=completed,
                    completed_at=_timestamp(clock.now()),
                    failure=f"canary observation interrupted: {error}",
                )
                persist(copy.deepcopy(failed))
                return failed
            entry["observation"] = "passed"
            entry["completedAt"] = _timestamp(clock.now())
            current = _next_revision(
                current,
                outcome="in_progress",
                runners=completed,
                completed_at=None,
            )
            persist(copy.deepcopy(current))
        advanced += 1

    remaining = {
        step["runner"] for step in plan["steps"]
    } - {
        runner["name"]
        for runner in completed
        if runner.get("probe") == "passed"
        and runner.get("observation") in ("not_required", "passed")
    }
    if remaining:
        return current
    succeeded = _next_revision(
        current,
        outcome="succeeded",
        runners=completed,
        completed_at=_timestamp(clock.now()),
    )
    persist(copy.deepcopy(succeeded))
    return succeeded


def _validate_runner_result(
    result: Any, runner: str, plan: dict[str, Any], fleet: dict[str, Any]
) -> None:
    value = _object(result, f"runner update result for {runner}")
    checks = (
        (value.get("drained") is True, "bounded drain did not succeed"),
        (value.get("online") is True, "runner is not online"),
        (value.get("admitted") is True, "runner is not admitted"),
        (value.get("healthy") is True, "runner runtime health failed"),
        (value.get("transactionLocked") is False, "runner has a transaction lock"),
        (
            value.get("runnerGroup") == fleet.get("runnerGroup"),
            "runner group differs from reviewed configuration",
        ),
        (
            set(fleet.get("requiredLabels", [])).issubset(set(value.get("labels", []))),
            "runner labels are incomplete",
        ),
        (
            set(fleet.get("requiredTools", [])).issubset(set(value.get("tools", []))),
            "runner tools are incomplete",
        ),
        (
            value.get("manifestIdentity") == plan.get("manifestIdentity"),
            "runner manifest identity differs",
        ),
        (value.get("afterDigest") == plan.get("targetDigest"), "runner digest differs"),
        (
            type(value.get("availableCapacity")) is int
            and value["availableCapacity"] >= fleet.get("minimumAvailable", 0),
            "post-update available capacity is below policy floor",
        ),
    )
    for accepted, message in checks:
        if not accepted:
            raise DeploymentError(message)
    if (
        not isinstance(value.get("beforeDigest"), str)
        or DIGEST.fullmatch(value["beforeDigest"]) is None
    ):
        raise DeploymentError("runner before digest is invalid")


def _validate_probe(result: Any, runner: str) -> str:
    value = _object(result, "representative probe result")
    if value.get("routedRunner") != runner:
        raise DeploymentError("representative probe routing differs from selected runner")
    outcome = value.get("outcome")
    if outcome not in ("passed", "failed"):
        raise DeploymentError("representative probe timed out or has invalid outcome")
    return outcome


def _child_environment(
    extra: dict[str, str] | None = None, *, control: bool = False
) -> dict[str, str]:
    allowed = (
        "PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
    )
    if control:
        allowed += (
            "RUNNER_TEMP", "VERJSON_DEPLOYMENT_CLI", "VERJSON_DEPLOYMENT_CLI_ROOT",
        )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    if control and extra:
        environment.update(extra)
    return environment


class ProcessAdapter:
    def __init__(
        self,
        config: dict[str, Any],
        fleet: dict[str, Any],
        evidence: dict[str, Any] | None = None,
        plan: dict[str, Any] | None = None,
    ):
        self.config = config
        self.fleet = fleet
        self.evidence = evidence
        self.plan = plan

    def _host_export(
        self, purpose: str, runner_name: str | None = None
    ) -> dict[str, Any]:
        if self.evidence is None or self.plan is None:
            raise DeploymentError("host export requires the admitted plan and evidence")
        request = _build_host_export_request(
            self.config,
            self.evidence,
            self.plan["fleetSelector"],
            purpose=purpose,
            runner_name=runner_name,
            plan=self.plan,
        )
        if request.get("planDigest") != canonical_digest(self.plan):
            raise DeploymentError("host export request does not bind admitted plan")
        return _run_host_export_transport(request)

    @staticmethod
    def _invoke(
        command: list[str],
        env: dict[str, str] | None = None,
        *,
        control: bool = False,
        timeout_seconds: int = 2_000,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=_child_environment(env, control=control),
            timeout=timeout_seconds,
        )

    @classmethod
    def _run(
        cls,
        command: list[str],
        *,
        timeout_seconds: int = POST_UPDATE_EVIDENCE_SECONDS,
    ) -> dict[str, Any]:
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds < 1
            or timeout_seconds > MAX_ADAPTER_JSON_SECONDS
        ):
            raise DeploymentError(
                f"adapter timeout must be between 1 and {MAX_ADAPTER_JSON_SECONDS} seconds"
            )
        completed = cls._invoke(command, timeout_seconds=timeout_seconds)
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise DeploymentError("adapter command did not emit one JSON object") from error
        return _object(value, "adapter command output")

    def update_runner(
        self, runner: str, manifest_identity: str, variant: str, timeout_seconds: int
    ) -> dict[str, Any]:
        deploy_token = os.environ.get("DIGITALOCEAN_RUNNER_FLEET_TOKEN")
        github_token = os.environ.get("GH_RUNNER_CONTROL_TOKEN")
        if not deploy_token:
            raise DeploymentError("DIGITALOCEAN_RUNNER_FLEET_TOKEN is unavailable")
        if not github_token:
            raise DeploymentError("GH_RUNNER_CONTROL_TOKEN is unavailable")
        environment = {
            "DIGITALOCEAN_ACCESS_TOKEN": deploy_token,
            "GH_TOKEN": github_token,
        }
        command = [
            _deployment_cli(),
            "runner",
            "update",
            self.fleet["lane"],
            "--cloud",
            "digitalocean",
            "--project",
            self.fleet["project"],
            "--runner-mode",
            "persistent",
            "--release-manifest",
            manifest_identity,
            "--release-variant",
            variant,
            "--only",
            runner,
        ]
        try:
            self._invoke(
                command,
                environment,
                control=True,
                timeout_seconds=timeout_seconds + 120,
            )
        except subprocess.SubprocessError as error:
            raise DeploymentInterrupted(
                f"runner update did not return verified terminal evidence for {runner}"
            ) from error
        return _object(
            self._host_export("post-update", runner).get("hostEvidence"),
            "post-update host evidence",
        )

    def available_capacity(self) -> int:
        result = _object(
            self._host_export("capacity").get("hostEvidence"),
            "host capacity evidence",
        )
        capacity = result.get("availableCapacity")
        if type(capacity) is not int or capacity < 0:
            raise DeploymentError("capacity evidence is malformed")
        return capacity

    def probe_runner(self, runner: str, timeout_seconds: int) -> dict[str, Any]:
        return self._run(
            [
                *_command(self.config, "probeCommand"),
                "--runner",
                runner,
                "--timeout-seconds",
                str(timeout_seconds),
            ],
            timeout_seconds=timeout_seconds + 30,
        )

    @staticmethod
    def observe(seconds: int) -> None:
        time.sleep(seconds)


def _persist_directory(receipt_dir: Path, start_index: int = 0):
    index = start_index

    def persist(receipt: dict[str, Any]) -> None:
        nonlocal index
        validate_receipt(receipt)
        destination = receipt_dir / f"revision-{index:04d}.json"
        if destination.exists():
            raise DeploymentError(f"refusing to overwrite retained receipt {destination}")
        _write(destination, receipt)
        index += 1

    return persist


def _restore_receipts(
    evidence: dict[str, Any], plan: dict[str, Any], receipt_dir: Path
) -> dict[str, Any] | None:
    retained = evidence.get("retainedRevisions", [])
    authority = evidence.get("retainedReceiptAuthority")
    if retained == [] and authority is None:
        return None
    if not isinstance(retained, list) or not retained or not all(
        isinstance(receipt, dict) for receipt in retained
    ):
        raise DeploymentError("retained receipt revisions are malformed")
    try:
        validate_receipt_chain(retained)
    except ValueError as error:
        raise DeploymentError(f"retained receipt chain is invalid: {error}") from error
    latest = retained[-1]
    expected_authority = (
        rf"{re.escape(latest['attemptId'])}/[1-9][0-9]*@"
        + re.escape(retained_authority_digest(plan, retained))
    )
    if not isinstance(authority, str) or re.fullmatch(expected_authority, authority) is None:
        raise DeploymentError("retained receipt authority does not bind the exact chain")
    expected = {
        "attemptId": plan["attemptId"],
        "headCommit": plan["headCommit"],
        "manifestIdentity": plan["manifestIdentity"],
        "planDigest": canonical_digest(plan),
    }
    if any(latest.get(field) != value for field, value in expected.items()):
        raise DeploymentError("retained receipt authority differs from this exact plan")
    for receipt in retained:
        destination = receipt_dir / f"revision-{receipt['revision']:04d}.json"
        if destination.exists():
            if _load(destination) != receipt:
                raise DeploymentError("local receipt revision differs from retained authority")
        else:
            _write(destination, receipt)
    return latest


def _build_host_export_request(
    config: dict[str, Any],
    evidence: dict[str, Any],
    fleet_selector: str,
    purpose: str = "baseline",
    runner_name: str | None = None,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if purpose not in ("baseline", "post-update", "capacity"):
        raise DeploymentError("host export purpose is invalid")
    fleet = _object(config.get("fleets"), "fleets").get(fleet_selector)
    fleet = _object(fleet, f"fleet {fleet_selector}")
    host_config = _object(fleet.get("hostEvidence"), "hostEvidence")
    if set(host_config) != {"doContext", "doSshKey", "maxAgeSeconds"}:
        raise DeploymentError("hostEvidence fields differ from reviewed configuration")
    do_context = _safe_token(host_config.get("doContext"), "hostEvidence.doContext", r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
    do_ssh_key = _safe_token(host_config.get("doSshKey"), "hostEvidence.doSshKey", r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
    max_age = host_config.get("maxAgeSeconds")
    if not isinstance(max_age, int) or isinstance(max_age, bool) or not 1 <= max_age <= 3600:
        raise DeploymentError("hostEvidence.maxAgeSeconds is invalid")
    runners = fleet.get("runners")
    if (
        not isinstance(runners, list)
        or not runners
        or any(not isinstance(name, str) for name in runners)
        or len(set(runners)) != len(runners)
    ):
        raise DeploymentError("reviewed fleet runner names are invalid")
    if purpose == "post-update" and runner_name not in runners:
        raise DeploymentError("post-update host runner is outside the reviewed fleet")
    if purpose != "post-update" and runner_name is not None:
        raise DeploymentError("host runner selection is only valid for post-update evidence")

    authority = _object(config.get("hostEvidenceAuthority"), "hostEvidenceAuthority")
    if set(authority) != {"appId", "installationId"}:
        raise DeploymentError("hostEvidenceAuthority fields differ")
    app_id, installation_id = authority.get("appId"), authority.get("installationId")
    if (
        not isinstance(app_id, int) or isinstance(app_id, bool) or app_id < 1
        or not isinstance(installation_id, int) or isinstance(installation_id, bool)
        or installation_id < 1
    ):
        raise DeploymentError("reviewed host observation App identities are unavailable")

    expected = _object(config.get("expectedRelease"), "expectedRelease")
    identity = _text(evidence.get("manifestIdentity"), "manifestIdentity")
    identity_match = MANIFEST_IDENTITY.fullmatch(identity)
    if identity_match is None:
        raise DeploymentError("host export manifest identity is invalid")
    if plan is not None and plan.get("manifestIdentity") != identity:
        raise DeploymentError("host export manifest identity differs admitted plan")
    manifest = _object(evidence.get("manifest"), "release manifest")
    source = _object(manifest.get("source"), "release manifest source")
    workflow = _object(_object(manifest.get("release"), "release manifest release").get("workflow"), "release manifest workflow")
    source_commit = source.get("commit")
    asset_id = evidence.get("releaseAssetId")
    if not isinstance(source_commit, str) or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise DeploymentError("release manifest source commit is unavailable")
    if not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id < 1:
        raise DeploymentError("release manifest asset identity is unavailable")
    manifest_bytes = evidence.get("manifestBytes")
    if not isinstance(manifest_bytes, str):
        raise DeploymentError("exact release manifest bytes are unavailable")
    _manifest_with_identity(manifest, identity, "release manifest", manifest_bytes)

    authority = _host_observation_authority(evidence)
    repository = _text(authority.get("repository"), "host observation repository")
    repository_id = authority.get("repositoryId")
    if repository != expected.get("sourceRepository") or not isinstance(repository_id, int) or isinstance(repository_id, bool) or repository_id < 1:
        raise DeploymentError("host observation repository identity is unavailable")
    attempt_id = plan.get("attemptId") if plan else None
    if not attempt_id:
        workflow_run_id = authority.get("workflowRunId")
        workflow_attempt = authority.get("workflowRunAttempt", evidence.get("workflowRunAttempt", 1))
        if (
            not isinstance(workflow_run_id, int) or isinstance(workflow_run_id, bool) or workflow_run_id < 1
            or not isinstance(workflow_attempt, int) or isinstance(workflow_attempt, bool) or workflow_attempt < 1
        ):
            raise DeploymentError("host observation workflow attempt identity is unavailable")
        attempt_id = f"{workflow_run_id}.{workflow_attempt}"
    action = plan.get("action") if plan else ("rollback" if evidence.get("rollbackSource") else "deploy")
    rollback_of = plan.get("rollbackOfAttempt") if plan else None
    if action == "rollback" and rollback_of is None:
        rollback_source = _object(evidence.get("rollbackSource"), "rollbackSource")
        rollback_of = rollback_source.get("attemptId")
    contract_ref = plan.get("deploymentContractCommit") if plan else os.environ.get("VERJSON_DEPLOYMENT_CONTRACT_REF")
    if not isinstance(contract_ref, str) or re.fullmatch(r"[0-9a-f]{40}", contract_ref) is None:
        raise DeploymentError("immutable deployment contract identity is unavailable")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    release = _release(
        manifest.get("releaseVersion"), identity, "host export release"
    )
    variant = expected.get("variant")
    release.update({
        "repository": expected.get("sourceRepository"),
        "assetId": asset_id,
        "variant": variant,
        "imageDigest": _release_variant_digest(
            manifest, release, variant, "release manifest", manifest_bytes
        ),
        "sourceCommit": source_commit,
        "sourceRef": expected.get("sourceRef"),
        "signerWorkflow": expected.get("signerWorkflow"),
        "signerCommit": workflow.get("contractCommit"),
    })
    host = {
        "project": fleet.get("project"),
        "doContext": do_context,
        "doSshKey": do_ssh_key,
        "runnerNames": runners,
        "maxAgeSeconds": max_age,
        "purpose": purpose,
        "runnerName": runner_name,
    }
    return {
        "schemaVersion": 1,
        "operation": "host-export",
        "attemptId": attempt_id,
        "fleetSelector": fleet_selector,
        "lane": fleet.get("lane"),
        "issuedAt": _timestamp(now),
        "expiresAt": _timestamp(now + timedelta(minutes=15)),
        "deploymentContractCommit": contract_ref,
        "configDigest": canonical_digest(config),
        "planDigest": canonical_digest(plan) if plan else None,
        "action": action,
        "rollbackOfAttempt": rollback_of,
        "github": {
            "repository": repository,
            "repositoryId": repository_id,
            "appId": app_id,
            "installationId": installation_id,
        },
        "release": release,
        "hostExport": host,
    }


def _run_host_export_transport(
    request: dict[str, Any], timeout_seconds: int = ADMISSION_EVIDENCE_SECONDS
) -> dict[str, Any]:
    missing = [name for name in HOST_EXPORT_SECRET_ENV if not os.environ.get(name)]
    if missing:
        raise DeploymentError("read-only host observation authority is not provisioned")
    with tempfile.TemporaryDirectory(prefix="deployment-host-request-") as temporary:
        root = Path(temporary)
        request_path = root / "request.json"
        output_path = root / "host-evidence.json"
        _write(request_path, request)
        try:
            ProcessAdapter._invoke(
                [
                    "python3", "scripts/container_deployment_transport.py",
                    "--request", str(request_path), "--output", str(output_path),
                ],
                {name: os.environ[name] for name in HOST_EXPORT_SECRET_ENV},
                control=True,
                timeout_seconds=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError, DeploymentError):
            raise DeploymentError("canonical host evidence transport failed") from None
        result = _load(output_path)
    if (
        result.get("schemaVersion") != 1
        or result.get("requestDigest") != canonical_digest(request)
        or result.get("outcome") != "passed"
    ):
        raise DeploymentError("canonical host evidence receipt differs from request")
    return result


def _collect_evidence(
    config: dict[str, Any],
    manifest_identity: str,
    fleet_selector: str,
    rollback_receipt: str = "",
    authorization_path: Path | None = None,
    preview: bool = False,
) -> dict[str, Any]:
    if MANIFEST_IDENTITY.fullmatch(manifest_identity) is None:
        raise DeploymentError("manifest identity must be an immutable digest reference")
    _safe_token(fleet_selector, "fleet selector", r"[a-z][a-z0-9_-]{1,31}")
    if any(not os.environ.get(name) for name in HOST_EXPORT_SECRET_ENV):
        raise DeploymentError("read-only host observation authority not provisioned")
    authority = _object(config.get("hostEvidenceAuthority"), "hostEvidenceAuthority")
    if set(authority) != {"appId", "installationId"} or any(
        type(authority.get(name)) is not int or authority[name] < 1
        for name in ("appId", "installationId")
    ):
        raise DeploymentError("host evidence authority identities are unavailable")
    fleet_config = _object(config.get("fleets"), "fleets").get(fleet_selector)
    fleet_config = _object(fleet_config, f"fleet {fleet_selector}")
    host_config = _object(fleet_config.get("hostEvidence"), "hostEvidence")
    if set(host_config) != {"doContext", "doSshKey", "maxAgeSeconds"}:
        raise DeploymentError("hostEvidence fields differ")
    command = [
        *_command(config, "evidenceCommand"),
        "--manifest-identity",
        manifest_identity,
        "--fleet",
        fleet_selector,
    ]
    if rollback_receipt:
        if DIGEST.fullmatch(rollback_receipt) is None:
            raise DeploymentError("rollback receipt identity must be a canonical digest")
        command.extend(("--rollback-receipt", rollback_receipt))
    evidence = ProcessAdapter._run(
        command, timeout_seconds=ADMISSION_EVIDENCE_SECONDS
    )
    github_run_id = os.environ.get("GITHUB_RUN_ID")
    github_run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
    if preview:
        if authorization_path is not None or "authorization" in evidence:
            raise DeploymentError("preview evidence must omit deployment authorization")
        repository = os.environ.get("GITHUB_REPOSITORY")
        repository_id = os.environ.get("GITHUB_REPOSITORY_ID")
        if not repository or any(
            value is None or re.fullmatch(r"[1-9][0-9]*", value) is None
            for value in (repository_id, github_run_id, github_run_attempt)
        ):
            raise DeploymentError("preview workflow identity is unavailable")
        evidence["observationAuthority"] = {
            "repository": repository,
            "repositoryId": int(repository_id),
            "workflowRunId": int(github_run_id),
            "workflowRunAttempt": int(github_run_attempt),
        }
        evidence["workflowRunAttempt"] = int(github_run_attempt)
        evidence["preview"] = True
    elif authorization_path is not None:
        evidence["authorization"] = _load(authorization_path)
    github_sha = os.environ.get("GITHUB_SHA")
    if github_sha and evidence.get("headCommit") != github_sha:
        raise DeploymentError("evidence checked-out head differs from workflow authority")
    github_tree = os.environ.get("GITHUB_HEAD_TREE")
    if github_tree and evidence.get("headTree") != github_tree:
        raise DeploymentError("evidence checked-out tree differs from workflow authority")
    authorization = evidence.get("authorization")
    if not preview and github_run_id and (
        not isinstance(authorization, dict)
        or authorization.get("workflowRunId") != int(github_run_id)
    ):
        raise DeploymentError("evidence workflow run differs from workflow authority")
    if not preview and github_run_attempt and evidence.get("workflowRunAttempt") != int(github_run_attempt):
        raise DeploymentError("evidence workflow attempt differs from workflow authority")
    observed_identity = evidence.get("manifestIdentity")
    if observed_identity not in (None, manifest_identity):
        raise DeploymentError("evidence command substituted manifest identity")
    evidence["manifestIdentity"] = manifest_identity
    if rollback_receipt:
        source = _object(evidence.get("rollbackSource"), "rollbackSource")
        validate_receipt(source)
        if receipt_digest(source) != rollback_receipt:
            raise DeploymentError("retrieved rollback receipt differs from requested digest")
        evidence["rollbackReceiptIdentity"] = rollback_receipt
    request = _build_host_export_request(config, evidence, fleet_selector)
    host_result = _run_host_export_transport(request)
    release = _object(host_result.get("releaseManifest"), "verified release manifest")
    if release.get("manifestIdentity") != request["release"]["manifestDigest"]:
        raise DeploymentError("verified release manifest identity differs request")
    evidence["manifestIdentity"] = release["manifestIdentity"]
    evidence["manifestBytes"] = release["manifestBytes"]
    evidence["manifest"] = release["manifest"]
    evidence["attestation"] = release["attestation"]
    host_report = _object(host_result.get("hostEvidence"), "host evidence report")
    host_fleet = _object(host_report.get("fleet"), "host evidence fleet")
    evidence["fleet"] = host_fleet
    evidence["hostExportRequest"] = request
    evidence["hostExport"] = host_result
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Protected sequential runner deployment")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect-evidence")
    collect.add_argument("--config", required=True, type=Path)
    collect.add_argument("--manifest-identity", required=True)
    collect.add_argument("--fleet", required=True)
    collect.add_argument("--rollback-receipt", default="")
    collect.add_argument("--authorization", type=Path)
    collect.add_argument("--preview", action="store_true")
    collect.add_argument("--output", required=True, type=Path)

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--config", required=True, type=Path)
    plan_parser.add_argument("--evidence", required=True, type=Path)
    plan_parser.add_argument("--fleet", required=True)
    plan_parser.add_argument("--action", choices=("deploy", "rollback"), required=True)
    plan_parser.add_argument("--contract-ref", required=True)
    plan_parser.add_argument("--rollback-source", type=Path)
    plan_parser.add_argument("--preview", action="store_true")
    plan_parser.add_argument("--output", required=True, type=Path)

    admit = subparsers.add_parser("admit")
    admit.add_argument("--fleet", required=True)
    admit.add_argument("--action", choices=("deploy", "rollback"), required=True)
    admit.add_argument("--contract-ref", required=True)
    reconcile = subparsers.add_parser("reconcile")
    for target in (admit, reconcile):
        target.add_argument("--plan", required=True, type=Path)
        target.add_argument("--config", required=True, type=Path)
        target.add_argument("--evidence", required=True, type=Path)
        target.add_argument("--receipt-dir", required=True, type=Path)

    execute = subparsers.add_parser("execute")
    execute.add_argument("--plan", required=True, type=Path)
    execute.add_argument("--config", required=True, type=Path)
    execute.add_argument("--evidence", required=True, type=Path)
    execute.add_argument("--receipt-dir", required=True, type=Path)
    validate_refresh = subparsers.add_parser("validate-refresh")
    validate_refresh.add_argument("--plan", required=True, type=Path)
    validate_refresh.add_argument("--config", required=True, type=Path)
    validate_refresh.add_argument("--evidence", required=True, type=Path)
    validate_refresh.add_argument("--receipt-dir", required=True, type=Path)

    args = parser.parse_args()
    try:
        if args.command == "collect-evidence":
            config = _load(args.config)
            _validate_commands(config)
            _write(
                args.output,
                _collect_evidence(
                    config,
                    args.manifest_identity,
                    args.fleet,
                    args.rollback_receipt,
                    args.authorization,
                    preview=args.preview,
                ),
            )
        elif args.command == "plan":
            config = _load(args.config)
            evidence = _load(args.evidence)
            resume_plan = None if args.preview else retained_plan(
                config, evidence, args.fleet, args.action, args.contract_ref
            )
            rollback_source = (
                _load(args.rollback_source)
                if args.rollback_source
                else evidence.get("rollbackSource")
            )
            _write(
                args.output,
                resume_plan
                or build_plan(
                    config,
                    evidence,
                    args.fleet,
                    action=args.action,
                    rollback_source=rollback_source,
                    deployment_contract_ref=args.contract_ref,
                    preview=args.preview,
                ),
            )
        elif args.command == "admit":
            plan = _load(args.plan)
            config = _load(args.config)
            evidence = _load(args.evidence)
            validate_deployment_plan(
                plan,
                config,
                evidence,
                datetime.now(timezone.utc),
                fleet_selector=args.fleet,
                action=args.action,
                deployment_contract_ref=args.contract_ref,
            )
            receipt = _restore_receipts(evidence, plan, args.receipt_dir)
            if receipt is None:
                receipt = admitted_receipt(plan, config, evidence, datetime.now(timezone.utc))
                _persist_directory(args.receipt_dir)(receipt)
        elif args.command == "reconcile":
            plan = _load(args.plan)
            config = _load(args.config)
            evidence = _load(args.evidence)
            existing = sorted(args.receipt_dir.glob("revision-*.json"))
            receipts = [_load(path) for path in existing]
            try:
                validate_receipt_chain(receipts)
            except ValueError as error:
                raise DeploymentError(f"local receipt chain is invalid: {error}") from error
            latest = receipts[-1]
            if any(
                runner.get("state") == "unknown"
                for runner in latest.get("runners", [])
            ):
                reconciled = reconcile_unknown_state(plan, latest, evidence, config)
                _persist_directory(args.receipt_dir, len(existing))(reconciled)
            elif latest.get("outcome") in ("failed", "succeeded"):
                raise DeploymentError(
                    "terminal receipt has no unknown state to reconcile; use protected rollback"
                )
        elif args.command == "validate-refresh":
            plan = _load(args.plan)
            config = _load(args.config)
            evidence = _load(args.evidence)
            existing = sorted(args.receipt_dir.glob("revision-*.json"))
            receipts = [_load(path) for path in existing]
            if not receipts:
                raise DeploymentError("refresh validation requires a retained receipt")
            if any(
                path.name != f"revision-{receipt.get('revision', -1):04d}.json"
                for path, receipt in zip(existing, receipts)
            ):
                raise DeploymentError("local receipt filename differs from its revision")
            try:
                validate_receipt_chain(receipts)
            except ValueError as error:
                raise DeploymentError(f"local receipt chain is invalid: {error}") from error
            _validate_refreshed_fleet(
                plan,
                receipts[-1],
                evidence,
                config,
                datetime.now(timezone.utc),
            )
        else:
            plan = _load(args.plan)
            config = _load(args.config)
            evidence = _load(args.evidence)
            fleet = config["fleets"][plan["fleetSelector"]]
            existing = sorted(args.receipt_dir.glob("revision-*.json"))
            receipts = [_load(path) for path in existing]
            if any(
                path.name != f"revision-{receipt.get('revision', -1):04d}.json"
                for path, receipt in zip(existing, receipts)
            ):
                raise DeploymentError("local receipt filename differs from its revision")
            try:
                validate_receipt_chain(receipts)
            except ValueError as error:
                raise DeploymentError(f"local receipt chain is invalid: {error}") from error
            final_receipt = receipts[-1]
            if final_receipt.get("outcome") != "succeeded":
                final_receipt = execute_plan(
                    plan,
                    config,
                    evidence,
                    ProcessAdapter(config, fleet, evidence, plan),
                    _persist_directory(args.receipt_dir, len(existing)),
                    previous_receipt=final_receipt,
                    max_hosts=1,
                )
            if final_receipt.get("outcome") not in ("in_progress", "succeeded"):
                raise DeploymentError(
                    f"deployment stopped with outcome {final_receipt.get('outcome')}"
                )
    except (DeploymentError, OSError, subprocess.SubprocessError) as error:
        print(f"container deployment rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
