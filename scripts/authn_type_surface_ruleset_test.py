#!/usr/bin/env python3
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/authn-type-surface-ruleset.py"
SPEC = importlib.util.spec_from_file_location("authn_type_surface_ruleset", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SHA = "a" * 40
HEAD = "b" * 40


class AuthnTypeSurfaceRulesetTest(unittest.TestCase):
    def setUp(self):
        self.contract = MODULE.read_contract()

    def test_required_workflow_keeps_acquisition_canonical_and_execution_secretless(self):
        MODULE.validate_workflow()
        workflow = yaml.safe_load(MODULE.WORKFLOW.read_text(encoding="utf-8"))
        job = workflow["jobs"]["type-surface"]
        self.assertEqual(
            "Verjson/.github/.github/workflows/node-ci-protected.yml@"
            "3f8fb4bae1bdf0ced0b821e1b4277523f72a93b9",
            job["uses"],
        )
        self.assertEqual("read", workflow["permissions"]["statuses"])
        self.assertTrue(job["with"]["secretless-pr"])
        self.assertEqual(
            {"NODE_AUTH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"},
            job["secrets"],
        )

    def test_required_workflow_is_exact_generator_output(self):
        generated = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts/gen-authn-type-surface-required-workflow.py"),
                MODULE.NODE_CI_SHA,
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout

        self.assertEqual(
            generated,
            MODULE.WORKFLOW.read_text(encoding="utf-8"),
        )

    def test_required_workflow_pin_resolves_to_deferred_ci_contract(self):
        current = MODULE.read_pinned_node_ci_workflow(MODULE.NODE_CI_SHA)
        self.assertIsInstance(current["jobs"]["deferred-ci"], dict)

        with self.assertRaisesRegex(MODULE.ContractError, "canonical workflow"):
            MODULE.read_pinned_node_ci_workflow(
                "c973a841694a41bf0b9bcd70432f64850cba0850"
            )

    def test_required_workflow_rejects_pre_deferred_pin(self):
        workflow = yaml.safe_load(MODULE.WORKFLOW.read_text(encoding="utf-8"))
        workflow["jobs"]["type-surface"]["uses"] = (
            "Verjson/.github/.github/workflows/node-ci-protected.yml@"
            "c973a841694a41bf0b9bcd70432f64850cba0850"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "required.yml"
            path.write_text(yaml.safe_dump(workflow), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ContractError, "canonical workflow"):
                MODULE.validate_workflow(path)

    def test_required_workflow_rejects_missing_status_permission(self):
        workflow = yaml.safe_load(MODULE.WORKFLOW.read_text(encoding="utf-8"))
        del workflow["permissions"]["statuses"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "required.yml"
            path.write_text(yaml.safe_dump(workflow), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ContractError, "permissions drifted"):
                MODULE.validate_workflow(path)

    def test_required_baseline_is_allowed_by_the_current_consumer_package_policy(self):
        policy = json.loads((ROOT / "scripts/fixtures/authn-type-surface/package-policy.json").read_text())
        workflow = yaml.safe_load(MODULE.WORKFLOW.read_text(encoding="utf-8"))
        inputs = workflow["jobs"]["type-surface"]["with"]

        self.assertEqual("@verjson/authn", inputs["protected-type-surface-expected-package"])
        self.assertEqual("test:type-surface-compatibility", inputs["protected-type-surface-expected-script"])
        self.assertEqual(["3.0.0"], policy["compatibility"]["@verjson/authn"])
        self.assertNotIn("1.0.3", policy["compatibility"]["@verjson/authn"])

    def test_required_workflow_rejects_the_failed_trial_baseline(self):
        workflow = yaml.safe_load(MODULE.WORKFLOW.read_text(encoding="utf-8"))
        workflow["jobs"]["type-surface"]["with"]["protected-type-surface-expected-package"] = "@verjson/authz"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "required.yml"
            path.write_text(yaml.safe_dump(workflow), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ContractError, "type-surface package drifted"):
                MODULE.validate_workflow(path)

    def test_payload_uses_exact_repository_workflow_sha_and_only_release_bypass(self):
        payload = MODULE.render_payload(self.contract, SHA)
        self.assertEqual([{
            "actor_id": 4583107, "actor_type": "Integration", "bypass_mode": "always",
        }], payload["bypass_actors"])
        self.assertEqual(
            {"repository_ids": [1302124584]},
            payload["conditions"]["repository_id"],
        )
        self.assertNotIn("repository_name", payload["conditions"])
        self.assertNotIn("repository_property", payload["conditions"])
        self.assertEqual(
            [{
                "path": ".github/workflows/authn-type-surface-required.yml",
                "repository_id": 1269388380,
                "ref": "refs/heads/main",
                "sha": SHA,
            }],
            payload["rules"][0]["parameters"]["workflows"],
        )
        self.assertFalse(payload["rules"][0]["parameters"]["do_not_enforce_on_create"])

    def test_pull_request_rewritten_check_name_cannot_satisfy_required_workflow(self):
        spoof = {
            "event": "pull_request",
            "id": 43,
            "created_at": "2026-08-28T15:01:00Z",
            "status": "completed",
            "conclusion": "success",
            "head_sha": HEAD,
            "name": "Authn required type surface",
            "path": ".github/workflows/ci.yml",
            "workflow_url": (
                "https://api.github.com/repos/Verjson/verjson-authn/"
                "actions/workflows/ci.yml"
            ),
        }
        with self.assertRaisesRegex(
            MODULE.ContractError,
            "run path is not the protected required workflow",
        ):
            MODULE.validate_required_run(
                spoof,
                self.contract,
                SHA,
                HEAD,
                minimum_run_id=42,
                not_before=datetime(2026, 8, 28, 15, tzinfo=timezone.utc),
            )

    def test_required_workflow_run_must_have_required_workflow_url_and_exact_head(self):
        run = {
            "event": "pull_request",
            "id": 43,
            "created_at": "2026-08-28T15:01:00Z",
            "status": "completed",
            "conclusion": "success",
            "head_sha": HEAD,
            "path": ".github/workflows/authn-type-surface-required.yml",
            "workflow_url": (
                "https://api.github.com/repos/Verjson/verjson-authn/"
                "actions/required_workflows/42"
            ),
        }
        arguments = {
            "minimum_run_id": 42,
            "not_before": datetime(2026, 8, 28, 15, tzinfo=timezone.utc),
        }
        MODULE.validate_required_run(run, self.contract, SHA, HEAD, **arguments)
        stale = copy.deepcopy(run)
        stale["head_sha"] = "c" * 40
        with self.assertRaisesRegex(MODULE.ContractError, "run is stale"):
                MODULE.validate_required_run(
                    stale, self.contract, SHA, HEAD, **arguments
                )
        local = copy.deepcopy(run)
        local["workflow_url"] = (
            "https://api.github.com/repos/Verjson/verjson-authn/actions/workflows/42"
        )
        with self.assertRaisesRegex(MODULE.ContractError, "was not created"):
                MODULE.validate_required_run(
                    local, self.contract, SHA, HEAD, **arguments
                )
        old_id = copy.deepcopy(run)
        old_id["id"] = 42
        with self.assertRaisesRegex(MODULE.ContractError, "trigger snapshot"):
            MODULE.validate_required_run(
                old_id, self.contract, SHA, HEAD, **arguments
            )
        old_time = copy.deepcopy(run)
        old_time["created_at"] = "2026-08-28T14:59:59Z"
        with self.assertRaisesRegex(MODULE.ContractError, "ruleset activation"):
            MODULE.validate_required_run(
                old_time, self.contract, SHA, HEAD, **arguments
            )

    def test_live_identities_compare_case_insensitively_and_refuse_another_owner(self):
        # The organization login became `verJSON` on 2026-10-02 and the API reports
        # that display case; the reviewed contract spells it `Verjson` (#1779).
        # U+017F LATIN SMALL LETTER LONG S case-folds to `s`: `Verjſon` folds to `verjson`.
        homoglyph = "Verjſon"
        expected = MODULE.render_payload(self.contract, SHA)
        organization_rule = expected | {"source_type": "Organization", "id": 1}
        MODULE.validate_live_ruleset(organization_rule | {"source": "verJSON"}, expected)
        retired_expected = self.contract["consumer"]["retired_repository_ruleset"]
        retired = retired_expected | {"source_type": "Repository"}
        MODULE.validate_retired_ruleset(retired | {"source": "verJSON/verjson-authn"}, retired_expected)
        run = {
            "event": "pull_request", "id": 43, "created_at": "2026-10-14T18:08:32Z",
            "status": "completed", "conclusion": "success", "head_sha": HEAD,
            "path": ".github/workflows/authn-type-surface-required.yml",
            "workflow_url": "https://api.github.com/repos/verJSON/verjson-authn/actions/required_workflows/346741024",
        }
        arguments = {"minimum_run_id": 42, "not_before": datetime(2026, 10, 14, tzinfo=timezone.utc)}
        MODULE.validate_required_run(run, self.contract, SHA, HEAD, **arguments)
        for owner in ("Elsewhere", homoglyph):
            with self.subTest(owner=owner):
                with self.assertRaisesRegex(MODULE.ContractError, "source organization drifted"):
                    MODULE.validate_live_ruleset(organization_rule | {"source": owner}, expected)
                with self.assertRaisesRegex(MODULE.ContractError, "repository ruleset source drifted"):
                    MODULE.validate_retired_ruleset(retired | {"source": f"{owner}/verjson-authn"}, retired_expected)
                foreign = run | {"workflow_url": run["workflow_url"].replace("verJSON", owner)}
                with self.assertRaisesRegex(MODULE.ContractError, "was not created"):
                    MODULE.validate_required_run(foreign, self.contract, SHA, HEAD, **arguments)
        with self.assertRaisesRegex(MODULE.ContractError, "was not created"):
            MODULE.validate_required_run(
                run | {"workflow_url": run["workflow_url"].replace("verjson-authn", "verjson-authz")},
                self.contract, SHA, HEAD, **arguments,
            )

    def test_snapshot_counts_required_runs_reported_under_the_renamed_owner(self):
        live = MODULE.render_payload(self.contract, SHA) | {
            "source_type": "Organization", "source": "verJSON", "id": 21750617,
            "updated_at": "2026-10-14T00:00:00Z",
        }
        prefix = "https://api.github.com/repos/{}/actions/required_workflows/7"
        runs = {"workflow_runs": [
            {"id": 90, "path": ".github/workflows/authn-type-surface-required.yml",
             "workflow_url": prefix.format("verJSON/verjson-authn")},
            {"id": 95, "path": ".github/workflows/authn-type-surface-required.yml",
             "workflow_url": prefix.format("Elsewhere/verjson-authn")},
        ]}
        responses = {"orgs/Verjson/rulesets/21750617": live,
                     "repos/Verjson/verjson-authn/actions/runs?event=pull_request&per_page=100": runs}
        with mock.patch.object(MODULE, "validate_workflow"), \
                mock.patch.object(MODULE, "discover_state", return_value=[{"id": 21750617}]), \
                mock.patch.object(MODULE, "gh_json", side_effect=lambda path: responses[path]), \
                mock.patch("builtins.print") as printed:
            self.assertEqual(MODULE.main(["snapshot", "--workflow-sha", SHA]), 0)
        self.assertEqual(json.loads(printed.call_args.args[0])["pre_trigger_max_run_id"], 90)

    def test_live_rule_must_match_every_reviewed_field(self):
        expected = MODULE.render_payload(self.contract, SHA)
        live = expected | {"source_type": "Organization", "source": "Verjson", "id": 1}
        MODULE.validate_live_ruleset(live, expected)
        widened = copy.deepcopy(live)
        widened["bypass_actors"] = [{
            "actor_id": None,
            "actor_type": "OrganizationAdmin",
            "bypass_mode": "always",
        }]
        with self.assertRaisesRegex(MODULE.ContractError, "differs"):
            MODULE.validate_live_ruleset(widened, expected)
        with self.assertRaisesRegex(MODULE.ContractError, "missing required fields"):
            MODULE.validate_live_ruleset(
                {"source_type": "Organization", "source": "Verjson"}, expected
            )

    def test_retiring_repository_ruleset_must_match_the_exact_preimage(self):
        expected = self.contract["consumer"]["retired_repository_ruleset"]
        live = expected | {
            "source_type": "Repository",
            "source": "Verjson/verjson-authn",
        }
        MODULE.validate_retired_ruleset(live, expected)
        mutations = []
        for key, value in (
            ("enforcement", "active"),
            ("bypass_actors", [{
                "actor_id": None,
                "actor_type": "OrganizationAdmin",
                "bypass_mode": "always",
            }]),
            ("target", "tag"),
        ):
            mutation = copy.deepcopy(live)
            mutation[key] = value
            mutations.append(mutation)
        scope = copy.deepcopy(live)
        scope["conditions"]["ref_name"]["include"] = ["~ALL"]
        mutations.append(scope)
        check = copy.deepcopy(live)
        check["rules"][0]["parameters"]["required_status_checks"][0]["context"] = "spoof"
        mutations.append(check)
        integration = copy.deepcopy(live)
        integration["rules"][0]["parameters"]["required_status_checks"][0]["integration_id"] = 1
        mutations.append(integration)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(MODULE.ContractError, "preimage drifted"):
                    MODULE.validate_retired_ruleset(mutation, expected)

    def test_activation_rollback_failures_report_only_a_bounded_outcome(self):
        staged = {"enforcement": "disabled"}
        cases = (
            (MODULE.ContractError("secret mutation response"), None, "mutation failed"),
            ({"id": 99}, OSError("secret read response"), "could not be verified"),
        )
        for mutation_result, read_result, outcome in cases:
            with (
                self.subTest(outcome=outcome),
                mock.patch.object(
                    MODULE, "gh_json_input", side_effect=[mutation_result]
                ),
                mock.patch.object(MODULE, "gh_json", side_effect=[read_result]),
            ):
                with self.assertRaisesRegex(MODULE.ContractError, outcome) as raised:
                    MODULE.restore_disabled_after_activation_failure(99, staged)
                self.assertNotIn("secret", str(raised.exception))

    def test_apply_requires_acknowledgement_before_any_mutation(self):
        with (
            mock.patch.object(MODULE, "discover_state", side_effect=[[], []]),
            mock.patch.object(MODULE, "gh_json") as api,
        ):
            with self.assertRaisesRegex(MODULE.ContractError, "acknowledgement"):
                MODULE.main(["apply", "--workflow-sha", SHA])
        api.assert_not_called()

    def test_apply_creates_then_reads_back_the_exact_rule(self):
        expected = MODULE.render_payload(self.contract, SHA)
        live = expected | {
            "id": 99,
            "source_type": "Organization",
            "source": "Verjson",
        }
        staged = copy.deepcopy(live)
        staged["enforcement"] = "disabled"
        with (
            mock.patch.object(MODULE, "discover_state", side_effect=[[], []]),
            mock.patch.object(
                MODULE,
                "gh_json_input",
                side_effect=[{"id": 99}, {"id": 99}],
            ) as mutate,
            mock.patch.object(MODULE, "gh_json", side_effect=[staged, live]),
        ):
            result = MODULE.main([
                "apply",
                "--workflow-sha", SHA,
                "--ack", "APPLY-AUTHN-TYPE-SURFACE-1154",
            ])
        self.assertEqual(0, result)
        self.assertEqual("POST", mutate.call_args_list[0].args[0])
        self.assertEqual("disabled", mutate.call_args_list[0].args[2]["enforcement"])
        self.assertEqual("PUT", mutate.call_args_list[1].args[0])
        self.assertEqual("active", mutate.call_args_list[1].args[2]["enforcement"])

    def test_active_postimage_mismatch_restores_the_rule_disabled(self):
        expected = MODULE.render_payload(self.contract, SHA)
        live = expected | {
            "id": 99,
            "source_type": "Organization",
            "source": "Verjson",
        }
        staged = copy.deepcopy(live)
        staged["enforcement"] = "disabled"
        widened = copy.deepcopy(live)
        widened["conditions"]["repository_id"]["repository_ids"] = [1]
        with (
            mock.patch.object(MODULE, "discover_state", side_effect=[[], []]),
            mock.patch.object(
                MODULE,
                "gh_json_input",
                side_effect=[{"id": 99}, {"id": 99}, {"id": 99}],
            ) as mutate,
            mock.patch.object(
                MODULE, "gh_json", side_effect=[staged, widened, staged]
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.ContractError, "restored and verified disabled"
            ):
                MODULE.main([
                    "apply",
                    "--workflow-sha", SHA,
                    "--ack", "APPLY-AUTHN-TYPE-SURFACE-1154",
                ])
        self.assertEqual("disabled", mutate.call_args_list[2].args[2]["enforcement"])

    def test_post_activation_api_outage_attempts_and_verifies_disabled_rollback(self):
        expected = MODULE.render_payload(self.contract, SHA)
        live = expected | {
            "id": 99,
            "source_type": "Organization",
            "source": "Verjson",
        }
        staged = copy.deepcopy(live)
        staged["enforcement"] = "disabled"
        with (
            mock.patch.object(MODULE, "discover_state", side_effect=[[], []]),
            mock.patch.object(
                MODULE,
                "gh_json_input",
                side_effect=[{"id": 99}, {"id": 99}, {"id": 99}],
            ) as mutate,
            mock.patch.object(
                MODULE,
                "gh_json",
                side_effect=[
                    staged,
                    MODULE.ContractError("simulated post-activation API outage"),
                    staged,
                ],
            ),
        ):
            with self.assertRaisesRegex(
                MODULE.ContractError, "restored and verified disabled"
            ) as raised:
                MODULE.main([
                    "apply",
                    "--workflow-sha", SHA,
                    "--ack", "APPLY-AUTHN-TYPE-SURFACE-1154",
                ])
        self.assertNotIn("simulated", str(raised.exception))
        self.assertEqual("disabled", mutate.call_args_list[2].args[2]["enforcement"])

    def test_contract_rejects_missing_or_widened_release_bypass_on_both_rulesets(self):
        release = {"actor_id": 4583107, "actor_type": "Integration", "bypass_mode": "always"}
        invalid = [
            [],
            [release, release],
            [release, release | {"actor_id": 4693283}],
            [release | {"actor_id": 4693283}],
            [release | {"bypass_mode": "pull_request"}],
            [release | {"actor_type": "OrganizationAdmin"}],
        ]
        for target in ("consumer", "ruleset"):
            for actors in invalid:
                with self.subTest(target=target, actors=actors), tempfile.TemporaryDirectory() as directory:
                    contract = copy.deepcopy(self.contract)
                    ruleset = (contract["consumer"]["retired_repository_ruleset"]
                               if target == "consumer" else contract["ruleset"])
                    ruleset["bypass_actors"] = actors
                    path = Path(directory) / "contract.json"
                    path.write_text(json.dumps(contract), encoding="utf-8")
                    with self.assertRaises(MODULE.ContractError):
                        MODULE.read_contract(path)

    def staged_images(self):
        expected = MODULE.render_payload(self.contract, SHA) | {"enforcement": "evaluate"}
        previous = MODULE.render_payload(self.contract, MODULE.PREVIOUS_WORKFLOW_SHA) | {
            "enforcement": "evaluate", "bypass_actors": [],
        }
        metadata = {"id": 21750617, "source_type": "Organization", "source": "Verjson"}
        return previous | metadata, expected, expected | metadata

    def test_stage_adds_only_release_bypass_and_repins_without_activating(self):
        previous, expected, staged = self.staged_images()
        with (
            mock.patch.object(MODULE, "discover_state", return_value=[{"id": 21750617}]),
            mock.patch.object(MODULE, "gh_json", side_effect=[previous, previous, staged]),
            mock.patch.object(MODULE, "gh_json_input") as mutate,
        ):
            self.assertEqual(0, MODULE.main([
                "stage-release-bypass", "--workflow-sha", SHA,
                "--ack", "APPLY-AUTHN-TYPE-SURFACE-1154",
            ]))
        mutate.assert_called_once_with("PUT", "orgs/Verjson/rulesets/21750617", expected)
        self.assertEqual("evaluate", expected["enforcement"])

    def test_stage_rejects_unreviewed_preimages_before_mutation(self):
        previous, expected, _ = self.staged_images()
        cases = [
            None,
            previous | {"id": 1},
            previous | {"enforcement": "active"},
            previous | {"bypass_actors": MODULE.RELEASE_BYPASS},
            previous | {"conditions": {"ref_name": {"include": ["~ALL"]}}},
            previous | {"source": "another-org"},
        ]
        wrong_pin = copy.deepcopy(previous)
        wrong_pin["rules"][0]["parameters"]["workflows"][0]["sha"] = "c" * 40
        cases.append(wrong_pin)
        for live in cases:
            with self.subTest(live=live), mock.patch.object(MODULE, "gh_json_input") as mutate:
                with self.assertRaises(MODULE.ContractError):
                    MODULE.stage_release_bypass(
                        self.contract, expected, live, "APPLY-AUTHN-TYPE-SURFACE-1154"
                    )
                mutate.assert_not_called()

    def test_stage_requires_ack_and_rechecks_for_concurrent_drift(self):
        previous, expected, _ = self.staged_images()
        with mock.patch.object(MODULE, "gh_json_input") as mutate:
            with self.assertRaisesRegex(MODULE.ContractError, "acknowledgement"):
                MODULE.stage_release_bypass(self.contract, expected, previous, "")
            mutate.assert_not_called()
        with (
            mock.patch.object(MODULE, "gh_json", return_value=previous | {"enforcement": "active"}),
            mock.patch.object(MODULE, "gh_json_input") as mutate,
        ):
            with self.assertRaises(MODULE.ContractError):
                MODULE.stage_release_bypass(
                    self.contract, expected, previous, "APPLY-AUTHN-TYPE-SURFACE-1154"
                )
            mutate.assert_not_called()

    def test_stage_rejects_postimage_drift_and_is_idempotent_after_success(self):
        previous, expected, staged = self.staged_images()
        with (
            mock.patch.object(MODULE, "gh_json", side_effect=[previous, staged | {"bypass_actors": []}]),
            mock.patch.object(MODULE, "gh_json_input"),
        ):
            with self.assertRaises(MODULE.ContractError):
                MODULE.stage_release_bypass(
                    self.contract, expected, previous, "APPLY-AUTHN-TYPE-SURFACE-1154"
                )
        with mock.patch.object(MODULE, "gh_json_input") as mutate:
            self.assertEqual(0, MODULE.stage_release_bypass(
                self.contract, expected, staged, "APPLY-AUTHN-TYPE-SURFACE-1154"
            ))
            mutate.assert_not_called()

    def test_evaluate_dry_run_accepts_staged_rule_without_mutation(self):
        _, _, staged = self.staged_images()
        with (
            mock.patch.object(MODULE, "discover_state", return_value=[{"id": 21750617}]),
            mock.patch.object(MODULE, "gh_json", return_value=staged),
            mock.patch.object(MODULE, "gh_json_input") as mutate,
        ):
            self.assertEqual(0, MODULE.main(["dry-run", "--workflow-sha", SHA, "--evaluate"]))
            mutate.assert_not_called()
        with self.assertRaisesRegex(MODULE.ContractError, "read-only modes"):
            MODULE.main(["apply", "--workflow-sha", SHA, "--evaluate"])

    def test_duplicate_contract_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.json"
            text = MODULE.CONTRACT.read_text(encoding="utf-8")
            path.write_text(text.replace('"schema_version": 1,',
                                         '"schema_version": 1, "schema_version": 1,'),
                            encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ContractError, "duplicate key"):
                MODULE.read_contract(path)


class TimestampParsingTest(unittest.TestCase):
    # GitHub reports run timestamps as `...Z` and ruleset `updated_at` in an
    # offset form; both must parse and compare in UTC (#1630).
    def parse(self, value):
        return MODULE.parse_timestamp(value, "ruleset updated_at")

    def test_z_and_offset_forms_compare_as_the_same_instant_in_utc(self):
        zulu = self.parse("2026-09-27T00:16:20Z")
        offset = self.parse("2026-09-26T20:16:20.000-04:00")
        self.assertEqual(zulu, offset)
        self.assertEqual(str(zulu.tzinfo), "UTC")
        self.assertLess(offset, self.parse("2026-09-27T00:16:39Z"))

    def test_naive_empty_and_malformed_values_are_rejected(self):
        for value in ("2026-09-27T00:16:20", "", "not-a-time", "2026-13-40T00:00:00Z", None, 7):
            with self.subTest(value=value), self.assertRaisesRegex(MODULE.ContractError, "ruleset updated_at is invalid"):
                self.parse(value)


if __name__ == "__main__":
    unittest.main()
