"""Compare trusted lane evidence; this module does not authenticate its inputs."""

import re

CONTEXT = {'schemaVersion', 'policyDigest', 'candidateCommit', 'candidateTree', 'baseCommit',
           'runId', 'runAttempt', 'provider', 'providerOrigin', 'ownerId', 'repositoryId', 'candidateKind',
           'workflowRepositoryId', 'workflowPath', 'workflowRevision'}
LANE = {'laneId', 'harnessDigest', 'fixtureDigest', 'packageName', 'packageVersion', 'artifactDigest',
        'harnessSource', 'harnessRevision', 'fixtureSource', 'fixtureRevision', 'dependencyDigest',
        'sourceDigest', 'networkProfile', 'completionToken'}


def text_matches(value, pattern):
    return type(value) is str and re.fullmatch(pattern, value) is not None


def exact_fields(value, fields):
    return type(value) is dict and all(type(key) is str for key in value) and set(value) == fields


def valid_version(value):
    if type(value) is not str or len(value) > 128:
        return False
    numeric = r'(?:0|[1-9][0-9]*)'
    prerelease = r'(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)'
    return re.fullmatch(
        rf'{numeric}\.{numeric}\.{numeric}(?:-{prerelease}(?:\.{prerelease})*)?'
        r'(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?', value
    ) is not None


def valid_context(value):
    return (type(value['schemaVersion']) is int and value['schemaVersion'] == 1 and
            text_matches(value['policyDigest'], r'[a-f0-9]{64}') and
            all(text_matches(value[key], r'[a-f0-9]{40}') for key in ('candidateCommit', 'candidateTree', 'baseCommit')) and
            text_matches(value['runId'], r'[1-9][0-9]{0,19}') and
            type(value['runAttempt']) is int and 0 < value['runAttempt'] <= 10000 and
            type(value['provider']) is str and value['provider'] == 'github' and
            type(value['providerOrigin']) is str and value['providerOrigin'] == 'https://github.com' and
            text_matches(value['ownerId'], r'[1-9][0-9]{0,19}') and
            text_matches(value['repositoryId'], r'[1-9][0-9]{0,19}') and
            type(value['candidateKind']) is str and value['candidateKind'] in ('pull_request', 'merge_group') and
            text_matches(value['workflowRepositoryId'], r'[1-9][0-9]{0,19}') and
            text_matches(value['workflowPath'], r'\.github/workflows/[A-Za-z0-9._-]{1,128}\.ya?ml') and
            text_matches(value['workflowRevision'], r'[a-f0-9]{40}'))


def valid_lane(value):
    return (exact_fields(value, LANE) and
            text_matches(value['laneId'], r'[a-z0-9][a-z0-9._-]{0,63}') and
            all(text_matches(value[key], r'[a-f0-9]{64}') for key in
                ('harnessDigest', 'fixtureDigest', 'artifactDigest', 'dependencyDigest', 'sourceDigest', 'completionToken')) and
            all(text_matches(value[key], r'[a-f0-9]{40}') for key in ('harnessRevision', 'fixtureRevision')) and
            all(text_matches(value[key], r'[A-Za-z0-9._/@:-]{1,256}') for key in ('harnessSource', 'fixtureSource')) and
            type(value['networkProfile']) is str and value['networkProfile'] in ('isolated', 'private-loopback') and
            text_matches(value['packageName'], r'@[a-z0-9][a-z0-9._-]{0,63}/[a-z0-9][a-z0-9._-]{0,127}') and
            valid_version(value['packageVersion']))


def complete_results(policy, results):
    incomplete = {'status': 'incomplete', 'readyForApply': False}
    if (not exact_fields(policy, CONTEXT | {'lanes'}) or
            type(results) is not list or type(policy['lanes']) is not list or
            not 1 <= len(policy['lanes']) <= 128 or len(results) != len(policy['lanes']) or
            not valid_context(policy) or not all(valid_lane(lane) for lane in policy['lanes']) or
            not all(exact_fields(result, CONTEXT | LANE | {'status'}) and
                    valid_context(result) and valid_lane({key: result[key] for key in LANE}) and
                    type(result['status']) is str for result in results)):
        return incomplete
    context = {key: value for key, value in policy.items() if key != 'lanes'}
    expected = {lane['laneId']: {**context, **lane, 'status': 'passed'} for lane in policy['lanes']}
    observed = {result['laneId']: result for result in results}
    matching = (bool(expected) and len(expected) == len(policy['lanes']) and
                len(observed) == len(results) and expected == observed)
    return {'status': 'matching' if matching else 'incomplete', 'readyForApply': False}
