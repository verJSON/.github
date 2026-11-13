#!/usr/bin/env python3
"""Inspect selection consistency offline using trusted local Git repositories.

The record has no candidate, signer, replay, or release authorization evidence.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import changelog


class SelectionEvidenceError(ValueError):
    pass


def _git_env():
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1',
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0')
    return env


def _run_git(repo, *args):
    try:
        return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=repo,
                              env=_git_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SelectionEvidenceError('git object inspection unavailable') from exc


def _bounded_tree(repo, source):
    try:
        process = subprocess.Popen(
            ['git', '-c', 'core.hooksPath=/dev/null', 'ls-tree', '-rz', '--full-tree',
             source, '--', 'NEXT', 'CHANGELOG'], cwd=repo, env=_git_env(),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise SelectionEvidenceError('git tree inspection unavailable') from exc
    chunks = []
    state = {'size': 0, 'overflow': False, 'error': None}

    def read_output():
        try:
            while True:
                chunk = process.stdout.read1(65536)
                if not chunk:
                    break
                state['size'] += len(chunk)
                if state['size'] > 4 * 1024 * 1024:
                    state['overflow'] = True
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    break
                chunks.append(chunk)
        except (OSError, ValueError) as exc:
            state['error'] = exc

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        reader.join(timeout=2)
        if not reader.is_alive():
            process.stdout.close()
        raise SelectionEvidenceError('git tree inspection timed out') from exc
    reader.join(timeout=2)
    if not reader.is_alive():
        process.stdout.close()
    if reader.is_alive() or state['overflow'] or state['error'] or process.returncode:
        raise SelectionEvidenceError('git tree inspection failed or exceeded limit')
    return b''.join(chunks)


def _git(repo, *args):
    result = _run_git(repo, *args)
    if result.returncode:
        raise SelectionEvidenceError('git object inspection failed')
    return result.stdout


def _commit(repo, value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value):
        raise SelectionEvidenceError('commit must be a full lowercase SHA-1')
    config = _run_git(repo, 'config', '--includes', '--get-regexp',
                      r'^(extensions\.partialclone|remote\..*\.promisor)$')
    if config.returncode != 1 or config.stdout:
        raise SelectionEvidenceError('partial or unreadable Git configuration unsupported')
    if _git(repo, 'rev-parse', '--show-object-format').strip() != b'sha1':
        raise SelectionEvidenceError('only SHA-1 repositories are supported')
    if _git(repo, 'cat-file', '-t', value).strip() != b'commit':
        raise SelectionEvidenceError('object must be a commit')
    return value


def _blob(repo, oid):
    size = int(_git(repo, 'cat-file', '-s', oid))
    if size > 16 * 1024 * 1024:
        raise SelectionEvidenceError('input blob exceeds inspection limit')
    data = _git(repo, 'cat-file', 'blob', oid)
    if len(data) != size:
        raise SelectionEvidenceError('input blob size mismatch')
    return data


def _inputs(repo, source):
    tree = _git(repo, 'rev-parse', f'{source}^{{tree}}').decode('ascii').strip()
    entries = []
    total = 0
    output = _bounded_tree(repo, source)
    seen_paths = set()
    for record in output.split(b'\0'):
        if not record:
            continue
        metadata, raw_path = record.split(b'\t', 1)
        mode, kind, oid = metadata.decode('ascii').split()
        try:
            path = raw_path.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise SelectionEvidenceError('input path is not UTF-8') from exc
        parts = path.split('/')
        if (len(parts) != 2 or parts[0] not in ('NEXT', 'CHANGELOG')
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', parts[1])
                or parts[1].endswith(('.', ' '))
                or re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])',
                                parts[1].split('.')[0])):
            raise SelectionEvidenceError('input path is not portable')
        normalized = path.casefold()
        if normalized in seen_paths:
            raise SelectionEvidenceError('case-colliding input paths unsupported')
        seen_paths.add(normalized)
        if len(seen_paths) > 10000:
            raise SelectionEvidenceError('input set exceeds entry limit')
        if mode not in ('100644', '100755') or kind != 'blob':
            raise SelectionEvidenceError('input must be a regular file')
        data = _blob(repo, oid)
        total += len(data)
        if total > 64 * 1024 * 1024:
            raise SelectionEvidenceError('input set exceeds inspection limit')
        entries.append((path, data))
    return tree, sorted(entries)


def build_selection_evidence(source_repo, source_commit, contract_repo, contract_commit):
    """Bind the complete unscoped fragment set and history to immutable Git objects."""
    source = _commit(source_repo, source_commit)
    contract = _commit(contract_repo, contract_commit)
    engine_entry = _git(contract_repo, 'ls-tree', '-z', contract, '--', 'scripts/changelog.py')
    match = re.fullmatch(rb'100644 blob ([0-9a-f]{40})\tscripts/changelog\.py\x00', engine_entry)
    if not match:
        raise SelectionEvidenceError('contract engine must be a regular tracked file')
    engine = _blob(contract_repo, match.group(1).decode('ascii'))
    if engine != Path(changelog.__file__).read_bytes():
        raise SelectionEvidenceError('loaded changelog engine differs from contract commit')
    tree, inputs = _inputs(source_repo, source)
    with tempfile.TemporaryDirectory(prefix='selection-evidence-') as directory:
        root = Path(directory)
        for name, data in inputs:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        try:
            fragments = changelog.fragments(root)
            if any(item.metadata.get('component') for item in fragments):
                raise SelectionEvidenceError('scoped fragments are unsupported')
            selected = changelog.select_release_fragments(root, [])
            version = changelog.next_version_for_entries(root, 'v', selected)
            digest = changelog.selection_digest_for_entries(None, 'v', selected)
        except changelog.ChangelogError as exc:
            raise SelectionEvidenceError(str(exc)) from exc
    selected_by_path = {f'NEXT/{item.path.name}': item for item in selected}
    selected_records = [
        {'path': name, 'identity': selected_by_path[name].identity,
         'sha256': hashlib.sha256(data).hexdigest()}
        for name, data in inputs if name in selected_by_path
    ]
    history = [
        {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}
        for name, data in inputs if name.startswith('CHANGELOG/')
    ]
    return {'schemaVersion': 1, 'sourceCommit': source, 'sourceTree': tree,
            'contractCommit': contract, 'engineSha256': hashlib.sha256(engine).hexdigest(),
            'prefix': 'v', 'component': None, 'version': version,
            'selectionDigest': digest, 'fragments': selected_records, 'history': history}


def _matches_expected(observed, expected):
    if type(observed) is not type(expected):
        return False
    if type(expected) is dict:
        if len(observed) != len(expected):
            return False
        return all(type(key) is str and key in expected
                   and _matches_expected(value, expected[key])
                   for key, value in observed.items())
    if type(expected) is list:
        if len(observed) != len(expected):
            return False
        return all(_matches_expected(item, expected[index])
                   for index, item in enumerate(observed))
    if type(expected) is str and len(observed) != len(expected):
        return False
    return observed == expected


def verify_selection_evidence(evidence, source_repo, source_commit, contract_repo, contract_commit):
    """Recompute against independently supplied commits; comparison confers no authority."""
    expected = build_selection_evidence(source_repo, source_commit, contract_repo, contract_commit)
    if not _matches_expected(evidence, expected):
        raise SelectionEvidenceError('selection evidence does not match independent inputs')
    canonical = json.dumps(expected, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=True, allow_nan=False).encode('ascii')
    return hashlib.sha256(canonical).hexdigest()
