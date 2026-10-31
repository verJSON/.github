#!/usr/bin/env python3
"""Adversarial coverage for typed-level fail-fast receipts."""
from __future__ import annotations

import errno
from dataclasses import replace
import json
import os
import queue
import select
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch
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


class HostSignalQueueTests(unittest.TestCase):
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
        notices = []

        def on_event(event):
            if event == 'control-accepted':
                notice = notifications.get_nowait()
                notices.append(notice)
                borrowed.extend(notice.borrow())
                self.assertFalse(select.select([borrowed[1]], [], [], 0)[0])

        try:
            artifacts.run_supervised_bubblewrap(
                [str(fake_bubblewrap)], environment={'PATH': '/usr/bin:/bin'}, pass_fds=(),
                namespace_notifications=notifications, namespace_attempt_id='lane-1',
                execution_timeout_seconds=5, test_hook=on_event,
            )
            self.assertEqual(len(borrowed), 2)
            self.assertEqual(os.fstat(borrowed[0]).st_ino, notices[0].namespace_inode)
            self.assertTrue(select.select([borrowed[1]], [], [], 0)[0])
            with self.assertRaises(artifacts.IntegrityError):
                notices[0].borrow()
        finally:
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
            print(json.dumps({{'version': 2, 'pid': 1, 'event': 'ready', 'pidNamespace': namespace}}), flush=True)
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
            print(json.dumps({'version': 2, 'pid': 1, 'event': 'ready', 'pidNamespace': namespace}), flush=True)
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
                record = {{'version': 2, 'pid': 1, 'event': event, **fields}}
                sys.stdout.buffer.write(json.dumps(record).encode() + bytes([10]))
                sys.stdout.buffer.flush()
            emit_bwrap({{'child-pid': os.getpid(), 'pid-namespace': pid_namespace}})
            emit('ready', pidNamespace=pid_namespace)
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
            "    record = {'version': 2, 'pid': 1, 'event': event, **fields}\n"
            "    sys.stdout.buffer.write(json.dumps(record).encode() + b'\\n')\n"
            "    sys.stdout.buffer.flush()\n"
            "namespace = os.stat('/proc/self/ns/pid').st_ino\n"
            "emit_bwrap({'child-pid': os.getpid(), 'pid-namespace': namespace})\n"
            "emit('ready', pidNamespace=namespace)\n"
            "if sys.stdin.buffer.readline() != b'launch\\n': raise SystemExit(125)\n"
            "emit('control-accepted')\n"
            "candidate = subprocess.Popen(sys.argv[sys.argv.index('--') + 1:], close_fds=True)\n"
            "emit('candidate-started')\n"
            "candidate.wait()\n"
            "sys.stdout.buffer.write(b'{\\\"version\\\":2,\\\"pid\\\":1,\\\"event\\\":\\\"candidate-outcome\\\",\\\"exitCode\\\":0}\\n')\n"
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
            "    record = {'version': 2, 'pid': 1, 'event': event, **fields}\n"
            "    sys.stdout.buffer.write(json.dumps(record).encode() + b'\\n')\n"
            "    sys.stdout.buffer.flush()\n"
            "namespace = os.stat('/proc/self/ns/pid').st_ino\n"
            "emit_bwrap({'child-pid': os.getpid(), 'pid-namespace': namespace})\n"
            "emit('ready', pidNamespace=namespace)\n"
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

    def test_out_of_order_candidate_outcome_and_cleanup_failure_keep_only_start_evidence(self) -> None:
        fake_bubblewrap = self.root / "fake-bwrap-out-of-order-outcome"
        candidate_marker = self.root / "candidate-ran-before-out-of-order-outcome"
        survivor_pid_file = self.root / "survivor.pid"
        fake_bubblewrap.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, subprocess, sys\n"
            "status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])\n"
            "def emit_bwrap(record):\n"
            "    os.write(status_fd, json.dumps(record).encode() + b'\\n')\n"
            "def emit(event, **fields):\n"
            "    record = {'version': 2, 'pid': 1, 'event': event, **fields}\n"
            "    sys.stdout.buffer.write(json.dumps(record).encode() + b'\\n')\n"
            "    sys.stdout.buffer.flush()\n"
            "namespace = os.stat('/proc/self/ns/pid').st_ino\n"
            "emit_bwrap({'child-pid': os.getpid(), 'pid-namespace': namespace})\n"
            "emit('ready', pidNamespace=namespace)\n"
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
            )

        try:
            with patch.object(
                artifacts,
                "extinguish_supervised_process_group",
                side_effect=artifacts.IntegrityError("simulated final cleanup verification failure"),
            ):
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
            "    record = {'version': 2, 'pid': 1, 'event': event, **fields}\n"
            "    sys.stdout.buffer.write(json.dumps(record).encode() + b'\\n')\n"
            "    sys.stdout.buffer.flush()\n"
            "namespace = os.stat('/proc/self/ns/pid').st_ino\n"
            "emit_bwrap({'child-pid': os.getpid(), 'pid-namespace': namespace})\n"
            "emit('ready', pidNamespace=namespace)\n"
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

                def after_outcome(event: str) -> None:
                    if event == "candidate-outcome":
                        host_signals.capture(signal.SIGTERM)
                        marker.touch()

                def already_exited(_pidfd: int, signum: int) -> None:
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

        def after_outcome(event: str) -> None:
            if event == "candidate-outcome":
                host_signals.capture(signal.SIGTERM)
                marker.touch()

        def denied(_pidfd: int, signum: int) -> None:
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
        self.assertEqual(process.wait(timeout=5), 0)
        artifacts.extinguish_supervised_process_group(process)
        with self.assertRaises(ProcessLookupError):
            os.killpg(process.pid, 0)


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
    def records(self) -> list[dict[str, object]]:
        return [
            {"version": 2, "pid": 1, "event": "ready", "pidNamespace": 1234},
            {"version": 2, "pid": 1, "event": "control-accepted"},
            {"version": 2, "pid": 1, "event": "candidate-started"},
            {
                "version": 2,
                "pid": 1,
                "event": "candidate-outcome",
                "exitCode": 23,
                "candidateSignal": None,
            },
            {"version": 2, "pid": 1, "event": "cleanup-start"},
            {"version": 2, "pid": 1, "event": "cleanup-complete"},
            {
                "version": 2,
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
        return b"".join(
            json.dumps(record, separators=(",", ":")).encode("ascii") + b"\n"
            for record in records
        )

    def test_trusted_wrapper_accepts_only_the_complete_ordered_status(self) -> None:
        status = artifacts._parse_trusted_candidate_status(self.encode(self.records()), 23)
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
                status = artifacts._parse_trusted_candidate_status(
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
            {"version": 2, "pid": 1, "event": "ready", "pidNamespace": 1234},
            {"version": 2, "pid": 1, "event": "control-accepted"},
            {
                "version": 2,
                "pid": 1,
                "event": "candidate-not-started",
                "reason": "exec-failure",
            },
            {"version": 2, "pid": 1, "event": "cleanup-start"},
            {"version": 2, "pid": 1, "event": "cleanup-complete"},
            {
                "version": 2,
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
        status = artifacts._parse_trusted_candidate_status(
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
        control_start = supervisor_source.index("def read_control():", cleanup_start)
        supervisor_source = (
            supervisor_source[:cleanup_start]
            + "def terminate_namespace_children():\n"
            + "    global cleanup_state\n"
            + "    cleanup_state = 'complete'\n"
            + "    return 0\n\n"
            + supervisor_source[control_start:]
        )
        supervisor_source = supervisor_source.replace(
            'record = {"version": 2, "pid": os.getpid(), "event": event}',
            'record = {"version": 2, "pid": 1, "event": event}',
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
                    input=b"launch\n",
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
                    status = artifacts._parse_trusted_candidate_status(
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
                    control_start = source.index("def read_control():", cleanup_start)
                    source = (
                        source[:cleanup_start]
                        + "def terminate_namespace_children():\n"
                        + "    global cleanup_state\n"
                        + "    cleanup_state = 'complete'\n"
                        + "    return 0\n\n"
                        + source[control_start:]
                    )
                    source = source.replace(
                        'record = {"version": 2, "pid": os.getpid(), "event": event}',
                        'record = {"version": 2, "pid": 1, "event": event}',
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

                    def signal_then_release(pidfd: int, signum: int) -> None:
                        real_pidfd_send_signal(pidfd, signum)
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
        invalid_records.append(valid[:2] + [{"version": 2, "pid": 1, "event": "unknown"}] + valid[2:])

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
                artifacts._parse_trusted_candidate_status(self.encode(records), 23)

        encoded = self.encode(valid)
        with self.assertRaises(artifacts.IntegrityError):
            artifacts._parse_trusted_candidate_status(encoded[:-1], 23)
        with self.assertRaises(artifacts.IntegrityError):
            artifacts._parse_trusted_candidate_status(
                encoded.replace(b'"version":2', b'"version":2,"version":2', 1), 23
            )
        with self.assertRaises(artifacts.IntegrityError):
            artifacts._parse_trusted_candidate_status(encoded, 22)

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
                "    record = {'version': 2, 'pid': 1, 'event': event, **fields}\n"
                "    sys.stdout.buffer.write(json.dumps(record, separators=(',', ':')).encode() + b'\\n')\n"
                "    sys.stdout.buffer.flush()\n"
                "def emit_bwrap(record):\n"
                "    os.write(status_fd, json.dumps(record, separators=(',', ':')).encode() + b'\\n')\n"
                "emit_bwrap({'child-pid': child, 'pid-namespace': pid_namespace})\n"
                "emit('ready', pidNamespace=pid_namespace)\n"
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

            def exited_pid1(_pidfd: int, signum: int) -> None:
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

        def supervisor_already_exited(_pidfd: int, signum: int) -> None:
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
        self.assertIn("os.waitpid(-1, os.WNOHANG)", protected)

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
