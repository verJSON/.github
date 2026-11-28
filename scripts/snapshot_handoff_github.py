"""Bind GitHub artifact transport to a protected snapshot dispatch claim."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from io import BytesIO
import re
import stat
import subprocess
import zipfile
import zlib

from snapshot_handoff import HandoffDelivery, MAX_ENVELOPE_BYTES
from snapshot_receipt_inspect import ReceiptError, decode_receipt_json


_CHECK_NAME = "verjson-snapshot-handoff-artifact"
_ENVELOPE_NAME = "snapshot-handoff.dsse.json"
_MAX_ARCHIVE_BYTES = 128 * 1024
_MAX_METADATA_BYTES = 128 * 1024
_APP_CLAIM_BYTES = 8192
_REPOSITORY_PATTERN = r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+"
_WORKFLOW_PATH_PATTERN = r"\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml"
_COMMIT_PATTERN = r"[0-9a-f]{40}"
_IDENTIFIER = re.compile(r"[1-9][0-9]{0,19}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True, kw_only=True)
class GitHubHandoffRoute:
    """Independently protected policy for the source workflow and claim issuer."""

    repository: str
    repository_authority: str
    repository_id: int
    workflow_path: str
    producer_workflow: str
    claim_app_id: int


@dataclass(frozen=True, kw_only=True)
class GitHubSnapshotDispatch:
    """Expected dispatch and subject from independent authenticated observations."""

    run_id: int
    run_attempt: int
    source_commit: str
    version: str
    subject_id: str
    subject_sha256: str


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _matches(value: object, expected: dict) -> bool:
    return isinstance(value, dict) and all(
        key in value and type(value[key]) is type(wanted) and value[key] == wanted
        for key, wanted in expected.items()
    )


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _read_envelope(archive: bytes) -> bytes:
    try:
        with zipfile.ZipFile(BytesIO(archive)) as bundle:
            entries = bundle.infolist()
            if len(entries) != 1:
                raise ReceiptError("GitHub handoff artifact must contain one envelope")
            entry = entries[0]
            if (entry.orig_filename != _ENVELOPE_NAME
                    or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                    or not 0 < entry.file_size <= MAX_ENVELOPE_BYTES
                    or entry.flag_bits & 1
                    or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                raise ReceiptError("GitHub handoff artifact envelope entry is invalid")
            with bundle.open(entry) as source:
                envelope = source.read(MAX_ENVELOPE_BYTES + 1)
            if len(envelope) != entry.file_size:
                raise ReceiptError("GitHub handoff artifact envelope size differs")
            return envelope
    except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, UnicodeDecodeError, zlib.error) as error:
        raise ReceiptError("GitHub handoff artifact archive is malformed") from error


def read_github_handoff(
    *,
    route: GitHubHandoffRoute,
    dispatch: GitHubSnapshotDispatch,
    check_id: int,
    api_get: Callable[[str], bytes],
) -> tuple[bytes, HandoffDelivery]:
    """Read exact-attempt evidence from authenticated GitHub REST responses.

    The caller must pick the route from protected policy and obtain the dispatch
    intent and subject observation independently of artifact contents. The
    configured App may attest only artifacts emitted by that immutable producer.
    The authenticated API adapter must bound downloads and handle GitHub archive
    redirects without forwarding credentials to the download host.
    """
    identifiers = route.repository_id, route.claim_app_id, dispatch.run_id, dispatch.run_attempt, check_id
    context_text = route.repository_authority, dispatch.version, dispatch.subject_id
    if (not isinstance(route.repository, str) or not re.fullmatch(_REPOSITORY_PATTERN, route.repository)
            or route.repository.split("/")[1] in (".", "..")
            or not isinstance(route.workflow_path, str) or not re.fullmatch(_WORKFLOW_PATH_PATTERN, route.workflow_path)
            or not isinstance(route.producer_workflow, str)
            or not re.fullmatch(f"{_REPOSITORY_PATTERN}/{_WORKFLOW_PATH_PATTERN}@{_COMMIT_PATTERN}", route.producer_workflow)
            or any(type(value) is not int or not 0 < value < 10**20 for value in identifiers)
            or any(not isinstance(value, str) or re.fullmatch(r"[!-~]{1,512}", value) is None for value in context_text)
            or not isinstance(dispatch.source_commit, str) or not re.fullmatch(_COMMIT_PATTERN, dispatch.source_commit)
            or not _is_digest(dispatch.subject_sha256)):
        raise ReceiptError("GitHub handoff route or dispatch is invalid")
    prefix = f"repos/{route.repository}"

    def fetch(path: str) -> bytes:
        try:
            return api_get(f"{prefix}/{path}")
        except (OSError, subprocess.SubprocessError) as error:
            raise ReceiptError("GitHub handoff evidence is unavailable") from error

    def metadata(path: str) -> dict:
        value = decode_receipt_json(fetch(path), max_bytes=_MAX_METADATA_BYTES)
        if not isinstance(value, dict):
            raise ReceiptError("GitHub handoff metadata must be an object")
        return value

    check = metadata(f"check-runs/{check_id}")
    if (not _matches(check, {"id": check_id, "name": _CHECK_NAME, "head_sha": dispatch.source_commit,
                             "status": "completed", "conclusion": "success"})
            or not _matches(check.get("app"), {"id": route.claim_app_id})):
        raise ReceiptError("GitHub handoff claim authority differs")
    output = check.get("output")
    summary = output.get("summary") if isinstance(output, dict) else None
    if not isinstance(summary, str) or not summary.isascii():
        raise ReceiptError("GitHub handoff claim is malformed")
    claim = decode_receipt_json(summary.encode("ascii"), max_bytes=_APP_CLAIM_BYTES)
    context = {
        "repositoryId": str(route.repository_id), "producerWorkflow": route.producer_workflow,
        "runId": str(dispatch.run_id), "runAttempt": str(dispatch.run_attempt),
        "sourceCommit": dispatch.source_commit, "version": dispatch.version,
    }
    expected_claim = {**context, "kind": "snapshot-handoff-artifact-v1",
                      "subjectId": dispatch.subject_id, "subjectSha256": dispatch.subject_sha256}
    if (not _matches(claim, expected_claim)
            or set(claim) != set(expected_claim) | {"artifactId", "archiveSha256", "envelopeSha256"}):
        raise ReceiptError("GitHub handoff claim does not match the expected dispatch attempt")
    artifact_id = claim["artifactId"]
    if (not isinstance(artifact_id, str) or not _IDENTIFIER.fullmatch(artifact_id)
            or not _is_digest(claim["archiveSha256"]) or not _is_digest(claim["envelopeSha256"])):
        raise ReceiptError("GitHub handoff claim artifact identity is invalid")

    run_path = f"actions/runs/{dispatch.run_id}"
    attempt_path = f"{run_path}/attempts/{dispatch.run_attempt}"
    expected_run = {
        "id": dispatch.run_id, "run_attempt": dispatch.run_attempt, "path": route.workflow_path,
        "head_sha": dispatch.source_commit, "event": "workflow_dispatch",
        "status": "completed", "conclusion": "success",
    }

    def verify_run(run: dict) -> None:
        repository = {"id": route.repository_id}
        if (not _matches(run, expected_run) or not _matches(run.get("repository"), repository)
                or not _matches(run.get("head_repository"), repository)):
            raise ReceiptError("GitHub handoff workflow attempt is obsolete or differs")

    verify_run(metadata(attempt_path))
    artifact_path = f"actions/artifacts/{artifact_id}"
    artifact = metadata(artifact_path)
    expected_artifact_run = {"id": dispatch.run_id, "repository_id": route.repository_id,
                             "head_repository_id": route.repository_id, "head_sha": dispatch.source_commit}
    if (not _matches(artifact, {"id": int(artifact_id), "expired": False, "digest": claim["archiveSha256"]})
            or not _matches(artifact.get("workflow_run"), expected_artifact_run)):
        raise ReceiptError("GitHub handoff artifact metadata differs")
    archive = fetch(f"{artifact_path}/zip")
    if (not isinstance(archive, bytes) or not 0 < len(archive) <= _MAX_ARCHIVE_BYTES
            or _sha256(archive) != claim["archiveSha256"]):
        raise ReceiptError("GitHub handoff artifact archive digest or size differs")
    envelope = _read_envelope(archive)
    if _sha256(envelope) != claim["envelopeSha256"]:
        raise ReceiptError("GitHub handoff envelope digest differs")
    verify_run(metadata(run_path))

    return envelope, HandoffDelivery(
        route.repository_authority, context, claim["envelopeSha256"], dispatch.subject_id, dispatch.subject_sha256,
    )
