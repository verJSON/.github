#!/usr/bin/env python3
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/node-ci.yml"
PACKAGE = "@verjson/authn"
PROTECTED_ENV = {
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    "ACTIONS_ID_TOKEN_REQUEST_URL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CREDENTIALS",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "NODE_AUTH_TOKEN",
    "NPM_TOKEN",
}


def add_file(archive, name, content):
    info = tarfile.TarInfo(name)
    info.mode = 0o644
    info.size = len(content)
    archive.addfile(info, io.BytesIO(content))


class SecretlessAbsentCompatibilityTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        steps = document["jobs"]["build-test"]["steps"]
        step = next(
            item for item in steps
            if item.get("name") == "Run runtime-resolved compatibility lanes without credentials"
        )
        cls.runner_text = step["run"]
        cls.temp_root = Path(tempfile.mkdtemp(prefix="node-ci-absent-compat-"))
        cls.runner_home = cls.temp_root / "runner-home"
        cls.runner_home.mkdir()
        cls.host_home_sentinel = cls.runner_home / ".host-runner-sentinel"
        cls.host_home_sentinel.write_text("runner home must stay hidden\n", encoding="utf-8")
        cls.runner = cls.temp_root / "run-lanes.sh"
        cls.runner.write_text(cls.runner_text, encoding="utf-8")
        cls.tool_cache = cls.temp_root / "tool-cache"
        cls.tool_bin = cls.tool_cache / "node" / "test" / "x64" / "bin"
        cls.tool_bin.mkdir(parents=True)
        node_source = shutil.which("node")
        if node_source is None:
            raise RuntimeError("Node is required for the compatibility consumer fixture")
        shutil.copy2(node_source, cls.tool_bin / "node")
        npm = cls.tool_bin / "npm"
        npm.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "[[ ${1:-} == run && ${2:-} == test:compat ]] || exit 64\n"
            "exec node test-compat.cjs\n",
            encoding="utf-8",
        )
        npm.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_root)

    def setUp(self):
        self.fixture = Path(tempfile.mkdtemp(dir=self.temp_root))

    def tearDown(self):
        shutil.rmtree(self.fixture)

    def prepare(self, *, target="absent", versions=("1.0.0",), package=PACKAGE,
                padding_members=0):
        node_modules = self.fixture / "node_modules"
        node_modules.mkdir()
        scope = node_modules / "@verjson"
        outside = self.fixture / "outside"
        outside.mkdir()
        if target == "node-modules-symlink":
            node_modules.rmdir()
            node_modules.symlink_to(outside, target_is_directory=True)
        elif target == "scope-symlink":
            scope.symlink_to(outside, target_is_directory=True)
        elif target != "missing-parent":
            scope.mkdir()

        package_target = node_modules.joinpath(*package.split("/"))
        if target == "existing":
            package_target.mkdir()
            (package_target / "package.json").write_text(
                json.dumps({"name": package, "version": "0.9.0"}) + "\n",
                encoding="utf-8",
            )
            (package_target / "baseline-only").write_text("baseline\n", encoding="utf-8")
        elif target == "target-symlink":
            package_target.symlink_to(outside, target_is_directory=True)

        ranges = [f"^{version}" for version in versions]
        request = {"package": package, "ranges": ranges, "script": "test:compat"}
        artifact_dir = self.fixture / "artifacts"
        artifact_dir.mkdir()
        lanes = []
        for index, version in enumerate(versions):
            artifact = artifact_dir / f"lane-{index}.tgz"
            with tarfile.open(artifact, "w:gz") as archive:
                add_file(
                    archive,
                    "package/package.json",
                    json.dumps({"name": package, "version": version}).encode(),
                )
                add_file(archive, "package/index.js", b"module.exports = true;\n")
                if index == 0:
                    for member in range(padding_members):
                        info = tarfile.TarInfo(f"package/padding/{member}")
                        info.type = tarfile.DIRTYPE
                        archive.addfile(info)
            artifact_bytes = artifact.read_bytes()
            digest = hashlib.sha512(artifact_bytes).digest()
            lanes.append({
                "index": index,
                "package": package,
                "range": ranges[index],
                "script": "test:compat",
                "version": version,
                "integrity": "sha512-" + base64.b64encode(digest).decode(),
                "tarball": f"https://npm.pkg.github.com/download/{package}/{version}/archive",
                "sha512": digest.hex(),
            })
        provenance = json.dumps(
            {"schemaVersion": 1, "request": request, "lanes": lanes},
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        (artifact_dir / "provenance.json").write_text(provenance, encoding="utf-8")
        (self.fixture / "package.json").write_text(json.dumps({
            "name": "self-package-consumer",
            "version": "1.0.0",
            "scripts": {"test:compat": "node test-compat.cjs"},
        }) + "\n", encoding="utf-8")
        (self.fixture / "results").mkdir()
        (self.fixture / "test-compat.cjs").write_text(
            """const assert = require('node:assert/strict');
const fs = require('node:fs');
if (process.env.TEST_SKIP_HOST_PATH_PROBE !== 'true') {
  assert.equal(fs.existsSync(process.env.HOST_HOME_SENTINEL), false);
  assert.equal(fs.existsSync('/run/docker.sock'), false);
  assert.equal(fs.existsSync('/var/run/docker.sock'), false);
}
const target = './node_modules/@verjson/authn';
function waitFor(path) {
  const deadline = Date.now() + 5000;
  while (!fs.existsSync(path)) {
    if (Date.now() >= deadline) throw new Error(`timed out waiting for ${path}`);
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
  }
}
if (process.env.TEST_SWAP_LOAD_RESTORE === 'true') {
  fs.writeFileSync('results/swap-ready', 'yes');
  waitFor('results/swap-complete');
}
if (process.env.TEST_ASSERT_NO_SEALED_DESCRIPTORS === 'true') {
  const leaked = fs.readdirSync('/proc/self/fd').filter((descriptor) => {
    try {
      return fs.readlinkSync('/proc/self/fd/' + descriptor)
        .includes('memfd:verjson-compatibility-artifact');
    } catch (error) {
      if (error.code === 'ENOENT') return false;
      throw error;
    }
  });
  assert.deepEqual(leaked, []);
}
const manifest = require(target + '/package.json');
fs.writeFileSync('results/loaded-version', manifest.version + '\\n');
if (process.env.TEST_SWAP_LOAD_RESTORE === 'true') {
  fs.writeFileSync('results/load-complete', 'yes');
  waitFor('results/restore-complete');
}
assert.equal(manifest.version, process.env.VERJSON_COMPATIBILITY_VERSION);
assert.deepEqual(fs.readdirSync(target).sort(), ['index.js', 'package.json']);
fs.appendFileSync('results/observed-versions', manifest.version + '\\n');
if (process.env.TEST_SIGNAL === 'true') {
  fs.writeFileSync('results/consumer-started', 'yes');
  setInterval(() => {}, 1000);
}
if (process.env.TEST_REPLACE_TARGET === 'true') {
  fs.renameSync(target, target + '-verified');
  fs.mkdirSync(target);
  fs.copyFileSync(target + '-verified/package.json', target + '/package.json');
  fs.copyFileSync(target + '-verified/index.js', target + '/index.js');
}
if (process.env.TEST_FAIL === 'true') process.exit(42);
""",
            encoding="utf-8",
        )
        self.request = request
        self.provenance_sha = hashlib.sha256(provenance.encode()).hexdigest()
        self.package_target = package_target
        self.scope = scope
        self.outside = outside

    def environment(self, **updates):
        env = os.environ.copy()
        for name in PROTECTED_ENV:
            env[name] = ""
        env.update({
            "COMPATIBILITY_ARTIFACT_DIR": str(self.fixture / "artifacts"),
            "COMPATIBILITY_RANGES": json.dumps(self.request, separators=(",", ":")),
            "EXPECTED_COMPATIBILITY_PROVENANCE_SHA256": self.provenance_sha,
            "BWRAP_BINARY": "/usr/bin/bwrap",
            "HOME": str(self.runner_home),
            "HOST_HOME_SENTINEL": str(self.host_home_sentinel),
            "PATH": f"{self.tool_bin}{os.pathsep}{env.get('PATH', '')}",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "RUNNER_OS": "Linux",
            "RUNNER_TOOL_CACHE": str(self.tool_cache),
            "RUNNER_TEMP": str(self.fixture / "runner-temp"),
        })
        env.update(updates)
        return env

    def instrument_runner(self, name, needle, replacement):
        self.assertEqual(1, self.runner_text.count(needle))
        runner = self.fixture / name
        runner.write_text(self.runner_text.replace(needle, replacement), encoding="utf-8")
        return runner

    def run_lane(self, runner=None, **updates):
        return subprocess.run(
            ["bash", str(runner or self.runner)],
            cwd=self.fixture,
            env=self.environment(**updates),
            capture_output=True,
            text=True,
        )

    def assert_absence_restored(self):
        self.assertFalse(self.package_target.exists())
        self.assertFalse(self.package_target.is_symlink())
        self.assertEqual([], list(self.scope.glob(".authn.*")))

    def wait_for_path(self, path, *, timeout=10):
        deadline = time.monotonic() + timeout
        while not path.exists():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.05, remaining))
        return True

    def test_initially_absent_target_is_removed_after_success(self):
        self.prepare()
        result = self.run_lane()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "1.0.0\n",
            (self.fixture / "results/observed-versions").read_text(),
        )
        self.assert_absence_restored()

    def test_memfd_unavailability_has_a_fixed_fail_closed_diagnostic(self):
        self.prepare()
        needle = "        descriptor = os.memfd_create(\n"
        replacement = (
            "        raise OSError(errno.EPERM, 'injected seccomp denial')\n"
            "        descriptor = os.memfd_create(\n"
        )
        runner = self.instrument_runner("memfd-unavailable.sh", needle, replacement)
        result = self.run_lane(runner=runner)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("sealed compatibility inputs are unavailable", result.stderr)
        self.assertNotIn("injected seccomp denial", result.stderr)
        self.assert_absence_restored()

    def test_sealed_descriptors_do_not_reach_the_consumer(self):
        self.prepare()
        result = self.run_lane(TEST_ASSERT_NO_SEALED_DESCRIPTORS="true")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assert_absence_restored()

    def test_nonempty_empty_target_backup_fails_with_fixed_diagnostic(self):
        self.prepare()
        needle = "    backup = backup_container / \"package\"\n"
        replacement = (
            needle
            + "    (backup_container / 'unexpected').touch()\n"
        )
        runner = self.instrument_runner("nonempty-backup.sh", needle, replacement)
        result = self.run_lane(runner=runner)
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "empty compatibility backup acquired unexpected entries",
            result.stderr,
        )
        self.assertFalse(self.package_target.exists())
        unexpected = list(self.scope.glob(".authn.previous-*-*/unexpected"))
        self.assertEqual(1, len(unexpected))

    def test_initially_absent_target_is_removed_after_script_failure(self):
        self.prepare()
        result = self.run_lane(TEST_FAIL="true")
        self.assertNotEqual(0, result.returncode)
        self.assert_absence_restored()

    def test_initially_absent_target_is_removed_after_signal(self):
        self.prepare()
        process = subprocess.Popen(
            ["bash", str(self.runner)],
            cwd=self.fixture,
            env=self.environment(TEST_SIGNAL="true"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        marker = self.fixture / "results/consumer-started"
        for _ in range(200):
            if marker.exists() or process.poll() is not None:
                break
            time.sleep(0.025)
        if not marker.exists():
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=5)
            self.fail(f"signal fixture did not reach consumer: {stdout}\n{stderr}")
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=10)
        self.assertNotEqual(0, process.returncode)
        self.assert_absence_restored()

    def test_multiple_lanes_replace_only_verified_staging_then_restore_absence(self):
        self.prepare(versions=("1.0.0", "2.0.0"))
        result = self.run_lane()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "1.0.0\n2.0.0\n",
            (self.fixture / "results/observed-versions").read_text(),
        )
        self.assert_absence_restored()

    def test_existing_target_behavior_remains_replaced(self):
        self.prepare(target="existing")
        result = self.run_lane()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("1.0.0", json.loads(
            (self.package_target / "package.json").read_text()
        )["version"])
        self.assertFalse((self.package_target / "baseline-only").exists())

    def test_each_parent_component_must_be_a_real_directory(self):
        for target, reason in (
            ("node-modules-symlink", "parent is not a real directory"),
            ("scope-symlink", "parent is not a real directory"),
            ("missing-parent", "parent is absent"),
        ):
            with self.subTest(target=target):
                self.tearDown()
                self.setUp()
                self.prepare(target=target)
                result = self.run_lane()
                self.assertNotEqual(0, result.returncode)
                self.assertIn(reason, result.stderr)
                self.assertEqual([], list(self.outside.iterdir()))

    def test_symlinked_target_fails_before_staging(self):
        self.prepare(target="target-symlink")
        result = self.run_lane()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("target is not a real directory", result.stderr)
        self.assertEqual([], list(self.outside.iterdir()))

    def test_package_path_escape_fails_before_staging(self):
        self.prepare(package="@verjson/../escaped")
        result = self.run_lane()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("package path is invalid", result.stderr)
        self.assertFalse((self.fixture / "node_modules/escaped").exists())

    def test_target_appearing_during_staging_fails_closed(self):
        self.prepare(padding_members=1024)
        appeared = threading.Event()

        def occupy_target():
            for _ in range(5000):
                if list(self.scope.glob(".authn.compat-*")):
                    self.package_target.mkdir()
                    appeared.set()
                    return
                time.sleep(0.001)

        watcher = threading.Thread(target=occupy_target)
        watcher.start()
        result = self.run_lane()
        watcher.join(timeout=6)
        self.assertTrue(appeared.is_set())
        self.assertNotEqual(0, result.returncode)
        self.assertIn("target appeared unexpectedly", result.stderr)
        self.assertFalse((self.fixture / "results/observed-versions").exists())
        self.assertEqual([], list(self.scope.glob(".authn.*")))

    def test_verified_staging_name_cannot_be_replaced_before_placement(self):
        self.prepare(padding_members=1024)
        replaced = threading.Event()

        def replace_staging():
            for _ in range(5000):
                candidates = list(self.scope.glob(".authn.compat-*"))
                if (candidates
                        and (candidates[0] / "package.json").exists()
                        and (candidates[0] / "index.js").exists()):
                    staging = candidates[0]
                    stolen = self.scope / ".authn.stolen"
                    staging.rename(stolen)
                    staging.mkdir()
                    shutil.copy(stolen / "package.json", staging / "package.json")
                    shutil.copy(stolen / "index.js", staging / "index.js")
                    replaced.set()
                    return
                time.sleep(0.001)

        watcher = threading.Thread(target=replace_staging)
        watcher.start()
        result = self.run_lane()
        watcher.join(timeout=6)
        self.assertTrue(replaced.is_set())
        self.assertNotEqual(0, result.returncode)
        self.assertIn("verified compatibility staging path changed", result.stderr)
        self.assertFalse((self.fixture / "results/observed-versions").exists())

    def test_target_appearance_in_atomic_placement_window_fails_closed(self):
        self.prepare()
        marker = self.fixture / "placement-ready"
        needle = "        try:\n            try:\n                place_without_replacement(staging.name, installed_leaf)"
        replacement = (
            "        try:\n"
            "            try:\n"
            "                Path(os.environ['TEST_PLACEMENT_READY']).touch()\n"
            "                __import__('time').sleep(0.2)\n"
            "                place_without_replacement(staging.name, installed_leaf)"
        )
        runner = self.instrument_runner("placement-window.sh", needle, replacement)
        process = subprocess.Popen(
            ["bash", str(runner)],
            cwd=self.fixture,
            env=self.environment(TEST_PLACEMENT_READY=str(marker)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(200):
            if marker.exists() or process.poll() is not None:
                break
            time.sleep(0.01)
        self.assertTrue(marker.exists())
        self.package_target.mkdir()
        _stdout, stderr = process.communicate(timeout=10)
        self.assertNotEqual(0, process.returncode)
        self.assertIn("target appeared during placement", stderr)
        self.assertFalse((self.fixture / "results/observed-versions").exists())

    def test_parent_replacement_during_staging_fails_closed(self):
        self.prepare(padding_members=1024)
        replaced = threading.Event()

        def replace_parent():
            for _ in range(5000):
                candidates = list(self.scope.glob(".authn.compat-*"))
                if candidates and (candidates[0] / "package.json").exists():
                    self.scope.rename(self.fixture / "verified-scope")
                    self.scope.mkdir()
                    replaced.set()
                    return
                time.sleep(0.001)

        watcher = threading.Thread(target=replace_parent)
        watcher.start()
        result = self.run_lane()
        watcher.join(timeout=6)
        self.assertTrue(replaced.is_set())
        self.assertNotEqual(0, result.returncode)
        self.assertIn("parent is not a real directory", result.stderr)
        self.assertFalse((self.fixture / "results/observed-versions").exists())

    def run_swap_load_restore(self, runner=None, *, skip_host_path_probe=False):
        attack_completed = threading.Event()
        attack_errors = []
        verified = self.scope / "authn-verified-outside"

        def swap_load_restore():
            try:
                ready = self.fixture / "results/swap-ready"
                loaded = self.fixture / "results/load-complete"
                if not self.wait_for_path(ready):
                    raise AssertionError("consumer never reached swap/load/restore window")
                self.package_target.rename(verified)
                self.package_target.mkdir()
                (self.package_target / "package.json").write_text(
                    json.dumps({"name": PACKAGE, "version": "9.9.9"}) + "\n",
                    encoding="utf-8",
                )
                (self.package_target / "index.js").write_text(
                    "throw new Error('malicious replacement loaded');\n",
                    encoding="utf-8",
                )
                (self.fixture / "results/swap-complete").touch()
                if not self.wait_for_path(loaded):
                    raise AssertionError("consumer never loaded a package during attack")
                shutil.rmtree(self.package_target)
                verified.rename(self.package_target)
                (self.fixture / "results/restore-complete").touch()
                attack_completed.set()
            except BaseException as error:
                attack_errors.append(error)
                if self.package_target.exists() and verified.exists():
                    shutil.rmtree(self.package_target)
                if verified.exists() and not self.package_target.exists():
                    verified.rename(self.package_target)
                (self.fixture / "results/restore-complete").touch()

        attacker = threading.Thread(target=swap_load_restore)
        attacker.start()
        result = self.run_lane(
            runner=runner,
            TEST_SWAP_LOAD_RESTORE="true",
            TEST_SKIP_HOST_PATH_PROBE=str(skip_host_path_probe).lower(),
        )
        attacker.join(timeout=10)
        self.assertFalse(attacker.is_alive())
        self.assertEqual([], attack_errors)
        self.assertTrue(attack_completed.is_set())
        return result

    def test_consumer_load_is_bound_during_external_swap_load_restore(self):
        self.prepare()
        result = self.run_swap_load_restore()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "1.0.0\n",
            (self.fixture / "results/loaded-version").read_text(),
        )
        self.assert_absence_restored()

    def test_removing_bound_execution_exposes_swap_load_restore_mutation(self):
        self.prepare()
        needle = (
            "                run_bound_consumer(\n"
            "                    sandbox_entries,\n"
            "                    artifact_bytes,\n"
            "                    provenance_bytes,\n"
            "                    script_env,\n"
            "                )"
        )
        replacement = (
            "                subprocess.run(\n"
            "                    ['npm', 'run', request['script']],\n"
            "                    check=True, env=script_env,\n"
            "                )"
        )
        runner = self.instrument_runner("unbound-consumer.sh", needle, replacement)
        result = self.run_swap_load_restore(runner, skip_host_path_probe=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("9.9.9", result.stderr)
        self.assertEqual(
            "9.9.9\n",
            (self.fixture / "results/loaded-version").read_text(),
        )
        self.assert_absence_restored()

    def test_target_replacement_after_use_cannot_produce_green_or_delete_entries(self):
        self.prepare()
        result = self.run_lane(TEST_REPLACE_TARGET="true")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("EBUSY", result.stderr)
        self.assert_absence_restored()
        self.assertEqual(
            "1.0.0\n",
            (self.fixture / "results/observed-versions").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
