#!/usr/bin/env python3
import contextlib
import base64
import copy
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
import zipfile

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('transport', ROOT / 'container_deployment_transport.py')
t = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(t)
NOW = 1_800_000_000


def exactly(reason):
    return '^' + re.escape(reason) + '$'


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def request(operation='probe'):
    return {'schemaVersion': 1, 'operation': operation, 'attemptId': '100.1', 'fleetSelector': 'production', 'lane': 'gate',
            'issuedAt': stamp(NOW - 1), 'expiresAt': stamp(NOW + 60),
            'deploymentContractCommit': 'd' * 40, 'configDigest': 'sha256:' + 'f' * 64, 'planDigest': 'sha256:' + 'e' * 64, 'action': 'deploy', 'rollbackOfAttempt': None,
            'github': {'repository': t.CANARY_REPOSITORY if operation != 'manifest' else 'Verjson/runners',
                       'repositoryId': 11, 'appId': 22, 'installationId': 33},
            'release': {'repository': 'Verjson/runners', 'assetId': 44, 'manifestDigest': 'sha256:' + '1' * 64,
                        'variant': 'pwsh', 'imageDigest': 'sha256:' + '2' * 64, 'sourceCommit': 'a' * 40,
                        'sourceRef': 'refs/heads/main', 'signerWorkflow': 'Verjson/.github/.github/workflows/container-release.yml',
                        'signerCommit': 'b' * 40},
            'probe': {'runnerId': 55, 'runnerName': 'gha-gate-1', 'runnerLabel': 'canary-private',
                      'transactionNonce': '11111111-2222-3333-4444-555555555555', 'workflowId': 66,
                      'workflowRef': 'refs/tags/runner-canary-v1.0.1', 'workflowCommit': 'c' * 40}}


def host_request():
    value = request('manifest')
    value['operation'] = 'host-export'
    value.pop('probe')
    value['hostExport'] = {
        'project': 'runner-project', 'doContext': 'readonly', 'doSshKey': 'runner-key',
        'runnerNames': ['gha-gate-1'], 'maxAgeSeconds': 300,
        'purpose': 'baseline', 'runnerName': None,
    }
    return value


class ProbeAPI:
    def __init__(self, value):
        self.value, self.calls, self.dispatched = value, [], False
        p, r = value['probe'], value['release']
        self.workflow = {'id': p['workflowId'], 'path': t.CANARY_PATH, 'state': 'active'}
        self.tag = {'object': {'type': 'commit', 'sha': p['workflowCommit'],
                    'url': f"https://api.github.com/repos/{t.CANARY_REPOSITORY}/git/commits/{p['workflowCommit']}"}}
        self.baseline = {'workflow_runs': [{'id': 100, 'display_title': 'prior'}]}
        self.record = {'id': 101, 'display_title': 'runner-canary-' + p['transactionNonce'], 'workflow_id': p['workflowId'],
                       'head_sha': p['workflowCommit'], 'head_branch': p['workflowRef'].removeprefix('refs/tags/'),
                       'event': 'workflow_dispatch', 'path': t.CANARY_PATH, 'run_attempt': 1, 'created_at': stamp(NOW),
                       'status': 'completed', 'conclusion': 'success'}
        self.job = {'id': 102, 'name': 'canary', 'runner_id': p['runnerId'], 'runner_name': p['runnerName'],
                    'labels': [p['runnerLabel']], 'status': 'completed', 'conclusion': 'success'}
        self.receipt = {'schemaVersion': 2, 'contract': 'verjson-runner-promotion/2', 'transactionNonce': p['transactionNonce'],
                        'runner': {'id': p['runnerId'], 'name': p['runnerName']},
                        'release': {'manifest': r['manifestDigest'], 'variant': r['variant'], 'imageDigest': r['imageDigest']},
                        'canary': {'repository': t.CANARY_REPOSITORY, 'workflow': 'canonical-db-backed-runner-canary',
                                   'runId': 101, 'jobId': 102, 'runAttempt': 1, 'event': 'workflow_dispatch',
                                   'headBranch': p['workflowRef'].removeprefix('refs/tags/'), 'headSha': p['workflowCommit'],
                                   'workflowPath': t.CANARY_PATH, 'workflowSha': p['workflowCommit'], 'conclusion': 'success',
                                   'databaseServiceReachable': True, 'dockerServiceReachable': True, 'pwshExecuted': True,
                                   'runUrl': f'https://github.com/{t.CANARY_REPOSITORY}/actions/runs/101',
                                   'jobUrl': f'https://github.com/{t.CANARY_REPOSITORY}/actions/runs/101/job/102'},
                        'imageBuild': {'conclusion': 'success', 'imageDigest': r['imageDigest'], 'diskBytesConsumed': 0},
                        'postCanary': {'freeBytes': 20_000_000_000, 'freeInodePercent': 90, 'observedAt': stamp(NOW)},
                        'dependencies': {'uploadArtifactCommit': 'a' * 40, **{name: 'image@sha256:' + 'b' * 64 for name in ('postgresImage', 'nginxImage', 'curlImage', 'buildBaseImage')}}, 'artifact': {'name': 'runner-promotion-receipt', 'contentDigest': ''}}
        self.artifact = {'id': 103, 'name': 'runner-promotion-receipt', 'expired': False, 'workflow_run': {'id': 101}}
        self.archive_path = 'runner-promotion-receipt.json'
        self.refresh_archive()

    def refresh_archive(self):
        self.receipt['artifact']['contentDigest'] = ''
        self.receipt['artifact']['contentDigest'] = t.digest(t.canonical(self.receipt))
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr(self.archive_path, t.canonical(self.receipt))
        self.archive = output.getvalue()
        self.artifact['digest'] = t.digest(self.archive)

    def call(self, method, path, token, body=None, binary=False):
        self.calls.append((method, path, token, body))
        if path.endswith('/dispatches'):
            self.dispatched = True
            return None
        if '/runs?' in path:
            runs = [self.record] if self.dispatched else self.baseline['workflow_runs']
            return {'total_count': len(runs), 'workflow_runs': runs}
        if path.endswith('/actions/workflows/66'): return self.workflow
        if '/git/ref/' in path: return self.tag
        if '/jobs?' in path: return {'total_count': 1, 'jobs': [self.job]}
        if path.endswith('/artifacts?per_page=100'): return {'total_count': 1, 'artifacts': [self.artifact]}
        if path.endswith('/zip'): return self.archive
        raise AssertionError((method, path))


class PagedProbeAPI(ProbeAPI):
    def __init__(self, value):
        super().__init__(value)
        self.baseline_runs = [
            {'id': 1000 + index, 'display_title': f'prior-{index}'}
            for index in range(100)
        ]
        self.baseline_runs.append(copy.deepcopy(self.record))

    def call(self, method, path, token, body=None, binary=False):
        if '/runs?' in path:
            self.calls.append((method, path, token, body))
            page = int(parse_qs(urlsplit(path).query)['page'][0])
            runs = [self.record] if self.dispatched else self.baseline_runs
            start = (page - 1) * t.RUNS_PAGE_SIZE
            return {
                'total_count': len(runs),
                'workflow_runs': runs[start:start + t.RUNS_PAGE_SIZE],
            }
        return super().call(method, path, token, body, binary)


class IncompleteProbeAPI(ProbeAPI):
    def call(self, method, path, token, body=None, binary=False):
        if '/runs?' in path and not self.dispatched:
            self.calls.append((method, path, token, body))
            return {'total_count': 101, 'workflow_runs': self.baseline['workflow_runs']}
        return super().call(method, path, token, body, binary)


class OversizedProbeAPI(ProbeAPI):
    def call(self, method, path, token, body=None, binary=False):
        if '/runs?' in path and not self.dispatched:
            self.calls.append((method, path, token, body))
            return {
                'total_count': t.MAX_RUN_RECORDS + 1,
                'workflow_runs': self.baseline['workflow_runs'],
            }
        return super().call(method, path, token, body, binary)


class RequestTests(unittest.TestCase):
    def test_full_request_preserves_distinct_selector_lane_and_transaction(self):
        value = request()
        self.assertIs(t.validate_request(value, NOW), value)
        self.assertNotEqual(value['fleetSelector'], value['lane'])

    def test_missing_wrong_and_stale_request_fields_fail(self):
        stale = 'request is stale or exceeds bounded lifetime'
        mutations = [(lambda v: v.pop('attemptId'), 'request fields differ'), (lambda v: v.update(schemaVersion=2), 'unsupported request version'),
                     (lambda v: v.update(expiresAt=stamp(NOW)), stale), (lambda v: v.update(issuedAt=stamp(NOW + 1)), stale),
                     (lambda v: v.update(expiresAt=stamp(NOW + 1801)), stale), (lambda v: v.update(extra='secret'), 'request fields differ'),
                     (lambda v: v.update(schemaVersion=True), 'unsupported request version'),
                     (lambda v: v['github'].update(repositoryId=11.0), 'reviewed numeric GitHub identities are required'),
                     (lambda v: v['probe'].update(runnerId=True), 'exact runner/workflow IDs are required'),
                     (lambda v: v['probe'].update(workflowCommit='main'), 'canary workflow commit must be immutable'),
                     (lambda v: v['probe'].update(transactionNonce=''), 'transaction nonce must be an exact UUID'),
                     (lambda v: v['github'].update(repository='attacker/repo'), 'canary dispatch targets another repository')]
        for mutate, reason in mutations:
            value = request(); mutate(value)
            with self.subTest(value=value), self.assertRaisesRegex(t.TransportError, exactly(reason)): t.validate_request(value, NOW)

    def test_rollback_requires_an_independent_attempt_and_binds_response_request(self):
        value = request(); value['action'] = 'rollback'
        with self.assertRaisesRegex(t.TransportError, exactly('rollback attempt identity is invalid')): t.validate_request(value, NOW)
        value['rollbackOfAttempt'] = '99.1'
        t.validate_request(value, NOW)
        value['rollbackOfAttempt'] = value['attemptId']
        with self.assertRaisesRegex(t.TransportError, exactly('rollback attempt identity is invalid')): t.validate_request(value, NOW)

    def test_preplan_manifest_binds_config_but_dispatch_requires_approved_plan(self):
        value = request('manifest'); value['planDigest'] = None
        t.validate_request(value, NOW)
        value['configDigest'] = None
        with self.assertRaisesRegex(t.TransportError, exactly('admitted configuration digest is invalid')): t.validate_request(value, NOW)
        value = request(); value['planDigest'] = None
        with self.assertRaisesRegex(t.TransportError, exactly('approved plan digest is invalid')): t.validate_request(value, NOW)

    def test_intent_persists_file_and_directory_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'intent.json'
            with mock.patch.object(t.os, 'fsync', wraps=t.os.fsync) as sync:
                t.persist_record(path, {'nonce': 'first'})
                self.assertEqual(sync.call_count, 2)
            with self.assertRaises(FileExistsError): t.persist_record(path, {'nonce': 'second'})
            self.assertEqual(json.loads(path.read_text()), {'nonce': 'first'})

    def test_duplicate_json_fields_fail(self):
        with self.assertRaisesRegex(t.TransportError, exactly('duplicate JSON field')): t.decode(b'{"operation":"probe","operation":"manifest"}')

    def test_host_export_and_dry_run_probe_never_acquire_credentials(self):
        # Host export builds no GitHub client before its dedicated key check; with the key present it does acquire credentials (#1525).
        environment = {key: value for key, value in os.environ.items() if key != t.HOST_EXPORT_APP_KEY_ENV}
        for value, dry, reason in [(host_request(), False, 'dedicated host export App private key is unavailable'),
                                   (request('probe'), True, 'dry-run has no dispatch authority')]:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'request.json'; path.write_text(json.dumps(value))
                argv = ['transport', '--request', str(path), '--output', str(Path(directory) / 'result')]
                if dry: argv.append('--dry-run')
                with mock.patch.object(sys, 'argv', argv), mock.patch.object(t.time, 'time', return_value=NOW), mock.patch.object(t, 'GitHubHTTP') as api, \
                        mock.patch.dict(os.environ, environment, clear=True):
                    with self.assertRaisesRegex(t.TransportError, exactly(reason)): t.main()
                    api.assert_not_called()


class HostCapacityReportTests(unittest.TestCase):
    def setUp(self):
        self.lane = 'gate'
        self.runner_names = ['gha-gate-1', 'gha-gate-2']
        self.report = {
            'availableCapacity': 1,
            'fleet': {
                'lane': self.lane,
                'runners': [
                    {'name': 'gha-gate-1', 'available': True},
                    {'name': 'gha-gate-2', 'available': False},
                ],
            },
        }

    def test_capacity_report_matches_requested_lane_roster_and_count(self):
        self.assertEqual(
            t._validate_capacity_report(self.report, self.lane, self.runner_names),
            1,
        )

    def test_capacity_report_rejects_wrong_lane(self):
        report = copy.deepcopy(self.report)
        report['fleet']['lane'] = 'other'
        with self.assertRaisesRegex(t.TransportError, 'lane differs'):
            t._validate_capacity_report(report, self.lane, self.runner_names)

    def test_capacity_report_rejects_incomplete_or_duplicate_roster(self):
        report = copy.deepcopy(self.report)
        report['fleet']['runners'][1]['name'] = 'gha-gate-1'
        with self.assertRaisesRegex(t.TransportError, 'runner identities differ'):
            t._validate_capacity_report(report, self.lane, self.runner_names)

    def test_capacity_report_rejects_missing_roster(self):
        report = copy.deepcopy(self.report)
        del report['fleet']['runners']
        with self.assertRaisesRegex(t.TransportError, 'runner identities differ'):
            t._validate_capacity_report(report, self.lane, self.runner_names)

    def test_capacity_report_rejects_unconfigured_runner_name(self):
        report = copy.deepcopy(self.report)
        report['fleet']['runners'][1]['name'] = 'gha-gate-3'
        with self.assertRaisesRegex(t.TransportError, 'runner identities differ'):
            t._validate_capacity_report(report, self.lane, self.runner_names)

    def test_capacity_report_rejects_non_object_runner(self):
        report = copy.deepcopy(self.report)
        report['fleet']['runners'][1] = None
        with self.assertRaisesRegex(t.TransportError, 'runner identities differ'):
            t._validate_capacity_report(report, self.lane, self.runner_names)

    def test_capacity_report_rejects_non_boolean_runner_availability(self):
        report = copy.deepcopy(self.report)
        report['fleet']['runners'][0]['available'] = 1
        with self.assertRaisesRegex(t.TransportError, 'availability is invalid'):
            t._validate_capacity_report(report, self.lane, self.runner_names)

    def test_capacity_report_rejects_count_that_disagrees_with_runner_states(self):
        report = copy.deepcopy(self.report)
        report['availableCapacity'] = 2
        with self.assertRaisesRegex(t.TransportError, 'count differs'):
            t._validate_capacity_report(report, self.lane, self.runner_names)

    def test_capacity_report_rejects_boolean_count(self):
        report = copy.deepcopy(self.report)
        report['availableCapacity'] = True
        with self.assertRaisesRegex(t.TransportError, 'count differs'):
            t._validate_capacity_report(report, self.lane, self.runner_names)


class HostExportTests(unittest.TestCase):
    def _run_capacity_export(self, report, *, provide_runner_temp=True):
        value = host_request()
        value['hostExport']['purpose'] = 'capacity'
        with tempfile.TemporaryDirectory() as directory:
            runner_temp = Path(directory) / 'runner-temp'
            runner_temp.mkdir()
            cli = Path(directory) / 'node_modules/.bin/verjson-cloud'
            cli.parent.mkdir(parents=True)
            cli.write_text('#!/bin/sh\n', encoding='utf-8')
            cli.chmod(0o700)
            environment = {
                'VERJSON_DEPLOYMENT_CLI': str(cli),
                'VERJSON_DEPLOYMENT_CLI_ROOT': directory,
                'RUNNER_TEMP': str(runner_temp),
                'RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY': 'ssh-private-key-secret',
                'RUNNER_HOST_EVIDENCE_DOCTL_CONFIG': 'doctl-read-token-secret',
                'RUNNER_HOST_EVIDENCE_KNOWN_HOSTS': 'preprovisioned-host-pin',
            }
            if not provide_runner_temp:
                environment.pop('RUNNER_TEMP')
            temp_directories = []

            def run(command, **kwargs):
                temporary = Path(kwargs['env']['TMPDIR'])
                self.assertEqual(runner_temp, temporary.parent)
                self.assertEqual(0o700, temporary.stat().st_mode & 0o777)
                temp_directories.append(temporary)
                return subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps(report), stderr='',
                )

            with mock.patch.dict(os.environ, environment, clear=True):
                try:
                    result = t.host_export(
                        value,
                        'installation-read-token',
                        run=run,
                        clock=lambda: NOW,
                    )
                finally:
                    self.assertEqual(int(provide_runner_temp), len(temp_directories))
                    for temporary in temp_directories:
                        self.assertFalse(temporary.exists())
            return result

    def test_capacity_export_returns_a_validated_fleet_report(self):
        report = {
            'schemaVersion': 1,
            'operation': 'host-export',
            'observedAt': stamp(NOW),
            'availableCapacity': 1,
            'fleet': {
                'lane': 'gate',
                'runners': [{'name': 'gha-gate-1', 'available': True}],
            },
        }
        result = self._run_capacity_export(report)
        self.assertEqual(result['outcome'], 'passed')
        self.assertEqual(result['hostEvidence'], report)
        self.assertIsNone(result['releaseManifest'])
        self.assertEqual(result['baselineAttestations'], [])

    def test_host_export_fails_closed_without_runner_temp(self):
        with self.assertRaisesRegex(
            t.TransportError, 'runner temporary directory unavailable'
        ):
            self._run_capacity_export({}, provide_runner_temp=False)

    def test_capacity_export_fails_closed_when_count_disagrees_with_roster(self):
        report = {
            'schemaVersion': 1,
            'operation': 'host-export',
            'observedAt': stamp(NOW),
            'availableCapacity': 0,
            'fleet': {
                'lane': 'gate',
                'runners': [{'name': 'gha-gate-1', 'available': True}],
            },
        }
        with self.assertRaisesRegex(t.TransportError, 'count differs'):
            self._run_capacity_export(report)

    def test_host_export_routes_read_only_credentials_outside_request_and_receipt(self):
        value = host_request()
        manifest = {
            'releaseVersion': '1.0.0',
            'source': {'repository': 'Verjson/runners', 'commit': 'a' * 40},
            'release': {'workflow': {
                'path': '.github/workflows/container-release.yml', 'contractCommit': 'b' * 40,
            }},
            'images': [{'variant': 'pwsh', 'indexDigest': 'sha256:' + '2' * 64}],
        }
        raw = json.dumps(manifest, sort_keys=True, separators=(',', ':'))
        identity = t.digest(raw.encode())
        value['release']['manifestDigest'] = identity
        runner = {
            'name': 'gha-gate-1', 'dropletId': '901', 'runnerId': 55,
            'manifestIdentity': identity,
            'release': {'releaseVersion': '1.0.0', 'manifestDigest': identity},
            'releaseManifest': manifest, 'releaseManifestBytes': raw,
            'deployedDigest': 'sha256:' + '2' * 64,
        }
        report = {
            'schemaVersion': 1, 'operation': 'host-export', 'observedAt': stamp(NOW - 1),
            'manifestIdentity': identity, 'manifestBytes': raw, 'manifest': manifest,
            'availableCapacity': 2, 'fleet': {'lane': 'gate', 'runners': [runner]},
        }
        secret_values = {
            'RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY': 'ssh-private-key-secret',
            'RUNNER_HOST_EVIDENCE_DOCTL_CONFIG': 'doctl-read-token-secret',
            'RUNNER_HOST_EVIDENCE_KNOWN_HOSTS': 'preprovisioned-host-pin',
            'DIGITALOCEAN_RUNNER_FLEET_TOKEN': 'fleet-write-token',
            'GH_RUNNER_CONTROL_TOKEN': 'runner-control-token',
        }
        completed_roots = []
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            contract_root = Path(directory) / 'contract'
            contract_root.mkdir()
            executable = contract_root / 'verjson-cloud'
            executable.write_text('#!/bin/sh\nexit 0\n')
            executable.chmod(0o700)
            secret_values.update({
                'VERJSON_DEPLOYMENT_CLI': str(executable),
                'VERJSON_DEPLOYMENT_CLI_ROOT': str(contract_root),
                'RUNNER_TEMP': directory,
            })

            def run(command, **kwargs):
                calls.append((command, kwargs))
                if command[0] == 'gh':
                    return subprocess.CompletedProcess(command, 0, stdout='[]', stderr='')
                key_path = Path(command[command.index('--read-only-ssh-private-key') + 1])
                completed_roots.append(key_path.parent)
                self.assertEqual(key_path.read_text(), 'ssh-private-key-secret')
                self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn('ssh-private-key-secret', command)
                self.assertEqual(kwargs['env']['GH_TOKEN'], 'installation-read-token')
                self.assertNotIn('DIGITALOCEAN_RUNNER_FLEET_TOKEN', kwargs['env'])
                self.assertNotIn('GH_RUNNER_CONTROL_TOKEN', kwargs['env'])
                self.assertNotIn('RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY', kwargs['env'])
                self.assertEqual(
                    (Path(kwargs['env']['HOME']) / '.config/doctl/config.yaml').read_text(),
                    'doctl-read-token-secret',
                )
                self.assertEqual(
                    (Path(kwargs['env']['HOME']) / '.verjson-digitalocean-known-hosts').read_text(),
                    'preprovisioned-host-pin',
                )
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(report), stderr='')

            with mock.patch.dict(os.environ, secret_values, clear=True):
                result = t.host_export(
                    value, 'installation-read-token',
                    {
                        'manifestIdentity': identity,
                        'manifestBytes': raw,
                        'manifest': manifest,
                        'attestation': {'verified': True, 'subjectDigest': identity},
                    },
                    run=run, clock=lambda: NOW,
                )
        self.assertEqual(result['outcome'], 'passed')
        self.assertEqual(result['hostEvidence'], report)
        self.assertEqual(result['releaseManifest']['manifestIdentity'], identity)
        self.assertTrue(result['releaseManifest']['attestation']['verified'])
        self.assertEqual(result['baselineAttestations'][0]['manifestIdentity'], identity)
        self.assertEqual([call[0][0] for call in calls], [str(executable), 'gh'])
        self.assertEqual(calls[0][1]['timeout'], 900)
        self.assertFalse(completed_roots[0].exists())
        self.assertNotIn('ssh-private-key-secret', json.dumps(value))
        self.assertNotIn('ssh-private-key-secret', json.dumps(result))

    def test_host_export_rejects_credentials_echoed_in_child_output(self):
        value = host_request()
        value["hostExport"]["purpose"] = "capacity"
        report = {
            "schemaVersion": 1,
            "operation": "host-export",
            "observedAt": stamp(NOW),
            "availableCapacity": 2,
            "unexpected": "ssh-private-key-secret",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / "node_modules/.bin/verjson-cloud"
            cli.parent.mkdir(parents=True)
            cli.write_text("#!/bin/sh\n", encoding="utf-8")
            cli.chmod(0o700)
            environment = {
                "VERJSON_DEPLOYMENT_CLI": str(cli),
                "VERJSON_DEPLOYMENT_CLI_ROOT": directory,
                'RUNNER_TEMP': directory,
                "RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY": "ssh-private-key-secret",
                "RUNNER_HOST_EVIDENCE_DOCTL_CONFIG": "doctl-read-token-secret",
                "RUNNER_HOST_EVIDENCE_KNOWN_HOSTS": "preprovisioned-host-pin",
            }
            with mock.patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(t.TransportError, "credential material"):
                    t.host_export(
                        value,
                        "installation-read-token",
                        run=mock.Mock(
                            return_value=subprocess.CompletedProcess(
                                [], 0, stdout=json.dumps(report), stderr=""
                            )
                        ),
                        clock=lambda: NOW,
                    )

    def test_host_export_fails_closed_when_observation_credentials_are_missing(self):
        value = host_request()
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(t.TransportError, 'credentials are unavailable'):
                t.host_export(
                    value, 'read-only-token',
                    {'manifestIdentity': value['release']['manifestDigest']},
                    run=mock.Mock(), clock=lambda: NOW,
                )


class HostExportJwtTests(unittest.TestCase):
    def test_host_export_jwt_fails_closed_without_runner_temp(self):
        private_key = "-----BEGIN PRIVATE KEY-----\nprivate-key-material\n-----END PRIVATE KEY-----\n"
        calls = []

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                t.TransportError, "runner temporary directory unavailable"
            ):
                t.mint_host_export_app_jwt(
                    204, private_key, NOW, run=lambda *args, **kwargs: calls.append(args)
                )

        self.assertEqual([], calls)

    def test_host_export_jwt_is_short_lived_and_private_key_stays_in_temp_file(self):
        private_key = "-----BEGIN PRIVATE KEY-----\nprivate-key-material\n-----END PRIVATE KEY-----\n"
        calls = []
        key_paths = []
        runner_temp = tempfile.TemporaryDirectory()
        self.addCleanup(runner_temp.cleanup)

        def sign(command, **kwargs):
            calls.append((command, kwargs))
            key_path = Path(command[-1])
            key_paths.append(key_path)
            self.assertEqual(Path(runner_temp.name), key_path.parent.parent)
            self.assertEqual(private_key, key_path.read_text(encoding="utf-8"))
            self.assertEqual(0o600, key_path.stat().st_mode & 0o777)
            self.assertNotIn(private_key, command)
            self.assertEqual({"PATH": os.environ.get("PATH", "/usr/bin:/bin")}, kwargs["env"])
            return subprocess.CompletedProcess(command, 0, stdout=b"signed-fixture", stderr=b"")

        with mock.patch.dict(os.environ, {"RUNNER_TEMP": runner_temp.name}, clear=False):
            token = t.mint_host_export_app_jwt(204, private_key, NOW, run=sign)
        header, payload, signature = token.split(".")
        decode = lambda value: json.loads(
            base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        )
        self.assertEqual({"alg": "RS256", "typ": "JWT"}, decode(header))
        self.assertEqual({"iat": NOW - 30, "exp": NOW + 510, "iss": "204"}, decode(payload))
        self.assertEqual(t._base64url(b"signed-fixture"), signature)
        self.assertNotIn("private-key-material", token)
        self.assertEqual(1, len(calls))
        self.assertFalse(key_paths[0].exists())


class AuthTests(unittest.TestCase):
    def api(self, value):
        permissions = copy.deepcopy(t.PERMISSIONS[value['operation']])
        return mock.Mock(call=mock.Mock(side_effect=[
            {'id': 22, 'permissions': permissions},
            {'id': 33, 'app_id': 22, 'account': {'login': 'Verjson'}, 'repository_selection': 'selected', 'suspended_at': None, 'permissions': permissions},
            {'permissions': permissions, 'token': 'installation-secret', 'expires_at': stamp(NOW + 3600)},
            {'total_count': 1, 'repositories': [{'id': 11, 'full_name': value['github']['repository']}]},
        ]))

    def test_role_credential_mints_exact_repository_and_permissions(self):
        for operation in ('manifest', 'probe', 'host-export'):
            value = host_request() if operation == 'host-export' else request(operation)
            api = self.api(value)
            self.assertEqual(t.installation_token(api, value, 'explicit-app-jwt', NOW), 'installation-secret')
            self.assertEqual(api.call.call_args_list[2].args[3], {'repository_ids': [11], 'permissions': t.PERMISSIONS[operation]})

    def test_missing_credential_does_not_fall_back_to_ambient_tokens(self):
        api = mock.Mock()
        with mock.patch.dict(os.environ, {'GH_TOKEN': 'broad', 'GH_RUNNER_CONTROL_TOKEN': 'control'}):
            with self.assertRaisesRegex(t.TransportError, exactly('explicit dedicated App JWT is required')): t.installation_token(api, request(), None, NOW)
        api.call.assert_not_called()

    def test_broad_app_wrong_installation_expiry_and_repository_are_rejected(self):
        app = 'App identity or permissions differ from the dedicated transport role'
        installation = 'installation identity or scope differs'
        scoped = 'installation token is not scoped to one repository'
        mutations = [(0, lambda v: v['permissions'].update(organization_self_hosted_runners='write'), app),
                     (0, lambda v: v.update(id=22.0), app), (0, lambda v: v.update(id=9), app), (1, lambda v: v.update(repository_selection='all'), installation),
                     (1, lambda v: v.update(app_id=9), installation), (1, lambda v: v.update(suspended_at=stamp(NOW)), installation),
                     (2, lambda v: v.update(expires_at=stamp(NOW)), 'installation token expiry is invalid'),
                     (2, lambda v: v.update(permissions={'administration': 'write'}), 'minted token permissions differ'),
                     (3, lambda v: v.update(total_count=True), scoped), (3, lambda v: v.update(total_count=2), scoped),
                     (3, lambda v: v['repositories'][0].update(id=9), 'installation token repository differs')]
        for index, mutate, reason in mutations:
            value = request(); api = self.api(value)
            replies = list(api.call.side_effect); replies = copy.deepcopy(replies); mutate(replies[index]); api.call.side_effect = replies
            with self.subTest(index=index), self.assertRaisesRegex(t.TransportError, exactly(reason)): t.installation_token(api, value, 'explicit-app-jwt', NOW)


class ManifestTests(unittest.TestCase):
    def fixture(self):
        value = request('manifest'); r = value['release']
        manifest = {'source': {'repository': r['repository'], 'commit': r['sourceCommit']},
                    'release': {'workflow': {'path': '.github/workflows/container-release.yml', 'contractCommit': r['signerCommit']}},
                    'images': [{'variant': r['variant'], 'indexDigest': r['imageDigest']}]}
        raw = (json.dumps(manifest, indent=2) + '\n').encode(); r['manifestDigest'] = t.digest(raw)
        metadata = {'id': 44, 'name': 'release-manifest.json', 'state': 'uploaded', 'size': len(raw)}
        return value, raw, metadata

    def test_exact_asset_bytes_and_strict_attestation_are_bound(self):
        value, raw, metadata = self.fixture(); api = mock.Mock(call=mock.Mock(side_effect=[{'id': value['github']['repositoryId'], 'full_name': value['github']['repository']}, metadata, raw]))
        verifier = mock.Mock(return_value=mock.Mock(stdout=b'[{}]'))
        with mock.patch.dict(os.environ, {'HOME': '/untrusted', 'SSH_AUTH_SOCK': 'agent', 'DIGITALOCEAN_RUNNER_FLEET_TOKEN': 'mutation'}):
            result = t.fetch_manifest(api, value, 'read-only-token', run=verifier, clock=lambda: NOW)
        self.assertEqual(result['manifestBytes'].encode(), raw)
        args, kwargs = verifier.call_args
        for flag in ('--signer-workflow', '--signer-digest', '--source-ref', '--source-digest'): self.assertIn(flag, args[0])
        self.assertEqual(set(kwargs['env']), {'PATH', 'HOME', 'GH_TOKEN', 'GH_HOST'})
        self.assertNotEqual(kwargs['env']['HOME'], '/untrusted')
        self.assertEqual(kwargs['env']['GH_TOKEN'], 'read-only-token')

    def test_renamed_repository_uses_stable_api_identity_and_historical_signed_source(self):
        raw = (Path(__file__).parent / 'fixtures/container-deployment/transport/runner-v0.2.1-manifest.json').read_bytes()
        self.assertEqual(t.digest(raw), 'sha256:4f5bb96e1fe07f7b56cfe124206ed85c4e59b9715b3b4e18b3054d890dd1ad32')
        manifest = json.loads(raw)
        value = request('manifest')
        value['github'].update(repository='Verjson/verjson-git-runners', repositoryId=1301436066)
        value['release'].update(repository='Verjson/verjson-github-runner', assetId=532627568,
                                manifestDigest=t.digest(raw), sourceCommit=manifest['source']['commit'],
                                signerCommit=manifest['release']['workflow']['contractCommit'],
                                imageDigest=next(image['indexDigest'] for image in manifest['images'] if image['variant'] == 'pwsh'))
        repository = {'id': 1301436066, 'full_name': 'Verjson/verjson-git-runners'}
        metadata = {'id': 532627568, 'name': 'release-manifest.json', 'state': 'uploaded', 'size': len(raw)}
        verifier = mock.Mock(return_value=mock.Mock(stdout=b'[{}]'))
        api = mock.Mock(call=mock.Mock(side_effect=[repository, metadata, raw]))
        result = t.fetch_manifest(api, value, 'read', run=verifier, clock=lambda: NOW)
        self.assertEqual(result['manifestBytes'].encode(), raw)
        self.assertEqual(api.call.call_args_list[0].args[1], '/repositories/1301436066')
        self.assertEqual(api.call.call_args_list[1].args[1], '/repos/Verjson/verjson-git-runners/releases/assets/532627568')
        args = verifier.call_args.args[0]
        self.assertEqual(args[args.index('--repo') + 1], 'Verjson/verjson-github-runner')
        self.assertEqual(args[args.index('--source-digest') + 1], manifest['source']['commit'])
        self.assertEqual(args[args.index('--signer-digest') + 1], manifest['release']['workflow']['contractCommit'])
        for changed in ({'id': 99, 'full_name': repository['full_name']},
                        {'id': 1301436066, 'full_name': 'Verjson/unreviewed-renamed-repository'}):
            verifier.reset_mock()
            with self.assertRaisesRegex(t.TransportError, exactly('release API repository identity differs')):
                t.fetch_manifest(mock.Mock(call=mock.Mock(return_value=changed)), value, 'read', run=verifier, clock=lambda: NOW)
            verifier.assert_not_called()
        wrong_source = copy.deepcopy(value); wrong_source['release']['repository'] = 'Verjson/verjson-git-runners'
        with self.assertRaisesRegex(t.TransportError, exactly('manifest source differs')):
            t.fetch_manifest(mock.Mock(call=mock.Mock(side_effect=[repository, metadata, raw])), wrong_source, 'read', run=verifier, clock=lambda: NOW)
        verifier.assert_not_called()

    def test_altered_asset_and_failed_attestation_never_return_evidence(self):
        value, raw, metadata = self.fixture()
        with self.assertRaisesRegex(t.TransportError, exactly('release asset bytes differ from reviewed digest')): t.fetch_manifest(mock.Mock(call=mock.Mock(side_effect=[{'id': value['github']['repositoryId'], 'full_name': value['github']['repository']}, metadata, raw + b' '])), value, 'read', clock=lambda: NOW)
        verifier = mock.Mock(side_effect=subprocess.CalledProcessError(1, 'gh', stderr='Authorization: secret'))
        with self.assertRaisesRegex(t.TransportError, '^release attestation verification failed$'):
            t.fetch_manifest(mock.Mock(call=mock.Mock(side_effect=[{'id': value['github']['repositoryId'], 'full_name': value['github']['repository']}, metadata, raw])), value, 'read', run=verifier, clock=lambda: NOW)


class ProbeTests(unittest.TestCase):
    def run_probe(self, api):
        intents = []
        result = t.probe_runner(api, api.value, 'dispatch-token', record_intent=intents.append, clock=lambda: NOW, sleep=lambda _: self.fail('unexpected wait'))
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]['preDispatchMaxRunId'], 100)
        return result

    def test_real_dispatch_shape_and_authenticated_receipt_bind_every_identity(self):
        value = request(); api = ProbeAPI(value); result = self.run_probe(api)
        self.assertEqual(result['outcome'], 'passed')
        self.assertEqual(result['requestDigest'], t.digest(t.canonical(value)))
        posts = [call for call in api.calls if call[0] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][3]['ref'], 'runner-canary-v1.0.1')
        self.assertEqual(set(posts[0][3]['inputs']), {'runner_name', 'runner_id', 'runner_label', 'transaction_nonce', 'release_manifest', 'release_variant', 'image_digest'})

    def test_wrong_runner_nonce_manifest_image_workflow_or_stale_receipt_fails(self):
        runner, release = 'receipt runner differs', 'receipt release differs'
        checks = 'receipt workflow/job or representative checks differ'
        mutations = [(lambda r: r.update(schemaVersion=2.0), 'unsupported canary receipt'), (lambda r: r['runner'].update(id=55.0), runner),
                     (lambda r: r['runner'].update(id=9), runner), (lambda r: r.update(transactionNonce='replayed'), 'receipt nonce differs'),
                     (lambda r: r['release'].update(manifest='sha256:' + '9' * 64), release), (lambda r: r['release'].update(imageDigest='sha256:' + '9' * 64), release),
                     (lambda r: r['canary'].update(workflowSha='9' * 40), checks), (lambda r: r['canary'].update(runAttempt=2), checks),
                     (lambda r: r['canary'].update(pwshExecuted=False), checks), (lambda r: r['canary'].update(databaseServiceReachable=1), checks),
                     (lambda r: r['canary'].update(runAttempt=True), checks), (lambda r: r['canary'].update(runId=101.0), checks),
                     (lambda r: r['postCanary'].update(observedAt=stamp(NOW - 1900)), 'canary evidence is stale'),
                     (lambda r: r['postCanary'].update(freeBytes=0), 'canary capacity evidence is below policy floor'),
                     (lambda r: r['imageBuild'].update(diskBytesConsumed=True), 'representative image build differs'),
                     (lambda r: r['dependencies'].update(postgresImage='postgres:latest'), 'canary image dependency must be immutable')]
        for mutate, reason in mutations:
            api = ProbeAPI(request()); mutate(api.receipt); api.refresh_archive()
            with self.subTest(mutate=mutate), self.assertRaisesRegex(t.TransportError, exactly(reason)): self.run_probe(api)

    def test_run_job_artifact_and_tag_independent_evidence_mismatch_fails(self):
        run, job = 'run identity is stale or differs', 'independent job routing/outcome differs'
        provenance = 'artifact run provenance differs'
        mutations = [(lambda a: a.record.update(id=99), run), (lambda a: a.record.update(run_attempt=2), run), (lambda a: a.record.update(run_attempt=True), run),
                     (lambda a: a.record.update(head_sha='9' * 40), run), (lambda a: a.record.update(conclusion='failure'), 'representative run failed'),
                     (lambda a: a.job.update(runner_id=9), job), (lambda a: a.job.update(labels=['other']), job),
                     (lambda a: a.artifact.update(expired=True), provenance), (lambda a: a.artifact.update(digest='sha256:' + '9' * 64), 'artifact archive digest differs'),
                     (lambda a: a.artifact['workflow_run'].update(id=9), provenance),
                     (lambda a: a.tag['object'].update(sha='9' * 40), 'canary tag does not resolve to reviewed commit')]
        for mutate, reason in mutations:
            api = ProbeAPI(request()); mutate(api)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(t.TransportError, exactly(reason)): self.run_probe(api)

    def test_replayed_nonce_and_unpersisted_intent_prevent_dispatch(self):
        api = ProbeAPI(request()); api.baseline['workflow_runs'][0]['display_title'] = 'runner-canary-' + api.value['probe']['transactionNonce']
        with self.assertRaisesRegex(t.TransportError, exactly('transaction nonce was already dispatched; reconcile before retry')): self.run_probe(api)
        self.assertFalse(api.dispatched)
        api = ProbeAPI(request()); unpersisted = FileExistsError()
        with self.assertRaises(FileExistsError) as raised:
            t.probe_runner(api, api.value, 'token', record_intent=mock.Mock(side_effect=unpersisted), clock=lambda: NOW)
        self.assertIs(raised.exception, unpersisted)
        self.assertFalse(api.dispatched)

    def test_replay_detection_searches_beyond_the_first_hundred_runs(self):
        api = PagedProbeAPI(request())
        with self.assertRaisesRegex(t.TransportError, 'already dispatched'):
            self.run_probe(api)
        self.assertFalse(api.dispatched)
        run_queries = [call[1] for call in api.calls if '/runs?' in call[1]]
        self.assertEqual(2, len(run_queries))
        self.assertIn('page=2', run_queries[1])

    def test_incomplete_run_inventory_is_rejected_before_dispatch(self):
        api = IncompleteProbeAPI(request())
        with self.assertRaisesRegex(t.TransportError, 'incomplete'):
            self.run_probe(api)
        self.assertFalse(api.dispatched)

    def test_over_sized_run_inventory_is_rejected_before_dispatch(self):
        api = OversizedProbeAPI(request())
        with self.assertRaisesRegex(t.TransportError, 'bounded complete query'):
            self.run_probe(api)
        self.assertFalse(api.dispatched)

    def test_run_inventory_request_budget_fails_closed_before_dispatch(self):
        api = PagedProbeAPI(request())
        intent = mock.Mock()
        with mock.patch.object(t, 'MAX_RUN_REQUESTS', 1):
            with self.assertRaisesRegex(t.TransportError, 'remaining request budget'):
                t.probe_runner(api, api.value, 'token', record_intent=intent,
                               clock=lambda: NOW, sleep=lambda _: self.fail('unexpected wait'))
        intent.assert_not_called()
        self.assertFalse(api.dispatched)
        run_queries = [call[1] for call in api.calls if '/runs?' in call[1]]
        self.assertEqual(1, len(run_queries))

    def test_run_inventory_request_budget_is_shared_with_polling(self):
        api = ProbeAPI(request())
        api.record['status'] = 'in_progress'
        with mock.patch.object(t, 'MAX_RUN_REQUESTS', 2):
            with self.assertRaisesRegex(t.TransportError, 'request budget'):
                t.probe_runner(api, api.value, 'token', record_intent=lambda _: None,
                               clock=lambda: NOW, sleep=lambda _: None)
        self.assertTrue(api.dispatched)
        run_queries = [call[1] for call in api.calls if '/runs?' in call[1]]
        self.assertEqual(2, len(run_queries))

    def test_uncertain_dispatch_is_never_retried(self):
        api = ProbeAPI(request()); original = api.call
        def fail(method, path, token, body=None, binary=False):
            if path.endswith('/dispatches'):
                api.calls.append((method, path, token, body)); raise t.TransportError('uncertain dispatch')
            return original(method, path, token, body, binary)
        api.call = fail
        with self.assertRaisesRegex(t.TransportError, exactly('uncertain dispatch')): self.run_probe(api)
        self.assertEqual(sum(call[0] == 'POST' for call in api.calls), 1)

    def test_archive_traversal_is_rejected_without_extracting_files(self):
        api = ProbeAPI(request()); api.archive_path = '../receipt.json'; api.refresh_archive()
        with self.assertRaisesRegex(t.TransportError, exactly('artifact archive layout is invalid')): self.run_probe(api)

    def test_timeout_never_returns_a_success_receipt(self):
        api = ProbeAPI(request()); api.record['status'] = 'in_progress'; current = [NOW]
        def advance(seconds): current[0] += seconds
        with self.assertRaisesRegex(t.TransportError, 'timed out'):
            t.probe_runner(api, api.value, 'token', record_intent=lambda _: None, clock=lambda: current[0], sleep=advance)


class HTTPTests(unittest.TestCase):
    def test_api_error_redacts_body_and_token(self):
        api = t.GitHubHTTP(); api.opener = mock.Mock()
        api.opener.open.side_effect = HTTPError('https://api.github.com/path', 403, 'Authorization: secret', {}, None)
        with self.assertRaisesRegex(t.TransportError, '^GitHub request failed with HTTP 403$'): api.call('GET', '/path', 'secret')

    def test_asset_redirect_never_forwards_api_authorization(self):
        api = t.GitHubHTTP(); api.opener = mock.Mock()
        redirect = HTTPError('https://api.github.com/path', 302, 'redirect', {'Location': 'https://release-assets.githubusercontent.com/signed'}, None)
        response = mock.MagicMock(); response.__enter__.return_value.read.return_value = b'bytes'
        api.opener.open.side_effect = [redirect, response]
        self.assertEqual(api.call('GET', '/path', 'secret', binary=True), b'bytes')
        second = api.opener.open.call_args_list[1].args[0]
        self.assertIsNone(second.get_header('Authorization'))

    def test_unreviewed_redirect_is_not_followed(self):
        for destination in ('http://release-assets.githubusercontent.com/x', 'https://attacker.invalid/x', 'https://user@release-assets.githubusercontent.com/x'):
            api = t.GitHubHTTP(); api.opener = mock.Mock()
            api.opener.open.side_effect = HTTPError('https://api.github.com/path', 302, 'redirect', {'Location': destination}, None)
            with self.subTest(destination=destination), self.assertRaisesRegex(t.TransportError, exactly('asset redirect escaped reviewed storage hosts')): api.call('GET', '/path', 'secret', binary=True)
            self.assertEqual(api.opener.open.call_count, 1)


if __name__ == '__main__':
    unittest.main()
