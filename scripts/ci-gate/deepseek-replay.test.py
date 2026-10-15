#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).with_name("prepare-deepseek-replay.py")
spec = importlib.util.spec_from_file_location("prepare_deepseek_replay", SCRIPT)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)
verdict_spec = importlib.util.spec_from_file_location("review_verdict", Path(__file__).with_name("review-verdict.py"))
review_verdict = importlib.util.module_from_spec(verdict_spec)
verdict_spec.loader.exec_module(review_verdict)
HEAD = "a" * 40
MODEL = "deepseek-v4-pro"


def bundle() -> dict:
    return {
        "schema": 1, "purpose": "diagnostic-replay", "authorizing": False,
        "cacheable": False, "transport": "completed",
        "provenance": {
            "reviewed_head": HEAD, "authorization_check_id": "9001",
            "repository": "Verjson/.github", "pr_number": 7, "review_pass": 1, "sensitive": False,
            "trusted_review_sha": "f" * 40,
            "review_policy_sha256": "b" * 64, "prompt_sha256": "c" * 64,
            "pr_metadata_sha256": "d" * 64, "pr_diff_sha256": "e" * 64,
        },
        "response": {
            "model": MODEL,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cache_hit_tokens": 2, "cache_miss_tokens": 8},
            "verdict": {"blocking": True, "summary": "bad evidence", "review_first": [], "findings": [{
                "location": "app.py:1", "reason": "broken", "failure_scenario": "fails", "evidence": "wrong source"
            }], "followups": []},
            "bounds": {"input_token_bound": 100, "max_output_tokens": 65536, "reported_cost_usd": "0.01"},
        },
    }


def extraction_bundle() -> dict:
    candidate = bundle()
    candidate["purpose"] = "extraction-diagnostic"
    candidate.pop("response")
    candidate["model"] = MODEL
    candidate["failure"] = {
        "stage": "completed_response_extraction", "kind": "json_decode",
        "content_bytes": 42, "content_sha256": "1" * 64,
        "json_error": {"line": 1, "column": 43, "position": 42},
    }
    return candidate


class DeepSeekReplayTest(unittest.TestCase):
    def run_prepare(self, transport="success", usable="false", publication="success", diagnostic=None):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        source, output = root / "source.json", root / "output.json"
        source.write_text(json.dumps(bundle()), encoding="utf-8")
        diagnostic = diagnostic or json.dumps({
            "path": "findings[0].evidence", "expected": "exact-head source fragment", "observed": "fragment mismatch"
        })
        available = replay.prepare(
            source, output, transport, usable, publication, diagnostic, HEAD, "9001", MODEL,
            "Verjson/.github", 7, 1, False,
            "f" * 40,
        )
        return temp, output, available

    def test_source_evidence_rejection_is_staged_as_non_authorizing_replay(self):
        reviewed_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        candidate = bundle()
        candidate["provenance"]["reviewed_head"] = reviewed_head
        verdict = candidate["response"]["verdict"]
        verdict["findings"][0]["location"] = ".github/workflows/ai-review-merge.yml:1"
        result = review_verdict.confirm_output(json.dumps(verdict), False, str(ROOT), reviewed_head)
        self.assertFalse(result["usable"])
        self.assertEqual(result["diagnostic"]["observed"], "fragment mismatch")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        source, output = root / "source.json", root / "output.json"
        source.write_text(json.dumps(candidate), encoding="utf-8")
        available = replay.prepare(
            source, output, "success", "false", "success",
            json.dumps(result["diagnostic"]), reviewed_head, "9001", MODEL,
            "Verjson/.github", 7, 1, False,
            "f" * 40,
        )
        staged = json.loads(output.read_text())
        self.assertTrue(available)
        self.assertEqual(staged["failure"]["phase"], "canonical-validation")
        self.assertEqual(staged["failure"]["diagnostic"]["observed"], "fragment mismatch")
        self.assertFalse(staged["authorizing"])
        self.assertFalse(staged["cacheable"])
        self.assertEqual(staged["provenance"]["reviewed_head"], reviewed_head)
        replayed = review_verdict.confirm_output(
            json.dumps(staged["response"]["verdict"]),
            staged["provenance"]["sensitive"],
            str(ROOT),
            staged["provenance"]["reviewed_head"],
        )
        self.assertFalse(replayed["usable"])
        self.assertEqual(replayed["diagnostic"], staged["failure"]["diagnostic"])

    def test_success_and_incomplete_transport_never_stage_artifacts(self):
        temp, output, available = self.run_prepare(usable="true", publication="success")
        self.addCleanup(temp.cleanup)
        self.assertFalse(available)
        self.assertFalse(output.exists())

        temp, output, available = self.run_prepare(transport="failure")
        self.addCleanup(temp.cleanup)
        self.assertFalse(available)
        self.assertFalse(output.exists())

    def test_publication_failure_stages_completed_usable_verdict(self):
        temp, output, available = self.run_prepare(usable="true", publication="failure")
        self.addCleanup(temp.cleanup)
        self.assertTrue(available)
        self.assertEqual(json.loads(output.read_text())["failure"], {
            "phase": "publication", "diagnostic": "publication step failed"
        })

    def test_completed_extraction_failure_stages_bounded_non_authorizing_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source.json", root / "output.json"
            source.write_text(json.dumps(extraction_bundle()), encoding="utf-8")
            available = replay.prepare(
                source, output, "failure", "false", "success", "{}", HEAD, "9001", MODEL,
                "Verjson/.github", 7, 1, False, "f" * 40,
            )

            self.assertTrue(available)
            staged = json.loads(output.read_text())
            self.assertEqual(staged["purpose"], "extraction-diagnostic")
            self.assertEqual(staged["failure"]["kind"], "json_decode")
            self.assertFalse(staged["authorizing"])
            self.assertFalse(staged["cacheable"])
            self.assertNotIn("response", staged)

    def test_diagnostic_artifact_size_is_independent_of_bounded_response_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source.json", root / "output.json"
            candidate = extraction_bundle()
            candidate["failure"]["content_bytes"] = 2 * 1024 * 1024
            source.write_text(json.dumps(candidate), encoding="utf-8")

            available = replay.prepare(
                source, output, "failure", "false", "success", "{}", HEAD, "9001", MODEL,
                "Verjson/.github", 7, 1, False, "f" * 40,
            )

            self.assertTrue(available)
            self.assertLess(output.stat().st_size, 16 * 1024)

    def test_staged_extraction_diagnostic_limit_counts_a_normalized_newline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source.json", root / "output.json"
            raw = json.dumps(extraction_bundle(), separators=(",", ":")).encode("utf-8")
            source.write_bytes(raw)

            with patch.object(replay, "MAX_DIAGNOSTIC_BYTES", len(raw)), self.assertRaisesRegex(
                ValueError, "non-authorizing provenance contract",
            ):
                replay.prepare(
                    source, output, "failure", "false", "success", "{}", HEAD, "9001", MODEL,
                    "Verjson/.github", 7, 1, False, "f" * 40,
                )

            with patch.object(replay, "MAX_DIAGNOSTIC_BYTES", len(raw) + 1):
                available = replay.prepare(
                    source, output, "failure", "false", "success", "{}", HEAD, "9001", MODEL,
                    "Verjson/.github", 7, 1, False, "f" * 40,
                )
            self.assertTrue(available)
            self.assertEqual(output.stat().st_size, len(raw) + 1)
            self.assertTrue(output.read_bytes().endswith(b"\n"))

    def test_two_passes_stage_distinct_extraction_causes_without_collapsing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = extraction_bundle()
            fallback = extraction_bundle()
            fallback["model"] = "deepseek-v4-flash"
            fallback["provenance"]["review_pass"] = 2
            fallback["failure"] = {
                "stage": "completed_response_extraction", "kind": "usage_envelope",
                "content_bytes": 60, "content_sha256": "2" * 64,
            }
            staged = []
            for pass_number, model, candidate in (
                (1, MODEL, primary), (2, "deepseek-v4-flash", fallback),
            ):
                source, output = root / f"source-{pass_number}.json", root / f"output-{pass_number}.json"
                source.write_text(json.dumps(candidate), encoding="utf-8")
                self.assertTrue(replay.prepare(
                    source, output, "failure", "false", "success", "{}", HEAD, "9001", model,
                    "Verjson/.github", 7, pass_number, False, "f" * 40,
                ))
                staged.append(json.loads(output.read_text()))

            self.assertEqual(
                [candidate["failure"]["kind"] for candidate in staged],
                ["json_decode", "usage_envelope"],
            )

    def test_provenance_mutation_and_sensitive_unknown_fields_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source", root / "output"
            candidate = bundle()
            candidate["provenance"]["prompt_sha256"] = "sensitive prompt"
            source.write_text(json.dumps(candidate), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "provenance"):
                replay.prepare(source, output, "success", "false", "success", "{}", HEAD, "9001", MODEL,
                               "Verjson/.github", 7, 1, False, "f" * 40)

    def test_authorization_check_substitution_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source", root / "output"
            source.write_text(json.dumps(bundle()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "provenance"):
                replay.prepare(source, output, "success", "false", "success", "{}", HEAD, "9002", MODEL,
                               "Verjson/.github", 7, 1, False, "f" * 40)

    def test_trusted_review_revision_substitution_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source", root / "output"
            source.write_text(json.dumps(bundle()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "provenance"):
                replay.prepare(source, output, "success", "false", "success", "{}", HEAD, "9001", MODEL,
                               "Verjson/.github", 7, 1, False, "e" * 40)

    def test_workflow_upload_is_failure_only_best_effort_and_short_lived(self):
        workflow = (ROOT / ".github/workflows/ai-review-merge.yml").read_text()
        self.assertEqual(workflow.count("actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2"), 3)
        for step_id in ("replay_primary", "replay_fallback"):
            self.assertIn(f"if: always() && steps.{step_id}.outputs.available == 'true'", workflow)
        self.assertEqual(workflow.count("retention-days: 1"), 2)
        self.assertIn("PUBLICATION: ${{ steps.submit.conclusion }}", workflow)
        self.assertEqual(workflow.count('REVIEW_PASS: "1"'), 2)
        self.assertEqual(workflow.count('REVIEW_PASS: "2"'), 2)
        self.assertEqual(workflow.count("SENSITIVE: ${{ needs.preflight.outputs.sensitive }}"), 6)
        for argument in (
            "--expected-check-id", "--expected-repository", "--expected-pr",
            "--expected-pass", "--expected-sensitive", "--expected-trusted-review-sha",
        ):
            self.assertEqual(workflow.count(argument), 2)
        self.assertNotIn("download-artifact", workflow)
        for step_id in ("deepseek_primary", "deepseek_fallback"):
            self.assertIn(f"TRANSPORT: ${{{{ steps.{step_id}.outcome }}}}", workflow)
            self.assertNotIn(f"TRANSPORT: ${{{{ steps.{step_id}.conclusion }}}}", workflow)
        self.assertIn(
            "PRIMARY_EXTRACTION_DIAGNOSTIC: ${{ steps.deepseek_primary.outputs.extraction_diagnostic }}",
            workflow,
        )
        self.assertIn(
            "FALLBACK_EXTRACTION_DIAGNOSTIC: ${{ steps.deepseek_fallback.outputs.extraction_diagnostic }}",
            workflow,
        )
        self.assertNotIn(
            "steps.deepseek_fallback.outputs.extraction_diagnostic || "
            "steps.deepseek_primary.outputs.extraction_diagnostic",
            workflow,
        )

    def test_documented_replay_uses_separate_trusted_and_target_checkouts(self):
        decision = (ROOT / "docs/decisions/0090-human-first-opt-in-ai-review/README.md").read_text()
        self.assertIn("provenance.trusted_review_sha", decision)
        self.assertIn('python3 "$trusted_checkout/scripts/ci-gate/review-verdict.py"', decision)
        self.assertIn("REVIEW_REPOSITORY=\"$target_checkout\"", decision)


if __name__ == "__main__":
    unittest.main()
