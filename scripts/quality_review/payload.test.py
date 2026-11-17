#!/usr/bin/env python3
"""Behavioral tests for quality-review payload filter and gitleaks fail-closed (#1820)."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_review.gitleaks import (  # noqa: E402
    scan_chunks,
    sha256_file,
    verify_pinned_binary,
)
from quality_review.payload import build_payload, should_drop_path  # noqa: E402


SAMPLE = """diff --git a/src/app.py b/src/app.py
index 111..222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-old
+new
diff --git a/package-lock.json b/package-lock.json
index 111..222 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1 +1 @@
-{}
+{"lock":true}
diff --git a/.env b/.env
index 111..222 100644
--- a/.env
+++ b/.env
@@ -1 +1 @@
-SECRET=1
+SECRET=2
diff --git a/vendor/lib.c b/vendor/lib.c
index 111..222 100644
--- a/vendor/lib.c
+++ b/vendor/lib.c
@@ -1 +1 @@
-a
+b
diff --git a/logo.png b/logo.png
index 111..222 100644
--- a/logo.png
+++ b/logo.png
@@ -1 +1 @@
-bin
+bin2
"""


class PayloadFilterTest(unittest.TestCase):
    def test_drops_lockfile_env_vendored_binary(self) -> None:
        self.assertEqual(should_drop_path("package-lock.json"), "lockfile")
        self.assertEqual(should_drop_path("gradle.lockfile"), "lockfile")
        self.assertEqual(should_drop_path(".env"), "env")
        self.assertEqual(should_drop_path("vendor/x.go"), "vendored")
        self.assertEqual(should_drop_path("assets/logo.png"), "binary")
        self.assertIsNone(should_drop_path("src/app.py"))

    def test_build_payload_filters_and_keeps_source(self) -> None:
        plan = build_payload(SAMPLE, token_cap=5000, total_budget=20000)
        joined = "".join(plan.chunks)
        self.assertIn("src/app.py", joined)
        self.assertTrue(any(x.endswith(":lockfile") for x in plan.dropped))
        self.assertTrue(any(x.endswith(":env") for x in plan.dropped))
        self.assertTrue(any(":vendored" in x for x in plan.dropped))
        self.assertTrue(any(":binary" in x for x in plan.dropped))


    def test_drops_deleted_or_renamed_lockfile(self) -> None:
        deleted = """diff --git a/package-lock.json b/package-lock.json
deleted file mode 100644
index 111..000
--- a/package-lock.json
+++ /dev/null
@@ -1 +0,0 @@
-{}
"""
        plan = build_payload(deleted, token_cap=5000, total_budget=20000)
        self.assertEqual(plan.chunks, [])
        self.assertTrue(any(":lockfile" in x for x in plan.dropped))

        renamed = """diff --git a/.env b/src/safe.py
similarity index 100%
rename from .env
rename to src/safe.py
"""
        plan = build_payload(renamed, token_cap=5000, total_budget=20000)
        self.assertEqual(plan.chunks, [])
        self.assertTrue(any(":env" in x for x in plan.dropped))

    def test_over_cap_file_dropped(self) -> None:
        big = "diff --git a/big.py b/big.py\n" + ("+" + ("x" * 200) + "\n") * 50
        plan = build_payload(big, token_cap=10, total_budget=10000)
        self.assertEqual(plan.chunks, [])
        self.assertTrue(any(x.startswith("big.py:over_cap:") for x in plan.dropped))

    def test_total_budget_omits_tail(self) -> None:
        files = []
        for i in range(5):
            files.append(
                f"diff --git a/f{i}.py b/f{i}.py\n+++ b/f{i}.py\n+{'y' * 40}\n"
            )
        plan = build_payload("".join(files), token_cap=1000, total_budget=30)
        self.assertTrue(plan.chunks)
        self.assertTrue(plan.omitted_for_budget)


class GitleaksFailClosedTest(unittest.TestCase):
    def test_absent_scanner_sends_nothing(self) -> None:
        result = scan_chunks(["clean"], scanner=None)
        self.assertEqual(result.sent, [])
        self.assertEqual(result.reason, "gitleaks scanner absent")

    def test_scanner_error_sends_nothing(self) -> None:
        def boom(_chunk: str) -> list[str]:
            raise RuntimeError("boom")

        result = scan_chunks(["clean"], scanner=boom)
        self.assertEqual(result.sent, [])
        self.assertIsNotNone(result.reason)
        self.assertIn("failed", result.reason or "")

    def test_findings_drop_chunk_clean_kept(self) -> None:
        def scan(chunk: str) -> list[str]:
            return ["leak"] if "SECRET" in chunk else []

        result = scan_chunks(["ok", "SECRET=1", "also-ok"], scanner=scan)
        self.assertEqual(result.sent, ["ok", "also-ok"])
        self.assertEqual(result.dropped, ["chunk[1]:1 finding(s)"])
        self.assertIsNone(result.reason)


    def test_exit1_without_findings_report_fails_closed(self) -> None:
        from quality_review.gitleaks import _parse_findings

        self.assertIsNone(_parse_findings("not json"))
        self.assertEqual(_parse_findings("[]"), [])
        self.assertEqual(_parse_findings('[{"RuleID":"aws-key"}]'), ["aws-key"])

        def empty_report(_chunk: str) -> list[str]:
            findings = _parse_findings("[]")
            if findings is None or len(findings) == 0:
                raise RuntimeError("gitleaks exit 1 without non-empty JSON findings")
            return findings

        empty = scan_chunks(["a", "b"], scanner=empty_report)
        self.assertEqual(empty.sent, [])
        self.assertIn("failed", empty.reason or "")

        def ambiguous(_chunk: str) -> list[str]:
            # Mimic subprocess_scanner's fail-closed path for exit 1 without JSON.
            findings = _parse_findings("fatal: repository not found")
            if findings is None:
                raise RuntimeError("gitleaks exit 1 without a JSON findings report")
            return findings

        result = scan_chunks(["clean", "also"], scanner=ambiguous)
        self.assertEqual(result.sent, [])
        self.assertIn("failed", result.reason or "")

    def test_pin_digest_mismatch_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "gitleaks"
            path.write_bytes(b"not-the-binary")
            digest = sha256_file(path)
            with self.assertRaises(ValueError):
                verify_pinned_binary(path, "0" * 64)
            verify_pinned_binary(path, digest)


if __name__ == "__main__":
    unittest.main()
