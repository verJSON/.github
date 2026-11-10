#!/usr/bin/env python3
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci-gate"))
import prisma_schema_engine

ENGINE_COMMIT = "d1b3f377ca835d73fd50954a5e22128b9ebdd6b8"
ENGINE_VERSION = f"8.1.0-1.{ENGINE_COMMIT}"
PACKAGE_VERSION = "7.10.1"
TARGET = "debian-openssl-3.0.x"
OS_RELEASE = 'ID=ubuntu\nVERSION_ID="24.04"\n'
OPENSSL_VERSION = "OpenSSL 3.0.13 30 Jan 2024"
ENGINE_BINARY = bytearray(20)
ENGINE_BINARY[:4] = b"\x7fELF"
ENGINE_BINARY[4] = 2
ENGINE_BINARY[5] = 1
ENGINE_BINARY[18:20] = (62).to_bytes(2, "little")
ENGINE_BINARY = bytes(ENGINE_BINARY) + b"trusted-schema-engine"


def lockfile_bytes(*, engine_version=ENGINE_VERSION, lockfile_version=3):
    return json.dumps(
        {
            "lockfileVersion": lockfile_version,
            "packages": {
                "": {"name": "catalog"},
                "node_modules/prisma": {
                    "version": PACKAGE_VERSION,
                    "dependencies": {"@prisma/engines": PACKAGE_VERSION},
                },
                "node_modules/@prisma/engines": {
                    "version": PACKAGE_VERSION,
                    "dependencies": {"@prisma/engines-version": engine_version},
                },
                "node_modules/@prisma/engines-version": {
                    "version": engine_version,
                },
            },
        }
    ).encode()


class Response:
    def __init__(self, url, body):
        self.url = url
        self.body = body
        self.offset = 0
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, size=-1):
        if size < 0:
            size = len(self.body)
        result = self.body[self.offset : self.offset + size]
        self.offset += len(result)
        return result


class PrismaSchemaEngineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.runner_temp = self.root / "runner-temp"
        self.workspace.mkdir()
        self.runner_temp.mkdir()
        self.lockfile = self.workspace / "package-lock.json"
        self.lockfile.write_bytes(lockfile_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def stage(self, *, opener=None, **overrides):
        values = {
            "run_id": "42763747771",
            "run_attempt": "1",
            "runner_environment": "github-hosted",
            "runner_os": "Linux",
            "runner_arch": "X64",
            "os_release_text": OS_RELEASE,
            "openssl_version_text": OPENSSL_VERSION,
        }
        values.update(overrides)
        return prisma_schema_engine.stage_engine(
            self.lockfile,
            self.runner_temp,
            opener=opener,
            **values,
        )

    def valid_opener(self, *, artifact=None, checksum=None, seen=None):
        artifact = artifact or gzip.compress(ENGINE_BINARY)
        digest = hashlib.sha256(artifact).hexdigest()
        checksum = checksum or f"{digest}  ./{TARGET}/schema-engine.gz\n".encode()
        responses = {
            f"https://binaries.prisma.sh/all_commits/{ENGINE_COMMIT}/{TARGET}/schema-engine.gz.sha256": checksum,
            f"https://binaries.prisma.sh/all_commits/{ENGINE_COMMIT}/{TARGET}/schema-engine.gz": artifact,
        }

        def open_url(request, *, timeout):
            self.assertEqual(timeout, 20)
            url = request.full_url
            if seen is not None:
                seen.append(url)
            return Response(url, responses[url])

        return open_url, artifact

    def test_reads_the_locked_prisma_engine_commit(self):
        spec = prisma_schema_engine.parse_engine_spec(lockfile_bytes())

        self.assertEqual(spec.package_version, PACKAGE_VERSION)
        self.assertEqual(spec.engine_version, ENGINE_VERSION)
        self.assertEqual(spec.commit, ENGINE_COMMIT)

    def test_downloader_rejects_malformed_engine_commit_before_network(self):
        spec = prisma_schema_engine.EngineSpec(
            package_version=PACKAGE_VERSION,
            engine_version=ENGINE_VERSION,
            commit="../outside",
        )

        with self.assertRaisesRegex(
            prisma_schema_engine.PrismaEngineError, "commit"
        ):
            prisma_schema_engine._download_and_stage(
                spec,
                TARGET,
                None,
                lambda *_args, **_kwargs: self.fail(
                    "invalid engine commit reached the network"
                ),
            )

    def test_non_prisma_lockfile_does_not_download_an_engine(self):
        self.lockfile.write_text('{"lockfileVersion":3,"packages":{"":{"name":"app"}}}')
        requested = []

        self.assertFalse(self.stage(opener=lambda *_args, **_kwargs: requested.append(True)))
        self.assertEqual(requested, [])
        self.assertFalse((self.runner_temp / "verjson-prisma-schema-engine-42763747771-1").exists())

    def test_rejects_malformed_or_inconsistent_locked_engine_metadata_before_download(self):
        self.lockfile.write_bytes(lockfile_bytes(engine_version="8.1.0-1.not-a-commit"))
        requested = []

        with self.assertRaises(prisma_schema_engine.PrismaEngineError):
            self.stage(opener=lambda *_args, **_kwargs: requested.append(True))

        self.assertEqual(requested, [])

    def test_rejects_unsupported_lockfile_version_before_download(self):
        self.lockfile.write_bytes(lockfile_bytes(lockfile_version=1))
        requested = []

        with self.assertRaises(prisma_schema_engine.PrismaEngineError):
            self.stage(opener=lambda *_args, **_kwargs: requested.append(True))

        self.assertEqual(requested, [])

    def test_rejects_unsupported_runner_targets_before_download(self):
        for unsupported in (
            {"runner_environment": "self-hosted"},
            {"runner_os": "Windows"},
            {"runner_arch": "ARM64"},
            {"os_release_text": 'ID=ubuntu\nVERSION_ID="22.04"\n'},
            {"openssl_version_text": "OpenSSL 1.1.1 11 Sep 2018"},
        ):
            requested = []
            with self.subTest(unsupported=unsupported):
                with self.assertRaises(prisma_schema_engine.PrismaEngineError):
                    self.stage(
                        opener=lambda *_args, **_kwargs: requested.append(True),
                        **unsupported,
                    )
                self.assertEqual(requested, [])

    def test_maps_only_the_supported_hosted_ubuntu_target(self):
        self.assertEqual(
            prisma_schema_engine.resolve_runner_target(
                "github-hosted",
                "Linux",
                "X64",
                OS_RELEASE,
                OPENSSL_VERSION,
            ),
            TARGET,
        )

    def test_downloads_only_the_fixed_target_and_stages_one_executable(self):
        requested = []
        opener, _artifact = self.valid_opener(seen=requested)

        self.assertTrue(self.stage(opener=opener))

        stage_dir = self.runner_temp / "verjson-prisma-schema-engine-42763747771-1"
        self.assertEqual(
            set(path.name for path in stage_dir.iterdir()),
            {".owner", "schema-engine"},
        )
        self.assertEqual(
            requested,
            [
                f"https://binaries.prisma.sh/all_commits/{ENGINE_COMMIT}/{TARGET}/schema-engine.gz.sha256",
                f"https://binaries.prisma.sh/all_commits/{ENGINE_COMMIT}/{TARGET}/schema-engine.gz",
            ],
        )
        self.assertTrue((stage_dir / "schema-engine").stat().st_mode & 0o111)
        self.assertEqual((stage_dir / "schema-engine").read_bytes(), ENGINE_BINARY)

    def test_checksum_mismatch_cleans_the_owned_stage(self):
        opener, _artifact = self.valid_opener(checksum=b"0" * 64 + b"  ./debian-openssl-3.0.x/schema-engine.gz\n")

        with self.assertRaises(prisma_schema_engine.PrismaEngineError):
            self.stage(opener=opener)

        self.assertFalse((self.runner_temp / "verjson-prisma-schema-engine-42763747771-1").exists())

    def test_rejects_a_checksum_for_a_different_artifact_path(self):
        artifact = gzip.compress(ENGINE_BINARY)
        checksum = (
            f"{hashlib.sha256(artifact).hexdigest()}  "
            "./linux-musl-openssl-3.0.x/schema-engine.gz\n"
        ).encode()
        opener, _artifact = self.valid_opener(
            artifact=artifact,
            checksum=checksum,
        )

        with self.assertRaises(prisma_schema_engine.PrismaEngineError):
            self.stage(opener=opener)

        self.assertFalse((self.runner_temp / "verjson-prisma-schema-engine-42763747771-1").exists())

    def test_rejects_an_oversized_artifact_and_cleans_the_stage(self):
        opener, _artifact = self.valid_opener()

        with patch.object(prisma_schema_engine, "MAX_COMPRESSED_BYTES", 1):
            with self.assertRaises(prisma_schema_engine.PrismaEngineError):
                self.stage(opener=opener)

        self.assertFalse((self.runner_temp / "verjson-prisma-schema-engine-42763747771-1").exists())

    def test_refuses_a_symlinked_lockfile_without_network_access(self):
        target = self.workspace / "locked.json"
        target.write_bytes(lockfile_bytes())
        self.lockfile.unlink()
        self.lockfile.symlink_to(target)
        requested = []

        with self.assertRaises(prisma_schema_engine.PrismaEngineError):
            self.stage(opener=lambda *_args, **_kwargs: requested.append(True))

        self.assertEqual(requested, [])

    def test_cleanup_requires_the_exact_run_owner_and_removes_the_stage(self):
        opener, _artifact = self.valid_opener()
        self.stage(opener=opener)
        stage_dir = self.runner_temp / "verjson-prisma-schema-engine-42763747771-1"
        owner_marker = stage_dir / ".owner"
        owner_marker.write_text("verjson-prisma-schema-engine-v1\nrun_id=42763747772\nrun_attempt=1\n")

        with self.assertRaises(prisma_schema_engine.PrismaEngineError):
            prisma_schema_engine.cleanup_engine(
                self.runner_temp,
                run_id="42763747771",
                run_attempt="1",
            )
        self.assertTrue(stage_dir.exists())
        owner_marker.write_bytes(prisma_schema_engine._owner_marker("42763747771", "1"))

        self.assertTrue(
            prisma_schema_engine.cleanup_engine(
                self.runner_temp,
                run_id="42763747771",
                run_attempt="1",
            )
        )
        self.assertFalse(stage_dir.exists())

    def test_cleanup_refuses_a_symlink_at_the_owned_stage_path(self):
        outside = self.root / "outside"
        outside.mkdir()
        stage_dir = self.runner_temp / "verjson-prisma-schema-engine-42763747771-1"
        stage_dir.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(prisma_schema_engine.PrismaEngineError):
            prisma_schema_engine.cleanup_engine(
                self.runner_temp,
                run_id="42763747771",
                run_attempt="1",
            )

        self.assertTrue(outside.exists())

    def test_cleanup_recovers_run_owned_stage_interrupted_before_marker_commit(self):
        for run_id, marker_content in (
            ("42763747771", None),
            ("42763747772", prisma_schema_engine._owner_marker("42763747772", "1")[:12]),
        ):
            with self.subTest(marker_content=marker_content):
                stage_dir = self.runner_temp / f"verjson-prisma-schema-engine-{run_id}-1"
                stage_dir.mkdir()
                if marker_content is not None:
                    (stage_dir / ".owner").write_bytes(marker_content)

                self.assertTrue(
                    prisma_schema_engine.cleanup_engine(
                        self.runner_temp,
                        run_id=run_id,
                        run_attempt="1",
                    )
                )
                self.assertFalse(stage_dir.exists())

    def test_cleanup_removes_helper_even_when_stage_cleanup_fails(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/node-ci.yml").read_text())
        cleanup = next(
            step
            for step in workflow["jobs"]["build-test"]["steps"]
            if step.get("name") == "Remove staged Prisma schema engine"
        )
        stage_dir = self.runner_temp / "verjson-prisma-schema-engine-42763747771-1"
        stage_dir.mkdir()
        stager = self.runner_temp / "prisma-schema-engine-42763747771-1.py"
        stager.write_text("raise SystemExit(23)\n")
        environment = os.environ.copy()
        environment.update(
            {
                "PRISMA_ENGINE_STAGER": str(stager),
                "PRISMA_ENGINE_STAGE_DIR": str(stage_dir),
                "RUN_ID": "42763747771",
                "RUN_ATTEMPT": "1",
                "RUNNER_TEMP": str(self.runner_temp),
            }
        )

        result = subprocess.run(
            ["bash", "-c", cleanup["run"]],
            env=environment,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertTrue(stage_dir.exists())
        self.assertFalse(stager.exists())

    def test_workflow_stages_before_candidate_scripts_and_cleans_after_every_outcome(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/node-ci.yml").read_text())
        steps = workflow["jobs"]["build-test"]["steps"]
        names = [step.get("name") for step in steps]
        download = steps[names.index("Download trusted Prisma schema engine stager")]
        stage = steps[names.index("Stage pinned Prisma schema engine")]
        execute = steps[names.index("Run exact credentialless consumer script plan")]
        cleanup = steps[names.index("Remove staged Prisma schema engine")]

        self.assertIn("JOB_WORKFLOW_SHA", download["env"])
        self.assertIn("raw.githubusercontent.com/Verjson/.github/${JOB_WORKFLOW_SHA}", download["run"])
        self.assertLess(names.index(download["name"]), names.index(stage["name"]))
        self.assertLess(names.index(stage["name"]), names.index(execute["name"]))
        self.assertGreater(names.index(cleanup["name"]), names.index(execute["name"]))
        self.assertIn("$PRISMA_ENGINE_STAGER", stage["run"])
        self.assertIn("inputs.secretless-pr", stage["if"])
        self.assertIn("--workspace", stage["run"])
        self.assertIn('"package-lock.json"', Path(prisma_schema_engine.__file__).read_text())
        self.assertIn("PRISMA_SCHEMA_ENGINE_BINARY", execute["run"])
        self.assertIn("PRISMA_SCHEMA_ENGINE_HOST_PATH", execute["run"])
        self.assertIn("/run/verjson-prisma/schema-engine", execute["run"])
        self.assertIn("bind_source(", execute["run"])
        self.assertIn('"--bind" if writable else "--ro-bind"', execute["run"])
        self.assertIn("bubblewrap_network_args", execute["run"])
        self.assertIn("*network_args", execute["run"])
        self.assertIn("postgres_profile=postgres_profile is not None", execute["run"])
        self.assertIn("binaries.prisma.sh", Path(prisma_schema_engine.__file__).read_text())
        self.assertIn("always()", cleanup["if"])


if __name__ == "__main__":
    unittest.main()
