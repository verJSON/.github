#!/usr/bin/env python3

import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("retry", ROOT / "container_candidate_retry.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


REPOSITORY = "ghcr.io/verjson/example"
DIGEST = "sha256:" + "a" * 64
BASE_REPOSITORY = "ghcr.io/verjson/base"
BASE_DIGEST = "sha256:" + "b" * 64


def evidence():
    return [
        {
            "attestation": {"bundle": {"mediaType": "application/vnd.dev.sigstore.bundle+json;version=0.3"}},
            "verificationResult": {
                "statement": {
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "subject": [
                        {"name": "verjson/example", "digest": {"sha256": "a" * 64}}
                    ],
                    "predicate": {
                        "buildDefinition": {
                            "resolvedDependencies": [
                                {
                                    "uri": f"pkg:docker/verjson/base@{BASE_DIGEST}",
                                    "digest": {"sha256": "b" * 64},
                                }
                            ]
                        }
                    },
                }
            },
        }
    ]


def buildkit_provenance():
    def entry(platform):
        return {
            "SLSA": {
                "buildDefinition": {
                    "buildType": "https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md",
                    "externalParameters": {
                        "request": {
                            "root": {
                                "configSource": {
                                    "path": "Dockerfile"
                                },
                                "request": {
                                    "args": {
                                        "vcs:revision": "c" * 40,
                                        "vcs:source": "https://github.com/Verjson/example",
                                    }
                                },
                            }
                        }
                    },
                    "resolvedDependencies": [
                        {
                            "uri": f"pkg:docker/{BASE_REPOSITORY}?digest={BASE_DIGEST}&platform={platform.replace('/', '%2F')}",
                            "digest": {"sha256": "b" * 64},
                        }
                    ],
                }
            }
        }

    return {"linux/amd64": entry("linux/amd64"), "linux/arm64": entry("linux/arm64")}


REVIEWED_PLATFORMS = [
    {"os": "linux", "architecture": "amd64"},
    {"os": "linux", "architecture": "arm64"},
]


class RetryEvidenceTests(unittest.TestCase):
    def assert_rejected(self, mutate, message):
        candidate = copy.deepcopy(evidence())
        mutate(candidate)
        with self.assertRaisesRegex(MODULE.RetryEvidenceError, message):
            MODULE.validate_verified_provenance(
                candidate, repository=REPOSITORY, digest=DIGEST
            )

    def test_accepts_one_exact_repository_and_digest_subject(self):
        identity = MODULE.validate_verified_provenance(
            evidence(), repository=REPOSITORY, digest=DIGEST
        )
        self.assertRegex(identity, r"^verified-bundle-sha256:[0-9a-f]{64}$")
        self.assertEqual(
            identity,
            MODULE.validate_verified_provenance(
                evidence(), repository=REPOSITORY, digest=DIGEST
            ),
        )

    def test_rejects_missing_duplicate_or_malformed_verification(self):
        for value in ([], evidence() * 2, {}, [None]):
            with self.subTest(value=value):
                with self.assertRaises(MODULE.RetryEvidenceError):
                    MODULE.validate_verified_provenance(
                        value, repository=REPOSITORY, digest=DIGEST
                    )

    def test_rejects_attacker_selected_repository_or_digest(self):
        self.assert_rejected(
            lambda value: value[0]["verificationResult"]["statement"]["subject"][0].update(name="attacker/image"),
            "repository differs",
        )
        self.assert_rejected(
            lambda value: value[0]["verificationResult"]["statement"]["subject"][0]["digest"].update(sha256="b" * 64),
            "digest differs",
        )

    def test_rejects_wrong_predicate_extra_subject_or_ambiguous_digest(self):
        self.assert_rejected(
            lambda value: value[0]["verificationResult"]["statement"].update(predicateType="https://example.invalid"),
            "predicate differs",
        )
        self.assert_rejected(
            lambda value: value[0]["verificationResult"]["statement"]["subject"].append(copy.deepcopy(value[0]["verificationResult"]["statement"]["subject"][0])),
            "exactly one subject",
        )
        self.assert_rejected(
            lambda value: value[0]["verificationResult"]["statement"]["subject"][0]["digest"].update(sha512="c" * 128),
            "digest differs",
        )

    def test_rejects_unexpected_unverified_envelope_fields(self):
        self.assert_rejected(
            lambda value: value[0].update(attackerSelected=True),
            "unexpected fields",
        )

    def test_accepts_one_exact_immutable_base_material_for_a_derived_image(self):
        MODULE.validate_buildkit_provenance(
            buildkit_provenance(),
            REVIEWED_PLATFORMS,
            source_repository="Verjson/example",
            source_commit="c" * 40,
            base_repository=BASE_REPOSITORY,
            base_digest=BASE_DIGEST,
        )

    def test_rejects_missing_mismatched_or_duplicate_base_material(self):
        cases = []
        missing = buildkit_provenance()
        missing["linux/amd64"]["SLSA"]["buildDefinition"]["resolvedDependencies"] = []
        cases.append(missing)
        mismatched = buildkit_provenance()
        mismatched["linux/amd64"]["SLSA"]["buildDefinition"]["resolvedDependencies"][0]["digest"]["sha256"] = "c" * 64
        cases.append(mismatched)
        duplicate = buildkit_provenance()
        duplicate["linux/amd64"]["SLSA"]["buildDefinition"]["resolvedDependencies"] *= 2
        cases.append(duplicate)
        for candidate in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(
                    MODULE.RetryEvidenceError, "exactly one immutable base dependency"
                ):
                    MODULE.validate_buildkit_provenance(
                        candidate,
                        REVIEWED_PLATFORMS,
                        source_repository="Verjson/example",
                        source_commit="c" * 40,
                        base_repository=BASE_REPOSITORY,
                        base_digest=BASE_DIGEST,
                    )

    def test_rejects_platform_or_source_substitution(self):
        wrong_platform = buildkit_provenance()
        wrong_platform["linux/s390x"] = wrong_platform.pop("linux/arm64")
        wrong_source = buildkit_provenance()
        wrong_source["linux/amd64"]["SLSA"]["buildDefinition"]["externalParameters"]["request"]["root"]["request"]["args"]["vcs:revision"] = "d" * 40
        for candidate, message in (
            (wrong_platform, "platforms differ"),
            (wrong_source, "source identity differs"),
        ):
            with self.assertRaisesRegex(MODULE.RetryEvidenceError, message):
                MODULE.validate_buildkit_provenance(
                    candidate,
                    REVIEWED_PLATFORMS,
                    source_repository="Verjson/example",
                    source_commit="c" * 40,
                    base_repository=BASE_REPOSITORY,
                    base_digest=BASE_DIGEST,
                )


class CosignProvenancePolicyTests(unittest.TestCase):
    def test_derives_statement_from_buildkit_evidence_and_authenticated_token_claims(self):
        claims = {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "sigstore",
            "repository": "Verjson/example",
            "repository_id": "12345",
            "ref": "refs/heads/main",
            "sha": "c" * 40,
            "workflow_ref": "Verjson/example/.github/workflows/container-candidate.yml@refs/heads/main",
            "workflow_sha": "c" * 40,
            "job_workflow_ref": "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "d" * 40,
            "job_workflow_sha": "d" * 40,
            "run_id": "987654",
            "run_attempt": "1",
        }
        statement = MODULE.build_cosign_provenance_statement(
            buildkit_provenance(),
            REVIEWED_PLATFORMS,
            identity_claims=claims,
            image_repository=REPOSITORY,
            image_digest=DIGEST,
        )

        identity = MODULE.validate_cosign_provenance(
            statement,
            buildkit_provenance=buildkit_provenance(),
            reviewed_platforms=REVIEWED_PLATFORMS,
            repository=REPOSITORY,
            digest=DIGEST,
            source_repository="Verjson/example",
            source_repository_id="12345",
            source_ref="refs/heads/main",
            source_commit="c" * 40,
            caller_workflow_ref=claims["workflow_ref"],
            caller_workflow_sha="c" * 40,
            publisher_workflow_ref=claims["job_workflow_ref"],
            contract_sha="d" * 40,
        )

        self.assertRegex(identity, r"^statement-sha256:[0-9a-f]{64}$")
        predicate = statement["predicate"]
        self.assertEqual(
            predicate["buildDefinition"]["externalParameters"]["reviewedPlatforms"],
            REVIEWED_PLATFORMS,
        )
        self.assertRegex(
            predicate["buildDefinition"]["externalParameters"]["buildkitProvenanceSha256"],
            r"^[0-9a-f]{64}$",
        )

    def test_refuses_buildkit_evidence_that_disagrees_with_oidc_source_claims(self):
        claims = {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "sigstore",
            "repository": "Verjson/example",
            "repository_id": "12345",
            "ref": "refs/heads/main",
            "sha": "c" * 40,
            "workflow_ref": "Verjson/example/.github/workflows/container-candidate.yml@refs/heads/main",
            "workflow_sha": "c" * 40,
            "job_workflow_ref": "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "d" * 40,
            "job_workflow_sha": "d" * 40,
            "run_id": "987654",
            "run_attempt": "1",
        }
        provenance = buildkit_provenance()
        provenance["linux/amd64"]["SLSA"]["buildDefinition"]["externalParameters"]["request"]["root"]["request"]["args"]["vcs:revision"] = "e" * 40

        with self.assertRaisesRegex(MODULE.RetryEvidenceError, "source identity differs"):
            MODULE.build_cosign_provenance_statement(
                provenance,
                REVIEWED_PLATFORMS,
                identity_claims=claims,
                image_repository=REPOSITORY,
                image_digest=DIGEST,
            )


    def test_accepts_one_statement_authorizing_source_caller_publisher_and_contract(self):
        identity = MODULE.validate_cosign_provenance(
            candidate_statement(),
            buildkit_provenance=buildkit_provenance(),
            reviewed_platforms=REVIEWED_PLATFORMS,
            repository=REPOSITORY,
            digest=DIGEST,
            source_repository="Verjson/example",
            source_repository_id="12345",
            source_ref="refs/heads/main",
            source_commit="c" * 40,
            caller_workflow_ref="Verjson/example/.github/workflows/container-candidate.yml@refs/heads/main",
            caller_workflow_sha="c" * 40,
            publisher_workflow_ref="Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "d" * 40,
            contract_sha="d" * 40,
            base_repository=BASE_REPOSITORY,
            base_digest=BASE_DIGEST,
        )
        self.assertRegex(identity, r"^statement-sha256:[0-9a-f]{64}$")

    def test_rejects_derived_image_with_different_buildkit_base_digest(self):
        provenance = buildkit_provenance()
        provenance["linux/amd64"]["SLSA"]["buildDefinition"]["resolvedDependencies"][0]["digest"] = {
            "sha256": "e" * 64
        }
        statement = candidate_statement()
        statement["predicate"]["buildDefinition"]["externalParameters"]["buildkitProvenanceSha256"] = hashlib.sha256(
            json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.assertRaisesRegex(MODULE.RetryEvidenceError, "base dependency"):
            MODULE.validate_cosign_provenance(
                statement,
                buildkit_provenance=provenance,
                reviewed_platforms=REVIEWED_PLATFORMS,
                repository=REPOSITORY,
                digest=DIGEST,
                source_repository="Verjson/example",
                source_repository_id="12345",
                source_ref="refs/heads/main",
                source_commit="c" * 40,
                caller_workflow_ref="Verjson/example/.github/workflows/container-candidate.yml@refs/heads/main",
                caller_workflow_sha="c" * 40,
                publisher_workflow_ref="Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "d" * 40,
                contract_sha="d" * 40,
                base_repository=BASE_REPOSITORY,
                base_digest=BASE_DIGEST,
            )

    def test_rejects_each_identity_dimension_when_it_differs(self):
        cases = (
            ("source", "repository", "attacker/repository"),
            ("source", "repository_id", "99999"),
            ("source", "ref", "refs/heads/feature"),
            ("source", "sha", "e" * 40),
            ("caller", "workflow_ref", "attacker/workflow.yml@refs/heads/main"),
            ("caller", "workflow_sha", "e" * 40),
            ("publisher", "workflow_ref", "attacker/publisher.yml@" + "d" * 40),
            ("publisher", "workflow_sha", "e" * 40),
        )
        for identity, claim, value in cases:
            with self.subTest(identity=identity, claim=claim):
                statement = candidate_statement()
                statement["predicate"]["buildDefinition"]["externalParameters"]["verjson"][identity][claim] = value
                with self.assertRaises(MODULE.RetryEvidenceError):
                    MODULE.validate_cosign_provenance(
                        statement,
                        buildkit_provenance=buildkit_provenance(),
                        reviewed_platforms=REVIEWED_PLATFORMS,
                        repository=REPOSITORY,
                        digest=DIGEST,
                        source_repository="Verjson/example",
                        source_repository_id="12345",
                        source_ref="refs/heads/main",
                        source_commit="c" * 40,
                        caller_workflow_ref="Verjson/example/.github/workflows/container-candidate.yml@refs/heads/main",
                        caller_workflow_sha="c" * 40,
                        publisher_workflow_ref="Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "d" * 40,
                        contract_sha="d" * 40,
                    )

    def test_rejects_multiple_subjects_wrong_digest_and_wrong_predicate(self):
        for mutate in (
            lambda value: value["subject"].append(copy.deepcopy(value["subject"][0])),
            lambda value: value["subject"][0]["digest"].update({"sha256": "e" * 64}),
            lambda value: value.update({"predicateType": "https://example.invalid/provenance"}),
        ):
            with self.subTest(mutate=mutate):
                statement = candidate_statement()
                mutate(statement)
                with self.assertRaises(MODULE.RetryEvidenceError):
                    MODULE.validate_cosign_provenance(
                        statement,
                        buildkit_provenance=buildkit_provenance(),
                        reviewed_platforms=REVIEWED_PLATFORMS,
                        repository=REPOSITORY,
                        digest=DIGEST,
                        source_repository="Verjson/example",
                        source_repository_id="12345",
                        source_ref="refs/heads/main",
                        source_commit="c" * 40,
                        caller_workflow_ref="Verjson/example/.github/workflows/container-candidate.yml@refs/heads/main",
                        caller_workflow_sha="c" * 40,
                        publisher_workflow_ref="Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "d" * 40,
                        contract_sha="d" * 40,
                    )


def candidate_statement():
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [{"name": REPOSITORY, "digest": {"sha256": "a" * 64}}],
        "predicate": {
            "buildDefinition": {
                "buildType": "https://github.com/moby/buildkit/blob/master/docs/attestations/slsa-definitions.md",
                "externalParameters": {
                    "buildkitProvenanceSha256": hashlib.sha256(
                        json.dumps(
                            buildkit_provenance(),
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode()
                    ).hexdigest(),
                    "reviewedPlatforms": REVIEWED_PLATFORMS,
                    "verjson": {
                        "source": {
                            "repository": "Verjson/example",
                            "repository_id": "12345",
                            "ref": "refs/heads/main",
                            "sha": "c" * 40,
                        },
                        "caller": {
                            "workflow_ref": "Verjson/example/.github/workflows/container-candidate.yml@refs/heads/main",
                            "workflow_sha": "c" * 40,
                        },
                        "publisher": {
                            "workflow_ref": "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "d" * 40,
                            "workflow_sha": "d" * 40,
                        },
                    }
                },
                "resolvedDependencies": [],
            }
        },
    }


if __name__ == "__main__":
    unittest.main()
