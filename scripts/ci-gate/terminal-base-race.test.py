#!/usr/bin/env python3
"""Red regression for a base move between terminal admission and merge.

This uses a fake GitHub CLI and makes no network requests. The positive control
proves the fixture catches the original direct-merge race; production remains
disabled until non-bypass admission is configured.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TERMINAL = ROOT / "scripts/ci-gate/terminal-merge.sh"
HEAD = "1" * 40
BASE = "2" * 40
ADVANCED_BASE = "3" * 40


def run_helper(helper: Path, root: Path) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    binaries = root / "bin"
    binaries.mkdir(parents=True)
    state = root / "base"
    state.write_text(BASE, encoding="utf-8")
    calls = root / "calls"
    fake_gh = binaries / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
state = pathlib.Path(os.environ['TEST_BASE_STATE'])
if args[:1] == ['api'] and '/pulls/' in args[1]:
    print(json.dumps({'state': 'open', 'draft': False, 'title': 'change', 'labels': [],
                      'head': {'sha': os.environ['EXPECTED_HEAD_SHA']},
                      'base': {'ref': 'main', 'sha': state.read_text()}}))
elif args[:1] == ['api'] and '/git/ref/heads/' in args[1]:
    print(state.read_text())
    state.write_text(os.environ['TEST_ADVANCED_BASE'])
elif args[:2] == ['pr', 'merge']:
    if '--auto' in args or '--merge-queue' in args:
        sys.exit(0)
    pathlib.Path(os.environ['TEST_CALLS']).write_text('direct merge dispatched')
else:
    sys.exit(90)
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    env = dict(
        os.environ,
        PATH=f"{binaries}{os.pathsep}{os.environ['PATH']}",
        GH_TOKEN="fixture-only",
        TARGET_REPO="verJSON/example",
        PR_NUMBER="7",
        EXPECTED_HEAD_SHA=HEAD,
        AUTHORIZED_BASE_SHA=BASE,
        DEFAULT_BRANCH="main",
        TEST_BASE_STATE=str(state),
        TEST_ADVANCED_BASE=ADVANCED_BASE,
        TEST_CALLS=str(calls),
    )
    result = subprocess.run(["bash", str(helper)], env=env, capture_output=True, text=True, check=False)
    return result, state, calls


class TerminalBaseRaceTest(unittest.TestCase):
    def test_fixture_reproduces_race_and_production_stays_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vulnerable = root / "vulnerable-terminal-merge.sh"
            vulnerable.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
gh api "repos/$TARGET_REPO/pulls/$PR_NUMBER" >/dev/null
gh api "repos/$TARGET_REPO/git/ref/heads/$DEFAULT_BRANCH" >/dev/null
gh pr merge "$PR_NUMBER" --repo "$TARGET_REPO" --squash
""",
                encoding="utf-8",
            )
            vulnerable.chmod(0o755)

            control, control_state, control_calls = run_helper(vulnerable, root / "control")
            self.assertEqual(control.returncode, 0, control.stderr)
            self.assertEqual(control_state.read_text(encoding="utf-8"), ADVANCED_BASE)
            self.assertTrue(control_calls.exists(), "positive control did not dispatch the vulnerable direct merge")

            production, _, production_calls = run_helper(TERMINAL, root / "production")
            self.assertNotEqual(production.returncode, 0, "terminal merge must remain disabled before rollout")
            self.assertIn("disabled pending non-bypass admission", production.stderr)
            self.assertFalse(production_calls.exists(), "production helper dispatched a direct merge")


if __name__ == "__main__":
    unittest.main()
