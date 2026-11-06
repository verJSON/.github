#!/usr/bin/env python3
"""Adversarial coverage for typed-level fail-fast receipts."""
from __future__ import annotations

import errno
import inspect
import ctypes
from dataclasses import replace
import json
import os
import queue
import select
import signal
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts/ci-gate"))
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


class HostSignalQueueTests(unittest.TestCase):
    def test_custom_queue_without_signal_latch_is_rejected_before_launch(self) -> None:
        class MissingLatch:
            def poll(self):
                return None

            def launch_decision(self):
                return None

            def acceptance_cutoff(self):
                return None

        with (
            patch.object(artifacts.subprocess, "Popen", side_effect=AssertionError("unexpected launch")) as launch,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, 'host cancellation queue is invalid'):
                artifacts.run_supervised_bubblewrap(['/usr/bin/true'], environment={}, pass_fds=(),
                                                    host_signals=MissingLatch())
        launch.assert_not_called()
        kill_group.assert_not_called()

    def test_invalid_latched_signal_is_rejected_before_launch(self) -> None:
        for value in (..., False, 0, "15", MagicMock()):
            with self.subTest(value=value):
                source = MagicMock()
                source.host_cancel_signal = value
                with (
                    patch.object(artifacts.subprocess, "Popen") as launch,
                    patch.object(artifacts.os, "killpg") as kill_group,
                ):
                    with self.assertRaisesRegex(artifacts.IntegrityError, "host cancellation queue is invalid"):
                        artifacts.run_supervised_bubblewrap(
                            ["/usr/bin/true"], environment={}, pass_fds=(), host_signals=source,
                        )
                launch.assert_not_called()
                kill_group.assert_not_called()

    def test_pending_termination_is_captured_after_handler_install_before_unblock(self) -> None:
        watched = {signal.SIGINT, signal.SIGTERM}
        host_signals = artifacts.HostSignalQueue()
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, watched)
        previous_handler = signal.getsignal(signal.SIGTERM)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            signal.signal(signal.SIGTERM, lambda signum, _frame: host_signals.capture(signum))
            signal.pthread_sigmask(signal.SIG_UNBLOCK, watched)
            self.assertEqual(host_signals.poll(), signal.SIGTERM)
        finally:
            signal.signal(signal.SIGTERM, previous_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    def test_launch_decision_observes_a_latched_signal_with_an_empty_queue(self) -> None:
        signals = artifacts.HostSignalQueue()
        signals.capture(signal.SIGTERM)

        self.assertEqual(signals.poll(), signal.SIGTERM)
        self.assertEqual(signals.launch_decision(), signal.SIGTERM)
        self.assertEqual(signals.acceptance_cutoff(), signal.SIGTERM)

    def test_launch_decision_reads_once_and_freezes_the_first_queued_signal(self) -> None:
        signals = artifacts.HostSignalQueue()
        signals.capture(signal.SIGINT)
        signals.capture(signal.SIGTERM)

        self.assertEqual(signals.launch_decision(), signal.SIGINT)
        self.assertEqual(signals._signals.get_nowait(), signal.SIGTERM)
        self.assertEqual(signals.launch_decision(), signal.SIGINT)

    def test_poll_latches_a_signal_arriving_after_the_initial_empty_read(self) -> None:
        class InterleavingQueue(queue.SimpleQueue[int]):
            def __init__(self) -> None:
                super().__init__()
                self.inserted = False

            def get_nowait(self) -> int:
                if not self.inserted:
                    self.inserted = True
                    self.put(int(signal.SIGTERM))
                    raise queue.Empty
                return super().get_nowait()

        signals = artifacts.HostSignalQueue()
        signals._signals = InterleavingQueue()

        self.assertEqual(signals.poll(), signal.SIGTERM)
        self.assertEqual(signals.host_cancel_signal, signal.SIGTERM)
        with self.assertRaises(queue.Empty):
            signals._signals.get_nowait()

    def test_final_cutoff_ignores_signals_enqueued_after_the_cutoff(self) -> None:
        signals = artifacts.HostSignalQueue()

        self.assertIsNone(signals.acceptance_cutoff())
        signals.capture(signal.SIGTERM)

        self.assertIsNone(signals.poll())

    def test_acceptance_cutoff_latches_preexisting_signal_and_ignores_later_capture(self) -> None:
        signals = artifacts.HostSignalQueue()
        signals.capture(signal.SIGINT)

        self.assertEqual(signals.acceptance_cutoff(), signal.SIGINT)
        signals.capture(signal.SIGTERM)
        self.assertEqual(signals.poll(), signal.SIGINT)

    def test_acceptance_cutoff_closes_before_draining_the_old_read_close_gap(self) -> None:
        signals = artifacts.HostSignalQueue()

        class CaptureAfterEmptyRead(queue.SimpleQueue[int]):
            injected = False

            def get_nowait(self) -> int:
                try:
                    return super().get_nowait()
                except queue.Empty:
                    if not self.injected:
                        self.injected = True
                        signals.capture(signal.SIGTERM)
                    raise

        signals._signals = CaptureAfterEmptyRead()

        self.assertIsNone(signals.acceptance_cutoff())
        self.assertTrue(signals._acceptance_closed)
        self.assertIsNone(signals.host_cancel_signal)
        with self.assertRaises(queue.Empty):
            signals._signals.get_nowait()
        self.assertIsNone(signals.poll())

    def test_signal_handler_capture_rejects_invalid_values_only_when_drained(self) -> None:
        signals = artifacts.HostSignalQueue()
        signals.capture(1)

        with self.assertRaises(artifacts.IntegrityError):
            signals.poll()


class InnerSupervisorPinTests(unittest.TestCase):
    def test_wrapper_challenge_reader_consumes_one_bounded_frame(self) -> None:
        source = artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT
        start = source.index("def read_identity_challenge():")
        end = source.index("def read_control():", start)
        scope = {"os": os, "select": select, "time": time, "re": __import__("re")}
        exec(source[start:end], scope)
        frame = b"challenge:" + b"ab" * 32 + b"\n"
        chunks = [bytes([byte]) for byte in frame]
        with (
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
            patch.object(artifacts.os, "read", side_effect=chunks) as read,
            patch.object(artifacts.select, "select", return_value=([0], [], [])),
            patch.object(artifacts.time, "monotonic", return_value=1),
        ):
            self.assertEqual(scope["read_identity_challenge"](), "ab" * 32)
        self.assertEqual(read.call_count, len(frame))
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_wrapper_challenge_reader_rejects_truncated_and_malformed_frames(self) -> None:
        source = artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT
        start = source.index("def read_identity_challenge():")
        end = source.index("def read_control():", start)
        scope = {"os": os, "select": select, "time": time, "re": __import__("re")}
        exec(source[start:end], scope)
        malformed = b"challenge:" + b"g" * 64 + b"\n"
        for chunks, reason in (([b"c", b""], "host identity challenge ended early"),
                               ([bytes([byte]) for byte in malformed], "host identity challenge is invalid")):
            with self.subTest(chunks=len(chunks)):
                with (
                    patch.object(artifacts.subprocess, "Popen"),
                    patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
                    patch.object(artifacts.os, "killpg") as kill_group,
                    patch.object(artifacts.os, "read", side_effect=chunks),
                    patch.object(artifacts.select, "select", return_value=([0], [], [])),
                    patch.object(artifacts.time, "monotonic", return_value=1),
                ):
                    with self.assertRaisesRegex(RuntimeError, f"^{reason}$"):
                        scope["read_identity_challenge"]()
                send_signal.assert_not_called()
                kill_group.assert_not_called()

    def test_fresh_proof_requires_live_pidfd_and_matching_namespace(self) -> None:
        nonce = "ab" * 32
        proof = json.dumps({"version": 3, "pid": 1, "event": "identity-proof",
                            "pidNamespace": 456, "nonce": nonce}).encode()
        with (
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
            patch.object(artifacts.select, "select", return_value=([], [], [])) as select_call,
            patch.object(artifacts.os, "open", return_value=60),
            patch.object(artifacts.os, "fstat", return_value=MagicMock(st_ino=456)),
            patch.object(artifacts.os, "close") as close,
        ):
            artifacts._verify_inner_supervisor_identity(50, 123, 456, nonce, proof)
        self.assertEqual(select_call.call_count, 2)
        close.assert_called_once_with(60)
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_stale_proof_is_rejected_before_namespace_lookup_or_signal(self) -> None:
        proof = json.dumps({"version": 3, "pid": 1, "event": "identity-proof",
                            "pidNamespace": 456, "nonce": "ab" * 32}).encode()
        with (
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
            patch.object(artifacts.os, "open") as open_namespace,
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "does not match"):
                artifacts._verify_inner_supervisor_identity(50, 123, 456, "cd" * 32, proof)
        open_namespace.assert_not_called()
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_exit_after_namespace_open_rejects_proof_and_closes_namespace(self) -> None:
        nonce = "ab" * 32
        proof = json.dumps({"version": 3, "pid": 1, "event": "identity-proof",
                            "pidNamespace": 456, "nonce": nonce}).encode()
        with (
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
            patch.object(artifacts.select, "select", side_effect=[([], [], []), ([50], [], [])]),
            patch.object(artifacts.os, "open", return_value=60),
            patch.object(artifacts.os, "fstat", return_value=MagicMock(st_ino=456)),
            patch.object(artifacts.os, "close") as close,
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "exited during"):
                artifacts._verify_inner_supervisor_identity(50, 123, 456, nonce, proof)
        close.assert_called_once_with(60)
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_unverified_namespace_closes_provisional_pidfd_without_signaling(self) -> None:
        for stat_result in (MagicMock(st_ino=999), FileNotFoundError(errno.ENOENT, "missing")):
            with self.subTest(stat_result=stat_result):
                with (
                    patch.object(artifacts.subprocess, "Popen") as popen,
                    patch.object(artifacts.os, "pidfd_open", return_value=50),
                    patch.object(artifacts.os, "stat") as stat_call,
                    patch.object(artifacts.os, "close") as close,
                    patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
                    patch.object(artifacts.os, "killpg") as kill_group,
                ):
                    if isinstance(stat_result, Exception):
                        stat_call.side_effect = stat_result
                    else:
                        stat_call.return_value = stat_result
                    with self.assertRaises(artifacts.IntegrityError):
                        artifacts._pin_reported_supervisor(123, 456)
                close.assert_called_once_with(50)
                popen.assert_not_called()
                send_signal.assert_not_called()
                kill_group.assert_not_called()

    def test_matching_namespace_transfers_the_pidfd_to_caller(self) -> None:
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.os, "pidfd_open", return_value=50),
            patch.object(artifacts.os, "stat", return_value=MagicMock(st_ino=456)),
            patch.object(artifacts.os, "close") as close,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            self.assertEqual(artifacts._pin_reported_supervisor(123, 456), 50)
        close.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_close_failure_keeps_namespace_mismatch_as_primary_error(self) -> None:
        for close_error in (OSError(errno.EIO, "close failure"), KeyboardInterrupt()):
            with self.subTest(close_error=close_error):
                with (
                    patch.object(artifacts.subprocess, "Popen"),
                    patch.object(artifacts.os, "pidfd_open", return_value=50),
                    patch.object(artifacts.os, "stat", return_value=MagicMock(st_ino=999)),
                    patch.object(artifacts.os, "close", side_effect=close_error),
                    patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
                    patch.object(artifacts.os, "killpg") as kill_group,
                ):
                    with self.assertRaisesRegex(artifacts.IntegrityError, "namespace identity changed") as caught:
                        artifacts._pin_reported_supervisor(123, 456)
                self.assertTrue(any("provisional pidfd close failed" in note for note in caught.exception.__notes__))
                send_signal.assert_not_called()
                kill_group.assert_not_called()

    def test_orphan_reaper_reaps_adopted_zombies_but_leaves_the_candidates_status_to_popen(self) -> None:
        # Deterministic form of the #2043 contract, isolated in a fresh process so the
        # reaper sees only its fixtures: an older exited child stands in for an adopted
        # orphan, and the candidate has exited but Popen has not collected it. Popen maps a
        # status stolen by another reaper to exit code 0.
        source = artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT
        start = source.index("def reap_adopted_children():")
        end = source.index("def terminate_namespace_children():", start)
        harness = textwrap.dedent(
            """\
            import json, os, subprocess, sys
            adopted = os.fork()
            if adopted == 0:
                os._exit(0)
            os.waitid(os.P_PID, adopted, os.WEXITED | os.WNOWAIT)
            candidate = subprocess.Popen([sys.executable, "-I", "-c", "raise SystemExit(37)"])
            os.waitid(os.P_PID, candidate.pid, os.WEXITED | os.WNOWAIT)
            scope = {"os": os, "candidate": candidate, "reaped_count": 0}
            exec(sys.argv[1], scope)
            scope["reap_adopted_children"]()
            try:
                os.waitid(os.P_PID, adopted, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                adopted_reaped = False
            except ChildProcessError:
                adopted_reaped = True
            print(json.dumps({
                "adoptedReaped": adopted_reaped,
                "reapedCount": scope["reaped_count"],
                "candidateReturnCode": candidate.poll(),
            }))
            """
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-c", harness, source[start:end]],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {"adoptedReaped": True, "reapedCount": 1, "candidateReturnCode": 37},
        )


class NativeNamespaceNoticeTests(unittest.TestCase):
    def make_notice(self, launcher):
        notice = artifacts.PinnedSupervisorNamespace("attempt", 123, 456, -1, -1, launcher)
        notice._namespace_fd, notice._pidfd = 50, 51
        notice._native_slots[0], notice._native_slots[1] = 50, 51
        return notice

    def test_borrowed_owner_is_retained_after_interrupted_close(self) -> None:
        launcher = MagicMock()
        interrupted = {"done": False}

        def duplicate(pointer, source):
            pointer[0] = source + 20
            return 0

        def close(pointer):
            if not interrupted["done"]:
                interrupted["done"] = True
                raise KeyboardInterrupt()
            pointer[0] = -1
            return 0

        launcher._owned_dup.side_effect = duplicate
        launcher._owned_close.side_effect = close
        notice = self.make_notice(launcher)
        owner = artifacts.BorrowedSupervisorNamespace(launcher)
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg"),
              patch.object(artifacts.os, "fstat", return_value=MagicMock(st_ino=456)),
              patch.object(artifacts.select, "select", return_value=([], [], []))):
            notice.borrow_into(owner)
            with self.assertRaises(KeyboardInterrupt) as caught:
                owner.close()
            self.assertIn(owner, artifacts._retained_atomic_owners)
            self.assertFalse(owner.all_closed())
            self.assertTrue(any("owner remains retained" in note for note in caught.exception.__notes__))
            owner.close()
        self.assertNotIn(owner, artifacts._retained_atomic_owners)


    def test_interruption_at_lease_entry_restores_count_and_allows_cleanup(self) -> None:
        launcher = MagicMock()
        closed = []

        def close(pointer):
            closed.append(pointer[0])
            pointer[0] = -1
            return 0

        launcher._owned_close.side_effect = close
        owner = artifacts.BorrowedSupervisorNamespace(launcher)
        owner._slots[0], owner._slots[1], owner._ready = 70, 71, True
        generator = artifacts.BorrowedSupervisorNamespace.lease.__wrapped__
        lines, start = inspect.getsourcelines(generator)
        yield_line = start + next(index for index, line in enumerate(lines) if "yield self._slots" in line)

        def interrupt(frame, event, _argument):
            if frame.f_code is generator.__code__ and event == "line" and frame.f_lineno == yield_line:
                raise KeyboardInterrupt()
            return interrupt

        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg"),
              patch.object(artifacts.select, "select", return_value=([], [], []))):
            previous_trace = sys.gettrace()
            try:
                sys.settrace(interrupt)
                with self.assertRaises(KeyboardInterrupt):
                    with owner.lease():
                        self.fail("lease entry did not interrupt")
            finally:
                sys.settrace(previous_trace)
            owner.close()
        self.assertEqual(closed, [70, 71])

    def test_nested_lease_cannot_close_its_active_descriptors(self) -> None:
        launcher = MagicMock()
        closed = []

        def close(pointer):
            closed.append(pointer[0])
            pointer[0] = -1
            return 0

        launcher._owned_close.side_effect = close
        owner = artifacts.BorrowedSupervisorNamespace(launcher)
        owner._slots[0], owner._slots[1], owner._ready = 70, 71, True
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg"),
              patch.object(artifacts.select, "select", return_value=([], [], []))):
            with owner.lease() as first:
                with owner.lease() as second:
                    self.assertEqual(first, second)
                    with self.assertRaisesRegex(artifacts.IntegrityError, "active lease"):
                        owner.close()
                with self.assertRaisesRegex(artifacts.IntegrityError, "active lease"):
                    owner.close()
            owner.close()
        self.assertEqual(closed, [70, 71])

    def test_revocation_waits_for_borrow_and_does_not_close_borrowed_descriptors(self) -> None:
        launcher = MagicMock()
        duplicate_started, allow_duplicate, revoke_started, revoke_done = (threading.Event() for _ in range(4))
        closed, failures = [], []

        def duplicate(pointer, source):
            if source == 50:
                duplicate_started.set()
                if not allow_duplicate.wait(1):
                    raise RuntimeError("borrow was not released")
            pointer[0] = source + 20
            return 0

        def close(pointer):
            closed.append(pointer[0])
            pointer[0] = -1
            return 0

        launcher._owned_dup.side_effect = duplicate
        launcher._owned_close.side_effect = close
        notice = self.make_notice(launcher)
        owner = artifacts.BorrowedSupervisorNamespace(launcher)

        def borrow():
            try:
                notice.borrow_into(owner)
            except BaseException as error:
                failures.append(error)

        def revoke():
            revoke_started.set()
            try:
                if not notice._revoke():
                    failures.append(RuntimeError("revoke failed"))
            except BaseException as error:
                failures.append(error)
            finally:
                revoke_done.set()

        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg"),
              patch.object(artifacts.os, "fstat", return_value=MagicMock(st_ino=456)),
              patch.object(artifacts.select, "select", return_value=([], [], []))):
            borrow_thread = threading.Thread(target=borrow)
            revoke_thread = threading.Thread(target=revoke)
            borrow_thread.start()
            self.assertTrue(duplicate_started.wait(1))
            revoke_thread.start()
            self.assertTrue(revoke_started.wait(1))
            self.assertFalse(revoke_done.wait(0.05))
            allow_duplicate.set()
            borrow_thread.join(timeout=1)
            revoke_thread.join(timeout=1)
            self.assertFalse(borrow_thread.is_alive())
            self.assertFalse(revoke_thread.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(closed, [50, 51])
            with owner.lease() as descriptors:
                self.assertEqual(descriptors, (70, 71))
            owner.close()
        self.assertEqual(closed, [50, 51, 70, 71])

    def test_close_waits_for_active_borrowed_descriptor_lease(self) -> None:
        launcher = MagicMock()
        closed = []

        def duplicate(pointer, source):
            pointer[0] = source + 20
            return 0

        def close(pointer):
            closed.append(pointer[0])
            pointer[0] = -1
            return 0

        launcher._owned_dup.side_effect = duplicate
        launcher._owned_close.side_effect = close
        notice = self.make_notice(launcher)
        owner = artifacts.BorrowedSupervisorNamespace(launcher)
        started = threading.Event()
        finished = threading.Event()

        def close_owner():
            started.set()
            owner.close()
            finished.set()

        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg"),
              patch.object(artifacts.os, "fstat", return_value=MagicMock(st_ino=456)),
              patch.object(artifacts.select, "select", return_value=([], [], []))):
            notice.borrow_into(owner)
            with owner.lease() as descriptors:
                thread = threading.Thread(target=close_owner)
                thread.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(0.05))
                self.assertEqual(descriptors, (70, 71))
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertTrue(finished.is_set())
        self.assertEqual(closed, [70, 71])

    def test_exited_supervisor_cannot_be_leased(self) -> None:
        launcher = MagicMock()
        owner = artifacts.BorrowedSupervisorNamespace(launcher)
        owner._slots[0], owner._slots[1], owner._ready = 70, 71, True
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg"),
              patch.object(artifacts.select, "select", return_value=([71], [], []))):
            self.assertTrue(owner.supervisor_exited())
            with self.assertRaisesRegex(artifacts.IntegrityError, "no longer live"):
                with owner.lease():
                    self.fail("exited supervisor became usable")

    def test_interrupted_borrow_keeps_every_duplicate_in_the_caller_owner(self) -> None:
        for interrupted_index in (0, 1):
            with self.subTest(interrupted_index=interrupted_index):
                launcher = MagicMock()
                closed = []
                calls = []

                def duplicate(pointer, _source):
                    index = len(calls)
                    calls.append(index)
                    pointer[0] = 70 + index
                    if index == interrupted_index:
                        raise KeyboardInterrupt()
                    return 0

                def close(pointer):
                    if pointer[0] >= 0:
                        closed.append(pointer[0])
                    pointer[0] = -1
                    return 0

                launcher._owned_dup.side_effect = duplicate
                launcher._owned_close.side_effect = close
                notice = self.make_notice(launcher)
                owner = artifacts.BorrowedSupervisorNamespace(launcher)
                with (patch.object(artifacts.subprocess, "Popen"),
                      patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
                      patch.object(artifacts.os, "killpg") as kill_group,
                      patch.object(artifacts.select, "select", return_value=([], [], []))):
                    with self.assertRaises(KeyboardInterrupt):
                        notice.borrow_into(owner)
                    with self.assertRaises(artifacts.IntegrityError):
                        with owner.lease():
                            self.fail("incomplete borrow became available")
                    owner.close()
                    owner.close()
                    with self.assertRaises(artifacts.IntegrityError):
                        notice.borrow_into(owner)
                self.assertEqual(closed, [70] if interrupted_index == 0 else [70, 71])
                send_signal.assert_not_called()
                kill_group.assert_not_called()

    def test_native_notice_rejects_unowned_borrow(self) -> None:
        launcher = MagicMock()
        notice = self.make_notice(launcher)
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg")):
            with self.assertRaisesRegex(artifacts.IntegrityError, "caller-owned lease"):
                notice.borrow()
            with self.assertRaises(artifacts.IntegrityError):
                notice.borrow_into(artifacts.BorrowedSupervisorNamespace(MagicMock()))
            owner = artifacts.BorrowedSupervisorNamespace(launcher)
            owner.close()
            with self.assertRaises(artifacts.IntegrityError):
                notice.borrow_into(owner)

    def test_interrupted_publication_retires_every_published_duplicate(self) -> None:
        for interrupted_index in (0, 1):
            with self.subTest(interrupted_index=interrupted_index):
                launcher = MagicMock()
                closed = []
                calls = []

                def duplicate(pointer, _source):
                    index = len(calls)
                    calls.append(index)
                    pointer[0] = 60 + index
                    if index == interrupted_index:
                        raise KeyboardInterrupt()
                    return 0

                def close(pointer):
                    closed.append(pointer[0])
                    pointer[0] = -1
                    return 0

                launcher._owned_dup.side_effect = duplicate
                launcher._owned_close.side_effect = close
                notice = artifacts.PinnedSupervisorNamespace("attempt", 123, 456, -1, -1, launcher)
                with (patch.object(artifacts.subprocess, "Popen"),
                      patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
                      patch.object(artifacts.os, "killpg") as kill_group):
                    with self.assertRaises(KeyboardInterrupt):
                        notice._acquire_native(50, 51)
                    self.assertTrue(notice._revoke())
                    self.assertTrue(notice._revoke())
                self.assertEqual(closed, [60] if interrupted_index == 0 else [60, 61])
                send_signal.assert_not_called()
                kill_group.assert_not_called()


class InnerSupervisorDescriptorTests(unittest.TestCase):
    def test_inner_descriptor_lease_blocks_reentrant_close(self) -> None:
        launcher = MagicMock()
        owner = artifacts.InnerSupervisorDescriptors(launcher)
        owner._slots[0], owner._slots[1] = 50, 51
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg")):
            with owner.lease(50) as namespace_fd:
                self.assertEqual(namespace_fd, 51)
                with self.assertRaisesRegex(artifacts.IntegrityError, "active lease"):
                    owner.close_all()
        launcher._owned_close.assert_not_called()

    def test_cleanup_preserves_interruption_after_an_earlier_close_failure(self) -> None:
        launcher = MagicMock()
        launcher._owned_close.side_effect = [OSError("close failed"), KeyboardInterrupt()]
        owner = artifacts.InnerSupervisorDescriptors(launcher)
        owner._slots[0], owner._slots[1] = 50, 51
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg")):
            with self.assertRaises(KeyboardInterrupt) as caught:
                owner.close_all()
        self.assertEqual(launcher._owned_close.call_count, 2)
        self.assertTrue(any("OSError" in note for note in caught.exception.__notes__))

    def test_interrupted_acquisition_keeps_published_descriptors_owned_for_cleanup(self) -> None:
        for interrupted_index in (0, 1):
            with self.subTest(interrupted_index=interrupted_index):
                launcher = MagicMock()
                closed = []

                def acquire(index):
                    def open_descriptor(pointer, _pid):
                        pointer[0] = 50 + index
                        if index == interrupted_index:
                            raise KeyboardInterrupt()
                        return 0
                    return open_descriptor

                def close(pointer):
                    if pointer[0] >= 0:
                        closed.append(pointer[0])
                        pointer[0] = -1
                    return 0

                launcher._owned_pidfd_open.side_effect = acquire(0)
                launcher._owned_pid_namespace.side_effect = acquire(1)
                launcher._owned_close.side_effect = close
                owner = artifacts.InnerSupervisorDescriptors(launcher)
                with (patch.object(artifacts.subprocess, "Popen") as popen,
                      patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
                      patch.object(artifacts.os, "killpg") as kill_group):
                    with self.assertRaises(KeyboardInterrupt):
                        owner.acquire(123, 456)
                    owner.close_all()
                self.assertEqual(closed, [50] if interrupted_index == 0 else [50, 51])
                popen.assert_not_called()
                send_signal.assert_not_called()
                kill_group.assert_not_called()

    def test_namespace_mismatch_keeps_both_descriptors_owned(self) -> None:
        launcher = MagicMock()
        closed = []

        def acquire(fd):
            def open_descriptor(pointer, _pid):
                pointer[0] = fd
                return 0
            return open_descriptor

        def close(pointer):
            if pointer[0] >= 0:
                closed.append(pointer[0])
                pointer[0] = -1
            return 0

        launcher._owned_pidfd_open.side_effect = acquire(50)
        launcher._owned_pid_namespace.side_effect = acquire(51)
        launcher._owned_close.side_effect = close
        owner = artifacts.InnerSupervisorDescriptors(launcher)
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
              patch.object(artifacts.os, "killpg") as kill_group,
              patch.object(artifacts.os, "fstat", return_value=MagicMock(st_ino=999))):
            with self.assertRaisesRegex(artifacts.IntegrityError, "namespace identity changed"):
                owner.acquire(123, 456)
            owner.close_all()
        self.assertEqual(closed, [50, 51])
        send_signal.assert_not_called()
        kill_group.assert_not_called()


class AtomicMountDescriptorTests(unittest.TestCase):
    def test_verified_traversal_interruption_at_state_transitions_fails_closed(self) -> None:
        function = artifacts.AtomicMountDescriptors.open_verified_directory
        lines, start = inspect.getsourcelines(function)
        markers = ("if encoded == b\"/\":", "metadata = os.fstat", "self._traversal_failed = False")
        marker_lines = [start + next(index for index, line in enumerate(lines) if marker in line) for marker in markers]
        for target in (*marker_lines, "return"):
            with self.subTest(target=target):
                launcher = MagicMock()
                descriptors = iter(range(70, 73))

                def open_at(pointer, _directory, _path, _flags, _mode):
                    pointer[0] = next(descriptors)
                    return 0

                def close(pointer):
                    pointer[0] = -1
                    return 0

                launcher._owned_openat.side_effect = open_at
                launcher._owned_close.side_effect = close
                owner = artifacts.AtomicMountDescriptors(launcher, 3)
                metadata = MagicMock(st_dev=1, st_ino=2, st_uid=3, st_mode=0o040755)

                def interrupt(frame, event, _argument):
                    if frame.f_code is function.__code__ and (
                        (target == "return" and event == "return") or (event == "line" and frame.f_lineno == target)
                    ):
                        raise KeyboardInterrupt()
                    return interrupt

                with (patch.object(artifacts.subprocess, "Popen"),
                      patch.object(artifacts.signal, "pidfd_send_signal"),
                      patch.object(artifacts.os, "killpg"),
                      patch.object(artifacts.os, "fstat", return_value=metadata)):
                    previous_trace = sys.gettrace()
                    try:
                        sys.settrace(interrupt)
                        with self.assertRaises(KeyboardInterrupt):
                            owner.open_verified_directory(2, "/trusted/tool", (1, 2, 3, 0o755))
                    finally:
                        sys.settrace(previous_trace)
                    if target == "return":
                        with owner.lease() as lease:
                            self.assertEqual(lease.descriptor(2), 72)
                    else:
                        with self.assertRaises(artifacts.IntegrityError):
                            with owner.lease():
                                self.fail("interrupted traversal became usable")
                    owner.close_all()
                self.assertNotIn(owner, artifacts._retained_atomic_owners)

    def test_verified_directory_interruption_during_each_acquisition_or_close_fails_closed(self) -> None:
        for failing_operation in ("open-0", "open-1", "open-2", "close-0", "close-1"):
            with self.subTest(failing_operation=failing_operation):
                launcher = MagicMock()
                opened = iter(range(70, 73))
                counts = {"open": 0, "close": 0}
                interrupted = {"done": False}

                def open_at(pointer, _directory, _path, _flags, _mode):
                    number = counts["open"]
                    counts["open"] += 1
                    pointer[0] = next(opened)
                    if failing_operation == f"open-{number}":
                        interrupted["done"] = True
                        raise KeyboardInterrupt()
                    return 0

                def close(pointer):
                    number = counts["close"]
                    counts["close"] += 1
                    pointer[0] = -1
                    if not interrupted["done"] and failing_operation == f"close-{number}":
                        interrupted["done"] = True
                        raise KeyboardInterrupt()
                    return 0

                launcher._owned_openat.side_effect = open_at
                launcher._owned_close.side_effect = close
                owner = artifacts.AtomicMountDescriptors(launcher, 3)
                with (patch.object(artifacts.subprocess, "Popen"),
                      patch.object(artifacts.signal, "pidfd_send_signal"),
                      patch.object(artifacts.os, "killpg")):
                    with self.assertRaises(KeyboardInterrupt):
                        owner.open_verified_directory(2, "/trusted/tool", (1, 2, 3, 0o755))
                    with self.assertRaises(artifacts.IntegrityError):
                        with owner.lease():
                            self.fail("interrupted traversal exposed its descriptors")
                    owner.close_all()
                self.assertNotIn(owner, artifacts._retained_atomic_owners)

    def test_verified_directory_handles_root_depth_and_capacity(self) -> None:
        metadata = MagicMock(st_dev=1, st_ino=2, st_uid=3, st_mode=0o040755)
        for path, capacity in (("/", 1), ("/one", 2), ("/one/two/three/four", 3)):
            with self.subTest(path=path):
                launcher = MagicMock()
                opened = iter(range(70, 80))

                def open_at(pointer, _directory, _path, _flags, _mode):
                    pointer[0] = next(opened)
                    return 0

                def close(pointer):
                    pointer[0] = -1
                    return 0

                launcher._owned_openat.side_effect = open_at
                launcher._owned_close.side_effect = close
                owner = artifacts.AtomicMountDescriptors(launcher, capacity)
                with (patch.object(artifacts.subprocess, "Popen"),
                      patch.object(artifacts.signal, "pidfd_send_signal"),
                      patch.object(artifacts.os, "killpg"),
                      patch.object(artifacts.os, "fstat", return_value=metadata)):
                    owner.open_verified_directory(capacity - 1, path, (1, 2, 3, 0o755))
                    with owner.lease() as lease:
                        self.assertGreaterEqual(lease.descriptor(capacity - 1), 70)
                    owner.close_all()
                self.assertNotIn(owner, artifacts._retained_atomic_owners)
        owner = artifacts.AtomicMountDescriptors(MagicMock(), 2)
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg")):
            with self.assertRaisesRegex(artifacts.IntegrityError, "insufficient"):
                owner.open_verified_directory(1, "/one/two", (1, 2, 3, 0o755))
            owner.close_all()

    def test_verified_directory_rejects_changed_identity_and_retains_owned_slots(self) -> None:
        launcher = MagicMock()
        opened = []
        closed = []
        descriptors = iter((70, 71, 72))

        def open_at(pointer, directory, path, _flags, _mode):
            pointer[0] = next(descriptors)
            opened.append((directory, path))
            return 0

        def close(pointer):
            closed.append(pointer[0])
            pointer[0] = -1
            return 0

        launcher._owned_openat.side_effect = open_at
        launcher._owned_close.side_effect = close
        owner = artifacts.AtomicMountDescriptors(launcher, 3)
        metadata = MagicMock(st_dev=1, st_ino=9, st_uid=3, st_mode=0o040755)
        with (patch.object(artifacts.subprocess, "Popen") as popen,
              patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
              patch.object(artifacts.os, "killpg") as kill_group,
              patch.object(artifacts.os, "fstat", return_value=metadata)):
            with self.assertRaisesRegex(artifacts.IntegrityError, "identity changed"):
                owner.open_verified_directory(2, "/trusted/tool", (1, 2, 3, 0o755))
            self.assertIn(owner, artifacts._retained_atomic_owners)
            with self.assertRaises(artifacts.IntegrityError):
                with owner.lease():
                    self.fail("failed traversal exposed its descriptors")
            with self.assertRaises(artifacts.IntegrityError):
                owner.seal({2: (1, 9, 3, 0o755)})
            owner.close_all()
        self.assertEqual(opened, [(-100, b"/"), (70, b"trusted"), (71, b"tool")])
        self.assertEqual(closed, [70, 71, 72])
        self.assertNotIn(owner, artifacts._retained_atomic_owners)
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_verified_directory_rejects_noncanonical_and_overdeep_paths(self) -> None:
        launcher = MagicMock()
        owner = artifacts.AtomicMountDescriptors(launcher, 3)
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg")):
            for path in ("relative", "/a/../b", "/a//b", "/" + "/".join(["a"] * (artifacts.MAX_DEPTH + 1))):
                with self.subTest(path=path), self.assertRaises(artifacts.IntegrityError):
                    owner.open_verified_directory(2, path, (1, 2, 3, 0o755))
        launcher._owned_openat.assert_not_called()

    def test_atomic_bind_sources_must_be_explicitly_owned(self) -> None:
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg")):
            for source in ("/dev/fd/1", "/proc/self/./fd/1", "/proc/123/fd/1", "/tmp/link"):
                with self.subTest(source=source):
                    with self.assertRaisesRegex(artifacts.IntegrityError, "not owned"):
                        artifacts._resolve_atomic_mount_arguments(
                            ["/usr/bin/bwrap", "--ro-bind", source, "/target"], {},
                        )
            arguments = ["/usr/bin/bwrap", "--bind", "/usr", "/usr"]
            with self.assertRaises(artifacts.IntegrityError):
                artifacts._resolve_atomic_mount_arguments(arguments, {})
            delimiter_value = ["/usr/bin/bwrap", "--setenv", "KEY", "--", "--ro-bind", "/dev/fd/0", "/target", "--", "/bin/true"]
            with self.assertRaisesRegex(artifacts.IntegrityError, "not owned"):
                artifacts._resolve_atomic_mount_arguments(delimiter_value, {})
            for option in ("--file", "--bind-data", "--ro-bind-data", "--seccomp", "--unknown"):
                with self.subTest(option=option):
                    with self.assertRaisesRegex(artifacts.IntegrityError, "unsupported"):
                        artifacts._resolve_atomic_mount_arguments(["/usr/bin/bwrap", option, "0", "/target"], {})
            with self.assertRaisesRegex(artifacts.IntegrityError, "incomplete"):
                artifacts._resolve_atomic_mount_arguments(["/usr/bin/bwrap", "--setenv", "KEY"], {})
            after_command = ["/usr/bin/bwrap", "--", "/bin/true", "--file", "0"]
            self.assertEqual(artifacts._resolve_atomic_mount_arguments(after_command, {}), after_command)

    def test_native_launch_holds_mount_owner_until_inheritance_is_complete(self) -> None:
        launcher = MagicMock()
        descriptors = iter(range(100, 112))
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        observed, failures = {}, []

        def create(pointer):
            pointer[0], pointer[1] = next(descriptors), next(descriptors)
            return 0

        def close(pointer):
            if pointer[0] == 70:
                closed.set()
            pointer[0] = -1
            return 0

        def launch(pidfd_pointer, _stdin, _stdout, _ready, _gate, _error, inherited, count, _cwd, argv, _envp):
            observed["inherited"] = [inherited[index] for index in range(count)]
            observed["arguments"] = [argv[index] for index in range(6)]
            entered.set()
            if not release.wait(1):
                raise RuntimeError("native launch was not released")
            pidfd_pointer[0] = 200
            return 0

        launcher._owned_pipe.side_effect = create
        launcher._owned_close.side_effect = close
        launcher.side_effect = launch
        mount_owner = artifacts.AtomicMountDescriptors(launcher, 1)
        mount_owner._slots[0] = 70
        outer = artifacts.AtomicDescriptorSlots(launcher._owned_pipe, launcher._owned_close)
        publication = artifacts.AtomicLaunchOwner()
        metadata = MagicMock(st_dev=1, st_ino=2, st_uid=3, st_mode=0o100444)

        def spawn():
            try:
                artifacts._spawn_slot_atomic_supervisor(
                    ["/usr/bin/bwrap", "--ro-bind", "/proc/self/fd/@verjson-slot-0@", "/target"],
                    {}, (), None, launcher, outer, publication, mount_owner, (0,),
                )
            except BaseException as error:
                failures.append(error)

        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg"),
              patch.object(artifacts.os, "set_blocking"),
              patch.object(artifacts.os, "fstat", return_value=metadata)):
            mount_owner.seal({0: (1, 2, 3, 0o444)})
            outer.open_pair(11)
            spawning = threading.Thread(target=spawn)
            closing = threading.Thread(target=mount_owner.close_all)
            spawning.start()
            self.assertTrue(entered.wait(1))
            closing.start()
            self.assertFalse(closed.wait(0.05))
            release.set()
            spawning.join(timeout=1)
            closing.join(timeout=1)
            self.assertFalse(spawning.is_alive())
            self.assertFalse(closing.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue(closed.is_set())
            self.assertIn(70, observed["inherited"])
            self.assertIn(b"/proc/self/fd/70", observed["arguments"])
            outer._reap_result.complete = 1
            outer.close_all()

    def test_sealed_mount_identity_is_rechecked_and_cannot_be_replaced(self) -> None:
        launcher = MagicMock()
        owner = artifacts.AtomicMountDescriptors(launcher, 2)
        owner._slots[0] = 70
        original = MagicMock(st_dev=1, st_ino=2, st_uid=3, st_mode=0o100444)
        changed = MagicMock(st_dev=1, st_ino=9, st_uid=3, st_mode=0o100444)
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg"),
              patch.object(artifacts.os, "fstat", side_effect=[original, changed])):
            owner.seal({0: (1, 2, 3, 0o444)})
            with self.assertRaisesRegex(artifacts.IntegrityError, "sealed"):
                owner.close(0)
            with owner.lease():
                with self.assertRaisesRegex(artifacts.IntegrityError, "identity changed"):
                    owner.verify_sealed((0,))

    def test_atomic_supervisor_rejects_unowned_inherited_descriptors_before_launch(self) -> None:
        with (patch.object(artifacts.subprocess, "Popen") as popen,
              patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
              patch.object(artifacts.os, "killpg") as kill_group):
            with self.assertRaisesRegex(artifacts.IntegrityError, "owned mount lease"):
                artifacts.run_supervised_bubblewrap(["/usr/bin/true"], environment={}, pass_fds=(50,),
                                                   _atomic_launcher=MagicMock())
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_interrupted_open_keeps_descriptor_registered_until_cleanup(self) -> None:
        launcher = MagicMock()
        closed = []

        def open_at(pointer, _directory, _path, _flags, _mode):
            pointer[0] = 70
            raise KeyboardInterrupt()

        def close(pointer):
            closed.append(pointer[0])
            pointer[0] = -1
            return 0

        launcher._owned_openat.side_effect = open_at
        launcher._owned_close.side_effect = close
        owner = artifacts.AtomicMountDescriptors(launcher, 2)
        with (patch.object(artifacts.subprocess, "Popen") as popen,
              patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
              patch.object(artifacts.os, "killpg") as kill_group):
            with self.assertRaises(KeyboardInterrupt):
                owner.open_at(0, None, "/", os.O_RDONLY | os.O_NOFOLLOW)
            self.assertIn(owner, artifacts._retained_atomic_owners)
            owner.close_all()
        self.assertEqual(closed, [70])
        self.assertNotIn(owner, artifacts._retained_atomic_owners)
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_failed_native_open_with_published_descriptor_is_retained_for_cleanup(self) -> None:
        launcher = MagicMock()
        closed = []

        def open_at(pointer, _directory, _path, _flags, _mode):
            pointer[0] = 70
            return -errno.EIO

        def close(pointer):
            closed.append(pointer[0])
            pointer[0] = -1
            return 0

        launcher._owned_openat.side_effect = open_at
        launcher._owned_close.side_effect = close
        owner = artifacts.AtomicMountDescriptors(launcher, 1)
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg")):
            with self.assertRaisesRegex(artifacts.IntegrityError, "errno 5"):
                owner.open_at(0, None, "/", os.O_RDONLY | os.O_NOFOLLOW)
            self.assertIn(owner, artifacts._retained_atomic_owners)
            owner.close_all()
        self.assertEqual(closed, [70])
        self.assertNotIn(owner, artifacts._retained_atomic_owners)

    def test_mount_lease_blocks_reentrant_or_concurrent_close(self) -> None:
        launcher = MagicMock()
        closed = threading.Event()

        def close(pointer):
            pointer[0] = -1
            closed.set()
            return 0

        launcher._owned_close.side_effect = close
        owner = artifacts.AtomicMountDescriptors(launcher, 1)
        owner._slots[0] = 70
        started = threading.Event()

        def close_owner():
            started.set()
            owner.close_all()

        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg")):
            with owner.lease() as lease:
                self.assertEqual(lease.descriptor(0), 70)
                with self.assertRaisesRegex(artifacts.IntegrityError, "active lease"):
                    owner.close_all()
                thread = threading.Thread(target=close_owner)
                thread.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(closed.wait(0.05))
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertTrue(closed.is_set())

    def test_mount_component_rejects_unsafe_traversal_and_open_flags(self) -> None:
        launcher = MagicMock()
        owner = artifacts.AtomicMountDescriptors(launcher, 2)
        owner._slots[0] = 70
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg")):
            for path, flags in (("../escape", os.O_RDONLY | os.O_NOFOLLOW),
                                ("entry", os.O_RDONLY),
                                ("entry", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW),
                                ("entry", os.O_RDONLY | os.O_TRUNC | os.O_NOFOLLOW)):
                with self.subTest(path=path, flags=flags):
                    with self.assertRaises(artifacts.IntegrityError):
                        owner.open_at(1, 0, path, flags)
            for path in ("/tmp/../etc", "/tmp/link", "relative"):
                with self.subTest(root=path):
                    with self.assertRaises(artifacts.IntegrityError):
                        owner.open_at(1, None, path, os.O_RDONLY | os.O_NOFOLLOW)
        launcher._owned_openat.assert_not_called()


class AtomicDescriptorSlotTests(unittest.TestCase):
    def test_retired_owners_are_unregistered_even_when_close_reports_failure(self) -> None:
        for kind in ("outer", "inner", "borrowed"):
            for interrupted in (False, True):
                with self.subTest(kind=kind, interrupted=interrupted):
                    launcher = MagicMock()

                    def close(pointer):
                        pointer[0] = -1
                        if interrupted:
                            raise KeyboardInterrupt()
                        return -errno.EINTR

                    launcher._owned_close.side_effect = close
                    if kind == "outer":
                        owner = artifacts.AtomicDescriptorSlots(MagicMock(), launcher._owned_close)
                        owner._slots[11] = 50
                        cleanup = owner.close_all
                    elif kind == "inner":
                        owner = artifacts.InnerSupervisorDescriptors(launcher)
                        owner._slots[0] = 50
                        cleanup = owner.close_all
                    else:
                        owner = artifacts.BorrowedSupervisorNamespace(launcher)
                        owner._slots[0] = 50
                        cleanup = owner.close
                    artifacts._retain_atomic_owner(owner)
                    with (patch.object(artifacts.subprocess, "Popen"),
                          patch.object(artifacts.signal, "pidfd_send_signal"),
                          patch.object(artifacts.os, "killpg")):
                        with self.assertRaises(KeyboardInterrupt if interrupted else artifacts.IntegrityError):
                            cleanup()
                    self.assertTrue(owner.all_closed())
                    self.assertNotIn(owner, artifacts._retained_atomic_owners)

    def test_reap_and_pidfd_close_are_serialized(self) -> None:
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()

        def reap(_pidfd, result_pointer):
            entered.set()
            if not release.wait(1):
                raise RuntimeError("reap was not released")
            result = ctypes.cast(result_pointer, ctypes.POINTER(artifacts.AtomicReapResult)).contents
            result.code, result.status, result.complete = os.CLD_EXITED, 0, 1
            return 0

        def close(pointer):
            pointer[0] = -1
            closed.set()
            return 0

        owner = artifacts.AtomicDescriptorSlots(MagicMock(), close, reap)
        owner._slots[10] = 50
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg")):
            reaping = threading.Thread(target=lambda: owner.reap(1, expected_pidfd=50))
            closing = threading.Thread(target=lambda: owner.close(10))
            reaping.start()
            self.assertTrue(entered.wait(1))
            closing.start()
            self.assertFalse(closed.wait(0.05))
            release.set()
            reaping.join(timeout=1)
            closing.join(timeout=1)
            self.assertFalse(reaping.is_alive())
            self.assertFalse(closing.is_alive())
            self.assertTrue(closed.is_set())

    def test_partial_nonblocking_control_write_fails_without_replaying_bytes(self) -> None:
        owner = artifacts.AtomicDescriptorSlots(MagicMock(), MagicMock())
        owner._slots[1] = 70
        stream = artifacts.SlotAtomicPipe(owner, 1)
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg"),
              patch.object(artifacts.fcntl, "fcntl", return_value=os.O_NONBLOCK),
              patch.object(artifacts.os, "write", side_effect=[2, BlockingIOError()]) as write):
            with self.assertRaises(BlockingIOError):
                stream.write(b"launch\n")
        self.assertEqual([call.args[1] for call in write.call_args_list], [b"launch\n", b"unch\n"])
        self.assertEqual(owner.descriptor(1), 70)

    def test_slot_wait_serializes_concurrent_close(self) -> None:
        owner = artifacts.AtomicDescriptorSlots(MagicMock(), MagicMock(return_value=0))
        owner._slots[2] = 70
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()

        def select_ready(*_args):
            entered.set()
            if not release.wait(1):
                raise RuntimeError("slot wait was not released")
            return ([70], [], [])

        def close():
            owner.close(2)
            closed.set()

        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg"),
              patch.object(artifacts.select, "select", side_effect=select_ready)):
            waiting = threading.Thread(target=lambda: owner.wait_readable((2,), 0.05))
            closing = threading.Thread(target=close)
            waiting.start()
            self.assertTrue(entered.wait(1))
            closing.start()
            self.assertFalse(closed.wait(0.05))
            release.set()
            waiting.join(timeout=1)
            closing.join(timeout=1)
            self.assertFalse(waiting.is_alive())
            self.assertFalse(closing.is_alive())
            self.assertTrue(closed.is_set())

    def test_transient_release_poll_failure_withholds_plan_receipt_after_source_recovers(self) -> None:
        state = {"fail_next": False}
        slots = artifacts.AtomicDescriptorSlots(MagicMock(), MagicMock())
        slots._ready = True
        slots._group_capable = True

        def read_signal():
            if state["fail_next"]:
                state["fail_next"] = False
                raise RuntimeError("temporary cancellation source failure")
            return None

        def execute(_ordinal, _entry):
            state["fail_next"] = True
            slots.release(artifacts._coerce_host_signal_queue(None, read_signal))
            raise AssertionError("release must fail")

        with (
            tempfile.TemporaryDirectory() as runner_temp,
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
            patch.object(slots, "_nonblocking_fd", return_value=17),
        ):
            with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                artifacts.run_script_plan_with_receipts(ENTRIES, IDENTITY, False, Path(runner_temp), execute,
                                                        cancellation_signal=read_signal)
            self.assertTrue(caught.exception.__cause__.cancellation_evidence_unavailable)
            self.assertFalse((caught.exception.receipt_directory / "plan.json").exists())
        self.assertFalse(state["fail_next"])
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_bubblewrap_launch_passes_only_owned_status_writer(self) -> None:
        descriptors = iter(range(10, 22))
        observed = {}

        def create(pointer):
            pointer[0], pointer[1] = next(descriptors), next(descriptors)
            return 0

        def native(pidfd_pointer, _stdin, _stdout, _ready, _gate, _error,
                   inherited, count, _cwd, argv, _envp):
            pidfd_pointer[0] = 50
            observed["inherited"] = [inherited[index] for index in range(count)]
            observed["argv"] = [argv[index] for index in range(4)]
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, MagicMock(return_value=0))
        with (
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            for index in (0, 2, 4, 6, 8, 11):
                slots.open_pair(index)
            self.assertEqual(slots.launch_bubblewrap(native, ["/usr/bin/true", "argument"], {}, (), None), 0)
        self.assertEqual(observed["inherited"], [21])
        self.assertEqual(observed["argv"], [b"/usr/bin/true", b"--json-status-fd", b"21", b"argument"])
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_cancellation_or_unavailable_source_prevents_release(self) -> None:
        for failure in (signal.SIGTERM, RuntimeError("source unavailable")):
            with self.subTest(failure=failure):
                descriptors = iter(range(10, 20))

                def create(pointer):
                    pointer[0], pointer[1] = next(descriptors), next(descriptors)
                    return 0

                def native(pidfd_pointer, *_arguments):
                    pidfd_pointer[0] = 50
                    return 0

                slots = artifacts.AtomicDescriptorSlots(create, MagicMock(return_value=0))
                host_signals = MagicMock()
                if isinstance(failure, Exception):
                    host_signals.poll.side_effect = failure
                    expected = artifacts.IntegrityError
                else:
                    host_signals.poll.return_value = failure
                    expected = artifacts.ScriptPlanInterrupted
                with (
                    patch.object(artifacts.subprocess, "Popen"),
                    patch.object(artifacts.signal, "pidfd_send_signal"),
                    patch.object(artifacts.os, "killpg"),
                    patch.object(artifacts.os, "set_blocking"),
                    patch.object(artifacts.fcntl, "fcntl", return_value=os.O_NONBLOCK),
                    patch.object(artifacts.os, "read", return_value=b"R"),
                    patch.object(artifacts.os, "write") as write,
                ):
                    for index in (0, 2, 4, 6, 8):
                        slots.open_pair(index)
                    slots.launch(native, (), None, None, None)
                    slots.set_nonblocking(4)
                    slots.read_ready()
                    self.assertTrue(slots.signal_group(0))
                    slots.set_nonblocking(7)
                    with self.assertRaises(expected) as caught:
                        slots.release(host_signals)
                    if isinstance(failure, Exception):
                        self.assertTrue(caught.exception.cancellation_evidence_unavailable)
                write.assert_not_called()

    def test_failed_release_is_treated_as_potentially_started(self) -> None:
        descriptors = iter(range(10, 20))

        def create(pointer):
            pointer[0], pointer[1] = next(descriptors), next(descriptors)
            return 0

        def close(pointer):
            pointer[0] = -1
            return 0

        def native(pidfd_pointer, *_arguments):
            pidfd_pointer[0] = 50
            return 0

        def reap(_pidfd, result_pointer):
            result = ctypes.cast(result_pointer, ctypes.POINTER(artifacts.AtomicReapResult)).contents
            result.code, result.status, result.complete = os.CLD_EXITED, 0, 1
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, close, reap)
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal", side_effect=[None, ProcessLookupError]),
            patch.object(artifacts.os, "killpg"),
            patch.object(artifacts.os, "set_blocking"),
            patch.object(artifacts.fcntl, "fcntl", return_value=os.O_NONBLOCK),
            patch.object(artifacts.os, "read", return_value=b"R"),
            patch.object(artifacts.os, "write", side_effect=BlockingIOError),
        ):
            for index in (0, 2, 4, 6, 8):
                slots.open_pair(index)
            slots.launch(native, (), None, None, None)
            slots.set_nonblocking(4)
            slots.read_ready()
            self.assertTrue(slots.signal_group(0))
            slots.set_nonblocking(7)
            with self.assertRaises(BlockingIOError):
                slots.release(artifacts.HostSignalQueue())
            self.assertEqual(slots.reap(1), 0)
            with self.assertRaisesRegex(artifacts.IntegrityError, "cleanup is not verified"):
                slots.close(10)
            self.assertFalse(slots.signal_group(0))
            slots.close_all()

    def test_unverified_group_keeps_pidfd_owned_until_later_verification(self) -> None:
        descriptors = iter(range(10, 20))
        closed = []

        def create(pointer):
            pointer[0], pointer[1] = next(descriptors), next(descriptors)
            return 0

        def close(pointer):
            closed.append(pointer[0])
            pointer[0] = -1
            return 0

        def native(pidfd_pointer, *_arguments):
            pidfd_pointer[0] = 50
            return 0

        def reap(_pidfd, result_pointer):
            result = ctypes.cast(result_pointer, ctypes.POINTER(artifacts.AtomicReapResult)).contents
            result.code, result.status, result.complete = os.CLD_EXITED, 0, 1
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, close, reap)
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal", side_effect=[None, None, ProcessLookupError]),
            patch.object(artifacts.os, "killpg"),
            patch.object(artifacts.os, "set_blocking"),
            patch.object(artifacts.fcntl, "fcntl", return_value=os.O_NONBLOCK),
            patch.object(artifacts.os, "read", return_value=b"R"),
            patch.object(artifacts.os, "write", return_value=1),
        ):
            for index in (0, 2, 4, 6, 8):
                slots.open_pair(index)
            slots.launch(native, (), None, None, None)
            slots.set_nonblocking(4)
            slots.read_ready()
            self.assertTrue(slots.signal_group(0))
            slots.set_nonblocking(7)
            slots.release(artifacts.HostSignalQueue())
            self.assertEqual(slots.reap(1), 0)
            self.assertTrue(slots.signal_group(0))
            with self.assertRaisesRegex(artifacts.IntegrityError, "descriptor cleanup failed"):
                slots.close_all()
            self.assertNotIn(50, closed)
            self.assertFalse(slots.signal_group(0))
            slots.close_all()
        self.assertEqual(closed.count(50), 1)

    def test_group_signal_is_forbidden_before_session_readiness(self) -> None:
        descriptors = iter(range(10, 20))

        def create(pointer):
            pointer[0], pointer[1] = next(descriptors), next(descriptors)
            return 0

        def native(pidfd_pointer, *_arguments):
            pidfd_pointer[0] = 50
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, MagicMock(return_value=0))
        with (
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            for index in (0, 2, 4, 6, 8):
                slots.open_pair(index)
            slots.launch(native, (), None, None, None)
            with self.assertRaisesRegex(artifacts.IntegrityError, "group is not ready"):
                slots.signal_group(signal.SIGKILL)
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_unsupported_group_probe_never_releases_the_launcher(self) -> None:
        descriptors = iter(range(10, 20))

        def create(pointer):
            pointer[0], pointer[1] = next(descriptors), next(descriptors)
            return 0

        def native(pidfd_pointer, *_arguments):
            pidfd_pointer[0] = 50
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, MagicMock(return_value=0))
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal", side_effect=OSError(errno.EINVAL, "unsupported")),
            patch.object(artifacts.os, "killpg") as kill_group,
            patch.object(artifacts.os, "set_blocking"),
            patch.object(artifacts.fcntl, "fcntl", return_value=os.O_NONBLOCK),
            patch.object(artifacts.os, "read", return_value=b"R"),
            patch.object(artifacts.os, "write") as write,
        ):
            for index in (0, 2, 4, 6, 8):
                slots.open_pair(index)
            slots.launch(native, (), None, None, None)
            slots.set_nonblocking(4)
            slots.read_ready()
            with self.assertRaisesRegex(artifacts.IntegrityError, "cannot signal"):
                slots.signal_group(0)
            with self.assertRaisesRegex(artifacts.IntegrityError, "release state is invalid"):
                slots.release(artifacts.HostSignalQueue())
        write.assert_not_called()
        kill_group.assert_not_called()

    def test_reaped_unreleased_helper_closes_without_group_signal_support(self) -> None:
        descriptors = iter(range(10, 20))
        closed = []

        def create(pointer):
            pointer[0], pointer[1] = next(descriptors), next(descriptors)
            return 0

        def close(pointer):
            closed.append(pointer[0])
            pointer[0] = -1
            return 0

        def native(pidfd_pointer, *_arguments):
            pidfd_pointer[0] = 50
            return 0

        def reap(_pidfd, result_pointer):
            result = ctypes.cast(result_pointer, ctypes.POINTER(artifacts.AtomicReapResult)).contents
            result.code, result.status, result.complete = os.CLD_KILLED, int(signal.SIGKILL), 1
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, close, reap)
        with (
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            for index in (0, 2, 4, 6, 8):
                slots.open_pair(index)
            slots.launch(native, (), None, None, None)
            slots.close(7)
            self.assertTrue(slots.terminate_leader())
            self.assertEqual(slots.reap(1), -int(signal.SIGKILL))
            slots.close_all()
        send_signal.assert_called_once_with(50, signal.SIGKILL)
        self.assertEqual(set(closed), set(range(10, 20)) | {50})
        popen.assert_not_called()
        kill_group.assert_not_called()

    def test_pipe_read_rejects_a_blocking_descriptor(self) -> None:
        def create(pointer):
            pointer[0], pointer[1] = 10, 11
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, MagicMock(return_value=0))
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal"),
            patch.object(artifacts.os, "killpg"),
            patch.object(artifacts.fcntl, "fcntl", return_value=0),
            patch.object(artifacts.os, "read") as read,
        ):
            slots.open_pair(2)
            with self.assertRaisesRegex(artifacts.IntegrityError, "not nonblocking"):
                slots.read(2, 1)
        read.assert_not_called()

    def test_invalid_group_signals_are_rejected_without_signaling(self) -> None:
        for signum in (True, False, -1, "15", MagicMock(), signal.SIGHUP):
            with self.subTest(signum=signum):
                with (
                    patch.object(artifacts.subprocess, "Popen") as popen,
                    patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
                    patch.object(artifacts.os, "killpg") as kill_group,
                ):
                    with self.assertRaisesRegex(artifacts.IntegrityError, "group signal is invalid"):
                        artifacts._signal_pinned_process_group(50, signum)
                popen.assert_not_called()
                send_signal.assert_not_called()
                kill_group.assert_not_called()

    def test_pidfd_requires_reaping_and_group_verification_and_owner_is_single_use(self) -> None:
        descriptors = iter(range(10, 30))

        def create(pointer):
            pointer[0], pointer[1] = next(descriptors), next(descriptors)
            return 0

        def close(pointer):
            pointer[0] = -1
            return 0

        def launch(pidfd_pointer, *_arguments):
            pidfd_pointer[0] = 50
            return 0

        def reap(_pidfd, result_pointer):
            result = ctypes.cast(result_pointer, ctypes.POINTER(artifacts.AtomicReapResult)).contents
            result.code, result.status, result.complete = os.CLD_EXITED, 0, 1
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, close, reap)
        native = MagicMock(side_effect=launch)
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal", side_effect=[None, ProcessLookupError]),
            patch.object(artifacts.os, "killpg"),
            patch.object(artifacts.os, "set_blocking"),
            patch.object(artifacts.fcntl, "fcntl", return_value=os.O_NONBLOCK),
            patch.object(artifacts.os, "read", return_value=b"R"),
            patch.object(artifacts.os, "write", return_value=1),
        ):
            for index in (0, 2, 4, 6, 8):
                slots.open_pair(index)
            self.assertEqual(slots.launch(native, (), None, None, None), 0)
            slots.set_nonblocking(4)
            slots.read_ready()
            slots.set_nonblocking(7)
            self.assertTrue(slots.signal_group(0))
            slots.release(artifacts.HostSignalQueue())
            with self.assertRaisesRegex(artifacts.IntegrityError, "cleanup is not verified"):
                slots.close(10)
            self.assertEqual(slots.reap(1), 0)
            with self.assertRaisesRegex(artifacts.IntegrityError, "cleanup is not verified"):
                slots.close(10)
            self.assertFalse(slots.signal_group(0))
            slots.close_all()
            for index in (0, 2, 4, 6, 8):
                slots.open_pair(index)
            with self.assertRaisesRegex(artifacts.IntegrityError, "descriptor state is invalid"):
                slots.launch(native, (), None, None, None)
            slots.close_all()
        native.assert_called_once()

    def test_reap_poll_allows_concurrent_group_signal(self) -> None:
        descriptors = iter(range(10, 20))
        sleeping, resume, signaled = threading.Event(), threading.Event(), threading.Event()

        def create(pointer):
            pointer[0], pointer[1] = next(descriptors), next(descriptors)
            return 0

        def native(pidfd_pointer, *_arguments):
            pidfd_pointer[0] = 50
            return 0

        def reap(_pidfd, result_pointer):
            if signaled.is_set():
                result = ctypes.cast(result_pointer, ctypes.POINTER(artifacts.AtomicReapResult)).contents
                result.code, result.status, result.complete = os.CLD_EXITED, 0, 1
            return 0

        def sleep(_seconds):
            sleeping.set()
            if not resume.wait(1):
                raise RuntimeError("reap test timed out")

        slots = artifacts.AtomicDescriptorSlots(create, MagicMock(return_value=0), reap)
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal", side_effect=lambda _fd, signum, *_args: signaled.set() if signum == signal.SIGTERM else None),
            patch.object(artifacts.os, "killpg"),
            patch.object(artifacts.time, "sleep", side_effect=sleep),
            patch.object(artifacts.os, "set_blocking"),
            patch.object(artifacts.fcntl, "fcntl", return_value=os.O_NONBLOCK),
            patch.object(artifacts.os, "read", return_value=b"R"),
        ):
            for index in (0, 2, 4, 6, 8):
                slots.open_pair(index)
            slots.launch(native, (), None, None, None)
            slots.set_nonblocking(4)
            slots.read_ready()
            self.assertTrue(slots.signal_group(0))
            reaping = threading.Thread(target=lambda: slots.reap(5))
            signaling = threading.Thread(target=lambda: slots.signal_group(signal.SIGTERM))
            reaping.start()
            try:
                self.assertTrue(sleeping.wait(1))
                signaling.start()
                self.assertTrue(signaled.wait(1))
            finally:
                resume.set()
                reaping.join(1)
                if signaling.ident is not None:
                    signaling.join(1)
        self.assertFalse(reaping.is_alive())
        self.assertFalse(signaling.is_alive())

    def test_close_all_preserves_interruption_after_attempting_other_slots(self) -> None:
        closed = []

        def create(pointer):
            pointer[0], pointer[1] = 10, 11
            return 0

        def close(pointer):
            descriptor = pointer[0]
            pointer[0] = -1
            closed.append(descriptor)
            if descriptor == 10:
                raise KeyboardInterrupt
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, close)
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal"),
            patch.object(artifacts.os, "killpg"),
        ):
            slots.open_pair(0)
            with self.assertRaises(KeyboardInterrupt):
                slots.close_all()
        self.assertEqual(closed, [10, 11])

    def test_launch_rejects_inherited_parent_pipe_endpoint(self) -> None:
        descriptors = iter(range(10, 20))

        def create(pointer):
            pointer[0], pointer[1] = next(descriptors), next(descriptors)
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, MagicMock(return_value=0))
        native = MagicMock()
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal"),
            patch.object(artifacts.os, "killpg"),
        ):
            for index in (0, 2, 4, 6, 8):
                slots.open_pair(index)
            with self.assertRaisesRegex(artifacts.IntegrityError, "overlap"):
                slots.launch(native, (17,), None, None, None)
        native.assert_not_called()

    def test_reap_outcome_survives_interruption_after_native_return(self) -> None:
        descriptors = iter(range(10, 20))
        closed = []

        def create(pointer):
            pointer[0], pointer[1] = next(descriptors), next(descriptors)
            return 0

        def close(pointer):
            closed.append(pointer[0])
            pointer[0] = -1
            return 0

        def native(pidfd_pointer, *_arguments):
            pidfd_pointer[0] = 50
            return 0

        def reap(_pidfd, result_pointer):
            result = ctypes.cast(result_pointer, ctypes.POINTER(artifacts.AtomicReapResult)).contents
            result.code, result.status, result.complete = os.CLD_EXITED, 7, 1
            raise KeyboardInterrupt

        slots = artifacts.AtomicDescriptorSlots(create, close, reap)
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal", side_effect=ProcessLookupError),
            patch.object(artifacts.os, "killpg"),
        ):
            for index in (0, 2, 4, 6, 8):
                slots.open_pair(index)
            self.assertEqual(slots.launch(native, (), None, None, None), 0)
            with self.assertRaises(KeyboardInterrupt):
                slots.reap(1)
            self.assertEqual(slots.reap(1), 7)
            slots.close_all()
        self.assertEqual(set(closed), set(range(10, 20)) | {50})

    def test_launch_keeps_borrowed_descriptors_locked_against_concurrent_close(self) -> None:
        descriptors = iter(range(10, 20))
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()

        def create(pointer):
            pointer[0], pointer[1] = next(descriptors), next(descriptors)
            return 0

        def close(pointer):
            pointer[0] = -1
            closed.set()
            return 0

        def native(pidfd_pointer, *_arguments):
            entered.set()
            if not release.wait(1):
                raise RuntimeError("launch release timed out")
            pidfd_pointer[0] = 50
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, close)
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal"),
            patch.object(artifacts.os, "killpg"),
        ):
            for index in (0, 2, 4, 6, 8):
                slots.open_pair(index)
            launching = threading.Thread(target=lambda: slots.launch(native, (), None, None, None))
            closing = threading.Thread(target=lambda: slots.close(0))
            launching.start()
            try:
                self.assertTrue(entered.wait(1))
                closing.start()
                self.assertFalse(closed.wait(0.05))
            finally:
                release.set()
                launching.join(1)
                if closing.ident is not None:
                    closing.join(1)
        self.assertFalse(launching.is_alive())
        self.assertFalse(closing.is_alive())
        self.assertTrue(closed.is_set())

    def test_close_eintr_retires_slot_and_is_not_retried(self) -> None:
        closed = []

        def create(pointer):
            pointer[0], pointer[1] = 10, 11
            return 0

        def close(pointer):
            descriptor = pointer[0]
            pointer[0] = -1
            closed.append(descriptor)
            return -errno.EINTR if descriptor == 10 else 0

        slots = artifacts.AtomicDescriptorSlots(create, close)
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal"),
            patch.object(artifacts.os, "killpg"),
        ):
            slots.open_pair(0)
            with self.assertRaisesRegex(artifacts.IntegrityError, "errno 4"):
                slots.close(0)
            slots.close_all()
        self.assertEqual(closed, [10, 11])

    def test_pipe_allocation_failure_leaves_no_owned_descriptors(self) -> None:
        close = MagicMock(return_value=0)
        slots = artifacts.AtomicDescriptorSlots(MagicMock(return_value=-errno.EMFILE), close)
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal"),
            patch.object(artifacts.os, "killpg"),
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "errno 24"):
                slots.open_pair(0)
            slots.close_all()
        close.assert_not_called()

    def test_interruption_after_pipe_creation_leaves_both_slots_owned(self) -> None:
        closed = []

        def create(pointer):
            pointer[0], pointer[1] = 10, 11
            raise KeyboardInterrupt

        def close(pointer):
            closed.append(pointer[0])
            pointer[0] = -1
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, close)
        with (
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaises(KeyboardInterrupt):
                slots.open_pair(0)
            slots.close_all()
        self.assertEqual(closed, [10, 11])
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_interrupted_native_close_is_not_retried_after_slot_retirement(self) -> None:
        closed = []

        def create(pointer):
            pointer[0], pointer[1] = 10, 11
            return 0

        def close(pointer):
            descriptor = pointer[0]
            pointer[0] = -1
            closed.append(descriptor)
            if descriptor == 10:
                raise KeyboardInterrupt
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, close)
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal"),
            patch.object(artifacts.os, "killpg"),
        ):
            slots.open_pair(0)
            with self.assertRaises(KeyboardInterrupt):
                slots.close(0)
            slots.close_all()
        self.assertEqual(closed, [10, 11])

    def test_lease_serializes_other_threads_creating_descriptors(self) -> None:
        entered, created = threading.Event(), threading.Event()

        def create(pointer):
            pointer[0], pointer[1] = 10, 11
            created.set()
            return 0

        slots = artifacts.AtomicDescriptorSlots(create, MagicMock(return_value=0))

        def worker():
            entered.set()
            slots.open_pair(0)

        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal"),
            patch.object(artifacts.os, "killpg"),
        ):
            with slots.lease():
                thread = threading.Thread(target=worker)
                thread.start()
                self.assertTrue(entered.wait(1))
                self.assertFalse(created.wait(0.05))
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(created.is_set())


class AtomicLauncherOwnershipTests(unittest.TestCase):
    def mock_launcher(self):
        launcher = MagicMock()

        def create(pointer):
            pointer[0], pointer[1] = 20, 21
            return 0

        def close(pointer):
            descriptor = pointer[0]
            pointer[0] = -1
            artifacts.os.close(descriptor)
            return 0

        launcher._owned_pipe.side_effect = create
        launcher._owned_close.side_effect = close
        return launcher

    def test_supervisor_owns_status_pipe_when_creation_is_interrupted(self) -> None:
        launcher = self.mock_launcher()
        closed = []

        def create(pointer):
            pointer[0], pointer[1] = 20, 21
            raise KeyboardInterrupt()

        def close(pointer):
            closed.append(pointer[0])
            pointer[0] = -1
            return 0

        launcher._owned_pipe.side_effect = create
        launcher._owned_close.side_effect = close
        with (patch.object(artifacts.subprocess, "Popen") as popen,
              patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
              patch.object(artifacts.os, "killpg") as kill_group,
              patch.object(artifacts, "_spawn_slot_atomic_supervisor") as spawn):
            with self.assertRaises(artifacts.ScriptCandidateTerminal) as caught:
                artifacts.run_supervised_bubblewrap(["/usr/bin/true"], environment={}, pass_fds=(),
                                                   _atomic_launcher=launcher)
        self.assertIsInstance(caught.exception.__cause__, KeyboardInterrupt)
        self.assertEqual(closed, [20, 21])
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()
        spawn.assert_not_called()

    def test_slot_supervisor_reaps_pidfd_published_before_native_launch_failure(self) -> None:
        for failure in (-errno.EIO, KeyboardInterrupt()):
            with self.subTest(failure=failure):
                descriptors = iter(range(20, 32))
                closed = []
                launcher = MagicMock()

                def create(pointer):
                    pointer[0], pointer[1] = next(descriptors), next(descriptors)
                    return 0

                def close(pointer):
                    if pointer[0] >= 0:
                        closed.append(pointer[0])
                    pointer[0] = -1
                    return 0

                def reap(_pidfd, result_pointer):
                    result = ctypes.cast(result_pointer, ctypes.POINTER(artifacts.AtomicReapResult)).contents
                    result.code, result.status, result.complete = os.CLD_EXITED, 0, 1
                    return 0

                def launch(pidfd_pointer, *_arguments):
                    pidfd_pointer[0] = 50
                    if isinstance(failure, BaseException):
                        raise failure
                    return failure

                launcher._owned_pipe.side_effect = create
                launcher._owned_close.side_effect = close
                launcher._owned_reap.side_effect = reap
                launcher.side_effect = launch
                retained_before = set(artifacts._retained_atomic_owners)
                with (patch.object(artifacts.subprocess, "Popen") as popen,
                      patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
                      patch.object(artifacts.os, "killpg") as kill_group,
                      patch.object(artifacts.os, "set_blocking")):
                    with self.assertRaises(artifacts.ScriptCandidateTerminal):
                        artifacts.run_supervised_bubblewrap(["/usr/bin/true"], environment={}, pass_fds=(),
                                                           _atomic_launcher=launcher)
                self.assertEqual(set(closed), set(range(20, 32)) | {50})
                self.assertEqual(artifacts._retained_atomic_owners, retained_before)
                popen.assert_not_called()
                send_signal.assert_not_called()
                kill_group.assert_not_called()

    def test_supervisor_conversion_preserves_unavailable_cancellation_evidence(self) -> None:
        child = artifacts.AtomicPidfdProcess(MagicMock(), MagicMock(), 50, 14, 17, 18)
        failure = artifacts.IntegrityError("cancellation evidence unavailable")
        failure.cancellation_evidence_unavailable = True
        with (
            patch.object(artifacts.os, "pipe", return_value=(20, 21)),
            patch.object(artifacts.os, "set_blocking"),
            patch.object(artifacts.os, "close"),
            patch.object(artifacts, "_spawn_slot_atomic_supervisor", return_value=child),
            patch.object(artifacts, "_await_atomic_launcher_ready", side_effect=failure),
            patch.object(artifacts, "_reap_pinned_supervisor"),
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaises(artifacts.ScriptCandidateTerminal) as caught:
                artifacts.run_supervised_bubblewrap(["/usr/bin/true"], environment={}, pass_fds=(), _atomic_launcher=self.mock_launcher())
        self.assertTrue(caught.exception.cancellation_evidence_unavailable)
        self.assertIs(caught.exception.__cause__, failure)
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_interruption_immediately_after_publication_leaves_resources_with_owner(self) -> None:
        class PublishThenInterrupt(artifacts.AtomicLaunchOwner):
            def __setattr__(self, name, value):
                object.__setattr__(self, name, value)
                if name == "child" and value is not None:
                    raise KeyboardInterrupt

        owner = PublishThenInterrupt()
        def native(pidfd_pointer, *_arguments):
            ctypes.cast(pidfd_pointer, ctypes.POINTER(ctypes.c_int)).contents.value = 50
            return 0

        with (
            patch.object(artifacts.os, "pipe2", side_effect=[(10, 11), (12, 13), (14, 15), (16, 17), (18, 19)]),
            patch.object(artifacts.os, "close") as close,
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
            patch.object(artifacts, "_reap_pinned_supervisor") as reap,
        ):
            with self.assertRaises(KeyboardInterrupt):
                artifacts._spawn_atomic_supervisor(["/usr/bin/true"], {}, (), None, native, owner)
        self.assertIsNotNone(owner.child)
        self.assertEqual((owner.child.pidfd, owner.child.ready_fd, owner.child.gate_fd, owner.child.error_fd), (50, 14, 17, 18))
        self.assertEqual((owner.child.stdin.fileno(), owner.child.stdout.fileno()), (11, 12))
        self.assertEqual({call.args[0] for call in close.call_args_list}, {10, 13, 15, 16, 19})
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()
        reap.assert_not_called()

    def test_interruption_during_return_handoff_reaps_published_child(self) -> None:
        child = artifacts.AtomicPidfdProcess(MagicMock(), MagicMock(), 50, 14, 17, 18)

        def interrupted_spawn(*args):
            args[6].child = child
            raise KeyboardInterrupt

        with (
            patch.object(artifacts.os, "pipe", return_value=(20, 21)),
            patch.object(artifacts.os, "set_blocking"),
            patch.object(artifacts.os, "close") as close,
            patch.object(artifacts, "_spawn_slot_atomic_supervisor", side_effect=interrupted_spawn),
            patch.object(artifacts, "_reap_pinned_supervisor") as reap,
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaises(artifacts.ScriptCandidateTerminal):
                artifacts.run_supervised_bubblewrap(["/usr/bin/true"], environment={}, pass_fds=(), _atomic_launcher=self.mock_launcher())
        self.assertEqual(reap.call_args.args[1:], (50, 1))
        self.assertIn((17,), [call.args for call in close.call_args_list])
        self.assertIn((50,), [call.args for call in close.call_args_list])
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_pre_release_close_failure_still_closes_gate_and_reaps(self) -> None:
        stdin, stdout = MagicMock(), MagicMock()
        stdin.close.side_effect = [OSError(errno.EIO, "close failure"), None]
        child = artifacts.AtomicPidfdProcess(stdin, stdout, 50, 14, 17, 18)
        with (
            patch.object(artifacts.os, "pipe", return_value=(20, 21)),
            patch.object(artifacts.os, "set_blocking"),
            patch.object(artifacts.os, "close") as close,
            patch.object(artifacts, "_spawn_slot_atomic_supervisor", return_value=child),
            patch.object(artifacts, "_await_atomic_launcher_ready", side_effect=artifacts.ScriptPlanInterrupted(signal.SIGTERM)),
            patch.object(artifacts, "_reap_pinned_supervisor") as reap,
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "unreleased trusted launcher cleanup failed") as caught:
                artifacts.run_supervised_bubblewrap(["/usr/bin/true"], environment={}, pass_fds=(), _atomic_launcher=self.mock_launcher())
        self.assertEqual(caught.exception.host_cancellation_signal, signal.SIGTERM)
        self.assertEqual(caught.exception.cleanup_status, "unverified")
        self.assertTrue(caught.exception.infrastructure_failure)
        self.assertIn((17,), [call.args for call in close.call_args_list])
        self.assertEqual(reap.call_args.args[1:], (50, 1))
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_interruption_while_publishing_atomic_pidfd_still_reaps(self) -> None:
        class InterruptedPidfd(artifacts.AtomicPidfdProcess):
            first_read = True

            def __getattribute__(self, name):
                if name == "pidfd" and object.__getattribute__(self, "first_read"):
                    object.__setattr__(self, "first_read", False)
                    raise KeyboardInterrupt
                return super().__getattribute__(name)

        child = InterruptedPidfd(MagicMock(), MagicMock(), 50, 14, 17, 18)
        with (
            patch.object(artifacts.os, "pipe", return_value=(20, 21)),
            patch.object(artifacts.os, "set_blocking"),
            patch.object(artifacts.os, "close") as close,
            patch.object(artifacts, "_spawn_slot_atomic_supervisor", return_value=child),
            patch.object(artifacts, "_reap_pinned_supervisor") as reap,
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaises(artifacts.ScriptCandidateTerminal):
                artifacts.run_supervised_bubblewrap(["/usr/bin/true"], environment={}, pass_fds=(), _atomic_launcher=self.mock_launcher())
        self.assertEqual(reap.call_args.args[1:], (50, 1))
        self.assertIn((50,), [call.args for call in close.call_args_list])
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_readiness_rejects_simultaneous_error_channel_eof(self) -> None:
        child = artifacts.AtomicPidfdProcess(None, None, 50, 14, 17, 18)
        host_signals = MagicMock()
        host_signals.poll.return_value = None
        with (
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "read", return_value=b"") as read,
            patch.object(artifacts.select, "select", return_value=([14, 18], [], [])),
            patch.object(artifacts.time, "monotonic", return_value=1),
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "exited before readiness"):
                artifacts._await_atomic_launcher_ready(child, host_signals, 5)
        read.assert_called_once_with(18, 9)
        popen.assert_not_called()
        send_signal.assert_not_called()

    def test_readiness_rejects_extra_bytes(self) -> None:
        child = artifacts.AtomicPidfdProcess(None, None, 50, 14, 17, 18)
        host_signals = MagicMock()
        host_signals.poll.return_value = None
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal"),
            patch.object(artifacts.os, "read", return_value=b"RR"),
            patch.object(artifacts.select, "select", return_value=([14], [], [])),
            patch.object(artifacts.time, "monotonic", return_value=1),
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "readiness frame is invalid"):
                artifacts._await_atomic_launcher_ready(child, host_signals, 5)

    def test_cancellation_precedes_waiting_for_launcher_readiness(self) -> None:
        child = artifacts.AtomicPidfdProcess(None, None, 50, 14, 17, 18)
        host_signals = MagicMock()
        host_signals.poll.return_value = signal.SIGTERM
        with (
            patch.object(artifacts.subprocess, "Popen"),
            patch.object(artifacts.signal, "pidfd_send_signal"),
            patch.object(artifacts.select, "select") as select_call,
        ):
            with self.assertRaises(artifacts.ScriptPlanInterrupted):
                artifacts._await_atomic_launcher_ready(child, host_signals, 5)
        select_call.assert_not_called()

    def test_exec_error_is_reported_only_after_complete_error_frame(self) -> None:
        child = artifacts.AtomicPidfdProcess(None, None, 50, 14, 17, 18)
        host_signals = MagicMock()
        host_signals.poll.return_value = None
        frame = struct.pack("@ii", 7, errno.ENOENT)
        with (
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "read", side_effect=[frame[:3], frame[3:], b""]),
            patch.object(artifacts.select, "select", return_value=([18], [], [])),
            patch.object(artifacts.time, "monotonic", return_value=1),
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "stage 7: errno 2"):
                artifacts._await_atomic_launcher_error_channel(child, host_signals, 5)
        popen.assert_not_called()
        send_signal.assert_not_called()

    def test_truncated_exec_error_frame_is_rejected(self) -> None:
        child = artifacts.AtomicPidfdProcess(None, None, 50, 14, 17, 18)
        host_signals = MagicMock()
        host_signals.poll.return_value = None
        with (
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "read", side_effect=[b"bad", b""]),
            patch.object(artifacts.select, "select", return_value=([18], [], [])),
            patch.object(artifacts.time, "monotonic", return_value=1),
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "error frame is invalid"):
                artifacts._await_atomic_launcher_error_channel(child, host_signals, 5)
        popen.assert_not_called()
        send_signal.assert_not_called()

    def test_pipe_wrapper_setup_failure_closes_endpoints_and_reaps_child(self) -> None:
        pipes = [(10, 11), (12, 13), (14, 15), (16, 17), (18, 19)]
        input_stream = MagicMock()

        def native(pidfd_pointer, *_arguments):
            ctypes.cast(pidfd_pointer, ctypes.POINTER(ctypes.c_int)).contents.value = 50
            return 0

        with (
            patch.object(artifacts.os, "pipe2", side_effect=pipes),
            patch.object(artifacts.os, "close") as close,
            patch.object(artifacts, "AtomicPipe", side_effect=[input_stream, OSError(errno.EMFILE, "limit")]),
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
            patch.object(artifacts, "_reap_pinned_supervisor") as reap,
        ):
            with self.assertRaises(OSError) as raised:
                artifacts._spawn_atomic_supervisor(["/usr/bin/true"], {}, (), None, native)
        self.assertEqual((raised.exception.errno, raised.exception.strerror), (errno.EMFILE, "limit"))
        input_stream.close.assert_not_called()
        send_signal.assert_called_once_with(50, signal.SIGKILL)
        self.assertEqual(reap.call_args.args[1:], (50, 5))
        self.assertEqual({call.args[0] for call in close.call_args_list}, set(range(10, 20)) | {50})
        popen.assert_not_called()
        kill_group.assert_not_called()

    def test_interrupted_signal_during_failed_launch_still_reaps_and_preserves_failure(self) -> None:
        pipes = [(10, 11), (12, 13), (14, 15), (16, 17), (18, 19)]

        def native(pidfd_pointer, *_arguments):
            ctypes.cast(pidfd_pointer, ctypes.POINTER(ctypes.c_int)).contents.value = 50
            raise RuntimeError("injected native failure")

        with (
            patch.object(artifacts.os, "pipe2", side_effect=pipes),
            patch.object(artifacts.os, "close") as close,
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal", side_effect=KeyboardInterrupt),
            patch.object(artifacts.os, "killpg") as kill_group,
            patch.object(artifacts, "_reap_pinned_supervisor") as reap,
        ):
            with self.assertRaisesRegex(RuntimeError, "injected native failure") as caught:
                artifacts._spawn_atomic_supervisor(["/usr/bin/true"], {}, (), None, native)
        self.assertTrue(any("KeyboardInterrupt" in note for note in caught.exception.__notes__))
        self.assertEqual(reap.call_args.args[1:], (50, 5))
        self.assertEqual({call.args[0] for call in close.call_args_list}, set(range(10, 20)) | {50})
        popen.assert_not_called()
        kill_group.assert_not_called()

    def test_partial_pipe_allocation_is_closed_without_starting_a_process(self) -> None:
        with (
            patch.object(artifacts.os, "pipe2", side_effect=[(10, 11), OSError(errno.EMFILE, "limit")]),
            patch.object(artifacts.os, "close") as close,
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            native = MagicMock()
            with self.assertRaises(OSError) as raised:
                artifacts._spawn_atomic_supervisor(["/usr/bin/true"], {}, (), None, native)
        self.assertEqual((raised.exception.errno, raised.exception.strerror), (errno.EMFILE, "limit"))
        self.assertEqual({call.args[0] for call in close.call_args_list}, {10, 11})
        native.assert_not_called()
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_failure_after_clone_terminates_and_reaps_only_the_pinned_child(self) -> None:
        pipes = [(10, 11), (12, 13), (14, 15), (16, 17), (18, 19)]

        def native(pidfd_pointer, *_arguments):
            ctypes.cast(pidfd_pointer, ctypes.POINTER(ctypes.c_int)).contents.value = 50
            return -errno.EIO

        with (
            patch.object(artifacts.os, "pipe2", side_effect=pipes),
            patch.object(artifacts.os, "close") as close,
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
            patch.object(artifacts, "_reap_pinned_supervisor") as reap,
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "errno 5"):
                artifacts._spawn_atomic_supervisor(["/usr/bin/true"], {}, (), None, native)
        send_signal.assert_called_once_with(50, signal.SIGKILL)
        self.assertEqual(reap.call_args.args[1:], (50, 5))
        self.assertEqual({call.args[0] for call in close.call_args_list}, set(range(10, 20)) | {50})
        self.assertEqual(close.call_args_list[0].args, (17,))
        popen.assert_not_called()
        kill_group.assert_not_called()

    def test_success_transfers_only_parent_endpoints_to_the_child_wrapper(self) -> None:
        pipes = [(10, 11), (12, 13), (14, 15), (16, 17), (18, 19)]
        def native(pidfd_pointer, *_arguments):
            ctypes.cast(pidfd_pointer, ctypes.POINTER(ctypes.c_int)).contents.value = 50
            return 0

        with (
            patch.object(artifacts.os, "pipe2", side_effect=pipes),
            patch.object(artifacts.os, "close") as close,
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            child = artifacts._spawn_atomic_supervisor(["/usr/bin/true"], {}, (), None, native)
        self.assertEqual((child.stdin.fileno(), child.stdout.fileno()), (11, 12))
        self.assertEqual((child.pidfd, child.ready_fd, child.gate_fd, child.error_fd), (50, 14, 17, 18))
        self.assertEqual({call.args[0] for call in close.call_args_list}, {10, 13, 15, 16, 19})
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()


def _cause_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    while error is not None and error not in chain:
        chain.append(error)
        error = error.__cause__ or error.__context__
    return chain


class SupervisedProcessGroupSafetyTests(unittest.TestCase):
    def test_failed_launcher_authentication_never_waits_on_a_numeric_pid(self) -> None:
        process = MagicMock()
        process.pid = 123456789
        process.returncode = None
        with (
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts, "_supervised_process_group_id", return_value=process.pid),
            patch.object(artifacts.os, "pidfd_open", side_effect=lambda _pid: os.open("/dev/null", os.O_RDONLY)),
            patch.object(artifacts, "_authenticate_bubblewrap_gate", side_effect=artifacts.IntegrityError("lost gate")),
            patch.object(artifacts.os, "waitid",
                         return_value=SimpleNamespace(si_code=os.CLD_EXITED, si_status=0)) as wait_identity,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaises(artifacts.ScriptCandidateTerminal):
                artifacts.run_supervised_bubblewrap(["/usr/bin/true"], environment={}, pass_fds=())
        process.wait.assert_not_called()
        self.assertEqual([call.args[0] for call in wait_identity.call_args_list], [os.P_PIDFD])
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_unreleased_launcher_ignoring_its_closed_gate_is_killed_through_its_pidfd(self) -> None:
        process = MagicMock()
        process.pid = 123456789
        process.returncode = None
        pinned_descriptors: list[int] = []

        def open_pinned(_pid: int) -> int:
            pinned_descriptors.append(os.open("/dev/null", os.O_RDONLY))
            return pinned_descriptors[-1]

        with (
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts, "_supervised_process_group_id", return_value=process.pid),
            patch.object(artifacts.os, "pidfd_open", side_effect=open_pinned),
            patch.object(artifacts, "_authenticate_bubblewrap_gate", side_effect=artifacts.IntegrityError("lost gate")),
            patch.object(artifacts, "_reap_pinned_supervisor",
                         side_effect=[artifacts._SupervisorReapTimeout("still running"), None]) as reap,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaises(artifacts.ScriptCandidateTerminal):
                artifacts.run_supervised_bubblewrap(["/usr/bin/true"], environment={}, pass_fds=())
        send_signal.assert_called_once_with(pinned_descriptors[0], signal.SIGKILL)
        self.assertEqual([call.args[1] for call in reap.call_args_list], [pinned_descriptors[0]] * 2)
        process.wait.assert_not_called()
        kill_group.assert_not_called()

    def test_failed_status_write_end_close_is_never_retried_on_a_reusable_number(self) -> None:
        process = MagicMock()
        process.pid = 123456789
        process.returncode = None
        process.stdin.fileno.return_value = 99
        status_read_fd, status_write_fd = os.pipe()
        real_close = os.close
        close_calls: list[int] = []

        def close(descriptor: int) -> None:
            close_calls.append(descriptor)
            if descriptor == status_write_fd and close_calls.count(descriptor) == 1:
                real_close(descriptor)
                raise OSError(errno.EIO, "status write end close failed")
            real_close(descriptor)

        with (
            patch.object(artifacts.os, "pipe", return_value=(status_read_fd, status_write_fd)),
            patch.object(artifacts.os, "close", side_effect=close),
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts, "_supervised_process_group_id", return_value=process.pid),
            patch.object(artifacts.os, "pidfd_open", side_effect=lambda _pid: os.open("/dev/null", os.O_RDONLY)),
            patch.object(artifacts, "_authenticate_bubblewrap_gate"),
            patch.object(artifacts, "_signal_pinned_process_group", return_value=True),
            patch.object(artifacts.os, "write", return_value=1),
            patch.object(artifacts, "_reap_pinned_supervisor"),
            patch.object(artifacts, "extinguish_supervised_process_group"),
        ):
            with self.assertRaises(artifacts.IntegrityError) as raised:
                artifacts.run_supervised_bubblewrap(["/usr/bin/true"], environment={}, pass_fds=())
        self.assertIn(errno.EIO, [getattr(error, "errno", None) for error in _cause_chain(raised.exception)])
        self.assertEqual(close_calls.count(status_write_fd), 1)

    def test_unreleased_launcher_kill_failure_is_reported_as_unexited(self) -> None:
        process = MagicMock()
        process.pid = 123456789
        process.returncode = None
        with (
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts, "_supervised_process_group_id", return_value=process.pid),
            patch.object(artifacts.os, "pidfd_open", side_effect=lambda _pid: os.open("/dev/null", os.O_RDONLY)),
            patch.object(artifacts, "_authenticate_bubblewrap_gate", side_effect=artifacts.IntegrityError("lost gate")),
            patch.object(artifacts, "_reap_pinned_supervisor",
                         side_effect=artifacts._SupervisorReapTimeout("still running")) as reap,
            patch.object(artifacts.signal, "pidfd_send_signal", side_effect=PermissionError(errno.EPERM, "denied")),
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaises(artifacts.IntegrityError) as raised:
                artifacts.run_supervised_bubblewrap(["/usr/bin/true"], environment={}, pass_fds=())
        chain = _cause_chain(raised.exception)
        self.assertTrue(any("unreleased trusted launcher did not exit" in str(error) for error in chain))
        self.assertIn(errno.EPERM, [getattr(error, "errno", None) for error in chain])
        self.assertEqual(reap.call_count, 1)
        process.wait.assert_not_called()
        kill_group.assert_not_called()

    def test_unreleased_launcher_surviving_sigkill_is_reported_as_unexited(self) -> None:
        process = MagicMock()
        process.pid = 123456789
        process.returncode = None
        with (
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts, "_supervised_process_group_id", return_value=process.pid),
            patch.object(artifacts.os, "pidfd_open", side_effect=lambda _pid: os.open("/dev/null", os.O_RDONLY)),
            patch.object(artifacts, "_authenticate_bubblewrap_gate", side_effect=artifacts.IntegrityError("lost gate")),
            patch.object(artifacts, "_reap_pinned_supervisor",
                         side_effect=artifacts._SupervisorReapTimeout("still running")),
            patch.object(artifacts.signal, "pidfd_send_signal"),
            patch.object(artifacts.os, "killpg"),
        ):
            with self.assertRaises(artifacts.IntegrityError) as raised:
                artifacts.run_supervised_bubblewrap(["/usr/bin/true"], environment={}, pass_fds=())
        self.assertIn("unreleased trusted launcher did not exit", str(raised.exception))
        process.wait.assert_not_called()

    def test_lost_pidfd_reaping_ownership_does_not_signal_a_group(self) -> None:
        process = MagicMock()
        process.returncode = None
        with (
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts.os, "waitid", side_effect=ChildProcessError) as waitid,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "reaping ownership was lost"):
                artifacts.extinguish_supervised_process_group(process, 44)
        waitid.assert_called_once_with(os.P_PIDFD, 44, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def test_gate_authenticates_a_fresh_challenge_against_the_pinned_child(self) -> None:
        process = MagicMock()
        process.stdin.fileno.return_value = 11
        process.stdout.fileno.return_value = 12
        host_signals = MagicMock()
        host_signals.poll.return_value = None
        challenge = b"x" * 32
        with (
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts.os, "urandom", return_value=challenge),
            patch.object(artifacts.os, "write", return_value=32) as write,
            patch.object(artifacts.os, "read", return_value=challenge),
            patch.object(artifacts.select, "select", return_value=([12], [], [])),
            patch.object(artifacts.os, "waitid", return_value=None) as waitid,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.time, "monotonic", return_value=1),
        ):
            artifacts._authenticate_bubblewrap_gate(process, 44, host_signals, 5)
        write.assert_called_once_with(11, challenge)
        waitid.assert_called_once_with(os.P_PIDFD, 44, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        send_signal.assert_not_called()

    def test_gate_rejects_an_incorrect_challenge_before_release(self) -> None:
        process = MagicMock()
        process.stdin.fileno.return_value = 11
        process.stdout.fileno.return_value = 12
        host_signals = MagicMock()
        host_signals.poll.return_value = None
        with (
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts.os, "urandom", return_value=b"x" * 32),
            patch.object(artifacts.os, "write", return_value=32),
            patch.object(artifacts.os, "read", return_value=b"y" * 32),
            patch.object(artifacts.select, "select", return_value=([12], [], [])),
            patch.object(artifacts.os, "waitid") as waitid,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.time, "monotonic", return_value=1),
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "authentication failed"):
                artifacts._authenticate_bubblewrap_gate(process, 44, host_signals, 5)
        waitid.assert_not_called()
        send_signal.assert_not_called()

    def test_unsupported_pidfd_group_signal_fails_closed(self) -> None:
        with (
            patch.object(artifacts.subprocess, "Popen") as launch,
            patch.object(artifacts.signal, "pidfd_send_signal", side_effect=OSError(errno.EINVAL, "unsupported")),
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, r"cannot signal.*Linux 6\.9 or later"):
                artifacts._signal_pinned_process_group(44, 0)
        launch.assert_not_called()
        kill_group.assert_not_called()

    def test_other_pidfd_group_signal_errors_do_not_blame_the_kernel(self) -> None:
        with patch.object(artifacts.signal, "pidfd_send_signal", side_effect=OSError(errno.EBADF, "closed")):
            with self.assertRaises(artifacts.IntegrityError) as raised:
                artifacts._signal_pinned_process_group(44, 0)
        self.assertNotIn("Linux 6.9", str(raised.exception))

    def test_reaping_during_group_validation_cannot_authorize_a_signal(self) -> None:
        process = MagicMock()
        process.pid = 123456789
        process.returncode = None

        with (
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts.os, "waitid", return_value=None),
            patch.object(artifacts.signal, "pidfd_send_signal", side_effect=ProcessLookupError) as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaises(artifacts.IntegrityError):
                artifacts.extinguish_supervised_process_group(process, 44)
        kill_group.assert_not_called()
        send_signal.assert_called_once_with(44, signal.SIGTERM, None, artifacts.PIDFD_SIGNAL_PROCESS_GROUP)

    def test_group_is_signaled_before_leader_is_reaped(self) -> None:
        process = MagicMock()
        process.pid = 123456789
        process.returncode = None
        exited = MagicMock(si_code=os.CLD_EXITED, si_status=0)
        events = []

        def signal_group(_pidfd, signum, _info, _flags):
            events.append(signum)
            if signum == 0:
                raise ProcessLookupError

        def waitid(_kind, _identity, flags):
            if not flags & os.WNOWAIT:
                events.append("reap")
            return exited

        with (
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts.os, "waitid", side_effect=waitid),
            patch.object(artifacts.signal, "pidfd_send_signal", side_effect=signal_group),
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            artifacts.extinguish_supervised_process_group(process, 44)
        kill_group.assert_not_called()
        self.assertEqual(events, [signal.SIGTERM, signal.SIGKILL, "reap", 0])

    def test_reaped_leader_cannot_authorize_a_reused_process_group(self) -> None:
        process = MagicMock()
        process.pid = 123456789
        process.returncode = 0
        with (
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts.os, "getpgid", return_value=process.pid),
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg", side_effect=ProcessLookupError) as kill_group,
        ):
            with self.assertRaises(artifacts.IntegrityError):
                artifacts.extinguish_supervised_process_group(process, 44)
        kill_group.assert_not_called()
        send_signal.assert_not_called()

    def test_invalid_group_ids_are_rejected_without_signaling(self) -> None:
        for pid in (MagicMock(), True, None, "123", -1, 0, 1, os.getpgrp()):
            with self.subTest(pid=pid):
                process = MagicMock()
                process.pid = pid
                with patch.object(artifacts.os, "killpg") as kill_group:
                    with self.assertRaisesRegex(artifacts.IntegrityError, "process group is invalid"):
                        artifacts._supervised_process_group_id(process)
                kill_group.assert_not_called()

    def test_nonisolated_group_is_rejected_without_signaling(self) -> None:
        process = MagicMock()
        process.pid = 123456789
        process.returncode = None
        with (
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts.os, "waitid", return_value=None),
            patch.object(artifacts.os, "getpgid", return_value=987654321),
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "process group is not isolated"):
                artifacts._supervised_process_group_id(process)
        kill_group.assert_not_called()

    def test_missing_leader_cannot_authorize_group_cleanup(self) -> None:
        process = MagicMock()
        process.pid = 123456789
        process.returncode = None
        with (
            patch.object(artifacts.subprocess, "Popen", return_value=process),
            patch.object(artifacts.os, "waitid", side_effect=ChildProcessError),
            patch.object(artifacts.os, "getpgid", side_effect=ProcessLookupError),
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "reaping ownership was lost"):
                artifacts._supervised_process_group_id(process)
        kill_group.assert_not_called()


def close_failure_popen(original_popen, *, on_close=None, processes=None):
    class CloseFailure:
        def __init__(self, stream):
            self.stream = stream

        def fileno(self):
            return self.stream.fileno()

        def close(self):
            self.stream.close()
            if on_close is not None:
                on_close()
            raise OSError('injected stdout close failure')

    def launch(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        process.stdout = CloseFailure(process.stdout)
        if processes is not None:
            processes.append(process)
        return process

    return launch


class HarnessLaunchGateTests(unittest.TestCase):
    def test_pinned_process_identity_survives_gate_exec(self):
        import compatibility_lane_peer as peer

        read_fd, write_fd = os.pipe()
        try:
            process = subprocess.Popen(
                [sys.executable, '-I', '-c', artifacts.HARNESS_LAUNCH_GATE_ENTRYPOINT,
                 str(read_fd), sys.executable, '-I', '-c',
                 "import os,sys; print(os.getpid(),flush=True); sys.stdin.buffer.read(1)"],
                pass_fds=(read_fd,), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except BaseException:
            os.close(write_fd)
            raise
        finally:
            os.close(read_fd)
        pidfd = proc_fd = -1
        communicated = False
        try:
            pidfd = os.pidfd_open(process.pid)
            proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            os.write(write_fd, b'G')
            os.close(write_fd)
            write_fd = -1
            self.assertTrue(select.select([process.stdout], [], [], 3)[0])
            self.assertEqual(int(process.stdout.readline()), process.pid)
            peer._assert_live(pidfd)
            self.assertEqual(peer._pidfd_pid(proc_fd, pidfd), process.pid)
            process.communicate(input=b'x', timeout=3)
            communicated = True
            self.assertEqual(process.returncode, 0)
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            for fd in (pidfd, proc_fd):
                if fd >= 0:
                    os.close(fd)
            if not communicated:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=3)

    def test_gate_executes_only_after_complete_authorization_and_closes_the_pipe(self):
        for authorization, expected_status in ((b'G', 0), (b'', 125), (b'GX', 125)):
            with self.subTest(authorization=authorization):
                read_fd, write_fd = os.pipe()
                code = (
                    "import os,sys\n"
                    "try: os.fstat(int(sys.argv[1]))\n"
                    "except OSError: print('gate closed')\n"
                    "else: sys.exit(9)\n"
                )
                communicated = False
                try:
                    process = subprocess.Popen(
                        [sys.executable, '-I', '-c', artifacts.HARNESS_LAUNCH_GATE_ENTRYPOINT,
                         str(read_fd), sys.executable, '-I', '-c', code, str(read_fd)],
                        pass_fds=(read_fd,), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
                except BaseException:
                    os.close(write_fd)
                    raise
                finally:
                    os.close(read_fd)
                try:
                    self.assertEqual(select.select([process.stdout], [], [], 0.1)[0], [])
                    if authorization:
                        os.write(write_fd, authorization)
                        if authorization == b'G':
                            self.assertEqual(select.select([process.stdout], [], [], 0.1)[0], [])
                    os.close(write_fd)
                    write_fd = -1
                    stdout, _ = process.communicate(timeout=3)
                    communicated = True
                    self.assertEqual(process.returncode, expected_status)
                    self.assertEqual(stdout, b'gate closed\n' if expected_status == 0 else b'')
                finally:
                    if write_fd >= 0:
                        os.close(write_fd)
                    if not communicated:
                        if process.poll() is None:
                            process.kill()
                        process.communicate(timeout=3)


class EntryReceiptSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runner_temp = self.root / "runner-temp"
        self.runner_temp.mkdir(mode=0o700)

    def test_namespace_event_is_published_before_launch_with_owned_handles(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-namespace-event'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0,
        )
        notifications = queue.Queue(maxsize=1)
        received = []

        def on_event(event):
            if event == 'control-accepted':
                notice = notifications.get_nowait()
                if notice._native_launcher is not None:
                    owner = artifacts.BorrowedSupervisorNamespace(notice._native_launcher)
                    try:
                        notice.borrow_into(owner)
                        with owner.lease() as (namespace_fd, pidfd):
                            self.assertEqual(os.fstat(namespace_fd).st_ino, notice.namespace_inode)
                            os.fstat(pidfd)
                    finally:
                        owner.close()
                else:
                    namespace_fd, pidfd = notice.borrow()
                    try:
                        self.assertEqual(os.fstat(namespace_fd).st_ino, notice.namespace_inode)
                        os.fstat(pidfd)
                    finally:
                        os.close(namespace_fd)
                        os.close(pidfd)
                received.append(notice)

        status = artifacts.run_supervised_bubblewrap(
            [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
            namespace_notifications=notifications, namespace_attempt_id='lane-1',
            execution_timeout_seconds=5, test_hook=on_event,
        )
        self.assertEqual(status.return_code, 0)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].attempt_id, 'lane-1')
        self.assertGreater(received[0].supervisor_pid, 0)
        with self.assertRaises(artifacts.IntegrityError):
            received[0].borrow()

    def test_borrowed_pidfd_reports_supervisor_exit_after_notice_revocation(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-borrowed-namespace'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0,
        )
        notifications = queue.Queue(maxsize=1)
        borrowed = []
        owners = []
        notices = []

        def on_event(event):
            if event == 'control-accepted':
                notice = notifications.get_nowait()
                notices.append(notice)
                if notice._native_launcher is not None:
                    owner = artifacts.BorrowedSupervisorNamespace(notice._native_launcher)
                    owners.append(owner)
                    notice.borrow_into(owner)
                    with owner.lease() as (namespace_fd, pidfd):
                        self.assertEqual(os.fstat(namespace_fd).st_ino, notice.namespace_inode)
                        self.assertFalse(select.select([pidfd], [], [], 0)[0])
                else:
                    borrowed.extend(notice.borrow())
                    self.assertFalse(select.select([borrowed[1]], [], [], 0)[0])

        try:
            artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
                namespace_notifications=notifications, namespace_attempt_id='lane-1',
                execution_timeout_seconds=5, test_hook=on_event,
            )
            if owners:
                self.assertTrue(owners[0].supervisor_exited())
                with self.assertRaisesRegex(artifacts.IntegrityError, "no longer live"):
                    with owners[0].lease():
                        self.fail("exited supervisor became usable")
            else:
                self.assertEqual(len(borrowed), 2)
                self.assertEqual(os.fstat(borrowed[0]).st_ino, notices[0].namespace_inode)
                self.assertTrue(select.select([borrowed[1]], [], [], 0)[0])
            with self.assertRaises(artifacts.IntegrityError):
                notices[0].borrow()
        finally:
            if owners:
                for owner in owners:
                    owner.close()
            else:
                for descriptor in borrowed:
                    os.close(descriptor)

    def test_concurrent_borrow_and_revocation_close_owned_descriptors(self) -> None:
        before = set(os.listdir('/proc/self/fd'))
        namespace_fd = os.open('/proc/self/ns/pid', os.O_RDONLY | os.O_CLOEXEC)
        pidfd = os.pidfd_open(os.getpid())
        notice = artifacts.PinnedSupervisorNamespace(
            'lane-1', os.getpid(), os.fstat(namespace_fd).st_ino, namespace_fd, pidfd,
        )
        barrier = threading.Barrier(2)
        failures = []

        def borrower():
            barrier.wait()
            try:
                try:
                    borrowed_namespace, borrowed_pidfd = notice.borrow()
                except artifacts.IntegrityError:
                    return
                try:
                    if os.fstat(borrowed_namespace).st_ino != notice.namespace_inode:
                        failures.append('wrong namespace')
                finally:
                    os.close(borrowed_namespace)
                    os.close(borrowed_pidfd)
            except Exception as error:
                failures.append(repr(error))

        thread = threading.Thread(target=borrower)
        thread.start()
        barrier.wait()
        self.assertTrue(notice._revoke())
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        with self.assertRaises(artifacts.IntegrityError):
            notice.borrow()
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_full_namespace_queue_prevents_candidate_launch(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-full-queue'
        marker = self.root / 'candidate-launched'
        fake_bubblewrap.write_text(textwrap.dedent(f'''\
            #!/usr/bin/python3
            import json, os, sys
            status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])
            namespace = os.stat('/proc/self/ns/pid').st_ino
            os.write(status_fd, json.dumps({{'child-pid': os.getpid(), 'pid-namespace': namespace}}).encode() + b'\\n')
            print(json.dumps({{'version': 3, 'pid': 1, 'event': 'ready', 'pidNamespace': namespace}}), flush=True)
            challenge = sys.stdin.buffer.readline()
            print(json.dumps({{'version': 3, 'pid': 1, 'event': 'identity-proof', 'pidNamespace': namespace, 'nonce': challenge[10:-1].decode('ascii')}}), flush=True)
            if sys.stdin.buffer.readline() == b'launch\\n':
                open({str(marker)!r}, 'w').close()
        '''), encoding='utf-8')
        fake_bubblewrap.chmod(0o700)
        notifications = queue.Queue(maxsize=1)
        notifications.put_nowait('occupied')
        with self.assertRaisesRegex(artifacts.ScriptCandidateTerminal, 'namespace publication failed') as caught:
            artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
                namespace_notifications=notifications, namespace_attempt_id='lane-1', execution_timeout_seconds=3,
            )
        self.assertFalse(caught.exception.candidate_started)
        self.assertFalse(marker.exists())
        self.assertEqual(notifications.get_nowait(), 'occupied')

    def test_publication_exception_revokes_notice_already_inserted_in_queue(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-publication-exception'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0,
        )
        notifications = queue.Queue(maxsize=1)
        original_put = notifications.put_nowait

        def publish_then_fail(notice):
            original_put(notice)
            raise RuntimeError('injected publication failure')

        with patch.object(notifications, 'put_nowait', side_effect=publish_then_fail):
            with self.assertRaisesRegex(artifacts.ScriptCandidateTerminal, 'injected publication failure'):
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
                    namespace_notifications=notifications, namespace_attempt_id='lane-1',
                    execution_timeout_seconds=5,
                )
        notice = notifications.get_nowait()
        self.assertEqual((notice._namespace_fd, notice._pidfd), (-1, -1))
        with self.assertRaises(artifacts.IntegrityError):
            notice.borrow()

    def test_unread_namespace_notification_does_not_retain_descriptors(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-unread-namespace'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0,
        )
        notifications = queue.Queue(maxsize=1)
        before = set(os.listdir('/proc/self/fd'))
        artifacts.run_supervised_bubblewrap(
            [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
            namespace_notifications=notifications, namespace_attempt_id='lane-1', execution_timeout_seconds=5,
        )
        event = notifications.get_nowait()
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
        with self.assertRaises(artifacts.IntegrityError):
            event.borrow()

    def test_cancellation_after_namespace_publication_revokes_notice_before_launch(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-namespace-cancel'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0,
        )
        notifications = queue.Queue(maxsize=1)
        original_put = notifications.put_nowait
        cancelled = False
        events = []

        def publish_then_cancel(notice):
            nonlocal cancelled
            original_put(notice)
            cancelled = True

        with patch.object(notifications, 'put_nowait', side_effect=publish_then_cancel):
            with self.assertRaises(artifacts.ScriptCandidateTerminal) as caught:
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
                    namespace_notifications=notifications, namespace_attempt_id='lane-1', execution_timeout_seconds=5,
                    cancellation_signal=lambda: signal.SIGTERM if cancelled else None, test_hook=events.append,
                )
        self.assertEqual(caught.exception.host_cancellation_signal, signal.SIGTERM)
        self.assertNotIn('candidate-started', events)
        with self.assertRaises(artifacts.IntegrityError):
            notifications.get_nowait().borrow()

    def test_cancellation_during_slow_namespace_publication_precedes_timeout(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-slow-namespace-cancel'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0,
        )
        notifications = queue.Queue(maxsize=1)
        original_put = notifications.put_nowait
        cancelled = False

        def publish_cancel_and_delay(notice):
            nonlocal cancelled
            original_put(notice)
            cancelled = True
            time.sleep(0.3)

        with patch.object(notifications, 'put_nowait', side_effect=publish_cancel_and_delay):
            with self.assertRaises(artifacts.ScriptPlanInterrupted) as caught:
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
                    namespace_notifications=notifications, namespace_attempt_id='lane-1', execution_timeout_seconds=0.2,
                    cancellation_signal=lambda: signal.SIGTERM if cancelled else None,
                )
        self.assertEqual(caught.exception.signum, signal.SIGTERM)
        self.assertFalse(caught.exception.candidate_started)
        with self.assertRaises(artifacts.IntegrityError):
            notifications.get_nowait().borrow()

    def test_supervisor_deadline_rejects_late_outcome_and_cleans_descendants(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-deadline'
        survivor_pid_file = self.root / 'deadline-pids.json'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0, survivor_pid_file=survivor_pid_file,
        )
        with self.assertRaisesRegex(artifacts.ScriptCandidateTerminal, 'execution deadline') as caught:
            artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'},
                pass_fds=(), execution_timeout_seconds=1.0,
            )
        self.assertEqual(caught.exception.cleanup_status, 'unverified')
        self.assertTrue(caught.exception.infrastructure_failure)
        self.assertTrue(survivor_pid_file.exists())
        for pid in json.loads(survivor_pid_file.read_text(encoding='ascii')).values():
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_supervisor_accepts_clean_completion_before_its_deadline(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-before-deadline'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0,
        )
        status = artifacts.run_supervised_bubblewrap(
            [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'},
            pass_fds=(), execution_timeout_seconds=5,
        )
        self.assertEqual(status.return_code, 0)
        self.assertEqual(status.cleanup, 'complete')

    def test_supervisor_does_not_accept_a_result_after_its_deadline(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-late-acceptance'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0,
        )
        original = artifacts._parse_trusted_candidate_status

        def delayed_parse(*args, **kwargs):
            result = original(*args, **kwargs)
            time.sleep(1.1)
            return result

        with patch.object(artifacts, '_parse_trusted_candidate_status', side_effect=delayed_parse):
            with self.assertRaisesRegex(artifacts.ScriptCandidateTerminal, 'execution deadline'):
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'},
                    pass_fds=(), execution_timeout_seconds=1.0,
                )

    def test_supervisor_rejects_invalid_deadlines_before_launch(self) -> None:
        for timeout in [0, -1, True, float('nan'), float('inf'), 10**1000, '1']:
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(artifacts.IntegrityError, 'execution deadline is invalid'):
                    artifacts.run_supervised_bubblewrap(
                        ['/not/a/launcher'], environment={}, pass_fds=(), execution_timeout_seconds=timeout,
                    )

    def test_host_cancellation_before_acceptance_takes_precedence_over_deadline(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-cancel-at-cutoff'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0,
        )
        original = artifacts._parse_trusted_candidate_status
        cancelled = False

        def delayed_parse(*args, **kwargs):
            nonlocal cancelled
            result = original(*args, **kwargs)
            cancelled = True
            time.sleep(1.1)
            return result

        with patch.object(artifacts, '_parse_trusted_candidate_status', side_effect=delayed_parse):
            with self.assertRaises(artifacts.ScriptPlanInterrupted) as caught:
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
                    execution_timeout_seconds=1.0,
                    cancellation_signal=lambda: signal.SIGTERM if cancelled else None,
                )
        self.assertEqual(caught.exception.signum, signal.SIGTERM)

    def test_supervisor_rejects_success_when_cleanup_finishes_after_deadline(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-late-cleanup'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0,
        )
        original_parse = artifacts._parse_trusted_candidate_status
        original_close = os.close
        parsed = False
        delayed = False

        def mark_parsed(*args, **kwargs):
            nonlocal parsed
            result = original_parse(*args, **kwargs)
            parsed = True
            return result

        def delayed_close(descriptor):
            nonlocal delayed
            if parsed and not delayed:
                delayed = True
                time.sleep(1.1)
            return original_close(descriptor)

        with patch.object(artifacts, '_parse_trusted_candidate_status', side_effect=mark_parsed):
            with patch.object(artifacts.os, 'close', side_effect=delayed_close):
                with self.assertRaisesRegex(artifacts.ScriptCandidateTerminal, 'execution deadline'):
                    artifacts.run_supervised_bubblewrap(
                        [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'},
                        pass_fds=(), execution_timeout_seconds=1.0,
                    )

    def test_cancellation_does_not_remove_the_deadline_during_bootstrap(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-no-pid'
        fake_bubblewrap.write_text(textwrap.dedent('''\
            #!/usr/bin/python3
            import json, os, sys, time
            status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])
            namespace = os.stat('/proc/self/ns/pid').st_ino
            print(json.dumps({'version': 3, 'pid': 1, 'event': 'ready', 'pidNamespace': namespace}), flush=True)
            time.sleep(2)
            os.close(status_fd)
        '''), encoding='utf-8')
        fake_bubblewrap.chmod(0o700)
        started = time.monotonic()
        with self.assertRaises(artifacts.ScriptPlanInterrupted) as caught:
            artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
                execution_timeout_seconds=0.2, cancellation_signal=lambda: signal.SIGTERM,
            )
        self.assertEqual(caught.exception.signum, signal.SIGTERM)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_infrastructure_failure_is_not_reclassified_as_late_cancellation(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-failure-at-cutoff'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0,
        )
        original = artifacts._parse_trusted_candidate_status
        cancelled = False

        def failed_parse(*args, **kwargs):
            nonlocal cancelled
            result = original(*args, **kwargs)
            cancelled = True
            return replace(result, state='bootstrap-failure')

        with patch.object(artifacts, '_parse_trusted_candidate_status', side_effect=failed_parse):
            with self.assertRaises(artifacts.ScriptCandidateTerminal) as caught:
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
                    execution_timeout_seconds=5, cancellation_signal=lambda: signal.SIGTERM if cancelled else None,
                )
        self.assertTrue(caught.exception.infrastructure_failure)

    def test_timeout_retains_cancellation_observed_during_cleanup(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-timeout-cleanup-signal'
        survivor_pid_file = self.root / 'timeout-cleanup-pids.json'
        self.write_fake_bubblewrap(
            fake_bubblewrap, state='candidate-exit', exit_code=0, candidate_signal=None,
            bubblewrap_exit=0, survivor_pid_file=survivor_pid_file,
        )
        original_close = os.close
        candidate_started = False
        cancelled = False

        def on_event(event):
            nonlocal candidate_started
            if event == 'candidate-started':
                candidate_started = True

        def cancel_during_close(descriptor):
            nonlocal cancelled
            if candidate_started:
                cancelled = True
            return original_close(descriptor)

        with patch.object(artifacts.os, 'close', side_effect=cancel_during_close):
            with self.assertRaises(artifacts.ScriptCandidateTerminal) as caught:
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
                    execution_timeout_seconds=0.4, test_hook=on_event,
                    cancellation_signal=lambda: signal.SIGTERM if cancelled else None,
                )
        self.assertTrue(caught.exception.infrastructure_failure)
        self.assertEqual(caught.exception.host_cancellation_signal, signal.SIGTERM)

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

    def write_fake_bubblewrap(
        self,
        path: Path,
        *,
        state: str,
        exit_code: int | None,
        candidate_signal: int | None,
        bubblewrap_exit: int,
        outcome_marker: Path | None = None,
        survivor_pid_file: Path | None = None,
    ) -> None:
        survivor_path = repr(str(survivor_pid_file) if survivor_pid_file is not None else None)
        source = textwrap.dedent(
            f"""\
            #!/usr/bin/python3
            import json, os, signal, sys, time
            status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])
            pid_namespace = os.stat('/proc/self/ns/pid').st_ino
            survivor_pid_file = {survivor_path}
            if survivor_pid_file is not None:
                child_pid = os.fork()
                if child_pid == 0:
                    os.close(status_fd)
                    for descriptor in (0, 1, 2):
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    while True:
                        time.sleep(1)
                with open(survivor_pid_file, 'w', encoding='ascii') as pid_file:
                    json.dump({{'wrapper': os.getpid(), 'child': child_pid}}, pid_file)
            def emit_bwrap(record):
                os.write(status_fd, json.dumps(record).encode() + bytes([10]))
            def emit(event, **fields):
                record = {{'version': 3, 'pid': 1, 'event': event, **fields}}
                sys.stdout.buffer.write(json.dumps(record).encode() + bytes([10]))
                sys.stdout.buffer.flush()
            emit_bwrap({{'child-pid': os.getpid(), 'pid-namespace': pid_namespace}})
            emit('ready', pidNamespace=pid_namespace)
            challenge = sys.stdin.buffer.readline()
            if not challenge.startswith(b'challenge:') or len(challenge) != 75:
                raise SystemExit(125)
            emit('identity-proof', pidNamespace=pid_namespace, nonce=challenge[10:-1].decode('ascii'))
            if sys.stdin.buffer.readline() != b'launch' + bytes([10]):
                raise SystemExit(125)
            emit('control-accepted')
            emit('candidate-started')
            emit('candidate-outcome', exitCode={exit_code!r}, candidateSignal={candidate_signal!r})
            marker = os.environ.get('OUTCOME_MARKER')
            if marker:
                deadline = time.monotonic() + 5
                while not os.path.exists(marker):
                    if time.monotonic() >= deadline:
                        raise SystemExit(125)
                    time.sleep(0.005)
            if survivor_pid_file is not None:
                while True:
                    time.sleep(1)
            time.sleep(0.1)
            emit('cleanup-start')
            emit('cleanup-complete')
            emit('terminal', state={state!r}, candidateStarted=True,
                 exitCode={exit_code!r}, candidateSignal={candidate_signal!r},
                 supervisorSignal=None, cleanup='complete', reaped=1,
                 control='launch', controlSignal=None)
            emit_bwrap({{'exit-code': {bubblewrap_exit}}})
            os.close(status_fd)
            raise SystemExit({bubblewrap_exit})
            """
        )
        path.write_text(source, encoding="utf-8")
        path.chmod(0o700)

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

    def test_completed_receipt_rejects_ambiguous_signal_style_exit_code(self) -> None:
        result, _calls, _receipts = self.run_plan(False)
        receipt_path = result.receipt_directory / "entry-000000.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["exitCode"] = 143
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

        with self.assertRaises(artifacts.IntegrityError):
            artifacts.validate_script_entry_receipts(
                result.receipt_directory, ENTRIES, IDENTITY, False
            )

    def test_cancellation_before_readiness_aborts_without_signaling_inner(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap-cancel-before-ready"
        marker = self.root / "before-ready-running"
        fake_bubblewrap.write_text(textwrap.dedent(f"""\
            #!/usr/bin/python3
            import time
            open({str(marker)!r}, 'w').close()
            time.sleep(30)
        """), encoding="utf-8")
        fake_bubblewrap.chmod(0o700)
        notifications = queue.Queue(maxsize=1)
        actual_signal = signal.pidfd_send_signal
        inner_signals = []

        def record_signal(pidfd, signum, info=None, flags=0):
            if flags == artifacts.PIDFD_SIGNAL_PROCESS_GROUP:
                return actual_signal(pidfd, signum, info, flags)
            inner_signals.append(signum)
            raise AssertionError("unverified inner pidfd was signaled")

        started = time.monotonic()
        with patch.object(artifacts.signal, "pidfd_send_signal", side_effect=record_signal):
            with self.assertRaises(artifacts.ScriptPlanInterrupted) as caught:
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                    cancellation_signal=lambda: signal.SIGTERM if marker.exists() else None,
                    execution_timeout_seconds=10, namespace_notifications=notifications,
                    namespace_attempt_id="cancel-before-ready",
                )
        self.assertEqual(caught.exception.signum, signal.SIGTERM)
        self.assertFalse(caught.exception.candidate_started)
        self.assertEqual(caught.exception.cleanup_status, "unverified")
        self.assertTrue(notifications.empty())
        self.assertEqual(inner_signals, [])
        self.assertLess(time.monotonic() - started, 5)

    def test_transient_cancellation_source_failure_during_challenge_withholds_plan(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap-uncertain-proof"
        marker = self.root / "uncertain-challenge-received"
        fake_bubblewrap.write_text(textwrap.dedent(f"""\
            #!/usr/bin/python3
            import json, os, sys, time
            status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])
            namespace = os.stat('/proc/self/ns/pid').st_ino
            os.write(status_fd, json.dumps({{'child-pid': os.getpid(), 'pid-namespace': namespace}}).encode() + b'\\n')
            print(json.dumps({{'version': 3, 'pid': 1, 'event': 'ready', 'pidNamespace': namespace}}), flush=True)
            sys.stdin.buffer.readline()
            open({str(marker)!r}, 'w').close()
            time.sleep(30)
        """), encoding="utf-8")
        fake_bubblewrap.chmod(0o700)
        state = {"failed": False}

        def read_signal():
            if marker.exists() and not state["failed"]:
                state["failed"] = True
                raise RuntimeError("transient cancellation source failure")
            return None

        def execute(_ordinal, _entry):
            return artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                cancellation_signal=read_signal, execution_timeout_seconds=10,
            )

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute, cancellation_signal=read_signal,
            )
        self.assertTrue(state["failed"])
        self.assertTrue(caught.exception.__cause__.cancellation_evidence_unavailable)
        self.assertFalse((caught.exception.receipt_directory / "plan.json").exists())

    def test_cancellation_during_missing_proof_aborts_without_signaling_inner(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap-cancel-proof"
        marker = self.root / "challenge-received"
        fake_bubblewrap.write_text(textwrap.dedent(f"""\
            #!/usr/bin/python3
            import json, os, sys, time
            status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])
            namespace = os.stat('/proc/self/ns/pid').st_ino
            os.write(status_fd, json.dumps({{'child-pid': os.getpid(), 'pid-namespace': namespace}}).encode() + b'\\n')
            print(json.dumps({{'version': 3, 'pid': 1, 'event': 'ready', 'pidNamespace': namespace}}), flush=True)
            sys.stdin.buffer.readline()
            open({str(marker)!r}, 'w').close()
            time.sleep(30)
        """), encoding="utf-8")
        fake_bubblewrap.chmod(0o700)
        notifications = queue.Queue(maxsize=1)
        actual_signal = signal.pidfd_send_signal
        inner_signals = []

        def record_signal(pidfd, signum, info=None, flags=0):
            if flags == artifacts.PIDFD_SIGNAL_PROCESS_GROUP:
                return actual_signal(pidfd, signum, info, flags)
            inner_signals.append(signum)
            raise AssertionError("unverified inner pidfd was signaled")

        started = time.monotonic()
        with patch.object(artifacts.signal, "pidfd_send_signal", side_effect=record_signal):
            with self.assertRaises(artifacts.ScriptPlanInterrupted) as caught:
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                    cancellation_signal=lambda: signal.SIGTERM if marker.exists() else None,
                    execution_timeout_seconds=10, namespace_notifications=notifications,
                    namespace_attempt_id="cancel-missing-proof",
                )
        self.assertEqual(caught.exception.signum, signal.SIGTERM)
        self.assertFalse(caught.exception.candidate_started)
        self.assertEqual(caught.exception.cleanup_status, "unverified")
        self.assertTrue(notifications.empty())
        self.assertEqual(inner_signals, [])
        self.assertLess(time.monotonic() - started, 5)

    def test_missing_identity_proof_cannot_publish_or_signal_inner_supervisor(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap-missing-proof"
        fake_bubblewrap.write_text(textwrap.dedent("""\
            #!/usr/bin/python3
            import json, os, sys
            status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])
            namespace = os.stat('/proc/self/ns/pid').st_ino
            os.write(status_fd, json.dumps({'child-pid': os.getpid(), 'pid-namespace': namespace}).encode() + b'\\n')
            print(json.dumps({'version': 3, 'pid': 1, 'event': 'ready', 'pidNamespace': namespace}), flush=True)
            sys.stdin.buffer.readline()
        """), encoding="utf-8")
        fake_bubblewrap.chmod(0o700)
        notifications = queue.Queue(maxsize=1)
        actual_signal = signal.pidfd_send_signal
        inner_signals = []

        def record_signal(pidfd, signum, info=None, flags=0):
            if flags == artifacts.PIDFD_SIGNAL_PROCESS_GROUP:
                return actual_signal(pidfd, signum, info, flags)
            inner_signals.append(signum)
            raise AssertionError("unverified inner pidfd was signaled")

        with patch.object(artifacts.signal, "pidfd_send_signal", side_effect=record_signal):
            with self.assertRaises(artifacts.ScriptCandidateTerminal) as caught:
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                    execution_timeout_seconds=5, namespace_notifications=notifications,
                    namespace_attempt_id="missing-proof",
                )
        self.assertFalse(caught.exception.candidate_started)
        self.assertTrue(notifications.empty())
        self.assertEqual(inner_signals, [])

    def test_stale_identity_proof_cannot_publish_or_signal_inner_supervisor(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap-stale-proof"
        marker = self.root / "stale-proof-launched"
        fake_bubblewrap.write_text(textwrap.dedent(f"""\
            #!/usr/bin/python3
            import json, os, sys
            status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])
            namespace = os.stat('/proc/self/ns/pid').st_ino
            os.write(status_fd, json.dumps({{'child-pid': os.getpid(), 'pid-namespace': namespace}}).encode() + b'\\n')
            print(json.dumps({{'version': 3, 'pid': 1, 'event': 'ready', 'pidNamespace': namespace}}), flush=True)
            challenge = sys.stdin.buffer.readline()
            nonce = '00' * 32 if challenge[10:-1] != b'00' * 32 else 'ff' * 32
            print(json.dumps({{'version': 3, 'pid': 1, 'event': 'identity-proof', 'pidNamespace': namespace,
                               'nonce': nonce}}), flush=True)
            if sys.stdin.buffer.readline() == b'launch\\n':
                open({str(marker)!r}, 'w').close()
        """), encoding="utf-8")
        fake_bubblewrap.chmod(0o700)
        notifications = queue.Queue(maxsize=1)
        actual_signal = signal.pidfd_send_signal
        inner_signals = []

        def record_signal(pidfd, signum, info=None, flags=0):
            if flags == artifacts.PIDFD_SIGNAL_PROCESS_GROUP:
                return actual_signal(pidfd, signum, info, flags)
            inner_signals.append(signum)
            raise AssertionError("unverified inner pidfd was signaled")

        with patch.object(artifacts.signal, "pidfd_send_signal", side_effect=record_signal):
            with self.assertRaises(artifacts.ScriptCandidateTerminal) as caught:
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                    execution_timeout_seconds=5, namespace_notifications=notifications,
                    namespace_attempt_id="stale-proof",
                )
        self.assertFalse(caught.exception.candidate_started)
        self.assertTrue(notifications.empty())
        self.assertFalse(marker.exists())
        self.assertEqual(inner_signals, [])

    def test_candidate_started_cleanup_failure_preserves_cancellation_uncertainty(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap-uncertain-cleanup"
        self.write_fake_bubblewrap(fake_bubblewrap, state="candidate-exit", exit_code=0,
                                   candidate_signal=None, bubblewrap_exit=0)
        uncertain = artifacts.IntegrityError("cancellation source failed")
        uncertain.cancellation_evidence_unavailable = True

        def execute(_ordinal, _entry):
            return artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap), "--", "/usr/bin/true"], environment={"PATH": "/usr/bin:/bin"},
                pass_fds=(), execution_timeout_seconds=5,
            )

        with (
            patch.object(artifacts, "_parse_trusted_candidate_status", side_effect=uncertain),
            patch.object(artifacts, "extinguish_supervised_process_group", side_effect=artifacts.IntegrityError("cleanup failure")),
        ):
            with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                artifacts.run_script_plan_with_receipts(
                    ENTRIES, IDENTITY, False, self.runner_temp, execute, cancellation_signal=lambda: None,
                )
        self.assertTrue(caught.exception.__cause__.candidate_started)
        self.assertTrue(caught.exception.__cause__.cancellation_evidence_unavailable)
        self.assertFalse((caught.exception.receipt_directory / "plan.json").exists())

    def test_runner_records_ambiguous_signal_style_exit_as_candidate_terminal(self) -> None:
        def execute(_ordinal: int, _entry: dict[str, object]):
            return artifacts.ScriptEntryExecutionResult(143)

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipts = load_receipts(receipt_directory)
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(receipts[0]["state"], "candidate-terminal")
        self.assertEqual(receipts[0]["exitCode"], 143)
        self.assertIsNone(receipts[0]["signal"])
        self.assertTrue(receipts[0]["candidateStarted"])
        self.assertEqual(
            [receipt["state"] for receipt in receipts[1:]],
            ["not-run-aborted"] * (len(ENTRIES) - 1),
        )
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )

    def test_validator_rejects_boolean_candidate_signal(self) -> None:
        def execute(_ordinal: int, _entry: dict[str, object]):
            raise artifacts.ScriptCandidateTerminal(
                "candidate was terminated by SIGTERM",
                signum=signal.SIGTERM,
                cleanup_status="verified",
                candidate_started=True,
            )

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )

        receipt_directory = caught.exception.receipt_directory
        first_path = receipt_directory / "entry-000000.json"
        first = json.loads(first_path.read_text(encoding="utf-8"))
        first["signal"] = True
        first_path.write_text(json.dumps(first), encoding="utf-8")

        with self.assertRaisesRegex(artifacts.IntegrityError, "candidate signal is invalid"):
            artifacts.validate_script_entry_receipts(
                receipt_directory, ENTRIES, IDENTITY, False
            )

    def test_supervised_ambiguous_exit_is_preserved_in_entry_receipt(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap"
        self.write_fake_bubblewrap(
            fake_bubblewrap,
            state="candidate-ambiguous-exit",
            exit_code=143,
            candidate_signal=None,
            bubblewrap_exit=143,
        )

        def execute(_ordinal: int, _entry: dict[str, object]):
            return artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap)],
                environment={"PATH": "/usr/bin:/bin"},
                pass_fds=(),
            )

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipts = load_receipts(receipt_directory)
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(receipts[0]["state"], "candidate-terminal")
        self.assertEqual(receipts[0]["exitCode"], 143)
        self.assertIsNone(receipts[0]["signal"])
        self.assertTrue(receipts[0]["candidateStarted"])
        self.assertEqual(
            [receipt["state"] for receipt in receipts[1:]],
            ["not-run-aborted"] * (len(ENTRIES) - 1),
        )
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )

    def test_authenticated_candidate_start_survives_malformed_outcome_without_fabrication(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap-malformed-outcome"
        candidate_marker = self.root / "candidate-ran"
        fake_bubblewrap.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, subprocess, sys, time\n"
            "status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])\n"
            "def emit_bwrap(record):\n"
            "    os.write(status_fd, json.dumps(record).encode() + b'\\n')\n"
            "def emit(event, **fields):\n"
            "    record = {'version': 3, 'pid': 1, 'event': event, **fields}\n"
            "    sys.stdout.buffer.write(json.dumps(record).encode() + b'\\n')\n"
            "    sys.stdout.buffer.flush()\n"
            "namespace = os.stat('/proc/self/ns/pid').st_ino\n"
            "emit_bwrap({'child-pid': os.getpid(), 'pid-namespace': namespace})\n"
            "emit('ready', pidNamespace=namespace)\n"
            "challenge = sys.stdin.buffer.readline()\n"
            "if not challenge.startswith(b'challenge:') or len(challenge) != 75: raise SystemExit(125)\n"
            "emit('identity-proof', pidNamespace=namespace, nonce=challenge[10:-1].decode('ascii'))\n"
            "if sys.stdin.buffer.readline() != b'launch\\n': raise SystemExit(125)\n"
            "emit('control-accepted')\n"
            "candidate = subprocess.Popen(sys.argv[sys.argv.index('--') + 1:], close_fds=True)\n"
            "emit('candidate-started')\n"
            "candidate.wait()\n"
            "sys.stdout.buffer.write(b'{\\\"version\\\":3,\\\"pid\\\":1,\\\"event\\\":\\\"candidate-outcome\\\",\\\"exitCode\\\":0}\\n')\n"
            "sys.stdout.buffer.flush()\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        fake_bubblewrap.chmod(0o700)

        def execute(_ordinal: int, _entry: dict[str, object]):
            return artifacts.run_supervised_bubblewrap(
                [
                    str(fake_bubblewrap),
                    "--",
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import os; Path(os.environ['CANDIDATE_MARKER']).write_text('ran')",
                ],
                environment={
                    "PATH": "/usr/bin:/bin",
                    "CANDIDATE_MARKER": str(candidate_marker),
                },
                pass_fds=(),
            )

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipt = load_receipts(receipt_directory)[0]
        self.assertTrue(candidate_marker.is_file())
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(receipt["state"], "infrastructure-failure")
        self.assertTrue(receipt["candidateStarted"])
        self.assertIsNone(receipt["exitCode"])
        self.assertIsNone(receipt["signal"])
        self.assertEqual(receipt["cleanup"], "unverified")
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )

    def test_authenticated_candidate_start_survives_missing_outcome(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap-missing-outcome"
        candidate_marker = self.root / "candidate-ran-without-outcome"
        fake_bubblewrap.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, subprocess, sys, time\n"
            "status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])\n"
            "def emit_bwrap(record):\n"
            "    os.write(status_fd, json.dumps(record).encode() + b'\\n')\n"
            "def emit(event, **fields):\n"
            "    record = {'version': 3, 'pid': 1, 'event': event, **fields}\n"
            "    sys.stdout.buffer.write(json.dumps(record).encode() + b'\\n')\n"
            "    sys.stdout.buffer.flush()\n"
            "namespace = os.stat('/proc/self/ns/pid').st_ino\n"
            "emit_bwrap({'child-pid': os.getpid(), 'pid-namespace': namespace})\n"
            "emit('ready', pidNamespace=namespace)\n"
            "challenge = sys.stdin.buffer.readline()\n"
            "if not challenge.startswith(b'challenge:') or len(challenge) != 75: raise SystemExit(125)\n"
            "emit('identity-proof', pidNamespace=namespace, nonce=challenge[10:-1].decode('ascii'))\n"
            "if sys.stdin.buffer.readline() != b'launch\\n': raise SystemExit(125)\n"
            "emit('control-accepted')\n"
            "candidate = subprocess.Popen(sys.argv[sys.argv.index('--') + 1:], close_fds=True)\n"
            "emit('candidate-started')\n"
            "candidate.wait()\n"
            "emit('cleanup-start')\n"
            "emit('cleanup-complete')\n"
            "emit('terminal', state='supervisor-failure', candidateStarted=True, exitCode=None, candidateSignal=None, supervisorSignal=None, cleanup='complete', reaped=1, control='launch', controlSignal=None)\n"
            "emit_bwrap({'exit-code': 125})\n"
            "os.close(status_fd)\n"
            "raise SystemExit(125)\n",
            encoding="utf-8",
        )
        fake_bubblewrap.chmod(0o700)

        def execute(_ordinal: int, _entry: dict[str, object]):
            return artifacts.run_supervised_bubblewrap(
                [
                    str(fake_bubblewrap),
                    "--",
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import os; Path(os.environ['CANDIDATE_MARKER']).write_text('ran')",
                ],
                environment={
                    "PATH": "/usr/bin:/bin",
                    "CANDIDATE_MARKER": str(candidate_marker),
                },
                pass_fds=(),
            )

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipt = load_receipts(receipt_directory)[0]
        self.assertTrue(candidate_marker.is_file())
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(receipt["state"], "infrastructure-failure")
        self.assertTrue(receipt["candidateStarted"])
        self.assertIsNone(receipt["exitCode"])
        self.assertIsNone(receipt["signal"])
        self.assertEqual(receipt["cleanup"], "verified")
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )

    def test_pidfd_close_failure_reports_unverified_infrastructure_failure(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-pidfd-close-failure'
        self.write_fake_bubblewrap(fake_bubblewrap, state='candidate-exit', exit_code=0,
                                   candidate_signal=None, bubblewrap_exit=0)
        original_open = os.pidfd_open
        original_close = os.close
        pidfds = []

        def record_pidfd(*args, **kwargs):
            fd = original_open(*args, **kwargs)
            pidfds.append(fd)
            return fd

        def close_then_fail(fd):
            original_close(fd)
            if fd in pidfds:
                raise OSError('injected pidfd close failure')

        with patch.object(artifacts.os, 'pidfd_open', side_effect=record_pidfd), \
                patch.object(artifacts.os, 'close', side_effect=close_then_fail):
            with self.assertRaises(artifacts.ScriptCandidateTerminal) as caught:
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap), '--', '/usr/bin/true'], environment={'PATH': '/usr/bin:/bin'},
                    pass_fds=(), execution_timeout_seconds=5,
                )
        self.assertTrue(caught.exception.candidate_started)
        self.assertEqual(caught.exception.return_code, 0)
        self.assertEqual(caught.exception.cleanup_status, 'unverified')
        self.assertTrue(caught.exception.infrastructure_failure)

    def test_close_failure_preserves_candidate_when_cancellation_source_fails(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-close-cancellation-unavailable'
        self.write_fake_bubblewrap(fake_bubblewrap, state='candidate-exit', exit_code=0,
                                   candidate_signal=None, bubblewrap_exit=0)
        state = {'closed': False}
        original_popen = subprocess.Popen

        def fail_after_close():
            if state['closed']:
                raise RuntimeError('injected cancellation source failure')
            return None

        def mark_closed():
            state['closed'] = True

        launch = close_failure_popen(original_popen, on_close=mark_closed)
        with patch.object(artifacts.subprocess, 'Popen', side_effect=launch):
            with self.assertRaises(artifacts.ScriptCandidateTerminal) as caught:
                artifacts.run_supervised_bubblewrap(
                    [str(fake_bubblewrap), '--', '/usr/bin/true'], environment={'PATH': '/usr/bin:/bin'},
                    pass_fds=(), cancellation_signal=fail_after_close, execution_timeout_seconds=5,
                )
        self.assertTrue(caught.exception.candidate_started)
        self.assertEqual(caught.exception.return_code, 0)
        self.assertEqual(caught.exception.cleanup_status, 'unverified')
        self.assertTrue(caught.exception.infrastructure_failure)
        self.assertTrue(caught.exception.cancellation_evidence_unavailable)

    def test_plan_withholds_receipt_when_close_failure_loses_cancellation_evidence(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-plan-cancellation-unavailable'
        self.write_fake_bubblewrap(fake_bubblewrap, state='candidate-exit', exit_code=0,
                                   candidate_signal=None, bubblewrap_exit=0)
        state = {'closed': False}

        def read_signal():
            if state['closed']:
                raise RuntimeError('injected cancellation source failure')
            return None

        def execute(_ordinal, _entry):
            return artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap), '--', '/usr/bin/true'], environment={'PATH': '/usr/bin:/bin'},
                pass_fds=(), cancellation_signal=read_signal, execution_timeout_seconds=5,
            )

        launch = close_failure_popen(subprocess.Popen, on_close=lambda: state.update(closed=True))
        with patch.object(artifacts.subprocess, 'Popen', side_effect=launch):
            with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                artifacts.run_script_plan_with_receipts(ENTRIES, IDENTITY, False, self.runner_temp,
                                                        execute, cancellation_signal=read_signal)
        self.assertIsInstance(caught.exception.__cause__, artifacts.ScriptCandidateTerminal)
        self.assertTrue(caught.exception.__cause__.cancellation_evidence_unavailable)
        self.assertTrue(caught.exception.__cause__.candidate_started)
        self.assertEqual(caught.exception.__cause__.return_code, 0)
        self.assertFalse((caught.exception.receipt_directory / 'plan.json').exists())

    def test_plan_withholds_receipt_if_cancellation_source_fails_at_acceptance(self) -> None:
        state = {'fail': False}
        primary = artifacts.ScriptCandidateTerminal('candidate failure', return_code=0,
                                                     cleanup_status='unverified', candidate_started=True,
                                                     infrastructure_failure=True)

        def read_signal():
            if state['fail']:
                raise RuntimeError('injected cancellation source failure')
            return None

        def execute(_ordinal, _entry):
            raise primary

        def checkpoint(name):
            if name == 'before-acceptance':
                state['fail'] = True

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(ENTRIES, IDENTITY, False, self.runner_temp, execute,
                                                    cancellation_signal=read_signal, test_hook=checkpoint)
        self.assertIs(caught.exception.__cause__, primary)
        self.assertTrue(primary.cancellation_evidence_unavailable)
        self.assertTrue(primary.candidate_started)
        self.assertEqual(primary.return_code, 0)
        self.assertFalse((caught.exception.receipt_directory / 'plan.json').exists())

    def test_plan_preserves_primary_failure_if_cancellation_poll_fails(self) -> None:
        state = {'fail': False}
        primary = artifacts.IntegrityError('candidate infrastructure failure')

        def read_signal():
            if state['fail']:
                raise RuntimeError('injected cancellation source failure')
            return None

        def execute(_ordinal, _entry):
            state['fail'] = True
            raise primary

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(ENTRIES, IDENTITY, False, self.runner_temp, execute,
                                                    cancellation_signal=read_signal)
        self.assertIs(caught.exception.__cause__, primary)
        self.assertTrue(primary.cancellation_evidence_unavailable)
        self.assertFalse((caught.exception.receipt_directory / 'plan.json').exists())

    def test_successful_plan_is_not_accepted_without_cancellation_evidence(self) -> None:
        state = {'fail': False}

        def read_signal():
            if state['fail']:
                raise RuntimeError('injected cancellation source failure')
            return None

        def checkpoint(name):
            if name == 'before-acceptance':
                state['fail'] = True

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp,
                lambda _ordinal, _entry: artifacts.ScriptEntryExecutionResult(0),
                cancellation_signal=read_signal, test_hook=checkpoint,
            )
        self.assertTrue(caught.exception.__cause__.cancellation_evidence_unavailable)
        self.assertFalse((caught.exception.receipt_directory / 'plan.json').exists())

    def test_stream_close_failure_reclassifies_signaled_candidate_as_infrastructure_failure(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-signaled-close-failure'
        self.write_fake_bubblewrap(fake_bubblewrap, state='candidate-signal', exit_code=None,
                                   candidate_signal=int(signal.SIGTERM), bubblewrap_exit=128 + int(signal.SIGTERM))
        original_popen = subprocess.Popen

        launch_with_close_failure = close_failure_popen(original_popen)

        def execute(_ordinal, _entry):
            return artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap), '--', '/usr/bin/true'], environment={'PATH': '/usr/bin:/bin'},
                pass_fds=(), execution_timeout_seconds=5,
            )

        with patch.object(artifacts.subprocess, 'Popen', side_effect=launch_with_close_failure):
            with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                artifacts.run_script_plan_with_receipts(ENTRIES, IDENTITY, False, self.runner_temp, execute)
        receipt = load_receipts(caught.exception.receipt_directory)[0]
        self.assertEqual(receipt['state'], 'infrastructure-failure')
        self.assertTrue(receipt['candidateStarted'])
        self.assertEqual(receipt['signal'], signal.SIGTERM)
        self.assertEqual(receipt['cleanup'], 'unverified')
        artifacts.validate_script_entry_receipts(caught.exception.receipt_directory, ENTRIES, IDENTITY, False)

    def test_stream_close_failure_preserves_interruption_as_infrastructure_failure(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-interrupted-close-failure'
        self.write_fake_bubblewrap(fake_bubblewrap, state='candidate-exit', exit_code=0,
                                   candidate_signal=None, bubblewrap_exit=0)
        host_signals = artifacts.HostSignalQueue()
        original_popen = subprocess.Popen
        original_parse = artifacts._parse_trusted_candidate_status

        launch_with_close_failure = close_failure_popen(original_popen)

        def parse_then_cancel(*args, **kwargs):
            result = original_parse(*args, **kwargs)
            host_signals.capture(signal.SIGTERM)
            return result

        def execute(_ordinal, _entry):
            return artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap), '--', '/usr/bin/true'], environment={'PATH': '/usr/bin:/bin'},
                pass_fds=(), host_signals=host_signals, execution_timeout_seconds=5,
            )

        with patch.object(artifacts.subprocess, 'Popen', side_effect=launch_with_close_failure), \
                patch.object(artifacts, '_parse_trusted_candidate_status', side_effect=parse_then_cancel):
            with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                artifacts.run_script_plan_with_receipts(ENTRIES, IDENTITY, False, self.runner_temp,
                                                        execute, host_signals=host_signals)
        receipt = load_receipts(caught.exception.receipt_directory)[0]
        self.assertEqual(receipt['state'], 'infrastructure-failure')
        self.assertTrue(receipt['candidateStarted'])
        self.assertEqual(receipt['exitCode'], 0)
        self.assertEqual(receipt['hostCancellationSignal'], signal.SIGTERM)
        self.assertEqual(receipt['cleanup'], 'unverified')
        artifacts.validate_script_entry_receipts(caught.exception.receipt_directory, ENTRIES, IDENTITY, False)

    def test_stream_close_failure_preserves_completed_candidate_and_cancellation(self) -> None:
        fake_bubblewrap = self.root / 'fake-bwrap-close-failure'
        self.write_fake_bubblewrap(fake_bubblewrap, state='candidate-exit', exit_code=0,
                                   candidate_signal=None, bubblewrap_exit=0)
        host_signals = artifacts.HostSignalQueue()
        original_popen = subprocess.Popen

        launch_with_close_failure = close_failure_popen(original_popen, on_close=lambda: host_signals.capture(signal.SIGTERM))

        def execute(_ordinal, _entry):
            return artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap), '--', '/usr/bin/true'], environment={'PATH': '/usr/bin:/bin'},
                pass_fds=(), host_signals=host_signals, execution_timeout_seconds=5,
            )

        with patch.object(artifacts.subprocess, 'Popen', side_effect=launch_with_close_failure):
            with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                artifacts.run_script_plan_with_receipts(ENTRIES, IDENTITY, False, self.runner_temp,
                                                        execute, host_signals=host_signals)
        receipt = load_receipts(caught.exception.receipt_directory)[0]
        self.assertEqual(receipt['state'], 'infrastructure-failure')
        self.assertTrue(receipt['candidateStarted'])
        self.assertEqual(receipt['exitCode'], 0)
        self.assertEqual(receipt['cleanup'], 'unverified')
        self.assertEqual(receipt['hostCancellationSignal'], signal.SIGTERM)
        artifacts.validate_script_entry_receipts(caught.exception.receipt_directory, ENTRIES, IDENTITY, False)

    def test_out_of_order_candidate_outcome_and_cleanup_failure_keep_only_start_evidence(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap-out-of-order-outcome"
        candidate_marker = self.root / "candidate-ran-before-out-of-order-outcome"
        survivor_pid_file = self.root / "survivor.pid"
        host_signals = artifacts.HostSignalQueue()
        fake_bubblewrap.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, subprocess, sys\n"
            "status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])\n"
            "def emit_bwrap(record):\n"
            "    os.write(status_fd, json.dumps(record).encode() + b'\\n')\n"
            "def emit(event, **fields):\n"
            "    record = {'version': 3, 'pid': 1, 'event': event, **fields}\n"
            "    sys.stdout.buffer.write(json.dumps(record).encode() + b'\\n')\n"
            "    sys.stdout.buffer.flush()\n"
            "namespace = os.stat('/proc/self/ns/pid').st_ino\n"
            "emit_bwrap({'child-pid': os.getpid(), 'pid-namespace': namespace})\n"
            "emit('ready', pidNamespace=namespace)\n"
            "challenge = sys.stdin.buffer.readline()\n"
            "if not challenge.startswith(b'challenge:') or len(challenge) != 75: raise SystemExit(125)\n"
            "emit('identity-proof', pidNamespace=namespace, nonce=challenge[10:-1].decode('ascii'))\n"
            "if sys.stdin.buffer.readline() != b'launch\\n': raise SystemExit(125)\n"
            "emit('control-accepted')\n"
            "candidate = subprocess.Popen(sys.argv[sys.argv.index('--') + 1:], close_fds=True)\n"
            "emit('candidate-started')\n"
            "candidate.wait()\n"
            "survivor = subprocess.Popen([sys.executable, '-c', \"import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)\"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)\n"
            "with open(os.environ['SURVIVOR_PID_FILE'], 'w', encoding='ascii') as handle: handle.write(str(survivor.pid))\n"
            "emit('cleanup-start')\n"
            "emit('candidate-outcome', exitCode=73, candidateSignal=None)\n"
            "emit_bwrap({'exit-code': 0})\n"
            "os.close(status_fd)\n",
            encoding="utf-8",
        )
        fake_bubblewrap.chmod(0o700)

        def execute(_ordinal: int, _entry: dict[str, object]):
            return artifacts.run_supervised_bubblewrap(
                [
                    str(fake_bubblewrap),
                    "--",
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import os; Path(os.environ['CANDIDATE_MARKER']).write_text('ran')",
                ],
                environment={
                    "PATH": "/usr/bin:/bin",
                    "CANDIDATE_MARKER": str(candidate_marker),
                    "SURVIVOR_PID_FILE": str(survivor_pid_file),
                },
                pass_fds=(),
                host_signals=host_signals,
            )

        before_descriptors = len(os.listdir('/proc/self/fd'))
        processes = []
        pipes = []
        pidfds = []
        original_popen = subprocess.Popen
        original_pipe = os.pipe
        original_pidfd_open = os.pidfd_open

        launch_with_close_failure = close_failure_popen(original_popen, on_close=lambda: host_signals.capture(signal.SIGTERM), processes=processes)

        def record_pipe(*args, **kwargs):
            result = original_pipe(*args, **kwargs)
            pipes.append(result)
            return result

        def record_pidfd(*args, **kwargs):
            result = original_pidfd_open(*args, **kwargs)
            pidfds.append(result)
            return result

        try:
            with patch.object(artifacts.subprocess, 'Popen', side_effect=launch_with_close_failure), \
                    patch.object(artifacts.os, 'pipe', side_effect=record_pipe), \
                    patch.object(artifacts.os, 'pidfd_open', side_effect=record_pidfd), patch.object(
                artifacts,
                "extinguish_supervised_process_group",
                side_effect=artifacts.IntegrityError("simulated final cleanup verification failure"),
            ):
                with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                    artifacts.run_script_plan_with_receipts(
                        ENTRIES, IDENTITY, False, self.runner_temp, execute, host_signals=host_signals
                    )

            receipt_directory = caught.exception.receipt_directory
            plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
            receipt = load_receipts(receipt_directory)[0]
            self.assertTrue(candidate_marker.is_file())
            self.assertEqual(plan["state"], "infrastructure-failure")
            self.assertEqual(receipt["state"], "infrastructure-failure")
            self.assertTrue(receipt["candidateStarted"])
            self.assertIsNone(receipt["exitCode"])
            self.assertIsNone(receipt["signal"])
            self.assertEqual(receipt["cleanup"], "unverified")
            self.assertEqual(receipt["hostCancellationSignal"], signal.SIGTERM)
            self.assertTrue(processes[0].stdout.stream.closed)
            for descriptor in (pipes[0][0], pidfds[0]):
                with self.assertRaises(OSError) as closed:
                    os.fstat(descriptor)
                self.assertEqual(closed.exception.errno, errno.EBADF)
            self.assertEqual(len(os.listdir('/proc/self/fd')), before_descriptors)
            artifacts.validate_script_entry_receipts(
                receipt_directory, ENTRIES, IDENTITY, False
            )
        finally:
            if survivor_pid_file.exists():
                try:
                    os.kill(int(survivor_pid_file.read_text(encoding="ascii")), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_rejected_candidate_started_event_after_not_started_is_not_evidence(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap-rejected-start-event"
        candidate_marker = self.root / "candidate-must-not-run"
        fake_bubblewrap.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, sys, time\n"
            "status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])\n"
            "def emit_bwrap(record):\n"
            "    os.write(status_fd, json.dumps(record).encode() + b'\\n')\n"
            "def emit(event, **fields):\n"
            "    record = {'version': 3, 'pid': 1, 'event': event, **fields}\n"
            "    sys.stdout.buffer.write(json.dumps(record).encode() + b'\\n')\n"
            "    sys.stdout.buffer.flush()\n"
            "namespace = os.stat('/proc/self/ns/pid').st_ino\n"
            "emit_bwrap({'child-pid': os.getpid(), 'pid-namespace': namespace})\n"
            "emit('ready', pidNamespace=namespace)\n"
            "challenge = sys.stdin.buffer.readline()\n"
            "if not challenge.startswith(b'challenge:') or len(challenge) != 75: raise SystemExit(125)\n"
            "emit('identity-proof', pidNamespace=namespace, nonce=challenge[10:-1].decode('ascii'))\n"
            "if sys.stdin.buffer.readline() != b'launch\\n': raise SystemExit(125)\n"
            "emit('control-accepted')\n"
            "emit('candidate-not-started', reason='exec-failure')\n"
            "emit('candidate-started')\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        fake_bubblewrap.chmod(0o700)

        def execute(_ordinal: int, _entry: dict[str, object]):
            return artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap), "--", sys.executable, "-c", "raise SystemExit(0)"],
                environment={"PATH": "/usr/bin:/bin"},
                pass_fds=(),
            )

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipt = load_receipts(receipt_directory)[0]
        self.assertFalse(candidate_marker.exists())
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(receipt["state"], "infrastructure-failure")
        self.assertFalse(receipt["candidateStarted"])
        self.assertIsNone(receipt["exitCode"])
        self.assertIsNone(receipt["signal"])
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )

    def test_late_host_cancellation_preserves_candidate_signal_and_ambiguous_exit(self) -> None:
        cases = (
            ("candidate-signal", None, int(signal.SIGTERM), 128 + signal.SIGTERM),
            ("candidate-ambiguous-exit", 143, None, 143),
        )
        for state, exit_code, candidate_signal, bubblewrap_exit in cases:
            with self.subTest(state=state):
                fake_bubblewrap = self.root / f"fake-bwrap-{state}"
                marker = self.root / f"{state}-observed"
                self.write_fake_bubblewrap(
                    fake_bubblewrap,
                    state=state,
                    exit_code=exit_code,
                    candidate_signal=candidate_signal,
                    bubblewrap_exit=bubblewrap_exit,
                    outcome_marker=marker,
                )
                host_signals = artifacts.HostSignalQueue()
                signal_attempts: list[int] = []
                actual_pidfd_send_signal = signal.pidfd_send_signal

                def after_outcome(event: str) -> None:
                    if event == "candidate-outcome":
                        host_signals.capture(signal.SIGTERM)
                        marker.touch()

                def already_exited(_pidfd: int, signum: int, info=None, flags=0) -> None:
                    if flags == artifacts.PIDFD_SIGNAL_PROCESS_GROUP:
                        return actual_pidfd_send_signal(_pidfd, signum, info, flags)
                    signal_attempts.append(signum)
                    raise ProcessLookupError(errno.ESRCH, "PID 1 exited after candidate outcome")

                def execute(_ordinal: int, _entry: dict[str, object]):
                    return artifacts.run_supervised_bubblewrap(
                        [str(fake_bubblewrap)],
                        environment={
                            "PATH": "/usr/bin:/bin",
                            "OUTCOME_MARKER": str(marker),
                        },
                        pass_fds=(),
                        host_signals=host_signals,
                        test_hook=after_outcome,
                    )

                with patch.object(
                    artifacts.signal, "pidfd_send_signal", side_effect=already_exited
                ):
                    with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                        artifacts.run_script_plan_with_receipts(
                            ENTRIES, IDENTITY, False, self.runner_temp, execute
                        )

                receipt_directory = caught.exception.receipt_directory
                plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
                receipts = load_receipts(receipt_directory)
                self.assertEqual(signal_attempts, [signal.SIGTERM])
                self.assertEqual(plan["state"], "infrastructure-failure")
                self.assertIsNone(plan["hostCancellationSignal"])
                self.assertEqual(receipts[0]["state"], "candidate-terminal")
                self.assertEqual(receipts[0]["exitCode"], exit_code)
                self.assertEqual(receipts[0]["signal"], candidate_signal)
                self.assertTrue(receipts[0]["candidateStarted"])
                self.assertIsNone(receipts[0]["hostCancellationSignal"])
                self.assertEqual(
                    [receipt["state"] for receipt in receipts[1:]],
                    ["not-run-aborted"] * (len(ENTRIES) - 1),
                )
                artifacts.validate_script_entry_receipts(
                    receipt_directory, ENTRIES, IDENTITY, False
                )

    def test_pidfd_eperm_uses_verified_group_fallback_for_resistant_descendant(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap-eperm"
        marker = self.root / "candidate-outcome-observed"
        survivor_pid_file = self.root / "survivor-pids.json"
        self.write_fake_bubblewrap(
            fake_bubblewrap,
            state="candidate-exit",
            exit_code=0,
            candidate_signal=None,
            bubblewrap_exit=0,
            outcome_marker=marker,
            survivor_pid_file=survivor_pid_file,
        )
        host_signals = artifacts.HostSignalQueue()
        signal_attempts: list[int] = []
        actual_pidfd_send_signal = signal.pidfd_send_signal

        def after_outcome(event: str) -> None:
            if event == "candidate-outcome":
                host_signals.capture(signal.SIGTERM)
                marker.touch()

        def denied(_pidfd: int, signum: int, info=None, flags=0) -> None:
            if flags == artifacts.PIDFD_SIGNAL_PROCESS_GROUP:
                return actual_pidfd_send_signal(_pidfd, signum, info, flags)
            signal_attempts.append(signum)
            raise OSError(errno.EPERM, "simulated pidfd signal permission failure")

        def execute(_ordinal: int, _entry: dict[str, object]):
            return artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap)],
                environment={"PATH": "/usr/bin:/bin", "OUTCOME_MARKER": str(marker)},
                pass_fds=(),
                host_signals=host_signals,
                test_hook=after_outcome,
            )

        with patch.object(artifacts.signal, "pidfd_send_signal", side_effect=denied):
            with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                artifacts.run_script_plan_with_receipts(
                    ENTRIES, IDENTITY, False, self.runner_temp, execute
                )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipts = load_receipts(receipt_directory)
        self.assertTrue(
            survivor_pid_file.exists(),
            json.dumps({"plan": plan, "receipts": receipts}, sort_keys=True),
        )
        survivor_pids = json.loads(survivor_pid_file.read_text(encoding="ascii"))
        self.assertGreaterEqual(len(signal_attempts), 1)
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)
        self.assertEqual(receipts[0]["state"], "infrastructure-failure")
        self.assertEqual(receipts[0]["exitCode"], 0)
        self.assertIsNone(receipts[0]["signal"])
        self.assertTrue(receipts[0]["candidateStarted"])
        self.assertEqual(receipts[0]["hostCancellationSignal"], signal.SIGTERM)
        self.assertIsNone(receipts[0]["supervisorSignal"])
        self.assertEqual(receipts[0]["cleanup"], "unverified")
        for pid in survivor_pids.values():
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        with self.assertRaises(ProcessLookupError):
            os.killpg(survivor_pids["wrapper"], 0)
        self.assertEqual(
            [entry["state"] for entry in receipts[1:]],
            ["not-run-aborted"] * (len(ENTRIES) - 1),
        )
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )

    def test_cancellation_is_terminal_and_aborts_later_entries_without_fabricating_exit(self) -> None:
        calls: list[str] = []
        host_signals = artifacts.HostSignalQueue()

        def execute(_ordinal: int, entry: dict[str, object]):
            calls.append(str(entry["script"]))
            host_signals.capture(int(signal.SIGTERM))
            interruption = artifacts.ScriptPlanInterrupted(int(signal.SIGTERM))
            interruption.cleanup_status = "verified"
            raise interruption

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute,
                host_signals=host_signals,
            )

        self.assertEqual(calls, ["first"])
        receipts = load_receipts(caught.exception.receipt_directory)
        self.assertEqual(receipts[0]["state"], "interrupted")
        self.assertIsNone(receipts[0]["exitCode"])
        self.assertIsNone(receipts[0]["signal"])
        self.assertEqual(receipts[0]["hostCancellationSignal"], signal.SIGTERM)
        self.assertEqual(receipts[0]["cleanup"], "verified")
        self.assertEqual([entry["state"] for entry in receipts[1:]], ["not-run-aborted"] * 2)

    def test_pre_entry_cancellation_aborts_every_entry_for_both_fail_fast_policies(self) -> None:
        for fail_fast in (False, True):
            with self.subTest(fail_fast=fail_fast):
                host_signals = artifacts.HostSignalQueue()
                host_signals.capture(int(signal.SIGTERM))
                calls: list[str] = []

                def execute(_ordinal: int, entry: dict[str, object]):
                    calls.append(str(entry["script"]))
                    return artifacts.ScriptEntryExecutionResult(0)

                with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                    artifacts.run_script_plan_with_receipts(
                        ENTRIES,
                        IDENTITY,
                        fail_fast,
                        self.runner_temp,
                        execute,
                        host_signals=host_signals,
                    )

                receipt_directory = caught.exception.receipt_directory
                plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
                receipts = load_receipts(receipt_directory)
                self.assertEqual(calls, [])
                self.assertEqual(plan["state"], "interrupted")
                self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)
                self.assertEqual(
                    [receipt["state"] for receipt in receipts],
                    ["interrupted", "not-run-aborted", "not-run-aborted"],
                )
                artifacts.validate_script_entry_receipts(
                    receipt_directory, ENTRIES, IDENTITY, fail_fast
                )

    def test_interrupted_started_candidate_requires_verified_cleanup(self) -> None:
        host_signals = artifacts.HostSignalQueue()
        host_signals.capture(int(signal.SIGTERM))

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES,
                IDENTITY,
                False,
                self.runner_temp,
                lambda _ordinal, _entry: artifacts.ScriptEntryExecutionResult(0),
                host_signals=host_signals,
            )

        receipt_directory = caught.exception.receipt_directory
        first_path = receipt_directory / "entry-000000.json"
        first = json.loads(first_path.read_text(encoding="utf-8"))
        first["candidateStarted"] = True
        first["exitCode"] = None
        first["signal"] = None
        first["cleanup"] = "not-started"
        first_path.write_text(json.dumps(first), encoding="utf-8")

        with self.assertRaisesRegex(
            artifacts.IntegrityError, "interrupted script-entry receipt is contradictory"
        ):
            artifacts.validate_script_entry_receipts(
                receipt_directory, ENTRIES, IDENTITY, False
            )

    def test_infrastructure_receipt_rejects_started_candidate_with_unstarted_cleanup(self) -> None:
        def execute(_ordinal: int, _entry: dict[str, object]):
            raise artifacts.ScriptCandidateTerminal(
                "candidate boundary failed after starting",
                return_code=0,
                cleanup_status="verified",
                candidate_started=True,
            )

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )

        receipt_directory = caught.exception.receipt_directory
        first_path = receipt_directory / "entry-000000.json"
        first = json.loads(first_path.read_text(encoding="utf-8"))
        self.assertEqual(first["state"], "infrastructure-failure")
        self.assertTrue(first["candidateStarted"])
        first["cleanup"] = "not-started"
        first_path.write_text(json.dumps(first), encoding="utf-8")

        with self.assertRaisesRegex(artifacts.IntegrityError, "terminal script-entry receipt"):
            artifacts.validate_script_entry_receipts(
                receipt_directory, ENTRIES, IDENTITY, False
            )

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

    def test_cleanup_failure_with_candidate_signal_persists_infrastructure_receipt(self) -> None:
        def execute(_ordinal: int, _entry: dict[str, object]):
            raise artifacts.ScriptCandidateTerminal(
                "candidate signal cleanup failed",
                signum=signal.SIGTERM,
                cleanup_status="failed",
                candidate_started=True,
            )

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipts = load_receipts(receipt_directory)
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(receipts[0]["state"], "infrastructure-failure")
        self.assertTrue(receipts[0]["candidateStarted"])
        self.assertEqual(receipts[0]["signal"], signal.SIGTERM)
        self.assertEqual(receipts[0]["cleanup"], "failed")
        self.assertEqual([entry["state"] for entry in receipts[1:]], ["not-run-aborted"] * 2)
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )


    def test_successful_candidate_cleanup_failure_retains_completed_outcome(self) -> None:
        def execute(_ordinal: int, _entry: dict[str, object]):
            return artifacts.ScriptEntryExecutionResult(0, cleanup_status="failed")

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipts = load_receipts(receipt_directory)
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(receipts[0]["state"], "infrastructure-failure")
        self.assertTrue(receipts[0]["candidateStarted"])
        self.assertEqual(receipts[0]["exitCode"], 0)
        self.assertIsNone(receipts[0]["signal"])
        self.assertIsNone(receipts[0]["supervisorSignal"])
        self.assertEqual(receipts[0]["cleanup"], "failed")
        self.assertEqual([entry["state"] for entry in receipts[1:]], ["not-run-aborted"] * 2)
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )

    def test_candidate_cleanup_adapter_preserves_success_status(self) -> None:
        candidate_result = artifacts.TrustedCandidateStatus(
            state="candidate-exit",
            return_code=0,
            signum=None,
            cleanup="complete",
            reaped_count=1,
            candidate_started=True,
            host_cancellation_signal=None,
            supervisor_signal=None,
        )

        def execute(_ordinal: int, _entry: dict[str, object]):
            raise artifacts._script_candidate_cleanup_failure(candidate_result)

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )

        receipt = load_receipts(caught.exception.receipt_directory)[0]
        self.assertEqual(receipt["state"], "infrastructure-failure")
        self.assertTrue(receipt["candidateStarted"])
        self.assertEqual(receipt["exitCode"], 0)
        self.assertIsNone(receipt["signal"])
        self.assertIsNone(receipt["supervisorSignal"])
        self.assertEqual(receipt["cleanup"], "failed")

    def test_host_cancellation_cleanup_failure_keeps_signal_provenance(self) -> None:
        host_signals = artifacts.HostSignalQueue()

        def execute(_ordinal: int, _entry: dict[str, object]):
            host_signals.capture(signal.SIGTERM)
            interruption = artifacts.ScriptPlanInterrupted(
                signal.SIGTERM,
                candidate_started=True,
                candidate_exit_code=0,
                candidate_signal=None,
                supervisor_signal=signal.SIGTERM,
            )
            interruption.cleanup_status = "failed"
            raise artifacts._script_candidate_cleanup_failure(None, interruption)

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES,
                IDENTITY,
                False,
                self.runner_temp,
                execute,
                host_signals=host_signals,
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipt = load_receipts(receipt_directory)[0]
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)
        self.assertEqual(receipt["state"], "infrastructure-failure")
        self.assertTrue(receipt["candidateStarted"])
        self.assertEqual(receipt["exitCode"], 0)
        self.assertIsNone(receipt["signal"])
        self.assertEqual(receipt["hostCancellationSignal"], signal.SIGTERM)
        self.assertEqual(receipt["supervisorSignal"], signal.SIGTERM)
        self.assertEqual(receipt["cleanup"], "failed")
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )
        plan["hostCancellationSignal"] = signal.SIGINT
        (receipt_directory / "plan.json").write_text(
            json.dumps(plan, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        with self.assertRaises(artifacts.IntegrityError):
            artifacts.validate_script_entry_receipts(
                receipt_directory, ENTRIES, IDENTITY, False
            )

    def test_infrastructure_plan_rejects_orphaned_host_signal(self) -> None:
        def execute(_ordinal: int, _entry: dict[str, object]):
            raise artifacts.ScriptCandidateTerminal(
                "cleanup failed without host cancellation",
                return_code=0,
                cleanup_status="failed",
                candidate_started=True,
            )

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )

        receipt_directory = caught.exception.receipt_directory
        plan_path = receipt_directory / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertIsNone(plan["hostCancellationSignal"])
        plan["hostCancellationSignal"] = signal.SIGTERM
        plan_path.write_text(
            json.dumps(plan, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        with self.assertRaises(artifacts.IntegrityError):
            artifacts.validate_script_entry_receipts(
                receipt_directory, ENTRIES, IDENTITY, False
            )

    def test_prelaunch_cleanup_failure_keeps_host_signal_without_candidate_outcome(self) -> None:
        host_signals = artifacts.HostSignalQueue()
        status = artifacts.TrustedCandidateStatus(
            state="cleanup-failure",
            return_code=None,
            signum=None,
            cleanup="failed",
            reaped_count=0,
            candidate_started=False,
            host_cancellation_signal=signal.SIGTERM,
            supervisor_signal=signal.SIGTERM,
        )
        terminal = artifacts._script_candidate_status_failure(status)

        def execute(_ordinal: int, _entry: dict[str, object]):
            host_signals.capture(signal.SIGTERM)
            raise terminal

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES,
                IDENTITY,
                False,
                self.runner_temp,
                execute,
                host_signals=host_signals,
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipt = load_receipts(receipt_directory)[0]
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)
        self.assertEqual(receipt["state"], "infrastructure-failure")
        self.assertFalse(receipt["candidateStarted"])
        self.assertIsNone(receipt["exitCode"])
        self.assertIsNone(receipt["signal"])
        self.assertEqual(receipt["hostCancellationSignal"], signal.SIGTERM)
        self.assertEqual(receipt["supervisorSignal"], signal.SIGTERM)
        self.assertEqual(receipt["cleanup"], "failed")
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )

    def test_cleanup_verification_exception_preserves_candidate_receipt_and_cause(self) -> None:
        inventory_failure = OSError("baseline inventory became unavailable")
        candidate_result = artifacts.TrustedCandidateStatus(
            state="candidate-exit",
            return_code=0,
            signum=None,
            cleanup="complete",
            reaped_count=1,
            candidate_started=True,
            host_cancellation_signal=None,
            supervisor_signal=None,
        )

        def inventory(_baseline: object) -> list[object]:
            raise inventory_failure

        def execute(_ordinal: int, _entry: dict[str, object]):
            artifacts._verify_script_candidate_cleanup(
                candidate_result,
                None,
                lambda: inventory(object()) != [],
            )
            raise AssertionError("inventory failure must terminate the entry")

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, execute
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipt = load_receipts(receipt_directory)[0]
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertIsNone(plan["hostCancellationSignal"])
        self.assertEqual(receipt["state"], "infrastructure-failure")
        self.assertTrue(receipt["candidateStarted"])
        self.assertEqual(receipt["exitCode"], 0)
        self.assertIsNone(receipt["signal"])
        self.assertEqual(receipt["cleanup"], "unverified")
        self.assertIs(caught.exception.__cause__.__cause__, inventory_failure)
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )

    def test_aborted_receipts_form_a_terminal_suffix(self) -> None:
        host_signals = artifacts.HostSignalQueue()
        host_signals.capture(signal.SIGTERM)
        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES,
                IDENTITY,
                True,
                self.runner_temp,
                lambda _ordinal, _entry: artifacts.ScriptEntryExecutionResult(0),
                host_signals=host_signals,
            )

        receipt_directory = caught.exception.receipt_directory
        first_path = receipt_directory / "entry-000000.json"
        first = json.loads(first_path.read_text(encoding="utf-8"))
        first.update(
            state="not-run-aborted",
            exitCode=None,
            signal=None,
            candidateStarted=False,
            hostCancellationSignal=None,
            supervisorSignal=None,
            cleanup="not-started",
        )
        first_path.write_text(json.dumps(first), encoding="utf-8")
        second_path = receipt_directory / "entry-000001.json"
        second = json.loads(second_path.read_text(encoding="utf-8"))
        second.update(
            state="completed",
            exitCode=0,
            signal=None,
            candidateStarted=True,
            hostCancellationSignal=None,
            supervisorSignal=None,
            cleanup="verified",
        )
        second_path.write_text(json.dumps(second), encoding="utf-8")

        with self.assertRaisesRegex(artifacts.IntegrityError, "aborted suffix"):
            artifacts.validate_script_entry_receipts(
                receipt_directory, ENTRIES, IDENTITY, True
            )

    def test_interrupted_receipts_reject_failed_or_unverified_cleanup(self) -> None:
        for cleanup in ("failed", "unverified"):
            with self.subTest(cleanup=cleanup):
                host_signals = artifacts.HostSignalQueue()
                host_signals.capture(signal.SIGTERM)

                def execute(_ordinal: int, _entry: dict[str, object]):
                    return artifacts.ScriptEntryExecutionResult(0)

                with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                    artifacts.run_script_plan_with_receipts(
                        ENTRIES,
                        IDENTITY,
                        False,
                        self.runner_temp,
                        execute,
                        host_signals=host_signals,
                    )

                receipt_directory = caught.exception.receipt_directory
                first_path = receipt_directory / "entry-000000.json"
                first = json.loads(first_path.read_text(encoding="utf-8"))
                first["cleanup"] = cleanup
                first_path.write_text(json.dumps(first), encoding="utf-8")

                with self.assertRaises(artifacts.IntegrityError):
                    artifacts.validate_script_entry_receipts(
                        receipt_directory, ENTRIES, IDENTITY, False
                    )

    def test_interrupted_plan_rejects_a_fail_fast_skipped_suffix(self) -> None:
        def execute(_ordinal: int, entry: dict[str, object]):
            return artifacts.ScriptEntryExecutionResult(
                1 if entry["script"] == "first" else 0
            )

        result = artifacts.run_script_plan_with_receipts(
            ENTRIES, IDENTITY, True, self.runner_temp, execute
        )
        receipt_directory = result.receipt_directory
        plan_path = receipt_directory / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["state"], "candidate-failure")
        plan["state"] = "interrupted"
        plan["hostCancellationSignal"] = signal.SIGTERM
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        with self.assertRaisesRegex(artifacts.IntegrityError, "interrupted plan"):
            artifacts.validate_script_entry_receipts(
                receipt_directory, ENTRIES, IDENTITY, True
            )

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
                host_signals = artifacts.HostSignalQueue()
                calls: list[str] = []
                injected = False

                def inject(name: str) -> None:
                    nonlocal injected
                    if name == point and not injected:
                        injected = True
                        host_signals.capture(int(signal.SIGTERM))

                def execute(_ordinal: int, entry: dict[str, object]):
                    calls.append(str(entry["script"]))
                    return artifacts.ScriptEntryExecutionResult(0)

                with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                    artifacts.run_script_plan_with_receipts(
                        ENTRIES,
                        IDENTITY,
                        False,
                        self.runner_temp,
                        execute,
                        host_signals=host_signals,
                        test_hook=inject,
                    )

                self.assertTrue(injected)
                receipt_directory = caught.exception.receipt_directory
                plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
                receipts = load_receipts(receipt_directory)
                self.assertEqual(plan["state"], "interrupted")
                self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)
                if point.startswith("entry-receipt"):
                    self.assertEqual(calls, ["first"])
                    self.assertEqual(
                        [receipt["state"] for receipt in receipts],
                        ["completed", "not-run-aborted", "not-run-aborted"],
                    )
                else:
                    self.assertEqual(len(calls), len(ENTRIES))
                    self.assertEqual(
                        [receipt["state"] for receipt in receipts],
                        ["completed"] * len(ENTRIES),
                    )
                artifacts.validate_script_entry_receipts(
                    receipt_directory, ENTRIES, IDENTITY, False
                )

    def test_cancellation_during_failed_plan_persistence_records_interruption(self) -> None:
        for fail_fast in (True, False):
            for point in ("plan-receipt-write", "plan-receipt-finalize", "before-acceptance"):
                with self.subTest(fail_fast=fail_fast, point=point):
                    host_signals = artifacts.HostSignalQueue()
                    injected: list[str] = []
                    calls: list[str] = []

                    def inject(name: str) -> None:
                        if name == point and not injected:
                            injected.append(name)
                            host_signals.capture(int(signal.SIGTERM))

                    def execute(_ordinal: int, entry: dict[str, object]):
                        name = str(entry["script"])
                        calls.append(name)
                        return artifacts.ScriptEntryExecutionResult(23 if name == "failure" else 0)

                    with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                        artifacts.run_script_plan_with_receipts(
                            ENTRIES,
                            IDENTITY,
                            fail_fast,
                            self.runner_temp,
                            execute,
                            host_signals=host_signals,
                            test_hook=inject,
                        )

                    self.assertIn("ScriptPlanInterrupted", str(caught.exception))
                    self.assertEqual(injected, [point])
                    receipt_directory = caught.exception.receipt_directory
                    plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
                    receipts = load_receipts(receipt_directory)
                    self.assertEqual(plan["state"], "interrupted")
                    self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)
                    self.assertEqual(receipts[0]["state"], "completed")
                    self.assertEqual(receipts[0]["exitCode"], 0)
                    self.assertEqual(receipts[1]["state"], "completed")
                    self.assertEqual(receipts[1]["exitCode"], 23)
                    self.assertEqual(receipts[1]["cleanup"], "verified")
                    if fail_fast:
                        self.assertEqual(calls, ["first", "failure"])
                        self.assertEqual(receipts[2]["state"], "not-run-aborted")
                        self.assertIsNone(receipts[2]["exitCode"])
                    else:
                        self.assertEqual(calls, ["first", "failure", "diagnostic"])
                        self.assertEqual(receipts[2]["state"], "completed")
                        self.assertEqual(receipts[2]["exitCode"], 0)
                    artifacts.validate_script_entry_receipts(
                        receipt_directory, ENTRIES, IDENTITY, fail_fast
                    )

    def test_legacy_callback_cancellation_during_failed_plan_persistence_records_interruption(self) -> None:
        for fail_fast in (True, False):
            for point in ("plan-receipt-write", "plan-receipt-finalize", "before-acceptance"):
                with self.subTest(fail_fast=fail_fast, point=point):
                    cancellation: dict[str, int] = {}
                    injected: list[str] = []
                    calls: list[str] = []

                    def inject(name: str) -> None:
                        if name == point and not injected:
                            injected.append(name)
                            cancellation["signal"] = int(signal.SIGTERM)

                    def execute(_ordinal: int, entry: dict[str, object]):
                        name = str(entry["script"])
                        calls.append(name)
                        return artifacts.ScriptEntryExecutionResult(23 if name == "failure" else 0)

                    with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                        artifacts.run_script_plan_with_receipts(
                            ENTRIES,
                            IDENTITY,
                            fail_fast,
                            self.runner_temp,
                            execute,
                            cancellation_signal=lambda: cancellation.get("signal"),
                            test_hook=inject,
                        )

                    self.assertIn("ScriptPlanInterrupted", str(caught.exception))
                    self.assertEqual(injected, [point])
                    receipt_directory = caught.exception.receipt_directory
                    plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
                    receipts = load_receipts(receipt_directory)
                    self.assertEqual(plan["state"], "interrupted")
                    self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)
                    self.assertEqual(receipts[0]["state"], "completed")
                    self.assertEqual(receipts[0]["exitCode"], 0)
                    self.assertEqual(receipts[1]["state"], "completed")
                    self.assertEqual(receipts[1]["exitCode"], 23)
                    self.assertEqual(receipts[1]["cleanup"], "verified")

                    if fail_fast:
                        self.assertEqual(calls, ["first", "failure"])
                        self.assertEqual(receipts[2]["state"], "not-run-aborted")
                        self.assertIsNone(receipts[2]["exitCode"])
                    else:
                        self.assertEqual(calls, ["first", "failure", "diagnostic"])
                        self.assertEqual(receipts[2]["state"], "completed")
                        self.assertEqual(receipts[2]["exitCode"], 0)

                    validated = artifacts.validate_script_entry_receipts(
                        receipt_directory, ENTRIES, IDENTITY, fail_fast
                    )
                    self.assertEqual(validated, receipts)

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
        leader_pidfd = os.pidfd_open(process.pid)
        try:
            deadline = time.monotonic() + 5
            while artifacts._observe_supervised_process(process, leader_pidfd) is None:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            artifacts.extinguish_supervised_process_group(process, leader_pidfd)
            self.assertFalse(artifacts._signal_pinned_process_group(leader_pidfd, 0))
        finally:
            os.close(leader_pidfd)


    def test_interrupted_plan_rejects_terminal_entries(self) -> None:
        for terminal_state in ("candidate-terminal", "infrastructure-failure"):
            with self.subTest(terminal_state=terminal_state):
                def execute(_ordinal: int, _entry: dict[str, object]):
                    if terminal_state == "candidate-terminal":
                        raise artifacts.ScriptCandidateTerminal(
                            "candidate signal ended the entry",
                            signum=signal.SIGTERM,
                            cleanup_status="verified",
                            candidate_started=True,
                        )
                    raise artifacts.ScriptCandidateTerminal(
                        "cleanup failed after candidate exit",
                        return_code=0,
                        cleanup_status="failed",
                        candidate_started=True,
                    )

                with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                    artifacts.run_script_plan_with_receipts(
                        ENTRIES, IDENTITY, False, self.runner_temp, execute
                    )

                receipt_directory = caught.exception.receipt_directory
                plan_path = receipt_directory / "plan.json"
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                plan["state"] = "interrupted"
                plan["hostCancellationSignal"] = signal.SIGTERM
                plan_path.write_text(
                    json.dumps(plan, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )

                with self.assertRaises(artifacts.IntegrityError):
                    artifacts.validate_script_entry_receipts(
                        receipt_directory, ENTRIES, IDENTITY, False
                    )

    def test_unverified_pre_identity_cancellation_publishes_an_infrastructure_failure_plan(self) -> None:
        host_signals = artifacts.HostSignalQueue()

        def cancel_before_identity(_ordinal: int, _entry: dict[str, object]):
            host_signals.capture(signal.SIGTERM)
            raise artifacts.ScriptPlanInterrupted(signal.SIGTERM)

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES, IDENTITY, False, self.runner_temp, cancel_before_identity,
                host_signals=host_signals,
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipts = load_receipts(receipt_directory)
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)
        self.assertEqual(receipts[0]["state"], "infrastructure-failure")
        self.assertFalse(receipts[0]["candidateStarted"])
        self.assertEqual(receipts[0]["cleanup"], "unverified")
        self.assertEqual(receipts[0]["hostCancellationSignal"], signal.SIGTERM)

    def test_interrupted_receipt_is_single_and_starts_the_aborted_suffix(self) -> None:
        host_signals = artifacts.HostSignalQueue()

        def interrupt(_ordinal: int, _entry: dict[str, object]):
            host_signals.capture(signal.SIGTERM)
            interruption = artifacts.ScriptPlanInterrupted(signal.SIGTERM)
            interruption.cleanup_status = "verified"
            raise interruption

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES,
                IDENTITY,
                False,
                self.runner_temp,
                interrupt,
                host_signals=host_signals,
            )

        receipt_directory = caught.exception.receipt_directory
        second_entry_path = receipt_directory / "entry-000001.json"
        second_entry = json.loads(second_entry_path.read_text(encoding="utf-8"))
        second_entry["state"] = "interrupted"
        second_entry["hostCancellationSignal"] = signal.SIGTERM
        second_entry_path.write_text(
            json.dumps(second_entry, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

        with self.assertRaises(artifacts.IntegrityError):
            artifacts.validate_script_entry_receipts(
                receipt_directory, ENTRIES, IDENTITY, False
            )

    def test_interrupted_entry_cannot_follow_a_fail_fast_terminal_state(self) -> None:
        result, _calls, _receipts = self.run_plan(True)
        receipt_directory = result.receipt_directory
        plan_path = receipt_directory / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["state"] = "interrupted"
        plan["hostCancellationSignal"] = signal.SIGTERM
        plan_path.write_text(
            json.dumps(plan, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        last_entry_path = receipt_directory / "entry-000002.json"
        last_entry = json.loads(last_entry_path.read_text(encoding="utf-8"))
        last_entry["state"] = "interrupted"
        last_entry["hostCancellationSignal"] = signal.SIGTERM
        last_entry_path.write_text(
            json.dumps(last_entry, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

        with self.assertRaises(artifacts.IntegrityError):
            artifacts.validate_script_entry_receipts(
                receipt_directory, ENTRIES, IDENTITY, True
            )

    def test_unverified_cleanup_after_cancellation_keeps_host_provenance(self) -> None:
        host_signals = artifacts.HostSignalQueue()

        def execute(_ordinal: int, _entry: dict[str, object]):
            host_signals.capture(signal.SIGTERM)
            interruption = artifacts.ScriptPlanInterrupted(
                signal.SIGTERM,
                candidate_started=True,
                candidate_exit_code=0,
                supervisor_signal=signal.SIGTERM,
            )

            def verify_cleanup() -> bool:
                raise OSError("cleanup inventory unavailable")

            artifacts._verify_script_candidate_cleanup(
                None, interruption, verify_cleanup
            )

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES,
                IDENTITY,
                False,
                self.runner_temp,
                execute,
                host_signals=host_signals,
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipt = load_receipts(receipt_directory)[0]
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)
        self.assertEqual(receipt["state"], "infrastructure-failure")
        self.assertTrue(receipt["candidateStarted"])
        self.assertEqual(receipt["exitCode"], 0)
        self.assertIsNone(receipt["signal"])
        self.assertEqual(receipt["hostCancellationSignal"], signal.SIGTERM)
        self.assertEqual(receipt["supervisorSignal"], signal.SIGTERM)
        self.assertEqual(receipt["cleanup"], "unverified")
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )

    def test_prelaunch_cancellation_cleanup_failure_keeps_signal_without_start(self) -> None:
        host_signals = artifacts.HostSignalQueue()

        def execute(_ordinal: int, _entry: dict[str, object]):
            host_signals.capture(signal.SIGTERM)
            interruption = artifacts.ScriptPlanInterrupted(
                signal.SIGTERM,
                candidate_started=False,
                supervisor_signal=signal.SIGTERM,
            )
            interruption.cleanup_status = "not-started"
            raise artifacts._script_candidate_cleanup_failure(
                None, interruption, cleanup_status="failed"
            )

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES,
                IDENTITY,
                False,
                self.runner_temp,
                execute,
                host_signals=host_signals,
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipt = load_receipts(receipt_directory)[0]
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(plan["hostCancellationSignal"], signal.SIGTERM)
        self.assertEqual(receipt["state"], "infrastructure-failure")
        self.assertFalse(receipt["candidateStarted"])
        self.assertIsNone(receipt["exitCode"])
        self.assertIsNone(receipt["signal"])
        self.assertEqual(receipt["hostCancellationSignal"], signal.SIGTERM)
        self.assertEqual(receipt["supervisorSignal"], signal.SIGTERM)
        self.assertEqual(receipt["cleanup"], "failed")
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )


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
    def test_control_status_requires_one_in_order_identity_proof(self) -> None:
        records = self.records()
        without_proof = b"".join(json.dumps(record).encode("ascii") + b"\n" for record in records)
        encoded = self.encode(records).splitlines(keepends=True)
        duplicate_proof = b"".join([encoded[0], encoded[1], encoded[1], *encoded[2:]])
        old_version = b"".join(encoded).replace(b'"version":3', b'"version":2')
        with (
            patch.object(artifacts.subprocess, "Popen") as popen,
            patch.object(artifacts.signal, "pidfd_send_signal") as send_signal,
            patch.object(artifacts.os, "killpg") as kill_group,
        ):
            with self.assertRaisesRegex(artifacts.IntegrityError, "control lacks identity proof"):
                self.parse_status(without_proof, 23)
            with self.assertRaisesRegex(artifacts.IntegrityError, "identity proof is contradictory"):
                self.parse_status(duplicate_proof, 23)
            with self.assertRaisesRegex(artifacts.IntegrityError, "invalid envelope"):
                self.parse_status(old_version, 23)
            self.assertFalse(artifacts._trusted_event_prefix_is_valid(["ready", "control-accepted"]))
            self.assertFalse(artifacts._trusted_event_prefix_is_valid(["ready", "identity-proof", "identity-proof"]))
            self.assertTrue(artifacts._trusted_event_prefix_is_valid(["ready", "identity-proof", "control-accepted"]))
        popen.assert_not_called()
        send_signal.assert_not_called()
        kill_group.assert_not_called()

    def records(self) -> list[dict[str, object]]:
        return [
            {"version": 3, "pid": 1, "event": "ready", "pidNamespace": 1234},
            {"version": 3, "pid": 1, "event": "control-accepted"},
            {"version": 3, "pid": 1, "event": "candidate-started"},
            {
                "version": 3,
                "pid": 1,
                "event": "candidate-outcome",
                "exitCode": 23,
                "candidateSignal": None,
            },
            {"version": 3, "pid": 1, "event": "cleanup-start"},
            {"version": 3, "pid": 1, "event": "cleanup-complete"},
            {
                "version": 3,
                "pid": 1,
                "event": "terminal",
                "state": "candidate-exit",
                "candidateStarted": True,
                "exitCode": 23,
                "candidateSignal": None,
                "supervisorSignal": None,
                "cleanup": "complete",
                "reaped": 2,
                "control": "launch",
                "controlSignal": None,
            },
        ]

    @staticmethod
    def encode(records: list[dict[str, object]]) -> bytes:
        if (records and records[0].get("event") == "ready"
                and any(record.get("event") in {"control-accepted", "control-aborted"} for record in records)):
            proof = {"version": 3, "pid": 1, "event": "identity-proof",
                     "pidNamespace": records[0].get("pidNamespace"), "nonce": "ab" * 32}
            records = [records[0], proof, *records[1:]]
        return b"".join(
            json.dumps(record, separators=(",", ":")).encode("ascii") + b"\n"
            for record in records
        )

    @staticmethod
    def parse_status(data: bytes, returncode: int, **kwargs):
        return artifacts._parse_trusted_candidate_status(
            data, returncode, expected_identity_nonce="ab" * 32, **kwargs,
        )

    def test_trusted_wrapper_accepts_only_the_complete_ordered_status(self) -> None:
        status = self.parse_status(self.encode(self.records()), 23)
        self.assertEqual(status.state, "candidate-exit")
        self.assertEqual(status.return_code, 23)
        self.assertEqual(status.cleanup, "complete")
        self.assertEqual(status.reaped_count, 2)
        self.assertTrue(status.candidate_started)

    def test_supervisor_failure_with_verified_cleanup_remains_infrastructure_receipt(self) -> None:
        candidate_outcomes = (
            (143, None),
            (None, int(signal.SIGTERM)),
        )
        for exit_code, candidate_signal in candidate_outcomes:
            with self.subTest(exit_code=exit_code, candidate_signal=candidate_signal):
                records = self.records()
                outcome = records[3]
                outcome["exitCode"] = exit_code
                outcome["candidateSignal"] = candidate_signal
                terminal = records[-1]
                terminal["state"] = "supervisor-failure"
                terminal["exitCode"] = exit_code
                terminal["candidateSignal"] = candidate_signal
                status = self.parse_status(
                    self.encode(records), 125
                )
                failure = artifacts._script_candidate_status_failure(status)
                self.assertTrue(failure.infrastructure_failure)

                with tempfile.TemporaryDirectory() as temporary:
                    runner_temp = Path(temporary) / "runner-temp"
                    runner_temp.mkdir()

                    def execute(_ordinal: int, _entry: dict[str, object]):
                        raise failure

                    with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                        artifacts.run_script_plan_with_receipts(
                            ENTRIES, IDENTITY, False, runner_temp, execute
                        )

                    receipt_directory = caught.exception.receipt_directory
                    plan = json.loads(
                        (receipt_directory / "plan.json").read_text(encoding="utf-8")
                    )
                    receipt = load_receipts(receipt_directory)[0]
                    self.assertEqual(plan["state"], "infrastructure-failure")
                    self.assertEqual(receipt["state"], "infrastructure-failure")
                    self.assertEqual(receipt["cleanup"], "verified")
                    self.assertTrue(receipt["candidateStarted"])
                    self.assertEqual(receipt["exitCode"], exit_code)
                    self.assertEqual(receipt["signal"], candidate_signal)
                    artifacts.validate_script_entry_receipts(
                        receipt_directory, ENTRIES, IDENTITY, False
                    )

    def test_enoent_or_eagain_exec_failure_with_latched_sigterm_remains_infrastructure_failure(
        self,
    ) -> None:
        # PID 1 reports exec OSErrors such as ENOENT or EAGAIN as exec-failure.
        records = [
            {"version": 3, "pid": 1, "event": "ready", "pidNamespace": 1234},
            {"version": 3, "pid": 1, "event": "control-accepted"},
            {
                "version": 3,
                "pid": 1,
                "event": "candidate-not-started",
                "reason": "exec-failure",
            },
            {"version": 3, "pid": 1, "event": "cleanup-start"},
            {"version": 3, "pid": 1, "event": "cleanup-complete"},
            {
                "version": 3,
                "pid": 1,
                "event": "terminal",
                "state": "exec-failure",
                "candidateStarted": False,
                "exitCode": None,
                "candidateSignal": None,
                "supervisorSignal": None,
                "cleanup": "complete",
                "reaped": 0,
                "control": "launch",
                "controlSignal": None,
            },
        ]
        status = self.parse_status(
            self.encode(records),
            125,
            host_cancel_signal=signal.SIGTERM,
            expected_pid_namespace=1234,
        )
        self.assertEqual(status.state, "exec-failure")
        self.assertEqual(status.host_cancellation_signal, signal.SIGTERM)
        self.assertFalse(status.candidate_started)

        error = artifacts._script_candidate_status_failure(status)
        self.assertEqual(error.host_cancellation_signal, signal.SIGTERM)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        runner_temp = Path(temporary.name) / "runner-temp"
        runner_temp.mkdir()

        def execute(_ordinal: int, _entry: dict[str, object]):
            raise error

        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
            artifacts.run_script_plan_with_receipts(
                ENTRIES,
                IDENTITY,
                False,
                runner_temp,
                execute,
            )

        receipt_directory = caught.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipt = load_receipts(receipt_directory)[0]
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertIsNone(plan["hostCancellationSignal"])
        self.assertEqual(receipt["state"], "infrastructure-failure")
        self.assertIsNone(receipt["hostCancellationSignal"])
        self.assertFalse(receipt["candidateStarted"])
        self.assertIsNone(receipt["exitCode"])
        artifacts.validate_script_entry_receipts(
            receipt_directory, ENTRIES, IDENTITY, False
        )

    def test_supervisor_exec_oserror_with_latched_signal_keeps_infrastructure_status(self) -> None:
        original = artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT
        self.assertEqual(original.count("if os.getpid() != 1:"), 2)
        supervisor_source = original.replace("if os.getpid() != 1:", "if False:")
        supervisor_source = supervisor_source.replace(
            "def enforce_candidate_security_state():",
            "def enforce_candidate_security_state():\n    return",
            1,
        )
        cleanup_start = supervisor_source.index("def terminate_namespace_children():")
        control_start = supervisor_source.index("def read_identity_challenge():", cleanup_start)
        supervisor_source = (
            supervisor_source[:cleanup_start]
            + "def terminate_namespace_children():\n"
            + "    global cleanup_state\n"
            + "    cleanup_state = 'complete'\n"
            + "    return 0\n\n"
            + supervisor_source[control_start:]
        )
        supervisor_source = supervisor_source.replace(
            'record = {"version": 3, "pid": os.getpid(), "event": event}',
            'record = {"version": 3, "pid": 1, "event": event}',
            1,
        )
        self.assertNotIn("if os.getpid() != 1:", supervisor_source)

        for execution_errno in (errno.ENOENT, errno.EAGAIN):
            with self.subTest(exec_errno=execution_errno):
                prefix = (
                    "import errno, os, signal, subprocess\n"
                    "def fail_candidate_exec(*args, **kwargs):\n"
                    "    os.kill(os.getpid(), signal.SIGTERM)\n"
                    f"    raise OSError({execution_errno}, 'simulated candidate exec failure')\n"
                    "subprocess.Popen = fail_candidate_exec\n"
                )
                completed = subprocess.run(
                    [sys.executable, "-c", prefix + supervisor_source, "supervisor", "/missing-candidate"],
                    input=b"challenge:" + b"ab" * 32 + b"\nlaunch\n",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env={**os.environ, "PATH": "/usr/bin:/bin"},
                    timeout=10,
                    check=False,
                )
                self.assertEqual(completed.returncode, 125, completed.stderr.decode())
                records = [
                    json.loads(line)
                    for line in completed.stdout.splitlines()
                    if line
                ]
                self.assertIn(
                    {"event": "candidate-not-started", "reason": "exec-failure"},
                    [
                        {"event": record.get("event"), "reason": record.get("reason")}
                        for record in records
                    ],
                )
                terminal = records[-1]
                self.assertEqual(terminal["state"], "exec-failure")
                self.assertEqual(terminal["supervisorSignal"], signal.SIGTERM)
                self.assertFalse(terminal["candidateStarted"])
                self.assertEqual(terminal["cleanup"], "complete", terminal)
                self.assertIsNone(terminal["exitCode"])
                self.assertIsNone(terminal["candidateSignal"])

                try:
                    status = self.parse_status(
                        completed.stdout,
                        completed.returncode,
                        host_cancel_signal=signal.SIGTERM,
                        expected_pid_namespace=records[0]["pidNamespace"],
                    )
                except artifacts.IntegrityError as error:
                    self.fail(f"{error}; terminal={terminal!r}; events={records!r}")
                self.assertEqual(status.state, "exec-failure")
                self.assertEqual(status.host_cancellation_signal, signal.SIGTERM)
                self.assertEqual(status.supervisor_signal, signal.SIGTERM)
                error = artifacts._script_candidate_status_failure(status)

                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                runner_temp = Path(temporary.name) / "runner-temp"
                runner_temp.mkdir()

                def execute(_ordinal: int, _entry: dict[str, object]):
                    raise error

                with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                    artifacts.run_script_plan_with_receipts(
                        ENTRIES, IDENTITY, False, runner_temp, execute
                    )

                receipt_directory = caught.exception.receipt_directory
                plan = json.loads(
                    (receipt_directory / "plan.json").read_text(encoding="utf-8")
                )
                receipt = load_receipts(receipt_directory)[0]
                self.assertEqual(plan["state"], "infrastructure-failure")
                self.assertIsNone(plan["hostCancellationSignal"])
                self.assertEqual(receipt["state"], "infrastructure-failure")
                self.assertIsNone(receipt["hostCancellationSignal"])
                self.assertIsNone(receipt["supervisorSignal"])
                self.assertFalse(receipt["candidateStarted"])
                artifacts.validate_script_entry_receipts(
                    receipt_directory, ENTRIES, IDENTITY, False
                )

    def test_late_host_signal_after_pid1_candidate_outcome_preserves_terminal_receipts(self) -> None:
        cases = (
            ("import os, signal; os.kill(os.getpid(), signal.SIGTERM)", None, signal.SIGTERM),
            ("raise SystemExit(143)", 143, None),
        )
        for candidate_code, expected_exit_code, expected_candidate_signal in cases:
            with self.subTest(candidate_code=candidate_code):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    runner_temp = root / "runner-temp"
                    runner_temp.mkdir()
                    ready_marker = root / "candidate-outcome-ready"
                    release_marker = root / "candidate-outcome-release"
                    host_signals = artifacts.HostSignalQueue()

                    source = artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT
                    source = source.replace("if os.getpid() != 1:", "if False:")
                    source = source.replace(
                        "if libc.prctl(4, 0, 0, 0, 0) != 0 or libc.prctl(3, 0, 0, 0, 0) != 0:",
                        "if False:",
                        1,
                    )
                    source = source.replace(
                        "def enforce_candidate_security_state():",
                        "def enforce_candidate_security_state():\n    return",
                        1,
                    )
                    cleanup_start = source.index("def terminate_namespace_children():")
                    control_start = source.index("def read_identity_challenge():", cleanup_start)
                    source = (
                        source[:cleanup_start]
                        + "def terminate_namespace_children():\n"
                        + "    global cleanup_state\n"
                        + "    cleanup_state = 'complete'\n"
                        + "    return 0\n\n"
                        + source[control_start:]
                    )
                    source = source.replace(
                        'record = {"version": 3, "pid": os.getpid(), "event": event}',
                        'record = {"version": 3, "pid": 1, "event": event}',
                        1,
                    )
                    outcome_anchor = "candidate_outcome_emitted = True"
                    barrier = (
                        outcome_anchor
                        + "\n                with open(os.environ['TEST_OUTCOME_READY'], 'w', encoding='ascii') as marker:\n"
                        + "                    marker.write('ready')\n"
                        + "                while not os.path.exists(os.environ['TEST_OUTCOME_RELEASE']):\n"
                        + "                    time.sleep(0.005)"
                    )
                    source = source.replace(outcome_anchor, barrier, 1)

                    supervisor_wrapper = root / "fake-bwrap"
                    supervisor_wrapper.write_text(
                        textwrap.dedent(
                            """\
                            #!/usr/bin/python3
                            import json, os, subprocess, sys
                            status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])
                            inner = sys.argv[sys.argv.index('--') + 1:]
                            child = subprocess.Popen(
                                inner, stdin=sys.stdin.buffer, stdout=sys.stdout.buffer,
                                stderr=sys.stderr.buffer, close_fds=True,
                            )
                            namespace = os.stat(f'/proc/{child.pid}/ns/pid').st_ino
                            ready = {'child-pid': child.pid, 'pid-namespace': namespace}
                            os.write(status_fd, json.dumps(ready).encode() + bytes([10]))
                            exit_code = child.wait()
                            os.write(status_fd, json.dumps({'exit-code': exit_code}).encode() + bytes([10]))
                            raise SystemExit(exit_code)
                            """
                        ),
                        encoding="utf-8",
                    )
                    supervisor_wrapper.chmod(0o700)
                    command = [
                        str(supervisor_wrapper),
                        "--unshare-user", "--unshare-pid", "--as-pid-1",
                        "--unshare-ipc", "--unshare-uts", "--unshare-net",
                        "--cap-drop", "ALL", "--die-with-parent", "--new-session",
                        "--ro-bind", "/usr", "/usr",
                        "--ro-bind", "/bin", "/bin",
                        "--ro-bind", "/lib", "/lib",
                        "--ro-bind", "/lib64", "/lib64",
                        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                        "--chdir", "/", "--",
                        sys.executable, "-I", "-c", source,
                        sys.executable, "-I", "-c", candidate_code,
                    ]

                    def after_candidate_outcome(event: str) -> None:
                        if event == "candidate-outcome":
                            host_signals.capture(signal.SIGTERM)

                    real_pidfd_send_signal = artifacts.signal.pidfd_send_signal

                    def signal_then_release(pidfd: int, signum: int, info=None, flags=0) -> None:
                        real_pidfd_send_signal(pidfd, signum, info, flags)
                        if flags == artifacts.PIDFD_SIGNAL_PROCESS_GROUP:
                            return
                        forwarded_signals.append(signum)
                        if signum == signal.SIGTERM:
                            release_marker.touch()

                    def execute(_ordinal: int, _entry: dict[str, object]):
                        return artifacts.run_supervised_bubblewrap(
                            command,
                            environment={
                                "PATH": "/usr/bin:/bin",
                                "TEST_OUTCOME_READY": str(ready_marker),
                                "TEST_OUTCOME_RELEASE": str(release_marker),
                            },
                            pass_fds=(),
                            host_signals=host_signals,
                            test_hook=after_candidate_outcome,
                        )

                    forwarded_signals: list[int] = []
                    with patch.object(
                        artifacts.signal, "pidfd_send_signal", side_effect=signal_then_release
                    ):
                        with self.assertRaises(artifacts.ScriptPlanExecutionError) as caught:
                            artifacts.run_script_plan_with_receipts(
                                ENTRIES, IDENTITY, False, runner_temp, execute
                            )

                    receipt_directory = caught.exception.receipt_directory
                    plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
                    receipt = load_receipts(receipt_directory)[0]
                    self.assertTrue(
                        ready_marker.exists(),
                        json.dumps(
                            {
                                "plan": plan,
                                "receipt": receipt,
                                "cause": repr(caught.exception.__cause__),
                            },
                            sort_keys=True,
                        ),
                    )
                    self.assertTrue(release_marker.exists())
                    self.assertEqual(plan["state"], "infrastructure-failure")
                    self.assertIsNone(plan["hostCancellationSignal"])
                    self.assertEqual(
                        receipt["state"],
                        "candidate-terminal",
                        json.dumps(
                            {
                                "plan": plan,
                                "receipt": receipt,
                                "cause": repr(caught.exception.__cause__),
                            },
                            sort_keys=True,
                        ),
                    )
                    self.assertEqual(receipt["exitCode"], expected_exit_code)
                    self.assertEqual(receipt["signal"], expected_candidate_signal)
                    self.assertTrue(forwarded_signals)
                    self.assertEqual(receipt["cleanup"], "verified")
                    self.assertIsNone(receipt["hostCancellationSignal"])
                    self.assertIsNone(receipt["supervisorSignal"])
                    self.assertTrue(receipt["candidateStarted"])
                    artifacts.validate_script_entry_receipts(
                        receipt_directory, ENTRIES, IDENTITY, False
                    )

    def test_trusted_wrapper_rejects_missing_duplicate_reversed_or_malformed_evidence(self) -> None:
        valid = self.records()
        invalid_records: list[list[dict[str, object]]] = []
        invalid_records.append(valid[1:])
        invalid_records.append(valid[:2] + [valid[3], valid[2]] + valid[4:])
        invalid_records.append(valid[:4] + [valid[4], valid[4], valid[5]])
        invalid_records.append(valid[:2] + [{"version": 3, "pid": 1, "event": "unknown"}] + valid[2:])

        mismatched_outcome = [dict(record) for record in valid]
        mismatched_outcome[-1]["exitCode"] = 22
        invalid_records.append(mismatched_outcome)

        wrong_pid = [dict(record) for record in valid]
        wrong_pid[0]["pid"] = 2
        invalid_records.append(wrong_pid)

        wrong_cleanup = [dict(record) for record in valid]
        wrong_cleanup[-1]["cleanup"] = "failed"
        invalid_records.append(wrong_cleanup)

        for records in invalid_records:
            with self.subTest(records=records), self.assertRaises(artifacts.IntegrityError):
                self.parse_status(self.encode(records), 23)

        encoded = self.encode(valid)
        with self.assertRaises(artifacts.IntegrityError):
            self.parse_status(encoded[:-1], 23)
        with self.assertRaises(artifacts.IntegrityError):
            self.parse_status(
                encoded.replace(b'"version":3', b'"version":3,"version":3', 1), 23
            )
        with self.assertRaises(artifacts.IntegrityError):
            self.parse_status(encoded, 22)

    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires provisioned hosted Bubblewrap/AppArmor")
    def test_pid1_rejects_invalid_candidate_security_state_before_launch(self) -> None:
        bubblewrap = Path("/usr/bin/bwrap")
        self.assertTrue(bubblewrap.is_file(), "Bubblewrap required registered platform suite")
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
            "raise SystemExit(91)",
        ]
        with self.assertRaises(artifacts.ScriptCandidateTerminal) as caught:
            artifacts.run_supervised_bubblewrap(
                arguments,
                environment={
                    "PATH": "/usr/bin:/bin",
                    "VERJSON_CI_TEST_FORCE_INVALID_APPARMOR": "1",
                },
                pass_fds=(),
                cancellation_signal=lambda: None,
            )
        self.assertFalse(caught.exception.candidate_started)
        self.assertIsNone(caught.exception.return_code)
        self.assertIsNone(caught.exception.signum)

    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires provisioned hosted Bubblewrap/AppArmor")
    def test_pid1_cancellation_during_security_validation_aborts_before_launch(self) -> None:
        bubblewrap = Path("/usr/bin/bwrap")
        self.assertTrue(bubblewrap.is_file(), "Bubblewrap required in provisioned hosted environment")
        with tempfile.TemporaryDirectory() as directory:
            candidate_marker = Path(directory) / "started"
            arguments = [
                str(bubblewrap),
                "--unshare-user", "--unshare-pid", "--as-pid-1",
                "--unshare-ipc", "--unshare-uts", "--unshare-net",
                "--cap-drop", "ALL", "--die-with-parent", "--new-session",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/bin", "/bin",
                "--ro-bind", "/lib", "/lib",
                "--ro-bind", "/lib64", "/lib64",
                "--bind", directory, "/probe",
                "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                "--chdir", "/", "--",
                "/usr/bin/python3", "-I", "-c", artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT,
                "/usr/bin/python3", "-I", "-c",
                "from pathlib import Path; Path('/probe/started').write_text('started')",
            ]
            with self.assertRaises(
                (artifacts.ScriptCandidateTerminal, artifacts.ScriptPlanInterrupted)
            ) as caught:
                artifacts.run_supervised_bubblewrap(
                    arguments,
                    environment={
                        "PATH": "/usr/bin:/bin",
                        "VERJSON_CI_TEST_SIGNAL_DURING_SECURITY": "SIGTERM",
                    },
                    pass_fds=(),
                    cancellation_signal=lambda: None,
                )

            self.assertFalse(caught.exception.candidate_started)
            self.assertIsNone(
                getattr(caught.exception, "return_code", getattr(caught.exception, "candidate_exit_code", None))
            )
            self.assertFalse(candidate_marker.exists())

    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires provisioned hosted Bubblewrap/AppArmor")
    def test_real_recorded_connector_matches_isolated_supervisor_namespace(self) -> None:
        self._exercise_supervised_sender(inherited=False)

    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires provisioned hosted Bubblewrap/AppArmor")
    def test_real_supervised_inherited_writer_is_not_the_connector(self) -> None:
        self._exercise_supervised_sender(inherited=True)

    def _exercise_supervised_sender(self, *, inherited: bool) -> None:
        import compatibility_lane_peer as peer
        import socket

        with tempfile.TemporaryDirectory() as directory:
            notifications = queue.Queue(maxsize=1)
            notices = []
            matched = []
            proc_fd = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            self.addCleanup(os.close, proc_fd)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                listener.bind(directory + '/listener')
                listener.listen(1)
                listener.settimeout(3)

                def on_event(event):
                    if event == 'control-accepted':
                        notices.append(notifications.get_nowait())
                    elif event == 'candidate-started':
                        accepted, _ = listener.accept()
                        with accepted:
                            namespace_fd, supervisor_pidfd = notices[0].borrow()
                            try:
                                self.assertNotEqual(os.fstat(namespace_fd).st_ino, os.stat('/proc/self/ns/pid').st_ino)
                                peer_pidfd = peer.pin_recorded_connector(
                                    accepted, expected_uid=os.getuid(), supervisor_pidfd=supervisor_pidfd,
                                    supervisor_namespace_fd=namespace_fd, trusted_proc_fd=proc_fd,
                                )
                                try:
                                    def read_chunk():
                                        return peer.read_pinned_chunk(
                                            accepted, sender_pidfd=peer_pidfd, expected_uid=os.getuid(),
                                            trusted_proc_fd=proc_fd, deadline=time.monotonic() + 2,
                                        )
                                    if inherited:
                                        with self.assertRaisesRegex(peer.PeerEvidenceError, 'sender credentials do not match'):
                                            read_chunk()
                                        peer._assert_live(peer_pidfd)
                                    else:
                                        self.assertEqual(read_chunk(), b's')
                                finally:
                                    os.close(peer_pidfd)
                                matched.append(True)
                            finally:
                                os.close(supervisor_pidfd)
                                os.close(namespace_fd)

                candidate = (
                    "import os,socket,time\ns=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
                    "s.connect('/probe/listener')\n"
                    + ("pid=os.fork()\nif pid==0:\n s.sendall(b's')\n os._exit(0)\nos.waitpid(pid,0)\n"
                       if inherited else "s.sendall(b's')\n")
                    + "time.sleep(30)\n"
                )
                arguments = [
                    '/usr/bin/bwrap', '--unshare-user', '--unshare-pid', '--as-pid-1',
                    '--unshare-ipc', '--unshare-uts', '--unshare-net', '--cap-drop', 'ALL',
                    '--die-with-parent', '--new-session', '--ro-bind', '/usr', '/usr',
                    '--ro-bind', '/bin', '/bin', '--ro-bind', '/lib', '/lib',
                    '--ro-bind', '/lib64', '/lib64', '--ro-bind', directory, '/probe',
                    '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--chdir', '/',
                    '--', '/usr/bin/python3', '-I', '-c', artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT,
                    '/usr/bin/python3', '-I', '-c', candidate,
                ]
                with self.assertRaisesRegex(artifacts.ScriptCandidateTerminal, 'execution deadline'):
                    artifacts.run_supervised_bubblewrap(
                        arguments, environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
                        execution_timeout_seconds=6, namespace_notifications=notifications,
                        namespace_attempt_id='real-connector', test_hook=on_event,
                    )
                self.assertEqual(matched, [True])

    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires provisioned hosted Bubblewrap/AppArmor")
    def test_real_deadline_kills_a_candidate_and_its_new_session(self) -> None:
        marker = f'verjson-deadline-probe-{os.getpid()}'
        notifications = queue.Queue(maxsize=1)
        notices = []

        def on_event(event):
            if event == 'control-accepted':
                notice = notifications.get_nowait()
                namespace_fd, pidfd = notice.borrow()
                try:
                    self.assertEqual(os.fstat(namespace_fd).st_ino, notice.namespace_inode)
                    self.assertNotEqual(notice.namespace_inode, os.stat('/proc/self/ns/pid').st_ino)
                    self.assertFalse(select.select([pidfd], [], [], 0)[0])
                    notices.append(notice)
                finally:
                    os.close(namespace_fd)
                    os.close(pidfd)

        candidate = "import os,time\nif os.fork() == 0:\n os.setsid()\n time.sleep(30)\ntime.sleep(30)\n"
        arguments = [
            '/usr/bin/bwrap', '--unshare-user', '--unshare-pid', '--as-pid-1',
            '--unshare-ipc', '--unshare-uts', '--unshare-net', '--cap-drop', 'ALL',
            '--die-with-parent', '--new-session', '--ro-bind', '/usr', '/usr',
            '--ro-bind', '/bin', '/bin', '--ro-bind', '/lib', '/lib',
            '--ro-bind', '/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev',
            '--tmpfs', '/tmp', '--chdir', '/', '--', '/usr/bin/python3', '-I', '-c',
            artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT, '/usr/bin/python3', '-I', '-c', candidate, marker,
        ]
        with self.assertRaisesRegex(artifacts.ScriptCandidateTerminal, 'execution deadline') as caught:
            artifacts.run_supervised_bubblewrap(
                arguments, environment={'PATH': '/usr/bin:/bin'}, pass_fds=(), execution_timeout_seconds=3,
                namespace_notifications=notifications, namespace_attempt_id='real-lane', test_hook=on_event,
            )
        self.assertEqual(len(notices), 1)
        with self.assertRaises(artifacts.IntegrityError):
            notices[0].borrow()
        self.assertTrue(caught.exception.candidate_started)
        self.assertTrue(caught.exception.infrastructure_failure)
        processes = subprocess.run(['/bin/ps', '-eww', '-o', 'args='], check=True, capture_output=True, text=True)
        self.assertFalse(any(marker in line for line in processes.stdout.splitlines()))

    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires provisioned hosted Bubblewrap/AppArmor")
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

    def _run_pid1_supervisor_without_apparmor_gate(self, candidate: str) -> artifacts.TrustedCandidateStatus:
        # Exercise the real PID 1 reaping code without claiming local AppArmor enforcement.
        security_gate = "def enforce_candidate_security_state():"
        source = artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT
        self.assertEqual(source.count(security_gate), 1, "supervisor security gate fixture boundary changed")
        supervisor = source.replace(security_gate, security_gate + "\n    return", 1)
        bubblewrap = Path("/usr/bin/bwrap")
        self.assertTrue(bubblewrap.is_file(), "Bubblewrap is required for the registered platform suite")
        arguments = [
            str(bubblewrap), "--unshare-user", "--unshare-pid", "--as-pid-1",
            "--unshare-ipc", "--unshare-uts", "--unshare-net", "--cap-drop", "ALL",
            "--die-with-parent", "--new-session",
            "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--chdir", "/",
            "--", "/usr/bin/python3", "-I", "-c", supervisor,
            "/usr/bin/python3", "-I", "-c", candidate,
        ]
        return artifacts.run_supervised_bubblewrap(
            arguments,
            environment={"PATH": "/usr/bin:/bin"},
            pass_fds=(),
            cancellation_signal=lambda: None,
            execution_timeout_seconds=20,
        )

    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires Bubblewrap PID namespaces")
    def test_pid1_supervisor_reaps_an_orphan_while_the_candidate_still_runs(self) -> None:
        # The consumer pattern from #2043: kill a process group whose leader dies first,
        # then wait for kill(-pgid, 0) to fail. An orphan adopted by PID 1 stays a group
        # member until PID 1 reaps it, so the group disappears only if reaping is live.
        candidate = textwrap.dedent(
            """\
            import os, signal, time
            ready_read, ready_write = os.pipe()
            leader = os.fork()
            if leader == 0:
                os.setpgid(0, 0)
                if os.fork() == 0:
                    os.write(ready_write, b"g")
                    time.sleep(30)
                    os._exit(0)
                time.sleep(30)
                os._exit(0)
            os.setpgid(leader, leader)
            os.close(ready_write)
            if os.read(ready_read, 1) != b"g":
                raise SystemExit(125)
            os.kill(leader, signal.SIGKILL)
            os.waitpid(leader, 0)
            os.killpg(leader, signal.SIGKILL)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    os.killpg(leader, 0)
                except ProcessLookupError:
                    raise SystemExit(0)
                time.sleep(0.01)
            raise SystemExit(7)
            """
        )
        status = self._run_pid1_supervisor_without_apparmor_gate(candidate)
        self.assertEqual(
            (status.state, status.return_code),
            ("candidate-exit", 0),
            "killed process group outlived the candidate's wait: PID 1 left the orphan a zombie",
        )
        self.assertEqual(status.cleanup, "complete")
        self.assertEqual(status.reaped_count, 2)

    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires Bubblewrap PID namespaces")
    def test_pid1_orphan_reaping_preserves_a_failing_candidate_exit_status(self) -> None:
        # A reaper that consumes the candidate's own status makes Popen report exit 0,
        # turning a failing test command into a pass.
        status = self._run_pid1_supervisor_without_apparmor_gate("import time\ntime.sleep(0.2)\nraise SystemExit(37)\n")
        self.assertEqual((status.state, status.return_code), ("candidate-exit", 37))
        self.assertEqual(status.reaped_count, 1)


    def test_pidfd_esrch_after_simulated_pid1_exit_preserves_terminal_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bubblewrap = root / "fake-bwrap"
            marker = root / "candidate-outcome-observed"
            fake_bubblewrap.write_text(
                "#!/usr/bin/python3\n"
                "import json, os, sys, time\n"
                "status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])\n"
                "child_read, child_write = os.pipe()\n"
                "child = os.fork()\n"
                "if child == 0:\n"
                "    os.close(child_write)\n"
                "    os.read(child_read, 1)\n"
                "    os._exit(0)\n"
                "os.close(child_read)\n"
                "pid_namespace = os.stat(f'/proc/{child}/ns/pid').st_ino\n"
                "def emit(event, **fields):\n"
                "    record = {'version': 3, 'pid': 1, 'event': event, **fields}\n"
                "    sys.stdout.buffer.write(json.dumps(record, separators=(',', ':')).encode() + b'\\n')\n"
                "    sys.stdout.buffer.flush()\n"
                "def emit_bwrap(record):\n"
                "    os.write(status_fd, json.dumps(record, separators=(',', ':')).encode() + b'\\n')\n"
                "emit_bwrap({'child-pid': child, 'pid-namespace': pid_namespace})\n"
                "emit('ready', pidNamespace=pid_namespace)\n"
            "challenge = sys.stdin.buffer.readline()\n"
            "if not challenge.startswith(b'challenge:') or len(challenge) != 75: raise SystemExit(125)\n"
            "emit('identity-proof', pidNamespace=pid_namespace, nonce=challenge[10:-1].decode('ascii'))\n"
                "if sys.stdin.buffer.readline() != b'launch\\n':\n"
                "    raise SystemExit(125)\n"
                "emit('control-accepted')\n"
                "emit('candidate-started')\n"
                "os.write(child_write, b'x')\n"
                "os.close(child_write)\n"
                "os.waitpid(child, 0)\n"
                "emit('candidate-outcome', exitCode=0, candidateSignal=None)\n"
                "deadline = time.monotonic() + 5\n"
                "while not os.path.exists(os.environ['OUTCOME_MARKER']):\n"
                "    if time.monotonic() >= deadline:\n"
                "        raise SystemExit(125)\n"
                "    time.sleep(0.01)\n"
                "time.sleep(0.15)\n"
                "emit('cleanup-start')\n"
                "emit('cleanup-complete')\n"
                "emit('terminal', state='candidate-exit', candidateStarted=True, exitCode=0, candidateSignal=None, supervisorSignal=None, cleanup='complete', reaped=1, control='launch', controlSignal=None)\n"
                "emit_bwrap({'exit-code': 0})\n"
                "os.close(status_fd)\n",
                encoding="utf-8",
            )
            fake_bubblewrap.chmod(0o700)
            host_signals = artifacts.HostSignalQueue()
            signal_attempts: list[int] = []

            def capture_after_candidate_outcome(event: str) -> None:
                if event == "candidate-outcome":
                    host_signals.capture(signal.SIGTERM)
                    marker.write_text("observed", encoding="utf-8")

            actual_pidfd_send_signal = signal.pidfd_send_signal

            def exited_pid1(_pidfd: int, signum: int, info=None, flags=0) -> None:
                if flags == artifacts.PIDFD_SIGNAL_PROCESS_GROUP:
                    return actual_pidfd_send_signal(_pidfd, signum, info, flags)
                signal_attempts.append(signum)
                raise ProcessLookupError(errno.ESRCH, "PID 1 exited before signal delivery")

            with patch.object(
                artifacts.signal, "pidfd_send_signal", side_effect=exited_pid1
            ):
                with self.assertRaises(artifacts.ScriptPlanInterrupted) as caught:
                    artifacts.run_supervised_bubblewrap(
                        [str(fake_bubblewrap)],
                        environment={
                            "PATH": "/usr/bin:/bin",
                            "OUTCOME_MARKER": str(marker),
                        },
                        pass_fds=(),
                        host_signals=host_signals,
                        test_hook=capture_after_candidate_outcome,
                    )

        interruption = caught.exception
        self.assertEqual(signal_attempts, [signal.SIGTERM])
        self.assertEqual(interruption.signum, signal.SIGTERM)
        self.assertTrue(interruption.candidate_started)
        self.assertEqual(interruption.candidate_exit_code, 0)
        self.assertIsNone(interruption.candidate_signal)
        self.assertIsNone(interruption.supervisor_signal)
        self.assertEqual(interruption.cleanup_status, "verified")

    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires provisioned hosted BubbleWrap/AppArmor")
    def test_pidfd_esrch_after_candidate_outcome_drains_without_claiming_forwarding(self) -> None:
        bubblewrap = Path("/usr/bin/bwrap")
        self.assertTrue(bubblewrap.is_file(), "Bubblewrap required in provisioned hosted environment")
        host_signals = artifacts.HostSignalQueue()
        signal_attempts: list[int] = []

        def capture_after_candidate_outcome(event: str) -> None:
            if event == "candidate-outcome":
                host_signals.capture(signal.SIGTERM)

        actual_pidfd_send_signal = signal.pidfd_send_signal

        def supervisor_already_exited(_pidfd: int, signum: int, info=None, flags=0) -> None:
            # Group probes and cleanup reach the real kernel; only the direct
            # forward to the supervisor finds it already gone.
            if flags == artifacts.PIDFD_SIGNAL_PROCESS_GROUP:
                return actual_pidfd_send_signal(_pidfd, signum, info, flags)
            signal_attempts.append(signum)
            raise ProcessLookupError(errno.ESRCH, "PID 1 exited before signal delivery")

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
            "raise SystemExit(0)",
        ]

        with patch.object(
            artifacts.signal,
            "pidfd_send_signal",
            side_effect=supervisor_already_exited,
        ):
            with self.assertRaises(artifacts.ScriptPlanInterrupted) as caught:
                artifacts.run_supervised_bubblewrap(
                    arguments,
                    environment={"PATH": "/usr/bin:/bin"},
                    pass_fds=(),
                    host_signals=host_signals,
                    test_hook=capture_after_candidate_outcome,
                )

        interruption = caught.exception
        self.assertIn(signal_attempts, ([], [signal.SIGTERM]))
        self.assertEqual(interruption.signum, signal.SIGTERM)
        self.assertTrue(interruption.candidate_started)
        self.assertEqual(interruption.candidate_exit_code, 0)
        self.assertIsNone(interruption.candidate_signal)
        self.assertIsNone(interruption.supervisor_signal)
        self.assertEqual(interruption.cleanup_status, "verified")


class ExecutorParityTests(unittest.TestCase):
    def test_secretless_jobs_disclose_shared_workspace_risk_before_sandbox_execution(self) -> None:
        with (patch.object(artifacts.subprocess, "Popen"),
              patch.object(artifacts.signal, "pidfd_send_signal"),
              patch.object(artifacts.os, "killpg")):
            for filename in ("node-ci.yml", "node-ci-protected.yml"):
                with self.subTest(workflow=filename):
                    workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"))
                    steps = workflow["jobs"]["build-test"]["steps"]
                    names = [step.get("name") for step in steps]
                    warning_index = names.index("Disclose shared workspace isolation assumption")
                    self.assertLess(warning_index, names.index("Rebuild exact approved lifecycle packages without credentials"))
                    self.assertLess(warning_index, names.index("Run exact credentialless consumer script plan"))
                    warning = steps[warning_index]
                    self.assertIn("inputs.secretless-pr", warning["if"])
                    self.assertIn("inputs.secretless-trusted-ref", warning["if"])
                    script_plan = steps[names.index("Run exact credentialless consumer script plan")]
                    self.assertEqual(warning["if"], script_plan["if"])
                    self.assertIn("::warning title=Shared workspace isolation::", warning["run"])
                    self.assertIn("$GITHUB_STEP_SUMMARY", warning["run"])
                    self.assertIn("concurrent or leftover host writer can redirect mounts", warning["run"])

    def test_host_and_pid1_handlers_are_installed_before_inherited_masks_unblock(self) -> None:
        source_workflow = (ROOT / ".github/workflows/node-ci.yml").read_text(encoding="utf-8")
        helper_source = (ROOT / "scripts/ci_build_artifacts.py").read_text(encoding="utf-8")

        for source, start_marker, staging_marker in (
            (
                source_workflow,
                "host_signals = ci_build_artifacts.HostSignalQueue()",
                "def stage_runtime_cache(",
            ),
            (
                helper_source,
                "host_signals = ci_build_artifacts.HostSignalQueue()",
                "run_script_plan_with_receipts(",
            ),
        ):
            start = source.index(start_marker)
            block = source.index("signal.pthread_sigmask(signal.SIG_BLOCK", start)
            install = source.index("signal.signal(caught_signal", start)
            unblock = source.index("signal.pthread_sigmask(signal.SIG_UNBLOCK", start)
            staging = source.index(staging_marker, unblock)
            self.assertLess(block, install)
            self.assertLess(install, unblock)
            self.assertLess(unblock, staging)

        supervisor = artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT
        block = supervisor.index("signal.pthread_sigmask(signal.SIG_BLOCK, watched_signals)")
        install = supervisor.index("signal.signal(caught_signal, capture_signal)")
        unblock = supervisor.index("signal.pthread_sigmask(signal.SIG_UNBLOCK, watched_signals)")
        self.assertLess(block, install)
        self.assertLess(install, unblock)

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
        self.assertIn("candidate.wait()", protected)
        # PID 1 peeks before reaping so it never consumes the candidate's own status (#2043).
        self.assertIn(
            "os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)",
            artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT,
        )
        self.assertNotIn("os.waitpid(-1", artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT)

    def test_both_executor_adapters_use_shared_cleanup_verification(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/node-ci.yml").read_text(encoding="utf-8")
        )
        step = next(
            item
            for item in workflow["jobs"]["build-test"]["steps"]
            if item.get("id") == "run-script-plan"
        )
        protected = __import__("inspect").getsource(
            artifacts.run_protected_candidate_plan
        )
        generator = (ROOT / "scripts/gen-node-ci-protected.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("ci_build_artifacts._verify_script_candidate_cleanup(", step["run"])
        self.assertIn("ci_build_artifacts._verify_script_candidate_cleanup(", protected)
        self.assertIn("ci_build_artifacts._verify_script_candidate_cleanup(", generator)


    def test_executor_adapters_capture_prelaunch_failure_before_cleanup(self) -> None:
        source_workflow = (ROOT / ".github/workflows/node-ci.yml").read_text(encoding="utf-8")
        helper_source = (ROOT / "scripts/ci_build_artifacts.py").read_text(encoding="utf-8")
        generator_source = (ROOT / "scripts/gen-node-ci-protected.py").read_text(encoding="utf-8")

        source_launch = source_workflow.index("ci_build_artifacts.run_supervised_bubblewrap(")
        source_capture = source_workflow.index("candidate_failure = failure", source_launch)
        source_cleanup = source_workflow.index(
            "ci_build_artifacts._verify_script_candidate_cleanup(", source_capture
        )
        self.assertLess(source_launch, source_capture)
        self.assertLess(source_capture, source_cleanup)

        for adapter_name, adapter_source in (
            ("helper", helper_source),
            ("generator", generator_source),
        ):
            with self.subTest(adapter=adapter_name):
                launch_poll = adapter_source.rfind("received_signal = host_signals.poll()")
                captured_failure = adapter_source.index("candidate_failure = failure", launch_poll)
                cleanup = adapter_source.index("_verify_script_candidate_cleanup(", captured_failure)
                self.assertGreaterEqual(launch_poll, 0)
                self.assertLess(launch_poll, captured_failure)
                self.assertLess(captured_failure, cleanup)


if __name__ == "__main__":
    unittest.main()
