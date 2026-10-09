#!/usr/bin/env python3
"""Validate and expand the reviewed OCI candidate registry destinations."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


class DestinationError(ValueError):
    pass


GHCR_OWNER = re.compile(r"^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*$")
GAR_NAMESPACE = re.compile(
    r"^(?P<location>[a-z]+(?:-[a-z0-9]+)*)-docker\.pkg\.dev/"
    r"(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9])/"
    r"(?P<repository>[a-z][a-z0-9-]{0,62}[a-z0-9])$"
)
WIF_PROVIDER = re.compile(
    r"^projects/[0-9]+/locations/global/workloadIdentityPools/"
    r"[A-Za-z0-9_-]+/providers/[A-Za-z0-9_-]+$"
)
SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$"
)
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TAG = re.compile(
    r"^(?:sha-[0-9a-f]{40}|(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-rc\.[0-9]+\.[0-9]+)?)$"
)
DEFAULT_CANDIDATE_RETENTION_DAYS = 88
DOCKER_ATTESTATION_REFERRER = "application/vnd.docker.attestation.manifest.v1+json"


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DestinationError(f"{field} must be an object")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise DestinationError(f"{field} must be a non-empty string")
    return value


def normalize_destinations(config: dict[str, Any], owner: str) -> list[dict[str, Any]]:
    owner = owner.lower()
    if not GHCR_OWNER.fullmatch(owner):
        raise DestinationError("repository owner is not a valid GHCR namespace")

    primary_namespace = _string(config.get("registryNamespace"), "registryNamespace").rstrip("/")
    expected_primary = f"ghcr.io/{owner}"
    if primary_namespace != expected_primary:
        raise DestinationError("registryNamespace must be the repository owner's GHCR namespace")

    raw_destinations = config.get("registryDestinations")
    if raw_destinations is None:
        raw_destinations = [{"provider": "ghcr", "namespace": primary_namespace}]
    if not isinstance(raw_destinations, list) or not raw_destinations:
        raise DestinationError("registryDestinations must be a non-empty array")

    destinations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_destinations):
        field = f"registryDestinations[{index}]"
        item = _object(raw, field)
        provider = _string(item.get("provider"), f"{field}.provider")
        namespace = _string(item.get("namespace"), f"{field}.namespace").rstrip("/")
        if namespace in seen:
            raise DestinationError("registryDestinations must not contain duplicate namespaces")
        seen.add(namespace)

        if provider == "ghcr":
            if index != 0 or namespace != expected_primary:
                raise DestinationError("GHCR must be the first destination and match registryNamespace")
            if set(item) - {"provider", "namespace", "candidateRetentionDays"}:
                raise DestinationError(f"{field} contains unsupported GHCR settings")
            retention_days = item.get("candidateRetentionDays", DEFAULT_CANDIDATE_RETENTION_DAYS)
            if type(retention_days) is not int or not 1 <= retention_days <= DEFAULT_CANDIDATE_RETENTION_DAYS:
                raise DestinationError(f"{field}.candidateRetentionDays must be 1 through {DEFAULT_CANDIDATE_RETENTION_DAYS}")
            destinations.append({
                "provider": provider,
                "namespace": namespace,
                "registryHost": "ghcr.io",
                "candidateRetentionDays": retention_days,
            })
        elif provider == "gar":
            if GAR_NAMESPACE.fullmatch(namespace) is None:
                raise DestinationError(f"{field}.namespace must be a GAR Docker repository namespace")
            if set(item) != {"provider", "namespace", "workloadIdentityProvider", "serviceAccount", "candidateRetentionDays"}:
                raise DestinationError(f"{field} must define only its namespace, OIDC identity, and candidate retention")
            identity_provider = _string(item.get("workloadIdentityProvider"), f"{field}.workloadIdentityProvider")
            service_account = _string(item.get("serviceAccount"), f"{field}.serviceAccount")
            retention_days = item.get("candidateRetentionDays")
            if WIF_PROVIDER.fullmatch(identity_provider) is None:
                raise DestinationError(f"{field}.workloadIdentityProvider is not a Google WIF provider resource")
            if SERVICE_ACCOUNT.fullmatch(service_account) is None:
                raise DestinationError(f"{field}.serviceAccount is not a Google service-account address")
            if type(retention_days) is not int or not 1 <= retention_days <= DEFAULT_CANDIDATE_RETENTION_DAYS:
                raise DestinationError(f"{field}.candidateRetentionDays must be 1 through {DEFAULT_CANDIDATE_RETENTION_DAYS}")
            if destinations and destinations[0]["provider"] != "ghcr":
                raise DestinationError("GAR destinations require GHCR as the canonical build and provenance source")
            if any(destination["provider"] == "gar" for destination in destinations):
                raise DestinationError("only one GAR destination is currently supported")
            destinations.append({
                "provider": provider,
                "namespace": namespace,
                "registryHost": namespace.split("/", 1)[0],
                "workloadIdentityProvider": identity_provider,
                "serviceAccount": service_account,
                "candidateRetentionDays": retention_days,
            })
        else:
            raise DestinationError(f"{field}.provider is unsupported")

    if destinations[0]["provider"] != "ghcr":
        raise DestinationError("GHCR must remain the canonical build and provenance source")
    return destinations


def expand_image_destinations(
    config: dict[str, Any], owner: str, image: dict[str, Any]
) -> list[dict[str, Any]]:
    destinations = normalize_destinations(config, owner)
    repository = _string(image.get("repository"), "image.repository")
    primary = destinations[0]["namespace"]
    if not repository.startswith(primary + "/"):
        raise DestinationError("image.repository escapes the canonical GHCR namespace")
    suffix = repository[len(primary):]
    if any(part in {"", ".", ".."} for part in suffix[1:].split("/")):
        raise DestinationError("image.repository contains an unsafe path")
    return [
        {**destination, "repository": destination["namespace"] + suffix}
        for destination in destinations
    ]


def _skopeo(arguments: list[str]) -> tuple[int, bytes, bytes]:
    try:
        result = subprocess.run(
            ["skopeo", *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise DestinationError("skopeo is unavailable") from error
    return result.returncode, result.stdout, result.stderr


def _oras(arguments: list[str]) -> tuple[int, bytes, bytes]:
    try:
        result = subprocess.run(
            ["oras", *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise DestinationError("ORAS is unavailable") from error
    return result.returncode, result.stdout, result.stderr


def parse_referrer_inventory(
    payload: bytes, *, repository: str, subject: str = "index"
) -> list[dict[str, str]]:
    try:
        discovery = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DestinationError("registry referrer discovery returned invalid JSON") from error
    if not isinstance(discovery, dict) or not isinstance(discovery.get("referrers"), list):
        raise DestinationError("registry referrer discovery is malformed")

    referrers: list[dict[str, str]] = []
    seen: set[str] = set()
    for offset, raw in enumerate(discovery["referrers"]):
        if not isinstance(raw, dict):
            raise DestinationError(f"registry referrer {offset} is malformed")
        digest = raw.get("digest")
        artifact_type = raw.get("artifactType")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise DestinationError(f"registry referrer {offset} digest is malformed")
        if not isinstance(artifact_type, str) or not artifact_type:
            raise DestinationError(f"registry referrer {offset} artifact type is missing")
        if raw.get("reference") != f"{repository}@{digest}":
            raise DestinationError(f"registry referrer {offset} is not bound to the image repository")
        if digest in seen:
            raise DestinationError("registry referrer inventory contains duplicate digests")
        seen.add(digest)
        referrers.append({"artifactType": artifact_type, "digest": digest})

    required_type = {
        "index": "application/vnd.dev.sigstore.bundle.v0.3+json",
        "platform": "application/spdx+json",
    }.get(subject)
    if required_type is None:
        raise DestinationError("registry referrer subject is unsupported")
    if sum(item["artifactType"] == required_type for item in referrers) != 1:
        raise DestinationError(f"registry {subject} is missing one unique {required_type} referrer")
    return sorted(referrers, key=lambda item: (item["artifactType"], item["digest"]))


def _remote_digest(reference: str, authfile: Path) -> str | None:
    status, output, error = _skopeo([
        "inspect", "--authfile", str(authfile), "--raw", f"docker://{reference}"
    ])
    if status:
        message = error.decode("utf-8", errors="replace").lower()
        if any(marker in message for marker in (
            "unauthorized", "denied", "forbidden", "authentication required",
            "insufficient_scope", "permission denied",
        )) or re.search(
            r"\b(?:http(?:\s+status(?:\s*code)?)?|status(?:\s*code)?)\s*[:=]?\s*(?:401|403)\b",
            message,
        ):
            raise DestinationError("registry observation failed: authorization")
        if any(marker in message for marker in (
            "name_unknown", "name unknown", "repository not found", "unknown repository",
        )):
            raise DestinationError("registry observation failed: repository")
        if any(marker in message for marker in (
            "manifest unknown", "manifest_unknown", "manifest not found", "no such manifest"
        )):
            return None
        raise DestinationError(f"registry observation failed: skopeo exit {status}")
    return "sha256:" + hashlib.sha256(output).hexdigest()


def _referrer_inventory(
    repository: str, digest: str, authfile: Path, subject: str = "index"
) -> list[dict[str, str]]:
    status, output, _ = _oras([
        "discover",
        "--format",
        "json",
        "--depth",
        "1",
        "--registry-config",
        str(authfile),
        f"{repository}@{digest}",
    ])
    if status != 0:
        raise DestinationError("registry referrer discovery failed")
    return parse_referrer_inventory(output, repository=repository, subject=subject)


def _platform_subjects(
    repository: str, digest: str, authfile: Path, reviewed_platforms: Any
) -> dict[str, str]:
    from container_oci_index import OCIIndexError, validate_index

    status, payload, _ = _skopeo([
        "inspect", "--authfile", str(authfile), "--raw", f"docker://{repository}@{digest}"
    ])
    if status or "sha256:" + hashlib.sha256(payload).hexdigest() != digest:
        raise DestinationError("source OCI index differs from the pinned candidate digest")
    try:
        inventory = validate_index(json.loads(payload), reviewed_platforms)
    except (UnicodeDecodeError, json.JSONDecodeError, OCIIndexError) as error:
        raise DestinationError("source OCI platform inventory is invalid") from error
    return {evidence["subjectDigest"]: evidence["digest"] for evidence in inventory["evidence"]}


def mirror_candidate(
    config: dict[str, Any], owner: str, variant: str, provider: str,
    tag: str, digest: str, authfile: Path, published_at: str | None = None,
) -> dict[str, Any]:
    if not TAG.fullmatch(tag) or not DIGEST.fullmatch(digest):
        raise DestinationError("candidate tag or digest is malformed")
    if not authfile.is_absolute() or not authfile.is_file():
        raise DestinationError("registry authfile is unavailable")
    images = config.get("images")
    if not isinstance(images, list):
        raise DestinationError("images must be a non-empty array")
    image = next(
        (item for item in images if isinstance(item, dict) and item.get("variant") == variant),
        None,
    )
    if image is None:
        raise DestinationError("image variant is not configured")
    destinations = expand_image_destinations(config, owner, image)
    source = destinations[0]["repository"]
    destination = next((item for item in destinations if item["provider"] == provider), None)
    if destination is None:
        raise DestinationError("registry provider is not configured for this candidate")
    target = f"{destination['repository']}:{tag}"
    existing = _remote_digest(target, authfile)
    if existing is not None and existing != digest:
        raise DestinationError("destination tag already names a different digest")
    platforms = _platform_subjects(source, digest, authfile, image.get("platforms"))
    source_referrers = _referrer_inventory(source, digest, authfile)
    platform_referrers = {
        platform: _referrer_inventory(source, platform, authfile, "platform")
        for platform in platforms
    }
    if existing is None:
        status, _, _ = _oras([
            "cp", "--recursive",
            "--from-registry-config", str(authfile),
            "--to-registry-config", str(authfile),
            f"{source}@{digest}", target,
        ])
        if status:
            raise DestinationError("registry mirror copy failed")
    observed = _remote_digest(target, authfile)
    if observed != digest:
        raise DestinationError("destination digest differs from the candidate digest")
    destination_referrers = _referrer_inventory(destination["repository"], digest, authfile)
    if destination_referrers != source_referrers:
        raise DestinationError("destination index provenance differs from source evidence")
    platform_evidence_referrers = []
    for platform in sorted(platform_referrers):
        referrers = platform_referrers[platform]
        observed_referrers = _referrer_inventory(destination["repository"], platform, authfile, "platform")
        indexed_attestation = {
            "artifactType": DOCKER_ATTESTATION_REFERRER,
            "digest": platforms[platform],
        }
        with_indexed_attestation = sorted(
            [*referrers, indexed_attestation], key=lambda item: (item["artifactType"], item["digest"])
        )
        if observed_referrers != referrers and observed_referrers != with_indexed_attestation:
            raise DestinationError("destination platform SBOM differs from source evidence")
        platform_evidence_referrers.append({
            "subjectDigest": platform,
            "evidenceReferrers": observed_referrers,
        })
    receipt = {
        "provider": provider,
        "variant": variant,
        "repository": destination["repository"],
        "digest": digest,
        "evidenceReferrers": destination_referrers,
        "platformEvidenceReferrers": platform_evidence_referrers,
    }
    return _with_candidate_expiry(receipt, config, owner, variant, digest, published_at)


def _with_candidate_expiry(
    receipt: dict[str, Any], config: dict[str, Any], owner: str, variant: str,
    digest: str, published_at: str | None,
) -> dict[str, Any]:
    if published_at is None:
        return receipt
    destination = next(
        item for item in manifest_destinations(config, owner, variant, digest, published_at)
        if item["provider"] == receipt["provider"]
    )
    return {
        **receipt,
        "candidateExpiresAt": destination["candidateExpiresAt"],
        "verifiedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def manifest_destinations(
    config: dict[str, Any], owner: str, variant: str, digest: str, published_at: str
) -> list[dict[str, str]]:
    if not DIGEST.fullmatch(digest):
        raise DestinationError("candidate digest is malformed")
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise DestinationError("candidate published-at time is malformed") from error
    if published.tzinfo is None or published.utcoffset() != timedelta(0):
        raise DestinationError("candidate published-at time must be UTC")
    images = config.get("images")
    if not isinstance(images, list):
        raise DestinationError("images must be a non-empty array")
    image = next(
        (item for item in images if isinstance(item, dict) and item.get("variant") == variant),
        None,
    )
    if image is None:
        raise DestinationError("image variant is not configured")
    published = published.astimezone(timezone.utc)
    return [
        {
            "provider": destination["provider"],
            "repository": destination["repository"],
            "digest": digest,
            "candidateExpiresAt": (
                published + timedelta(days=destination["candidateRetentionDays"])
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        for destination in expand_image_destinations(config, owner, image)
    ]


def verify_candidate(
    config: dict[str, Any], owner: str, variant: str, provider: str,
    tag: str, digest: str, authfile: Path, published_at: str | None = None,
) -> dict[str, str]:
    if not TAG.fullmatch(tag) or not DIGEST.fullmatch(digest):
        raise DestinationError("candidate tag or digest is malformed")
    if not authfile.is_absolute() or not authfile.is_file():
        raise DestinationError("registry authfile is unavailable")
    images = config.get("images")
    if not isinstance(images, list):
        raise DestinationError("images must be a non-empty array")
    image = next(
        (item for item in images if isinstance(item, dict) and item.get("variant") == variant),
        None,
    )
    if image is None:
        raise DestinationError("image variant is not configured")
    destination = next(
        (item for item in expand_image_destinations(config, owner, image) if item["provider"] == provider),
        None,
    )
    if destination is None:
        raise DestinationError("registry provider is not configured for this candidate")
    observed = _remote_digest(f"{destination['repository']}:{tag}", authfile)
    if observed != digest:
        raise DestinationError("candidate destination is expired or resolves to a different digest")
    receipt = {"provider": provider, "repository": destination["repository"], "digest": digest}
    return _with_candidate_expiry(receipt, config, owner, variant, digest, published_at)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and expand OCI registry destinations")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--mirror-provider")
    parser.add_argument("--variant")
    parser.add_argument("--tag")
    parser.add_argument("--digest")
    parser.add_argument("--authfile", type=Path)
    parser.add_argument("--published-at")
    parser.add_argument("--verify-provider")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise DestinationError("candidate config must contain an object")
        normalized = normalize_destinations(config, args.owner)
        images = config.get("images")
        if not isinstance(images, list) or not images:
            raise DestinationError("images must be a non-empty array")
        expanded = []
        for image in images:
            expanded.append({
                "variant": _string(_object(image, "image").get("variant"), "image.variant"),
                "destinations": expand_image_destinations(config, args.owner, image),
            })
        if args.mirror_provider is not None:
            if not all((args.variant, args.tag, args.digest, args.authfile)):
                raise DestinationError("mirror mode requires a variant, tag, digest, and authfile")
            receipt = mirror_candidate(
                config, args.owner, args.variant, args.mirror_provider,
                args.tag, args.digest, args.authfile, args.published_at,
            )
            print(json.dumps(receipt, separators=(",", ":")))
            return 0
        if args.verify_provider is not None:
            if not all((args.variant, args.tag, args.digest, args.authfile)):
                raise DestinationError("verification mode requires a variant, tag, digest, and authfile")
            receipt = verify_candidate(
                config, args.owner, args.variant, args.verify_provider,
                args.tag, args.digest, args.authfile, args.published_at,
            )
            print(json.dumps(receipt, separators=(",", ":")))
            return 0
        if args.published_at is not None:
            if not args.variant or not args.digest:
                raise DestinationError("manifest mode requires a variant and digest")
            record = manifest_destinations(
                config, args.owner, args.variant, args.digest, args.published_at
            )
            print(json.dumps(record, separators=(",", ":")))
            return 0
    except (OSError, json.JSONDecodeError, DestinationError) as error:
        print(f"container registry destinations rejected: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"destinations": normalized, "images": expanded}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
