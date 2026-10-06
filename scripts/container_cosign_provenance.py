#!/usr/bin/env python3

import argparse
import base64
import binascii
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class CosignEvidenceError(ValueError):
    pass


PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
OIDC_ISSUER = "https://token.actions.githubusercontent.com"


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "container_candidate_retry", ROOT / "container_candidate_retry.py"
)
assert SPEC and SPEC.loader
RETRY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RETRY)


def cosign_attest_blob_command(
    *, statement: Path, identity_token: Path, bundle: Path, digest: str
) -> list[str]:
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise CosignEvidenceError("image digest must be a lowercase sha256 digest")
    return [
        "cosign",
        "attest-blob",
        "--yes",
        "--statement",
        str(statement),
        "--hash",
        digest,
        "--identity-token",
        str(identity_token),
        "--bundle",
        str(bundle),
        "--type",
        PREDICATE_TYPE,
    ]


def cosign_verify_attestation_command(
    *, identity: str, bundle: Path, digest: str, identity_claims: dict[str, Any] | None = None
) -> list[str]:
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise CosignEvidenceError("image digest must be a lowercase sha256 digest")
    if not identity.startswith("https://github.com/"):
        raise CosignEvidenceError("publisher certificate identity must be a GitHub workflow URI")
    command = [
        "cosign",
        "verify-blob-attestation",
        "--bundle",
        str(bundle),
        "--digest",
        digest,
        "--digestAlg",
        "sha256",
        "--type",
        PREDICATE_TYPE,
        "--certificate-identity",
        identity,
        "--certificate-oidc-issuer",
        OIDC_ISSUER,
    ]
    if identity_claims is not None:
        command.extend(
            [
                "--certificate-github-workflow-repository",
                str(identity_claims["repository"]),
                "--certificate-github-workflow-ref",
                str(identity_claims["ref"]),
                "--certificate-github-workflow-sha",
                str(identity_claims["sha"]),
            ]
        )
    return command


def cosign_sign_blob_command(
    *, blob: Path, identity_token: Path, bundle: Path
) -> list[str]:
    return [
        "cosign",
        "sign-blob",
        "--yes",
        "--bundle",
        str(bundle),
        "--identity-token",
        str(identity_token),
        str(blob),
    ]


def cosign_verify_blob_command(
    *,
    blob: Path,
    bundle: Path,
    identity: str,
    identity_claims: dict[str, Any] | None = None,
) -> list[str]:
    if not identity.startswith("https://github.com/"):
        raise CosignEvidenceError("publisher certificate identity must be a GitHub workflow URI")
    command = [
        "cosign",
        "verify-blob",
        "--bundle",
        str(bundle),
        "--certificate-identity",
        identity,
        "--certificate-oidc-issuer",
        OIDC_ISSUER,
        str(blob),
    ]
    if identity_claims is not None:
        command.extend(
            [
                "--certificate-github-workflow-repository",
                str(identity_claims["repository"]),
                "--certificate-github-workflow-ref",
                str(identity_claims["ref"]),
                "--certificate-github-workflow-sha",
                str(identity_claims["sha"]),
            ]
        )
    return command


def sign_and_attach_statement(
    statement: dict[str, Any],
    identity_claims: dict[str, Any],
    identity_token: Path,
    bundle: Path,
    referrer_manifest: Path,
    *,
    repository: str,
    digest: str,
) -> dict[str, str]:
    image_digest = digest.removeprefix("sha256:")
    publisher_ref = identity_claims.get("job_workflow_ref")
    if not isinstance(publisher_ref, str) or not publisher_ref:
        raise CosignEvidenceError("GitHub OIDC publisher workflow claim is missing")
    publisher_identity = f"https://github.com/{publisher_ref}"
    statement_path = bundle.with_suffix(".statement.json")
    statement_path.write_text(
        json.dumps(statement, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    try:
        subprocess.run(
            cosign_attest_blob_command(
                statement=statement_path,
                identity_token=identity_token,
                bundle=bundle,
                digest=image_digest,
            ),
            check=True,
        )
        subprocess.run(
            cosign_verify_attestation_command(
                identity=publisher_identity,
                bundle=bundle,
                digest=image_digest,
                identity_claims=identity_claims,
            ),
            check=True,
        )
        signed_statement = statement_from_sigstore_bundle(_load_json(bundle))
        if signed_statement != statement:
            raise CosignEvidenceError("Cosign bundle statement differs from controlled provenance")
        artifact_type = "application/vnd.dev.sigstore.bundle.v0.3+json"
        subprocess.run(
            [
                "oras",
                "attach",
                "--artifact-type",
                artifact_type,
                "--export-manifest",
                str(referrer_manifest),
                f"{repository}@sha256:{image_digest}",
                f"{bundle}:{artifact_type}",
            ],
            check=True,
        )
    finally:
        statement_path.unlink(missing_ok=True)

    return {
        "predicateType": PREDICATE_TYPE,
        "bundleDigest": "sha256:" + hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "referrerManifestDigest": "sha256:" + hashlib.sha256(referrer_manifest.read_bytes()).hexdigest(),
        "publisherIdentity": publisher_identity,
    }


def sign_and_verify_blob(
    blob: Path, identity_token: Path, bundle: Path, identity_claims: dict[str, Any]
) -> dict[str, str]:
    publisher_ref = identity_claims.get("job_workflow_ref")
    if not isinstance(publisher_ref, str) or not publisher_ref:
        raise CosignEvidenceError("GitHub OIDC publisher workflow claim is missing")
    publisher_identity = f"https://github.com/{publisher_ref}"
    subprocess.run(
        cosign_sign_blob_command(
            blob=blob,
            identity_token=identity_token,
            bundle=bundle,
        ),
        check=True,
    )
    subprocess.run(
        cosign_verify_blob_command(
            blob=blob,
            bundle=bundle,
            identity=publisher_identity,
        ),
        check=True,
    )
    return {
        "blobDigest": "sha256:" + hashlib.sha256(blob.read_bytes()).hexdigest(),
        "bundleDigest": "sha256:" + hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "publisherIdentity": publisher_identity,
    }


def sign_and_attach_sbom(
    *,
    sbom: Path,
    identity_token: Path,
    bundle: Path,
    referrer_manifest: Path,
    repository: str,
    digest: str,
) -> dict[str, str]:
    receipt = sign_and_verify_blob(
        blob=sbom,
        identity_token=identity_token,
        bundle=bundle,
        identity_claims=decode_github_oidc_claims(
            identity_token.read_text(encoding="utf-8").strip()
        ),
    )
    image_digest = digest.removeprefix("sha256:")
    if len(image_digest) != 64 or any(character not in "0123456789abcdef" for character in image_digest):
        raise CosignEvidenceError("image digest must be a lowercase sha256 digest")
    subprocess.run(
        [
            "oras",
            "attach",
            "--artifact-type",
            "application/spdx+json",
            "--export-manifest",
            str(referrer_manifest),
            f"{repository}@sha256:{image_digest}",
            f"{sbom}:application/spdx+json",
            f"{bundle}:application/vnd.dev.sigstore.bundle.v0.3+json",
        ],
        check=True,
    )
    return {
        **receipt,
        "referrerManifestDigest": "sha256:" + hashlib.sha256(referrer_manifest.read_bytes()).hexdigest(),
        "spdxDigest": receipt["blobDigest"],
    }


def verify_registry_image_provenance(
    *,
    repository: str,
    registry_repository: str | None = None,
    digest: str,
    buildkit_provenance: Any,
    reviewed_platforms: Any,
    identity_token: Path | None = None,
    expected_identity_claims: dict[str, str] | None = None,
    expected_bundle_digest: str | None = None,
    expected_referrer_digest: str | None = None,
    base_repository: str | None = None,
    base_digest: str | None = None,
) -> dict[str, str]:
    token_claims = (
        decode_github_oidc_claims(identity_token.read_text(encoding="utf-8").strip())
        if identity_token is not None
        else None
    )
    if expected_identity_claims is None:
        if token_claims is None:
            raise CosignEvidenceError("verification requires an independent identity policy")
        claims = token_claims
    else:
        required_claims = {
            "repository",
            "repository_id",
            "ref",
            "sha",
            "workflow_ref",
            "workflow_sha",
            "job_workflow_ref",
            "job_workflow_sha",
        }
        if set(expected_identity_claims) != required_claims:
            raise CosignEvidenceError("verification identity policy is incomplete or ambiguous")
        if token_claims is not None and any(
            token_claims.get(claim) != value
            for claim, value in expected_identity_claims.items()
        ):
            raise CosignEvidenceError("current workflow identity differs from independent policy")
        claims = expected_identity_claims
    subject_digest = digest.removeprefix("sha256:")
    registry_repository = registry_repository or repository
    reference = f"{registry_repository}@sha256:{subject_digest}"
    discovery_result = subprocess.run(
        ["oras", "discover", "--format", "json", "--depth", "1", reference],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        discovery = json.loads(discovery_result.stdout)
    except json.JSONDecodeError as error:
        raise CosignEvidenceError("ORAS referrer discovery returned invalid JSON") from error
    referrer = select_sigstore_bundle_referrer(discovery, repository=registry_repository)
    referrer_digest = referrer["digest"]
    if expected_referrer_digest and referrer_digest != expected_referrer_digest:
        raise CosignEvidenceError("registry referrer manifest digest differs from recorded evidence")

    with tempfile.TemporaryDirectory(prefix="cosign-referrer-") as directory:
        subprocess.run(
            ["oras", "pull", "--output", directory, referrer["reference"]],
            check=True,
        )
        bundles = list(Path(directory).rglob("*.sigstore.json"))
        if len(bundles) != 1:
            raise CosignEvidenceError("registry referrer does not contain exactly one Sigstore bundle")
        bundle = bundles[0]
        bundle_digest = "sha256:" + hashlib.sha256(bundle.read_bytes()).hexdigest()
        if expected_bundle_digest and bundle_digest != expected_bundle_digest:
            raise CosignEvidenceError("registry Sigstore bundle digest differs from recorded evidence")
        statement = statement_from_sigstore_bundle(_load_json(bundle))
        caller_workflow_ref = str(claims.get("workflow_ref", ""))
        caller_workflow_sha = str(claims.get("workflow_sha", ""))
        source_repository_id = str(claims.get("repository_id", ""))
        publisher_workflow_ref = str(claims.get("job_workflow_ref", ""))
        contract_sha = str(claims.get("job_workflow_sha", ""))
        RETRY.validate_cosign_provenance(
            statement,
            buildkit_provenance=buildkit_provenance,
            reviewed_platforms=reviewed_platforms,
            repository=repository,
            digest="sha256:" + subject_digest,
            source_repository=str(claims.get("repository", "")),
            source_repository_id=source_repository_id,
            source_ref=str(claims.get("ref", "")),
            source_commit=str(claims.get("sha", "")),
            caller_workflow_ref=caller_workflow_ref,
            caller_workflow_sha=caller_workflow_sha,
            publisher_workflow_ref=publisher_workflow_ref,
            contract_sha=contract_sha,
            base_repository=base_repository,
            base_digest=base_digest,
        )
        subprocess.run(
            cosign_verify_attestation_command(
                identity=f"https://github.com/{publisher_workflow_ref}",
                bundle=bundle,
                digest=subject_digest,
                identity_claims=claims,
            ),
            check=True,
        )
    return {
        "bundleDigest": bundle_digest,
        "referrerManifestDigest": referrer_digest,
        "publisherIdentity": f"https://github.com/{publisher_workflow_ref}",
    }


def decode_github_oidc_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        raise CosignEvidenceError("GitHub OIDC token is malformed")
    payload = parts[1]
    try:
        encoded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CosignEvidenceError("GitHub OIDC token payload is malformed") from error
    if not isinstance(claims, dict):
        raise CosignEvidenceError("GitHub OIDC token claims must be an object")
    return claims


class NoOidcRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise CosignEvidenceError("GitHub OIDC request endpoint redirected")


def write_github_oidc_token(
    output: Path, request_url: str, request_token: str
) -> None:
    parsed = urllib.parse.urlsplit(request_url)
    host = parsed.hostname or ""
    github_request_host = host == "token.actions.githubusercontent.com" or bool(
        re.fullmatch(
            r"(?:pipelines[a-z0-9-]*|run-actions-[0-9]+-azure-[a-z0-9]+(?:-[a-z0-9]+)*)\.actions\.githubusercontent\.com",
            host,
        )
    )
    if (
        parsed.scheme != "https"
        or parsed.netloc != host
        or parsed.fragment
        or not github_request_host
    ):
        raise CosignEvidenceError("GitHub OIDC request endpoint is invalid")
    if not request_token:
        raise CosignEvidenceError("GitHub OIDC request credential is missing")

    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if any(name == "audience" for name, _ in query):
        raise CosignEvidenceError("GitHub OIDC request endpoint already sets an audience")
    query.append(("audience", "sigstore"))
    endpoint = urllib.parse.urlunsplit(
        parsed._replace(query=urllib.parse.urlencode(query))
    )
    request = urllib.request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {request_token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoOidcRedirectHandler()).open(
            request, timeout=30
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CosignEvidenceError("GitHub OIDC token request failed") from error
    token = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(token, str) or len(token.split(".")) != 3:
        raise CosignEvidenceError("GitHub OIDC endpoint returned an invalid token")

    file_descriptor = None
    try:
        file_descriptor = os.open(
            output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as token_file:
            file_descriptor = None
            token_file.write(token)
    except OSError as error:
        raise CosignEvidenceError("cannot securely write GitHub OIDC token") from error
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)


def statement_from_sigstore_bundle(bundle: Any) -> dict[str, Any]:
    if not isinstance(bundle, dict) or bundle.get("mediaType") != (
        "application/vnd.dev.sigstore.bundle.v0.3+json"
    ):
        raise CosignEvidenceError("Sigstore bundle format is unsupported")
    envelope = bundle.get("dsseEnvelope")
    if not isinstance(envelope, dict) or envelope.get("payloadType") != (
        "application/vnd.in-toto+json"
    ):
        raise CosignEvidenceError("Sigstore bundle does not contain an in-toto statement")
    payload = envelope.get("payload")
    if not isinstance(payload, str) or not payload:
        raise CosignEvidenceError("Sigstore bundle statement payload is missing")
    try:
        statement = json.loads(base64.b64decode(payload, validate=True).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CosignEvidenceError("Sigstore bundle statement payload is malformed") from error
    if not isinstance(statement, dict):
        raise CosignEvidenceError("Sigstore bundle statement must be an object")
    return statement


def select_sigstore_bundle_referrer(
    discovery: Any, *, repository: str
) -> dict[str, Any]:
    if not isinstance(discovery, dict) or not isinstance(
        discovery.get("referrers"), list
    ):
        raise CosignEvidenceError("ORAS referrer discovery is malformed")
    bundle_type = "application/vnd.dev.sigstore.bundle.v0.3+json"
    matches = [
        item
        for item in discovery["referrers"]
        if isinstance(item, dict) and item.get("artifactType") == bundle_type
    ]
    if len(matches) != 1:
        raise CosignEvidenceError(
            "expected exactly one direct Sigstore bundle referrer"
        )
    referrer = matches[0]
    digest = referrer.get("digest")
    if not isinstance(digest, str) or not RETRY.DIGEST.fullmatch(digest):
        raise CosignEvidenceError("Sigstore referrer digest is malformed")
    if referrer.get("reference") != f"{repository}@{digest}":
        raise CosignEvidenceError("Sigstore referrer points outside the image repository")
    return referrer


def verify_registry_sbom(
    *,
    registry_repository: str,
    image_digest: str,
    expected_spdx_digest: str,
    expected_bundle_digest: str,
    expected_referrer_digest: str,
    publisher_workflow_ref: str,
    identity_claims: dict[str, str],
) -> dict[str, str]:
    for label, value in (
        ("image", image_digest),
        ("SPDX", expected_spdx_digest),
        ("Cosign bundle", expected_bundle_digest),
        ("SBOM referrer", expected_referrer_digest),
    ):
        if not isinstance(value, str) or not RETRY.DIGEST.fullmatch(value):
            raise CosignEvidenceError(f"{label} digest is malformed")
    discovery_result = subprocess.run(
        [
            "oras",
            "discover",
            "--format",
            "json",
            "--depth",
            "1",
            f"{registry_repository}@{image_digest}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        discovery = json.loads(discovery_result.stdout)
    except json.JSONDecodeError as error:
        raise CosignEvidenceError("ORAS SBOM discovery returned invalid JSON") from error
    if not isinstance(discovery, dict) or not isinstance(discovery.get("referrers"), list):
        raise CosignEvidenceError("ORAS SBOM discovery is malformed")
    matches = [
        item
        for item in discovery["referrers"]
        if isinstance(item, dict)
        and item.get("artifactType") == "application/spdx+json"
        and item.get("digest") == expected_referrer_digest
        and item.get("reference") == f"{registry_repository}@{expected_referrer_digest}"
    ]
    if len(matches) != 1:
        raise CosignEvidenceError("expected exactly one recorded SPDX referrer from the registry")

    with tempfile.TemporaryDirectory(prefix="cosign-sbom-referrer-") as directory:
        subprocess.run(
            ["oras", "pull", "--output", directory, matches[0]["reference"]],
            check=True,
        )
        spdx_files = list(Path(directory).rglob("*.spdx.json"))
        bundles = list(Path(directory).rglob("*.sigstore.json"))
        if len(spdx_files) != 1 or len(bundles) != 1:
            raise CosignEvidenceError("SPDX referrer must contain exactly one document and bundle")
        spdx, bundle = spdx_files[0], bundles[0]
        spdx_digest = "sha256:" + hashlib.sha256(spdx.read_bytes()).hexdigest()
        bundle_digest = "sha256:" + hashlib.sha256(bundle.read_bytes()).hexdigest()
        if spdx_digest != expected_spdx_digest:
            raise CosignEvidenceError("registry SPDX document digest differs from recorded evidence")
        if bundle_digest != expected_bundle_digest:
            raise CosignEvidenceError("registry SBOM bundle digest differs from recorded evidence")
        subprocess.run(
            cosign_verify_blob_command(
                blob=spdx,
                bundle=bundle,
                identity=f"https://github.com/{publisher_workflow_ref}",
                identity_claims=identity_claims,
            ),
            check=True,
        )
    return {
        "spdxDigest": spdx_digest,
        "bundleDigest": bundle_digest,
        "referrerManifestDigest": expected_referrer_digest,
    }


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CosignEvidenceError(f"cannot read {path}: {error}") from error


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build signed provenance from verified BuildKit and GitHub OIDC evidence"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    token_parser = commands.add_parser("request-oidc-token")
    token_parser.add_argument("--request-url", required=True)
    token_parser.add_argument("--output", required=True, type=Path)
    attest_parser = commands.add_parser("attest-image")
    attest_parser.add_argument("--identity-token", required=True, type=Path)
    attest_parser.add_argument("--buildkit-provenance", required=True, type=Path)
    attest_parser.add_argument("--reviewed-platforms", required=True, type=Path)
    attest_parser.add_argument("--image-repository", required=True)
    attest_parser.add_argument("--image-digest", required=True)
    attest_parser.add_argument("--base-repository")
    attest_parser.add_argument("--base-digest")
    attest_parser.add_argument("--bundle", required=True, type=Path)
    attest_parser.add_argument("--referrer-manifest", required=True, type=Path)
    verify_parser = commands.add_parser("verify-image")
    verify_parser.add_argument("--identity-token", type=Path)
    verify_parser.add_argument("--buildkit-provenance", required=True, type=Path)
    verify_parser.add_argument("--reviewed-platforms", required=True, type=Path)
    verify_parser.add_argument("--image-repository", required=True)
    verify_parser.add_argument("--registry-repository")
    verify_parser.add_argument("--image-digest", required=True)
    verify_parser.add_argument("--expected-bundle-digest")
    verify_parser.add_argument("--expected-referrer-digest")
    verify_parser.add_argument("--expected-source-repository", required=True)
    verify_parser.add_argument("--expected-source-repository-id", required=True)
    verify_parser.add_argument("--expected-source-ref", required=True)
    verify_parser.add_argument("--expected-source-commit", required=True)
    verify_parser.add_argument("--expected-caller-workflow-ref", required=True)
    verify_parser.add_argument("--expected-caller-workflow-sha", required=True)
    verify_parser.add_argument("--expected-publisher-workflow-ref", required=True)
    verify_parser.add_argument("--expected-contract-sha", required=True)
    sbom_verify_parser = commands.add_parser("verify-sbom")
    sbom_verify_parser.add_argument("--registry-repository", required=True)
    sbom_verify_parser.add_argument("--image-digest", required=True)
    sbom_verify_parser.add_argument("--expected-spdx-digest", required=True)
    sbom_verify_parser.add_argument("--expected-bundle-digest", required=True)
    sbom_verify_parser.add_argument("--expected-referrer-digest", required=True)
    sbom_verify_parser.add_argument("--expected-source-repository", required=True)
    sbom_verify_parser.add_argument("--expected-source-repository-id", required=True)
    sbom_verify_parser.add_argument("--expected-source-ref", required=True)
    sbom_verify_parser.add_argument("--expected-source-commit", required=True)
    sbom_verify_parser.add_argument("--expected-caller-workflow-ref", required=True)
    sbom_verify_parser.add_argument("--expected-caller-workflow-sha", required=True)
    sbom_verify_parser.add_argument("--expected-publisher-workflow-ref", required=True)
    sbom_verify_parser.add_argument("--expected-contract-sha", required=True)
    verify_parser.add_argument("--base-repository")
    verify_parser.add_argument("--base-digest")
    manifest_parser = commands.add_parser("sign-manifest")
    manifest_parser.add_argument("--identity-token", required=True, type=Path)
    manifest_parser.add_argument("--manifest", required=True, type=Path)
    manifest_parser.add_argument("--bundle", required=True, type=Path)
    sbom_parser = commands.add_parser("sign-sbom")
    sbom_parser.add_argument("--identity-token", required=True, type=Path)
    sbom_parser.add_argument("--sbom", required=True, type=Path)
    sbom_parser.add_argument("--bundle", required=True, type=Path)
    sbom_parser.add_argument("--referrer-manifest", required=True, type=Path)
    sbom_parser.add_argument("--image-repository", required=True)
    sbom_parser.add_argument("--image-digest", required=True)
    statement_parser = commands.add_parser("statement")
    statement_parser.add_argument("--identity-token", required=True, type=Path)
    statement_parser.add_argument("--buildkit-provenance", required=True, type=Path)
    statement_parser.add_argument("--reviewed-platforms", required=True, type=Path)
    statement_parser.add_argument("--image-repository", required=True)
    statement_parser.add_argument("--image-digest", required=True)
    statement_parser.add_argument("--base-repository")
    statement_parser.add_argument("--base-digest")
    statement_parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "request-oidc-token":
            write_github_oidc_token(
                args.output,
                args.request_url,
                os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", ""),
            )
            print("GitHub OIDC token written")
            return 0
        if args.command == "attest-image":
            claims = decode_github_oidc_claims(
                args.identity_token.read_text(encoding="utf-8").strip()
            )
            statement = RETRY.build_cosign_provenance_statement(
                _load_json(args.buildkit_provenance),
                _load_json(args.reviewed_platforms),
                identity_claims=claims,
                image_repository=args.image_repository,
                image_digest=args.image_digest,
                base_repository=args.base_repository,
                base_digest=args.base_digest,
            )
            receipt = sign_and_attach_statement(
                statement,
                claims,
                args.identity_token,
                args.bundle,
                args.referrer_manifest,
                repository=args.image_repository,
                digest=args.image_digest,
            )
            print(json.dumps(receipt, sort_keys=True))
            return 0
        if args.command == "verify-image":
            receipt = verify_registry_image_provenance(
                repository=args.image_repository,
                registry_repository=args.registry_repository,
                digest=args.image_digest,
                buildkit_provenance=_load_json(args.buildkit_provenance),
                reviewed_platforms=_load_json(args.reviewed_platforms),
                identity_token=args.identity_token,
                expected_bundle_digest=args.expected_bundle_digest,
                expected_referrer_digest=args.expected_referrer_digest,
                expected_identity_claims={
                    "repository": args.expected_source_repository,
                    "repository_id": args.expected_source_repository_id,
                    "ref": args.expected_source_ref,
                    "sha": args.expected_source_commit,
                    "workflow_ref": args.expected_caller_workflow_ref,
                    "workflow_sha": args.expected_caller_workflow_sha,
                    "job_workflow_ref": args.expected_publisher_workflow_ref,
                    "job_workflow_sha": args.expected_contract_sha,
                },
                base_repository=args.base_repository,
                base_digest=args.base_digest,
            )
            print(json.dumps(receipt, sort_keys=True))
            return 0
        if args.command == "verify-sbom":
            claims = {
                "repository": args.expected_source_repository,
                "repository_id": args.expected_source_repository_id,
                "ref": args.expected_source_ref,
                "sha": args.expected_source_commit,
                "workflow_ref": args.expected_caller_workflow_ref,
                "workflow_sha": args.expected_caller_workflow_sha,
                "job_workflow_ref": args.expected_publisher_workflow_ref,
                "job_workflow_sha": args.expected_contract_sha,
            }
            receipt = verify_registry_sbom(
                registry_repository=args.registry_repository,
                image_digest=args.image_digest,
                expected_spdx_digest=args.expected_spdx_digest,
                expected_bundle_digest=args.expected_bundle_digest,
                expected_referrer_digest=args.expected_referrer_digest,
                publisher_workflow_ref=args.expected_publisher_workflow_ref,
                identity_claims=claims,
            )
            print(json.dumps(receipt, sort_keys=True))
            return 0
        if args.command == "sign-manifest":
            claims = decode_github_oidc_claims(
                args.identity_token.read_text(encoding="utf-8").strip()
            )
            receipt = sign_and_verify_blob(
                args.manifest,
                args.identity_token,
                args.bundle,
                claims,
            )
            print(json.dumps(receipt, sort_keys=True))
            return 0
        if args.command == "sign-sbom":
            receipt = sign_and_attach_sbom(
                sbom=args.sbom,
                identity_token=args.identity_token,
                bundle=args.bundle,
                referrer_manifest=args.referrer_manifest,
                repository=args.image_repository,
                digest=args.image_digest,
            )
            print(json.dumps(receipt, sort_keys=True))
            return 0
        # The same token file is passed to Cosign; Fulcio validates it when issuing the signing certificate.
        claims = decode_github_oidc_claims(
            args.identity_token.read_text(encoding="utf-8").strip()
        )
        statement = RETRY.build_cosign_provenance_statement(
            _load_json(args.buildkit_provenance),
            _load_json(args.reviewed_platforms),
            identity_claims=claims,
            image_repository=args.image_repository,
            image_digest=args.image_digest,
            base_repository=args.base_repository,
            base_digest=args.base_digest,
        )
        args.output.write_text(
            json.dumps(statement, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        CosignEvidenceError,
        RETRY.RetryEvidenceError,
    ) as error:
        print(f"Cosign provenance rejected: {error}", file=sys.stderr)
        return 1
    print("provenance statement written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
