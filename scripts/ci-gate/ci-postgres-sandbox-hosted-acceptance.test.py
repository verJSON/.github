#!/usr/bin/env python3
"""Exercise the reviewed Postgres boundary on an isolated GitHub-hosted runner."""

from __future__ import annotations

import json
import errno
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
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
import subprocess
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

docker_socket = Path("/var/run/docker.sock")
if docker_socket.exists():
    try:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(docker_socket))
    except OSError:
        pass
    else:
        raise SystemExit("Docker socket reachable")
docker = subprocess.run(["docker", "info"], capture_output=True, timeout=3, check=False)
if docker.returncode == 0:
    raise SystemExit("Docker daemon reachable")
print(json.dumps(observed))
""".replace("__LOOPBACK_PORT__", str(loopback_port))
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
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("SKIP: hosted Postgres acceptance runs in GitHub Actions")
        raise SystemExit(0)
    unittest.main()
