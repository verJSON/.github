#!/usr/bin/env python3
"""Normalize and confirm provider-neutral AI review verdicts."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


REQUIRED_FIELDS = {"blocking", "summary", "review_first", "findings", "followups"}
MISSING = object()
FIELD_ALIASES = {
    "blocking": ("blocking", "isBlocking", "is_blocking"),
    "summary": ("summary",),
    "review_first": ("review_first", "reviewFirst"),
    "findings": ("findings",),
    "followups": ("followups", "followUps", "follow_ups"),
}
TOP_LEVEL_METADATA_FIELDS = {"confidence"}
ITEM_METADATA_FIELDS = {"confidence", "priority", "severity"}
FOLLOWUP_TEXT_FIELDS = ("note", "suggestion", "recommendation", "reason", "description")
MAX_EVIDENCE_CHARS = 240
MIN_EVIDENCE_CHARS = 8
MAX_SOURCE_BLOB_BYTES = 2 * 1024 * 1024
LINE_REFERENCE = r"[1-9][0-9]*(?:-[1-9][0-9]*)?"
NORMALIZABLE_LOCATION = re.compile(
    rf"^(?P<path>[^\r\n]+):(?P<line>[1-9][0-9]*)(?:-[1-9][0-9]*)?(?:\s*,\s*{LINE_REFERENCE})*$"
)
EXACT_LOCATION = re.compile(r"^(?P<path>[^\r\n]+):(?P<line>[1-9][0-9]*)$")
# The verdict is posted to the pull request. The review model's process held these
# values, so the workflow passes the same secrets here for an exact-match refusal.
SECRET_ENVIRONMENT = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GITHUB_TOKEN",
)
# Credential-shaped text even when the value is unknown here: a prefix followed by a
# token-length body. A bare prefix, as in "rotate any ghp_ tokens", is not material.
CREDENTIAL_SHAPE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}|sk-ant-[A-Za-z0-9_-]{20,})"
)


class VerdictError(ValueError):
    def __init__(self, path: str, expected: str, observed: str):
        self.path = path
        self.expected = expected
        self.observed = observed
        super().__init__(f"path={path} expected={expected} observed={observed}")


def observed_shape(value: object) -> str:
    if isinstance(value, dict):
        return f"object with {len(value)} fields"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__


def strictly_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(strictly_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(strictly_equal(a, b) for a, b in zip(left, right))
    return left == right


def alias_value(item: dict, aliases: tuple[str, ...], path: str) -> object:
    present = [name for name in aliases if name in item]
    if not present:
        return MISSING
    value = item[present[0]]
    if any(not strictly_equal(item[name], value) for name in present[1:]):
        raise VerdictError(path, "one unambiguous value", "conflicting aliases")
    return value


def reject_unknown_fields(item: dict, accepted: set[str], path: str) -> None:
    if any(field not in accepted for field in item):
        raise VerdictError(path, "documented fields and metadata", "one or more unknown fields")


def normalize_location_text(location: object, path: str, *, exact_line: bool = False) -> str:
    if not isinstance(location, str):
        raise VerdictError(path, "location text", observed_shape(location))
    match = (EXACT_LOCATION if exact_line else NORMALIZABLE_LOCATION).fullmatch(location.strip())
    if not match:
        expected = "one file and exactly one positive line" if exact_line else "one file and positive line"
        raise VerdictError(path, expected, "invalid location text")
    return f"{match.group('path').strip()}:{match.group('line')}"


def normalize_location(item: dict, path: str, *, exact_line: bool = False) -> str:
    location = alias_value(item, ("location",), path)
    file = alias_value(item, ("file", "path"), path)
    line = alias_value(item, ("line", "line_number"), path)
    candidates = []
    if location is not MISSING:
        candidates.append(normalize_location_text(location, path, exact_line=exact_line))
    if file is not MISSING or line is not MISSING:
        if not isinstance(file, str) or not isinstance(line, (int, str)) or isinstance(line, bool):
            raise VerdictError(path, "file/path plus line", observed_shape(item))
        candidates.append(normalize_location_text(f"{file}:{line}", path, exact_line=exact_line))
    if not candidates:
        raise VerdictError(path, "location text or file/path plus line", observed_shape(item))
    if any(candidate != candidates[0] for candidate in candidates[1:]):
        raise VerdictError(path, "one unambiguous location", "conflicting aliases")
    return candidates[0]


def normalize_review_first(item: object, index: int) -> dict:
    if not isinstance(item, dict):
        raise VerdictError(f"review_first[{index}]", "object", observed_shape(item))
    why = alias_value(item, ("why", "reason", "rationale"), f"review_first[{index}].why")
    if not isinstance(why, str) or not why.strip():
        raise VerdictError(
            f"review_first[{index}].why",
            "non-empty text in why, reason, or rationale",
            observed_shape(item),
        )
    location = normalize_location(item, f"review_first[{index}].location")
    reject_unknown_fields(
        item,
        {"location", "file", "path", "line", "line_number", "why", "reason", "rationale"}
        | ITEM_METADATA_FIELDS,
        f"review_first[{index}]",
    )
    return {"location": location, "why": why.strip()}


def required_text(item: dict, aliases: tuple[str, ...], path: str) -> str:
    value = alias_value(item, aliases, path)
    if not isinstance(value, str) or not value.strip():
        raise VerdictError(path, "non-empty text in " + ", ".join(aliases), observed_shape(item))
    return value.strip()


def normalize_finding(item: object, index: int) -> dict:
    if not isinstance(item, dict):
        raise VerdictError(f"findings[{index}]", "object", observed_shape(item))
    location = normalize_location(item, f"findings[{index}].location", exact_line=True)
    reason = required_text(item, ("reason", "why", "description"), f"findings[{index}].reason")
    failure_scenario = required_text(
        item,
        ("failure_scenario", "scenario", "impact"),
        f"findings[{index}].failure_scenario",
    )
    evidence = required_text(item, ("evidence",), f"findings[{index}].evidence")
    if any(ord(character) < 32 or ord(character) == 127 for character in evidence) or len(evidence) > MAX_EVIDENCE_CHARS:
        raise VerdictError(
            f"findings[{index}].evidence",
            f"one source-line fragment of at most {MAX_EVIDENCE_CHARS} characters",
            "multiline or oversized text",
        )
    reject_unknown_fields(
        item,
        {
            "location", "file", "path", "line", "line_number",
            "reason", "why", "description", "failure_scenario", "scenario", "impact", "evidence",
        }
        | ITEM_METADATA_FIELDS,
        f"findings[{index}]",
    )
    return {
        "location": location,
        "reason": reason,
        "failure_scenario": failure_scenario,
        "evidence": evidence,
    }


def normalize_followup(item: object, index: int) -> dict:
    if not isinstance(item, dict):
        raise VerdictError(f"followups[{index}]", "object", observed_shape(item))
    location = normalize_location(item, f"followups[{index}].location")
    note = required_text(item, FOLLOWUP_TEXT_FIELDS, f"followups[{index}].note")
    reject_unknown_fields(
        item,
        {"location", "file", "path", "line", "line_number", *FOLLOWUP_TEXT_FIELDS}
        | ITEM_METADATA_FIELDS,
        f"followups[{index}]",
    )
    return {
        "location": location,
        "note": note,
    }


def require_array(verdict: dict, field: str) -> list:
    value = verdict.get(field)
    if not isinstance(value, list):
        raise VerdictError(field, "array", observed_shape(value))
    return value


def canonical_fields(verdict: dict) -> dict:
    accepted = TOP_LEVEL_METADATA_FIELDS | {alias for aliases in FIELD_ALIASES.values() for alias in aliases}
    reject_unknown_fields(verdict, accepted, "$")
    canonical = {}
    for field, aliases in FIELD_ALIASES.items():
        present = [name for name in aliases if name in verdict]
        if not present:
            raise VerdictError("$", "canonical fields", f"missing {field}")
        value = alias_value(verdict, aliases, field)
        canonical[field] = value
    return canonical


def canonicalize_verdict(verdict: object, sensitive: bool) -> dict:
    if not isinstance(verdict, dict):
        raise VerdictError("$", "object", observed_shape(verdict))
    verdict = canonical_fields(verdict)
    blocking = verdict["blocking"]
    if not isinstance(blocking, bool):
        raise VerdictError("blocking", "boolean", observed_shape(blocking))
    summary = verdict["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise VerdictError("summary", "non-empty text", observed_shape(summary))
    review_first = [
        normalize_review_first(item, index)
        for index, item in enumerate(require_array(verdict, "review_first"))
    ]
    findings = [
        normalize_finding(item, index)
        for index, item in enumerate(require_array(verdict, "findings"))
    ]
    followups = [
        normalize_followup(item, index)
        for index, item in enumerate(require_array(verdict, "followups"))
    ]
    if sensitive and not review_first:
        raise VerdictError("review_first", "non-empty array for sensitive change", "empty array")
    if blocking != bool(findings):
        raise VerdictError("blocking", "true exactly when findings is non-empty", str(blocking).lower())
    return {
        "blocking": blocking,
        "summary": summary.strip(),
        "review_first": review_first,
        "findings": findings,
        "followups": followups,
    }


def parse_json_value(raw: str) -> object:
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n(?P<json>.*)\n```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group("json").strip()
    return json.loads(text)


def source_path(location: str, finding_path: str) -> tuple[str, int]:
    path_text, line_text = location.rsplit(":", 1)
    path = PurePosixPath(path_text)
    parts = path_text.split("/")
    if (
        path.is_absolute()
        or "\\" in path_text
        or any(part in {"", ".", ".."} for part in parts)
        or path_text == ".git"
        or path_text.startswith(".git/")
    ):
        raise VerdictError(finding_path, "repository-relative source path", "unsafe path")
    return path_text, int(line_text)


def git_output(repository: Path, *arguments: str) -> bytes:
    environment = {name: value for name, value in os.environ.items() if name not in SECRET_ENVIRONMENT}
    try:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            env=environment,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("review source repository is unavailable") from error


def validate_source_evidence(verdict: dict, repository_text: str, reviewed_head: str) -> None:
    if not verdict["findings"]:
        return
    if not re.fullmatch(r"[0-9a-f]{40}", reviewed_head):
        raise VerdictError("reviewed_head", "40-character lowercase commit SHA", "invalid value")
    repository = Path(repository_text).resolve()
    try:
        checkout_head = git_output(repository, "rev-parse", "HEAD").decode("ascii").strip()
    except (UnicodeDecodeError, ValueError) as error:
        raise VerdictError("reviewed_head", "available exact-head checkout", "repository unavailable") from error
    if checkout_head != reviewed_head:
        raise VerdictError("reviewed_head", "checked-out exact reviewed head", "head mismatch")

    for index, finding in enumerate(verdict["findings"]):
        evidence_path = f"findings[{index}].evidence"
        path_text, line_number = source_path(finding["location"], f"findings[{index}].location")
        object_name = f"{reviewed_head}:{path_text}"
        try:
            object_type = git_output(repository, "cat-file", "-t", object_name).decode("ascii").strip()
            source_size = int(git_output(repository, "cat-file", "-s", object_name).decode("ascii").strip())
            if source_size > MAX_SOURCE_BLOB_BYTES:
                raise VerdictError(
                    evidence_path,
                    f"source file no larger than {MAX_SOURCE_BLOB_BYTES} bytes",
                    "source exceeds bounded validation size",
                )
            source = git_output(repository, "cat-file", "blob", object_name).decode("utf-8")
        except VerdictError:
            raise
        except (UnicodeDecodeError, ValueError) as error:
            raise VerdictError(evidence_path, "UTF-8 source at reviewed head", "source unavailable") from error
        if object_type != "blob":
            raise VerdictError(evidence_path, "source file blob at reviewed head", object_type)
        lines = source.splitlines()
        if line_number > len(lines):
            raise VerdictError(evidence_path, "existing line at reviewed head", "line is past end of file")
        source_line = lines[line_number - 1].strip()
        evidence = finding["evidence"]
        required_length = min(MIN_EVIDENCE_CHARS, len(source_line))
        if not source_line or len(evidence) < required_length or evidence not in source_line:
            raise VerdictError(
                evidence_path,
                "nontrivial exact fragment of the cited source line at reviewed head",
                "fragment mismatch",
            )


def text_leaves(value: object, path: str = "$"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            child = key if path == "$" else f"{path}.{key}"
            yield child, key
            yield from text_leaves(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from text_leaves(item, f"{path}[{index}]")


def reject_credential_material(verdict: dict, secrets: tuple[str, ...]) -> None:
    for path, text in text_leaves(verdict):
        if any(secret in text for secret in secrets) or CREDENTIAL_SHAPE.search(text):
            raise VerdictError(path, "no credential material", "credential material")


def configured_secrets() -> tuple[str, ...]:
    return tuple(value for name in SECRET_ENVIRONMENT if (value := os.environ.get(name, "")).strip())


def confirm_output(
    raw: str,
    sensitive: bool,
    repository: str = "",
    reviewed_head: str = "",
    secrets: tuple[str, ...] = (),
) -> dict:
    if not isinstance(raw, str) or not raw.strip():
        return {
            "usable": False,
            "diagnostic": {"path": "$", "expected": "one JSON object", "observed": "empty output"},
        }
    try:
        parsed = parse_json_value(raw)
        verdict = canonicalize_verdict(parsed, sensitive)
        reject_credential_material(verdict, secrets)
        validate_source_evidence(verdict, repository, reviewed_head)
    except json.JSONDecodeError:
        return {
            "usable": False,
            "diagnostic": {"path": "$", "expected": "one JSON object", "observed": "invalid JSON"},
        }
    except VerdictError as error:
        return {
            "usable": False,
            "diagnostic": {"path": error.path, "expected": error.expected, "observed": error.observed},
        }
    return {
        "usable": True,
        "normalized": raw.strip() != json.dumps(verdict, separators=(",", ":")),
        "verdict": verdict,
    }


def main() -> int:
    raw = os.environ.get("VERDICT", "")
    sensitive_text = os.environ.get("SENSITIVE", "")
    output_path = os.environ.get("GITHUB_OUTPUT", "")
    review_pass = os.environ.get("REVIEW_PASS", "unknown")
    repository = os.environ.get("REVIEW_REPOSITORY", "")
    reviewed_head = os.environ.get("REVIEWED_HEAD_SHA", "")
    if review_pass not in {"1", "2"}:
        review_pass = "unknown"
    if sensitive_text not in {"true", "false"} or not output_path:
        raise ValueError("SENSITIVE must be true or false and GITHUB_OUTPUT is required")
    result = confirm_output(raw, sensitive_text == "true", repository, reviewed_head, configured_secrets())
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"usable={'true' if result['usable'] else 'false'}\n")
        if result["usable"]:
            output.write(f"verdict={json.dumps(result['verdict'], separators=(',', ':'))}\n")
            output.write(f"normalized={'true' if result['normalized'] else 'false'}\n")
            print(
                f"::notice::phase=model pass={review_pass} result=confirmed normalized={str(result['normalized']).lower()}"
            )
        else:
            diagnostic = json.dumps(result["diagnostic"], separators=(",", ":"))
            output.write(f"diagnostic={diagnostic}\n")
            detail = result["diagnostic"]
            print(
                f"::error::phase=model pass={review_pass} result=semantic-invalid "
                f"path={detail['path']} expected={detail['expected']} observed={detail['observed']}",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(1)
