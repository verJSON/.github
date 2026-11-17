#!/usr/bin/env python3
"""Fail-closed tests for the Renovate agent delivery claim (#2191 / ADR 0243)."""
from __future__ import annotations

import importlib.util
import re
import sys
import threading
import unittest
from unittest import mock
from pathlib import Path

PATH = Path(__file__).with_name("agent_delivery_claim.py")
SPEC = importlib.util.spec_from_file_location("agent_delivery_claim", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

REPO = "Verjson/demo"
PR = 42
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
TRUSTED = "github-actions[bot]"
RUN_ID = "1234567890"
WORKFLOW = ".github/workflows/agent-delivery-claim.yml"


def claim_id_factory(seq: list[str] | None = None):
    values = list(seq or ["c" * 32])
    lock = threading.Lock()

    def next_id() -> str:
        with lock:
            if not values:
                raise AssertionError("claim_id_factory exhausted")
            return values.pop(0)

    return next_id


class AgentDeliveryClaimTest(unittest.TestCase):
    def store(self, **kwargs):
        return MODULE.MemoryClaimStore(**kwargs)

    def claim(self, store, *, repo=REPO, pr=PR, head=HEAD, max_concurrent=8, ids=None,
               run_id=RUN_ID, workflow_path=WORKFLOW):
        return MODULE.claim_delivery(
            store,
            repository=repo,
            pull_request=pr,
            head_sha=head,
            run_id=run_id,
            workflow_path=workflow_path,
            max_concurrent=max_concurrent,
            new_claim_id=claim_id_factory(ids),
        )

    def test_claims_exactly_one_job_per_repo_pr_head_triple(self):
        store = self.store()
        first = self.claim(store, ids=["1" * 32])
        self.assertTrue(first.created)
        self.assertEqual(first.claim.claim_id, "1" * 32)
        self.assertEqual(first.claim.repository, REPO)
        self.assertEqual(first.claim.pull_request, PR)
        self.assertEqual(first.claim.head_sha, HEAD)
        second = self.claim(store, ids=["2" * 32])
        self.assertFalse(second.created)
        self.assertEqual(second.claim.claim_id, first.claim.claim_id)
        self.assertEqual(store.active_count(head_sha=HEAD), 1)

    def test_double_claim_race_returns_the_same_claim_id(self):
        store = self.store(race_on_create=True)
        results: list[MODULE.ClaimResult] = []
        errors: list[BaseException] = []
        shared_ids = claim_id_factory(["d" * 32, "e" * 32])

        def run():
            try:
                results.append(
                    MODULE.claim_delivery(
                        store,
                        repository=REPO,
                        pull_request=PR,
                        head_sha=HEAD,
                        run_id=RUN_ID,
                        workflow_path=WORKFLOW,
                        max_concurrent=8,
                        new_claim_id=shared_ids,
                    )
                )
            except BaseException as exc:  # noqa: BLE001 — collect any race failure
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        ids = {result.claim.claim_id for result in results}
        self.assertEqual(len(ids), 1)
        self.assertEqual(store.active_count(head_sha=HEAD), 1)
        created = sum(1 for result in results if result.created)
        self.assertEqual(created, 1)

    def test_malformed_repository_pr_and_head_fail_closed(self):
        store = self.store()
        cases = [
            ("not a repo", PR, HEAD, "repository"),
            ("Verjson/demo/extra", PR, HEAD, "repository"),
            (REPO, 0, HEAD, "pull request"),
            (REPO, -1, HEAD, "pull request"),
            (REPO, "42", HEAD, "pull request"),
            (REPO, PR, "A" * 40, "head"),
            (REPO, PR, "a" * 39, "head"),
            (REPO, PR, "g" * 40, "head"),
            (REPO, PR, "", "head"),
        ]
        for repo, pr, head, needle in cases:
            with self.subTest(repo=repo, pr=pr, head=head):
                with self.assertRaisesRegex(MODULE.ClaimRefusal, needle):
                    self.claim(store, repo=repo, pr=pr, head=head)
        self.assertEqual(store.active_count(), 0)

    def test_concurrency_bound_scopes_to_current_head_sha(self):
        store = self.store()
        first = self.claim(store, head=HEAD, max_concurrent=1, ids=["1" * 32])
        self.assertTrue(first.created)
        # synchronize to a new head must not be blocked by the stale receipt.
        second = self.claim(store, head=OTHER_HEAD, max_concurrent=1, ids=["2" * 32])
        self.assertTrue(second.created)
        self.assertEqual(store.active_count(head_sha=HEAD), 1)
        self.assertEqual(store.active_count(head_sha=OTHER_HEAD), 1)

    def test_concurrency_bound_refuses_another_claim_for_same_head(self):
        store = self.store()
        first = self.claim(
            store, repo="Verjson/a", pr=1, head=HEAD, max_concurrent=1, ids=["1" * 32]
        )
        self.assertTrue(first.created)
        with self.assertRaisesRegex(MODULE.ClaimRefusal, "concurrency"):
            self.claim(
                store, repo="Verjson/b", pr=2, head=HEAD, max_concurrent=1, ids=["2" * 32]
            )
        again = self.claim(
            store, repo="Verjson/a", pr=1, head=HEAD, max_concurrent=1, ids=["3" * 32]
        )
        self.assertFalse(again.created)
        self.assertEqual(again.claim.claim_id, first.claim.claim_id)

    def test_receipt_carries_claim_id_and_triple_without_secrets(self):
        store = self.store()
        result = self.claim(store, ids=["f" * 32])
        body = MODULE.format_claim_receipt(result.claim)
        self.assertIn(result.claim.claim_id, body)
        self.assertIn(REPO, body)
        self.assertIn(str(PR), body)
        self.assertIn(HEAD, body)
        marker = MODULE.claim_marker(result.claim)
        self.assertTrue(body.startswith(marker) or marker in body)
        self.assertRegex(
            marker,
            rf"^<!-- agent-delivery-claim:v2 repo:{re.escape(REPO)} "
            rf"pr:{PR} head:{HEAD} claim_id:{'f' * 32} "
            rf"run:{RUN_ID} workflow:{re.escape(WORKFLOW)} -->$",
        )
        lowered = body.lower()
        for forbidden in ("token", "secret", "password", "authorization:", "bearer "):
            self.assertNotIn(forbidden, lowered)

    def test_worker_interface_is_invoked_only_for_new_claims(self):
        store = self.store()
        calls: list[str] = []

        class RecordingWorker:
            def run(self, claim: MODULE.Claim) -> None:
                calls.append(claim.claim_id)

        worker = RecordingWorker()
        first = MODULE.claim_delivery(
            store,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
            max_concurrent=8,
            new_claim_id=claim_id_factory(["1" * 32]),
            worker=worker,
        )
        second = MODULE.claim_delivery(
            store,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
            max_concurrent=8,
            new_claim_id=claim_id_factory(["2" * 32]),
            worker=worker,
        )
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(calls, ["1" * 32])

    def test_worker_fail_closed_on_major_bump_without_skill_run(self):
        runs: list[str] = []

        def skill(claim):
            runs.append(claim.claim_id)
            return MODULE.WorkerOutcome(status="completed", detail="should-not-run")

        worker = MODULE.DependencyUpgradeWorker(
            bump_kind="major",
            skill_runner=skill,
        )
        claim = MODULE.Claim(
            claim_id="9" * 32,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            status="claimed",
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
        )
        outcome = worker.run(claim)
        self.assertEqual(outcome.status, "failed")
        self.assertIn("major", outcome.detail.lower())
        self.assertIn("no fragment", outcome.detail.lower())
        self.assertEqual(runs, [])

    def test_worker_fail_closed_on_breaking_bump_without_skill_run(self):
        runs: list[str] = []

        def skill(claim):
            runs.append(claim.claim_id)
            return MODULE.WorkerOutcome(status="completed", detail="should-not-run")

        worker = MODULE.DependencyUpgradeWorker(
            bump_kind="breaking",
            skill_runner=skill,
        )
        claim = MODULE.Claim(
            claim_id="8" * 32,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            status="claimed",
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
        )
        outcome = worker.run(claim)
        self.assertEqual(outcome.status, "failed")
        self.assertIn("breaking", outcome.detail.lower())
        self.assertEqual(runs, [])

    def test_worker_runs_skill_for_eligible_bump_and_records_completed(self):
        runs: list[str] = []

        def skill(claim):
            runs.append(claim.claim_id)
            return MODULE.WorkerOutcome(
                status="completed",
                detail=(
                    f"dependency-upgrade skill completed for "
                    f"{claim.repository}#{claim.pull_request}@{claim.head_sha}"
                ),
            )

        worker = MODULE.DependencyUpgradeWorker(
            bump_kind="minor",
            skill_runner=skill,
        )
        claim = MODULE.Claim(
            claim_id="7" * 32,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            status="claimed",
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
        )
        outcome = worker.run(claim)
        self.assertEqual(outcome.status, "completed")
        self.assertIn("dependency-upgrade", outcome.detail)
        self.assertEqual(runs, ["7" * 32])

    def test_default_handoff_runner_queues_without_inventing_fragment(self):
        posted: list[str] = []

        def post(body: str) -> None:
            posted.append(body)

        worker = MODULE.DependencyUpgradeWorker(
            bump_kind="patch",
            skill_runner=MODULE.AgentHandoffRunner(post_comment=post),
        )
        claim = MODULE.Claim(
            claim_id="6" * 32,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            status="claimed",
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
        )
        outcome = worker.run(claim)
        self.assertEqual(outcome.status, "queued")
        self.assertEqual(len(posted), 1)
        body = posted[0]
        self.assertIn("agent-delivery-worker:v1", body)
        self.assertIn(claim.claim_id, body)
        self.assertIn("dependency-upgrade", body)
        # Must not embed a fabricated fragment body
        self.assertNotRegex(body, r"(?m)^impact:\s")
        self.assertNotRegex(body, r"(?m)^---\s*$")

    def test_classify_renovate_title_detects_major(self):
        self.assertEqual(
            MODULE.classify_renovate_bump(
                title="chore(deps): update dependency left-pad to v2",
                labels=["major"],
            ),
            "major",
        )
        self.assertEqual(
            MODULE.classify_renovate_bump(
                title="fix(deps): update dependency foo to v3 (breaking change)",
                labels=[],
            ),
            "breaking",
        )
        self.assertEqual(
            MODULE.classify_renovate_bump(
                title="chore(deps): update dependency left-pad to v1.2.3",
                labels=[],
            ),
            "eligible",
        )

    def test_classify_major_label_wins_over_breaking_title(self):
        # Label precedence: major label beats a "breaking" title heuristic (#2215).
        self.assertEqual(
            MODULE.classify_renovate_bump(
                title="fix(deps): update dependency foo to v2 (breaking change)",
                labels=["major"],
            ),
            "major",
        )
        self.assertEqual(
            MODULE.classify_renovate_bump(
                title="chore(deps): update dependency foo to v2",
                labels=["breaking"],
            ),
            "breaking",
        )

    def test_main_fail_closes_major_before_comment_store(self):
        # #2214: classify before opening CommentClaimStore / posting.
        # Without an early classify, --store comment would require a token and
        # would call `gh api`. Fail-closed majors must exit 1 with no GH_TOKEN.
        with mock.patch.dict("os.environ", {}, clear=True):
            status = MODULE.main(
                [
                    "--repository",
                    REPO,
                    "--pull-request",
                    "7",
                    "--head-sha",
                    "a" * 40,
                    "--pr-title",
                    "chore(deps): update left-pad to v2",
                    "--pr-label",
                    "major",
                    "--store",
                    "comment",
                ]
            )
        self.assertEqual(1, status)

    def test_claim_delivery_invokes_worker_only_on_create(self):
        calls: list[str] = []

        class RecordingWorker:
            def run(self, claim):
                calls.append(claim.claim_id)
                return MODULE.WorkerOutcome(status="queued", detail="ok")

        store = self.store()
        worker = RecordingWorker()
        first = MODULE.claim_delivery(
            store,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
            max_concurrent=8,
            new_claim_id=claim_id_factory(["1" * 32]),
            worker=worker,
        )
        second = MODULE.claim_delivery(
            store,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
            max_concurrent=8,
            new_claim_id=claim_id_factory(["2" * 32]),
            worker=worker,
        )
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(calls, ["1" * 32])



    def test_new_claims_require_run_id_and_workflow_path(self):
        store = self.store()
        with self.assertRaisesRegex(MODULE.ClaimRefusal, "run id"):
            MODULE.claim_delivery(
                store,
                repository=REPO,
                pull_request=PR,
                head_sha=HEAD,
                run_id="",
                workflow_path=WORKFLOW,
                new_claim_id=claim_id_factory(["1" * 32]),
            )
        with self.assertRaisesRegex(MODULE.ClaimRefusal, "workflow path"):
            MODULE.claim_delivery(
                store,
                repository=REPO,
                pull_request=PR,
                head_sha=HEAD,
                run_id=RUN_ID,
                workflow_path="workflows/evil.yml",
                new_claim_id=claim_id_factory(["2" * 32]),
            )

    def test_parse_accepts_legacy_v1_markers_without_run_binding(self):
        body = (
            f"<!-- agent-delivery-claim:v1 repo:{REPO} "
            f"pr:{PR} head:{HEAD} claim_id:{'a' * 32} -->\n"
        )
        parsed = MODULE.parse_claim_marker(body)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertIsNone(parsed.run_id)
        self.assertIsNone(parsed.workflow_path)

class CommentClaimStoreTest(unittest.TestCase):
    def fake_store(self, *, trusted_authors=None, race_on_create: bool = False):
        comments: list[dict] = []
        lock = threading.Lock()

        def list_comments():
            with lock:
                return list(comments)

        def create_comment(body: str):
            with lock:
                comments.append(
                    {
                        "id": len(comments) + 1,
                        "body": body,
                        "user": {"login": TRUSTED},
                    }
                )
                return comments[-1]

        kwargs = {
            "list_comments": list_comments,
            "create_comment": create_comment,
            "race_on_create": race_on_create,
        }
        if trusted_authors is not None:
            kwargs["trusted_authors"] = trusted_authors
        store = MODULE.CommentClaimStore(**kwargs)
        store._test_comments = comments  # noqa: SLF001 — test fixture handle
        return store

    def test_parses_and_posts_durable_comment_markers(self):
        store = self.fake_store()
        claim = MODULE.Claim(
            claim_id="a" * 32,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            status="claimed",
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
        )
        created = store.create(claim)
        self.assertEqual(created.claim_id, claim.claim_id)
        found = store.find(REPO, PR, HEAD)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.claim_id, claim.claim_id)
        self.assertEqual(store.active_count(head_sha=HEAD), 1)

    def test_ignores_forged_markers_from_untrusted_authors(self):
        store = self.fake_store()
        forged = MODULE.Claim(
            claim_id="b" * 32,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            status="claimed",
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
        )
        store._test_comments.append(  # noqa: SLF001
            {
                "id": 99,
                "body": MODULE.format_claim_receipt(forged),
                "user": {"login": "hostile-user"},
            }
        )
        self.assertIsNone(store.find(REPO, PR, HEAD))
        self.assertEqual(store.active_count(head_sha=HEAD), 0)
        # Forged markers must not squat the triple or exhaust concurrency.
        real = MODULE.claim_delivery(
            store,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
            max_concurrent=1,
            new_claim_id=claim_id_factory(["c" * 32]),
        )
        self.assertTrue(real.created)
        self.assertEqual(real.claim.claim_id, "c" * 32)
        self.assertEqual(store.active_count(head_sha=HEAD), 1)

    def test_forged_markers_do_not_count_toward_concurrency_bound(self):
        store = self.fake_store()
        for index in range(8):
            head = f"{index:040x}"
            forged = MODULE.Claim(
                claim_id=f"{index:032x}",
                repository=REPO,
                pull_request=PR,
                head_sha=head,
                status="claimed",
                run_id=RUN_ID,
                workflow_path=WORKFLOW,
            )
            store._test_comments.append(  # noqa: SLF001
                {
                    "id": index + 1,
                    "body": MODULE.format_claim_receipt(forged),
                    "user": {"login": "attacker"},
                }
            )
        self.assertEqual(store.active_count(head_sha=HEAD), 0)
        result = MODULE.claim_delivery(
            store,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
            max_concurrent=1,
            new_claim_id=claim_id_factory(["d" * 32]),
        )
        self.assertTrue(result.created)

    def test_trusted_author_allowlist_is_configurable(self):
        store = self.fake_store(trusted_authors=frozenset({"delivery-bot[bot]"}))
        claim = MODULE.Claim(
            claim_id="e" * 32,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            status="claimed",
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
        )
        store._test_comments.append(  # noqa: SLF001
            {
                "id": 1,
                "body": MODULE.format_claim_receipt(claim),
                "user": {"login": TRUSTED},
            }
        )
        self.assertIsNone(store.find(REPO, PR, HEAD))
        store._test_comments.append(  # noqa: SLF001
            {
                "id": 2,
                "body": MODULE.format_claim_receipt(claim),
                "user": {"login": "delivery-bot[bot]"},
            }
        )
        found = store.find(REPO, PR, HEAD)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.claim_id, "e" * 32)

    def test_comment_store_double_claim_race_returns_one_claim_id(self):
        store = self.fake_store(race_on_create=True)
        results: list[MODULE.ClaimResult] = []
        errors: list[BaseException] = []
        shared_ids = claim_id_factory(["1" * 32, "2" * 32])

        def run():
            try:
                results.append(
                    MODULE.claim_delivery(
                        store,
                        repository=REPO,
                        pull_request=PR,
                        head_sha=HEAD,
                        run_id=RUN_ID,
                        workflow_path=WORKFLOW,
                        max_concurrent=8,
                        new_claim_id=shared_ids,
                    )
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual({result.claim.claim_id for result in results}, {"1" * 32})
        self.assertEqual(sum(1 for result in results if result.created), 1)
        self.assertEqual(len(store._test_comments), 1)  # noqa: SLF001

    def test_stale_head_markers_do_not_block_synchronize(self):
        store = self.fake_store()
        first = MODULE.claim_delivery(
            store,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
            max_concurrent=1,
            new_claim_id=claim_id_factory(["1" * 32]),
        )
        self.assertTrue(first.created)
        second = MODULE.claim_delivery(
            store,
            repository=REPO,
            pull_request=PR,
            head_sha=OTHER_HEAD,
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
            max_concurrent=1,
            new_claim_id=claim_id_factory(["2" * 32]),
        )
        self.assertTrue(second.created)


if __name__ == "__main__":
    unittest.main()
