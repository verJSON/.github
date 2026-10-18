#!/usr/bin/env python3
"""The merge-gate review never loads agent configuration from the pull request (ADR 0237).

The gate job checks out the pull-request head and runs the pinned Claude Code action over
it with model credentials and the job token. At that pin the action loads the user,
project, and local setting sources unless `--setting-sources` says otherwise, and it
restores project configuration from the base branch only for pull-request events, never
for the `workflow_dispatch` this gate runs under.
"""
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/ai-review-merge.yml').read_text())
GATE_STEPS = WORKFLOW['jobs']['gate']['steps']
STEPS = {step.get('id'): step for step in GATE_STEPS if step.get('id')}
REMOVAL_ID = 'pr-agent-config'
# The root-level paths the pinned action treats as sensitive (restore-config.ts SENSITIVE_PATHS).
AGENT_CONFIG_PATHS = (
    '.claude', '.claude.json', '.mcp.json', 'CLAUDE.md', 'CLAUDE.local.md',
    '.gitmodules', '.ripgreprc', '.husky',
)


def claude_flags(step):
    """Flags as the pinned action's parseClaudeArgsToExtraArgs reads `claude_args`.

    A flag takes the next token as its value unless that token is another flag; a
    repeated non-accumulating flag keeps its last value, so every occurrence is kept here.
    """
    text = '\n'.join(line for line in step['with']['claude_args'].splitlines()
                     if not line.strip().startswith('#'))
    tokens = shlex.split(text)
    flags = []
    for index, token in enumerate(tokens):
        if not token.startswith('--'):
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        value = following if following is not None and not following.startswith('--') else None
        flags.append((token[2:], value))
    return flags


class ClaudeReviewArgumentsTest(unittest.TestCase):
    def test_claude_review_loads_only_the_runner_user_setting_source(self):
        sources = [value for flag, value in claude_flags(STEPS['claude']) if flag == 'setting-sources']
        self.assertEqual(sources, ['user'])

    def test_claude_review_starts_only_explicitly_configured_mcp_servers(self):
        strict = [value for flag, value in claude_flags(STEPS['claude']) if flag == 'strict-mcp-config']
        # A value would be consumed as the flag's argument and passed on as a stray token.
        self.assertEqual(strict, [None])


def plant_agent_config(workspace):
    (workspace / '.claude').mkdir()
    (workspace / '.claude/settings.json').write_text('{"hooks": {}}\n')
    (workspace / '.claude/settings.local.json').write_text('{"env": {}}\n')
    (workspace / '.husky').mkdir()
    (workspace / '.husky/pre-commit').write_text('exit 0\n')
    for name in ('.claude.json', '.mcp.json', 'CLAUDE.md', 'CLAUDE.local.md', '.gitmodules', '.ripgreprc'):
        (workspace / name).write_text('pull-request content\n')


def run_removal(workspace):
    step = STEPS[REMOVAL_ID]
    env = {'PATH': os.defpath, 'GITHUB_WORKSPACE': str(workspace)}
    # Run from elsewhere: the step must act on the workspace, not on whatever cwd it inherits.
    return subprocess.run(['bash', '-e', '-c', step['run']], env=env, cwd='/', text=True, capture_output=True)


class PullRequestAgentConfigRemovalTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(REMOVAL_ID in STEPS, 'the gate job has no pull-request agent configuration removal step')
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()

    def test_removal_deletes_every_agent_config_path_and_keeps_reviewed_code(self):
        plant_agent_config(self.workspace)
        (self.workspace / 'README.md').write_text('readme\n')
        (self.workspace / 'AGENTS.md').write_text('agents\n')
        (self.workspace / 'src').mkdir()
        (self.workspace / 'src/app.py').write_text('print(1)\n')
        result = run_removal(self.workspace)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in AGENT_CONFIG_PATHS:
            with self.subTest(path=name):
                self.assertFalse(os.path.lexists(self.workspace / name))
        self.assertEqual(sorted(path.name for path in self.workspace.iterdir()), ['AGENTS.md', 'README.md', 'src'])
        self.assertEqual((self.workspace / 'src/app.py').read_text(), 'print(1)\n')

    def test_removal_succeeds_when_the_head_carries_no_agent_config(self):
        (self.workspace / 'README.md').write_text('readme\n')
        result = run_removal(self.workspace)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.workspace / 'README.md').exists())


if __name__ == '__main__':
    unittest.main()
