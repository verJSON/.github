#!/usr/bin/env python3
"""Contracts the fixed Playwright system-library profile boundary."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import inspect
import json
import os
import select
import signal
import stat
import subprocess
import sys
import tempfile
import tarfile
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import yaml
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ci_build_artifacts
import ci_playwright_system_libraries as playwright_profile


PROFILE_ID = "playwright-chromium-1.63.0-ubuntu-24.04-amd64"


class PlaywrightSystemLibraryProfileTests(unittest.TestCase):
    def test_only_the_reviewed_profile_id_resolves(self) -> None:
        profile = playwright_profile.resolve_profile(PROFILE_ID)

        self.assertEqual(profile["playwrightVersion"], "1.63.0")
        for caller_value in (
            "",
            "playwright-chromium",
            "../../etc/passwd",
            "playwright-chromium-1.63.0-ubuntu-24.04-amd64;id",
        ):
            with self.subTest(caller_value=caller_value):
                with self.assertRaises(playwright_profile.PlaywrightProfileError):
                    playwright_profile.resolve_profile(caller_value)

    def test_profile_requires_github_hosted_ubuntu_24_04_amd64(self) -> None:
        valid_os_release = {"ID": "ubuntu", "VERSION_ID": "24.04"}

        playwright_profile.validate_runner_platform(
            valid_os_release,
            architecture="amd64",
            runner_environment="github-hosted",
        )
        for os_release, architecture, runner_environment in (
            ({"ID": "ubuntu", "VERSION_ID": "22.04"}, "amd64", "github-hosted"),
            ({"ID": "ubuntu", "VERSION_ID": "24.04"}, "arm64", "github-hosted"),
            ({"ID": "debian", "VERSION_ID": "24.04"}, "amd64", "github-hosted"),
            (valid_os_release, "amd64", "self-hosted"),
        ):
            with self.subTest(
                os_release=os_release,
                architecture=architecture,
                runner_environment=runner_environment,
            ):
                with self.assertRaises(playwright_profile.PlaywrightProfileError):
                    playwright_profile.validate_runner_platform(
                        os_release,
                        architecture=architecture,
                        runner_environment=runner_environment,
                    )

    def test_apt_plan_accepts_only_catalogued_exact_package_versions(self) -> None:
        profile = playwright_profile.resolve_profile(PROFILE_ID)
        package = profile["directPackages"][0]
        record = profile["packages"][package]
        plan = (
            f"Inst {package}:{record['architecture']} "
            f"({record['version']} Ubuntu:24.04/noble [{record['architecture']}])\n"
        )

        planned = playwright_profile.parse_install_plan(plan, profile)

        self.assertEqual(
            planned,
            {(package, record["architecture"]): record["version"]},
        )
        for unsafe_plan in (
            "Inst curl:amd64 (8.5.0-2ubuntu10 Ubuntu:24.04/noble [amd64])\n",
            f"Inst {package}:amd64 (999.0 Ubuntu:24.04/noble [amd64])\n",
            f"Remv {package}:amd64 [1.0]\n",
        ):
            with self.subTest(unsafe_plan=unsafe_plan):
                with self.assertRaises(playwright_profile.PlaywrightProfileError):
                    playwright_profile.parse_install_plan(unsafe_plan, profile)

    def test_apt_plan_accepts_empty_simulation_annotations(self) -> None:
        profile = playwright_profile.resolve_profile(PROFILE_ID)
        package = "libgcc-s1"
        record = profile["packages"][package]
        expected = {(package, record["architecture"]): record["version"]}
        rows = (
            f"Inst {package} [] ({record['version']} Ubuntu:24.04/noble [amd64])\n",
            f"Inst {package} ({record['version']} Ubuntu:24.04/noble) []\n",
            f"Inst {package} ({record['version']} Ubuntu:24.04/noble [amd64]) []\n",
        )

        for row in rows:
            with self.subTest(row=row):
                self.assertEqual(
                    playwright_profile.parse_install_plan(row, profile),
                    expected,
                )

    def test_apt_plan_validates_inner_architecture_annotations(self) -> None:
        profile = playwright_profile.resolve_profile(PROFILE_ID)
        amd64 = profile["packages"]["libgcc-s1"]
        all_arch = profile["packages"]["at-spi2-common"]
        valid_rows = (
            (
                f"Inst libgcc-s1 ({amd64['version']} Ubuntu:24.04/noble [amd64])\n",
                ("libgcc-s1", "amd64"),
                amd64["version"],
            ),
            (
                f"Inst at-spi2-common ({all_arch['version']} Ubuntu:24.04/noble [all])\n",
                ("at-spi2-common", "all"),
                all_arch["version"],
            ),
            (
                f"Inst libgcc-s1 ({amd64['version']} Ubuntu:24.04/noble)\n",
                ("libgcc-s1", "amd64"),
                amd64["version"],
            ),
        )
        for row, package_key, version in valid_rows:
            with self.subTest(row=row):
                self.assertEqual(
                    playwright_profile.parse_install_plan(row, profile),
                    {package_key: version},
                )

        invalid_rows = (
            f"Inst libgcc-s1 ({amd64['version']} Ubuntu:24.04/noble [arm64])\n",
            f"Inst libgcc-s1 ({amd64['version']} Ubuntu:24.04/noble [amd64)\n",
            f"Inst libgcc-s1 ({amd64['version']} Ubuntu:24.04/noble amd64])\n",
            f"Inst libgcc-s1 ({amd64['version']} Ubuntu:24.04/noble [amd64] extra)\n",
            f"Inst libgcc-s1 ({amd64['version']} Ubuntu:24.04/noble [])\n",
        )
        for row in invalid_rows:
            with self.subTest(row=row):
                with self.assertRaises(playwright_profile.PlaywrightProfileError):
                    playwright_profile.parse_install_plan(row, profile)

    def test_actual_install_plan_rejects_dpkg_but_complete_closure_can_contain_it(self) -> None:
        profile = {
            "packages": {
                "dpkg": {"architecture": "amd64", "version": "1.0"},
            },
        }
        row = "Inst dpkg:amd64 (1.0 Ubuntu:24.04/noble [amd64])\n"
        planned = playwright_profile.parse_install_plan(row, profile)
        with self.assertRaisesRegex(playwright_profile.PlaywrightProfileError, "mutate the dpkg package manager"):
            playwright_profile._reject_dpkg_install_plan(planned)

        complete_closure = playwright_profile.parse_install_plan(row, profile)
        self.assertEqual(complete_closure, {("dpkg", "amd64"): "1.0"})
        playwright_profile._reject_dpkg_install_plan({})

        tree = ast.parse(inspect.getsource(playwright_profile.provision))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_reject_dpkg_install_plan"
        ]
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0].args[0], ast.Name)
        self.assertEqual(calls[0].args[0].id, "planned")

    def test_global_dpkg_configuration_rejects_hook_and_punctuation_forms(self) -> None:
        hook_fixtures = (
            b"pre-invoke = touch /tmp/verjson-dpkg-pre\n",
            b"post-invoke /tmp/verjson-dpkg-post\n",
            b"status-logger=(touch /tmp/verjson-dpkg-status)\n",
            b"status-logger,=(touch /tmp/verjson-dpkg-punctuation)\n",
            b"--pre-invoke=touch /tmp/verjson-dpkg-option\n",
        )
        for fixture in hook_fixtures:
            with self.subTest(fixture=fixture):
                snapshot = {
                    "main": {"contents": fixture},
                    "fragments": {},
                }
                with self.assertRaises(playwright_profile.PlaywrightProfileError):
                    playwright_profile._validate_global_dpkg_config(snapshot)

    def test_global_dpkg_configuration_allows_only_known_benign_directives(self) -> None:
        snapshot = {
            "main": {
                "contents": b"# dpkg config\nno-debsig\nlog /var/log/dpkg.log\n",
            },
            "fragments": {},
        }
        self.assertFalse(playwright_profile._validate_global_dpkg_config(snapshot))

    def test_dpkg_parser_checks_raw_physical_line_bytes_before_comments_or_trimming(self) -> None:
        valid_at_bound = b"no-debsig" + b" " * (playwright_profile.MAX_DPKG_CONFIG_LINE_BYTES - 1 - len(b"no-debsig"))
        self.assertEqual(
            playwright_profile._parse_dpkg_config(valid_at_bound, "fixture"),
            [("no-debsig", "")],
        )
        oversized_comment = b"#" + b"x" * (playwright_profile.MAX_DPKG_CONFIG_LINE_BYTES - 1)
        for contents in (oversized_comment, oversized_comment + b"\n"):
            with self.subTest(newline=contents.endswith(b"\n")):
                with self.assertRaises(playwright_profile.PlaywrightProfileError):
                    playwright_profile._parse_dpkg_config(contents, "fixture")

    def test_dpkg_parser_rejects_boolean_quoting_and_differential_syntax(self) -> None:
        for contents in (
            b'"no-debsig"\n',
            b'no-debsig ""\n',
            b"no-debsig=\n",
            b'no-debsig=""\n',
            b"no-debsig += enabled\n",
            b"log += /var/log/dpkg.log\n",
            b"status-logger += (command)\n",
            b"status-logger -= (command)\n",
            b"status-logger == (command)\n",
        ):
            with self.subTest(contents=contents):
                with self.assertRaises(playwright_profile.PlaywrightProfileError):
                    playwright_profile._parse_dpkg_config(contents, "fixture")

    def test_directory_snapshot_ignores_mutable_directory_counters_only(self) -> None:
        first = SimpleNamespace(
            st_dev=1,
            st_ino=2,
            st_mode=0o40755,
            st_uid=0,
            st_gid=0,
            st_nlink=2,
            st_size=32,
            st_mtime_ns=10,
            st_ctime_ns=11,
        )
        changed_counters = SimpleNamespace(
            st_dev=1,
            st_ino=2,
            st_mode=0o40755,
            st_uid=0,
            st_gid=0,
            st_nlink=3,
            st_size=48,
            st_mtime_ns=20,
            st_ctime_ns=21,
        )
        self.assertEqual(
            playwright_profile._directory_identity(first),
            playwright_profile._directory_identity(changed_counters),
        )
        changed_owner = SimpleNamespace(**{**vars(changed_counters), "st_uid": 1})
        self.assertNotEqual(
            playwright_profile._directory_identity(first),
            playwright_profile._directory_identity(changed_owner),
        )

    def test_only_the_exact_needrestart_status_logger_fragment_is_admitted(self) -> None:
        contents = b"status-logger=(verified package hook)\n"
        snapshot = {
            "main": None,
            "fragments": {"needrestart": {"contents": contents}},
        }
        self.assertTrue(playwright_profile._validate_global_dpkg_config(snapshot))
        for name in ("99-needrestart", "needrestart.conf"):
            with self.subTest(name=name):
                snapshot["fragments"] = {name: {"contents": contents}}
                with self.assertRaises(playwright_profile.PlaywrightProfileError):
                    playwright_profile._validate_global_dpkg_config(snapshot)

        snapshot["fragments"] = {
            "needrestart": {
                "contents": b"status-logger=(verified package hook)\nno-debsig\n",
            },
        }
        with self.assertRaises(playwright_profile.PlaywrightProfileError):
            playwright_profile._validate_global_dpkg_config(snapshot)

    def test_global_dpkg_configuration_rejects_unknown_and_malformed_directives(self) -> None:
        for fixture in (
            b"force-confold\n",
            b"path-exclude=/usr/share/doc/*\n",
            b"force-confold \"unterminated\n",
            b"status-logger::=touch /tmp/hook\n",
        ):
            with self.subTest(fixture=fixture):
                snapshot = {"main": {"contents": fixture}, "fragments": {}}
                with self.assertRaises(playwright_profile.PlaywrightProfileError):
                    playwright_profile._validate_global_dpkg_config(snapshot)

    def test_fragment_selection_matches_dpks_exact_shell_glob(self) -> None:
        accepted = ("0", "10local", "A", "_", "-local", "NeedRestart_1")
        rejected = (".hidden", " dpkg.cfg", "needrestart.conf", "bad/name", "")
        for name in accepted:
            with self.subTest(name=name):
                self.assertTrue(playwright_profile._matches_dpkg_config_filename(name))
        for name in rejected:
            with self.subTest(name=name):
                self.assertFalse(playwright_profile._matches_dpkg_config_filename(name))

    def test_global_dpkg_scan_reads_only_matching_regular_fragments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            dpkg = root / "etc" / "dpkg"
            fragments = dpkg / "dpkg.cfg.d"
            fragments.mkdir(parents=True)
            (dpkg / "dpkg.cfg").write_text("no-debsig\n", encoding="ascii")
            (fragments / "10-safe").write_text("log=/var/log/dpkg.log\n", encoding="ascii")
            (fragments / ".ignored").write_text("status-logger=ignored\n", encoding="ascii")
            for file_path in (dpkg / "dpkg.cfg", fragments / "10-safe", fragments / ".ignored"):
                file_path.chmod(0o644)
            for directory in (root / "etc", root / "etc" / "dpkg", fragments):
                directory.chmod(0o755)
            snapshot = playwright_profile._scan_global_dpkg_config(root, owner_uid=os.geteuid())
        self.assertEqual(set(snapshot["fragments"]), {"10-safe"})
        self.assertEqual(snapshot["main"]["contents"], b"no-debsig\n")
        self.assertFalse(playwright_profile._validate_global_dpkg_config(snapshot))

    def test_global_dpkg_scan_rejects_matching_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fragments = root / "etc" / "dpkg" / "dpkg.cfg.d"
            fragments.mkdir(parents=True)
            outside = root / "outside"
            outside.write_text("no-debsig\n", encoding="ascii")
            (fragments / "10-link").symlink_to(outside)
            with self.assertRaises(playwright_profile.PlaywrightProfileError):
                for directory in (root / "etc", root / "etc" / "dpkg", fragments):
                    directory.chmod(0o755)
                playwright_profile._scan_global_dpkg_config(root, owner_uid=os.geteuid())

    def test_private_dpkg_view_suppresses_only_verified_needrestart_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            snapshot = {
                "main": {"contents": b"no-debsig\n"},
                "fragments": {
                    "10-safe": {"contents": b"log /var/log/dpkg.log\n"},
                    "needrestart": {"contents": b"status-logger=(verified)\n"},
                },
            }
            with patch.object(playwright_profile.os, "chown"), patch.object(playwright_profile.os, "fchown"):
                view = playwright_profile._make_dpkg_config_view(root, snapshot, suppress_needrestart=True)
            self.assertEqual((view / "dpkg.cfg").read_bytes(), b"no-debsig\n")
            self.assertEqual(
                sorted(path.name for path in (view / "dpkg.cfg.d").iterdir()),
                ["10-safe"],
            )
            self.assertEqual(
                (view / "dpkg.cfg.d" / "10-safe").read_bytes(),
                b"log /var/log/dpkg.log\n",
            )

    def test_needrestart_archive_member_is_read_without_extracting(self) -> None:
        fragment = b"status-logger=(trusted)\n"
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:") as package_tar:
            member = tarfile.TarInfo("./etc/dpkg/dpkg.cfg.d/needrestart")
            member.mode = 0o644
            member.uid = 0
            member.gid = 0
            member.size = len(fragment)
            package_tar.addfile(member, io.BytesIO(fragment))
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "needrestart.deb"
            archive_path.touch()
            with patch.object(playwright_profile, "_run", return_value=archive.getvalue()):
                self.assertEqual(
                    playwright_profile._needrestart_payload_from_archive(archive_path),
                    fragment,
                )

    def test_needrestart_hook_is_suppressed_only_when_archive_payload_matches(self) -> None:
        version = "1.0-1ubuntu1"
        package_bytes = b"exact signed archive bytes"
        package_fragment = b"status-logger=(verified package hook)\n"
        record = {
            "package": "needrestart",
            "architecture": "all",
            "version": version,
            "size": len(package_bytes),
            "sha256": hashlib.sha256(package_bytes).hexdigest(),
            "filename": "pool/universe/n/needrestart.deb",
        }
        archive_tar = io.BytesIO()
        with tarfile.open(fileobj=archive_tar, mode="w:") as package_tar:
            member = tarfile.TarInfo("etc/dpkg/dpkg.cfg.d/needrestart")
            member.mode = 0o644
            member.uid = 0
            member.gid = 0
            member.size = len(package_fragment)
            package_tar.addfile(member, io.BytesIO(package_fragment))
        tar_bytes = archive_tar.getvalue()

        for installed_fragment, accepted in (
            (package_fragment, True),
            (b"status-logger=(changed hook)\n", False),
        ):
            with self.subTest(accepted=accepted), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                archive_name = playwright_profile._apt_cache_filename("needrestart", version, "all")
                archive_path = root / "needrestart-archive" / archive_name
                real_lstat = os.lstat

                def run_package_command(argv: tuple[str, ...] | list[str], **kwargs: object) -> bytes:
                    if argv[0] == "/usr/bin/apt-get":
                        archive_path.parent.mkdir(mode=0o700, exist_ok=True)
                        archive_path.write_bytes(package_bytes)
                        archive_path.chmod(0o644)
                        return b""
                    if argv[:2] == ("/usr/bin/dpkg-deb", "--fsys-tarfile"):
                        return tar_bytes
                    raise AssertionError(f"unexpected trusted command: {argv!r}")

                def lstat_with_root_owned_archive(path: str | os.PathLike[str]) -> os.stat_result | SimpleNamespace:
                    metadata = real_lstat(path)
                    if Path(path) == archive_path:
                        return SimpleNamespace(
                            st_mode=metadata.st_mode,
                            st_size=metadata.st_size,
                            st_nlink=metadata.st_nlink,
                            st_uid=0,
                        )
                    return metadata

                fragment = {
                    "contents": installed_fragment,
                    "metadata": (1, 2, 0o100644, 0, 0, 1, len(installed_fragment), 0, 0),
                }
                commands = (
                    patch.object(playwright_profile, "_run", side_effect=run_package_command),
                    patch.object(
                        playwright_profile,
                        "_exact_output",
                        return_value=(
                            f"Package: needrestart\nVersion: {version}\nArchitecture: all\n".encode("ascii")
                        ),
                    ),
                    patch.object(playwright_profile, "_needrestart_package_version", return_value=version),
                    patch.object(playwright_profile.os, "chown"),
                    patch.object(playwright_profile.os, "lstat", side_effect=lstat_with_root_owned_archive),
                )
                with commands[0], commands[1], commands[2], commands[3], commands[4]:
                    if accepted:
                        playwright_profile._authenticate_needrestart_fragment(
                            root,
                            fragment,
                            {("needrestart", "all", version): record},
                            [],
                            playwright_profile.SAFE_ENV,
                        )
                    else:
                        with self.assertRaises(playwright_profile.PlaywrightProfileError):
                            playwright_profile._authenticate_needrestart_fragment(
                                root,
                                fragment,
                                {("needrestart", "all", version): record},
                                [],
                                playwright_profile.SAFE_ENV,
                            )

    def test_global_dpkg_hooks_are_checked_immediately_before_offline_install(self) -> None:
        tree = ast.parse(inspect.getsource(playwright_profile.provision))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        install_call = next(
            node
            for node in calls
            if isinstance(node.func, ast.Name)
            and node.func.id == "_run_dpkg_install_in_namespace"
        )
        hook_check = next(
            node
            for node in calls
            if isinstance(node.func, ast.Name) and node.func.id == "_validate_global_dpkg_config"
        )
        self.assertLess(hook_check.lineno, install_call.lineno)
        self.assertIn("_scan_global_dpkg_config()", inspect.getsource(playwright_profile.provision))

    def test_provisioning_uses_a_private_empty_home_for_dpkg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            home = root / "home"
            home.mkdir()
            home.chmod(0o555)
            paths = {
                "root": root,
                "home": home,
                "configparts": root / "configparts",
                "apt_config": root / "apt.conf",
            }
            paths["configparts"].mkdir()
            paths["configparts"].chmod(0o500)
            paths["apt_config"].write_bytes(
                playwright_profile._apt_config_contents(paths["configparts"])
            )
            paths["apt_config"].chmod(0o400)
            with patch.object(playwright_profile, "_validate_apt_config", return_value=paths["apt_config"]):
                environment = playwright_profile._apt_environment(paths)
        self.assertEqual(environment["HOME"], str(home))
        self.assertNotEqual(environment["HOME"], "/root")

    def test_private_apt_config_redirects_before_host_config_hooks_load(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            host_parts = root / "host-parts"
            private_parts = root / "private-parts"
            host_parts.mkdir()
            private_parts.mkdir()
            (host_parts / "99-host-hook.conf").write_text(
                'DPkg::Pre-Invoke { "echo VERJSON_HOST_APT_HOOK"; };\n',
                encoding="utf-8",
            )
            host_main = root / "host-main.conf"
            host_main.write_text(
                'Test::HostMain "VERJSON_HOST_APT_MAIN";\n',
                encoding="utf-8",
            )
            apt_config = root / "apt.conf"
            apt_config.write_bytes(
                (
                    f'Dir::Etc::Parts "{host_parts}";\n'
                    f'Dir::Etc::main "{host_main}";\n'
                ).encode("ascii")
                + playwright_profile._apt_config_contents(private_parts)
            )
            environment = {**playwright_profile.SAFE_ENV, "APT_CONFIG": str(apt_config)}

            def apt_config_output(*arguments: str) -> str:
                result = subprocess.run(
                    ["/usr/bin/apt-config", *arguments],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=environment,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stdout.decode("utf-8", "replace"))
                return result.stdout.decode("utf-8", "strict")

            self.assertEqual(
                apt_config_output("shell", "APT_PARTS", "Dir::Etc::Parts"),
                f"APT_PARTS='{private_parts}'\n",
            )
            self.assertEqual(
                apt_config_output("shell", "APT_MAIN", "Dir::Etc::main"),
                "APT_MAIN='/dev/null'\n",
            )
            dump = apt_config_output("dump")
            self.assertNotIn("VERJSON_HOST_APT_HOOK", dump)
            self.assertNotIn("VERJSON_HOST_APT_MAIN", dump)

    def test_apt_environment_uses_the_validated_config_path(self) -> None:
        paths = {
            "apt_config": Path("/var/tmp/verjson-playwright-apt/apt.conf"),
            "home": Path("/var/tmp/verjson-playwright-apt/home"),
        }
        with patch.object(
            playwright_profile,
            "_validate_apt_config",
            return_value=paths["apt_config"],
        ) as validate:
            environment = playwright_profile._apt_environment(paths)

        validate.assert_called_once_with(paths)
        self.assertEqual(
            environment,
            {
                **playwright_profile.SAFE_ENV,
                "HOME": str(paths["home"]),
                "APT_CONFIG": str(paths["apt_config"]),
            },
        )

    def test_apt_config_loading_calls_use_the_validated_private_environment(self) -> None:
        for function in (playwright_profile._read_package_indexes, playwright_profile.provision):
            with self.subTest(function=function.__name__):
                tree = ast.parse(inspect.getsource(function))
                calls = [
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_run"
                ]
                self.assertTrue(calls)
                for call in calls:
                    self.assertTrue(
                        any(
                            keyword.arg == "env"
                            and isinstance(keyword.value, ast.Name)
                            and keyword.value.id == "apt_environment"
                            for keyword in call.keywords
                        ),
                    )

    def test_receipt_covers_the_complete_closure_and_rejects_transitive_drift(self) -> None:
        profile = playwright_profile.resolve_profile(PROFILE_ID)
        package_versions = {
            (name, record["architecture"]): record["version"]
            for name, record in profile["packages"].items()
        }
        receipt = playwright_profile.create_receipt(PROFILE_ID, package_versions)

        self.assertEqual(len(receipt["packages"]), len(profile["packages"]))
        playwright_profile.verify_receipt(receipt, PROFILE_ID, package_versions)

        transitive_package = next(
            name for name in profile["packages"] if name not in profile["directPackages"]
        )
        transitive = profile["packages"][transitive_package]
        changed = dict(package_versions)
        changed[(transitive_package, transitive["architecture"])] = "0:changed"
        with self.assertRaises(playwright_profile.PlaywrightProfileError):
            playwright_profile.create_receipt(PROFILE_ID, changed)

        direct_only = {
            key: version
            for key, version in package_versions.items()
            if key[0] in profile["directPackages"]
        }
        with self.assertRaises(playwright_profile.PlaywrightProfileError):
            playwright_profile.create_receipt(PROFILE_ID, direct_only)

    def test_installed_state_requires_every_transitive_package_at_its_pinned_version(self) -> None:
        profile = playwright_profile.resolve_profile(PROFILE_ID)
        installed = {
            (name, record["architecture"]): ("ii ", record["version"])
            for name, record in profile["packages"].items()
        }
        expected = {
            (name, record["architecture"]): record["version"]
            for name, record in profile["packages"].items()
        }
        self.assertEqual(playwright_profile._installed_profile_closure(profile, installed), expected)

        transitive_package = next(
            name for name in profile["packages"] if name not in profile["directPackages"]
        )
        transitive = profile["packages"][transitive_package]
        installed[(transitive_package, transitive["architecture"])] = ("ii ", "0:changed")
        with self.assertRaises(playwright_profile.PlaywrightProfileError):
            playwright_profile._installed_profile_closure(profile, installed)

    def test_dpkg_deb_control_fields_parse_labeled_output(self) -> None:
        output = "Package: libexample1\nVersion: 1:2.3-4\nArchitecture: amd64\n"
        self.assertEqual(
            playwright_profile._parse_deb_control_fields(output),
            {
                "Package": "libexample1",
                "Version": "1:2.3-4",
                "Architecture": "amd64",
            },
        )
        with self.assertRaises(playwright_profile.PlaywrightProfileError):
            playwright_profile._parse_deb_control_fields(
                output + "Version: 1:2.3-4\n"
            )

    def test_apt_cache_filename_encodes_epochs(self) -> None:
        self.assertEqual(
            playwright_profile._apt_cache_filename("libexample1", "1:2.3-4", "amd64"),
            "libexample1_1%3a2.3-4_amd64.deb",
        )
        self.assertEqual(
            playwright_profile._apt_cache_filename("libexample1", "2.3-4", "amd64"),
            "libexample1_2.3-4_amd64.deb",
        )

    def test_archive_cache_validates_apt_lock_and_rejects_unexpected_entries(self) -> None:
        profile = playwright_profile.resolve_profile(PROFILE_ID)
        record = next(
            item for item in profile["packages"].values() if ":" in item["version"]
        )
        package = record["package"]
        planned = {(package, record["architecture"]): record["version"]}

        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_dir = Path(temporary_directory)
            archive = archive_dir / playwright_profile._apt_cache_filename(
                package, record["version"], record["architecture"]
            )
            archive.touch()
            lock = archive_dir / "lock"
            lock.touch()
            lock.chmod(0o640)
            with patch.object(playwright_profile, "_verify_archive") as verify_archive:
                playwright_profile._validate_archive_cache(
                    {"archives": archive_dir}, planned, profile
                )
            verify_archive.assert_called_once_with(archive, package, record)

            lock.unlink()
            lock.symlink_to(archive)
            with patch.object(playwright_profile, "_verify_archive"):
                with self.assertRaises(playwright_profile.PlaywrightProfileError):
                    playwright_profile._validate_archive_cache(
                        {"archives": archive_dir}, planned, profile
                    )

            lock.unlink()
            (archive_dir / "unexpected.deb").touch()
            with patch.object(playwright_profile, "_verify_archive"):
                with self.assertRaises(playwright_profile.PlaywrightProfileError):
                    playwright_profile._validate_archive_cache(
                        {"archives": archive_dir}, planned, profile
                    )

    def test_namespace_install_is_fixed_private_and_no_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config_view = root / "dpkg-view"
            (config_view / "dpkg.cfg.d").mkdir(parents=True)
            config_view.chmod(0o555)
            env = {
                **playwright_profile.SAFE_ENV,
                "HOME": str(root / "home"),
                "APT_CONFIG": str(root / "apt.conf"),
            }
            install_argv = [
                "/usr/bin/apt-get",
                "--no-download",
                "--no-install-recommends",
                "--no-remove",
                "--yes",
                "install",
                "libfixture=1.0",
            ]
            real_lstat = os.lstat

            def lstat_with_root_owned_view(path: str | os.PathLike[str]) -> os.stat_result | SimpleNamespace:
                metadata = real_lstat(path)
                if Path(path) == config_view and os.geteuid() != 0:
                    return SimpleNamespace(st_mode=metadata.st_mode, st_uid=0)
                return metadata

            with (
                patch.object(playwright_profile.os, "lstat", side_effect=lstat_with_root_owned_view),
                patch.object(playwright_profile, "_run", return_value=b"") as run,
            ):
                playwright_profile._run_dpkg_install_in_namespace(
                    install_argv,
                    config_view,
                    env,
                    timeout=600,
                )
        namespace_argv = run.call_args.args[0]
        for flag in (
            "/usr/bin/unshare",
            "--mount",
            "--pid",
            "--fork",
            "--kill-child",
            "--propagation",
            "private",
            "--mount-proc=/proc",
        ):
            self.assertIn(flag, namespace_argv)
        self.assertIn("mount(source_bytes, target_bytes", playwright_profile._NAMESPACE_MOUNT_SCRIPT)
        self.assertIn("os.execve(apt_argv[0]", playwright_profile._NAMESPACE_MOUNT_SCRIPT)
        self.assertEqual(list(namespace_argv[-len(install_argv):]), install_argv)

    def test_receipt_publication_rolls_back_signals_before_the_commit_cutoff(self) -> None:
        boundaries = (
            ("open", signal.SIGINT),
            ("write", signal.SIGTERM),
            ("fsync", signal.SIGINT),
            ("close", signal.SIGTERM),
            ("link", signal.SIGINT),
        )
        receipt = {"profileId": PROFILE_ID, "packages": []}
        for boundary, signum in boundaries:
            with self.subTest(boundary=boundary, signal=signum):
                with tempfile.TemporaryDirectory() as temporary_directory, contextlib.ExitStack() as cleanup:
                    directory = Path(temporary_directory)
                    final_path = directory / "receipt.json"
                    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                    cleanup.callback(os.close, directory_fd)
                    previous_handlers = {
                        item: signal.getsignal(item)
                        for item in (signal.SIGINT, signal.SIGTERM)
                    }
                    previous_mask = signal.pthread_sigmask(
                        signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM}
                    )
                    signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
                    original_open = os.open
                    original_method = getattr(os, boundary)
                    stage_descriptor: int | None = None
                    injected = False

                    def inject() -> None:
                        nonlocal injected
                        if not injected:
                            injected = True
                            os.kill(os.getpid(), signum)

                    def open_wrapper(path: str | os.PathLike[str], *args: Any, **kwargs: Any) -> int:
                        nonlocal stage_descriptor
                        descriptor = original_open(path, *args, **kwargs)
                        if str(path).startswith(".receipt.json.stage-"):
                            stage_descriptor = descriptor
                            if boundary == "open":
                                inject()
                        return descriptor

                    def method_wrapper(*args: Any, **kwargs: Any) -> Any:
                        result = original_method(*args, **kwargs)
                        if boundary == "write" and not injected:
                            inject()
                        elif boundary == "fsync" and not injected and stat.S_ISREG(os.fstat(args[0]).st_mode):
                            inject()
                        elif boundary == "close" and not injected and args[0] == stage_descriptor:
                            inject()
                        elif boundary == "link" and not injected:
                            inject()
                        return result

                    with self.assertRaisesRegex(playwright_profile.PlaywrightProfileError, "cancelled by"):
                        with playwright_profile.OperationLifecycle() as operation:
                            with contextlib.ExitStack() as stack:
                                stack.enter_context(patch.object(playwright_profile.os, "open", side_effect=open_wrapper))
                                if boundary != "open":
                                    stack.enter_context(
                                        patch.object(playwright_profile.os, boundary, side_effect=method_wrapper)
                                    )
                                playwright_profile._publish_receipt_in_directory(
                                    directory_fd, final_path.name, receipt, operation
                                )
                    self.assertTrue(injected)
                    self.assertFalse(final_path.exists(), "precommit cancellation left a visible receipt")
                    self.assertEqual(list(directory.glob(".receipt.json.stage-*")), [])
                    self.assertEqual(
                        {item: signal.getsignal(item) for item in previous_handlers},
                        previous_handlers,
                    )
                    current_mask = signal.pthread_sigmask(
                        signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM}
                    )
                    signal.pthread_sigmask(signal.SIG_SETMASK, current_mask)
                    self.assertEqual(
                        current_mask & {signal.SIGINT, signal.SIGTERM},
                        previous_mask & {signal.SIGINT, signal.SIGTERM},
                    )

    def test_receipt_publication_rolls_back_failures_after_open_write_fsync_close_and_link(self) -> None:
        receipt = {"profileId": PROFILE_ID, "packages": []}
        for boundary in ("open", "write", "fsync", "close", "link"):
            with self.subTest(boundary=boundary):
                with tempfile.TemporaryDirectory() as temporary_directory, contextlib.ExitStack() as cleanup:
                    directory = Path(temporary_directory)
                    final_path = directory / "receipt.json"
                    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                    cleanup.callback(os.close, directory_fd)
                    original_open = os.open
                    original_method = getattr(os, boundary)
                    stage_descriptor: int | None = None
                    injected = False

                    def open_wrapper(path: str | os.PathLike[str], *args: Any, **kwargs: Any) -> int:
                        nonlocal stage_descriptor
                        descriptor = original_open(path, *args, **kwargs)
                        if str(path).startswith(".receipt.json.stage-"):
                            stage_descriptor = descriptor
                        return descriptor

                    def fail_after_operation(*args: Any, **kwargs: Any) -> Any:
                        nonlocal injected
                        result = original_method(*args, **kwargs)
                        should_fail = (
                            boundary == "write"
                            or boundary == "link"
                            or (boundary == "fsync" and stat.S_ISREG(os.fstat(args[0]).st_mode))
                            or (boundary == "close" and args[0] == stage_descriptor)
                        ) and not injected
                        if should_fail:
                            injected = True
                            raise OSError(f"injected failure after {boundary}")
                        return result

                    with self.assertRaisesRegex(OSError, "injected failure"):
                        with playwright_profile.OperationLifecycle() as operation:
                            with contextlib.ExitStack() as stack:
                                stack.enter_context(patch.object(playwright_profile.os, "open", side_effect=open_wrapper))
                                if boundary == "open":
                                    def fail_after_open_record(publication: Any, descriptor: int) -> None:
                                        nonlocal injected
                                        original_record(publication, descriptor)
                                        injected = True
                                        raise OSError("injected failure after open")

                                    original_record = playwright_profile._ReceiptPublication.remember_staging_file
                                    stack.enter_context(
                                        patch.object(
                                            playwright_profile._ReceiptPublication,
                                            "remember_staging_file",
                                            new=fail_after_open_record,
                                        )
                                    )
                                else:
                                    stack.enter_context(
                                        patch.object(playwright_profile.os, boundary, side_effect=fail_after_operation)
                                    )
                                playwright_profile._publish_receipt_in_directory(
                                    directory_fd, final_path.name, receipt, operation
                                )

                    self.assertTrue(injected)
                    self.assertFalse(final_path.exists(), "precommit failure left a visible receipt")
                    self.assertEqual(list(directory.glob(".receipt.json.stage-*")), [])

    def test_receipt_publication_cancels_before_directory_sync_and_during_commit(self) -> None:
        receipt = {"profileId": PROFILE_ID, "packages": []}
        for signum in (signal.SIGINT, signal.SIGTERM):
            for injection_point in ("before-directory-sync", "during-commit-mask"):
                with self.subTest(signal=signal.Signals(signum).name, point=injection_point):
                    with tempfile.TemporaryDirectory() as temporary_directory:
                        directory = Path(temporary_directory)
                        final_path = directory / "receipt.json"
                        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                        original_fsync = os.fsync
                        original_pthread_sigmask = signal.pthread_sigmask
                        injected = False

                        def interrupt_before_directory_sync(descriptor: int) -> None:
                            nonlocal injected
                            if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not injected:
                                injected = True
                                os.kill(os.getpid(), signum)
                            original_fsync(descriptor)

                        def interrupt_during_commit_mask(how: int, mask: Any) -> Any:
                            nonlocal injected
                            previous = original_pthread_sigmask(how, mask)
                            if (
                                how == signal.SIG_BLOCK
                                and set(mask) == playwright_profile._CANCELLATION_SIGNALS
                                and final_path.exists()
                                and not injected
                            ):
                                injected = True
                                os.kill(os.getpid(), signum)
                            return previous

                        try:
                            with self.assertRaisesRegex(
                                playwright_profile.PlaywrightProfileError, "cancelled by"
                            ):
                                with playwright_profile.OperationLifecycle() as operation:
                                    with contextlib.ExitStack() as patches:
                                        if injection_point == "before-directory-sync":
                                            patches.enter_context(
                                                patch.object(
                                                    playwright_profile.os,
                                                    "fsync",
                                                    side_effect=interrupt_before_directory_sync,
                                                )
                                            )
                                        else:
                                            patches.enter_context(
                                                patch.object(
                                                    playwright_profile.signal,
                                                    "pthread_sigmask",
                                                    side_effect=interrupt_during_commit_mask,
                                                )
                                            )
                                        playwright_profile._publish_receipt_in_directory(
                                            directory_fd, final_path.name, receipt, operation
                                        )
                            self.assertTrue(injected)
                            self.assertFalse(final_path.exists())
                            self.assertEqual(list(directory.glob(".receipt.json.stage-*")), [])
                        finally:
                            os.close(directory_fd)

    def test_receipt_publication_ignores_signals_after_durable_commit_and_preserves_collisions(self) -> None:
        receipt = {"profileId": PROFILE_ID, "packages": []}
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signal.Signals(signum).name):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    directory = Path(temporary_directory)
                    final_path = directory / "receipt.json"
                    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                    original_mark = playwright_profile.OperationLifecycle.mark_receipt_published
                    injected = False

                    def interrupt_after_commit(operation: Any) -> None:
                        nonlocal injected
                        original_mark(operation)
                        if not injected:
                            injected = True
                            os.kill(os.getpid(), signum)

                    try:
                        with playwright_profile.OperationLifecycle() as operation:
                            with patch.object(
                                playwright_profile.OperationLifecycle,
                                "mark_receipt_published",
                                new=interrupt_after_commit,
                            ):
                                playwright_profile._publish_receipt_in_directory(
                                    directory_fd, final_path.name, receipt, operation
                                )
                        self.assertTrue(operation.receipt_published)
                        self.assertTrue(injected)
                        self.assertEqual(json.loads(final_path.read_text()), receipt)
                        self.assertEqual(stat.S_IMODE(final_path.stat().st_mode), 0o444)
                        self.assertEqual(list(directory.glob(".receipt.json.stage-*")), [])
                    finally:
                        os.close(directory_fd)

        for collision in ("file", "symlink"):
            with self.subTest(collision=collision), tempfile.TemporaryDirectory() as temporary_directory:
                directory = Path(temporary_directory)
                final_path = directory / "receipt.json"
                target_path = directory / "existing-target"
                target_path.write_text("preserve me")
                if collision == "file":
                    final_path.write_text("existing receipt")
                else:
                    final_path.symlink_to(target_path)
                original_bytes = os.lstat(final_path).st_ino
                directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with self.assertRaisesRegex(playwright_profile.PlaywrightProfileError, "already exists"):
                        with playwright_profile.OperationLifecycle() as operation:
                            playwright_profile._publish_receipt_in_directory(
                                directory_fd, final_path.name, receipt, operation
                            )
                finally:
                    os.close(directory_fd)
                self.assertEqual(os.lstat(final_path).st_ino, original_bytes)
                if collision == "file":
                    self.assertEqual(final_path.read_text(), "existing receipt")
                else:
                    self.assertTrue(final_path.is_symlink())
                    self.assertEqual(final_path.read_text(), "preserve me")
                self.assertEqual(list(directory.glob(".receipt.json.stage-*")), [])

    def test_cleanup_only_removes_the_receipt_inode_owned_by_this_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            receipt_path = directory / "receipt.json"
            receipt_path.write_text("owned receipt", encoding="utf-8")
            receipt_path.chmod(0o444)
            metadata = receipt_path.stat()
            expected_identity = f"{metadata.st_dev}:{metadata.st_ino}"
            with patch.object(playwright_profile, "_require_root"):
                with patch.object(playwright_profile, "_state_file_path", return_value=receipt_path):
                    playwright_profile.cleanup(receipt_path, expected_identity)
            self.assertFalse(receipt_path.exists())

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            receipt_path = directory / "receipt.json"
            replacement_path = directory / "replacement.json"
            receipt_path.write_text("preexisting receipt", encoding="utf-8")
            replacement_path.write_text("replacement receipt", encoding="utf-8")
            receipt_path.chmod(0o444)
            replacement_path.chmod(0o444)
            original = receipt_path.stat()
            replacement = replacement_path.stat()
            self.assertNotEqual((original.st_dev, original.st_ino), (replacement.st_dev, replacement.st_ino))
            os.replace(replacement_path, receipt_path)
            expected_identity = f"{original.st_dev}:{original.st_ino}"
            with patch.object(playwright_profile, "_require_root"):
                with patch.object(playwright_profile, "_state_file_path", return_value=receipt_path):
                    with self.assertRaisesRegex(
                        playwright_profile.PlaywrightProfileError, "not created by this invocation"
                    ):
                        playwright_profile.cleanup(receipt_path, expected_identity)
            self.assertEqual(receipt_path.read_text(encoding="utf-8"), "replacement receipt")

    def test_trusted_command_timeout_kills_its_descendant_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            child_pid_file = Path(temporary_directory) / "child.pid"
            child_script = (
                "import os,signal,sys,time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "child=os.fork()\n"
                "if child == 0:\n"
                " time.sleep(30)\n"
                " os._exit(0)\n"
                "open(sys.argv[1], 'w').write(str(child))\n"
                "time.sleep(30)\n"
            )
            with self.assertRaises(playwright_profile.PlaywrightProfileError):
                playwright_profile._run(
                    (sys.executable, "-c", child_script, str(child_pid_file)),
                    timeout=0.5,
                )
            child_pid = int(child_pid_file.read_text(encoding="ascii"))
            stopped = False
            for _attempt in range(100):
                try:
                    stat_line = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii")
                except (FileNotFoundError, ProcessLookupError):
                    stopped = True
                    break
                process_state = stat_line[stat_line.rfind(")") + 2:].split()[0]
                if process_state == "Z":
                    stopped = True
                    break
                time.sleep(0.02)
            self.assertTrue(stopped, f"descendant process {child_pid} remained live after timeout")

    def test_sigint_and_mixed_signals_during_spawn_are_registered_before_cleanup(self) -> None:
        child_script = (
            "import os, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "    open(sys.argv[1], 'w').write(str(os.getpid()))\n"
            "    time.sleep(60)\n"
            "    os._exit(0)\n"
            "open(sys.argv[2], 'w').write(str(os.getpid()))\n"
            "time.sleep(60)\n"
        )

        def process_stopped(pid: int) -> bool:
            try:
                state = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            except FileNotFoundError:
                return True
            return state.rsplit(")", 1)[1].split()[0] == "Z"

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            original_handlers = {
                signum: signal.getsignal(signum)
                for signum in (signal.SIGINT, signal.SIGTERM)
            }
            temp_roots_before = set(Path("/var/tmp").glob("verjson-playwright-apt-*"))
            for index, pending_signals in enumerate(
                ((signal.SIGINT,), (signal.SIGTERM, signal.SIGTERM, signal.SIGINT))
            ):
                with self.subTest(signals=tuple(signal.Signals(s).name for s in pending_signals)):
                    descendant_pid_file = temporary_path / f"descendant-{index}.pid"
                    parent_pid_file = temporary_path / f"parent-{index}.pid"
                    real_popen = playwright_profile.subprocess.Popen

                    def signal_while_spawn_is_unregistered(*args: Any, **kwargs: Any) -> Any:
                        process = real_popen(*args, **kwargs)
                        deadline = time.monotonic() + 5
                        while not descendant_pid_file.exists() or not parent_pid_file.exists():
                            if time.monotonic() >= deadline:
                                process.kill()
                                process.wait(timeout=5)
                                self.fail("supervised child tree did not reach the spawn barrier")
                            time.sleep(0.01)
                        for signum in pending_signals:
                            os.kill(os.getpid(), signum)
                        return process

                    with patch.object(
                        playwright_profile.subprocess,
                        "Popen",
                        new=signal_while_spawn_is_unregistered,
                    ):
                        with self.assertRaisesRegex(
                            playwright_profile.PlaywrightProfileError, "cancelled"
                        ):
                            with playwright_profile.OperationLifecycle():
                                playwright_profile._run_supervised(
                                    (
                                        sys.executable,
                                        "-c",
                                        child_script,
                                        str(descendant_pid_file),
                                        str(parent_pid_file),
                                    ),
                                    timeout=30,
                                    maximum=4096,
                                    env=playwright_profile.SAFE_ENV,
                                    cwd=None,
                                    merge_stderr=False,
                                )

                    self.assertEqual(
                        {signum: signal.getsignal(signum) for signum in original_handlers},
                        original_handlers,
                    )
                    self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()), original_mask)
                    pids = [int(path.read_text(encoding="ascii")) for path in (parent_pid_file, descendant_pid_file)]
                    for pid in pids:
                        for _attempt in range(300):
                            if process_stopped(pid):
                                break
                            time.sleep(0.02)
                        self.assertTrue(process_stopped(pid), f"descendant process {pid} survived cancellation")

            self.assertEqual(set(Path("/var/tmp").glob("verjson-playwright-apt-*")), temp_roots_before)
            self.assertEqual(list(temporary_path.glob("receipt-*")), [])

    def test_term_during_process_registration_cleans_descendants_and_never_writes_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(__file__).resolve().parents[2]
            child_pid_file = Path(temporary_directory) / "child.pid"
            receipt_marker = Path(temporary_directory) / "receipt-written"
            child_command = (
                "import os,signal,sys,time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
                "time.sleep(60)\n"
            )
            helper_script = r'''import os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import ci_playwright_system_libraries as profile
profile._require_root = lambda: None
profile._read_os_release = lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"}
profile._exact_output = lambda *args, **kwargs: b"amd64"
profile._validate_safe_key = lambda: None
profile._state_file_path = lambda _path: Path(sys.argv[3])
profile._make_apt_environment = lambda _root: {}
profile._apt_options = lambda _paths: []
profile._apt_environment = lambda _paths: profile.SAFE_ENV
profile._scan_global_dpkg_config = lambda: {
    "root": (1,), "etc": (1,), "dpkg": (1,), "main": None,
    "fragments_dir": None, "fragments": {},
}
profile._validate_global_dpkg_config = lambda _snapshot: False
profile._assert_dpkg_healthy = lambda _env: None
profile._installed_package_state = lambda _env=None: {}
real_popen = subprocess.Popen
def delayed_popen(*args, **kwargs):
    process = real_popen(*args, **kwargs)
    print("spawned", flush=True)
    time.sleep(0.5)
    return process
profile.subprocess.Popen = delayed_popen
real_run = profile._run
def interruptible_run(argv, **kwargs):
    if tuple(argv)[:2] == ("/usr/bin/apt-get", "update"):
        return real_run((sys.executable, "-c", sys.argv[4], sys.argv[2]), timeout=30)
    return real_run(argv, **kwargs)
profile._run = interruptible_run
profile._write_receipt = lambda *_args: Path(sys.argv[3]).write_text("written")
try:
    profile.provision(profile.PROFILE_ID, "/run/unused.json", "github-hosted", "Linux")
except profile.PlaywrightProfileError:
    print("cancelled", flush=True)
    raise SystemExit(42)
raise SystemExit(0)
'''
            process = subprocess.Popen(
                (
                    sys.executable,
                    "-c",
                    helper_script,
                    str(root / "scripts"),
                    str(child_pid_file),
                    str(receipt_marker),
                    child_command,
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertIsNotNone(process.stdout)
            readable, _, _ = select.select([process.stdout], [], [], 10)
            self.assertTrue(readable, "trusted provisioner did not reach process spawn")
            self.assertEqual(process.stdout.readline().strip(), "spawned")
            for _attempt in range(200):
                if child_pid_file.exists():
                    break
                time.sleep(0.01)
            self.assertTrue(child_pid_file.exists(), "child process did not register before cancellation")
            os.kill(process.pid, signal.SIGTERM)
            os.kill(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 42, stderr)
            self.assertIn("cancelled", stdout)
            self.assertFalse(receipt_marker.exists(), "cancelled provisioner wrote a receipt")
            child_pid = int(child_pid_file.read_text(encoding="ascii"))
            child_stat = Path(f"/proc/{child_pid}/stat")
            for _attempt in range(100):
                try:
                    state = child_stat.read_text(encoding="ascii").rsplit(")", 1)[1].split()[0]
                except FileNotFoundError:
                    break
                if state in {"Z", "X"}:
                    break
                time.sleep(0.02)
            else:
                self.fail(f"TERM-resistant descendant {child_pid} remained live")

    def test_trusted_command_rejects_a_successful_parent_with_a_live_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            child_pid_file = Path(temporary_directory) / "child.pid"
            child_script = (
                "import os,sys,time\n"
                "child=os.fork()\n"
                "if child == 0:\n"
                " os.close(1); os.close(2); time.sleep(30); os._exit(0)\n"
                "open(sys.argv[1], 'w').write(str(child))\n"
            )
            with self.assertRaisesRegex(
                playwright_profile.PlaywrightProfileError,
                "left child processes",
            ):
                playwright_profile._run(
                    (sys.executable, "-c", child_script, str(child_pid_file)),
                    timeout=5,
                )
            child_pid = int(child_pid_file.read_text(encoding="ascii"))
            stopped = False
            for _attempt in range(100):
                try:
                    stat_line = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii")
                except (FileNotFoundError, ProcessLookupError):
                    stopped = True
                    break
                process_state = stat_line[stat_line.rfind(")") + 2 :].split()[0]
                if process_state == "Z":
                    stopped = True
                    break
                time.sleep(0.02)
            self.assertTrue(stopped, f"descendant process {child_pid} remained live after parent exit")

    def test_dpkg_audit_must_be_clean_before_and_after_install(self) -> None:
        with patch.object(playwright_profile, "_exact_output", return_value=b""):
            playwright_profile._assert_dpkg_healthy(playwright_profile.SAFE_ENV)
        with patch.object(playwright_profile, "_exact_output", return_value=b"half-configured package\n"):
            with self.assertRaises(playwright_profile.PlaywrightProfileError):
                playwright_profile._assert_dpkg_healthy(playwright_profile.SAFE_ENV)

    def test_exact_queries_and_dpkg_deb_share_the_supervised_process_path(self) -> None:
        exact_output = inspect.getsource(playwright_profile._exact_output)
        archive_read = inspect.getsource(playwright_profile._needrestart_payload_from_archive)
        self.assertIn("_run_supervised(", exact_output)
        self.assertNotIn("subprocess.run(", exact_output)
        self.assertIn("_run(", archive_read)
        self.assertNotIn("subprocess.", archive_read)
        self.assertEqual(
            playwright_profile._exact_output(
                (
                    sys.executable,
                    "-c",
                    "import sys; print('stdout'); print('stderr', file=sys.stderr)",
                ),
            ),
            b"stdout\n",
        )

    def test_receipt_cleanup_is_bound_to_the_created_inode_in_all_callers(self) -> None:
        root = Path(__file__).resolve().parents[2]
        acceptance = (root / "scripts/ci-gate/playwright-profile-hosted-acceptance.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('receipt_identity=""', acceptance)
        self.assertIn('if [[ -n "$receipt_identity" ]]', acceptance)
        self.assertIn('--expected-identity "$receipt_identity"', acceptance)
        self.assertIn('receipt_identity="$(sudo /usr/bin/stat -c \'%d:%i\' -- "$state_file")"', acceptance)

        for relative_path in (
            ".github/workflows/node-ci.yml",
            ".github/workflows/node-ci-protected.yml",
        ):
            workflow = yaml.safe_load((root / relative_path).read_text(encoding="utf-8"))
            steps = workflow["jobs"]["build-test"]["steps"]
            provision = next(
                step for step in steps if step.get("name") == "Provision Playwright system-library profile"
            )
            self.assertEqual(provision["id"], "provision-playwright-profile")
            self.assertIn('echo "receipt_identity=$receipt_identity" >> "$GITHUB_OUTPUT"', provision["run"])
            cleanup = next(
                step for step in steps if step.get("name") == "Remove Playwright system-library receipt"
            )
            self.assertIn(
                "steps.provision-playwright-profile.outputs.receipt_identity",
                cleanup["env"]["PLAYWRIGHT_STATE_IDENTITY"],
            )
            self.assertIn("--expected-identity", cleanup["run"])

    def test_hosted_acceptance_is_same_repository_read_only_and_path_gated(self) -> None:
        root = Path(__file__).resolve().parents[2]
        workflow_text = (root / ".github/workflows/playwright-profile-hosted-acceptance.yml").read_text(
            encoding="utf-8"
        )
        workflow = yaml.load(workflow_text, Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["on"]), {"pull_request"})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        job = workflow["jobs"]["hosted-profile-acceptance"]
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertEqual(
            job["if"],
            "github.event.pull_request.head.repo.full_name == github.repository",
        )
        checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["persist-credentials"], "false")
        self.assertRegex(checkout["uses"].split("@", 1)[1].split()[0], r"^[0-9a-f]{40}$")
        self.assertNotIn("actions: write", workflow_text)
        self.assertNotIn("secrets.", workflow_text)
        paths = workflow["on"]["pull_request"]["paths"]
        self.assertIn("scripts/ci_playwright_system_libraries.py", paths)
        acceptance = (root / "scripts/ci-gate/playwright-profile-hosted-acceptance.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("--require-nonempty-plan", acceptance)
        self.assertIn('"${VERSION_ID:-}" != 24.04', acceptance)
        self.assertIn('"RUNNER_IMAGE_OS=${ImageOS:-}"', acceptance)
        self.assertIn('"RUNNER_IMAGE_VERSION=${ImageVersion:-}"', acceptance)
        self.assertIn("profile acceptance receipt was not removed", acceptance)
        self.assertTrue((root / "scripts/ci-gate/playwright-profile-hosted-acceptance.sh").stat().st_mode & 0o111)

    def test_workflows_verify_the_receipt_before_launch_without_candidate_apt(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for relative_path in (
            ".github/workflows/node-ci.yml",
            ".github/workflows/node-ci-protected.yml",
        ):
            workflow = yaml.safe_load((root / relative_path).read_text(encoding="utf-8"))
            steps = workflow["jobs"]["build-test"]["steps"]
            provision = next(
                step for step in steps if step.get("name") == "Provision Playwright system-library profile"
            )
            launch = next(
                step for step in steps if step.get("name") == "Run exact credentialless consumer script plan"
            )
            launch_script = launch["run"]

            self.assertIn("inputs.playwright-sandbox-profile != ''", provision["if"])
            self.assertIn("sudo /usr/bin/env -i", provision["run"])
            self.assertNotIn("apt-get", launch_script)
            self.assertNotIn("playwright install-deps", launch_script)
            if relative_path.endswith("node-ci.yml"):
                self.assertLess(
                    launch_script.index("playwright_profile_module.verify_runtime("),
                    launch_script.index("ci_build_artifacts.run_supervised_bubblewrap("),
                )
            else:
                self.assertIn("run-protected-candidate-plan", launch_script)
            candidate_source = inspect.getsource(ci_build_artifacts.run_protected_candidate_plan)
            self.assertIn("playwright_profile_module.verify_runtime(", candidate_source)
            network_call_start = candidate_source.index("ci_build_artifacts.bubblewrap_network_args(")
            network_call_end = candidate_source.index("\n            )", network_call_start)
            self.assertNotIn("playwright", candidate_source[network_call_start:network_call_end])
            self.assertEqual(
                ci_build_artifacts.RUNTIME_INPUT_DEFAULTS["playwright-sandbox-profile"],
                "",
            )


    def test_cli_reports_bounded_single_line_profile_diagnostics(self) -> None:
        message = "hosted profile precondition failed\n::warning::forged\x1b\x00\x08\x7f" + ("x" * 300)
        with patch.object(
            playwright_profile,
            "resolve_profile",
            side_effect=playwright_profile.PlaywrightProfileError(message),
        ), patch("sys.stderr", io.StringIO()) as error_stream:
            result = playwright_profile._command_line(
                [
                    "validate",
                    "--profile",
                    PROFILE_ID,
                    "--runner-environment",
                    "github-hosted",
                    "--runner-os",
                    "Linux",
                ]
            )

        diagnostic = error_stream.getvalue()
        self.assertEqual(result, 1)
        self.assertIn(
            "hosted profile precondition failed%0A::warning::forged%1B%00%08%7F",
            diagnostic,
        )
        self.assertEqual(diagnostic.count("\n"), 1)
        self.assertLessEqual(len(diagnostic), 360)
        self.assertTrue(all(char.isprintable() or char == "\n" for char in diagnostic))
        self.assertNotIn("x" * 241, diagnostic)


if __name__ == "__main__":
    unittest.main()
