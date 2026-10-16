#!/usr/bin/env python3

import argparse
import contextlib
import importlib.util
import io
import datetime
import hashlib
import json
import pathlib
import socket
import sys
import threading
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("package_retention", ROOT / "package_retention.py")
assert SPEC and SPEC.loader
retention = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = retention
SPEC.loader.exec_module(retention)


class PackageRetentionTest(unittest.TestCase):
    def test_npm_keeps_three_highest_stable_versions_and_ignores_prereleases(self):
        target = retention.Target("npm", "cli-cloud", "4.0.0")
        versions = [
            {"id": 1, "name": "1.0.0"},
            {"id": 2, "name": "2.0.0"},
            {"id": 3, "name": "2.10.0"},
            {"id": 4, "name": "3.0.0-beta.1"},
            {"id": 5, "name": "4.0.0"},
        ]

        plan = retention.plan_target(target, versions)

        self.assertEqual([(item.version_id, item.labels) for item in plan], [(1, ("1.0.0",))])

    def test_npm_keep_is_configurable_and_defaults_to_three(self):
        target = retention.Target("npm", "cli-cloud", "5.0.0")
        versions = [
            {"id": 1, "name": "1.0.0"},
            {"id": 2, "name": "2.0.0"},
            {"id": 3, "name": "3.0.0"},
            {"id": 4, "name": "4.0.0"},
            {"id": 5, "name": "5.0.0"},
        ]

        default_plan = retention.plan_target(target, versions)
        self.assertEqual([item.version_id for item in default_plan], [1, 2])

        widened_plan = retention.plan_target(target, versions, keep=5)
        self.assertEqual(widened_plan, [])

    def test_keep_must_be_a_positive_integer(self):
        target = retention.Target("npm", "cli-cloud", "1.0.0")
        versions = [{"id": 1, "name": "1.0.0"}]
        for invalid in (0, -1, True):
            with self.assertRaisesRegex(retention.RetentionError, "positive integer"):
                retention.plan_target(target, versions, keep=invalid)

    def test_keep_policy_accepts_only_all_or_an_integer_from_1_to_999(self):
        self.assertEqual(retention.parse_keep("3"), 3)
        self.assertEqual(retention.parse_keep("1"), 1)
        self.assertEqual(retention.parse_keep("999"), 999)
        self.assertIs(retention.parse_keep("all"), retention.KEEP_ALL)
        for invalid in (
            "0", "00", "03", "-1", "+3", " 3", "3 ", "1000", str(2**64), "1e3", "3.0",
            "", "ALL", "All", "aLL", " all", "all ", "none", "\u0663", "\uff13", "\u0661\u0660",
        ):
            with self.subTest(value=invalid):
                with self.assertRaises(argparse.ArgumentTypeError) as raised:
                    retention.parse_keep(invalid)
                self.assertIn("an integer from 1 to 999 or 'all'", str(raised.exception))

    def run_main(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["package_retention.py", *arguments]), \
                mock.patch.dict(retention.os.environ, {"GH_TOKEN": "token"}), \
                mock.patch.object(retention, "GitHubPackages", side_effect=AssertionError("API used")), \
                mock.patch.object(retention, "build_plan", side_effect=AssertionError("planned")), \
                mock.patch.object(retention, "apply_plan", side_effect=AssertionError("deleted")), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                status = retention.main()
            except SystemExit as exit_:
                status = exit_.code
        return status, stdout.getvalue(), stderr.getvalue()

    def test_keep_all_disables_retention_without_listing_or_deleting(self):
        targets = '[{"type":"npm","name":"cli","releasedVersion":"1.0.0"}]'
        status, stdout, _ = self.run_main(
            "--owner", "Verjson", "--targets", targets, "--keep", "all", "--apply"
        )
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "package-retention: disabled by policy (keep=all)\n")

    def test_keep_all_still_validates_the_targets(self):
        with self.assertRaisesRegex(retention.RetentionError, "non-empty JSON array"):
            self.run_main("--owner", "Verjson", "--targets", "[]", "--keep", "all")

    def test_the_cli_refuses_a_keep_outside_the_policy_language(self):
        targets = '[{"type":"npm","name":"cli","releasedVersion":"1.0.0"}]'
        for invalid in ("0", "ALL", "1000", "\u0663"):
            with self.subTest(value=invalid):
                status, _, stderr = self.run_main(
                    "--owner", "Verjson", "--targets", targets, "--keep", invalid
                )
                self.assertEqual(status, 2)
                self.assertIn("an integer from 1 to 999 or 'all'", stderr)

    def test_container_safety_honors_a_configured_keep(self):
        target = retention.Target("container", "api", "3.0.0")
        now = datetime.datetime(2026, 8, 18, tzinfo=datetime.timezone.utc)

        def raw_manifest(marker):
            return json.dumps(
                {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json", "annotations": {"marker": marker}},
                separators=(",", ":"),
            ).encode()

        indexes = []
        for version in ("1.0.0", "2.0.0", "3.0.0"):
            raw = raw_manifest(version)
            indexes.append((version, raw, f"sha256:{hashlib.sha256(raw).hexdigest()}"))
        versions = [
            {"id": index, "name": digest, "created_at": "2026-07-01T00:00:00Z", "metadata": {"container": {"tags": [version]}}}
            for index, (version, _, digest) in enumerate(indexes, 1)
        ]
        inspector = mock.Mock()
        inspector.raw.side_effect = {f"ghcr.io/verjson/api:{version}": raw for version, raw, _ in indexes}.__getitem__

        default_safety = retention._container_safety("Verjson", target, versions, inspector, now)
        self.assertEqual(len(default_safety.protected_version_ids), 3)

        narrowed_safety = retention._container_safety("Verjson", target, versions, inspector, now, keep=1)
        self.assertEqual(narrowed_safety.protected_version_ids, {3})

    def test_build_plan_and_apply_plan_thread_keep_through_to_container_safety(self):
        target = retention.Target("container", "api", "3.0.0")

        def raw_manifest(marker):
            return json.dumps(
                {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json", "annotations": {"marker": marker}},
                separators=(",", ":"),
            ).encode()

        indexes = []
        for version in ("1.0.0", "2.0.0", "3.0.0"):
            raw = raw_manifest(version)
            indexes.append((version, raw, f"sha256:{hashlib.sha256(raw).hexdigest()}"))
        versions = [
            {"id": index, "name": digest, "created_at": "2020-01-01T00:00:00Z", "metadata": {"container": {"tags": [version]}}}
            for index, (version, _, digest) in enumerate(indexes, 1)
        ]

        class Client:
            def __init__(self):
                self.deleted = []

            def versions(self, _target):
                return versions

            def delete(self, deletion):
                self.deleted.append(deletion)

        inspector = mock.Mock()
        inspector.raw.side_effect = {f"ghcr.io/verjson/api:{version}": raw for version, raw, _ in indexes}.__getitem__
        client = Client()

        plan = retention.build_plan(
            client,
            [target],
            owner="Verjson",
            inspector=inspector,
            now=datetime.datetime(2026, 8, 18, tzinfo=datetime.timezone.utc),
            keep=1,
        )

        self.assertEqual([item.version_id for item in plan], [1, 2])

        retention.apply_plan(client, plan, "Verjson", inspector, keep=1)
        self.assertEqual([deletion.version_id for deletion in client.deleted], [1, 2])

    def test_container_safety_rejects_a_non_positive_keep_directly(self):
        target = retention.Target("container", "api", "1.0.0")
        now = datetime.datetime(2026, 8, 18, tzinfo=datetime.timezone.utc)
        versions = [
            {"id": 1, "name": "sha256:" + "0" * 64, "created_at": "2026-07-01T00:00:00Z", "metadata": {"container": {"tags": ["1.0.0"]}}},
        ]
        inspector = mock.Mock()
        for invalid in (0, -1, True):
            with self.assertRaisesRegex(retention.RetentionError, "positive integer"):
                retention._container_safety("Verjson", target, versions, inspector, now, keep=invalid)

    def test_build_plan_and_apply_plan_fail_closed_on_a_non_positive_keep_for_a_container_target(self):
        # Gap: `_container_safety` must reject an invalid `keep` on its own, not merely
        # rely on `build_plan` calling `plan_target` (which also validates) for the same
        # target first. Drive the invalid value through the real `build_plan`/`apply_plan`
        # call chain — the path `apply_plan` actually uses — rather than only exercising
        # `plan_target`'s or `_container_safety`'s boundary directly.
        target = retention.Target("container", "api", "1.0.0")

        def raw_manifest(marker):
            return json.dumps(
                {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json", "annotations": {"marker": marker}},
                separators=(",", ":"),
            ).encode()

        raw = raw_manifest("1.0.0")
        digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        versions = [
            {"id": 1, "name": digest, "created_at": "2020-01-01T00:00:00Z", "metadata": {"container": {"tags": ["1.0.0"]}}},
        ]

        class Client:
            def __init__(self):
                self.deleted = []

            def versions(self, _target):
                return versions

            def delete(self, deletion):
                self.deleted.append(deletion)

        inspector = mock.Mock()
        inspector.raw.side_effect = {f"ghcr.io/verjson/api:1.0.0": raw}.__getitem__
        client = Client()
        now = datetime.datetime(2026, 8, 18, tzinfo=datetime.timezone.utc)

        for invalid in (0, -1):
            with self.assertRaisesRegex(retention.RetentionError, "positive integer"):
                retention.build_plan(client, [target], owner="Verjson", inspector=inspector, now=now, keep=invalid)
            with self.assertRaisesRegex(retention.RetentionError, "positive integer"):
                retention.apply_plan(client, [retention.Deletion(target, 1, "numbered release older than newest 1", ("1.0.0",))], "Verjson", inspector, keep=invalid)
        self.assertEqual(client.deleted, [])

    def test_container_deletes_old_numbered_and_only_prevalidated_untagged_versions(self):
        target = retention.Target("container", "api", "1.3.0")
        versions = [
            {"id": 10, "metadata": {"container": {"tags": ["1.0.0"]}}},
            {"id": 11, "metadata": {"container": {"tags": ["1.1.0"]}}},
            {"id": 12, "metadata": {"container": {"tags": ["1.2.0"]}}},
            {"id": 13, "metadata": {"container": {"tags": ["1.3.0"]}}},
            {"id": 14, "metadata": {"container": {"tags": []}}},
            {"id": 15, "metadata": {"container": {"tags": ["candidate-abc"]}}},
        ]

        plan = retention.plan_target(target, versions, deletable_untagged_ids={14})

        self.assertEqual([(item.version_id, item.reason) for item in plan], [
            (10, "numbered release older than newest 3"),
            (14, "untagged container version"),
        ])

    def test_inventory_all_targets_completes_before_first_deletion(self):
        targets = [
            retention.Target("npm", "one", "1.0.0"),
            retention.Target("npm", "two", "1.0.0"),
        ]

        class Client:
            def __init__(self):
                self.reads = []

            def versions(self, target):
                self.reads.append(target.name)
                if target.name == "two":
                    raise retention.RetentionError("ambiguous")
                return [{"id": 1, "name": "1.0.0"}]

        client = Client()
        with self.assertRaisesRegex(retention.RetentionError, "ambiguous"):
            retention.build_plan(client, targets)
        self.assertEqual(client.reads, ["one", "two"])

    def test_fails_closed_when_released_version_is_missing(self):
        target = retention.Target("npm", "cli", "2.0.0")
        with self.assertRaisesRegex(retention.RetentionError, "released version"):
            retention.plan_target(target, [{"id": 1, "name": "1.0.0"}])

    def test_fails_closed_instead_of_deleting_a_historically_resumed_release(self):
        target = retention.Target("npm", "cli", "1.0.0")
        versions = [
            {"id": 1, "name": "1.0.0"},
            {"id": 2, "name": "2.0.0"},
            {"id": 3, "name": "3.0.0"},
            {"id": 4, "name": "4.0.0"},
        ]
        with self.assertRaisesRegex(retention.RetentionError, "historical cleanup"):
            retention.plan_target(target, versions)

    def test_container_numbered_releases_may_share_their_digest_with_aliases(self):
        target = retention.Target("container", "api", "4.0.0")
        versions = [
            {"id": 1, "metadata": {"container": {"tags": ["1.0.0", "1"]}}},
            {"id": 2, "metadata": {"container": {"tags": ["2.0.0", "2"]}}},
            {"id": 3, "metadata": {"container": {"tags": ["3.0.0", "3"]}}},
            {"id": 4, "metadata": {"container": {"tags": ["4.0.0", "4", "latest"]}}},
        ]

        plan = retention.plan_target(target, versions)

        self.assertEqual([(item.version_id, item.labels) for item in plan], [(1, ("1.0.0", "1"))])

    def test_fails_closed_when_container_digest_has_multiple_numbered_tags(self):
        target = retention.Target("container", "api", "2.0.0")
        versions = [{"id": 1, "metadata": {"container": {"tags": ["1.0.0", "2.0.0"]}}}]
        with self.assertRaisesRegex(retention.RetentionError, "multiple numbered releases"):
            retention.plan_target(target, versions)

    def test_fails_closed_on_duplicate_semver_or_version_id(self):
        target = retention.Target("npm", "cli", "2.0.0")
        with self.assertRaisesRegex(retention.RetentionError, "multiple version ids"):
            retention.plan_target(target, [{"id": 1, "name": "2.0.0"}, {"id": 2, "name": "2.0.0"}])
        with self.assertRaisesRegex(retention.RetentionError, "duplicate version id"):
            retention.plan_target(target, [{"id": 1, "name": "2.0.0"}, {"id": 1, "name": "1.0.0"}])

    def test_target_boundary_rejects_duplicates_prereleases_and_unknown_fields(self):
        with self.assertRaises(retention.RetentionError):
            retention.parse_targets('[{"type":"npm","name":"cli","releasedVersion":"1.0.0-beta.1"}]', "Verjson")
        with self.assertRaises(retention.RetentionError):
            retention.parse_targets('[{"type":"npm","name":"cli","releasedVersion":"1.0.0","extra":true}]', "Verjson")
        with self.assertRaises(retention.RetentionError):
            retention.parse_targets('[{"type":"npm","name":"cli","releasedVersion":"1.0.0"},{"type":"npm","name":"cli","releasedVersion":"1.0.0"}]', "Verjson")
        with self.assertRaises(retention.RetentionError):
            retention.parse_targets('[{"type":"npm","name":"scope/name","releasedVersion":"1.0.0"}]', "Verjson")
        with self.assertRaises(retention.RetentionError):
            retention.parse_targets('[{"type":"container","name":"scope//name","releasedVersion":"1.0.0"}]', "Verjson")

    def test_pagination_must_stay_on_configured_api_origin(self):
        with self.assertRaisesRegex(retention.RetentionError, "escaped"):
            retention._next_path('<https://evil.invalid/p?page=2>; rel="next"', "https://api.github.com")
        self.assertEqual(
            retention._next_path('<https://api.github.com/p?page=2>; rel="next"', "https://api.github.com"),
            "/p?page=2",
        )
        self.assertEqual(
            retention._next_path('<https://api.github.com/p?page=1>; rel="first", <https://api.github.com/p?page=1>; rel="prev"', "https://api.github.com"),
            "",
        )

    def test_versions_accepts_a_full_first_page_and_final_page_without_next_link(self):
        target = retention.Target("npm", "cli", "101.0.0")
        client = retention.GitHubPackages("Verjson", "token", "https://api.github.test")
        first = [{"id": number, "name": f"{number}.0.0"} for number in range(1, 101)]
        final = [{"id": 101, "name": "101.0.0"}]
        client._request = mock.Mock(side_effect=[
            (first, '<https://api.github.test/p?page=2>; rel="next"'),
            (final, '<https://api.github.test/p?page=1>; rel="prev", <https://api.github.test/p?page=1>; rel="first"'),
        ])

        versions = client.versions(target)

        self.assertEqual(len(versions), 101)
        self.assertEqual(client._request.call_count, 2)

    def test_multiarch_children_and_fresh_untagged_versions_are_never_deleted(self):
        target = retention.Target("container", "api", "3.0.0")
        now = datetime.datetime(2026, 8, 18, tzinfo=datetime.timezone.utc)

        def raw_manifest(media_type, **extra):
            return json.dumps({"schemaVersion": 2, "mediaType": media_type, **extra}, separators=(",", ":")).encode()

        child_raw = raw_manifest("application/vnd.oci.image.manifest.v1+json")
        child_digest = f"sha256:{hashlib.sha256(child_raw).hexdigest()}"
        artifact_child_raw = raw_manifest(
            "application/vnd.oci.image.manifest.v1+json",
            annotations={"artifact-child": "true"},
        )
        artifact_child_digest = f"sha256:{hashlib.sha256(artifact_child_raw).hexdigest()}"
        provenance_raw = raw_manifest(
            "application/vnd.oci.image.manifest.v1+json",
            artifactType="application/vnd.in-toto+json",
            manifests=[{"digest": artifact_child_digest}],
            annotations={"vnd.docker.reference.type": "attestation-manifest"},
        )
        provenance_digest = f"sha256:{hashlib.sha256(provenance_raw).hexdigest()}"
        indexes = []
        for version in ("1.0.0", "2.0.0", "3.0.0"):
            raw = raw_manifest(
                "application/vnd.oci.image.index.v1+json",
                manifests=[
                    {"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": child_digest, "platform": {"os": "linux", "architecture": "amd64"}},
                    {"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": provenance_digest, "platform": {"os": "unknown", "architecture": "unknown"}, "annotations": {"vnd.docker.reference.type": "attestation-manifest"}},
                ],
                annotations={"version": version},
            )
            indexes.append((version, raw, f"sha256:{hashlib.sha256(raw).hexdigest()}"))
        orphan_raw = raw_manifest("application/vnd.oci.image.manifest.v1+json", annotations={"orphan": "true"})
        orphan_digest = f"sha256:{hashlib.sha256(orphan_raw).hexdigest()}"
        artifact_raw = raw_manifest(
            "application/vnd.oci.image.manifest.v1+json",
            artifactType="application/vnd.in-toto+json",
        )
        artifact_digest = f"sha256:{hashlib.sha256(artifact_raw).hexdigest()}"
        fresh_raw = raw_manifest("application/vnd.oci.image.manifest.v1+json", annotations={"fresh": "true"})
        fresh_digest = f"sha256:{hashlib.sha256(fresh_raw).hexdigest()}"
        versions = [
            {"id": index, "name": digest, "created_at": "2026-07-01T00:00:00Z", "metadata": {"container": {"tags": [version]}}}
            for index, (version, _, digest) in enumerate(indexes, 1)
        ] + [
            {"id": 10, "name": child_digest, "created_at": "2026-07-01T00:00:00Z", "metadata": {"container": {"tags": []}}},
            {"id": 13, "name": provenance_digest, "created_at": "2026-07-01T00:00:00Z", "metadata": {"container": {"tags": []}}},
            {"id": 15, "name": artifact_child_digest, "created_at": "2026-07-01T00:00:00Z", "metadata": {"container": {"tags": []}}},
            {"id": 11, "name": orphan_digest, "created_at": "2026-07-01T00:00:00Z", "metadata": {"container": {"tags": []}}},
            {"id": 12, "name": fresh_digest, "created_at": "2026-08-17T12:00:00Z", "metadata": {"container": {"tags": []}}},
            {"id": 14, "name": artifact_digest, "created_at": "2026-07-01T00:00:00Z", "metadata": {"container": {"tags": []}}},
        ]
        raw_by_reference = {
            **{f"ghcr.io/verjson/api:{version}": raw for version, raw, _ in indexes},
            f"ghcr.io/verjson/api@{child_digest}": child_raw,
            f"ghcr.io/verjson/api@{provenance_digest}": provenance_raw,
            f"ghcr.io/verjson/api@{artifact_child_digest}": artifact_child_raw,
            f"ghcr.io/verjson/api@{orphan_digest}": orphan_raw,
            f"ghcr.io/verjson/api@{artifact_digest}": artifact_raw,
            f"ghcr.io/verjson/api@{fresh_digest}": fresh_raw,
        }
        inspector = mock.Mock()
        inspector.raw.side_effect = raw_by_reference.__getitem__

        safety = retention._container_safety("Verjson", target, versions, inspector, now)

        self.assertEqual(safety.deletable_untagged_ids, {11})
        self.assertNotIn(13, safety.deletable_untagged_ids)
        self.assertIn(10, safety.protected_version_ids)
        self.assertIn(13, safety.protected_version_ids)
        self.assertIn(15, safety.protected_version_ids)
        self.assertNotIn(15, safety.deletable_untagged_ids)
        self.assertIn(14, safety.protected_version_ids)
        self.assertNotIn(14, safety.deletable_untagged_ids)
        self.assertNotIn(f"ghcr.io/verjson/api@{fresh_digest}", [call.args[0] for call in inspector.raw.call_args_list])

    def test_untagged_manifest_bytes_must_match_the_package_digest(self):
        target = retention.Target("container", "api", "3.0.0")
        raw = json.dumps(
            {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json"},
            separators=(",", ":"),
        ).encode()
        stable_digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        claimed_digest = "sha256:" + "a" * 64
        versions = [
            {"id": 1, "name": stable_digest, "created_at": "2026-07-01T00:00:00Z", "metadata": {"container": {"tags": ["3.0.0"]}}},
            {"id": 2, "name": claimed_digest, "created_at": "2026-07-01T00:00:00Z", "metadata": {"container": {"tags": []}}},
        ]
        inspector = mock.Mock()
        inspector.raw.return_value = raw

        with self.assertRaisesRegex(retention.RetentionError, "does not match GitHub package digest"):
            retention._container_safety(
                "Verjson",
                target,
                versions,
                inspector,
                datetime.datetime(2026, 8, 18, tzinfo=datetime.timezone.utc),
                keep=1,
            )

    def test_invalid_artifact_type_fails_closed(self):
        raw_document = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
        }
        for artifact_type in (None, "", "   ", {"malformed": True}):
            with self.subTest(artifact_type=artifact_type):
                raw_document["artifactType"] = artifact_type
                raw = json.dumps(raw_document, separators=(",", ":")).encode()
                with self.assertRaisesRegex(retention.RetentionError, "invalid artifactType"):
                    retention._oci_document(raw, "ghcr.io/verjson/api@sha256:" + "a" * 64)

    def test_retained_artifact_with_malformed_children_fails_closed(self):
        target = retention.Target("container", "api", "3.0.0")
        raw = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "artifactType": "application/vnd.in-toto+json",
                "manifests": "malformed",
            },
            separators=(",", ":"),
        ).encode()
        digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        versions = [
            {"id": 1, "name": digest, "created_at": "2026-07-01T00:00:00Z", "metadata": {"container": {"tags": ["3.0.0"]}}},
        ]
        inspector = mock.Mock()
        inspector.raw.return_value = raw

        with self.assertRaisesRegex(retention.RetentionError, "ambiguous child descriptors"):
            retention._container_safety(
                "Verjson",
                target,
                versions,
                inspector,
                datetime.datetime(2026, 8, 18, tzinfo=datetime.timezone.utc),
                keep=1,
            )

    def test_retained_alias_tagged_index_protects_its_untagged_children(self):
        target = retention.Target("container", "api", "3.0.0")
        now = datetime.datetime(2026, 8, 18, tzinfo=datetime.timezone.utc)

        child_raw = json.dumps({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
        }, separators=(",", ":")).encode()
        child_digest = f"sha256:{hashlib.sha256(child_raw).hexdigest()}"
        index_raw = json.dumps({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [{"digest": child_digest}],
        }, separators=(",", ":")).encode()
        index_digest = f"sha256:{hashlib.sha256(index_raw).hexdigest()}"
        versions = [
            {
                "id": 1,
                "name": index_digest,
                "created_at": "2026-07-01T00:00:00Z",
                "metadata": {"container": {"tags": ["3.0.0", "3", "latest"]}},
            },
            {
                "id": 2,
                "name": child_digest,
                "created_at": "2026-07-01T00:00:00Z",
                "metadata": {"container": {"tags": []}},
            },
        ]
        inspector = mock.Mock()
        inspector.raw.side_effect = {
            "ghcr.io/verjson/api:3.0.0": index_raw,
            f"ghcr.io/verjson/api@{child_digest}": child_raw,
        }.__getitem__

        safety = retention._container_safety("Verjson", target, versions, inspector, now)

        self.assertIn(2, safety.protected_version_ids)
        self.assertNotIn(2, safety.deletable_untagged_ids)

    def test_nested_retained_index_protects_an_old_numbered_digest(self):
        target = retention.Target("container", "api", "3.0.0")
        now = datetime.datetime(2026, 8, 18, tzinfo=datetime.timezone.utc)

        def raw_manifest(media_type, manifests=None, marker=""):
            value = {"schemaVersion": 2, "mediaType": media_type, "annotations": {"marker": marker}}
            if manifests is not None:
                value["manifests"] = manifests
            return json.dumps(value, separators=(",", ":")).encode()

        old_raw = raw_manifest("application/vnd.oci.image.manifest.v1+json", marker="old-numbered")
        old_digest = f"sha256:{hashlib.sha256(old_raw).hexdigest()}"
        nested_raw = raw_manifest(
            "application/vnd.oci.image.index.v1+json",
            manifests=[{"digest": old_digest}],
            marker="nested",
        )
        nested_digest = f"sha256:{hashlib.sha256(nested_raw).hexdigest()}"
        indexes = []
        for version in ("1.0.0", "2.0.0", "3.0.0"):
            children = [{"digest": nested_digest}] if version == "3.0.0" else []
            raw = raw_manifest("application/vnd.oci.image.index.v1+json", manifests=children, marker=version)
            indexes.append((version, raw, f"sha256:{hashlib.sha256(raw).hexdigest()}"))
        versions = [
            {"id": 99, "name": old_digest, "created_at": "2026-01-01T00:00:00Z", "metadata": {"container": {"tags": ["0.5.0"]}}},
            *[
                {"id": index, "name": digest, "created_at": "2026-07-01T00:00:00Z", "metadata": {"container": {"tags": [version]}}}
                for index, (version, _, digest) in enumerate(indexes, 1)
            ],
        ]
        raw_by_reference = {
            **{f"ghcr.io/verjson/api:{version}": raw for version, raw, _ in indexes},
            f"ghcr.io/verjson/api@{nested_digest}": nested_raw,
            f"ghcr.io/verjson/api@{old_digest}": old_raw,
        }
        inspector = mock.Mock()
        inspector.raw.side_effect = raw_by_reference.__getitem__

        safety = retention._container_safety("Verjson", target, versions, inspector, now)
        plan = retention.plan_target(
            target,
            versions,
            protected_version_ids=set(safety.protected_version_ids),
        )

        self.assertIn(99, safety.protected_version_ids)
        self.assertNotIn(99, [deletion.version_id for deletion in plan])

    def test_apply_reinventories_and_refuses_when_a_concurrent_run_changed_versions(self):
        target = retention.Target("npm", "cli", "4.0.0")
        initial = [
            {"id": 1, "name": "1.0.0"},
            {"id": 2, "name": "2.0.0"},
            {"id": 3, "name": "3.0.0"},
            {"id": 4, "name": "4.0.0"},
        ]
        changed = initial[1:]

        class Client:
            def __init__(self):
                self.inventories = [initial, changed]
                self.deleted = []

            def versions(self, _target):
                return self.inventories.pop(0)

            def delete(self, deletion):
                self.deleted.append(deletion)

        client = Client()
        plan = retention.build_plan(client, [target])

        with self.assertRaisesRegex(retention.RetentionError, "inventory changed"):
            retention.apply_plan(client, plan, "Verjson", mock.Mock())
        self.assertEqual(client.deleted, [])

    def test_delete_uses_encoded_supported_package_endpoint_and_bearer_token(self):
        target = retention.Target("container", "studio/api", "1.0.0")
        deletion = retention.Deletion(target, 42, "old", ("0.1.0",))
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b""
        response.__enter__.return_value.headers.get.return_value = None
        client = retention.GitHubPackages("Verjson", "test-token", "https://api.github.test")

        with mock.patch.object(retention.urllib.request, "urlopen", return_value=response) as urlopen:
            client.delete(deletion)

        request = urlopen.call_args.args[0]
        self.assertEqual(request.method, "DELETE")
        self.assertEqual(
            request.full_url,
            "https://api.github.test/orgs/Verjson/packages/container/studio%2Fapi/versions/42",
        )
        self.assertEqual(request.headers["Authorization"], "Bearer test-token")

    def test_request_bounds_every_call_with_a_socket_timeout(self):
        target = retention.Target("container", "studio/api", "1.0.0")
        deletion = retention.Deletion(target, 42, "old", ("0.1.0",))
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b""
        response.__enter__.return_value.headers.get.return_value = None
        client = retention.GitHubPackages("Verjson", "test-token", "https://api.github.test")

        with mock.patch.object(retention.urllib.request, "urlopen", return_value=response) as urlopen:
            client.delete(deletion)

        # A stalled connection to the GitHub API must fail fast rather than
        # block the job indefinitely until an external job-level timeout
        # kills it with zero diagnostic output (observed: a 30-minute hang
        # with no output at all on a real release run).
        self.assertIn("timeout", urlopen.call_args.kwargs)
        self.assertGreater(urlopen.call_args.kwargs["timeout"], 0)

    def test_a_connect_phase_timeout_fails_closed_with_a_reported_reason(self):
        target = retention.Target("container", "studio/api", "1.0.0")
        deletion = retention.Deletion(target, 42, "old", ("0.1.0",))
        client = retention.GitHubPackages("Verjson", "test-token", "https://api.github.test")
        timeout_error = retention.urllib.error.URLError(TimeoutError("timed out"))

        with mock.patch.object(retention.urllib.request, "urlopen", side_effect=timeout_error):
            with self.assertRaises(retention.RetentionError) as context:
                client.delete(deletion)

        self.assertIn("timed out", str(context.exception))

    def test_a_response_that_stalls_after_connecting_fails_closed(self):
        # urlopen wraps only a connect-phase failure in URLError. A server
        # that accepts the connection and reads the request but never
        # responds raises a *bare* TimeoutError from getresponse()/read() —
        # a different, unwrapped failure surface the handler must also catch.
        # This is real-world the more likely shape of the observed incident
        # (a live release run producing zero output for 30 minutes), so it is
        # exercised here against a real local socket, not a mocked exception.
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        stop = threading.Event()

        def accept_and_stall():
            server.settimeout(5)
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(5)
                try:
                    conn.recv(65536)
                except OSError:
                    return
                stop.wait(5)

        thread = threading.Thread(target=accept_and_stall, daemon=True)
        thread.start()
        try:
            client = retention.GitHubPackages("Verjson", "test-token", f"http://127.0.0.1:{port}")
            with mock.patch.object(retention, "REQUEST_TIMEOUT_SECONDS", 0.5):
                with self.assertRaises(retention.RetentionError) as context:
                    client.versions(retention.Target("npm", "studio-api", "1.0.0"))
            self.assertIn("did not respond", str(context.exception))
        finally:
            stop.set()
            server.close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
