#!/usr/bin/env python3

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any


class RetryEvidenceError(ValueError):
    pass


DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RetryEvidenceError(f"{field} must be an object")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RetryEvidenceError(f"{field} must be a non-empty string")
    return value


def validate_verified_provenance(
    evidence: Any,
    *,
    repository: str,
    digest: str,
) -> str:
    if not isinstance(evidence, list) or len(evidence) != 1:
        raise RetryEvidenceError("exactly one verified provenance attestation is required")
    verified = _object(evidence[0], "verified provenance")
    if set(verified) != {"attestation", "verificationResult"}:
        raise RetryEvidenceError("verified provenance has unexpected fields")
    attestation = _object(verified["attestation"], "verified provenance.attestation")
    result = _object(
        verified["verificationResult"], "verified provenance.verificationResult"
    )
    statement = _object(result.get("statement"), "verified provenance statement")
    if statement.get("predicateType") != "https://slsa.dev/provenance/v1":
        raise RetryEvidenceError("verified provenance predicate differs")
    subjects = statement.get("subject")
    expected_name = repository.removeprefix("ghcr.io/")
    if not isinstance(subjects, list) or len(subjects) != 1:
        raise RetryEvidenceError("verified provenance must name exactly one subject")
    subject = _object(subjects[0], "verified provenance subject")
    subject_digest = _object(subject.get("digest"), "verified provenance subject.digest")
    if set(subject_digest) != {"sha256"} or subject_digest["sha256"] != digest.removeprefix("sha256:"):
        raise RetryEvidenceError("verified provenance subject digest differs")
    if _text(subject.get("name"), "verified provenance subject.name").removeprefix("pkg:docker/") not in {
        repository,
        expected_name,
    }:
        raise RetryEvidenceError("verified provenance subject repository differs")
    bundle = json.dumps(attestation, sort_keys=True, separators=(",", ":")).encode()
    return "verified-bundle-sha256:" + hashlib.sha256(bundle).hexdigest()


def validate_cosign_provenance(
    statement: Any,
    *,
    buildkit_provenance: Any,
    reviewed_platforms: Any,
    repository: str,
    digest: str,
    source_repository: str,
    source_repository_id: str,
    source_ref: str,
    source_commit: str,
    caller_workflow_ref: str,
    caller_workflow_sha: str,
    publisher_workflow_ref: str,
    contract_sha: str,
    base_repository: str | None = None,
    base_digest: str | None = None,
) -> str:
    statement = _object(statement, "Cosign provenance statement")
    if statement.get("_type") != "https://in-toto.io/Statement/v1":
        raise RetryEvidenceError("Cosign provenance statement type differs")
    if statement.get("predicateType") != "https://slsa.dev/provenance/v1":
        raise RetryEvidenceError("Cosign provenance predicate type differs")

    subjects = statement.get("subject")
    if not isinstance(subjects, list) or len(subjects) != 1:
        raise RetryEvidenceError("Cosign provenance must name exactly one subject")
    subject = _object(subjects[0], "Cosign provenance subject")
    subject_name = _text(subject.get("name"), "Cosign provenance subject.name")
    subject_digest = _object(subject.get("digest"), "Cosign provenance subject.digest")
    if subject_name.removeprefix("pkg:docker/") not in {
        repository,
        repository.removeprefix("ghcr.io/"),
    }:
        raise RetryEvidenceError("Cosign provenance subject repository differs")
    if subject_digest != {"sha256": digest.removeprefix("sha256:")}:
        raise RetryEvidenceError("Cosign provenance subject digest differs")

    predicate = _object(statement.get("predicate"), "Cosign provenance predicate")
    build_definition = _object(
        predicate.get("buildDefinition"), "Cosign provenance buildDefinition"
    )
    if build_definition.get("buildType") != (
        "https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md"
    ):
        raise RetryEvidenceError("Cosign provenance build type differs")
    if not isinstance(build_definition.get("resolvedDependencies"), list):
        raise RetryEvidenceError("Cosign provenance resolved dependencies are missing")

    external_parameters = _object(
        build_definition.get("externalParameters"),
        "Cosign provenance externalParameters",
    )
    verjson = _object(external_parameters.get("verjson"), "Cosign provenance verjson")
    provenance_digest = hashlib.sha256(
        json.dumps(buildkit_provenance, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if external_parameters.get("buildkitProvenanceSha256") != provenance_digest:
        raise RetryEvidenceError("Cosign provenance BuildKit evidence digest differs")
    if external_parameters.get("reviewedPlatforms") != reviewed_platforms:
        raise RetryEvidenceError("Cosign provenance reviewed platforms differ")
    expected_claims = {
        "source": {
            "repository": source_repository,
            "repository_id": source_repository_id,
            "ref": source_ref,
            "sha": source_commit,
        },
        "caller": {
            "workflow_ref": caller_workflow_ref,
            "workflow_sha": caller_workflow_sha,
        },
        "publisher": {
            "workflow_ref": publisher_workflow_ref,
            "workflow_sha": contract_sha,
        },
    }
    if set(verjson) != set(expected_claims):
        raise RetryEvidenceError("Cosign provenance identity dimensions are incomplete or ambiguous")
    for name, expected in expected_claims.items():
        actual = _object(verjson.get(name), f"Cosign provenance {name}")
        if actual != expected:
            raise RetryEvidenceError(f"Cosign provenance {name} authorization differs")
    if not caller_workflow_ref.endswith(f"@{source_ref}"):
        raise RetryEvidenceError("caller workflow ref does not match authorized source ref")
    if not re.fullmatch(r"[0-9a-f]{40}", contract_sha):
        raise RetryEvidenceError("contract SHA must be a 40-hex commit")
    if not publisher_workflow_ref.endswith(f"@{contract_sha}"):
        raise RetryEvidenceError("publisher workflow ref does not pin the contract SHA")
    validate_buildkit_provenance(
        buildkit_provenance,
        reviewed_platforms,
        source_repository=source_repository,
        source_commit=source_commit,
        base_repository=base_repository,
        base_digest=base_digest,
    )

    canonical = json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    return "statement-sha256:" + hashlib.sha256(canonical).hexdigest()


def build_cosign_provenance_statement(
    buildkit_provenance: Any,
    reviewed_platforms: Any,
    *,
    identity_claims: Any,
    image_repository: str,
    image_digest: str,
    base_repository: str | None = None,
    base_digest: str | None = None,
) -> dict[str, Any]:
    claims = _object(identity_claims, "GitHub OIDC claims")
    required = (
        "iss",
        "aud",
        "repository",
        "repository_id",
        "ref",
        "sha",
        "workflow_ref",
        "workflow_sha",
        "job_workflow_ref",
        "job_workflow_sha",
        "run_id",
        "run_attempt",
    )
    if any(name not in claims for name in required):
        raise RetryEvidenceError("GitHub OIDC identity claims are incomplete")
    if claims["iss"] != "https://token.actions.githubusercontent.com":
        raise RetryEvidenceError("GitHub OIDC issuer differs")
    audience = claims["aud"]
    if audience != "sigstore" and not (
        isinstance(audience, list) and "sigstore" in audience
    ):
        raise RetryEvidenceError("GitHub OIDC audience differs")

    source_repository = _text(claims["repository"], "GitHub OIDC repository")
    source_repository_id = claims["repository_id"]
    if isinstance(source_repository_id, int):
        source_repository_id = str(source_repository_id)
    source_repository_id = _text(source_repository_id, "GitHub OIDC repository_id")
    source_ref = _text(claims["ref"], "GitHub OIDC ref")
    source_commit = _text(claims["sha"], "GitHub OIDC sha")
    caller_workflow_ref = _text(claims["workflow_ref"], "GitHub OIDC workflow_ref")
    caller_workflow_sha = _text(claims["workflow_sha"], "GitHub OIDC workflow_sha")
    publisher_workflow_ref = _text(
        claims["job_workflow_ref"], "GitHub OIDC job_workflow_ref"
    )
    publisher_workflow_sha = _text(
        claims["job_workflow_sha"], "GitHub OIDC job_workflow_sha"
    )
    run_id = _text(claims["run_id"], "GitHub OIDC run_id")
    run_attempt = _text(claims["run_attempt"], "GitHub OIDC run_attempt")
    if not re.fullmatch(r"[0-9]+", source_repository_id):
        raise RetryEvidenceError("GitHub OIDC repository_id must be numeric")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise RetryEvidenceError("GitHub OIDC source SHA must be a 40-hex commit")
    if caller_workflow_sha != source_commit or not caller_workflow_ref.endswith(
        f"@{source_ref}"
    ):
        raise RetryEvidenceError("GitHub OIDC caller workflow identity differs from source")
    if not re.fullmatch(r"[0-9a-f]{40}", publisher_workflow_sha) or not publisher_workflow_ref.endswith(
        f"@{publisher_workflow_sha}"
    ):
        raise RetryEvidenceError("GitHub OIDC publisher workflow is not immutably pinned")
    if not re.fullmatch(r"[0-9]+", run_id) or not re.fullmatch(r"[0-9]+", run_attempt):
        raise RetryEvidenceError("GitHub OIDC run identity is malformed")
    if not DIGEST.fullmatch(image_digest):
        raise RetryEvidenceError("image digest must be a lowercase sha256 digest")

    platform_provenance = validate_buildkit_provenance(
        buildkit_provenance,
        reviewed_platforms,
        source_repository=source_repository,
        source_commit=source_commit,
        base_repository=base_repository,
        base_digest=base_digest,
    )
    dependencies = {}
    for evidence in platform_provenance.values():
        definition = _object(
            _object(evidence, "BuildKit platform evidence").get("SLSA"),
            "BuildKit SLSA evidence",
        ).get("buildDefinition")
        definition = _object(definition, "BuildKit buildDefinition")
        for offset, dependency in enumerate(definition.get("resolvedDependencies", [])):
            item = _object(dependency, f"BuildKit resolvedDependencies[{offset}]")
            key = json.dumps(item, sort_keys=True, separators=(",", ":"))
            dependencies[key] = item

    safe_claims = {
        "source": {
            "repository": source_repository,
            "repository_id": source_repository_id,
            "ref": source_ref,
            "sha": source_commit,
        },
        "caller": {
            "workflow_ref": caller_workflow_ref,
            "workflow_sha": caller_workflow_sha,
        },
        "publisher": {
            "workflow_ref": publisher_workflow_ref,
            "workflow_sha": publisher_workflow_sha,
        },
    }
    evidence_digest = hashlib.sha256(
        json.dumps(buildkit_provenance, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {
                "name": image_repository,
                "digest": {"sha256": image_digest.removeprefix("sha256:")},
            }
        ],
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
            "buildDefinition": {
                "buildType": "https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md",
                "externalParameters": {
                    "buildkitProvenanceSha256": evidence_digest,
                    "reviewedPlatforms": reviewed_platforms,
                    "verjson": safe_claims,
                },
                "resolvedDependencies": [
                    dependencies[key] for key in sorted(dependencies)
                ],
            },
            "runDetails": {
                "builder": {"id": publisher_workflow_ref},
                "metadata": {
                    "invocationId": (
                        f"https://github.com/{source_repository}/actions/runs/"
                        f"{run_id}/attempts/{run_attempt}"
                    )
                },
            },
        },
    }
    validate_cosign_provenance(
        statement,
        buildkit_provenance=buildkit_provenance,
        reviewed_platforms=reviewed_platforms,
        repository=image_repository,
        digest=image_digest,
        source_repository=source_repository,
        source_repository_id=source_repository_id,
        source_ref=source_ref,
        source_commit=source_commit,
        caller_workflow_ref=caller_workflow_ref,
        caller_workflow_sha=caller_workflow_sha,
        publisher_workflow_ref=publisher_workflow_ref,
        contract_sha=publisher_workflow_sha,
    )
    return statement


def validate_buildkit_provenance(
    provenance: Any,
    reviewed_platforms: Any,
    *,
    source_repository: str,
    source_commit: str,
    base_repository: str | None = None,
    base_digest: str | None = None,
) -> dict[str, Any]:
    provenance = _object(provenance, "BuildKit provenance index")
    if not isinstance(reviewed_platforms, list) or not reviewed_platforms:
        raise RetryEvidenceError("reviewed platforms must be a non-empty array")
    reviewed = set()
    for offset, platform_value in enumerate(reviewed_platforms):
        platform = _object(platform_value, f"reviewed platforms[{offset}]")
        os_name = _text(platform.get("os"), f"reviewed platforms[{offset}].os")
        architecture = _text(
            platform.get("architecture"), f"reviewed platforms[{offset}].architecture"
        )
        variant = platform.get("variant", "")
        if not isinstance(variant, str):
            raise RetryEvidenceError(f"reviewed platforms[{offset}].variant must be a string")
        identity = f"{os_name}/{architecture}{('/' + variant) if variant else ''}"
        if identity in reviewed:
            raise RetryEvidenceError("reviewed platforms contain a duplicate identity")
        reviewed.add(identity)
    if set(provenance) == {"SLSA"} and len(reviewed) == 1:
        provenance = {next(iter(reviewed)): provenance}
    if set(provenance) != reviewed:
        raise RetryEvidenceError("BuildKit provenance platforms differ from review")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise RetryEvidenceError("source commit must be a 40-hex commit")
    if (base_repository is None) != (base_digest is None):
        raise RetryEvidenceError("base repository and digest must be provided together")
    if base_digest is not None and not DIGEST.fullmatch(base_digest):
        raise RetryEvidenceError("base digest must be a lowercase sha256 digest")

    for identity in sorted(reviewed):
        entry = _object(provenance[identity], f"BuildKit provenance[{identity!r}]")
        slsa = _object(entry.get("SLSA"), f"BuildKit provenance[{identity!r}].SLSA")
        build_definition = _object(
            slsa.get("buildDefinition"),
            f"BuildKit provenance[{identity!r}].SLSA.buildDefinition",
        )
        if build_definition.get("buildType") != "https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md":
            raise RetryEvidenceError("BuildKit provenance build type differs")
        external = _object(
            build_definition.get("externalParameters"),
            f"BuildKit provenance[{identity!r}].externalParameters",
        )
        request = _object(external.get("request"), f"BuildKit provenance[{identity!r}].request")
        root = _object(request.get("root"), f"BuildKit provenance[{identity!r}].request.root")
        root_request = _object(
            root.get("request"),
            f"BuildKit provenance[{identity!r}].request.root.request",
        )
        vcs = _object(
            root_request.get("args"),
            f"BuildKit provenance[{identity!r}].request.root.request.args",
        )
        if vcs.get("vcs:revision") != source_commit or vcs.get("vcs:source") != f"https://github.com/{source_repository}":
            raise RetryEvidenceError("BuildKit provenance source identity differs")
        if base_repository is not None and base_digest is not None:
            dependencies = build_definition.get("resolvedDependencies")
            if not isinstance(dependencies, list):
                raise RetryEvidenceError("BuildKit resolved dependencies are missing")
            matches = []
            for offset, dependency_value in enumerate(dependencies):
                dependency = _object(
                    dependency_value,
                    f"BuildKit provenance[{identity!r}].resolvedDependencies[{offset}]",
                )
                uri = _text(
                    dependency.get("uri"),
                    f"BuildKit provenance[{identity!r}].resolvedDependencies[{offset}].uri",
                )
                parsed = urllib.parse.urlsplit(uri)
                if parsed.scheme != "pkg" or not parsed.path.startswith("docker/") or parsed.fragment:
                    continue
                query = urllib.parse.parse_qs(
                    parsed.query, keep_blank_values=True, strict_parsing=True
                )
                if set(query) != {"digest", "platform"} or any(
                    len(values) != 1 for values in query.values()
                ):
                    continue
                dependency_digest = _object(
                    dependency.get("digest"),
                    f"BuildKit provenance[{identity!r}].resolvedDependencies[{offset}].digest",
                )
                if (
                    parsed.path.removeprefix("docker/") == base_repository
                    and query["digest"] == [base_digest]
                    and query["platform"] == [identity]
                    and dependency_digest
                    == {"sha256": base_digest.removeprefix("sha256:")}
                ):
                    matches.append(dependency)
            if len(matches) != 1:
                raise RetryEvidenceError(
                    "BuildKit provenance does not bind exactly one immutable base dependency"
                )
    return provenance


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RetryEvidenceError(f"cannot read {path}: {error}") from error


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate exact-SHA container retry provenance"
    )
    parser.add_argument("--verified-provenance", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--base-repository")
    parser.add_argument("--base-digest")
    parser.add_argument("--buildkit-provenance", required=True, type=Path)
    parser.add_argument("--reviewed-platforms", required=True, type=Path)
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    try:
        if not DIGEST.fullmatch(args.digest):
            raise RetryEvidenceError("digest must be a lowercase sha256 digest")
        identity = validate_verified_provenance(
            _load(args.verified_provenance),
            repository=args.repository,
            digest=args.digest,
        )
        validate_buildkit_provenance(
            _load(args.buildkit_provenance),
            _load(args.reviewed_platforms),
            source_repository=args.source_repository,
            source_commit=args.source_commit,
            base_repository=args.base_repository,
            base_digest=args.base_digest,
        )
    except RetryEvidenceError as error:
        print(f"container retry evidence rejected: {error}", file=sys.stderr)
        return 1
    print(identity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
