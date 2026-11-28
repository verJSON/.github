#!/usr/bin/env python3
import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from snapshot_handoff import (
    HandoffDelivery,
    HandoffPolicy,
    SignatureVerifierUnavailable,
    SnapshotPushIntent,
    SnapshotPushObservation,
    issue_snapshot_handoff,
    verify_snapshot_handoff,
)
from snapshot_receipt_inspect import ReceiptError


TYPE = b"application/vnd.verjson.snapshot-handoff.v1+json"
NOW = 1_800_000_000
VERSION = "v1.0.0"
REF = f"refs/tags/{VERSION}"


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class SnapshotHandoffTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        cls.repo = cls.root / "checkout"
        cls.repo.mkdir()
        cls.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        cls.env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        cls.git("init", "-qb", "main")
        cls.git("config", "user.name", "Handoff Test")
        cls.git("config", "user.email", "handoff@example.invalid")
        fragment = cls.repo / "NEXT/2026-11-27-issue-1954-handoff.md"
        fragment.parent.mkdir()
        fragment.write_text("---\ndate: 2026-11-27\nissue: 1954\ntitle: Handoff test\nimpact: patch\n---\n\nTest.\n")
        cls.git("add", "NEXT")
        cls.git("commit", "-qm", "source")
        cls.context = {
            "repositoryId": "123", "producerWorkflow": "provider/workflow@immutable",
            "runId": "456", "runAttempt": "2", "sourceCommit": cls.git("rev-parse", "HEAD"),
            "version": VERSION,
        }
        engine = Path(__file__).with_name("changelog.py")
        subprocess.run([sys.executable, str(engine), "release", "--repo-root", str(cls.repo),
                        "--version", VERSION, "--fragment", fragment.name], check=True, env=cls.env)
        cls.snapshot = dict(
            cls.context, schemaVersion=1, kind="snapshot-receipt-prototype", tagRef=REF,
            tagObject=cls.git("rev-parse", REF), snapshotCommit=cls.git("rev-parse", "HEAD"),
            snapshotTree=cls.git("rev-parse", "HEAD^{tree}"),
            changelogSha256=digest((cls.repo / f"CHANGELOG/{VERSION}.md").read_bytes()),
        )
        for name in ("issuer", "other"):
            cls.openssl("genpkey", "-algorithm", "Ed25519", "-out", name + ".pem")
            cls.openssl("pkey", "-in", name + ".pem", "-pubout", "-out", name + ".pub")

    @classmethod
    def git(cls, *args):
        return subprocess.check_output(["git", "-C", str(cls.repo), *args], env=cls.env, text=True).strip()

    @classmethod
    def openssl(cls, *args):
        return subprocess.run(["openssl", *args], cwd=cls.root, check=True, capture_output=True).stdout

    def setUp(self):
        self.body = {
            "issuer": "release-broker", "consumer": "registry/publisher", "policyRevision": "policy@3",
            "repositoryAuthority": "https://provider.example", "destinationId": "registry:destination-15",
            "issuedAt": NOW - 5, "expiresAt": NOW + 55,
            "subjectId": "artifact:415",
            "subjectSha256": digest(b"subject artifact"), "snapshot": self.snapshot.copy(),
        }
        self.policy = HandoffPolicy(
            issuer="release-broker", consumer="registry/publisher", revision="policy@3",
            repository_authority="https://provider.example", repository_id="123",
            producer_workflow="provider/workflow@immutable", destination_id="registry:destination-15",
            max_lifetime_seconds=60, verify_signature=self.verify_signature,
        )

    def verify_signature(self, message, signature):
        (self.root / "message").write_bytes(message)
        (self.root / "signature").write_bytes(signature)
        command = ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", "issuer.pub",
                   "-in", "message", "-sigfile", "signature"]
        return subprocess.run(command, cwd=self.root, capture_output=True).returncode == 0

    def envelope(self, *, payload=None, key="issuer", hint="issuer"):
        if payload is None:
            payload = json.dumps(self.body, indent=2).encode()
        # Sign the original bytes, including JSON whitespace, using DSSE PAE.
        message = b"DSSEv1 %d %s %d %s" % (len(TYPE), TYPE, len(payload), payload)
        signature = self.sign(message, key=key)
        return json.dumps({
            "payloadType": TYPE.decode(), "payload": base64.b64encode(payload).decode(),
            "signatures": [{"sig": base64.b64encode(signature).decode(), "keyid": hint}],
        }).encode()

    def sign(self, message, *, key="issuer"):
        (self.root / "signing-message").write_bytes(message)
        return self.openssl("pkeyutl", "-sign", "-rawin", "-inkey", key + ".pem", "-in", "signing-message")

    def delivery(self, envelope):
        return HandoffDelivery("https://provider.example", self.context, digest(envelope),
                               "artifact:415", digest(b"subject artifact"))

    def verify(self, envelope, *, delivery=None, policy=None, now=NOW):
        return verify_snapshot_handoff(
            envelope, delivery=delivery or self.delivery(envelope), policy=policy or self.policy,
            now=now, repository=self.repo,
        )

    def test_authenticates_exact_signed_bytes_and_produces_stable_publication_scope(self):
        envelope = self.envelope(hint="other")
        evidence = self.verify(envelope)
        self.assertEqual(evidence.publication_scope, ("https://provider.example", "123", REF, "registry:destination-15"))
        self.assertEqual(evidence.snapshot["tagObject"], self.snapshot["tagObject"])
        self.assertEqual(evidence.subject_sha256, digest(b"subject artifact"))
        self.assertEqual(evidence.payload_sha256, digest(base64.b64decode(json.loads(envelope)["payload"])))
        self.assertEqual((evidence.issued_at, evidence.expires_at), (NOW - 5, NOW + 55))

    def test_missing_receipt_and_wrong_transport_digest_are_rejected(self):
        with self.assertRaisesRegex(ReceiptError, "envelope size"):
            self.verify(b"")
        envelope = self.envelope()
        with self.assertRaisesRegex(ReceiptError, "delivery digest"):
            self.verify(envelope + b" ", delivery=self.delivery(envelope))

    def test_forged_signature_does_not_select_an_untrusted_key(self):
        with patch("snapshot_handoff.inspect_snapshot_receipt") as inspect:
            with self.assertRaisesRegex(ReceiptError, "signature is invalid"):
                self.verify(self.envelope(key="other", hint="other"))
            inspect.assert_not_called()

    def test_signature_backend_failure_preserves_the_operational_cause(self):
        envelope = self.envelope()
        for unavailable in (OSError("backend unavailable"), subprocess.TimeoutExpired("verify", 1)):
            with self.subTest(error=type(unavailable)), patch("snapshot_handoff.inspect_snapshot_receipt") as inspect:
                policy = replace(self.policy, verify_signature=Mock(side_effect=unavailable))
                with self.assertRaisesRegex(SignatureVerifierUnavailable, "signature verifier is unavailable") as caught:
                    self.verify(envelope, policy=policy)
                self.assertIs(caught.exception.__cause__, unavailable)
                inspect.assert_not_called()

    def test_changed_payload_bytes_and_payload_type_are_rejected(self):
        envelope = json.loads(self.envelope())
        envelope["payload"] = base64.b64encode(base64.b64decode(envelope["payload"]) + b" ").decode()
        with self.assertRaisesRegex(ReceiptError, "signature"):
            self.verify(json.dumps(envelope).encode())
        envelope["payloadType"] = "application/json"
        with self.assertRaisesRegex(ReceiptError, "payload type"):
            self.verify(json.dumps(envelope).encode())

    def test_duplicate_fields_are_rejected_even_with_a_valid_signature(self):
        payload = b'{"issuer":"duplicate",' + json.dumps(self.body).encode()[1:]
        with self.assertRaisesRegex(ReceiptError, "duplicate receipt field"):
            self.verify(self.envelope(payload=payload))

    def test_wire_limits_are_checked_before_calling_the_signature_backend(self):
        signature = {"sig": "YQ=="}
        envelope = {"payloadType": TYPE.decode(), "payload": "e30=", "signatures": [signature]}
        payload_over_limit = base64.b64encode(b" " * (20 * 1024 + 1)).decode()
        signature_over_limit = base64.b64encode(b"a" * (8 * 1024 + 1)).decode()
        cases = (
            (b" " * (48 * 1024 + 1), "envelope size"),
            (json.dumps(dict(envelope, signatures=[signature, signature])).encode(), "requires one signature"),
            (json.dumps(dict(envelope, payload=payload_over_limit)).encode(), "payload encoding"),
            (json.dumps(dict(envelope, signatures=[{"sig": signature_over_limit}])).encode(), "signature encoding"),
            (json.dumps(dict(envelope, signatures=[{"sig": "YR=="}])).encode(), "signature encoding"),
        )
        for index, (wire, reason) in enumerate(cases):
            with self.subTest(case=index):
                verifier = Mock(return_value=True)
                with self.assertRaisesRegex(ReceiptError, reason):
                    self.verify(wire, policy=replace(self.policy, verify_signature=verifier))
                verifier.assert_not_called()

    def test_dsse_frame_uses_utf8_byte_lengths(self):
        envelope = b'{"payloadType":"application/vnd.verjson.snapshot-handoff.v1+json",'
        envelope += b'"payload":"eyJ0ZXh0Ijoiw6kifQ==","signatures":[{"sig":"YQ=="}]}'
        verifier = Mock(return_value=True)

        with self.assertRaisesRegex(ReceiptError, "payload fields"):
            self.verify(envelope, policy=replace(self.policy, verify_signature=verifier))

        verifier.assert_called_once_with(
            b'DSSEv1 48 application/vnd.verjson.snapshot-handoff.v1+json 13 {"text":"\xc3\xa9"}', b"a",
        )

    def test_signed_policy_and_subject_cannot_be_substituted(self):
        envelope = self.envelope()
        for field in ("issuer", "consumer", "revision"):
            with self.subTest(field=field), self.assertRaisesRegex(ReceiptError, "trusted policy"):
                self.verify(envelope, policy=replace(self.policy, **{field: "different"}))
        for field in ("subject_id", "subject_sha256"):
            wrong = replace(self.delivery(envelope), **{field: "different"})
            with self.subTest(field=field), self.assertRaisesRegex(ReceiptError, "subject artifact"):
                self.verify(envelope, delivery=wrong)

    def test_delivery_context_rejects_cross_repository_workflow_attempt_and_version(self):
        envelope = self.envelope()
        for field in self.context:
            wrong = replace(self.delivery(envelope), snapshot_context=dict(self.context, **{field: "different"}))
            with self.subTest(field=field), self.assertRaisesRegex(ReceiptError, "expected context"):
                self.verify(envelope, delivery=wrong)

    def test_delivery_keeps_the_dispatch_context_observed_at_construction(self):
        envelope = self.envelope()
        context = dict(self.context)
        delivery = replace(self.delivery(envelope), snapshot_context=context)
        context["runAttempt"] = "3"

        evidence = self.verify(envelope, delivery=delivery)

        self.assertEqual(evidence.snapshot["runAttempt"], self.context["runAttempt"])

    def test_current_policy_must_authorize_the_signed_source_and_destination(self):
        envelope = self.envelope()
        for field, value in (
            ("repository_authority", "https://other.example"),
            ("repository_id", "124"),
            ("producer_workflow", "provider/workflow@other-immutable"),
            ("destination_id", "registry:destination-16"),
        ):
            with self.subTest(field=field), patch("snapshot_handoff.inspect_snapshot_receipt") as inspect:
                with self.assertRaisesRegex(ReceiptError, "outside current trusted policy scope"):
                    self.verify(envelope, policy=replace(self.policy, **{field: value}))
                inspect.assert_not_called()

    def test_repository_id_cannot_be_reused_across_provider_authorities(self):
        envelope = self.envelope()
        wrong = replace(self.delivery(envelope), repository_authority="https://other.example")
        with self.assertRaisesRegex(ReceiptError, "repository authority"):
            self.verify(envelope, delivery=wrong)

    def test_time_window_rejects_future_expired_and_overlong_receipts(self):
        envelope = self.envelope()
        for now in (self.body["issuedAt"] - 1, self.body["expiresAt"]):
            with self.subTest(now=now), self.assertRaisesRegex(ReceiptError, "time window"):
                self.verify(envelope, now=now)
        with self.assertRaisesRegex(ReceiptError, "time window"):
            self.verify(envelope, policy=replace(self.policy, max_lifetime_seconds=59))

    def test_matching_signature_does_not_authorize_a_replaced_tag(self):
        envelope = self.envelope()
        self.addCleanup(self.git, "update-ref", REF, self.snapshot["tagObject"])
        self.git("tag", "-fa", VERSION, "-m", "different annotation", self.snapshot["snapshotCommit"])
        with self.assertRaisesRegex(ReceiptError, "observed tag ref differs"):
            self.verify(envelope)

    def test_another_attempt_uses_the_same_scope_but_has_distinct_receipt_bytes(self):
        first = self.verify(self.envelope())
        self.body["snapshot"]["runAttempt"] = "3"
        envelope = self.envelope()
        delivery = replace(self.delivery(envelope), snapshot_context=dict(self.context, runAttempt="3"))
        second = self.verify(envelope, delivery=delivery)
        self.assertEqual(first.publication_scope, second.publication_scope)
        self.assertNotEqual(first.payload_sha256, second.payload_sha256)

    def test_consumer_rename_preserves_the_destination_claim(self):
        first = self.verify(self.envelope())
        self.body["consumer"] = "renamed-publisher"
        self.body["policyRevision"] = "policy@4"
        policy = replace(self.policy, consumer="renamed-publisher", revision="policy@4")

        second = self.verify(self.envelope(), policy=policy)

        self.assertEqual(first.publication_scope, second.publication_scope)

    def push_evidence(self):
        intent = SnapshotPushIntent(
            operation_id="snapshot:456:2",
            repository_authority=self.policy.repository_authority,
            branch_ref="refs/heads/main",
            policy_revision=self.policy.revision,
            destination_id=self.policy.destination_id,
            subject_id=self.body["subjectId"],
            subject_sha256=self.body["subjectSha256"],
            snapshot=json.dumps(self.snapshot).encode(),
        )
        observation = SnapshotPushObservation(
            operation_id=intent.operation_id,
            intent_sha256=intent.intent_sha256,
            repository_authority=intent.repository_authority,
            snapshot_context=self.context,
            dispatch_ref=intent.branch_ref,
            branch_ref=intent.branch_ref,
            branch_before=self.snapshot["sourceCommit"],
            branch_after=self.snapshot["snapshotCommit"],
            branch_at_read=self.snapshot["snapshotCommit"],
            tag_ref=REF,
            tag_before=None,
            tag_after=self.snapshot["tagObject"],
            tag_at_read=self.snapshot["tagObject"],
            committed_at=NOW - 5,
        )
        return intent, observation

    def issue(self, intent, *, observe_push, sign, now=NOW, policy=None):
        return issue_snapshot_handoff(
            intent=intent, policy=self.policy if policy is None else policy, allowed_branch_ref="refs/heads/main",
            now=now, repository=self.repo,
            observe_push=observe_push, sign=sign,
        )

    def test_issuer_signs_the_observed_atomic_update_for_the_remaining_effect_window(self):
        intent, observation = self.push_evidence()
        observe_push = Mock(return_value=observation)
        sign = Mock(side_effect=self.sign)

        evidence = self.verify(self.issue(intent, observe_push=observe_push, sign=sign))

        observe_push.assert_called_once_with(intent.operation_id)
        sign.assert_called_once()
        self.assertEqual(evidence.snapshot["tagObject"], observation.tag_after)
        self.assertEqual(evidence.subject_sha256, intent.subject_sha256)
        self.assertEqual(evidence.issued_at, observation.committed_at)
        self.assertEqual(evidence.expires_at, observation.committed_at + self.policy.max_lifetime_seconds)

    def test_issuer_refuses_matching_refs_without_an_authenticated_push_observation(self):
        intent, _ = self.push_evidence()
        observe_push = Mock(return_value=None)
        sign = Mock()

        with self.assertRaisesRegex(ReceiptError, "snapshot push outcome requires reconciliation"):
            self.issue(intent, observe_push=observe_push, sign=sign)

        observe_push.assert_called_once_with(intent.operation_id)
        sign.assert_not_called()

    def test_issuer_refuses_observation_from_another_operation(self):
        intent, observation = self.push_evidence()
        observe_push = Mock(return_value=replace(observation, operation_id="snapshot:other"))
        sign = Mock(side_effect=self.sign)

        with self.assertRaisesRegex(ReceiptError, "snapshot push observation differs from the recorded atomic update"):
            self.issue(intent, observe_push=observe_push, sign=sign)

        sign.assert_not_called()

    def test_issuer_refuses_using_a_recorded_operation_with_a_different_subject(self):
        intent, observation = self.push_evidence()
        changed_intent = replace(intent, subject_id="artifact:416")
        sign = Mock(side_effect=self.sign)

        with self.assertRaisesRegex(ReceiptError, "snapshot push observation differs from the recorded atomic update"):
            self.issue(changed_intent, observe_push=Mock(return_value=observation), sign=sign)

        sign.assert_not_called()

    def test_issuer_refuses_a_tag_already_present_with_the_identical_object(self):
        intent, observation = self.push_evidence()
        observe_push = Mock(return_value=replace(observation, tag_before=observation.tag_after))
        sign = Mock(side_effect=self.sign)

        with self.assertRaisesRegex(ReceiptError, "snapshot push observation differs from the recorded atomic update"):
            self.issue(intent, observe_push=observe_push, sign=sign)

        sign.assert_not_called()

    def test_issuer_refuses_an_observed_push_when_remote_refs_differ(self):
        intent, observation = self.push_evidence()
        for field, oid in (
            ("branch_at_read", observation.branch_before),
            ("tag_at_read", observation.branch_after),
        ):
            with self.subTest(field=field):
                changed = replace(observation, **{field: oid})
                sign = Mock(side_effect=self.sign)

                with self.assertRaisesRegex(ReceiptError, "snapshot push readback differs from the recorded postimage"):
                    self.issue(intent, observe_push=Mock(return_value=changed), sign=sign)

                sign.assert_not_called()

    def test_issuer_refuses_to_renew_an_expired_push_effect(self):
        intent, observation = self.push_evidence()
        expired = replace(observation, committed_at=NOW - self.policy.max_lifetime_seconds)
        sign = Mock(side_effect=self.sign)

        with self.assertRaisesRegex(ReceiptError, "snapshot push time window is invalid"):
            self.issue(intent, observe_push=Mock(return_value=expired), sign=sign)

        sign.assert_not_called()

    def test_issuer_rechecks_the_bound_destination_and_revision(self):
        intent, observation = self.push_evidence()
        for field in ("destination_id", "revision"):
            with self.subTest(field=field):
                observe_push = Mock(return_value=observation)
                sign = Mock()
                policy = replace(self.policy, **{field: "changed"})

                with self.assertRaisesRegex(ReceiptError, "outside current trusted policy"):
                    self.issue(intent, observe_push=observe_push, sign=sign, policy=policy)

                observe_push.assert_not_called()
                sign.assert_not_called()

    def test_issuer_rejects_an_independently_observed_different_attempt(self):
        intent, observation = self.push_evidence()
        different_attempt = replace(observation, snapshot_context=dict(self.context, runAttempt="9"))
        sign = Mock()

        with self.assertRaisesRegex(ReceiptError, "independent expected context"):
            self.issue(intent, observe_push=Mock(return_value=different_attempt), sign=sign)

        sign.assert_not_called()

    def test_reobservation_cannot_extend_the_signed_window_or_change_the_payload(self):
        intent, observation = self.push_evidence()
        observe_push = Mock(return_value=observation)
        first = self.verify(self.issue(intent, observe_push=observe_push, sign=self.sign))
        retry = self.verify(self.issue(intent, observe_push=observe_push, sign=self.sign, now=NOW + 1))

        self.assertEqual(retry.payload_sha256, first.payload_sha256)
        self.assertEqual(retry.expires_at, first.expires_at)

    def test_issuer_rejects_an_intent_and_push_that_agree_on_an_unauthorized_branch(self):
        intent, observation = self.push_evidence()
        intent = replace(intent, branch_ref="refs/heads/other")
        observation = replace(observation, branch_ref=intent.branch_ref, intent_sha256=intent.intent_sha256)
        observe_push = Mock(return_value=observation)
        sign = Mock()

        with self.assertRaisesRegex(ReceiptError, "branch is outside current trusted policy"):
            self.issue(intent, observe_push=observe_push, sign=sign)

        observe_push.assert_not_called()
        sign.assert_not_called()

    def test_issuer_requires_the_dispatch_ref_to_match_the_authorized_branch(self):
        intent, observation = self.push_evidence()
        different_dispatch = replace(observation, dispatch_ref="refs/heads/other")
        sign = Mock()

        with self.assertRaisesRegex(ReceiptError, "differs from the recorded atomic update"):
            self.issue(intent, observe_push=Mock(return_value=different_dispatch), sign=sign)

        sign.assert_not_called()

    def test_issuer_withholds_envelope_when_the_signing_key_differs_from_policy(self):
        intent, observation = self.push_evidence()
        other_signer = Mock(side_effect=lambda frame: self.sign(frame, key="other"))

        with self.assertRaisesRegex(ReceiptError, "signature is invalid"):
            self.issue(intent, observe_push=Mock(return_value=observation), sign=other_signer)

    def test_issuer_holds_when_the_operation_observer_is_unavailable(self):
        intent, _ = self.push_evidence()
        error = OSError("operation observer unavailable")
        sign = Mock()

        with self.assertRaisesRegex(ReceiptError, "push observation is unavailable") as caught:
            self.issue(intent, observe_push=Mock(side_effect=error), sign=sign)

        self.assertIs(caught.exception.__cause__, error)
        sign.assert_not_called()

    def test_issuer_holds_when_the_signer_is_unavailable(self):
        intent, observation = self.push_evidence()
        error = subprocess.TimeoutExpired("sign", 1)

        with self.assertRaisesRegex(ReceiptError, "signer is unavailable") as caught:
            self.issue(intent, observe_push=Mock(return_value=observation), sign=Mock(side_effect=error))

        self.assertIs(caught.exception.__cause__, error)


if __name__ == "__main__":
    unittest.main()
