#!/usr/bin/env python3
"""Behavioral tests for typed CI test levels (ADR 0238, test-levels amendment)."""

import importlib.util
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures/ci-levels"
GENERATOR = ROOT / "scripts/gen-node-ci-caller.py"
SHA = "a" * 40

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/ci-gate/conformance"))
import ci_levels  # noqa: E402
from expressions import Evaluator, UnsupportedExpression  # noqa: E402

_spec = importlib.util.spec_from_file_location("gen_node_ci_caller", GENERATOR)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)
KNOWN = gen.known_node_ci_inputs()
SECRETS = gen.known_node_ci_secrets()


def parse(document):
    return ci_levels.parse_levels(json.dumps(document), KNOWN, SECRETS)


def codes(document):
    try:
        parse(document)
    except ci_levels.LevelsError as exc:
        return [e.code for e in exc.errors]
    return []


def levels(*items, **top):
    return {"version": 1, "levels": list(items), **top}


def run_generator(*args):
    return subprocess.run([sys.executable, str(GENERATOR), *args], capture_output=True, text=True)


def aggregate_script(rendered):
    workflow = yaml.safe_load(rendered)
    return workflow["jobs"][ci_levels.AGGREGATE_ID]["steps"][0]["run"]


def run_aggregate(script, expected, results):
    env = {"PATH": os.environ["PATH"], "LEVEL_COUNT": str(len(results))}
    for index, (exp, result) in enumerate(zip(expected, results)):
        env.update({f"LEVEL_{index}": f"l{index}", f"EXPECTED_{index}": exp, f"RESULT_{index}": result})
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True).returncode


class GoldenTests(unittest.TestCase):
    def test_without_a_levels_file_the_caller_is_the_single_ci_job_byte_for_byte(self):
        result = run_generator(SHA)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, (FIXTURES / "default.golden.yml").read_text())
        jobs = yaml.safe_load(result.stdout)["jobs"]
        self.assertEqual(list(jobs), ["ci"])

    def test_declared_levels_render_byte_for_byte_with_edges_and_aggregate(self):
        result = run_generator(SHA, "--levels", str(FIXTURES / "gitflow.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, (FIXTURES / "gitflow.golden.yml").read_text())

    def test_a_single_level_named_ci_keeps_the_ci_job_name(self):
        plan = parse(levels({"id": "ci"}))
        jobs = yaml.safe_load(ci_levels.render_caller(plan, SHA, ""))["jobs"]
        self.assertEqual(list(jobs), ["ci", ci_levels.AGGREGATE_ID])

    def test_the_concurrency_block_equals_the_documented_caller_snippet(self):
        docs = (ROOT / "docs/node-workflows.md").read_text()
        snippet = re.search(r"```yaml\n(concurrency:.*?)```", docs, re.S).group(1)
        self.assertEqual(ci_levels.CONCURRENCY, snippet)

    def test_the_aggregate_has_no_permissions_and_no_continue_on_error(self):
        rendered = (FIXTURES / "gitflow.golden.yml").read_text()
        self.assertEqual(yaml.safe_load(rendered)["jobs"][ci_levels.AGGREGATE_ID]["permissions"], {})
        self.assertNotIn("continue-on-error", rendered)


class BuildArtifactSchemaTests(unittest.TestCase):
    def artifact_document(self, **overrides):
        document = levels(
            {"id": "build"},
            {"id": "test"},
            secrets=["NODE_AUTH_TOKEN"],
            buildArtifact={"producer": "build", "consumers": ["test"], "paths": [".next"]},
        )
        document.update(overrides)
        return document

    def test_build_artifact_accepts_the_typed_request_and_resolves_declared_levels(self):
        plan = parse(self.artifact_document())
        self.assertEqual(plan.build_artifact.producer, "build")
        self.assertEqual(plan.build_artifact.consumers, ("test",))
        self.assertEqual(plan.build_artifact.paths, (".next",))

    def test_build_artifact_rejects_extra_fields_and_unsafe_roots(self):
        for request in (
            {"producer": "build", "consumers": ["test"], "paths": ["dist/*"]},
            {"producer": "build", "consumers": ["test"], "paths": ["dist/../secret"]},
            {"producer": "build", "consumers": ["test"], "paths": ["/tmp/output"]},
            {"producer": "build", "consumers": ["test"], "paths": [".next"], "profileFiles": ["package.json"]},
        ):
            with self.subTest(request=request):
                self.assertNotEqual(codes(self.artifact_document(buildArtifact=request)), [])

    def test_build_artifact_requires_exact_acquisition_secret_and_reserved_job_ids(self):
        self.assertIn("build_artifact_secrets", codes(self.artifact_document(secrets=[])))
        document = self.artifact_document()
        document["levels"].append({"id": "artifact-preflight"})
        self.assertIn("reserved_job_id", codes(document))

    def test_build_artifact_rejects_caller_secretless_mode_overrides(self):
        document = self.artifact_document()
        document["levels"][0]["inputs"] = {"secretless-pr": True}
        self.assertIn("managed_secretless_mode", codes(document))

    def test_build_artifact_rejects_caller_internal_role_and_duplicate_yaml_inputs(self):
        document = self.artifact_document()
        document["levels"][0]["inputs"] = {"build-artifact-role": "producer"}
        self.assertIn("build_artifact_internal_input", codes(document))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ci-levels.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            result = run_generator(SHA, "--levels", str(path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("build_artifact_internal_input", result.stderr)

    def test_internal_build_artifact_inputs_are_rejected_without_feature_request(self):
        document = levels({"id": "test", "inputs": {"build-artifact-role": "unknown"}})
        self.assertIn("build_artifact_internal_input", codes(document))

    def render(self, document):
        return ci_levels.render_caller(parse(document), SHA, " --levels ci-levels.json")

    def test_share_eligible_artifact_dag_matches_golden_and_gates_every_level(self):
        document = levels(
            {"id": "setup"},
            {"id": "build", "needs": [{"level": "setup"}]},
            {"id": "test", "needs": [{"level": "setup"}]},
            secrets=["NODE_AUTH_TOKEN"],
            buildArtifact={"producer": "build", "consumers": ["test"], "paths": [".next"]},
        )
        rendered = self.render(document)
        self.assertEqual(rendered, (FIXTURES / "build-artifact-share.golden.yml").read_text())
        jobs = yaml.safe_load(rendered)["jobs"]
        self.assertEqual(jobs["artifact-mode-gate"].get("needs"), None)
        self.assertEqual(jobs["artifact-preflight"]["needs"], ["artifact-mode-gate", "setup"])
        self.assertEqual(
            jobs["artifact-preflight"]["with"]["build-artifact-mode-share-eligible"],
            "${{ fromJSON(needs.artifact-mode-gate.outputs.build-artifact-mode-share-eligible) }}",
        )
        self.assertEqual(
            jobs["artifact-preflight"]["with"]["build-artifact-needs-results"],
            '{"setup":{"result":${{ toJSON(needs.setup.result) }}}}',
        )
        self.assertEqual(jobs["setup"]["needs"], ["artifact-mode-gate"])
        self.assertNotIn("artifact-preflight", jobs["setup"]["needs"])
        self.assertIn("github.run_attempt == '1'", jobs["artifact-producer"]["if"])
        self.assertIn(
            "toJSON(fromJSON(needs.artifact-preflight.outputs.build-artifact-share-selected)) == 'false'",
            jobs["test"]["with"]["build-artifact-role"],
        )
        self.assertIn("github.run_attempt == '1'", jobs["test"]["if"])
        self.assertIn(
            "needs.artifact-producer.result == 'success'",
            jobs["test"]["if"],
        )
        self.assertIn(
            "toJSON(fromJSON(needs.artifact-producer.outputs.build-artifact-share-available)) == 'true'",
            jobs["test"]["if"],
        )
        self.assertIn("needs.artifact-producer.result == 'skipped'", jobs["test-levels"]["steps"][1]["with"]["share-selected"])
        self.assertEqual(jobs["test-levels"]["steps"][1]["if"], "always()")
        self.assertEqual(jobs["test-levels"]["if"], "${{ always() }}")
        self.assertNotIn("continue-on-error", json.dumps(jobs["test-levels"]))
        aggregate_projection = jobs["test-levels"]["steps"][1]["with"]["needs-json"]
        self.assertIn('"build-artifact-receipt":${{ toJSON(needs.build.outputs.build-artifact-receipt) }}', aggregate_projection)
        self.assertIn('needs.test.outputs.build-artifact-receipt', aggregate_projection)
        self.assertIn('needs.test.outputs.build-artifact-command-digest', aggregate_projection)
        self.assertNotIn('"setup"', aggregate_projection)
        self.assertEqual(
            jobs["test-levels"]["steps"][1]["with"]["expected-lanes"],
            "${{ needs.artifact-preflight.outputs.build-artifact-aggregate-lanes }}",
        )
        self.assertEqual(
            jobs["test-levels"]["steps"][1]["with"]["upload-result"],
            "${{ needs.artifact-producer.outputs.build-artifact-upload-result }}",
        )
        self.assertIn("policy-selected", jobs["test-levels"]["steps"][1]["with"])
        self.assertNotIn("receipts", jobs["test-levels"]["steps"][1]["with"])

    def test_generated_participant_selection_is_exact_and_fails_closed(self):
        document = self.artifact_document()
        jobs = yaml.safe_load(self.render(document))["jobs"]
        selected = "toJSON(fromJSON(needs.artifact-preflight.outputs.build-artifact-share-selected))"
        available = "toJSON(fromJSON(needs.artifact-producer.outputs.build-artifact-share-available))"
        participant_if = (
            "${{ always() && !cancelled() && needs.artifact-mode-gate.result == 'success' "
            "&& needs.artifact-preflight.result == 'success' "
            f"&& ({selected} == 'false' || (github.run_attempt == '1' && {selected} == 'true' "
            f"&& needs.artifact-producer.result == 'success' && {available} == 'true')) && (true) }}}}"
        )
        for level_id in ("build", "test"):
            self.assertEqual(jobs[level_id]["if"], participant_if)
            self.assertIn(f"{selected} == 'false'", jobs[level_id]["if"])
            self.assertIn(f"{selected} == 'true'", jobs[level_id]["if"])
            self.assertIn("github.run_attempt == '1'", jobs[level_id]["if"])
            self.assertIn("needs.artifact-producer.result == 'success'", jobs[level_id]["if"])
            self.assertIn(f"{available} == 'true'", jobs[level_id]["if"])

        def route(preflight_result, raw_selected, attempt, producer_result, raw_available):
            bindings = {
                "needs.artifact-mode-gate.result": "success",
                "needs.artifact-preflight.result": preflight_result,
                "github.run_attempt": attempt,
                "needs.artifact-preflight.outputs.build-artifact-share-selected": raw_selected,
                "needs.artifact-producer.result": producer_result,
                "needs.artifact-producer.outputs.build-artifact-share-available": raw_available,
            }
            functions = {
                "always": lambda: True,
                "cancelled": lambda: False,
                "success": lambda: True,
                "failure": lambda: False,
            }
            level_ids = [level["id"] for level in document["levels"]]
            try:
                launches = [
                    Evaluator(bindings, functions=functions).evaluate(jobs[level_id]["if"])
                    for level_id in level_ids
                ]
                if not all(launches):
                    return "no-launch"

                level_roles = []
                for level_id in level_ids:
                    role_expression = jobs[level_id]["with"]["build-artifact-role"]
                    role_expression = role_expression.removeprefix("${{").removesuffix("}}").strip()
                    level_roles.append(
                        Evaluator(bindings).evaluate(f"({role_expression}) == 'level'")
                    )
            except UnsupportedExpression:
                return "no-launch"

            if all(level_roles):
                return "original-plan"
            if not any(level_roles):
                return "consumer-plan"
            return "no-launch"

        cases = (
            ("success", "true", "1", "failure", "true", "no-launch"),
            ("success", "true", "1", "success", "", "no-launch"),
            ("success", "false", "1", "skipped", "", "original-plan"),
            ("success", "false", "2", "skipped", "", "original-plan"),
            ("success", "true", "1", "success", "true", "consumer-plan"),
            ("success", "TRUE", "1", "success", "true", "no-launch"),
            ("success", "False", "1", "skipped", "", "no-launch"),
            ("success", '"true"', "1", "success", "true", "no-launch"),
            ("success", "1", "1", "success", "true", "no-launch"),
            ("success", "0", "1", "skipped", "", "no-launch"),
            ("success", "null", "1", "skipped", "", "no-launch"),
            ("success", "", "1", "skipped", "", "no-launch"),
            ("success", "true", "2", "success", "true", "no-launch"),
            ("success", "true", "1", "failed", "true", "no-launch"),
            ("success", "true", "1", "skipped", "", "no-launch"),
            ("success", "true", "1", "cancelled", "", "no-launch"),
            ("success", "true", "1", "success", "false", "no-launch"),
            ("success", "true", "1", "success", "TRUE", "no-launch"),
            ("success", "true", "1", "success", "1", "no-launch"),
            ("success", "true", "1", "success", '"true"', "no-launch"),
            ("success", "true", "1", "success", "null", "no-launch"),
            ("success", "true", "1", "success", "", "no-launch"),
            ("failure", "false", "1", "skipped", "", "no-launch"),
        )
        for preflight, raw_selected, attempt, producer, raw_available, expected in cases:
            with self.subTest(raw_selected=raw_selected, attempt=attempt, producer=producer):
                self.assertEqual(
                    route(preflight, raw_selected, attempt, producer, raw_available),
                    expected,
                )

    def test_producer_keeps_runtime_script_plans_while_execution_uses_preflight_plan(self):
        document = levels(
            {"id": "build", "inputs": {
                "secretless-ci-script-plan": '["build","test"]',
                "secretless-ci-non-app-script-plan": '["build","lint"]',
            }},
            {"id": "test"},
            secrets=["NODE_AUTH_TOKEN"],
            buildArtifact={"producer": "build", "consumers": ["test"], "paths": [".next"]},
        )
        jobs = yaml.safe_load(self.render(document))["jobs"]
        producer = jobs["artifact-producer"]["with"]
        self.assertEqual(producer["secretless-ci-script-plan"], '["build","test"]')
        self.assertEqual(producer["secretless-ci-non-app-script-plan"], '["build","lint"]')
        self.assertIn("needs.artifact-preflight.outputs.build-artifact-producer-plan", producer["build-artifact-plan-json"])
        authorization_plan = json.loads(producer["build-artifact-authorization-plan-json"])
        self.assertEqual(
            authorization_plan["levels"][0]["inputs"]["secretless-ci-script-plan"],
            '["build","test"]',
        )

    def test_participant_prerequisite_cycle_selects_static_fallback_dag(self):
        document = levels(
            {"id": "build"},
            {"id": "test", "events": ["push"], "needs": [{"level": "build"}]},
            secrets=["NODE_AUTH_TOKEN"],
            buildArtifact={"producer": "build", "consumers": ["test"], "paths": [".next"]},
        )
        rendered = self.render(document)
        self.assertEqual(rendered, (FIXTURES / "build-artifact-static-fallback.golden.yml").read_text())
        jobs = yaml.safe_load(rendered)["jobs"]
        self.assertEqual(jobs["artifact-preflight"]["needs"], ["artifact-mode-gate"])
        self.assertNotIn("uses", jobs["artifact-preflight"])
        self.assertIn("share-selected=false", jobs["artifact-preflight"]["steps"][0]["run"])
        self.assertEqual(jobs["artifact-preflight"]["outputs"]["build-artifact-aggregate-lanes"], "${{ steps.fallback.outputs.aggregate-lanes }}")
        self.assertIn("aggregate-lanes=[]", jobs["artifact-preflight"]["steps"][0]["run"])
        self.assertNotIn("checkout", json.dumps(jobs["artifact-preflight"]))
        self.assertEqual(jobs["artifact-producer"]["needs"], ["artifact-preflight"])
        self.assertNotIn("artifact-producer", jobs["artifact-producer"]["needs"])
        self.assertEqual(
            jobs["test-levels"]["steps"][0]["env"]["EXPECTED_1"],
            "${{ github.event_name == 'push' }}",
        )

    def test_static_eligibility_uses_canonical_parser_for_string_shorthand(self):
        document = self.artifact_document()
        document["levels"][0]["inputs"] = {
            "secretless-ci-script-plan": json.dumps(["build", "lint"]),
        }
        document["levels"][1]["inputs"] = {
            "secretless-ci-script-plan": json.dumps(["lint"]),
        }
        plan = parse(document)
        self.assertIsNone(ci_levels._artifact_static_fallback_reason(plan))

    def test_whitespace_only_script_plans_render_static_per_lane_fallback(self):
        for field in ("secretless-ci-script-plan", "secretless-ci-non-app-script-plan"):
            for source in (" ", "\t", "\n", "\u00a0", "\u2003"):
                with self.subTest(field=field, source=source):
                    document = self.artifact_document()
                    document["levels"][0]["inputs"] = {field: source}
                    plan = parse(document)
                    self.assertEqual(
                        ci_levels._artifact_static_fallback_reason(plan),
                        "participant-script-plan-not-static",
                    )
                    rendered = self.render(document)
                    self.assertIn(
                        "Static per-lane fallback: participant-script-plan-not-static.",
                        rendered,
                    )
                    workflow = yaml.safe_load(rendered)
                    self.assertIn(
                        "share-selected=false",
                        workflow["jobs"]["artifact-preflight"]["steps"][0]["run"],
                    )


class AggregateTruthTableTests(unittest.TestCase):
    def setUp(self):
        self.script = aggregate_script((FIXTURES / "gitflow.golden.yml").read_text())

    def test_one_level_passes_only_when_expected_success_or_unexpected_skipped(self):
        for expected, result in itertools.product(["true", "false"], ["success", "failure", "cancelled", "skipped", ""]):
            with self.subTest(expected=expected, result=result):
                passes = (expected, result) in {("true", "success"), ("false", "skipped")}
                self.assertEqual(run_aggregate(self.script, [expected], [result]) == 0, passes)

    def test_an_unexpected_expectation_value_fails(self):
        self.assertNotEqual(run_aggregate(self.script, [""], ["success"]), 0)

    def test_any_bad_level_among_good_ones_fails_the_aggregate(self):
        self.assertEqual(run_aggregate(self.script, ["true", "true", "false"], ["success", "success", "skipped"]), 0)
        for bad in ("failure", "cancelled", "skipped"):
            with self.subTest(bad=bad):
                self.assertNotEqual(run_aggregate(self.script, ["true", "true", "false"], ["success", bad, "skipped"]), 0)


def context(event, target):
    """Actions context: base_ref is set only for pull requests; ref_name differs per event."""
    if event == "pull_request":
        return target, "7/merge"
    return "", target


def ancestors(jobs, name):
    """Implicit success() covers the whole dependency chain, not only direct needs."""
    found = set()
    pending = list(jobs[name].get("needs", []))
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending.extend(jobs[current].get("needs", []))
    return found


def simulate(rendered, outcomes, event="pull_request", base="main"):
    """Model GitHub's scheduling of the generated jobs; `outcomes` maps level -> result if it runs."""
    base_ref, ref_name = context(event, base)
    jobs = yaml.safe_load(rendered)["jobs"]
    results = {}

    def evaluate(expression):
        py = expression.replace("&&", " and ").replace("||", " or ").replace("!cancelled()", "True")
        py = py.replace("github.event_name", repr(event)).replace("github.base_ref", repr(base_ref))
        py = py.replace("github.ref_name", repr(ref_name))
        py = re.sub(r"needs\.([a-z-]+)\.result", lambda m: repr(results[m.group(1)]), py)
        return eval(py)  # noqa: S307 - restricted generated grammar under test

    for name, job in jobs.items():
        if name == ci_levels.AGGREGATE_ID:
            continue
        condition = None
        if "if" in job:
            condition = job["if"][len("${{ "):-len(" }}")]
        has_status_function = condition is not None and "!cancelled()" in condition
        upstream_ok = all(results[n] == "success" for n in ancestors(jobs, name))
        runs = (has_status_function or upstream_ok) and (condition is None or evaluate(condition))
        results[name] = outcomes.get(name, "success") if runs else "skipped"
    return results


def expected_flags(rendered, event="pull_request", base="main"):
    base_ref, ref_name = context(event, base)
    env = yaml.safe_load(rendered)["jobs"][ci_levels.AGGREGATE_ID]["steps"][0]["env"]
    flags = []
    for index in range(int(env["LEVEL_COUNT"])):
        expression = env[f"EXPECTED_{index}"][len("${{ "):-len(" }}")]
        py = expression.replace("&&", " and ").replace("||", " or ")
        py = py.replace("github.event_name", repr(event)).replace("github.base_ref", repr(base_ref))
        py = py.replace("github.ref_name", repr(ref_name)).replace("true", "True")
        flags.append("true" if eval(py) else "false")  # noqa: S307
    return env, flags


class EdgeConditionTests(unittest.TestCase):
    rendered = (FIXTURES / "gitflow.golden.yml").read_text()

    def aggregate_exit(self, outcomes, event="pull_request", base="main"):
        results = simulate(self.rendered, outcomes, event, base)
        _, flags = expected_flags(self.rendered, event, base)
        ordered = [results[env_level] for env_level in ["lint", "unit", "integration", "diagnostics", "release-gate"]]
        return results, run_aggregate(aggregate_script(self.rendered), flags, ordered)

    def test_all_success_on_a_pull_request_passes(self):
        results, code = self.aggregate_exit({})
        self.assertEqual(results["release-gate"], "skipped")
        self.assertEqual(code, 0)

    def test_failed_prerequisite_skips_a_success_edge_but_runs_a_completed_edge_and_still_fails(self):
        results, code = self.aggregate_exit({"unit": "failure"})
        self.assertEqual(results["integration"], "skipped")
        self.assertEqual(results["diagnostics"], "success")
        self.assertNotEqual(code, 0)

    def test_a_cancelled_prerequisite_runs_neither_edge_and_fails(self):
        results, code = self.aggregate_exit({"unit": "cancelled"})
        self.assertEqual((results["integration"], results["diagnostics"]), ("skipped", "skipped"))
        self.assertNotEqual(code, 0)

    def test_a_failing_independent_level_fails_even_when_the_rest_succeed(self):
        results, code = self.aggregate_exit({"lint": "failure"})
        self.assertEqual(results["unit"], "success")
        self.assertNotEqual(code, 0)

    def test_a_push_to_main_expects_the_release_gate_and_not_diagnostics(self):
        results, code = self.aggregate_exit({}, event="push")
        self.assertEqual((results["release-gate"], results["diagnostics"]), ("success", "skipped"))
        self.assertEqual(code, 0)

    def test_a_success_edge_after_a_completed_edge_runs_when_its_grandparent_failed(self):
        document = levels(
            {"id": "a"},
            {"id": "b", "needs": [{"level": "a", "condition": "completed"}]},
            {"id": "c", "needs": [{"level": "b"}]},
        )
        rendered = ci_levels.render_caller(parse(document), SHA, "")
        results = simulate(rendered, {"a": "failure"})
        self.assertEqual(results, {"a": "failure", "b": "success", "c": "success"})

    def test_without_the_status_guard_the_third_hop_would_skip(self):
        document = levels(
            {"id": "a"},
            {"id": "b", "needs": [{"level": "a", "condition": "completed"}]},
            {"id": "c", "needs": [{"level": "b"}]},
        )
        rendered = ci_levels.render_caller(parse(document), SHA, "").replace("!cancelled() && ", "")
        self.assertEqual(simulate(rendered, {"a": "failure"})["c"], "skipped")

    def test_a_push_to_a_branch_other_than_main_skips_branch_filtered_levels(self):
        results, code = self.aggregate_exit({}, event="push", base="feature-x")
        self.assertEqual(results["release-gate"], "skipped")
        self.assertEqual(code, 0)

    def test_a_workflow_dispatch_on_main_selects_by_ref_name(self):
        results, code = self.aggregate_exit({}, event="workflow_dispatch", base="main")
        self.assertEqual((results["unit"], results["integration"], results["release-gate"]), ("success", "skipped", "skipped"))
        self.assertEqual(code, 0)

    def test_a_failed_diagnostics_level_fails_the_aggregate(self):
        _, code = self.aggregate_exit({"diagnostics": "failure"})
        self.assertNotEqual(code, 0)


class ValidatorTests(unittest.TestCase):
    def test_valid_plan_is_immutable(self):
        plan = parse(levels({"id": "unit"}, {"id": "e2e", "needs": [{"level": "unit"}]}))
        self.assertEqual([l.id for l in plan.levels], ["unit", "e2e"])
        with self.assertRaises(Exception):
            plan.levels[0].id = "x"

    def test_needs_default_to_success(self):
        plan = parse(levels({"id": "a"}, {"id": "b", "needs": [{"level": "a"}]}))
        self.assertEqual(plan.levels[1].needs[0].condition, "success")

    def test_cancel_is_an_unknown_field_with_a_pointer_to_workflow_concurrency(self):
        with self.assertRaises(ci_levels.LevelsError) as ctx:
            parse(levels({"id": "a", "cancel": True}))
        error = ctx.exception.errors[0]
        self.assertEqual((error.code, error.path), ("unknown_field", "$.levels[0].cancel"))
        self.assertIn("concurrency", error.message)

    def test_unknown_top_level_and_need_fields_are_rejected(self):
        self.assertIn("unknown_field", codes(levels({"id": "a"}, extra=1)))
        self.assertIn("unknown_field", codes(levels({"id": "a"}, {"id": "b", "needs": [{"level": "a", "when": "x"}]})))

    def test_a_dependency_cycle_is_unrepresentable(self):
        self.assertEqual(codes(levels({"id": "a", "needs": [{"level": "b"}]}, {"id": "b", "needs": [{"level": "a"}]})), ["forward_need"])
        self.assertEqual(codes(levels({"id": "a", "needs": [{"level": "a"}]})), ["forward_need"])

    def test_unknown_need_is_reported(self):
        self.assertEqual(codes(levels({"id": "a", "needs": [{"level": "zzz"}]})), ["unknown_need"])

    def test_duplicate_ids_and_duplicate_needs_are_rejected(self):
        self.assertIn("duplicate_id", codes(levels({"id": "a"}, {"id": "a"})))
        self.assertIn("duplicate_need", codes(levels({"id": "a"}, {"id": "b", "needs": [{"level": "a"}, {"level": "a"}]})))

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(ci_levels.LevelsError) as ctx:
            ci_levels.parse_levels('{"version":1,"version":1,"levels":[{"id":"a"}]}', KNOWN, SECRETS)
        self.assertEqual(ctx.exception.errors[0].code, "duplicate_key")

    def test_invalid_json_and_non_object_documents_are_rejected(self):
        for text, code in (("{", "invalid_json"), ("[]", "bad_type")):
            with self.subTest(text=text), self.assertRaises(ci_levels.LevelsError) as ctx:
                ci_levels.parse_levels(text, KNOWN, SECRETS)
            self.assertEqual(ctx.exception.errors[0].code, code)

    def test_malformed_booleans_are_rejected_and_fail_fast_false_is_preserved(self):
        self.assertEqual(codes(levels({"id": "a", "failFast": "true"})), ["bad_type"])
        self.assertEqual(codes(levels({"id": "a", "failFast": 1})), ["bad_type"])
        parsed = ci_levels.parse_levels(
            json.dumps({"version": 1, "levels": [{"id": "a", "failFast": False}]}),
            KNOWN,
            SECRETS,
        )
        self.assertIs(parsed.levels[0].fail_fast, False)
        self.assertEqual(codes(levels({"id": "a", "failFast": True})), [])

    def test_empty_work_is_rejected(self):
        self.assertEqual(codes(levels()), ["empty_work"])
        self.assertEqual(codes(levels({"id": "a", "events": []})), ["empty_work"])
        self.assertEqual(codes(levels({"id": "a", "branches": []})), ["empty_work"])

    def test_ids_must_be_safe_and_not_the_aggregate(self):
        self.assertEqual(codes(levels({"id": "Bad Id"})), ["bad_value"])
        self.assertEqual(codes(levels({"id": ci_levels.AGGREGATE_ID})), ["reserved_id"])

    def test_plan_size_is_bounded(self):
        many = [{"id": f"l{i}"} for i in range(ci_levels.MAX_LEVELS + 1)]
        self.assertIn("too_many", codes(levels(*many)))

    def test_inputs_must_exist_in_node_ci_and_hold_inert_values(self):
        self.assertEqual(codes(levels({"id": "a", "inputs": {"nope": 1}})), ["unknown_input"])
        self.assertEqual(codes(levels({"id": "a", "inputs": {"node-version": "${{ secrets.X }}"}})), ["bad_input_value"])
        self.assertEqual(codes(levels({"id": "a", "inputs": {"node-version": 1.5}})), ["bad_input_value"])

    def test_a_level_cannot_need_one_that_skips_where_it_runs(self):
        document = levels({"id": "a", "events": ["push"]}, {"id": "b", "needs": [{"level": "a"}]})
        self.assertEqual(codes(document), ["need_not_covering"])
        self.assertEqual(codes(levels({"id": "a"}, {"id": "b", "events": ["push"], "needs": [{"level": "a"}]})), [])

    def test_secretless_modes_must_match_the_events_node_ci_accepts(self):
        pr = {"secretless-pr": True}
        trusted = {"secretless-trusted-ref": True}
        self.assertEqual(codes(levels({"id": "a", "inputs": pr})), ["input_event_conflict"])
        self.assertEqual(codes(levels({"id": "a", "events": ["pull_request", "push"], "inputs": pr})), ["input_event_conflict"])
        self.assertEqual(codes(levels({"id": "a", "events": ["pull_request"], "inputs": pr})), [])
        self.assertEqual(codes(levels({"id": "a", "inputs": trusted})), ["input_event_conflict"])
        self.assertEqual(codes(levels({"id": "a", "events": ["push", "workflow_dispatch"], "inputs": trusted})), [])
        both = {**pr, **trusted}
        self.assertEqual(codes(levels({"id": "a", "events": ["pull_request"], "inputs": both})), ["input_event_conflict"])

    def test_a_push_only_chain_is_covered_by_the_push_branches(self):
        document = levels(
            {"id": "a", "events": ["push"], "branches": ["main"]},
            {"id": "b", "events": ["push"], "needs": [{"level": "a"}]},
        )
        self.assertEqual(codes(document), [])
        self.assertEqual(codes({**document, "pushBranches": ["main", "develop"]}), ["need_not_covering"])

    def test_characters_yaml_cannot_carry_faithfully_are_rejected(self):
        for char in ("\x7f", "\x85", "\ufffe", "\uffff", "\u2028", "\x00"):
            with self.subTest(char=repr(char)):
                self.assertEqual(codes(levels({"id": "a", "inputs": {"schema-dir": f"x{char}y"}})), ["bad_input_value"])

    def test_tabs_and_newlines_survive_the_yaml_round_trip(self):
        plan = parse(levels({"id": "a", "inputs": {"schema-dir": "a\tb\nc"}}))
        jobs = yaml.safe_load(ci_levels.render_caller(plan, SHA, ""))["jobs"]
        self.assertEqual(jobs["a"]["with"]["schema-dir"], "a\tb\nc")

    def test_explicit_push_branches_are_intersected_with_the_push_trigger(self):
        document = levels(
            {"id": "a", "events": ["push"], "branches": ["main"]},
            {"id": "b", "events": ["push"], "branches": ["main", "develop"], "needs": [{"level": "a"}]},
        )
        self.assertEqual(codes(document), [])

    def test_branch_names_git_forbids_are_rejected(self):
        for name in ("release..candidate", "foo//bar", "foo.lock", "foo/", "foo.", "release/.hidden", "release/./x", "HEAD"):
            with self.subTest(name):
                self.assertIn("bad_value", codes(levels({"id": "a", "branches": [name]})))

    def test_supplementary_unicode_inputs_round_trip_through_yaml(self):
        plan = parse(levels({"id": "a", "inputs": {"schema-dir": "schemas/\U0001F600"}}))
        jobs = yaml.safe_load(ci_levels.render_caller(plan, SHA, ""))["jobs"]
        self.assertEqual(jobs["a"]["with"]["schema-dir"], "schemas/\U0001F600")

    def test_merge_group_is_not_an_event_yet(self):
        self.assertIn("bad_value", codes(levels({"id": "a", "events": ["merge_group"]})))

    def test_yaml_scalar_ids_are_reserved(self):
        for name in ("null", "true", "no", "off"):
            with self.subTest(name):
                self.assertEqual(codes(levels({"id": name})), ["reserved_id"])

    def test_a_push_only_level_outside_push_branches_is_unreachable(self):
        document = levels({"id": "a", "events": ["push"], "branches": ["release"]})
        self.assertEqual(codes(document), ["unreachable_level"])
        self.assertEqual(codes({**document, "pushBranches": ["release"]}), [])

    def test_input_values_must_match_the_declared_type(self):
        self.assertEqual(codes(levels({"id": "a", "inputs": {"cache": "garbage"}})), ["bad_input_value"])
        self.assertEqual(codes(levels({"id": "a", "inputs": {"secretless-pr": "false"}})), ["bad_input_value"])
        self.assertEqual(codes(levels({"id": "a", "inputs": {"timeout-minutes": True}})), ["bad_input_value"])
        self.assertEqual(codes(levels({"id": "a", "inputs": {"timeout-minutes": 45, "cache": True}})), [])

    def test_lone_surrogates_are_rejected(self):
        text = '{"version":1,"levels":[{"id":"a","inputs":{"node-version":"\\ud800"}}]}'
        with self.assertRaises(ci_levels.LevelsError) as ctx:
            ci_levels.parse_levels(text, KNOWN, SECRETS)
        self.assertEqual(ctx.exception.errors[0].code, "bad_input_value")

    def test_version_must_be_the_integer_one(self):
        for version in (True, 1.0, "1"):
            with self.subTest(version=version):
                self.assertEqual(codes({"version": version, "levels": [{"id": "a"}]}), ["bad_value"])

    def test_closed_deeply_nested_values_are_a_structured_error(self):
        depth = 20000
        nested = "[" * depth + "]" * depth
        text = '{"version":1,"levels":[{"id":"a","events":["push"],"branches":[' + nested + ']}]}'
        with self.assertRaises(ci_levels.LevelsError) as ctx:
            ci_levels.parse_levels(text, KNOWN, SECRETS)
        self.assertTrue(ctx.exception.errors)

    def test_pathologically_deep_json_is_a_structured_error(self):
        with self.assertRaises(ci_levels.LevelsError) as ctx:
            ci_levels.parse_levels("[" * 100000, KNOWN, SECRETS)
        self.assertEqual(ctx.exception.errors[0].code, "invalid_json")

    def test_trailing_newlines_do_not_slip_past_the_patterns(self):
        self.assertEqual(codes(levels({"id": "a\n"})), ["bad_value"])
        self.assertIn("bad_value", codes(levels({"id": "a", "branches": ["main\n"]})))
        self.assertEqual(codes(levels({"id": "a"}, secrets=["NODE_AUTH_TOKEN\n"])), ["bad_value"])

    def test_secrets_must_be_declared_by_node_ci(self):
        self.assertEqual(codes(levels({"id": "a"}, secrets=["NOPE"])), ["unknown_secret"])
        self.assertEqual(codes(levels({"id": "a"}, secrets=["submodules-token"])), [])

    def test_push_branches_that_look_like_yaml_scalars_stay_strings(self):
        plan = parse(levels({"id": "a"}, pushBranches=["null", "1.0"]))
        on = yaml.safe_load(ci_levels.render_caller(plan, SHA, ""))[True]
        self.assertEqual(on["push"]["branches"], ["null", "1.0"])

    def test_bad_condition_is_rejected(self):
        self.assertEqual(codes(levels({"id": "a"}, {"id": "b", "needs": [{"level": "a", "condition": "failure"}]})), ["bad_value"])


class CommandLineTests(unittest.TestCase):
    def test_invalid_levels_exit_nonzero_with_structured_lines_and_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "cancel.json"
            bad.write_text(json.dumps(levels({"id": "a", "cancel": True})))
            result = run_generator(SHA, "--levels", str(bad))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertRegex(result.stderr, r"^unknown_field \$\.levels\[0\]\.cancel: ")

    def test_a_hostile_file_name_stays_on_one_comment_line(self):
        with tempfile.TemporaryDirectory() as directory:
            hostile = Path(directory) / "x\nenv:\n  INJECTED: yes\n#.json"
            hostile.write_text(json.dumps(levels({"id": "a"})))
            result = run_generator(SHA, "--levels", str(hostile))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(yaml.safe_load(result.stdout).get("env"))
        self.assertEqual(result.stdout.splitlines()[0].count("INJECTED"), 1)

    def test_a_missing_levels_file_is_a_structured_error(self):
        result = run_generator(SHA, "--levels", "/nonexistent/levels.json")
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertTrue(result.stderr.startswith("unreadable_file $: "))

    def test_sha_must_be_forty_hex(self):
        self.assertNotEqual(run_generator("main").returncode, 0)


if __name__ == "__main__":
    unittest.main()
