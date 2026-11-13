#!/usr/bin/env python3
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("compat", ROOT / "scripts/renovate-compatibility.py")
assert SPEC and SPEC.loader
compat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compat)


def observation(**changes):
    value = {
        "repository": "Verjson/one", "pullRequest": 7, "ecosystem": "npm",
        "package": "typescript", "targetMajor": 7, "stackProfile": "node-jest-ts-jest",
        "baseGreen": True, "majorUpdate": True, "relevantCheckFailed": True,
        "retryCompleted": True, "firstFailure": "/tmp/a.ts: TS1234 7.0.1 deadbeef",
        "retryFailure": "/work/b.ts: TS1234 7.0.2 feedface",
    }
    value.update(changes)
    return value


class CompatibilityPolicyTest(unittest.TestCase):
    def test_red_base_is_rejected(self):
        self.assertEqual("reject", compat.reconcile([observation(baseGreen=False)], [])["results"][0]["disposition"])

    def test_flaky_failure_is_rejected(self):
        self.assertEqual("reject", compat.reconcile([observation(retryFailure="another failure")], [])["results"][0]["disposition"])

    def test_malformed_observations_are_rejected_before_fingerprinting(self):
        malformed = [
            observation(pullRequest=True), observation(pullRequest=0),
            observation(targetMajor=True), observation(targetMajor=0),
            observation(repository="Other/one"), observation(repository="Verjson/../one"),
            observation(package="typescript;echo pwned"), observation(package=""),
            observation(stackProfile="Node Jest"), observation(firstFailure="   "),
            observation(baseGreen=1), {**observation(), "unexpected": "field"},
        ]
        results = compat.reconcile(malformed, [])["results"]
        self.assertEqual(["invalid-observation"] * len(malformed), [item["reason"] for item in results])
        for item in malformed:
            with self.assertRaisesRegex(ValueError, "^invalid compatibility observation$"):
                compat.fingerprint(item)

    def test_supported_ecosystems_have_bounded_package_shapes(self):
        self.assertTrue(compat.valid_observation(observation()))
        self.assertTrue(compat.valid_observation(observation(
            ecosystem="github-actions", package="actions/checkout", stackProfile="github-actions-callers"
        )))
        self.assertTrue(compat.valid_observation(observation(
            ecosystem="docker", package="grafana/tempo", stackProfile="compose-observability"
        )))
        self.assertFalse(compat.valid_observation(observation(ecosystem="unknown")))

    def test_repeatable_failure_is_reported_once(self):
        result = compat.reconcile([observation(), observation(repository="Verjson/two")], [])["results"]
        self.assertEqual(["report", "known"], [item["disposition"] for item in result])
        self.assertEqual([True, False], [item["aiClassificationRequired"] for item in result])

    def test_existing_stack_scoped_hold_is_reused(self):
        item = observation()
        key = compat.fingerprint(item).split("/")[-1]
        hold = {"ecosystem": "npm", "package": "typescript", "targetMajor": 7,
                "stackProfile": "node-jest-ts-jest", "failureFingerprint": key}
        self.assertEqual("known", compat.reconcile([item], [hold])["results"][0]["disposition"])
        self.assertEqual("report", compat.reconcile([observation(stackProfile="node-vitest")], [hold])["results"][0]["disposition"])

    def test_discovery_ignores_rollout_hold(self):
        hold = {"id": "ts7", "package": "typescript", "status": "held", "testedThroughVersion": "7.0.2",
                "stackProfile": "node-jest-ts-jest", "representativeRepositories": ["Verjson/one"]}
        result = compat.discover([hold], {"typescript": ["7.0.2", "7.1.0"]})
        self.assertEqual("7.1.0", result["candidates"][0]["candidate"])
        self.assertEqual(["Verjson/one"], result["candidates"][0]["repositories"])


if __name__ == "__main__":
    unittest.main()
