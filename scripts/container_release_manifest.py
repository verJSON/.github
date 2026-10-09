#!/usr/bin/env python3

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from container_registry_destinations import DestinationError, manifest_destinations


class ManifestError(ValueError):
    pass


DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
STABLE_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
CANDIDATE_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)


def _objects(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ManifestError(f"{field} must be a non-empty array")
    if any(not isinstance(item, dict) for item in value):
        raise ManifestError(f"{field} entries must be objects")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{field} must be a non-empty string")
    return value


def _digest(value: Any, field: str) -> str:
    value = _text(value, field)
    if not DIGEST.fullmatch(value):
        raise ManifestError(f"{field} must be a lowercase sha256 digest")
    return value


def _required_referrer_digest(
    value: Any, field: str, artifact_type: str, description: str
) -> str:
    referrers = _objects(value, field)
    normalized = []
    for position, referrer in enumerate(referrers):
        if set(referrer) != {"artifactType", "digest"}:
            raise ManifestError(f"{field}[{position}] fields differ")
        normalized.append((
            _text(referrer.get("artifactType"), f"{field}[{position}].artifactType"),
            _digest(referrer.get("digest"), f"{field}[{position}].digest"),
        ))
    if len(set(normalized)) != len(normalized):
        raise ManifestError(f"{field} contains duplicate referrers")
    matches = [digest for observed_type, digest in normalized if observed_type == artifact_type]
    if len(matches) != 1:
        raise ManifestError(f"{description} must contain one {artifact_type} referrer")
    return matches[0]


def _utc_timestamp(value: Any, field: str) -> datetime:
    text = _text(value, field)
    try:
        timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ManifestError(f"{field} must be an RFC3339 UTC timestamp") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ManifestError(f"{field} must be an RFC3339 UTC timestamp")
    return timestamp


def _platform_identity(platform: dict[str, Any], field: str) -> tuple[str, str, str]:
    variant = platform.get("variant", "")
    if not isinstance(variant, str):
        raise ManifestError(f"{field}.variant must be a string")
    return (
        _text(platform.get("os"), f"{field}.os"),
        _text(platform.get("architecture"), f"{field}.architecture"),
        variant,
    )


def _index_unique(
    values: list[dict[str, Any]], field: str, identity
) -> dict[Any, dict[str, Any]]:
    indexed: dict[Any, dict[str, Any]] = {}
    for offset, value in enumerate(values):
        key = identity(value, f"{field}[{offset}]")
        if key in indexed:
            raise ManifestError(f"{field} contains duplicate identity {key!r}")
        indexed[key] = value
    return indexed


def validate_manifest(manifest: dict[str, Any], config: dict[str, Any]) -> None:
    private_packages = config.get("privateNodePackages", [])
    if not isinstance(private_packages, list) or any(
        not isinstance(name, str)
        or not re.fullmatch(r"@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*", name)
        for name in private_packages
    ):
        raise ManifestError("config.privateNodePackages must contain exact lowercase scoped package names")
    if len(private_packages) != len(set(private_packages)):
        raise ManifestError("config.privateNodePackages contains duplicate package names")
    if config.get("packageManager", "npm") not in ("npm", "pnpm"):
        raise ManifestError("config.packageManager must be npm or pnpm")

    if manifest.get("schemaVersion") != 4:
        raise ManifestError(
            "manifest.schemaVersion must be 4; rebuild candidates published with schema v2 or v3"
        )
    if manifest.get("kind") != "container-candidate":
        raise ManifestError("manifest.kind must be container-candidate")
    if manifest.get("promotionEligible") is not False:
        raise ManifestError(
            "candidate promotion eligibility is disabled until provenance gates are enabled by contract"
        )

    source = manifest.get("source")
    if not isinstance(source, dict):
        raise ManifestError("manifest.source must be an object")
    expected_repository = _text(config.get("repository"), "config.repository")
    source_repository = _text(source.get("repository"), "manifest.source.repository")
    if (
        not source_repository.isascii()
        or not expected_repository.isascii()
        or source_repository.lower() != expected_repository.lower()
    ):
        raise ManifestError("manifest source repository differs from reviewed config")
    for key in ("commit", "ref", "workflow", "runId", "runAttempt", "candidatePublishedAt"):
        _text(source.get(key), f"manifest.source.{key}")
    candidate_published_at = _utc_timestamp(
        source["candidatePublishedAt"], "manifest.source.candidatePublishedAt"
    )
    if source["ref"] != "refs/heads/main":
        raise ManifestError("candidate source ref must be refs/heads/main")
    if not re.fullmatch(r"[0-9a-f]{40}", source["commit"]):
        raise ManifestError("manifest.source.commit must be a 40-hex commit")
    if not source["workflow"].startswith(
        "Verjson/.github/.github/workflows/container-candidate-publish.yml@"
    ):
        raise ManifestError("candidate signer workflow differs from expected publisher")

    next_stable = _text(config.get("nextStableVersion"), "config.nextStableVersion")
    if not STABLE_VERSION.fullmatch(next_stable):
        raise ManifestError("config.nextStableVersion must be stable SemVer")
    candidate = _text(manifest.get("candidateVersion"), "manifest.candidateVersion")
    match = CANDIDATE_VERSION.fullmatch(candidate)
    if not match or candidate != f"{next_stable}-rc.{source['runId']}.{source['runAttempt']}":
        raise ManifestError("candidateVersion is not derived from nextStableVersion and source run")

    expected_images = _index_unique(
        _objects(config.get("images"), "config.images"),
        "config.images",
        lambda image, field: _text(image.get("variant"), f"{field}.variant"),
    )
    actual_images = _index_unique(
        _objects(manifest.get("images"), "manifest.images"),
        "manifest.images",
        lambda image, field: _text(image.get("variant"), f"{field}.variant"),
    )
    if actual_images.keys() != expected_images.keys():
        raise ManifestError("manifest variants differ from reviewed config")

    for variant, expected in expected_images.items():
        actual = actual_images[variant]
        if actual.get("repository") != expected.get("repository"):
            raise ManifestError(f"image repository differs for variant {variant!r}")
        repository = _text(actual.get("repository"), f"manifest.images[{variant!r}].repository")
        namespace = _text(config.get("registryNamespace"), "config.registryNamespace").rstrip("/")
        if not repository.startswith(namespace + "/"):
            raise ManifestError(f"image repository escapes registry namespace for variant {variant!r}")
        index_digest = _digest(
            actual.get("indexDigest"), f"manifest.images[{variant!r}].indexDigest"
        )
        try:
            expected_destinations = manifest_destinations(
                config,
                expected_repository.split("/", 1)[0],
                variant,
                index_digest,
                source["candidatePublishedAt"],
            )
        except DestinationError as error:
            raise ManifestError(f"candidate destination contract is invalid: {error}") from error
        destinations = _objects(
            actual.get("destinations"), f"manifest.images[{variant!r}].destinations"
        )
        if len(destinations) != len(expected_destinations):
            raise ManifestError(f"candidate destination receipts differ for variant {variant!r}")
        gar_receipt: dict[str, Any] | None = None
        gar_index_referrer_digest: str | None = None
        for receipt, expected_destination in zip(destinations, expected_destinations, strict=True):
            expected_fields = {*expected_destination, "verifiedAt"}
            if expected_destination["provider"] == "gar":
                expected_fields.update(("evidenceReferrers", "platformEvidenceReferrers"))
                gar_receipt = receipt
            if set(receipt) != expected_fields:
                raise ManifestError(f"candidate destination receipt fields differ for variant {variant!r}")
            if any(receipt.get(key) != value for key, value in expected_destination.items()):
                raise ManifestError(f"candidate destination digest or expiry differs for variant {variant!r}")
            verified_at = _utc_timestamp(
                receipt.get("verifiedAt"), f"manifest.images[{variant!r}].destinations.verifiedAt"
            )
            expiry = _utc_timestamp(
                receipt.get("candidateExpiresAt"),
                f"manifest.images[{variant!r}].destinations.candidateExpiresAt",
            )
            if verified_at < candidate_published_at or verified_at >= expiry:
                raise ManifestError(
                    f"candidate destination was not verified before expiry for variant {variant!r}"
                )
            if expected_destination["provider"] == "gar":
                gar_index_referrer_digest = _required_referrer_digest(
                    receipt.get("evidenceReferrers"),
                    f"manifest.images[{variant!r}].destinations.evidenceReferrers",
                    "application/vnd.dev.sigstore.bundle.v0.3+json",
                    "GAR index evidence",
                )
        identities = actual.get("identities")
        if not isinstance(identities, dict):
            raise ManifestError(f"identities must be an object for variant {variant!r}")
        commit_identity = f"sha-{source['commit']}"
        if identities != {
            "commit": commit_identity,
            "candidate": candidate,
        }:
            raise ManifestError(f"immutable identities differ for variant {variant!r}")

        expected_provenance = expected.get("provenance")
        actual_provenance = actual.get("provenance")
        if not isinstance(expected_provenance, dict) or not isinstance(actual_provenance, dict):
            raise ManifestError(f"provenance must be an object for variant {variant!r}")
        for key in ("predicateType",):
            if actual_provenance.get(key) != expected_provenance.get(key):
                raise ManifestError(
                    f"provenance {key} differs for variant {variant!r}"
                )
        if actual_provenance.get("builderIdentity") != source["workflow"]:
            raise ManifestError(f"provenance signer workflow differs for variant {variant!r}")
        if actual_provenance.get("builderIdentity") != expected_provenance.get("builderIdentity"):
            raise ManifestError(f"provenance builder identity differs for variant {variant!r}")
        _digest(
            actual_provenance.get("bundleDigest"),
            f"manifest.images[{variant!r}].provenance.bundleDigest",
        )
        provenance_referrer_digest = _digest(
            actual_provenance.get("referrerManifestDigest"),
            f"manifest.images[{variant!r}].provenance.referrerManifestDigest",
        )
        if gar_receipt is not None:
            if provenance_referrer_digest != gar_index_referrer_digest:
                raise ManifestError(
                    f"GAR provenance referrer differs for variant {variant!r}"
                )
        provenance_subject = _digest(
            actual_provenance.get("subjectDigest"),
            f"manifest.images[{variant!r}].provenance.subjectDigest",
        )
        sbom = actual.get("sbom")
        if not isinstance(sbom, dict):
            raise ManifestError(f"sbom must be an object for variant {variant!r}")
        if sbom.get("predicateType") != "https://spdx.dev/Document/v2.3":
            raise ManifestError(f"SBOM predicate differs for variant {variant!r}")
        if provenance_subject != actual["indexDigest"]:
            raise ManifestError(f"provenance names a different subject for variant {variant!r}")

        base_variant = expected.get("baseVariant")
        if base_variant is not None:
            if not isinstance(base_variant, str) or base_variant not in actual_images:
                raise ManifestError(f"unknown base variant for {variant!r}")
            binding = actual.get("base")
            if not isinstance(binding, dict) or binding != {
                "variant": base_variant,
                "digest": actual_images[base_variant].get("indexDigest"),
            }:
                raise ManifestError(f"derived variant {variant!r} is not bound to same-run base digest")

        expected_platforms = _index_unique(
            _objects(expected.get("platforms"), f"config.images[{variant!r}].platforms"),
            f"config.images[{variant!r}].platforms",
            _platform_identity,
        )
        actual_platforms = _index_unique(
            _objects(actual.get("platforms"), f"manifest.images[{variant!r}].platforms"),
            f"manifest.images[{variant!r}].platforms",
            _platform_identity,
        )
        if actual_platforms.keys() != expected_platforms.keys():
            raise ManifestError(
                f"platform matrix differs for variant {variant!r}"
            )
        for identity, platform in actual_platforms.items():
            _digest(platform.get("digest"), f"manifest.images[{variant!r}].platforms[{identity!r}].digest")

        sbom_attestations = _index_unique(
            _objects(
                sbom.get("attestations"),
                f"manifest.images[{variant!r}].sbom.attestations",
            ),
            f"manifest.images[{variant!r}].sbom.attestations",
            _platform_identity,
        )
        if sbom_attestations.keys() != actual_platforms.keys():
            raise ManifestError(
                f"SBOM platform attestations differ for variant {variant!r}"
            )
        gar_platform_referrers = None
        if gar_receipt is not None:
            def platform_evidence_subject(value: dict[str, Any], field: str) -> str:
                if set(value) != {"subjectDigest", "evidenceReferrers"}:
                    raise ManifestError(f"{field} fields differ")
                return _digest(value.get("subjectDigest"), f"{field}.subjectDigest")

            gar_platform_referrers = _index_unique(
                _objects(
                    gar_receipt.get("platformEvidenceReferrers"),
                    f"manifest.images[{variant!r}].destinations.platformEvidenceReferrers",
                ),
                f"manifest.images[{variant!r}].destinations.platformEvidenceReferrers",
                platform_evidence_subject,
            )
            platform_subjects = {
                _digest(platform.get("digest"), f"manifest.images[{variant!r}].platforms.digest")
                for platform in actual_platforms.values()
            }
            if gar_platform_referrers.keys() != platform_subjects:
                raise ManifestError(
                    f"GAR platform evidence subjects differ for variant {variant!r}"
                )
        for identity, attestation in sbom_attestations.items():
            for field in ("bundleDigest", "referrerManifestDigest", "spdxDigest"):
                _digest(
                    attestation.get(field),
                    f"manifest.images[{variant!r}].sbom.attestations[{identity!r}].{field}",
                )
            digest = _digest(
                attestation.get("digest"),
                f"manifest.images[{variant!r}].sbom.attestations[{identity!r}].digest",
            )
            if digest != actual_platforms[identity]["digest"]:
                raise ManifestError(
                    f"SBOM digest differs for variant {variant!r} platform {identity!r}"
                )
            if gar_platform_referrers is not None:
                subject_digest = actual_platforms[identity]["digest"]
                evidence_field = (
                    f"manifest.images[{variant!r}].destinations.platformEvidenceReferrers"
                    f"[{subject_digest}].evidenceReferrers"
                )
                recorded_referrer = _required_referrer_digest(
                    gar_platform_referrers[subject_digest].get("evidenceReferrers"),
                    evidence_field,
                    "application/spdx+json",
                    "GAR platform SBOM evidence",
                )
                sbom_referrer_digest = _digest(
                    attestation.get("referrerManifestDigest"),
                    f"manifest.images[{variant!r}].sbom.attestations[{identity!r}].referrerManifestDigest",
                )
                if recorded_referrer != sbom_referrer_digest:
                    raise ManifestError(
                        f"GAR SBOM referrer differs for variant {variant!r} platform {identity!r}"
                    )


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ManifestError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a release manifest against reviewed consumer identity"
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    try:
        validate_manifest(_load(args.manifest), _load(args.config))
    except ManifestError as error:
        print(f"container release manifest rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
