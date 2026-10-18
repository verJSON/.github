#!/usr/bin/env python3
"""The merge-gate review never loads agent configuration from the pull request (ADR 0237).

The gate job checks out the pull-request head and runs the pinned Claude Code action over
it with model credentials and the job token. At that pin the action loads the user,
project, and local setting sources unless `--setting-sources` says otherwise, and it
restores project configuration from the base branch only for pull-request events, never
for the `workflow_dispatch` this gate runs under.
"""
import shlex
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/ai-review-merge.yml').read_text())
GATE_STEPS = WORKFLOW['jobs']['gate']['steps']
STEPS = {step.get('id'): step for step in GATE_STEPS if step.get('id')}


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


if __name__ == '__main__':
    unittest.main()
