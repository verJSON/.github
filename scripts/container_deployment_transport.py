#!/usr/bin/env python3
"""Credential-separated GitHub transport for deployment evidence and probes."""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from urllib import error, parse, request
import zipfile

MAX_BYTES = 2_000_000
SHA = re.compile(r'[0-9a-f]{40}')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
CANARY_PATH = '.github/workflows/runner-canary.yml'
CANARY_REPOSITORY = 'Verjson/.github'
# deployment-receipt uses the job GITHUB_TOKEN (actions:read), not an App JWT.
PERMISSIONS = {
    'manifest': {'metadata': 'read', 'contents': 'read', 'attestations': 'read'},
    'probe': {'metadata': 'read', 'contents': 'read', 'actions': 'write'},
    'host-export': {
        'metadata': 'read',
        'contents': 'read',
        'attestations': 'read',
        'organization_self_hosted_runners': 'read',
    },
}
HOST_EXPORT_SECRET_ENV = (
    'RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY',
    'RUNNER_HOST_EVIDENCE_DOCTL_CONFIG',
    'RUNNER_HOST_EVIDENCE_KNOWN_HOSTS',
)
HOST_EXPORT_APP_KEY_ENV = 'RUNNER_HOST_EVIDENCE_APP_PRIVATE_KEY'
RUNS_PAGE_SIZE = 100
MAX_RUN_RECORDS = 1_000
MAX_RUN_REQUESTS = 40
MAX_ARTIFACT_DOWNLOADS = 20
ATTEMPT_ID = re.compile(r'[1-9][0-9]*\.[1-9][0-9]*')
RETAINED_RECEIPT_AUTHORITY = re.compile(
    r'(?P<attemptId>[1-9][0-9]*\.[1-9][0-9]*)/'
    r'(?P<artifactId>[1-9][0-9]*)@'
    r'(?P<digest>sha256:[0-9a-f]{64})'
)
DEPLOYMENT_ARTIFACT_NAME = re.compile(
    r'^runner-deployment-(?P<runId>[1-9][0-9]*)-(?P<runAttempt>[1-9][0-9]*)-'
    r'(?P<kind>admitted|progress(?:-[1-9][0-9]*)?|final)$'
)
RECEIPT_MEMBER = re.compile(
    r'^(?:deployment-receipts/)?revision-(?P<revision>\d{4})\.json$'
)
ARTIFACT_KIND_RANK = {'progress': 0, 'admitted': 1, 'final': 2}


class TransportError(ValueError):
    pass


class ReceiptAbsentFromArtifact(TransportError):
    """The requested receipt digest is not present in this artifact."""


def require(condition, message):
    if not condition:
        raise TransportError(message)


def object_keys(value, keys, name):
    require(isinstance(value, dict) and set(value) == set(keys), f'{name} fields differ')


def positive(value):
    return type(value) is int and value > 0


# GitHub owner/repository names are ASCII case-insensitive identities (ADR 0213).
# Live API `login` / `full_name` may use the post-rename display case while the
# reviewed request still carries the prior spelling.
_ORGANIZATION_IDENTITY = re.compile(r'[A-Za-z0-9._-]+')
_REPOSITORY_IDENTITY = re.compile(r'[A-Za-z0-9._-]+/[A-Za-z0-9._-]+')


def same_organization(observed, configured):
    if not isinstance(observed, str) or not isinstance(configured, str):
        return False
    if (
        _ORGANIZATION_IDENTITY.fullmatch(observed) is None
        or _ORGANIZATION_IDENTITY.fullmatch(configured) is None
    ):
        return False
    return observed.casefold() == configured.casefold()


def same_repository(observed, configured):
    if not isinstance(observed, str) or not isinstance(configured, str):
        return False
    if (
        _REPOSITORY_IDENTITY.fullmatch(observed) is None
        or _REPOSITORY_IDENTITY.fullmatch(configured) is None
    ):
        return False
    return observed.casefold() == configured.casefold()


def timestamp(value):
    require(isinstance(value, str) and value.endswith('Z'), 'timestamp must be UTC')
    try:
        return datetime.fromisoformat(value[:-1] + '+00:00').timestamp()
    except ValueError:
        raise TransportError('timestamp is invalid') from None


def digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def persist_record(path, value):
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, sort_keys=True) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON field')
            result[key] = value
        return result
    require(len(raw) <= MAX_BYTES, 'JSON exceeds transport size limit')
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: require(False, 'non-finite JSON number'))
    except (UnicodeError, json.JSONDecodeError):
        raise TransportError('transport JSON is invalid') from None


def _validate_request_lifetime(value, now):
    issued, expires = timestamp(value['issuedAt']), timestamp(value['expiresAt'])
    require(issued == int(issued) and expires == int(expires), 'request timestamps must use whole seconds')
    require(issued <= now < expires and expires - issued <= 1800, 'request is stale or exceeds bounded lifetime')


def _validate_repository_name(name, field):
    require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', name)
            and name.split('/')[1] not in ('.', '..'), f'{field} is invalid')


def validate_request(value, now):
    require(isinstance(value, dict), 'request must be an object')
    operation = value.get('operation')
    require(value.get('schemaVersion') == 1 and type(value.get('schemaVersion')) is int, 'unsupported request version')
    if operation == 'deployment-receipt':
        object_keys(
            value,
            (
                'schemaVersion', 'operation', 'issuedAt', 'expiresAt', 'github',
                'receiptDigest', 'purpose', 'sourceAttemptId', 'retainedReceiptAuthority',
            ),
            'request',
        )
        require(operation == 'deployment-receipt', 'unsupported transport operation')
        _validate_request_lifetime(value, now)
        github = value['github']
        object_keys(github, ('repository', 'repositoryId'), 'GitHub authority')
        _validate_repository_name(github['repository'], 'repository identity')
        require(positive(github['repositoryId']), 'reviewed numeric GitHub identities are required')
        require(isinstance(value['receiptDigest'], str) and DIGEST.fullmatch(value['receiptDigest']),
                'receipt digest is invalid')
        require(value['purpose'] in ('rollback', 'resume'), 'deployment receipt purpose is invalid')
        source = value['sourceAttemptId']
        require(source is None or (isinstance(source, str) and ATTEMPT_ID.fullmatch(source)),
                'source attempt identity is invalid')
        authority = value['retainedReceiptAuthority']
        require(
            authority is None
            or (isinstance(authority, str) and RETAINED_RECEIPT_AUTHORITY.fullmatch(authority)),
            'retained receipt authority is invalid',
        )
        return value

    common = ('schemaVersion', 'operation', 'attemptId', 'fleetSelector', 'lane',
              'issuedAt', 'expiresAt', 'deploymentContractCommit', 'configDigest',
              'planDigest', 'action', 'rollbackOfAttempt', 'github', 'release')
    if operation == 'host-export':
        object_keys(value, (*common, 'hostExport'), 'request')
    else:
        object_keys(value, (*common, 'probe'), 'request')
    require(operation in ('manifest', 'probe', 'host-export'), 'unsupported transport operation')
    require(isinstance(value['attemptId'], str) and ATTEMPT_ID.fullmatch(value['attemptId']), 'attempt identity is invalid')
    require(isinstance(value['deploymentContractCommit'], str) and SHA.fullmatch(value['deploymentContractCommit']), 'deployment contract must be immutable')
    require(isinstance(value['configDigest'], str) and DIGEST.fullmatch(value['configDigest']), 'admitted configuration digest is invalid')
    require((operation != 'probe' and value['planDigest'] is None) or
            (isinstance(value['planDigest'], str) and DIGEST.fullmatch(value['planDigest'])), 'approved plan digest is invalid')
    require(value['action'] in ('deploy', 'rollback'), 'deployment action is invalid')
    rollback = value['rollbackOfAttempt']
    require((value['action'] == 'deploy' and rollback is None) or
            (value['action'] == 'rollback' and isinstance(rollback, str)
             and ATTEMPT_ID.fullmatch(rollback) and rollback != value['attemptId']),
            'rollback attempt identity is invalid')
    for field in ('fleetSelector', 'lane'):
        require(isinstance(value[field], str) and re.fullmatch(r'[a-z][a-z0-9_-]{1,31}', value[field]), f'{field} is invalid')
    _validate_request_lifetime(value, now)
    github = value['github']
    object_keys(github, ('repository', 'repositoryId', 'appId', 'installationId'), 'GitHub authority')
    _validate_repository_name(github['repository'], 'repository identity')
    require(all(positive(github[k]) for k in ('repositoryId', 'appId', 'installationId')), 'reviewed numeric GitHub identities are required')
    release = value['release']
    object_keys(release, ('repository', 'assetId', 'manifestDigest', 'variant', 'imageDigest',
                         'sourceCommit', 'sourceRef', 'signerWorkflow', 'signerCommit'), 'release')
    _validate_repository_name(release['repository'], 'release repository')
    require(positive(release['assetId']), 'release asset identity is required')
    for field in ('manifestDigest', 'imageDigest'):
        require(isinstance(release[field], str) and DIGEST.fullmatch(release[field]), f'{field} is invalid')
    for field in ('sourceCommit', 'signerCommit'):
        require(isinstance(release[field], str) and SHA.fullmatch(release[field]), f'{field} must be immutable')
    require(release['sourceRef'] == 'refs/heads/main', 'release source must be main')
    require(release['signerWorkflow'] == 'Verjson/.github/.github/workflows/container-release.yml', 'release signer differs')
    require(isinstance(release['variant'], str) and re.fullmatch(r'[a-z][a-z0-9-]{0,63}', release['variant']), 'release variant is invalid')
    if operation != 'host-export':
        probe = value['probe']
        object_keys(probe, ('runnerId', 'runnerName', 'runnerLabel', 'transactionNonce', 'workflowId', 'workflowRef', 'workflowCommit'), 'probe')
        require(positive(probe['runnerId']) and positive(probe['workflowId']), 'exact runner/workflow IDs are required')
        for field in ('runnerName', 'runnerLabel'):
            require(isinstance(probe[field], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', probe[field]), f'{field} is invalid')
        require(isinstance(probe['transactionNonce'], str) and re.fullmatch(r'[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}', probe['transactionNonce']), 'transaction nonce must be an exact UUID')
        require(isinstance(probe['workflowCommit'], str) and SHA.fullmatch(probe['workflowCommit']), 'canary workflow commit must be immutable')
        require(isinstance(probe['workflowRef'], str) and re.fullmatch(r'refs/tags/runner-canary-v[0-9]+\.[0-9]+\.[0-9]+', probe['workflowRef']), 'reviewed canary tag is required')
    if operation == 'host-export':
        host = value['hostExport']
        object_keys(host, ('project', 'doContext', 'doSshKey', 'runnerNames', 'maxAgeSeconds', 'purpose', 'runnerName'), 'host export')
        for field in ('project', 'doContext', 'doSshKey'):
            require(isinstance(host[field], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', host[field]), f'host export {field} is invalid')
        names = host['runnerNames']
        require(isinstance(names, list) and 1 <= len(names) <= 3 and all(
            isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', name)
            for name in names
        ) and len(set(names)) == len(names), 'host export runner names are invalid')
        age = host['maxAgeSeconds']
        require(type(age) is int and 1 <= age <= 3600, 'host export maximum age is invalid')
        require(host['purpose'] in ('baseline', 'post-update', 'capacity'), 'host export purpose is invalid')
        runner_name = host['runnerName']
        require((host['purpose'] == 'post-update' and isinstance(runner_name, str)
                 and runner_name in names)
                or (host['purpose'] != 'post-update' and runner_name is None),
                'host export runner selection differs from purpose')
    if operation == 'probe':
        require(same_repository(github['repository'], CANARY_REPOSITORY),
                'canary dispatch targets another repository')
    return value


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubHTTP:
    def __init__(self):
        self.opener = request.build_opener(request.ProxyHandler({}), NoRedirect())

    def call(self, method, path, token, body=None, binary=False):
        require(path.startswith('/') and not path.startswith('//') and '\\' not in path, 'invalid API path')
        headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'}
        req = request.Request('https://api.github.com' + path, data=None if body is None else canonical(body), headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=30) as response:
                raw = response.read(MAX_BYTES + 1)
        except error.HTTPError as failure:
            if binary and method == 'GET' and failure.code in (301, 302, 303, 307, 308):
                location = failure.headers.get('Location', '')
                target = parse.urlsplit(location)
                host = target.hostname or ''
                require(target.scheme == 'https' and target.port in (None, 443) and not target.username and not target.password
                        and (host in ('objects.githubusercontent.com', 'release-assets.githubusercontent.com')
                             or host.endswith('.blob.core.windows.net') or host.endswith('.actions.githubusercontent.com')),
                        'asset redirect escaped reviewed storage hosts')
                # Signed storage receives no API Authorization header or ambient credentials.
                try:
                    with self.opener.open(request.Request(location), timeout=30) as response:
                        raw = response.read(MAX_BYTES + 1)
                except (error.URLError, OSError):
                    raise TransportError('asset download failed') from None
            else:
                raise TransportError(f'GitHub request failed with HTTP {failure.code}') from None
        except (error.URLError, OSError):
            raise TransportError('GitHub transport failed') from None
        require(len(raw) <= MAX_BYTES, 'GitHub response exceeds size limit')
        if binary:
            return raw
        value = decode(raw) if raw else None
        require(value is None or isinstance(value, dict), 'GitHub response is not an object')
        return value


def installation_token(api, value, jwt, now):
    require(isinstance(jwt, str) and jwt and '\n' not in jwt, 'explicit dedicated App JWT is required')
    operation = value['operation']
    require(operation in PERMISSIONS, 'host export has no GitHub credential capability')
    expected = PERMISSIONS[operation]
    authority = value['github']
    app = api.call('GET', '/app', jwt)
    require(positive(app.get('id')) and app['id'] == authority['appId'] and app.get('permissions') == expected,
            'App identity or permissions differ from the dedicated transport role')
    installation = api.call('GET', f"/app/installations/{authority['installationId']}", jwt)
    require(positive(installation.get('id')) and positive(installation.get('app_id')) and installation['id'] == authority['installationId'] and installation.get('app_id') == authority['appId']
            and same_organization(installation.get('account', {}).get('login'), authority['repository'].split('/')[0])
            and installation.get('repository_selection') == 'selected' and installation.get('suspended_at') is None
            and installation.get('permissions') == expected, 'installation identity or scope differs')
    minted = api.call('POST', f"/app/installations/{authority['installationId']}/access_tokens", jwt,
                      {'repository_ids': [authority['repositoryId']], 'permissions': expected})
    require(minted.get('permissions') == expected and isinstance(minted.get('token'), str) and minted['token'], 'minted token permissions differ')
    require(now < timestamp(minted.get('expires_at')) <= now + 3660, 'installation token expiry is invalid')
    token = minted['token']
    repositories = api.call('GET', '/installation/repositories?per_page=100', token)
    require(type(repositories.get('total_count')) is int and repositories['total_count'] == 1 and len(repositories.get('repositories', [])) == 1,
            'installation token is not scoped to one repository')
    repository = repositories['repositories'][0]
    require(positive(repository.get('id')) and repository['id'] == authority['repositoryId']
            and same_repository(repository.get('full_name'), authority['repository']),
            'installation token repository differs')
    return token


def _base64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def mint_host_export_app_jwt(app_id, private_key, now, run=subprocess.run):
    require(positive(app_id), 'host export App identity is invalid')
    require(isinstance(private_key, str) and private_key.startswith('-----BEGIN ')
            and '\0' not in private_key, 'dedicated host export App private key is unavailable')
    issued_at = int(now) - 30
    header = _base64url(canonical({'alg': 'RS256', 'typ': 'JWT'}))
    payload = _base64url(canonical({'iat': issued_at, 'exp': issued_at + 540, 'iss': str(app_id)}))
    signing_input = f'{header}.{payload}'.encode('ascii')
    with _runner_temp_directory('deployment-host-app-key-') as temporary:
        key_path = Path(temporary) / 'app-private-key.pem'
        _write_private_file(key_path, private_key, 64_000, 'host export App private key')
        try:
            completed = run(
                ['openssl', 'dgst', '-sha256', '-sign', str(key_path)],
                input=signing_input,
                check=False,
                capture_output=True,
                timeout=30,
                env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')},
            )
        except (OSError, subprocess.SubprocessError):
            raise TransportError('host export App JWT could not be signed') from None
        require(completed.returncode == 0 and completed.stdout,
                'host export App JWT could not be signed')
    signature = _base64url(completed.stdout)
    return f'{header}.{payload}.{signature}'


def fetch_manifest(api, value, token, run=subprocess.run, clock=time.time):
    validate_request(value, clock())
    release = value['release']
    authority = value['github']
    repository = api.call('GET', f"/repositories/{authority['repositoryId']}", token)
    require(positive(repository.get('id')) and repository['id'] == authority['repositoryId']
            and same_repository(repository.get('full_name'), authority['repository']),
            'release API repository identity differs')
    path = f"/repos/{authority['repository']}/releases/assets/{release['assetId']}"
    metadata = api.call('GET', path, token)
    require(positive(metadata.get('id')) and metadata['id'] == release['assetId'] and metadata.get('name') == 'release-manifest.json'
            and metadata.get('state') == 'uploaded' and positive(metadata.get('size')) and metadata['size'] <= MAX_BYTES,
            'release asset metadata differs')
    raw = api.call('GET', path, token, binary=True)
    require(len(raw) == metadata['size'] and digest(raw) == release['manifestDigest'], 'release asset bytes differ from reviewed digest')
    manifest = decode(raw)
    require(same_repository(manifest.get('source', {}).get('repository'), release['repository'])
            and manifest['source'].get('commit') == release['sourceCommit'], 'manifest source differs')
    workflow = manifest.get('release', {}).get('workflow', {})
    require(workflow.get('path') == '.github/workflows/container-release.yml' and workflow.get('contractCommit') == release['signerCommit'], 'manifest release signer differs')
    images = manifest.get('images')
    require(isinstance(images, list), 'manifest images are invalid')
    selected = [image for image in images if isinstance(image, dict) and image.get('variant') == release['variant']]
    require(len(selected) == 1 and selected[0].get('indexDigest') == release['imageDigest'], 'manifest variant/image differs')
    with tempfile.TemporaryDirectory(prefix='deployment-manifest-') as directory:
        subject = Path(directory) / 'release-manifest.json'
        subject.write_bytes(raw)
        # No HOME configuration, SSH agent, mutation token, or App JWT reaches gh.
        environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': directory, 'GH_TOKEN': token, 'GH_HOST': 'github.com'}
        try:
            verified = run(['gh', 'attestation', 'verify', str(subject), '--repo', release['repository'],
                            '--signer-workflow', release['signerWorkflow'], '--signer-digest', release['signerCommit'],
                            '--source-ref', release['sourceRef'], '--source-digest', release['sourceCommit'], '--format', 'json'],
                           check=True, capture_output=True, timeout=120, env=environment)
        except (OSError, subprocess.SubprocessError):
            raise TransportError('release attestation verification failed') from None
        verification = decode(verified.stdout)
        require(isinstance(verification, list) and verification, 'attestation verifier returned no verified evidence')
    validate_request(value, clock())
    return {'schemaVersion': 1, 'requestDigest': digest(canonical(value)), 'manifestIdentity': digest(raw),
            'manifestBytes': raw.decode('utf-8'), 'manifest': manifest,
            'attestation': {'verified': True, 'repository': release['repository'], 'sourceRef': release['sourceRef'],
                            'sourceCommit': release['sourceCommit'], 'signerWorkflow': release['signerWorkflow'],
                            'contractCommit': release['signerCommit'], 'subjectDigest': digest(raw),
                            'expiresAt': value['expiresAt'], 'verificationDigest': digest(canonical(verification))}}


def validate_probe_receipt(value, receipt, run_record, job, artifact, now):
    validate_request(value, now)
    probe, release = value['probe'], value['release']
    require(positive(run_record.get('id')) and type(run_record.get('run_attempt')) is int
            and run_record['run_attempt'] == 1 and positive(job.get('id')), 'independent run/job scalar types differ')
    object_keys(receipt, ('schemaVersion', 'contract', 'transactionNonce', 'runner', 'release', 'canary',
                          'imageBuild', 'postCanary', 'dependencies', 'artifact'), 'canary receipt')
    object_keys(receipt['imageBuild'], ('conclusion', 'imageDigest', 'diskBytesConsumed'), 'image build')
    object_keys(receipt['postCanary'], ('freeBytes', 'freeInodePercent', 'observedAt'), 'post-canary observation')
    object_keys(receipt['artifact'], ('name', 'contentDigest'), 'receipt artifact')
    dependencies = receipt['dependencies']
    object_keys(dependencies, ('uploadArtifactCommit', 'postgresImage', 'nginxImage', 'curlImage', 'buildBaseImage'), 'canary dependencies')
    require(isinstance(dependencies['uploadArtifactCommit'], str) and SHA.fullmatch(dependencies['uploadArtifactCommit']), 'artifact uploader must be immutable')
    for name in ('postgresImage', 'nginxImage', 'curlImage', 'buildBaseImage'):
        require(isinstance(dependencies[name], str) and re.fullmatch(r'[a-z0-9/._-]+@sha256:[a-f0-9]{64}', dependencies[name]), 'canary image dependency must be immutable')
    require(type(receipt.get('schemaVersion')) is int and receipt['schemaVersion'] == 2 and receipt.get('contract') == 'verjson-runner-promotion/2', 'unsupported canary receipt')
    require(receipt.get('transactionNonce') == probe['transactionNonce'], 'receipt nonce differs')
    require(canonical(receipt.get('runner')) == canonical({'id': probe['runnerId'], 'name': probe['runnerName']}), 'receipt runner differs')
    require(receipt.get('release') == {'manifest': release['manifestDigest'], 'variant': release['variant'], 'imageDigest': release['imageDigest']}, 'receipt release differs')
    canary = receipt.get('canary', {})
    expected = {'repository': CANARY_REPOSITORY, 'workflow': 'canonical-db-backed-runner-canary',
                'runId': run_record['id'], 'jobId': job['id'], 'runAttempt': run_record['run_attempt'],
                'event': 'workflow_dispatch', 'headBranch': probe['workflowRef'].removeprefix('refs/tags/'),
                'headSha': probe['workflowCommit'], 'workflowPath': CANARY_PATH, 'workflowSha': probe['workflowCommit'],
                'conclusion': 'success', 'databaseServiceReachable': True, 'dockerServiceReachable': True, 'pwshExecuted': True,
                'runUrl': f"https://github.com/{CANARY_REPOSITORY}/actions/runs/{run_record['id']}",
                'jobUrl': f"https://github.com/{CANARY_REPOSITORY}/actions/runs/{run_record['id']}/job/{job['id']}"}
    require(canonical(canary) == canonical(expected), 'receipt workflow/job or representative checks differ')
    require(positive(job.get('runner_id')) and job['runner_id'] == probe['runnerId'] and job.get('runner_name') == probe['runnerName']
            and probe['runnerLabel'] in job.get('labels', []) and job.get('status') == 'completed'
            and job.get('conclusion') == 'success', 'independent job routing/outcome differs')
    observed = receipt.get('postCanary', {})
    require(timestamp(value['issuedAt']) <= timestamp(observed.get('observedAt')) <= now
            and now - timestamp(observed['observedAt']) <= 1800, 'canary evidence is stale')
    require(type(observed.get('freeBytes')) is int and observed['freeBytes'] >= 10_737_418_240
            and type(observed.get('freeInodePercent')) is int and 10 <= observed['freeInodePercent'] <= 100,
            'canary capacity evidence is below policy floor')
    require(receipt.get('imageBuild', {}).get('conclusion') == 'success'
            and receipt['imageBuild'].get('imageDigest') == release['imageDigest']
            and type(receipt['imageBuild']['diskBytesConsumed']) is int and receipt['imageBuild']['diskBytesConsumed'] == 0,
            'representative image build differs')
    self_digest = receipt.get('artifact', {}).get('contentDigest')
    unsigned = dict(receipt) | {'artifact': {'name': 'runner-promotion-receipt', 'contentDigest': ''}}
    require(receipt.get('artifact', {}).get('name') == 'runner-promotion-receipt' and digest(canonical(unsigned)) == self_digest, 'receipt content digest differs')
    require(artifact.get('expired') is False and positive(artifact.get('workflow_run', {}).get('id')) and artifact['workflow_run']['id'] == run_record['id'], 'artifact run provenance differs')


def _runner_temp_directory(prefix):
    runner_temp = os.environ.get('RUNNER_TEMP')
    require(
        isinstance(runner_temp, str) and Path(runner_temp).is_absolute(),
        'runner temporary directory unavailable',
    )
    try:
        return tempfile.TemporaryDirectory(prefix=prefix, dir=runner_temp)
    except OSError:
        raise TransportError('runner temporary directory unavailable') from None


def _write_private_file(path, contents, maximum, description):
    require(isinstance(contents, str) and contents and '\0' not in contents,
            f'{description} is unavailable')
    raw = contents.encode('utf-8')
    require(len(raw) <= maximum, f'{description} exceeds its size limit')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _validate_runner_roster(fleet, lane, expected_names):
    require(
        isinstance(fleet, dict) and fleet.get('lane') == lane,
        'host export lane differs',
    )
    runners = fleet.get('runners')
    require(
        isinstance(runners, list)
        and len(runners) == len(expected_names)
        and all(
            isinstance(runner, dict) and isinstance(runner.get('name'), str)
            for runner in runners
        )
        and sorted(runner['name'] for runner in runners) == sorted(expected_names),
        'host export runner identities differ',
    )
    return runners


def _validate_capacity_report(report, lane, expected_names):
    runners = _validate_runner_roster(report.get('fleet'), lane, expected_names)
    require(
        all(type(runner.get('available')) is bool for runner in runners),
        'host capacity runner availability is invalid',
    )
    available_capacity = report.get('availableCapacity')
    require(
        type(available_capacity) is int
        and available_capacity >= 0
        and available_capacity == sum(runner['available'] for runner in runners),
        'host capacity count differs from runner availability',
    )
    return available_capacity


def _deployment_cli():
    executable = os.environ.get('VERJSON_DEPLOYMENT_CLI')
    root = os.environ.get('VERJSON_DEPLOYMENT_CLI_ROOT')
    require(bool(executable and root), 'contract-acquired verjson-cloud executable is unavailable')
    try:
        path = Path(executable).resolve(strict=True)
        acquired_root = Path(root).resolve(strict=True)
        path.relative_to(acquired_root)
    except (OSError, ValueError):
        raise TransportError('deployment CLI escapes its immutable acquisition root') from None
    require(Path(executable).is_absolute() and Path(root).is_absolute()
            and os.access(path, os.X_OK), 'contract-acquired verjson-cloud executable is invalid')
    return str(path)


def _verify_host_baseline(raw, runner, value, token, home, run):
    require(isinstance(raw, str) and len(raw.encode('utf-8')) <= MAX_BYTES,
            'runner baseline manifest is invalid')
    encoded = raw.encode('utf-8')
    identity = digest(encoded)
    require(runner.get('manifestIdentity') == identity
            and runner.get('release', {}).get('manifestDigest') == identity,
            'runner baseline manifest identity differs')
    manifest = decode(encoded)
    require(manifest == runner.get('releaseManifest'),
            'runner baseline manifest bytes differ from parsed evidence')
    source = manifest.get('source')
    workflow = manifest.get('release', {}).get('workflow')
    require(isinstance(source, dict)
            and same_repository(source.get('repository'), value['release']['repository'])
            and isinstance(source.get('commit'), str)
            and SHA.fullmatch(source['commit']),
            'runner baseline manifest source differs')
    require(isinstance(workflow, dict)
            and workflow.get('path') == '.github/workflows/container-release.yml'
            and isinstance(workflow.get('contractCommit'), str)
            and SHA.fullmatch(workflow['contractCommit']),
            'runner baseline manifest signer differs')
    require(manifest.get('releaseVersion') == runner.get('release', {}).get('releaseVersion'),
            'runner baseline release version differs')
    images = manifest.get('images')
    selected = [image for image in images if isinstance(image, dict)
                and image.get('variant') == value['release']['variant']] if isinstance(images, list) else []
    require(len(selected) == 1
            and selected[0].get('indexDigest') == runner.get('deployedDigest'),
            'runner deployed image differs from its baseline manifest')

    manifest_path = Path(home) / f'baseline-{runner["name"]}.json'
    _write_private_file(manifest_path, raw, MAX_BYTES, 'runner baseline manifest')
    environment = {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'HOME': str(home),
        'GH_TOKEN': token,
        'GH_HOST': 'github.com',
    }
    command = [
        'gh', 'attestation', 'verify', str(manifest_path),
        '--repo', value['release']['repository'],
        '--signer-workflow', value['release']['signerWorkflow'],
        '--signer-digest', workflow['contractCommit'],
        '--source-ref', value['release']['sourceRef'],
        '--source-digest', source['commit'],
        '--format', 'json',
    ]
    try:
        completed = run(command, check=False, capture_output=True, text=True,
                        errors='replace', env=environment, timeout=120)
    except (OSError, subprocess.SubprocessError):
        raise TransportError('runner baseline attestation verification failed') from None
    require(completed.returncode == 0, 'runner baseline attestation verification failed')
    return {
        'runnerName': runner['name'],
        'manifestIdentity': identity,
        'repository': value['release']['repository'],
        'sourceRef': value['release']['sourceRef'],
        'signerWorkflow': value['release']['signerWorkflow'],
        'signerCommit': workflow['contractCommit'],
        'verified': True,
    }


# Operator diagnostics reach public Actions logs (.github#2250), so only
# allowlisted forms are relayed. A line outside them is withheld, never
# replaced by an earlier line.
#
# A relayable line is short plain prose: letters, digits, spaces and
# . , : ; ' ( ) / _ + -. No URL, query, assignment, double quote, brace, @,
# $, backtick or redirection fits, so no signed URL, HTTP body, key=value
# credential or quoted command can be relayed.
DIAGNOSTIC_FORM = re.compile(r"[A-Za-z0-9 .,:;'()/_+-]+")
MAX_DIAGNOSTIC_LENGTH = 200
COMMAND_FLAG = re.compile(r'(?:^|\s)--?[A-Za-z]')
# Hyphens separate words (runner names such as verjson-git-runner-01), so they
# end a run; token prefixes such as dop_v1_ and ghs_ use underscores. A run
# mixing letters and digits is token-shaped from 20 characters; any run is from
# 32, which no English word or ordinary identifier reaches.
LONG_RUN = re.compile(r'[A-Za-z0-9_+/]{20,}')
MAX_WORD_RUN = 32
CREDENTIAL_MARKERS = ('-----', 'bearer', 'basic ', 'authorization', 'x-amz-')
# Shorter secret lines (YAML keys, a lone brace) carry no credential.
MIN_SECRET_FRAGMENT_LENGTH = 8
# verjson-cli-cloud prefixes every error it logs with this marker (log.err).
CLI_ERROR_MARKER = '\u2717'
# Prefix the transport adds to a relayed CLI or doctl line.
HOST_EXPORT_FAILURE = 'read-only runner host export failed'

# The transport's deliberate exit line starts with this marker. The controller
# relays only a marked line, never a warning or other interpreter output.
DIAGNOSTIC_MARKER = 'deployment transport: '
# doctl output is classified into fixed messages; its own text never relays.
DOCTL_HTTP_STATUS = re.compile(r'get https?://\S+: ([0-9]{3})(?: [^\r\n]*)?')


def _is_token_shaped(run):
    if len(run) >= MAX_WORD_RUN:
        return True
    return any(c.isdigit() for c in run) and any(c.isalpha() for c in run)


def _json_string_values(secret):
    """String values of a JSON-shaped secret (e.g. a minified doctl config)."""
    try:
        parsed = json.loads(secret)
    except (ValueError, RecursionError):
        return []
    pending, values = [parsed], []
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            values.append(item.strip())
        elif isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return values


def _secret_fragments(sensitive_values):
    """Substrings a relayed line must not contain.

    A single-line secret is checked whole, whatever its length. Every line of a
    secret (a key, a YAML or JSON config) of 8+ characters is checked, plus the
    value of each ``key: value`` or ``key=value`` line, so a token embedded in
    a config is caught on its own. Non-secret config values of 8+ characters,
    such as a context name, are withheld too; that fails closed.
    """
    fragments = set()
    for secret in sensitive_values:
        if not isinstance(secret, str) or not secret.strip():
            continue
        lines = [part.strip() for part in secret.strip().splitlines()]
        if len(lines) == 1:
            fragments.add(lines[0])
        fragments.update(
            value for value in _json_string_values(secret)
            if len(value) >= MIN_SECRET_FRAGMENT_LENGTH
        )
        for part in lines:
            value = re.split(r'[:=]', part, maxsplit=1)[-1].strip().strip('"\',')
            fragments.update(
                candidate for candidate in (part, value)
                if len(candidate) >= MIN_SECRET_FRAGMENT_LENGTH
            )
    return fragments


def is_relayable_diagnostic(line, sensitive_values=(), max_length=MAX_DIAGNOSTIC_LENGTH):
    """Return True when ``line`` fits the operator-diagnostic allowlist."""
    lowered = line.lower()
    fragments = _secret_fragments(sensitive_values)
    return bool(
        len(line) <= max_length
        and DIAGNOSTIC_FORM.fullmatch(line)
        and '//' not in line
        and not COMMAND_FLAG.search(line)
        and not any(_is_token_shaped(run) for run in LONG_RUN.findall(line))
        and not any(marker in lowered for marker in CREDENTIAL_MARKERS)
        and not any(fragment in line for fragment in fragments)
    )


def _doctl_http_diagnostic(status):
    if type(status) is not int or not 400 <= status <= 599:
        return 'doctl probe failed'
    if status == 401:
        return 'doctl authentication rejected (HTTP 401)'
    return f'doctl API request rejected (HTTP {status})'


def _legacy_doctl_diagnostic(detail):
    text = detail.strip().lower()
    if len(text.splitlines()) != 1:
        return 'doctl probe failed'
    status = DOCTL_HTTP_STATUS.fullmatch(text)
    if status:
        return _doctl_http_diagnostic(int(status.group(1)))
    text = text.removeprefix('unable to initialize digitalocean api client: ')
    if text == 'access token is required' or text.startswith('access token is required.'):
        return 'doctl access token is required'
    if text.rstrip('.') == 'unable to authenticate you':
        return 'doctl authentication rejected (HTTP 401)'
    return 'doctl probe failed'


def doctl_diagnostic(stdout, stderr):
    """Classify doctl JSON stdout before considering legacy text diagnostics."""
    if not (stdout or '').strip():
        text = (stderr or '').strip()
        if text.lower().startswith('error: '):
            return _legacy_doctl_diagnostic(text[len('Error: '):])
        return 'doctl probe failed'

    try:
        response = decode(stdout)
    except (TransportError, ValueError, RecursionError):
        return 'doctl probe failed'
    errors = response.get('errors') if isinstance(response, dict) else None
    if not isinstance(errors, list) or len(errors) != 1 or not isinstance(errors[0], dict):
        return 'doctl probe failed'

    failure = errors[0]
    if 'status' not in failure:
        detail = failure.get('detail')
        return _legacy_doctl_diagnostic(detail) if isinstance(detail, str) else 'doctl probe failed'
    return _doctl_http_diagnostic(failure['status'])


def cli_diagnostic(output, sensitive_values):
    """Return the deployment CLI's last marked error line when it fits the allowlist.

    The CLI marks every error it reports; an unmarked crash (a Node stack and
    its version trailer) relays nothing. The bound leaves room for the relay
    prefix, so a line accepted here also fits the controller's bound.
    """
    marked = [
        line.strip()[len(CLI_ERROR_MARKER):].strip()
        for line in (output or '').splitlines()
        if line.strip().startswith(CLI_ERROR_MARKER)
    ]
    if not marked:
        return None
    line = marked[-1]
    bound = MAX_DIAGNOSTIC_LENGTH - len(f'{HOST_EXPORT_FAILURE}: ')
    return line if is_relayable_diagnostic(line, sensitive_values, bound) else None


def transport_failure_line(stderr, sensitive_values):
    """Return this transport's final failure line when the controller may relay it.

    Any traceback, including an exception group, ends in an unvetted exception
    repr that can carry a command line, so its presence withholds everything.
    """
    text = (stderr or '').strip()
    if not text or 'Traceback (most recent call last)' in text:
        return None
    line = text.splitlines()[-1].strip()
    if not line.startswith(DIAGNOSTIC_MARKER):
        return None
    detail = line[len(DIAGNOSTIC_MARKER):].strip()
    return detail if is_relayable_diagnostic(detail, sensitive_values) else None


def exit_message(failure):
    """Marked exit line; only a TransportError message is ever surfaced."""
    detail = str(failure) if isinstance(failure, TransportError) else 'deployment transport failed'
    return DIAGNOSTIC_MARKER + detail


def host_export(value, token, manifest_result=None, *, run=subprocess.run, clock=time.time):
    validate_request(value, clock())
    host = value['hostExport']
    purpose = host['purpose']
    if purpose != 'capacity':
        require(isinstance(manifest_result, dict)
                and manifest_result.get('manifestIdentity') == value['release']['manifestDigest'],
                'verified release manifest is unavailable')
    secrets = {name: os.environ.get(name) for name in HOST_EXPORT_SECRET_ENV}
    require(all(isinstance(secrets[name], str) and secrets[name]
                for name in HOST_EXPORT_SECRET_ENV),
            'read-only host observation credentials are unavailable')
    cli = _deployment_cli()
    attestations = []
    with _runner_temp_directory('deployment-host-export-') as temporary:
        root = Path(temporary)
        home = root / 'home'
        doctl_config = home / '.config' / 'doctl' / 'config.yaml'
        known_hosts = home / '.verjson-digitalocean-known-hosts'
        private_key = root / 'read-only-ssh-private-key'
        manifest_path = root / 'release-manifest.json'
        doctl_config.parent.mkdir(parents=True, mode=0o700)
        _write_private_file(doctl_config, secrets['RUNNER_HOST_EVIDENCE_DOCTL_CONFIG'],
                           1_000_000, 'read-only DigitalOcean context')
        _write_private_file(known_hosts, secrets['RUNNER_HOST_EVIDENCE_KNOWN_HOSTS'],
                           256_000, 'pre-provisioned DigitalOcean known-hosts file')
        _write_private_file(private_key, secrets['RUNNER_HOST_EVIDENCE_SSH_PRIVATE_KEY'],
                           64_000, 'read-only SSH private key')
        os.chmod(home, 0o700)
        os.chmod(home / '.config', 0o700)
        os.chmod(doctl_config.parent, 0o700)
        if purpose != 'capacity':
            _write_private_file(manifest_path, manifest_result['manifestBytes'], MAX_BYTES,
                               'verified release manifest')
        command = [
            cli, 'runner-host-evidence', '--cloud', 'digitalocean',
            '--github-org', value['github']['repository'].split('/')[0],
            '--fleet', value['lane'], '--project', host['project'],
            '--do-context', host['doContext'], '--do-ssh-key', host['doSshKey'],
            '--do-strict-known-hosts', '--read-only-ssh-private-key', str(private_key),
            '--max-age-seconds', str(host['maxAgeSeconds']),
        ]
        if purpose == 'capacity':
            command.append('--capacity-only')
        else:
            command.extend((
                '--manifest-identity', value['release']['manifestDigest'],
                '--manifest-file', str(manifest_path),
                '--release-variant', value['release']['variant'],
            ))
        if purpose == 'post-update':
            command.extend(('--runner', host['runnerName'], '--post-update'))
        child_environment = {
            'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
            'HOME': str(home), 'TMPDIR': temporary,
            'GH_TOKEN': token, 'GH_HOST': 'github.com',
        }
        for name in ('LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR'):
            if name in os.environ:
                child_environment[name] = os.environ[name]
        # Probe doctl with the same isolated HOME/config before the CLI so
        # dry-runs surface auth/context failures (exit 1 "access token is
        # required") instead of execa's opaque shortMessage only
        # (.github#2244 / dry-run 44248683810). Skip when doctl is absent
        # (unit tests clear PATH to /usr/bin:/bin).
        doctl_bin = shutil.which('doctl', path=child_environment['PATH'])
        if doctl_bin:
            try:
                probe = run(
                    [doctl_bin, 'projects', 'list', '--context', host['doContext'],
                     '--output', 'json'],
                    check=False, capture_output=True, text=True, errors='replace',
                    env=child_environment, timeout=60,
                )
            except (OSError, subprocess.SubprocessError):
                probe = None
            if probe is not None and probe.returncode != 0:
                # doctl --output json writes its error to stdout.
                diagnostic = doctl_diagnostic(probe.stdout, probe.stderr)
                raise TransportError(f'{HOST_EXPORT_FAILURE}: {diagnostic}') from None
        try:
            completed = run(command, check=False, capture_output=True, text=True,
                            errors='replace', env=child_environment, timeout=900)
        except (OSError, subprocess.SubprocessError):
            raise TransportError('read-only runner host export failed') from None
        if completed.returncode != 0:
            # CLI stderr is usually operator-facing (dry-run 44236715751); relay
            # its final line only in the allowlisted form and only when it
            # carries none of the host-export secrets or installation token.
            line = cli_diagnostic(
                completed.stderr or completed.stdout, (*secrets.values(), token)
            )
            if line:
                raise TransportError(f'{HOST_EXPORT_FAILURE}: {line}') from None
            raise TransportError('read-only runner host export failed') from None
        try:
            response_bytes = completed.stdout.encode('utf-8')
        except (AttributeError, UnicodeError):
            raise TransportError('host export response is invalid') from None
        sensitive_values = (*secrets.values(), token)
        require(
            all(value.encode('utf-8') not in response_bytes for value in sensitive_values),
            'host export response contains credential material',
        )
        report = decode(response_bytes)
        require(isinstance(report, dict) and type(report.get('schemaVersion')) is int
                and report['schemaVersion'] == 1 and report.get('operation') == 'host-export',
                'host export response schema differs')
        observed = timestamp(report.get('observedAt'))
        current_time = clock()
        require(observed <= current_time and current_time - observed <= host['maxAgeSeconds'],
                'host export response is stale')
        if purpose != 'capacity':
            require(report.get('manifestIdentity') == value['release']['manifestDigest']
                    and report.get('manifestBytes') == manifest_result['manifestBytes']
                    and report.get('manifest') == manifest_result['manifest'],
                    'host export release identity differs')
        if purpose == 'capacity':
            _validate_capacity_report(report, value['lane'], host['runnerNames'])
            runners = []
        elif purpose == 'baseline':
            runners = _validate_runner_roster(
                report.get('fleet'), value['lane'], host['runnerNames'],
            )
        else:
            runners = [report]
            require(report.get('name') == host['runnerName'],
                    'post-update host identity differs')
        for runner in runners:
            attestations.append(_verify_host_baseline(
                runner.get('releaseManifestBytes'), runner, value, token, home, run
            ))

    return {
        'schemaVersion': 1,
        'requestDigest': digest(canonical(value)),
        'outcome': 'passed',
        'releaseManifest': (
            None
            if manifest_result is None
            else {
                'manifestIdentity': manifest_result['manifestIdentity'],
                'manifestBytes': manifest_result['manifestBytes'],
                'manifest': manifest_result['manifest'],
                'attestation': manifest_result['attestation'],
            }
        ),
        'hostEvidence': report,
        'baselineAttestations': attestations,
    }


def complete_workflow_runs(api, path, token, request_budget):
    require(type(request_budget) is int and request_budget > 0,
            'run inventory request budget is exhausted')
    separator = '&' if '?' in path else '?'
    first_page_path = f'{path}{separator}per_page={RUNS_PAGE_SIZE}&page=1'
    first_page = api.call('GET', first_page_path, token)
    require(isinstance(first_page, dict), 'run inventory baseline is unavailable')
    total_count = first_page.get('total_count')
    require(type(total_count) is int and 0 <= total_count <= MAX_RUN_RECORDS,
            'run inventory total is not a bounded complete query')
    page_count = max(1, (total_count + RUNS_PAGE_SIZE - 1) // RUNS_PAGE_SIZE)
    require(page_count <= request_budget,
            'run inventory exceeds the remaining request budget')
    runs = []
    for page_number in range(1, page_count + 1):
        page = first_page if page_number == 1 else api.call(
            'GET', f'{path}{separator}per_page={RUNS_PAGE_SIZE}&page={page_number}', token
        )
        require(isinstance(page, dict)
                and type(page.get('total_count')) is int and page['total_count'] == total_count
                and isinstance(page.get('workflow_runs'), list)
                and len(page['workflow_runs']) <= RUNS_PAGE_SIZE,
                'run inventory page is not a trusted complete query')
        runs.extend(page['workflow_runs'])
    require(len(runs) == total_count and all(isinstance(run, dict) for run in runs),
            'run inventory is incomplete')
    ids = [run.get('id') for run in runs]
    require(all(positive(run_id) for run_id in ids), 'run inventory has invalid identity')
    require(len(set(ids)) == len(ids), 'run inventory has duplicate identity')
    return runs, request_budget - page_count


def probe_runner(api, value, token, *, record_intent, clock=time.time, sleep=time.sleep):
    validate_request(value, clock())
    probe = value['probe']
    repo = f'/repos/{CANARY_REPOSITORY}'
    workflow = api.call('GET', f"{repo}/actions/workflows/{probe['workflowId']}", token)
    require(positive(workflow.get('id')) and workflow['id'] == probe['workflowId'] and workflow.get('path') == CANARY_PATH and workflow.get('state') == 'active', 'dispatch workflow identity differs')
    ref = probe['workflowRef'].removeprefix('refs/')
    tag = api.call('GET', f'{repo}/git/ref/{ref}', token)
    target = tag.get('object', {})
    if target.get('type') == 'tag':
        require(isinstance(target.get('sha'), str) and SHA.fullmatch(target['sha']), 'annotated tag identity is invalid')
        annotated = api.call('GET', f"{repo}/git/tags/{target.get('sha')}", token)
        target = annotated.get('object', {})
    require(target == {'type': 'commit', 'sha': probe['workflowCommit'], 'url': f"https://api.github.com/repos/{CANARY_REPOSITORY}/git/commits/{probe['workflowCommit']}"}, 'canary tag does not resolve to reviewed commit')
    runs_path = f"{repo}/actions/workflows/{probe['workflowId']}/runs?event=workflow_dispatch"
    run_request_budget = MAX_RUN_REQUESTS
    prior_runs, run_request_budget = complete_workflow_runs(api, runs_path, token, run_request_budget)
    title = 'runner-canary-' + probe['transactionNonce']
    require(not any(run.get('display_title') == title for run in prior_runs), 'transaction nonce was already dispatched; reconcile before retry')
    ids = [run['id'] for run in prior_runs]
    maximum = max(ids, default=0)
    inputs = {'runner_name': probe['runnerName'], 'runner_id': str(probe['runnerId']), 'runner_label': probe['runnerLabel'],
              'transaction_nonce': probe['transactionNonce'], 'release_manifest': value['release']['manifestDigest'],
              'release_variant': value['release']['variant'], 'image_digest': value['release']['imageDigest']}
    require(run_request_budget > 0, 'run inventory request budget exhausted before dispatch')
    validate_request(value, clock())
    record_intent({'requestDigest': digest(canonical(value)), 'transactionNonce': probe['transactionNonce'],
                   'preDispatchMaxRunId': maximum, 'workflowCommit': probe['workflowCommit']})
    # Never retry an ambiguous dispatch: the caller must inspect its nonce/run inventory.
    api.call('POST', f"{repo}/actions/workflows/{probe['workflowId']}/dispatches", token,
             {'ref': probe['workflowRef'].removeprefix('refs/tags/'), 'inputs': inputs})
    found = None
    while clock() < timestamp(value['expiresAt']):
        runs, run_request_budget = complete_workflow_runs(api, runs_path, token, run_request_budget)
        matching = [run for run in runs if run.get('display_title') == title]
        require(len(matching) <= 1, 'ambiguous transaction nonce runs')
        if matching:
            found = matching[0]
            require(positive(found.get('id')) and found['id'] > maximum and positive(found.get('workflow_id')) and found['workflow_id'] == probe['workflowId']
                    and found.get('event') == 'workflow_dispatch' and found.get('head_sha') == probe['workflowCommit']
                    and found.get('head_branch') == probe['workflowRef'].removeprefix('refs/tags/')
                    and found.get('path') == CANARY_PATH and type(found.get('run_attempt')) is int and found['run_attempt'] == 1
                    and timestamp(value['issuedAt']) <= timestamp(found.get('created_at')) <= clock(), 'run identity is stale or differs')
            if found.get('status') == 'completed':
                require(found.get('conclusion') == 'success', 'representative run failed')
                break
        sleep(min(5, max(0, timestamp(value['expiresAt']) - clock())))
    require(found is not None and found.get('status') == 'completed', 'representative run timed out')
    jobs = api.call('GET', f"{repo}/actions/runs/{found['id']}/attempts/1/jobs?per_page=100", token)
    require(type(jobs.get('total_count')) is int and jobs['total_count'] == 1 and len(jobs.get('jobs', [])) == 1 and jobs['jobs'][0].get('name') == 'canary', 'canary job identity is ambiguous')
    job = jobs['jobs'][0]
    require(positive(job.get('id')), 'canary job ID is invalid')
    artifacts = api.call('GET', f"{repo}/actions/runs/{found['id']}/artifacts?per_page=100", token)
    require(type(artifacts.get('total_count')) is int and artifacts['total_count'] == 1 and len(artifacts.get('artifacts', [])) == 1, 'canary artifact identity is ambiguous')
    artifact = artifacts['artifacts'][0]
    require(positive(artifact.get('id')) and artifact.get('name') == 'runner-promotion-receipt', 'canary artifact differs')
    archive = api.call('GET', f"{repo}/actions/artifacts/{artifact['id']}/zip", token, binary=True)
    require(digest(archive) == artifact.get('digest'), 'artifact archive digest differs')
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            require(len(members) == 1 and members[0].filename == 'runner-promotion-receipt.json'
                    and 0 < members[0].file_size <= MAX_BYTES and not members[0].is_dir(), 'artifact archive layout is invalid')
            receipt = decode(bundle.read(members[0]))
    except (zipfile.BadZipFile, RuntimeError):
        raise TransportError('canary artifact archive is invalid') from None
    validate_probe_receipt(value, receipt, found, job, artifact, clock())
    return {'schemaVersion': 1, 'requestDigest': digest(canonical(value)), 'routedRunner': probe['runnerName'],
            'outcome': 'passed', 'preDispatchMaxRunId': maximum, 'runId': found['id'], 'runAttempt': 1, 'artifactId': artifact['id'],
            'artifactDigest': artifact['digest'], 'receipt': receipt}


def _receipt_helpers():
    import importlib.util
    path = Path(__file__).with_name('container_deployment_preflight.py')
    require(path.is_file(), 'deployment receipt preflight is unavailable')
    spec = importlib.util.spec_from_file_location('container_deployment_preflight', path)
    require(spec is not None and spec.loader is not None, 'deployment receipt preflight is unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.receipt_digest, module.validate_receipt_chain, module.PreflightError


def _artifact_kind_rank(kind):
    base = kind.split('-', 1)[0]
    return ARTIFACT_KIND_RANK.get(base, 99)


def _parse_deployment_artifact(artifact):
    require(isinstance(artifact, dict), 'artifact inventory entry is invalid')
    name = artifact.get('name')
    matched = DEPLOYMENT_ARTIFACT_NAME.fullmatch(name) if isinstance(name, str) else None
    if matched is None:
        return None
    if artifact.get('expired') is not False:
        return None
    require(positive(artifact.get('id')), 'artifact identity is invalid')
    require(isinstance(artifact.get('digest'), str) and DIGEST.fullmatch(artifact['digest']),
            'artifact archive digest is invalid')
    return {
        'id': artifact['id'],
        'name': name,
        'digest': artifact['digest'],
        'runId': int(matched.group('runId')),
        'runAttempt': int(matched.group('runAttempt')),
        'kind': matched.group('kind'),
        'attemptId': f"{matched.group('runId')}.{matched.group('runAttempt')}",
    }


def complete_artifacts(api, path, token, request_budget):
    require(type(request_budget) is int and request_budget > 0,
            'artifact inventory request budget is exhausted')
    separator = '&' if '?' in path else '?'
    first_page_path = f'{path}{separator}per_page={RUNS_PAGE_SIZE}&page=1'
    first_page = api.call('GET', first_page_path, token)
    require(isinstance(first_page, dict), 'artifact inventory baseline is unavailable')
    total_count = first_page.get('total_count')
    require(type(total_count) is int and 0 <= total_count <= MAX_RUN_RECORDS,
            'artifact inventory total is not a bounded complete query')
    page_count = max(1, (total_count + RUNS_PAGE_SIZE - 1) // RUNS_PAGE_SIZE)
    require(page_count <= request_budget,
            'artifact inventory exceeds the remaining request budget')
    artifacts = []
    for page_number in range(1, page_count + 1):
        page = first_page if page_number == 1 else api.call(
            'GET', f'{path}{separator}per_page={RUNS_PAGE_SIZE}&page={page_number}', token
        )
        require(isinstance(page, dict)
                and type(page.get('total_count')) is int and page['total_count'] == total_count
                and isinstance(page.get('artifacts'), list)
                and len(page['artifacts']) <= RUNS_PAGE_SIZE,
                'artifact inventory page is not a trusted complete query')
        artifacts.extend(page['artifacts'])
    require(len(artifacts) == total_count and all(isinstance(item, dict) for item in artifacts),
            'artifact inventory is incomplete')
    ids = [item.get('id') for item in artifacts]
    require(all(positive(item_id) for item_id in ids), 'artifact inventory has invalid identity')
    require(len(set(ids)) == len(ids), 'artifact inventory has duplicate identity')
    return artifacts, request_budget - page_count


def _prefer_deployment_artifact(candidates):
    require(candidates, 'deployment receipt artifact was not found')
    ranked = sorted(
        candidates,
        key=lambda item: (_artifact_kind_rank(item['kind']), item['id']),
    )
    best_rank = _artifact_kind_rank(ranked[0]['kind'])
    preferred = [item for item in ranked if _artifact_kind_rank(item['kind']) == best_rank]
    require(len(preferred) == 1, 'deployment receipt artifact identity is ambiguous')
    return preferred[0]


def _decode_receipt_archive(archive):
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            require(members, 'deployment receipt archive is empty')
            receipts = {}
            for member in members:
                require(not member.is_dir(), 'deployment receipt archive layout is invalid')
                matched = RECEIPT_MEMBER.fullmatch(member.filename)
                require(matched is not None and 0 < member.file_size <= MAX_BYTES,
                        'deployment receipt archive layout is invalid')
                revision = int(matched.group('revision'))
                require(revision not in receipts, 'deployment receipt archive repeats a revision')
                receipts[revision] = decode(bundle.read(member))
    except (zipfile.BadZipFile, RuntimeError):
        raise TransportError('deployment receipt archive is invalid') from None
    require(receipts and min(receipts) == 0 and set(receipts) == set(range(len(receipts))),
            'deployment receipt revisions are not contiguous')
    return [receipts[index] for index in range(len(receipts))]


def _select_receipt(chain, receipt_digest_fn, requested, purpose):
    matches = [
        receipt for receipt in chain
        if receipt_digest_fn(receipt) == requested
    ]
    if not matches:
        raise ReceiptAbsentFromArtifact('requested deployment receipt was not found')
    require(len(matches) == 1, 'requested deployment receipt digest is ambiguous')
    selected = matches[0]
    if purpose == 'rollback':
        require(selected.get('outcome') in ('failed', 'interrupted'),
                'rollback source attempt must be failed or interrupted')
    return selected


def _inspect_artifact(api, token, artifact, receipt_digest_fn, validate_chain, PreflightError,
                      requested, purpose):
    archive = api.call(
        'GET',
        f"/repos/{artifact['_repository']}/actions/artifacts/{artifact['id']}/zip",
        token,
        binary=True,
    )
    require(digest(archive) == artifact['digest'], 'artifact archive digest differs')
    chain = _decode_receipt_archive(archive)
    try:
        validate_chain(chain)
    except PreflightError as error:
        raise TransportError(f'deployment receipt chain is invalid: {error}') from None
    selected = _select_receipt(chain, receipt_digest_fn, requested, purpose)
    return {
        'artifactId': artifact['id'],
        'artifactName': artifact['name'],
        'artifactDigest': artifact['digest'],
        'sourceAttemptId': artifact['attemptId'],
        'rollbackSource': selected,
        'retainedRevisions': chain,
    }


def fetch_deployment_receipt(api, value, token, *, clock=time.time):
    validate_request(value, clock())
    require(isinstance(token, str) and token and '\n' not in token,
            'job-scoped GitHub token is required')
    receipt_digest_fn, validate_chain, PreflightError = _receipt_helpers()
    repository = value['github']['repository']
    repository_id = value['github']['repositoryId']
    repo = api.call('GET', f'/repositories/{repository_id}', token)
    require(positive(repo.get('id')) and repo['id'] == repository_id
            and same_repository(repo.get('full_name'), repository),
            'receipt API repository identity differs')
    requested = value['receiptDigest']
    purpose = value['purpose']
    authority = value.get('retainedReceiptAuthority')
    source_attempt = value.get('sourceAttemptId')
    request_budget = MAX_RUN_REQUESTS

    if isinstance(authority, str):
        matched = RETAINED_RECEIPT_AUTHORITY.fullmatch(authority)
        require(matched is not None, 'retained receipt authority is invalid')
        artifact_id = int(matched.group('artifactId'))
        attempt_id = matched.group('attemptId')
        metadata = api.call('GET', f'/repos/{repository}/actions/artifacts/{artifact_id}', token)
        require(isinstance(metadata, dict) and metadata.get('expired') is False,
                'deployment receipt artifact is expired')
        parsed = _parse_deployment_artifact(metadata)
        require(parsed is not None and parsed['attemptId'] == attempt_id,
                'retained receipt artifact identity differs')
        parsed['_repository'] = repository
        result = _inspect_artifact(
            api, token, parsed, receipt_digest_fn, validate_chain, PreflightError,
            requested, purpose,
        )
        validate_request(value, clock())
        return {
            'schemaVersion': 1,
            'requestDigest': digest(canonical(value)),
            'outcome': 'passed',
            **result,
        }

    if isinstance(source_attempt, str):
        run_id = source_attempt.split('.', 1)[0]
        artifacts, request_budget = complete_artifacts(
            api, f'/repos/{repository}/actions/runs/{run_id}/artifacts', token, request_budget
        )
        candidates = []
        for item in artifacts:
            parsed = _parse_deployment_artifact(item)
            if parsed is None or parsed['attemptId'] != source_attempt:
                continue
            parsed['_repository'] = repository
            candidates.append(parsed)
        selected_artifact = _prefer_deployment_artifact(candidates)
        result = _inspect_artifact(
            api, token, selected_artifact, receipt_digest_fn, validate_chain, PreflightError,
            requested, purpose,
        )
        validate_request(value, clock())
        return {
            'schemaVersion': 1,
            'requestDigest': digest(canonical(value)),
            'outcome': 'passed',
            **result,
        }

    artifacts, request_budget = complete_artifacts(
        api, f'/repos/{repository}/actions/artifacts', token, request_budget
    )
    downloads = 0
    matches = []
    for item in artifacts:
        parsed = _parse_deployment_artifact(item)
        if parsed is None:
            continue
        require(downloads < MAX_ARTIFACT_DOWNLOADS,
                'deployment receipt search exceeded the download budget')
        downloads += 1
        parsed['_repository'] = repository
        try:
            result = _inspect_artifact(
                api, token, parsed, receipt_digest_fn, validate_chain, PreflightError,
                requested, purpose,
            )
        except ReceiptAbsentFromArtifact:
            # Digest miss in this artifact — try the next candidate.
            continue
        matches.append(result)
        require(len(matches) == 1, 'requested deployment receipt digest is ambiguous')
    require(matches, 'requested deployment receipt was not found')
    validate_request(value, clock())
    return {
        'schemaVersion': 1,
        'requestDigest': digest(canonical(value)),
        'outcome': 'passed',
        **matches[0],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    value = validate_request(decode(args.request.read_bytes()), time.time())
    require(not args.dry_run or value['operation'] == 'manifest', 'dry-run has no dispatch authority')
    intent_path = args.output.with_name(args.output.name + '.dispatch-intent.json')
    require(not args.output.exists() and not intent_path.exists(), 'output or dispatch intent already exists; reconcile before retry')
    def record_intent(value):
        persist_record(intent_path, value)
    if value['operation'] == 'deployment-receipt':
        token = os.environ.get('GITHUB_TOKEN')
        require(isinstance(token, str) and token and '\n' not in token,
                'job-scoped GitHub token is required')
        api = GitHubHTTP()
        result = fetch_deployment_receipt(api, value, token)
    else:
        role = value['operation'].replace('-', '_').upper()
        if value['operation'] == 'host-export':
            jwt = mint_host_export_app_jwt(
                value['github']['appId'],
                os.environ.get(HOST_EXPORT_APP_KEY_ENV),
                time.time(),
            )
        else:
            jwt = os.environ.get(f'VERJSON_DEPLOYMENT_{role}_APP_JWT')
        api = GitHubHTTP()
        token = installation_token(api, value, jwt, time.time())
        if value['operation'] == 'manifest':
            result = fetch_manifest(api, value, token)
        elif value['operation'] == 'probe':
            result = probe_runner(api, value, token, record_intent=record_intent)
        else:
            manifest_result = None
            if value['hostExport']['purpose'] != 'capacity':
                manifest_result = fetch_manifest(api, value, token)
            result = host_export(value, token, manifest_result)
    persist_record(args.output, result)


if __name__ == '__main__':
    try:
        main()
    except (TransportError, OSError, KeyError, TypeError) as failure:
        # Never relay HTTP bodies, JWTs, headers, signed asset URLs, or subprocess stderr.
        raise SystemExit(exit_message(failure)) from None
