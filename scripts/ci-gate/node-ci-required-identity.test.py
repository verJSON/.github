#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import unittest
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT / ".github/workflows/node-ci.yml"
PROTECTED = ROOT / ".github/workflows/node-ci-protected.yml"
HEAD = "a" * 40
LEGACY_SHA256 = "19693ab0f03eab1110a6882e41f89e4a4ca3d6733b317891c97b3757a34b9deb"


class RequiredWorkflowIdentityTest(unittest.TestCase):
    credential_keys = (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "NODE_AUTH_TOKEN",
        "NPM_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "AZURE_CREDENTIALS",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
    )

    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load(PROTECTED.read_text(encoding="utf-8"))
        cls.verifiers = [step for job in cls.workflow["jobs"].values()
                         for step in job.get("steps", [])
                         if step.get("name") == "Revalidate protected pull-request identity"]

    def run_verifier(self, step, *, run_record=None, pr_record=None, token="bounded-token"):
        with tempfile.TemporaryDirectory() as directory:
            fake_gh = Path(directory) / "gh"
            fake_gh.write_text(
                "#!/bin/sh\n[ \"${GH_TOKEN:-}\" = bounded-token ] || exit 70\n"
                "case \"$*\" in\n *actions/runs/2468*) printf '%s\\n' \"$RUN_RECORD\" ;;\n"
                " *pulls/114*) printf '%s\\n' \"$PR_RECORD\" ;;\n *) exit 71 ;;\nesac\n",
                encoding="utf-8")
            fake_gh.chmod(0o755)
            environment = {**os.environ, "PATH": f"{directory}:{os.environ['PATH']}",
                           "GH_TOKEN": token, "ADMITTED_EVENT": "pull_request",
                           "ADMITTED_HEAD_REPOSITORY": "Verjson/repository",
                           "ADMITTED_HEAD_SHA": HEAD, "REPOSITORY": "Verjson/repository",
                           "RUN_ID": "2468",
                           "RUN_RECORD": run_record if run_record is not None else f"pull_request\t{HEAD}\t1\t114",
                           "PR_RECORD": pr_record if pr_record is not None else f"open\tVerjson/repository\t{HEAD}"}
            return subprocess.run(["/usr/bin/bash", "-c", step["run"]], env=environment,
                                  capture_output=True).returncode

    def test_generator_is_exact_and_node_ci_workflow_is_pinned(self):
        self.assertEqual(LEGACY_SHA256, hashlib.sha256(LEGACY.read_bytes()).hexdigest())
        before = PROTECTED.read_bytes()
        subprocess.run(["python3", "scripts/gen-node-ci-protected.py"], cwd=ROOT, check=True)
        self.assertEqual(before, PROTECTED.read_bytes())

    def test_changelog_cache_is_verified_before_the_networkless_sandbox(self):
        steps = self.workflow["jobs"]["build-test"]["steps"]
        cache_setup = next(
            step for step in steps
            if step.get("name") == "Prepare job-scoped changelog tool cache"
        )
        warm_steps = [
            step for step in steps
            if step.get("name") == "Warm verified changelog contract cache"
        ]
        self.assertEqual(1, len(warm_steps))
        warm = warm_steps[0]
        plan = next(
            step for step in steps
            if step.get("name") == "Run exact credentialless consumer script plan"
        )
        self.assertLess(steps.index(cache_setup), steps.index(warm))
        self.assertLess(steps.index(warm), steps.index(plan))
        self.assertIn('"$RUNNER_TEMP/', cache_setup["run"])
        self.assertNotIn("$GITHUB_WORKSPACE", cache_setup["run"])
        self.assertEqual(plan["if"], warm["if"])
        self.assertIn('workspace.resolve(strict=True)', warm["run"])
        self.assertIn('os.open("scripts", directory_flags, dir_fd=workspace_fd)', warm["run"])
        self.assertIn('"render-next.sh"', warm["run"])
        self.assertIn("os.O_NOFOLLOW", warm["run"])
        self.assertIn("max_renderer_bytes = 1024 * 1024", warm["run"])
        self.assertIn("CONTRACT_REF", warm["run"])
        self.assertIn("CONTRACT_SHA256", warm["run"])
        self.assertIn("hashlib.sha256", warm["run"])
        self.assertNotIn("bash scripts/render-next.sh", warm["run"])
        self.assertIn("VERJSON_CHANGELOG_TOOL_CACHE", plan["run"])
        self.assertIn('"--unshare-net"', plan["run"])
        self.assertIn('"--ro-bind"', plan["run"])

    def changelog_cache_warm_step(self):
        return next(
            step for step in self.workflow["jobs"]["build-test"]["steps"]
            if step.get("name") == "Warm verified changelog contract cache"
        )

    def run_changelog_cache_warm_step(
        self, renderer, contract_bytes, *, symlink_renderer=False, track_renderer_reads=False
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            (workspace / "scripts").mkdir(parents=True)
            renderer_path = workspace / "scripts/render-next.sh"
            if symlink_renderer:
                outside_renderer = root / "outside" / "render-next.sh"
                outside_renderer.parent.mkdir()
                outside_renderer.write_text(renderer, encoding="utf-8")
                renderer_path.symlink_to(outside_renderer)
            else:
                renderer_path.write_text(renderer, encoding="utf-8")
            runner_temp = root / "runner-temp"
            runner_temp.mkdir()
            cache_root = runner_temp / "verjson-changelog-tools-test"
            cache_root.mkdir(mode=0o700)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            contract_source = root / "contract.py"
            contract_source.write_bytes(contract_bytes)
            curl_log = root / "curl.log"
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                "from pathlib import Path\n"
                "args = sys.argv[1:]\n"
                "Path(os.environ['CURL_LOG']).write_text('\\n'.join(args), encoding='utf-8')\n"
                "Path(args[args.index('-o') + 1]).write_bytes(Path(os.environ['CURL_BODY']).read_bytes())\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            python_site = root / "python-site"
            read_marker = root / "renderer-read"
            python_path = os.environ.get("PYTHONPATH", "")
            if track_renderer_reads:
                python_site.mkdir()
                (python_site / "sitecustomize.py").write_text(
                    "import os\n"
                    "from pathlib import Path\n"
                    "original_read_text = Path.read_text\n"
                    "def tracked_read_text(self, *args, **kwargs):\n"
                    "    if self.name == 'render-next.sh':\n"
                    "        Path(os.environ['RENDERER_READ_MARKER']).write_text(str(self))\n"
                    "    return original_read_text(self, *args, **kwargs)\n"
                    "Path.read_text = tracked_read_text\n",
                    encoding="utf-8",
                )
                python_path = f"{python_site}:{python_path}" if python_path else str(python_site)
            output = root / "step-output"
            result = subprocess.run(
                ["/usr/bin/bash", "-c", self.changelog_cache_warm_step()["run"]],
                cwd=workspace,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "GITHUB_WORKSPACE": str(workspace),
                    "RUNNER_TEMP": str(runner_temp),
                    "VERJSON_CHANGELOG_TOOL_CACHE": str(cache_root),
                    "GITHUB_OUTPUT": str(output),
                    "CURL_BODY": str(contract_source),
                    "CURL_LOG": str(curl_log),
                    "EXECUTED_MARKER": str(root / "renderer-executed"),
                    "PYTHONPATH": python_path,
                    "RENDERER_READ_MARKER": str(read_marker),
                },
                capture_output=True,
                text=True,
            )
            cached_paths = list(cache_root.glob("*/changelog.py"))
            cached_contract = cached_paths[0] if len(cached_paths) == 1 else None
            return (
                result,
                cached_contract.read_bytes() if cached_contract is not None else None,
                cached_contract.stat().st_mode & 0o777 if cached_contract is not None else None,
                cached_contract.parent.stat().st_mode & 0o777 if cached_contract is not None else None,
                curl_log.read_text(encoding="utf-8") if curl_log.exists() else None,
                output.read_text(encoding="utf-8") if output.exists() else "",
                (root / "renderer-executed").exists(),
                list(cache_root.iterdir()),
                read_marker.exists(),
            )

    def test_changelog_cache_warmup_rejects_outside_renderer_symlink_without_reading_it(self):
        contract = b"# pinned changelog engine fixture\n"
        reference = "c" * 40
        renderer = (
            f'CONTRACT_REF="{reference}"\n'
            f'CONTRACT_SHA256="{hashlib.sha256(contract).hexdigest()}"\n'
        )

        (
            result,
            cached_contract,
            _,
            _,
            curl_args,
            _,
            _,
            cache_entries,
            renderer_read,
        ) = self.run_changelog_cache_warm_step(
            renderer,
            contract,
            symlink_renderer=True,
            track_renderer_reads=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIsNone(cached_contract)
        self.assertIsNone(curl_args)
        self.assertEqual([], cache_entries)
        self.assertFalse(renderer_read)

    def test_changelog_cache_warmup_reads_pins_as_data_and_never_executes_renderer(self):
        contract = b"# pinned changelog engine fixture\n"
        reference = "a" * 40
        digest = hashlib.sha256(contract).hexdigest()
        renderer = (
            f'CONTRACT_REF="{reference}"\n'
            f'CONTRACT_SHA256="{digest}"\n'
            'printf executed > "$EXECUTED_MARKER"\n'
        )

        (
            result,
            cached_contract,
            file_mode,
            directory_mode,
            curl_args,
            outputs,
            executed,
            _,
            _,
        ) = self.run_changelog_cache_warm_step(renderer, contract)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(contract, cached_contract)
        self.assertEqual(0o400, file_mode)
        self.assertEqual(0o700, directory_mode)
        self.assertIn(
            f"https://raw.githubusercontent.com/Verjson/.github/{reference}/scripts/changelog.py",
            curl_args,
        )
        self.assertIn("--max-filesize\n16777216", curl_args)
        self.assertIn(f"contract_ref={reference}", outputs)
        self.assertIn(f"contract_sha256={digest}", outputs)
        self.assertFalse(executed)

    def test_changelog_cache_warmup_rejects_bad_digest_and_malformed_pin(self):
        contract = b"# pinned changelog engine fixture\n"
        reference = "b" * 40
        bad_digest_renderer = (
            f'CONTRACT_REF="{reference}"\n'
            f'CONTRACT_SHA256="{"0" * 64}"\n'
            'printf executed > "$EXECUTED_MARKER"\n'
        )
        (
            bad_digest,
            bad_contract,
            _,
            _,
            bad_curl_args,
            _,
            bad_executed,
            _,
            _,
        ) = self.run_changelog_cache_warm_step(bad_digest_renderer, contract)
        self.assertNotEqual(0, bad_digest.returncode)
        self.assertIsNone(bad_contract)
        self.assertIsNotNone(bad_curl_args)
        self.assertFalse(bad_executed)

        malformed_renderer = (
            'CONTRACT_REF="not-a-commit"\n'
            f'CONTRACT_SHA256="{hashlib.sha256(contract).hexdigest()}"\n'
        )
        (
            malformed,
            malformed_contract,
            _,
            _,
            malformed_curl_args,
            _,
            _,
            malformed_entries,
            _,
        ) = self.run_changelog_cache_warm_step(malformed_renderer, contract)
        self.assertNotEqual(0, malformed.returncode)
        self.assertIsNone(malformed_contract)
        self.assertIsNone(malformed_curl_args)
        self.assertEqual([], malformed_entries)

    def test_contract_requires_scopes_and_explicit_nonambient_token(self):
        self.assertEqual(7, len(self.verifiers))
        for step in self.verifiers:
            self.assertEqual("${{ github.token }}", step["env"].get("GH_TOKEN"))
            self.assertEqual(0, self.run_verifier(step))
            self.assertNotEqual(0, self.run_verifier(step, token="ambient-token"))
        for name in ("acquire-secretless-dependencies", "build-test"):
            permissions = self.workflow["jobs"][name]["permissions"]
            self.assertEqual("read", permissions["actions"])
            self.assertEqual("read", permissions["pull-requests"])

    def test_protected_variant_rejects_every_non_pr_secretless_mode(self):
        acquisition = self.workflow["jobs"]["acquire-secretless-dependencies"]
        self.assertEqual("needs.eligibility.outputs.should-run != 'false'", acquisition["if"])
        boundary = next(step for step in acquisition["steps"]
                        if step.get("name") == "Enforce the secretless event boundary")
        base = {**os.environ, "APPROVED_INTERNAL_PACKAGES": "@verjson/package",
                "EVENT_NAME": "pull_request", "HEAD_REPOSITORY": "Verjson/repository",
                "NODE_AUTH_TOKEN": "package-token", "REPOSITORY": "Verjson/repository",
                "SCHEMA_DIR": ""}
        for secretless_pr, trusted_ref, expected in (
            ("true", "false", 0), ("false", "false", 1),
            ("false", "true", 1), ("true", "true", 1)):
            result = subprocess.run(
                ["/usr/bin/bash", "-c", boundary["run"]],
                env={**base, "SECRETLESS_PR": secretless_pr,
                     "SECRETLESS_TRUSTED_REF": trusted_ref}, capture_output=True)
            self.assertEqual(expected, int(result.returncode != 0))

        nonempty_schema = subprocess.run(
            ["/usr/bin/bash", "-c", boundary["run"]],
            env={
                **base,
                "SCHEMA_DIR": "candidate-schema",
                "SECRETLESS_PR": "true",
                "SECRETLESS_TRUSTED_REF": "false",
            },
            capture_output=True,
        )
        self.assertNotEqual(0, nonempty_schema.returncode)
        self.assertNotIn(
            "Install schema submodule deps",
            {
                step.get("name")
                for step in self.workflow["jobs"]["build-test"]["steps"]
            },
        )

    def test_malformed_ambiguous_foreign_stale_and_partial_records_fail_closed(self):
        cases = ((f"pull_request\t{HEAD}\t0\t", None),
                 (f"pull_request\t{HEAD}\t2\t114", None),
                 (f"push\t{HEAD}\t1\t114", None),
                 (f"pull_request\t{'b' * 40}\t1\t114", None),
                 (f"pull_request\t{HEAD}\t1\tbad", None),
                 (None, f"closed\tVerjson/repository\t{HEAD}"),
                 (None, f"open\tattacker/fork\t{HEAD}"),
                 (None, f"open\tVerjson/repository\t{'c' * 40}"), ("", ""))
        for run_record, pr_record in cases:
            with self.subTest(run_record=run_record, pr_record=pr_record):
                self.assertNotEqual(0, self.run_verifier(self.verifiers[0],
                                                        run_record=run_record, pr_record=pr_record))

    def test_close_and_synchronize_between_boundaries_are_rejected(self):
        self.assertEqual(0, self.run_verifier(self.verifiers[0]))
        changed = "d" * 40
        for execution_verifier in self.verifiers[1:]:
            self.assertNotEqual(0, self.run_verifier(
                execution_verifier, pr_record=f"closed\tVerjson/repository\t{HEAD}"))
            self.assertNotEqual(0, self.run_verifier(
                execution_verifier, run_record=f"pull_request\t{changed}\t1\t114",
                pr_record=f"open\tVerjson/repository\t{changed}"))

    def test_shared_script_immediately_guards_both_boundaries_and_checkout(self):
        self.assertTrue(all(step["run"] == self.verifiers[0]["run"]
                            for step in self.verifiers[1:]))
        acquisition = self.workflow["jobs"]["acquire-secretless-dependencies"]["steps"]
        build = self.workflow["jobs"]["build-test"]["steps"]
        first = next(i for i, step in enumerate(acquisition)
                     if step.get("name") == "Revalidate protected pull-request identity")
        self.assertTrue(str(acquisition[first - 1].get("uses", "")).startswith("actions/checkout@"))
        self.assertEqual(
            "Download pinned secretless dependency transfer implementation",
            acquisition[first + 1]["name"],
        )
        self.assertEqual(
            "Reject consumer-controlled npm configuration",
            acquisition[first + 2]["name"],
        )
        acquisition_verifiers = [i for i, step in enumerate(acquisition)
                                 if step.get("name") == "Revalidate protected pull-request identity"]
        self.assertEqual(3, len(acquisition_verifiers))
        auxiliary_guard, populate_guard = acquisition_verifiers[1:]
        self.assertEqual("inputs.secretless-auxiliary-source != ''",
                         acquisition[auxiliary_guard]["if"])
        self.assertEqual("Acquire immutable auxiliary source",
                         acquisition[auxiliary_guard + 1]["name"])
        self.assertNotIn("if", acquisition[populate_guard])
        self.assertEqual("Populate verified private dependency cache",
                         acquisition[populate_guard + 1]["name"])
        guarded_routes = (
            "Rebuild exact approved lifecycle packages without credentials",
            "Run exact credentialless consumer script plan",
            None,
        )
        verifier_indexes = [i for i, step in enumerate(build)
                            if step.get("name") == "Revalidate protected pull-request identity"]
        self.assertEqual(4, len(verifier_indexes))
        self.assertIn("inputs.secretless-rebuild-packages != ''",
                      build[verifier_indexes[0]]["if"])
        self.assertIn("inputs.secretless-ci-script-plan != ''",
                      build[verifier_indexes[1]]["if"])
        self.assertIn("inputs.secretless-ci-script-plan == ''",
                      build[verifier_indexes[2]]["if"])
        for verifier_index in verifier_indexes:
            self.assertEqual(build[verifier_index]["if"],
                             build[verifier_index + 1]["if"])
        compatibility_condition = (
            "needs.eligibility.outputs.should-run != 'false' && "
            "(inputs.secretless-pr || inputs.secretless-trusted-ref) && "
            "(inputs.protected-type-surface-declaration-path != '' || "
            "inputs.secretless-compatibility-ranges != '')"
        )
        self.assertEqual(compatibility_condition, build[verifier_indexes[3]]["if"])
        self.assertEqual(guarded_routes[0], build[verifier_indexes[0] + 1]["name"])
        self.assertEqual(guarded_routes[1], build[verifier_indexes[1] + 1]["name"])
        grouped = build[verifier_indexes[2] + 1]
        self.assertEqual("Run default build, typecheck, test, and lint plan", grouped["name"])
        self.assertEqual(build[verifier_indexes[2]]["if"], grouped["if"])
        self.assertEqual(
            ["npm run build", "npm run typecheck --if-present", "npm test",
             "npm run lint --if-present"], grouped["run"].splitlines()[1:])
        self.assertEqual("Run runtime-resolved compatibility lanes without credentials",
                         build[verifier_indexes[3] + 1]["name"])
        self.assertEqual(build[verifier_indexes[3]]["if"],
                         build[verifier_indexes[3] + 1]["if"])
        for steps in (acquisition, build):
            checkout = next(step for step in steps
                            if str(step.get("uses", "")).startswith("actions/checkout@"))
            self.assertEqual("${{ inputs.head-sha }}", checkout["with"]["ref"])

    def test_candidate_routes_remove_credential_keys_instead_of_emptying_them(self):
        routes = {
            "Rebuild exact approved lifecycle packages without credentials",
            "Run exact credentialless consumer script plan",
            "Run default build, typecheck, test, and lint plan",
            "Run runtime-resolved compatibility lanes without credentials",
        }
        steps = {
            step.get("name"): step
            for step in self.workflow["jobs"]["build-test"]["steps"]
            if step.get("name") in routes
        }
        self.assertEqual(routes, set(steps))
        expected_scrub = "unset -v " + " ".join(self.credential_keys)
        probe = (
            "node -e 'const keys="
            + json.dumps(self.credential_keys)
            + "; if (keys.some((key) => Object.hasOwn(process.env, key))) process.exit(1)'"
        )
        populated = {**os.environ, **dict.fromkeys(self.credential_keys, "sensitive")}
        empty_string_mutation = "export " + " ".join(
            f"{key}=''" for key in self.credential_keys
        )

        for route, step in steps.items():
            with self.subTest(route=route):
                self.assertEqual(expected_scrub, step["run"].splitlines()[0])
                self.assertEqual(
                    0,
                    subprocess.run(
                        ["/usr/bin/bash", "-c", f"{expected_scrub}\n{probe}"],
                        env=populated,
                    ).returncode,
                )
                self.assertNotEqual(
                    0,
                    subprocess.run(
                        ["/usr/bin/bash", "-c", f"{empty_string_mutation}\n{probe}"],
                        env=populated,
                    ).returncode,
                )


    def candidate_plan_step(self):
        return next(
            step
            for step in self.workflow["jobs"]["build-test"]["steps"]
            if step.get("name") == "Run exact credentialless consumer script plan"
        )

    def create_changelog_cache_fixture(self, runner_temp):
        cache_root = runner_temp / "verjson-changelog-tools-test"
        reference = "c" * 40
        contract_bytes = b"# pinned changelog engine fixture\n"
        cache_root.mkdir(mode=0o700)
        cache_dir = cache_root / reference
        cache_dir.mkdir(mode=0o700)
        contract = cache_dir / "changelog.py"
        contract.write_bytes(contract_bytes)
        contract.chmod(0o400)
        return cache_root, reference, contract_bytes

    def run_candidate_plan(
        self,
        cache_setup,
        npm_body,
        *,
        run=None,
        environment_updates=None,
        workspace_in_runner_temp=False,
        shadow_candidate_path=False,
        tool_root_mode=0o755,
        tool_bin_mode=0o755,
        workspace_symlink=False,
        swap_tool_prefix=False,
        root_owned_tools=True,
        tool_root_uid=None,
        tool_tree_mode=None,
        tool_tree_gid=None,
        foreign_entry=None,
        pwsh_fixture=None,
        mutate_tool_in_place=False,
        npm_cli_layout=False,
        decoy_npm_cli=False,
        symlink_npm_cli_layout=False,
        extra_npm_cli=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner_temp = root / "runner-temp"
            runner_temp.mkdir()
            baseline = runner_temp / "baseline"
            baseline.mkdir()
            changelog_cache_root, changelog_ref, changelog_bytes = (
                self.create_changelog_cache_fixture(runner_temp)
            )
            workspace_parent = runner_temp if workspace_in_runner_temp else root
            workspace = workspace_parent / "workspace"
            if workspace_symlink:
                workspace_real = workspace_parent / "workspace-real"
                workspace_real.mkdir()
                workspace.symlink_to(workspace_real, target_is_directory=True)
            else:
                workspace.mkdir()
            cache_setup(baseline)
            (workspace / "package.json").write_text(
                json.dumps({"scripts": {"first": "true", "second": "true"}}),
                encoding="utf-8",
            )
            tool_bin = root / "tool" / "bin"
            tool_bin.mkdir(parents=True)
            tool_bin.parent.chmod(tool_root_mode)
            tool_bin.chmod(tool_bin_mode)
            npm = tool_bin / "npm"
            npm.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + npm_body, encoding="utf-8")
            npm.chmod(0o755)
            node = tool_bin / "node"
            if npm_cli_layout:
                # Match the npm CLI created from tool_package below; any other path must fail.
                node.write_text(
                    "#!/usr/bin/env bash\n"
                    "case \"$1\" in */tool/lib/node_modules/npm/bin/npm-cli.js) "
                    "printf '%s\\n' 'trusted npm CLI fixture invoked' ;; "
                    "*) exit 92 ;; esac\n",
                    encoding="utf-8",
                )
            else:
                node.write_text(
                    "#!/usr/bin/env bash\nexec /usr/bin/node \"$@\"\n",
                    encoding="utf-8",
                )
            node.chmod(0o755)
            tool_package = tool_bin.parent / "lib" / "node_modules" / "npm" / "package.json"
            tool_package.parent.mkdir(parents=True)
            tool_package.write_text('{"name":"npm"}\n', encoding="utf-8")
            tool_package.chmod(0o644)
            if npm_cli_layout:
                npm_cli = tool_package.parent / "bin" / "npm-cli.js"
                npm_cli.parent.mkdir()
                npm_cli.write_text(
                    "console.log('trusted npm CLI fixture invoked');\n",
                    encoding="utf-8",
                )
                npm_cli.chmod(0o644)
            if symlink_npm_cli_layout:
                (tool_bin / "node_modules").symlink_to(
                    tool_bin.parent / "lib" / "node_modules", target_is_directory=True
                )
            if extra_npm_cli:
                extra_npm_cli_path = tool_bin / "node_modules" / "npm" / "bin" / "npm-cli.js"
                extra_npm_cli_path.parent.mkdir(parents=True)
                extra_npm_cli_path.write_text(
                    "console.log('second trusted npm CLI fixture invoked');\n",
                    encoding="utf-8",
                )
                extra_npm_cli_path.chmod(0o644)
            for directory_name, _, _ in os.walk(tool_bin.parent):
                Path(directory_name).chmod(0o755)
            if tool_tree_mode is not None:
                # GitHub-hosted images ship every descendant of the tool cache at one
                # mode (0777 measured on ubuntu-latest for #1599), so apply it to every
                # directory and regular file in the tree, root included.
                for directory_name, _, file_names in os.walk(tool_bin.parent):
                    Path(directory_name).chmod(tool_tree_mode)
                    for file_name in file_names:
                        entry = Path(directory_name) / file_name
                        if not entry.is_symlink():
                            entry.chmod(tool_tree_mode)
            tool_bin.parent.chmod(tool_root_mode)
            tool_bin.chmod(tool_bin_mode)
            if root_owned_tools:
                subprocess.run(
                    ["sudo", "-n", "chown", "-R", "0:0", str(tool_bin.parent)], check=True
                )
            elif tool_root_uid is not None:
                # The hosted root itself is gid 0 while its descendants carry the image
                # builder's gid; tool_tree_gid models that split when given.
                subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "chown",
                        "-R",
                        f"{tool_root_uid}:{0 if tool_tree_gid is None else tool_tree_gid}",
                        str(tool_bin.parent),
                    ],
                    check=True,
                )
                subprocess.run(
                    ["sudo", "-n", "chown", f"{tool_root_uid}:0", str(tool_bin.parent)],
                    check=True,
                )
            if foreign_entry is not None:
                foreign_relative_path, foreign_uid = foreign_entry
                subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "chown",
                        "-h",
                        f"{foreign_uid}:0",
                        str(tool_bin.parent / foreign_relative_path),
                    ],
                    check=True,
                )
            fixture_roots = []
            if pwsh_fixture is not None:
                fixture_usr = root / "usr"
                fixture_opt = root / "opt"
                fixture_link = fixture_usr / "bin" / "pwsh"
                fixture_link.parent.mkdir(parents=True)
                if pwsh_fixture == "valid":
                    fixture_target = fixture_opt / "microsoft" / "powershell" / "7" / "pwsh"
                else:
                    fixture_target = root / "untrusted" / "pwsh"
                fixture_target.parent.mkdir(parents=True)
                fixture_target.write_text(
                    "#!/usr/bin/env bash\nprintf verified > \"$PWD/pwsh-marker\"\n",
                    encoding="utf-8",
                )
                fixture_target.chmod(0o755)
                if decoy_npm_cli:
                    decoy_cli = fixture_target.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
                    decoy_cli.parent.mkdir(parents=True)
                    decoy_cli.write_text(
                        "console.log('decoy npm CLI from another trusted tool prefix');\n",
                        encoding="utf-8",
                    )
                fixture_link.symlink_to(fixture_target)
                fixture_roots = [fixture_usr, fixture_opt, root / "untrusted"]
                for fixture_root in fixture_roots:
                    if fixture_root.exists():
                        for directory, _, _ in os.walk(fixture_root):
                            Path(directory).chmod(0o755)
                        subprocess.run(
                            ["sudo", "-n", "chown", "-R", "0:0", str(fixture_root)], check=True
                        )
            path = f"{tool_bin}:{os.environ['PATH']}"
            if shadow_candidate_path:
                shadow_bin = workspace / "bin"
                shadow_bin.mkdir()
                shadow_npm = shadow_bin / "npm"
                shadow_npm.write_text("#!/usr/bin/env bash\nexit 91\n", encoding="utf-8")
                shadow_npm.chmod(0o755)
                path = f"{shadow_bin}:{path}"
            env = {
                **os.environ,
                "PATH": path,
                "RUNNER_TEMP": str(runner_temp),
                "CI_SCRIPT_PLAN": json.dumps(["first", "second"]),
                "CANDIDATE_CACHE_ROOT": str(runner_temp / "verjson-candidate-caches-test"),
                "npm_config_cache": str(baseline),
                "RUNNER_TOOL_CACHE": str(tool_bin.parent),
                "VERJSON_CHANGELOG_TOOL_CACHE": str(changelog_cache_root),
                "VERJSON_CHANGELOG_CONTRACT_REF": changelog_ref,
                "VERJSON_CHANGELOG_CONTRACT_SHA256": hashlib.sha256(changelog_bytes).hexdigest(),
                "PWD": str(workspace),
            }
            env.update(environment_updates or {})
            env = {
                name: (
                    str(workspace)
                    if value == "WORKSPACE"
                    else str(tool_bin.parent)
                    if value == "TOOL_ROOT"
                    else value
                )
                for name, value in env.items()
            }
            swap_thread = None
            if swap_tool_prefix:
                def replace_tool_prefix():
                    cache_root = Path(env["CANDIDATE_CACHE_ROOT"])
                    for _ in range(10000):
                        if cache_root.exists():
                            original = tool_bin.parent.with_name("tool-original")
                            tool_bin.parent.rename(original)
                            replacement_bin = tool_bin.parent / "bin"
                            replacement_bin.mkdir(parents=True)
                            replacement_bin.parent.chmod(0o755)
                            replacement_npm = replacement_bin / "npm"
                            replacement_npm.write_text(
                                "#!/usr/bin/env bash\nexit 91\n", encoding="utf-8"
                            )
                            replacement_npm.chmod(0o755)
                            replacement_node = replacement_bin / "node"
                            replacement_node.write_text(
                                "#!/usr/bin/env bash\nexit 91\n", encoding="utf-8"
                            )
                            replacement_node.chmod(0o755)
                            return
                        time.sleep(0.0001)
                swap_thread = threading.Thread(target=replace_tool_prefix)
                swap_thread.start()
            mutation_thread = None
            if mutate_tool_in_place:
                def mutate_selected_executable():
                    with npm.open("a", encoding="utf-8") as stream:
                        stream.write("\nexit 91\n")
                    with tool_package.open("a", encoding="utf-8") as stream:
                        stream.write("{}\n")
                mutation_thread = threading.Thread(target=mutate_selected_executable)
                mutation_thread.start()
            candidate_run = run or self.candidate_plan_step()["run"]
            if pwsh_fixture is not None:
                candidate_run = candidate_run.replace(
                    'Path("/usr/bin/pwsh")', f'Path("{fixture_link}")'
                ).replace(
                    'Path("/opt/microsoft/powershell")',
                    f'Path("{fixture_opt / "microsoft" / "powershell"}")',
                ).replace(
                    'Path("/usr"), system_candidate',
                    f'Path("{fixture_usr}"), system_candidate',
                ).replace('Path("/opt")', f'Path("{fixture_opt}")').replace(
                    'is_relative_to("/opt/microsoft/powershell")',
                    f'is_relative_to("{fixture_opt / "microsoft" / "powershell"}")',
                )
            result = subprocess.run(
                ["/usr/bin/bash", "-c", candidate_run],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
            )
            if swap_thread is not None:
                swap_thread.join(timeout=5)
                self.assertFalse(swap_thread.is_alive())
            if mutation_thread is not None:
                mutation_thread.join(timeout=5)
                self.assertFalse(mutation_thread.is_alive())
            remaining = [path.name for path in runner_temp.glob("verjson-candidate-caches-*")]
            if root_owned_tools or tool_root_uid is not None or foreign_entry is not None:
                for owned_tool_root in [*root.glob("tool*"), *fixture_roots]:
                    if not owned_tool_root.exists():
                        continue
                    subprocess.run(
                        [
                            "sudo",
                            "-n",
                            "chown",
                            "-R",
                            f"{os.getuid()}:{os.getgid()}",
                            str(owned_tool_root),
                        ],
                        check=True,
                    )
            return result, remaining

    def test_candidate_mount_paths_reject_overlap_and_noncanonical_aliases(self):
        cache_setup = lambda baseline: (baseline / "blob").write_text(
            "verified", encoding="utf-8"
        )
        cases = (
            ("workspace-runner-temp", {}, True),
            ("baseline-root", {"CANDIDATE_CACHE_ROOT": "BASELINE"}, False),
            ("shared-service-network", {"DB_PORT": "5432"}, False),
        )
        for name, updates, nested_workspace in cases:
            if updates.get("CANDIDATE_CACHE_ROOT") == "BASELINE":
                with tempfile.TemporaryDirectory() as directory:
                    runner_temp = Path(directory) / "runner-temp"
                    runner_temp.mkdir()
                    baseline = runner_temp / "baseline"
                    baseline.mkdir()
                    changelog_cache_root, changelog_ref, changelog_bytes = (
                        self.create_changelog_cache_fixture(runner_temp)
                    )
                    (baseline / "blob").write_text("verified", encoding="utf-8")
                    workspace = Path(directory) / "workspace"
                    workspace.mkdir()
                    (workspace / "package.json").write_text(
                        json.dumps({"scripts": {"first": "true"}}), encoding="utf-8"
                    )
                    run = self.candidate_plan_step()["run"]
                    result = subprocess.run(
                        ["/usr/bin/bash", "-c", run],
                        cwd=workspace,
                        env={
                            **os.environ,
                            "RUNNER_TEMP": str(runner_temp),
                            "CI_SCRIPT_PLAN": '["first"]',
                            "CANDIDATE_CACHE_ROOT": str(baseline),
                            "npm_config_cache": str(baseline),
                            "VERJSON_CHANGELOG_TOOL_CACHE": str(changelog_cache_root),
                            "VERJSON_CHANGELOG_CONTRACT_REF": changelog_ref,
                            "VERJSON_CHANGELOG_CONTRACT_SHA256": hashlib.sha256(changelog_bytes).hexdigest(),
                        },
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("candidate cache root exists before script execution", result.stderr)
                continue
            result, remaining = self.run_candidate_plan(
                cache_setup,
                "exit 0\n",
                environment_updates=updates,
                workspace_in_runner_temp=nested_workspace,
            )
            with self.subTest(case=name):
                self.assertNotEqual(0, result.returncode)
                self.assertEqual([], remaining)

    def test_candidate_path_shadow_cannot_replace_setup_node_tools(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "exit 0\n",
            shadow_candidate_path=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], remaining)

    def test_node_26_npm_layout_executes_cli_from_the_validated_tool_prefix(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "exit 91\n",
            npm_cli_layout=True,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("trusted npm CLI fixture invoked", result.stdout)
        self.assertEqual([], remaining)

    def test_node_26_npm_cli_is_selected_from_its_own_tool_prefix(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "exit 91\n",
            npm_cli_layout=True,
            pwsh_fixture="valid",
            decoy_npm_cli=True,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("trusted npm CLI fixture invoked", result.stdout)
        self.assertNotIn("decoy npm CLI", result.stdout)
        self.assertEqual([], remaining)

    def test_identical_npm_cli_candidates_from_symlink_layout_are_deduplicated(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "exit 91\n",
            npm_cli_layout=True,
            symlink_npm_cli_layout=True,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("trusted npm CLI fixture invoked", result.stdout)
        self.assertEqual([], remaining)

    def test_distinct_npm_cli_candidates_fail_closed(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "exit 91\n",
            npm_cli_layout=True,
            extra_npm_cli=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("trusted npm CLI is ambiguous", result.stderr)
        self.assertEqual([], remaining)

    def test_verified_changelog_contract_is_read_only_inside_the_networkless_sandbox(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "contract=\"$VERJSON_CHANGELOG_TOOL_CACHE/$VERJSON_CHANGELOG_CONTRACT_REF/changelog.py\"\n"
            "test -r \"$contract\"\n"
            "grep -q 'pinned changelog engine fixture' \"$contract\"\n"
            "if printf poisoned >\"$contract\" 2>/dev/null; then exit 91; fi\n"
            "grep -q 'pinned changelog engine fixture' \"$contract\"\n",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], remaining)

    def test_changelog_contract_digest_is_rechecked_immediately_before_sandbox_entry(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "exit 0\n",
            environment_updates={"VERJSON_CHANGELOG_CONTRACT_SHA256": "0" * 64},
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("does not match its pinned SHA-256", result.stderr)
        self.assertEqual([], remaining)

    def test_trusted_tool_root_rejects_workspace_equality_and_writable_mode(self):
        cache_setup = lambda baseline: (baseline / "blob").write_text(
            "verified", encoding="utf-8"
        )
        equal_result, _ = self.run_candidate_plan(
            cache_setup,
            "exit 0\n",
            environment_updates={"RUNNER_TOOL_CACHE": "WORKSPACE"},
        )
        writable_result, _ = self.run_candidate_plan(
            cache_setup,
            "exit 0\n",
            tool_root_mode=0o775,
        )
        self.assertNotEqual(0, equal_result.returncode)
        self.assertNotEqual(0, writable_result.returncode)

    def test_hosted_tool_cache_root_allows_only_the_runner_convention(self):
        cache_setup = lambda baseline: (baseline / "blob").write_text(
            "verified", encoding="utf-8"
        )
        hosted_run = self.candidate_plan_step()["run"].replace(
            'Path("/opt/hostedtoolcache")',
            'Path(os.environ["RUNNER_TOOL_CACHE"])',
        )
        result, remaining = self.run_candidate_plan(
            cache_setup,
            "exit 0\n",
            run=hosted_run,
            tool_root_mode=0o777,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], remaining)

        runner_owned_result, remaining = self.run_candidate_plan(
            cache_setup,
            "exit 0\n",
            run=hosted_run,
            tool_root_mode=0o777,
            root_owned_tools=False,
            tool_root_uid=1001,
        )
        self.assertEqual(0, runner_owned_result.returncode, runner_owned_result.stderr)
        self.assertEqual([], remaining)

        unapproved_owner_result, remaining = self.run_candidate_plan(
            cache_setup,
            "exit 0\n",
            run=hosted_run,
            tool_root_mode=0o777,
            root_owned_tools=False,
            tool_root_uid=1002,
        )
        self.assertNotEqual(0, unapproved_owner_result.returncode)
        self.assertEqual([], remaining)

    def test_hosted_tool_cache_descendants_share_the_runner_convention(self):
        # Measured on ubuntu-latest (Verjson/.github#1599): the root is uid 1001 gid 0
        # mode 0777 and every descendant -- directories, files, symlinks -- is uid 1001
        # gid 1000 mode 0777. setup-node resolves into that tree, so the admitted
        # convention has to cover the whole tree or every hosted run fails closed.
        cache_setup = lambda baseline: (baseline / "blob").write_text(
            "verified", encoding="utf-8"
        )
        hosted_run = self.candidate_plan_step()["run"].replace(
            'Path("/opt/hostedtoolcache")',
            'Path(os.environ["RUNNER_TOOL_CACHE"])',
        )
        hosted_shape = dict(
            run=hosted_run,
            tool_root_mode=0o777,
            tool_bin_mode=0o777,
            tool_tree_mode=0o777,
            root_owned_tools=False,
            tool_root_uid=1001,
            tool_tree_gid=1000,
        )

        result, remaining = self.run_candidate_plan(cache_setup, "exit 0\n", **hosted_shape)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], remaining)

        # A foreign owner anywhere inside the admitted tree is still rejected at each
        # relaxed site -- PATH ancestry, the selected executable, and the mounted tree
        # walk: the owner allowlist, not the mode, is what the hosted convention trusts.
        for foreign_relative_path, expected_rejection in (
            ("bin", "setup-node lexical PATH ancestry has unsafe ownership mode"),
            ("bin/npm", "trusted npm resolved path ancestry has unsafe ownership mode"),
            ("lib/node_modules/npm/package.json", "trusted tool tree entry has unapproved ownership"),
        ):
            with self.subTest(foreign_entry=foreign_relative_path):
                foreign_result, remaining = self.run_candidate_plan(
                    cache_setup,
                    "exit 0\n",
                    **hosted_shape,
                    foreign_entry=(foreign_relative_path, 1002),
                )
                self.assertNotEqual(0, foreign_result.returncode)
                self.assertIn(expected_rejection, foreign_result.stderr)
                self.assertEqual([], remaining)

        # The descendant exemption is unlocked only by a root that matches the hosted
        # convention exactly; a root-owned 0755 root with world-writable descendants
        # is tampering, even at the hosted path.
        strict_root_result, remaining = self.run_candidate_plan(
            cache_setup,
            "exit 0\n",
            run=hosted_run,
            tool_root_mode=0o755,
            tool_bin_mode=0o777,
            tool_tree_mode=0o777,
        )
        self.assertNotEqual(0, strict_root_result.returncode)
        self.assertIn(
            "setup-node lexical PATH ancestry has unsafe ownership mode", strict_root_result.stderr
        )
        self.assertEqual([], remaining)

    def test_nested_writable_tool_directory_rejects_replacement_executable(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "exit 91\n",
            tool_bin_mode=0o777,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], remaining)

    def test_runner_owned_tool_and_in_place_executable_mutation_are_rejected(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "exit 0\n",
            root_owned_tools=False,
            mutate_tool_in_place=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], remaining)

    def test_tool_prefix_swap_between_validation_and_bind_fails_closed(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_bytes(b"x" * (32 * 1024 * 1024)),
            "exit 0\n",
            swap_tool_prefix=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], remaining)

    def test_lexical_workspace_symlink_is_rejected(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "exit 0\n",
            workspace_symlink=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], remaining)

    def test_pwsh_accepts_only_verified_microsoft_runtime_contract(self):
        run = self.candidate_plan_step()["run"]
        self.assertIn('Path("/usr/bin/pwsh")', run)
        self.assertIn('Path("/opt/microsoft/powershell")', run)
        self.assertIn('"pwsh resolved runtime"', run)
        self.assertIn("system_metadata.st_uid != 0", run)

        valid, _ = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "pwsh\ntest -f pwsh-marker\n",
            pwsh_fixture="valid",
        )
        invalid, _ = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "exit 0\n",
            pwsh_fixture="invalid",
        )
        self.assertEqual(0, valid.returncode, valid.stderr)
        self.assertNotEqual(0, invalid.returncode)
    def test_each_candidate_script_receives_a_fresh_verified_cache_copy(self):
        for attempt in range(2):
            result, remaining = self.run_candidate_plan(
                lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
                """case "$2" in
first) rm -f "$NPM_CONFIG_CACHE/blob" ;;
second) [ "$(cat "$NPM_CONFIG_CACHE/blob")" = verified ] ;;
esac
[ "$NPM_CONFIG_CACHE" = "$npm_config_cache" ]
[ ! -e "$RUNNER_TEMP/baseline/blob" ]
if mv "$CANDIDATE_CACHE_ROOT" "$CANDIDATE_CACHE_ROOT-renamed" 2>/dev/null; then exit 92; fi
[ ! -e /run/docker.sock ]
[ ! -e /var/run/docker.sock ]
[ ! -e /root ]
[ ! -e "$RUNNER_TEMP/baseline" ]
python3 - <<'PY'
import socket
for endpoint in (("127.0.0.1", 22), ("169.254.169.254", 80)):
    probe = socket.socket()
    probe.settimeout(0.1)
    try:
        probe.connect(endpoint)
    except OSError:
        pass
    else:
        raise SystemExit(f"unexpected network reachability: {{endpoint}}")
    finally:
        probe.close()
PY
""",
            )
            with self.subTest(attempt=attempt):
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual([], remaining)

        steps = self.workflow["jobs"]["build-test"]["steps"]
        plan_index = steps.index(self.candidate_plan_step())
        cleanup = steps[plan_index + 1]
        self.assertEqual("Remove isolated candidate runtime caches", cleanup["name"])
        self.assertTrue(cleanup["if"].startswith("always() && "))
        self.assertIn('rm -rf -- "$CANDIDATE_CACHE_ROOT"', cleanup["run"])

    def marked_processes(self, marker):
        matches = []
        for command_line in Path("/proc").glob("[0-9]*/cmdline"):
            try:
                arguments = command_line.read_bytes().split(b"\0")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            if marker.encode() in arguments:
                matches.append(command_line.parent.name)
        return matches

    def test_success_and_failure_extinguish_background_and_setsid_descendants(self):
        for status in (0, 23):
            marker = f"verjson-cache-descendant-{uuid.uuid4().hex}"
            body = (
                f"setsid bash -c 'exec -a {marker} sleep 300' >/dev/null 2>&1 &\n"
                f"exit {status}\n"
            )
            result, remaining = self.run_candidate_plan(
                lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
                body,
            )
            with self.subTest(status=status):
                self.assertEqual(status != 0, result.returncode != 0)
                self.assertEqual([], remaining)
                self.assertEqual([], self.marked_processes(marker))

    def test_cache_inventory_rejects_symlinks_special_files_and_bounds(self):
        def symlink(baseline):
            (baseline / "link").symlink_to("outside")

        def special(baseline):
            os.mkfifo(baseline / "fifo")

        def too_many(baseline):
            for index in range(3):
                (baseline / str(index)).write_text("x", encoding="utf-8")

        def too_large(baseline):
            (baseline / "large").write_text("0123456789", encoding="utf-8")

        cases = (
            ("symlink", symlink, None),
            ("special", special, None),
            ("count", too_many, ("max_cache_files = 4096", "max_cache_files = 2")),
            ("bytes", too_large, ("max_cache_bytes = 268435456", "max_cache_bytes = 4")),
        )
        for name, setup, replacement in cases:
            run = self.candidate_plan_step()["run"]
            if replacement:
                run = run.replace(*replacement)
            with self.subTest(case=name):
                result, remaining = self.run_candidate_plan(setup, "exit 0\n", run=run)
                self.assertNotEqual(0, result.returncode)
                self.assertEqual([], remaining)

    def test_failing_candidate_script_removes_isolated_cache_root(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "exit 23\n",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([], remaining)

    def test_terminated_candidate_script_removes_cache_and_child_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner_temp = root / "runner-temp"
            runner_temp.mkdir()
            baseline = runner_temp / "baseline"
            baseline.mkdir()
            changelog_cache_root, changelog_ref, changelog_bytes = (
                self.create_changelog_cache_fixture(runner_temp)
            )
            workspace = root / "workspace"
            workspace.mkdir()
            (baseline / "blob").write_text("verified", encoding="utf-8")
            (workspace / "package.json").write_text(
                json.dumps({"scripts": {"first": "true"}}), encoding="utf-8"
            )
            tool_bin = root / "tool" / "bin"
            tool_bin.mkdir(parents=True)
            tool_bin.parent.chmod(0o755)
            tool_bin.chmod(0o755)
            npm = tool_bin / "npm"
            npm.write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s' \"$$\" > child.pid\nwhile :; do sleep 1; done\n",
                encoding="utf-8",
            )
            npm.chmod(0o755)
            node = tool_bin / "node"
            node.write_text("#!/usr/bin/env bash\nexec /usr/bin/node \"$@\"\n", encoding="utf-8")
            node.chmod(0o755)
            tool_package = tool_bin.parent / "lib" / "node_modules" / "npm" / "package.json"
            tool_package.parent.mkdir(parents=True)
            tool_package.write_text('{"name":"npm"}\n', encoding="utf-8")
            tool_package.chmod(0o644)
            for directory_name, _, _ in os.walk(tool_bin.parent):
                Path(directory_name).chmod(0o755)
            subprocess.run(
                ["sudo", "-n", "chown", "-R", "0:0", str(tool_bin.parent)], check=True
            )
            process = subprocess.Popen(
                ["/usr/bin/bash", "-c", self.candidate_plan_step()["run"]],
                cwd=workspace,
                env={
                    **os.environ,
                    "PATH": f"{tool_bin}:{os.environ['PATH']}",
                    "RUNNER_TEMP": str(runner_temp),
                    "CI_SCRIPT_PLAN": '["first"]',
                    "CANDIDATE_CACHE_ROOT": str(runner_temp / "verjson-candidate-caches-test"),
                    "npm_config_cache": str(baseline),
                    "RUNNER_TOOL_CACHE": str(tool_bin.parent),
                    "VERJSON_CHANGELOG_TOOL_CACHE": str(changelog_cache_root),
                    "VERJSON_CHANGELOG_CONTRACT_REF": changelog_ref,
                    "VERJSON_CHANGELOG_CONTRACT_SHA256": hashlib.sha256(changelog_bytes).hexdigest(),
                },
            )
            for _ in range(100):
                if (workspace / "child.pid").exists():
                    break
                time.sleep(0.02)
            process.send_signal(signal.SIGTERM)
            return_code = process.wait(timeout=20)
            self.assertEqual([], list(runner_temp.glob("verjson-candidate-caches-*")))
            subprocess.run(
                [
                    "sudo",
                    "-n",
                    "chown",
                    "-R",
                    f"{os.getuid()}:{os.getgid()}",
                    str(tool_bin.parent),
                ],
                check=True,
            )
            self.assertTrue((workspace / "child.pid").exists())
            self.assertNotEqual(0, return_code)


if __name__ == "__main__":
    unittest.main()
