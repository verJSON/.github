#!/usr/bin/env python3

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from container_release_manifest import validate_manifest


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def _write_json(path: Path, value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    path.write_text(encoded, encoding="utf-8")
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def _one_gar_repository(image: dict[str, Any]) -> str:
    destinations = image.get("destinations")
    if not isinstance(destinations, list):
        raise ValueError("candidate image destinations are malformed")
    repositories = [
        item.get("repository")
        for item in destinations
        if isinstance(item, dict) and item.get("provider") == "gar"
    ]
    if len(repositories) != 1 or not isinstance(repositories[0], str):
        raise ValueError("Cosign consumer verification requires exactly one reviewed GAR destination")
    return repositories[0]


def _identity_arguments(
    source: dict[str, Any],
    *,
    expected_repository: str,
    repository_id: str,
    expected_source_ref: str,
    expected_source_commit: str,
    contract_ref: str,
) -> list[str]:
    if not re.fullmatch(r"[0-9a-f]{40}", contract_ref):
        raise ValueError("release contract revision must be a full commit SHA")
    if source["repository"] != expected_repository:
        raise ValueError("candidate source repository differs from the release repository")
    if source["ref"] != expected_source_ref or source["commit"] != expected_source_commit:
        raise ValueError("candidate source ref or commit differs from the verified workflow run")
    publisher_workflow_ref = (
        "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + contract_ref
    )
    caller_workflow_ref = (
        f"{expected_repository}/.github/workflows/container-candidate.yml@{expected_source_ref}"
    )
    values = (
        ("--expected-source-repository", expected_repository),
        ("--expected-source-repository-id", repository_id),
        ("--expected-source-ref", expected_source_ref),
        ("--expected-source-commit", expected_source_commit),
        ("--expected-caller-workflow-ref", caller_workflow_ref),
        ("--expected-caller-workflow-sha", expected_source_commit),
        ("--expected-publisher-workflow-ref", publisher_workflow_ref),
        ("--expected-contract-sha", contract_ref),
    )
    return [argument for pair in values for argument in pair]


def _verify_helper(
    command: list[str],
    *,
    run: RunCommand,
) -> dict[str, Any]:
    completed = run(command, check=True, text=True, capture_output=True)
    try:
        receipt = json.loads(completed.stdout)
    except (AttributeError, json.JSONDecodeError) as error:
        raise ValueError("Cosign verifier returned an invalid receipt") from error
    if not isinstance(receipt, dict):
        raise ValueError("Cosign verifier receipt must be an object")
    return receipt


def verify(
    candidate: dict[str, Any],
    config: dict[str, Any],
    receipt_directory: Path,
    *,
    expected_repository: str,
    repository_id: str,
    expected_source_ref: str,
    expected_source_commit: str,
    contract_ref: str,
    cosign_helper: Path,
    run: RunCommand = subprocess.run,
) -> dict[str, dict[str, str]]:
    validate_manifest(candidate, config)
    receipt_directory.mkdir(parents=True, exist_ok=True)
    source = candidate["source"]
    receipt_digests: dict[str, dict[str, str]] = {"provenance": {}, "sbom": {}}
    configured_images = {
        image["variant"]: image for image in config["images"] if isinstance(image, dict)
    }

    for image in sorted(candidate["images"], key=lambda value: value["variant"]):
        variant = image["variant"]
        configured = configured_images[variant]
        registry_repository = _one_gar_repository(image)
        publisher_workflow_ref = image["provenance"]["builderIdentity"]
        expected_publisher_workflow_ref = (
            "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + contract_ref
        )
        if publisher_workflow_ref != expected_publisher_workflow_ref:
            raise ValueError("candidate publisher identity differs from the pinned contract")
        identity_args = _identity_arguments(
            source,
            expected_repository=expected_repository,
            repository_id=repository_id,
            expected_source_ref=expected_source_ref,
            expected_source_commit=expected_source_commit,
            contract_ref=contract_ref,
        )
        evidence_directory = receipt_directory / variant
        evidence_directory.mkdir(parents=True, exist_ok=True)
        buildkit_path = evidence_directory / "buildkit-provenance.json"
        reviewed_path = evidence_directory / "reviewed-platforms.json"
        registry_reference = f"{registry_repository}@{image['indexDigest']}"
        registry_digest_result = run(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                registry_reference,
                "--format",
                "{{json .Manifest.Digest}}",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        try:
            registry_digest = json.loads(registry_digest_result.stdout)
        except (AttributeError, json.JSONDecodeError) as error:
            raise ValueError("GAR image digest readback is invalid JSON") from error
        if registry_digest != image["indexDigest"]:
            raise ValueError("GAR image digest differs from the candidate source digest")
        buildkit = run(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                registry_reference,
                "--format",
                "{{json .Provenance}}",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        try:
            buildkit_value = json.loads(buildkit.stdout)
        except (AttributeError, json.JSONDecodeError) as error:
            raise ValueError("GAR BuildKit provenance is invalid JSON") from error
        buildkit_path.write_text(
            json.dumps(buildkit_value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        reviewed_path.write_text(
            json.dumps(configured["platforms"], sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        provenance = image["provenance"]
        provenance_receipt = _verify_helper(
            [
                sys.executable,
                str(cosign_helper),
                "verify-image",
                "--buildkit-provenance",
                str(buildkit_path),
                "--reviewed-platforms",
                str(reviewed_path),
                "--image-repository",
                image["repository"],
                "--registry-repository",
                registry_repository,
                "--image-digest",
                image["indexDigest"],
                "--expected-bundle-digest",
                provenance["bundleDigest"],
                "--expected-referrer-digest",
                provenance["referrerManifestDigest"],
                *identity_args,
            ],
            run=run,
        )
        provenance_receipt["registryImageDigest"] = registry_digest
        receipt_digests["provenance"][variant] = _write_json(
            receipt_directory / f"{variant}-provenance.json",
            provenance_receipt,
        )

        sbom_receipts = []
        for attestation in sorted(
            image["sbom"]["attestations"],
            key=lambda value: (
                value["os"],
                value["architecture"],
                value.get("variant", ""),
            ),
        ):
            sbom_receipts.append(
                _verify_helper(
                    [
                        sys.executable,
                        str(cosign_helper),
                        "verify-sbom",
                        "--registry-repository",
                        registry_repository,
                        "--image-digest",
                        attestation["digest"],
                        "--expected-spdx-digest",
                        attestation["spdxDigest"],
                        "--expected-bundle-digest",
                        attestation["bundleDigest"],
                        "--expected-referrer-digest",
                        attestation["referrerManifestDigest"],
                        *identity_args,
                    ],
                    run=run,
                )
            )
        receipt_digests["sbom"][variant] = _write_json(
            receipt_directory / f"{variant}-sbom.json",
            sbom_receipts,
        )

    return receipt_digests


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--expected-source-ref", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--contract-ref", required=True)
    parser.add_argument("--cosign-helper", type=Path, required=True)
    args = parser.parse_args(argv)

    state = _load(args.state)
    state.update(
        verify(
            _load(args.candidate),
            _load(args.config),
            args.receipts,
            expected_repository=args.expected_repository,
            repository_id=args.repository_id,
            expected_source_ref=args.expected_source_ref,
            expected_source_commit=args.expected_source_commit,
            contract_ref=args.contract_ref,
            cosign_helper=args.cosign_helper,
        )
    )
    temporary = args.state.with_suffix(args.state.suffix + ".next")
    _write_json(temporary, state)
    temporary.replace(args.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
