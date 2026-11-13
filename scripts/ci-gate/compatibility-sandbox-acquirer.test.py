#!/usr/bin/env python3
import copy
import errno
import hashlib
import io
import os
from pathlib import Path
import shlex
import stat
import subprocess
import traceback
import types
import unittest
import warnings

import yaml


ROOT = Path(__file__).resolve().parents[2]
STEP = "Provision trusted compatibility sandbox"
PROFILE_MEMBER = "usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
ARCHIVE_NAME = "apparmor-profiles_4.0.1really4.0.1-0ubuntu0.24.04.7_all.deb"
PROFILE = b"""abi <abi/4.0>,
profile bwrap /usr/bin/bwrap flags=(attach_disconnected) {
  allow px /** -> bwrap//&unpriv_bwrap,
}
profile unpriv_bwrap flags=(attach_disconnected) {
  allow pix /** -> &unpriv_bwrap,
  audit deny capability,
}
"""


def provisioner(path, job):
    document = yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))
    return next(
        step["run"]
        for step in document["jobs"][job]["steps"]
        if step.get("name") == STEP
    )


def embedded(source, name):
    start = source.index(f"{name}() {{\n")
    marker = "/usr/bin/cat <<'PY'\n"
    body = source.index(marker, start) + len(marker)
    end = source.index("\nPY\n}", body)
    return source[body:end] + "\n"


def load_acquirer():
    actions = provisioner(".github/workflows/actions-ci.yml", "hosted-compatibility-tests")
    node = provisioner(".github/workflows/node-ci.yml", "build-test")
    actions_source = embedded(actions, "sandbox_acquirer_source")
    node_source = embedded(node, "sandbox_acquirer_source")
    if actions_source != node_source:
        raise AssertionError("embedded acquirer sources differ")
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        code = compile(actions_source, "<exact-sandbox-acquirer>", "exec")
    namespace = {"__name__": "exact_sandbox_acquirer_test"}
    exec(code, namespace)
    supervisor_source = embedded(actions, "sandbox_acquirer_supervisor_source")
    with warnings.catch_warnings():
        warnings.simplefilter("error", SyntaxWarning)
        supervisor_code = compile(
            supervisor_source,
            "<exact-sandbox-acquirer-supervisor>",
            "exec",
        )
    supervisor = {"__name__": "exact_sandbox_acquirer_supervisor_test"}
    exec(supervisor_code, supervisor)
    return namespace, actions_source, supervisor_source, supervisor


class FakeStdout(io.BytesIO):
    def close(self):
        pass


class FakePopen:
    def __init__(self, payload, status=0):
        self.stdout = FakeStdout(payload)
        self.status = status
        self.killed = False

    def wait(self, timeout):
        return self.status

    def kill(self):
        self.killed = True


class Entry:
    def __init__(self, name):
        self.name = name
        self.path = f"/archive/{name}"


class AcquirerBehavior(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.acquirer, cls.source, cls.supervisor_source, cls.supervisor = load_acquirer()
        cls.error = cls.acquirer["AcquisitionError"]

    def assertRaisedBy(self, guard, function, *args):
        """A bare AcquisitionError carries no message, so its reason is the guard that raised it."""
        try:
            function(*args)
        except self.error as error:
            # assertRaises drops the traceback, and the raising frame is the reason.
            frame = traceback.extract_tb(error.__traceback__)[-1]
        else:
            self.fail("AcquisitionError not raised")
        self.assertEqual(frame.filename, "<exact-sandbox-acquirer>")
        lines = self.source.splitlines()
        start = frame.lineno - 2
        while not lines[start].lstrip().startswith("if "):
            start -= 1
        self.assertIn(guard, "\n".join(lines[start:frame.lineno - 1]))

    def packages(self, filename=None, *, duplicate=b"", fields=b""):
        filename = filename or f"pool/main/a/apparmor/{ARCHIVE_NAME}"
        return (
            b"Package: apparmor-profiles\n"
            b"Version: 4.0.1really4.0.1-0ubuntu0.24.04.7\n"
            b"Architecture: all\n"
            b"Size: 4096\n"
            + f"SHA256: {'a' * 64}\n".encode()
            + f"Filename: {filename}\n".encode()
            + fields
            + b"\n"
            + duplicate
        )

    def parse(self, payloads):
        queue = list(payloads)
        fake = types.SimpleNamespace(
            DEVNULL=subprocess.DEVNULL,
            PIPE=subprocess.PIPE,
            Popen=lambda *args, **kwargs: FakePopen(queue.pop(0)),
        )
        original = self.acquirer["subprocess"]
        self.acquirer["subprocess"] = fake
        try:
            return self.acquirer["parse_packages"](
                [f"/lists/{index}" for index in range(len(payloads))],
                "/usr/lib/apt/apt-helper",
                "apparmor-profiles",
                "4.0.1really4.0.1-0ubuntu0.24.04.7",
            )
        finally:
            self.acquirer["subprocess"] = original

    def acquire_fixture(self, failure=None, *, invoke_main=False):
        calls = []
        archive_bytes = b"a" * 4096
        archive_digest = hashlib.sha256(archive_bytes).hexdigest()
        status_identity = (1, 20, stat.S_IFREG | 0o600, 0, 0, 1, 2048, 1, 1)
        status_reads = 0
        expected_filename = f"pool/main/a/apparmor/{ARCHIVE_NAME}"

        fake_os = types.SimpleNamespace(
            geteuid=lambda: 0,
            getuid=lambda: 1000,
            lstat=lambda path: (_ for _ in ()).throw(FileNotFoundError(path)),
            mkdir=lambda path, mode: calls.append(("mkdir", path, mode)),
            scandir=lambda path: [Entry(ARCHIVE_NAME), Entry("partial"), Entry("lock")],
        )
        fake_pwd = types.SimpleNamespace(
            getpwnam=lambda name: types.SimpleNamespace(pw_uid=101)
        )

        def open_regular(path, **kwargs):
            nonlocal status_reads
            calls.append(("open_regular", path, kwargs))
            if path == self.acquirer["SAFE_KEY"]:
                return b"k" * 2048, (1, 10, stat.S_IFREG | 0o400, 0, 0, 1, 2048, 1, 1)
            if path == "/var/lib/dpkg/status":
                status_reads += 1
                identity = status_identity
                if failure == "status-drift" and status_reads > 1:
                    identity = (1, 21, *status_identity[2:])
                return b"s" * 2048, identity
            if path.endswith(".deb"):
                return archive_bytes, (1, 30, stat.S_IFREG | 0o600, 0, 0, 1, len(archive_bytes), 1, 1)
            return b"x" * 1024, (1, 40, stat.S_IFREG | 0o755, 0, 0, 1, 1024, 1, 1)

        def exact_output(args, maximum=65536):
            calls.append(("exact_output", args, maximum))
            joined = " ".join(args)
            if args[:3] == ("/usr/bin/dpkg-query", "-S", self.acquirer["SAFE_KEY"]):
                return f"ubuntu-keyring: {self.acquirer['SAFE_KEY']}\n".encode()
            if args == ("/usr/bin/dpkg", "--print-architecture"):
                return b"amd64\n"
            if "--simulate" in args:
                return b"Inst apparmor\nInst apparmor-profiles\nInst bubblewrap\n"
            if "/usr/bin/dpkg-query -W" in joined:
                return b"ii \tapparmor-profiles\t4.0.1really4.0.1-0ubuntu0.24.04.7\tall\n"
            if "indextargets" in args:
                return f"{self.acquirer['SESSION_ROOT']}/lists/packages\n".encode()
            if args[:2] == ("/usr/bin/dpkg-deb", "--field"):
                field = args[-1]
                values = {
                    "Package": "apparmor-profiles",
                    "Version": "4.0.1really4.0.1-0ubuntu0.24.04.7",
                    "Architecture": "all",
                }
                if failure == "control" and field == "Package":
                    return b"wrong\n"
                return f"{values[field]}\n".encode()
            raise AssertionError(f"unexpected exact_output: {args}")

        def run(args, **kwargs):
            calls.append(("run", args, kwargs))
            status = 0
            if failure == "update" and args[-1] == "update":
                status = 1
            elif failure == "install" and "install" in args and "--download-only" not in args:
                status = 2
            elif failure == "download" and "--download-only" in args:
                status = 3
            return types.SimpleNamespace(returncode=status)

        def parse_packages(*args):
            calls.append(("parse_packages", args))
            filename = expected_filename
            basename = ARCHIVE_NAME
            if failure == "filename":
                filename = f"pool/main/a/apparmor/wrong_{ARCHIVE_NAME}"
                basename = f"wrong_{ARCHIVE_NAME}"
            expected_size = len(archive_bytes) + (1 if failure == "archive-size" else 0)
            expected_digest = "0" * 64 if failure == "archive-digest" else archive_digest
            return expected_size, expected_digest, filename, basename

        originals = {
            name: self.acquirer[name]
            for name in (
                "os",
                "pwd",
                "validate_directory",
                "open_regular",
                "exact_output",
                "run",
                "record_owned_root",
                "create_apt_partial",
                "write_root_file",
                "validate_regular_path",
                "parse_packages",
                "extract_profile",
                "stage_profile",
                "cleanup_owned_root",
                "signal",
                "sys",
            )
        }
        try:
            self.acquirer.update(
                os=fake_os,
                pwd=fake_pwd,
                validate_directory=lambda path, **kwargs: calls.append(("validate_directory", path, kwargs)),
                open_regular=open_regular,
                exact_output=exact_output,
                run=run,
                record_owned_root=lambda path: calls.append(("record_owned_root", path)),
                create_apt_partial=lambda path, uid: calls.append(("create_apt_partial", path, uid)),
                write_root_file=lambda path, data, mode: calls.append(("write_root_file", path, data, mode)),
                validate_regular_path=lambda path, **kwargs: calls.append(("validate_regular_path", path, kwargs)),
                parse_packages=parse_packages,
                extract_profile=lambda path: calls.append(("extract_profile", path)) or PROFILE,
                stage_profile=lambda data, identity: (
                    (_ for _ in ()).throw(self.error())
                    if failure == "stage"
                    else calls.append(("stage_profile", data, identity))
                ),
                cleanup_owned_root=lambda path: calls.append(("cleanup_owned_root", path)),
                signal=types.SimpleNamespace(
                    SIGHUP=1,
                    SIGINT=2,
                    SIGTERM=15,
                    signal=lambda number, handler: calls.append(("signal", number)),
                ),
                sys=types.SimpleNamespace(
                    version_info=(3, 11),
                    stderr=types.SimpleNamespace(buffer=io.BytesIO()),
                ),
            )
            if invoke_main:
                self.acquirer["main"]()
            else:
                self.acquirer["acquire"]()
        finally:
            self.acquirer.update(originals)
        return calls

    def test_full_acquire_golden_wires_every_boundary(self):
        calls = self.acquire_fixture(invoke_main=True)
        apt = [call[1] for call in calls if call[0] in ("run", "exact_output")]
        rendered = [" ".join(args) for args in apt]
        self.assertTrue(any(command.endswith(" update") for command in rendered))
        self.assertTrue(any("--simulate" in command for command in rendered))
        self.assertTrue(any(" install apparmor apparmor-profiles bubblewrap" in command for command in rendered))
        self.assertTrue(any("--download-only --reinstall" in command for command in rendered))
        self.assertTrue(any(" indextargets " in f" {command} " for command in rendered))
        metadata_commands = [
            call[1]
            for call in calls
            if call[0] == "exact_output" and "indextargets" in call[1]
        ]
        self.assertEqual(len(metadata_commands), 1)
        self.assertEqual(
            metadata_commands[0][-5:],
            (
                "indextargets",
                "--format",
                "$(FILENAME)",
                "Created-By: Packages",
                "Component: main",
            ),
        )
        for command in (command for command in rendered if command.startswith("/usr/bin/apt-get")):
            self.assertIn("Dir::Etc::netrc=/dev/null", command)
            self.assertIn("Dir::Etc::netrcparts=/var/lib/verjson-compatibility-apt/empty-auth", command)
            self.assertIn("Dir::Etc::preferences=/dev/null", command)
            self.assertIn("Dir::Etc::preferencesparts=/var/lib/verjson-compatibility-apt/empty-preferences", command)
        self.assertEqual(sum(call[0] == "parse_packages" for call in calls), 1)
        self.assertIn(
            (
                "validate_regular_path",
                f"{self.acquirer['SESSION_ROOT']}/lists/packages",
                {"executable": False, "maximum": 256 * 1024 * 1024},
            ),
            calls,
        )
        self.assertEqual(sum(call[0] == "extract_profile" for call in calls), 1)
        self.assertEqual(sum(call[0] == "stage_profile" for call in calls), 1)
        created = [call[1] for call in calls if call[0] == "mkdir"]
        self.assertIn(self.acquirer["SESSION_ROOT"], created)
        self.assertIn(self.acquirer["STAGE_ROOT"], created)
        self.assertTrue(any(path.endswith("empty-auth") for path in created))
        self.assertTrue(any(path.endswith("empty-preferences") for path in created))
        self.assertEqual(
            [call for call in calls if call[0] == "cleanup_owned_root"],
            [("cleanup_owned_root", self.acquirer["SESSION_ROOT"])],
        )

    def test_full_acquire_routes_representative_failures_through_orchestration(self):
        for failure in (
            "update",
            "install",
            "download",
            "status-drift",
            "filename",
            "archive-size",
            "archive-digest",
            "control",
            "stage",
        ):
            with self.subTest(failure=failure), self.assertRaises(self.error):
                self.acquire_fixture(failure)

    def test_exact_source_import_is_side_effect_free_and_options_are_isolated(self):
        self.assertIn('if __name__ == "__main__":\n    main()', self.source)
        options = self.acquirer["apt_options"]("/cache", "/lists", "/sources")
        joined = "\n".join(options)
        for exact in (
            "Dir::Etc::netrc=/dev/null",
            "Dir::Etc::netrcparts=/var/lib/verjson-compatibility-apt/empty-auth",
            "Dir::Etc::preferences=/dev/null",
            "Dir::Etc::preferencesparts=/var/lib/verjson-compatibility-apt/empty-preferences",
            "Dir::State::status=/var/lib/dpkg/status",
        ):
            self.assertIn(exact, joined)
        for unsafe in ("/etc/apt/auth.conf", "/etc/apt/preferences.d", "/var/cache/apt"):
            self.assertNotIn(unsafe, joined)

    def test_packages_golden_and_filename_are_bound(self):
        size, digest, filename, basename = self.parse([self.packages()])
        self.assertEqual(size, 4096)
        self.assertEqual(digest, "a" * 64)
        self.assertEqual(filename, f"pool/main/a/apparmor/{ARCHIVE_NAME}")
        self.assertEqual(basename, ARCHIVE_NAME)

    def test_main_component_selection_excludes_oversized_unrelated_universe_record(self):
        oversized_line = (
            b"Provides: " + b"x" * (70_841 - len(b"Provides: ") - 1) + b"\n"
        )
        universe_record = (
            b"Package: librust-winapi-dev\n"
            b"Version: 0\n"
            b"Architecture: all\n"
            + oversized_line
            + b"\n"
        )
        self.assertEqual(len(oversized_line), 70_841)

        size, digest, filename, basename = self.parse([self.packages()])
        self.assertEqual(
            (size, digest, filename, basename),
            (
                4096,
                "a" * 64,
                f"pool/main/a/apparmor/{ARCHIVE_NAME}",
                ARCHIVE_NAME,
            ),
        )
        with self.assertRaises(self.error):
            self.parse([universe_record, self.packages()])

    def test_packages_reject_malformed_missing_duplicate_conflict_and_paths(self):
        valid = self.packages()
        cases = {
            "missing": valid.replace(b"SHA256: " + b"a" * 64 + b"\n", b""),
            "duplicate-field": valid.replace(b"Size: 4096\n", b"Size: 4096\nSize: 4096\n"),
            "malformed-size": valid.replace(b"Size: 4096", b"Size: zero"),
            "absolute": self.packages(f"/pool/main/a/apparmor/{ARCHIVE_NAME}"),
            "traversal": self.packages(f"pool/main/../apparmor/{ARCHIVE_NAME}"),
            "encoded": self.packages(f"pool/main/a/apparmor/{ARCHIVE_NAME.replace('_', '%5f', 1)}"),
            "ambiguous": self.packages(f"pool//main/a/apparmor/{ARCHIVE_NAME}"),
            "wrong-arch": valid.replace(b"Architecture: all", b"Architecture: amd64"),
        }
        # The filename shape guard also refuses an absolute or traversing path, so
        # each case names the guard that must reject it first.
        record_shape = 'or not filename_raw.startswith(b"pool/")\n        or b".." in filename_raw.split(b"/")'
        path_shape = 'or "%" in filename\n        or "//" in filename'
        guards = {
            "missing": "if any(name not in fields for name in required):",
            "duplicate-field": "if name in fields:",
            "malformed-size": 're.fullmatch(rb"[1-9][0-9]{0,8}", size_raw) is None',
            "absolute": record_shape,
            "traversal": record_shape,
            "encoded": path_shape,
            "ambiguous": path_shape,
            "wrong-arch": "if not matches or len(set(matches)) != 1:",
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                self.assertRaisedBy(guards[name], self.parse, [payload])
        conflict = self.packages().replace(b"Size: 4096", b"Size: 4097")
        with self.assertRaises(self.error):
            self.parse([valid, conflict])

    def test_install_plan_rejects_removal_overflow_and_invalid_encoding(self):
        self.acquirer["validate_install_plan"](b"Inst bubblewrap\n")
        for plan in (
            b"Remv base-files\n",
            b"Purg base-files\n",
            b"Inst x\n" * 33,
            b"\xff",
        ):
            with self.subTest(plan=plan[:16]), self.assertRaises(self.error):
                self.acquirer["validate_install_plan"](plan)

    def test_update_install_and_download_fail_closed(self):
        self.assertEqual(self.source.count("require_success(run("), 3)
        self.acquirer["require_success"](types.SimpleNamespace(returncode=0))
        for phase in ("update", "install", "download"):
            with self.subTest(phase=phase), self.assertRaises(self.error):
                self.acquirer["require_success"](
                    types.SimpleNamespace(returncode={"update": 1, "install": 2, "download": 3}[phase])
                )

    def test_archive_selection_binds_exact_name_and_rejects_residue(self):
        archive, locks = self.acquirer["select_archive"](
            [Entry(ARCHIVE_NAME), Entry("partial"), Entry("lock")],
            ARCHIVE_NAME,
        )
        self.assertEqual(archive.name, ARCHIVE_NAME)
        self.assertEqual([entry.name for entry in locks], ["lock"])
        for entries in (
            [],
            [Entry(ARCHIVE_NAME), Entry(ARCHIVE_NAME)],
            [Entry("wrong.deb")],
            [Entry(ARCHIVE_NAME), Entry("residue")],
            [Entry(ARCHIVE_NAME), Entry("lock"), Entry("lock")],
        ):
            with self.subTest(entries=[entry.name for entry in entries]), self.assertRaises(self.error):
                self.acquirer["select_archive"](entries, ARCHIVE_NAME)

    def test_open_regular_rejects_unsafe_archive_metadata_and_drift(self):
        content = b"archive"
        regular = stat.S_IFREG | 0o600

        def fake_os(metadata, after=None, capability=False):
            reads = [content, b""]
            return types.SimpleNamespace(
                O_RDONLY=os.O_RDONLY,
                O_CLOEXEC=os.O_CLOEXEC,
                O_NOFOLLOW=getattr(os, "O_NOFOLLOW", 0),
                open=lambda *args: 7,
                fstat=lambda descriptor: (after or metadata) if not reads else metadata,
                read=lambda descriptor, maximum: reads.pop(0),
                close=lambda descriptor: None,
                getxattr=lambda descriptor, name: b"cap" if capability else (_ for _ in ()).throw(OSError(errno.ENODATA, "absent")),
            )

        def metadata(**changes):
            values = dict(
                st_mode=regular,
                st_uid=0,
                st_gid=0,
                st_nlink=1,
                st_size=len(content),
                st_dev=1,
                st_ino=2,
                st_mtime_ns=3,
                st_ctime_ns=4,
            )
            values.update(changes)
            return types.SimpleNamespace(**values)

        original = self.acquirer["os"]
        try:
            self.acquirer["os"] = fake_os(metadata())
            actual, _identity = self.acquirer["open_regular"]("/archive", executable=False, maximum=32)
            self.assertEqual(actual, content)
            # The post-read identity check also refuses an oversized file, so each
            # case names the pre-read metadata guard that must reject it first.
            unsafe = (
                (metadata(st_uid=1), "or before.st_uid != 0"),
                (metadata(st_gid=1), "or before.st_gid != 0"),
                (metadata(st_nlink=2), "or before.st_nlink != 1"),
                (metadata(st_mode=regular | 0o022), "or before.st_mode & 0o022"),
                (metadata(st_mode=stat.S_IFLNK | 0o600), "not stat.S_ISREG(before.st_mode)"),
                (metadata(st_size=33), "or before.st_size > maximum"),
            )
            for value, guard in unsafe:
                with self.subTest(value=value):
                    self.acquirer["os"] = fake_os(value)
                    self.assertRaisedBy(
                        guard, lambda: self.acquirer["open_regular"]("/archive", executable=False, maximum=32))
            with self.assertRaises(self.error):
                self.acquirer["os"] = fake_os(metadata(), capability=True)
                self.acquirer["open_regular"]("/archive", executable=False, maximum=32)
            with self.assertRaises(self.error):
                self.acquirer["os"] = fake_os(metadata(), after=metadata(st_ino=9))
                self.acquirer["open_regular"]("/archive", executable=False, maximum=32)
        finally:
            self.acquirer["os"] = original

    def test_key_ancestor_and_status_metadata_fail_closed(self):
        safe = types.SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o755,
            st_uid=0,
            st_gid=0,
        )
        original = self.acquirer["os"]
        try:
            self.acquirer["os"] = types.SimpleNamespace(lstat=lambda path: safe)
            self.acquirer["validate_directory"]("/etc/apt/trusted.gpg.d")
            unsafe = (
                types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0, st_gid=0),
                types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0, st_gid=0),
                types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=1, st_gid=0),
            )
            for metadata in unsafe:
                with self.subTest(metadata=metadata), self.assertRaises(self.error):
                    self.acquirer["os"] = types.SimpleNamespace(lstat=lambda path, metadata=metadata: metadata)
                    self.acquirer["validate_directory"]("/etc/apt/trusted.gpg.d")
        finally:
            self.acquirer["os"] = original

    def test_tar_member_golden_and_unsafe_shapes(self):
        target = f"./{PROFILE_MEMBER}".encode()
        golden = (
            f"./{PROFILE_MEMBER}\n".encode(),
            b"-rw-r--r-- 0/0 " + str(len(PROFILE)).encode() + b" now " + target + b"\n",
            PROFILE,
        )

        def run(outputs):
            queue = list(outputs)
            original = self.acquirer["tar_command"]
            self.acquirer["tar_command"] = lambda *args, **kwargs: queue.pop(0)
            try:
                return self.acquirer["extract_profile"]("/archive.deb")
            finally:
                self.acquirer["tar_command"] = original

        self.assertEqual(run(golden), PROFILE)
        escaping = 'if name.startswith("/") or posixpath.normpath(name).startswith("../"):'
        header = 'or header[0] != b"-rw-r--r--"\n        or header[1] != b"0/0"'
        # Every case below that changes the extracted bytes also changes their
        # length, so the declared-size guard rejects it before validate_profile
        # reads the content: these pin the size guard, not profile content checks.
        declared_size = "if not profile_bytes or len(profile_bytes) != declared_profile_size:"
        cases = (
            ((b"/" + PROFILE_MEMBER.encode() + b"\n", golden[1], PROFILE), escaping),
            ((b"../" + PROFILE_MEMBER.encode() + b"\n", golden[1], PROFILE), escaping),
            ((golden[0] + golden[0], golden[1], PROFILE), "if aliases != [target]:"),
            ((golden[0], golden[1] + golden[1], PROFILE), "if len(member_lines) != 1:"),
            ((golden[0], golden[1].replace(b"-rw-r--r--", b"lrwxrwxrwx"), PROFILE), header),
            ((golden[0], golden[1].replace(b"0/0", b"1/0"), PROFILE), header),
            ((golden[0], golden[1].replace(str(len(PROFILE)).encode(), b"1", 1), PROFILE), declared_size),
            ((golden[0], golden[1], PROFILE[:-1]), declared_size),  # one byte short of the declared size
            ((golden[0], golden[1], b"not a profile"), declared_size),  # a different length, not a content check
        )
        for outputs, guard in cases:
            with self.subTest(outputs=outputs[0][:24]):
                self.assertRaisedBy(guard, run, outputs)

    def test_staging_rejects_fsync_identity_and_unexpected_entries(self):
        calls = []
        fake_os = types.SimpleNamespace(
            O_RDONLY=os.O_RDONLY,
            O_DIRECTORY=os.O_DIRECTORY,
            O_CLOEXEC=os.O_CLOEXEC,
            O_NOFOLLOW=getattr(os, "O_NOFOLLOW", 0),
            rename=lambda source, target: calls.append(("rename", source, target)),
            open=lambda *args: 8,
            fsync=lambda descriptor: calls.append(("fsync", descriptor)),
            close=lambda descriptor: None,
            chmod=lambda path, mode: calls.append(("chmod", path, mode)),
            listdir=lambda path: ["bwrap-userns-restrict"],
        )
        originals = {name: self.acquirer[name] for name in ("os", "write_root_file", "validate_directory", "open_regular")}
        try:
            self.acquirer["os"] = fake_os
            self.acquirer["write_root_file"] = lambda path, data, mode: calls.append(("write", path, data, mode))
            self.acquirer["validate_directory"] = lambda path: None
            self.acquirer["open_regular"] = lambda *args, **kwargs: (
                PROFILE,
                (1, 20, stat.S_IFREG | 0o400, 0, 0, 1, len(PROFILE), 1, 1),
            )
            self.acquirer["stage_profile"](PROFILE, (1, 10))
            self.assertEqual([call[0] for call in calls[:3]], ["write", "rename", "fsync"])
            collision = FileExistsError("collision")
            self.acquirer["write_root_file"] = lambda *args: (_ for _ in ()).throw(collision)
            with self.assertRaises(FileExistsError) as raised:
                self.acquirer["stage_profile"](PROFILE, (1, 10))
            self.assertIs(raised.exception, collision)
            self.acquirer["write_root_file"] = lambda path, data, mode: calls.append(("write", path, data, mode))
            fake_os.listdir = lambda path: ["bwrap-userns-restrict", "unexpected"]
            with self.assertRaises(self.error):
                self.acquirer["stage_profile"](PROFILE, (1, 10))
            fake_os.listdir = lambda path: ["bwrap-userns-restrict"]
            self.acquirer["open_regular"] = lambda *args, **kwargs: (
                PROFILE,
                (1, 10, stat.S_IFREG | 0o400, 0, 0, 1, len(PROFILE), 1, 1),
            )
            with self.assertRaises(self.error):
                self.acquirer["stage_profile"](PROFILE, (1, 10))
            self.acquirer["open_regular"] = lambda *args, **kwargs: (
                PROFILE,
                (2, 10, stat.S_IFREG | 0o400, 0, 0, 1, len(PROFILE), 1, 1),
            )
            self.acquirer["stage_profile"](PROFILE, (1, 10))
            fsync_failure = OSError("fsync")
            fake_os.fsync = lambda descriptor: (_ for _ in ()).throw(fsync_failure)
            with self.assertRaises(OSError) as raised:
                self.acquirer["stage_profile"](PROFILE, (1, 10))
            self.assertIs(raised.exception, fsync_failure)
        finally:
            self.acquirer.update(originals)

    def test_main_restores_failure_and_cleanup_postconditions(self):
        original = {
            name: self.acquirer[name]
            for name in ("acquire", "cleanup_owned_root", "signal", "sys", "CURRENT_PHASE")
        }

        class Sink:
            def __init__(self):
                self.buffer = io.BytesIO()

        try:
            events = []
            self.acquirer["signal"] = types.SimpleNamespace(
                SIGHUP=1,
                SIGINT=2,
                SIGTERM=15,
                signal=lambda number, handler: events.append(("signal", number)),
            )
            self.acquirer["sys"] = types.SimpleNamespace(stderr=Sink())
            self.acquirer["acquire"] = lambda: events.append(("acquire",))
            self.acquirer["cleanup_owned_root"] = lambda path: events.append(("cleanup", path))
            self.acquirer["main"]()
            self.assertEqual([event for event in events if event[0] == "cleanup"], [("cleanup", self.acquirer["SESSION_ROOT"])])

            events.clear()
            self.acquirer["CURRENT_PHASE"] = "package-acquisition-key"
            self.acquirer["acquire"] = lambda: (_ for _ in ()).throw(self.error())
            with self.assertRaises(SystemExit) as raised:
                self.acquirer["main"]()
            self.assertEqual(raised.exception.code, 1)
            self.assertEqual(
                [event for event in events if event[0] == "cleanup"],
                [("cleanup", self.acquirer["SESSION_ROOT"]), ("cleanup", self.acquirer["STAGE_ROOT"])],
            )
            self.assertEqual(
                self.acquirer["sys"].stderr.buffer.getvalue(),
                self.acquirer["ACQUISITION_DIAGNOSTICS"]["package-acquisition-key"],
            )

            self.acquirer["sys"] = types.SimpleNamespace(stderr=Sink())
            self.acquirer["acquire"] = lambda: None
            self.acquirer["cleanup_owned_root"] = lambda path: (_ for _ in ()).throw(self.error())
            with self.assertRaises(SystemExit) as raised:
                self.acquirer["main"]()
            self.assertEqual(raised.exception.code, 1)
            self.assertIn(b"phase=package-profile-cleanup", self.acquirer["sys"].stderr.buffer.getvalue())
        finally:
            self.acquirer.update(original)

    def test_main_emits_only_the_fixed_current_acquisition_phase(self):
        original = {
            name: self.acquirer[name]
            for name in ("acquire", "cleanup_owned_root", "signal", "sys", "CURRENT_PHASE")
        }

        class Sink:
            def __init__(self):
                self.buffer = io.BytesIO()

        try:
            self.acquirer["signal"] = types.SimpleNamespace(
                SIGHUP=1,
                SIGINT=2,
                SIGTERM=15,
                signal=lambda *_args: None,
            )
            self.acquirer["cleanup_owned_root"] = lambda _path: None
            for phase in self.acquirer["ACQUISITION_PHASES"]:
                with self.subTest(phase=phase):
                    sink = Sink()
                    self.acquirer["sys"] = types.SimpleNamespace(stderr=sink)

                    def fail_in_phase(selected=phase):
                        self.acquirer["CURRENT_PHASE"] = selected
                        raise RuntimeError("BARE_CLASSIFICATION_SECRET")

                    self.acquirer["acquire"] = fail_in_phase
                    with self.assertRaises(SystemExit) as raised:
                        self.acquirer["main"]()
                    self.assertEqual(raised.exception.code, 1)
                    self.assertEqual(
                        sink.buffer.getvalue(),
                        self.acquirer["ACQUISITION_DIAGNOSTICS"][phase],
                    )
                    self.assertNotIn(b"BARE_CLASSIFICATION_SECRET", sink.buffer.getvalue())

            sink = Sink()
            self.acquirer["sys"] = types.SimpleNamespace(stderr=sink)

            def fail_unknown_phase():
                self.acquirer["CURRENT_PHASE"] = "non-allowlisted-secret-phase"
                raise RuntimeError("BARE_UNKNOWN_SECRET")

            self.acquirer["acquire"] = fail_unknown_phase
            with self.assertRaises(SystemExit) as raised:
                self.acquirer["main"]()
            self.assertEqual(raised.exception.code, 1)
            self.assertEqual(sink.buffer.getvalue(), self.acquirer["UNKNOWN_DIAGNOSTIC"])
            self.assertNotIn(b"BARE_UNKNOWN_SECRET", sink.buffer.getvalue())
        finally:
            self.acquirer.update(original)

    def test_preexisting_residue_and_cleanup_identity_drift_fail_closed(self):
        original = {name: self.acquirer[name] for name in ("os", "shutil")}
        path = self.acquirer["SESSION_ROOT"]
        try:
            self.acquirer["os"] = types.SimpleNamespace(
                lstat=lambda target: (_ for _ in ()).throw(FileNotFoundError(target))
            )
            self.acquirer["require_absent"](path)
            metadata = types.SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o755,
                st_uid=0,
                st_gid=0,
                st_dev=1,
                st_ino=3,
            )
            self.acquirer["os"] = types.SimpleNamespace(lstat=lambda target: metadata)
            with self.assertRaises(self.error):
                self.acquirer["require_absent"](path)
            removed = []
            self.acquirer["OWNED_ROOTS"][path] = (1, 2)
            self.acquirer["shutil"] = types.SimpleNamespace(rmtree=lambda target: removed.append(target))
            with self.assertRaises(self.error):
                self.acquirer["cleanup_owned_root"](path)
            self.assertEqual(removed, [])
        finally:
            self.acquirer["OWNED_ROOTS"].clear()
            self.acquirer.update(original)

    def test_exact_supervisor_runs_only_through_in_process_runner_mocks(self):
        source = b"pass\n"
        digest = hashlib.sha256(source).hexdigest()
        original_os = self.supervisor["os"]
        original_run = subprocess.run
        supervise = self.supervisor["supervise"]
        original_defaults = supervise.__defaults__
        forbidden_runner = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("external child execution forbidden in behavior suite")
        )
        self.supervisor["os"] = types.SimpleNamespace(geteuid=lambda: 0)
        subprocess.run = forbidden_runner
        supervise.__defaults__ = (forbidden_runner,)
        unknown = self.supervisor["unknown"]
        diagnostics = dict(self.supervisor["diagnostics"])
        diagnostic = next(
            output for output, status in diagnostics.items() if status == 83
        )
        cleanup = self.supervisor["cleanup_diagnostic"]
        try:
            calls = []
            self.assertEqual(supervise(source, digest), (unknown, 81))

            def success_runner(args, **kwargs):
                calls.append((args, kwargs))
                return types.SimpleNamespace(returncode=0, stdout=b"")

            self.assertEqual(
                supervise(source, digest, success_runner),
                (b"", 0),
            )
            self.assertEqual(calls[0][0], ("/usr/bin/python3", "-I", "-c", "pass\n"))
            self.assertIs(calls[0][1]["stdin"], subprocess.DEVNULL)
            self.assertEqual(calls[0][1]["timeout"], 900)

            never_called = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("digest/length guard invoked child")
            )
            for bad_source, bad_digest in (
                (source, "0" * 64),
                (b"x" * 1048577, hashlib.sha256(b"x" * 1048577).hexdigest()),
            ):
                with self.subTest(boundary=(len(bad_source), bad_digest[:4])):
                    self.assertEqual(
                        supervise(bad_source, bad_digest, never_called),
                        (unknown, 81),
                    )

            failures = (
                lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("python", 900)),
                lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("python")),
                lambda *args, **kwargs: types.SimpleNamespace(returncode=0, stdout=b"extra"),
                lambda *args, **kwargs: types.SimpleNamespace(returncode=2, stdout=diagnostic),
                lambda *args, **kwargs: types.SimpleNamespace(returncode=1, stdout=b"BARE_SECRET"),
                lambda *args, **kwargs: types.SimpleNamespace(returncode=1, stdout=b"\x00" + diagnostic),
            )
            for runner in failures:
                with self.subTest(runner=runner):
                    output, status = supervise(source, digest, runner)
                    self.assertEqual((output, status), (unknown, 81))
                    self.assertNotIn(b"BARE_SECRET", output)

            nul_source = b"pass\x00\n"
            self.assertEqual(
                supervise(
                    nul_source,
                    hashlib.sha256(nul_source).hexdigest(),
                    lambda *args, **kwargs: types.SimpleNamespace(
                        returncode=1,
                        stdout=b"interpreter rejected NUL BARE_SECRET",
                    ),
                ),
                (unknown, 81),
            )
            for output, status, expected in (
                *(
                    (output, 1, (output, expected_status))
                    for output, expected_status in diagnostics.items()
                ),
                (cleanup, 1, (cleanup, 82)),
            ):
                self.assertEqual(
                    supervise(
                        source,
                        digest,
                        lambda *args, output=output, status=status, **kwargs: types.SimpleNamespace(
                            returncode=status,
                            stdout=output,
                        ),
                    ),
                    expected,
                )
            for output, expected_status in diagnostics.items():
                for bad_status in (0, 2, expected_status):
                    with self.subTest(expected_status=expected_status, bad_status=bad_status):
                        self.assertEqual(
                            supervise(
                                source,
                                digest,
                                lambda *args, output=output, bad_status=bad_status, **kwargs: types.SimpleNamespace(
                                    returncode=bad_status,
                                    stdout=output,
                                ),
                            ),
                            (unknown, 81),
                        )
                for malformed in (
                    output + b"\n",
                    output + b"\x00",
                    b"\x00" + output,
                    output[:-1],
                ):
                    with self.subTest(expected_status=expected_status, malformed=malformed[-4:]):
                        self.assertEqual(
                            supervise(
                                source,
                                digest,
                                lambda *args, malformed=malformed, **kwargs: types.SimpleNamespace(
                                    returncode=1,
                                    stdout=malformed,
                                ),
                            ),
                            (unknown, 81),
                        )
        finally:
            self.supervisor["os"] = original_os
            subprocess.run = original_run
            supervise.__defaults__ = original_defaults

    def test_exact_outer_mapping_rejects_every_cross_pair_and_extra_output(self):
        source = provisioner(
            ".github/workflows/actions-ci.yml",
            "hosted-compatibility-tests",
        )
        start = source.index('if [ "$acquirer_status" -ne 0 ] || [ -n "$acquirer_output" ]; then')
        unset = source.index("unset acquirer_output", start)
        end = source.rfind("\n", start, unset)
        mapping = source[start:end]
        unknown = (
            b"::error::trusted compatibility sandbox filesystem boundary unsafe "
            b"phase=unknown\n"
        )
        diagnostics = dict(self.supervisor["diagnostics"])
        diagnostics[self.supervisor["cleanup_diagnostic"]] = 82

        def run(status, output):
            fixture = (
                f"acquirer_status={status}\n"
                f"acquirer_output={shlex.quote(output.decode('ascii'))}\n"
                f"{mapping}\n"
            )
            return subprocess.run(
                ("/bin/bash", "-eu", "-o", "pipefail", "-c", fixture),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={},
                check=False,
                timeout=10,
            )

        success = run(0, b"")
        self.assertEqual((success.returncode, success.stdout, success.stderr), (0, b"", b""))
        for output, status in diagnostics.items():
            with self.subTest(status=status, exact=True):
                result = run(status, output.rstrip(b"\n"))
                self.assertEqual((result.returncode, result.stdout, result.stderr), (1, b"", output))
            wrong_status = 83 if status != 83 else 92
            for malformed_status, malformed_output in (
                (wrong_status, output.rstrip(b"\n")),
                (status, output.rstrip(b"\n") + b"-trailing"),
                (status, b"BARE_OUTER_SECRET"),
            ):
                with self.subTest(status=status, malformed_status=malformed_status):
                    result = run(malformed_status, malformed_output)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (1, b"", unknown))
                    self.assertNotIn(b"BARE_OUTER_SECRET", result.stderr)

    def test_behavior_suite_source_contains_no_privileged_invocation(self):
        source = Path(__file__).read_text(encoding="utf-8")
        forbidden = "su" + "do"
        self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
