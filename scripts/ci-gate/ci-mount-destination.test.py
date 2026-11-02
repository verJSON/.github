#!/usr/bin/python3
import hashlib
import hmac
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).with_name("pidfd-launcher.c")


def is_prestart_destination_rejection(error: BaseException, swapped: bool) -> bool:
    return swapped and "destination" in str(error).lower() and getattr(error, "candidate_started", None) is False


def is_started_destination_rejection(error: BaseException, swapped: bool) -> bool:
    return (swapped and "destination" in str(error).lower() and getattr(error, "candidate_started", None) is True
            and getattr(error, "infrastructure_failure", None) is True)


def await_pidfd_reaped(pidfd: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        readable = bool(select.select([pidfd], [], [], 0)[0])
        fdinfo = Path(f"/proc/self/fdinfo/{pidfd}").read_text(encoding="ascii")
        if readable and any(line.split() == ["Pid:", "-1"] for line in fdinfo.splitlines()):
            return True
        time.sleep(0.01)
    return False


def exercise_destination_replacement(library_path: str, when: str) -> None:
    uid_map = Path("/proc/self/uid_map").read_text(encoding="ascii").split()
    if (os.getpid() != 2 or os.getppid() != 1 or os.getuid() != 0
            or Path("/proc/1/comm").read_text(encoding="ascii").strip() != "bwrap"
            or len(uid_map) != 3 or uid_map[0] != "0" or uid_map[2] != "1"):
        raise RuntimeError("destination race requires an isolated PID namespace with a PID 1 reaper")
    if when not in ("before-mount", "after-mount", "after-start"):
        raise ValueError("destination race stage is invalid")
    sys.path.insert(0, str(ROOT))
    import ci_build_artifacts as artifacts

    digest = hashlib.sha256(Path(library_path).read_bytes()).hexdigest()
    launcher = artifacts._load_atomic_launcher(Path(library_path), digest)
    marker_path = "/work/other/marker" if when == "before-mount" else "/work/validated-target/marker"
    candidate = (
        "import os, socket, sys, time\n"
        "channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "channel.settimeout(5)\n"
        "channel.connect('/work/candidate.sock')\n"
        "challenge = b''\n"
        "while len(challenge) < 32:\n"
        "    part = channel.recv(32 - len(challenge))\n"
        "    if not part: raise SystemExit(124)\n"
        "    challenge += part\n"
        "channel.sendall(challenge)\n"
        "if channel.recv(1) != b'G': raise SystemExit(124)\n"
        "channel.close()\n"
    )
    if when == "after-start":
        candidate += (
            "deadline = time.monotonic() + 5\n"
            "while not os.path.exists('/work/swap-complete'):\n"
            "    if time.monotonic() >= deadline: raise SystemExit(124)\n"
            "    time.sleep(0.005)\n"
        )
    candidate += f"raise SystemExit(0 if open({marker_path!r}, 'rb').read() == b'artifact' else 1)\n"
    fixture = """\
import json, os, subprocess, sys
def emit(event, **fields):
    print(json.dumps({'version': 3, 'pid': os.getpid(), 'event': event, **fields}), flush=True)
namespace = os.stat('/proc/self/ns/pid').st_ino
emit('ready', pidNamespace=namespace)
challenge = sys.stdin.buffer.readline()
emit('identity-proof', pidNamespace=namespace, nonce=challenge[10:-1].decode('ascii'))
if sys.stdin.buffer.readline() != b'launch\\n':
    raise SystemExit(125)
emit('control-accepted')
child = subprocess.Popen(['/usr/bin/python3', '-I', '-c', __CANDIDATE__],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
emit('candidate-started')
try:
    code = child.wait(timeout=5)
except BaseException:
    child.kill()
    child.wait()
    raise
emit('candidate-outcome', exitCode=code, candidateSignal=None)
emit('cleanup-start')
emit('cleanup-complete')
emit('terminal', state='candidate-exit', candidateStarted=True, exitCode=code, candidateSignal=None,
     supervisorSignal=None, cleanup='complete', reaped=1, control='launch', controlSignal=None)
"""
    fixture = fixture.replace("__CANDIDATE__", repr(candidate))
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for workflow_name in ("node-ci.yml", "node-ci-protected.yml"):
            workflow = yaml.safe_load((ROOT.parent / ".github" / "workflows" / workflow_name).read_text(encoding="utf-8"))
            steps = workflow["jobs"]["build-test"]["steps"]
            warning = next(step for step in steps if step.get("name") == "Disclose shared workspace isolation assumption")
            summary = root / f"{workflow_name}.summary"
            result = subprocess.run(["/usr/bin/bash", "-e", "-c", warning["run"]],
                                    env={"GITHUB_STEP_SUMMARY": str(summary), "PATH": "/usr/bin:/bin"},
                                    capture_output=True, text=True, check=True)
            if ("::warning title=Shared workspace isolation::" not in result.stdout
                    or "concurrent or leftover host writer" not in result.stdout
                    or "concurrent or leftover host writer" not in summary.read_text(encoding="utf-8")):
                raise AssertionError(f"{workflow_name} did not disclose the shared-workspace risk")
        workspace, artifact = root / "workspace", root / "artifact"
        workspace.mkdir()
        artifact.mkdir()
        (workspace / "target").mkdir()
        (workspace / "other").mkdir()
        (artifact / "marker").write_bytes(b"artifact")
        receiver = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        receiver.settimeout(3)
        receiver.bind(str(workspace / "candidate.sock"))
        receiver.listen(1)
        candidate_pidfd = -1
        validated_target = (workspace / "target").stat()
        mount_owner = artifacts.AtomicMountDescriptors(launcher, 10)
        sources = {0: Path(os.path.realpath("/usr")), 1: Path(os.path.realpath("/lib")),
                   2: Path(os.path.realpath("/lib64")), 3: workspace, 4: artifact}
        expected = {}
        try:
            for slot, source in sources.items():
                metadata = source.stat()
                identity = (metadata.st_dev, metadata.st_ino, metadata.st_uid,
                            artifacts.stat.S_IMODE(metadata.st_mode))
                mount_owner.open_verified_directory(slot, source, identity)
                expected[slot] = identity
            mount_owner.seal(expected)
            arguments = [
                "/usr/bin/bwrap", "--unshare-user", "--unshare-pid", "--as-pid-1",
                "--ro-bind", "/proc/self/fd/@verjson-slot-0@", "/usr",
                "--ro-bind", "/proc/self/fd/@verjson-slot-1@", "/lib",
                "--ro-bind", "/proc/self/fd/@verjson-slot-2@", "/lib64",
                "--bind", "/proc/self/fd/@verjson-slot-3@", "/work",
                "--ro-bind", "/proc/self/fd/@verjson-slot-4@", "/work/target",
                "--proc", "/proc", "--dev", "/dev", "--", "/usr/bin/python3", "-I", "-c", fixture,
            ]
            original_release = artifacts.AtomicDescriptorSlots.release
            swap_completed = False

            def swap_target():
                nonlocal swap_completed
                (workspace / "target").rename(workspace / "validated-target")
                (workspace / "target").symlink_to("other", target_is_directory=True)
                if (workspace / "validated-target").stat().st_ino != validated_target.st_ino:
                    raise AssertionError("fixture failed to retain the validated target")
                swap_completed = True

            def replace_target(owner, signals):
                if when == "before-mount":
                    swap_target()
                return original_release(owner, signals)

            def after_ready(event):
                nonlocal candidate_pidfd
                if when == "after-mount" and event == "ready":
                    swap_target()
                if event == "candidate-started":
                    connection, _address = receiver.accept()
                    with connection:
                        connection.settimeout(3)
                        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                        peer_pid, _peer_uid, _peer_gid = struct.unpack("3i", credentials)
                        if peer_pid <= 1:
                            raise AssertionError("candidate identity handshake was invalid")
                        candidate_pidfd = os.pidfd_open(peer_pid)
                        if select.select([candidate_pidfd], [], [], 0)[0]:
                            raise AssertionError("candidate exited before its identity was pinned")
                        challenge = os.urandom(32)
                        connection.sendall(challenge)
                        response = bytearray()
                        while len(response) < len(challenge):
                            part = connection.recv(len(challenge) - len(response))
                            if not part:
                                raise AssertionError("candidate identity proof was incomplete")
                            response.extend(part)
                        if not hmac.compare_digest(bytes(response), challenge) or select.select([candidate_pidfd], [], [], 0)[0]:
                            raise AssertionError("candidate identity proof did not match its live pidfd")
                        if when == "after-start":
                            swap_target()
                            (workspace / "swap-complete").touch()
                        connection.sendall(b"G")

            try:
                with patch.object(artifacts.AtomicDescriptorSlots, "release", replace_target):
                    result = artifacts.run_supervised_bubblewrap(
                        arguments, environment={"PATH": "/usr/bin:/bin"}, pass_fds=(),
                        execution_timeout_seconds=5, _atomic_launcher=launcher,
                        _mount_owner=mount_owner, _mount_slots=tuple(sources),
                        test_hook=after_ready,
                    )
            except (artifacts.IntegrityError, artifacts.ScriptCandidateTerminal) as error:
                valid = (is_started_destination_rejection(error, swap_completed) if when == "after-start"
                         else is_prestart_destination_rejection(error, swap_completed))
                if not valid:
                    raise AssertionError("failure did not reject the replaced destination with correct start evidence") from error
                if when == "after-start":
                    if candidate_pidfd < 0:
                        raise AssertionError("started candidate identity was not pinned") from error
                    if not await_pidfd_reaped(candidate_pidfd, 3):
                        if getattr(error, "cleanup_status", None) != "unverified":
                            raise AssertionError("unreaped candidate was not reported as unverified") from error
                        raise AssertionError("rejected candidate was not proven reaped") from error
                return
            if not swap_completed:
                raise AssertionError("destination replacement did not run before the candidate succeeded")
            if result.return_code != 0 or result.reaped_count != 1 or result.cleanup != "complete":
                raise AssertionError("fixture did not observe the artifact at the redirected destination")
            if candidate_pidfd < 0 or not await_pidfd_reaped(candidate_pidfd, 3):
                raise AssertionError("fixture did not independently prove the candidate was reaped")
            if not result.candidate_started:
                raise AssertionError("fixture did not observe a started candidate after destination replacement")
        finally:
            try:
                mount_owner.close_all()
            finally:
                if candidate_pidfd >= 0:
                    os.close(candidate_pidfd)
                receiver.close()


class MountDestinationIsolationTests(unittest.TestCase):
    def test_early_or_unstructured_failure_is_not_destination_rejection(self):
        class PrestartFailure(Exception):
            candidate_started = False

        class StartedFailure(Exception):
            candidate_started = True
            infrastructure_failure = True

        self.assertFalse(is_prestart_destination_rejection(PrestartFailure("destination error"), False))
        self.assertFalse(is_prestart_destination_rejection(RuntimeError("destination error"), True))
        self.assertFalse(is_prestart_destination_rejection(PrestartFailure("other error"), True))
        self.assertTrue(is_prestart_destination_rejection(PrestartFailure("destination error"), True))
        self.assertFalse(is_started_destination_rejection(StartedFailure("destination error"), False))
        self.assertFalse(is_started_destination_rejection(RuntimeError("destination error"), True))
        self.assertTrue(is_started_destination_rejection(StartedFailure("destination error"), True))

    def exercise_in_isolated_namespace(self, when):
        with tempfile.TemporaryDirectory() as directory:
            library = str(Path(directory) / "launcher.so")
            subprocess.run(["/usr/bin/cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                            "-fPIC", "-shared", "-pthread", str(SOURCE), "-o", library], check=True)
            subprocess.run(["unshare", "--user", "--map-root-user", "--mount", "--pid", "--fork", "--mount-proc",
                            "/usr/bin/bwrap", "--unshare-pid", "--bind", "/", "/", "--dev", "/dev",
                            "--proc", "/proc", "--", "/usr/bin/python3", str(Path(__file__).resolve()),
                            "--inside", library, when], check=True)

    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires provisioned hosted Bubblewrap/AppArmor")
    def test_destination_replaced_before_mount_is_disclosed(self):
        self.exercise_in_isolated_namespace("before-mount")

    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires provisioned hosted Bubblewrap/AppArmor")
    def test_destination_replaced_after_mount_is_disclosed(self):
        self.exercise_in_isolated_namespace("after-mount")

    @unittest.skipUnless(os.environ.get("VERJSON_TEST_REAL_BWRAP") == "1", "requires provisioned hosted Bubblewrap/AppArmor")
    def test_destination_replaced_after_candidate_start_is_disclosed(self):
        self.exercise_in_isolated_namespace("after-start")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--inside":
        exercise_destination_replacement(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
