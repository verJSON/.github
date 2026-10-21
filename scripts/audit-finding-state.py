#!/usr/bin/env python3
"""Adjudicate a scheduled audit's findings against a reviewed expectation file.

The wrapped audit reports findings as `ERROR: <finding>` lines on **stderr**;
stdout is passed through untouched and is read only for the optional
structured report described below. An audit adopting this
adjudicator must report on stderr, and must declare the exit statuses it uses
with `--expect-status` if they are not 0 and 1.

An audit may also print, as a JSON object on stdout, `finding_classes`: every
finding class it evaluated with that class's sorted items, empty ones included.
Against an expectation entry that records the same class's item set, a strict
subset is then an improvement rather than drift (ADR 0189, 2026-10-21 amendment).
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path


FINDING_LINE = re.compile(r"^ERROR: (.+)$")
ENTRY_FIELDS = ("fingerprint", "finding", "issue", "reason", "expires")
ITEM_SET_FIELDS = ("finding_class", "items")
# A finding carries adopter-controlled repository names. It is only ever hashed,
# compared, and rendered as literal text -- never executed, and never emitted at
# the start of a line, so it cannot become an Actions workflow command.
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# A finding line has already been split, but a structured item has not: it may
# carry a line break, which must not reach a recorded entry or a rendered line.
ITEM_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

MATCH = 0
DRIFT = 1
UNDETERMINED = 2


class Undetermined(Exception):
    """The comparison could not be made, so the control fails closed."""


def fingerprint(finding: str) -> str:
    return hashlib.sha256(finding.encode("utf-8")).hexdigest()


def items_digest(items: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(items), ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def read_expectations(path: Path, audit: str) -> list[dict]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Undetermined(f"expectation file is unreadable: {error}") from None
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise Undetermined("expectation file has an unsupported schema")
    audits = document.get("audits")
    if not isinstance(audits, dict):
        raise Undetermined("expectation file declares no audits")
    if audit not in audits:
        raise Undetermined(f"audit {audit!r} has no recorded expectation")
    entries = audits[audit]
    if not isinstance(entries, list):
        raise Undetermined(f"audit {audit!r} expectation is not a list")
    validated = [validated_entry(entry, audit, index) for index, entry in enumerate(entries)]
    digests = [entry["fingerprint"] for entry in validated]
    if len(digests) != len(set(digests)):
        raise Undetermined(f"audit {audit!r} records the same finding more than once")
    classes = [entry["finding_class"] for entry in validated if "items" in entry]
    if len(classes) != len(set(classes)):
        raise Undetermined(f"audit {audit!r} records an item set for one finding class more than once")
    return validated


def validated_entry(entry: object, audit: str, index: int) -> dict:
    where = f"{audit}[{index}]"
    shapes = (set(ENTRY_FIELDS), set(ENTRY_FIELDS) | set(ITEM_SET_FIELDS))
    if not isinstance(entry, dict) or set(entry) not in shapes:
        raise Undetermined(
            f"expectation {where} must declare exactly {sorted(ENTRY_FIELDS)}, "
            f"optionally with {sorted(ITEM_SET_FIELDS)}"
        )
    finding = entry["finding"]
    if not isinstance(finding, str) or not finding:
        raise Undetermined(f"expectation {where} records no finding text")
    digest = entry["fingerprint"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise Undetermined(f"expectation {where} fingerprint is not a sha256 digest")
    # The recorded text is what a reviewer reads; the digest is what the
    # comparison uses. Requiring them to agree is what stops a waiver from
    # quietly covering a finding other than the one it claims to describe.
    if digest != fingerprint(finding):
        raise Undetermined(f"expectation {where} fingerprint does not describe its recorded finding")
    issue = entry["issue"]
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        raise Undetermined(f"expectation {where} names no tracking issue")
    reason = entry["reason"]
    if not isinstance(reason, str) or not reason:
        raise Undetermined(f"expectation {where} records no reason")
    if "items" in entry:
        validated_item_set(entry, where)
    return {**entry, "expires": parse_expiry(entry["expires"], where)}


def validated_item_set(entry: dict, where: str) -> None:
    # The recorded list decides whether a changed finding is an improvement, so
    # it must be the list the reviewed text commits to: the class is the text's
    # prefix and the text's item digest is the digest of exactly these items.
    name = entry["finding_class"]
    if not isinstance(name, str) or not name or not entry["finding"].startswith(f"{name}: "):
        raise Undetermined(f"expectation {where} finding_class is not its finding's class")
    items = validated_items(entry["items"], f"expectation {where}")
    if not items:
        raise Undetermined(f"expectation {where} records an empty item set")
    if not entry["finding"].endswith(f" items=sha256:{items_digest(items)}"):
        raise Undetermined(f"expectation {where} items do not match its finding's item digest")


def parse_expiry(value: object, where: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise Undetermined(f"expectation {where} expiry {value!r} is not an ISO date") from None


def observe(command: list[str], expected_statuses: frozenset[int]) -> tuple[list[str], dict | None]:
    if not command:
        raise Undetermined("no audit command was given to run")
    try:
        completed = subprocess.run(command, capture_output=True, text=True)
    except OSError as error:
        raise Undetermined(f"audit command could not be started: {error}") from None
    findings = []
    for line in completed.stderr.splitlines():
        match = FINDING_LINE.match(line)
        if match:
            findings.append(CONTROL_CHARACTERS.sub("\ufffd", match.group(1)))
    # The wrapped audit's own stdout is its structured result. It goes to this
    # process's stderr rather than its stdout, which carries the adjudicator's
    # own JSON; dropping it would make wrapping an audit quietly remove its
    # payload from the job log.
    sys.stderr.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    # An exit status the audit does not use is a crash, not a verdict. Without
    # this, an audit that printed the recorded finding and was then killed
    # satisfied both guards below and adjudicated `match`: a hub-privileged
    # control reporting conformance from a run that never finished.
    if completed.returncode not in expected_statuses:
        raise Undetermined(
            f"audit exited {completed.returncode}, which is not one of the statuses "
            f"it is declared to use ({', '.join(str(s) for s in sorted(expected_statuses))})"
        )
    # Neither shape can be compared, and treating either as "no finding" is the
    # fail-open ADR 0024 rules out: a crashed audit would read as conformance.
    if completed.returncode != 0 and not findings:
        raise Undetermined(
            f"audit exited {completed.returncode} and reported no finding to compare"
        )
    if completed.returncode == 0 and findings:
        raise Undetermined("audit succeeded while reporting findings")
    return findings, finding_classes(completed.stdout, findings)


def finding_classes(stdout: str, findings: list[str]) -> dict | None:
    """The item set behind each list finding, from the audit's structured stdout report.

    Absent evidence is not an error: the comparison is then the exact text, as
    for any audit that does not report item sets. Evidence that is present but
    malformed, or that disagrees with the findings it describes, is undetermined
    rather than ignored -- silently discarding it would hide a broken producer.
    """
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(report, dict) or "finding_classes" not in report:
        return None
    records = report["finding_classes"]
    if not isinstance(records, list):
        raise Undetermined("audit finding_classes is not a list")
    classes = {}
    for index, record in enumerate(records):
        name, items = validated_class_record(record, index)
        if name in classes:
            raise Undetermined(f"audit reports finding class {index} more than once")
        classes[name] = {"items": items, "finding": reported_finding(name, items, findings, index)}
    return classes


def validated_class_record(record: object, index: int) -> tuple[str, list[str]]:
    if not isinstance(record, dict) or set(record) != {"class", "items"}:
        raise Undetermined(f"audit finding class {index} must declare exactly ['class', 'items']")
    name, items = record["class"], record["items"]
    if not isinstance(name, str) or not name:
        raise Undetermined(f"audit finding class {index} has no name")
    return name, validated_items(items, f"audit finding class {index}")


def validated_items(items: object, where: str) -> list[str]:
    # Sorted and unique is the canonical form the item digest is computed over;
    # anything else is a producer the digest binding cannot be trusted to describe.
    if not isinstance(items, list) or not all(isinstance(item, str) and item for item in items):
        raise Undetermined(f"{where} items are not a list of names")
    if items != sorted(set(items)):
        raise Undetermined(f"{where} items are not sorted and unique")
    if any(ITEM_CONTROL_CHARACTERS.search(item) for item in items):
        raise Undetermined(f"{where} items carry a control character")
    return items


def reported_finding(name: str, items: list[str], findings: list[str], index: int) -> str | None:
    """The one finding a class's items describe, bound to them by the item digest."""
    reported = [finding for finding in findings if finding.startswith(f"{name}: ")]
    if not items:
        if reported:
            raise Undetermined(f"audit finding class {index} reports no items but has a finding")
        return None
    if len(reported) != 1:
        raise Undetermined(f"audit finding class {index} matches {len(reported)} findings, not one")
    if not reported[0].endswith(f" items=sha256:{items_digest(items)}"):
        raise Undetermined(f"audit finding class {index} items do not match its finding's item digest")
    return reported[0]


def adjudicate(
    observed: list[str], entries: list[dict], today: date, classes: dict | None = None
) -> dict:
    # An expired waiver stops keeping its finding quiet. Without that the file
    # accumulates acknowledgements that outlive the work they point at, which is
    # the same "permanently expected red" the comparison exists to end.
    expired = {entry["fingerprint"] for entry in entries if entry["expires"] < today}
    active = {entry["fingerprint"] for entry in entries} - expired
    seen = {fingerprint(finding): finding for finding in observed}
    new = seen.keys() - active - expired
    stale = active - seen.keys()
    improved = []
    resolved = []
    for entry in entries:
        if entry["fingerprint"] not in stale or "items" not in entry or classes is None:
            continue
        reported = classes.get(entry["finding_class"])
        if reported is None or not set(reported["items"]) < set(entry["items"]):
            continue
        stale.discard(entry["fingerprint"])
        if not reported["items"]:
            resolved.append({"fingerprint": entry["fingerprint"], "issue": entry["issue"]})
            continue
        new.discard(fingerprint(reported["finding"]))
        improved.append({
            "fingerprint": entry["fingerprint"],
            "issue": entry["issue"],
            "recorded_items": len(entry["items"]),
            "observed_items": len(reported["items"]),
            "entry": rerecorded_entry(entry, reported),
        })
    new, stale = sorted(new), sorted(stale)
    return {
        "verdict": "drift" if new or stale or expired else "match",
        "improved": improved,
        "resolved": resolved,
        "observed": [
            {"fingerprint": digest, "finding": seen[digest]} for digest in sorted(seen)
        ],
        "expected": sorted(active),
        "new": new,
        "stale": stale,
        "expired": sorted(expired),
    }


def rerecorded_entry(entry: dict, reported: dict) -> dict:
    """The recorded entry with this run's finding and items, ready to replace it.

    Issue, reason, and expiry are carried over unchanged: re-recording an
    improvement is the reviewer's chance to revisit them, not this process's.
    """
    return {
        **{key: entry[key] for key in ENTRY_FIELDS},
        "expires": entry["expires"].isoformat(),
        "finding": reported["finding"],
        "fingerprint": fingerprint(reported["finding"]),
        "finding_class": entry["finding_class"],
        "items": reported["items"],
    }


def render_summary(state: dict) -> str:
    lines = [
        f"## Scheduled audit finding state - `{state['audit']}`",
        "",
        f"Verdict: **{state['verdict']}**",
        "",
    ]
    if state["verdict"] == "undetermined":
        return "\n".join(lines + [f"- {state['reason']}", ""]) + "\n"
    classes = (
        ("new", "Newly appeared - not recorded in the reviewed expectation"),
        ("stale", "Recorded but no longer reproducing - remove the expectation"),
        ("expired", "Recorded acknowledgement has expired"),
    )
    findings = {item["fingerprint"]: item["finding"] for item in state["observed"]}
    for key, heading in classes:
        if not state[key]:
            continue
        lines.append(f"### {heading}")
        for digest in state[key]:
            lines.append(f"- `{digest}` {findings.get(digest, '(not observed this run)')}")
        lines.append("")
    for item in state["improved"]:
        lines += [
            "### Recorded item set shrank - re-record the expectation",
            f"- `{item['fingerprint']}` now reports {item['observed_items']} of its "
            f"{item['recorded_items']} recorded items. Replace its entry with:",
            "",
            "```json",
            json.dumps(item["entry"], indent=4, sort_keys=True),
            "```",
            "",
        ]
    if state["resolved"]:
        lines.append("### Recorded item set reports no items - remove the expectation")
        for item in state["resolved"]:
            lines.append(f"- `{item['fingerprint']}` (issue #{item['issue']})")
        lines.append("")
    if state["verdict"] == "match":
        lines.append("### Recorded findings still reproducing")
        for digest in state["expected"]:
            lines.append(f"- `{digest}` {findings.get(digest, '(not observed this run)')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def announce_improvements(state: dict) -> None:
    # Deliberately a workflow command, so the improvement is visible on the run
    # without opening it. Every interpolated value is an integer or a hex digest
    # this process computed; no audit- or adopter-controlled text reaches it.
    for item in state["improved"]:
        print(
            f"::notice title=Recorded audit finding improved::recorded finding "
            f"{item['fingerprint'][:12]} (issue #{item['issue']}) now reports "
            f"{item['observed_items']} of its {item['recorded_items']} recorded items; "
            "re-record its expectation from this run",
            file=sys.stderr,
        )
    for item in state["resolved"]:
        print(
            f"::notice title=Recorded audit finding resolved::recorded finding "
            f"{item['fingerprint'][:12]} (issue #{item['issue']}) reports none of its "
            "recorded items; remove its expectation",
            file=sys.stderr,
        )


def publish_summary(state: dict) -> None:
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destination:
        return
    with open(destination, "a", encoding="utf-8") as handle:
        handle.write(render_summary(state))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--expectations", required=True)
    parser.add_argument(
        "--expect-status",
        type=int,
        action="append",
        metavar="N",
        help=(
            "an exit status the audit is declared to use; repeatable. "
            "Any other status is a crash and the run is undetermined. "
            "Defaults to 0 and 1."
        ),
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        entries = read_expectations(Path(args.expectations), args.audit)
        today = datetime.now(timezone.utc).date()
        expected_statuses = frozenset(args.expect_status or (0, 1))
        findings, classes = observe(command, expected_statuses)
        state = adjudicate(findings, entries, today, classes)
    except Undetermined as error:
        publish_summary({"audit": args.audit, "verdict": "undetermined", "reason": str(error)})
        print(f"ERROR: audit-state-undetermined: {error}", file=sys.stderr)
        return UNDETERMINED
    state["audit"] = args.audit
    publish_summary(state)
    announce_improvements(state)
    print(json.dumps(state, sort_keys=True, separators=(",", ":")))
    return DRIFT if state["verdict"] == "drift" else MATCH


if __name__ == "__main__":
    raise SystemExit(main())
