#!/usr/bin/env python3
"""Tests for scripts/ci-gate/adr-number-collision.py (Verjson/.github#983).

Runs the real script as a subprocess against a stub `gh` binary so the
GitHub API surface it depends on is pinned by these tests, not narrated.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("adr-number-collision.py")
REPO = "Verjson/.github"


class AdrNumberCollisionTest(unittest.TestCase):
    def run_check(self, responses, *, pr_number=983, failing_paths=None, extra_env=None):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            calls = temp / "calls.jsonl"
            gh = temp / "gh"
            gh.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['CALLS'], 'a', encoding='utf-8') as stream:\n"
                "    stream.write(json.dumps(args) + '\\n')\n"
                "paginate = '--paginate' in args\n"
                "expected = ['api', '--hostname', 'github.com', '--method', 'GET']\n"
                "if paginate:\n"
                "    expected += ['--paginate', '--slurp']\n"
                "if args[:-1] != expected:\n"
                "    print('unexpected gh arguments', file=sys.stderr); sys.exit(90)\n"
                "path = args[-1]\n"
                "failing = json.loads(os.environ['FAILING_PATHS'])\n"
                "if path in failing:\n"
                "    print('simulated HTTP failure', file=sys.stderr); sys.exit(1)\n"
                "responses = json.loads(os.environ['RESPONSES'])\n"
                "if path not in responses:\n"
                "    print('unexpected API path', file=sys.stderr); sys.exit(91)\n"
                "print(json.dumps(responses[path]))\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            env = os.environ | {
                "PATH": f"{temp}:{os.environ['PATH']}",
                "RESPONSES": json.dumps(responses),
                "FAILING_PATHS": json.dumps(failing_paths or []),
                "CALLS": str(calls),
            }
            if extra_env:
                env.update(extra_env)
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", REPO, "--pr-number", str(pr_number)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            recorded = []
            if calls.exists():
                recorded = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
            return result, recorded

    def pr_files_path(self, number=983):
        return f"repos/{REPO}/pulls/{number}/files?per_page=100"

    def main_contents_path(self):
        return f"repos/{REPO}/contents/docs/decisions?ref=main"

    def open_pulls_path(self):
        return f"repos/{REPO}/pulls?state=open&per_page=100"

    def file_entry(self, filename, status="added"):
        return {"status": status, "filename": filename}

    # 1. PR touches no docs/decisions/ paths at all -> a single API call, exit 0.
    def test_pr_with_no_adr_directories_passes_with_one_api_call(self):
        responses = {self.pr_files_path(): [[self.file_entry("scripts/foo.py")]]}
        result, calls = self.run_check(responses)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("adds no docs/decisions", result.stdout)
        self.assertEqual(len(calls), 1)

    # 2. A genuinely new number, absent from main and every other open PR.
    def test_new_unclaimed_number_passes(self):
        responses = {
            self.pr_files_path(): [[self.file_entry("docs/decisions/0114-widget/README.md")]],
            self.main_contents_path(): [{"name": "0113-thing", "type": "dir"}],
            self.open_pulls_path(): [[{"number": 983}, {"number": 990}]],
            self.pr_files_path(990): [[self.file_entry("docs/decisions/0050-old/README.md")]],
        }
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no ADR number collisions", result.stdout)

    # 3. Same number already exists on main under a different slug -> fail, name both paths.
    def test_number_already_on_main_under_different_path_fails(self):
        responses = {
            self.pr_files_path(): [[self.file_entry("docs/decisions/0004-alpha/README.md")]],
            self.main_contents_path(): [{"name": "0004-beta", "type": "dir"}],
            self.open_pulls_path(): [[{"number": 983}]],
        }
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("docs/decisions/0004-alpha", result.stderr)
        self.assertIn("docs/decisions/0004-beta", result.stderr)
        self.assertIn("already exists on main", result.stderr)

    # 4. Same number newly added by another currently-open PR -> fail, name the PR and path.
    def test_number_claimed_by_another_open_pr_fails(self):
        responses = {
            self.pr_files_path(): [[self.file_entry("docs/decisions/0004-alpha/README.md")]],
            self.main_contents_path(): [{"name": "0003-existing", "type": "dir"}],
            self.open_pulls_path(): [[{"number": 983}, {"number": 991}]],
            self.pr_files_path(991): [[self.file_entry("docs/decisions/0004-gamma/README.md")]],
        }
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("docs/decisions/0004-alpha", result.stderr)
        self.assertIn("docs/decisions/0004-gamma", result.stderr)
        self.assertIn("#991", result.stderr)

    # 5. Editing an existing, already-merged ADR (adding a new file under it) is not
    #    a new allocation -- must not fire even if an unrelated open PR misuses the
    #    same number, since that PR's own run independently catches its own mistake.
    def test_editing_an_existing_merged_adr_is_not_flagged(self):
        responses = {
            self.pr_files_path(): [[self.file_entry("docs/decisions/0042-original/diagram.svg")]],
            self.main_contents_path(): [{"name": "0042-original", "type": "dir"}],
            self.open_pulls_path(): [[{"number": 983}, {"number": 992}]],
            self.pr_files_path(992): [[self.file_entry("docs/decisions/0042-unrelated/README.md")]],
        }
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 0, result.stderr)

    # 6. A renamed (renumbered) ADR directory counts as a new allocation too.
    def test_renamed_directory_is_checked_as_a_new_allocation(self):
        responses = {
            self.pr_files_path(): [
                [{"status": "renamed", "filename": "docs/decisions/0006-widget/README.md",
                  "previous_filename": "docs/decisions/0004-widget/README.md"}]
            ],
            self.main_contents_path(): [{"name": "0006-other", "type": "dir"}],
            self.open_pulls_path(): [[{"number": 983}]],
        }
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("docs/decisions/0006-widget", result.stderr)
        self.assertIn("docs/decisions/0006-other", result.stderr)

    # 6b. Renaming your own already-merged ADR to fix a slug typo, keeping the
    #     same number, must not be flagged even though main still shows the
    #     pre-rename path (main hasn't merged the rename yet).
    def test_same_number_rename_of_own_merged_adr_is_not_flagged(self):
        responses = {
            self.pr_files_path(): [
                [{"status": "renamed", "filename": "docs/decisions/0050-fixed/README.md",
                  "previous_filename": "docs/decisions/0050-tpyo/README.md"}]
            ],
            self.main_contents_path(): [{"name": "0050-tpyo", "type": "dir"}],
            self.open_pulls_path(): [[{"number": 983}]],
        }
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no ADR number collisions", result.stdout)

    # 6c. A same-number rename against a *different* main path (a genuine
    #     collision, not this PR's own prior allocation) still fails.
    def test_same_number_rename_against_unrelated_main_path_still_fails(self):
        responses = {
            self.pr_files_path(): [
                [{"status": "renamed", "filename": "docs/decisions/0050-fixed/README.md",
                  "previous_filename": "docs/decisions/0050-tpyo/README.md"}]
            ],
            self.main_contents_path(): [{"name": "0050-someone-elses", "type": "dir"}],
            self.open_pulls_path(): [[{"number": 983}]],
        }
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("docs/decisions/0050-fixed", result.stderr)
        self.assertIn("docs/decisions/0050-someone-elses", result.stderr)

    # 6d. Two different directories added under the same number in one PR's
    #     own file list is a collision visible without any GitHub API state,
    #     and must not be silently dropped to only the first one seen.
    def test_intra_pr_duplicate_number_is_flagged(self):
        responses = {
            self.pr_files_path(): [[
                self.file_entry("docs/decisions/0050-alpha/README.md"),
                self.file_entry("docs/decisions/0050-beta/README.md"),
            ]],
            self.main_contents_path(): [{"name": "0049-existing", "type": "dir"}],
            self.open_pulls_path(): [[{"number": 983}]],
        }
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("docs/decisions/0050-alpha", result.stderr)
        self.assertIn("docs/decisions/0050-beta", result.stderr)
        self.assertIn("same number", result.stderr)

    # 6e. An intra-PR duplicate in *another* open PR's own files is that PR's
    #     own problem, caught by its own run -- must not be flagged here.
    def test_intra_pr_duplicate_in_another_open_pr_is_not_this_prs_problem(self):
        responses = {
            self.pr_files_path(): [[self.file_entry("docs/decisions/0114-widget/README.md")]],
            self.main_contents_path(): [{"name": "0113-thing", "type": "dir"}],
            self.open_pulls_path(): [[{"number": 983}, {"number": 993}]],
            self.pr_files_path(993): [[
                self.file_entry("docs/decisions/0060-alpha/README.md"),
                self.file_entry("docs/decisions/0060-beta/README.md"),
            ]],
        }
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # 7. A GitHub API failure fails closed (does not pass the PR silently).
    def test_api_failure_fails_closed(self):
        responses = {self.pr_files_path(): [[self.file_entry("docs/decisions/0114-widget/README.md")]]}
        result, _ = self.run_check(
            responses,
            failing_paths=[self.main_contents_path()],
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("failed closed", result.stderr)

    # 8. Malformed main-tree payload also fails closed rather than crashing uncaught.
    def test_malformed_main_listing_fails_closed(self):
        responses = {
            self.pr_files_path(): [[self.file_entry("docs/decisions/0114-widget/README.md")]],
            self.main_contents_path(): {"not": "a list"},
        }
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    # 8b. Genuine multi-page pagination is merged, not just a single-page list.
    def test_multi_page_pagination_is_merged_across_pr_files_and_open_pulls(self):
        responses = {
            self.pr_files_path(): [
                [self.file_entry("docs/decisions/0114-widget/README.md")],
                [self.file_entry("docs/decisions/0115-gadget/README.md")],
            ],
            self.main_contents_path(): [{"name": "0113-thing", "type": "dir"}],
            self.open_pulls_path(): [
                [{"number": 983}],
                [{"number": 990}],
            ],
            self.pr_files_path(990): [[self.file_entry("docs/decisions/0050-old/README.md")]],
        }
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0114", result.stdout)
        self.assertIn("0115", result.stdout)

    # 8c. Malformed pulls/.../files payload (this PR's own) fails closed.
    def test_malformed_pr_files_listing_fails_closed(self):
        responses = {self.pr_files_path(): {"not": "a list of pages"}}
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("failed closed", result.stderr)

    # 8d. Malformed open-pulls-list payload fails closed.
    def test_malformed_open_pulls_listing_fails_closed(self):
        responses = {
            self.pr_files_path(): [[self.file_entry("docs/decisions/0114-widget/README.md")]],
            self.main_contents_path(): [{"name": "0113-thing", "type": "dir"}],
            self.open_pulls_path(): {"not": "a list of pages"},
        }
        result, _ = self.run_check(responses)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("failed closed", result.stderr)

    # 9. Argument validation rejects a malformed --repo.
    def test_malformed_repo_argument_is_rejected(self):
        result = subprocess.run(
            ["python3", str(SCRIPT), "--repo", "not-owner-slash-repo", "--pr-number", "1"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--repo", result.stderr)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr.splitlines()[-1], "adr-number-collision.py: error: --repo must be 'owner/repo'")

    # 10. Argument validation rejects a non-positive --pr-number.
    def test_non_positive_pr_number_is_rejected(self):
        result = subprocess.run(
            ["python3", str(SCRIPT), "--repo", REPO, "--pr-number", "0"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--pr-number", result.stderr)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr.splitlines()[-1], "adr-number-collision.py: error: --pr-number must be a positive integer")

if __name__ == "__main__":
    unittest.main()
