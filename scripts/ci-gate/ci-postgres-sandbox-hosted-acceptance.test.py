#!/usr/bin/env python3
"""Exercise the reviewed Postgres boundary on an isolated GitHub-hosted runner."""

from __future__ import annotations

import json
import errno
import inspect
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import types
import typing
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

import ci_build_artifacts
import ci_postgres_sandbox

LOOPBACK_NETWORK_ERRNOS = frozenset((errno.ECONNREFUSED, errno.ENETUNREACH))
OUTBOUND_NETWORK_ERRNOS = frozenset(
    (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ETIMEDOUT)
)


def _checked_output(command: list[str], *, timeout: float = 30) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": os.environ.get("RUNNER_TEMP", "/tmp")},
    )
    if result.returncode != 0:
        raise AssertionError(
            f"trusted acceptance command failed ({result.returncode}): {result.stderr[-2000:]}"
        )
    return result.stdout.strip()


def _assert_network_namespace_error(
    label: str,
    error_number: int | None,
    error_message: str | None,
    allowed_numbers: frozenset[int],
) -> None:
    if error_number not in allowed_numbers or error_message != os.strerror(error_number):
        expected = sorted(
            (number, os.strerror(number)) for number in allowed_numbers
        )
        raise AssertionError(
            f"{label} did not fail at the network namespace boundary: "
            f"expected one of {expected!r}, "
            f"got errno={error_number} ({error_message!r})"
        )


def _docker_daemon_exposures(environ, well_known_sockets=None, timeout=2.0):
    """Return why any Docker daemon endpoint is not proven out of reach.

    The endpoints are dialed directly, not through the docker CLI: `docker info`
    starts every installed CLI plugin before it touches a daemon, so its run time
    depends on the runner, not on the boundary (verJSON/.github#1999). A rootless
    daemon in another network namespace binds under $XDG_RUNTIME_DIR, which is
    dialed whenever it is set. The source ships alone into the sandbox probe, so
    it imports what it needs itself.
    """
    import errno
    import json
    import os
    import socket
    from pathlib import Path
    from urllib.parse import urlsplit

    # Only an answer the kernel gives at once proves a daemon is out of reach;
    # a busy backlog or a timeout says nothing about reachability.
    refused = {
        errno.ENOENT,
        errno.ENOTDIR,
        errno.ECONNREFUSED,
        errno.EACCES,
        errno.EPERM,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
    }

    def connect(parts):
        if parts.scheme == "tcp":
            with socket.create_connection((parts.hostname, parts.port), timeout=timeout):
                return
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(parts.path)

    def probe(endpoint):
        try:
            parts = urlsplit(endpoint)
            dialable = parts.scheme == "unix" or (
                parts.scheme == "tcp" and bool(parts.hostname) and bool(parts.port)
            )
        except ValueError as error:
            return (
                f"Docker endpoint {endpoint} cannot be parsed ({error}); "
                "unreachability not proven"
            )
        if not dialable:
            return (
                f"Docker endpoint {endpoint} is not a unix path or tcp host and port "
                "this probe can dial; unreachability not proven"
            )
        try:
            connect(parts)
        except TimeoutError:
            return (
                f"Docker endpoint {endpoint} did not answer within {timeout}s; "
                "unreachability not proven"
            )
        except OSError as error:
            if error.errno in refused:
                return None
            return (
                f"Docker endpoint {endpoint} failed with errno {error.errno} "
                f"({error.strerror}); unreachability not proven"
            )
        return f"Docker daemon reachable at {endpoint}"

    if well_known_sockets is None:
        well_known_sockets = [
            "/var/run/docker.sock",
            "/run/docker.sock",
            f"/run/user/{os.getuid()}/docker.sock",
        ]
        if environ.get("XDG_RUNTIME_DIR"):
            well_known_sockets.append(f"{environ['XDG_RUNTIME_DIR']}/docker.sock")
    endpoints = [f"unix://{path}" for path in well_known_sockets]
    if environ.get("DOCKER_HOST"):
        endpoints.append(environ["DOCKER_HOST"])
    config_directory = Path(
        environ.get("DOCKER_CONFIG") or Path(environ.get("HOME", "/")) / ".docker"
    )
    exposures = []
    for meta in sorted((config_directory / "contexts" / "meta").glob("*/meta.json")):
        try:
            context = json.loads(meta.read_text(encoding="utf-8"))
            endpoints.append(context["Endpoints"]["docker"]["Host"])
        except (OSError, ValueError, LookupError, TypeError) as error:
            exposures.append(
                f"Docker context {meta} has no readable endpoint ({error!r}); "
                "unreachability not proven"
            )
    exposures.extend(
        exposure
        for exposure in (probe(endpoint) for endpoint in endpoints)
        if exposure is not None
    )
    return exposures


def _unexpected_sockets(root, *, unwalked, entry_limit=100_000):
    """Return each socket under root, other than the Postgres socket, by sandbox path.

    Daemon sockets are found by type, not name, so a containerd, podman, or
    bind-mounted socket at any walked path counts. `unwalked` comes from
    _sandbox_socket_scope: the /proc mount and every read-only bind of a host
    distribution tree, including the node root, which is /usr/local on hosted
    images. Walking those trees, which hold the image's toolchains and SDKs, timed
    the candidate out (verJSON/.github#1999); skipping them is an accepted
    residual that the host-side _distribution_tree_listeners check covers. The
    walk stops after `entry_limit` entries and fails closed, so its run time is
    bounded whatever the walked mounts hold. The source ships alone into the
    sandbox probe.
    """
    import errno
    import os
    import stat

    allowed = {"/run/verjson-postgres/.s.PGSQL.5432"}
    pruned = set(unwalked)

    def visible(path):
        relative = os.path.relpath(path, root)
        return "/" if relative == "." else "/" + relative

    findings = []
    pending = [root]
    visited = 0
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > entry_limit:
                        # A bound walk that cannot finish has proven nothing.
                        return sorted(findings) + [
                            f"socket walk stopped at {visible(directory)} after more than "
                            f"{entry_limit} entries; socket absence not proven"
                        ]
                    path = visible(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        if path not in pruned:
                            pending.append(entry.path)
                    elif (
                        stat.S_ISSOCK(entry.stat(follow_symlinks=False).st_mode)
                        and path not in allowed
                    ):
                        findings.append(f"unexpected socket {path}")
        except OSError as error:
            # The candidate cannot reach a socket below a directory it cannot
            # search; one it can search but not list could hide one.
            if error.errno == errno.EACCES and not os.access(directory, os.X_OK):
                continue
            findings.append(
                f"cannot list {visible(directory)} ({error.strerror}); "
                "socket absence not proven"
            )
    return sorted(findings)


DISTRIBUTION_ROOTS = ("/usr", "/bin", "/lib", "/lib64")
SO_ACCEPTCON = 0x00010000
BUBBLEWRAP_OPTION_ARITY = {
    "--unshare-user": 0,
    "--unshare-pid": 0,
    "--as-pid-1": 0,
    "--unshare-net": 0,
    "--unshare-ipc": 0,
    "--unshare-uts": 0,
    "--unshare-cgroup-try": 0,
    "--disable-userns": 0,
    "--die-with-parent": 0,
    "--new-session": 0,
    "--cap-drop": 1,
    "--tmpfs": 1,
    "--proc": 1,
    "--dev": 1,
    "--dir": 1,
    "--ro-bind": 2,
    "--setenv": 2,
}


class SandboxSocketScope(typing.NamedTuple):
    unwalked: tuple[str, ...]
    host_trees: tuple[str, ...]


def _sandbox_socket_scope(arguments: list[str]) -> SandboxSocketScope:
    """Split the sandbox's mounts into walked ones and host-covered ones.

    Both lists come from the bubblewrap arguments the sandbox is built from. A
    read-only bind of a host distribution tree is not walked inside the sandbox;
    the host-side listener check covers its source instead.
    """
    def within(path, tree):
        return path == tree or path.startswith(tree.rstrip("/") + "/")

    unwalked = []
    host_trees = []
    index = 1
    while index < len(arguments) and arguments[index] != "--":
        option = arguments[index]
        if option not in BUBBLEWRAP_OPTION_ARITY:
            raise ValueError(f"socket scope cannot classify bubblewrap option {option!r}")
        arity = BUBBLEWRAP_OPTION_ARITY[option]
        operands = arguments[index + 1 : index + 1 + arity]
        if len(operands) != arity:
            raise ValueError(f"socket scope found {option!r} without its {arity} operands")
        if option in ("--tmpfs", "--dev", "--proc", "--ro-bind"):
            # A later mount above, at, or below a skipped tree shows content the
            # skip was never proven for; an earlier one is hidden by it. --dir
            # only creates a directory.
            overlapped = [
                tree
                for tree in unwalked
                if within(tree, operands[-1]) or within(operands[-1], tree)
            ]
            if overlapped:
                raise ValueError(
                    f"socket scope: mount {operands[-1]} overlaps unwalked "
                    f"{', '.join(overlapped)}"
                )
        if option == "--proc":
            unwalked.append(operands[0])
        elif option == "--ro-bind" and any(
            within(os.path.realpath(operands[0]), os.path.realpath(root))
            for root in DISTRIBUTION_ROOTS
        ):
            unwalked.append(operands[1])
            host_trees.append(operands[0])
        index += 1 + arity
    if index >= len(arguments):
        raise ValueError("socket scope found no '--' ending the bubblewrap options")
    return SandboxSocketScope(tuple(unwalked), tuple(host_trees))


def _distribution_tree_listeners(
    proc_net_unix: str,
    trees: tuple[str, ...],
) -> list[str]:
    """Return each host listener the sandbox's unwalked read-only trees could expose.

    The sandbox socket walk skips the read-only distribution binds that
    _sandbox_socket_scope names, so the trusted host reads its own /proc/net/unix
    for their sources instead: a listening pathname socket (Flags 00010000) bound
    under one of them would be visible inside the sandbox through the read-only
    bind. The table records each bound path verbatim, so a path is resolved
    (symlinks and `..`) and compared with each resolved tree, which also covers a
    merged /usr where /bin and /lib link into it. A relative path, or a row this
    parser cannot read, fails closed.

    It cannot see listeners in other network namespaces, whose sockets this
    table omits, or a socket renamed into one of the trees after bind(), whose
    row keeps the original path.
    """
    lines = proc_net_unix.splitlines()
    header = lines[0] if lines else ""
    if header.split() != ["Num", "RefCount", "Protocol", "Flags", "Type", "St", "Inode", "Path"]:
        return [
            f"/proc/net/unix header {header!r} is not the known layout; "
            "listener absence not proven"
        ]
    findings = []
    for number, line in enumerate(lines[1:], start=2):
        fields = line.split(None, 7)
        try:
            if len(fields) not in (7, 8) or not fields[0].endswith(":"):
                raise ValueError(f"expected 7 or 8 fields after 'Num:', got {len(fields)}")
            flags = int(fields[3], 16)
            for field in fields[1:6]:
                int(field, 16)
            int(fields[6], 10)
        except ValueError as error:
            findings.append(
                f"/proc/net/unix line {number} is unparseable ({error}): {line!r}; "
                "listener absence not proven"
            )
            continue
        path = fields[7] if len(fields) == 8 else ""
        if not flags & SO_ACCEPTCON or not path or path.startswith("@"):
            continue
        if not path.startswith("/"):
            # A relative bind resolves against the binder's working directory,
            # which this table does not record.
            findings.append(
                f"host socket listening at relative path {path!r}; "
                "listener location not proven"
            )
            continue
        if any(
            os.path.realpath(path).startswith(os.path.realpath(tree) + "/") for tree in trees
        ):
            findings.append(f"host socket listening at {path}")
    return findings


def _assert_no_host_distribution_tree_listeners(
    trees: tuple[str, ...],
    proc_net_unix: Path = Path("/proc/net/unix"),
) -> None:
    findings = _distribution_tree_listeners(
        proc_net_unix.read_text(encoding="utf-8", errors="surrogateescape"),
        trees,
    )
    if findings:
        raise AssertionError("; ".join(findings))


def _daemon_isolation_probe_source(
    unwalked: tuple[str, ...],
    socket_root: str = "/",
) -> str:
    """Return a self-contained sandbox script that exits while any daemon is in reach."""
    return (
        inspect.getsource(_docker_daemon_exposures)
        + "\n"
        + inspect.getsource(_unexpected_sockets)
        + "\n_findings = _docker_daemon_exposures(__import__('os').environ)"
        + f" + _unexpected_sockets({socket_root!r}, unwalked={tuple(unwalked)!r})\n"
        + "if _findings:\n"
        + "    raise SystemExit('; '.join(_findings))\n"
    )

class NetworkBoundaryAssertionTests(unittest.TestCase):
    def test_unrelated_socket_error_is_not_accepted_as_network_isolation(self) -> None:
        with self.assertRaisesRegex(AssertionError, "network namespace"):
            _assert_network_namespace_error(
                "TCP egress",
                errno.ECONNREFUSED,
                "Connection refused",
                OUTBOUND_NETWORK_ERRNOS,
            )

    def test_network_unreachable_requires_its_matching_message(self) -> None:
        with self.assertRaisesRegex(AssertionError, "network namespace"):
            _assert_network_namespace_error(
                "TCP egress",
                errno.ENETUNREACH,
                "Permission denied",
                OUTBOUND_NETWORK_ERRNOS,
            )

    def test_accepts_the_specific_network_unreachable_error(self) -> None:
        _assert_network_namespace_error(
            "TCP egress",
            errno.ENETUNREACH,
            os.strerror(errno.ENETUNREACH),
            OUTBOUND_NETWORK_ERRNOS,
        )

    def test_loopback_allows_only_its_network_specific_failures(self) -> None:
        _assert_network_namespace_error(
            "loopback probe",
            errno.ECONNREFUSED,
            os.strerror(errno.ECONNREFUSED),
            LOOPBACK_NETWORK_ERRNOS,
        )


class DockerDaemonExposureTests(unittest.TestCase):
    def setUp(self) -> None:
        # A short base keeps socket paths inside the 107-byte AF_UNIX limit.
        directory = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def _listening_unix_socket(self, name: str) -> Path:
        path = self.root / name
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(str(path))
        listener.listen(8)
        return path

    def _write_context(self, config_directory: Path, meta: str) -> None:
        context = config_directory / "contexts" / "meta" / "0123abcd"
        context.mkdir(parents=True)
        (context / "meta.json").write_text(meta, encoding="utf-8")

    def test_listening_well_known_socket_is_reported_reachable(self) -> None:
        daemon = self._listening_unix_socket("docker.sock")

        exposures = _docker_daemon_exposures(
            {"HOME": str(self.root)},
            well_known_sockets=(str(daemon),),
        )

        self.assertEqual(len(exposures), 1)
        self.assertIn("Docker daemon reachable", exposures[0])
        self.assertIn(str(daemon), exposures[0])

    def test_stale_socket_without_a_listener_is_unreachable(self) -> None:
        stale = self.root / "docker.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as abandoned:
            abandoned.bind(str(stale))

        exposures = _docker_daemon_exposures(
            {"HOME": str(self.root)},
            well_known_sockets=(str(stale),),
        )

        self.assertEqual(exposures, [])

    def test_connect_that_times_out_is_not_counted_unreachable(self) -> None:
        daemon = self._listening_unix_socket("docker.sock")

        with patch.object(socket.socket, "connect", side_effect=TimeoutError("timed out")):
            exposures = _docker_daemon_exposures(
                {"HOME": str(self.root)},
                well_known_sockets=(str(daemon),),
            )

        self.assertEqual(len(exposures), 1)
        self.assertIn("did not answer within", exposures[0])
        self.assertIn("unreachability not proven", exposures[0])

    def test_non_definitive_connect_errors_are_not_counted_unreachable(self) -> None:
        for number in (errno.ECONNRESET, errno.EINVAL, errno.EAGAIN):
            with self.subTest(errno=errno.errorcode[number]):
                failure = OSError(number, os.strerror(number))
                with patch.object(socket.socket, "connect", side_effect=failure):
                    exposures = _docker_daemon_exposures(
                        {"HOME": str(self.root)},
                        well_known_sockets=("/run/docker.sock",),
                    )

                self.assertEqual(len(exposures), 1)
                self.assertIn(f"errno {number}", exposures[0])
                self.assertIn("unreachability not proven", exposures[0])

    def test_unresolvable_tcp_host_is_not_counted_unreachable(self) -> None:
        unresolved = socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")

        with patch.object(socket, "getaddrinfo", side_effect=unresolved):
            exposures = _docker_daemon_exposures(
                {"HOME": str(self.root), "DOCKER_HOST": "tcp://daemon.example:2375"},
                well_known_sockets=(),
            )

        self.assertEqual(len(exposures), 1)
        self.assertIn(f"errno {socket.EAI_AGAIN}", exposures[0])
        self.assertIn("unreachability not proven", exposures[0])

    def test_each_definitive_refusal_counts_as_unreachable(self) -> None:
        for number in (
            errno.ENOENT,
            errno.ENOTDIR,
            errno.ECONNREFUSED,
            errno.EACCES,
            errno.EPERM,
            errno.ENETUNREACH,
            errno.EHOSTUNREACH,
        ):
            with self.subTest(errno=errno.errorcode[number]):
                refusal = OSError(number, os.strerror(number))
                with patch.object(socket.socket, "connect", side_effect=refusal):
                    exposures = _docker_daemon_exposures(
                        {"HOME": str(self.root)},
                        well_known_sockets=("/run/docker.sock",),
                    )

                self.assertEqual(exposures, [])

    def test_docker_host_unix_endpoint_is_probed(self) -> None:
        daemon = self._listening_unix_socket("remote.sock")

        exposures = _docker_daemon_exposures(
            {"HOME": str(self.root), "DOCKER_HOST": f"unix://{daemon}"},
            well_known_sockets=(),
        )

        self.assertEqual(exposures, [f"Docker daemon reachable at unix://{daemon}"])

    def test_docker_host_tcp_endpoint_is_probed(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        endpoint = f"tcp://127.0.0.1:{listener.getsockname()[1]}"

        exposures = _docker_daemon_exposures(
            {"HOME": str(self.root), "DOCKER_HOST": endpoint},
            well_known_sockets=(),
        )

        self.assertEqual(exposures, [f"Docker daemon reachable at {endpoint}"])

    def test_tcp_endpoint_without_a_usable_port_is_not_counted_unreachable(self) -> None:
        for endpoint in ("tcp://127.0.0.1", "tcp://127.0.0.1:docker"):
            with self.subTest(endpoint=endpoint):
                exposures = _docker_daemon_exposures(
                    {"HOME": str(self.root), "DOCKER_HOST": endpoint},
                    well_known_sockets=(),
                )

                self.assertEqual(len(exposures), 1)
                self.assertIn(endpoint, exposures[0])
                self.assertIn("unreachability not proven", exposures[0])

    def test_unparseable_endpoint_is_reported_with_its_reason(self) -> None:
        exposures = _docker_daemon_exposures(
            {"HOME": str(self.root), "DOCKER_HOST": "tcp://[::1"},
            well_known_sockets=(),
        )

        self.assertEqual(len(exposures), 1)
        self.assertIn("tcp://[::1", exposures[0])
        self.assertIn("Invalid IPv6 URL", exposures[0])
        self.assertIn("unreachability not proven", exposures[0])

    def test_endpoint_the_probe_cannot_dial_is_not_counted_unreachable(self) -> None:
        exposures = _docker_daemon_exposures(
            {"HOME": str(self.root), "DOCKER_HOST": "npipe:////./pipe/docker_engine"},
            well_known_sockets=(),
        )

        self.assertEqual(len(exposures), 1)
        self.assertIn("npipe:////./pipe/docker_engine", exposures[0])
        self.assertIn("unreachability not proven", exposures[0])

    def test_configured_context_endpoint_is_probed(self) -> None:
        daemon = self._listening_unix_socket("context.sock")
        self._write_context(
            self.root / ".docker",
            json.dumps({"Name": "remote", "Endpoints": {"docker": {"Host": f"unix://{daemon}"}}}),
        )

        exposures = _docker_daemon_exposures(
            {"HOME": str(self.root)},
            well_known_sockets=(),
        )

        self.assertEqual(exposures, [f"Docker daemon reachable at unix://{daemon}"])

    def test_unreadable_context_is_not_counted_unreachable(self) -> None:
        config_directory = self.root / "docker-config"
        self._write_context(config_directory, "{not json")

        exposures = _docker_daemon_exposures(
            {"HOME": str(self.root), "DOCKER_CONFIG": str(config_directory)},
            well_known_sockets=(),
        )

        self.assertEqual(len(exposures), 1)
        self.assertIn("meta.json", exposures[0])
        self.assertIn("unreachability not proven", exposures[0])

    def test_default_probe_covers_system_and_rootless_daemon_sockets(self) -> None:
        dialed = []

        def refuse(address):
            dialed.append(address)
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT))

        runtime_directory = self.root / "runtime"
        with patch.object(socket.socket, "connect", side_effect=refuse):
            exposures = _docker_daemon_exposures(
                {"HOME": str(self.root), "XDG_RUNTIME_DIR": str(runtime_directory)},
            )

        self.assertEqual(exposures, [])
        self.assertEqual(
            set(dialed),
            {
                "/var/run/docker.sock",
                "/run/docker.sock",
                f"/run/user/{os.getuid()}/docker.sock",
                f"{runtime_directory}/docker.sock",
            },
        )

    def test_sandbox_probe_source_exits_when_a_daemon_answers(self) -> None:
        source = _daemon_isolation_probe_source(HOSTED_UNWALKED, socket_root=str(self.root))

        with patch.dict(os.environ, {"HOME": str(self.root)}, clear=True), patch.object(
            socket.socket, "connect", return_value=None
        ):
            with self.assertRaisesRegex(SystemExit, "Docker daemon reachable at unix:///var/run/docker.sock"):
                exec(compile(source, "<docker-probe>", "exec"), {"__name__": "docker_probe"})

    def test_sandbox_probe_source_passes_when_no_daemon_answers(self) -> None:
        source = _daemon_isolation_probe_source(HOSTED_UNWALKED, socket_root=str(self.root))
        absent = FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT))

        with patch.dict(os.environ, {"HOME": str(self.root)}, clear=True), patch.object(
            socket.socket, "connect", side_effect=absent
        ):
            exec(compile(source, "<docker-probe>", "exec"), {"__name__": "docker_probe"})


def _hosted_shape_bubblewrap_arguments(*extra: str) -> list[str]:
    """The hosted sandbox's mount list: the node root is /usr/local there."""
    return [
        "/usr/bin/bwrap",
        "--unshare-user",
        "--unshare-pid",
        "--as-pid-1",
        "--unshare-net",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup-try",
        "--disable-userns",
        "--die-with-parent",
        "--new-session",
        "--cap-drop", "ALL",
        "--tmpfs", "/",
        "--tmpfs", "/tmp",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/etc", "/etc",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/dev/shm",
        "--dir", "/run",
        "--ro-bind", "/home/runner/work/_temp/profile/socket", "/run/verjson-postgres",
        "--dir", "/opt",
        "--dir", "/opt/verjson",
        "--ro-bind", "/home/runner/work/_temp/profile/client", "/opt/verjson/postgres-client",
        "--dir", "/opt/verjson/node",
        "--ro-bind", "/usr/local", "/opt/verjson/node",
        "--dir", "/opt/verjson/drizzle",
        "--ro-bind", "/home/runner/work/fixtures/drizzle", "/opt/verjson/drizzle",
        *extra,
        "--setenv", "HOME", "/tmp",
        "--",
        "/usr/bin/python3",
    ]


HOSTED_SCOPE = _sandbox_socket_scope(_hosted_shape_bubblewrap_arguments())
HOSTED_UNWALKED = HOSTED_SCOPE.unwalked
HOSTED_HOST_TREES = HOSTED_SCOPE.host_trees


class SandboxSocketInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        # A short base keeps socket paths inside the 107-byte AF_UNIX limit.
        directory = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def _bound_socket(self, relative: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bound:
            bound.bind(str(path))

    def test_socket_other_than_postgres_is_reported_by_its_sandbox_path(self) -> None:
        self._bound_socket("run/verjson-postgres/.s.PGSQL.5432")
        self._bound_socket("run/containerd/containerd.sock")

        findings = _unexpected_sockets(str(self.root), unwalked=HOSTED_UNWALKED)

        self.assertEqual(len(findings), 1)
        self.assertIn("/run/containerd/containerd.sock", findings[0])
        self.assertNotIn(str(self.root), findings[0])

    def test_unreadable_directory_fails_closed_with_its_reason(self) -> None:
        (self.root / "run" / "sealed").mkdir(parents=True)
        real_scandir = os.scandir

        def scandir(path):
            if Path(path) == self.root / "run" / "sealed":
                raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), path)
            return real_scandir(path)

        with patch.object(os, "scandir", side_effect=scandir):
            findings = _unexpected_sockets(str(self.root), unwalked=HOSTED_UNWALKED)

        self.assertEqual(len(findings), 1)
        self.assertIn("/run/sealed", findings[0])
        self.assertIn(os.strerror(errno.EACCES), findings[0])
        self.assertIn("socket absence not proven", findings[0])

    def test_directory_the_candidate_cannot_search_is_out_of_reach(self) -> None:
        sealed = self.root / "etc" / "ssl" / "private"
        sealed.mkdir(parents=True)
        real_scandir = os.scandir
        real_access = os.access

        def scandir(path):
            if Path(path) == sealed:
                raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), path)
            return real_scandir(path)

        def access(path, mode):
            if Path(path) == sealed:
                return False
            return real_access(path, mode)

        with patch.object(os, "scandir", side_effect=scandir), patch.object(
            os, "access", side_effect=access
        ):
            findings = _unexpected_sockets(str(self.root), unwalked=HOSTED_UNWALKED)

        self.assertEqual(findings, [])

    def test_symlinked_directories_are_not_followed(self) -> None:
        (self.root / "run").mkdir()
        (self.root / "run" / "loop").symlink_to(self.root / "run")

        findings = _unexpected_sockets(str(self.root), unwalked=HOSTED_UNWALKED)

        self.assertEqual(findings, [])

    def test_kernel_and_read_only_distribution_trees_are_not_walked(self) -> None:
        for tree in ("proc", "usr", "bin", "lib", "lib64", "opt/verjson/node"):
            self._bound_socket(f"{tree}/nested/daemon.sock")

        findings = _unexpected_sockets(str(self.root), unwalked=HOSTED_UNWALKED)

        self.assertEqual(findings, [])

    def test_walk_past_its_entry_limit_fails_closed_with_a_reason(self) -> None:
        crowded = self.root / "etc" / "crowded"
        crowded.mkdir(parents=True)
        for index in range(6):
            (crowded / f"entry-{index}").touch()

        findings = _unexpected_sockets(str(self.root), unwalked=(), entry_limit=5)

        self.assertEqual(len(findings), 1)
        self.assertIn("more than 5 entries", findings[0])
        self.assertIn("socket absence not proven", findings[0])

    def test_walk_past_its_entry_limit_keeps_what_it_already_found(self) -> None:
        self._bound_socket("engine.sock")
        crowded = self.root / "etc"
        crowded.mkdir()
        for index in range(6):
            (crowded / f"entry-{index}").touch()

        findings = _unexpected_sockets(str(self.root), unwalked=(), entry_limit=5)

        self.assertEqual(findings[0], "unexpected socket /engine.sock")
        self.assertIn("socket absence not proven", findings[1])

    def test_walk_within_its_entry_limit_passes(self) -> None:
        crowded = self.root / "etc"
        crowded.mkdir()
        for index in range(4):
            (crowded / f"entry-{index}").touch()

        self.assertEqual(_unexpected_sockets(str(self.root), unwalked=(), entry_limit=5), [])

    def test_walk_skips_exactly_the_unwalked_mounts_it_is_given(self) -> None:
        self._bound_socket("opt/verjson/node/lib/daemon.sock")
        self._bound_socket("usr/lib/daemon.sock")

        findings = _unexpected_sockets(str(self.root), unwalked=("/opt/verjson/node",))

        self.assertEqual(findings, ["unexpected socket /usr/lib/daemon.sock"])

    def test_every_other_tree_is_walked_by_path_not_by_name(self) -> None:
        relatives = [f"{tree}/nested/daemon.sock" for tree in ("etc", "tmp", "opt", "dev", "var", "run")]
        relatives += ["opt/x/lib/d.sock", "tmp/bin/d.sock"]
        for relative in relatives:
            self._bound_socket(relative)

        findings = _unexpected_sockets(str(self.root), unwalked=HOSTED_UNWALKED)

        self.assertEqual(
            findings,
            sorted(f"unexpected socket /{relative}" for relative in relatives),
        )

    def test_postgres_socket_name_elsewhere_is_still_reported(self) -> None:
        for relative in ("tmp/.s.PGSQL.5432", "run/other/.s.PGSQL.5432"):
            self._bound_socket(relative)

        findings = _unexpected_sockets(str(self.root), unwalked=HOSTED_UNWALKED)

        self.assertEqual(
            findings,
            [
                "unexpected socket /run/other/.s.PGSQL.5432",
                "unexpected socket /tmp/.s.PGSQL.5432",
            ],
        )

    def test_sandbox_probe_source_skips_the_unwalked_mounts_it_is_given(self) -> None:
        self._bound_socket("usr/lib/engine.sock")
        source = _daemon_isolation_probe_source(("/usr",), socket_root=str(self.root))
        absent = FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT))

        with patch.dict(os.environ, {"HOME": str(self.root)}, clear=True), patch.object(
            socket.socket, "connect", side_effect=absent
        ):
            exec(compile(source, "<daemon-probe>", "exec"), {"__name__": "daemon_probe"})

    def test_sandbox_probe_source_exits_on_an_unexpected_socket(self) -> None:
        self._bound_socket("run/podman/podman.sock")
        source = _daemon_isolation_probe_source(HOSTED_UNWALKED, socket_root=str(self.root))
        absent = FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT))

        with patch.dict(os.environ, {"HOME": str(self.root)}, clear=True), patch.object(
            socket.socket, "connect", side_effect=absent
        ):
            with self.assertRaisesRegex(SystemExit, "unexpected socket /run/podman/podman.sock"):
                exec(compile(source, "<daemon-probe>", "exec"), {"__name__": "daemon_probe"})


PROC_NET_UNIX_HEADER = "Num       RefCount Protocol Flags    Type St Inode Path"


def _proc_net_unix(*rows: str) -> str:
    return "\n".join((PROC_NET_UNIX_HEADER, *rows)) + "\n"


class SandboxSocketScopeTests(unittest.TestCase):
    def test_distribution_binds_are_unwalked_and_host_covered(self) -> None:
        scope = _sandbox_socket_scope(_hosted_shape_bubblewrap_arguments())

        self.assertEqual(
            scope.unwalked,
            ("/usr", "/bin", "/lib", "/lib64", "/proc", "/opt/verjson/node"),
        )
        self.assertEqual(scope.host_trees, ("/usr", "/bin", "/lib", "/lib64", "/usr/local"))

    def test_every_unwalked_bind_is_host_covered_and_every_other_bind_walked(self) -> None:
        arguments = _hosted_shape_bubblewrap_arguments(
            "--ro-bind", "/home/runner/work/_temp/shim", "/tmp/untrusted-bin",
        )
        options = arguments[: arguments.index("--")]
        binds = [
            (options[index + 1], options[index + 2])
            for index, option in enumerate(options)
            if option == "--ro-bind"
        ]

        scope = _sandbox_socket_scope(arguments)

        covered = [(source, target) for source, target in binds if target in scope.unwalked]
        self.assertEqual(tuple(source for source, _ in covered), scope.host_trees)
        self.assertEqual(
            sorted(set(scope.unwalked) - {target for _, target in covered}),
            ["/proc"],
        )
        self.assertEqual(
            sorted(target for source, target in binds if (source, target) not in covered),
            [
                "/etc",
                "/opt/verjson/drizzle",
                "/opt/verjson/postgres-client",
                "/run/verjson-postgres",
                "/tmp/untrusted-bin",
            ],
        )

    def test_real_hosted_bind_list_matches_the_pinned_scope(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            socket_directory = root / "socket"
            socket_directory.mkdir()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as postgres:
                postgres.bind(str(socket_directory / ci_postgres_sandbox.POSTGRES_SOCKET_NAME))
                client_bin = root / "client" / "bin"
                client_bin.mkdir(parents=True)
                for tool in ("psql", "pg_dump"):
                    (client_bin / tool).write_text("#!/bin/sh\n", encoding="utf-8")
                    (client_bin / tool).chmod(0o755)
                hosted = types.SimpleNamespace(
                    primary={"containerName": "primary"},
                    secondary={"containerName": "secondary"},
                    schema_profile={"containerName": "schema"},
                    primary_state_file=root / "primary.json",
                    secondary_state_file=root / "secondary.json",
                    schema_state_file=root / "schema.json",
                    runner_temp=root,
                    bwrap="/usr/bin/bwrap",
                    node_root=Path("/usr/local"),
                    drizzle_dir=root / "drizzle",
                )
                state = {
                    "containerName": "primary",
                    "profileName": ci_postgres_sandbox.PROFILE_NAME,
                    "socketDirectory": str(socket_directory),
                    "clientBundleDirectory": str(root / "client"),
                }
                with patch.object(ci_postgres_sandbox, "verify_profile_runtime"), patch.object(
                    ci_postgres_sandbox, "verify_local_auth_policy"
                ):
                    arguments = PostgresSandboxHostedAcceptanceTests._bubblewrap_command(
                        hosted, state, []
                    )

        self.assertEqual(_sandbox_socket_scope(arguments), HOSTED_SCOPE)

    def test_unknown_truncated_or_unterminated_options_fail_closed(self) -> None:
        unknown = _hosted_shape_bubblewrap_arguments("--bind", "/var/run", "/var/run")
        prefix = _hosted_shape_bubblewrap_arguments()
        options = prefix[: prefix.index("--")]
        for label, arguments, reason in (
            ("unknown", unknown, "cannot classify bubblewrap option '--bind'"),
            ("truncated", options + ["--ro-bind", "/srv"], "'--ro-bind' without its 2 operands"),
            ("unterminated", options, "no '--' ending the bubblewrap options"),
        ):
            with self.subTest(label):
                with self.assertRaisesRegex(ValueError, f"socket scope.*{re.escape(reason)}"):
                    _sandbox_socket_scope(arguments)

    def test_bind_source_is_classified_by_its_resolved_path(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            link = Path(directory) / "node"
            link.symlink_to("/usr/local")
            scope = _sandbox_socket_scope(
                _hosted_shape_bubblewrap_arguments("--ro-bind", str(link), "/opt/tools")
            )

        self.assertEqual(scope.unwalked[-1], "/opt/tools")
        self.assertEqual(scope.host_trees[-1], str(link))

    def test_bind_source_sharing_only_a_name_prefix_is_walked(self) -> None:
        scope = _sandbox_socket_scope(
            _hosted_shape_bubblewrap_arguments(
                "--ro-bind", "/usrdata/x", "/opt/data",
                "--dir", "/opt/verjson/nodes",
            )
        )

        self.assertNotIn("/opt/data", scope.unwalked)
        self.assertNotIn("/usrdata/x", scope.host_trees)

    def test_each_distribution_root_counts_without_a_merged_usr(self) -> None:
        with patch.object(os.path, "realpath", side_effect=lambda path: path):
            scope = _sandbox_socket_scope(_hosted_shape_bubblewrap_arguments())

        self.assertEqual(scope, HOSTED_SCOPE)

    def test_walked_mount_under_an_unwalked_bind_fails_closed(self) -> None:
        arguments = _hosted_shape_bubblewrap_arguments(
            "--ro-bind", "/home/runner/work/_temp/shim", "/usr/local/shim",
        )

        with self.assertRaisesRegex(ValueError, "mount /usr/local/shim overlaps unwalked /usr"):
            _sandbox_socket_scope(arguments)

    def test_mount_over_an_ancestor_of_an_unwalked_bind_fails_closed(self) -> None:
        for extra in (
            ("--tmpfs", "/opt"),
            ("--ro-bind", "/home/runner/work/_temp/shim", "/opt/verjson"),
            ("--ro-bind", "/home/runner/work/_temp/shim", "/"),
        ):
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(
                    ValueError,
                    f"socket scope: mount {re.escape(extra[-1])} overlaps unwalked .*/opt/verjson/node",
                ):
                    _sandbox_socket_scope(_hosted_shape_bubblewrap_arguments(*extra))

    def test_device_proc_and_distribution_mounts_over_an_ancestor_fail_closed(self) -> None:
        for extra in (("--dev", "/opt"), ("--proc", "/opt"), ("--ro-bind", "/usr/share", "/opt")):
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(ValueError, "overlaps unwalked /opt/verjson/node"):
                    _sandbox_socket_scope(_hosted_shape_bubblewrap_arguments(*extra))

    def test_directory_created_at_an_ancestor_of_an_unwalked_bind_is_accepted(self) -> None:
        scope = _sandbox_socket_scope(_hosted_shape_bubblewrap_arguments("--dir", "/opt/verjson"))

        self.assertEqual(scope, HOSTED_SCOPE)

    def test_mount_replacing_an_unwalked_bind_fails_closed(self) -> None:
        arguments = _hosted_shape_bubblewrap_arguments(
            "--ro-bind", "/home/runner/work/_temp/shim", "/opt/verjson/node",
        )

        with self.assertRaisesRegex(ValueError, "mount /opt/verjson/node overlaps unwalked /opt/verjson/node"):
            _sandbox_socket_scope(arguments)


class HostDistributionTreeListenerTests(unittest.TestCase):
    def test_listener_under_a_read_only_tree_is_reported(self) -> None:
        table = _proc_net_unix(
            "0000000000000000: 00000002 00000000 00010000 0001 01 41234 /usr/lib/engine.sock",
        )

        findings = _distribution_tree_listeners(table, HOSTED_HOST_TREES)

        self.assertEqual(len(findings), 1)
        self.assertIn("/usr/lib/engine.sock", findings[0])

    def test_listener_under_each_read_only_tree_is_reported_by_full_path(self) -> None:
        paths = ["/bin/engine.sock", "/lib/engine.sock", "/lib64/engine.sock", "/usr/local/my engine.sock"]
        table = _proc_net_unix(
            *(f"0000000000000000: 00000002 00000000 00010000 0001 01 4123{index} {path}" for index, path in enumerate(paths))
        )

        findings = _distribution_tree_listeners(table, HOSTED_HOST_TREES)

        self.assertEqual(findings, [f"host socket listening at {path}" for path in paths])

    def test_dot_dot_path_into_a_read_only_tree_is_reported(self) -> None:
        table = _proc_net_unix(
            "0000000000000000: 00000002 00000000 00010000 0001 01 41234 /tmp/../usr/lib/engine.sock",
        )

        findings = _distribution_tree_listeners(table, HOSTED_HOST_TREES)

        self.assertEqual(len(findings), 1)
        self.assertIn("/tmp/../usr/lib/engine.sock", findings[0])

    def test_symlinked_path_into_a_read_only_tree_is_reported(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            link = Path(directory) / "toolchain"
            link.symlink_to("/usr/lib")
            path = f"{link}/engine.sock"
            table = _proc_net_unix(
                f"0000000000000000: 00000002 00000000 00010000 0001 01 41234 {path}",
            )

            findings = _distribution_tree_listeners(table, HOSTED_HOST_TREES)

        self.assertEqual(len(findings), 1)
        self.assertIn(path, findings[0])

    def test_tree_reached_through_a_merged_usr_link_is_matched(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            root = Path(directory)
            (root / "usr" / "bin").mkdir(parents=True)
            (root / "bin").symlink_to("usr/bin")
            path = f"{root}/usr/bin/engine.sock"
            table = _proc_net_unix(
                f"0000000000000000: 00000002 00000000 00010000 0001 01 41234 {path}",
            )

            findings = _distribution_tree_listeners(table, trees=(f"{root}/bin",))

        self.assertEqual(findings, [f"host socket listening at {path}"])

    def test_relative_listener_path_is_not_proven_outside_the_trees(self) -> None:
        table = _proc_net_unix(
            "0000000000000000: 00000002 00000000 00010000 0001 01 41234 engine.sock",
            "0000000000000000: 00000002 00000000 00000000 0001 01 41235 connected.sock",
        )

        findings = _distribution_tree_listeners(table, HOSTED_HOST_TREES)

        self.assertEqual(len(findings), 1)
        self.assertIn("engine.sock", findings[0])
        self.assertIn("listener location not proven", findings[0])

    def test_rows_outside_the_residual_are_not_reported(self) -> None:
        table = _proc_net_unix(
            "0000000000000000: 00000002 00000000 00000000 0001 01 41230 /usr/lib/connected.sock",
            "0000000000000000: 00000002 00000000 00010000 0001 01 41231 /run/docker.sock",
            "0000000000000000: 00000002 00000000 00010000 0001 01 41232 /usrlocal/engine.sock",
            "0000000000000000: 00000002 00000000 00010000 0001 01 41233 /library/engine.sock",
            "0000000000000000: 00000002 00000000 00010000 0001 01 41234 @/usr/abstract",
            "0000000000000000: 00000002 00000000 00010000 0001 01 41235",
        )

        self.assertEqual(_distribution_tree_listeners(table, HOSTED_HOST_TREES), [])

    def test_unparseable_rows_fail_closed_with_a_reason(self) -> None:
        for row in (
            "0000000000000000: 00000002 00000000 0001 01 41234",
            "0000000000000000: 00000002 00000000 listening 0001 01 41234 /run/x.sock",
            "0000000000000000: 00000002 00000000 00010000 0001 01 inode /run/x.sock",
            "0000000000000000: refs 00000000 00010000 0001 01 41234 /run/x.sock",
            "0000000000000000 00000002 00000000 00010000 0001 01 41234 /run/x.sock",
            "",
        ):
            with self.subTest(row=row):
                findings = _distribution_tree_listeners(_proc_net_unix(row), HOSTED_HOST_TREES)

                self.assertEqual(len(findings), 1)
                self.assertIn("line 2", findings[0])
                self.assertIn("listener absence not proven", findings[0])

    def test_host_check_reads_the_table_and_fails_on_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            table = Path(directory) / "unix"
            table.write_text(
                _proc_net_unix(
                    "0000000000000000: 00000002 00000000 00010000 0001 01 41234 /usr/lib/engine.sock",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(AssertionError, "host socket listening at /usr/lib/engine.sock"):
                _assert_no_host_distribution_tree_listeners(HOSTED_HOST_TREES, table)

    def test_unrecognized_header_fails_closed(self) -> None:
        for table in (
            "",
            "Num RefCount Protocol Flags Type St Inode Netns Path\n",
        ):
            with self.subTest(table=table):
                findings = _distribution_tree_listeners(table, HOSTED_HOST_TREES)

                self.assertEqual(len(findings), 1)
                self.assertIn("header", findings[0])
                self.assertIn("listener absence not proven", findings[0])


class PostgresSandboxHostedAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get("VERJSON_CI_POSTGRES_HOSTED_ACCEPTANCE") != "1":
            raise unittest.SkipTest("requires the explicit Postgres hosted acceptance step")
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise unittest.SkipTest("requires GitHub Actions")
        if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
            raise AssertionError("Postgres sandbox acceptance requires a GitHub-hosted runner")

        cls.runner_temp = Path(os.environ["RUNNER_TEMP"]).resolve(strict=True)
        cls.primary_state_file = cls.runner_temp / "ci-postgres-sandbox-acceptance-state.json"
        cls.secondary_state_file = cls.runner_temp / "ci-postgres-sandbox-fresh-state.json"
        cls.schema_state_file = cls.runner_temp / "ci-postgres-schema-validation-state.json"
        cls.primary = None
        cls.secondary = None
        cls.schema_profile = None
        cls.npm_global_config = cls.runner_temp / "ci-postgres-sandbox-npm-globalconfig"
        cls.candidate_state_file = None
        cls.candidate_state_snapshot = None
        candidate_state_path = os.environ.get("VERJSON_CI_POSTGRES_CANDIDATE_STATE")
        if candidate_state_path:
            cls.candidate_state_file = Path(candidate_state_path)
            candidate_state = ci_postgres_sandbox.read_profile_state(
                cls.candidate_state_file,
                cls.runner_temp,
            )
            if candidate_state["status"] != "ready":
                raise AssertionError("production Postgres candidate profile is not ready")
            candidate_bundle = Path(candidate_state["clientBundleDirectory"])
            if any(candidate_bundle.iterdir()):
                raise AssertionError("production Postgres profile was staged before acceptance")
            cls.candidate_state_snapshot = cls.candidate_state_file.read_bytes()
        try:
            if cls.primary_state_file.exists() or cls.primary_state_file.is_symlink():
                raise AssertionError("disposable Postgres acceptance state path already exists")
            cls.primary = ci_postgres_sandbox.provision_profile(
                ci_postgres_sandbox.PROFILE_NAME,
                runner_temp=cls.runner_temp,
                state_file=cls.primary_state_file,
            )
            cls.primary = ci_postgres_sandbox.prepare_profile_for_candidate(
                cls.primary_state_file,
                runner_temp=cls.runner_temp,
            )
            cls.profile = ci_postgres_sandbox.resolve_profile(cls.primary["profileName"])
            cls.bwrap = shutil.which("bwrap")
            cls.node = shutil.which("node")
            if cls.bwrap is None or cls.node is None:
                raise AssertionError("hosted acceptance requires Bubblewrap and the configured Node runtime")

            downloaded_fixture = SCRIPT_DIR / "postgres-sandbox-drizzle"
            checkout_fixture = SCRIPT_DIR / "fixtures/postgres-sandbox-drizzle"
            cls.drizzle_dir = (
                downloaded_fixture if downloaded_fixture.is_dir() else checkout_fixture
            )
            query = cls.drizzle_dir / "query.mjs"
            package_json = cls.drizzle_dir / "package.json"
            package_lock = cls.drizzle_dir / "package-lock.json"
            if (
                not query.is_file()
                or not (cls.drizzle_dir / "schema.ts").is_file()
                or not (cls.drizzle_dir / "migration.sql").is_file()
                or not package_json.is_file()
                or not package_lock.is_file()
            ):
                raise AssertionError("pinned Drizzle acceptance fixture is incomplete")
            if not (
                (cls.drizzle_dir / "node_modules/drizzle-orm").is_dir()
                and (cls.drizzle_dir / "node_modules/drizzle-kit").is_dir()
            ):
                with cls.npm_global_config.open("x", encoding="utf-8"):
                    pass
                cls.npm_global_config.chmod(0o600)
                _checked_output(
                    [
                        "npm",
                        "ci",
                        "--prefix",
                        str(cls.drizzle_dir),
                        "--registry=https://registry.npmjs.org",
                        "--userconfig=/dev/null",
                    f"--globalconfig={cls.npm_global_config}",
                        "--ignore-scripts",
                        "--no-audit",
                        "--no-fund",
                    ],
                    timeout=120,
                )
            cls.query = query
            cls.node_root = Path(cls.node).resolve().parent.parent

            if cls.secondary_state_file.exists() or cls.secondary_state_file.is_symlink():
                raise AssertionError("secondary Postgres state path already exists")
            cls.secondary = ci_postgres_sandbox.provision_profile(
                cls.primary["profileName"],
                runner_temp=cls.runner_temp,
                state_file=cls.secondary_state_file,
            )
            cls.secondary = ci_postgres_sandbox.prepare_profile_for_candidate(
                cls.secondary_state_file,
                runner_temp=cls.runner_temp,
            )
            if cls.schema_state_file.exists() or cls.schema_state_file.is_symlink():
                raise AssertionError("schema-validation Postgres state path already exists")
            cls.schema_profile = ci_postgres_sandbox.provision_profile(
                ci_postgres_sandbox.SCHEMA_VALIDATION_PROFILE_NAME,
                runner_temp=cls.runner_temp,
                state_file=cls.schema_state_file,
            )
            cls.schema_profile = ci_postgres_sandbox.prepare_profile_for_candidate(
                cls.schema_state_file,
                runner_temp=cls.runner_temp,
            )
        except BaseException:
            ci_postgres_sandbox.cleanup_profile(
                cls.schema_state_file,
                runner_temp=cls.runner_temp,
            )
            ci_postgres_sandbox.cleanup_profile(
                cls.secondary_state_file,
                runner_temp=cls.runner_temp,
            )
            ci_postgres_sandbox.cleanup_profile(
                cls.primary_state_file,
                runner_temp=cls.runner_temp,
            )
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        schema_profile = getattr(cls, "schema_profile", None)
        ci_postgres_sandbox.cleanup_profile(
            cls.schema_state_file,
            runner_temp=cls.runner_temp,
        )
        if schema_profile is not None:
            inspect = subprocess.run(
                ["docker", "inspect", schema_profile["containerName"]],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            )
            if inspect.returncode == 0:
                raise AssertionError("schema-validation Postgres container survived cleanup")
        if cls.schema_state_file.exists():
            raise AssertionError("schema-validation Postgres cleanup receipt survived cleanup")
        secondary = getattr(cls, "secondary", None)
        ci_postgres_sandbox.cleanup_profile(
            cls.secondary_state_file,
            runner_temp=cls.runner_temp,
        )
        if secondary is not None:
            inspect = subprocess.run(
                ["docker", "inspect", secondary["containerName"]],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            )
            if inspect.returncode == 0:
                raise AssertionError("secondary Postgres container survived cleanup")
        if cls.secondary_state_file.exists():
            raise AssertionError("secondary Postgres cleanup receipt survived cleanup")
        primary = getattr(cls, "primary", None)
        ci_postgres_sandbox.cleanup_profile(
            cls.primary_state_file,
            runner_temp=cls.runner_temp,
        )
        cls.npm_global_config.unlink(missing_ok=True)
        if primary is not None:
            inspect = subprocess.run(
                ["docker", "inspect", primary["containerName"]],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            )
            if inspect.returncode == 0:
                raise AssertionError("primary Postgres container survived cleanup")
        if cls.primary_state_file.exists():
            raise AssertionError("primary Postgres cleanup receipt survived cleanup")
        if cls.candidate_state_file is not None:
            if not cls.candidate_state_file.is_file() or cls.candidate_state_file.is_symlink():
                raise AssertionError("production Postgres candidate receipt disappeared or was replaced")
            if cls.candidate_state_file.read_bytes() != cls.candidate_state_snapshot:
                raise AssertionError("hosted acceptance changed the production candidate receipt")
            candidate_state = ci_postgres_sandbox.read_profile_state(
                cls.candidate_state_file,
                cls.runner_temp,
            )
            candidate_bundle = Path(candidate_state["clientBundleDirectory"])
            if any(candidate_bundle.iterdir()):
                raise AssertionError("hosted acceptance staged the production candidate profile")

    def _bubblewrap_command(
        self,
        state: dict[str, object],
        command: list[str],
        *,
        extra_mounts: tuple[str, ...] = (),
    ) -> list[str]:
        state_file = {
            self.primary["containerName"]: self.primary_state_file,
            self.secondary["containerName"]: self.secondary_state_file,
            self.schema_profile["containerName"]: self.schema_state_file,
        }[state["containerName"]]
        ci_postgres_sandbox.verify_profile_runtime(
            state_file,
            runner_temp=self.runner_temp,
        )
        environment = ci_postgres_sandbox.profile_environment(
            "/opt/verjson/node/bin:/opt/verjson/postgres-client/bin:/usr/bin:/bin",
            profile_name=state["profileName"],
        )
        arguments = [
            self.bwrap,
            "--unshare-user",
            "--unshare-pid",
            "--as-pid-1",
            "--unshare-net",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
            "--disable-userns",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--tmpfs",
            "/",
            "--tmpfs",
            "/tmp",
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
            "--ro-bind",
            "/etc",
            "/etc",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/dev/shm",
            *ci_postgres_sandbox.profile_mount_args(state),
            "--dir",
            "/opt/verjson/node",
            "--ro-bind",
            str(self.node_root),
            "/opt/verjson/node",
            "--dir",
            "/opt/verjson/drizzle",
            "--ro-bind",
            str(self.drizzle_dir),
            "/opt/verjson/drizzle",
            *extra_mounts,
        ]
        for name, value in environment.items():
            arguments.extend(("--setenv", name, value))
        arguments.extend(
            (
                "--setenv",
                "HOME",
                "/tmp",
                "--setenv",
                "PATH",
                "/opt/verjson/node/bin:/opt/verjson/postgres-client/bin:/usr/bin:/bin",
                "--",
                *command,
            )
        )
        return arguments

    def _candidate(
        self,
        state: dict[str, object],
        command: list[str],
        *,
        success: bool = True,
        timeout: float = 30,
        extra_mounts: tuple[str, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            self._bubblewrap_command(state, command, extra_mounts=extra_mounts),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.runner_temp)},
        )
        if success and result.returncode != 0:
            self.fail(f"sandbox command failed: {result.stderr[-2000:]}")
        if not success and result.returncode == 0:
            self.fail("sandbox command unexpectedly succeeded")
        return result

    def _assert_network_boundary(self, state: dict[str, object]) -> None:
        scope = _sandbox_socket_scope(self._bubblewrap_command(state, []))
        _assert_no_host_distribution_tree_listeners(scope.host_trees)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(2)
            loopback_port = listener.getsockname()[1]

            with socket.create_connection(("127.0.0.1", loopback_port), timeout=2) as client:
                accepted, _ = listener.accept()
                with accepted:
                    accepted.settimeout(2)
                    client.sendall(b"host-positive-control")
                    self.assertEqual(accepted.recv(64), b"host-positive-control")

            network_probe = r"""
import json
import os
import socket
from pathlib import Path

observed = []
def observe(label, operation):
    try:
        operation()
    except OSError as error:
        observed.append({"label": label, "errno": error.errno, "message": error.strerror})
    else:
        observed.append({"label": label, "errno": None, "message": "operation succeeded"})

def connect(destination):
    with socket.create_connection(destination, timeout=2):
        pass

observe(
    "host loopback",
    lambda: connect(("127.0.0.1", __LOOPBACK_PORT__)),
)
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as dns:
    observe("DNS egress", lambda: dns.sendto(b"verjson-probe", ("8.8.8.8", 53)))
for label, destination in (
    ("TCP egress", ("1.1.1.1", 443)),
    ("metadata access", ("169.254.169.254", 80)),
):
    observe(label, lambda destination=destination: connect(destination))

def must_reject(label, operation):
    try:
        operation()
    except OSError:
        return
    raise SystemExit(f"{label} unexpectedly succeeded")

socket_path = Path("/run/verjson-postgres/.s.PGSQL.5432")
for label, mutation in (
    ("socket permission mutation", lambda: os.chmod(socket_path, 0o600)),
    ("socket replacement", lambda: socket_path.unlink()),
):
    must_reject(label, mutation)
must_reject(
    "socket directory mutation",
    lambda: (socket_path.parent / "candidate-write").write_text("forbidden"),
)

__DAEMON_ISOLATION_PROBE__
print(json.dumps(observed))
""".replace("__LOOPBACK_PORT__", str(loopback_port)).replace(
                "__DAEMON_ISOLATION_PROBE__", _daemon_isolation_probe_source(scope.unwalked)
            )
            network = self._candidate(
                state,
                ["/usr/bin/python3", "-I", "-c", network_probe],
            )
            observations = json.loads(network.stdout)
            expected_labels = [
                "host loopback",
                "DNS egress",
                "TCP egress",
                "metadata access",
            ]
            self.assertEqual([item["label"] for item in observations], expected_labels)
            for observation in observations:
                allowed_numbers = (
                    LOOPBACK_NETWORK_ERRNOS
                    if observation["label"] == "host loopback"
                    else OUTBOUND_NETWORK_ERRNOS
                )
                _assert_network_namespace_error(
                    observation["label"],
                    observation["errno"],
                    observation["message"],
                    allowed_numbers,
                )

        server_egress = subprocess.run(
            [
                "docker",
                "exec",
                str(state["containerId"]),
                "/usr/lib/postgresql/17/bin/psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "--username=postgres",
                "--dbname=postgres",
                "--command",
                "COPY (SELECT 1) TO PROGRAM 'bash -c ''echo > /dev/tcp/1.1.1.1/443''';",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
        server_output = server_egress.stdout + "\n" + server_egress.stderr
        self.assertNotEqual(server_egress.returncode, 0, server_output)
        container_network = subprocess.run(
            [
                "docker",
                "exec",
                str(state["containerId"]),
                "bash",
                "-c",
                "echo > /dev/tcp/1.1.1.1/443",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
        container_output = container_network.stdout + "\n" + container_network.stderr
        self.assertNotEqual(container_network.returncode, 0, container_output)
        network_messages = [
            os.strerror(number) for number in OUTBOUND_NETWORK_ERRNOS
        ]
        self.assertTrue(
            any(message in container_output for message in network_messages),
            container_output,
        )

    def _assert_supervisor_stops_candidate(self, cause: str) -> None:
        self.assertIn(cause, {"timeout", "host-cancellation"})
        candidate_started_at: float | None = None
        host_signals = ci_build_artifacts.HostSignalQueue()

        def test_hook(event: str) -> None:
            nonlocal candidate_started_at
            if event != "candidate-started":
                return
            candidate_started_at = time.monotonic()
            if cause == "host-cancellation":
                host_signals.capture(signal.SIGTERM, None)

        def timeout_signal() -> int | None:
            if (
                cause == "timeout"
                and candidate_started_at is not None
                and time.monotonic() - candidate_started_at >= 0.25
            ):
                return signal.SIGTERM
            return None

        supervisor_signals = (
            {"host_signals": host_signals}
            if cause == "host-cancellation"
            else {"cancellation_signal": timeout_signal}
        )
        with self.assertRaises(ci_build_artifacts.ScriptPlanInterrupted) as interrupted:
            ci_build_artifacts.run_supervised_bubblewrap(
                self._bubblewrap_command(
                    self.secondary,
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-c",
                        ci_build_artifacts.SANDBOX_SUPERVISOR_ENTRYPOINT,
                        "/usr/bin/sleep",
                        "30",
                    ],
                ),
                environment={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": str(self.runner_temp),
                },
                pass_fds=(),
                test_hook=test_hook,
                **supervisor_signals,
            )
        self.assertTrue(interrupted.exception.candidate_started)
        self.assertEqual(interrupted.exception.signum, signal.SIGTERM)
        self.assertEqual(interrupted.exception.cleanup_status, "verified")

    def _psql(
        self,
        state: dict[str, object],
        sql: str,
        *,
        database: str | None = None,
        user: str = "verjson_ci",
        success: bool = True,
    ) -> str:
        if database is None:
            database = (
                "app_test"
                if state["profileName"] == ci_postgres_sandbox.PROFILE_NAME
                else "schema_migrations"
            )
        result = self._candidate(
            state,
            [
                "/opt/verjson/postgres-client/bin/psql",
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "--username=" + user,
                "--dbname=" + database,
                "--command",
                sql,
            ],
            success=success,
        )
        return result.stdout.strip()

    def _push_drizzle_schema(
        self, state: dict[str, object], *, database: str | None = None
    ) -> None:
        profile_environment = ci_postgres_sandbox.profile_environment(
            profile_name=state["profileName"]
        )
        if state["profileName"] == ci_postgres_sandbox.PROFILE_NAME:
            url = profile_environment["DATABASE_URL"]
        else:
            database = database or "schema_snapshot"
            if database not in ci_postgres_sandbox.SCHEMA_VALIDATION_DATABASES:
                raise AssertionError("Drizzle target database is outside the schema profile")
            url_key = ci_postgres_sandbox.SCHEMA_VALIDATION_DATABASES[database]
            url = profile_environment[url_key]
        config_path = self.runner_temp / f"drizzle-config-{state['containerName']}-{database or 'app_test'}.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema": "/opt/verjson/drizzle/schema.ts",
                    "out": "/tmp/ci-postgres-sandbox-drizzle-out",
                    "dialect": "postgresql",
                    "dbCredentials": {
                        "url": url
                    },
                    "strict": False,
                    "verbose": False,
                }
            ),
            encoding="utf-8",
        )
        try:
            self._candidate(
                state,
                [
                    "/opt/verjson/node/bin/node",
                    "/opt/verjson/drizzle/node_modules/drizzle-kit/bin.cjs",
                    "push",
                    "--config=/tmp/ci-postgres-sandbox-drizzle.json",
                    "--force",
                ],
                timeout=60,
                extra_mounts=(
                    "--ro-bind",
                    str(config_path),
                    "/tmp/ci-postgres-sandbox-drizzle.json",
                ),
            )
        finally:
            config_path.unlink(missing_ok=True)

    def _apply_routines(
        self, state: dict[str, object], *, database: str | None = None
    ) -> None:
        migration = (self.drizzle_dir / "migration.sql").read_text(encoding="utf-8")
        routines = re.findall(
            r"(?ms)^-- BEGIN schema-routines: ([^\s]+)\s*\n(.*?)^-- END schema-routines: \1\s*$",
            migration,
        )
        if not routines:
            self.fail("schema migration fixture has no marked routine block")
        routine_sql = "\n\n".join(body.strip() for _name, body in routines)
        routines_path = self.runner_temp / f"postgres-routines-{state['containerName']}.sql"
        routines_path.write_text(routine_sql + "\n", encoding="utf-8")
        try:
            command = [
                    "/opt/verjson/postgres-client/bin/psql",
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                ]
            if state["profileName"] == ci_postgres_sandbox.SCHEMA_VALIDATION_PROFILE_NAME:
                command.extend(("--username=verjson_ci", "--dbname=" + (database or "schema_snapshot")))
            command.extend(("--file", "/tmp/ci-postgres-sandbox-routines.sql"))
            self._candidate(
                state,
                command,
                extra_mounts=(
                    "--ro-bind",
                    str(routines_path),
                    "/tmp/ci-postgres-sandbox-routines.sql",
                ),
            )
        finally:
            routines_path.unlink(missing_ok=True)

    def _apply_migration(
        self, state: dict[str, object], *, database: str | None = None
    ) -> None:
        command = [
            "/opt/verjson/postgres-client/bin/psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
        ]
        if state["profileName"] == ci_postgres_sandbox.SCHEMA_VALIDATION_PROFILE_NAME:
            command.extend(("--username=verjson_ci", "--dbname=" + (database or "schema_migrations")))
        command.extend(("--file", "/opt/verjson/drizzle/migration.sql"))
        self._candidate(
            state,
            command,
        )

    def _normalized_schema_dump(
        self, state: dict[str, object], *, database: str | None = None
    ) -> str:
        if database is None:
            database = (
                "app_test"
                if state["profileName"] == ci_postgres_sandbox.PROFILE_NAME
                else "schema_migrations"
            )
        result = self._candidate(
            state,
            [
                "/opt/verjson/postgres-client/bin/pg_dump",
                "--schema-only",
                "--no-owner",
                "--no-privileges",
                "--no-comments",
                "--schema=public",
                "--dbname=" + database,
            ],
        )
        lines = [
            line
            for line in result.stdout.splitlines()
            if not re.match(r"^\\(un)?restrict\s", line)
            and not re.match(r"^-- Dumped (from database|by pg_dump) version ", line)
            and line != "CREATE SCHEMA public;"
        ]
        return "\n".join(lines) + "\n"

    def test_real_bubblewrap_postgres_boundary_and_failure_receipts(self) -> None:
        self._push_drizzle_schema(self.primary)
        self._apply_routines(self.primary)
        self._apply_migration(self.secondary)
        reference_dump = self._normalized_schema_dump(self.primary)
        migration_dump = self._normalized_schema_dump(self.secondary)
        self.assertEqual(reference_dump, migration_dump)

        for state in (self.primary, self.secondary):
            versions = self._psql(
                state,
                "SELECT current_user || '|' || current_setting('server_version') || '|' || (inet_server_addr() IS NULL)::text;",
            )
            version_fields = versions.split("|")
            self.assertEqual(version_fields[0], "verjson_ci")
            self.assertTrue(version_fields[1].startswith("17.11"), versions)
            self.assertEqual(version_fields[2], "true")
            for role, expected_error in (
                ("postgres", "Peer authentication failed"),
                ("ci_unreviewed", "pg_hba.conf rejects connection"),
            ):
                with self.subTest(role=role):
                    denied = self._candidate(
                        state,
                        [
                            "/opt/verjson/postgres-client/bin/psql",
                            "-X",
                            "--username=" + role,
                            "--dbname=app_test",
                            "--command=SELECT 1",
                        ],
                        success=False,
                    )
                    self.assertIn(expected_error, denied.stderr)
            dump_version = self._candidate(
                state,
                ["/opt/verjson/postgres-client/bin/pg_dump", "--version"],
            )
            self.assertIn("17.11", dump_version.stdout)

            privileges = self._psql(
                state,
                "SELECT rolsuper::text || '|' || rolcreatedb::text || '|' || rolcreaterole::text || '|' || has_schema_privilege(current_user, 'public', 'CREATE')::text FROM pg_roles WHERE rolname = current_user;",
            )
            self.assertEqual(privileges, "false|false|false|true")
            for forbidden_sql in (
                "CREATE ROLE d2_forbidden;",
                "CREATE DATABASE d2_forbidden;",
                "CREATE EXTENSION dblink;",
                "COPY (SELECT 1) TO PROGRAM 'true';",
            ):
                with self.subTest(statement=forbidden_sql):
                    self._psql(state, forbidden_sql, success=False)

        first = self._candidate(
            self.primary,
            ["/opt/verjson/node/bin/node", "/opt/verjson/drizzle/query.mjs"],
        )
        second = self._candidate(
            self.secondary,
            ["/opt/verjson/node/bin/node", "/opt/verjson/drizzle/query.mjs"],
        )
        self.assertIn('"fresh_database":true', first.stdout)
        self.assertIn('"fresh_database":true', second.stdout)

        self._assert_network_boundary(self.primary)

        bad_profile = {**self.profile, "digest": "sha256:" + "0" * 64}
        with tempfile.TemporaryDirectory(dir=self.runner_temp) as directory:
            invalid_config = Path(directory) / "profiles.json"
            document = json.loads(ci_postgres_sandbox.PROFILE_CONFIG.read_text(encoding="utf-8"))
            document["profiles"][ci_postgres_sandbox.PROFILE_NAME]["digest"] = bad_profile["digest"]
            invalid_config.write_text(json.dumps(document), encoding="utf-8")
            with patch.object(ci_postgres_sandbox, "PROFILE_CONFIG", invalid_config):
                with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                    ci_postgres_sandbox.resolve_profile(ci_postgres_sandbox.PROFILE_NAME)
            with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                ci_postgres_sandbox.resolve_profile("unreviewed")

        bad_state_file = self.runner_temp / "ci-postgres-sandbox-invalid-runtime-state.json"
        bad_state = dict(self.primary)
        bad_state["socketMode"] = int(bad_state["socketMode"]) ^ 0o100
        ci_postgres_sandbox._write_state(bad_state_file, bad_state, self.runner_temp)
        entries = [{"directory": ".", "script": "boundary", "unsetEnv": [], "requiresServices": False}]
        identity = {
            "repository": "Verjson/hosted-acceptance",
            "event": "workflow_dispatch",
            "ref": "refs/heads/main",
            "commit": "a" * 40,
            "workflowSha": "b" * 40,
            "runId": os.environ["GITHUB_RUN_ID"],
            "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT", "1"),
            "jobId": "postgres-sandbox-acceptance",
            "levelId": "postgres-17.11",
        }

        def fail_on_invalid_boundary(_ordinal: int, _entry: dict[str, object]):
            ci_postgres_sandbox.verify_profile_runtime(
                bad_state_file,
                runner_temp=self.runner_temp,
            )
            return ci_build_artifacts.ScriptEntryExecutionResult(0)

        with self.assertRaises(ci_build_artifacts.ScriptPlanExecutionError) as failure:
            ci_build_artifacts.run_script_plan_with_receipts(
                entries,
                identity,
                True,
                self.runner_temp,
                fail_on_invalid_boundary,
            )
        receipt_directory = failure.exception.receipt_directory
        plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
        receipt = json.loads((receipt_directory / "entry-000000.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["state"], "infrastructure-failure")
        self.assertEqual(receipt["state"], "infrastructure-failure")
        bad_state_file.unlink(missing_ok=True)

    def test_schema_validation_profile_exercises_three_isolated_socket_databases(self) -> None:
        state = self.schema_profile
        profile_name = ci_postgres_sandbox.SCHEMA_VALIDATION_PROFILE_NAME

        self.assertEqual(state["profileName"], profile_name)
        startup = self._candidate(
            state,
            [
                "/usr/bin/env",
                "PSQLRC=/dev/null",
                "/opt/verjson/postgres-client/bin/psql",
                "--no-align",
                "--tuples-only",
                "--username=verjson_ci",
                "--dbname=schema_snapshot",
                "--command=SELECT current_user || '|' || current_setting('application_name');",
            ],
        )
        self.assertEqual(startup.stdout.strip(), "verjson_ci|psql")
        self.assertNotIn("invalid binary", startup.stderr.lower())
        self.assertNotIn("could not find own program executable", startup.stderr.lower())
        self.assertEqual(
            ci_postgres_sandbox.local_auth_policy(profile_name).splitlines(),
            [
                "local all postgres peer",
                "local schema_snapshot verjson_ci trust",
                "local schema_migrations verjson_ci trust",
                "local schema_reference verjson_ci trust",
                "local all all reject",
                "host all all 0.0.0.0/0 reject",
                "host all all ::/0 reject",
            ],
        )

        self._push_drizzle_schema(state, database="schema_snapshot")
        self._apply_routines(state, database="schema_snapshot")
        self._push_drizzle_schema(state, database="schema_reference")
        self._apply_routines(state, database="schema_reference")
        self._apply_migration(state, database="schema_migrations")

        dumps = {
            database: self._normalized_schema_dump(state, database=database)
            for database in ci_postgres_sandbox.SCHEMA_VALIDATION_DATABASES
        }
        self.assertEqual(dumps["schema_snapshot"], dumps["schema_migrations"])
        self.assertEqual(dumps["schema_snapshot"], dumps["schema_reference"])

        self._psql(
            state,
            "ALTER TABLE auth.users ADD COLUMN schema_acceptance_probe integer; "
            "CREATE TABLE storage.schema_acceptance_probe (id integer PRIMARY KEY); "
            "CREATE TABLE public.schema_policy_marker (value integer); "
            "ALTER TABLE public.schema_policy_marker ENABLE ROW LEVEL SECURITY; "
            "CREATE POLICY schema_validation_policy ON public.schema_policy_marker "
            "USING (value IS NOT NULL);",
            database="schema_migrations",
        )
        migration_ddl = self._psql(
            state,
            "SELECT (SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema='auth' AND table_name='users' "
            "AND column_name='schema_acceptance_probe')::text || '|' || "
            "(to_regclass('storage.schema_acceptance_probe') IS NOT NULL)::text || '|' || "
            "(SELECT relrowsecurity FROM pg_class "
            "WHERE oid='public.schema_policy_marker'::regclass)::text || '|' || "
            "(SELECT count(*) FROM pg_policies WHERE schemaname='public' "
            "AND tablename='schema_policy_marker' "
            "AND policyname='schema_validation_policy')::text;",
            database="schema_migrations",
        )
        self.assertEqual(migration_ddl, "1|true|true|1")

        for database in ci_postgres_sandbox.SCHEMA_VALIDATION_DATABASES:
            with self.subTest(database=database):
                access = self._psql(
                    state,
                    "SELECT current_user || '|' || current_database() || '|' || "
                    "current_setting('server_version') || '|' || "
                    "(inet_server_addr() IS NULL)::text || '|' || "
                    "has_database_privilege(current_user, current_database(), 'CONNECT')::text || '|' || "
                    "has_database_privilege(current_user, current_database(), 'TEMP')::text || '|' || "
                    "has_database_privilege(current_user, current_database(), 'CREATE')::text;",
                    database=database,
                )
                expected_temp = "true" if database == "schema_migrations" else "false"
                fields = access.split("|")
                self.assertEqual(fields[0:2], ["verjson_ci", database])
                self.assertTrue(fields[2].startswith("17.11"), access)
                self.assertEqual(fields[3:], ["true", "true", expected_temp, "false"])
                if database != "schema_migrations":
                    self._psql(
                        state,
                        "CREATE TEMP TABLE forbidden_temp(value integer);",
                        database=database,
                        success=False,
                    )

        prelude = self._psql(
            state,
            "SELECT current_user || '|' || "
            "(SELECT NOT rolcanlogin FROM pg_roles WHERE rolname='authenticated')::text || '|' || "
            "(SELECT count(*) FROM pg_namespace WHERE nspname IN ('auth', 'storage'))::text || '|' || "
            "(SELECT count(*) FROM pg_class WHERE relname IN ('users', 'identities', 'objects'))::text || '|' || "
            "(to_regprocedure('auth.uid()') IS NOT NULL)::text || '|' || "
            "(to_regprocedure('storage.foldername(text)') IS NOT NULL)::text;",
            database="schema_migrations",
        )
        self.assertEqual(prelude, "verjson_ci|true|2|3|true|true")
        for database in ("schema_snapshot", "schema_reference"):
            self.assertEqual(
                self._psql(
                    state,
                    "SELECT to_regnamespace('auth') IS NULL AND to_regnamespace('storage') IS NULL;",
                    database=database,
                ),
                "t",
            )

        self._psql(
            state,
            "CREATE TABLE public.profile_isolation_marker(value integer);",
            database="schema_snapshot",
        )
        for database in ("schema_migrations", "schema_reference"):
            self.assertEqual(
                self._psql(
                    state,
                    "SELECT to_regclass('public.profile_isolation_marker') IS NULL;",
                    database=database,
                ),
                "t",
            )

        for user, expected_error in (
            ("postgres", "Peer authentication failed"),
            ("ci_unreviewed", "pg_hba.conf rejects connection"),
        ):
            denied = self._candidate(
                state,
                [
                    "/opt/verjson/postgres-client/bin/psql",
                    "-X",
                    "--username=" + user,
                    "--dbname=schema_migrations",
                    "--command=SELECT 1",
                ],
                success=False,
            )
            self.assertIn(expected_error, denied.stderr)

        self._assert_network_boundary(state)

    def test_workspace_client_shims_cannot_replace_profile_client_tools(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.runner_temp) as directory:
            shim_directory = Path(directory)
            for name in ("psql", "pg_dump"):
                shim = shim_directory / name
                shim.write_text(
                    "#!/bin/sh\necho untrusted-workspace-shim >&2\nexit 93\n",
                    encoding="utf-8",
                )
                shim.chmod(0o555)

            probe = r"""
import os
import shutil
import subprocess

os.environ["PATH"] = "/tmp/untrusted-bin:/opt/verjson/postgres-client/bin"
for tool in ("psql", "pg_dump"):
    resolved = shutil.which(tool)
    if resolved != f"/tmp/untrusted-bin/{tool}":
        raise SystemExit(f"test did not put untrusted {tool} first on PATH: {resolved}")
    result = subprocess.run(
        [f"/opt/verjson/postgres-client/bin/{tool}", "--version"],
        capture_output=True,
        check=True,
        text=True,
    )
    if "PostgreSQL) 17.11" not in result.stdout:
        raise SystemExit(f"trusted {tool} version mismatch: {result.stdout}")
    if "untrusted-workspace-shim" in result.stderr:
        raise SystemExit(f"untrusted {tool} shim executed")
    print(f"trusted {tool}: {result.stdout.strip()}")
"""
            result = self._candidate(
                self.schema_profile,
                ["/usr/bin/python3", "-I", "-c", probe],
                extra_mounts=("--ro-bind", str(shim_directory), "/tmp/untrusted-bin"),
            )
            self.assertIn("trusted psql: psql (PostgreSQL) 17.11", result.stdout)
            self.assertIn("trusted pg_dump: pg_dump (PostgreSQL) 17.11", result.stdout)

    def test_production_supervisor_cleans_timeout_and_host_cancellation(self) -> None:
        self._assert_supervisor_stops_candidate("timeout")
        self._assert_supervisor_stops_candidate("host-cancellation")


if __name__ == "__main__":
    unittest.main()
