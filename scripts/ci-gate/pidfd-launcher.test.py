#!/usr/bin/python3
import ctypes
import errno
import fcntl
import gc
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).with_name("pidfd-launcher.c")


def run_inside_namespace(library_path, unrelated_library_path):
    if os.getpid() != 1:
        raise RuntimeError("native process tests require an isolated PID namespace")
    signal.alarm(20)
    sys.path.insert(0, str(SOURCE.parent.parent))
    import ci_build_artifacts as artifacts

    expected_digest = hashlib.sha256(Path(library_path).read_bytes()).hexdigest()
    link_path = Path(library_path + ".link")
    link_path.symlink_to(library_path)
    try:
        try:
            artifacts._load_atomic_launcher(link_path, expected_digest)
        except artifacts.IntegrityError:
            pass
        else:
            raise AssertionError("launcher loader accepted a symlink")
    finally:
        link_path.unlink()
    reserve_fd = os.open(library_path, os.O_RDONLY | os.O_CLOEXEC)
    unrelated_fd = os.memfd_create("unrelated-library", os.MFD_CLOEXEC)
    os.write(unrelated_fd, Path(unrelated_library_path).read_bytes())
    unrelated_library = ctypes.CDLL(f"/proc/self/fd/{unrelated_fd}")
    os.close(unrelated_fd)
    os.close(reserve_fd)
    launch = artifacts._load_atomic_launcher(Path(library_path), expected_digest)
    with tempfile.TemporaryDirectory() as mount_directory:
        mount_root = Path(mount_directory)
        (mount_root / "source").write_bytes(b"verified mount")
        (mount_root / "link").symlink_to("source")
        (mount_root / "directory-link").symlink_to("/tmp", target_is_directory=True)
        mount_owner = artifacts.AtomicMountDescriptors(launch, 6)
        try:
            mount_owner.open_at(0, None, "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            mount_owner.open_at(1, 0, "tmp", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            mount_owner.open_at(2, 1, mount_root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            mount_owner.open_at(3, 2, "source", os.O_RDONLY | os.O_NOFOLLOW)
            mount_owner.duplicate(4, 3)
            with mount_owner.lease() as lease:
                source_fd, duplicate_fd = lease.descriptor(3), lease.descriptor(4)
                if os.fstat(source_fd).st_ino != os.fstat(duplicate_fd).st_ino:
                    raise AssertionError("owned mount duplicate changed identity")
                if not fcntl.fcntl(duplicate_fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
                    raise AssertionError("owned mount duplicate is inheritable")
                if os.read(source_fd, 64) != b"verified mount":
                    raise AssertionError("owned mount source changed")
            try:
                mount_owner.open_at(5, 2, "link", os.O_RDONLY | os.O_NOFOLLOW)
            except artifacts.IntegrityError:
                pass
            else:
                raise AssertionError("owned mount open followed a symlink")
            for component in ("directory-link", "source"):
                try:
                    mount_owner.open_at(5, 2, component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                except artifacts.IntegrityError:
                    pass
                else:
                    raise AssertionError("owned mount traversal accepted a symlink or non-directory")
        finally:
            mount_owner.close_all()
        if mount_owner in artifacts._retained_atomic_owners:
            raise AssertionError("retired mount owner remained registered")
        verified_owner = artifacts.AtomicMountDescriptors(launch, 3)
        try:
            metadata = mount_root.stat()
            expected_identity = (metadata.st_dev, metadata.st_ino, metadata.st_uid,
                                 artifacts.stat.S_IMODE(metadata.st_mode))
            verified_owner.open_verified_directory(2, mount_root, expected_identity)
            with verified_owner.lease() as lease:
                if os.fstat(lease.descriptor(2)).st_ino != metadata.st_ino:
                    raise AssertionError("verified mount traversal opened a different directory")
        finally:
            verified_owner.close_all()
        rejected_owner = artifacts.AtomicMountDescriptors(launch, 3)
        try:
            try:
                rejected_owner.open_verified_directory(2, mount_root / "directory-link", expected_identity)
            except artifacts.IntegrityError:
                pass
            else:
                raise AssertionError("verified mount traversal followed a symlink")
        finally:
            rejected_owner.close_all()
    create_owned_pipe = launch._trusted_library.verjson_owned_pipe
    create_owned_pipe.argtypes = [ctypes.POINTER(ctypes.c_int)]
    create_owned_pipe.restype = ctypes.c_int
    close_owned = launch._trusted_library.verjson_owned_close
    close_owned.argtypes = [ctypes.POINTER(ctypes.c_int)]
    close_owned.restype = ctypes.c_int
    slots = (ctypes.c_int * 2)(-1, -1)
    if create_owned_pipe(slots) != 0:
        raise AssertionError("native owned pipe creation failed")
    for index in range(2):
        if not fcntl.fcntl(slots[index], fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
            raise AssertionError("native owned pipe was inheritable")
        slot = ctypes.cast(ctypes.byref(slots, index * ctypes.sizeof(ctypes.c_int)), ctypes.POINTER(ctypes.c_int))
        if close_owned(slot) != 0 or slots[index] != -1 or close_owned(slot) != 0:
            raise AssertionError("native owned close did not retire its slot")
    with subprocess.Popen(["/usr/bin/sleep", "1"]) as child:
        for acquire in (launch._owned_pidfd_open, launch._owned_pid_namespace):
            slot = ctypes.c_int(-1)
            if acquire(ctypes.byref(slot), 1) != -errno.EINVAL or slot.value != -1:
                raise AssertionError("native acquisition accepted an invalid PID")
            if acquire(ctypes.byref(slot), child.pid) != 0 or slot.value < 0:
                raise AssertionError("native acquisition did not publish its descriptor")
            if not fcntl.fcntl(slot.value, fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
                raise AssertionError("native acquired descriptor is inheritable")
            if acquire is launch._owned_pid_namespace and os.fstat(slot.value).st_ino != os.stat(f"/proc/{child.pid}/ns/pid").st_ino:
                raise AssertionError("native acquisition opened the wrong PID namespace")
            if acquire(ctypes.byref(slot), child.pid) != -errno.EINVAL:
                raise AssertionError("native acquisition overwrote an occupied slot")
            duplicate = ctypes.c_int(-1)
            if launch._owned_dup(ctypes.byref(duplicate), slot.value) != 0 or duplicate.value == slot.value:
                raise AssertionError("native duplication did not publish a separate descriptor")
            if not fcntl.fcntl(duplicate.value, fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
                raise AssertionError("native duplicate is inheritable")
            if os.fstat(duplicate.value).st_ino != os.fstat(slot.value).st_ino:
                raise AssertionError("native duplicate refers to a different object")
            if launch._owned_dup(ctypes.byref(duplicate), slot.value) != -errno.EINVAL:
                raise AssertionError("native duplication overwrote an occupied slot")
            if close_owned(ctypes.byref(duplicate)) != 0 or duplicate.value != -1:
                raise AssertionError("native duplicate was not retired")
            if close_owned(ctypes.byref(slot)) != 0 or slot.value != -1:
                raise AssertionError("native acquired descriptor was not retired")
    owned_slots = artifacts.AtomicDescriptorSlots(launch._owned_pipe, launch._owned_close)
    with owned_slots.lease():
        owned_slots.open_pair(0)
        owned_slots.close(0)
        owned_slots.close_all()
    owned_child = artifacts.AtomicDescriptorSlots(launch._owned_pipe, launch._owned_close, launch._owned_reap)
    with owned_child.lease():
        for index in (0, 2, 4, 6, 8):
            owned_child.open_pair(index)
        for index in (4, 7, 8):
            owned_child.set_nonblocking(index)
        argv = (ctypes.c_char_p * 2)(b"/usr/bin/true", None)
        environment = (ctypes.c_char_p * 1)(None)
        if owned_child.launch(launch, (), None, argv, environment) != 0:
            raise AssertionError("slot-owned native launch failed")
        for index in (0, 1, 3, 5, 6, 9):
            owned_child.close(index)
        deadline = time.monotonic() + 5
        while 4 not in owned_child.wait_readable((4,), 0.05):
            if time.monotonic() >= deadline:
                raise AssertionError("slot-owned launcher readiness timed out")
        owned_child.read_ready()
        if not owned_child.signal_group(0):
            raise AssertionError("slot-owned launcher did not become ready")
        owned_child.release(artifacts.HostSignalQueue())
        owned_child.close(7)
        while 8 not in owned_child.wait_readable((8,), 0.05):
            if time.monotonic() >= deadline:
                raise AssertionError("slot-owned launcher exec channel timed out")
        if owned_child.read(8, 1) != b"" or owned_child.reap(5) != 0:
            raise AssertionError("slot-owned launcher did not exit cleanly")
        if owned_child.signal_group(0):
            raise AssertionError("slot-owned launcher group survived reaping")
        owned_child.close_all()
    status_fixture = Path(library_path).with_name("status-fixture")
    status_fixture.write_text(
        "#!/usr/bin/python3\nimport os,sys\n"
        "fd=int(sys.argv[sys.argv.index('--json-status-fd')+1])\n"
        "os.write(fd,b'status')\n", encoding="ascii",
    )
    status_fixture.chmod(0o700)
    status_owner = artifacts.AtomicDescriptorSlots(launch._owned_pipe, launch._owned_close, launch._owned_reap)
    with status_owner.lease():
        for index in (0, 2, 4, 6, 8, 11):
            status_owner.open_pair(index)
        for index in (4, 7, 8, 11):
            status_owner.set_nonblocking(index)
        if status_owner.launch_bubblewrap(launch, [str(status_fixture)], {}, (), None) != 0:
            raise AssertionError("status-owned native launch failed")
        for index in (0, 1, 3, 5, 6, 9, 12):
            status_owner.close(index)
        deadline = time.monotonic() + 5
        while 4 not in status_owner.wait_readable((4,), 0.05):
            if time.monotonic() >= deadline:
                raise AssertionError("status launcher readiness timed out")
        status_owner.read_ready()
        if not status_owner.signal_group(0):
            raise AssertionError("status launcher group is unavailable")
        status_owner.release(artifacts.HostSignalQueue())
        status_owner.close(7)
        while 11 not in status_owner.wait_readable((11,), 0.05):
            if time.monotonic() >= deadline:
                raise AssertionError("status launcher output timed out")
        if status_owner.read(11, 32) != b"status" or status_owner.reap(5) != 0:
            raise AssertionError("native status descriptor was not inherited correctly")
        if status_owner.read(11, 32) != b"":
            raise AssertionError("parent status writer prevented EOF")
        if status_owner.signal_group(0):
            raise AssertionError("status launcher group survived reaping")
        status_owner.close_all()
    descendant_owner = artifacts.AtomicDescriptorSlots(launch._owned_pipe, launch._owned_close, launch._owned_reap)
    with descendant_owner.lease():
        for index in (0, 2, 4, 6, 8):
            descendant_owner.open_pair(index)
        for index in (4, 7, 8):
            descendant_owner.set_nonblocking(index)
        code = b"import subprocess; subprocess.Popen(['/usr/bin/sleep', '30'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
        argv = (ctypes.c_char_p * 5)(b"/usr/bin/python3", b"-I", b"-c", code, None)
        environment = (ctypes.c_char_p * 1)(None)
        if descendant_owner.launch(launch, (), None, argv, environment) != 0:
            raise AssertionError("descendant launcher failed")
        for index in (0, 1, 3, 5, 6, 9):
            descendant_owner.close(index)
        deadline = time.monotonic() + 5
        while 4 not in descendant_owner.wait_readable((4,), 0.05):
            if time.monotonic() >= deadline:
                raise AssertionError("descendant launcher readiness timed out")
        descendant_owner.read_ready()
        if not descendant_owner.signal_group(0):
            raise AssertionError("descendant launcher group is unavailable")
        descendant_owner.release(artifacts.HostSignalQueue())
        descendant_owner.close(7)
        if descendant_owner.reap(5) != 0 or not descendant_owner.signal_group(0):
            raise AssertionError("live descendant group was not retained after leader exit")
        try:
            descendant_owner.close(10)
        except artifacts.IntegrityError:
            pass
        else:
            raise AssertionError("live descendant group allowed pidfd closure")
        descendant_owner.signal_group(signal.SIGKILL)
        os.waitpid(-1, 0)
        if descendant_owner.signal_group(0):
            raise AssertionError("descendant group survived pinned cleanup")
        descendant_owner.close_all()
    descriptors_before = len(os.listdir("/proc/self/fd"))
    second_launch = artifacts._load_atomic_launcher(Path(library_path), expected_digest)
    if second_launch is not launch or len(os.listdir("/proc/self/fd")) != descriptors_before:
        raise AssertionError("matching launcher was not reused without opening another descriptor")
    del second_launch
    gc.collect()
    if len(os.listdir("/proc/self/fd")) != descriptors_before:
        raise AssertionError("reusing the launcher leaked a descriptor")
    Path(library_path).write_bytes(b"replaced after loading")
    try:
        artifacts._load_atomic_launcher(Path(library_path), expected_digest)
    except artifacts.IntegrityError:
        pass
    else:
        raise AssertionError("a library with a mismatched digest was accepted")
    replacement_digest = hashlib.sha256(Path(library_path).read_bytes()).hexdigest()
    try:
        artifacts._load_atomic_launcher(Path(library_path), replacement_digest)
    except artifacts.IntegrityError:
        pass
    else:
        raise AssertionError("a malformed replacement reused an earlier loaded library")

    child = artifacts._spawn_atomic_supervisor(["/usr/bin/true"], {"PATH": "/usr/bin:/bin"}, (), None, launch)
    try:
        signals = artifacts.HostSignalQueue()
        artifacts._await_atomic_launcher_ready(child, signals, time.monotonic() + 5)
        if not artifacts._signal_pinned_process_group(child.pidfd, 0):
            raise AssertionError("atomic wrapper lost its process group")
        os.write(child.gate_fd, b"G")
        os.close(child.gate_fd)
        child.gate_fd = -1
        artifacts._await_atomic_launcher_error_channel(child, signals, time.monotonic() + 5)
        artifacts._reap_pinned_supervisor(child, child.pidfd, 5)
        if child.returncode != 0:
            raise AssertionError("atomic wrapper did not retain the child outcome")
    finally:
        child.stdin.close()
        child.stdout.close()
        for descriptor in (child.pidfd, child.ready_fd, child.gate_fd, child.error_fd):
            if descriptor >= 0:
                os.close(descriptor)

    receipt_test_path = SOURCE.parent / "ci-entry-receipts.test.py"
    spec = importlib.util.spec_from_file_location("atomic_receipt_tests", receipt_test_path)
    receipt_tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(receipt_tests)
    original_supervisor = artifacts.run_supervised_bubblewrap

    def atomic_supervisor(*args, **kwargs):
        kwargs["_atomic_launcher"] = launch
        return original_supervisor(*args, **kwargs)

    cases = unittest.TestSuite(
        receipt_tests.EntryReceiptSupervisorTests(name) for name in (
            "test_namespace_event_is_published_before_launch_with_owned_handles",
            "test_borrowed_pidfd_reports_supervisor_exit_after_notice_revocation",
            "test_full_namespace_queue_prevents_candidate_launch",
            "test_supervisor_accepts_clean_completion_before_its_deadline",
            "test_supervised_ambiguous_exit_is_preserved_in_entry_receipt",
            "test_candidate_started_cleanup_failure_preserves_cancellation_uncertainty",
            "test_stale_identity_proof_cannot_publish_or_signal_inner_supervisor",
            "test_missing_identity_proof_cannot_publish_or_signal_inner_supervisor",
            "test_cancellation_during_missing_proof_aborts_without_signaling_inner",
            "test_transient_cancellation_source_failure_during_challenge_withholds_plan",
            "test_cancellation_before_readiness_aborts_without_signaling_inner",
        )
    )
    receipt_output = io.StringIO()
    with patch.object(artifacts, "run_supervised_bubblewrap", side_effect=atomic_supervisor):
        receipt_result = unittest.TextTestRunner(stream=receipt_output).run(cases)
    if not receipt_result.wasSuccessful():
        raise AssertionError(receipt_output.getvalue())
    receipt_case = receipt_tests.EntryReceiptSupervisorTests("test_namespace_event_is_published_before_launch_with_owned_handles")
    receipt_case.setUp()
    try:
        fake_bubblewrap = receipt_case.root / "fake-bwrap-revocation-failure"
        receipt_case.write_fake_bubblewrap(fake_bubblewrap, state="candidate-exit", exit_code=0,
                                           candidate_signal=None, bubblewrap_exit=0)
        mount_fixture = receipt_case.root / "fake-bwrap-owned-mount"
        receipt_case.write_fake_bubblewrap(mount_fixture, state="candidate-exit", exit_code=0,
                                           candidate_signal=None, bubblewrap_exit=0)
        fixture_source = mount_fixture.read_text(encoding="utf-8")
        fixture_source = fixture_source.replace(
            "status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])",
            "mount_path = sys.argv[sys.argv.index('--ro-bind') + 1]\n"
            "if open(mount_path, 'rb').read() != b'owned mount': raise SystemExit(126)\n"
            "status_fd = int(sys.argv[sys.argv.index('--json-status-fd') + 1])",
            1,
        )
        mount_fixture.write_text(fixture_source, encoding="utf-8")
        with tempfile.TemporaryDirectory() as mount_directory:
            mount_root = Path(mount_directory)
            (mount_root / "source").write_bytes(b"owned mount")
            mount_owner = artifacts.AtomicMountDescriptors(launch, 4)
            try:
                mount_owner.open_at(0, None, "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                mount_owner.open_at(1, 0, "tmp", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                mount_owner.open_at(2, 1, mount_root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                mount_owner.open_at(3, 2, "source", os.O_RDONLY | os.O_NOFOLLOW)
                with mount_owner.lease() as lease:
                    metadata = os.fstat(lease.descriptor(3))
                for index in (0, 1, 2):
                    mount_owner.close(index)
                mount_owner.seal({3: (metadata.st_dev, metadata.st_ino, metadata.st_uid,
                                       artifacts.stat.S_IMODE(metadata.st_mode))})
                result = original_supervisor(
                    [str(mount_fixture), "--ro-bind", "/proc/self/fd/@verjson-slot-3@", "/unused"],
                    environment={"PATH": "/usr/bin:/bin"}, pass_fds=(), execution_timeout_seconds=5,
                    _atomic_launcher=launch, _mount_owner=mount_owner, _mount_slots=(3,),
                )
                if result.return_code != 0 or not result.candidate_started:
                    raise AssertionError("slot-owned inherited mount did not reach the supervisor")
            finally:
                mount_owner.close_all()
        notifications = artifacts.queue.Queue(maxsize=1)
        original_revoke = artifacts.PinnedSupervisorNamespace._revoke

        def fail_after_revoke(notice):
            original_revoke(notice)
            raise OSError("injected revocation failure")

        with (patch.object(artifacts.PinnedSupervisorNamespace, "_revoke", fail_after_revoke),
              patch.object(artifacts, "_reap_pinned_supervisor", wraps=artifacts._reap_pinned_supervisor) as reap):
            try:
                original_supervisor([str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                                    namespace_notifications=notifications, namespace_attempt_id="lane-1",
                                    execution_timeout_seconds=5, _atomic_launcher=launch)
            except artifacts.ScriptCandidateTerminal as error:
                if not error.candidate_started or error.return_code != 0 or error.cleanup_status != "unverified":
                    raise AssertionError("revocation failure lost candidate evidence") from error
            else:
                raise AssertionError("revocation failure was accepted")
            if not reap.called:
                raise AssertionError("revocation failure bypassed supervisor reaping")
        for event, candidate_started in (("control-accepted", False), ("candidate-outcome", True)):
            notifications = artifacts.queue.Queue(maxsize=1)

            def interrupt_at_checkpoint(observed):
                if observed == event:
                    interruption = artifacts.ScriptPlanInterrupted(signal.SIGTERM)
                    interruption.cancellation_evidence_unavailable = True
                    raise interruption

            with patch.object(artifacts.PinnedSupervisorNamespace, "_revoke", fail_after_revoke):
                try:
                    original_supervisor([str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                                        namespace_notifications=notifications, namespace_attempt_id="lane-1",
                                        execution_timeout_seconds=5, test_hook=interrupt_at_checkpoint,
                                        _atomic_launcher=launch)
                except artifacts.ScriptCandidateTerminal as error:
                    if (error.candidate_started != candidate_started or error.host_cancellation_signal != signal.SIGTERM
                            or not getattr(error, "cancellation_evidence_unavailable", False)
                            or (candidate_started and error.return_code != 0)):
                        raise AssertionError("revocation failure lost interruption or candidate evidence") from error
                else:
                    raise AssertionError("revocation failure accepted interrupted execution")
        def execute_uncertain(_ordinal, _entry):
            def interrupt_after_ack(event):
                if event == "control-accepted":
                    interruption = artifacts.ScriptPlanInterrupted(signal.SIGTERM)
                    interruption.cancellation_evidence_unavailable = True
                    raise interruption
            return original_supervisor([str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                                       namespace_notifications=artifacts.queue.Queue(maxsize=1),
                                       namespace_attempt_id="lane-1", execution_timeout_seconds=5,
                                       test_hook=interrupt_after_ack, _atomic_launcher=launch)

        with tempfile.TemporaryDirectory() as runner_temp:
            with patch.object(artifacts.PinnedSupervisorNamespace, "_revoke", fail_after_revoke):
                try:
                    artifacts.run_script_plan_with_receipts(receipt_tests.ENTRIES, receipt_tests.IDENTITY, False,
                                                            Path(runner_temp), execute_uncertain)
                except artifacts.ScriptPlanExecutionError as error:
                    if not getattr(error.__cause__, "cancellation_evidence_unavailable", False):
                        raise AssertionError("plan lost cancellation uncertainty") from error
                    if (error.receipt_directory / "plan.json").exists():
                        raise AssertionError("uncertain cancellation produced a plan receipt")
                else:
                    raise AssertionError("uncertain cancellation was accepted")
        def interrupt_revocation(notice):
            original_revoke(notice)
            raise KeyboardInterrupt()

        def interrupt_after_outcome(event):
            if event == "candidate-outcome":
                interruption = artifacts.ScriptPlanInterrupted(signal.SIGTERM)
                interruption.cancellation_evidence_unavailable = True
                raise interruption

        with patch.object(artifacts.PinnedSupervisorNamespace, "_revoke", interrupt_revocation):
            try:
                original_supervisor([str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                                    namespace_notifications=artifacts.queue.Queue(maxsize=1),
                                    namespace_attempt_id="lane-1", execution_timeout_seconds=5,
                                    test_hook=interrupt_after_outcome, _atomic_launcher=launch)
            except KeyboardInterrupt as error:
                if (not error.candidate_started or error.candidate_exit_code != 0
                        or error.host_cancellation_signal != signal.SIGTERM
                        or not getattr(error, "cancellation_evidence_unavailable", False)):
                    raise AssertionError("revocation interruption lost candidate or cancellation evidence") from error
            else:
                raise AssertionError("revocation interruption was suppressed")

        def fail_after_reap(*args, **kwargs):
            original_reap(*args, **kwargs)
            raise OSError("injected post-reap failure")

        original_reap = artifacts._reap_pinned_supervisor
        with (patch.object(artifacts.PinnedSupervisorNamespace, "_revoke", interrupt_revocation),
              patch.object(artifacts, "_reap_pinned_supervisor", fail_after_reap)):
            try:
                original_supervisor([str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                                    namespace_notifications=artifacts.queue.Queue(maxsize=1),
                                    namespace_attempt_id="lane-1", execution_timeout_seconds=5,
                                    test_hook=interrupt_after_outcome, _atomic_launcher=launch)
            except KeyboardInterrupt as error:
                if (not error.candidate_started or error.candidate_exit_code != 0
                        or error.host_cancellation_signal != signal.SIGTERM
                        or not getattr(error, "cancellation_evidence_unavailable", False)
                        or error.__cause__ is None):
                    raise AssertionError("simultaneous cleanup errors lost interruption evidence") from error
            else:
                raise AssertionError("simultaneous cleanup errors suppressed the interruption")

        close_state = {"reaped": False, "failed": False}
        original_reap = artifacts._reap_pinned_supervisor
        original_pipe_close = artifacts.SlotAtomicPipe.close

        def record_reap(*args, **kwargs):
            result = original_reap(*args, **kwargs)
            close_state["reaped"] = True
            return result

        def fail_final_close(pipe):
            original_pipe_close(pipe)
            if close_state["reaped"] and not close_state["failed"]:
                close_state["failed"] = True
                raise OSError("injected final descriptor close failure")

        def execute_close_failure(_ordinal, _entry):
            return original_supervisor([str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                                       execution_timeout_seconds=5, test_hook=interrupt_after_outcome,
                                       _atomic_launcher=launch)

        with tempfile.TemporaryDirectory() as runner_temp:
            with (patch.object(artifacts, "_reap_pinned_supervisor", record_reap),
                  patch.object(artifacts.SlotAtomicPipe, "close", fail_final_close)):
                try:
                    artifacts.run_script_plan_with_receipts(receipt_tests.ENTRIES, receipt_tests.IDENTITY, False,
                                                            Path(runner_temp), execute_close_failure)
                except artifacts.ScriptPlanExecutionError as error:
                    if not close_state["failed"] or not getattr(error.__cause__, "cancellation_evidence_unavailable", False):
                        raise AssertionError("descriptor close failure lost cancellation uncertainty") from error
                    if not error.__cause__.candidate_started or error.__cause__.return_code != 0:
                        raise AssertionError("descriptor close failure lost authenticated candidate outcome") from error
                    if (error.receipt_directory / "plan.json").exists():
                        raise AssertionError("descriptor close failure produced a plan receipt")
                else:
                    raise AssertionError("descriptor close failure was accepted")

        close_state.update(reaped=False, failed=False)
        with (patch.object(artifacts.PinnedSupervisorNamespace, "_revoke", fail_after_revoke),
              patch.object(artifacts, "_reap_pinned_supervisor", record_reap),
              patch.object(artifacts.SlotAtomicPipe, "close", fail_final_close)):
            try:
                original_supervisor([str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                                    namespace_notifications=artifacts.queue.Queue(maxsize=1),
                                    namespace_attempt_id="lane-1", execution_timeout_seconds=5,
                                    _atomic_launcher=launch)
            except artifacts.ScriptCandidateTerminal as error:
                if (not close_state["failed"] or not error.candidate_started or error.return_code != 0
                        or not any("namespace revocation" in note for note in getattr(error, "__notes__", ()))):
                    raise AssertionError("simultaneous cleanup failures lost their evidence") from error
            else:
                raise AssertionError("simultaneous cleanup failures were accepted")

        close_state.update(reaped=False, failed=False)
        with (patch.object(artifacts.PinnedSupervisorNamespace, "_revoke", interrupt_revocation),
              patch.object(artifacts, "_reap_pinned_supervisor", record_reap),
              patch.object(artifacts.SlotAtomicPipe, "close", fail_final_close)):
            try:
                original_supervisor([str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                                    namespace_notifications=artifacts.queue.Queue(maxsize=1),
                                    namespace_attempt_id="lane-1", execution_timeout_seconds=5,
                                    test_hook=interrupt_after_outcome, _atomic_launcher=launch)
            except KeyboardInterrupt as error:
                if (not close_state["failed"] or not error.candidate_started or error.candidate_exit_code != 0
                        or error.host_cancellation_signal != signal.SIGTERM
                        or not getattr(error, "cancellation_evidence_unavailable", False)):
                    raise AssertionError("close failure masked revocation interruption evidence") from error
            else:
                raise AssertionError("close failure suppressed revocation interruption")

        original_owned_close = launch._owned_close
        inner_close_state = {"interrupted": False}

        def interrupt_inner_close(pointer):
            result = original_owned_close(pointer)
            if close_state["reaped"] and not inner_close_state["interrupted"]:
                inner_close_state["interrupted"] = True
                raise KeyboardInterrupt()
            return result

        close_state["reaped"] = False
        with (patch.object(launch, "_owned_close", interrupt_inner_close),
              patch.object(artifacts, "_reap_pinned_supervisor", record_reap)):
            try:
                execute_close_failure(0, receipt_tests.ENTRIES[0])
            except KeyboardInterrupt as error:
                if (not inner_close_state["interrupted"] or not error.candidate_started
                        or error.candidate_exit_code != 0 or error.host_cancellation_signal != signal.SIGTERM
                        or not getattr(error, "cancellation_evidence_unavailable", False)):
                    raise AssertionError("inner close interruption lost candidate or cancellation evidence") from error
            else:
                raise AssertionError("inner close interruption was suppressed")
        retained_before = set(artifacts._retained_atomic_owners)
        with patch.object(artifacts.InnerSupervisorDescriptors, "close_all", side_effect=KeyboardInterrupt()):
            try:
                original_supervisor([str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                                    execution_timeout_seconds=5, _atomic_launcher=launch)
            except KeyboardInterrupt as error:
                if not error.candidate_started or error.candidate_exit_code != 0:
                    raise AssertionError("inner cleanup interruption lost candidate outcome") from error
                if not any("inner supervisor descriptor owner remains retained" in note for note in error.__notes__):
                    raise AssertionError("inner cleanup interruption did not report retention") from error
            else:
                raise AssertionError("inner cleanup interruption was suppressed")
        retained_inner = artifacts._retained_atomic_owners - retained_before
        if len(retained_inner) != 1 or not isinstance(next(iter(retained_inner)), artifacts.InnerSupervisorDescriptors):
            raise AssertionError("inner descriptor owner was not retained")
        inner_owner = next(iter(retained_inner))
        inner_owner.close_all()
        artifacts._release_atomic_owner(inner_owner)

        retained_before = set(artifacts._retained_atomic_owners)
        with patch.object(artifacts.PinnedSupervisorNamespace, "_revoke", side_effect=OSError("injected revoke failure")):
            try:
                original_supervisor([str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                                    namespace_notifications=artifacts.queue.Queue(maxsize=1),
                                    namespace_attempt_id="lane-1", execution_timeout_seconds=5,
                                    _atomic_launcher=launch)
            except artifacts.ScriptCandidateTerminal as error:
                if not error.candidate_started or not any("sandbox namespace descriptor owner remains retained" in note
                                                          for note in getattr(error, "__notes__", ())):
                    raise AssertionError("namespace revocation failure did not report retained owner") from error
            else:
                raise AssertionError("namespace revocation failure was accepted")
        retained_notices = artifacts._retained_atomic_owners - retained_before
        if len(retained_notices) != 1 or not isinstance(next(iter(retained_notices)), artifacts.PinnedSupervisorNamespace):
            raise AssertionError("namespace notice owner was not retained")
        retained_notice = next(iter(retained_notices))
        if not retained_notice._revoke():
            raise AssertionError("retained namespace notice could not be revoked")
        artifacts._release_atomic_owner(retained_notice)

        for pending_owner in tuple(artifacts._retained_atomic_owners):
            if pending_owner._release_attempted and pending_owner.signal_group(0):
                raise AssertionError("earlier injected cleanup left a live group")
            pending_owner.close_all()
            artifacts._release_atomic_owner(pending_owner)
        retained_before = set(artifacts._retained_atomic_owners)
        original_group_signal = artifacts._signal_pinned_process_group

        def fail_disappearance_probe(pidfd, signum):
            present = original_group_signal(pidfd, signum)
            if signum == 0 and not present:
                raise OSError("injected group disappearance failure")
            return present

        with patch.object(artifacts, "_signal_pinned_process_group", fail_disappearance_probe):
            try:
                original_supervisor([str(fake_bubblewrap)], environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                                    execution_timeout_seconds=5, _atomic_launcher=launch)
            except artifacts.ScriptCandidateTerminal as error:
                if not error.candidate_started or error.cleanup_status != "unverified":
                    raise AssertionError("unverified group cleanup lost candidate evidence") from error
                if not any("owner remains retained" in note for note in getattr(error, "__notes__", ())):
                    raise AssertionError("unverified group cleanup did not report retained ownership") from error
            else:
                raise AssertionError("unverified group cleanup was accepted")
        newly_retained = artifacts._retained_atomic_owners - retained_before
        if len(newly_retained) != 1:
            raise AssertionError("unverified group cleanup did not retain exactly one owner")
        retained_owner = next(iter(newly_retained))
        if retained_owner.descriptor(10) < 0 or retained_owner.signal_group(0):
            raise AssertionError("retained owner lost its pidfd or still has a live group")
        retained_owner.close_all()
        artifacts._release_atomic_owner(retained_owner)
    finally:
        receipt_case.doCleanups()
    # This fixture exercises the protocol inside a real PID namespace, not AppArmor enforcement.
    namespace_fixture = """\
import json, os, sys
def emit(event, **fields):
    print(json.dumps({'version': 3, 'pid': os.getpid(), 'event': event, **fields}), flush=True)
namespace = os.stat('/proc/self/ns/pid').st_ino
emit('ready', pidNamespace=namespace)
challenge = sys.stdin.buffer.readline()
emit('identity-proof', pidNamespace=namespace, nonce=challenge[10:-1].decode('ascii'))
if sys.stdin.buffer.readline() != b'launch\\n':
    raise SystemExit(125)
emit('control-accepted')
emit('candidate-started')
emit('candidate-outcome', exitCode=0, candidateSignal=None)
emit('cleanup-start')
emit('cleanup-complete')
emit('terminal', state='candidate-exit', candidateStarted=True, exitCode=0, candidateSignal=None,
     supervisorSignal=None, cleanup='complete', reaped=0, control='launch', controlSignal=None)
"""
    real_bwrap = ["/usr/bin/bwrap", "--unshare-user", "--unshare-pid", "--as-pid-1",
                  "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
                  "--ro-bind", "/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
                  "--", "/usr/bin/python3", "-I", "-c", namespace_fixture]
    system_owner = artifacts.AtomicMountDescriptors(launch, 6)
    try:
        source_slots = {"/usr": 0, "/lib": 1, "/lib64": 2}
        expected = {}
        for source, final_slot in source_slots.items():
            resolved = Path(os.path.realpath(source))
            metadata = resolved.stat()
            expected[final_slot] = (metadata.st_dev, metadata.st_ino, metadata.st_uid,
                                    artifacts.stat.S_IMODE(metadata.st_mode))
            system_owner.open_verified_directory(final_slot, resolved, expected[final_slot])
        system_owner.seal(expected)
        for position in range(1, len(real_bwrap)):
            if real_bwrap[position - 1] == "--ro-bind" and real_bwrap[position] in source_slots:
                real_bwrap[position] = f"/proc/self/fd/@verjson-slot-{source_slots[real_bwrap[position]]}@"
        real_status = original_supervisor(real_bwrap, environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                                          execution_timeout_seconds=5, _atomic_launcher=launch,
                                          _mount_owner=system_owner, _mount_slots=tuple(source_slots.values()))
    finally:
        system_owner.close_all()
    if real_status.state != "candidate-exit" or real_status.return_code != 0:
        raise AssertionError("real PID namespace protocol did not complete")
    # Exercise the real PID1 cleanup code without claiming local AppArmor enforcement.
    security_gate = "def enforce_candidate_security_state():"
    if artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT.count(security_gate) != 1:
        raise AssertionError("supervisor security gate fixture boundary changed")
    cleanup_supervisor = artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT.replace(
        security_gate, security_gate + "\n    return", 1,
    )
    first_candidate = """\
import os, signal, time
child = os.fork()
if child == 0:
    os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    open('/work/leftover-ready', 'w').close()
    deadline = time.monotonic() + 10
    while not os.path.exists('/work/release-leftover') and time.monotonic() < deadline:
        time.sleep(0.01)
    if os.path.exists('/work/release-leftover'):
        open('/work/leftover-ran', 'w').close()
    os._exit(0)
deadline = time.monotonic() + 3
while not os.path.exists('/work/leftover-ready'):
    if time.monotonic() >= deadline: raise SystemExit(125)
    time.sleep(0.01)
"""
    second_candidate = """\
import os, time
open('/work/primary-started', 'w').close()
open('/work/release-leftover', 'w').close()
time.sleep(0.2)
raise SystemExit(1 if os.path.exists('/work/leftover-ran') else 0)
"""
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory) / "workspace"
        workspace.mkdir()
        mount_owner = artifacts.AtomicMountDescriptors(launch, 7)
        source_slots = {0: Path(os.path.realpath("/usr")), 1: Path(os.path.realpath("/lib")),
                        2: Path(os.path.realpath("/lib64")), 3: workspace}
        expected = {}
        try:
            for slot, source in source_slots.items():
                metadata = source.stat()
                expected[slot] = (metadata.st_dev, metadata.st_ino, metadata.st_uid,
                                  artifacts.stat.S_IMODE(metadata.st_mode))
                mount_owner.open_verified_directory(slot, source, expected[slot])
            mount_owner.seal(expected)
            common = ["/usr/bin/bwrap", "--unshare-user", "--unshare-pid", "--as-pid-1",
                      "--unshare-ipc", "--unshare-uts", "--unshare-net", "--cap-drop", "ALL",
                      "--die-with-parent", "--new-session",
                      "--ro-bind", "/proc/self/fd/@verjson-slot-0@", "/usr",
                      "--ro-bind", "/proc/self/fd/@verjson-slot-1@", "/lib",
                      "--ro-bind", "/proc/self/fd/@verjson-slot-2@", "/lib64",
                      "--bind", "/proc/self/fd/@verjson-slot-3@", "/work",
                      "--proc", "/proc", "--dev", "/dev", "--chdir", "/work", "--",
                      "/usr/bin/python3", "-I", "-c", cleanup_supervisor,
                      "/usr/bin/python3", "-I", "-c"]
            outcomes = []

            def execute_sequential(ordinal, _entry):
                candidate = first_candidate if ordinal == 0 else second_candidate
                result = original_supervisor([*common, candidate], environment={"PATH": "/usr/bin:/bin"},
                                             pass_fds=(), execution_timeout_seconds=8, _atomic_launcher=launch,
                                             _mount_owner=mount_owner, _mount_slots=tuple(source_slots))
                outcomes.append(result)
                if result.return_code is None or result.cleanup != "complete":
                    raise AssertionError("candidate cleanup was not verified")
                return artifacts.ScriptEntryExecutionResult(result.return_code)

            plan = artifacts.run_script_plan_with_receipts(receipt_tests.ENTRIES[:2], receipt_tests.IDENTITY, False,
                                                           Path(directory), execute_sequential)
            if (plan.exit_code != 0 or len(outcomes) != 2 or outcomes[0].reaped_count < 2
                    or not (workspace / "primary-started").exists() or (workspace / "leftover-ran").exists()):
                raise AssertionError("next candidate started without verified cleanup of the detached predecessor")
            artifacts.validate_script_entry_receipts(plan.receipt_directory, receipt_tests.ENTRIES[:2],
                                                      receipt_tests.IDENTITY, False)
        finally:
            mount_owner.close_all()
    try:
        original_supervisor(["/missing-verjson-executable"], environment={}, pass_fds=(),
                            execution_timeout_seconds=5, _atomic_launcher=launch)
    except artifacts.ScriptCandidateTerminal as error:
        if error.candidate_started or "stage 7" not in str(error):
            raise AssertionError("failed exec lost its pre-candidate evidence") from error
    else:
        raise AssertionError("failed exec was accepted")
    cancelled = artifacts.HostSignalQueue()
    cancelled.capture(signal.SIGTERM)
    try:
        original_supervisor(["/usr/bin/true"], environment={}, pass_fds=(),
                            host_signals=cancelled, execution_timeout_seconds=5, _atomic_launcher=launch)
    except artifacts.ScriptPlanInterrupted as error:
        if error.candidate_started:
            raise AssertionError("pre-release cancellation fabricated candidate execution") from error
    else:
        raise AssertionError("pre-release cancellation was accepted")

    state = {"source_failed": False, "close_failed": False}
    original_close = artifacts.SlotAtomicPipe.close

    def read_cancellation():
        if state["source_failed"]:
            raise RuntimeError("cancellation source unavailable")
        return None

    def interrupt_before_release(*_args):
        state["source_failed"] = True
        raise artifacts.ScriptPlanInterrupted(signal.SIGTERM)

    def close_with_failure(pipe):
        if not state["close_failed"]:
            state["close_failed"] = True
            raise OSError(errno.EIO, "injected close failure")
        return original_close(pipe)

    def execute(_ordinal, _entry):
        return original_supervisor(["/usr/bin/true"], environment={}, pass_fds=(),
                                   cancellation_signal=read_cancellation,
                                   execution_timeout_seconds=5, _atomic_launcher=launch)

    with tempfile.TemporaryDirectory() as runner_temp:
        with (patch.object(artifacts, "_await_atomic_launcher_ready", side_effect=interrupt_before_release),
              patch.object(artifacts.SlotAtomicPipe, "close", close_with_failure)):
            try:
                artifacts.run_script_plan_with_receipts(
                    receipt_tests.ENTRIES, receipt_tests.IDENTITY, False, Path(runner_temp), execute,
                    cancellation_signal=read_cancellation,
                )
            except artifacts.ScriptPlanExecutionError as error:
                cause = error.__cause__
                if (getattr(cause, "host_cancellation_signal", None) != signal.SIGTERM or
                        not getattr(cause, "cancellation_evidence_unavailable", False) or
                        (error.receipt_directory / "plan.json").exists()):
                    raise AssertionError("cancellation evidence was not preserved safely") from error
            else:
                raise AssertionError("unavailable cancellation evidence was accepted")
    state["close_failed"] = False
    uncertain = artifacts.IntegrityError("transient cancellation source failure")
    uncertain.cancellation_evidence_unavailable = True

    def execute_uncertain(_ordinal, _entry):
        return original_supervisor(["/usr/bin/true"], environment={}, pass_fds=(),
                                   execution_timeout_seconds=5, _atomic_launcher=launch)

    with tempfile.TemporaryDirectory() as runner_temp:
        with (patch.object(artifacts, "_await_atomic_launcher_ready", side_effect=uncertain),
              patch.object(artifacts.SlotAtomicPipe, "close", close_with_failure)):
            try:
                artifacts.run_script_plan_with_receipts(
                    receipt_tests.ENTRIES, receipt_tests.IDENTITY, False, Path(runner_temp), execute_uncertain,
                    cancellation_signal=lambda: None,
                )
            except artifacts.ScriptPlanExecutionError as error:
                if (not getattr(error.__cause__, "cancellation_evidence_unavailable", False) or
                        (error.receipt_directory / "plan.json").exists()):
                    raise AssertionError("cleanup failure lost cancellation uncertainty") from error
            else:
                raise AssertionError("cleanup failure accepted missing cancellation evidence")

    def start(arguments, inherited=()):
        pipes = [os.pipe2(os.O_CLOEXEC) for _ in range(5)]
        (stdin_r, stdin_w), (stdout_r, stdout_w), (ready_r, ready_w), (gate_r, gate_w), (error_r, error_w) = pipes
        pidfd = ctypes.c_int(-1)
        argv = (ctypes.c_char_p * (len(arguments) + 1))(*[arg.encode() for arg in arguments], None)
        environment = (ctypes.c_char_p * 2)(b"PATH=/usr/bin:/bin", None)
        descriptors = (ctypes.c_int * len(inherited))(*inherited) if inherited else None
        result = launch(ctypes.byref(pidfd), stdin_r, stdout_w, ready_w, gate_r, error_w,
                        descriptors, len(inherited), None, argv, environment)
        if result != 0:
            for pair in pipes:
                for descriptor in pair:
                    os.close(descriptor)
            if pidfd.value >= 0:
                try:
                    os.waitid(os.P_PIDFD, pidfd.value, os.WEXITED)
                finally:
                    os.close(pidfd.value)
            raise AssertionError(f"launch failed: {result}")
        for descriptor in (stdin_r, stdin_w, stdout_w, ready_w, gate_r, error_w):
            os.close(descriptor)
        if os.read(ready_r, 1) != b"R":
            raise AssertionError("launcher did not become ready")
        signal.pidfd_send_signal(pidfd.value, 0, None, 4)
        return pidfd.value, stdout_r, ready_r, gate_w, error_r

    def finish(child, release, expected_status, expected_error):
        pidfd, stdout_r, ready_r, gate_w, error_r = child
        if release:
            os.write(gate_w, b"G")
        os.close(gate_w)
        status = os.waitid(os.P_PIDFD, pidfd, os.WEXITED)
        error = os.read(error_r, 32)
        output = os.read(stdout_r, 1024)
        if status.si_status != expected_status:
            raise AssertionError((status.si_status, expected_status))
        if expected_error is None:
            if error:
                raise AssertionError(error)
        elif len(error) != ctypes.sizeof(ctypes.c_int) * 2 or tuple((ctypes.c_int * 2).from_buffer_copy(error)) != expected_error:
            raise AssertionError((error, expected_error))
        for descriptor in (pidfd, stdout_r, ready_r, error_r):
            os.close(descriptor)
        return output

    if finish(start(["/usr/bin/true"]), True, 0, None) != b"":
        raise AssertionError("unexpected output")
    finish(start(["/missing-verjson-executable"]), True, 125, (7, errno.ENOENT))
    finish(start(["/usr/bin/true"]), False, 125, (6, errno.ECANCELED))

    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    child = start(["/usr/bin/python3", "-I", "-c", "import os,sys; os.write(int(sys.argv[1]), b'kept')", str(write_fd)], (write_fd,))
    os.close(write_fd)
    finish(child, True, 0, None)
    if os.read(read_fd, 8) != b"kept":
        raise AssertionError("explicitly inherited descriptor was lost")
    os.close(read_fd)

    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    os.dup2(write_fd, 200, inheritable=True)
    os.close(write_fd)
    try:
        code = "import os,sys\ntry: os.fstat(200)\nexcept OSError: sys.exit(0)\nsys.exit(1)"
        finish(start(["/usr/bin/python3", "-I", "-c", code]), True, 0, None)
    finally:
        os.close(read_fd)
        os.close(200)

    code = "import subprocess; subprocess.Popen(['/usr/bin/sleep', '30'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
    pidfd, stdout_r, ready_r, gate_w, error_r = start(["/usr/bin/python3", "-I", "-c", code])
    os.write(gate_w, b"G")
    os.close(gate_w)
    os.waitid(os.P_PIDFD, pidfd, os.WEXITED | os.WNOWAIT)
    signal.pidfd_send_signal(pidfd, signal.SIGKILL, None, 4)
    os.waitid(os.P_PIDFD, pidfd, os.WEXITED)
    os.waitpid(-1, 0)
    try:
        signal.pidfd_send_signal(pidfd, 0, None, 4)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("pinned descendant group survived cleanup")
    for descriptor in (pidfd, stdout_r, ready_r, error_r):
        os.close(descriptor)

    descriptors = [os.open("/dev/null", os.O_RDONLY) for _ in range(4)]
    argv = (ctypes.c_char_p * 2)(b"/usr/bin/true", None)
    environment = (ctypes.c_char_p * 1)(None)
    pidfd = ctypes.c_int(-1)
    try:
        result = launch(ctypes.byref(pidfd), descriptors[0], descriptors[0], descriptors[1],
                        descriptors[2], descriptors[3], None, 0, None, argv, environment)
        if result != -errno.EINVAL or pidfd.value != -1:
            raise AssertionError("descriptor collision was not rejected before cloning")
    finally:
        for descriptor in descriptors:
            os.close(descriptor)

    descriptors = [os.open("/dev/null", os.O_RDONLY) for _ in range(5)]
    old_child_handler = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    try:
        result = launch(ctypes.byref(pidfd), *descriptors, None, 0, None, argv, environment)
        if result != -errno.EINVAL or pidfd.value != -1:
            raise AssertionError("auto-reaping SIGCHLD disposition was accepted")
    finally:
        signal.signal(signal.SIGCHLD, old_child_handler)
        for descriptor in descriptors:
            os.close(descriptor)

    old_child_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGCHLD})
    try:
        code = "import signal; print(int(signal.SIGCHLD in signal.pthread_sigmask(signal.SIG_BLOCK, set())))"
        output = finish(start(["/usr/bin/python3", "-I", "-c", code]), True, 0, None)
        if output != b"0\n" or signal.SIGCHLD not in signal.pthread_sigmask(signal.SIG_BLOCK, set()):
            raise AssertionError("child or parent SIGCHLD mask was not restored correctly")
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_child_mask)

    old_handler = signal.signal(signal.SIGTERM, lambda *_args: None)
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    try:
        child = start(["/usr/bin/true"])
        pidfd, stdout_r, ready_r, gate_w, error_r = child
        signal.pidfd_send_signal(pidfd, signal.SIGTERM)
        status = os.waitid(os.P_PIDFD, pidfd, os.WEXITED)
        if status.si_code != os.CLD_KILLED or status.si_status != signal.SIGTERM:
            raise AssertionError("child inherited a Python handler or blocked SIGTERM")
        if signal.SIGTERM not in signal.pthread_sigmask(signal.SIG_BLOCK, set()):
            raise AssertionError("parent signal mask was not restored")
        for descriptor in (pidfd, stdout_r, ready_r, gate_w, error_r):
            os.close(descriptor)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        signal.signal(signal.SIGTERM, old_handler)
    signal.alarm(0)
    if unrelated_library is None:
        raise AssertionError("unrelated loader fixture was lost")


class PidfdLauncherIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires provisioned hosted Bubblewrap/AppArmor")
    def test_atomic_launcher_lifecycle_in_isolated_namespace(self):
        with tempfile.TemporaryDirectory() as directory:
            library = str(Path(directory) / "launcher.so")
            subprocess.run(["/usr/bin/cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                            "-fPIC", "-shared", "-pthread", str(SOURCE), "-o", library], check=True)
            unrelated_source = Path(directory) / "unrelated.c"
            unrelated_library = str(Path(directory) / "unrelated.so")
            unrelated_source.write_text("int verjson_pidfd_launch(void) { return -987; }\n", encoding="ascii")
            subprocess.run(["/usr/bin/cc", "-std=c11", "-fPIC", "-shared", str(unrelated_source),
                            "-o", unrelated_library], check=True)
            subprocess.run(["unshare", "--user", "--map-root-user", "--mount", "--pid", "--fork", "--mount-proc",
                            "/usr/bin/python3", str(Path(__file__).resolve()), "--inside", library, unrelated_library], check=True)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--inside":
        run_inside_namespace(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
