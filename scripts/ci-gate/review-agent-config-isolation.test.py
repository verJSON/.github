#!/usr/bin/env python3
"""The merge-gate review never loads agent configuration from the pull request (ADR 0237).

The gate job checks out the pull-request head and runs the pinned Claude Code action over
it with model credentials and the job token. At that pin the action loads the user,
project, and local setting sources unless `--setting-sources` says otherwise, and it
restores project configuration from the base branch only for pull-request events, never
for the `workflow_dispatch` this gate runs under.
"""
import itertools
import json
import os
import re
import shutil
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

    def test_claude_review_offers_only_read_only_tools(self):
        tools = [value for flag, value in claude_flags(STEPS['claude']) if flag == 'tools']
        self.assertEqual(tools, ['Read,Grep,Glob'])

    def test_claude_review_reads_only_the_checkout_and_its_review_directory(self):
        flags = claude_flags(STEPS['claude'])
        # A bare allow rule for a read tool permits reads anywhere the runner user can read.
        self.assertEqual([value for flag, value in flags if flag in ('allowedTools', 'allowed-tools')], [])
        self.assertEqual([value for flag, value in flags if flag == 'add-dir'], ['${{ steps.prep.outputs.review_dir }}'])

    def test_claude_review_denies_reads_of_process_state(self):
        denied = [value for flag, value in claude_flags(STEPS['claude']) if flag in ('disallowedTools', 'disallowed-tools')]
        rules = {rule.strip() for value in denied for rule in value.split(',')}
        self.assertIn('Read(//proc/**)', rules)


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


class VerdictCredentialRefusalWiringTest(unittest.TestCase):
    EXPECTED = {
        'ANTHROPIC_API_KEY': '${{ secrets.ANTHROPIC_API_KEY }}',
        'CLAUDE_CODE_OAUTH_TOKEN': '${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}',
        'OPENAI_API_KEY': '${{ secrets.OPENAI_API_KEY }}',
        'DEEPSEEK_API_KEY': '${{ secrets.DEEPSEEK_API_KEY }}',
        'GITHUB_TOKEN': '${{ github.token }}',
    }

    def test_every_verdict_validation_sees_the_values_it_must_refuse(self):
        validator = (ROOT / 'scripts/ci-gate/review-verdict.py').read_text()
        names = re.search(r'SECRET_ENVIRONMENT = \((.*?)\)', validator, re.S).group(1)
        self.assertEqual(set(re.findall(r'"([A-Z_]+)"', names)), set(self.EXPECTED))
        for step_id in ('verdict_1', 'verdict_2'):
            env = STEPS[step_id].get('env', {})
            with self.subTest(step=step_id):
                self.assertEqual({name: env.get(name) for name in self.EXPECTED}, self.EXPECTED)


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

    @unittest.skipIf(os.geteuid() == 0, 'root ignores directory write permission')
    def test_isolation_fails_when_the_remote_cannot_be_removed(self):
        git_dir = self.workspace / '.git'
        git_dir.chmod(0o555)
        self.addCleanup(git_dir.chmod, 0o755)
        result = run_isolation(self.root, self.workspace)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('url = https://github.com/Verjson/target.git', (git_dir / 'config').read_text())

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
            '  *reviews,comments*) cat "$PRDATA" 2>/dev/null || echo "{}" ;;\n'
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

    def add_head_commit(self, files):
        for relative, content in files.items():
            path = self.workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if content is None:
                git(self.workspace, 'rm', '-q', '--', relative)
                continue
            if isinstance(content, tuple):
                kind, value = content
                if os.path.lexists(path):
                    git(self.workspace, 'rm', '-q', '--cached', '--', relative)
                    path.unlink()
                if kind == 'symlink':
                    path.symlink_to(value)
                    git(self.workspace, 'add', '--', relative)
                else:
                    # A submodule pointer needs only its commit id in the index.
                    git(self.workspace, 'update-index', '--add', '--cacheinfo', f'160000,{value},{relative}')
                continue
            if path.is_symlink():
                path.unlink()
            path.write_bytes(content) if isinstance(content, bytes) else path.write_text(content)
            git(self.workspace, 'add', '--', relative)
        git(self.workspace, 'commit', '-qm', 'more changes')
        self.head = git(self.workspace, 'rev-parse', 'HEAD')
        self.env['HEAD_SHA'] = self.head

    def publish_as_base(self):
        """Make the current head the base branch, so the next commit is the whole change."""
        git(self.workspace, 'push', '-q', 'origin', 'HEAD:refs/heads/main')

    def prep(self):
        self.assertEqual(self.run_step(ATTRIBUTES_ID).returncode, 0)
        (self.root / 'output').unlink(missing_ok=True)
        return self.run_step('prep'), step_outputs(self.root / 'output')

    def assert_reviewed(self, *expected_lines):
        result, outputs = self.prep()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(outputs.get('review_input_failure'), 'none')
        diff = (Path(outputs['review_dir']) / 'pr.diff').read_text()
        for line in expected_lines:
            self.assertIn(line, diff)
        return diff

    def rereview_outputs(self):
        (self.root / 'output').unlink(missing_ok=True)
        result = self.run_step('rereview')
        self.assertEqual(result.returncode, 0, result.stderr)
        return step_outputs(self.root / 'output')

    def approve_current_head(self):
        self.assertEqual(self.run_step(ATTRIBUTES_ID).returncode, 0)
        patch_id = self.rereview_outputs().get('patch_id', '')
        self.assertRegex(patch_id, r'^[0-9a-f]{40}$')
        body = f'approved <!-- ai-review-head:{self.head} patchid:{patch_id} model:m -->'
        prdata = self.root / 'prdata.json'
        prdata.write_text(json.dumps({'reviews': [{'state': 'APPROVED', 'author': {'login': 'gate-bot'},
                                                    'submittedAt': '2026-10-18T00:00:00Z', 'body': body}],
                                      'comments': []}))
        self.env['PRDATA'] = str(prdata)

    def model_input(self):
        """The review directory prep hands to every provider, and its outputs."""
        outputs = step_outputs(self.root / 'output')
        review_dir = Path(outputs.get('review_dir', ''))
        self.assertTrue(review_dir.is_absolute(), 'prep publishes no absolute review directory')
        self.assertTrue(review_dir.is_relative_to(self.runner_temp))
        self.assertFalse(review_dir.is_relative_to(self.workspace))
        return review_dir, outputs


class ReviewDiffKeepsEveryPathSpellingTest(PullRequestCheckoutFixture):
    """Lockfile elision must select files by path, never by parsing diff headers."""

    def reviewed_diff(self):
        self.assertEqual(self.run_step(ATTRIBUTES_ID).returncode, 0)
        prep = self.run_step('prep')
        self.assertEqual(prep.returncode, 0, prep.stderr + prep.stdout)
        review_dir, outputs = self.model_input()
        return (review_dir / 'pr.diff').read_text(), outputs

    def test_a_spaced_path_reaches_the_model_input(self):
        self.add_head_commit({'docs/release notes.md': 'spaced-path-content\n'})
        diff, _ = self.reviewed_diff()
        self.assertIn('+spaced-path-content\n', diff)

    def test_a_spaced_path_naming_a_lockfile_is_reviewed_not_elided(self):
        self.add_head_commit({'tools/run b/yarn.lock now.sh': 'lockfile-named-content\n'})
        diff, outputs = self.reviewed_diff()
        self.assertIn('+lockfile-named-content\n', diff)
        self.assertNotIn('Generated lockfiles were omitted', outputs.get('review_prompt', ''))

    def test_a_path_with_a_newline_reaches_the_model_input(self):
        self.add_head_commit({'notes/line\nbreak yarn.lock': 'newline-path-content\n'})
        diff, _ = self.reviewed_diff()
        self.assertIn('+newline-path-content\n', diff)

    def test_real_lockfiles_at_any_depth_are_elided_and_reported(self):
        self.add_head_commit({'yarn.lock': 'root-lock-content\n', 'svc/app dir/Cargo.lock': 'nested-lock-content\n'})
        diff, outputs = self.reviewed_diff()
        self.assertNotIn('lock-content', diff)
        self.assertIn('+echo hidden-shell-change', diff)
        self.assertIn('Generated lockfiles were omitted', outputs.get('review_prompt', ''))


class BinaryContentNeedsHumanReviewTest(PullRequestCheckoutFixture):
    """No provider can read a binary section, so only inert binary types may be AI-approved."""

    def prep(self):
        self.assertEqual(self.run_step(ATTRIBUTES_ID).returncode, 0)
        (self.root / 'output').unlink(missing_ok=True)
        return self.run_step('prep'), step_outputs(self.root / 'output')

    def assert_human_review_required(self):
        result, outputs = self.prep()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outputs.get('review_input_available'), 'false')
        self.assertEqual(outputs.get('review_input_failure'), 'binary_content_unreviewable')
        self.assertNotIn('review_dir', outputs)

    def test_a_script_carrying_a_nul_byte_is_not_ai_reviewable(self):
        self.add_head_commit({'tools/setup.sh': b'echo visible\n\x00\necho not-shown\n'})
        self.assert_human_review_required()

    def test_an_archive_is_not_ai_reviewable(self):
        self.add_head_commit({'vendor/bundle.zip': b'PK\x03\x04\x00\x00opaque'})
        self.assert_human_review_required()

    def test_an_image_or_font_stays_ai_reviewable(self):
        self.add_head_commit({'assets/logo.PNG': b'\x89PNG\r\n\x1a\n\x00\x00', 'assets/body.woff2': b'wOF2\x00\x01'})
        result, outputs = self.prep()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(outputs.get('review_input_available'), 'true')

    def test_deleting_a_binary_stays_ai_reviewable(self):
        self.add_head_commit({'tools/old.bin': b'\x00\x01'})
        git(self.workspace, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
        self.add_head_commit({'tools/old.bin': None})
        result, outputs = self.prep()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(outputs.get('review_input_available'), 'true')


class RereviewSkipBindsExactContentTest(PullRequestCheckoutFixture):
    """An approval may be reused only for byte-identical changes, whitespace included."""

    def rereview(self):
        return self.rereview_outputs()

    def test_an_unchanged_diff_reuses_its_approval(self):
        self.approve_current_head()
        self.assertEqual(self.rereview().get('skip_model'), 'true')

    def test_a_whitespace_only_change_inside_a_command_is_reviewed_again(self):
        self.approve_current_head()
        self.add_head_commit({'deploy.sh': 'echo base\necho hidden-shell- change\n'})
        self.assertEqual(self.rereview().get('skip_model'), 'false')

    def test_a_git_without_verbatim_patch_ids_never_skips(self):
        self.approve_current_head()
        wrapper = self.root / 'bin' / 'git'
        wrapper.write_text('#!/bin/sh\nfor argument in "$@"; do [ "$argument" = --verbatim ] && exit 129; done\n'
                           f'exec {shutil.which("git")} "$@"\n')
        wrapper.chmod(0o755)
        outputs = self.rereview()
        self.assertEqual(outputs.get('skip_model'), 'false')
        self.assertEqual(outputs.get('patch_id'), '')


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
        expected = subprocess.run(['git', 'patch-id', '--verbatim'], input=text_diff + '\n', cwd=self.workspace,
                                  text=True, capture_output=True, check=True).stdout.split()[0]
        self.assertEqual(outputs.get('patch_id'), expected)

    def test_neutralizing_refuses_a_planted_link_at_the_attributes_file(self):
        outside = self.root / 'runner-file'
        outside.write_text('runner\n')
        attributes = Path(git(self.workspace, 'rev-parse', '--absolute-git-dir')) / 'info/attributes'
        attributes.parent.mkdir(exist_ok=True)
        attributes.unlink(missing_ok=True)
        attributes.symlink_to(outside)
        result = self.run_step(ATTRIBUTES_ID)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outside.read_text(), 'runner\n')
        self.assertTrue(attributes.is_symlink())

    def test_neutralizing_refuses_a_planted_link_at_the_info_directory(self):
        outside = self.root / 'runner-dir'
        outside.mkdir()
        info = Path(git(self.workspace, 'rev-parse', '--absolute-git-dir')) / 'info'
        shutil.rmtree(info, ignore_errors=True)
        info.symlink_to(outside)
        result = self.run_step(ATTRIBUTES_ID)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])

    def test_neutralizing_rewrites_its_own_previous_override(self):
        self.assertEqual(self.run_step(ATTRIBUTES_ID).returncode, 0)
        result = self.run_step(ATTRIBUTES_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        attributes = Path(git(self.workspace, 'rev-parse', '--absolute-git-dir')) / 'info/attributes'
        self.assertEqual(attributes.read_text(), '* !diff\n')

class ReviewInputsStayOutsideTheCheckoutTest(PullRequestCheckoutFixture):
    """Links the pull request commits where the gate writes must not redirect or empty the review input."""

    def plant(self, link, target):
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)

    def test_planted_links_neither_empty_the_model_input_nor_reach_their_targets(self):
        self.assertEqual(self.run_step(ATTRIBUTES_ID).returncode, 0)
        attributes = Path(git(self.workspace, 'rev-parse', '--absolute-git-dir')) / 'info/attributes'
        targets = {name: self.root / f'runner-{name}' for name in ('pr.diff', 'reviewed.status', 'base-fetch.error')}
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
        self.assertEqual(self.run_step(ATTRIBUTES_ID).returncode, 0)
        self.assertEqual(self.run_step('prep').returncode, 0)
        review_dir, _ = self.model_input()
        self.assertFalse(review_dir.is_symlink())
        self.assertEqual(review_dir.stat().st_mode & 0o777, 0o700)


class ReviewDiffIgnoresTextconvTest(PullRequestCheckoutFixture):
    """--no-textconv alone keeps a configured conversion driver from rewriting the reviewed text."""

    def setUp(self):
        super().setUp()
        (self.workspace / 'settings.conf').write_text('mode = review-sees-this-raw-line\n')
        # Only the conversion driver is in play: no attribute here marks a file binary.
        (self.workspace / '.gitattributes').write_text('settings.conf diff=conv\n')
        git(self.workspace, 'rm', '-q', 'lib/.gitattributes')
        git(self.workspace, 'add', 'settings.conf', '.gitattributes')
        git(self.workspace, 'commit', '-qm', 'converted file')
        self.head = git(self.workspace, 'rev-parse', 'HEAD')
        self.env['HEAD_SHA'] = self.head
        converter = self.root / 'converter'
        converter.write_text('#!/bin/sh\necho converted-text-hides-the-change\n')
        converter.chmod(0o755)
        # Persistent-runner shape: the reused checkout's own config names the driver.
        git(self.workspace, 'config', 'diff.conv.textconv', str(converter))

    def test_prep_renders_raw_text_for_a_textconv_attribute(self):
        prep = self.run_step('prep')
        self.assertEqual(prep.returncode, 0, prep.stderr + prep.stdout)
        review_dir, _ = self.model_input()
        diff = (review_dir / 'pr.diff').read_text()
        self.assertIn('+mode = review-sees-this-raw-line\n', diff)
        self.assertNotIn('converted-text-hides-the-change', diff)

    def test_rereview_patch_id_binds_raw_text_for_a_textconv_attribute(self):
        result = self.run_step('rereview')
        self.assertEqual(result.returncode, 0, result.stderr)
        base = git(self.workspace, 'merge-base', 'origin/main', 'HEAD')
        raw = git(self.workspace, 'diff', '--no-ext-diff', '--no-textconv', base, 'HEAD')
        self.assertIn('+mode = review-sees-this-raw-line', raw)
        expected = subprocess.run(['git', 'patch-id', '--verbatim'], input=raw + '\n', cwd=self.workspace,
                                  text=True, capture_output=True, check=True).stdout.split()[0]
        self.assertEqual(step_outputs(self.root / 'output').get('patch_id'), expected)


POINTER_BASE, POINTER_HEAD, POINTER_LATER = 'a1' * 20, 'b2' * 20, 'c3' * 20
IGNORE_ALL = '[submodule "vendor/lib"]\n\tpath = vendor/lib\n\turl = ../lib.git\n\tignore = all\n'
TRACKED = '[submodule "vendor/lib"]\n\tpath = vendor/lib\n\turl = ../lib.git\n'


class EveryChangeShapeReachesTheReviewTest(PullRequestCheckoutFixture):
    """Type changes, renames, submodule pointers, and binary lockfiles keep the review complete and accurate."""

    def test_file_to_symlink_and_back_are_reviewed(self):
        for name, file_content in (('tools/run.sh', 'echo run\n'), ('assets/icon.png', b'\x89PNG\r\n\x1a\n\x00')):
            for to_link in (True, False):
                with self.subTest(path=name, to_link=to_link):
                    self.setUp()
                    first, second = (file_content, ('symlink', 'target')) if to_link else (('symlink', 'target'), file_content)
                    self.add_head_commit({name: first})
                    self.publish_as_base()
                    self.add_head_commit({name: second})
                    diff = self.assert_reviewed(f'diff --git a/{name} b/{name}')
                    self.assertEqual(diff.count(f'diff --git a/{name} b/{name}'), 2)

    def test_a_rename_is_reviewed_as_one_section(self):
        # Runner configuration may turn git's default rename detection off.
        git(self.workspace, 'config', 'diff.renames', 'false')
        body = ''.join(f'line_{index} = {index}\n' for index in range(20))
        self.add_head_commit({'src/module.py': body})
        self.publish_as_base()
        self.add_head_commit({'src/renamed.py': body + 'added_after_rename = True\n', 'src/module.py': None})
        diff = self.assert_reviewed('rename from src/module.py', 'rename to src/renamed.py', '+added_after_rename = True')
        self.assertEqual(diff.count('diff --git '), 1)

    def test_a_binary_lockfile_is_elided_not_sent_to_a_human(self):
        self.add_head_commit({'bun.lockb': b'\x00\x01binary-lock'})
        diff = self.assert_reviewed('+echo hidden-shell-change')
        self.assertNotIn('bun.lockb', diff)

    def submodule_pr(self, base_modules, head_modules):
        self.add_head_commit({'.gitmodules': base_modules, 'vendor/lib': ('gitlink', POINTER_BASE)})
        self.publish_as_base()
        self.add_head_commit({'.gitmodules': head_modules, 'vendor/lib': ('gitlink', POINTER_HEAD),
                              'deploy.sh': 'echo submodule-pr\n'})

    def test_a_pointer_change_under_an_ignore_setting_on_the_base_is_reviewed(self):
        self.submodule_pr(IGNORE_ALL, IGNORE_ALL)
        self.assert_reviewed(f'+Subproject commit {POINTER_HEAD}')

    def test_a_pointer_only_change_under_an_ignore_setting_is_reviewed(self):
        self.add_head_commit({'.gitmodules': IGNORE_ALL, 'vendor/lib': ('gitlink', POINTER_BASE)})
        self.publish_as_base()
        self.add_head_commit({'vendor/lib': ('gitlink', POINTER_HEAD)})
        diff = self.assert_reviewed(f'+Subproject commit {POINTER_HEAD}')
        self.assertEqual(diff.count('diff --git '), 1)

    def test_a_pointer_change_under_an_ignore_setting_the_pr_adds_is_reviewed(self):
        self.submodule_pr(TRACKED, IGNORE_ALL)
        self.assert_reviewed(f'+Subproject commit {POINTER_HEAD}')

    def test_a_pointer_change_is_reviewed_when_runner_config_picks_the_log_format(self):
        git(self.workspace, 'config', 'diff.submodule', 'log')
        self.submodule_pr(IGNORE_ALL, IGNORE_ALL)
        self.assert_reviewed(f'+Subproject commit {POINTER_HEAD}')

    def test_a_re_point_after_approval_is_reviewed_again(self):
        for base_modules in (IGNORE_ALL, TRACKED):
            with self.subTest(base_ignores=base_modules == IGNORE_ALL):
                self.setUp()
                git(self.workspace, 'config', 'diff.submodule', 'log')
                self.submodule_pr(base_modules, IGNORE_ALL)
                self.approve_current_head()
                self.assertEqual(self.rereview_outputs().get('skip_model'), 'true')
                self.add_head_commit({'vendor/lib': ('gitlink', POINTER_LATER)})
                self.assertEqual(self.rereview_outputs().get('skip_model'), 'false')

    def test_a_submodule_replaced_by_a_binary_still_needs_a_human(self):
        self.add_head_commit({'.gitmodules': IGNORE_ALL, 'vendor/lib': ('gitlink', POINTER_BASE)})
        self.publish_as_base()
        git(self.workspace, 'rm', '-q', '--cached', 'vendor/lib')
        self.add_head_commit({'vendor/lib': b'\x00opaque-binary'})
        result, outputs = self.prep()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outputs.get('review_input_failure'), 'binary_content_unreviewable')

    def test_a_link_or_submodule_at_a_lockfile_path_is_never_elided(self):
        for name, entry in (('vendor/yarn.lock', ('gitlink', POINTER_HEAD)), ('package-lock.json', ('symlink', 'deploy.sh'))):
            with self.subTest(path=name):
                self.setUp()
                files = {name: entry}
                if entry[0] == 'gitlink':
                    files['.gitmodules'] = IGNORE_ALL.replace('vendor/lib', name)
                self.add_head_commit(files)
                result, outputs = self.prep()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(outputs.get('review_input_failure'), 'lockfile_not_regular_file')


class EmptyReviewInputFailsClosedTest(PullRequestCheckoutFixture):
    """A non-empty change must never reach a provider as an empty diff."""

    def run_prep_with_rendered_diff(self, rendered):
        wrapper = self.root / 'bin' / 'git'
        wrapper.write_text(
            '#!/bin/sh\n'
            'case " $* " in *" --name-status "*|*" --raw "*) [ -n "$BLANK_LISTS" ] && exit 0 ;; *" --name-only "*) ;; *" diff "*" --find-renames "*) printf "%s" "$RENDERED"; exit 0 ;; esac\n'
            f'exec {shutil.which("git")} "$@"\n')
        wrapper.chmod(0o755)
        self.env['RENDERED'] = rendered
        self.assertEqual(self.run_step(ATTRIBUTES_ID).returncode, 0)
        return self.run_step('prep'), step_outputs(self.root / 'output')

    def assert_unavailable(self, rendered, reason):
        result, outputs = self.run_prep_with_rendered_diff(rendered)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outputs.get('review_input_available'), 'false')
        self.assertEqual(outputs.get('review_input_failure'), reason)
        self.assertNotIn('review_dir', outputs)
        return result

    def test_empty_rendered_diff_of_a_real_change_is_unavailable_input(self):
        self.assert_unavailable('', 'review_input_empty')

    def test_rendered_diff_with_no_reviewable_section_is_unavailable_input(self):
        result = self.assert_unavailable('not a diff section\n', 'review_input_incomplete')
        self.assertNotIn('is empty', result.stdout + result.stderr)

    def sections(self, count):
        return ''.join(f'diff --git a/f{index} b/f{index}\n+line\n' for index in range(count))

    def test_a_change_missing_from_every_path_list_is_unavailable_input(self):
        # Pointer-only change under an ignore setting: only git's own comparison still sees it.
        self.add_head_commit({'.gitmodules': IGNORE_ALL, 'vendor/lib': ('gitlink', POINTER_BASE)})
        self.publish_as_base()
        self.add_head_commit({'vendor/lib': ('gitlink', POINTER_HEAD)})
        self.env['BLANK_LISTS'] = '1'
        self.assert_unavailable('', 'review_input_empty')

    def test_fewer_sections_than_reviewed_paths_is_incomplete_input(self):
        self.assert_unavailable(self.sections(3), 'review_input_incomplete')

    def test_more_sections_than_reviewed_paths_is_incomplete_input(self):
        self.assert_unavailable(self.sections(5), 'review_input_incomplete')


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
