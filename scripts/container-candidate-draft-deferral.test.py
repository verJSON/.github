#!/usr/bin/env python3
"""Generated container-candidate callers defer drafts (#1944, ADR 0261).

A draft holds no runner for the multi-architecture candidate validation, a
ready pull request runs it, and a new commit is the only event that cancels a
running validation. Push publication keeps its own path.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
GENERATOR_SOURCES = (
    "gen-container-candidate.sh",
    "container_private_dependencies.py",
    "container_dependency_transfer.py",
    "container_candidate_disk_usage.sh",
    "container_candidate_retry.py",
    "container_cosign_provenance.py",
)
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts/ci-gate/conformance"))
from expressions import Evaluator  # noqa: E402


def generate_caller(config_fixture="single.json"):
    """The caller the generator emits at a fixture contract commit."""
    with tempfile.TemporaryDirectory() as directory:
        contract = Path(directory, "contract")
        (contract / "scripts").mkdir(parents=True)
        for name in GENERATOR_SOURCES:
            shutil.copy2(ROOT / "scripts" / name, contract / "scripts" / name)
        git = ["git", "-C", str(contract), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid"]
        subprocess.run(["git", "init", "-q", str(contract)], check=True)
        subprocess.run([*git, "add", "scripts"], check=True)
        subprocess.run([*git, "commit", "-qm", "fixture"], check=True)
        ref = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        consumer = Path(directory, "consumer")
        consumer.mkdir()
        shutil.copy2(ROOT / "scripts/fixtures/container-candidate" / config_fixture, consumer / "container-candidate.json")
        rendered = subprocess.run(
            ["bash", str(contract / "scripts/gen-container-candidate.sh"), "workflow", ref, "container-candidate.json"],
            cwd=consumer, check=True, capture_output=True, text=True,
        ).stdout
    return yaml.safe_load(rendered)


CALLER = generate_caller()


def scheduled_jobs(*, event, draft=None, ref="refs/pull/7/merge"):
    """The caller jobs GitHub starts for one event; a push carries no draft flag."""
    bindings = {
        "github.event_name": event,
        "github.event.pull_request.draft": draft,
        "github.ref": ref,
    }
    return {name for name, job in CALLER["jobs"].items() if Evaluator(bindings).evaluate(job.get("if"))}


class DraftDeferralTests(unittest.TestCase):
    def test_a_draft_pull_request_schedules_no_job(self):
        self.assertEqual(
            scheduled_jobs(event="pull_request", draft=True),
            set(),
            "a draft must hold no runner and report no nested validation context",
        )

    def test_a_ready_pull_request_schedules_validation_and_never_publication(self):
        self.assertEqual(scheduled_jobs(event="pull_request", draft=False), {"validate"})

    def test_a_push_to_main_carries_no_draft_flag_and_schedules_only_publication(self):
        self.assertEqual(
            scheduled_jobs(event="push", ref="refs/heads/main"),
            {"publish"},
            "a push is never a draft, so a bare draft guard would rebuild main as pull-request validation",
        )

    def test_a_pull_request_caller_reruns_when_a_draft_becomes_ready(self):
        self.assertEqual(
            CALLER[True]["pull_request"],
            {"types": ["opened", "synchronize", "reopened", "ready_for_review"]},
            "without ready_for_review nothing re-runs a deferred draft once it is ready",
        )


def cancels(event, action=None):
    cancel = CALLER.get("concurrency", {}).get("cancel-in-progress", False)
    return Evaluator({"github.event_name": event, "github.event.action": action}).evaluate(cancel)


class CancellationTests(unittest.TestCase):
    def test_a_new_commit_cancels_the_run_it_supersedes(self):
        self.assertTrue(
            cancels("pull_request", "synchronize"),
            "a superseded head's multi-architecture validation would keep its runners to the end",
        )

    def test_a_same_commit_transition_waits_for_the_running_run(self):
        for action in ("opened", "reopened", "ready_for_review"):
            with self.subTest(action=action):
                self.assertFalse(
                    cancels("pull_request", action),
                    "a same-head event must not cancel a running ready validation",
                )

    def test_a_push_to_main_never_cancels_publication(self):
        self.assertFalse(cancels("push"), "a later main push would cancel an earlier commit's publication")


if __name__ == "__main__":
    unittest.main()
