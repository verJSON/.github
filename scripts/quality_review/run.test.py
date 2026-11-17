#!/usr/bin/env python3
"""Orchestration tests: green paths that report evidence not obtained (#1820)."""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_review.providers.jev import JevError  # noqa: E402
from quality_review.run import NOT_OBTAINED_PREFIX, run_review  # noqa: E402


DIFF = """diff --git a/src/ok.py b/src/ok.py
index 111..222 100644
--- a/src/ok.py
+++ b/src/ok.py
@@ -1 +1 @@
-a
+b
"""


class RunReviewTest(unittest.TestCase):
    def test_fake_provider_obtains_evidence(self) -> None:
        result = run_review(DIFF, provider_id="fake", scanner=lambda _c: [])
        self.assertTrue(result.obtained)
        self.assertTrue(result.summary.startswith("quality evidence obtained"))
        self.assertGreaterEqual(len(result.answers), 1)

    def test_absent_gitleaks_reports_not_obtained(self) -> None:
        result = run_review(DIFF, provider_id="fake", scanner=None)
        self.assertFalse(result.obtained)
        self.assertTrue(result.summary.startswith(NOT_OBTAINED_PREFIX))
        self.assertIn("gitleaks scanner absent", result.summary)

    def test_gitleaks_failure_reports_not_obtained(self) -> None:
        def boom(_chunk: str) -> list[str]:
            raise RuntimeError("scanner down")

        result = run_review(DIFF, provider_id="fake", scanner=boom)
        self.assertFalse(result.obtained)
        self.assertIn("gitleaks scanner failed", result.summary)

    def test_provider_outage_reports_not_obtained(self) -> None:
        class Boom:
            def evaluate(self, state, questions):
                raise JevError("Jev HTTP 503", status=503)

        # Patch via unknown adapter path: inject by monkeypatching build_evaluator
        import quality_review.run as run_mod

        original = run_mod.build_evaluator

        def fake_build(*args, **kwargs):
            return Boom(), "jev", "jev-1.14.0"

        run_mod.build_evaluator = fake_build  # type: ignore[assignment]
        try:
            result = run_review(DIFF, provider_id="fake", scanner=lambda _c: [])
        finally:
            run_mod.build_evaluator = original  # type: ignore[assignment]
        self.assertFalse(result.obtained)
        self.assertIn("Jev HTTP 503", result.summary)

    def test_schema_violation_reports_not_obtained(self) -> None:
        class Bad:
            def evaluate(self, state, questions):
                raise JevError("Jev schema error: probability must be in [0, 1]")

        import quality_review.run as run_mod

        original = run_mod.build_evaluator

        def fake_build(*args, **kwargs):
            return Bad(), "jev", "jev-1.14.0"

        run_mod.build_evaluator = fake_build  # type: ignore[assignment]
        try:
            result = run_review(DIFF, provider_id="fake", scanner=lambda _c: [])
        finally:
            run_mod.build_evaluator = original  # type: ignore[assignment]
        self.assertFalse(result.obtained)
        self.assertIn("schema error", result.summary)


if __name__ == "__main__":
    unittest.main()
