#!/usr/bin/env python3
"""The merge-gate review never loads agent configuration from the pull request (ADR 0237).

The gate job checks out the pull-request head and runs the pinned Claude Code action over
it with model credentials and the job token. At that pin the action loads the user,
project, and local setting sources unless `--setting-sources` says otherwise, and it
restores project configuration from the base branch only for pull-request events, never
for the `workflow_dispatch` this gate runs under.
"""
import itertools
import os
import re
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
ISOLATION_ID = 'claude-isolation'
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


def evaluate_condition(condition, values, job_succeeded):
    """Evaluate a step `if:` the way Actions does for the expressions this job uses.

    Without a status function Actions applies an implicit `success()`.
    """
    expression = condition.removeprefix('${{').removesuffix('}}').strip()
    has_status_function = re.search(r'\b(always|success|failure|cancelled)\(\)', expression)
    expression = re.sub(r'\b(?:steps|needs)\.[\w-]+\.(?:outputs\.[\w-]+|outcome|conclusion|result)',
                        lambda match: repr(values.get(match.group(0), '')), expression)
    expression = (expression.replace('always()', 'True').replace('cancelled()', 'False')
                  .replace('success()', repr(job_succeeded)).replace('failure()', repr(not job_succeeded)))
    expression = re.sub(r'(?<![\w\'"])true(?![\w\'"])', 'True', expression)
    expression = re.sub(r'(?<![\w\'"])false(?![\w\'"])', 'False', expression)
    expression = expression.replace('&&', ' and ').replace('||', ' or ')
    expression = re.sub(r'!(?!=)', 'not ', expression)
    result = eval(expression, {'__builtins__': {}}, {})  # noqa: S307 - workflow text under test
    return bool(result) and (job_succeeded or bool(has_status_function))


def checkout_index():
    names = [step.get('name') for step in GATE_STEPS]
    return names.index('Checkout PR head')


class ReviewCheckoutCredentialTest(unittest.TestCase):
    def test_head_checkout_does_not_persist_the_job_token(self):
        checkout = GATE_STEPS[checkout_index()]
        self.assertIs(checkout['with'].get('persist-credentials'), False)

    def test_every_later_fetch_authenticates_from_its_own_step_environment(self):
        fetching = [step for step in GATE_STEPS[checkout_index() + 1:] if 'git fetch' in step.get('run', '')]
        self.assertEqual(sorted(step.get('id') for step in fetching), ['prep', 'rereview'])
        for step in fetching:
            with self.subTest(step=step['id']), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                subprocess.run(['git', 'init', '-q', str(home / 'repo')], check=True)
                env = {'PATH': os.defpath, 'HOME': str(home), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
                for name, value in step.get('env', {}).items():
                    if name == 'GH_TOKEN' or name.startswith('GIT_CONFIG_'):
                        env[name] = (str(value).replace('${{ github.server_url }}', 'https://github.com')
                                     .replace('${{ github.token }}', 'mock-job-token'))
                request = 'protocol=https\nhost=github.com\npath=Verjson/target.git\n\n'
                fill = subprocess.run(['git', 'credential', 'fill'], input=request, env=env,
                                      cwd=home / 'repo', text=True, capture_output=True)
                self.assertEqual(fill.returncode, 0, fill.stderr)
                self.assertIn('password=mock-job-token\n', fill.stdout)
                subprocess.run(['git', 'credential', 'approve'], input=fill.stdout, env=env,
                               cwd=home / 'repo', text=True, capture_output=True)
                written = [path for path in home.rglob('*') if path.is_file() and b'mock-job-token' in path.read_bytes()]
                self.assertEqual(written, [])


def run_isolation(root, workspace, extra_env=None):
    env = {'PATH': os.defpath, 'HOME': str(root), 'GIT_CONFIG_NOSYSTEM': '1', 'GITHUB_WORKSPACE': str(workspace),
           'RUNNER_TEMP': str(root / 'runner-temp'), 'GITHUB_RUN_ID': '4242', 'GITHUB_RUN_ATTEMPT': '3'}
    env.update(extra_env or {})
    return subprocess.run(['bash', '-e', '-c', STEPS[ISOLATION_ID]['run']], env=env, cwd='/',
                          text=True, capture_output=True)


class ClaudeIsolationStepTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(ISOLATION_ID in STEPS, 'the gate job has no Claude isolation step')
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'runner-temp').mkdir()
        self.workspace = self.root / 'workspace'
        subprocess.run(['git', 'init', '-q', str(self.workspace)], check=True)
        subprocess.run(['git', '-C', str(self.workspace), 'remote', 'add', 'origin', 'https://github.com/Verjson/target.git'],
                       check=True)

    def test_isolation_runs_immediately_before_the_claude_review_under_its_condition(self):
        claude = GATE_STEPS.index(STEPS['claude'])
        self.assertEqual(GATE_STEPS[claude - 1].get('id'), ISOLATION_ID)
        self.assertEqual(STEPS[ISOLATION_ID].get('if'), STEPS['claude'].get('if'))

    def test_action_cannot_write_the_job_token_into_the_review_checkout_remote(self):
        result = run_isolation(self.root, self.workspace)
        self.assertEqual(result.returncode, 0, result.stderr)
        # The pinned action's configureGitAuth runs exactly this, inside a try/catch.
        set_url = subprocess.run(['git', 'remote', 'set-url', 'origin', 'https://x-access-token:mock-job-token@github.com/Verjson/target.git'],
                                 cwd=self.workspace, env={'PATH': os.defpath, 'HOME': str(self.root), 'GIT_CONFIG_NOSYSTEM': '1'},
                                 text=True, capture_output=True)
        self.assertNotEqual(set_url.returncode, 0)
        self.assertNotIn('mock-job-token', (self.workspace / '.git/config').read_text())


class RemovalPrecedesReviewTest(unittest.TestCase):
    def test_removal_runs_immediately_after_the_head_checkout_under_its_condition(self):
        checkout = checkout_index()
        removal = GATE_STEPS[checkout + 1]
        self.assertEqual(removal.get('id'), REMOVAL_ID)
        self.assertEqual(removal.get('if'), GATE_STEPS[checkout].get('if'))
        self.assertLess(checkout + 1, GATE_STEPS.index(STEPS['claude']))

    def test_claude_review_never_runs_unless_the_removal_succeeded(self):
        removal = STEPS[REMOVAL_ID]
        claude = STEPS['claude']
        removal_tolerated = bool(removal.get('continue-on-error'))
        for lane, provider, skip_model, allowed, removal_fails in itertools.product(
            ('ai', 'fast'), ('anthropic', 'openai', 'deepseek'), ('true', 'false'), ('true', 'false'), (True, False),
        ):
            values = {
                'needs.preflight.outputs.lane': lane,
                'needs.preflight.outputs.provider': provider,
                'steps.rereview.outputs.skip_model': skip_model,
                'steps.reserve_1.outputs.allowed': allowed,
            }
            removal_ran = evaluate_condition('${{ %s }}' % removal['if'], values, True)
            outcome = 'skipped' if not removal_ran else 'failure' if removal_fails else 'success'
            values[f'steps.{REMOVAL_ID}.outcome'] = outcome
            job_succeeded = not (outcome == 'failure' and not removal_tolerated)
            claude_runs = evaluate_condition('${{ %s }}' % claude['if'], values, job_succeeded)
            with self.subTest(lane=lane, provider=provider, skip_model=skip_model, allowed=allowed,
                              removal_fails=removal_fails):
                self.assertFalse(claude_runs and outcome != 'success')


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

    def test_removal_unlinks_symlinks_without_touching_their_targets(self):
        outside = self.root / 'runner-state'
        outside.mkdir()
        (outside / 'keep').write_text('runner\n')
        (self.workspace / 'AGENTS.md').write_text('agents\n')
        (self.workspace / 'CLAUDE.md').symlink_to('AGENTS.md')
        (self.workspace / '.mcp.json').symlink_to(outside / 'keep')
        (self.workspace / '.claude').symlink_to(outside)
        (self.workspace / '.husky').mkdir()
        (self.workspace / '.husky/state').symlink_to(outside)
        result = run_removal(self.workspace)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ('CLAUDE.md', '.mcp.json', '.claude', '.husky'):
            with self.subTest(path=name):
                self.assertFalse(os.path.lexists(self.workspace / name))
        self.assertEqual((self.workspace / 'AGENTS.md').read_text(), 'agents\n')
        self.assertEqual((outside / 'keep').read_text(), 'runner\n')

    @unittest.skipIf(os.geteuid() == 0, 'root ignores directory write permission')
    def test_removal_fails_closed_when_a_path_cannot_be_removed(self):
        plant_agent_config(self.workspace)
        self.workspace.chmod(0o555)
        self.addCleanup(self.workspace.chmod, 0o755)
        result = run_removal(self.workspace)
        self.assertNotEqual(result.returncode, 0)

    def test_removal_fails_closed_without_a_workspace(self):
        plant_agent_config(self.workspace)
        step = STEPS[REMOVAL_ID]
        result = subprocess.run(['bash', '-e', '-c', step['run']], env={'PATH': os.defpath},
                                cwd=self.workspace, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.workspace / '.mcp.json').exists())


if __name__ == '__main__':
    unittest.main()
