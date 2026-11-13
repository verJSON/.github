#!/usr/bin/env python3
import json
import subprocess
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("review-attempt-count.py")
APP = "ai-review-authorization[bot]"
HEAD = "a" * 40


def review(author: str, body: str, *, head: str = HEAD, state: str = "COMMENTED") -> dict[str, object]:
    return {"user": {"login": author}, "body": body, "commit_id": head, "state": state}


def marker(pass_number: int, *, pr: int = 7, head: str = HEAD, run: int = 101, attempt: int = 1) -> str:
    return (
        f"<!-- ai-review-pass:v2:{pass_number}/2 pr:{pr} check:9001 head:{head} "
        f"run:{run} attempt:{attempt} provider:anthropic model:haiku -->"
    )


def explicit_marker(*, pr: int = 7, head: str = HEAD, run: int = 201, attempt: int = 1) -> str:
    return (
        f"<!-- ai-review-explicit:v1 pr:{pr} check:9001 head:{head} "
        f"run:{run} attempt:{attempt} provider:openai model:gpt-5.6-luna -->"
    )


def count(*reviews: dict[str, object], head: str = HEAD) -> int:
    result = subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--app-login",
            APP,
            "--pr-number",
            "7",
            "--head-sha",
            head,
        ],
        input=json.dumps(list(reviews)),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return int(result.stdout)


class ReviewAttemptCountTest(unittest.TestCase):
    def test_each_exact_head_app_reservation_counts(self) -> None:
        self.assertEqual(count(review(APP, marker(1)), review(APP, marker(2, run=102))), 2)

    def test_failed_or_inconclusive_reserved_pass_still_counts(self) -> None:
        self.assertEqual(count(review(APP, "AI review pass consumed before invocation.\n" + marker(1))), 1)

    def test_superseded_head_reservations_do_not_consume_current_head_allowance(self) -> None:
        old_head = "b" * 40
        self.assertEqual(
            count(
                review(APP, marker(1, head=old_head), head=old_head),
                review(APP, marker(2, head=old_head, run=102), head=old_head),
            ),
            0,
        )

    def test_explicit_reservations_count_in_cumulative_telemetry(self) -> None:
        self.assertEqual(
            count(
                review(APP, marker(1)),
                review(APP, marker(2, run=102)),
                review(APP, explicit_marker()),
            ),
            3,
        )

    def test_shared_actions_identity_cannot_forge_reservations(self) -> None:
        self.assertEqual(count(review("github-actions[bot]", marker(1))), 0)

    def test_foreign_pr_or_head_marker_does_not_count(self) -> None:
        self.assertEqual(
            count(
                review(APP, marker(1, pr=8)),
                review(APP, marker(1, head="b" * 40)),
                review(APP, explicit_marker(pr=8)),
                review(APP, explicit_marker(head="b" * 40)),
            ),
            0,
        )

    def test_approval_or_malformed_marker_does_not_count(self) -> None:
        self.assertEqual(
            count(
                review(APP, marker(1), state="APPROVED"),
                review(APP, "<!-- ai-review-pass:v2:3/2 pr:7 check:9001 head:" + HEAD + " run:102 attempt:1 provider:openai model:luna -->"),
                review(APP, "<!-- ai-review-explicit:v2 pr:7 check:9001 head:" + HEAD + " run:103 attempt:1 provider:openai model:luna -->"),
            ),
            0,
        )

    def test_duplicate_delivery_of_same_reservation_counts_once(self) -> None:
        reservation = marker(1, attempt=2)
        self.assertEqual(count(review(APP, reservation), review(APP, reservation)), 1)

        explicit = explicit_marker(attempt=2)
        self.assertEqual(count(review(APP, explicit), review(APP, explicit)), 1)

    def test_invalid_api_payload_fails_closed(self) -> None:
        result = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--app-login",
                APP,
                "--pr-number",
                "7",
                "--head-sha",
                HEAD,
            ],
            input="{}",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("review attempt accounting failed closed: reviews payload must be an array", result.stderr)


if __name__ == "__main__":
    unittest.main()
