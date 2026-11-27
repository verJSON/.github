#!/usr/bin/env python3
from dataclasses import replace
import hashlib
from io import BytesIO
import json
import unittest
from unittest.mock import Mock, call, patch
import zipfile

from snapshot_handoff import HandoffDelivery
from snapshot_handoff_github import GitHubHandoffRoute, GitHubSnapshotDispatch, read_github_handoff
from snapshot_receipt_inspect import ReceiptError


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class GitHubHandoffTest(unittest.TestCase):
    def setUp(self):
        self.route = GitHubHandoffRoute(
            repository="example/project",
            repository_authority="https://github.com",
            repository_id=123,
            workflow_path=".github/workflows/release.yml",
            producer_workflow="verJSON/.github/.github/workflows/changelog-release.yml@" + "a" * 40,
            claim_app_id=456,
        )
        self.dispatch = GitHubSnapshotDispatch(
            run_id=789,
            run_attempt=2,
            source_commit="b" * 40,
            version="v1.2.3",
            subject_id="release-snapshot",
            subject_sha256=digest(b"release snapshot"),
        )
        self.envelope = (
            b'{"payloadType":"application/vnd.verjson.snapshot-handoff.v1+json",'
            b'"payload":"e30=","signatures":[{"keyid":"fixture","sig":"YQ=="}]}\n'
        )
        archive = BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("snapshot-handoff.dsse.json", self.envelope)
        self.archive = archive.getvalue()
        self.context = {
            "repositoryId": "123",
            "producerWorkflow": self.route.producer_workflow,
            "runId": "789",
            "runAttempt": "2",
            "sourceCommit": self.dispatch.source_commit,
            "version": self.dispatch.version,
        }

    def provider(
        self, *, claimed_attempt: str = "2", app_id: int = 456, latest_attempt: int = 2,
        archive: bytes | None = None, advertised_envelope: bytes | None = None,
        archive_response: bytes | None = None, expired: bool = False,
    ) -> Mock:
        archive = self.archive if archive is None else archive
        advertised_envelope = self.envelope if advertised_envelope is None else advertised_envelope
        claim = {
            **self.context,
            "kind": "snapshot-handoff-artifact-v1",
            "runAttempt": claimed_attempt,
            "subjectId": self.dispatch.subject_id,
            "subjectSha256": self.dispatch.subject_sha256,
            "artifactId": "321",
            "archiveSha256": digest(archive),
            "envelopeSha256": digest(advertised_envelope),
        }
        check = {
            "id": 987,
            "app": {"id": app_id},
            "name": "verjson-snapshot-handoff-artifact",
            "head_sha": self.dispatch.source_commit,
            "status": "completed",
            "conclusion": "success",
            "output": {"summary": json.dumps(claim)},
        }
        run = {
            "id": self.dispatch.run_id,
            "run_attempt": self.dispatch.run_attempt,
            "repository": {"id": self.route.repository_id},
            "head_repository": {"id": self.route.repository_id},
            "path": self.route.workflow_path,
            "head_sha": self.dispatch.source_commit,
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
        }
        artifact = {
            "id": 321,
            "expired": expired,
            "digest": digest(archive),
            "workflow_run": {
                "id": self.dispatch.run_id,
                "repository_id": self.route.repository_id,
                "head_repository_id": self.route.repository_id,
                "head_sha": self.dispatch.source_commit,
            },
        }
        responses = {
            "repos/example/project/check-runs/987": json.dumps(check).encode(),
            "repos/example/project/actions/runs/789/attempts/2": json.dumps(run).encode(),
            "repos/example/project/actions/runs/789": json.dumps(dict(run, run_attempt=latest_attempt)).encode(),
            "repos/example/project/actions/artifacts/321": json.dumps(artifact).encode(),
            "repos/example/project/actions/artifacts/321/zip": archive if archive_response is None else archive_response,
        }
        return Mock(side_effect=responses.__getitem__)

    def read_handoff(self, api_get):
        return read_github_handoff(route=self.route, dispatch=self.dispatch, check_id=987, api_get=api_get)

    def test_matching_attempt_claim_returns_exact_envelope_and_delivery(self):
        api_get = self.provider(claimed_attempt="2")

        envelope, delivery = self.read_handoff(api_get)

        self.assertEqual(envelope, self.envelope)
        self.assertEqual(delivery, HandoffDelivery(
            self.route.repository_authority,
            self.context,
            digest(self.envelope),
            self.dispatch.subject_id,
            self.dispatch.subject_sha256,
        ))
        self.assertCountEqual(
            [call.args[0] for call in api_get.call_args_list],
            [
                "repos/example/project/check-runs/987",
                "repos/example/project/actions/runs/789/attempts/2",
                "repos/example/project/actions/runs/789",
                "repos/example/project/actions/artifacts/321",
                "repos/example/project/actions/artifacts/321/zip",
            ],
        )

    def test_run_artifact_cannot_reuse_a_claim_from_an_earlier_attempt(self):
        api_get = self.provider(claimed_attempt="1")

        with self.assertRaisesRegex(ReceiptError, "claim does not match the expected dispatch attempt"):
            self.read_handoff(api_get)
        self.assertNotIn(call("repos/example/project/actions/artifacts/321/zip"), api_get.call_args_list)

    def test_claim_requires_the_app_from_independent_policy(self):
        api_get = self.provider(app_id=457)
        with self.assertRaisesRegex(ReceiptError, "claim authority differs"):
            self.read_handoff(api_get)
        api_get.assert_called_once_with("repos/example/project/check-runs/987")

    def test_dispatch_requires_independent_version_and_subject(self):
        for field in ("version", "subject_id"):
            with self.subTest(field=field):
                api_get = self.provider()
                dispatch = replace(self.dispatch, **{field: None})
                with self.assertRaisesRegex(ReceiptError, "route or dispatch is invalid"):
                    read_github_handoff(route=self.route, dispatch=dispatch, check_id=987, api_get=api_get)
                api_get.assert_not_called()

    def test_missing_claim_does_not_fall_back_to_run_level_artifacts(self):
        api_get = Mock(side_effect=FileNotFoundError("missing check run"))
        with self.assertRaisesRegex(ReceiptError, "evidence is unavailable"):
            self.read_handoff(api_get)
        api_get.assert_called_once_with("repos/example/project/check-runs/987")

    def test_latest_run_is_checked_after_reading_the_envelope(self):
        api_get = self.provider(latest_attempt=3)
        with self.assertRaisesRegex(ReceiptError, "workflow attempt is obsolete or differs"):
            self.read_handoff(api_get)
        self.assertEqual(api_get.call_args.args[0], "repos/example/project/actions/runs/789")

    def test_expired_artifact_is_rejected_before_downloading(self):
        api_get = self.provider(expired=True)
        with self.assertRaisesRegex(ReceiptError, "artifact metadata differs"):
            self.read_handoff(api_get)
        self.assertEqual(api_get.call_args.args[0], "repos/example/project/actions/artifacts/321")

    def test_downloaded_archive_must_have_the_attested_digest(self):
        with self.assertRaisesRegex(ReceiptError, "archive digest or size differs"):
            self.read_handoff(self.provider(archive_response=b"different archive"))

    def test_archive_requires_exactly_one_envelope(self):
        archive = BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("snapshot-handoff.dsse.json", self.envelope)
            bundle.writestr("manifest.json", b"{}")
        with self.assertRaisesRegex(ReceiptError, "must contain one envelope"):
            self.read_handoff(self.provider(archive=archive.getvalue()))

    def test_archive_filename_decoding_errors_are_typed_rejections(self):
        error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid filename")
        with patch("snapshot_handoff_github.zipfile.ZipFile", side_effect=error):
            with self.assertRaisesRegex(ReceiptError, "artifact archive is malformed") as caught:
                self.read_handoff(self.provider())
        self.assertIs(caught.exception.__cause__, error)

    def test_envelope_digest_is_verified_separately_from_archive_digest(self):
        with self.assertRaisesRegex(ReceiptError, "envelope digest differs"):
            self.read_handoff(self.provider(advertised_envelope=b"different envelope"))


if __name__ == "__main__":
    unittest.main()
