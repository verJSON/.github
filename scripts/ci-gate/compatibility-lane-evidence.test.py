#!/usr/bin/env python3
import unittest

import compatibility_lane_evidence as module


class CompatibilityLaneEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            'schemaVersion': 1, 'policyDigest': 'a' * 64, 'candidateCommit': 'b' * 40,
            'baseCommit': 'c' * 40, 'runId': '123', 'runAttempt': 1,
            'provider': 'github', 'repositoryId': '42', 'candidateKind': 'merge_group',
            'providerOrigin': 'https://github.com', 'ownerId': '41',
            'candidateTree': '1' * 40, 'workflowRepositoryId': '50',
            'workflowPath': '.github/workflows/ci.yml', 'workflowRevision': '7' * 40,
        }
        self.lane = {
            'laneId': 'stable', 'harnessDigest': 'd' * 64, 'fixtureDigest': 'e' * 64,
            'packageName': '@verjson/identity-contracts', 'packageVersion': '1.0.0',
            'artifactDigest': 'f' * 64,
            'harnessSource': 'owner/harness', 'harnessRevision': '2' * 40,
            'fixtureSource': 'owner/fixture', 'fixtureRevision': '3' * 40,
            'dependencyDigest': '4' * 64, 'sourceDigest': '5' * 64,
            'networkProfile': 'private-loopback', 'completionToken': '6' * 64,
        }
    def test_accepts_complete_results_bound_to_the_authorized_plan(self):
        policy = {**self.context, 'lanes': [self.lane]}
        result = {**self.context, **self.lane, 'status': 'passed'}

        self.assertEqual(module.complete_results(policy, [result]), {'status': 'matching', 'readyForApply': False})

    def test_lanes_are_an_unordered_unique_set_and_results_must_be_complete(self):
        second = {**self.lane, 'laneId': 'candidate'}
        policy = {**self.context, 'lanes': [self.lane, second]}
        first_result = {**self.context, **self.lane, 'status': 'passed'}
        second_result = {**self.context, **second, 'status': 'passed'}

        self.assertEqual(module.complete_results(policy, [second_result, first_result])['status'], 'matching')
        for lanes, results in [
            ([self.lane, self.lane], [first_result, first_result]),
            ([self.lane, second], [first_result]),
            ([self.lane, second], [first_result, first_result]),
            ([self.lane], [first_result, second_result]),
        ]:
            with self.subTest(lanes=lanes, results=results):
                self.assertEqual(module.complete_results({**self.context, 'lanes': lanes}, results)['status'], 'incomplete')

    def test_malformed_plans_and_extra_fields_cannot_match(self):
        for context, lane in [
            ({**self.context, 'schemaVersion': True}, self.lane),
            ({**self.context, 'policyDigest': 'not-a-digest'}, self.lane),
            ({**self.context, 'runAttempt': True}, self.lane),
            (self.context, {**self.lane, 'candidateCommit': '0' * 40}),
            (self.context, {**self.lane, 'unexpected': 'value'}),
            (self.context, {**self.lane, 'packageVersion': '01.0.0'}),
        ]:
            with self.subTest(context=context, lane=lane):
                result = {**context, **lane, 'status': 'passed'}
                self.assertEqual(module.complete_results({**context, 'lanes': [lane]}, [result])['status'], 'incomplete')
        for policy, results in [(None, []), ({**self.context, 'lanes': []}, []), ({**self.context, 'lanes': [self.lane]}, [None])]:
            with self.subTest(policy=policy, results=results):
                self.assertEqual(module.complete_results(policy, results)['status'], 'incomplete')

    def test_results_cannot_change_bindings_or_coerce_types(self):
        policy = {**self.context, 'lanes': [self.lane]}
        result = {**self.context, **self.lane, 'status': 'passed'}
        for field, replacement in [
            ('schemaVersion', True), ('runAttempt', True), ('baseCommit', '0' * 40),
            ('policyDigest', '0' * 64), ('candidateCommit', '0' * 40),
            ('harnessDigest', '0' * 64), ('fixtureDigest', '0' * 64),
            ('artifactDigest', '0' * 64), ('status', 'skipped'),
            ('candidateTree', '0' * 40), ('workflowRepositoryId', '51'),
            ('workflowPath', '.github/workflows/other.yml'), ('workflowRevision', '0' * 40),
            ('repositoryId', '43'), ('candidateKind', 'pull_request'), ('runId', '124'),
            ('providerOrigin', 'https://other.example'), ('ownerId', '44'),
            ('harnessSource', 'other/harness'), ('harnessRevision', '0' * 40),
            ('fixtureSource', 'other/fixture'), ('fixtureRevision', '0' * 40),
            ('dependencyDigest', '0' * 64), ('sourceDigest', '0' * 64),
            ('packageName', '@verjson/other'), ('packageVersion', '2.0.0'),
            ('networkProfile', 'isolated'), ('completionToken', '0' * 64),
        ]:
            with self.subTest(field=field):
                self.assertEqual(module.complete_results(policy, [{**result, field: replacement}])['status'], 'incomplete')

    def test_untrusted_values_are_rejected_without_running_comparison_hooks(self):
        class Malicious:
            def __eq__(self, other):
                raise AssertionError('comparison hook invoked')

        policy = {**self.context, 'provider': Malicious(), 'lanes': [self.lane]}
        result = {**self.context, **self.lane, 'status': 'passed'}
        self.assertEqual(module.complete_results(policy, [result])['status'], 'incomplete')

    def test_nonstring_keys_are_rejected_without_rehashing_them(self):
        class Key:
            armed = False

            def __hash__(self):
                if self.armed:
                    raise AssertionError('hash hook invoked')
                return hash('schemaVersion')

            def __eq__(self, other):
                if self.armed:
                    raise AssertionError('comparison hook invoked')
                return False

        key = Key()
        policy = {**self.context, 'lanes': [self.lane], key: 'unexpected'}
        bad_lane = {**self.lane, key: 'unexpected'}
        bad_result = {**self.context, **self.lane, 'status': 'passed', key: 'unexpected'}
        key.armed = True
        self.assertEqual(module.complete_results(policy, [])['status'], 'incomplete')
        self.assertEqual(module.complete_results({**self.context, 'lanes': [bad_lane]}, [{}])['status'], 'incomplete')
        self.assertEqual(module.complete_results({**self.context, 'lanes': [self.lane]}, [bad_result])['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
