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
import ci_levels  # noqa: E402

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

    def test_malformed_booleans_are_rejected_and_fail_fast_false_is_not_yet_supported(self):
        self.assertEqual(codes(levels({"id": "a", "failFast": "true"})), ["bad_type"])
        self.assertEqual(codes(levels({"id": "a", "failFast": 1})), ["bad_type"])
        self.assertEqual(codes(levels({"id": "a", "failFast": False})), ["fail_fast_unsupported"])
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
