#!/usr/bin/env python3
"""Red regression for a base move between terminal admission and merge.

This uses a fake GitHub CLI and makes no network requests. It must be wired into
the merge-gate test group with the implementation that makes it pass.
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


class TerminalBaseRaceTest(unittest.TestCase):
    def test_base_move_after_final_read_never_dispatches_direct_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binaries = root / "bin"
            binaries.mkdir()
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
    assert state.read_text() == os.environ['TEST_ADVANCED_BASE']
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
            result = subprocess.run(
                ["bash", str(TERMINAL)], env=env, capture_output=True, text=True, check=False
            )

            self.assertFalse(calls.exists(), "direct merge dispatched after the authorized base moved")
            self.assertNotEqual(result.returncode, 0, "base move must not report terminal success")


if __name__ == "__main__":
    unittest.main()
