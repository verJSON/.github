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
ATTRIBUTES_ID = 'pr-git-attributes'
# The root-level paths the pinned action treats as sensitive (restore-config.ts SENSITIVE_PATHS).
AGENT_CONFIG_PATHS = (
    '.claude', '.claude.json', '.mcp.json', 'CLAUDE.md', 'CLAUDE.local.md',
    '.gitmodules', '.ripgreprc', '.husky',
)
# The gate's former in-tree review directory: a decoy there could mislead a reviewer told to read it.
GATE_DECOY_PATHS = ('.ai-review',)


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
                # Runner state may already configure a helper that stores what it is given.
                (home / '.gitconfig').write_text('[credential]\n\thelper = store\n')
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

    def claude_config_dir(self):
        value = STEPS['claude'].get('env', {}).get('CLAUDE_CONFIG_DIR', '')
        for expression, rendered in (('${{ runner.temp }}', str(self.root / 'runner-temp')),
                                     ('${{ github.run_id }}', '4242'), ('${{ github.run_attempt }}', '3')):
            value = value.replace(expression, rendered)
        self.assertNotIn('${{', value)
        return Path(value)

    def test_claude_review_reads_its_user_layer_from_a_fresh_empty_per_run_directory(self):
        config_dir = self.claude_config_dir()
        self.assertTrue(config_dir.is_relative_to(self.root / 'runner-temp'))
        result = run_isolation(self.root, self.workspace)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(config_dir.is_dir() and not config_dir.is_symlink())
        self.assertEqual(list(config_dir.iterdir()), [])
        self.assertEqual(config_dir.stat().st_mode & 0o777, 0o700)
        # The action writes these inputs to ~/.claude, which the review no longer reads.
        self.assertFalse({'settings', 'plugins', 'plugin_marketplaces'} & set(STEPS['claude']['with']))

    def test_isolation_refuses_a_config_directory_that_already_exists(self):
        config_dir = self.claude_config_dir()
        config_dir.mkdir()
        (config_dir / 'settings.json').write_text('{"hooks": {}}\n')
        result = run_isolation(self.root, self.workspace)
        self.assertNotEqual(result.returncode, 0)

    def test_isolation_refuses_a_planted_link_at_the_config_directory(self):
        config_dir = self.claude_config_dir()
        planted = self.root / 'planted'
        planted.mkdir()
        config_dir.symlink_to(planted)
        result = run_isolation(self.root, self.workspace)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(planted.iterdir()), [])


def git(cwd, *args, env=None):
    base = {'PATH': os.defpath, 'HOME': str(cwd), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid',
            'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid'}
    return subprocess.run(['git', *args], cwd=cwd, env={**base, **(env or {})}, check=True,
                          text=True, capture_output=True).stdout.strip()


def step_outputs(path):
    """Parse a GITHUB_OUTPUT file, including `name<<DELIMITER` blocks."""
    outputs, lines = {}, path.read_text().splitlines() if path.exists() else []
    index = 0
    while index < len(lines):
        line = lines[index]
        if '<<' in line and '=' not in line.split('<<', 1)[0]:
            name, delimiter = line.split('<<', 1)
            end = lines.index(delimiter, index + 1)
            outputs[name] = '\n'.join(lines[index + 1:end])
            index = end + 1
            continue
        if '=' in line:
            name, value = line.split('=', 1)
            outputs[name] = value
        index += 1
    return outputs


class PullRequestCheckoutFixture(unittest.TestCase):
    """A checked-out pull-request head whose text changes are marked with git attributes."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        source = self.root / 'source'
        source.mkdir()
        git(source, 'init', '-q', '-b', 'main')
        (source / 'deploy.sh').write_text('echo base\n')
        (source / 'lib').mkdir()
        (source / 'lib/util.py').write_text('VALUE = 1\n')
        git(source, 'add', '-A')
        git(source, 'commit', '-qm', 'base')
        git(source, 'switch', '-qc', 'feature')
        (source / 'deploy.sh').write_text('echo base\necho hidden-shell-change\n')
        (source / 'lib/util.py').write_text('VALUE = 1\nHIDDEN = "hidden-python-change"\n')
        (source / '.gitattributes').write_text('deploy.sh -diff\n[attr]opaque binary\n')
        (source / 'lib/.gitattributes').write_text('util.py opaque diff=nonexistent\n')
        git(source, 'add', '-A')
        git(source, 'commit', '-qm', 'pull request')
        self.head = git(source, 'rev-parse', 'HEAD')
        self.workspace = self.root / 'workspace'
        git(self.root, 'clone', '-q', '--no-checkout', f'file://{source}', str(self.workspace))
        git(self.workspace, 'checkout', '-q', self.head)
        bin_path = self.root / 'bin'
        bin_path.mkdir()
        (bin_path / 'gh').write_text(
            '#!/bin/sh\ncase "$*" in\n'
            '  *headRefOid*) printf "%s\\n" "$HEAD_SHA" ;;\n'
            '  *"--jq .baseRefName"*) echo main ;;\n'
            '  *"api user"*) echo gate-bot ;;\n'
            '  *reviews,comments*) echo "{}" ;;\n'
            '  *) printf \'{"title":"t","body":"","baseRefName":"main","headRefName":"feature"}\\n\' ;;\n'
            'esac\n')
        (bin_path / 'gh').chmod(0o755)
        self.runner_temp = self.root / 'runner-temp'
        self.runner_temp.mkdir()
        self.env = {'RUNNER_TEMP': str(self.runner_temp), 'PATH': f'{bin_path}{os.pathsep}{os.defpath}', 'HOME': str(self.root), 'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_CONFIG_GLOBAL': os.devnull, 'GITHUB_WORKSPACE': str(self.workspace), 'HEAD_SHA': self.head,
                    'GITHUB_OUTPUT': str(self.root / 'output'), 'PR_NUMBER': '7', 'TARGET_REPO': 'Verjson/target',
                    'DEPENDENCY_MAJOR': 'false', 'GH_TOKEN': 'mock-job-token',
                    # Runner state may configure an external diff that prints nothing.
                    'GIT_EXTERNAL_DIFF': '/bin/true'}

    def run_step(self, step_id):
        script = STEPS[step_id]['run'].replace('${{ needs.preflight.outputs.head_sha }}', self.head)
        self.assertNotIn('${{', script)
        return subprocess.run(['bash', '-e', '-c', script], cwd=self.workspace, env=self.env,
                              text=True, capture_output=True)

    def model_input(self):
        """The review directory prep hands to every provider, and its outputs."""
        outputs = step_outputs(self.root / 'output')
        review_dir = Path(outputs.get('review_dir', ''))
        self.assertTrue(review_dir.is_absolute(), 'prep publishes no absolute review directory')
        self.assertTrue(review_dir.is_relative_to(self.runner_temp))
        self.assertFalse(review_dir.is_relative_to(self.workspace))
        return review_dir, outputs


class ReviewDiffIgnoresPullRequestAttributesTest(PullRequestCheckoutFixture):
    """A pull request must not hide a text change from the reviewer behind git attributes."""

    def test_neutralizing_step_runs_after_the_head_checkout_and_before_any_diff(self):
        self.assertTrue(ATTRIBUTES_ID in STEPS, 'the gate job has no git attribute neutralization step')
        position = GATE_STEPS.index(STEPS[ATTRIBUTES_ID])
        self.assertLess(checkout_index(), position)
        self.assertLess(position, GATE_STEPS.index(STEPS['rereview']))
        self.assertEqual(STEPS[ATTRIBUTES_ID].get('if'), GATE_STEPS[checkout_index()].get('if'))

    def test_review_diff_shows_attribute_marked_changes_as_text_hunks(self):
        self.assertTrue(ATTRIBUTES_ID in STEPS, 'the gate job has no git attribute neutralization step')
        neutralize = self.run_step(ATTRIBUTES_ID)
        self.assertEqual(neutralize.returncode, 0, neutralize.stderr)
        prep = self.run_step('prep')
        self.assertEqual(prep.returncode, 0, prep.stderr + prep.stdout)
        review_dir, _ = self.model_input()
        diff = (review_dir / 'pr.diff').read_text()
        self.assertIn('+echo hidden-shell-change\n', diff)
        self.assertIn('+HIDDEN = "hidden-python-change"\n', diff)
        self.assertNotIn('Binary files', diff)

    def test_rereview_patch_id_binds_the_attribute_marked_text(self):
        fake = self.root / 'fake-diff'
        fake.write_text('#!/bin/sh\nprintf "diff --git a/x b/x\\n--- a/x\\n+++ b/x\\n@@ -1 +1 @@\\n-a\\n+b\\n"\n')
        fake.chmod(0o755)
        self.env['GIT_EXTERNAL_DIFF'] = str(fake)
        self.assertEqual(self.run_step(ATTRIBUTES_ID).returncode, 0)
        result = self.run_step('rereview')
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = dict(line.split('=', 1) for line in (self.root / 'output').read_text().splitlines() if '=' in line)
        base = git(self.workspace, 'merge-base', 'origin/main', 'HEAD')
        neutral = {'GIT_CONFIG_GLOBAL': os.devnull}
        text_diff = git(self.workspace, 'diff', '--no-ext-diff', '--no-textconv', base, 'HEAD', env=neutral)
        self.assertIn('+echo hidden-shell-change', text_diff)
        expected = subprocess.run(['git', 'patch-id', '--stable'], input=text_diff + '\n', cwd=self.workspace,
                                  text=True, capture_output=True, check=True).stdout.split()[0]
        self.assertEqual(outputs.get('patch_id'), expected)

    def test_neutralizing_replaces_a_planted_link_without_writing_through_it(self):
        outside = self.root / 'runner-file'
        outside.write_text('runner\n')
        attributes = Path(git(self.workspace, 'rev-parse', '--absolute-git-dir')) / 'info/attributes'
        attributes.parent.mkdir(exist_ok=True)
        attributes.unlink(missing_ok=True)
        attributes.symlink_to(outside)
        result = self.run_step(ATTRIBUTES_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outside.read_text(), 'runner\n')
        self.assertFalse(attributes.is_symlink())
        self.assertEqual(attributes.read_text(), '* !diff\n')


class ReviewInputsStayOutsideTheCheckoutTest(PullRequestCheckoutFixture):
    """Links the pull request commits where the gate writes must not redirect or empty the review input."""

    def plant(self, link, target):
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)

    def test_planted_links_neither_empty_the_model_input_nor_reach_their_targets(self):
        self.assertEqual(self.run_step(ATTRIBUTES_ID).returncode, 0)
        attributes = Path(git(self.workspace, 'rev-parse', '--absolute-git-dir')) / 'info/attributes'
        targets = {name: self.root / f'runner-{name}' for name in ('pr.full.diff', 'pr.diff', 'base-fetch.error')}
        for name, target in targets.items():
            target.write_text('runner state\n')
            self.plant(self.workspace / '.ai-review' / name, target)
        self.plant(self.workspace / '.ai-review/pr.json', attributes)
        prep = self.run_step('prep')
        self.assertEqual(prep.returncode, 0, prep.stderr + prep.stdout)
        review_dir, outputs = self.model_input()
        self.assertEqual(outputs.get('review_input_available'), 'true')
        self.assertIn('+echo hidden-shell-change\n', (review_dir / 'pr.diff').read_text())
        self.assertIn(f'{review_dir}/pr.diff', outputs.get('review_prompt', ''))
        for name, target in targets.items():
            with self.subTest(path=name):
                self.assertEqual(target.read_text(), 'runner state\n')
        self.assertEqual(attributes.read_text(), '* !diff\n')

    def test_a_planted_review_directory_link_is_never_written_through(self):
        outside = self.root / 'runner-dir'
        outside.mkdir()
        self.plant(self.workspace / '.ai-review', outside)
        self.assertEqual(self.run_step(ATTRIBUTES_ID).returncode, 0)
        prep = self.run_step('prep')
        self.assertEqual(prep.returncode, 0, prep.stderr + prep.stdout)
        review_dir, _ = self.model_input()
        self.assertIn('+echo hidden-shell-change\n', (review_dir / 'pr.diff').read_text())
        self.assertEqual(list(outside.iterdir()), [])

    def test_review_directory_is_private_to_the_run(self):
        self.assertEqual(self.run_step('prep').returncode, 0)
        review_dir, _ = self.model_input()
        self.assertFalse(review_dir.is_symlink())
        self.assertEqual(review_dir.stat().st_mode & 0o777, 0o700)


class TelemetryStaysOutsideTheCheckoutTest(unittest.TestCase):
    def test_telemetry_payload_is_never_written_through_a_planted_link(self):
        step = next(step for step in GATE_STEPS if step.get('name') == 'Build ai-review telemetry payload')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, runner_temp, outside = root / 'workspace', root / 'runner-temp', root / 'runner-dir'
            for path in (workspace, runner_temp, outside):
                path.mkdir()
            (workspace / '.telemetry').symlink_to(outside)
            env = {'PATH': os.defpath, 'RUNNER_TEMP': str(runner_temp), 'GITHUB_OUTPUT': str(root / 'output'),
                   'VERDICT_JSON': '{"blocking":false,"findings":[]}', 'TARGET_REPO': 'Verjson/target',
                   'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_WORKFLOW': 'AI review'}
            for name in ('ACTION_CONCLUSION', 'BUDGET_USD', 'DEPENDENCY_MAJOR', 'EXECUTION_FILE', 'FALLBACK_COST_USD',
                         'INPUT_TOKEN_BOUND', 'LANE', 'LANE_REASON', 'MAX_OUTPUT_TOKENS', 'MODEL', 'PRICING_VERSION',
                         'PRIMARY_COST_USD', 'PROVIDER', 'REPORTED_COST_USD', 'REPORTED_INPUT_TOKENS',
                         'REPORTED_OUTPUT_TOKENS'):
                env[name] = ''
            result = subprocess.run(['bash', '-c', step['run']], cwd=workspace, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(len(list(runner_temp.rglob('ai-review.json'))), 1)


class RemovalPrecedesReviewTest(unittest.TestCase):
    def test_removal_runs_immediately_after_the_head_checkout_under_its_condition(self):
        checkout = checkout_index()
        removal = GATE_STEPS[checkout + 1]
        self.assertEqual(removal.get('id'), REMOVAL_ID)
        self.assertEqual(removal.get('if'), GATE_STEPS[checkout].get('if'))
        self.assertLess(checkout + 1, GATE_STEPS.index(STEPS['claude']))

    def test_no_consumer_runs_unless_each_protection_succeeded(self):
        pairs = (('claude', REMOVAL_ID), ('claude', ISOLATION_ID), ('claude', ATTRIBUTES_ID),
                 ('rereview', ATTRIBUTES_ID), ('prep', ATTRIBUTES_ID), ('prep', REMOVAL_ID))
        for consumer_id, protection_id in pairs:
            protection, consumer = STEPS[protection_id], STEPS[consumer_id]
            tolerated = bool(protection.get('continue-on-error'))
            for lane, provider, skip_model, allowed, fails in itertools.product(
                ('ai', 'fast'), ('anthropic', 'openai', 'deepseek'), ('true', 'false'), ('true', 'false'), (True, False),
            ):
                values = {
                    'needs.preflight.outputs.lane': lane,
                    'needs.preflight.outputs.provider': provider,
                    'steps.rereview.outputs.skip_model': skip_model,
                    'steps.reserve_1.outputs.allowed': allowed,
                }
                ran = evaluate_condition('${{ %s }}' % protection['if'], values, True)
                outcome = 'skipped' if not ran else 'failure' if fails else 'success'
                values[f'steps.{protection_id}.outcome'] = outcome
                job_succeeded = not (outcome == 'failure' and not tolerated)
                consumer_runs = evaluate_condition('${{ %s }}' % consumer['if'], values, job_succeeded)
                with self.subTest(consumer=consumer_id, protection=protection_id, lane=lane, provider=provider,
                                  skip_model=skip_model, allowed=allowed, fails=fails):
                    self.assertFalse(consumer_runs and outcome != 'success')

def plant_agent_config(workspace):
    (workspace / '.claude').mkdir()
    (workspace / '.claude/settings.json').write_text('{"hooks": {}}\n')
    (workspace / '.claude/settings.local.json').write_text('{"env": {}}\n')
    (workspace / '.husky').mkdir()
    (workspace / '.husky/pre-commit').write_text('exit 0\n')
    for name in ('.claude.json', '.mcp.json', 'CLAUDE.md', 'CLAUDE.local.md', '.gitmodules', '.ripgreprc'):
        (workspace / name).write_text('pull-request content\n')
    (workspace / '.ai-review').mkdir()
    (workspace / '.ai-review/pr.diff').write_text('decoy\n')


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
        for name in AGENT_CONFIG_PATHS + GATE_DECOY_PATHS:
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
