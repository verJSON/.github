#!/usr/bin/env python3

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "cosign_provenance", ROOT / "container_cosign_provenance.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def token(payload):
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


class OidcTokenTests(unittest.TestCase):
    def test_extracts_claims_from_the_token_payload_without_printing_token(self):
        claims = {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "sigstore",
            "repository": "Verjson/example",
            "repository_id": "12345",
        }

        self.assertEqual(MODULE.decode_github_oidc_claims(token(claims)), claims)

    def test_rejects_malformed_token_or_missing_payload(self):
        for value in ("", "not-a-jwt", "header.!.signature", token([])):
            with self.subTest(value=value):
                with self.assertRaises(MODULE.CosignEvidenceError):
                    MODULE.decode_github_oidc_claims(value)

    def test_requests_a_sigstore_audience_token_into_a_private_file(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"value":"header.payload.signature"}'

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "oidc-token"
            with patch.object(urllib.request, "urlopen", return_value=Response()) as urlopen:
                MODULE.write_github_oidc_token(
                    output,
                    "https://token.actions.githubusercontent.com/?foo=bar",
                    "request-secret",
                )

            request = urlopen.call_args.args[0]
            self.assertIn("audience=sigstore", request.full_url)
            self.assertIn("foo=bar", request.full_url)
            self.assertEqual(request.get_header("Authorization"), "Bearer request-secret")
            self.assertEqual(output.read_text(), "header.payload.signature")
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)

    def test_rejects_an_invalid_oidc_endpoint_response_without_writing_a_token(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"value":""}'

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "oidc-token"
            with patch.object(urllib.request, "urlopen", return_value=Response()):
                with self.assertRaises(MODULE.CosignEvidenceError):
                    MODULE.write_github_oidc_token(
                        output,
                        "https://token.actions.githubusercontent.com/",
                        "request-secret",
                    )
            self.assertFalse(output.exists())


class SigstoreBundleTests(unittest.TestCase):
    def test_sbom_verification_reads_and_checks_the_original_gar_referrer(self):
        repository = "us-central1-docker.pkg.dev/verjson/candidates/example"
        image_digest = "sha256:" + "a" * 64
        referrer_digest = "sha256:" + "b" * 64
        spdx = b'{"spdxVersion":"SPDX-2.3"}\n'
        bundle = b"cosign bundle"
        identity = {
            "repository": "Verjson/example",
            "repository_id": "12345",
            "ref": "refs/heads/main",
            "sha": "c" * 40,
            "workflow_ref": "Verjson/example/.github/workflows/container-candidate.yml@refs/heads/main",
            "workflow_sha": "c" * 40,
            "job_workflow_ref": (
                "Verjson/.github/.github/workflows/container-candidate-publish.yml@"
                + "d" * 40
            ),
            "job_workflow_sha": "d" * 40,
        }
        expected_referrer = {
            "artifactType": "application/spdx+json",
            "digest": referrer_digest,
            "reference": f"{repository}@{referrer_digest}",
        }

        def run(command, **kwargs):
            if command[:3] == ["oras", "discover", "--format"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"referrers": [expected_referrer]}),
                    "",
                )
            if command[:2] == ["oras", "pull"]:
                output = Path(command[command.index("--output") + 1])
                (output / "sbom.spdx.json").write_bytes(spdx)
                (output / "sbom.sigstore.json").write_bytes(bundle)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(subprocess, "run", side_effect=run) as run_command:
            receipt = MODULE.verify_registry_sbom(
                registry_repository=repository,
                image_digest=image_digest,
                expected_spdx_digest="sha256:" + hashlib.sha256(spdx).hexdigest(),
                expected_bundle_digest="sha256:" + hashlib.sha256(bundle).hexdigest(),
                expected_referrer_digest=referrer_digest,
                publisher_workflow_ref=identity["job_workflow_ref"],
                identity_claims=identity,
            )

        self.assertEqual(receipt["referrerManifestDigest"], referrer_digest)
        self.assertEqual(receipt["spdxDigest"], "sha256:" + hashlib.sha256(spdx).hexdigest())
        verify = next(
            call.args[0]
            for call in run_command.call_args_list
            if call.args[0][:2] == ["cosign", "verify-blob"]
        )
        self.assertIn("--certificate-github-workflow-repository", verify)

    def test_signs_and_attaches_spdx_with_its_cosign_bundle(self):
        claims = {
            "job_workflow_ref": (
                "Verjson/.github/.github/workflows/container-candidate-publish.yml@"
                + "d" * 40
            )
        }

        def run(command, check):
            self.assertTrue(check)
            if command[1] == "sign-blob":
                Path(command[command.index("--bundle") + 1]).write_text("signed-bundle")
            elif command[1] == "attach":
                Path(command[command.index("--export-manifest") + 1]).write_text(
                    '{"schemaVersion":2}'
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sbom = root / "sbom.spdx.json"
            sbom.write_text('{"spdxVersion":"SPDX-2.3"}')
            sbom_digest = "sha256:" + hashlib.sha256(sbom.read_bytes()).hexdigest()
            token_path = root / "oidc-token"
            token_path.write_text("header.payload.signature")
            bundle = root / "sbom.sigstore.json"
            referrer = root / "sbom-referrer.json"
            with patch.object(MODULE, "decode_github_oidc_claims", return_value=claims), patch.object(
                subprocess, "run", side_effect=run
            ) as run_command:
                receipt = MODULE.sign_and_attach_sbom(
                    sbom=sbom,
                    identity_token=token_path,
                    bundle=bundle,
                    referrer_manifest=referrer,
                    repository="ghcr.io/verjson/example",
                    digest="sha256:" + "a" * 64,
                )

        attach = next(call.args[0] for call in run_command.call_args_list if call.args[0][1] == "attach")
        self.assertTrue(any(value.endswith("sbom.spdx.json:application/spdx+json") for value in attach))
        self.assertTrue(
            any(
                value.endswith(
                    "sbom.sigstore.json:application/vnd.dev.sigstore.bundle.v0.3+json"
                )
                for value in attach
            )
        )
        self.assertEqual(receipt["spdxDigest"], sbom_digest)
        self.assertTrue(receipt["referrerManifestDigest"].startswith("sha256:"))

    def test_registry_verification_rejects_token_identity_outside_independent_policy(self):
        identity = {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "sigstore",
            "repository": "Verjson/example",
            "repository_id": "12345",
            "ref": "refs/heads/main",
            "sha": "c" * 40,
            "workflow_ref": "Verjson/example/.github/workflows/container-candidate.yml@refs/heads/main",
            "workflow_sha": "c" * 40,
            "job_workflow_ref": (
                "Verjson/.github/.github/workflows/container-candidate-publish.yml@"
                + "d" * 40
            ),
            "job_workflow_sha": "d" * 40,
        }
        expected = {key: value for key, value in identity.items() if key not in {"iss", "aud"}}
        expected["repository_id"] = "99999"
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "oidc-token"
            token_path.write_text(token(identity))
            with patch.object(subprocess, "run") as run:
                with self.assertRaisesRegex(
                    MODULE.CosignEvidenceError,
                    "current workflow identity differs from independent policy",
                ):
                    MODULE.verify_registry_image_provenance(
                        repository="ghcr.io/verjson/example",
                        registry_repository="us-central1-docker.pkg.dev/verjson/candidates/example",
                        digest="sha256:" + "a" * 64,
                        buildkit_provenance={},
                        reviewed_platforms=[],
                        identity_token=token_path,
                        expected_identity_claims=expected,
                    )
            run.assert_not_called()

    def test_selects_exactly_one_direct_sigstore_bundle_referrer(self):
        referrer = {
            "digest": "sha256:" + "b" * 64,
            "reference": "ghcr.io/verjson/example@sha256:" + "b" * 64,
            "artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json",
        }

        self.assertEqual(
            MODULE.select_sigstore_bundle_referrer(
                {"referrers": [referrer]},
                repository="ghcr.io/verjson/example",
            ),
            referrer,
        )

    def test_rejects_missing_ambiguous_or_foreign_bundle_referrers(self):
        valid = {
            "digest": "sha256:" + "b" * 64,
            "reference": "ghcr.io/verjson/example@sha256:" + "b" * 64,
            "artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json",
        }
        for discovery, repository in (
            ({"referrers": []}, "ghcr.io/verjson/example"),
            ({"referrers": [valid, valid]}, "ghcr.io/verjson/example"),
            ({"referrers": [valid]}, "ghcr.io/verjson/other"),
        ):
            with self.subTest(discovery=discovery, repository=repository):
                with self.assertRaises(MODULE.CosignEvidenceError):
                    MODULE.select_sigstore_bundle_referrer(
                        discovery,
                        repository=repository,
                    )

    def test_extracts_the_signed_statement_from_one_sigstore_bundle(self):
        statement = {"predicateType": "https://slsa.dev/provenance/v1"}
        payload = base64.b64encode(json.dumps(statement).encode()).decode()
        bundle = {
            "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
            "dsseEnvelope": {
                "payloadType": "application/vnd.in-toto+json",
                "payload": payload,
            },
        }

        self.assertEqual(MODULE.statement_from_sigstore_bundle(bundle), statement)

    def test_rejects_missing_or_malformed_signed_statements(self):
        for bundle in (
            {},
            {"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json"},
            {
                "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                "dsseEnvelope": {
                    "payloadType": "application/vnd.in-toto+json",
                    "payload": "!",
                },
            },
        ):
            with self.subTest(bundle=bundle):
                with self.assertRaises(MODULE.CosignEvidenceError):
                    MODULE.statement_from_sigstore_bundle(bundle)

    def test_attest_image_cli_attaches_evidence_to_the_subject_repository(self):
        claims = {"repository": "Verjson/example"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_path = root / "oidc-token"
            buildkit_path = root / "buildkit.json"
            platforms_path = root / "platforms.json"
            token_path.write_text("header.payload.signature")
            buildkit_path.write_text("{}")
            platforms_path.write_text("[]")
            with patch.object(
                sys,
                "argv",
                [
                    "container_cosign_provenance.py",
                    "attest-image",
                    "--identity-token",
                    str(token_path),
                    "--buildkit-provenance",
                    str(buildkit_path),
                    "--reviewed-platforms",
                    str(platforms_path),
                    "--image-repository",
                    "ghcr.io/verjson/example",
                    "--image-digest",
                    "sha256:" + "a" * 64,
                    "--bundle",
                    str(root / "bundle.sigstore.json"),
                    "--referrer-manifest",
                    str(root / "referrer.json"),
                ],
            ), patch.object(MODULE, "decode_github_oidc_claims", return_value=claims), patch.object(
                MODULE.RETRY, "build_cosign_provenance_statement", return_value={}
            ), patch.object(MODULE, "sign_and_attach_statement", return_value={}) as sign:
                self.assertEqual(MODULE.main(), 0)

        self.assertEqual(sign.call_args.kwargs["repository"], "ghcr.io/verjson/example")

    def test_verify_image_cli_forwards_registry_repository_to_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_path = root / "oidc-token"
            buildkit_path = root / "buildkit.json"
            platforms_path = root / "platforms.json"
            token_path.write_text("header.payload.signature")
            buildkit_path.write_text("{}")
            platforms_path.write_text("[]")
            with patch.object(
                sys,
                "argv",
                [
                    "container_cosign_provenance.py",
                    "verify-image",
                    "--identity-token",
                    str(token_path),
                    "--buildkit-provenance",
                    str(buildkit_path),
                    "--reviewed-platforms",
                    str(platforms_path),
                    "--image-repository",
                    "ghcr.io/verjson/example",
                    "--registry-repository",
                    "us-central1-docker.pkg.dev/verjson/candidates/example",
                    "--expected-source-repository",
                    "Verjson/example",
                    "--expected-source-repository-id",
                    "12345",
                    "--expected-source-ref",
                    "refs/heads/main",
                    "--expected-source-commit",
                    "c" * 40,
                    "--expected-caller-workflow-ref",
                    "Verjson/example/.github/workflows/container-candidate.yml@refs/heads/main",
                    "--expected-caller-workflow-sha",
                    "c" * 40,
                    "--expected-publisher-workflow-ref",
                    "Verjson/.github/.github/workflows/container-candidate-publish.yml@" + "d" * 40,
                    "--expected-contract-sha",
                    "d" * 40,
                    "--image-digest",
                    "sha256:" + "a" * 64,
                ],
            ), patch.object(
                MODULE, "verify_registry_image_provenance", return_value={}
            ) as verify:
                self.assertEqual(MODULE.main(), 0)

        self.assertEqual(
            verify.call_args.kwargs["registry_repository"],
            "us-central1-docker.pkg.dev/verjson/candidates/example",
        )
        self.assertEqual(
            verify.call_args.kwargs["expected_identity_claims"],
            {
                "repository": "Verjson/example",
                "repository_id": "12345",
                "ref": "refs/heads/main",
                "sha": "c" * 40,
                "workflow_ref": "Verjson/example/.github/workflows/container-candidate.yml@refs/heads/main",
                "workflow_sha": "c" * 40,
                "job_workflow_ref": (
                    "Verjson/.github/.github/workflows/container-candidate-publish.yml@"
                    + "d" * 40
                ),
                "job_workflow_sha": "d" * 40,
            },
        )

    def test_signing_and_verification_commands_use_the_same_digest_and_identity(self):
        sign = MODULE.cosign_attest_blob_command(
            statement=Path("statement.json"),
            identity_token=Path("oidc-token"),
            bundle=Path("provenance.sigstore.json"),
            digest="a" * 64,
        )
        verify = MODULE.cosign_verify_attestation_command(
            identity="https://github.com/Verjson/.github/.github/workflows/publish.yml@" + "d" * 40,
            bundle=Path("provenance.sigstore.json"),
            digest="a" * 64,
        )

        self.assertEqual(sign[:2], ["cosign", "attest-blob"])
        self.assertEqual(sign[sign.index("--identity-token") + 1], "oidc-token")
        self.assertEqual(sign[sign.index("--hash") + 1], "a" * 64)
        self.assertEqual(verify[:2], ["cosign", "verify-blob-attestation"])
        self.assertEqual(verify[verify.index("--digest") + 1], "a" * 64)
        self.assertEqual(
            verify[verify.index("--certificate-identity") + 1],
            "https://github.com/Verjson/.github/.github/workflows/publish.yml@" + "d" * 40,
        )

    def test_manifest_blob_commands_require_the_verified_publisher_identity(self):
        sign = MODULE.cosign_sign_blob_command(
            blob=Path("candidate-manifest.json"),
            identity_token=Path("oidc-token"),
            bundle=Path("candidate-manifest.sigstore.json"),
        )
        verify = MODULE.cosign_verify_blob_command(
            blob=Path("candidate-manifest.json"),
            bundle=Path("candidate-manifest.sigstore.json"),
            identity="https://github.com/Verjson/.github/.github/workflows/publish.yml@" + "d" * 40,
        )

        self.assertEqual(sign[:2], ["cosign", "sign-blob"])
        self.assertIn("candidate-manifest.json", sign)
        self.assertEqual(verify[:2], ["cosign", "verify-blob"])
        self.assertIn("--certificate-oidc-issuer", verify)

    def test_attaches_only_a_cosign_verified_statement_and_records_bundle_digests(self):
        statement = {"predicateType": MODULE.PREDICATE_TYPE}
        claims = {
            "job_workflow_ref": "Verjson/.github/.github/workflows/publish.yml@" + "d" * 40,
            "repository": "Verjson/example",
            "ref": "refs/heads/main",
            "sha": "c" * 40,
        }

        def run(command, check):
            self.assertTrue(check)
            if command[1] == "attest-blob":
                bundle = Path(command[command.index("--bundle") + 1])
                payload = base64.b64encode(json.dumps(statement).encode()).decode()
                bundle.write_text(
                    json.dumps(
                        {
                            "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                            "dsseEnvelope": {
                                "payloadType": "application/vnd.in-toto+json",
                                "payload": payload,
                            },
                        }
                    )
                )
            elif command[1] == "attach":
                manifest = Path(command[command.index("--export-manifest") + 1])
                manifest.write_text('{"schemaVersion":2}')

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "oidc-token"
            token.write_text("header.payload.signature")
            bundle = root / "provenance.sigstore.json"
            manifest = root / "referrer.json"
            with patch.object(subprocess, "run", side_effect=run) as run_command:
                receipt = MODULE.sign_and_attach_statement(
                    statement,
                    claims,
                    token,
                    bundle,
                    manifest,
                    repository="ghcr.io/verjson/example",
                    digest="sha256:" + "a" * 64,
                )
                self.assertEqual(len(run_command.call_args_list), 3)
                self.assertEqual(
                    receipt["bundleDigest"],
                    "sha256:" + hashlib.sha256(bundle.read_bytes()).hexdigest(),
                )
                self.assertEqual(
                    receipt["referrerManifestDigest"],
                    "sha256:" + hashlib.sha256(manifest.read_bytes()).hexdigest(),
                )

    def test_registry_verification_binds_policy_and_cosign_certificate_claims(self):
        claims = {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "sigstore",
            "repository": "Verjson/example",
            "repository_id": "12345",
            "ref": "refs/heads/main",
            "sha": "c" * 40,
            "workflow_ref": "Verjson/example/.github/workflows/candidate.yml@refs/heads/main",
            "workflow_sha": "c" * 40,
            "job_workflow_ref": "Verjson/.github/.github/workflows/publish.yml@" + "d" * 40,
            "job_workflow_sha": "d" * 40,
            "run_id": "987654",
            "run_attempt": "1",
        }
        statement = {"predicateType": MODULE.PREDICATE_TYPE}
        payload = base64.b64encode(json.dumps(statement).encode()).decode()
        bundle = {
            "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
            "dsseEnvelope": {
                "payloadType": "application/vnd.in-toto+json",
                "payload": payload,
            },
        }
        descriptor_digest = "sha256:" + "b" * 64
        discovery = {
            "referrers": [
                {
                    "digest": descriptor_digest,
                    "reference": "ghcr.io/verjson/example@" + descriptor_digest,
                    "artifactType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                }
            ]
        }

        def run(command, **_kwargs):
            if command[1] == "discover":
                return SimpleNamespace(stdout=json.dumps(discovery))
            if command[1] == "pull":
                bundle_path = Path(command[command.index("--output") + 1]) / "provenance.sigstore.json"
                bundle_path.write_text(json.dumps(bundle))
            return SimpleNamespace(stdout="")

        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "oidc-token"
            token_path.write_text(token(claims))
            bundle_digest = "sha256:" + hashlib.sha256(
                json.dumps(bundle).encode()
            ).hexdigest()
            with (
                patch.object(subprocess, "run", side_effect=run) as run_command,
                patch.object(MODULE.RETRY, "validate_cosign_provenance") as validate,
            ):
                receipt = MODULE.verify_registry_image_provenance(
                    repository="ghcr.io/verjson/example",
                    digest="sha256:" + "a" * 64,
                    buildkit_provenance={},
                    reviewed_platforms=[],
                    identity_token=token_path,
                    expected_bundle_digest=bundle_digest,
                    expected_referrer_digest=descriptor_digest,
                )

        validate.assert_called_once()
        verify_command = run_command.call_args_list[-1].args[0]
        self.assertIn("--certificate-github-workflow-repository", verify_command)
        self.assertIn("Verjson/example", verify_command)
        self.assertEqual(receipt["referrerManifestDigest"], descriptor_digest)


if __name__ == "__main__":
    unittest.main()
