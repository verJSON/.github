#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import os
import re
from contextlib import ExitStack, redirect_stderr
from io import StringIO
from pathlib import Path
import textwrap
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PLAN_ACTION = ROOT / ".github/actions/node-ci-script-plan"
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "scripts"))
import ci_build_artifacts as artifact_helper
sys.path.insert(0, str(SCRIPT_PLAN_ACTION))
import script_plan as shared_script_plan
LEGACY = ROOT / ".github/workflows/node-ci.yml"
PROTECTED = ROOT / ".github/workflows/node-ci-protected.yml"
GENERATOR = ROOT / "scripts/gen-node-ci-protected.py"
TRUSTED_HELPER = ROOT / "scripts/ci_build_artifacts.py"
HEAD = "a" * 40
LEGACY_SHA256 = "eb56f66fd892809c9c470dded58ed2371c3412af95a7e6668c924812a56ba5b0"


class RequiredWorkflowIdentityTest(unittest.TestCase):
    def load_generator(self):
        spec = importlib.util.spec_from_file_location("gen_node_ci_protected", GENERATOR)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

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
        before_mtime = PROTECTED.stat().st_mtime_ns
        subprocess.run(["python3", "scripts/gen-node-ci-protected.py", "--check"], cwd=ROOT, check=True)
        self.assertEqual(before, PROTECTED.read_bytes())
        self.assertEqual(before_mtime, PROTECTED.stat().st_mtime_ns)

    def test_protected_generator_check_compares_exact_bytes_without_writing(self):
        generator = self.load_generator()
        expected = generator.render().encode("utf-8")
        self.assertLessEqual(len(LEGACY.read_bytes()), generator.MAX_SOURCE_BYTES)
        self.assertLessEqual(len(expected), generator.MAX_OUTPUT_BYTES)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "node-ci-protected.yml"
            stale = bytearray(expected)
            stale[-1] ^= 1
            target.write_bytes(stale)
            before_mtime = target.stat().st_mtime_ns
            generator.TARGET = target
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                generator.main(["--check"])
            self.assertEqual(bytes(stale), target.read_bytes())
            self.assertEqual(before_mtime, target.stat().st_mtime_ns)

    def test_protected_generator_byte_guards_reject_utf8_overflow_without_writing(self):
        generator = self.load_generator()
        sentinel = b"existing protected workflow\n"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "node-ci.yml"
            target = root / "node-ci-protected.yml"
            target.write_bytes(sentinel)
            generator.SOURCE = source
            generator.TARGET = target

            source.write_bytes(("é" * (generator.MAX_SOURCE_BYTES // 2 + 1)).encode("utf-8"))
            before_source_guard = target.stat().st_mtime_ns
            source_error = StringIO()
            with redirect_stderr(source_error), self.assertRaises(SystemExit):
                generator.main(["--check"])
            self.assertIn("source workflow exceeds 480000 UTF-8 bytes", source_error.getvalue())
            self.assertEqual(sentinel, target.read_bytes())
            self.assertEqual(before_source_guard, target.stat().st_mtime_ns)

            source.write_text("valid source\n", encoding="utf-8")
            generator.render = lambda: "é" * (generator.MAX_OUTPUT_BYTES // 2 + 1)
            before_output_guard = target.stat().st_mtime_ns
            output_error = StringIO()
            with redirect_stderr(output_error), self.assertRaises(SystemExit):
                generator.main(["--check"])
            self.assertIn("generated protected workflow exceeds 480000 UTF-8 bytes", output_error.getvalue())
            self.assertEqual(sentinel, target.read_bytes())
            self.assertEqual(before_output_guard, target.stat().st_mtime_ns)

    def test_protected_generator_rejects_decorated_async_or_parameterized_helper(self):
        generator = self.load_generator()
        helper = (ROOT / "scripts/ci_build_artifacts.py").read_text(encoding="utf-8")
        mutations = (
            helper.replace(
                "def run_protected_candidate_plan() -> None:",
                "@staticmethod\ndef run_protected_candidate_plan() -> None:",
                1,
            ),
            helper.replace(
                "def run_protected_candidate_plan() -> None:",
                "async def run_protected_candidate_plan() -> None:",
                1,
            ),
            helper.replace(
                "def run_protected_candidate_plan() -> None:",
                "def run_protected_candidate_plan(untrusted=None) -> None:",
                1,
            ),
        )
        for mutated in mutations:
            with self.assertRaises(SystemExit):
                generator.protected_candidate_helper_body(mutated)

    def test_protected_candidate_runtime_uses_the_pinned_helper_command(self):
        steps = self.workflow["jobs"]["build-test"]["steps"]
        fetch_index = next(
            index for index, step in enumerate(steps)
            if step.get("name") == "Download pinned protected candidate helper"
        )
        authorization_index = next(
            index for index, step in enumerate(steps)
            if step.get("name") == "Authorize producer or consumer from immutable policy"
        )
        runner_index = next(
            index for index, step in enumerate(steps)
            if step.get("name") == "Run exact credentialless consumer script plan"
        )
        fetch_step = steps[fetch_index]
        run_step = steps[runner_index]
        self.assertLess(fetch_index, authorization_index)
        self.assertLess(authorization_index, runner_index)
        self.assertIn("JOB_WORKFLOW_SHA", fetch_step["env"])
        self.assertIn("${JOB_WORKFLOW_SHA}", fetch_step["run"])
        self.assertIn(
            'python3 -I "$RUNNER_TEMP/ci_build_artifacts.py" run-protected-candidate-plan &',
            run_step["run"],
        )
        self.assertNotIn("python3 -I - <<'PY'", run_step["run"])

    def test_protected_helper_is_staged_once_and_reused_read_only_by_runner_uid(self):
        steps = self.workflow["jobs"]["build-test"]["steps"]
        fetch_step = next(
            step for step in steps
            if step.get("name") == "Download pinned protected candidate helper"
        )
        reuse_step = next(
            step for step in steps
            if step.get("name") == "Download trusted script-plan receipt supervisor"
        )
        self.assertEqual(fetch_step.get("id"), "protected-candidate-helper")
        self.assertIn("sha256sum", fetch_step["run"])
        self.assertNotIn("curl ", reuse_step["run"])
        with tempfile.TemporaryDirectory(prefix="protected-helper-stage-") as temporary:
            root = Path(temporary)
            runner_temp = root / "runner temp"
            runner_temp.mkdir(mode=0o700)
            output_file = root / "fetch-output"
            fixture = root / "helper-fixture.py"
            fixture.write_bytes(TRUSTED_HELPER.read_bytes())
            fake_bin = root / "bin"
            fake_bin.mkdir()
            curl_log = root / "curl-calls"
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "destination=''\n"
                "while (($#)); do\n"
                "  if [[ $1 == -o ]]; then destination=$2; shift 2; else shift; fi\n"
                "done\n"
                "[[ -n $destination ]]\n"
                "cp -- \"$CI_HELPER_FIXTURE\" \"$destination\"\n"
                "printf 'called\\n' >> \"$CI_HELPER_CURL_LOG\"\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o700)
            environment = os.environ.copy()
            environment.update(
                {
                    "CI_HELPER_FIXTURE": str(fixture),
                    "CI_HELPER_CURL_LOG": str(curl_log),
                    "GITHUB_OUTPUT": str(output_file),
                    "JOB_WORKFLOW_SHA": "a" * 40,
                    "PATH": f"{fake_bin}:{environment['PATH']}",
                    "RUNNER_TEMP": str(runner_temp),
                }
            )
            subprocess.run(
                ["bash", "-euo", "pipefail", "-c", fetch_step["run"]],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            helper = runner_temp / "ci_build_artifacts.py"
            metadata = helper.stat(follow_symlinks=False)
            self.assertTrue(helper.is_file())
            self.assertEqual(metadata.st_uid, os.geteuid())
            self.assertEqual(metadata.st_nlink, 1)
            self.assertEqual(metadata.st_mode & 0o777, 0o400)
            with self.assertRaises(PermissionError):
                os.open(helper, os.O_WRONLY)
            staged_digest = output_file.read_text(encoding="utf-8").strip().split("=", 1)[1]
            self.assertEqual(staged_digest, hashlib.sha256(helper.read_bytes()).hexdigest())
            reuse_environment = environment.copy()
            reuse_environment["PROTECTED_CANDIDATE_HELPER_SHA256"] = staged_digest
            subprocess.run(
                ["bash", "-euo", "pipefail", "-c", reuse_step["run"]],
                env=reuse_environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(curl_log.read_text(encoding="utf-8").splitlines(), ["called"])
            self.assertEqual(helper.stat().st_mode & 0o777, 0o400)
            self.assertEqual(hashlib.sha256(helper.read_bytes()).hexdigest(), staged_digest)

    def test_candidate_plan_executes_successfully_through_the_staged_trusted_helper(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "printf 'trusted helper candidate ran\\n'\n",
            script_plan=["first"],
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("trusted helper candidate ran", result.stderr)
        self.assertEqual([], remaining)

    def test_protected_bubblewrap_continues_after_failure_and_extinguishes_detached_child(self):
        with tempfile.TemporaryDirectory(prefix="entry receipts ") as capture_dir:
            receipt_capture = Path(capture_dir) / "receipts.json"
            result, remaining = self.run_candidate_plan(
                lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
                "case \" $* \" in\n"
                "  *' run first '*)\n"
                "    /usr/bin/setsid /bin/bash -c 'sleep 1; touch \"$1\"' detached \"$GITHUB_WORKSPACE/late-marker\" </dev/null >/dev/null 2>&1 &\n"
                "    /usr/bin/setsid /bin/bash -c 'trap \"\" TERM; kill -STOP $$; sleep 1; touch \"$1\"' detached \"$GITHUB_WORKSPACE/stopped-late\" </dev/null >/dev/null 2>&1 &\n"
                "    /usr/bin/python3 -c 'import os,signal,sys,time\np=os.fork()\nif p == 0:\n q=os.fork()\n if q: os._exit(0)\n os.setsid()\n signal.signal(signal.SIGTERM, signal.SIG_IGN)\n time.sleep(1)\n open(sys.argv[1],\"w\").close()\n os._exit(0)\nos.waitpid(p,0)' \"$GITHUB_WORKSPACE/double-fork-late\" </dev/null >/dev/null 2>&1 &\n"
                "    printf started > \"$GITHUB_WORKSPACE/first-started\"\n"
                "    exit 23 ;;\n"
                "  *' run second '*)\n"
                "    sleep 2\n"
                "    test ! -e \"$GITHUB_WORKSPACE/stopped-late\" || exit 31\n"
                "    test ! -e \"$GITHUB_WORKSPACE/double-fork-late\" || exit 32\n"
                "    test -f \"$GITHUB_WORKSPACE/first-started\" || exit 30\n"
                "    test ! -e \"$GITHUB_WORKSPACE/late-marker\" || exit 29\n"
                "    printf ran > \"$GITHUB_WORKSPACE/second-ran\" ;;\n"
                "esac\n",
                script_plan=["first", "second"],
                environment_updates={"CI_LEVEL_FAIL_FAST": "false"},
                receipt_capture=receipt_capture,
                post_run_absent="late-marker",
            )
            self.assertNotEqual(0, result.returncode, result.stderr)
            self.assertEqual([], remaining)
            receipts = json.loads(receipt_capture.read_text(encoding="utf-8"))
            self.assertEqual([receipt["state"] for receipt in receipts], ["completed", "completed"])
            self.assertEqual([receipt["exitCode"] for receipt in receipts], [23, 0])
            self.assertEqual([receipt["identity"]["jobId"] for receipt in receipts], ["build-test"] * 2)
            self.assertEqual([receipt["identity"]["levelId"] for receipt in receipts], ["test-levels"] * 2)

    def test_protected_bubblewrap_signal_style_exit_does_not_continue(self):
        with tempfile.TemporaryDirectory(prefix="entry receipts ") as capture_dir:
            receipt_capture = Path(capture_dir) / "receipts.json"
            result, remaining = self.run_candidate_plan(
                lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
                "case \" $* \" in\n"
                "  *' run first '*) exit 143 ;;\n"
                "  *' run second '*) touch \"$GITHUB_WORKSPACE/second-ran\" ;;\n"
                "esac\n",
                script_plan=["first", "second"],
                environment_updates={"CI_LEVEL_FAIL_FAST": "false"},
                receipt_capture=receipt_capture,
                post_run_absent="second-ran",
            )
            self.assertNotEqual(0, result.returncode, result.stderr)
            self.assertEqual([], remaining)
            receipts = json.loads(receipt_capture.read_text(encoding="utf-8"))
            self.assertEqual(
                [receipt["state"] for receipt in receipts],
                ["candidate-terminal", "not-run-aborted"],
            )
            self.assertEqual(receipts[0]["exitCode"], 143)
            self.assertIsNone(receipts[0]["signal"])
            self.assertTrue(receipts[0]["candidateStarted"])

    def test_protected_candidate_self_sigint_and_sigterm_are_terminal_without_host_cancellation(self):
        for self_signal, expected_signal in (("sigint", signal.SIGINT), ("sigterm", signal.SIGTERM)):
            with self.subTest(self_signal=self_signal), tempfile.TemporaryDirectory(prefix="entry receipts ") as capture_dir:
                receipt_capture = Path(capture_dir) / "receipts.json"
                plan_receipt_capture = Path(capture_dir) / "plan.json"
                result, remaining = self.run_candidate_plan(
                    lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
                    "case \"$*\" in\n"
                    f"  *self-{self_signal}*) exec /usr/bin/python3 -c 'import os,signal; signal.signal(signal.{expected_signal.name}, signal.SIG_DFL); os.kill(os.getpid(), signal.{expected_signal.name})' ;;\n"
                    "  *second*) touch \"$GITHUB_WORKSPACE/second-ran\" ;;\n"
                    "esac\n",
                    script_plan=[f"self-{self_signal}", "second"],
                    environment_updates={"CI_LEVEL_FAIL_FAST": "false"},
                    receipt_capture=receipt_capture,
                    plan_receipt_capture=plan_receipt_capture,
                    post_run_absent="second-ran",
                )
                self.assertNotEqual(0, result.returncode, result.stderr + result.stdout)
                self.assertEqual([], remaining)
                receipts = json.loads(receipt_capture.read_text(encoding="utf-8"))
                self.assertEqual(
                    [item["state"] for item in receipts],
                    ["candidate-terminal", "not-run-aborted"],
                )
                self.assertTrue(receipts[0]["candidateStarted"])
                self.assertEqual(receipts[0]["signal"], int(expected_signal))
                self.assertIsNone(receipts[0]["hostCancellationSignal"])
                self.assertIsNone(receipts[0]["supervisorSignal"])
                plan = json.loads(plan_receipt_capture.read_text(encoding="utf-8"))
                self.assertEqual(plan["state"], "infrastructure-failure")
                self.assertIsNone(plan["hostCancellationSignal"])

    def test_protected_bubblewrap_startup_failure_does_not_continue_after_deleted_cwd(self):
        with tempfile.TemporaryDirectory(prefix="entry receipts ") as capture_dir:
            receipt_capture = Path(capture_dir) / "receipts.json"
            result, remaining = self.run_candidate_plan(
                lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
                "case \" $* \" in\n"
                "  *' run first '*) rm -rf \"$GITHUB_WORKSPACE/nested\"; exit 23 ;;\n"
                "  *' run nested-after '*) touch \"$GITHUB_WORKSPACE/nested-ran\" ;;\n"
                "esac\n",
                script_plan=["first"],
                nested_manifests=[
                    {"path": "nested", "approvedPackages": [], "scriptPlan": ["nested-after"]}
                ],
                environment_updates={"CI_LEVEL_FAIL_FAST": "false"},
                receipt_capture=receipt_capture,
                post_run_absent="nested-ran",
            )
            self.assertNotEqual(0, result.returncode, result.stderr)
            self.assertEqual([], remaining)
            receipts = json.loads(receipt_capture.read_text(encoding="utf-8"))
            self.assertEqual(
                [receipt["state"] for receipt in receipts],
                ["completed", "infrastructure-failure"],
            )
            self.assertEqual(receipts[0]["exitCode"], 23)

    def test_protected_bubblewrap_cancellation_after_ack_prevents_candidate_start(self):
        with tempfile.TemporaryDirectory(prefix="entry receipts ") as capture_dir:
            receipt_capture = Path(capture_dir) / "receipts.json"
            plan_receipt_capture = Path(capture_dir) / "plan.json"
            result, remaining = self.run_candidate_plan(
                lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
                "case \"$2\" in\n"
                " first) touch \"$GITHUB_WORKSPACE/first-ran\" ;;\n"
                " second) touch \"$GITHUB_WORKSPACE/second-ran\" ;;\n"
                "esac\n",
                script_plan=["first", "second"],
                environment_updates={"CI_LEVEL_FAIL_FAST": "false"},
                receipt_capture=receipt_capture,
                plan_receipt_capture=plan_receipt_capture,
                post_run_absent=["first-ran", "second-ran", ".after-ack-ready"],
                cancel_after_ack_probe=True,
                block_host_signal_mask=True,
            )

            self.assertNotEqual(0, result.returncode, result.stderr)
            self.assertEqual([], remaining)
            receipts = json.loads(receipt_capture.read_text(encoding="utf-8"))
            self.assertEqual(
                [receipt["state"] for receipt in receipts], ["interrupted", "not-run-aborted"]
            )
            self.assertFalse(receipts[0]["candidateStarted"], receipts[0])
            self.assertIsNone(receipts[0]["exitCode"], receipts[0])
            self.assertIsNone(receipts[0]["signal"], receipts[0])
            self.assertEqual(receipts[0]["hostCancellationSignal"], signal.SIGTERM)
            self.assertEqual(receipts[0]["supervisorSignal"], signal.SIGTERM)
            self.assertEqual(receipts[0]["cleanup"], "verified")
            plan = json.loads(plan_receipt_capture.read_text(encoding="utf-8"))
            self.assertEqual(plan["state"], "interrupted")
            self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)

    def test_protected_bubblewrap_cancellation_during_cleanup_invalidates_plan(self):
        with tempfile.TemporaryDirectory(prefix="entry receipts ") as capture_dir:
            receipt_capture = Path(capture_dir) / "receipts.json"
            result, remaining = self.run_candidate_plan(
                lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
                "case \"$2\" in\n"
                " first)\n"
                "  /usr/bin/setsid /bin/bash -c 'trap \"\" TERM; while :; do sleep 0.02; done' detached </dev/null >/dev/null 2>&1 &\n"
                "  exit 0 ;;\n"
                " second) touch \"$GITHUB_WORKSPACE/second-ran\" ;;\n"
                "esac\n",
                script_plan=["first", "second"],
                environment_updates={"CI_LEVEL_FAIL_FAST": "false"},
                receipt_capture=receipt_capture,
                plan_receipt_capture=Path(capture_dir) / "plan.json",
                post_run_absent="second-ran",
                cancel_cleanup_probe=True,
            )
            self.assertNotEqual(0, result.returncode, result.stderr)
            self.assertEqual([], remaining)
            receipts = json.loads(receipt_capture.read_text(encoding="utf-8"))
            self.assertEqual([item["state"] for item in receipts], ["interrupted", "not-run-aborted"], receipts)
            self.assertTrue(receipts[0]["candidateStarted"])
            self.assertEqual(receipts[0]["exitCode"], 0)
            self.assertIsNone(receipts[0]["signal"])
            self.assertEqual(receipts[0]["hostCancellationSignal"], signal.SIGTERM)
            self.assertEqual(receipts[0]["cleanup"], "verified")
            plan = json.loads((Path(capture_dir) / "plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["state"], "interrupted")
            self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)

    def test_protected_host_captures_pending_sigterm_before_handler_unblock(self):
        with tempfile.TemporaryDirectory(prefix="entry receipts ") as capture_dir:
            receipt_capture = Path(capture_dir) / "receipts.json"
            plan_receipt_capture = Path(capture_dir) / "plan.json"
            result, remaining = self.run_candidate_plan(
                lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
                "case \"$2\" in\n"
                " first) touch \"$GITHUB_WORKSPACE/first-ran\" ;;\n"
                " second) touch \"$GITHUB_WORKSPACE/second-ran\" ;;\n"
                "esac\n",
                script_plan=["first", "second"],
                environment_updates={"CI_LEVEL_FAIL_FAST": "false", "VERJSON_CI_TEST_PENDING_HOST_SIGNAL": "1"},
                receipt_capture=receipt_capture,
                plan_receipt_capture=plan_receipt_capture,
                post_run_absent=["first-ran", "second-ran"],
            )
            self.assertNotEqual(0, result.returncode, result.stderr)
            self.assertEqual([], remaining)
            self.assertTrue(receipt_capture.is_file(), result.stderr)
            receipts = json.loads(receipt_capture.read_text(encoding="utf-8"))
            plan = json.loads(plan_receipt_capture.read_text(encoding="utf-8"))
            self.assertEqual(plan["state"], "interrupted")
            self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)
            self.assertEqual(
                [receipt["state"] for receipt in receipts],
                ["interrupted", "not-run-aborted"],
            )
            self.assertFalse(receipts[0]["candidateStarted"])
            self.assertIsNone(receipts[0]["exitCode"])
            self.assertEqual(receipts[0]["hostCancellationSignal"], signal.SIGTERM)

    def test_protected_bubblewrap_cancels_live_term_resistant_candidate_and_descendants(self):
        with tempfile.TemporaryDirectory(prefix="entry receipts ") as capture_dir:
            receipt_capture = Path(capture_dir) / "receipts.json"
            plan_receipt_capture = Path(capture_dir) / "plan.json"
            candidate_script = """count_file="$GITHUB_WORKSPACE/.live-cancel-count"
count=0
[[ -f "$count_file" ]] && read -r count < "$count_file"
count=$((count + 1))
/usr/bin/python3 -c 'import signal; assert signal.SIGTERM not in signal.pthread_sigmask(signal.SIG_BLOCK, set())'
printf '%s\\n' "$count" > "$count_file"
if (( count == 1 )); then
 /usr/bin/setsid /bin/bash -c 'trap "" TERM; touch "$1"; for _ in {1..1000}; do [[ -f "$3" ]] && break; sleep 0.01; done; [[ -f "$3" ]] || exit 91; sleep 4; touch "$2"; while :; do :; done' detached "$GITHUB_WORKSPACE/.term-resistant-started" "$GITHUB_WORKSPACE/.term-resistant-late" "$GITHUB_WORKSPACE/.cancel-sent" </dev/null >/dev/null 2>&1 &
 for _ in {1..200}; do [[ -f "$GITHUB_WORKSPACE/.term-resistant-started" ]] && break; sleep 0.01; done
 [[ -f "$GITHUB_WORKSPACE/.term-resistant-started" ]] || exit 90
 trap "" TERM
 touch "$GITHUB_WORKSPACE/.candidate-ready"
 while :; do sleep 0.02; done
else
 touch "$GITHUB_WORKSPACE/second-ran"
fi
"""
            result, remaining = self.run_candidate_plan(
                lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
                candidate_script,
                script_plan=["live-cancel", "second"],
                environment_updates={"CI_LEVEL_FAIL_FAST": "false"},
                receipt_capture=receipt_capture,
                plan_receipt_capture=plan_receipt_capture,
                post_run_absent=["second-ran", ".term-resistant-late"],
                post_run_absent_delay=2.3,
                cancel_live_candidate_probe=True,
                block_host_signal_mask=True,
            )

            self.assertNotEqual(0, result.returncode, result.stderr)
            self.assertEqual([], remaining)
            elapsed_match = re.search(r"LIVE_CANDIDATE_CANCEL_SECONDS=([0-9.]+)", result.stderr)
            self.assertIsNotNone(elapsed_match, result.stderr)
            self.assertGreaterEqual(float(elapsed_match.group(1)), 0.45, result.stderr)
            self.assertTrue(receipt_capture.is_file(), result.stderr)
            receipts = json.loads(receipt_capture.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["state"] for item in receipts],
                ["interrupted", "not-run-aborted"],
            )
            self.assertIsNone(receipts[0]["exitCode"])
            self.assertEqual(receipts[0]["signal"], signal.SIGKILL)
            self.assertEqual(receipts[0]["hostCancellationSignal"], signal.SIGTERM)
            self.assertEqual(receipts[0]["supervisorSignal"], signal.SIGTERM)
            self.assertEqual(receipts[0]["cleanup"], "verified")
            plan = json.loads(plan_receipt_capture.read_text(encoding="utf-8"))
            self.assertEqual(plan["state"], "interrupted")
            self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)

    def test_protected_candidate_cannot_access_or_forge_the_supervisor_channel(self):
        with tempfile.TemporaryDirectory(prefix="entry receipts ") as capture_dir:
            receipt_capture = Path(capture_dir) / "receipts.json"
            result, remaining = self.run_candidate_plan(
                lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
                "case \"$2\" in\n"
                " first)\n"
                "  /usr/bin/python3 -I -c 'import os,sys; p=\"/proc/1/fd/1\";\n"
                "for flags in (os.O_RDONLY, os.O_WRONLY):\n"
                " try:\n  fd=os.open(p, flags)\n"
                " except PermissionError:\n  continue\n"
                " else:\n  os.close(fd); sys.exit(91)\n"
                "print(\"{\\\"version\\\":1,\\\"pid\\\":1,\\\"state\\\":\\\"candidate-exit\\\"}\")'\n"
                "  ;;\n"
                "esac\n",
                script_plan=["first"],
                receipt_capture=receipt_capture,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual([], remaining)
            receipts = json.loads(receipt_capture.read_text(encoding="utf-8"))
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["state"], "completed")
            self.assertEqual(receipts[0]["exitCode"], 0)
            self.assertIn('"state":"candidate-exit"', result.stderr)

    def test_consumer_mounts_verified_artifact_read_only_with_live_fd_at_launch(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "printf 'consumer candidate ran\\n'\n",
            script_plan=["first"],
            verified_artifact=True,
            bwrap_probe=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], remaining)

    def test_workflow_mappings_have_no_duplicate_keys(self):
        for path in (LEGACY, PROTECTED):
            with self.subTest(workflow=path.name):
                root = yaml.compose(path.read_text(encoding="utf-8"))

                def visit(node, location="$"):
                    if isinstance(node, yaml.MappingNode):
                        seen = set()
                        for key, value in node.value:
                            key_value = key.value if isinstance(key, yaml.ScalarNode) else str(key)
                            self.assertNotIn(key_value, seen, f"duplicate key {key_value!r} at {location}")
                            seen.add(key_value)
                            visit(value, f"{location}.{key_value}")
                    elif isinstance(node, yaml.SequenceNode):
                        for index, value in enumerate(node.value):
                            visit(value, f"{location}[{index}]")

                visit(root)

    def test_postgres_profile_is_validated_before_services_and_credentialed_install(self):
        for workflow_path in (LEGACY, PROTECTED):
            with self.subTest(workflow=workflow_path.name):
                workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
                steps = workflow["jobs"]["build-test"]["steps"]
                validation = next(
                    (index, step)
                    for index, step in enumerate(steps)
                    if step.get("name") == "Validate Postgres sandbox profile request"
                )
                validation_index, validation_step = validation
                if workflow_path == LEGACY:
                    expected_compatibility_ranges = "${{ inputs.secretless-compatibility-ranges }}"
                else:
                    expected_compatibility_ranges = (
                        "${{ needs.acquire-secretless-dependencies.outputs.protected-baseline-request "
                        "|| inputs.secretless-compatibility-ranges }}"
                    )
                self.assertEqual(
                    validation_step.get("env", {}).get("POSTGRES_COMPATIBILITY_RANGES"),
                    expected_compatibility_ranges,
                )
                self.assertIn(
                    '--compatibility-ranges "$POSTGRES_COMPATIBILITY_RANGES"',
                    validation_step["run"],
                )
                self.assertIn("inputs.postgres-sandbox-profile != ''", validation_step["if"])
                self.assertIn("python3 -I", validation_step["run"])
                self.assertIn('ci_postgres_sandbox.py" validate', validation_step["run"])
                for service_name in ("Start database service", "Start cache service"):
                    service_index = next(
                        index for index, step in enumerate(steps)
                        if step.get("name") == service_name
                    )
                    self.assertLess(validation_index, service_index)
                credentialed_installs = [
                    index
                    for index, step in enumerate(steps)
                    if "npm ci" in step.get("run", "")
                    and step.get("env", {}).get("NODE_AUTH_TOKEN")
                    == "${{ secrets.NODE_AUTH_TOKEN }}"
                ]
                self.assertTrue(credentialed_installs)
                self.assertLess(validation_index, min(credentialed_installs))

    def test_postgres_compatibility_conflicts_fail_before_token_acquisition(self):
        generator = self.load_generator()
        generated_protected = yaml.safe_load(generator.render())

        for workflow_path in (LEGACY, PROTECTED):
            with self.subTest(workflow=workflow_path.name):
                workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
                eligibility = workflow["jobs"]["eligibility"]
                guard = next(
                    step
                    for step in eligibility["steps"]
                    if step.get("name") == "Reject Postgres compatibility profile conflicts"
                )
                self.assertNotIn("if", guard)
                self.assertEqual(
                    "${{ inputs.postgres-sandbox-profile }}",
                    guard["env"]["POSTGRES_SANDBOX_PROFILE"],
                )
                self.assertEqual(
                    "${{ inputs.secretless-compatibility-ranges }}",
                    guard["env"]["POSTGRES_PROFILE_COMPATIBILITY_INPUT"],
                )
                if workflow_path == LEGACY:
                    self.assertNotIn("PROTECTED_BASELINE_DECLARATION_PATH", guard["env"])
                else:
                    self.assertEqual(
                        "${{ inputs.protected-type-surface-declaration-path }}",
                        guard["env"]["PROTECTED_BASELINE_DECLARATION_PATH"],
                    )
                    generated_guard = next(
                        step
                        for step in generated_protected["jobs"]["eligibility"]["steps"]
                        if step.get("name") == "Reject Postgres compatibility profile conflicts"
                    )
                    self.assertEqual(guard["env"], generated_guard["env"])
                    self.assertEqual(guard["run"], generated_guard["run"])

                self.assertIn("Postgres sandbox profile", guard["run"])
                self.assertIn('compatibility_ranges.strip() not in {"", "{}"}', guard["run"])
                self.assertIn("or protected_baseline", guard["run"])
                self.assertIn("sys.exit(", guard["run"])
                self.assertNotIn("NODE_AUTH_TOKEN", guard.get("env", {}))

                acquisition = workflow["jobs"]["acquire-secretless-dependencies"]
                acquisition_needs = acquisition.get("needs", [])
                if isinstance(acquisition_needs, str):
                    acquisition_needs = [acquisition_needs]
                self.assertIn("eligibility", acquisition_needs)
                self.assertNotIn("always()", acquisition.get("if", ""))
                token_steps = [
                    step
                    for step in acquisition["steps"]
                    if "NODE_AUTH_TOKEN" in step.get("env", {})
                ]
                self.assertTrue(token_steps, "dependency acquisition must remain token-bearing")

    def test_postgres_profile_rejects_artifact_sharing_before_token_acquisition(self):
        generator = self.load_generator()
        generated_protected = yaml.safe_load(generator.render())

        for workflow_path in (LEGACY, PROTECTED):
            with self.subTest(workflow=workflow_path.name):
                workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
                workflow_events = workflow.get("on", workflow.get(True, {}))
                self.assertEqual(
                    "level",
                    workflow_events["workflow_call"]["inputs"]["build-artifact-role"]["default"],
                )
                eligibility = workflow["jobs"]["eligibility"]
                guard = next(
                    step
                    for step in eligibility["steps"]
                    if step.get("name") == "Reject Postgres compatibility profile conflicts"
                )
                generated_guard = next(
                    step
                    for step in generated_protected["jobs"]["eligibility"]["steps"]
                    if step.get("name") == "Reject Postgres compatibility profile conflicts"
                )
                self.assertEqual(guard["run"], generated_guard["run"])
                for variable in (
                    "POSTGRES_SANDBOX_PROFILE",
                    "POSTGRES_PROFILE_COMPATIBILITY_INPUT",
                    "BUILD_ARTIFACT_ROLE",
                ):
                    self.assertEqual(guard["env"].get(variable), generated_guard["env"].get(variable))

                self.assertLess(
                    eligibility["steps"].index(guard),
                    len(eligibility["steps"]),
                )
                acquisition = workflow["jobs"]["acquire-secretless-dependencies"]
                self.assertIn("eligibility", acquisition.get("needs", []))
                self.assertNotIn("always()", acquisition.get("if", ""))
                self.assertTrue(
                    any(
                        "NODE_AUTH_TOKEN" in step.get("env", {})
                        for step in acquisition.get("steps", [])
                    ),
                    "dependency acquisition must remain token-bearing",
                )

                for role, expected_returncode in (("level", 0), ("producer", 1), ("consumer", 1)):
                    with self.subTest(role=role):
                        environment = os.environ.copy()
                        environment.update(
                            {
                                "POSTGRES_SANDBOX_PROFILE": "postgres-17",
                                "POSTGRES_PROFILE_COMPATIBILITY_INPUT": "",
                                "BUILD_ARTIFACT_ROLE": role,
                            }
                        )
                        environment.pop("PROTECTED_BASELINE_DECLARATION_PATH", None)
                        result = subprocess.run(
                            ["bash", "-euo", "pipefail", "-c", guard["run"]],
                            check=False,
                            capture_output=True,
                            text=True,
                            env=environment,
                        )
                        self.assertEqual(expected_returncode, result.returncode, result.stderr)

                self.assertEqual(
                    "${{ inputs.build-artifact-role }}",
                    guard["env"]["BUILD_ARTIFACT_ROLE"],
                )

    def test_postgres_candidate_runtime_is_present_in_canonical_and_protected_paths(self):
        canonical_workflow = yaml.safe_load(LEGACY.read_text(encoding="utf-8"))
        canonical_step = next(
            step
            for step in canonical_workflow["jobs"]["build-test"]["steps"]
            if step.get("name") == "Run exact credentialless consumer script plan"
        )
        protected_step = next(
            step
            for step in self.workflow["jobs"]["build-test"]["steps"]
            if step.get("name") == "Run exact credentialless consumer script plan"
        )
        generator = self.load_generator()
        protected_runtime = generator.protected_candidate_helper_body()
        generated_workflow = yaml.safe_load(generator.render())
        generated_protected_step = next(
            step
            for step in generated_workflow["jobs"]["build-test"]["steps"]
            if step.get("name") == "Run exact credentialless consumer script plan"
        )

        for runtime_name, runtime in (
            ("canonical", canonical_step["run"]),
            ("protected helper", protected_runtime),
        ):
            with self.subTest(runtime=runtime_name):
                self.assertIn("postgres_profile_module.candidate_environment(", runtime)
                self.assertIn("postgres_profile_module.profile_mount_args(", runtime)
                self.assertIn("postgres_profile_module.verify_profile_runtime(", runtime)
                self.assertLess(
                    runtime.index("postgres_profile_module.verify_profile_runtime("),
                    runtime.index("arguments = ["),
                )

        self.assertEqual(1, canonical_step["run"].count("*postgres_mount_args,"))
        self.assertEqual(1, protected_runtime.count("*postgres_mount_args,"))
        self.assertEqual(
            "${{ inputs.postgres-sandbox-profile }}",
            protected_step["env"]["VERJSON_CI_POSTGRES_PROFILE"],
        )
        self.assertEqual(
            "${{ runner.temp }}/ci-postgres-sandbox-state.json",
            protected_step["env"]["VERJSON_CI_POSTGRES_SANDBOX_STATE"],
        )
        self.assertIn(
            '"$RUNNER_TEMP/ci_build_artifacts.py" run-protected-candidate-plan',
            protected_step["run"],
        )
        self.assertEqual(protected_step["env"], generated_protected_step["env"])
        self.assertEqual(protected_step["run"], generated_protected_step["run"])

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
        self.assertIn("inputs.secretless-pr || inputs.secretless-trusted-ref", warm["if"])
        self.assertNotIn("RUN_DEFAULTS", plan["env"])
        self.assertIn(
            'run_defaults = os.environ.get("SCRIPT_PLAN_DEFAULT_PLAN") == "true"',
            self.candidate_plan_step()["run"],
        )
        self.assertIn("SCRIPT_PLAN_ENTRIES_FILE", plan["env"])
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
        self.assertIn("VERJSON_CHANGELOG_TOOL_CACHE", self.candidate_plan_step()["run"])
        candidate_run = self.candidate_plan_step()["run"]
        self.assertIn("ci_build_artifacts.bubblewrap_network_args(", candidate_run)
        self.assertIn("postgres_profile=postgres_profile is not None", candidate_run)
        self.assertIn("*network_args,", candidate_run)
        self.assertIn('"--ro-bind"', self.candidate_plan_step()["run"])

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
        self.assertEqual(
            "(inputs.build-artifact-role == 'level' || inputs.build-artifact-role == 'consumer' "
            "|| inputs.build-artifact-role == 'producer') && needs.eligibility.outputs.should-run != 'false'",
            acquisition["if"],
        )
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
        compatibility_condition = (
            "needs.eligibility.outputs.should-run != 'false' && "
            "(inputs.secretless-pr || inputs.secretless-trusted-ref) && "
            "(inputs.protected-type-surface-declaration-path != '' || "
            "inputs.secretless-compatibility-ranges != '')"
        )
        verifier_by_condition = {build[index]["if"]: index for index in verifier_indexes}
        rebuild_condition = next(
            build[index]["if"] for index in verifier_indexes
            if "inputs.secretless-rebuild-packages != ''" in build[index]["if"]
        )
        plan_condition = (
            "needs.eligibility.outputs.should-run != 'false' && "
            "(inputs.secretless-pr || inputs.secretless-trusted-ref)"
        )
        default_condition = (
            "needs.eligibility.outputs.should-run != 'false' && "
            "!(inputs.secretless-pr || inputs.secretless-trusted-ref)"
        )
        self.assertIn(rebuild_condition, verifier_by_condition)
        self.assertIn(plan_condition, verifier_by_condition)
        self.assertIn(default_condition, verifier_by_condition)
        self.assertIn(compatibility_condition, verifier_by_condition)
        self.assertEqual(guarded_routes[0], build[verifier_by_condition[rebuild_condition] + 1]["name"])
        plan_step = next(i for i, step in enumerate(build)
                         if step.get("name") == "Run exact credentialless consumer script plan")
        default_step = next(i for i, step in enumerate(build)
                            if step.get("name") == "Run default build, typecheck, test, and lint plan")
        compatibility_verifier = verifier_by_condition[compatibility_condition]
        self.assertLess(verifier_by_condition[plan_condition], plan_step)
        self.assertLess(compatibility_verifier, plan_step)
        self.assertLess(verifier_by_condition[default_condition], default_step)
        self.assertLess(verifier_by_condition[default_condition], plan_step)
        self.assertLess(max(verifier_indexes), min(plan_step, default_step))
        grouped = build[default_step]
        self.assertEqual("Run default build, typecheck, test, and lint plan", grouped["name"])
        self.assertEqual(build[verifier_by_condition[default_condition]]["if"], grouped["if"])
        self.assertEqual(
            ["npm run build", "npm run typecheck --if-present", "npm test",
             "npm run lint --if-present"],
            [line.strip() for line in grouped["run"].splitlines()
             if line.strip().startswith("npm ")],
        )
        self.assertNotIn("BASH_ENV", grouped.get("env", {}))
        self.assertLess(
            grouped["run"].index("unset -v " + " ".join(self.credential_keys)),
            grouped["run"].index("npm run build"),
        )
        compatibility_step = next(
            i for i, step in enumerate(build)
            if step.get("name") == "Run runtime-resolved compatibility lanes without credentials"
        )
        self.assertGreater(compatibility_step, max(plan_step, default_step))
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

    def test_protected_entry_receipts_bind_the_admitted_checkout_head_sha(self):
        step = self.candidate_plan_step()
        self.assertEqual(step["env"]["CI_ENTRY_COMMIT"], "${{ inputs.head-sha }}")
        checkout = next(
            step
            for step in self.workflow["jobs"]["build-test"]["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout@")
        )
        self.assertEqual(checkout["with"]["ref"], "${{ inputs.head-sha }}")


    def candidate_plan_step(self):
        step = next(
            step
            for step in self.workflow["jobs"]["build-test"]["steps"]
            if step.get("name") == "Run exact credentialless consumer script plan"
        )
        body = self.load_generator().protected_candidate_helper_body()
        fixture = "\n# BEGIN TRUSTED_HELPER_BODY_FIXTURE\n"
        fixture += "\n".join(f"#{line}" for line in body.splitlines())
        fixture += "\n# END TRUSTED_HELPER_BODY_FIXTURE\n"
        return {**step, "run": step["run"] + fixture}

    def stage_candidate_helper(
        self,
        runner_temp,
        candidate_run,
        *,
        bwrap_probe=False,
        cancel_cleanup_probe=False,
        cancel_live_candidate_probe=False,
        cancel_after_ack_probe=False,
    ):
        helper_source = TRUSTED_HELPER.read_text(encoding="utf-8")
        start = "# BEGIN TRUSTED_HELPER_BODY_FIXTURE\n"
        end = "# END TRUSTED_HELPER_BODY_FIXTURE"
        if start in candidate_run:
            if candidate_run.count(start) != 1 or candidate_run.count(end) != 1:
                self.fail("candidate helper test fixture boundaries are malformed")
            fixture = candidate_run.split(start, 1)[1].split(end, 1)[0]
            fixture_lines = fixture.splitlines()
            if any(not line.startswith("#") for line in fixture_lines):
                self.fail("candidate helper fixture contains non-comment data")
            candidate_body = "\n".join(line[1:] for line in fixture_lines)
            generator = self.load_generator()
            original_body = generator.protected_candidate_helper_body(helper_source)
            if candidate_body != original_body:
                function_marker = "def run_protected_candidate_plan() -> None:\n"
                function_start = helper_source.index(function_marker) + len(function_marker)
                function_end = helper_source.index("\ndef main(", function_start)
                helper_source = (
                    helper_source[:function_start]
                    + textwrap.indent(candidate_body, "    ")
                    + "\n\n"
                    + helper_source[function_end:]
                )
        if bwrap_probe:
            marker = "candidate_result = ci_build_artifacts.run_supervised_bubblewrap(\n"
            self.assertEqual(1, helper_source.count(marker))
            probe = r'''_d1_original_supervisor = ci_build_artifacts.run_supervised_bubblewrap
def _d1_probe_supervisor(arguments, *, environment, pass_fds, host_signals, cwd=None, test_hook=None):
    import fcntl
    import stat

    workspace_bind = ["--bind", str(workspace), str(workspace)]
    workspace_index = next(
        index
        for index in range(len(arguments))
        if arguments[index:index + len(workspace_bind)] == workspace_bind
    )
    artifact_mounts = []
    for index, argument in enumerate(arguments[:-2]):
        if argument != "--ro-bind":
            continue
        source = arguments[index + 1]
        target = Path(arguments[index + 2])
        if source.startswith("/proc/self/fd/") and target == workspace / "app.js":
            artifact_mounts.append((index, int(source.rsplit("/", 1)[1])))
    if len(artifact_mounts) != 1:
        raise AssertionError("consumer did not create exactly one read-only ArtifactSet mount")
    mount_index, descriptor = artifact_mounts[0]
    if workspace_index >= mount_index:
        raise AssertionError("workspace bind must precede ArtifactSet mounts")
    if descriptor not in pass_fds:
        raise AssertionError("ArtifactSet descriptor not retained at process launch")
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise AssertionError("ArtifactSet descriptor is not a regular file")
    if fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
        raise AssertionError("ArtifactSet descriptor is not open read-only")
    if os.read(descriptor, 4096) != b"verified ArtifactSet bytes":
        raise AssertionError("ArtifactSet descriptor did not read verified bytes")
    os.lseek(descriptor, 0, os.SEEK_SET)
    try:
        os.write(descriptor, b"x")
    except OSError:
        pass
    else:
        raise AssertionError("ArtifactSet descriptor accepted a write")
    child_check = (
        "import os,sys\n"
        "fd=int(sys.argv[1])\n"
        "data=os.read(fd,4096)\n"
        "assert data == b'verified ArtifactSet bytes', data\n"
        "try:\n os.write(fd,b'x')\n"
        "except OSError:\n pass\n"
        "else:\n raise AssertionError('inherited ArtifactSet FD is writable')\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_check, str(descriptor)],
        pass_fds=(descriptor,),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    child_stdout, child_stderr = child.communicate()
    if child.returncode != 0:
        raise AssertionError(
            f"ArtifactSet descriptor was not usable at child launch: {child_stdout}{child_stderr}"
        )
    return _d1_original_supervisor(
        arguments,
        environment=environment,
        pass_fds=pass_fds,
        host_signals=host_signals,
        cwd=cwd,
        test_hook=test_hook,
    )
ci_build_artifacts.run_supervised_bubblewrap = _d1_probe_supervisor
'''
            source_line = helper_source.index(marker)
            line_start = helper_source.rfind("\n", 0, source_line) + 1
            indent = helper_source[line_start:source_line]
            helper_source = (
                helper_source[:line_start]
                + textwrap.indent(probe, indent)
                + helper_source[line_start:]
            )

        if cancel_live_candidate_probe:
            marker = "candidate_result = ci_build_artifacts.run_supervised_bubblewrap(\n"
            self.assertEqual(1, helper_source.count(marker))
            probe = r'''_d1_original_live_supervisor = ci_build_artifacts.run_supervised_bubblewrap
def _d1_cancel_live_candidate(arguments, *, environment, pass_fds, host_signals, cwd=None, test_hook=None):
    import threading
    ready_marker = Path(environment["GITHUB_WORKSPACE"]) / ".candidate-ready"
    cancel_marker = Path(environment["GITHUB_WORKSPACE"]) / ".cancel-sent"
    watcher_stopped = threading.Event()
    signalled_at = None
    def _watch_candidate():
        nonlocal signalled_at
        deadline = time.monotonic() + 20
        while not watcher_stopped.is_set() and time.monotonic() < deadline:
            if ready_marker.is_file():
                signalled_at = time.monotonic()
                cancel_marker.touch(exist_ok=True)
                host_signals.capture(int(signal.SIGTERM))
                return
            time.sleep(0.01)
    watcher = threading.Thread(target=_watch_candidate, daemon=True)
    watcher.start()
    try:
        return _d1_original_live_supervisor(
            arguments,
            environment=environment,
            pass_fds=pass_fds,
            host_signals=host_signals,
            cwd=cwd,
            test_hook=test_hook,
        )
    finally:
        watcher_stopped.set()
        watcher.join(timeout=1)
        if watcher.is_alive():
            raise AssertionError("host-signal watcher did not stop")
        if signalled_at is not None:
            print(
                f"LIVE_CANDIDATE_CANCEL_SECONDS={time.monotonic() - signalled_at:.3f}",
                file=sys.stderr,
            )
ci_build_artifacts.run_supervised_bubblewrap = _d1_cancel_live_candidate'''
            source_line = helper_source.index(marker)
            line_start = helper_source.rfind("\n", 0, source_line) + 1
            indent = helper_source[line_start:source_line]
            helper_source = (
                helper_source[:line_start]
                + textwrap.indent(probe, indent)
                + "\n"
                + helper_source[line_start:]
            )
        if cancel_after_ack_probe:
            marker = "candidate_result = ci_build_artifacts.run_supervised_bubblewrap(\n"
            self.assertEqual(1, helper_source.count(marker))
            probe = r'''_d1_original_after_ack_supervisor = ci_build_artifacts.run_supervised_bubblewrap
def _d1_cancel_after_ack(arguments, *, environment, pass_fds, host_signals, cwd=None, test_hook=None):
    import os
    import signal
    import threading
    import time

    marker = Path(environment["GITHUB_WORKSPACE"]) / ".after-ack-ready"
    stopped = threading.Event()
    observed = threading.Event()
    shim = Path(os.environ["RUNNER_TEMP"]) / "bwrap-blocked-signal-mask"
    shim.write_text(
        "#!/usr/bin/python3" + chr(10)
        + "import os,signal,sys" + chr(10)
        + "signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGINT,signal.SIGTERM})" + chr(10)
        + "os.execvpe('/usr/bin/bwrap',['/usr/bin/bwrap',*sys.argv[1:]],os.environ)" + chr(10),
        encoding="utf-8",
    )
    shim.chmod(0o700)

    def _signal_after_ack():
        deadline = time.monotonic() + 10
        while not stopped.is_set() and time.monotonic() < deadline:
            if marker.is_file():
                observed.set()
                os.kill(os.getpid(), signal.SIGTERM)
                return
            time.sleep(0.01)

    watcher = threading.Thread(target=_signal_after_ack, daemon=True)
    watcher.start()
    arguments = [str(shim), *arguments[1:]]
    try:
        return _d1_original_after_ack_supervisor(
            arguments,
            environment=environment,
            pass_fds=pass_fds,
            host_signals=host_signals,
            cwd=cwd,
            test_hook=test_hook,
            test_after_ack_marker=str(marker),
        )
    finally:
        stopped.set()
        watcher.join(timeout=1)
        if watcher.is_alive() or not observed.is_set():
            raise AssertionError("host cancellation did not arrive after the launch ACK")
ci_build_artifacts.run_supervised_bubblewrap = _d1_cancel_after_ack
'''
            source_line = helper_source.index(marker)
            line_start = helper_source.rfind("\n", 0, source_line) + 1
            indent = helper_source[line_start:source_line]
            helper_source = (
                helper_source[:line_start]
                + textwrap.indent(probe, indent)
                + helper_source[line_start:]
            )
        if cancel_cleanup_probe:
            marker = "candidate_result = ci_build_artifacts.run_supervised_bubblewrap(\n"
            self.assertEqual(1, helper_source.count(marker))
            probe = r'''_d1_original_supervisor = ci_build_artifacts.run_supervised_bubblewrap
def _d1_cancel_cleanup(arguments, *, environment, pass_fds, host_signals, cwd=None, test_hook=None):
    def _inject_cleanup_cancellation(event):
        if test_hook is not None:
            test_hook(event)
        if event == "cleanup-start":
            host_signals.capture(int(signal.SIGTERM))

    return _d1_original_supervisor(
        arguments,
        environment=environment,
        pass_fds=pass_fds,
        host_signals=host_signals,
        cwd=cwd,
        test_hook=_inject_cleanup_cancellation,
    )
ci_build_artifacts.run_supervised_bubblewrap = _d1_cancel_cleanup
'''
            source_line = helper_source.index(marker)
            line_start = helper_source.rfind("\n", 0, source_line) + 1
            indent = helper_source[line_start:source_line]
            helper_source = (
                helper_source[:line_start]
                + textwrap.indent(probe, indent)
                + helper_source[line_start:]
            )
        helper_path = runner_temp / "ci_build_artifacts.py"
        helper_path.write_text(helper_source, encoding="utf-8")
        helper_path.chmod(0o400)
        self.assertTrue(helper_path.is_file())
        self.assertFalse(helper_path.is_symlink())
        return helper_path

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

    def script_plan_environment(self, runner_temp, plan_source, nested_source=""):
        entries, default_plan = shared_script_plan.normalize_outputs(plan_source, nested_source)
        entries_path = shared_script_plan.write_normalized_entries(entries, str(runner_temp))
        return {
            "SCRIPT_PLAN_DEFAULT_PLAN": "true" if default_plan else "false",
            "SCRIPT_PLAN_ENTRIES_FILE": entries_path,
            "NESTED_MANIFESTS": nested_source,
        }

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
        script_plan=None,
        verified_artifact=False,
        bwrap_probe=False,
        cancel_cleanup_probe=False,
        receipt_capture=None,
        plan_receipt_capture=None,
        post_run_absent=None,
        post_run_absent_delay=0,
        nested_manifests=None,
        cancel_live_candidate_probe=False,
        cancel_after_ack_probe=False,
        block_host_signal_mask=False,
    ):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup_stack:
            root = Path(directory)
            runner_temp = root / "runner-temp"
            runner_temp.mkdir()
            command_files_dir = runner_temp / "_runner_file_commands"
            command_files_dir.mkdir()
            command_file = command_files_dir / "GITHUB_ENV"
            command_file.touch()
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
                (workspace_real / ".git").mkdir()
                workspace.symlink_to(workspace_real, target_is_directory=True)
            else:
                workspace.mkdir()
                (workspace / ".git").mkdir()
            cache_setup(baseline)
            (workspace / "package.json").write_text(
                json.dumps({"scripts": {"first": "true", "second": "true", "live-cancel": "true", "self-sigint": "true", "self-sigterm": "true"}}),
                encoding="utf-8",
            )
            for manifest in nested_manifests or []:
                nested_workspace = workspace / manifest["path"]
                nested_workspace.mkdir(parents=True)
                (nested_workspace / "package.json").write_text(
                    json.dumps({"scripts": {name: "true" for name in manifest["scriptPlan"]}}),
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
            def restore_tool_tree_owner():
                if root_owned_tools or tool_root_uid is not None or foreign_entry is not None:
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

            cleanup_stack.callback(restore_tool_tree_owner)
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
            plan_value = ["first", "second"] if script_plan is None else script_plan
            nested_source = json.dumps(nested_manifests) if nested_manifests else ""
            plan_environment = self.script_plan_environment(
                runner_temp, json.dumps(plan_value), nested_source
            )
            env = {
                **os.environ,
                "PATH": path,
                "RUNNER_TEMP": str(runner_temp),
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
            "GITHUB_ENV": str(command_file),
                "GITHUB_OUTPUT": str(runner_temp / "GITHUB_OUTPUT"),
                "VERJSON_CI_OUTPUT_FILE": str(runner_temp / "GITHUB_OUTPUT"),
                **plan_environment,
                "CI_ENTRY_REPOSITORY": "Verjson/fixture",
                "CI_ENTRY_EVENT": "pull_request",
                "CI_ENTRY_REF": "refs/pull/7/merge",
                "CI_ENTRY_COMMIT": "a" * 40,
                "CI_ENTRY_WORKFLOW_SHA": "b" * 40,
                "CI_ENTRY_RUN_ID": "123",
                "CI_ENTRY_RUN_ATTEMPT": "1",
                "CI_ENTRY_JOB_ID": "build-test",
                "CI_LEVEL_ID": "test-levels",
                "CI_LEVEL_FAIL_FAST": "true",
                "CANDIDATE_CACHE_ROOT": str(runner_temp / "verjson-candidate-caches-test"),
                "npm_config_cache": str(baseline),
                "RUNNER_TOOL_CACHE": str(tool_bin.parent),
                "VERJSON_CHANGELOG_TOOL_CACHE": str(changelog_cache_root),
                "VERJSON_CHANGELOG_CONTRACT_REF": changelog_ref,
                "VERJSON_CHANGELOG_CONTRACT_SHA256": hashlib.sha256(changelog_bytes).hexdigest(),
                "COREPACK_HOME": str(root / "corepack-cache-not-present"),
                "PWD": str(workspace),
            }
            env.update(environment_updates or {})
            if verified_artifact:
                artifact_source = runner_temp / "artifact-source"
                artifact_source.mkdir()
                (artifact_source / "app.js").write_bytes(b"verified ArtifactSet bytes")
                identity = {
                    "repository": "Verjson/app",
                    "ref": "refs/heads/main",
                    "commit": "a" * 40,
                    "callerWorkflowSha": "b" * 40,
                    "reusableWorkflowSha": "c" * 40,
                    "runId": "123",
                    "runAttempt": "1",
                    "lane": "unit",
                    "profile": "d" * 64,
                    "commands": {"full": ["build"]},
                    "inputs": {"node-version": "24"},
                    "toolchain": {"node": "24.1.0"},
                }
                archive = runner_temp / "artifact-set.tar.gz"
                exported = artifact_helper.export_artifact(
                    artifact_source,
                    archive,
                    identity=identity,
                    roots=["app.js"],
                    consumers=1,
                )
                artifact_digest = artifact_helper._file_digest(archive)
                verified_root = runner_temp / "ci-build-artifact-verified"
                verified_result = artifact_helper.verify_and_extract(
                    archive,
                    verified_root,
                    expected_digest=artifact_digest,
                    expected_identity=identity,
                    artifact_id="101",
                    consumers=1,
                )
                manifest_path = runner_temp / "ci-build-artifact-manifest.json"
                manifest_path.write_text(
                    json.dumps(verified_result["manifest"], sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                manifest_path.chmod(0o400)
                env.update(
                    {
                        "BUILD_ARTIFACT_ROLE": "consumer",
                        "BUILD_ARTIFACT_ID": "101",
                        "BUILD_ARTIFACT_DIGEST": artifact_digest,
                    }
                )
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
            helper_path = self.stage_candidate_helper(
                runner_temp,
                candidate_run,
                bwrap_probe=bwrap_probe,
                cancel_cleanup_probe=cancel_cleanup_probe,
                cancel_live_candidate_probe=cancel_live_candidate_probe,
                cancel_after_ack_probe=cancel_after_ack_probe,
            )
            self.assertTrue(
                workspace.is_dir(),
                f"candidate workspace disappeared before helper invocation: {workspace}; root={list(root.iterdir())}",
            )
            command = ["/usr/bin/bash", "-c", candidate_run]
            if block_host_signal_mask:
                blocked_mask_exec = (
                    "import os,signal,sys; "
                    "signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGINT,signal.SIGTERM}); "
                    "os.execvpe(sys.argv[1],sys.argv[1:],os.environ)"
                )
                command = [sys.executable, "-c", blocked_mask_exec, *command]
            result = subprocess.run(
                command,
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
            )
            if post_run_absent is not None:
                absent_markers = (
                    (post_run_absent,)
                    if isinstance(post_run_absent, str)
                    else post_run_absent
                )
                for marker in absent_markers:
                    self.assertFalse(
                        (workspace / marker).exists(),
                    f"candidate wrote forbidden marker {marker}; stdout={result.stdout!r}; stderr={result.stderr!r}",
                    )
            if post_run_absent_delay:
                if not isinstance(post_run_absent_delay, (int, float)) or not 0 < post_run_absent_delay <= 3.0:
                    raise AssertionError("delayed marker verification exceeded its bound")
                deadline = time.monotonic() + post_run_absent_delay
                while time.monotonic() < deadline:
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                for marker in absent_markers:
                    self.assertFalse(
                        (workspace / marker).exists(),
                        f"candidate wrote delayed forbidden marker {marker}",
                    )
            if receipt_capture is not None or plan_receipt_capture is not None:
                receipt_directories = list(runner_temp.glob(".verjson-node-ci-entry-receipts-*"))
                self.assertEqual(len(receipt_directories), 1, result.stderr)
                if receipt_capture is not None:
                    receipt_records = sorted(receipt_directories[0].glob("entry-*.json"))
                    Path(receipt_capture).write_text(
                        json.dumps(
                            [json.loads(record.read_text(encoding="utf-8")) for record in receipt_records]
                        ),
                        encoding="utf-8",
                    )
                if plan_receipt_capture is not None:
                    plan_receipt = receipt_directories[0] / "plan.json"
                    self.assertTrue(plan_receipt.is_file(), result.stderr)
                    Path(plan_receipt_capture).write_bytes(plan_receipt.read_bytes())
            self.assertNotIn(f"can't open file '{helper_path}'", result.stderr)
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
            ("service-variable-hidden", {"DB_PORT": "5432"}, False),
        )
        for name, updates, nested_workspace in cases:
            if updates.get("CANDIDATE_CACHE_ROOT") == "BASELINE":
                with tempfile.TemporaryDirectory() as directory:
                    runner_temp = Path(directory) / "runner-temp"
                    runner_temp.mkdir()
                    command_files_dir = runner_temp / "_runner_file_commands"
                    command_files_dir.mkdir()
                    command_file = command_files_dir / "GITHUB_ENV"
                    command_file.touch()
                    baseline = runner_temp / "baseline"
                    baseline.mkdir()
                    changelog_cache_root, changelog_ref, changelog_bytes = (
                        self.create_changelog_cache_fixture(runner_temp)
                    )
                    (baseline / "blob").write_text("verified", encoding="utf-8")
                    workspace = Path(directory) / "workspace"
                    workspace.mkdir()
                    (workspace / ".git").mkdir()
                    (workspace / "package.json").write_text(
                        json.dumps({"scripts": {"first": "true"}}), encoding="utf-8"
                    )
                    run = self.candidate_plan_step()["run"]
                    self.stage_candidate_helper(runner_temp, run)
                    result = subprocess.run(
                        ["/usr/bin/bash", "-c", run],
                        cwd=workspace,
                        env={
                            **os.environ,
                            "RUNNER_TEMP": str(runner_temp),
                            "RUNNER_ENVIRONMENT": "github-hosted",
                            "RUNNER_OS": "Linux",
                            "GITHUB_ENV": str(command_file),
                            "GITHUB_OUTPUT": str(runner_temp / "GITHUB_OUTPUT"),
                        "VERJSON_CI_OUTPUT_FILE": str(runner_temp / "GITHUB_OUTPUT"),
                        "CI_ENTRY_REPOSITORY": "Verjson/fixture",
                        "CI_ENTRY_EVENT": "pull_request",
                        "CI_ENTRY_REF": "refs/pull/7/merge",
                        "CI_ENTRY_COMMIT": "a" * 40,
                        "CI_ENTRY_WORKFLOW_SHA": "b" * 40,
                        "CI_ENTRY_RUN_ID": "123",
                        "CI_ENTRY_RUN_ATTEMPT": "1",
                        "CI_ENTRY_JOB_ID": "build-test",
                        "CI_LEVEL_ID": "test-levels",
                        "CI_LEVEL_FAIL_FAST": "true",
                        "PWD": str(workspace),
                            **self.script_plan_environment(runner_temp, '["first"]'),
                            "CANDIDATE_CACHE_ROOT": str(baseline),
                            "npm_config_cache": str(baseline),
                            "VERJSON_CHANGELOG_TOOL_CACHE": str(changelog_cache_root),
                            "VERJSON_CHANGELOG_CONTRACT_REF": changelog_ref,
                            "VERJSON_CHANGELOG_CONTRACT_SHA256": hashlib.sha256(changelog_bytes).hexdigest(),
                            "COREPACK_HOME": str(Path(directory) / "corepack-cache-not-present"),
                        },
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("candidate cache root exists before script execution", result.stderr)
                continue
            npm_body = (
                'test -z "${DB_PORT+x}"\n'
                if name == "service-variable-hidden"
                else "exit 0\n"
            )
            result, remaining = self.run_candidate_plan(
                cache_setup,
                npm_body,
                environment_updates=updates,
                workspace_in_runner_temp=nested_workspace,
            )
            with self.subTest(case=name):
                if name == "service-variable-hidden":
                    self.assertEqual(0, result.returncode, result.stderr)
                else:
                    self.assertNotEqual(0, result.returncode)
                self.assertEqual([], remaining)

    def test_service_enabled_candidate_script_gets_only_validated_service_env_without_network_unshare(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text(
                "verified", encoding="utf-8"
            ),
            'test "$DB_HOST" = "172.18.0.2"\n'
            'test "$DB_PORT" = "5432"\n'
            'test "$DATABASE_URL" = "postgres://app:secret@172.18.0.2:5432/app_test"\n'
            'test "$OPENAI_API_KEY" = "ci-dummy-key"\n'
            'test -z "${POSTGRES_PASSWORD+x}"\n'
            'printf "service variables reached candidate npm script\\n"\n',
            environment_updates={
                "RUNNER_ENVIRONMENT": "github-hosted",
                "DB_HOST": "172.18.0.2",
                "DB_PORT": "5432",
                "VERJSON_CI_TRUSTED_DB_HOST": "172.18.0.2",
                "DB_ENV": (
                    "DATABASE_URL=postgres://app:secret@172.18.0.2:5432/app_test\n"
                    "OPENAI_API_KEY=ci-dummy-key\n"
                    "POSTGRES_PASSWORD=postgres"
                ),
                "DATABASE_URL": "postgres://app:secret@172.18.0.2:5432/app_test",
                "OPENAI_API_KEY": "ci-dummy-key",
                "POSTGRES_PASSWORD": "postgres",
            },
            script_plan=[{"script": "first", "requiresServices": True}],
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("service variables reached candidate npm script", result.stderr)
        plan_run = self.candidate_plan_step()["run"]
        self.assertIn("ci_build_artifacts.bubblewrap_network_args(", plan_run)
        self.assertIn("legacy_unshare=not requires_services", plan_run)
        self.assertIn("*legacy_network_args,", plan_run)
        self.assertIn(
            "if any(requires_services for _directory, _name, _unset_env, requires_services in normalized):",
            plan_run,
        )
        self.assertIn('os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"', plan_run)
        self.assertEqual([], remaining)

    def test_candidate_script_can_execute_alternatives_managed_tools(self):
        # #1782: /usr/bin/awk is a link into /etc/alternatives on Debian and Ubuntu.
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "test \"$(awk 'BEGIN { print \"awk-ok\" }')\" = awk-ok\n"
            'printf "alternatives-managed awk ran in the candidate sandbox\\n"\n',
            script_plan=["first"],
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("alternatives-managed awk ran in the candidate sandbox", result.stderr)
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
        self.assertIn("trusted npm CLI fixture invoked", result.stderr)
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
        self.assertIn("trusted npm CLI fixture invoked", result.stderr)
        self.assertNotIn("decoy npm CLI", result.stderr)
        self.assertEqual([], remaining)

    def test_identical_npm_cli_candidates_from_symlink_layout_are_deduplicated(self):
        result, remaining = self.run_candidate_plan(
            lambda baseline: (baseline / "blob").write_text("verified", encoding="utf-8"),
            "exit 91\n",
            npm_cli_layout=True,
            symlink_npm_cli_layout=True,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("trusted npm CLI fixture invoked", result.stderr)
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
[ -z "${CANDIDATE_CACHE_ROOT+x}" ]
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
        plan_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Run exact credentialless consumer script plan"
        )
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
            command_files_dir = runner_temp / "_runner_file_commands"
            command_files_dir.mkdir()
            command_file = command_files_dir / "GITHUB_ENV"
            command_file.touch()
            baseline = runner_temp / "baseline"
            baseline.mkdir()
            changelog_cache_root, changelog_ref, changelog_bytes = (
                self.create_changelog_cache_fixture(runner_temp)
            )
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".git").mkdir()
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
            candidate_run = self.candidate_plan_step()["run"]
            helper_path = self.stage_candidate_helper(runner_temp, candidate_run)
            process = subprocess.Popen(
                ["/usr/bin/bash", "-c", candidate_run],
                cwd=workspace,
                env={
                    **os.environ,
                    "PATH": f"{tool_bin}:{os.environ['PATH']}",
                    "RUNNER_TEMP": str(runner_temp),
                    "RUNNER_ENVIRONMENT": "github-hosted",
                    "RUNNER_OS": "Linux",
                    "GITHUB_ENV": str(command_file),
                    "GITHUB_OUTPUT": str(runner_temp / "GITHUB_OUTPUT"),
                    "VERJSON_CI_OUTPUT_FILE": str(runner_temp / "GITHUB_OUTPUT"),
                    "CI_ENTRY_REPOSITORY": "Verjson/fixture",
                    "CI_ENTRY_EVENT": "pull_request",
                    "CI_ENTRY_REF": "refs/pull/7/merge",
                    "CI_ENTRY_COMMIT": "a" * 40,
                    "CI_ENTRY_WORKFLOW_SHA": "b" * 40,
                    "CI_ENTRY_RUN_ID": "123",
                    "CI_ENTRY_RUN_ATTEMPT": "1",
                    "CI_ENTRY_JOB_ID": "build-test",
                    "CI_LEVEL_ID": "test-levels",
                    "CI_LEVEL_FAIL_FAST": "true",
                    "PWD": str(workspace),
                    **self.script_plan_environment(runner_temp, '["first"]'),
                    "CANDIDATE_CACHE_ROOT": str(runner_temp / "verjson-candidate-caches-test"),
                    "npm_config_cache": str(baseline),
                    "RUNNER_TOOL_CACHE": str(tool_bin.parent),
                    "VERJSON_CHANGELOG_TOOL_CACHE": str(changelog_cache_root),
                    "VERJSON_CHANGELOG_CONTRACT_REF": changelog_ref,
                    "VERJSON_CHANGELOG_CONTRACT_SHA256": hashlib.sha256(changelog_bytes).hexdigest(),
                    "COREPACK_HOME": str(root / "corepack-cache-not-present"),
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
