#!/usr/bin/env python3
"""Estimate rounded runner-minutes for workflow runs, for before/after comparisons (#1817).

This is an estimate, not the invoice: each job that acquired a runner is rounded up to a
whole minute (GitHub's per-job rounding), reruns and started-then-cancelled jobs are
included, jobs that never started are excluded, and labels do not establish billing
class (public standard-hosted runs are free). Compare equal workloads over equal windows,
and report retry frequency and wall time beside the minutes.

Usage: ci_runner_minutes.py OWNER/REPO --workflow FILE --since YYYY-MM-DD --until YYYY-MM-DD [--event pull_request]
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import datetime


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def estimate(jobs: list[dict]) -> dict:
    """Pure summary of job records from the Actions jobs API (filter=all)."""
    seen: set[int] = set()
    minutes = 0
    counted = 0
    flagged = 0
    for job in jobs:
        job_id = job.get("id")
        if job_id is not None:
            if job_id in seen:
                continue
            seen.add(job_id)
        started, completed = _parse(job.get("started_at")), _parse(job.get("completed_at"))
        # A skipped job reports equal start and finish stamps but never held a runner.
        if started is None or job.get("conclusion") == "skipped" or job.get("runner_id") == 0:
            continue
        if completed is None or completed < started:
            flagged += 1
            continue
        seconds = (completed - started).total_seconds()
        minutes += max(1, math.ceil(seconds / 60))
        counted += 1
    return {"jobs_counted": counted, "jobs_flagged_bad_timestamps": flagged, "estimated_rounded_minutes": minutes}


def _gh_pages(path: str) -> list[dict]:
    out = subprocess.run(["gh", "api", "--paginate", "--slurp", path], capture_output=True, text=True, check=True).stdout
    return [page for chunk in json.loads(out) for page in ([chunk] if isinstance(chunk, dict) else chunk)]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo")
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--event")
    args = parser.parse_args(argv)
    query = f"created={args.since}..{args.until}&per_page=100" + (f"&event={args.event}" if args.event else "")
    runs = [run for page in _gh_pages(f"repos/{args.repo}/actions/workflows/{args.workflow}/runs?{query}")
            for run in page.get("workflow_runs", [])]
    jobs: list[dict] = []
    for run in runs:
        for page in _gh_pages(f"repos/{args.repo}/actions/runs/{run['id']}/jobs?filter=all&per_page=100"):
            jobs.extend(page.get("jobs", []))
    if len(runs) >= 1000:
        print("error: the runs listing is capped at 1000, so the total would be an undercount; narrow the window", file=sys.stderr)
        return 2
    summary = estimate(jobs) | {"runs": len(runs), "reruns": sum(1 for r in runs if r.get("run_attempt", 1) > 1)}
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
