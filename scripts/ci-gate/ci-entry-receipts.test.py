#!/usr/bin/env python3
"""Adversarial coverage for typed-level fail-fast receipts."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".github/actions/node-ci-script-plan"))

import ci_build_artifacts as artifacts
import ci_levels


IDENTITY = {
    "repository": "Verjson/fixture",
    "event": "pull_request",
    "ref": "refs/pull/7/merge",
    "commit": "a" * 40,
    "workflowSha": "b" * 40,
    "runId": "42",
    "runAttempt": "2",
    "jobId": "build-test",
    "levelId": "test-levels",
}
_OMITTED = object()
ENTRIES = [
    {"directory": "src", "script": name, "unsetEnv": [], "requiresServices": False}
    for name in ("first", "failure", "diagnostic")
]


def load_receipts(directory: Path) -> list[dict[str, object]]:
    return [
        json.loads((directory / f"entry-{ordinal:06d}.json").read_text(encoding="utf-8"))
        for ordinal in range(len(ENTRIES))
    ]


class LevelFailFastParsingTests(unittest.TestCase):
    def parse(self, fail_fast: object = _OMITTED) -> ci_levels.Plan:
        level: dict[str, object] = {"id": "unit"}
        if fail_fast is not _OMITTED:
            level["failFast"] = fail_fast
        text = json.dumps({"version": 1, "levels": [level]})
        return ci_levels.parse_levels(text, {"timeout-minutes": "integer"}, frozenset())

    def test_omission_defaults_true_and_generated_caller_binds_level_identity(self) -> None:
        plan = self.parse()
        self.assertIs(plan.levels[0].fail_fast, True)
        caller = yaml.safe_load(ci_levels.render_caller(plan, "c" * 40, " --levels ci-levels.json"))
        self.assertEqual(caller["jobs"]["unit"]["with"]["ci-level-id"], "unit")
        self.assertIs(caller["jobs"]["unit"]["with"]["ci-level-fail-fast"], True)

    def test_false_is_carried_in_immutable_level_and_authorization_plan(self) -> None:
        plan = self.parse(False)
        self.assertIs(plan.levels[0].fail_fast, False)
        authorization_plan = ci_levels.Plan(
            levels=(
                plan.levels[0],
                ci_levels.Level("consumer", (), None, None, (), True),
            ),
            secrets=("NODE_AUTH_TOKEN",),
            push_branches=("main",),
            explicit=True,
            build_artifact=ci_levels.BuildArtifact("unit", ("consumer",), ("dist",)),
        )
        self.assertIn('"failFast":false', ci_levels._artifact_plan_json(authorization_plan))
        caller = yaml.safe_load(
            ci_levels.render_caller(plan, "c" * 40, " --levels ci-levels.json")
        )
        self.assertIs(caller["jobs"]["unit"]["with"]["ci-level-fail-fast"], False)

    def test_fail_fast_is_strict_boolean_and_internal_inputs_cannot_be_overridden(self) -> None:
        for invalid in ("false", 0, 1, None, []):
            with self.subTest(invalid=invalid), self.assertRaises(ci_levels.LevelsError):
                self.parse(invalid)
        level = {"id": "unit", "inputs": {"ci-level-fail-fast": False}}
        with self.assertRaises(ci_levels.LevelsError):
            ci_levels.parse_levels(
                json.dumps({"version": 1, "levels": [level]}),
                {"timeout-minutes": "integer"},
                frozenset(),
            )


class EntryReceiptSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runner_temp = self.root / "runner-temp"
        self.runner_temp.mkdir(mode=0o700)

    def run_plan(self, fail_fast: bool, outcomes: dict[str, int] | None = None):
        calls: list[str] = []
        statuses = outcomes or {"first": 0, "failure": 9, "diagnostic": 0}

        def execute(_ordinal: int, entry: dict[str, object]):
            name = str(entry["script"])
            calls.append(name)
            return artifacts.ScriptEntryExecutionResult(statuses[name])

        result = artifacts.run_script_plan_with_receipts(
            ENTRIES, IDENTITY, fail_fast, self.runner_temp, execute
        )
        return result, calls, load_receipts(result.receipt_directory)

    def test_false_continues_in_order_and_latches_failure_against_independent_oracle(self) -> None:
        result, calls, receipts = self.run_plan(False)
        oracle = [(name, code) for name, code in (("first", 0), ("failure", 9), ("diagnostic", 0))]
        self.assertEqual(calls, [name for name, _code in oracle])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(
            [(item["entry"]["script"], item["exitCode"]) for item in receipts],
            oracle,
        )
        self.assertTrue(all(item["state"] == "completed" for item in receipts))
        self.assertEqual(receipts[0]["identity"]["jobId"], IDENTITY["jobId"])
        self.assertEqual(receipts[0]["identity"]["levelId"], IDENTITY["levelId"])
        self.assertEqual(len({item["planDigest"] for item in receipts}), 1)

    def test_true_stops_after_first_nonzero_and_emits_null_status_for_unstarted_entry(self) -> None:
        result, calls, receipts = self.run_plan(True)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(calls, ["first", "failure"])
        self.assertEqual(receipts[-1]["state"], "not-run-fail-fast")
        self.assertIsNone(receipts[-1]["exitCode"])
        self.assertIsNone(receipts[-1]["signal"])
        self.assertEqual(receipts[-1]["cleanup"], "not-started")

    def test_cancellation_is_terminal_and_aborts_later_entries_without_fabricating_exit(self) -> None:
        calls: list[str] = []

        def execute(_ordinal: int, entry: dict[str, object]):
            calls.append(str(entry["script"]))
            interrupted = artifacts.ScriptPlanInterrupted(int(signal.SIGTERM))
            interrupted.cleanup_status = "verified"
            raise interrupted

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )
        self.assertEqual(calls, ["first"])
        receipts = load_receipts(caught.exception.receipt_directory)
        self.assertEqual(receipts[0]["state"], "infrastructure-failure")
        self.assertIsNone(receipts[0]["exitCode"])
        self.assertEqual(receipts[0]["signal"], signal.SIGTERM)
        self.assertEqual(receipts[0]["cleanup"], "verified")
        self.assertEqual([entry["state"] for entry in receipts[1:]], ["not-run-aborted"] * 2)

    def test_terminal_status_failure_can_record_verified_cleanup_without_fabricated_status(self) -> None:
        calls: list[str] = []

        def execute(_ordinal: int, entry: dict[str, object]):
            calls.append(str(entry["script"]))
            error = artifacts.IntegrityError("Bubblewrap status receipt is incomplete")
            error.cleanup_status = "verified"
            raise error

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )
        receipts = load_receipts(caught.exception.receipt_directory)
        self.assertEqual(calls, ["first"])
        self.assertEqual(receipts[0]["state"], "infrastructure-failure")
        self.assertIsNone(receipts[0]["exitCode"])
        self.assertIsNone(receipts[0]["signal"])
        self.assertEqual(receipts[0]["cleanup"], "verified")
        self.assertEqual([entry["state"] for entry in receipts[1:]], ["not-run-aborted"] * 2)


    def test_launch_or_supervisor_failure_is_terminal_and_records_abort(self) -> None:
        def execute(_ordinal: int, _entry: dict[str, object]):
            error = OSError("sandbox launch failed")
            error.cleanup_status = "not-started"
            raise error

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )
        receipts = load_receipts(caught.exception.receipt_directory)
        self.assertEqual(receipts[0]["state"], "infrastructure-failure")
        self.assertEqual(receipts[0]["cleanup"], "not-started")
        self.assertEqual([entry["state"] for entry in receipts[1:]], ["not-run-aborted"] * 2)

    def test_cancellation_during_receipt_validation_and_acceptance_invalidates_plan(self) -> None:
        injection_points = (
            "entry-receipt-write",
            "entry-receipt-finalize",
            "validation",
            "validation-entry",
            "plan-receipt-write",
            "plan-receipt-finalize",
            "before-acceptance",
        )
        for point in injection_points:
            with self.subTest(point=point):
                cancellation = {"signal": None}
                previous_handler = signal.getsignal(signal.SIGTERM)
                signal.signal(
                    signal.SIGTERM,
                    lambda signum, _frame: cancellation.__setitem__("signal", signum),
                )
                calls: list[str] = []
                injected = False

                def inject(name: str) -> None:
                    nonlocal injected
                    if name == point and not injected:
                        injected = True
                        os.kill(os.getpid(), signal.SIGTERM)

                def execute(_ordinal: int, entry: dict[str, object]):
                    calls.append(str(entry["script"]))
                    return artifacts.ScriptEntryExecutionResult(0)

                try:
                    with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                        artifacts.run_script_plan_with_receipts(
                            ENTRIES,
                            IDENTITY,
                            False,
                            self.runner_temp,
                            execute,
                            cancellation_signal=lambda: cancellation["signal"],
                            test_hook=inject,
                        )
                finally:
                    signal.signal(signal.SIGTERM, previous_handler)

                self.assertTrue(injected)
                receipt_directory = caught.exception.receipt_directory
                plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
                receipts = load_receipts(receipt_directory)
                self.assertEqual(plan["state"], "interrupted")
                self.assertEqual(plan["signal"], signal.SIGTERM)
                if point.startswith("entry-receipt"):
                    self.assertEqual(calls, ["first"])
                    self.assertEqual(
                        [receipt["state"] for receipt in receipts],
                        ["completed", "not-run-aborted", "not-run-aborted"],
                    )
                else:
                    self.assertEqual(len(calls), len(ENTRIES))
                    self.assertEqual([receipt["state"] for receipt in receipts], ["completed"] * len(ENTRIES))

    def test_workspace_and_stdout_spoofing_do_not_change_supervisor_receipts(self) -> None:
        workspace = self.root / "workspace"
        workspace.mkdir()
        calls = 0

        def execute(_ordinal: int, _entry: dict[str, object]):
            nonlocal calls
            calls += 1
            (workspace / "entry-000000.json").write_text(
                json.dumps({"state": "completed", "exitCode": 0}), encoding="utf-8"
            )
            with redirect_stdout(StringIO()):
                print(json.dumps({"state": "completed", "exitCode": 0}))
            return artifacts.ScriptEntryExecutionResult(13)

        result = artifacts.run_script_plan_with_receipts(
            ENTRIES[:1], IDENTITY, False, self.runner_temp, execute
        )
        receipt = json.loads(
            (result.receipt_directory / "entry-000000.json").read_text(encoding="utf-8")
        )
        self.assertEqual(calls, 1)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(receipt["exitCode"], 13)
        self.assertNotEqual(receipt["entry"], {"state": "completed", "exitCode": 0})

    def test_root_workspace_entry_uses_dot_as_the_canonical_directory(self) -> None:
        entry = {"directory": ".", "script": "build", "unsetEnv": [], "requiresServices": False}
        result = artifacts.run_script_plan_with_receipts(
            [entry],
            IDENTITY,
            True,
            self.runner_temp,
            lambda _ordinal, _entry: artifacts.ScriptEntryExecutionResult(0),
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            artifacts.validate_script_entry_receipts(
                result.receipt_directory, [entry], IDENTITY, True
            )[0]["entry"],
            entry,
        )
        for directory in ("../outside", "./src", "src/../outside", "/absolute"):
            invalid_entry = {**entry, "directory": directory}
            with self.subTest(directory=directory), self.assertRaises(artifacts.IntegrityError):
                artifacts.run_script_plan_with_receipts(
                    [invalid_entry],
                    IDENTITY,
                    True,
                    self.runner_temp,
                    lambda _ordinal, _entry: artifacts.ScriptEntryExecutionResult(0),
                )

    def test_coverage_duplicate_keys_identity_and_contradictory_outcomes_fail_closed(self) -> None:
        result, _calls, receipts = self.run_plan(False)
        directory = result.receipt_directory

        (directory / "entry-000002.json").unlink()
        with self.assertRaises(artifacts.IntegrityError):
            artifacts.validate_script_entry_receipts(directory, ENTRIES, IDENTITY, False)

        # Restore the missing record from a fresh, independently supervised run.
        result, _calls, receipts = self.run_plan(False)
        directory = result.receipt_directory
        with (directory / "entry-000001.json").open("w", encoding="utf-8") as handle:
            handle.write('{"version":1,"version":1}')
        with self.assertRaisesRegex(artifacts.IntegrityError, "duplicate JSON key"):
            artifacts.validate_script_entry_receipts(directory, ENTRIES, IDENTITY, False)

        result, _calls, receipts = self.run_plan(False)
        directory = result.receipt_directory
        altered = dict(receipts[0])
        altered["exitCode"] = 0
        altered["signal"] = signal.SIGTERM
        (directory / "entry-000000.json").write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaises(artifacts.IntegrityError):
            artifacts.validate_script_entry_receipts(directory, ENTRIES, IDENTITY, False)

    def test_host_supervisor_extinguishes_descendant_process_group(self) -> None:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import subprocess,sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);",
            ],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(process.wait(timeout=5), 0)
        artifacts.extinguish_supervised_process_group(process)
        with self.assertRaises(ProcessLookupError):
            os.killpg(process.pid, 0)


class BubblewrapJsonStatusTests(unittest.TestCase):
    def test_valid_child_and_exit_events_match_the_supervised_status(self) -> None:
        data = b'{"child-pid":123}\n{"exit-code":23}\n'
        self.assertEqual(23, artifacts.parse_bubblewrap_json_status(data, 23))

    def test_unknown_objects_and_members_are_ignored(self) -> None:
        data = (
            b'{"future-before":{"enabled":true}}\n'
            b'{"child-pid":123,"namespace-pid":7}\n'
            b'{"future-after":null}\n'
            b'{"exit-code":23,"namespace-status":"ok"}\n'
        )
        self.assertEqual(23, artifacts.parse_bubblewrap_json_status(data, 23))

    def test_duplicate_reversed_combined_and_contradictory_events_fail_closed(self) -> None:
        invalid_receipts = (
            b'{"child-pid":123}\n{"child-pid":123}\n{"exit-code":0}\n',
            b'{"exit-code":0}\n{"child-pid":123}\n',
            b'{"child-pid":123,"exit-code":0}\n',
            b'{"child-pid":123}\n{"exit-code":0}\n{"exit-code":0}\n',
            b'{"child-pid":123}\n{"exit-code":1}\n',
            b'{"child-pid":123}\n{"exit-code":-1}\n',
            b'{"child-pid":true}\n{"exit-code":0}\n',
            b'{"child-pid":123}\n{"exit-code":0.0}\n',
            b'{"child-pid":123}\n{"future":NaN}\n{"exit-code":0}\n',
            b'{"child-pid":123}\n{"future":Infinity}\n{"exit-code":0}\n',
            b'{"child-pid":123}\n\n{"exit-code":0}\n',
        )
        for receipt in invalid_receipts:
            with self.subTest(receipt=receipt), self.assertRaises(artifacts.IntegrityError):
                artifacts.parse_bubblewrap_json_status(receipt, 0)

    def test_record_and_line_bounds_are_enforced(self) -> None:
        oversized_line = b'{"future":"' + b"x" * artifacts.MAX_BWRAP_STATUS_LINE_BYTES + b'"}\n'
        too_many_records = b''.join(b'{"future":true}\n' for _ in range(artifacts.MAX_BWRAP_STATUS_RECORDS + 1))
        for receipt in (oversized_line, too_many_records):
            with self.subTest(size=len(receipt)), self.assertRaises(artifacts.IntegrityError):
                artifacts.parse_bubblewrap_json_status(receipt, 0)

    def test_missing_startup_and_mismatched_status_fail_closed(self) -> None:
        invalid_receipts = (
            b"",
            b'{"child-pid":123}\n',
            b'{"child-pid":123}\n{"exit-code":0}\n',
            b'{"child-pid":123}\n{"exit-code":23}\n',
        )
        for receipt in invalid_receipts:
            with self.subTest(receipt=receipt), self.assertRaises(artifacts.IntegrityError):
                artifacts.parse_bubblewrap_json_status(receipt, 1 if receipt != b"" else 0)

    def test_duplicate_json_keys_and_ambiguous_signal_exit_codes_fail_closed(self) -> None:
        with self.assertRaises(artifacts.IntegrityError):
            artifacts.parse_bubblewrap_json_status(
                b'{"child-pid":123,"child-pid":124}\n{"exit-code":0}\n', 0
            )
        with self.assertRaises(artifacts.IntegrityError):
            artifacts.parse_bubblewrap_json_status(
                b'{"child-pid":123}\n{"exit-code":143}\n', 143
            )


class TrustedCandidateStatusTests(unittest.TestCase):
    @staticmethod
    def record(**updates: object) -> bytes:
        record: dict[str, object] = {
            "version": 1,
            "pid": 1,
            "state": "candidate-exit",
            "exitCode": 23,
            "signal": None,
            "cleanup": "complete",
            "reaped": 2,
        }
        record.update(updates)
        return (
            b'{"version":1,"pid":1,"event":"cleanup-start"}\n'
            + b'{"version":1,"pid":1,"event":"cleanup-complete"}\n'
            + json.dumps(record, separators=(",", ":")).encode("ascii")
            + b"\n"
        )

    def test_trusted_wrapper_accepts_only_the_complete_ordered_status(self) -> None:
        status = artifacts._parse_trusted_candidate_status(self.record(), 23)
        self.assertEqual(status.state, "candidate-exit")
        self.assertEqual(status.return_code, 23)
        self.assertEqual(status.cleanup, "complete")
        self.assertEqual(status.reaped_count, 2)

    def test_trusted_wrapper_rejects_missing_duplicate_reversed_or_malformed_evidence(self) -> None:
        valid = self.record()
        start, complete, final = valid.splitlines(keepends=True)
        invalid = (
            b"",
            valid[:-1],
            start + start + complete + final,
            complete + start + final,
            start + final,
            start + complete + complete + final,
            start + b'{"version":1,"pid":1,"event":"unknown"}\n' + complete + final,
            valid.replace(b'"reaped":2', b'"reaped":true'),
            valid.replace(b'"pid":1,"state"', b'"pid":2,"state"'),
            valid.replace(b'"exitCode":23', b'"exitCode":22'),
            valid.replace(b'"state":"candidate-exit"', b'"state":"unknown"'),
            valid.replace(b'"version":1,"pid":1,"state"', b'"version":1,"version":1,"pid":1,"state"'),
            valid.replace(b'"cleanup":"complete"', b'"cleanup":"failed"'),
        )
        for receipt in invalid:
            with self.subTest(receipt=receipt), self.assertRaises(artifacts.IntegrityError):
                artifacts._parse_trusted_candidate_status(receipt, 23)

    def test_pid1_supervisor_reaps_an_adopted_zombie_before_returning(self) -> None:
        bubblewrap = Path("/usr/bin/bwrap")
        self.assertTrue(bubblewrap.is_file(), "Bubblewrap is required for the registered platform suite")
        candidate = "import os,time\nchild=os.fork()\nif child == 0:\n os._exit(0)\ntime.sleep(0.15)\n"
        arguments = [
            str(bubblewrap),
            "--unshare-user",
            "--unshare-pid",
            "--as-pid-1",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-net",
            "--cap-drop",
            "ALL",
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/lib64",
            "/lib64",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--chdir",
            "/",
            "--",
            "/usr/bin/python3",
            "-I",
            "-c",
            artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT,
            "/usr/bin/python3",
            "-I",
            "-c",
            candidate,
        ]
        status = artifacts.run_supervised_bubblewrap(
            arguments,
            environment={"PATH": "/usr/bin:/bin"},
            pass_fds=(),
            cancellation_signal=lambda: None,
        )
        self.assertEqual(status.state, "candidate-exit")
        self.assertEqual(status.return_code, 0)
        self.assertEqual(status.cleanup, "complete")
        self.assertGreaterEqual(status.reaped_count, 2)


class ExecutorParityTests(unittest.TestCase):
    def test_source_and_protected_executors_use_the_pid1_receipt_supervisor(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/node-ci.yml").read_text())
        step = next(
            item for item in workflow["jobs"]["build-test"]["steps"]
            if item.get("id") == "run-script-plan"
        )
        source = step["run"]
        protected = (ROOT / "scripts/ci_build_artifacts.py").read_text(encoding="utf-8")
        self.assertIn("run_supervised_bubblewrap(", source)
        self.assertIn('"--as-pid-1"', source)
        self.assertIn("run_script_plan_with_receipts(", source)
        self.assertIn("run_supervised_bubblewrap(", protected)
        self.assertIn("run_script_plan_with_receipts(", protected)
        self.assertIn("subprocess.Popen(", protected)
        self.assertIn("close_fds=True", protected)
        self.assertIn("libc.prctl(4, 0, 0, 0, 0)", protected)
        self.assertIn("candidate.wait(timeout=0.05)", protected)
        self.assertIn("os.waitpid(-1, os.WNOHANG)", protected)


if __name__ == "__main__":
    unittest.main()
