#!/usr/bin/env python3
"""Behavioral tests for the merge-admission outage monitor (ADR 0260 C10 (ii), #2031)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci-gate" / "merge-admission-outage.py"
WORKFLOW = ROOT / ".github" / "workflows" / "merge-admission-outage.yml"

NOW = datetime(2026, 11, 16, 1, 0, tzinfo=timezone.utc)
NOW_TEXT = "2026-11-16T01:00:00Z"
EVALUATE = "admit / evaluate (0123456789abcdef0123456789abcdef01234567, 2129)"
UNEVALUATED = (
    "merge-admission for Verjson/.github#7 head abc "
    "was not evaluated: no AI review App token is available"
)
NOT_ADMITTED = (
    "merge-admission for Verjson/.github#7 head abc stays in progress: #7 was not admitted"
)
GAP = "An Actions-wide outage or a billing limit that stops this monitor is the accepted C1 gap."
OWNER = (
    "The delivery channel is a failing run of this workflow on the default branch, "
    "owned by the Verjson/.github repository owner."
)


def load_monitor():
    spec = importlib.util.spec_from_file_location("merge_admission_outage", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MONITOR = load_monitor()


def ago(**delta) -> str:
    return (NOW - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def one_job(name, conclusion="success", started=True, annotations=None):
    return {
        "name": name,
        "conclusion": conclusion,
        "started_at": ago(minutes=10) if started else None,
        "completed_at": ago(minutes=9) if started else None,
        "annotations": annotations or [],
    }


def success(run_id=1, updated=None, jobs=None):
    stamp = updated or ago(minutes=10)
    return {
        "id": run_id,
        "status": "completed",
        "conclusion": "success",
        "updated_at": stamp,
        "jobs": jobs if jobs is not None else [one_job(EVALUATE)],
    }


def snap(runs, heads=1, state="active", truncated=False):
    return {
        "now": NOW_TEXT,
        "open_non_draft_heads": heads,
        "runs_truncated": truncated,
        "producer_workflow": {
            "state": state,
            "path": ".github/workflows/merge-admission-caller.yml",
        },
        "runs": runs,
    }


def classify(snapshot):
    return MONITOR.classify(snapshot)


class ClassifyTests(unittest.TestCase):
    def test_a_recent_evaluate_success_is_quiet(self):
        jobs = [
            one_job("admit / plan"),
            one_job("admit / app-key-policy / validate-ai-review-key"),
            one_job(EVALUATE, annotations=[NOT_ADMITTED]),
        ]
        self.assertEqual(classify(snap([success(jobs=jobs)])), [])

    def test_displaced_pending_cancellations_do_not_alert(self):
        # The live shape on 2026-11-16 (runs 43783573012, 43783524824, 43783510768)
        # is a cancelled run with zero jobs. GitHub can also leave cancelled jobs
        # that never started. Neither is an outage: the caller keeps one pending
        # run and replaces it.
        no_jobs = {
            "id": 10,
            "status": "completed",
            "conclusion": "cancelled",
            "updated_at": ago(minutes=20),
            "jobs": [],
        }
        not_started = {
            "id": 11,
            "status": "completed",
            "conclusion": "cancelled",
            "updated_at": ago(minutes=18),
            "jobs": [
                one_job("admit / plan", conclusion="cancelled", started=False),
                one_job(EVALUATE, conclusion="cancelled", started=False),
            ],
        }
        self.assertEqual(classify(snap([no_jobs, not_started, success()])), [])

    def test_a_cancelled_run_that_started_a_job_alerts(self):
        cancelled = {
            "id": 12,
            "status": "completed",
            "conclusion": "cancelled",
            "updated_at": ago(minutes=5),
            "jobs": [
                one_job("admit / plan", conclusion="success"),
                one_job(EVALUATE, conclusion="cancelled", started=False),
            ],
        }
        self.assertEqual(
            classify(snap([cancelled, success()])),
            ["cancelled run 12 started_jobs=1"],
        )

    def test_failed_and_timed_out_runs_inside_the_window_alert(self):
        failed = {
            "id": 4,
            "status": "completed",
            "conclusion": "failure",
            "updated_at": ago(minutes=45),
        }
        timed_out = {
            "id": 5,
            "status": "completed",
            "conclusion": "timed_out",
            "updated_at": ago(minutes=2),
        }
        self.assertEqual(
            classify(snap([failed, timed_out, success()])),
            ["failed run 4 conclusion=failure", "failed run 5 conclusion=timed_out"],
        )

    def test_a_failure_older_than_the_window_does_not_alert_after_a_later_success(self):
        failed = {
            "id": 4,
            "status": "completed",
            "conclusion": "failure",
            "updated_at": ago(minutes=45, seconds=1),
        }
        self.assertEqual(classify(snap([failed, success()])), [])

    def test_was_not_evaluated_alerts_and_a_refusal_notice_does_not(self):
        warned = success(
            jobs=[
                one_job("admit / plan", annotations=[UNEVALUATED]),
                one_job(EVALUATE, annotations=[NOT_ADMITTED]),
            ]
        )
        self.assertEqual(
            classify(snap([warned])),
            ["was-not-evaluated run 1 job=admit / plan"],
        )

    def test_a_skipped_evaluation_still_counts_as_the_heartbeat(self):
        # The producer exits 0 after the warning, so the run is a qualifying success
        # and the annotation is the outage signal. The heartbeat must not also fire.
        warned = success(jobs=[one_job(EVALUATE, annotations=[UNEVALUATED])])
        self.assertEqual(
            classify(snap([warned])),
            [f"was-not-evaluated run 1 job={EVALUATE}"],
        )

    def test_heartbeat_alerts_at_forty_five_minutes_and_not_one_second_sooner(self):
        at_limit = success(run_id=2, updated=ago(minutes=45))
        inside = success(run_id=3, updated=ago(minutes=44, seconds=59))
        self.assertEqual(
            classify(snap([at_limit])),
            [
                "heartbeat gap 45m00s open_non_draft_heads=1 "
                "last_success=2026-11-16T00:15:00Z"
            ],
        )
        self.assertEqual(classify(snap([inside])), [])

    def test_no_qualifying_evaluate_success_alerts_while_a_non_draft_head_is_open(self):
        plan_only = success(jobs=[one_job("admit / plan"), one_job("admit / preevaluate")])
        self.assertEqual(
            classify(snap([plan_only])),
            ["heartbeat gap unbounded open_non_draft_heads=1 last_success=none"],
        )
        running = {
            "id": 8,
            "status": "in_progress",
            "conclusion": None,
            "updated_at": ago(minutes=1),
        }
        self.assertEqual(
            classify(snap([running])),
            ["heartbeat gap unbounded open_non_draft_heads=1 last_success=none"],
        )

    def test_no_open_non_draft_head_suppresses_the_heartbeat(self):
        self.assertEqual(classify(snap([], heads=0)), [])
        self.assertEqual(classify(snap([success(updated=ago(minutes=90))], heads=0)), [])

    def test_a_disabled_producer_schedule_alerts_with_nothing_open(self):
        self.assertEqual(
            classify(snap([success()], heads=0, state="disabled_inactivity")),
            ["producer workflow state=disabled_inactivity"],
        )
        self.assertEqual(
            classify(snap([success()], state="disabled_manually")),
            ["producer workflow state=disabled_manually"],
        )

    def test_a_truncated_run_listing_alerts_on_its_own(self):
        self.assertEqual(
            classify(snap([success()], truncated=True)),
            ["producer run listing truncated inside the alert window"],
        )

    def test_an_old_qualifying_success_is_the_heartbeat_clock(self):
        old = success(updated=ago(minutes=46))
        self.assertEqual(
            classify(snap([old])),
            [
                "heartbeat gap 46m00s open_non_draft_heads=1 "
                "last_success=2026-11-16T00:14:00Z"
            ],
        )

    def test_a_cancelled_run_without_jobs_is_a_data_error_when_the_key_is_missing(self):
        # An omitted job list is not evidence that nothing started.
        cancelled = {
            "id": 9,
            "status": "completed",
            "conclusion": "cancelled",
            "updated_at": ago(minutes=5),
        }
        with self.assertRaises(MONITOR.OutageDataError):
            classify(snap([cancelled, success()]))

    def test_a_boolean_head_count_or_a_duplicate_run_is_a_data_error(self):
        with self.assertRaises(MONITOR.OutageDataError):
            classify(snap([success()], heads=True))
        duplicated = snap([success(), success()])
        with self.assertRaises(MONITOR.OutageDataError):
            classify(duplicated)


class Api:
    def __init__(self, runs, jobs, annotations, pulls, state="active"):
        self.runs = runs
        self.jobs = jobs
        self.annotations = annotations
        self.pulls = pulls
        self.state = state
        self.calls = []

    def __call__(self, path, query):
        self.calls.append((path, query))
        if path.endswith("/merge-admission-caller.yml"):
            if query is not None:
                raise AssertionError(query)
            return {
                "id": 9,
                "state": self.state,
                "path": ".github/workflows/merge-admission-caller.yml",
            }
        if path.endswith("/runs"):
            start = (query["page"] - 1) * MONITOR.PAGE_SIZE
            return {"workflow_runs": self.runs[start:start + MONITOR.PAGE_SIZE]}
        if path.endswith("/jobs"):
            run_id = int(path.split("/runs/")[1].split("/")[0])
            batch = self.jobs.get(run_id, []) if query["page"] == 1 else []
            return {"jobs": batch}
        if "/check-runs/" in path and path.endswith("/annotations"):
            check_id = int(path.split("/check-runs/")[1].split("/")[0])
            return self.annotations.get(check_id, []) if query["page"] == 1 else []
        if path.endswith("/pulls"):
            return self.pulls if query["page"] == 1 else []
        raise AssertionError(path)


def raw_run(run_id, conclusion, updated):
    return {
        "id": run_id,
        "status": "completed",
        "conclusion": conclusion,
        "updated_at": updated,
        "html_url": f"https://github.com/Verjson/.github/actions/runs/{run_id}",
    }


def api_job(name, check_id, conclusion="success", started=True):
    return {
        "name": name,
        "conclusion": conclusion,
        "started_at": ago(minutes=10) if started else None,
        "completed_at": ago(minutes=9) if started else None,
        "check_run_url": f"https://api.github.com/repos/Verjson/.github/check-runs/{check_id}",
    }


def pull(sha, draft=False, title="SECRET-TITLE"):
    return {"draft": draft, "title": title, "head": {"sha": sha}}


class CollectTests(unittest.TestCase):
    def test_annotations_are_read_only_for_started_evaluate_jobs(self):
        api = Api(
            runs=[raw_run(1, "success", ago(minutes=10))],
            jobs={
                1: [
                    api_job("admit / plan", 101),
                    api_job("admit / app-key-policy / validate-ai-review-key", 102),
                    api_job(EVALUATE, 103),
                    api_job("admit / evaluate (skipped)", 104, conclusion="skipped", started=False),
                ]
            },
            annotations={103: []},
            pulls=[pull("abc123", draft=True), pull("def456")],
        )
        snapshot = MONITOR.collect(api, "Verjson/.github", NOW)
        annotation_calls = [path for path, _ in api.calls if path.endswith("/annotations")]
        self.assertEqual(
            annotation_calls,
            ["repos/Verjson/.github/check-runs/103/annotations"],
        )
        self.assertEqual(snapshot["open_non_draft_heads"], 1)
        self.assertEqual(classify(snapshot), [])

    def test_a_jobless_cancelled_run_from_the_api_stays_quiet(self):
        api = Api(
            runs=[raw_run(10, "cancelled", ago(minutes=20)), raw_run(1, "success", ago(minutes=10))],
            jobs={
                1: [
                    api_job("admit / plan", 101),
                    api_job("admit / app-key-policy / validate-ai-review-key", 102),
                    api_job(EVALUATE, 103),
                ]
            },
            annotations={101: [], 102: [], 103: []},
            pulls=[pull("abc123", title="SECRET-TITLE")],
        )
        snapshot = MONITOR.collect(api, "Verjson/.github", NOW)
        self.assertNotIn("SECRET-TITLE", json.dumps(snapshot))
        self.assertEqual(classify(snapshot), [])
        self.assertTrue(all(path.startswith("repos/Verjson/.github/") for path, _ in api.calls))
        self.assertNotIn("POST", json.dumps(api.calls))

    def test_api_failure_annotation_and_disabled_state_alert(self):
        api = Api(
            runs=[raw_run(5, "failure", ago(minutes=3)), raw_run(1, "success", ago(minutes=10))],
            jobs={1: [api_job(EVALUATE, 103)]},
            annotations={103: [{"message": UNEVALUATED, "annotation_level": "warning"}]},
            pulls=[pull("abc123")],
            state="disabled_manually",
        )
        self.assertEqual(
            classify(MONITOR.collect(api, "Verjson/.github", NOW)),
            [
                "producer workflow state=disabled_manually",
                f"was-not-evaluated run 1 job={EVALUATE}",
                "failed run 5 conclusion=failure",
            ],
        )

    def test_a_run_listing_that_never_leaves_the_window_is_truncated(self):
        runs = [
            raw_run(index, "success", ago(minutes=1))
            for index in range(1, MONITOR.RUN_PAGE_CAP * MONITOR.PAGE_SIZE + 1)
        ]
        jobs = {index: [api_job(EVALUATE, 1000 + index)] for index in range(1, len(runs) + 1)}
        api = Api(runs=runs, jobs=jobs, annotations={}, pulls=[pull("abc123")])
        findings = classify(MONITOR.collect(api, "Verjson/.github", NOW))
        self.assertEqual(findings, ["producer run listing truncated inside the alert window"])

    def test_live_requests_are_gets_and_errors_omit_the_body(self):
        command = MONITOR.get_command(
            "repos/Verjson/.github/actions/workflows/merge-admission-caller.yml",
            {"per_page": 100, "page": 1},
        )
        self.assertEqual(command[:4], ["gh", "api", "--method", "GET"])
        self.assertNotIn("POST", command)
        with self.assertRaises(MONITOR.OutageDataError):
            MONITOR.get_command("https://example.test/repos", None)

        def run(argv, check, capture_output, text, shell):
            self.assertEqual(shell, False)
            self.assertIn("--method", argv)
            self.assertEqual(argv[argv.index("--method") + 1], "GET")
            return subprocess.CompletedProcess(argv, 1, stdout='{"token":"secret"}', stderr="nope")

        with self.assertRaises(MONITOR.OutageDataError) as caught:
            MONITOR.gh_fetch("repos/Verjson/.github/actions/workflows/merge-admission-caller.yml", None, run=run)
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("nope", str(caught.exception))


class CliTests(unittest.TestCase):
    def run_fixture(self, snapshot):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--fixture", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_a_quiet_fixture_exits_0_and_names_the_channel(self):
        result = self.run_fixture(snap([success()]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("merge-admission-outage: quiet\n", result.stdout)
        self.assertIn(MONITOR.CHANNEL, result.stdout)
        self.assertNotIn("::error::", result.stdout)

    def test_an_alert_fixture_exits_1_without_saying_quiet(self):
        result = self.run_fixture(snap([success()], state="disabled_inactivity"))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("producer workflow state=disabled_inactivity", result.stdout)
        self.assertIn("::error::", result.stdout)
        self.assertNotIn("quiet", result.stdout)
        self.assertIn(MONITOR.CHANNEL, result.stdout)

    def test_an_unreadable_fixture_exits_2_and_still_names_the_channel(self):
        result = self.run_fixture([])
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot establish producer state", result.stderr)
        self.assertNotIn("quiet", result.stdout)
        self.assertIn(MONITOR.CHANNEL, result.stdout)


def triggers(document):
    return document.get("on", document.get(True))


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.document = yaml.safe_load(self.text)

    def test_the_monitor_names_its_channel_owner_and_accepted_gap(self):
        self.assertEqual(self.document["name"], "Merge admission outage")
        for source in (self.text, SCRIPT.read_text(encoding="utf-8")):
            self.assertIn(GAP, source)
            self.assertIn(OWNER, source)
        self.assertIn(GAP, MONITOR.__doc__)
        self.assertIn("Verjson/.github repository owner", MONITOR.__doc__)

    def test_the_monitor_only_reads_and_runs_on_the_ephemeral_lane(self):
        self.assertEqual(
            set(triggers(self.document)),
            {"schedule", "workflow_dispatch"},
        )
        minutes = [int(part) for part in triggers(self.document)["schedule"][0]["cron"].split()[0].split(",")]
        gaps = [right - left for left, right in zip(minutes, minutes[1:])]
        gaps.append(minutes[0] + 60 - minutes[-1])
        self.assertTrue(all(0 < gap <= 15 for gap in gaps), gaps)
        self.assertNotIn("secrets.", self.text)
        for value in self.document["permissions"].values():
            self.assertNotIn("write", value)
        self.assertEqual(
            set(self.document["permissions"]),
            {"actions", "checks", "contents", "pull-requests"},
        )
        job = self.document["jobs"]["alert"]
        self.assertNotIn("permissions", job)
        self.assertEqual(
            job["runs-on"],
            "${{ fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]') }}",
        )
        self.assertEqual(
            job["if"],
            "${{ github.event_name == 'schedule' || github.ref_name == github.event.repository.default_branch }}",
        )
        self.assertIs(self.document["concurrency"]["cancel-in-progress"], False)
        checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(
            checkout["uses"],
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        )
        self.assertIn(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
            self.text,
        )
        self.assertIs(checkout["with"]["persist-credentials"], False)
        self.assertEqual(
            set(checkout["with"]["sparse-checkout"].split()),
            {"scripts/ci-gate/merge-admission-outage.py"},
        )
        alert = next(step for step in job["steps"] if step.get("name") == "Alert when the merge-admission producer is in outage")
        self.assertIn("python3 -I ", alert["run"])
        self.assertNotIn("secrets.", alert["run"])


if __name__ == "__main__":
    unittest.main()
