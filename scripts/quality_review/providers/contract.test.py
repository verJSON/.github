#!/usr/bin/env python3
"""Shared provider contract suite (#1820).

Every adapter must answer probability and level questions with validated
scalars. The fake provider proves a second provider can satisfy the port
without workflow or rubric changes. The jev adapter is exercised against a
local stub HTTP server (never the live TypeSafe endpoint in CI).
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_review.config import (  # noqa: E402
    load_providers,
    load_rubric,
    rubric_questions,
)
from quality_review.port import validate_answer  # noqa: E402
from quality_review.providers.fake import FakeEvaluator  # noqa: E402
from quality_review.providers.jev import JevEvaluator  # noqa: E402


QUESTIONS = (
    {"id": "correctness", "kind": "probability", "prompt": "Is the change correct?"},
    {
        "id": "security",
        "kind": "level",
        "prompt": "Security impact?",
        "levels": ["low", "medium", "high"],
    },
)


class _StubJevHandler(BaseHTTPRequestHandler):
    responses: dict[str, Any] = {}
    last_request: dict[str, Any] | None = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        del format, args

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return
        auth = self.headers.get("Authorization", "")
        type(self).last_request = {"path": self.path, "auth": auth, "payload": payload}
        mode = type(self).responses.get("mode", "ok")
        if mode == "429":
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()
            return
        if mode == "bad_schema":
            body = json.dumps({"model": "stub", "answers": {"correctness": {"type": "noul", "noul": 2.0}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        answers = {
            "correctness": {"type": "noul", "noul": 0.7},
            "security": {
                "type": "score",
                "score": 4.5,
                "legend": {},
                "probabilities": {},
                "confidence": 0.9,
            },
        }
        body = json.dumps(
            {
                "model": payload.get("model", "stub"),
                "answers": answers,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeProviderContractTest(unittest.TestCase):
    def test_fake_answers_both_kinds(self) -> None:
        answers = FakeEvaluator().evaluate({"diff": "demo"}, QUESTIONS)
        self.assertEqual(len(answers), 2)
        self.assertEqual(answers[0]["kind"], "probability")
        self.assertEqual(answers[1]["kind"], "level")
        self.assertEqual(answers[0]["probability"], 0.5)
        self.assertEqual(answers[1]["level"], "medium")

    def test_validate_answer_rejects_out_of_range_probability(self) -> None:
        with self.assertRaises(ValueError):
            validate_answer(QUESTIONS[0], {"probability": 1.5})

    def test_validate_answer_rejects_missing_level(self) -> None:
        with self.assertRaises(ValueError):
            validate_answer(QUESTIONS[1], {"confidence": 0.5})

    def test_fake_satisfies_rubric_questions(self) -> None:
        questions = rubric_questions()
        answers = FakeEvaluator().evaluate({"diff": "demo"}, questions)
        self.assertEqual(len(answers), len(questions))


class ConfigContractTest(unittest.TestCase):
    def test_providers_manifest_loads(self) -> None:
        data = load_providers()
        self.assertEqual(data["default_provider"], "jev")
        self.assertIn("jev", data["providers"])
        self.assertIn("fake", data["providers"])
        self.assertFalse(str(data["providers"]["jev"]["default_model"]).endswith("-latest"))

    def test_rubric_manifest_loads(self) -> None:
        data = load_rubric()
        ids = [d["id"] for d in data["dimensions"]]
        self.assertEqual(ids, ["correctness", "security", "reliability", "compatibility", "test_gap"])


class JevStubContractTest(unittest.TestCase):
    def setUp(self) -> None:
        _StubJevHandler.responses = {"mode": "ok"}
        _StubJevHandler.last_request = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _StubJevHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.endpoint = f"http://{host}:{port}/v1/systemone"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_jev_against_stub_answers_both_kinds(self) -> None:
        evaluator = JevEvaluator(api_key="test-key", endpoint=self.endpoint, model="jev-1.14.0", max_retries=0)
        answers = evaluator.evaluate({"diff": "demo"}, QUESTIONS)
        self.assertEqual(answers[0]["probability"], 0.7)
        self.assertEqual(answers[1]["kind"], "level")
        self.assertIn(answers[1]["level"], QUESTIONS[1]["levels"])
        self.assertEqual(answers[1]["confidence"], 0.9)
        req = _StubJevHandler.last_request
        assert req is not None
        self.assertEqual(req["auth"], "Bearer test-key")
        self.assertEqual(req["payload"]["model"], "jev-1.14.0")
        self.assertEqual(req["payload"]["questions"]["correctness"]["type"], "noul")
        self.assertEqual(req["payload"]["questions"]["security"]["type"], "score")

    def test_jev_refuses_latest_model(self) -> None:
        with self.assertRaises(Exception):
            JevEvaluator(api_key="k", endpoint=self.endpoint, model="jev-latest")

    def test_jev_rejects_out_of_range_noul_from_stub(self) -> None:
        _StubJevHandler.responses = {"mode": "bad_schema"}
        evaluator = JevEvaluator(api_key="test-key", endpoint=self.endpoint, model="jev-1.14.0", max_retries=0)
        with self.assertRaises(Exception):
            evaluator.evaluate({"diff": "demo"}, QUESTIONS)


if __name__ == "__main__":
    unittest.main()
