#!/usr/bin/env python3
"""Contracts for the explicit, digest-pinned Postgres sandbox profile."""

from __future__ import annotations

import sys
import socket
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ci_postgres_sandbox
import ci_build_artifacts


class PostgresSandboxProfileTests(unittest.TestCase):
    def test_cleanup_steps_attempt_every_receipt_after_an_earlier_failure(self) -> None:
        root = Path(__file__).resolve().parents[2]
        workflow_paths = (
            root / ".github/workflows/node-ci.yml",
            root / ".github/workflows/node-ci-protected.yml",
            root / ".github/workflows/actions-ci.yml",
        )
        expected_receipts = (
            "ci-postgres-sandbox-state.json",
            "ci-postgres-sandbox-acceptance-state.json",
            "ci-postgres-sandbox-fresh-state.json",
            "ci-postgres-schema-validation-state.json",
        )

        for workflow_path in workflow_paths:
            with self.subTest(workflow=workflow_path.name), tempfile.TemporaryDirectory() as temporary_root:
                runner_temp = Path(temporary_root)
                (runner_temp / "ci_postgres_sandbox.py").touch()
                for receipt in expected_receipts:
                    (runner_temp / receipt).touch()

                workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
                if workflow_path.name == "actions-ci.yml":
                    steps = workflow["jobs"]["hosted-compatibility-tests"]["steps"]
                    cleanup_step = next(step for step in steps if step.get("name") == "Clean up Postgres sandbox containers")
                    receipts = expected_receipts[1:]
                else:
                    steps = workflow["jobs"]["build-test"]["steps"]
                    cleanup_step = next(step for step in steps if step.get("name") == "Stop Postgres sandbox profile")
                    receipts = expected_receipts

                bin_dir = runner_temp / "bin"
                bin_dir.mkdir()
                cleanup_log = runner_temp / "cleanup.log"
                cleanup_marker = runner_temp / "cleanup.failed-once"
                python_stub = bin_dir / "python3"
                python_stub.write_text(
                    "#!/bin/sh\n"
                    'printf "%s\\n" "$*" >> "$CLEANUP_LOG"\n'
                    'if [ ! -e "$CLEANUP_MARKER" ]; then touch "$CLEANUP_MARKER"; exit 17; fi\n'
                    "exit 0\n",
                    encoding="utf-8",
                )
                python_stub.chmod(0o755)
                environment = os.environ.copy()
                environment.update(
                    {
                        "PATH": f"{bin_dir}:/usr/bin:/bin",
                        "RUNNER_TEMP": str(runner_temp),
                        "POSTGRES_STATE_FILE": str(runner_temp / expected_receipts[0]),
                        "CLEANUP_LOG": str(cleanup_log),
                        "CLEANUP_MARKER": str(cleanup_marker),
                    }
                )
                result = subprocess.run(
                    ["/bin/bash", "-euo", "pipefail", "-c", cleanup_step["run"]],
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                calls = cleanup_log.read_text(encoding="utf-8").splitlines()
                for receipt in receipts:
                    self.assertEqual(
                        sum(receipt in call for call in calls),
                        1,
                        f"{workflow_path.name} did not attempt {receipt}: {calls}",
                    )

    def test_local_auth_policy_uses_peer_maintenance_and_rejects_other_roles(self) -> None:
        policy = ci_postgres_sandbox.local_auth_policy()

        self.assertEqual(
            policy.splitlines(),
            [
                "local all postgres peer",
                "local app_test verjson_ci trust",
                "local all all reject",
                "host all all 0.0.0.0/0 reject",
                "host all all ::/0 reject",
            ],
        )

    def test_schema_validation_auth_policy_allows_only_its_fixed_databases(self) -> None:
        policy = ci_postgres_sandbox.local_auth_policy(
            ci_postgres_sandbox.SCHEMA_VALIDATION_PROFILE_NAME
        )

        self.assertEqual(
            policy.splitlines(),
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
        with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
            ci_postgres_sandbox.local_auth_policy("unreviewed")

    def test_local_auth_policy_is_staged_read_only_for_postgres(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            socket_directory = Path(temporary_root) / "socket"
            socket_directory.mkdir()

            policy_path = ci_postgres_sandbox.write_local_auth_policy(socket_directory)

            self.assertEqual(policy_path.name, "pg_hba.conf")
            self.assertEqual(policy_path.read_text(encoding="utf-8"), ci_postgres_sandbox.local_auth_policy())
            self.assertEqual(policy_path.stat().st_mode & 0o777, 0o444)

    def test_hosted_acceptance_runs_after_compatibility_sandbox_provisioning(self) -> None:
        workflow_path = Path(__file__).resolve().parents[2] / ".github/workflows/actions-ci.yml"
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

        self.assertNotIn("postgres-sandbox-hosted-acceptance", workflow["jobs"])
        compatibility_job = workflow["jobs"]["hosted-compatibility-tests"]
        self.assertEqual(compatibility_job["runs-on"], "ubuntu-24.04")
        self.assertEqual(compatibility_job["timeout-minutes"], 25)
        steps = compatibility_job["steps"]
        provision_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Provision trusted compatibility sandbox"
        )
        acceptance_steps = [
            step
            for step in steps
            if "scripts/ci-gate/ci-postgres-sandbox-hosted-acceptance.test.py"
            in step.get("run", "")
        ]
        self.assertEqual(len(acceptance_steps), 1)
        acceptance_index = steps.index(acceptance_steps[0])
        self.assertGreater(acceptance_index, provision_index)
        self.assertEqual(
            acceptance_steps[0]["env"]["VERJSON_CI_POSTGRES_HOSTED_ACCEPTANCE"],
            "1",
        )
        cleanup_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Clean up Postgres sandbox containers"
        )
        self.assertGreater(cleanup_index, acceptance_index)
        self.assertEqual(steps[cleanup_index].get("if"), "always()")

        shell_test_job = workflow["jobs"]["shell-test-groups"]
        self.assertNotIn("hosted-compatibility-tests", shell_test_job["needs"])
        self.assertFalse(
            any(
                step.get("name") == "Require hosted compatibility acceptance"
                for step in shell_test_job["steps"]
            )
        )
        aggregate_job = workflow["jobs"]["shell-tests"]
        self.assertIn("hosted-compatibility-tests", aggregate_job["needs"])
        self.assertEqual(
            aggregate_job["steps"][0]["env"]["COMPATIBILITY_RESULT"],
            "${{ needs.hosted-compatibility-tests.result }}",
        )
        self.assertIn(
            'if [ "$COMPATIBILITY_RESULT" != "success" ]; then',
            aggregate_job["steps"][0]["run"],
        )
        self.assertEqual(shell_test_job["if"], compatibility_job["if"])

    def test_node_ci_acceptance_keeps_candidate_profile_pristine_for_the_next_step(self) -> None:
        root = Path(__file__).resolve().parents[2]
        workflow = yaml.safe_load((root / ".github/workflows/node-ci.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["build-test"]["steps"]
        acceptance_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Verify Postgres sandbox hosted acceptance boundary"
        )
        acceptance = steps[acceptance_index]
        self.assertEqual(
            acceptance["env"]["VERJSON_CI_POSTGRES_CANDIDATE_STATE"],
            "${{ runner.temp }}/ci-postgres-sandbox-state.json",
        )
        self.assertNotIn("VERJSON_CI_POSTGRES_SANDBOX_STATE", acceptance["env"])
        candidate_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Run exact credentialless consumer script plan"
        )
        self.assertLess(acceptance_index, candidate_index)
        acceptance_source = Path(__file__).with_name(
            "ci-postgres-sandbox-hosted-acceptance.test.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'cls.primary_state_file = cls.runner_temp / "ci-postgres-sandbox-acceptance-state.json"',
            acceptance_source,
        )

    def test_profile_is_opt_in_and_resolves_only_the_reviewed_postgres_image(self) -> None:
        self.assertIsNone(ci_postgres_sandbox.resolve_profile(None))
        self.assertIsNone(ci_postgres_sandbox.resolve_profile(""))

        profile = ci_postgres_sandbox.resolve_profile("postgres-17.11")

        self.assertEqual(
            profile["image"],
            "docker.io/library/postgres@sha256:3518071376d0b6156b02e1db2747a78511b5b9dc3763cc79c506ebe2dc2ee71c",
        )
        self.assertEqual(profile["platform"], "linux/amd64")
        self.assertEqual(profile["serverVersion"], "17.11")
        self.assertEqual(profile["clientVersion"], "17.11")
        self.assertEqual(profile["networkMode"], "none")
        self.assertEqual(profile["publishedPorts"], [])

    def test_schema_validation_profile_resolves_the_same_reviewed_image(self) -> None:
        profile = ci_postgres_sandbox.resolve_profile("postgres-schema-validation-17.11")

        self.assertEqual(profile["name"], "postgres-schema-validation-17.11")
        self.assertEqual(
            profile["image"],
            "docker.io/library/postgres@sha256:3518071376d0b6156b02e1db2747a78511b5b9dc3763cc79c506ebe2dc2ee71c",
        )
        self.assertEqual(profile["platform"], "linux/amd64")
        self.assertEqual(profile["serverVersion"], "17.11")
        self.assertEqual(profile["clientVersion"], "17.11")
        self.assertEqual(profile["networkMode"], "none")
        self.assertEqual(profile["publishedPorts"], [])

    def test_profile_config_digest_tampering_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            config = Path(temporary_root) / "profiles.json"
            document = json.loads(ci_postgres_sandbox.PROFILE_CONFIG.read_text(encoding="utf-8"))
            document["profiles"]["postgres-17.11"]["digest"] = "sha256:" + "0" * 64
            config.write_text(json.dumps(document), encoding="utf-8")
            with patch.object(ci_postgres_sandbox, "PROFILE_CONFIG", config):
                with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                    ci_postgres_sandbox.resolve_profile("postgres-17.11")

    def test_profile_config_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            config = Path(temporary_root) / "profiles.json"
            config.symlink_to(ci_postgres_sandbox.PROFILE_CONFIG)
            with patch.object(ci_postgres_sandbox, "PROFILE_CONFIG", config):
                with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                    ci_postgres_sandbox.resolve_profile("postgres-17.11")

    def test_profile_rejects_secretless_compatibility_requests(self) -> None:
        for compatibility_ranges in (
            '{"package":"@verjson/example","ranges":["1.2.3"],"script":"check"}',
            "malformed request",
        ):
            with self.subTest(compatibility_ranges=compatibility_ranges):
                with self.assertRaisesRegex(
                    ci_postgres_sandbox.PostgresSandboxError,
                    "secretless compatibility",
                ):
                    ci_postgres_sandbox.validate_profile_request(
                        "postgres-17.11",
                        compatibility_ranges=compatibility_ranges,
                    )

        for compatibility_ranges in ("", "{}"):
            with self.subTest(compatibility_ranges=compatibility_ranges):
                self.assertIsNotNone(
                    ci_postgres_sandbox.validate_profile_request(
                        "postgres-17.11",
                        compatibility_ranges=compatibility_ranges,
                    )
                )

    def test_profile_rejects_legacy_services_and_artifact_sharing_combinations(self) -> None:
        with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
            ci_postgres_sandbox.validate_profile_request("unreviewed")

        for options in (
            {"db_image": "postgres:16"},
            {"cache_image": "redis:7"},
            {"artifact_role": "producer"},
            {"artifact_role": "consumer"},
        ):
            with self.subTest(options=options):
                with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                    ci_postgres_sandbox.validate_profile_request("postgres-17.11", **options)

        self.assertIsNone(
            ci_postgres_sandbox.validate_profile_request(
                "", db_image="postgres:16", artifact_role="producer"
            )
        )
        with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
            ci_postgres_sandbox.validate_profile_request(
                "postgres-17.11", secretless_mode=False
            )

    def test_profile_allows_ordinary_level_artifact_role_without_sharing(self) -> None:
        workflow_path = Path(__file__).resolve().parents[2] / ".github/workflows/node-ci.yml"
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        workflow_call = workflow.get("on", workflow.get(True))["workflow_call"]
        default_artifact_role = workflow_call["inputs"]["build-artifact-role"]["default"]
        self.assertEqual("level", default_artifact_role)

        for artifact_role in ("", default_artifact_role):
            with self.subTest(artifact_role=artifact_role):
                self.assertIsNotNone(
                    ci_postgres_sandbox.validate_profile_request(
                        "postgres-17.11",
                        artifact_role=artifact_role,
                    )
                )

    def test_profile_forces_network_namespace_when_legacy_services_are_enabled(self) -> None:
        args = ci_build_artifacts.bubblewrap_network_args(
            shared_artifact=False,
            legacy_unshare=False,
            postgres_profile=True,
        )

        self.assertEqual(args.count("--unshare-net"), 1)

    def test_container_command_has_no_network_or_published_ports(self) -> None:
        profile = ci_postgres_sandbox.resolve_profile("postgres-17.11")
        with tempfile.TemporaryDirectory() as temporary_root:
            socket_dir = Path(temporary_root) / "socket"
            socket_dir.mkdir()
            args = ci_postgres_sandbox.build_container_args(
                profile,
                container_name="verjson-postgres-0123456789ab",
                socket_dir=socket_dir,
            )
        environment = ci_postgres_sandbox.container_environment("test-only-password")

        self.assertIn("--network", args)
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertIn("--platform", args)
        self.assertEqual(args[args.index("--platform") + 1], "linux/amd64")
        self.assertEqual(args[args.index("--user") + 1], "999:999")
        self.assertNotIn("--publish", args)
        self.assertNotIn("-p", args)
        self.assertIn(profile["image"], args)
        self.assertIn("listen_addresses=", args)
        self.assertIn(
            f"hba_file={ci_postgres_sandbox.POSTGRES_HBA_SANDBOX_PATH}",
            args,
        )
        self.assertFalse(any("test-only-password" in argument for argument in args))
        self.assertEqual(environment["POSTGRES_PASSWORD"], "test-only-password")

    def test_candidate_mounts_only_the_socket_and_client_bundle_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            socket_dir = root / "socket"
            socket_dir.mkdir()
            ci_postgres_sandbox.write_local_auth_policy(socket_dir)
            client_bundle = root / "client"
            (client_bundle / "bin").mkdir(parents=True)
            (client_bundle / "lib").mkdir()
            for name in ("psql", "pg_dump"):
                executable = client_bundle / "bin" / name
                executable.write_text("#!/bin/sh\n", encoding="utf-8")
                executable.chmod(0o555)
            server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_socket.bind(str(socket_dir / ".s.PGSQL.5432"))
            try:
                args = ci_postgres_sandbox.build_bubblewrap_profile_args(
                    socket_dir=socket_dir,
                    client_bundle=client_bundle,
                )
            finally:
                server_socket.close()

        read_only_sources = [
            args[index + 1]
            for index, value in enumerate(args)
            if value == "--ro-bind"
        ]
        self.assertEqual(read_only_sources, [str(socket_dir), str(client_bundle)])
        self.assertNotIn("--bind", args)
        self.assertEqual(args.count("--ro-bind"), 2)

    def test_candidate_mount_rejects_a_mutated_local_auth_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            socket_dir = root / "socket"
            socket_dir.mkdir()
            hba_path = ci_postgres_sandbox.write_local_auth_policy(socket_dir)
            hba_path.chmod(0o644)
            hba_path.write_text("local all all trust\n", encoding="utf-8")
            client_bundle = root / "client"
            (client_bundle / "bin").mkdir(parents=True)
            for name in ("psql", "pg_dump"):
                executable = client_bundle / "bin" / name
                executable.write_text("#!/bin/sh\n", encoding="utf-8")
                executable.chmod(0o555)
            server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_socket.bind(str(socket_dir / ci_postgres_sandbox.POSTGRES_SOCKET_NAME))
            self.addCleanup(server_socket.close)

            with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                ci_postgres_sandbox.build_bubblewrap_profile_args(
                    socket_dir=socket_dir,
                    client_bundle=client_bundle,
                )

    def test_candidate_connection_uses_unix_socket_and_restricted_database_role(self) -> None:
        environment = ci_postgres_sandbox.profile_environment()
        with_path = ci_postgres_sandbox.profile_environment("/usr/bin:/bin")
        bootstrap = ci_postgres_sandbox.database_bootstrap_sql()

        self.assertEqual(environment["PGHOST"], ci_postgres_sandbox.SOCKET_SANDBOX_PATH)
        self.assertEqual(environment["PGUSER"], "verjson_ci")
        self.assertEqual(
            with_path["PATH"],
            f"{ci_postgres_sandbox.CLIENT_SANDBOX_PATH}/bin:/usr/bin:/bin",
        )
        self.assertIn("@%2Frun%2Fverjson-postgres/", environment["DATABASE_URL"])
        self.assertIn("CREATE ROLE verjson_ci LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE", bootstrap)
        self.assertIn("GRANT CONNECT ON DATABASE app_test TO verjson_ci", bootstrap)
        self.assertIn("GRANT USAGE, CREATE ON SCHEMA public TO verjson_ci", bootstrap)
        self.assertNotIn("GRANT CREATE ON DATABASE", bootstrap)

    def test_candidate_environment_discards_inherited_libpq_connection_overrides(self) -> None:
        inherited = {
            "PATH": "/usr/bin:/bin",
            "PGHOSTADDR": "127.0.0.1",
            "PGSERVICE": "untrusted-service",
            "PGPASSWORD": "untrusted-password",
            "database_url": "postgresql://127.0.0.1/private",
            "SAFE_VALUE": "preserved",
        }

        candidate = ci_postgres_sandbox.candidate_environment(inherited, "/usr/bin:/bin")

        self.assertEqual(candidate["SAFE_VALUE"], "preserved")
        self.assertEqual(candidate["PGHOST"], ci_postgres_sandbox.SOCKET_SANDBOX_PATH)
        self.assertEqual(
            candidate["DATABASE_URL"],
            ci_postgres_sandbox.profile_environment()["DATABASE_URL"],
        )
        self.assertNotIn("PGHOSTADDR", candidate)
        self.assertNotIn("PGSERVICE", candidate)
        self.assertNotIn("PGPASSWORD", candidate)
        self.assertNotIn("database_url", candidate)

    def test_schema_validation_environment_uses_only_fixed_socket_urls(self) -> None:
        profile_name = ci_postgres_sandbox.SCHEMA_VALIDATION_PROFILE_NAME
        environment = ci_postgres_sandbox.profile_environment(
            "/usr/bin:/bin",
            profile_name=profile_name,
        )
        socket_host = "%2Frun%2Fverjson-postgres"
        expected = {
            "DATABASE_URL_SCHEMA_SNAPSHOT": (
                f"postgresql://verjson_ci@{socket_host}/schema_snapshot?port=5432"
            ),
            "DATABASE_URL_SCHEMA_MIGRATIONS": (
                f"postgresql://verjson_ci@{socket_host}/schema_migrations?port=5432"
            ),
            "DATABASE_URL_SCHEMA_REFERENCE": (
                f"postgresql://verjson_ci@{socket_host}/schema_reference?port=5432"
            ),
            "PGHOST": "/run/verjson-postgres",
            "PGPORT": "5432",
            "PGUSER": "verjson_ci",
            "PATH": "/opt/verjson/postgres-client/bin:/usr/bin:/bin",
        }
        self.assertEqual(environment, expected)

        inherited = {
            **expected,
            "DATABASE_URL": "postgresql://127.0.0.1/untrusted",
            "DATABASE_URL_UNREVIEWED": "postgresql://127.0.0.1/untrusted",
            "DATABASE_URL_SCHEMA_SNAPSHOT": "postgresql://127.0.0.1/untrusted",
            "PGHOSTADDR": "127.0.0.1",
            "PGSERVICE": "untrusted-service",
            "PGPASSWORD": "untrusted-password",
            "SAFE_VALUE": "preserved",
        }
        candidate = ci_postgres_sandbox.candidate_environment(
            inherited,
            "/usr/bin:/bin",
            profile_name=profile_name,
        )
        self.assertEqual(candidate, {**expected, "SAFE_VALUE": "preserved"})

    def test_schema_validation_bootstrap_limits_database_capabilities_and_seeds_prelude(self) -> None:
        bootstrap = ci_postgres_sandbox.database_bootstrap_sql(
            profile_name=ci_postgres_sandbox.SCHEMA_VALIDATION_PROFILE_NAME
        )

        for database in ("schema_snapshot", "schema_migrations", "schema_reference"):
            self.assertIn(f"CREATE DATABASE {database} OWNER postgres", bootstrap)
            self.assertIn(f"GRANT CONNECT ON DATABASE {database} TO verjson_ci", bootstrap)
            self.assertIn(f"GRANT USAGE, CREATE ON SCHEMA public TO verjson_ci", bootstrap)
        self.assertIn("GRANT TEMP ON DATABASE schema_migrations TO verjson_ci", bootstrap)
        self.assertNotIn("GRANT TEMP ON DATABASE schema_snapshot", bootstrap)
        self.assertNotIn("GRANT TEMP ON DATABASE schema_reference", bootstrap)
        self.assertIn("CREATE ROLE authenticated NOLOGIN", bootstrap)
        self.assertIn("CREATE SCHEMA auth AUTHORIZATION verjson_ci", bootstrap)
        self.assertIn("CREATE SCHEMA storage AUTHORIZATION verjson_ci", bootstrap)
        self.assertIn("CREATE TABLE IF NOT EXISTS auth.users", bootstrap)
        self.assertIn("CREATE TABLE IF NOT EXISTS auth.identities", bootstrap)
        self.assertIn("CREATE TABLE IF NOT EXISTS storage.objects", bootstrap)
        self.assertIn("CREATE OR REPLACE FUNCTION auth.uid()", bootstrap)
        self.assertIn("CREATE OR REPLACE FUNCTION storage.foldername(name text)", bootstrap)
        self.assertIn("NOCREATEDB NOCREATEROLE", bootstrap)
        self.assertNotIn("GRANT CREATE ON DATABASE", bootstrap)

    def test_schema_acceptance_fixture_pins_drizzle_kit_and_carries_a_migration(self) -> None:
        fixture = Path(__file__).parent / "fixtures/postgres-sandbox-drizzle"
        package = json.loads((fixture / "package.json").read_text(encoding="utf-8"))

        self.assertEqual(package["dependencies"]["drizzle-kit"], "0.31.11")
        self.assertTrue((fixture / "schema.ts").is_file())
        self.assertTrue((fixture / "migration.sql").is_file())

    def test_client_dependency_parser_rejects_missing_libraries(self) -> None:
        dependencies = ci_postgres_sandbox.parse_ldd_dependencies(
            "\tlibpq.so.5 => /lib/x86_64-linux-gnu/libpq.so.5 (0x1234)\n"
            "\t/lib64/ld-linux-x86-64.so.2 (0x5678)\n"
            "\tlinux-vdso.so.1 (0x9abc)\n"
        )

        self.assertEqual(
            dependencies,
            {
                "libpq.so.5": "/lib/x86_64-linux-gnu/libpq.so.5",
                "ld-linux-x86-64.so.2": "/lib64/ld-linux-x86-64.so.2",
            },
        )
        with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
            ci_postgres_sandbox.parse_ldd_dependencies("libpq.so.5 => not found\n")

    def test_client_bundle_manifest_detects_mutation_and_symlinks(self) -> None:
        profile = ci_postgres_sandbox.resolve_profile("postgres-17.11")
        with tempfile.TemporaryDirectory() as temporary_root:
            bundle = Path(temporary_root) / "client"
            (bundle / "bin").mkdir(parents=True)
            (bundle / "lib").mkdir()
            for name in ("psql", "pg_dump"):
                executable = bundle / "bin" / name
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o555)
                payload = bundle / "lib" / f"{name}.elf"
                payload.write_bytes(f"{name} payload".encode())
                payload.chmod(0o444)
            (bundle / "lib" / "ld-linux-x86-64.so.2").write_bytes(b"loader")
            (bundle / "lib" / "ld-linux-x86-64.so.2").chmod(0o555)
            ci_postgres_sandbox.write_client_manifest(bundle, profile)
            ci_postgres_sandbox.verify_client_bundle(bundle, profile)

            payload = bundle / "lib" / "psql.elf"
            payload.chmod(0o555)
            with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                ci_postgres_sandbox.verify_client_bundle(bundle, profile)
            payload.chmod(0o444)

            executable = bundle / "bin" / "psql"
            executable.chmod(0o755)
            executable.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                ci_postgres_sandbox.verify_client_bundle(bundle, profile)

            (bundle / "bin").chmod(0o755)
            executable.unlink()
            executable.symlink_to("pg_dump")
            (bundle / "bin").chmod(0o555)
            with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                ci_postgres_sandbox.write_client_manifest(bundle, profile)

    def test_client_staging_rejects_a_wrong_client_version_before_copy(self) -> None:
        profile = ci_postgres_sandbox.resolve_profile("postgres-17.11")
        with tempfile.TemporaryDirectory() as temporary_root:
            bundle = Path(temporary_root) / "client"
            bundle.mkdir()
            with (
                patch.object(
                    ci_postgres_sandbox,
                    "_run_docker",
                    side_effect=(
                        profile["image"],
                        "psql (PostgreSQL) 17.11 (Debian build)",
                        "pg_dump (PostgreSQL) 16.9 (Debian build)",
                    ),
                ),
                patch.object(ci_postgres_sandbox, "_copy_from_container") as copy,
            ):
                with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                    ci_postgres_sandbox.stage_client_bundle(
                        "a" * 64, profile=profile, bundle=bundle
                    )
        copy.assert_not_called()

    def test_staged_client_probe_passes_the_wrapper_as_argv0(self) -> None:
        loader = Path("/tmp/postgres-client/lib/ld-linux-x86-64.so.2")
        library_directory = loader.parent
        executable = library_directory / "psql.elf"
        wrapper = Path("/tmp/postgres-client/bin/psql")
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="psql (PostgreSQL) 17.11", stderr=""
        )

        with patch.object(
            ci_postgres_sandbox.subprocess, "run", return_value=completed
        ) as run:
            output = ci_postgres_sandbox._run_staged_client(
                loader, library_directory, executable, wrapper
            )

        self.assertEqual(output, "psql (PostgreSQL) 17.11")
        self.assertEqual(
            run.call_args.args[0],
            [
                str(loader),
                "--argv0",
                str(wrapper),
                "--library-path",
                str(library_directory),
                str(executable),
                "--version",
            ],
        )

        rejected = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="unrecognized option"
        )
        with patch.object(
            ci_postgres_sandbox.subprocess, "run", return_value=rejected
        ):
            with self.assertRaisesRegex(
                ci_postgres_sandbox.PostgresSandboxError, "could not be executed"
            ):
                ci_postgres_sandbox._run_staged_client(
                    loader, library_directory, executable, wrapper
                )

    def test_client_staging_keeps_payloads_read_only_and_generates_fixed_argv0(
        self,
    ) -> None:
        profile = ci_postgres_sandbox.resolve_profile("postgres-17.11")
        ldd_output = (
            "\tlibpq.so.5 => /usr/lib/x86_64-linux-gnu/libpq.so.5 (0x1234)\n"
            "\t/lib64/ld-linux-x86-64.so.2 (0x5678)\n"
            "\tlinux-vdso.so.1 (0x9abc)\n"
        )
        staged_clients: list[tuple[Path, Path]] = []

        def run_docker(command: list[str]) -> str:
            if command[1] == "inspect":
                return profile["image"]
            if command[-1] == "--version":
                executable = Path(command[-2]).name
                return f"{executable} (PostgreSQL) 17.11 (Debian build)"
            if command[-2] == "ldd":
                return ldd_output
            if command[-2] == "-f":
                path = command[-1]
                if path == "/lib64/ld-linux-x86-64.so.2":
                    return "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
                return path
            self.fail(f"unexpected Docker command: {command!r}")

        def copy_from_container(
            _container_id: str, source: str, destination: Path
        ) -> None:
            destination.write_bytes(f"copied {Path(source).name}".encode())

        def run_staged(
            _loader: Path,
            _library_directory: Path,
            executable: Path,
            argv0: Path,
        ) -> str:
            staged_clients.append((executable, argv0))
            return f"{executable.stem} (PostgreSQL) 17.11"

        with tempfile.TemporaryDirectory() as temporary_root:
            bundle = Path(temporary_root) / "client"
            bundle.mkdir()
            with (
                patch.object(
                    ci_postgres_sandbox, "_run_docker", side_effect=run_docker
                ),
                patch.object(
                    ci_postgres_sandbox,
                    "_copy_from_container",
                    side_effect=copy_from_container,
                ),
                patch.object(
                    ci_postgres_sandbox,
                    "_run_staged_client",
                    side_effect=run_staged,
                ),
            ):
                ci_postgres_sandbox.stage_client_bundle(
                    "a" * 64, profile=profile, bundle=bundle
                )

            ci_postgres_sandbox.verify_client_bundle(bundle, profile)
            self.assertEqual(
                [argv0.name for _, argv0 in staged_clients], ["psql", "pg_dump"]
            )
            for name in ("psql", "pg_dump"):
                self.assertEqual(
                    (bundle / "lib" / f"{name}.elf").stat().st_mode & 0o777,
                    0o444,
                )
                wrapper = bundle / "bin" / name
                self.assertEqual(wrapper.stat().st_mode & 0o777, 0o555)
                self.assertIn(
                    f'--argv0 "{ci_postgres_sandbox.CLIENT_SANDBOX_PATH}/bin/{name}"',
                    wrapper.read_text(encoding="utf-8"),
                )

    def test_profile_state_rejects_paths_outside_the_per_run_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / f"{ci_postgres_sandbox.PROFILE_ROOT_PREFIX}01234567"
            root.mkdir()
            state = {
                "schemaVersion": 1,
                "profileName": "postgres-17.11",
                "containerName": "verjson-postgres-0123456789ab",
                "containerId": None,
                "rootDirectory": str(root),
                "socketDirectory": str(root / "socket"),
                "clientBundleDirectory": str(root / "client"),
                "socketDevice": None,
                "socketInode": None,
                "socketMode": None,
                "status": "starting",
            }
            ci_postgres_sandbox.validate_profile_state(state, temporary_root)
            schema_state = {**state, "profileName": ci_postgres_sandbox.SCHEMA_VALIDATION_PROFILE_NAME}
            ci_postgres_sandbox.validate_profile_state(schema_state, temporary_root)

            state["socketDirectory"] = str(Path(temporary_root) / "other")
            with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                ci_postgres_sandbox.validate_profile_state(state, temporary_root)

    def test_cleanup_rejects_noncanonical_root_before_container_or_recursive_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            runner = Path(temporary_root) / "runner"
            runner.mkdir()
            generated_root = runner / f"{ci_postgres_sandbox.PROFILE_ROOT_PREFIX}01234567"
            generated_root.mkdir()
            invalid_roots = (
                str(runner / ".."),
                str(runner / "outside-profile"),
                f"{runner}/./{generated_root.name}",
            )

            for index, root_directory in enumerate(invalid_roots):
                with self.subTest(root_directory=root_directory):
                    root = Path(root_directory)
                    state_path = runner / f"postgres-state-{index}.json"
                    state = {
                        "schemaVersion": 1,
                        "profileName": "postgres-17.11",
                        "containerName": "verjson-postgres-0123456789ab",
                        "containerId": None,
                        "rootDirectory": root_directory,
                        "socketDirectory": str(root / "socket"),
                        "clientBundleDirectory": str(root / "client"),
                        "socketDevice": None,
                        "socketInode": None,
                        "socketMode": None,
                        "status": "starting",
                    }
                    state_path.write_text(json.dumps(state), encoding="utf-8")

                    with (
                        patch.object(ci_postgres_sandbox, "_remove_container") as remove_container,
                        patch.object(ci_postgres_sandbox, "_make_tree_removable") as make_removable,
                        patch.object(ci_postgres_sandbox.shutil, "rmtree") as rmtree,
                        self.assertRaises(ci_postgres_sandbox.PostgresSandboxError),
                    ):
                        ci_postgres_sandbox.cleanup_profile(state_path, runner_temp=runner)

                    remove_container.assert_not_called()
                    make_removable.assert_not_called()
                    rmtree.assert_not_called()
                    self.assertTrue(state_path.exists())

    def test_privilege_check_accepts_only_non_admin_role_and_unix_socket(self) -> None:
        with patch.object(
            ci_postgres_sandbox,
            "_run_docker",
            side_effect=("false:false:false:false", "t", "t"),
        ):
            ci_postgres_sandbox._assert_postgres_privileges("a" * 64)

    def test_schema_privilege_check_requires_temp_only_on_migration_database(self) -> None:
        responses = (
            "false:false:false:false",
            "t",
            "true:false:false",
            "t",
            "t",
            "true:true:false",
            "t",
            "t",
            "true:false:false",
            "t",
            "t",
        )
        with patch.object(ci_postgres_sandbox, "_run_docker", side_effect=responses) as run_docker:
            ci_postgres_sandbox._assert_postgres_privileges(
                "a" * 64,
                profile_name=ci_postgres_sandbox.SCHEMA_VALIDATION_PROFILE_NAME,
            )

        commands = [call.args[0] for call in run_docker.call_args_list]
        privilege_queries = [
            next(argument for argument in command if argument.startswith("--command=SELECT has_database_privilege"))
            for command in commands
            if any(argument.startswith("--command=SELECT has_database_privilege") for argument in command)
        ]
        self.assertEqual(
            privilege_queries,
            [
                "--command=SELECT has_database_privilege('verjson_ci', 'schema_snapshot', 'CONNECT') || ':' || has_database_privilege('verjson_ci', 'schema_snapshot', 'TEMP') || ':' || has_database_privilege('verjson_ci', 'schema_snapshot', 'CREATE');",
                "--command=SELECT has_database_privilege('verjson_ci', 'schema_migrations', 'CONNECT') || ':' || has_database_privilege('verjson_ci', 'schema_migrations', 'TEMP') || ':' || has_database_privilege('verjson_ci', 'schema_migrations', 'CREATE');",
                "--command=SELECT has_database_privilege('verjson_ci', 'schema_reference', 'CONNECT') || ':' || has_database_privilege('verjson_ci', 'schema_reference', 'TEMP') || ':' || has_database_privilege('verjson_ci', 'schema_reference', 'CREATE');",
            ],
        )

    def test_pre_receipt_staging_failures_remove_temp_tree_and_partial_state(self) -> None:
        original_mkdir = Path.mkdir
        original_chmod = os.chmod

        def fail_socket_mkdir(path, *args, **kwargs):
            if path.name == "socket":
                raise OSError("socket mkdir failed")
            return original_mkdir(path, *args, **kwargs)

        def fail_socket_chmod(path, mode, *args, **kwargs):
            if Path(path).name == "socket":
                raise OSError("socket chmod failed")
            return original_chmod(path, mode, *args, **kwargs)

        def fail_client_mkdir(path, *args, **kwargs):
            if path.name == "client":
                raise OSError("client mkdir failed")
            return original_mkdir(path, *args, **kwargs)

        def write_partial_state_then_fail(path, state, runner_temp):
            Path(path).write_text("partial", encoding="utf-8")
            raise OSError("state write failed")

        faults = (
            ("socket mkdir", lambda: patch.object(Path, "mkdir", new=fail_socket_mkdir)),
            ("socket chmod", lambda: patch.object(os, "chmod", new=fail_socket_chmod)),
            ("client mkdir", lambda: patch.object(Path, "mkdir", new=fail_client_mkdir)),
            ("state write", lambda: patch.object(
                ci_postgres_sandbox, "_write_state", new=write_partial_state_then_fail
            )),
        )
        for label, fault in faults:
            with self.subTest(failure=label), tempfile.TemporaryDirectory() as directory:
                runner = Path(directory)
                state_file = runner / "ci-postgres-sandbox-state.json"
                with fault(), patch.object(ci_postgres_sandbox, "_run_docker") as docker:
                    with self.assertRaises(OSError):
                        ci_postgres_sandbox.provision_profile(
                            "postgres-17.11",
                            runner_temp=runner,
                            state_file=state_file,
                        )
                docker.assert_not_called()
                self.assertEqual([], list(runner.iterdir()))

    def test_staging_cleanup_failure_is_reported_without_replacing_original_error(self) -> None:
        original_mkdir = Path.mkdir

        def fail_socket_mkdir(path, *args, **kwargs):
            if path.name == "socket":
                raise OSError("socket mkdir failed")
            return original_mkdir(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            runner = Path(directory)
            with (
                patch.object(Path, "mkdir", new=fail_socket_mkdir),
                patch.object(ci_postgres_sandbox.shutil, "rmtree", side_effect=PermissionError("cleanup denied")),
                self.assertRaisesRegex(OSError, "socket mkdir failed") as raised,
            ):
                ci_postgres_sandbox.provision_profile(
                    "postgres-17.11",
                    runner_temp=runner,
                    state_file=runner / "ci-postgres-sandbox-state.json",
                )
            self.assertTrue(
                any("cleanup denied" in note for note in raised.exception.__notes__)
            )

    def test_startup_waits_for_final_server_marker_before_sampling_postmaster(self) -> None:
        marker_seen = False
        log_responses = iter(("", "temporary init server is accepting connections",
                              ci_postgres_sandbox.POSTGRES_INITIALIZATION_COMPLETE_MARKER))
        commands = []

        def run_docker(command, **kwargs):
            nonlocal marker_seen
            commands.append(command)
            if command[1] == "logs":
                output = next(log_responses)
                marker_seen = ci_postgres_sandbox.POSTGRES_INITIALIZATION_COMPLETE_MARKER in output
                return output
            self.assertTrue(marker_seen, "postmaster sampled before official init server stopped")
            return "2026-10-26 00:00:02+00"

        with (
            patch.object(ci_postgres_sandbox, "_run_docker", side_effect=run_docker),
            patch.object(ci_postgres_sandbox.time, "sleep"),
        ):
            ci_postgres_sandbox._wait_for_postgres("a" * 64)

        self.assertEqual(6, len(commands))
        self.assertEqual(["logs"] * 3, [command[1] for command in commands[:3]])
        self.assertEqual(["exec"] * 3, [command[1] for command in commands[3:]])

    def test_socket_identity_check_rejects_replacement_and_permission_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root) / f"{ci_postgres_sandbox.PROFILE_ROOT_PREFIX}01234567"
            socket_dir = root / "socket"
            socket_dir.mkdir(parents=True)
            ci_postgres_sandbox.write_local_auth_policy(socket_dir)
            socket_path = socket_dir / ci_postgres_sandbox.POSTGRES_SOCKET_NAME
            server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(server_socket.close)
            server_socket.bind(str(socket_path))
            identity = socket_path.lstat()
            state = {
                "socketDirectory": str(socket_dir),
                "socketDevice": identity.st_dev,
                "socketInode": identity.st_ino,
                "socketMode": identity.st_mode & 0o777,
            }
            ci_postgres_sandbox.verify_profile_socket(state)

            socket_path.chmod(0o600)
            with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                ci_postgres_sandbox.verify_profile_socket(state)

            socket_path.chmod(state["socketMode"])
            socket_path.unlink()
            socket_path.write_text("replacement", encoding="utf-8")
            with self.assertRaises(ci_postgres_sandbox.PostgresSandboxError):
                ci_postgres_sandbox.verify_profile_socket(state)
            server_socket.close()

    def test_cleanup_removes_container_and_per_run_state_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            runner = Path(temporary_root)
            root = runner / f"{ci_postgres_sandbox.PROFILE_ROOT_PREFIX}01234567"
            (root / "socket").mkdir(parents=True)
            (root / "client").mkdir()
            state_path = runner / "postgres-state.json"
            state = {
                "schemaVersion": 1,
                "profileName": "postgres-17.11",
                "containerName": "verjson-postgres-0123456789ab",
                "containerId": None,
                "rootDirectory": str(root),
                "socketDirectory": str(root / "socket"),
                "clientBundleDirectory": str(root / "client"),
                "socketDevice": None,
                "socketInode": None,
                "socketMode": None,
                "status": "starting",
            }
            state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
            with patch.object(ci_postgres_sandbox, "_remove_container") as remove:
                ci_postgres_sandbox.cleanup_profile(state_path, runner_temp=runner)
                ci_postgres_sandbox.cleanup_profile(state_path, runner_temp=runner)

        remove.assert_called_once_with("verjson-postgres-0123456789ab")
        self.assertFalse(root.exists())
        self.assertFalse(state_path.exists())

    def test_cleanup_failure_preserves_receipt_and_runtime_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            runner = Path(temporary_root)
            root = runner / f"{ci_postgres_sandbox.PROFILE_ROOT_PREFIX}01234567"
            (root / "socket").mkdir(parents=True)
            (root / "client").mkdir()
            state_path = runner / "postgres-state.json"
            state = {
                "schemaVersion": 1,
                "profileName": "postgres-17.11",
                "containerName": "verjson-postgres-0123456789ab",
                "containerId": None,
                "rootDirectory": str(root),
                "socketDirectory": str(root / "socket"),
                "clientBundleDirectory": str(root / "client"),
                "socketDevice": None,
                "socketInode": None,
                "socketMode": None,
                "status": "starting",
            }
            state_path.write_text(__import__("json").dumps(state), encoding="utf-8")

            with patch.object(
                ci_postgres_sandbox,
                "_remove_container",
                side_effect=ci_postgres_sandbox.PostgresSandboxError("retry required"),
            ):
                with self.assertRaisesRegex(ci_postgres_sandbox.PostgresSandboxError, "retry required"):
                    ci_postgres_sandbox.cleanup_profile(state_path, runner_temp=runner)

            self.assertTrue(state_path.exists())
            self.assertTrue(root.exists())
            with patch.object(ci_postgres_sandbox, "_remove_container") as remove:
                ci_postgres_sandbox.cleanup_profile(state_path, runner_temp=runner)

            remove.assert_called_once_with("verjson-postgres-0123456789ab")
            self.assertFalse(state_path.exists())
            self.assertFalse(root.exists())

    def test_cleanup_retries_after_runtime_removal_before_receipt_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            runner = Path(temporary_root)
            root = runner / f"{ci_postgres_sandbox.PROFILE_ROOT_PREFIX}01234567"
            socket_directory = root / "socket"
            socket_directory.mkdir(parents=True)
            (root / "client").mkdir()
            socket_path = socket_directory / ci_postgres_sandbox.POSTGRES_SOCKET_NAME
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server_socket:
                server_socket.bind(str(socket_path))
                socket_metadata = socket_path.lstat()
                state_path = runner / "postgres-state.json"
                state = {
                    "schemaVersion": 1,
                    "profileName": "postgres-17.11",
                    "containerName": "verjson-postgres-0123456789ab",
                    "containerId": "a" * 64,
                    "rootDirectory": str(root),
                    "socketDirectory": str(socket_directory),
                    "clientBundleDirectory": str(root / "client"),
                    "socketDevice": socket_metadata.st_dev,
                    "socketInode": socket_metadata.st_ino,
                    "socketMode": socket_metadata.st_mode & 0o777,
                    "status": "ready",
                }
                state_path.write_text(json.dumps(state), encoding="utf-8")
                original_unlink = Path.unlink
                fail_receipt_unlink = True

                def unlink_receipt_once(path, *args, **kwargs):
                    nonlocal fail_receipt_unlink
                    if path == state_path and fail_receipt_unlink:
                        fail_receipt_unlink = False
                        raise InterruptedError("simulated interruption")
                    return original_unlink(path, *args, **kwargs)

                with patch.object(ci_postgres_sandbox, "_remove_container") as remove:
                    with patch.object(
                        Path, "unlink", autospec=True, side_effect=unlink_receipt_once
                    ):
                        with self.assertRaisesRegex(
                            InterruptedError, "simulated interruption"
                        ):
                            ci_postgres_sandbox.cleanup_profile(
                                state_path, runner_temp=runner
                            )
                        self.assertFalse(root.exists())
                        self.assertTrue(state_path.exists())
                        ci_postgres_sandbox.cleanup_profile(
                            state_path, runner_temp=runner
                        )

                self.assertEqual(remove.call_count, 2)
                self.assertFalse(root.exists())
                self.assertFalse(state_path.exists())

    def test_runtime_boundary_failure_cannot_write_a_success_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            runner_temp = Path(temporary_root)
            entry = {
                "directory": ".",
                "script": "boundary",
                "unsetEnv": [],
                "requiresServices": False,
            }
            identity = {
                "repository": "Verjson/fixture",
                "event": "workflow_dispatch",
                "ref": "refs/heads/main",
                "commit": "a" * 40,
                "workflowSha": "b" * 40,
                "runId": "42",
                "runAttempt": "1",
                "jobId": "build-test",
                "levelId": "postgres-17.11",
            }

            def reject_boundary(_ordinal: int, _entry: dict[str, object]):
                raise ci_postgres_sandbox.PostgresSandboxError("socket identity changed")

            with self.assertRaises(ci_build_artifacts.ScriptPlanExecutionError) as failure:
                ci_build_artifacts.run_script_plan_with_receipts(
                    [entry],
                    identity,
                    True,
                    runner_temp,
                    reject_boundary,
                )

            receipt_directory = failure.exception.receipt_directory
            plan = json.loads((receipt_directory / "plan.json").read_text(encoding="utf-8"))
            receipt = json.loads((receipt_directory / "entry-000000.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["state"], "infrastructure-failure")
            self.assertEqual(receipt["state"], "infrastructure-failure")


if __name__ == "__main__":
    unittest.main()
