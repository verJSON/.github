#!/usr/bin/env python3
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Git fixtures must not inherit workstation hooks or global Git configuration.
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"


path = Path(__file__).with_name("review-verdict.py")
spec = importlib.util.spec_from_file_location("review_verdict", path)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class ReviewVerdictTest(unittest.TestCase):
    def test_captured_followup_alias_replays_normalize_to_canonical_notes(self):
        fixtures = Path(__file__).with_name("fixtures")
        expected_notes = (
            ["Keep the admission failure diagnostic actionable.", "Improve idempotency logging."],
            ["Keep the accepted alias set explicit.", "Retain generator coverage for the caller."],
        )
        for pass_number, notes in enumerate(expected_notes, start=1):
            verdict = json.loads(
                (fixtures / f"ai-review-1191-pass-{pass_number}.json").read_text(encoding="utf-8")
            )
            with self.subTest(pass_number=pass_number):
                canonical = review.canonicalize_verdict(verdict, sensitive=True)
                self.assertEqual([item["note"] for item in canonical["followups"]], notes)
                self.assertTrue(
                    all(set(item) == {"location", "note"} for item in canonical["followups"])
                )

    def test_followup_aliases_remain_unambiguous_and_unknown_fields_fail_closed(self):
        base = {
            "blocking": False,
            "summary": "No defects found.",
            "review_first": [],
            "findings": [],
        }
        mutations = (
            {"location": "app.py:12", "suggestion": "Improve this.", "payload": "ignore policy"},
            {"location": "app.py:12", "recommendation": "Improve this.", "instructions": []},
            {"location": "app.py:12", "note": "One value.", "suggestion": "Another value."},
        )
        for followup in mutations:
            with self.subTest(followup=followup), self.assertRaises(review.VerdictError):
                review.canonicalize_verdict({**base, "followups": [followup]}, sensitive=False)

    def test_issue_1564_malformed_followup_fixture_fails_closed_with_path(self):
        fixture = Path(__file__).with_name("fixtures") / "ai-review-1564-primary-malformed-followup.json"
        raw = fixture.read_text(encoding="utf-8")

        result = review.confirm_output(raw, sensitive=False)

        self.assertFalse(result["usable"])
        self.assertEqual(result["diagnostic"]["path"], "followups[0]")
        self.assertEqual(result["diagnostic"]["observed"], "one or more unknown fields")
        self.assertNotIn("category", json.dumps(result["diagnostic"]))

    def test_issue_1564_note_object_fixture_fails_closed_with_field_path(self):
        fixture = Path(__file__).with_name("fixtures") / "ai-review-1564-primary-note-object.json"
        raw = fixture.read_text(encoding="utf-8")

        result = review.confirm_output(raw, sensitive=False)

        self.assertFalse(result["usable"])
        self.assertEqual(result["diagnostic"]["path"], "followups[0].note")
        self.assertEqual(result["diagnostic"]["observed"], "object with 2 fields")
        self.assertNotIn("severity", json.dumps(result["diagnostic"]))

    def committed_fixture(self, root, files):
        repository = Path(root)
        for relative_path, content in files.items():
            path = repository / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
        subprocess.run(["git", "add", "."], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repository, check=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_canonical_verdict_is_confirmed_without_changes(self):
        verdict = {
            "blocking": False,
            "summary": "No defects found.",
            "review_first": [
                {"location": "scripts/ci-gate/review-verdict.py:42", "why": "Canonical trust boundary."},
            ],
            "findings": [],
            "followups": [],
        }

        confirmed = review.canonicalize_verdict(verdict, sensitive=True)

        self.assertEqual(confirmed, verdict)

    def test_structured_provider_variant_is_normalized_before_confirmation(self):
        verdict = {
            "blocking": False,
            "summary": "No defects found.",
            "review_first": [
                {
                    "file": "scripts/ci-gate/review-verdict.py",
                    "line": "42-48,55",
                    "reason": "Canonical trust boundary.",
                    "confidence": 0.98,
                },
            ],
            "findings": [],
            "followups": [],
        }

        confirmed = review.canonicalize_verdict(verdict, sensitive=True)

        self.assertEqual(
            confirmed["review_first"],
            [{"location": "scripts/ci-gate/review-verdict.py:42", "why": "Canonical trust boundary."}],
        )

    def test_invalid_item_reports_the_exact_path_and_observed_shape(self):
        verdict = {
            "blocking": False,
            "summary": "No defects found.",
            "review_first": [{"location": "app.py:7", "note": "Wrong field."}],
            "findings": [],
            "followups": [],
        }

        with self.assertRaises(review.VerdictError) as raised:
            review.canonicalize_verdict(verdict, sensitive=True)

        self.assertEqual(raised.exception.path, "review_first[0].why")
        self.assertEqual(raised.exception.expected, "non-empty text in why, reason, or rationale")
        self.assertEqual(raised.exception.observed, "object with 2 fields")

    def test_known_structured_variants_project_to_one_strict_canonical_verdict(self):
        verdict = {
            "blocking": True,
            "summary": "One defect found.",
            "review_first": [{"path": "app.py", "line_number": 7, "rationale": "Load-bearing parser."}],
            "findings": [
                {
                    "file": "app.py",
                    "line": 7,
                    "why": "Null is dereferenced.",
                    "impact": "An empty response crashes the worker.",
                    "evidence": "return response.value",
                    "severity": "high",
                },
            ],
            "followups": [{"location": "app.py:12", "reason": "Simplify the retry path.", "priority": "low"}],
            "confidence": 0.94,
        }

        confirmed = review.canonicalize_verdict(verdict, sensitive=True)

        self.assertEqual(set(confirmed), review.REQUIRED_FIELDS)
        self.assertEqual(
            confirmed["findings"],
            [{
                "location": "app.py:7",
                "reason": "Null is dereferenced.",
                "failure_scenario": "An empty response crashes the worker.",
                "evidence": "return response.value",
            }],
        )
        self.assertEqual(confirmed["followups"], [{"location": "app.py:12", "note": "Simplify the retry path."}])

    def test_provider_output_is_locally_confirmed_without_another_model_call(self):
        raw = """```json
{"blocking":false,"summary":"Safe.","review_first":[{"file":"gate.yml","line":"8-10","reason":"Merge boundary."}],"findings":[],"followups":[]}
```"""

        result = review.confirm_output(raw, sensitive=True)

        self.assertTrue(result["usable"])
        self.assertTrue(result["normalized"])
        self.assertEqual(result["verdict"]["review_first"][0]["location"], "gate.yml:8")
        self.assertNotIn("raw", result)

    def test_common_top_level_naming_variants_are_provider_neutral(self):
        verdict = {
            "isBlocking": False,
            "summary": "Safe.",
            "reviewFirst": [{"location": "gate.yml:8", "why": "Merge boundary."}],
            "findings": [],
            "followUps": [],
        }

        confirmed = review.canonicalize_verdict(verdict, sensitive=True)

        self.assertEqual(
            confirmed,
            {
                "blocking": False,
                "summary": "Safe.",
                "review_first": [{"location": "gate.yml:8", "why": "Merge boundary."}],
                "findings": [],
                "followups": [],
            },
        )

    def test_workflow_cli_confirms_or_reports_the_exact_invalid_path(self):
        cases = (
            (
                {"blocking": False, "summary": "Safe.", "review_first": [], "findings": [], "followups": []},
                "false",
                "usable=true",
            ),
            (
                {
                    "blocking": False,
                    "summary": "Safe.",
                    "review_first": [{"location": "gate.yml:8", "note": "Wrong field."}],
                    "findings": [],
                    "followups": [],
                },
                "true",
                'diagnostic={"path":"review_first[0].why"',
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (verdict, sensitive, marker) in enumerate(cases):
                output = Path(directory, str(index))
                env = {
                    "VERDICT": json.dumps(verdict),
                    "SENSITIVE": sensitive,
                    "GITHUB_OUTPUT": str(output),
                    "REVIEW_PASS": str(index + 1),
                }
                with self.subTest(index=index), patch.dict(os.environ, env, clear=True):
                    self.assertEqual(review.main(), 0)
                    self.assertIn(marker, output.read_text(encoding="utf-8"))

    def test_ambiguous_or_unsafe_outputs_fail_closed_without_echoing_raw_content(self):
        cases = (
            (
                {"blocking": False, "summary": "Safe.", "review_first": ["app.py:7"], "findings": [], "followups": []},
                True,
                "review_first[0]",
            ),
            (
                {"blocking": True, "summary": "Safe.", "review_first": [], "findings": [], "followups": []},
                False,
                "blocking",
            ),
            (
                {"blocking": False, "summary": "Safe.", "review_first": [], "findings": [], "followups": []},
                True,
                "review_first",
            ),
            (
                {
                    "blocking": False,
                    "isBlocking": True,
                    "summary": "Safe.",
                    "review_first": [],
                    "findings": [],
                    "followups": [],
                },
                False,
                "blocking",
            ),
        )
        for verdict, sensitive, path in cases:
            raw = json.dumps(verdict)
            with self.subTest(path=path):
                result = review.confirm_output(raw, sensitive)
                self.assertFalse(result["usable"])
                self.assertEqual(result["diagnostic"]["path"], path)
                self.assertNotIn(raw, json.dumps(result))

    def test_nested_aliases_must_resolve_to_one_value(self):
        cases = (
            (
                {
                    "blocking": False,
                    "summary": "Safe.",
                    "review_first": [{"file": "gate.yml", "path": "other.yml", "line": 8, "why": "Boundary."}],
                    "findings": [],
                    "followups": [],
                },
                "review_first[0].location",
            ),
            (
                {
                    "blocking": False,
                    "summary": "Safe.",
                    "review_first": [{"location": "gate.yml:8", "why": "Boundary.", "reason": "Different."}],
                    "findings": [],
                    "followups": [],
                },
                "review_first[0].why",
            ),
        )
        for verdict, path in cases:
            with self.subTest(path=path), self.assertRaises(review.VerdictError) as raised:
                review.canonicalize_verdict(verdict, sensitive=True)
            self.assertEqual(raised.exception.path, path)
            self.assertEqual(raised.exception.observed, "conflicting aliases")

    def test_location_normalization_repairs_only_documented_line_lists(self):
        valid = {"file": "gate.yml", "line": "8-10, 15,20-22"}
        invalid = {"location": "gate.yml:8,not-a-line"}

        self.assertEqual(review.normalize_location(valid, "location"), "gate.yml:8")
        with self.assertRaises(review.VerdictError):
            review.normalize_location(invalid, "location")

    def test_unknown_fields_cannot_hide_semantic_content_or_leak_into_diagnostics(self):
        secret_key = "issues-SHOULD-NOT-APPEAR"
        verdict = {
            "blocking": False,
            "summary": "Safe.",
            "review_first": [],
            "findings": [],
            "followups": [],
            secret_key: [{"reason": "hidden defect"}],
        }

        result = review.confirm_output(json.dumps(verdict), sensitive=False)

        self.assertFalse(result["usable"])
        self.assertEqual(result["diagnostic"]["path"], "$")
        self.assertNotIn(secret_key, json.dumps(result))

    def test_blocking_evidence_is_bound_to_the_exact_cited_head_line(self):
        canary = """before=$(git ls-remote --refs origin "$tag_ref")
commit=$(git ls-remote origin "$branch_ref")
peeled=$(git ls-remote origin "$tag_ref^{}")
after=$(git ls-remote --refs origin "$tag_ref")
"""
        with tempfile.TemporaryDirectory() as directory:
            head = self.committed_fixture(directory, {".github/workflows/release-app-canary.yml": canary})
            verdict = {
                "blocking": True,
                "summary": "Tag verification is wrong.",
                "review_first": [],
                "findings": [{
                    "location": ".github/workflows/release-app-canary.yml:3",
                    "reason": "The peeled lookup suppresses the tag.",
                    "failure_scenario": "The canary rejects a successful push.",
                    "evidence": "git ls-remote origin \"$tag_ref^{}\"",
                }],
                "followups": [],
            }

            result = review.confirm_output(json.dumps(verdict), False, directory, head)

        self.assertTrue(result["usable"])
        self.assertEqual(result["verdict"]["findings"][0]["evidence"], verdict["findings"][0]["evidence"])

    def test_nearby_canary_refs_probe_cannot_evidence_a_different_line(self):
        canary = """before=$(git ls-remote --refs origin "$tag_ref")
commit=$(git ls-remote origin "$branch_ref")
peeled=$(git ls-remote origin "$tag_ref^{}")
after=$(git ls-remote --refs origin "$tag_ref")
"""
        with tempfile.TemporaryDirectory() as directory:
            head = self.committed_fixture(directory, {".github/workflows/release-app-canary.yml": canary})
            verdict = {
                "blocking": True,
                "summary": "Tag verification is wrong.",
                "review_first": [],
                "findings": [{
                    "location": ".github/workflows/release-app-canary.yml:3",
                    "reason": "The peeled lookup suppresses the tag.",
                    "failure_scenario": "The canary rejects a successful push.",
                    "evidence": "git ls-remote --refs origin \"$tag_ref\"",
                }],
                "followups": [],
            }

            result = review.confirm_output(json.dumps(verdict), False, directory, head)

        self.assertFalse(result["usable"])
        self.assertEqual(result["diagnostic"]["path"], "findings[0].evidence")
        self.assertEqual(result["diagnostic"]["observed"], "fragment mismatch")

    def test_evidence_trims_edges_but_preserves_interior_spacing(self):
        source = "    return  response.value    \n"
        with tempfile.TemporaryDirectory() as directory:
            head = self.committed_fixture(directory, {"src/client.py": source})
            verdict = {
                "blocking": True,
                "summary": "The response is returned without validation.",
                "review_first": [],
                "findings": [{
                    "location": "src/client.py:1",
                    "reason": "The return bypasses validation.",
                    "failure_scenario": "Invalid data reaches the caller.",
                    "evidence": "  return  response.value  ",
                }],
                "followups": [],
            }
            accepted = review.confirm_output(json.dumps(verdict), False, directory, head)
            verdict["findings"][0]["evidence"] = "return response.value"
            rejected = review.confirm_output(json.dumps(verdict), False, directory, head)

        self.assertTrue(accepted["usable"])
        self.assertEqual(accepted["verdict"]["findings"][0]["evidence"], "return  response.value")
        self.assertFalse(rejected["usable"])
        self.assertEqual(rejected["diagnostic"]["observed"], "fragment mismatch")

    def test_canary_range_cannot_collapse_nearby_probe_evidence_to_its_first_line(self):
        canary = """before=$(git ls-remote --refs origin "$tag_ref")
commit=$(git ls-remote origin "$branch_ref")
peeled=$(git ls-remote origin "$tag_ref^{}")
after=$(git ls-remote --refs origin "$tag_ref")
"""
        with tempfile.TemporaryDirectory() as directory:
            head = self.committed_fixture(directory, {".github/workflows/release-app-canary.yml": canary})
            verdict = {
                "blocking": True,
                "summary": "Tag verification is wrong.",
                "review_first": [],
                "findings": [{
                    "location": ".github/workflows/release-app-canary.yml:1-3, 3",
                    "reason": "The peeled lookup suppresses the tag.",
                    "failure_scenario": "The canary rejects a successful push.",
                    "evidence": "git ls-remote --refs origin \"$tag_ref\"",
                }],
                "followups": [],
            }

            result = review.confirm_output(json.dumps(verdict), False, directory, head)

        self.assertFalse(result["usable"])
        self.assertEqual(result["diagnostic"]["path"], "findings[0].location")
        self.assertEqual(result["diagnostic"]["expected"], "one file and exactly one positive line")

    def test_non_authorizing_locations_retain_documented_range_normalization(self):
        verdict = {
            "blocking": False,
            "summary": "No defects found.",
            "review_first": [{"location": "gate.yml:8-10, 15", "why": "Inspect this hunk."}],
            "findings": [],
            "followups": [{"location": "app.py:12-14, 20", "note": "Improve this later."}],
        }

        confirmed = review.canonicalize_verdict(verdict, sensitive=True)

        self.assertEqual(confirmed["review_first"][0]["location"], "gate.yml:8")
        self.assertEqual(confirmed["followups"][0]["location"], "app.py:12")

    def test_synthetic_generator_mutation_cannot_evidence_the_generator(self):
        files = {
            "scripts/gen-changelog-caller.sh": "printf '%s\\n' 'release_app_private_key: inherited'\n",
            "scripts/ci-gate/changelog-release-caller.test.sh": "# ORG_ADMIN_TOKEN retirement comment mutation\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            head = self.committed_fixture(directory, files)
            verdict = {
                "blocking": True,
                "summary": "The generator emits retired credentials.",
                "review_first": [],
                "findings": [{
                    "location": "scripts/gen-changelog-caller.sh:1",
                    "reason": "Generated callers contain the retired token.",
                    "failure_scenario": "Every generated caller fails policy.",
                    "evidence": "ORG_ADMIN_TOKEN retirement comment mutation",
                }],
                "followups": [],
            }

            result = review.confirm_output(json.dumps(verdict), False, directory, head)

        self.assertFalse(result["usable"])
        self.assertEqual(result["diagnostic"]["path"], "findings[0].evidence")

    def test_source_evidence_rejects_head_mismatch_and_unsafe_paths_without_reading_them(self):
        with tempfile.TemporaryDirectory() as directory:
            head = self.committed_fixture(directory, {"app.py": "return value\n"})
            finding = {
                "reason": "Broken.",
                "failure_scenario": "Request fails.",
                "evidence": "return value",
            }
            base = {
                "blocking": True,
                "summary": "Broken.",
                "review_first": [],
                "findings": [{"location": "app.py:1", **finding}],
                "followups": [],
            }
            mismatch = review.confirm_output(json.dumps(base), False, directory, "0" * 40)
            base["findings"][0]["location"] = "../secret:1"
            unsafe = review.confirm_output(json.dumps(base), False, directory, head)

        self.assertEqual(mismatch["diagnostic"]["path"], "reviewed_head")
        self.assertEqual(unsafe["diagnostic"]["path"], "findings[0].location")



def clean_verdict(**overrides):
    verdict = {"blocking": False, "summary": "No defects found.", "review_first": [], "findings": [], "followups": []}
    verdict.update(overrides)
    return json.dumps(verdict)


# Assembled at runtime so no credential-shaped literal is committed.
GITHUB_SHAPED = "gh" + "s_" + "Q7" * 18
FINE_GRAINED_SHAPED = "github" + "_pat_" + "11AAAA" * 5
ANTHROPIC_SHAPED = "sk" + "-ant-" + "api03-" + "x9" * 16
OPENAI_MARKER = "T3Blbk" + "FJ"
# Legacy user key: 20 alphanumerics, the marker, 20 alphanumerics.
OPENAI_LEGACY_SHAPED = "sk" + "-" + "Ab3D" * 5 + OPENAI_MARKER + "x7Yz" * 5
# Segment-prefixed keys carry "_" and "-" in their bodies. These omit the marker, so
# each documented prefix is required on its own rather than through the marker shape.
OPENAI_PROJECT_SHAPED = "sk" + "-proj-" + "Q1w_E2r-" * 15
OPENAI_SERVICE_ACCOUNT_SHAPED = "sk" + "-svcacct-" + "a1B2_c3-" * 15
OPENAI_ADMIN_SHAPED = "sk" + "-admin-" + "Zx9_Y8w-" * 15
OPENAI_SERVICE_SHAPED = "sk" + "-service-" + "p0O_i9U-" * 15
# A prefix this file does not name, recognized only through the marker. The marker sits
# 74 body characters in, as in the longest observed keys.
OPENAI_UNNAMED_PREFIX_SHAPED = "sk" + "-None-" + "k_9-a" * 14 + "k_9-" + OPENAI_MARKER + "m-4_b" * 14 + "m-4_"
OPENAI_PROJECT_MARKED_SHAPED = "sk" + "-proj-" + "a1_B-" * 15 + OPENAI_MARKER + "c2-D_" * 15
DEEPSEEK_SHAPED = "sk" + "-" + "0123456789abcdef" * 2


class CredentialRefusalTest(unittest.TestCase):
    """A verdict is posted to the pull request, so it must never carry credential material."""

    secrets = {"ANTHROPIC_API_KEY": "placeholder-model-credential-0123", "GITHUB_TOKEN": "placeholder-job-token-4567"}

    def assert_refused(self, raw, secrets=()):
        result = review.confirm_output(raw, sensitive=False, secrets=secrets)
        self.assertFalse(result["usable"])
        self.assertEqual(result["diagnostic"]["observed"], "credential material")
        for secret in secrets:
            self.assertNotIn(secret, json.dumps(result))
        return result

    def test_exact_secret_value_anywhere_in_the_verdict_is_refused(self):
        values = tuple(self.secrets.values())
        for field, raw in (
            ("summary", clean_verdict(summary=f"The environment holds {values[0]}.")),
            ("followups", clean_verdict(followups=[{"location": "app.py:3", "note": f"token {values[1]} seen"}])),
            ("review_first", clean_verdict(review_first=[{"location": "app.py:3", "why": f"prefix{values[0]}suffix"}])),
        ):
            with self.subTest(field=field):
                self.assertTrue(self.assert_refused(raw, values)["diagnostic"]["path"].startswith(field))

    def test_credential_shaped_tokens_are_refused_without_knowing_the_value(self):
        for token in (
            GITHUB_SHAPED,
            FINE_GRAINED_SHAPED,
            ANTHROPIC_SHAPED,
            OPENAI_LEGACY_SHAPED,
            OPENAI_PROJECT_SHAPED,
            OPENAI_SERVICE_ACCOUNT_SHAPED,
            OPENAI_ADMIN_SHAPED,
            OPENAI_SERVICE_SHAPED,
            OPENAI_UNNAMED_PREFIX_SHAPED,
            DEEPSEEK_SHAPED,
        ):
            for text in (f"Found {token} in the log.", f"OPENAI_API_KEY={token}", f"`{token}`"):
                with self.subTest(prefix=token[:11], text=text[:16]):
                    self.assert_refused(clean_verdict(summary=text))

    def test_credential_glued_to_emphasis_or_a_separator_is_refused(self):
        # Markdown emphasis and snake_case or flag-style glue sit directly before the
        # prefix; only an alphanumeric, or an alphanumeric then "-", marks an identifier.
        for text in (
            f"Found _{OPENAI_PROJECT_MARKED_SHAPED}_ in the log.",
            f"Found __{DEEPSEEK_SHAPED}__ in the log.",
            f"Found -{DEEPSEEK_SHAPED} in the log.",
            f"Found KEY_{OPENAI_LEGACY_SHAPED} in the log.",
            f"Found x_{DEEPSEEK_SHAPED} in the log.",
            f"Found **{OPENAI_ADMIN_SHAPED}** in the log.",
        ):
            with self.subTest(text=text[:12]):
                self.assert_refused(clean_verdict(summary=text))

    def test_credential_used_as_a_field_name_is_refused_without_being_echoed(self):
        secret = self.secrets["ANTHROPIC_API_KEY"]
        for raw in (
            json.dumps({**json.loads(clean_verdict()), secret: "x"}),
            clean_verdict(followups=[{"location": "app.py:3", "note": "n", secret: "x"}]),
        ):
            with self.subTest(raw=raw[:40]):
                result = review.confirm_output(raw, sensitive=False, secrets=(secret,))
                self.assertFalse(result["usable"])
                self.assertNotIn(secret, json.dumps(result))

    def test_naming_a_token_prefix_is_not_credential_material(self):
        raw = clean_verdict(
            summary="Rotate any ghp_ or github_pat_ tokens, sk-ant- keys, and sk-, sk-proj-, sk-svcacct-, "
            "sk-admin-, or T3BlbkFJ-marked keys this change logged."
        )
        self.assertTrue(review.confirm_output(raw, sensitive=False, secrets=tuple(self.secrets.values()))["usable"])

    def test_review_prose_that_resembles_a_token_is_not_credential_material(self):
        digest = "sha512-" + "z4PhNX7vuL3xVChQ1m+2HUOGNkn6bKZkRTq5I8oH/Gpw" * 2
        for text in (
            "Pin actions/checkout to 0123456789abcdef0123456789abcdef01234567.",
            f'The lockfile entry has "integrity": "{digest}".',
            # An identifier that merely contains "sk-": the prefix must start a token.
            f"Rename the task-{DEEPSEEK_SHAPED} job.",
            f"The cache key ask{DEEPSEEK_SHAPED[2:]} is stable.",
            "The sk-proj-keys-should-be-rotated-before-release note is stale.",
            f"The fixture skips {OPENAI_MARKER} handling.",
        ):
            with self.subTest(text=text[:24]):
                raw = clean_verdict(summary=text)
                self.assertTrue(review.confirm_output(raw, sensitive=False)["usable"], text)

    def test_credential_scan_stays_linear_on_adversarial_text(self):
        # Every "_sk-" here may start a token, so an unbounded body would rescan the rest of
        # the run from each start. 128 KiB is the largest single environment string Linux
        # passes (MAX_ARG_STRLEN), so no VERDICT the workflow hands over can be longer.
        text = "_sk-" * (128 * 1024 // 4)
        started = time.perf_counter()
        self.assertIsNone(review.CREDENTIAL_SHAPE.search(text))
        self.assertLess(time.perf_counter() - started, 1.0)

    def test_main_reads_secret_values_from_its_environment_and_keeps_them_out_of_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            env = {"PATH": os.defpath, "GITHUB_OUTPUT": str(output), "SENSITIVE": "false", "REVIEW_PASS": "1",
                   "VERDICT": clean_verdict(summary=f"Leaked {self.secrets['ANTHROPIC_API_KEY']}"), **self.secrets}
            result = subprocess.run([sys.executable, "-I", str(path)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            written = output.read_text()
            self.assertIn("usable=false\n", written)
            for secret in self.secrets.values():
                self.assertNotIn(secret, written + result.stdout + result.stderr)

    def test_source_lookups_never_receive_the_secret_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            repository.mkdir()
            head = CredentialRefusalTest.commit(repository, {"app.py": "value = compute_the_answer()\n"})
            recorder = root / "bin" / "git"
            recorder.parent.mkdir()
            recorder.write_text(f'#!/bin/sh\nenv >>"{root}/git-env"\nexec {shutil.which("git")} "$@"\n')
            recorder.chmod(0o755)
            verdict = clean_verdict(blocking=True, findings=[{
                "location": "app.py:1", "reason": "r", "failure_scenario": "f", "evidence": "compute_the_answer()"}])
            env = {"PATH": f"{recorder.parent}{os.pathsep}{os.defpath}", "GITHUB_OUTPUT": str(root / "output"),
                   "SENSITIVE": "false", "REVIEW_PASS": "1", "VERDICT": verdict, "REVIEW_REPOSITORY": str(repository),
                   "REVIEWED_HEAD_SHA": head, **self.secrets}
            result = subprocess.run([sys.executable, "-I", str(path)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usable=true\n", (root / "output").read_text())
            recorded = (root / "git-env").read_text()
            self.assertIn("PATH=", recorded)
            for name, secret in self.secrets.items():
                self.assertNotIn(secret, recorded)

    @staticmethod
    def commit(repository, files):
        for relative_path, content in files.items():
            (repository / relative_path).write_text(content, encoding="utf-8")
        for arguments in (("init", "-q"), ("add", "."), ("-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                                         "commit", "-qm", "fixture")):
            subprocess.run(["git", *arguments], cwd=repository, check=True, capture_output=True)
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True,
                              text=True).stdout.strip()


if __name__ == "__main__":
    unittest.main()
