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

from snapshot_handoff import HandoffDelivery, HandoffPolicy, verify_snapshot_handoff
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
            "repositoryAuthority": "https://provider.example", "issuedAt": NOW - 5, "expiresAt": NOW + 55,
            "subjectId": "artifact:415",
            "subjectSha256": digest(b"subject artifact"), "snapshot": self.snapshot.copy(),
        }
        self.policy = HandoffPolicy("release-broker", "registry/publisher", "policy@3", 60, self.verify_signature)

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
        (self.root / "signing-message").write_bytes(message)
        signature = self.openssl("pkeyutl", "-sign", "-rawin", "-inkey", key + ".pem", "-in", "signing-message")
        return json.dumps({
            "payloadType": TYPE.decode(), "payload": base64.b64encode(payload).decode(),
            "signatures": [{"sig": base64.b64encode(signature).decode(), "keyid": hint}],
        }).encode()

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
        self.assertEqual(evidence.publication_scope, ("https://provider.example", "123", REF, "registry/publisher"))
        self.assertEqual(evidence.snapshot["tagObject"], self.snapshot["tagObject"])
        self.assertEqual(evidence.subject_sha256, digest(b"subject artifact"))
        self.assertEqual(evidence.payload_sha256, digest(base64.b64decode(json.loads(envelope)["payload"])))

    def test_missing_receipt_and_wrong_transport_digest_are_rejected(self):
        with self.assertRaisesRegex(ReceiptError, "envelope size"):
            self.verify(b"")
        envelope = self.envelope()
        with self.assertRaisesRegex(ReceiptError, "delivery digest"):
            self.verify(envelope + b" ", delivery=self.delivery(envelope))

    def test_forged_signature_does_not_select_an_untrusted_key(self):
        with self.assertRaisesRegex(ReceiptError, "signature"):
            self.verify(self.envelope(key="other", hint="other"))

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


if __name__ == "__main__":
    unittest.main()
