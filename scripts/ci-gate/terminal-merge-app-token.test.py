#!/usr/bin/env python3
"""The retired promotion path cannot mint a merge token or issue a merge."""
import copy
import pathlib
import subprocess
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/ai-privileged-merge.yml'
HELPER = ROOT / 'scripts/ci-gate/terminal-merge.sh'


def validate(document):
    job = document['jobs']['privileged_merge']
    steps = job['steps']
    holds = [step for step in steps if step.get('name') == 'Hold autonomous terminal merge']
    if len(holds) != 1 or set(holds[0]) != {'name', 'run'}:
        return False
    if holds[0]['run'].strip() != ('echo "::error::Autonomous terminal merge is disabled pending non-bypass admission (#1945)."\nexit 1'):
        return False
    if job['outputs'].get('terminal_merge_succeeded') != 'false':
        return False
    serialized = yaml.safe_dump(steps)
    return not any(term in serialized for term in (
        'actions/create-github-app-token', 'MERGE_APP_PRIVATE_KEY',
        'gh pr merge', 'merge-app-token', 'terminal-merge.sh',
    ))


def main():
    doc = yaml.safe_load(WORKFLOW.read_text())
    assert validate(doc)
    for change in ('mint', 'merge', 'success', 'hold'):
        mutant = copy.deepcopy(doc)
        job = mutant['jobs']['privileged_merge']
        if change == 'mint':
            job['steps'].append({'uses': 'actions/create-github-app-token@v3'})
        elif change == 'merge':
            job['steps'].append({'run': 'gh pr merge 1 --admin'})
        elif change == 'success':
            job['outputs']['terminal_merge_succeeded'] = 'true'
        else:
            next(s for s in job['steps'] if s.get('name') == 'Hold autonomous terminal merge')['run'] = 'exit 0'
        assert not validate(mutant), change
    with tempfile.TemporaryDirectory() as tmp:
        gh = pathlib.Path(tmp) / 'gh'
        calls = pathlib.Path(tmp) / 'calls'
        gh.write_text(f'#!/bin/sh\nprintf called >> {calls}\n')
        gh.chmod(0o755)
        hold = next(step['run'] for step in doc['jobs']['privileged_merge']['steps']
                    if step.get('name') == 'Hold autonomous terminal merge')
        for command in (['/bin/bash', str(HELPER)], ['/bin/bash', '-c', hold]):
            result = subprocess.run(command, env={'PATH': tmp}, capture_output=True, text=True)
            assert result.returncode != 0 and not calls.exists()
            assert 'disabled' in result.stdout + result.stderr
    print('ok - terminal merge and token mint fail closed')


if __name__ == '__main__':
    main()
