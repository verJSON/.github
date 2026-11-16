#!/usr/bin/env python3
"""Report a merge-admission producer outage (ADR 0260, ARB-2031 C10 (ii), #2031).

The delivery channel is a failing run of this workflow on the default branch, owned by the Verjson/.github repository owner.
An Actions-wide outage or a billing limit that stops this monitor is the accepted C1 gap.

Reads workflow runs, job metadata, check annotations, and open pull requests.
It never writes. A fixture (`--fixture`) classifies a snapshot and contacts no API.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

# Three missed 15-minute sweeps. A qualifying success at exactly this age alerts.
HEARTBEAT = timedelta(minutes=45)
# Failed, cancelled, and was-not-evaluated runs stay visible for the same horizon.
WINDOW = HEARTBEAT
PRODUCER_WORKFLOW = "merge-admission-caller.yml"
PRODUCER_PATH = f".github/workflows/{PRODUCER_WORKFLOW}"
RUN_PAGE_CAP = 5
PAGE_SIZE = 100
CHANNEL = (
    "channel=failing default-branch workflow run "
    "owner=Verjson/.github repository owner "
    "gap=Actions-wide outage or billing limit that stops this monitor"
)
TRUNCATED = "producer run listing truncated inside the alert window"
WAS_NOT_EVALUATED = "was not evaluated"
# Conclusions that are not a successful evaluation and are not the cancelled class.
# Anything else completed is reported, so a new GitHub conclusion cannot pass quietly.
IN_PROGRESS = frozenset({"queued", "in_progress", "pending", "waiting", "requested"})
REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CHECK_RUN_URL = re.compile(
    r"^https://api\.github\.com/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/check-runs/([1-9][0-9]*)$"
)


class OutageDataError(ValueError):
    """The producer state could not be read. The monitor must not report quiet."""


def parse_time(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise OutageDataError(f"{label} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise OutageDataError(f"{label} is not a timestamp") from error
    if parsed.tzinfo is None:
        raise OutageDataError(f"{label} has no timezone")
    return parsed.astimezone(timezone.utc)


def format_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_span(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    return f"{seconds // 60}m{seconds % 60:02d}s"


def require_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OutageDataError(f"{label} is not an integer")
    return value


def is_evaluate_job(name: str) -> bool:
    """True for the producer evaluate job, including its caller prefix and matrix suffix.

    Live runs name it ``admit / evaluate (<head>, <pr>)``. A substring check would
    also have to reject the neighbouring ``validate-ai-review-key`` job, so the
    match is one `` / ``-separated segment.
    """
    return any(part == "evaluate" or part.startswith("evaluate ") for part in name.split(" / "))


def job_started(job: dict) -> bool:
    started = job.get("started_at")
    return isinstance(started, str) and started != ""


def annotation_messages(job: dict, label: str) -> list[str]:
    notes = job.get("annotations", [])
    if not isinstance(notes, list):
        raise OutageDataError(f"{label} annotations are not a list")
    messages = []
    for note in notes:
        if isinstance(note, str):
            messages.append(note)
        elif isinstance(note, dict) and isinstance(note.get("message"), str):
            messages.append(note["message"])
        else:
            raise OutageDataError(f"{label} annotation is not a message")
    return messages


def require_jobs(run: dict) -> list[dict]:
    jobs = run.get("jobs")
    if not isinstance(jobs, list):
        raise OutageDataError(f"run {run.get('id')} has no job list")
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get("name"), str):
            raise OutageDataError(f"run {run.get('id')} job has no name")
    return jobs


def qualifies(run: dict) -> bool:
    """A producer run that reached evaluate and concluded success."""
    if run.get("conclusion") != "success":
        return False
    return any(
        is_evaluate_job(job["name"]) and job_started(job) and job.get("conclusion") == "success"
        for job in require_jobs(run)
    )


def classify(snapshot: dict) -> list[str]:
    """Findings for one snapshot. Empty means quiet.

    Alerts on a producer workflow that is not ``active``; a run listing truncated
    inside the window; a completed run in the window whose conclusion is neither
    success nor a not-yet-started cancellation; a ``was not evaluated`` annotation
    on a run in the window; and, while a non-draft head is open, no qualifying
    success within ``HEARTBEAT``. A cancelled run with no started job is a displaced
    pending cancellation and is quiet.
    """
    if not isinstance(snapshot, dict):
        raise OutageDataError("snapshot is not an object")
    now = parse_time(snapshot.get("now"), "snapshot now")
    heads = require_int(snapshot.get("open_non_draft_heads"), "open_non_draft_heads")
    if heads < 0:
        raise OutageDataError("open_non_draft_heads is negative")
    if not isinstance(snapshot.get("runs_truncated"), bool):
        raise OutageDataError("runs_truncated is not a boolean")
    workflow = snapshot.get("producer_workflow")
    if not isinstance(workflow, dict) or workflow.get("path") != PRODUCER_PATH:
        raise OutageDataError("producer workflow path is not the caller")
    state = workflow.get("state")
    if not isinstance(state, str) or not state:
        raise OutageDataError("producer workflow state is absent")
    runs = snapshot.get("runs")
    if not isinstance(runs, list):
        raise OutageDataError("runs is not a list")

    findings = []
    if state != "active":
        findings.append(f"producer workflow state={state}")
    if snapshot["runs_truncated"]:
        findings.append(TRUNCATED)

    seen: set[int] = set()
    newest: datetime | None = None
    window_start = now - WINDOW
    for run in sorted(runs, key=lambda item: require_int(item.get("id"), "run id") if isinstance(item, dict) else -1):
        if not isinstance(run, dict):
            raise OutageDataError("run is not an object")
        run_id = require_int(run.get("id"), "run id")
        if run_id in seen:
            raise OutageDataError(f"run {run_id} is listed twice")
        seen.add(run_id)
        status = run.get("status")
        if not isinstance(status, str) or not status:
            raise OutageDataError(f"run {run_id} status is absent")
        updated = parse_time(run.get("updated_at"), f"run {run_id} updated_at")
        conclusion = run.get("conclusion")
        in_window = updated >= window_start
        if status in IN_PROGRESS and conclusion in (None, ""):
            continue
        if not isinstance(conclusion, str) or not conclusion:
            raise OutageDataError(f"run {run_id} conclusion is absent")
        if conclusion == "success":
            if qualifies(run):
                newest = updated if newest is None else max(newest, updated)
            if in_window:
                for job in require_jobs(run):
                    if any(
                        WAS_NOT_EVALUATED in message
                        for message in annotation_messages(job, f"run {run_id}")
                    ):
                        findings.append(f"was-not-evaluated run {run_id} job={job['name']}")
            continue
        if not in_window:
            continue
        if conclusion == "cancelled":
            jobs = require_jobs(run)
            started = [job for job in jobs if job_started(job)]
            if started:
                findings.append(f"cancelled run {run_id} started_jobs={len(started)}")
        else:
            findings.append(f"failed run {run_id} conclusion={conclusion}")
            jobs = require_jobs(run) if isinstance(run.get("jobs"), list) else []
        for job in jobs:
            messages = annotation_messages(job, f"run {run_id}")
            if any(WAS_NOT_EVALUATED in message for message in messages):
                findings.append(f"was-not-evaluated run {run_id} job={job['name']}")

    if heads > 0 and (newest is None or now - newest >= HEARTBEAT):
        age = "unbounded" if newest is None else format_span(now - newest)
        last = "none" if newest is None else format_stamp(newest)
        findings.append(f"heartbeat gap {age} open_non_draft_heads={heads} last_success={last}")
    return findings


def get_command(path: str, query: dict | None) -> list[str]:
    if (
        not isinstance(path, str)
        or not path
        or path.startswith(("-", "/"))
        or "://" in path
        or any(character.isspace() for character in path)
    ):
        raise OutageDataError("refusing a non-path GitHub request")
    url = path if not query else f"{path}?{urlencode(query)}"
    return ["gh", "api", "--method", "GET", url]


def gh_fetch(path: str, query: dict | None = None, run=subprocess.run) -> object:
    command = get_command(path, query)
    completed = run(command, check=False, capture_output=True, text=True, shell=False)
    if completed.returncode != 0:
        raise OutageDataError(f"GET {path} failed ({completed.returncode})")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise OutageDataError(f"GET {path} returned non-JSON") from error


def _pages(fetch, path: str, label: str, collection: str | None) -> list:
    """Pages of one GET until a short page or the cap. Over the cap, raises."""
    rows: list = []
    for page in range(1, RUN_PAGE_CAP + 1):
        body = fetch(path, {"per_page": PAGE_SIZE, "page": page})
        if collection is None:
            if not isinstance(body, list):
                raise OutageDataError(f"{label} is not a list")
            batch = body
        else:
            if not isinstance(body, dict) or not isinstance(body.get(collection), list):
                raise OutageDataError(f"{label} is not a page")
            batch = body[collection]
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
    raise OutageDataError(f"{label} was truncated")


def _run_pages(fetch, path: str, now: datetime) -> tuple[list, bool]:
    rows: list = []
    for page in range(1, RUN_PAGE_CAP + 1):
        body = fetch(path, {"per_page": PAGE_SIZE, "page": page})
        if not isinstance(body, dict) or not isinstance(body.get("workflow_runs"), list):
            raise OutageDataError("producer run listing is not a page of runs")
        batch = body["workflow_runs"]
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows, False
        oldest = min(parse_time(run.get("updated_at"), "run updated_at") for run in batch)
        if oldest <= now - WINDOW:
            return rows, False
    return rows, True


def _check_run_id(job: dict, label: str) -> int:
    match = CHECK_RUN_URL.fullmatch(job.get("check_run_url") or "")
    if match is None:
        raise OutageDataError(f"{label} check run url is absent")
    return int(match.group(1))


def _annotations(fetch, repository: str, check_run_id: int) -> list[str]:
    rows = _pages(
        fetch,
        f"repos/{repository}/check-runs/{check_run_id}/annotations",
        f"check run {check_run_id} annotations",
        None,
    )
    messages = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("message"), str):
            raise OutageDataError(f"check run {check_run_id} annotation is not a message")
        messages.append(row["message"])
    return messages


def _jobs(fetch, repository: str, run_id: int, with_annotations: bool) -> list[dict]:
    raw = _pages(
        fetch,
        f"repos/{repository}/actions/runs/{run_id}/jobs",
        f"run {run_id} jobs",
        "jobs",
    )
    jobs = []
    for job in raw:
        if not isinstance(job, dict) or not isinstance(job.get("name"), str):
            raise OutageDataError(f"run {run_id} job has no name")
        started = job.get("started_at")
        if started is not None and not isinstance(started, str):
            raise OutageDataError(f"run {run_id} job started_at is not a timestamp")
        annotations: list[str] = []
        # The warning is printed by the evaluate step. Other started jobs are not
        # re-read, so a sweep does not spend a request per plan and key-policy job.
        if with_annotations and isinstance(started, str) and started and is_evaluate_job(job["name"]):
            annotations = _annotations(fetch, repository, _check_run_id(job, f"run {run_id}"))
        completed = job.get("completed_at")
        jobs.append(
            {
                "name": job["name"],
                "conclusion": job.get("conclusion"),
                "started_at": started,
                "completed_at": completed if isinstance(completed, str) else None,
                "annotations": annotations,
            }
        )
    return jobs


def _normalize(raw: dict, jobs: list[dict] | None) -> dict:
    run_id = require_int(raw.get("id"), "run id")
    status = raw.get("status")
    if not isinstance(status, str):
        raise OutageDataError(f"run {run_id} status is absent")
    conclusion = raw.get("conclusion")
    if conclusion is not None and not isinstance(conclusion, str):
        raise OutageDataError(f"run {run_id} conclusion is not a string")
    run = {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "updated_at": raw.get("updated_at"),
    }
    if jobs is not None:
        run["jobs"] = jobs
    return run


def _open_heads(fetch, repository: str) -> int:
    heads: set[str] = set()
    for page in range(1, RUN_PAGE_CAP + 1):
        batch = fetch(
            f"repos/{repository}/pulls",
            {"state": "open", "per_page": PAGE_SIZE, "page": page},
        )
        if not isinstance(batch, list):
            raise OutageDataError("open pull request listing is not a list")
        for pull in batch:
            if not isinstance(pull, dict) or not isinstance(pull.get("draft"), bool):
                raise OutageDataError("open pull request draft flag is absent")
            head = pull.get("head")
            sha = head.get("sha") if isinstance(head, dict) else None
            if not isinstance(sha, str) or not sha:
                raise OutageDataError("open pull request head is absent")
            if pull["draft"] is False:
                heads.add(sha)
        if len(batch) < PAGE_SIZE:
            return len(heads)
    if heads:
        return len(heads)
    raise OutageDataError("open pull request listing was truncated before a non-draft head")


def collect(fetch, repository: str, now: datetime) -> dict:
    """Read-only snapshot of one repository's producer. ``fetch(path, query)`` is GET-only."""
    if not isinstance(repository, str) or REPOSITORY_NAME.fullmatch(repository) is None:
        raise OutageDataError("repository is not owner/name")
    if now.tzinfo is None:
        raise OutageDataError("collector clock has no timezone")
    workflow = fetch(f"repos/{repository}/actions/workflows/{PRODUCER_WORKFLOW}", None)
    if not isinstance(workflow, dict):
        raise OutageDataError("producer workflow response is not an object")
    if workflow.get("path") != PRODUCER_PATH:
        raise OutageDataError("producer workflow response names another path")
    state = workflow.get("state")
    if not isinstance(state, str) or not state:
        raise OutageDataError("producer workflow state is absent")
    workflow_id = require_int(workflow.get("id"), "producer workflow id")
    if workflow_id < 1:
        raise OutageDataError("producer workflow id is absent")

    raw_runs, truncated = _run_pages(
        fetch, f"repos/{repository}/actions/workflows/{workflow_id}/runs", now
    )
    window_start = now - WINDOW
    prepared = []
    for raw in raw_runs:
        if not isinstance(raw, dict):
            raise OutageDataError("producer run is not an object")
        prepared.append((parse_time(raw.get("updated_at"), "run updated_at"), raw))
    prepared.sort(key=lambda item: item[0], reverse=True)

    normalized = []
    found_recent = False
    for updated, raw in prepared:
        conclusion = raw.get("conclusion")
        in_window = updated >= window_start
        jobs = None
        if conclusion == "success" or (conclusion == "cancelled" and in_window):
            # Older successes are read only until one qualifying success is in hand.
            if conclusion == "success" and not in_window and found_recent:
                jobs = None
            else:
                jobs = _jobs(
                    fetch,
                    repository,
                    require_int(raw.get("id"), "run id"),
                    with_annotations=conclusion == "success" and in_window,
                )
        run = _normalize(raw, jobs)
        if conclusion == "success" and jobs is not None and qualifies(run):
            # Newest first, so the first qualifying success is the heartbeat clock.
            found_recent = True
        if jobs is None and conclusion == "success" and not in_window and found_recent:
            continue
        normalized.append(run)

    return {
        "now": format_stamp(now),
        "open_non_draft_heads": _open_heads(fetch, repository),
        "runs_truncated": truncated,
        "producer_workflow": {"state": state, "path": PRODUCER_PATH},
        "runs": normalized,
    }


def collect_live(now: datetime | None = None, fetch=None) -> dict:
    repository = os.environ.get("REPOSITORY", "")
    moment = now if now is not None else datetime.now(timezone.utc)
    return collect(fetch or gh_fetch, repository, moment)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report a merge-admission producer outage.")
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.fixture is not None:
            snapshot = json.loads(args.fixture.read_text(encoding="utf-8"))
            if not isinstance(snapshot, dict):
                raise OutageDataError("fixture is not an object")
        else:
            snapshot = collect_live()
        findings = classify(snapshot)
    except (OSError, json.JSONDecodeError, OutageDataError) as error:
        print(f"merge-admission-outage: cannot establish producer state: {error}", file=sys.stderr)
        print(f"merge-admission-outage: {CHANNEL}")
        return 2
    for finding in findings:
        print(f"::error::merge-admission-outage: {finding}")
    if findings:
        print(f"merge-admission-outage: alert findings={len(findings)}")
    else:
        print("merge-admission-outage: quiet")
    print(f"merge-admission-outage: {CHANNEL}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
