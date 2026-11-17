#!/usr/bin/env python3
"""Fail-closed tests for the Renovate agent delivery claim (#2191 / ADR 0243)."""
from __future__ import annotations

import importlib.util
import re
import sys
import threading
import unittest
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

    def claim(self, store, *, repo=REPO, pr=PR, head=HEAD, max_concurrent=8, ids=None):
        return MODULE.claim_delivery(
            store,
            repository=repo,
            pull_request=pr,
            head_sha=head,
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
        self.assertEqual(store.active_count(), 1)

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
        self.assertEqual(store.active_count(), 1)
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

    def test_concurrency_bound_refuses_a_new_triple(self):
        store = self.store()
        first = self.claim(store, head=HEAD, max_concurrent=1, ids=["1" * 32])
        self.assertTrue(first.created)
        with self.assertRaisesRegex(MODULE.ClaimRefusal, "concurrency"):
            self.claim(store, head=OTHER_HEAD, max_concurrent=1, ids=["2" * 32])
        # Idempotent re-claim of the held triple still succeeds under the bound.
        again = self.claim(store, head=HEAD, max_concurrent=1, ids=["3" * 32])
        self.assertFalse(again.created)
        self.assertEqual(again.claim.claim_id, first.claim.claim_id)
        self.assertEqual(store.active_count(), 1)

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
            rf"^<!-- agent-delivery-claim:v1 repo:{re.escape(REPO)} "
            rf"pr:{PR} head:{HEAD} claim_id:{'f' * 32} -->$",
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
            max_concurrent=8,
            new_claim_id=claim_id_factory(["1" * 32]),
            worker=worker,
        )
        second = MODULE.claim_delivery(
            store,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            max_concurrent=8,
            new_claim_id=claim_id_factory(["2" * 32]),
            worker=worker,
        )
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(calls, ["1" * 32])

    def test_stub_worker_records_deferred_dependency_upgrade(self):
        worker = MODULE.StubDependencyUpgradeWorker()
        claim = MODULE.Claim(
            claim_id="9" * 32,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            status="claimed",
        )
        outcome = worker.run(claim)
        self.assertEqual(outcome.status, "deferred")
        self.assertIn("dependency-upgrade", outcome.detail)
        self.assertIn("#2191", outcome.detail)


class CommentClaimStoreTest(unittest.TestCase):
    def test_parses_and_posts_durable_comment_markers(self):
        comments: list[dict] = []

        def list_comments():
            return list(comments)

        def create_comment(body: str):
            comments.append({"id": len(comments) + 1, "body": body})
            return comments[-1]

        store = MODULE.CommentClaimStore(
            list_comments=list_comments,
            create_comment=create_comment,
        )
        claim = MODULE.Claim(
            claim_id="a" * 32,
            repository=REPO,
            pull_request=PR,
            head_sha=HEAD,
            status="claimed",
        )
        created = store.create(claim)
        self.assertEqual(created.claim_id, claim.claim_id)
        found = store.find(REPO, PR, HEAD)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.claim_id, claim.claim_id)
        self.assertEqual(store.active_count(), 1)


if __name__ == "__main__":
    unittest.main()
