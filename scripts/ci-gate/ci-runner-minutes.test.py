#!/usr/bin/env python3
"""Behavior of the #1817 before/after runner-minute estimator (pure function; no network)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ci_runner_minutes import estimate  # noqa: E402


def job(job_id, start, end):
    return {"id": job_id, "started_at": start, "completed_at": end}


class EstimateTest(unittest.TestCase):
    def test_each_job_rounds_up_to_a_whole_minute(self):
        jobs = [job(1, "2026-10-23T00:00:00Z", "2026-10-23T00:00:05Z"), job(2, "2026-10-23T00:00:00Z", "2026-10-23T00:02:01Z")]
        self.assertEqual(estimate(jobs)["estimated_rounded_minutes"], 1 + 3)

    def test_duplicate_job_ids_from_paging_are_counted_once(self):
        record = job(1, "2026-10-23T00:00:00Z", "2026-10-23T00:01:00Z")
        self.assertEqual(estimate([record, dict(record)])["jobs_counted"], 1)

    def test_jobs_that_never_started_cost_nothing(self):
        self.assertEqual(estimate([job(1, None, None)])["estimated_rounded_minutes"], 0)

    def test_started_but_cancelled_jobs_with_a_finish_time_are_counted(self):
        self.assertEqual(estimate([job(1, "2026-10-23T00:00:00Z", "2026-10-23T00:00:30Z")])["jobs_counted"], 1)

    def test_skipped_jobs_with_equal_stamps_cost_nothing(self):
        skipped = {**job(1, "2026-10-23T00:00:00Z", "2026-10-23T00:00:00Z"), "conclusion": "skipped"}
        self.assertEqual(estimate([skipped])["estimated_rounded_minutes"], 0)

    def test_missing_or_inverted_timestamps_are_flagged_not_guessed(self):
        result = estimate([job(1, "2026-10-23T00:00:00Z", None), job(2, "2026-10-23T00:05:00Z", "2026-10-23T00:00:00Z")])
        self.assertEqual((result["jobs_counted"], result["jobs_flagged_bad_timestamps"], result["estimated_rounded_minutes"]), (0, 2, 0))


if __name__ == "__main__":
    unittest.main()
