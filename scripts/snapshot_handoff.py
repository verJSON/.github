"""Verify a signed snapshot handoff against independently observed release evidence."""
from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from types import MappingProxyType

from snapshot_receipt_inspect import ReceiptError, decode_receipt, decode_receipt_json, inspect_snapshot_receipt


_PAYLOAD_TYPE = b"application/vnd.verjson.snapshot-handoff.v1+json"
_PAYLOAD_FIELDS = {
    "issuer", "consumer", "policyRevision", "repositoryAuthority", "destinationId", "issuedAt", "expiresAt",
    "subjectId", "subjectSha256", "snapshot",
}
MAX_ENVELOPE_BYTES = 48 * 1024
_MAX_PAYLOAD_BYTES = 20 * 1024
_MAX_SIGNATURE_BYTES = 8 * 1024
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True, kw_only=True)
class HandoffPolicy:
    """Protected authorization for one source and canonical destination."""

    issuer: str
    consumer: str
    revision: str
    repository_authority: str
    repository_id: str
    producer_workflow: str
    destination_id: str
    max_lifetime_seconds: int
    verify_signature: Callable[[bytes, bytes], bool]


@dataclass(frozen=True)
class HandoffDelivery:
    """Authenticated observations of the dispatch, transport, and subject artifact."""

    repository_authority: str
    snapshot_context: Mapping[str, str]
    envelope_sha256: str
    subject_id: str
    subject_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_context", MappingProxyType(dict(self.snapshot_context)))


class SignatureVerifierUnavailable(ReceiptError):
    """The trusted signature verifier could not return a verdict."""


class SnapshotIssuanceHeld(ReceiptError):
    """The issuer cannot establish authority to sign a snapshot handoff."""


@dataclass(frozen=True, kw_only=True)
class SnapshotPushIntent:
    """The protected producer's immutable record from before the push."""

    operation_id: str
    repository_authority: str
    destination_id: str
    branch_ref: str
    policy_revision: str
    subject_id: str
    subject_sha256: str
    snapshot: bytes

    @property
    def intent_sha256(self) -> str:
        return _sha256(json.dumps({
            "kind": "snapshot-push-intent-v1",
            "operationId": self.operation_id,
            "repositoryAuthority": self.repository_authority,
            "destinationId": self.destination_id,
            "branchRef": self.branch_ref,
            "policyRevision": self.policy_revision,
            "subjectId": self.subject_id,
            "subjectSha256": self.subject_sha256,
            "snapshotSha256": _sha256(self.snapshot),
        }, sort_keys=True, separators=(",", ":")).encode("ascii"))


@dataclass(frozen=True, kw_only=True)
class SnapshotPushObservation:
    """An authenticated observation of one committed, atomic provider update."""

    operation_id: str
    intent_sha256: str
    repository_authority: str
    snapshot_context: Mapping[str, str]
    dispatch_ref: str
    branch_ref: str
    branch_before: str
    branch_after: str
    tag_ref: str
    tag_before: str | None
    tag_after: str
    branch_at_read: str | None
    tag_at_read: str | None
    committed_at: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_context", MappingProxyType(dict(self.snapshot_context)))


@dataclass(frozen=True)
class VerifiedSnapshotHandoff:
    """Point-in-time evidence for the publisher's separate acceptance transaction."""

    snapshot: Mapping[str, str | int]
    repository_authority: str
    consumer: str
    destination_id: str
    subject_id: str
    subject_sha256: str
    policy_revision: str
    payload_sha256: str
    issued_at: int
    expires_at: int

    @property
    def publication_scope(self) -> tuple[str, str, str, str]:
        # New attempts and replacement tag objects must contend for the same scope.
        return self.repository_authority, str(self.snapshot["repositoryId"]), str(self.snapshot["tagRef"]), self.destination_id


def _sha256(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _is_identifier(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 512 and all(33 <= ord(char) <= 126 for char in value)


def _decode_base64(value: object, *, field: str, max_bytes: int) -> bytes:
    if not isinstance(value, str) or len(value) > 4 * ((max_bytes + 2) // 3):
        raise ReceiptError(f"handoff {field} encoding is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ReceiptError(f"handoff {field} encoding is invalid") from exc
    if not 0 < len(decoded) <= max_bytes or base64.b64encode(decoded).decode("ascii") != value:
        raise ReceiptError(f"handoff {field} encoding is invalid")
    return decoded


def _signature_frame(payload: bytes) -> bytes:
    return b"DSSEv1 %d %s %d %s" % (len(_PAYLOAD_TYPE), _PAYLOAD_TYPE, len(payload), payload)


def _check_signature(policy: HandoffPolicy, frame: bytes, signature: bytes) -> None:
    try:
        verified = policy.verify_signature(frame, signature)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SignatureVerifierUnavailable("handoff signature verifier is unavailable") from exc
    if verified is not True:
        raise ReceiptError("handoff signature is invalid")


def issue_snapshot_handoff(
    *,
    intent: SnapshotPushIntent,
    policy: HandoffPolicy,
    allowed_branch_ref: str,
    now: int,
    repository: Path,
    observe_push: Callable[[str], SnapshotPushObservation | None],
    sign: Callable[[bytes], bytes],
) -> bytes:
    """Sign only after the protected adapter proves the recorded operation committed.

    The adapter supplies the current protected policy and allowed release
    branch, durable pre-push intent, and authoritative operation observation.
    It returns no observation for unknown, partial, or readback-only results.
    The signer and verifier are provisioned for the current policy's issuer.
    """
    snapshot = decode_receipt(intent.snapshot)
    identifiers = (intent.operation_id, intent.branch_ref, intent.repository_authority, intent.destination_id,
                   intent.policy_revision, intent.subject_id, policy.issuer, policy.consumer)
    if (not all(_is_identifier(value) for value in identifiers)
            or not intent.branch_ref.startswith("refs/heads/") or intent.branch_ref == "refs/heads/"
            or not isinstance(intent.subject_sha256, str) or not _SHA256.fullmatch(intent.subject_sha256)):
        raise SnapshotIssuanceHeld("snapshot push intent is invalid")
    if (intent.repository_authority, intent.destination_id, snapshot["repositoryId"],
        snapshot["producerWorkflow"], intent.policy_revision) != (
        policy.repository_authority, policy.destination_id, policy.repository_id, policy.producer_workflow, policy.revision,
    ):
        raise SnapshotIssuanceHeld("snapshot push intent is outside current trusted policy")
    if intent.branch_ref != allowed_branch_ref:
        raise SnapshotIssuanceHeld("snapshot push branch is outside current trusted policy")
    try:
        observed = observe_push(intent.operation_id)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SnapshotIssuanceHeld("snapshot push observation is unavailable") from exc
    if observed is None:
        raise SnapshotIssuanceHeld("snapshot push outcome requires reconciliation")
    if (not isinstance(observed, SnapshotPushObservation)
            or observed.operation_id != intent.operation_id or observed.intent_sha256 != intent.intent_sha256
            or observed.repository_authority != intent.repository_authority
            or observed.dispatch_ref != allowed_branch_ref
            or observed.branch_ref != intent.branch_ref or observed.branch_before != snapshot["sourceCommit"]
            or observed.branch_after != snapshot["snapshotCommit"] or observed.tag_ref != snapshot["tagRef"]
            or observed.tag_before is not None or observed.tag_after != snapshot["tagObject"]):
        raise SnapshotIssuanceHeld("snapshot push observation differs from the recorded atomic update")
    if observed.branch_at_read != observed.branch_after or observed.tag_at_read != observed.tag_after:
        raise SnapshotIssuanceHeld("snapshot push readback differs from the recorded postimage")

    issued, lifetime = observed.committed_at, policy.max_lifetime_seconds
    if (any(type(value) is not int for value in (issued, now, lifetime))
            or not 0 < issued <= now < issued + lifetime <= 2**63 - 1):
        raise SnapshotIssuanceHeld("snapshot push time window is invalid")
    inspect_snapshot_receipt(intent.snapshot, expected=observed.snapshot_context, repository=repository)
    payload = json.dumps({
        "issuer": policy.issuer, "consumer": policy.consumer, "policyRevision": policy.revision,
        "repositoryAuthority": intent.repository_authority, "destinationId": intent.destination_id,
        "issuedAt": issued, "expiresAt": issued + lifetime, "subjectId": intent.subject_id,
        "subjectSha256": intent.subject_sha256, "snapshot": snapshot,
    }, sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(payload) > _MAX_PAYLOAD_BYTES:
        raise SnapshotIssuanceHeld("snapshot handoff payload is too large")
    frame = _signature_frame(payload)
    try:
        signature = sign(frame)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SnapshotIssuanceHeld("snapshot handoff signer is unavailable") from exc
    if not isinstance(signature, bytes) or not 0 < len(signature) <= _MAX_SIGNATURE_BYTES:
        raise SnapshotIssuanceHeld("snapshot handoff signer returned an invalid signature")
    _check_signature(policy, frame, signature)
    return json.dumps({
        "payloadType": _PAYLOAD_TYPE.decode("ascii"), "payload": base64.b64encode(payload).decode("ascii"),
        "signatures": [{"sig": base64.b64encode(signature).decode("ascii")}],
    }, separators=(",", ":")).encode("ascii")


def verify_snapshot_handoff(
    envelope_bytes: bytes,
    *,
    delivery: HandoffDelivery,
    policy: HandoffPolicy,
    now: int,
    repository: Path,
) -> VerifiedSnapshotHandoff:
    """Require a policy-bound signature and an independently authenticated delivery.

    The caller supplies a verifier pinned to the current issuer's key and algorithm,
    an authenticated observation of a successful dispatch attempt and both artifacts,
    and a trusted clock. This function performs no publication or replay accounting.
    The signature verifier returns False for an invalid signature and raises
    SignatureVerifierUnavailable for an outage specific to its backend.
    """
    if not isinstance(envelope_bytes, bytes) or not 0 < len(envelope_bytes) <= MAX_ENVELOPE_BYTES:
        raise ReceiptError("handoff envelope size is invalid")
    if _sha256(envelope_bytes) != delivery.envelope_sha256:
        raise ReceiptError("handoff envelope differs from authenticated delivery digest")
    envelope = decode_receipt_json(envelope_bytes, max_bytes=MAX_ENVELOPE_BYTES)
    if not isinstance(envelope, dict) or set(envelope) != {"payloadType", "payload", "signatures"}:
        raise ReceiptError("handoff envelope fields are invalid")
    if envelope["payloadType"] != _PAYLOAD_TYPE.decode("ascii"):
        raise ReceiptError("handoff payload type is invalid")
    signatures = envelope["signatures"]
    if not isinstance(signatures, list) or len(signatures) != 1 or not isinstance(signatures[0], dict):
        raise ReceiptError("handoff requires one signature")
    signature_entry = signatures[0]
    if signature_entry.keys() not in ({"sig"}, {"sig", "keyid"}) or not isinstance(signature_entry.get("keyid", ""), str):
        raise ReceiptError("handoff signature fields are invalid")
    payload = _decode_base64(envelope["payload"], field="payload", max_bytes=_MAX_PAYLOAD_BYTES)
    signature = _decode_base64(signature_entry["sig"], field="signature", max_bytes=_MAX_SIGNATURE_BYTES)
    _check_signature(policy, _signature_frame(payload), signature)

    body = decode_receipt_json(payload, max_bytes=_MAX_PAYLOAD_BYTES)
    if not isinstance(body, dict) or set(body) != _PAYLOAD_FIELDS:
        raise ReceiptError("handoff payload fields are invalid")
    expected_policy = {"issuer": policy.issuer, "consumer": policy.consumer, "policyRevision": policy.revision}
    if any(not _is_identifier(body[field]) or body[field] != value for field, value in expected_policy.items()):
        raise ReceiptError("handoff differs from current trusted policy")
    if not _is_identifier(body["repositoryAuthority"]) or body["repositoryAuthority"] != delivery.repository_authority:
        raise ReceiptError("handoff repository authority differs from authenticated delivery")
    snapshot = body["snapshot"]
    if (body["repositoryAuthority"] != policy.repository_authority
            or not _is_identifier(body["destinationId"]) or body["destinationId"] != policy.destination_id
            or not isinstance(snapshot, dict)
            or snapshot.get("repositoryId") != policy.repository_id
            or snapshot.get("producerWorkflow") != policy.producer_workflow):
        raise ReceiptError("handoff is outside current trusted policy scope")
    if (not _is_identifier(body["subjectId"]) or body["subjectId"] != delivery.subject_id
            or not isinstance(body["subjectSha256"], str) or not _SHA256.fullmatch(body["subjectSha256"])
            or body["subjectSha256"] != delivery.subject_sha256):
        raise ReceiptError("handoff subject artifact differs from authenticated delivery")
    issued, expires, max_lifetime = body["issuedAt"], body["expiresAt"], policy.max_lifetime_seconds
    if (any(type(value) is not int for value in (issued, expires, now, max_lifetime))
            or not 0 < issued <= now < expires <= 2**63 - 1 or not 0 < expires - issued <= max_lifetime):
        raise ReceiptError("handoff time window is invalid")

    inspect_snapshot_receipt(json.dumps(snapshot, separators=(",", ":")).encode(),
                             expected=delivery.snapshot_context, repository=repository)
    return VerifiedSnapshotHandoff(
        snapshot=MappingProxyType(snapshot), repository_authority=delivery.repository_authority,
        consumer=policy.consumer, destination_id=policy.destination_id,
        subject_id=delivery.subject_id, subject_sha256=delivery.subject_sha256,
        policy_revision=policy.revision, payload_sha256=_sha256(payload),
        issued_at=issued, expires_at=expires,
    )
