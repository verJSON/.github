#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path


OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
IN_TOTO = "application/vnd.in-toto+json"
ATTESTATION_TYPE = "attestation-manifest"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_ATTESTATION_PREDICATE_BYTES = 16 * 1024 * 1024


class OCIIndexError(ValueError):
    pass


def _object(value, name):
    if not isinstance(value, dict):
        raise OCIIndexError(f"{name} must be an object")
    return value


def _array(value, name):
    if not isinstance(value, list):
        raise OCIIndexError(f"{name} must be an array")
    return value


def _digest(value, name):
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise OCIIndexError(f"{name} must be a sha256 digest")
    return value


def _platform(value, name):
    platform = _object(value, name)
    os_name = platform.get("os")
    architecture = platform.get("architecture")
    variant = platform.get("variant", "")
    if not isinstance(os_name, str) or not os_name:
        raise OCIIndexError(f"{name}.os must be a non-empty string")
    if not isinstance(architecture, str) or not architecture:
        raise OCIIndexError(f"{name}.architecture must be a non-empty string")
    if not isinstance(variant, str):
        raise OCIIndexError(f"{name}.variant must be a string")
    return os_name, architecture, variant


def _platform_record(identity, digest):
    os_name, architecture, variant = identity
    record = {"os": os_name, "architecture": architecture, "digest": digest}
    if variant:
        record["variant"] = variant
    return record


def validate_index(index, reviewed_platforms):
    index = _object(index, "index")
    if index.get("schemaVersion") != 2 or index.get("mediaType") != OCI_INDEX:
        raise OCIIndexError("index must be an OCI image index with schemaVersion 2")

    reviewed = {}
    for position, platform in enumerate(_array(reviewed_platforms, "reviewed platforms")):
        identity = _platform(platform, f"reviewed platforms[{position}]")
        if identity[0] == "unknown" or identity[1] == "unknown":
            raise OCIIndexError("reviewed platforms cannot use unknown coordinates")
        if identity in reviewed:
            raise OCIIndexError(f"duplicate reviewed platform: {'/'.join(filter(None, identity))}")
        reviewed[identity] = None
    if not reviewed:
        raise OCIIndexError("reviewed platforms must not be empty")

    deployable = {}
    evidence_by_subject = {}
    descriptor_digests = set()
    manifests = _array(index.get("manifests"), "index.manifests")
    for position, descriptor in enumerate(manifests):
        descriptor = _object(descriptor, f"index.manifests[{position}]")
        if descriptor.get("mediaType") != OCI_MANIFEST:
            raise OCIIndexError(f"index.manifests[{position}] has an unsupported media type")
        digest = _digest(descriptor.get("digest"), f"index.manifests[{position}].digest")
        if digest in descriptor_digests:
            raise OCIIndexError(f"duplicate descriptor digest: {digest}")
        descriptor_digests.add(digest)
        identity = _platform(descriptor.get("platform"), f"index.manifests[{position}].platform")

        if identity[0] == "unknown" and identity[1] == "unknown" and not identity[2]:
            annotations = _object(
                descriptor.get("annotations"), f"index.manifests[{position}].annotations"
            )
            if annotations.get("vnd.docker.reference.type") != ATTESTATION_TYPE:
                raise OCIIndexError("unknown/unknown descriptor is not an attestation manifest")
            subject = _digest(
                annotations.get("vnd.docker.reference.digest"),
                f"index.manifests[{position}] subject",
            )
            if subject in evidence_by_subject:
                raise OCIIndexError(f"duplicate attestation evidence for subject: {subject}")
            evidence_by_subject[subject] = digest
            continue

        if identity[0] == "unknown" or identity[1] == "unknown":
            raise OCIIndexError("partially unknown platform descriptor is not deployable evidence")
        annotations = descriptor.get("annotations")
        if annotations is not None and not isinstance(annotations, dict):
            raise OCIIndexError(f"index.manifests[{position}].annotations must be an object")
        if isinstance(annotations, dict) and annotations.get("vnd.docker.reference.type") == ATTESTATION_TYPE:
            raise OCIIndexError("attestation descriptor must use unknown/unknown coordinates")
        if identity in deployable:
            raise OCIIndexError(f"duplicate deployable platform: {'/'.join(filter(None, identity))}")
        deployable[identity] = digest

    expected = set(reviewed)
    observed = set(deployable)
    if observed != expected:
        missing = sorted(expected - observed)
        unexpected = sorted(observed - expected)
        raise OCIIndexError(
            f"deployable platforms differ from review: missing={missing!r}, unexpected={unexpected!r}"
        )

    platform_digests = set(deployable.values())
    evidence_subjects = set(evidence_by_subject)
    if evidence_subjects != platform_digests:
        missing = sorted(platform_digests - evidence_subjects)
        unexpected = sorted(evidence_subjects - platform_digests)
        raise OCIIndexError(
            f"attestation evidence differs from platform subjects: missing={missing!r}, unexpected={unexpected!r}"
        )

    platforms = []
    evidence = []
    for identity in sorted(deployable):
        subject = deployable[identity]
        platforms.append(_platform_record(identity, subject))
        evidence_record = _platform_record(identity, evidence_by_subject[subject])
        evidence_record["subjectDigest"] = subject
        evidence.append(evidence_record)
    return {"platforms": platforms, "evidence": evidence}


def validate_spdx_evidence(manifest):
    manifest = _object(manifest, "evidence manifest")
    if manifest.get("schemaVersion") != 2 or manifest.get("mediaType") != OCI_MANIFEST:
        raise OCIIndexError("evidence must be an OCI image manifest with schemaVersion 2")
    layers = _array(manifest.get("layers"), "evidence manifest.layers")
    spdx_layers = []
    for position, layer in enumerate(layers):
        layer = _object(layer, f"evidence manifest.layers[{position}]")
        if layer.get("mediaType") != IN_TOTO:
            continue
        annotations = layer.get("annotations")
        if not isinstance(annotations, dict):
            continue
        if annotations.get("in-toto.io/predicate-type") == "https://spdx.dev/Document":
            spdx_layers.append(
                _digest(layer.get("digest"), f"evidence manifest.layers[{position}].digest")
            )
    if len(spdx_layers) != 1:
        raise OCIIndexError(f"evidence must contain exactly one SPDX layer; found {len(spdx_layers)}")
    return {"spdxLayerDigest": spdx_layers[0]}


def compact_spdx_document(sbom_index, platform, inventory):
    sbom_index = _object(sbom_index, "SBOM index")
    if not isinstance(platform, str) or not platform:
        raise OCIIndexError("platform must be a non-empty string")
    inventory = _object(inventory, "validated OCI inventory")
    platforms = _array(inventory.get("platforms"), "validated OCI inventory.platforms")
    platform_keys = []
    for position, value in enumerate(platforms):
        os_name, architecture, variant = _platform(
            value, f"validated OCI inventory.platforms[{position}]"
        )
        platform_keys.append(f"{os_name}/{architecture}{f'/{variant}' if variant else ''}")
    if len(set(platform_keys)) != len(platform_keys):
        raise OCIIndexError("validated OCI inventory has duplicate platforms")
    if platform not in platform_keys:
        raise OCIIndexError(f"platform is absent from validated OCI inventory: {platform}")
    if platform in sbom_index:
        entry = _object(sbom_index[platform], f"SBOM index[{platform!r}]")
    elif len(platform_keys) == 1 and set(sbom_index) == {"SPDX"}:
        entry = sbom_index
    else:
        raise OCIIndexError(f"SBOM index has no exact platform entry: {platform}")
    document = _object(entry.get("SPDX"), f"SBOM index[{platform!r}].SPDX")
    if document.get("spdxVersion") != "SPDX-2.3" or document.get("SPDXID") != "SPDXRef-DOCUMENT":
        raise OCIIndexError("platform SBOM must be an SPDX 2.3 document")
    try:
        compact = json.dumps(
            document, allow_nan=False, ensure_ascii=False, separators=(",", ":")
        ) + "\n"
    except ValueError as error:
        raise OCIIndexError(f"platform SBOM is not strict JSON: {error}") from error
    size = len(compact.encode("utf-8"))
    if size > MAX_ATTESTATION_PREDICATE_BYTES:
        raise OCIIndexError(
            "compact platform SBOM exceeds GitHub's "
            f"{MAX_ATTESTATION_PREDICATE_BYTES}-byte predicate limit: {size}"
        )
    return compact


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OCIIndexError(f"cannot read JSON from {path}: {error}") from error


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate BuildKit OCI platform and evidence topology")
    subparsers = parser.add_subparsers(dest="command", required=True)
    index_parser = subparsers.add_parser("index")
    index_parser.add_argument("--index", required=True)
    index_parser.add_argument("--reviewed-platforms", required=True)
    evidence_parser = subparsers.add_parser("spdx-evidence")
    evidence_parser.add_argument("--manifest", required=True)
    document_parser = subparsers.add_parser("spdx-document")
    document_parser.add_argument("--sbom-index", required=True)
    document_parser.add_argument("--platform", required=True)
    document_parser.add_argument("--inventory", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "index":
            result = validate_index(_read_json(args.index), _read_json(args.reviewed_platforms))
        elif args.command == "spdx-evidence":
            result = validate_spdx_evidence(_read_json(args.manifest))
        else:
            sys.stdout.write(compact_spdx_document(
                _read_json(args.sbom_index), args.platform, _read_json(args.inventory)
            ))
            return 0
    except OCIIndexError as error:
        parser.error(str(error))
    json.dump(result, sys.stdout, separators=(",", ":"), sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
