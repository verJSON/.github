#!/usr/bin/env python3
"""Regression suite for scripts/contract-version.py (Verjson/.github#1374)."""
import contextlib
import datetime
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

_root = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "contract_version", _root / "scripts" / "contract-version.py")
cv = importlib.util.module_from_spec(_spec)
# Registered before execution: `@dataclasses.dataclass` under
# `from __future__ import annotations` resolves field types through
# `sys.modules[cls.__module__]`, which is absent for a module loaded straight
# from a path.
sys.modules["contract_version"] = cv
_spec.loader.exec_module(cv)


def rel(version, commit_char="a", published="2026-01-01"):
    return cv.Release(version=version, commit=commit_char * 40, published=published)


# The scan enumerates tracked content, so a fixture tree has to be a real
# repository with a real index. Global and system config are neutralized so a
# developer's own `core.excludesFile` cannot decide what a fixture tracks.
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, env=GIT_ENV,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def init_repo(root):
    git(root, "init", "-q", "-b", "main")
    return root


def track(root):
    """Stage everything git would take, which is exactly what the scan enumerates."""
    git(root, "add", "-A")


class SupportedWindow(unittest.TestCase):
    def test_window_never_refuses_a_version_package_retention_still_keeps(self):
        # Three minor lines, three releases: "current and previous minor" alone
        # would refuse v3.0.0 while the keep-3 retention policy still considers
        # it current, stranding a consumer the org calls supported.
        releases = [rel("v2.9.0"), rel("v3.0.0"), rel("v3.1.0"), rel("v3.2.0")]
        self.assertEqual(
            cv.supported_versions(releases), {"v3.0.0", "v3.1.0", "v3.2.0"})


class DeprecationClock(unittest.TestCase):
    def test_a_version_pushed_out_of_the_window_expires_90_days_after_the_release_that_did_it(self):
        # v3.0.0 is still supported at v3.2.0 (it is one of the newest three);
        # v3.3.0, published 2026-03-01, is what pushes it out. The deprecation
        # error has to name a date a maintainer can act on, and that date is a
        # function of release metadata rather than a stored deadline.
        releases = [
            rel("v3.0.0", published="2026-01-01"),
            rel("v3.1.0", published="2026-01-15"),
            rel("v3.2.0", published="2026-02-01"),
            rel("v3.3.0", published="2026-03-01"),
        ]
        verdict = cv.classify("v3.0.0", releases, today="2026-04-01")
        self.assertEqual(verdict.state, cv.DEPRECATED)
        self.assertEqual(verdict.expires_on, "2026-05-30")


class DeclarationVersusReality(unittest.TestCase):
    def write_repo(self, declaration, workflow):
        root = init_repo(pathlib.Path(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".github" / "workflows").mkdir(parents=True)
        if declaration is not None:
            (root / ".github" / "verjson-contract.json").write_text(
                json.dumps(declaration))
        (root / ".github" / "workflows" / "ci.yml").write_text(workflow)
        track(root)
        return root

    def test_a_pin_that_is_not_the_declared_release_commit_is_a_finding(self):
        # The failure mode a declared version exists to close: the declaration
        # says v3.2.0 while the repository actually runs some other contract
        # commit. A version nothing reads back is a pin nobody advances.
        releases = [rel("v3.2.0", commit_char="a", published="2026-02-01")]
        root = self.write_repo(
            {"contract_version": "v3.2.0"},
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "b" * 40 + "\n")
        findings = cv.verify(root, releases, today="2026-02-02")
        self.assertEqual([f.kind for f in findings], ["PIN_MISMATCH"])
        self.assertIn(".github/workflows/ci.yml", findings[0].detail)

    def test_a_moving_tag_is_not_an_immutable_contract_reference(self):
        # `@v2` resolves to whatever the hub last re-pointed it at, so it can
        # name a different contract tomorrow while the declaration stays put.
        releases = [rel("v3.2.0", commit_char="a", published="2026-02-01")]
        root = self.write_repo(
            {"contract_version": "v3.2.0"},
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@v2\n")
        findings = cv.verify(root, releases, today="2026-02-02")
        self.assertEqual([f.kind for f in findings], ["UNPINNED_REFERENCE"])

    def test_a_consistent_repository_reports_nothing(self):
        releases = [rel("v3.2.0", commit_char="a", published="2026-02-01")]
        root = self.write_repo(
            {"contract_version": "v3.2.0"},
            "# Generated by Verjson/.github scripts/gen-changelog-caller.sh workflow "
            + "a" * 40 + "\njobs:\n  ci:\n    uses: "
            "Verjson/.github/.github/workflows/node-ci.yml@" + "a" * 40 + "\n")
        self.assertEqual(cv.verify(root, releases, today="2026-02-02"), [])

    def test_a_generated_header_claiming_another_contract_is_a_finding(self):
        # The intra-repository skew shape: the caller is repinned and the header
        # the generator stamped still names the contract it was generated at.
        releases = [rel("v3.2.0", commit_char="a", published="2026-02-01")]
        root = self.write_repo(
            {"contract_version": "v3.2.0"},
            "# Generated by Verjson/.github scripts/gen-changelog-caller.sh workflow "
            + "c" * 40 + "\njobs:\n  ci:\n    uses: "
            "Verjson/.github/.github/workflows/node-ci.yml@" + "a" * 40 + "\n")
        findings = cv.verify(root, releases, today="2026-02-02")
        self.assertEqual([f.kind for f in findings], ["PIN_MISMATCH"])
        self.assertIn("header", findings[0].detail)

    def test_references_without_a_declaration_are_reported(self):
        releases = [rel("v3.2.0", commit_char="a", published="2026-02-01")]
        root = self.write_repo(
            None,
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "a" * 40 + "\n")
        self.assertEqual([f.kind for f in cv.verify(root, releases, today="2026-02-02")],
                         ["DECLARATION_MISSING"])

    def test_a_declaration_governing_nothing_is_reported(self):
        releases = [rel("v3.2.0", commit_char="a", published="2026-02-01")]
        root = self.write_repo({"contract_version": "v3.2.0"}, "jobs:\n  ci:\n    steps: []\n")
        self.assertEqual([f.kind for f in cv.verify(root, releases, today="2026-02-02")],
                         ["UNGOVERNED_DECLARATION"])

    def test_a_repository_that_adopts_nothing_is_not_an_adopter(self):
        # No declaration and no reference is not a defect: it is a repository
        # that does not consume the contract. Reporting it would make the check
        # fire on every repository in the organization and get muted.
        releases = [rel("v3.2.0", commit_char="a", published="2026-02-01")]
        root = self.write_repo(None, "jobs:\n  ci:\n    steps: []\n")
        self.assertEqual(cv.verify(root, releases, today="2026-02-02"), [])

    def test_a_pin_naming_another_release_commit_is_still_a_mismatch(self):
        # The repinned-but-undeclared adopter: the pin is a perfectly good
        # release commit, just not the declared release's. "Not a release
        # commit" and "not THIS release's commit" are different assertions and
        # only the second one is the skew this check exists to catch.
        releases = [rel("v3.1.0", commit_char="c", published="2026-01-01"),
                    rel("v3.2.0", commit_char="a", published="2026-02-01")]
        root = self.write_repo(
            {"contract_version": "v3.2.0"},
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "c" * 40 + "\n")
        findings = cv.verify(root, releases, today="2026-02-02")
        self.assertEqual([f.kind for f in findings], ["PIN_MISMATCH"])
        self.assertIn("c" * 40, findings[0].detail)

    def test_an_expired_contract_is_refused_even_when_every_pin_agrees(self):
        # ADR 0191 sec.5/sec.6: expiry IS the enforcement. A repository whose pins are
        # internally consistent but whose declared version expired must not pass.
        releases = [
            rel("v3.0.0", commit_char="a", published="2026-01-01"),
            rel("v3.1.0", commit_char="d", published="2026-01-15"),
            rel("v3.2.0", commit_char="e", published="2026-02-01"),
            rel("v3.3.0", commit_char="f", published="2026-03-01"),
        ]
        root = self.write_repo(
            {"contract_version": "v3.0.0"},
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "a" * 40 + "\n")
        findings = cv.verify(root, releases, today="2026-06-01")
        self.assertEqual([f.kind for f in findings], [cv.EXPIRED])

    def test_a_corrupt_declaration_is_reported_even_when_the_tree_has_no_references(self):
        # "No declaration and no reference" is not an adopter. A declaration
        # that exists and cannot be parsed is a defect either way, and the
        # not-an-adopter guard must not swallow it.
        releases = [rel("v3.2.0", commit_char="a", published="2026-02-01")]
        root = init_repo(pathlib.Path(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".github").mkdir(parents=True)
        (root / ".github" / "verjson-contract.json").write_text("{not json")
        track(root)
        self.assertEqual([f.kind for f in cv.verify(root, releases, today="2026-02-02")],
                         ["DECLARATION_UNREADABLE"])

    def test_a_declaration_without_a_string_version_is_reported_on_a_bare_tree(self):
        releases = [rel("v3.2.0", commit_char="a", published="2026-02-01")]
        root = init_repo(pathlib.Path(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".github").mkdir(parents=True)
        (root / ".github" / "verjson-contract.json").write_text(
            json.dumps({"contract_version": 3}))
        track(root)
        self.assertEqual([f.kind for f in cv.verify(root, releases, today="2026-02-02")],
                         ["DECLARATION_UNREADABLE"])

    def test_an_unresolved_release_commit_fails_closed(self):
        # `target_commitish` is a branch name for many releases; comparing a pin
        # against "main" would report every adopter broken.
        releases = [cv.Release(version="v3.2.0", commit="main", published="2026-02-01")]
        root = self.write_repo(
            {"contract_version": "v3.2.0"},
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "a" * 40 + "\n")
        self.assertEqual([f.kind for f in cv.verify(root, releases, today="2026-02-02")],
                         [cv.UNKNOWN])


class WindowEdges(unittest.TestCase):
    def test_moving_and_non_contract_tags_never_occupy_a_window_slot(self):
        # The hub really carries `v1`, `v2`, and `runner-canary-v1.0.0`. A moving
        # tag holding a slot would shrink the window to two real releases.
        releases = [rel("v3.0.0"), rel("v3.1.0"), rel("v3.2.0"),
                    rel("v3"), rel("runner-canary-v1.0.0")]
        self.assertEqual(cv.supported_versions(releases),
                         {"v3.0.0", "v3.1.0", "v3.2.0"})

    def test_the_window_is_exactly_two_minor_lines_when_retention_does_not_widen_it(self):
        # Five releases on three minor lines, so the newest-three floor is a
        # subset of the minor-line half and the minor-line count alone decides.
        # One line would drop v3.1.0; three would admit v3.0.0.
        releases = [rel("v3.0.0"), rel("v3.1.0"), rel("v3.1.1"),
                    rel("v3.2.0"), rel("v3.2.1")]
        self.assertEqual(cv.supported_versions(releases),
                         {"v3.1.0", "v3.1.1", "v3.2.0", "v3.2.1"})

    def test_no_releases_supports_nothing(self):
        self.assertEqual(cv.supported_versions([]), set())

    def test_an_unreleased_version_is_unknown_not_supported(self):
        self.assertEqual(cv.classify("v9.9.9", [rel("v3.0.0")], today="2026-02-02").state,
                         cv.UNKNOWN)

    def test_a_malformed_version_is_unknown(self):
        self.assertEqual(cv.classify("main", [rel("v3.0.0")], today="2026-02-02").state,
                         cv.UNKNOWN)

    def test_crossing_a_major_buys_the_longer_clock(self):
        releases = [
            rel("v3.0.0", published="2026-01-01"),
            rel("v3.1.0", published="2026-01-15"),
            rel("v3.2.0", published="2026-02-01"),
            rel("v4.0.0", published="2026-03-01"),
        ]
        verdict = cv.classify("v3.0.0", releases, today="2026-04-01")
        self.assertEqual(verdict.state, cv.DEPRECATED)
        self.assertEqual(verdict.expires_on, "2026-08-28")

    def test_the_deprecation_becomes_a_refusal_on_its_stated_date(self):
        releases = [
            rel("v3.0.0", published="2026-01-01"),
            rel("v3.1.0", published="2026-01-15"),
            rel("v3.2.0", published="2026-02-01"),
            rel("v3.3.0", published="2026-03-01"),
        ]
        self.assertEqual(cv.classify("v3.0.0", releases, today="2026-05-30").state,
                         cv.EXPIRED)


class ScanTotality(unittest.TestCase):
    """ADR 0191 sec.3 claims the scan is the whole tree. A missed reference is a
    clean PASS, and UNGOVERNED_DECLARATION only fires when the tree has zero
    references, so it never catches a partial miss."""

    def repo(self):
        root = init_repo(pathlib.Path(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "verjson-contract.json").write_text(
            json.dumps({"contract_version": "v3.2.0"}))
        # One conformant pin, so the tree is never reference-free and the
        # UNGOVERNED_DECLARATION net cannot stand in for the scan.
        (root / ".github" / "workflows" / "ci.yml").write_text(
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "a" * 40 + "\n")
        track(root)
        return root

    def verify(self, root):
        releases = [rel("v3.2.0", commit_char="a", published="2026-02-01")]
        return cv.verify(root, releases, today="2026-02-02")

    def test_a_single_quoted_uses_scalar_is_a_contract_reference(self):
        root = self.repo()
        (root / ".github" / "workflows" / "quoted.yml").write_text(
            "jobs:\n  ci:\n    uses: 'Verjson/.github/.github/workflows/node-ci.yml@"
            + "b" * 40 + "'\n")
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["PIN_MISMATCH"])

    def test_a_double_quoted_uses_scalar_is_a_contract_reference(self):
        root = self.repo()
        (root / ".github" / "workflows" / "quoted.yml").write_text(
            'jobs:\n  ci:\n    uses: "Verjson/.github/.github/workflows/node-ci.yml@'
            + "b" * 40 + '"\n')
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["PIN_MISMATCH"])

    def test_generated_shell_assertions_with_variable_refs_are_not_live_pins(self):
        root = self.repo()
        (root / "scripts").mkdir()
        (root / "scripts" / "changelog-contract.test.sh").write_text(
            "grep -qF 'uses: Verjson/.github/.github/workflows/changelog.yml@"
            "$CONTRACT_REF' caller.yml\n"
            "grep -qF 'uses: Verjson/.github/.github/workflows/changelog.yml@"
            "${CONTRACT_REF}' caller.yml\n"
            "uses: Verjson/.github/.github/workflows/changelog.yml@"
            "${CONTRACT_REF}\n")
        track(root)
        self.assertEqual(self.verify(root), [])

    def test_variable_ref_in_a_workflow_remains_unpinned(self):
        root = self.repo()
        (root / ".github" / "workflows" / "dynamic.yml").write_text(
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
            "$CONTRACT_REF\n")
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["UNPINNED_REFERENCE"])

    def test_a_header_below_the_first_six_lines_is_still_a_claim(self):
        # gen-changelog-caller.sh stamps CONTRACT_REF on line 13 of the ADR
        # index test and emits its workflow header below `concurrency:`. A
        # fixed leading window misses both.
        root = self.repo()
        (root / ".github" / "workflows" / "deep.yml").write_text(
            "name: x\n" * 8
            + "# Generated by Verjson/.github scripts/gen-changelog-caller.sh workflow "
            + "b" * 40 + "\n")
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["PIN_MISMATCH"])
        self.assertIn("header", findings[0].detail)

    def test_a_commented_out_pin_is_one_reference_not_two(self):
        # The reason the old code used a window at all: a `uses:` line that is
        # also a comment must not be counted once as a pin and once as a header.
        root = self.repo()
        (root / ".github" / "workflows" / "commented.yml").write_text(
            "#    uses: Verjson/.github/.github/workflows/node-ci.yml@" + "b" * 40 + "\n")
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["PIN_MISMATCH"])

    def test_an_unreadable_file_is_reported_rather_than_skipped(self):
        # A tracked path that is a directory on disk, rather than `chmod 0o000`:
        # mode bits deny nothing to root, so a permission fixture passes
        # vacuously in exactly the container CI runs this in.
        root = self.repo()
        locked = root / ".github" / "workflows" / "locked.yml"
        locked.write_text("jobs: {}\n")
        track(root)
        locked.unlink()
        locked.mkdir()
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["UNSCANNED"])
        self.assertIn("locked.yml", findings[0].detail)

    def test_a_file_past_the_scan_limit_is_reported_rather_than_skipped(self):
        root = self.repo()
        (root / "big.txt").write_text("x" * (cv.MAX_SCAN_BYTES + 1))
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["UNSCANNED"])

    def test_text_in_an_unknown_encoding_is_reported_rather_than_skipped(self):
        root = self.repo()
        (root / "latin.txt").write_bytes("caf\u00e9 pins\n".encode("latin-1"))
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["UNSCANNED"])

    def test_a_binary_file_carries_no_text_reference_and_is_quiet(self):
        # The counterweight: reporting every PNG as UNSCANNED is the noisy
        # check ADR 0185 says gets muted. Git's own NUL heuristic decides.
        root = self.repo()
        (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")
        track(root)
        self.assertEqual(self.verify(root), [])

    def test_a_reference_under_node_modules_is_not_a_hole(self):
        root = self.repo()
        vendored = root / "node_modules" / "@verjson" / "thing" / ".github" / "workflows"
        vendored.mkdir(parents=True)
        (vendored / "ci.yml").write_text(
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "b" * 40 + "\n")
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["PIN_MISMATCH"])

    def test_an_ignored_tree_is_neither_scanned_nor_reported_as_a_gap(self):
        # The cost claim, asserted rather than assumed. A whole-tree walk reads
        # untracked build output and tool caches, and every binary in them past
        # the scan limit becomes an UNSCANNED finding no adopter can ever clear
        # -- a permanent exit 1, which is the muted check ADR 0185 warns about.
        # Ignored content is not what Actions checks out, so it is neither a
        # reference the check can miss nor a gap it has to name.
        root = self.repo()
        (root / ".gitignore").write_text("junk/\n")
        junk = root / "junk"
        junk.mkdir()
        (junk / "vendored.yml").write_text(
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "b" * 40 + "\n")
        (junk / "tool-cache.bin").write_bytes(b"\xff" * (cv.MAX_SCAN_BYTES + 1))
        track(root)
        self.assertEqual(self.verify(root), [])

    def test_a_64_hex_container_digest_is_not_a_40_hex_contract_sha(self):
        # Without the trailing boundary on HEADER_RE, the first 40 characters
        # of a `sha256:` digest read as a contract SHA and the repository gets
        # a PIN_MISMATCH against a reference it does not have. This line is
        # constructed, not sampled: no file in this repository puts a 64-hex run
        # on a line that also names the hub, so the boundary defends a shape the
        # header pass admits rather than one it has already met.
        root = self.repo()
        (root / ".github" / "workflows" / "image.yml").write_text(
            "# Verjson/.github runner image sha256:" + "0123456789abcdef" * 4 + "\n")
        track(root)
        self.assertEqual(self.verify(root), [])

    def test_a_binary_past_the_scan_limit_is_quiet_like_any_other_binary(self):
        # The size check ran first, so the binary heuristic never got to speak
        # for anything over 1 MiB and every large image, archive, or compiled
        # artifact became an UNSCANNED finding. "Binary" does not become
        # "might carry a UTF-8 `uses:` line" at 1048577 bytes.
        root = self.repo()
        (root / "large.png").write_bytes(
            b"\x89PNG\r\n\x1a\n\x00" + b"\xde\xad\xbe\xef" * cv.MAX_SCAN_BYTES)
        track(root)
        self.assertEqual(self.verify(root), [])

    def test_a_utf_16_file_carrying_a_skewed_pin_is_a_finding(self):
        # The NUL heuristic's premise is sound -- a file with a NUL byte holds
        # no UTF-8 `uses:` line -- but the conclusion drawn from it was not: a
        # UTF-16 file is full of NUL bytes and holds a perfectly readable
        # `uses:` line in its own encoding. It was dropped as neither a finding
        # nor a gap, which is the clean PASS on a real skew.
        root = self.repo()
        (root / ".github" / "workflows" / "utf16.yml").write_bytes(
            ("jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
             + "b" * 40 + "\n").encode("utf-16"))
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["PIN_MISMATCH"])
        self.assertIn("utf16.yml", findings[0].detail)

    def test_a_utf_16_file_without_a_bom_is_a_gap_rather_than_a_silent_drop(self):
        # The BOM repair only covered the declared case. UTF-16 without a BOM
        # is still full of NUL bytes, so the binary heuristic dropped it with
        # no finding and no gap: a real skew read as a clean PASS. Undeclared
        # encoding is a guess rather than a claim, so the honest report is a
        # gap someone resolves by hand, not a decoded verdict.
        root = self.repo()
        (root / ".github" / "workflows" / "utf16-nobom.yml").write_bytes(
            ("jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
             + "b" * 40 + "\n").encode("utf-16-le"))
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["UNSCANNED"])
        self.assertIn("utf16-nobom.yml", findings[0].detail)

    def test_a_tree_with_no_declaration_still_names_the_files_it_could_not_read(self):
        # "No declaration and no reference" is only a repository that does not
        # consume the contract when the scan that found no reference was
        # total. The early return dropped `gaps`, so a tree whose files are
        # all unscannable -- where nothing is known about what they reference
        # -- returned the same clean PASS as a tree that was read end to end.
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        init_repo(root)
        (root / "latin.txt").write_bytes("caf\u00e9 pins\n".encode("latin-1"))
        track(root)
        findings = cv.verify(root, [rel("v3.2.0")], today="2026-02-02")
        self.assertEqual([f.kind for f in findings], ["UNSCANNED"])
        self.assertIn("latin.txt", findings[0].detail)

    def test_a_binary_that_opens_with_a_utf_32_bom_is_not_a_utf_16_file(self):
        # `\xff\xfe` is the UTF-16LE BOM and also the first two bytes of the
        # UTF-32LE one, so every binary opening that way was exempted from the
        # binary heuristic and then failed to decode as UTF-16 -- a gap on a
        # file that carries no text at all, which is the noise that gets a
        # check muted rather than read.
        root = self.repo()
        (root / "asset.bin").write_bytes(
            b"\xff\xfe\x00\x00" + bytes(range(256)) * 4)
        track(root)
        self.assertEqual(self.verify(root), [])

    def test_a_sparse_checkout_gap_names_sparse_checkout_as_its_cause(self):
        # A sparse checkout marks the paths it left out skip-worktree and
        # deletes them from the work tree, so the scan refuses every one of
        # them. That refusal is kept rather than skipped: those paths are
        # repository content that `actions/checkout` does check out in full,
        # and skipping them would let a local sparse run report a PASS the
        # enforcing run cannot reproduce. What was wrong is the diagnosis --
        # "could not be sized (No such file or directory)" reads as a broken
        # tree, when the file is exactly where git put it and the checkout is
        # what is partial.
        root = self.repo()
        sparse = root / ".github" / "workflows" / "sparse.yml"
        sparse.write_text("jobs: {}\n")
        track(root)
        git(root, "update-index", "--skip-worktree",
            ".github/workflows/sparse.yml")
        sparse.unlink()
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["UNSCANNED"])
        self.assertIn("sparse.yml", findings[0].detail)
        self.assertIn("skip-worktree", findings[0].detail)

    def test_a_tracked_symlink_is_its_target_path_not_its_target_content(self):
        # git tracks a symlink as a blob holding the target path, which is never
        # a `uses:` line. Following it would read something outside the tree
        # being verified -- or, where the link points at tracked content, would
        # report the same file twice. This is the case the walk could not state:
        # `os.walk` never descended a symlinked directory at all, so a symlinked
        # `node_modules` (this branch's own 48c9cd36) was invisible to it.
        root = self.repo()
        outside = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / "evil.yml").write_text(
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "b" * 40 + "\n")
        (root / "link.yml").symlink_to(outside / "evil.yml")
        track(root)
        self.assertEqual(
            git(root, "ls-files", "-s", "link.yml").stdout.split()[0], "120000")
        self.assertEqual(self.verify(root), [])

    def test_a_symlinked_node_modules_is_skipped_rather_than_opened_as_a_gap(self):
        # The shape the guard was written for, and the one the vendored-caller
        # test above does not reach: `node_modules` is a symlink to a directory
        # (this repository's own 48c9cd36), not a directory. A directory
        # fixture passes whether or not the guard exists, because a tracked
        # directory is never an index entry in the first place. A symlink is,
        # and without the guard the scan follows it: `stat` succeeds on the
        # target directory, `open` then raises `IsADirectoryError`, and a
        # vendored tree becomes an `UNSCANNED` gap no adopter can clear -- the
        # permanent exit 1 that gets a check muted.
        root = self.repo()
        vendored = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, vendored, ignore_errors=True)
        (vendored / "ci.yml").write_text(
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "b" * 40 + "\n")
        (root / "node_modules").symlink_to(vendored, target_is_directory=True)
        track(root)
        self.assertEqual(
            git(root, "ls-files", "-s", "node_modules").stdout.split()[0], "120000")
        self.assertEqual(self.verify(root), [])

    def test_an_absent_git_is_a_refusal_rather_than_an_empty_scan(self):
        # Without git there is no index to read, and an enumerator that
        # returned nothing would make every tree look reference-free -- the
        # exact clean PASS this check exists to deny.
        root = self.repo()
        previous = os.environ.get("PATH")
        os.environ["PATH"] = str(root / "no-such-bin")
        self.addCleanup(os.environ.__setitem__, "PATH", previous or "")
        with self.assertRaises(cv.TreeNotEnumerable):
            cv.verify(root, [rel("v3.2.0")], today="2026-02-02")

    def test_a_submodule_gitlink_is_not_an_unscannable_file(self):
        # A submodule is an index entry of mode 160000 whose path is a
        # directory, so opening it raises IsADirectoryError and the path
        # becomes an UNSCANNED finding no adopter can clear -- a permanent
        # exit 1, the muted-check shape ADR 0191 sec.3 cites as the reason the
        # walk was abandoned. actions/checkout does not fetch submodule content
        # by default, so that content is not the tree this check compares.
        root = self.repo()
        inner = root / "vendor"
        inner.mkdir()
        git(inner, "init", "-q", "-b", "main")
        (inner / "ci.yml").write_text("jobs: {}\n")
        git(inner, "add", "ci.yml")
        git(inner, "-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture",
            "commit", "-qm", "inner")
        git(root, "add", "vendor")
        self.assertEqual(
            git(root, "ls-files", "-s", "vendor").stdout.split()[0], "160000")
        self.assertEqual(self.verify(root), [])

    def test_a_bare_repository_is_a_refusal_rather_than_an_empty_scan(self):
        # `git ls-files` in a bare repository exits 0 with no output, so the
        # enumerator returned an empty list and the sweep reported zero files
        # scanned, zero references found, PASS. "Scanned nothing, found
        # nothing" is the fail-open degradation this check exists to close, and
        # it is indistinguishable from a conformant tree in the exit code.
        root = self.repo()
        bare = pathlib.Path(tempfile.mkdtemp()) / "mirror.git"
        self.addCleanup(shutil.rmtree, bare.parent, ignore_errors=True)
        git(root, "-c", "user.email=fixture@example.invalid", "-c", "user.name=fixture",
            "commit", "-qm", "fixture")
        git(root, "clone", "-q", "--bare", str(root), str(bare))
        self.assertEqual(git(bare, "rev-parse", "--is-bare-repository").stdout.strip(),
                         "true")
        with self.assertRaises(cv.TreeNotEnumerable):
            cv.verify(bare, [rel("v3.2.0")], today="2026-02-02")

    def test_a_git_directory_handed_in_as_the_root_is_a_refusal(self):
        # The other shape of "there is no work tree here": `git -C <repo>/.git
        # ls-files` enumerates the index happily, but every path it names is
        # resolved against `.git/` and so exists nowhere. Reported one gap at a
        # time that reads as a tree full of unreadable files rather than as the
        # wrong root, which is the diagnosis a refusal states outright.
        root = self.repo()
        with self.assertRaises(cv.TreeNotEnumerable):
            cv.verify(root / ".git", [rel("v3.2.0")], today="2026-02-02")

    def test_an_empty_index_is_an_empty_scan_rather_than_a_refusal(self):
        # The counterweight to the bare-repository refusal, and the reason the
        # two cannot share one rule: a repository that tracks nothing yet is a
        # work tree whose whole content really was scanned, and it declares no
        # contract version, so it is a repository that does not consume the
        # contract. Refusing it would fire exit 2 on every new repository in
        # the organization, which is the muted check ADR 0191 sec.3 warns about.
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        init_repo(root)
        self.assertEqual(git(root, "ls-files").stdout, "")
        self.assertEqual(cv.verify(root, [rel("v3.2.0")], today="2026-02-02"), [])

    def test_the_git_directory_is_not_repository_content(self):
        # The one skip that is correct rather than a hole: `.git` is git's own
        # object and ref storage, not the tree Actions executes.
        root = self.repo()
        (root / ".git" / "COMMIT_EDITMSG").write_text(
            "uses: Verjson/.github/.github/workflows/node-ci.yml@" + "b" * 40 + "\n")
        self.assertEqual(self.verify(root), [])


class UsesShapeCoverage(unittest.TestCase):
    """Which `uses:` *shapes* the line scan recognizes, as opposed to which
    *files* it reads. ADR 0191 sec.3's totality claim is about the file set; a
    shape the recognizer cannot see is the same clean PASS on a real skew, and
    nothing above measures it (Verjson/.github#1433)."""

    repo = ScanTotality.repo
    verify = ScanTotality.verify

    def test_a_quoted_uses_key_is_a_contract_reference(self):
        # `"uses": x` is legal YAML and Actions accepts it. The pattern looked
        # for the literal `uses:`, which a quoted key never contains -- so a
        # skewed pin written this way was no finding at all.
        root = self.repo()
        (root / ".github" / "workflows" / "quoted-key.yml").write_text(
            'jobs:\n  ci:\n    "uses": Verjson/.github/.github/workflows/node-ci.yml@'
            + "b" * 40 + "\n")
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["PIN_MISMATCH"])

    def test_a_uses_value_on_the_following_line_is_a_gap_not_a_silence(self):
        # Legal YAML puts a scalar on the line after its key. A line-based scan
        # cannot read it, and reading it as "this file has no reference" is the
        # clean PASS on a real skew that the whole check exists to refuse. The
        # honest report is the same shape as UNSCANNED: a named gap.
        root = self.repo()
        (root / ".github" / "workflows" / "folded.yml").write_text(
            "jobs:\n  ci:\n    uses:\n      Verjson/.github/.github/workflows/"
            "node-ci.yml@" + "b" * 40 + "\n")
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["UNRESOLVED_REFERENCE"])
        self.assertIn("folded.yml", findings[0].detail)

    def test_a_hub_uses_line_with_no_readable_pin_is_a_gap_not_a_silence(self):
        # The line names the hub and is a `uses:` key, but no `path@ref` can be
        # read off it -- here because an expression expands into the path. The
        # scan knows it is looking at a contract reference and knows it cannot
        # read it, which is exactly the state UNSCANNED exists to report.
        root = self.repo()
        (root / ".github" / "workflows" / "templated.yml").write_text(
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/"
            "${{ matrix.flavor }}.yml@" + "b" * 40 + "\n")
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["UNRESOLVED_REFERENCE"])
        self.assertIn("templated.yml", findings[0].detail)

    def test_a_uses_block_scalar_is_a_gap_not_a_silence(self):
        root = self.repo()
        (root / ".github" / "workflows" / "block.yml").write_text(
            "jobs:\n  ci:\n    uses: >-\n      Verjson/.github/.github/workflows/"
            "node-ci.yml@" + "b" * 40 + "\n")
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["UNRESOLVED_REFERENCE"])
        self.assertIn("block.yml", findings[0].detail)

    def test_a_uses_alias_is_a_gap_not_a_silence(self):
        # The one shape no line scan can ever resolve: the reference is an
        # anchor defined elsewhere, so the literal never appears beside the key.
        root = self.repo()
        (root / ".github" / "workflows" / "alias.yml").write_text(
            "x: &hub Verjson/.github/.github/workflows/node-ci.yml@" + "b" * 40
            + "\njobs:\n  ci:\n    uses: *hub\n")
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["UNRESOLVED_REFERENCE"])
        self.assertIn("alias.yml", findings[0].detail)

    def test_a_root_action_pin_is_a_reference_not_a_gap(self):
        # `uses: Verjson/.github@<sha>` names the repository's root action. The
        # pin is present, immutable and readable; a pattern that requires a path
        # segment reports it as a gap instead -- a false gap on a correct pin,
        # which is the muting hazard ADR 0185 names (Verjson/.github#1472).
        root = self.repo()
        (root / ".github" / "workflows" / "root-action.yml").write_text(
            "jobs:\n  ci:\n    steps:\n      - uses: Verjson/.github@" + "b" * 40 + "\n")
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["PIN_MISMATCH"])
        self.assertIn("root-action.yml", findings[0].detail)

    def test_a_flow_style_step_pin_is_a_reference_not_a_gap(self):
        # Verjson/.github#1482. `steps: [{uses: ...@<sha>}]` parses to exactly
        # the block-style structure, so the pin is present, immutable and
        # readable. Unifying the two recognizers let the ref class run through
        # the closing `}` and `]`, which made this line a gap on one sweep and
        # a clean file on the other -- the muting direction again. Two entries,
        # because with one the class that stops at the structure and the class
        # that runs past it differ only in a ref's content, not in the number
        # of findings.
        root = self.repo()
        (root / ".github" / "workflows" / "flow.yml").write_text(
            "jobs:\n  ci:\n    steps: [{uses: Verjson/.github/.github/workflows/"
            "node-ci.yml@" + "b" * 40 + "}, {uses: Verjson/.github/.github/"
            "workflows/changelog.yml@" + "c" * 40 + "}]\n")
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings],
                         ["PIN_MISMATCH", "PIN_MISMATCH"])

    def test_an_unpinned_expression_ref_is_quoted_whole(self):
        # The verdict was already right; the detail string was not. `[^\s"']+`
        # stops at the first space, so the reader was told the offending ref is
        # `${{`, which is not a thing anyone wrote (Verjson/.github#1472).
        root = self.repo()
        (root / ".github" / "workflows" / "expr.yml").write_text(
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/x.yml@"
            "${{ env.CONTRACT_REF }}\n")
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["UNPINNED_REFERENCE"])
        self.assertIn("'${{ env.CONTRACT_REF }}'", findings[0].detail)

    def test_a_lookalike_repository_is_not_a_root_action_reference(self):
        # The boundary the optional path segment must not cross. An owner/repo
        # that merely starts with the hub name is a different repository, and a
        # pattern reading its pin as a hub pin invents a PIN_MISMATCH nobody can
        # clear. Making the path optional widens what the scan *reads*; it must
        # not widen what the scan *claims* (Verjson/.github#1472, #1468).
        root = self.repo()
        (root / ".github" / "workflows" / "mirror.yml").write_text(
            "jobs:\n  ci:\n    steps:\n      - uses: Verjson/.github-mirror@"
            + "b" * 40 + "\n")
        track(root)
        # A gap, because the line does name the hub string and carries no pin
        # this scan can read -- but never a reference whose ref is compared.
        self.assertEqual([f.kind for f in self.verify(root)], ["UNRESOLVED_REFERENCE"])

    def test_whitespace_before_the_uses_colon_is_a_contract_reference(self):
        # `uses : x` is legal YAML; the old pattern required `uses:` exactly.
        root = self.repo()
        (root / ".github" / "workflows" / "spaced.yml").write_text(
            "jobs:\n  ci:\n    uses : Verjson/.github/.github/workflows/node-ci.yml@"
            + "b" * 40 + "\n")
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["PIN_MISMATCH"])

    def test_a_key_merely_ending_in_uses_is_not_a_reference(self):
        # The cost of making the path segment optional: `uses` is a substring of
        # `statuses`, so an unanchored pattern reads a key that is not `uses:` at
        # all. Scoping the case flag off the key does not stop this one --
        # `statuses` is lowercase -- so the delimiter anchor is what rejects it:
        # the character before the key must be the line start, a quote, a
        # backtick, a `#`, or an escaped newline, and `t` is none of those. Both
        # shapes are asserted, because the path form carried this hazard long
        # before the pathless form existed (Verjson/.github#1472).
        root = self.repo()
        (root / ".github" / "workflows" / "statuses.yml").write_text(
            "jobs:\n  ci:\n    statuses: Verjson/.github@" + "b" * 40 + "\n"
            "    statuses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "c" * 40 + "\n")
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], [])

    def test_a_capitalised_prose_list_item_is_not_a_reference(self):
        # The invariant `USES_KEY_RE` is documented to protect: Actions requires
        # a lowercase key, so `- Uses:` in English prose is never a reference.
        # The gap half honours that by being case-sensitive; before this fix the
        # pin half did not, so a Markdown list item in a file naming the hub
        # produced a PIN_MISMATCH -- strictly worse than the false *gap* the
        # documented rationale exists to prevent (Verjson/.github#1472).
        root = self.repo()
        (root / "README.md").write_text(
            "- Uses: Verjson/.github@" + "b" * 40 + " for its CI.\n"
            "- Uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "c" * 40 + " for its CI.\n")
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], [])

    def test_lowercase_prose_reads_alike_in_both_uses_shapes(self):
        # The residual this fix closes, and the symmetry it must preserve while
        # closing it. A lowercase `uses:` mid-sentence was read as a key in the
        # path form long before the pathless form existed; delimiter anchoring
        # rejects it in *both*, because the character before a real key is the
        # line start, a quote, a backtick, a `#`, or an escaped newline, and
        # English puts a space there. The two shapes never diverge -- that is
        # what #1472's criterion 5 asks for -- and they now agree on the
        # stricter verdict rather than on the looser one (Verjson/.github#1472).
        root = self.repo()
        (root / "PROSE.md").write_text(
            "The repo uses: Verjson/.github@" + "b" * 40 + " today.\n"
            "The repo uses: Verjson/.github/.github/workflows/x.yml@"
            + "c" * 40 + " today.\n")
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], [])

    def test_a_pin_inside_a_source_string_fixture_is_still_read(self):
        # The `\\n` alternative in the delimiter class. Deleting it alone drops
        # 22 of the 632 references the fleet carries -- 13 `.py`, 3 `.sh`, 3
        # `.js`, 2 `.ts`, 1 `.mjs` -- pins written inside a source-string
        # literal, where the key follows a two-character `\\n` escape rather
        # than a real line break. Line anchoring drops these too, which with the
        # quoted shape below is the actual reason `^\\s*(?:-\\s+)?` was
        # rejected (Verjson/.github#1472).
        root = self.repo()
        (root / "fixture.py").write_text(
            'EXPECTED = "jobs:\\n  ci:\\n    uses: '
            'Verjson/.github/.github/workflows/node-ci.yml@' + "b" * 40 + '"\n')
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["PIN_MISMATCH"])

    def test_a_shell_grep_assertion_quoting_a_pin_is_still_read(self):
        # The quote alternative, which is the one the anchor most depends on:
        # deleting it alone drops 122 of the fleet's 632 references, 105 of them
        # `.sh` assertions that quote the pin they expect. The key here follows
        # the opening `"` of the grep pattern,
        # never the line start, so an anchor that only accepts the line start
        # reports a repository with skewed assertions as carrying no reference
        # at all (Verjson/.github#1472).
        root = self.repo()
        (root / "check.sh").write_text(
            'grep -qF "uses: Verjson/.github/.github/workflows/node-ci.yml@'
            + "b" * 40 + '" "$1"\n')
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["PIN_MISMATCH"])

    def test_a_pin_inside_a_backtick_literal_is_read_as_its_sha(self):
        # The backtick alternative of the key anchor, which nothing else in this
        # suite covers: a pin documented as Markdown inline code, or built into
        # a caller fixture as a template literal, opens its string with a
        # backtick exactly as the quote characters do.
        #
        # This asserted the opposite until Verjson/.github#1483. The ref class
        # excluded whitespace and the two quotes but not a backtick, so the
        # closing backtick was absorbed and the ref was 41 characters -- a
        # correct, immutable 40-hex pin that could never compare equal to a
        # release commit, so the verdict was UNPINNED_REFERENCE on a line that
        # is in fact pinned. That is the muting direction ADR 0185 names. The
        # verdict is now PIN_MISMATCH, which is the true statement about this
        # fixture: the pin is read, and it is not the tracked release commit.
        root = self.repo()
        (root / "surface.test.ts").write_text(
            "const pinnedUses = `    uses: "
            "Verjson/.github/.github/workflows/node-ci.yml@" + "b" * 40 + "`;\n")
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["PIN_MISMATCH"])
        self.assertIn("b" * 40, findings[0].detail)
        self.assertNotIn("b" * 40 + "`", findings[0].detail)

    def test_two_adjacent_backtick_pins_are_two_references_not_one(self):
        # The boundary needs more than one instance to be pinned. With a single
        # backtick-delimited pin on the line, admitting the backtick and
        # excluding it differ only in the *content* of one ref; with two written
        # adjacently, with no whitespace to stop the ref on the pattern's
        # behalf, they differ in the number of matches -- 1 against 2. Measured
        # both ways before this test was written: admitting the backtick yields
        # one match whose ref is `<sha-a>``uses:`, having run straight through
        # the closing backtick and into the second reference.
        line = ("`uses: Verjson/.github/.github/workflows/node-ci.yml@" + "a" * 40
                + "``uses: Verjson/.github/.github/workflows/node-ci.yml@"
                + "b" * 40 + "`")
        self.assertEqual([m.group("ref") for m in cv.USES_RE.finditer(line)],
                         ["a" * 40, "b" * 40])

    def test_two_adjacent_backtick_refs_are_two_references_when_unpinned(self):
        # The test above pins the #1483 backtick exclusion with 40-hex refs, and
        # #1482's flow-style fix quietly took that job over: the ref now matches
        # a 40-hex run as a whole alternative and stops after 40 characters, so
        # the backtick never gets the chance to be absorbed and re-admitting it
        # reddens nothing. Measured -- with both fixes in, flipping the backtick
        # back into the class left every test green. The exclusion is still the
        # only thing doing the work for a ref that is *not* a SHA, which is
        # where #1483 is now load-bearing, so that is what this pins. Admitting
        # the backtick yields one match whose ref is `v1``uses:`.
        line = ("`uses: Verjson/.github/.github/workflows/node-ci.yml@v1"
                "``uses: Verjson/.github/.github/workflows/changelog.yml@v2`")
        self.assertEqual([m.group("ref") for m in cv.USES_RE.finditer(line)],
                         ["v1", "v2"])

    def test_two_adjacent_escaped_newline_refs_are_two_references(self):
        # The same argument for the backslash, which the ref class excludes as
        # the closing half of the `\n` the key anchor already reads as an
        # opener. A source-string fixture writes several workflow lines into one
        # Python line; without the exclusion the first ref runs through the
        # escape and swallows the next key, so two references read as one.
        line = (r'text = "uses: Verjson/.github/.github/workflows/node-ci.yml@v1'
                r'\nuses: Verjson/.github/.github/workflows/changelog.yml@v2\n"')
        self.assertEqual([m.group("ref") for m in cv.USES_RE.finditer(line)],
                         ["v1", "v2"])

    def test_two_adjacent_single_quoted_refs_are_two_references(self):
        # The single quote, found by running the same routine matrix that found
        # the backtick and the backslash rather than by suspecting it. It was
        # subsumed the same way: the 40-hex alternative stops a pin after 40
        # characters, so re-admitting `\'` to the ref class reddened nothing on
        # any pinned fixture in this suite. Measured -- dropping it from the
        # class left the whole suite green until this test. A single-quoted
        # scalar is the commonest shape the corpus writes, so the exclusion is
        # load-bearing for every non-SHA ref in one; admitting the quote yields
        # one match whose ref is `v1\'\'uses:`.
        line = ("'uses: Verjson/.github/.github/workflows/node-ci.yml@v1"
                "''uses: Verjson/.github/.github/workflows/changelog.yml@v2'")
        self.assertEqual([m.group("ref") for m in cv.USES_RE.finditer(line)],
                         ["v1", "v2"])

    def test_a_shell_grep_assertion_single_quoting_a_pin_is_still_read(self):
        # The twin of the double-quoted grep assertion above, for the other
        # quote in the *delimiter* class -- and the fourth exclusion the matrix
        # found untested. Dropping `'` from that class left the whole suite
        # green: `sh` quotes a grep pattern either way, and with the key sitting
        # after the opening `'` rather than at the line start, the entire
        # reference goes unread and the repository reports as carrying none.
        # That is a whole reference muted, not a corrupted one.
        root = self.repo()
        (root / "check.sh").write_text(
            "grep -qF 'uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "b" * 40 + "' \"$1\"\n")
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["PIN_MISMATCH"])

    def test_a_single_quoted_uses_key_is_a_contract_reference(self):
        # `'uses': x` is legal YAML exactly as `"uses": x` is, and the key
        # alternative for it had no test of its own -- deleting `'uses'` from
        # the pattern left the suite green while the double-quoted twin above
        # was covered.
        root = self.repo()
        (root / ".github" / "workflows" / "sq-key.yml").write_text(
            "jobs:\n  ci:\n    'uses': Verjson/.github/.github/workflows/node-ci.yml@"
            + "b" * 40 + "\n")
        track(root)
        self.assertEqual([f.kind for f in self.verify(root)], ["PIN_MISMATCH"])

    def test_a_repository_whose_name_only_resembles_the_hub_is_not_a_reference(self):
        # The `.` in `Verjson/\.github` is escaped, and nothing asserted it:
        # unescaping it left the suite green. `Verjson/xgithub` is a name the
        # org could hold, and reading one as the contract hub would compare an
        # unrelated repository's pin against this repository's releases -- the
        # inventing direction, and on a repository nobody referenced at all.
        line = "    uses: Verjson/xgithub/.github/workflows/node-ci.yml@" + "b" * 40
        self.assertEqual([m.group("ref") for m in cv.USES_RE.finditer(line)], [])

    def test_a_forty_hex_run_a_ref_continues_past_is_not_a_pin(self):
        # The lookahead after the 40-hex alternative, character by character.
        # `\w`, `.` and `-` are covered by the fall-through tests above; `/` and
        # `+` were not, and dropping either left the suite green. `@` was not in
        # the class at all until this, and that was a live inventing case:
        # `git check-ref-format` accepts `refs/tags/<40-hex>@2`, and the scan
        # read its first 40 characters as a pin, reporting PIN_MISMATCH against
        # a SHA nobody wrote. All four are legal refname characters, so a ref
        # that continues past a 40-hex run falls through to the general class
        # and is reported unpinned -- the muting direction, which for an
        # already-unpinned ref costs nothing.
        sha = "b" * 40
        for suffix in ("/b", "+b", "@2", "-rc1", ".1"):
            with self.subTest(suffix=suffix):
                line = ("    uses: Verjson/.github/.github/workflows/node-ci.yml@"
                        + sha + suffix)
                matches = list(cv.USES_RE.finditer(line))
                # One reference, whose path is unchanged -- the path class
                # excludes `@` so the `@2` form cannot be re-cut as a longer
                # path and a one-character ref -- and whose ref is not a pin.
                self.assertEqual([m.group("path") for m in matches],
                                 [".github/workflows/node-ci.yml"])
                self.assertEqual([m.group("ref") for m in matches], [sha + suffix])
                self.assertFalse(cv.SHA_RE.match(matches[0].group("ref")))

    def test_a_ref_that_merely_ends_in_forty_hex_is_not_a_pin(self):
        # `@v1-<40-hex>` is a legal tag, and reading it as a pin would report a
        # moving tag as immutably pinned -- the inventing direction, and the
        # one that makes a drifting adopter look conformant.
        #
        # What does the work is `SHA_RE`'s trailing `$`; its leading `^` is
        # redundant and no test can kill it, because every call site in this
        # module, `contract-version` and `fleet-contract-inventory` uses
        # `.match()`, which anchors at position 0 regardless. Dropping `^` was
        # measured against the whole suite and changed nothing. It stays as a
        # guard against a later `.search()` call site, stated here rather than
        # asserted, on the same footing as the other deliberately unasserted
        # characters named in `contract_reference`.
        #
        # Assembled at the `@` rather than written literally, for the reason the
        # semver fixture below states: `doc-tag-pins.sh` greps every tracked
        # file for `workflows/<name>.yml@v<ref>` and checks that ref against
        # this repository's tags, so a literal `@v1-<40-hex>` here reads as a
        # documented pin to a tag that was never cut. Measured -- it failed
        # exactly that way before the split.
        ref = "v1-" + "b" * 40
        line = "    uses: Verjson/.github/.github/workflows/node-ci.yml@" + ref
        refs = [m.group("ref") for m in cv.USES_RE.finditer(line)]
        self.assertEqual(refs, [ref])
        self.assertIsNone(cv.SHA_RE.match(refs[0]))

    def test_the_ref_class_still_admits_a_full_semver_build_ref(self):
        # The other side of the same boundary. Excluding one character from a
        # class is the kind of edit that over-narrows by one more, and a ref is
        # not only ever a 40-hex SHA: `@v2.2.0-rc.1+build` is a legal ref whose
        # `.`, `-` and `+` must all survive. Truncating it would quote a ref
        # back at the reader that nobody wrote, which is the complaint that
        # produced the `${{ ... }}` branch alongside it.
        # Assembled rather than written literally: doc-tag-pins.sh greps every
        # tracked file for `workflows/<name>.yml@v<ref>` and checks that ref
        # against this repository's tag list, so a literal fixture here would
        # read as a documented pin to a tag that was never cut. Splitting the
        # string at the `@` keeps the fixture a fixture.
        ref = "v2.2.0-rc.1+build"
        line = "  uses: Verjson/.github/.github/workflows/node-ci.yml@" + ref
        self.assertEqual([m.group("ref") for m in cv.USES_RE.finditer(line)],
                         [ref])

    def test_an_expression_ref_stops_at_its_own_closing_braces(self):
        # Discriminates lazy from greedy, which the first version of this test
        # did not: its fixture carried one `}}`, so both spans were identical
        # and a `.*?` -> `.*` mutant survived the whole suite. Two expressions
        # with a header SHA between them is the shape that tells them apart --
        # lazy stops at the first `}}` and leaves the SHA outside the `uses:`
        # span, greedy runs to the last `}}` and swallows the header claim.
        root = self.repo()
        (root / ".github" / "workflows" / "two-exprs.yml").write_text(
            "#    uses: Verjson/.github/.github/workflows/x.yml@${{ env.A }} "
            "pinned at Verjson/.github " + "b" * 40 + " ${{ env.B }}\n")
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings],
                         ["UNPINNED_REFERENCE", "PIN_MISMATCH"])
        self.assertIn("'${{ env.A }}'", findings[0].detail)
        self.assertIn("header names " + "b" * 40, findings[1].detail)

    def test_an_overlong_expression_is_not_absorbed_whole(self):
        # The bound that keeps the scan linear, asserted as behaviour because a
        # timing assertion would be flaky. An unbounded `.*?` re-scans the line
        # tail from every `$`, so a line dense with unterminated `${{` costs
        # O(n^2): measured 53ms at 1600 openers, 833ms at 6400, 13.1s at 25600,
        # and over 120s at 102400, against a 1 MiB `MAX_SCAN_BYTES` and a scan
        # that reads every tracked file. The bounded class is linear on the same
        # inputs (3.1ms / 12.7ms / 39ms / 188ms). The price is this fixture: an
        # expression longer than the bound is no longer read as one unit and
        # falls back to the plain class, quoting `${{` again. No fleet line is
        # anywhere near 200 characters of expression (Verjson/.github#1472).
        root = self.repo()
        (root / ".github" / "workflows" / "overlong.yml").write_text(
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/x.yml@"
            "${{ env." + "A" * 250 + " }}\n")
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["UNPINNED_REFERENCE"])
        self.assertIn("'${{'", findings[0].detail)

    def test_a_sha_inside_an_expression_is_not_a_second_claim(self):
        # An intended consequence of reading the expression whole, stated rather
        # than left to be discovered. A 40-hex run *inside* `${{ ... }}` now
        # falls within the `uses:` span, so the header pass skips it and the
        # line is one reference rather than a templated ref plus a phantom pin
        # claim. The base pattern stopped at `${{` and counted both.
        root = self.repo()
        (root / ".github" / "workflows" / "hex-in-expr.yml").write_text(
            "#    uses: Verjson/.github/.github/workflows/x.yml@"
            "${{ env.X_" + "b" * 40 + " }}\n")
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["UNPINNED_REFERENCE"])

    def test_a_crlf_pin_is_a_contract_reference(self):
        # Five files in the measured fleet carry CRLF and a `uses:` key. The
        # trailing `\r` must not end up inside the captured ref, or every one of
        # them reports a mismatch against a SHA that differs by one byte.
        root = self.repo()
        (root / ".github" / "workflows" / "crlf.yml").write_bytes(
            ("jobs:\r\n  ci:\r\n    uses: Verjson/.github/.github/workflows/"
             "node-ci.yml@" + "b" * 40 + "\r\n").encode("utf-8"))
        track(root)
        findings = self.verify(root)
        self.assertEqual([f.kind for f in findings], ["PIN_MISMATCH"])
        self.assertIn("b" * 40, findings[0].detail)

    def test_an_offline_uses_value_is_quiet_when_the_file_never_names_the_hub(self):
        # The noise counterweight. A YAML anchor and a continuation line are
        # both file-scoped, so a file with no `Verjson/.github` in it cannot
        # hold a hub reference the scan missed -- and the fleet's ~1500
        # third-party `uses:` keys must not each become a finding.
        root = self.repo()
        (root / ".github" / "workflows" / "third-party.yml").write_text(
            "x: &pin actions/checkout@" + "c" * 40
            + "\njobs:\n  ci:\n    steps:\n      - uses: *pin\n"
            "      - uses:\n          actions/setup-node@" + "d" * 40 + "\n")
        track(root)
        self.assertEqual(self.verify(root), [])

    def test_prose_ending_a_sentence_with_uses_is_not_an_unresolved_reference(self):
        # Sampled, not invented: Markdown in this organization really does end a
        # line with `uses:` inside a file that names the hub. An unanchored key
        # pattern reports every one of them.
        root = self.repo()
        (root / "NEXT.md").write_text(
            "A consumer installing the Verjson/.github gate does it with `uses:`\n"
            "and nothing else.\n")
        track(root)
        self.assertEqual(self.verify(root), [])

    def test_a_key_merely_ending_in_uses_is_not_a_uses_key(self):
        # Also sampled: `statuses: read # ... (Verjson/.github ADR 0023)` is a
        # real line in three adopter workflows, and `statuses:` contains
        # `uses:`. Anchoring the key is what keeps it quiet.
        root = self.repo()
        (root / ".github" / "workflows" / "perms.yml").write_text(
            "permissions:\n  statuses: read # eligibility reads renovate "
            "(Verjson/.github ADR 0023)\n")
        track(root)
        self.assertEqual(self.verify(root), [])

    def test_a_capitalized_uses_in_prose_is_not_a_uses_key(self):
        # Actions requires the key to be lowercase -- `Uses:` is a workflow parse
        # error, never a reference -- so case-insensitivity on the key side can
        # only manufacture gaps and can never catch a pin. An ordinary English
        # list item in a file that happens to name the hub is a permanent red
        # check an adopter clears only by rewriting prose, which is precisely the
        # muting hazard ADR 0185 refuses.
        root = self.repo()
        (root / "README.md").write_text(
            "The Verjson/.github contract:\n\n- Uses:\n  - the release caller\n")
        track(root)
        self.assertEqual(self.verify(root), [])

    def test_a_source_string_opening_with_a_uses_literal_is_not_a_uses_key(self):
        # The third sampled shape, and the reason the key's quotes have to
        # balance: this repository's own test source opens lines with the
        # literal `"uses: Verjson/.github/...@" + sha`. A key pattern that
        # accepts a lone opening quote reports the scanner's own fixtures.
        root = self.repo()
        (root / "fixture.py").write_text(
            '    "uses: Verjson/.github/.github/workflows/node-ci.yml@" + sha\n')
        track(root)
        self.assertEqual(self.verify(root), [])


class ReleaseDocument(unittest.TestCase):
    def load(self, payload):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(payload, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return cv.load_releases(handle.name)

    def test_one_version_published_at_two_commits_is_a_usage_failure(self):
        # First-wins picks a commit silently, and which one it picks decides
        # every PIN_MISMATCH in the sweep. An ambiguous list cannot be asked.
        with self.assertRaises(ValueError):
            self.load([{"version": "v3.2.0", "commit": "a" * 40, "published": "2026-02-01"},
                       {"version": "v3.2.0", "commit": "b" * 40, "published": "2026-02-01"}])

    def test_a_commit_that_is_not_a_40_hex_object_id_is_a_usage_failure(self):
        # `target_commitish` is a branch name for v2.0.0 and older on this
        # repository right now; a producer that used it is broken at the source.
        with self.assertRaises(ValueError):
            self.load([{"version": "v3.2.0", "commit": "main", "published": "2026-02-01"}])

    def test_a_timestamp_shaped_publication_date_is_a_usage_failure(self):
        # `published_at` is `2026-03-01T00:00:00Z` raw; the deprecation clock
        # calls date.fromisoformat on it and would raise mid-verdict.
        with self.assertRaises(ValueError):
            self.load([{"version": "v3.2.0", "commit": "a" * 40,
                        "published": "2026-02-01T00:00:00Z"}])

    def test_a_releases_document_that_is_not_an_array_is_a_usage_failure(self):
        # A producer that emitted one object instead of a list, or `null`, must
        # not be iterated: whatever a non-list yields on iteration is not a
        # release, and a TypeError escaping load_releases is not the exit-2
        # usage failure the caller handles.
        with self.assertRaisesRegex(ValueError, "JSON array"):
            self.load({"version": "v3.2.0", "commit": "a" * 40,
                       "published": "2026-02-01"})

    def test_a_release_entry_that_is_not_an_object_is_a_usage_failure(self):
        # A list of tag names rather than release objects. Without the guard
        # `.get` raises AttributeError, which is not a usage failure any caller
        # catches, so the sweep dies with a traceback instead of exit 2.
        with self.assertRaisesRegex(ValueError, "must be an object"):
            self.load(["v3.2.0"])

    def test_a_well_formed_document_loads(self):
        releases = self.load(
            [{"version": "v3.2.0", "commit": "a" * 40, "published": "2026-02-01"}])
        self.assertEqual(releases, [cv.Release("v3.2.0", "a" * 40, "2026-02-01")])


class CommandLine(unittest.TestCase):
    def releases_file(self, payload):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(payload, handle)
        handle.close()
        return handle.name

    def test_verify_exits_nonzero_when_the_declaration_does_not_match_the_tree(self):
        root = init_repo(pathlib.Path(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "verjson-contract.json").write_text(
            json.dumps({"contract_version": "v3.2.0"}))
        (root / ".github" / "workflows" / "ci.yml").write_text(
            "jobs:\n  ci:\n    uses: Verjson/.github/.github/workflows/node-ci.yml@"
            + "b" * 40 + "\n")
        track(root)
        releases = self.releases_file(
            [{"version": "v3.2.0", "commit": "a" * 40, "published": "2026-02-01"}])
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            status = cv.main(["verify", "--repo-root", str(root), "--releases", releases,
                              "--today", "2026-02-02"])
        self.assertEqual(status, 1)
        self.assertIn("PIN_MISMATCH", captured.getvalue())

    def test_a_today_that_is_not_an_iso_date_is_rejected_at_the_flag(self):
        releases = self.releases_file(
            [{"version": "v3.2.0", "commit": "a" * 40, "published": "2026-02-01"}])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cv.main(["classify", "--version", "v3.2.0", "--releases", releases,
                     "--today", "not-a-date"])

    def test_the_default_today_is_utc_not_the_runner_local_date(self):
        # Two zones 26 hours apart, so whatever hour it is in UTC at least one
        # of them has a different local date. `date.today()` would follow TZ and
        # the expiry verdict would depend on which host ran the check.
        for zone in ("Pacific/Kiritimati", "Etc/GMT+12"):
            with self.subTest(zone=zone):
                previous = os.environ.get("TZ")
                os.environ["TZ"] = zone
                time.tzset()
                self.addCleanup(time.tzset)
                if previous is None:
                    self.addCleanup(os.environ.pop, "TZ", None)
                else:
                    self.addCleanup(os.environ.__setitem__, "TZ", previous)
                # Bracketed rather than compared against one freshly computed
                # date: midnight UTC landing between the two calls is a
                # failure of the clock, not of the function under test.
                before = datetime.datetime.now(datetime.timezone.utc).date()
                observed = cv.today_utc()
                after = datetime.datetime.now(datetime.timezone.utc).date()
                self.assertIn(observed, {before.isoformat(), after.isoformat()})

    def test_a_target_that_is_not_a_repository_is_a_usage_failure_not_a_verdict(self):
        # The scan enumerates the index, so a target with no index was never
        # compared against anything. Reporting that as conformant is the
        # fail-open shape ADR 0185 refused, and exit 2 is "the question could
        # not be asked" -- the same answer an unreadable releases file gets.
        root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        releases = self.releases_file(
            [{"version": "v3.2.0", "commit": "a" * 40, "published": "2026-02-01"}])
        with contextlib.redirect_stderr(io.StringIO()) as captured:
            status = cv.main(["verify", "--repo-root", str(root), "--releases", releases,
                              "--today", "2026-02-02"])
        self.assertEqual(status, 2)
        self.assertIn("could not enumerate", captured.getvalue())

    def test_a_releases_file_that_cannot_be_read_is_a_usage_failure_not_a_verdict(self):
        # A sweep that lost its input must not report the tree it never compared
        # as conformant; exit 2 is "the question could not be asked".
        with contextlib.redirect_stderr(io.StringIO()):
            status = cv.main(["verify", "--repo-root", ".", "--releases",
                              "/nonexistent.json", "--today", "2026-02-02"])
        self.assertEqual(status, 2)


if __name__ == "__main__":
    unittest.main()
