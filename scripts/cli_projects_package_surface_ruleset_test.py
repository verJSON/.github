#!/usr/bin/env python3
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import re
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/cli-projects-package-surface-ruleset.py"
SPEC = importlib.util.spec_from_file_location("cli_projects_ruleset", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
GENERATOR_SPEC = importlib.util.spec_from_file_location(
    "gen_node_required_workflow", ROOT / "scripts/gen-node-required-workflow.py"
)
GENERATOR_MODULE = importlib.util.module_from_spec(GENERATOR_SPEC)
GENERATOR_SPEC.loader.exec_module(GENERATOR_MODULE)
SHA = "a" * 40
HEAD = "b" * 40
PREVIOUS = "c" * 40


class CliProjectsPackageSurfaceRulesetTest(unittest.TestCase):
    def setUp(self):
        self.contract = MODULE.read_contract()

    def test_rollout_guard_accepts_legacy_only_while_default_main_matches(self):
        generated_bytes = b"current generated consumer workflow\n"
        legacy_bytes = b"previous published consumer workflow\n"
        other_bytes = b"unreviewed consumer workflow\n"
        legacy_sha256 = hashlib.sha256(legacy_bytes).hexdigest()

        self.assertTrue(
            MODULE.is_accepted_consumer_workflow_for_rollout(
                generated_bytes, generated_bytes, legacy_sha256
            )
        )
        self.assertTrue(
            MODULE.is_accepted_consumer_workflow_for_rollout(
                legacy_bytes, generated_bytes, legacy_sha256, legacy_bytes
            )
        )
        self.assertFalse(
            MODULE.is_accepted_consumer_workflow_for_rollout(
                legacy_bytes, generated_bytes, legacy_sha256, other_bytes
            )
        )
        self.assertFalse(
            MODULE.is_accepted_consumer_workflow_for_rollout(
                legacy_bytes, generated_bytes, legacy_sha256
            )
        )
        self.assertFalse(
            MODULE.is_accepted_consumer_workflow_for_rollout(
                other_bytes, generated_bytes, legacy_sha256
            )
        )
        self.assertFalse(
            MODULE.is_accepted_consumer_workflow_for_rollout(
                legacy_bytes,
                generated_bytes,
                hashlib.sha256(other_bytes).hexdigest(),
            )
        )
        for malformed_sha256 in ("a" * 63, "A" * 64, "g" * 64):
            with self.subTest(malformed_sha256=malformed_sha256):
                self.assertFalse(
                    MODULE.is_accepted_consumer_workflow_for_rollout(
                        legacy_bytes, generated_bytes, malformed_sha256
                    )
                )

    def test_admission_rejects_legacy_caller_without_reading_default_main(self):
        config = GENERATOR_MODULE.load_config(MODULE.GENERATOR_CONFIG)
        config_path = Path("config/cli-projects-required-node-ci.json")
        generated_bytes = GENERATOR_MODULE.render_consumer(
            config, config_path
        ).encode("utf-8")
        legacy_bytes = b"legacy consumer workflow\n"
        workflow = yaml.safe_load(GENERATOR_MODULE.render(config, config_path))
        admission_script = workflow["jobs"]["admission"]["steps"][0]["run"]
        head_sha = "b" * 40

        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            candidate_path = temp / "candidate.yml"
            output_path = temp / "output"
            fake_gh = temp / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env python3\n"
                "import os\n"
                "from pathlib import Path\n"
                "import sys\n"
                "args = ' '.join(sys.argv[1:])\n"
                "if os.environ.get('GH_TOKEN') != 'bounded-test-token':\n"
                "    raise SystemExit(70)\n"
                "if 'actions/runs/' in args:\n"
                "    print(os.environ['RUN_RECORD'])\n"
                "elif 'pulls/' in args:\n"
                "    print(os.environ['PR_RECORD'])\n"
                "elif 'contents/.github/workflows/ci.yml' in args:\n"
                "    if f\"ref={os.environ['HEAD_SHA']}\" in args:\n"
                "        sys.stdout.buffer.write(Path(os.environ['CANDIDATE_FILE']).read_bytes())\n"
                "    else:\n"
                "        raise SystemExit(72)\n"
                "else:\n"
                "    raise SystemExit(73)\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)

            def execute(candidate, *, base_ref="main"):
                candidate_path.write_bytes(candidate)
                output_path.unlink(missing_ok=True)
                environment = {
                    **os.environ,
                    "PATH": f"{directory}:{os.environ['PATH']}",
                    "GH_TOKEN": "bounded-test-token",
                    "REPOSITORY": "Verjson/verjson-cli-projects",
                    "RUN_ID": "33306897795",
                    "RUN_RECORD": f"pull_request\t{head_sha}\t1\t114",
                    "PR_RECORD": (
                        f"114\topen\tVerjson/verjson-cli-projects\t"
                        f"{head_sha}\t{base_ref}"
                    ),
                    "CONSUMER_WORKFLOW_SHA256": hashlib.sha256(
                        generated_bytes
                    ).hexdigest(),
                    "HEAD_SHA": head_sha,
                    "CANDIDATE_FILE": str(candidate_path),
                    "GITHUB_OUTPUT": str(output_path),
                }
                return subprocess.run(
                    ["bash", "-c", admission_script],
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            current = execute(generated_bytes)
            self.assertEqual(0, current.returncode, current.stderr)
            legacy = execute(legacy_bytes)
            self.assertNotEqual(
                0,
                legacy.returncode,
                "legacy PR caller must fail even when no default-main state is read",
            )
            non_main = execute(generated_bytes, base_ref="release")
            self.assertNotEqual(
                0, non_main.returncode,
                "admission must reject pull requests that are not based on main",
            )

    def test_workflow_is_exact_repository_hosted_credentialless_boundary(self):
        MODULE.validate_workflow()

    def test_caller_ref_input_and_credential_mutations_are_rejected(self):
        source = MODULE.WORKFLOW.read_text(encoding="utf-8")
        mutations = (
            source.replace("node-ci-protected.yml@", "node-ci-protected.yml@aaaaaaaa", 1),
            source.replace("secretless-pr: true", "secretless-pr: false", 1),
            source.replace("NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}", "NODE_AUTH_TOKEN: mutation", 1),
            source.replace("needs: admission", "needs: []", 1),
        )
        for candidate in mutations:
            self.assertNotEqual(source, candidate)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8") as stream:
                stream.write(candidate)
                stream.flush()
                with self.subTest(candidate=candidate[:80]), self.assertRaisesRegex(
                    MODULE.ContractError, "differs from canonical generator output"
                ):
                    MODULE.validate_workflow(Path(stream.name))

    def test_identity_admission_rejects_every_untrusted_identity_mutation(self):
        workflow = yaml.safe_load(MODULE.WORKFLOW.read_text(encoding="utf-8"))
        script = workflow["jobs"]["admission"]["steps"][0]["run"]
        self.assertEqual(
            1,
            script.count('>>"$GITHUB_OUTPUT"'),
            "admission outputs must use one grouped append",
        )
        head = "b" * 40
        base = {
            "REPOSITORY": "Verjson/verjson-cli-projects",
            "RUN_ID": "33306897795",
        }
        with tempfile.TemporaryDirectory() as directory:
            consumer = subprocess.run(
                [str(MODULE.GENERATOR), "--consumer",
                 "config/cli-projects-required-node-ci.json"],
                cwd=MODULE.ROOT, capture_output=True, check=True,
            ).stdout
            consumer_path = Path(directory) / "consumer.yml"
            output_path = Path(directory) / "output"
            consumer_path.write_bytes(consumer)
            base["CONSUMER_WORKFLOW_SHA256"] = hashlib.sha256(consumer).hexdigest()
            fake_gh = Path(directory) / "gh"
            fake_gh.write_text(
                "#!/bin/sh\n"
                "[ \"${GH_TOKEN:-}\" = bounded-test-token ] || exit 70\n"
                "[ \"${API_FAILURE:-}\" != 1 ] || exit 71\n"
                "case \"$*\" in\n"
                "  *actions/runs/*) printf '%s\\n' \"$RUN_RECORD\" ;;\n"
                "  *pulls/*) printf '%s\\n' \"$PR_RECORD\" ;;\n"
                "  *git/ref/heads/main*) printf '%s\\n' \"$MAIN_REF_SHA\" ;;\n"
                "  *contents/.github/workflows/ci.yml*) cat \"$CONSUMER_FILE\" ;;\n"
                "  *) exit 72 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)

            def execute(values, **changes):
                environment = {
                    **os.environ,
                    **values,
                    "PATH": f"{directory}:{os.environ['PATH']}",
                    "GH_TOKEN": "bounded-test-token",
                    "RUN_RECORD": f"pull_request\t{head}\t1\t114",
                    "PR_RECORD": f"114\topen\tVerjson/verjson-cli-projects\t{head}\tmain",
                    "MAIN_REF_SHA": "c" * 40,
                    "CONSUMER_FILE": str(consumer_path),
                    "GITHUB_OUTPUT": str(output_path),
                    **changes,
                }
                return subprocess.run(
                    ["bash", "-c", script], env=environment,
                    capture_output=True, text=True, check=False,
                ).returncode

            self.assertEqual(0, execute(base))
            output_path.unlink(missing_ok=True)
            self.assertEqual(0, execute(base))
            mutated = consumer.replace(b"  push:\n", b"  pull_request:\n", 1)
            consumer_path.write_bytes(mutated)
            self.assertNotEqual(0, execute(base))
            consumer_path.write_bytes(consumer + b"\n")
            self.assertNotEqual(0, execute(base))
            consumer_path.write_bytes(consumer)
            mutations = (
                ({**base, "REPOSITORY": "Verjson/other"}, {}),
                (base, {"RUN_RECORD": f"push\t{head}\t1\t114"}),
                (base, {"RUN_RECORD": f"pull_request\t{head}\t0\t"}),
                (base, {"RUN_RECORD": f"pull_request\t{head}\t2\t114"}),
                (base, {"RUN_RECORD": "pull_request\tnot-a-sha\t1\t114"}),
                (base, {"RUN_RECORD": f"pull_request\t{head}\t1\tbad"}),
                (base, {"PR_RECORD": f"114\tclosed\tVerjson/verjson-cli-projects\t{head}"}),
                (base, {"PR_RECORD": f"114\topen\tattacker/fork\t{head}"}),
                (base, {"PR_RECORD": f"114\topen\tVerjson/verjson-cli-projects\t{'e' * 40}"}),
                (base, {"API_FAILURE": "1"}),
                (base, {"GH_TOKEN": "ambient-token"}),
            )
            for values, changes in mutations:
                with self.subTest(values=values, changes=changes):
                    self.assertNotEqual(0, execute(values, **changes))

    def test_consumer_caller_is_push_only_and_exactly_generated(self):
        result = subprocess.run(
            [str(MODULE.GENERATOR), "--consumer",
             "config/cli-projects-required-node-ci.json"],
            cwd=MODULE.ROOT, capture_output=True, text=True, check=True,
        )
        consumer = yaml.safe_load(result.stdout)
        self.assertEqual({"push": {"branches": ["main"]}}, consumer[True])
        self.assertNotIn("pull_request", result.stdout)
        self.assertNotIn("pull_request_target", result.stdout)

    def test_rollout_rejects_default_branch_consumer_caller_drift(self):
        generated = subprocess.run(
            [str(MODULE.GENERATOR), "--consumer",
             "config/cli-projects-required-node-ci.json"],
            cwd=MODULE.ROOT, capture_output=True, check=True,
        ).stdout
        with mock.patch.object(
            MODULE.subprocess, "run",
            side_effect=[
                subprocess.CompletedProcess([], 0, stdout=generated),
                subprocess.CompletedProcess([], 0, stdout=generated + b"# mutation\n"),
            ],
        ), mock.patch.object(
            MODULE, "gh_json", return_value={"object": {"sha": HEAD}}
        ), self.assertRaisesRegex(
            MODULE.ContractError, "not the reviewed push-only generated image"
        ):
            MODULE.verify_consumer_workflow(self.contract)

    def test_consumer_workflow_read_is_bound_to_unchanged_branch_commit(self):
        generated = subprocess.run(
            [str(MODULE.GENERATOR), "--consumer",
             "config/cli-projects-required-node-ci.json"],
            cwd=MODULE.ROOT, capture_output=True, check=True,
        ).stdout
        with (
            mock.patch.object(
                MODULE.subprocess, "run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, stdout=generated),
                    subprocess.CompletedProcess([], 0, stdout=generated),
                ],
            ),
            mock.patch.object(
                MODULE, "gh_json",
                side_effect=[{"object": {"sha": HEAD}}, {"object": {"sha": "c" * 40}}],
            ),
            self.assertRaisesRegex(MODULE.ContractError, "moved during transaction"),
        ):
            MODULE.verify_consumer_workflow(self.contract)

    def test_generator_rejects_mutable_or_injectable_configuration(self):
        config = json.loads(MODULE.GENERATOR_CONFIG.read_text(encoding="utf-8"))
        mutations = (
            {**config, "protected_node_ci_sha": "main"},
            {**config, "consumer_node_ci_sha": "main"},
            {**config, "repository": "Verjson/other"},
            {**config, "repository": "Verjson/verjson-cli-projects\npermissions: write"},
            {**config, "approved_internal_packages": ["@verjson/eslint-config\nrun: id"]},
            {**config, "scripts": ["test\nrun: id"]},
        )
        for mutation in mutations:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8") as stream:
                json.dump(mutation, stream)
                stream.flush()
                result = subprocess.run(
                    [str(MODULE.GENERATOR), stream.name], cwd=MODULE.ROOT,
                    capture_output=True, text=True, check=False,
                )
                with self.subTest(mutation=mutation):
                    self.assertEqual(2, result.returncode)

    def test_payload_binds_numeric_consumer_and_immutable_protected_workflow(self):
        payload = MODULE.render_payload(self.contract, SHA)
        self.assertEqual([], payload["bypass_actors"])
        self.assertEqual({"repository_ids": [1277452690]}, payload["conditions"]["repository_id"])
        self.assertEqual([
            {
                "type": "workflows",
                "parameters": {
                    "do_not_enforce_on_create": False,
                    "workflows": [{
                        "path": ".github/workflows/cli-projects-package-surface-required.yml",
                        "repository_id": 1269388380,
                        "ref": "refs/heads/main",
                        "sha": SHA,
                    }],
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [{
                        "context": "admission",
                        "integration_id": 15368,
                    }],
                    "strict_required_status_checks_policy": True,
                },
            },
        ], payload["rules"])

    def test_freshness_check_is_bound_to_current_head_and_required_workflow_run(self):
        not_before = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
        check = {
            "name": "admission",
            "head_sha": HEAD,
            "status": "completed",
            "conclusion": "success",
            "app": {"id": 15368},
            "details_url": "https://github.com/Verjson/verjson-cli-projects/actions/runs/42/job/7",
            "started_at": "2026-09-29T12:01:00Z",
            "completed_at": "2026-09-29T12:05:00Z",
        }
        MODULE.validate_freshness_check(
            [check], self.contract, HEAD, 42, not_before
        )

        stale_or_untrusted_checks = (
            {**check, "head_sha": "c" * 40},
            {**check, "conclusion": "failure"},
            {**check, "app": {"id": 1}},
            {**check, "details_url": "https://github.com/Verjson/verjson-cli-projects/actions/runs/41/job/7"},
            {**check, "completed_at": "2026-09-29T11:59:00Z"},
        )
        for stale_check in stale_or_untrusted_checks:
            with self.subTest(stale_check=stale_check), self.assertRaises(
                MODULE.ContractError
            ):
                MODULE.validate_freshness_check(
                    [stale_check], self.contract, HEAD, 42, not_before
                )

    def test_live_identities_compare_case_insensitively_and_refuse_another_owner(self):
        # The organization login became `verJSON` on 2026-10-02 and the API reports
        # that display case; the reviewed contract spells it `Verjson` (#1779).
        # U+017F LATIN SMALL LETTER LONG S case-folds to `s`: `Verjſon` folds to `verjson`.
        homoglyph = "Verjſon"
        expected = MODULE.render_payload(self.contract, SHA)
        organization_rule = expected | {"source_type": "Organization", "id": 1}
        MODULE.validate_org_ruleset(organization_rule | {"source": "verJSON"}, expected)
        repository_expected = MODULE.expected_repository_ruleset(self.contract)
        repository_rule = self.contract["consumer"]["repository_ruleset"] | {"source_type": "Repository"}
        MODULE.validate_repository_ruleset(
            repository_rule | {"source": "verJSON/verjson-cli-projects"}, repository_expected
        )
        not_before = datetime(2026, 10, 14, tzinfo=timezone.utc)
        run = {
            "event": "pull_request", "status": "completed", "conclusion": "success",
            "head_sha": HEAD, "id": 42, "created_at": "2026-10-14T12:01:00Z",
            "path": ".github/workflows/cli-projects-package-surface-required.yml",
            "workflow_url": "https://api.github.com/repos/verJSON/verjson-cli-projects/actions/required_workflows/7",
        }
        MODULE.validate_required_run(run, self.contract, SHA, HEAD, 41, not_before)
        check = {
            "name": "admission", "head_sha": HEAD, "status": "completed", "conclusion": "success",
            "app": {"id": 15368},
            "details_url": "https://github.com/verJSON/verjson-cli-projects/actions/runs/42/job/7",
            "started_at": "2026-10-14T12:01:00Z", "completed_at": "2026-10-14T12:05:00Z",
        }
        MODULE.validate_freshness_check([check], self.contract, HEAD, 42, not_before)
        for owner in ("Elsewhere", homoglyph):
            with self.subTest(owner=owner):
                with self.assertRaisesRegex(MODULE.ContractError, "ruleset organization drifted"):
                    MODULE.validate_org_ruleset(organization_rule | {"source": owner}, expected)
                with self.assertRaisesRegex(MODULE.ContractError, "repository ruleset source drifted"):
                    MODULE.validate_repository_ruleset(
                        repository_rule | {"source": f"{owner}/verjson-cli-projects"}, repository_expected
                    )
                foreign_run = run | {"workflow_url": run["workflow_url"].replace("verJSON", owner)}
                with self.assertRaisesRegex(MODULE.ContractError, "was not created"):
                    MODULE.validate_required_run(foreign_run, self.contract, SHA, HEAD, 41, not_before)
                foreign_check = check | {"details_url": check["details_url"].replace("verJSON", owner)}
                with self.assertRaisesRegex(MODULE.ContractError, "strict admission check is absent"):
                    MODULE.validate_freshness_check([foreign_check], self.contract, HEAD, 42, not_before)

    def test_contract_rejects_consumer_preimage_or_receipt_scope_drift(self):
        for mutation, message in (
            (("consumer", "repository_ruleset", "id", 1), "preimage drifted"),
            (("rollout", "required_run", "workflow_url_prefix", "https://example.invalid/"),
             "receipt contract drifted"),
        ):
            candidate = copy.deepcopy(self.contract)
            *keys, value = mutation
            target = candidate
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            with tempfile.NamedTemporaryFile("w", encoding="utf-8") as stream:
                json.dump(candidate, stream)
                stream.flush()
                with self.subTest(mutation=mutation), self.assertRaisesRegex(
                    MODULE.ContractError, message
                ):
                    MODULE.read_contract(Path(stream.name))

    def test_pull_request_owned_check_name_cannot_spoof_required_workflow_receipt(self):
        run = {
            "event": "pull_request", "status": "completed", "conclusion": "success",
            "head_sha": HEAD, "id": 2, "created_at": "2026-08-29T12:01:00Z",
            "path": ".github/workflows/ci.yml",
            "workflow_url": "https://api.github.com/repos/Verjson/verjson-cli-projects/actions/workflows/ci.yml",
        }
        with self.assertRaisesRegex(MODULE.ContractError, "path is not protected"):
            MODULE.validate_required_run(
                run, self.contract, SHA, HEAD, 1,
                datetime(2026, 8, 29, 12, tzinfo=timezone.utc),
            )

    def test_obsolete_repository_ruleset_must_remain_exactly_disabled(self):
        before = self.contract["consumer"]["repository_ruleset"] | {
            "source_type": "Repository", "source": "Verjson/verjson-cli-projects",
        }
        MODULE.validate_repository_ruleset(
            before, MODULE.expected_repository_ruleset(self.contract)
        )
        mutations = []
        for path, value in (
            (("enforcement",), "active"),
            (("bypass_actors",), [{"actor_type": "OrganizationAdmin"}]),
            (("conditions", "ref_name", "include"), ["~ALL"]),
            (("rules", 0, "parameters", "required_status_checks", 0, "context"), "spoof"),
        ):
            candidate = copy.deepcopy(before)
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            mutations.append(candidate)

        for candidate in mutations:
            with self.subTest(candidate=candidate), self.assertRaisesRegex(
                MODULE.ContractError, "preimage drifted"
            ):
                MODULE.validate_repository_ruleset(
                    candidate, MODULE.expected_repository_ruleset(self.contract)
                )

    def test_obsolete_repository_activation_mode_is_removed(self):
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            MODULE.main(["activate-repository", "--workflow-sha", SHA])

    def test_apply_requires_acknowledgement_before_mutation(self):
        with (
            mock.patch.object(MODULE, "discover_state", return_value=([], HEAD)),
            mock.patch.object(MODULE, "gh_json_input") as mutate,
            self.assertRaisesRegex(MODULE.ContractError, "acknowledgement"),
        ):
            MODULE.main(["apply", "--workflow-sha", SHA])
        mutate.assert_not_called()

    def test_organization_activation_mismatch_restores_disabled(self):
        expected = MODULE.render_payload(self.contract, SHA)
        staged = copy.deepcopy(expected)
        staged["enforcement"] = "disabled"
        live_staged = staged | {"id": 9, "source_type": "Organization", "source": "Verjson"}
        widened = expected | {"id": 9, "source_type": "Organization", "source": "Verjson"}
        widened = copy.deepcopy(widened)
        widened["conditions"]["repository_id"]["repository_ids"] = [1]
        with (
            mock.patch.object(MODULE, "discover_state", side_effect=[([], HEAD), ([], HEAD)]),
            mock.patch.object(MODULE, "assert_consumer_branch_sha"),
            mock.patch.object(MODULE, "gh_json_input", side_effect=[{"id": 9}, {}, {}]) as mutate,
            mock.patch.object(
                MODULE, "gh_json",
                side_effect=[live_staged, widened, widened, live_staged],
            ),
            self.assertRaisesRegex(MODULE.ContractError, "restored and verified disabled"),
        ):
            MODULE.main([
                "apply", "--workflow-sha", SHA,
                "--ack", "ROTATE-CLI-PROJECTS-REQUIRED-WORKFLOW-1187",
            ])
        self.assertEqual("disabled", mutate.call_args_list[2].args[2]["enforcement"])

    def test_apply_rotates_only_the_exact_reviewed_prior_workflow(self):
        expected = MODULE.render_payload(self.contract, SHA)
        previous = MODULE.render_payload(
            self.contract, PREVIOUS,
            include_freshness=False,
        )
        live_previous = previous | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        live_expected = expected | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        with (
            mock.patch.object(MODULE, "admit_previous_revision") as admit,
            mock.patch.object(MODULE, "discover_state", return_value=([{"id": 9}], HEAD)),
            mock.patch.object(MODULE, "assert_consumer_branch_sha"),
            mock.patch.object(MODULE, "gh_json", side_effect=[live_previous, live_previous, live_expected]),
            mock.patch.object(MODULE, "gh_json_input", return_value={}) as mutate,
        ):
            MODULE.main([
                "apply", "--workflow-sha", SHA,
                "--ack", "ROTATE-CLI-PROJECTS-REQUIRED-WORKFLOW-1187",
            ])
        self.assertEqual(expected, mutate.call_args.args[2])
        admit.assert_called_once_with(PREVIOUS, SHA)

    def test_dry_run_reads_the_predecessor_from_the_live_ruleset_and_admits_it(self):
        # No committed record names the live revision: it is read from the ruleset
        # and admitted against the rotation target, so a rotation never needs a
        # follow-up record update (#1824).
        for include_freshness, enforcement in (
            (True, "active"), (False, "active"), (False, "disabled"), (True, "disabled"),
        ):
            image = MODULE.render_payload(
                self.contract, PREVIOUS, include_freshness=include_freshness,
            )
            image["enforcement"] = enforcement
            live = image | {"id": 9, "source_type": "Organization", "source": "Verjson"}
            with (
                self.subTest(include_freshness=include_freshness, enforcement=enforcement),
                mock.patch.object(MODULE, "discover_state", return_value=([{"id": 9}], HEAD)),
                mock.patch.object(MODULE, "gh_json", return_value=live),
                mock.patch.object(MODULE, "admit_previous_revision") as admit,
                mock.patch.object(MODULE, "gh_json_input") as mutate,
            ):
                self.assertEqual(0, MODULE.main(["dry-run", "--workflow-sha", SHA]))
            admit.assert_called_once_with(PREVIOUS, SHA)
            mutate.assert_not_called()

    def test_apply_refuses_an_unadmitted_predecessor_before_any_mutation(self):
        live = MODULE.render_payload(self.contract, PREVIOUS) | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        for mode in ("dry-run", "apply"):
            with (
                self.subTest(mode=mode),
                mock.patch.object(MODULE, "discover_state", return_value=([{"id": 9}], HEAD)),
                mock.patch.object(
                    MODULE, "admit_previous_revision",
                    side_effect=MODULE.ContractError("not its generator's output"),
                ),
                mock.patch.object(MODULE, "gh_json", return_value=live),
                mock.patch.object(MODULE, "gh_json_input") as mutate,
                self.assertRaisesRegex(MODULE.ContractError, "generator's output"),
            ):
                MODULE.main([
                    mode, "--workflow-sha", SHA,
                    "--ack", "ROTATE-CLI-PROJECTS-REQUIRED-WORKFLOW-1187",
                ])
            mutate.assert_not_called()

    def test_discovery_refuses_a_target_whose_protected_lanes_predate_the_floor(self):
        with (
            mock.patch.object(MODULE, "validate_workflow"),
            mock.patch.object(MODULE, "verify_canonical_bytes"),
            mock.patch.object(MODULE, "canonical_compare", return_value="behind") as compare,
            mock.patch.object(MODULE, "verify_consumer_workflow") as consumer,
            self.assertRaisesRegex(MODULE.ContractError, "security floor"),
        ):
            MODULE.discover_state(self.contract, SHA)
        compare.assert_called_once_with(
            "21eb48e713bd8304951fb7b26a900d08359af900",
            "576707fe0be0dabe43ea57c504575f8cda88b0d2",
        )
        consumer.assert_not_called()

    def test_audit_covers_every_file_the_workflow_executes_from_its_own_revision(self):
        # The package-surface job checks the verifier out at `github.workflow_sha`, so
        # the verifier, its sibling imports, and its config are runtime bytes of the
        # live revision; a change to any of them is a pending rotation.
        workflow = yaml.safe_load(MODULE.WORKFLOW.read_text(encoding="utf-8"))
        executed = set()
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                for token in str(step.get("run", "")).split():
                    if token.startswith("policy/"):
                        executed.add(token.removeprefix("policy/"))
        self.assertEqual({"scripts/cli-projects-package-surface.py"}, executed)
        verifier = (ROOT / "scripts/cli-projects-package-surface.py").read_text(encoding="utf-8")
        siblings = {
            f"scripts/{name}.py"
            for name in re.findall(r"^from (\w+) import", verifier, re.MULTILINE)
            if (ROOT / f"scripts/{name}.py").exists()
        }
        configs = set(re.findall(r'"(config/[\w./-]+)"', verifier))
        self.assertEqual(
            {MODULE.WORKFLOW_PATH} | executed | siblings | configs,
            set(MODULE.RUNTIME_PATHS),
        )

    def test_regenerate_at_reproduces_the_generated_workflow_from_revision_bytes(self):
        reads = []

        def canonical_file(path, ref):
            reads.append((path, ref))
            return (ROOT / path).read_bytes()

        with mock.patch.object(MODULE, "canonical_file", side_effect=canonical_file):
            output = MODULE.regenerate_at(PREVIOUS, b"ignored")
        self.assertEqual(MODULE.WORKFLOW.read_bytes(), output)
        self.assertEqual(
            sorted((path, PREVIOUS) for path in MODULE.GENERATOR_INPUTS), sorted(reads)
        )

    def test_audit_checks_the_live_revision_against_protected_main_read_only(self):
        live = MODULE.render_payload(self.contract, PREVIOUS) | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        with (
            mock.patch.object(MODULE, "list_named_rulesets", return_value=[{"id": 9}]),
            mock.patch.object(MODULE, "gh_json", return_value=live),
            mock.patch.object(MODULE, "audit_live_revision") as audit,
            mock.patch.object(MODULE, "gh_json_input") as mutate,
            mock.patch("sys.stdout"),
        ):
            self.assertEqual(0, MODULE.main(["audit"]))
        audit.assert_called_once_with(PREVIOUS)
        mutate.assert_not_called()

    def test_audit_refuses_a_live_image_that_is_not_the_reviewed_active_one(self):
        def image(mutate):
            value = MODULE.render_payload(self.contract, PREVIOUS)
            mutate(value)
            return value | {"id": 9, "source_type": "Organization", "source": "Verjson"}

        cases = {
            "disabled": image(lambda value: value.update(enforcement="disabled")),
            "no freshness": image(lambda value: value["rules"].pop()),
            "bypass": image(lambda value: value.update(
                bypass_actors=[{"actor_type": "OrganizationAdmin"}])),
            "mutable": image(lambda value: value["rules"][0]["parameters"]["workflows"][0]
                             .update(sha="main")),
        }
        for name, live in cases.items():
            with (
                self.subTest(name),
                mock.patch.object(MODULE, "list_named_rulesets", return_value=[{"id": 9}]),
                mock.patch.object(MODULE, "gh_json", return_value=live),
                mock.patch.object(MODULE, "audit_live_revision") as audit,
                self.assertRaises(MODULE.ContractError),
            ):
                MODULE.main(["audit"])
            audit.assert_not_called()
        for named in ([], [{"id": 9}, {"id": 10}]):
            with (
                self.subTest(named=named),
                mock.patch.object(MODULE, "list_named_rulesets", return_value=named),
                self.assertRaisesRegex(MODULE.ContractError, "uniquely present"),
            ):
                MODULE.main(["audit"])

    def test_dry_run_accepts_the_exact_reviewed_prior_disabled_workflow(self):
        previous_disabled = MODULE.render_payload(
            self.contract, PREVIOUS,
            include_freshness=False,
        )
        previous_disabled["enforcement"] = "disabled"
        live_previous_disabled = previous_disabled | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        with (
            mock.patch.object(MODULE, "admit_previous_revision") as admit,
            mock.patch.object(MODULE, "discover_state", return_value=([{"id": 9}], HEAD)),
            mock.patch.object(MODULE, "gh_json", return_value=live_previous_disabled),
        ):
            self.assertEqual(0, MODULE.main(["dry-run", "--workflow-sha", SHA]))
        admit.assert_called_once_with(PREVIOUS, SHA)

    def test_dry_run_rejects_an_unreviewed_live_organization_image(self):
        previous_disabled = MODULE.render_payload(
            self.contract, PREVIOUS,
            include_freshness=False,
        )
        previous_disabled["enforcement"] = "disabled"
        live_unreviewed = previous_disabled | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        live_unreviewed = copy.deepcopy(live_unreviewed)
        live_unreviewed["bypass_actors"] = [{"actor_type": "OrganizationAdmin"}]
        with (
            mock.patch.object(MODULE, "admit_previous_revision") as admit,
            mock.patch.object(MODULE, "discover_state", return_value=([{"id": 9}], HEAD)),
            mock.patch.object(MODULE, "gh_json", return_value=live_unreviewed),
            self.assertRaisesRegex(MODULE.ContractError, "reviewed active and disabled images"),
        ):
            MODULE.main(["dry-run", "--workflow-sha", SHA])
        admit.assert_not_called()

    def test_dry_run_rejects_a_non_object_live_organization_image(self):
        with (
            mock.patch.object(MODULE, "admit_previous_revision") as admit,
            mock.patch.object(MODULE, "discover_state", return_value=([{"id": 9}], HEAD)),
            mock.patch.object(MODULE, "gh_json", return_value=[]),
            self.assertRaisesRegex(MODULE.ContractError, "reviewed active and disabled images"),
        ):
            MODULE.main(["dry-run", "--workflow-sha", SHA])
        admit.assert_not_called()

    def test_apply_rotates_the_exact_reviewed_prior_disabled_workflow(self):
        expected = MODULE.render_payload(self.contract, SHA)
        previous_disabled = MODULE.render_payload(
            self.contract, PREVIOUS,
            include_freshness=False,
        )
        previous_disabled["enforcement"] = "disabled"
        live_previous_disabled = previous_disabled | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        live_expected = expected | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        with (
            mock.patch.object(MODULE, "admit_previous_revision") as admit,
            mock.patch.object(MODULE, "discover_state", return_value=([{"id": 9}], HEAD)),
            mock.patch.object(MODULE, "assert_consumer_branch_sha"),
            mock.patch.object(
                MODULE, "gh_json",
                side_effect=[live_previous_disabled, live_previous_disabled, live_expected],
            ),
            mock.patch.object(MODULE, "gh_json_input", return_value={}) as mutate,
        ):
            MODULE.main([
                "apply", "--workflow-sha", SHA,
                "--ack", "ROTATE-CLI-PROJECTS-REQUIRED-WORKFLOW-1187",
            ])
        self.assertEqual(expected, mutate.call_args.args[2])
        admit.assert_called_once_with(PREVIOUS, SHA)

    def test_apply_rotates_reviewed_prior_workflow_that_appears_during_discovery(self):
        expected = MODULE.render_payload(self.contract, SHA)
        previous = MODULE.render_payload(
            self.contract, PREVIOUS,
            include_freshness=False,
        )
        live_previous = previous | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        live_expected = expected | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        with (
            mock.patch.object(MODULE, "admit_previous_revision") as admit,
            mock.patch.object(
                MODULE, "discover_state",
                side_effect=[([], HEAD), ([{"id": 9}], HEAD)],
            ),
            mock.patch.object(MODULE, "assert_consumer_branch_sha"),
            mock.patch.object(MODULE, "gh_json", side_effect=[live_previous, live_previous, live_expected]),
            mock.patch.object(MODULE, "gh_json_input", return_value={}) as mutate,
        ):
            MODULE.main([
                "apply", "--workflow-sha", SHA,
                "--ack", "ROTATE-CLI-PROJECTS-REQUIRED-WORKFLOW-1187",
            ])
        self.assertEqual(expected, mutate.call_args.args[2])
        admit.assert_called_once_with(PREVIOUS, SHA)

    def test_apply_rotates_prior_disabled_workflow_that_appears_during_discovery(self):
        expected = MODULE.render_payload(self.contract, SHA)
        previous_disabled = MODULE.render_payload(
            self.contract, PREVIOUS,
            include_freshness=False,
        )
        previous_disabled["enforcement"] = "disabled"
        live_previous_disabled = previous_disabled | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        live_expected = expected | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        with (
            mock.patch.object(MODULE, "admit_previous_revision") as admit,
            mock.patch.object(
                MODULE, "discover_state",
                side_effect=[([], HEAD), ([{"id": 9}], HEAD)],
            ),
            mock.patch.object(MODULE, "assert_consumer_branch_sha"),
            mock.patch.object(
                MODULE, "gh_json",
                side_effect=[live_previous_disabled, live_previous_disabled, live_expected],
            ),
            mock.patch.object(MODULE, "gh_json_input", return_value={}) as mutate,
        ):
            MODULE.main([
                "apply", "--workflow-sha", SHA,
                "--ack", "ROTATE-CLI-PROJECTS-REQUIRED-WORKFLOW-1187",
            ])
        self.assertEqual(expected, mutate.call_args.args[2])
        admit.assert_called_once_with(PREVIOUS, SHA)

    def test_apply_rotates_prior_disabled_workflow_recovered_after_create_race(self):
        expected = MODULE.render_payload(self.contract, SHA)
        previous_disabled = MODULE.render_payload(
            self.contract, PREVIOUS,
            include_freshness=False,
        )
        previous_disabled["enforcement"] = "disabled"
        live_previous_disabled = previous_disabled | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        live_expected = expected | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        with (
            mock.patch.object(MODULE, "admit_previous_revision") as admit,
            mock.patch.object(MODULE, "discover_state", side_effect=[([], HEAD), ([], HEAD)]),
            mock.patch.object(MODULE, "list_named_rulesets", return_value=[{"id": 9}]),
            mock.patch.object(MODULE, "assert_consumer_branch_sha"),
            mock.patch.object(
                MODULE, "gh_json",
                side_effect=[live_previous_disabled, live_previous_disabled, live_expected],
            ),
            mock.patch.object(
                MODULE, "gh_json_input",
                side_effect=[MODULE.ContractError("create raced"), {}],
            ) as mutate,
        ):
            MODULE.main([
                "apply", "--workflow-sha", SHA,
                "--ack", "ROTATE-CLI-PROJECTS-REQUIRED-WORKFLOW-1187",
            ])
        self.assertEqual("POST", mutate.call_args_list[0].args[0])
        self.assertEqual(expected, mutate.call_args_list[1].args[2])
        admit.assert_called_once_with(PREVIOUS, SHA)

    def test_partial_rotation_restores_and_verifies_the_prior_active_workflow(self):
        expected = MODULE.render_payload(self.contract, SHA)
        previous = MODULE.render_payload(
            self.contract, PREVIOUS,
            include_freshness=False,
        )
        live_previous = previous | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        partial = copy.deepcopy(expected) | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        partial["bypass_actors"] = [{"actor_type": "OrganizationAdmin"}]
        with (
            mock.patch.object(MODULE, "gh_json", side_effect=[live_previous, partial, partial, live_previous]),
            mock.patch.object(MODULE, "gh_json_input", return_value={}) as mutate,
            self.assertRaisesRegex(MODULE.ContractError, "prior reviewed workflow restored"),
        ):
            MODULE.rotate_existing_org_rule(9, previous, expected)
        self.assertEqual(expected, mutate.call_args_list[0].args[2])
        self.assertEqual(previous, mutate.call_args_list[1].args[2])

    def test_org_rotation_branch_drift_restores_prior_after_ambiguous_put(self):
        expected = MODULE.render_payload(self.contract, SHA)
        previous = MODULE.render_payload(
            self.contract, PREVIOUS,
            include_freshness=False,
        )
        live_previous = previous | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        for response in ({}, MODULE.ContractError("client lost response")):
            with (
                mock.patch.object(
                    MODULE, "assert_consumer_branch_sha",
                    side_effect=[None, MODULE.ContractError("branch moved"),
                                 MODULE.ContractError("branch moved")],
                ),
                mock.patch.object(
                    MODULE, "gh_json",
                    side_effect=[live_previous, live_previous],
                ),
                mock.patch.object(
                    MODULE, "gh_json_input", side_effect=[response, {}],
                ) as mutate,
                self.assertRaisesRegex(MODULE.ContractError, "prior reviewed workflow restored"),
            ):
                MODULE.rotate_existing_org_rule(9, previous, expected, HEAD)
            self.assertEqual(previous, mutate.call_args_list[1].args[2])

    def test_partial_rotation_restores_the_exact_prior_disabled_workflow(self):
        expected = MODULE.render_payload(self.contract, SHA)
        previous_disabled = MODULE.render_payload(
            self.contract, PREVIOUS,
            include_freshness=False,
        )
        previous_disabled["enforcement"] = "disabled"
        live_previous_disabled = previous_disabled | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        partial = copy.deepcopy(expected) | {
            "id": 9, "source_type": "Organization", "source": "Verjson",
        }
        partial["conditions"]["repository_id"]["repository_ids"] = [1]
        with (
            mock.patch.object(
                MODULE, "gh_json",
                side_effect=[live_previous_disabled, partial, partial, live_previous_disabled],
            ),
            mock.patch.object(MODULE, "gh_json_input", return_value={}) as mutate,
            self.assertRaisesRegex(MODULE.ContractError, "prior reviewed workflow restored"),
        ):
            MODULE.rotate_existing_org_rule(9, previous_disabled, expected)
        self.assertEqual(expected, mutate.call_args_list[0].args[2])
        self.assertEqual(previous_disabled, mutate.call_args_list[1].args[2])

    def test_applied_organization_put_with_client_failure_rolls_back_disabled(self):
        expected = MODULE.render_payload(self.contract, SHA)
        staged = copy.deepcopy(expected)
        staged["enforcement"] = "disabled"
        live_staged = staged | {"id": 9, "source_type": "Organization", "source": "Verjson"}
        live_active = expected | {"id": 9, "source_type": "Organization", "source": "Verjson"}
        with (
            mock.patch.object(MODULE, "discover_state", side_effect=[([], HEAD), ([], HEAD)]),
            mock.patch.object(MODULE, "assert_consumer_branch_sha"),
            mock.patch.object(
                MODULE, "gh_json_input",
                side_effect=[{"id": 9}, MODULE.ContractError("client lost response"), {}],
            ) as mutate,
            mock.patch.object(
                MODULE, "gh_json",
                side_effect=[live_staged, live_active, live_staged],
            ),
            self.assertRaisesRegex(MODULE.ContractError, "restored and verified disabled"),
        ):
            MODULE.main([
                "apply", "--workflow-sha", SHA,
                "--ack", "ROTATE-CLI-PROJECTS-REQUIRED-WORKFLOW-1187",
            ])
        self.assertEqual("active", mutate.call_args_list[1].args[2]["enforcement"])
        self.assertEqual("disabled", mutate.call_args_list[2].args[2]["enforcement"])

    def test_retry_resumes_exact_interrupted_disabled_creation(self):
        expected = MODULE.render_payload(self.contract, SHA)
        staged = copy.deepcopy(expected)
        staged["enforcement"] = "disabled"
        live_staged = staged | {"id": 9, "source_type": "Organization", "source": "Verjson"}
        live_active = expected | {"id": 9, "source_type": "Organization", "source": "Verjson"}
        with (
            mock.patch.object(MODULE, "discover_state", return_value=([{"id": 9}], HEAD)),
            mock.patch.object(MODULE, "assert_consumer_branch_sha"),
            mock.patch.object(MODULE, "gh_json", side_effect=[live_staged, live_active]),
            mock.patch.object(MODULE, "gh_json_input", return_value={}) as mutate,
        ):
            result = MODULE.main([
                "apply", "--workflow-sha", SHA,
                "--ack", "ROTATE-CLI-PROJECTS-REQUIRED-WORKFLOW-1187",
            ])
        self.assertEqual(0, result)
        self.assertEqual(1, mutate.call_count)
        self.assertEqual("PUT", mutate.call_args.args[0])
        self.assertEqual("active", mutate.call_args.args[2]["enforcement"])


class TimestampParsingTest(unittest.TestCase):
    # GitHub reports run timestamps as `...Z` and ruleset `updated_at` in an
    # offset form; both must parse and compare in UTC (#1630).
    def parse(self, value):
        return MODULE.parse_timestamp(value)

    def test_z_and_offset_forms_compare_as_the_same_instant_in_utc(self):
        zulu = self.parse("2026-09-27T00:16:20Z")
        offset = self.parse("2026-09-26T20:16:20.000-04:00")
        self.assertEqual(zulu, offset)
        self.assertEqual(str(zulu.tzinfo), "UTC")
        self.assertLess(offset, self.parse("2026-09-27T00:16:39Z"))

    def test_naive_empty_and_malformed_values_are_rejected(self):
        for value in ("2026-09-27T00:16:20", "", "not-a-time", "2026-13-40T00:00:00Z", None, 7):
            with self.subTest(value=value), self.assertRaisesRegex(MODULE.ContractError, "ruleset timestamp is invalid"):
                self.parse(value)


if __name__ == "__main__":
    unittest.main()
