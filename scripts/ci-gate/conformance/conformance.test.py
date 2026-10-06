#!/usr/bin/env python3
"""Conformance matrix: the contract, against synthetic adopters, before release.

`Verjson/verjson-ci#184` was a contract defect, not adopter drift. `node-ci.yml`
was published in a state where a deferred head reported `ci / build-test`
SUCCESS having executed nothing, and no adopter was positioned to catch it — a
reusable workflow is only exercised by real callers, after publication.

The property every case here asserts is **positive evidence of execution**, not
absence of red. A suite that only asserts "the run was green" reproduces the
exact defect it exists to prevent: #184 was green.

Step 3 of the contract-distribution sequence (Verjson/.github#1369, ADR 0185).
"""

import base64
import contextlib
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adopter import ADOPTERS, AdopterContractMismatch, bind_inputs, callers_for  # noqa: E402
from expressions import Evaluator, UnknownContext, UnsupportedExpression  # noqa: E402
from contract_steps import (ContractStep, MissingContractStep,  # noqa: E402
                            StepEnvironmentMismatch, StepExecutionFault,
                            execute_step, locate_step)
from model import Scenario, model_workflow  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / '.github/workflows/node-ci.yml'
PROTECTED_CONTRACT = ROOT / '.github/workflows/node-ci-protected.yml'
PRE_ADR_0178 = (Path(__file__).resolve().parent
                / 'regressions/node-ci-pre-adr-0178.yml')
MISSING = object()


def document_differences(before, after, path=()):
    if isinstance(before, dict) and isinstance(after, dict):
        differences = []
        keys = list(before)
        keys.extend(key for key in after if key not in before)
        for key in keys:
            if key not in before:
                differences.append((path + (key,), MISSING, after[key]))
            elif key not in after:
                differences.append((path + (key,), before[key], MISSING))
            else:
                differences.extend(
                    document_differences(before[key], after[key], path + (key,))
                )
        return differences
    if before != after:
        return [(path, before, after)]
    return []

# A merge predicate may treat these as satisfying a required context; anything
# else is a positive signal that something did not go to plan. Keeping the set
# here, next to the property it defines, is deliberate: widening it is what
# reopens Verjson/verjson-ci#184, and it should take an edit someone reviews.
CONCLUSIONS_A_MERGE_GATE_ACCEPTS = frozenset({'success', 'skipped', 'neutral'})

# The steps whose presence in a run is the evidence that the lane did real
# work — the credentialed lane's package scripts, and the secretless lane's
# script plan. A lane reporting success without one of these has reported #184.
#
# These are exact step names rather than substrings, and
# `test_every_registered_work_step_still_exists` asserts each one is still in
# the contract. An evidence list that silently stops matching is worse than no
# evidence list: every lane then looks like it did nothing, or — depending on
# which way the assertion runs — like it did something.
WORK_STEPS = frozenset({
    'Run exact credentialless consumer script plan',
    'npm ci',
    'npm run build',
    'npm test',
})

# What each (adopter, scenario) pair must execute, exactly. Asserting only that
# *some* evidence exists accepts a contract that guards off `npm test` while
# leaving `npm ci` running — a partial #184, and the shape a defect is most
# likely to take once the obvious one is tested for.
#
# The key is the pair, not the adopter: `typescript-service.yml` happens to opt
# into the secretless lane on both events, but the realistic adopter shape is
# secretless on pull_request and credentialed on push, and an adopter-keyed
# table cannot express it.
CREDENTIALED = frozenset({'npm ci', 'npm run build', 'npm test'})
SECRETLESS = frozenset({'Run exact credentialless consumer script plan'})
EXPECTED_EVIDENCE = {
    ('minimal-defaults.yml', 'secretless-pr'): CREDENTIALED,
    ('minimal-defaults.yml', 'trusted-ref-push'): CREDENTIALED,
    ('typescript-service.yml', 'secretless-pr'): SECRETLESS,
    ('typescript-service.yml', 'trusted-ref-push'): SECRETLESS,
}

# Context values a caller cannot supply: they come from the event and from jobs
# the contract runs itself. A scenario is exactly a choice of these.
def scenario_bindings(*, should_run, event_name='pull_request',
                      auxiliary_path='', cache_hits=False, hosted=True):
    return {
        'github.event_name': event_name,
        'github.event.pull_request.head.repo.full_name': 'fork/verjson-ci',
        'github.event.pull_request.head.sha': '0' * 40,
        'needs.eligibility.outputs.should-run': should_run,
        'needs.acquire-secretless-dependencies.outputs.auxiliary-content-path': auxiliary_path,
        'steps.restore-secretless-public-cache.outputs.cache-hit': 'true' if cache_hits else '',
        'steps.restore-playwright-browser-cache.outputs.cache-hit': 'true' if cache_hits else '',
        'steps.prepare-playwright-browser-cache.outputs.owned': 'true',
        'steps.bound-playwright-browser-cache.outputs.should-save': 'true',
        'runner.environment': 'github-hosted' if hosted else 'self-hosted',
    }


SCENARIOS = (
    Scenario(
        name='deferred',
        description='eligibility defers the run (a held Renovate PR)',
        bindings=scenario_bindings(should_run='false'),
        functions={'hashFiles': lambda *_: ''},
    ),
    Scenario(
        name='secretless-pr',
        description='pull-request lane; secretless for an adopter that opts in',
        bindings=scenario_bindings(should_run='true', event_name='pull_request'),
        functions={'hashFiles': lambda *_: 'deadbeef'},
    ),
    Scenario(
        name='trusted-ref-push',
        description='push to a trusted ref',
        bindings=scenario_bindings(should_run='true', event_name='push'),
        functions={'hashFiles': lambda *_: 'deadbeef'},
    ),
)


def jobs_a_merge_gate_would_reject(contract, caller, binds_as=None):
    """The deferred run's jobs that an all-SUCCESS assertion would reject.

    This is the whole of ADR 0178 stated as a predicate: not "is the run
    green", but "does the rollup carry an entry that an all-SUCCESS assertion
    would reject". Returning the job names rather than a boolean is deliberate
    — a spurious failure anywhere else in the contract would otherwise satisfy
    the property for the wrong reason, and look identical to conformance.
    """
    outcomes = outcomes_for(contract, caller, SCENARIOS[0], binds_as)
    return {name for name, outcome in outcomes.items()
            if outcome.conclusion not in CONCLUSIONS_A_MERGE_GATE_ACCEPTS}


def outcomes_for(contract, caller, scenario, binds_as=None):
    bindings = dict(scenario.bindings)
    # A caller's `with:` values are expressions evaluated in the *caller's*
    # context, so `secretless-pr: ${{ github.event_name == \'pull_request\' }}`
    # is what makes one fixture serve both the PR and the push lane. Binding the
    # literal string instead would make every such input permanently truthy and
    # quietly collapse the scenarios into one.
    caller_evaluator = Evaluator(bindings, functions={'always': lambda: True})
    for name, value in bind_inputs(contract, caller, binds_as).items():
        if isinstance(value, str) and value.strip().startswith('${{'):
            bindings[name] = caller_evaluator.evaluate(value)
        else:
            bindings[name] = value
    resolved = Scenario(scenario.name, scenario.description, bindings, scenario.functions)
    return model_workflow(contract, resolved)


class DeferredLaneIsDistinguishableFromSuccess(unittest.TestCase):
    """The #184 regression test. It must fail against a pre-ADR-0178 contract."""

    def test_a_deferred_run_publishes_a_check_that_is_not_success(self):
        for contract in (CONTRACT, PROTECTED_CONTRACT):
            for caller in callers_for(contract):
                with self.subTest(contract=contract.name, adopter=caller.name):
                    self.assertEqual(
                        {'deferred-ci'},
                        jobs_a_merge_gate_would_reject(contract, caller),
                        'a deferred head must publish exactly one non-SUCCESS '
                        'check, ADR 0178\'s `deferred-ci`. Nothing there means a '
                        'merge gate cannot tell a deferred head from a verified '
                        'one (Verjson/verjson-ci#184); anything else means the '
                        'signal a gate acts on is a spurious failure')

    def test_the_property_fails_against_the_contract_that_shipped_the_defect(self):
        """A regression test that passes against the defect proves nothing."""
        for caller in callers_for(CONTRACT):
            with self.subTest(adopter=caller.name):
                self.assertEqual(
                    set(),
                    jobs_a_merge_gate_would_reject(PRE_ADR_0178, caller,
                                                   binds_as=CONTRACT.name),
                    'the pre-ADR-0178 contract now looks conformant, so this '
                    'matrix would not have caught Verjson/verjson-ci#184 — '
                    'either the fixture drifted or the property weakened')

    def test_the_counter_example_differs_from_the_contract_only_by_the_deferred_job(self):
        """Otherwise the counter-example drifts and the test above passes for free.

        The fixture is a YAML round-trip of the contract, so it cannot be
        compared byte for byte; comparing the parsed documents is what makes
        `deferred-ci` provably the only difference, and keeps an unrelated
        upstream edit from quietly turning the regression test into a tautology.
        """
        contract = yaml.safe_load(CONTRACT.read_text(encoding='utf-8'))
        counter_example = yaml.safe_load(PRE_ADR_0178.read_text(encoding='utf-8'))
        differences = document_differences(contract, counter_example)
        self.assertEqual(
            1,
            len(differences),
            'the counter-example must carry exactly one structural delta; '
            f'found paths {[difference[0] for difference in differences]!r}')
        path, contract_value, fixture_value = differences[0]
        self.assertEqual(
            ('jobs', 'deferred-ci'),
            path,
            'the counter-example mutated the wrong field instead of the '
            'deferred-ci job')
        self.assertIsInstance(
            contract_value,
            dict,
            'the contract jobs.deferred-ci value is no longer a job mapping')
        self.assertIs(
            MISSING,
            fixture_value,
            'the counter-example changed jobs.deferred-ci instead of removing it')

    def test_the_delta_probe_accepts_the_exact_deferred_job_removal(self):
        before = {'jobs': {'build-test': {}, 'deferred-ci': {'if': 'deferred'}}}
        after = {'jobs': {'build-test': {}}}

        self.assertEqual(
            [(('jobs', 'deferred-ci'), {'if': 'deferred'}, MISSING)],
            document_differences(before, after))

    def test_the_delta_probe_exposes_a_wrong_field_mutation(self):
        before = {'jobs': {'build-test': {'if': 'run'}, 'deferred-ci': {}}}
        after = {'jobs': {'build-test': {}, 'deferred-ci': {}}}

        differences = document_differences(before, after)

        self.assertEqual((('jobs', 'build-test', 'if'),),
                         tuple(difference[0] for difference in differences))
        self.assertNotIn(
            ('jobs', 'deferred-ci'),
            (difference[0] for difference in differences))

    def test_a_deferred_run_executes_no_work_while_the_required_context_reports(self):
        for caller in callers_for(CONTRACT):
            with self.subTest(adopter=caller.name):
                outcomes = outcomes_for(CONTRACT, caller, SCENARIOS[0])
                build_test = outcomes['build-test']
                self.assertTrue(
                    build_test.ran,
                    'the required context must still report, or a held Renovate PR '
                    'wedges on a permanently unsatisfied check (Verjson/.github#191)')
                executed = sorted(WORK_STEPS & set(build_test.executed_steps))
                self.assertEqual(
                    [], executed,
                    'a deferred run executed work steps; the scenario no longer '
                    f'models deferral: {executed}')


class EveryExecutingLaneShowsPositiveEvidence(unittest.TestCase):
    def test_a_lane_that_runs_executes_steps_that_do_the_work(self):
        for scenario in SCENARIOS[1:]:
            for caller in callers_for(CONTRACT):
                with self.subTest(scenario=scenario.name, adopter=caller.name):
                    outcomes = outcomes_for(CONTRACT, caller, scenario)
                    key = (caller.name, scenario.name)
                    self.assertIn(
                        key, EXPECTED_EVIDENCE,
                        'a synthetic adopter with no registered evidence: add '
                        'what its lane must execute rather than leaving the '
                        'matrix silent about it')
                    evidence = WORK_STEPS & set(outcomes['build-test'].executed_steps)
                    self.assertEqual(
                        EXPECTED_EVIDENCE[key], evidence,
                        f'{scenario.name} did not execute the work its lane is '
                        'defined by; a lane reporting success on less than this '
                        'is Verjson/verjson-ci#184, whole or in part')

    def test_a_lane_that_runs_concludes_successfully(self):
        """The lane is neither skipped nor unconditionally failing.

        That is the honest statement of what this can detect: the model derives
        `failure` only from an unconditional top-level `exit`, so the
        contract's guarded error paths are all modelled as succeeding. Without
        it, though, a lane modelled as failing would satisfy every other
        property here by accident — spurious red looks like conformance to an
        assertion that only ever demands a non-SUCCESS entry somewhere.
        """
        for scenario in SCENARIOS[1:]:
            for caller in callers_for(CONTRACT):
                with self.subTest(scenario=scenario.name, adopter=caller.name):
                    outcomes = outcomes_for(CONTRACT, caller, scenario)
                    self.assertEqual(
                        'success', outcomes['build-test'].conclusion,
                        'the healthy lane does not conclude successfully, so '
                        'every other assertion here is reading a model of a '
                        'contract nobody could merge against')


class TheEvidenceRegistryMatchesTheContract(unittest.TestCase):
    def test_every_registered_work_step_still_exists(self):
        steps = yaml.safe_load(CONTRACT.read_text(encoding='utf-8'))['jobs']['build-test']['steps']
        names = {step.get('name') or str(step.get('run', '')).strip() for step in steps}
        missing = sorted(WORK_STEPS - names)
        self.assertEqual(
            [], missing,
            'the evidence registry names steps the contract no longer has; a '
            'renamed work step makes every execution assertion vacuous')

    def test_every_expected_step_is_one_the_registry_can_match(self):
        """Assertions compare against `WORK_STEPS & executed`, so a typo in an
        expected name is permanently unsatisfiable rather than caught."""
        for key, expected in EXPECTED_EVIDENCE.items():
            with self.subTest(case=key):
                self.assertEqual(
                    set(), expected - WORK_STEPS,
                    'an expected step outside the work-step registry can never '
                    'appear in the evidence set, so this case can never pass')


class TheAdopterSurfaceIsTheContractSurface(unittest.TestCase):
    def test_every_synthetic_adopter_binds_against_the_contract(self):
        for contract in (CONTRACT, PROTECTED_CONTRACT):
            for caller in callers_for(contract):
                with self.subTest(contract=contract.name, adopter=caller.name):
                    bindings = bind_inputs(contract, caller)
                    self.assertTrue(bindings, f'{caller.name} bound no inputs at all')

    def test_an_adopter_passing_an_undeclared_input_is_rejected(self):
        # Outside `adopters/`: an interrupted run must not leave a fixture that
        # `callers_for` then globs into every subsequent test.
        with tempfile.TemporaryDirectory() as scratch:
            drifted = Path(scratch) / 'drifted-adopter.yml'
            drifted.write_text(
                (ADOPTERS / 'typescript-service.yml').read_text(encoding='utf-8')
                + '      node-verison: 24\n', encoding='utf-8')
            with self.assertRaises(AdopterContractMismatch):
                bind_inputs(CONTRACT, drifted)


# Both shipped contracts declare a default for every optional input, so the
# implicit-default path is unreachable from them. It is still the value the
# harness would model an adopter as running with the moment one input loses its
# default, which is exactly when nobody is looking — hence a synthetic pair.
_SYNTHETIC_CONTRACT = """
on:
  workflow_call:
    inputs:
      flag: {type: boolean}
      count: {type: number}
      label: {type: string}
jobs:
  only: {steps: [{run: 'true'}]}
"""
_SYNTHETIC_CALLER = """
on: push
jobs:
  call:
    uses: Verjson/.github/.github/workflows/synthetic.yml@%s
""" % ('0' * 40)


class AnOmittedOptionalInputTakesTheRuntimesValue(unittest.TestCase):
    def _bind(self, contract_text):
        with tempfile.TemporaryDirectory() as scratch:
            contract = Path(scratch) / 'synthetic.yml'
            caller = Path(scratch) / 'synthetic-caller.yml'
            contract.write_text(contract_text, encoding='utf-8')
            caller.write_text(_SYNTHETIC_CALLER, encoding='utf-8')
            return bind_inputs(contract, caller)

    def test_each_declared_type_gets_the_value_the_runtime_supplies(self):
        self.assertEqual(
            {'inputs.flag': False, 'inputs.count': 0, 'inputs.label': ''},
            self._bind(_SYNTHETIC_CONTRACT),
            'a number bound as the empty string compares unequal to every '
            'number a guard could test it against, so the guard is modelled '
            'false for a reason the contract never expressed')

    def test_a_type_with_no_known_runtime_value_is_rejected(self):
        with self.assertRaises(AdopterContractMismatch):
            self._bind(_SYNTHETIC_CONTRACT.replace('type: string', 'type: environment'))


class TheHarnessFailsClosed(unittest.TestCase):
    """The harness's own #184: a model that guesses cannot assert anything."""

    def test_an_unbound_context_raises_rather_than_defaulting(self):
        scenario = Scenario('unbound', 'missing a binding the contract reads', {})
        with self.assertRaises(UnknownContext):
            model_workflow(CONTRACT, scenario)

    def test_an_unsupported_construct_raises_rather_than_evaluating_false(self):
        with self.assertRaises(UnsupportedExpression):
            Evaluator({}).evaluate("fromJSON('[]')")

    def test_a_status_function_nothing_bound_raises_the_modules_own_error(self):
        """A bare NameError reads as a harness crash, not an unmodelled guard."""
        with self.assertRaises(UnsupportedExpression):
            Evaluator({}, functions={}).evaluate('success()')

    def test_a_step_the_contract_no_longer_declares_raises(self):
        """A test naming a dropped step must fail, not exercise nothing."""
        with self.assertRaises(MissingContractStep):
            locate_step(CONTRACT, 'acquire-secretless-dependencies',
                        'Enforce the secretless event boundary that was renamed')
        with self.assertRaises(MissingContractStep):
            locate_step(CONTRACT, 'a-job-this-contract-does-not-declare',
                        'Enforce the secretless event boundary')

    def test_a_script_that_could_not_run_raises_instead_of_reading_as_a_refusal(self):
        """A refusal and a broken interpreter are both non-zero exits.

        They are not the same fact, and every refusal asserted in this file
        would be satisfied by the second. This is how the sandbox's stripped
        `LD_LIBRARY_PATH` reached CI: the contract's embedded Python could not
        load its own runtime, exited 127, and only the assertions that name the
        contract's *reason* noticed.
        """
        for status in (126, 127):
            with self.subTest(status=status):
                unrunnable = ContractStep(
                    job='synthetic', name='a step whose interpreter is absent',
                    script=f'exit {status}\n', runner_supplied_env=frozenset(),
                    literal_env={})
                with self.assertRaises(StepExecutionFault):
                    execute_step(unrunnable, {})

    def test_a_step_runs_with_the_runners_interpreter_available(self):
        """The sandbox withholds the developer's environment, not the
        runtime the contract's own interpreter needs to start."""
        step = ContractStep(
            job='synthetic', name='a step that runs the runner\'s python3',
            script='python3 -c "print(1)"\n', runner_supplied_env=frozenset(),
            literal_env={})
        self.assertTrue(
            execute_step(step, {}).admitted,
            'a contract step cannot start python3 in this sandbox, so every '
            'embedded-program assertion in this file is reading a startup '
            'failure rather than the contract')

    def test_binding_an_environment_the_step_does_not_declare_raises(self):
        """An input the contract stopped reading, or one it started reading,
        both invalidate what an executed-script assertion claims to prove."""
        step = locate_step(CONTRACT, 'acquire-secretless-dependencies',
                           BOUNDARY_STEP)
        with self.assertRaises(StepEnvironmentMismatch):
            execute_step(step, {**SECRETLESS_PR_CALL, 'SECRETLESS_PT': 'true'})
        with self.assertRaises(StepEnvironmentMismatch):
            execute_step(step, {key: value for key, value
                                in SECRETLESS_PR_CALL.items()
                                if key != 'EVENT_NAME'})


# The credential the secretless lanes exist to keep away from PR-controlled
# code. A reference to `secrets.NODE_AUTH_TOKEN` anywhere in a step definition
# — `env:`, a `with:` token, an inline expression — is that credential reaching
# the step, so the detector reads the whole definition rather than `env` alone.
PACKAGE_CREDENTIAL = 'secrets.NODE_AUTH_TOKEN'


def steps_given_the_package_credential(outcome):
    """The executed steps whose definition references NODE_AUTH_TOKEN."""
    return sorted(
        _step_label_of(definition)
        for definition in outcome.executed_step_definitions
        if PACKAGE_CREDENTIAL in yaml.safe_dump(definition))


def _step_label_of(definition):
    return definition.get('name') or definition.get('uses') or str(
        definition.get('run', ''))[:60]


def build_test_in(contract, caller, scenario):
    return outcomes_for(contract, caller, scenario)['build-test']


SECRETLESS_PR = SCENARIOS[1]
TRUSTED_REF_PUSH = SCENARIOS[2]
SECRETLESS_ADOPTER = ADOPTERS / 'typescript-service.yml'
CREDENTIALED_ADOPTER = ADOPTERS / 'minimal-defaults.yml'


class TheSecretlessLaneNeverReceivesThePackageCredential(unittest.TestCase):
    """Requirement 2 of Verjson/.github#1369: no job on the secretless path
    receives NODE_AUTH_TOKEN. The acquisition job holds the credential and
    executes nothing PR-controlled; `build-test` runs PR-controlled code and
    must therefore never see it, on either secretless lane."""

    def test_no_executed_build_step_references_the_package_credential(self):
        for scenario in (SECRETLESS_PR, TRUSTED_REF_PUSH):
            with self.subTest(scenario=scenario.name):
                outcome = build_test_in(CONTRACT, SECRETLESS_ADOPTER, scenario)
                self.assertEqual(
                    [], steps_given_the_package_credential(outcome),
                    'a step executing in the credentialless build job was '
                    'handed the package credential the secretless lane exists '
                    'to withhold from PR-controlled code')

    def test_the_detector_finds_the_credential_where_the_contract_does_pass_it(self):
        """Otherwise the assertion above passes because nothing is detected.

        The credentialed lane is the positive control: the same detector, the
        same contract, the opposite verdict. Without it a detector that never
        matches — a renamed secret, a serialization that drops `env` — would
        report every lane as clean.
        """
        outcome = build_test_in(CONTRACT, CREDENTIALED_ADOPTER, SECRETLESS_PR)
        self.assertIn(
            'npm ci', steps_given_the_package_credential(outcome),
            'the credentialed lane no longer passes NODE_AUTH_TOKEN to any '
            'step this detector can see, so the secretless assertion above '
            'proves nothing')


# The contract's own secretless boundary, exercised by running it. `env:` names
# come from the step itself (`contract_steps` rejects a binding set that is not
# exactly the declared one), so renaming one of these inputs upstream fails
# here instead of quietly leaving the matrix asserting against a stale surface.
BOUNDARY_STEP = 'Enforce the secretless event boundary'


def boundary_verdict(**bindings):
    """Run the contract's boundary script over one call's inputs."""
    step = locate_step(CONTRACT, 'acquire-secretless-dependencies', BOUNDARY_STEP)
    return execute_step(step, bindings)


def admitted(**bindings):
    return boundary_verdict(**bindings).admitted


# A same-repository pull request opting into the PR lane: the one shape the
# secretless PR lane is specified to admit.
SECRETLESS_PR_CALL = dict(
    APPROVED_INTERNAL_PACKAGES='@verjson/compliance',
    EVENT_NAME='pull_request',
    HEAD_REPOSITORY='Verjson/verjson-ci',
    NODE_AUTH_TOKEN='x' * 40,
    REPOSITORY='Verjson/verjson-ci',
    SCHEMA_DIR='',
    SECRETLESS_PR='true',
    SECRETLESS_TRUSTED_REF='false',
)
TRUSTED_REF_CALL = {
    **SECRETLESS_PR_CALL,
    'EVENT_NAME': 'push',
    'HEAD_REPOSITORY': '',
    'SECRETLESS_PR': 'false',
    'SECRETLESS_TRUSTED_REF': 'true',
}


class RefusalAssertions:
    """Assert *why* the contract refused, not only that it did.

    A non-zero exit is satisfied by a fixture that has drifted into being
    malformed, or by a validator that refuses every input. Naming the reason is
    what keeps each case bound to the rule it claims to exercise.
    """

    def assertRefusedBecause(self, result, reason, message):
        self.assertNotEqual(0, result.status, message)
        self.assertIn(
            reason, result.output,
            f'refused, but not because {reason!r}; this case no longer '
            f'exercises the rule it names:\n{result.output.strip()[-400:]}')

    def assertAdmitted(self, result, message):
        self.assertEqual(
            0, result.status,
            f'{message}\n{result.output.strip()[-400:]}')


class TheTwoSecretlessLanesAdmitDisjointEvents(RefusalAssertions, unittest.TestCase):
    """Requirement 3 of Verjson/.github#1369, and the security property under it.

    The lanes differ only in which heads they trust: the PR lane admits a
    same-repository `pull_request` and nothing else, the trusted-ref lane
    admits `push` and explicit `workflow_dispatch` and nothing else. Both are
    enforced in one script, so what matters is that no combination of the two
    inputs lets an untrusted head reach the credentialed acquisition job.
    """

    def test_the_pr_lane_admits_a_same_repository_pull_request(self):
        self.assertTrue(
            admitted(**SECRETLESS_PR_CALL),
            'the boundary refuses the one call the PR lane exists to admit, so '
            'every refusal asserted below is satisfied by a script that '
            'refuses everything')

    def test_the_trusted_ref_lane_admits_a_push(self):
        self.assertTrue(
            admitted(**TRUSTED_REF_CALL),
            'the boundary refuses the one call the trusted-ref lane exists to '
            'admit')

    def test_the_trusted_ref_lane_refuses_a_pull_request(self):
        self.assertRefusedBecause(
            boundary_verdict(**{**TRUSTED_REF_CALL,
                                'EVENT_NAME': 'pull_request',
                                'HEAD_REPOSITORY': 'fork/verjson-ci'}),
            'secretless-trusted-ref accepts only push',
            'secretless-trusted-ref admitted a pull_request, so PR-controlled '
            'code entered the credentialed acquisition job by declaring the '
            'wrong lane')

    def test_the_pr_lane_refuses_a_fork_head(self):
        self.assertRefusedBecause(
            boundary_verdict(**{**SECRETLESS_PR_CALL,
                                'HEAD_REPOSITORY': 'fork/verjson-ci'}),
            'fork PRs receive no package credential',
            'a fork pull request was admitted to the acquisition job, which '
            'holds the package credential')

    def test_the_pr_lane_refuses_every_other_event(self):
        for event in ('push', 'workflow_dispatch', 'pull_request_target',
                      'issue_comment', 'schedule', ''):
            with self.subTest(event=event):
                self.assertFalse(
                    admitted(**{**SECRETLESS_PR_CALL, 'EVENT_NAME': event}),
                    'secretless-pr is restricted to pull_request validation')

    def test_the_trusted_ref_lane_refuses_every_event_but_push_and_dispatch(self):
        for event in ('pull_request', 'pull_request_target', 'issue_comment',
                      'schedule', ''):
            with self.subTest(event=event):
                self.assertFalse(
                    admitted(**{**TRUSTED_REF_CALL, 'EVENT_NAME': event}),
                    'the trusted-ref lane admitted an event whose head is not '
                    'a trusted ref')
        self.assertTrue(
            admitted(**{**TRUSTED_REF_CALL, 'EVENT_NAME': 'workflow_dispatch'}),
            'an explicit dispatch is the trusted-ref lane\'s second admitted '
            'event; refusing it means the refusals above prove less than they '
            'claim')

    def test_neither_lane_is_admitted_when_both_are_declared(self):
        """The combination is the one an attacker would want: a `pull_request`
        that also claims the trusted-ref lane, hoping one guard admits it."""
        for event in ('pull_request', 'push', 'workflow_dispatch'):
            with self.subTest(event=event):
                self.assertRefusedBecause(
                    boundary_verdict(**{**SECRETLESS_PR_CALL,
                                        'EVENT_NAME': event,
                                        'SECRETLESS_PR': 'true',
                                        'SECRETLESS_TRUSTED_REF': 'true'}),
                    'enable exactly one secretless event mode',
                    'both secretless modes were admitted at once, so which '
                    'head the acquisition job trusts is no longer decided by '
                    'either lane\'s rule')

    def test_a_call_declaring_neither_lane_is_refused(self):
        """The acquisition job's own guard already requires one of the two, so
        this is defense in depth — and it is what makes the exactly-one rule a
        rule rather than a side effect of the guard above it."""
        self.assertFalse(
            admitted(**{**SECRETLESS_PR_CALL, 'SECRETLESS_PR': 'false',
                        'SECRETLESS_TRUSTED_REF': 'false'}),
            'the acquisition job ran with neither secretless mode declared')

    def test_both_lanes_refuse_credentialed_submodule_acquisition(self):
        for lane, call in (('secretless-pr', SECRETLESS_PR_CALL),
                           ('secretless-trusted-ref', TRUSTED_REF_CALL)):
            with self.subTest(lane=lane):
                self.assertRefusedBecause(
                    boundary_verdict(**{**call, 'SCHEMA_DIR': 'schema'}),
                    'do not permit credentialed submodule acquisition',
                    'a secretless lane admitted a schema-dir call, whose '
                    '`npm ci` in build-test is handed NODE_AUTH_TOKEN')

    def test_both_lanes_refuse_an_acquisition_with_no_credential(self):
        for lane, call in (('secretless-pr', SECRETLESS_PR_CALL),
                           ('secretless-trusted-ref', TRUSTED_REF_CALL)):
            with self.subTest(lane=lane):
                self.assertFalse(
                    admitted(**{**call, 'NODE_AUTH_TOKEN': ''}),
                    'the acquisition job continued without the credential it '
                    'exists to hold, so it would report success having '
                    'acquired nothing')


# The contract's own allowlist enforcement, exercised by running it against a
# synthetic checkout. Requirement 2 of Verjson/.github#1369 turns on a package
# deliberately outside the allowlist, and what the contract does with one is
# decided by an embedded Python program, not by a job guard.
LOCK_VALIDATION_STEP = 'Validate approved internal dependency lock'


def github_packages_url(package, version='1.0.0'):
    return f'https://npm.pkg.github.com/download/{package}/{version}/deadbeef'


def lock_integrity(package):
    """One exact, well-formed sha512 lock integrity, stable per package.

    The validator requires a decodable 64-byte digest, so this has to be a real
    base64 encoding rather than a plausible-looking literal; deriving it from
    the name keeps a repeated download URL consistent with itself.
    """
    digest = hashlib.sha512(package.encode('utf-8')).digest()
    return 'sha512-' + base64.b64encode(digest).decode('ascii')


def npm_lock(*internal_packages):
    """A lockfileVersion 3 lock whose only internal deps are those named."""
    packages = {'': {'name': 'synthetic-adopter', 'version': '0.0.0'}}
    for package in internal_packages:
        packages[f'node_modules/{package}'] = {
            'version': '1.0.0',
            'resolved': github_packages_url(package),
            'integrity': lock_integrity(package),
        }
    # One public dependency, so the lock is not exclusively internal and the
    # validator's "ignore anything outside the approved scopes" path is
    # actually taken rather than assumed.
    packages['node_modules/left-pad'] = {
        'version': '1.3.0',
        'resolved': 'https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz',
        'integrity': lock_integrity('left-pad'),
    }
    return {'name': 'synthetic-adopter', 'lockfileVersion': 3,
            'requires': True, 'packages': packages}


def _write_manifest(directory, lock):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'package.json').write_text(
        json.dumps({'name': 'synthetic-adopter', 'version': '0.0.0'}),
        encoding='utf-8')
    (directory / 'package-lock.json').write_text(json.dumps(lock),
                                                 encoding='utf-8')


def lock_validation(approved, lock, *, nested=None, nested_manifests=''):
    """Run the contract's lock validator over a synthetic checkout.

    `nested` maps a repository-relative directory to the lock it should
    contain, so a manifest's own approvals can be exercised separately from
    the root's.
    """
    step = locate_step(CONTRACT, 'acquire-secretless-dependencies',
                       LOCK_VALIDATION_STEP)
    with tempfile.TemporaryDirectory() as scratch:
        workspace = Path(scratch).resolve()
        _write_manifest(workspace, lock)
        for directory, nested_lock in (nested or {}).items():
            _write_manifest(workspace / directory, nested_lock)
        return execute_step(step, {
            'APPROVED_INTERNAL_PACKAGES': '\n'.join(approved),
            'APPROVED_INTERNAL_SCOPES': '@verjson',
            'COMPATIBILITY_RANGES': '',
            'NESTED_MANIFESTS': nested_manifests,
            # Outside the workspace: the validator creates this file and
            # refuses a second run that finds it, so it must not be a path a
            # later assertion in the same checkout would collide with.
            'PRIVATE_CACHE_ENTRIES': str(Path(scratch) / 'private-entries'),
            'TRUSTED_PACKAGE_POLICY': '',
            'PACKAGE_MANAGER': 'npm',
        }, workspace=workspace)


APPROVED = '@verjson/compliance'
OUTSIDE_THE_ALLOWLIST = '@verjson/unapproved-by-this-call'


class TheAllowlistIsExactlyWhatTheLaneMayAcquire(RefusalAssertions, unittest.TestCase):
    """Requirement 2 of Verjson/.github#1369: a package in
    `approved-internal-packages` is acquirable, one outside it is refused, and
    the refusal is a failure rather than a silent skip."""

    def test_an_approved_internal_package_is_acquirable(self):
        self.assertAdmitted(
            lock_validation([APPROVED], npm_lock(APPROVED)),
            'the lane refuses the package its caller approved, so every '
            'refusal asserted below is satisfied by a validator that refuses '
            'every lock it is given')

    def test_registry_scope_casing_does_not_change_an_approved_package_identity(self):
        lock = npm_lock(APPROVED)
        lock['packages'][f'node_modules/{APPROVED}']['resolved'] = (
            github_packages_url('@verJSON/Compliance'))

        self.assertAdmitted(
            lock_validation([APPROVED], lock),
            'the registry-issued URL differs in ASCII casing but names the '
            'same package approved by the caller and recorded in the lock')

    def test_nested_npm_installation_path_keeps_the_approved_identity(self):
        lock = npm_lock(APPROVED)
        package = lock['packages'].pop(f'node_modules/{APPROVED}')
        package['resolved'] = github_packages_url('@verJSON/Compliance')
        lock['packages'][f'node_modules/outer/node_modules/{APPROVED}'] = package

        self.assertAdmitted(
            lock_validation([APPROVED], lock),
            'a nested npm installation path names the same approved package')

    def test_registry_casing_does_not_approve_other_or_non_ascii_identities(self):
        for identity in ('@verJSON/Unapproved', '@Other/Compliance',
                         '@verjs\u043en/Compliance'):
            with self.subTest(identity=identity):
                lock = npm_lock(APPROVED)
                lock['packages'][f'node_modules/{APPROVED}']['resolved'] = (
                    github_packages_url(identity))

                self.assertRefusedBecause(
                    lock_validation([APPROVED], lock),
                    f'unapproved GitHub Packages download: {identity}',
                    'URL identity outside the exact ASCII approval was admitted')

    def test_registry_casing_does_not_hide_a_mismatched_lock_name(self):
        lock = npm_lock(APPROVED)
        lock['packages'][f'node_modules/{APPROVED}'].update({
            'name': '@verJSON/Compliance',
            'resolved': github_packages_url('@verJSON/Compliance'),
        })

        self.assertRefusedBecause(
            lock_validation([APPROVED], lock),
            f'{APPROVED} lock entry aliases unexpected package',
            'the URL casing exception also changed lock identity matching')

    def test_registry_casing_does_not_admit_a_mixed_case_lock_path(self):
        for locked_name in (None, APPROVED):
            with self.subTest(locked_name=locked_name):
                lock = npm_lock(APPROVED)
                mixed_path = 'node_modules/@verJSON/Compliance'
                lock['packages'][mixed_path] = {
                    'version': '1.0.0',
                    'resolved': github_packages_url('@verJSON/Compliance'),
                    'integrity': lock_integrity(APPROVED),
                }
                if locked_name:
                    lock['packages'][mixed_path]['name'] = locked_name

                self.assertRefusedBecause(
                    lock_validation([APPROVED], lock),
                    'is not pinned to its GitHub Packages download URL',
                    'a mixed-case lock path added an unapproved installation')

    def test_registry_tarball_requires_an_npm_installation_path(self):
        for path in ('vendor/private', APPROVED,
                     f'fake_node_modules/{APPROVED}',
                     f'/node_modules/{APPROVED}',
                     f'vendor//node_modules/{APPROVED}',
                     f'vendor/../node_modules/{APPROVED}',
                     f'vendor/./node_modules/{APPROVED}',
                     f'vendor\\item/node_modules/{APPROVED}',
                     f'vendor\x00/node_modules/{APPROVED}',
                     f'vendor\nnode_modules/{APPROVED}'):
            with self.subTest(path=path):
                lock = npm_lock(APPROVED)
                lock['packages'][path] = {
                    'name': APPROVED,
                    'version': '1.0.0',
                    'resolved': github_packages_url('@verJSON/Compliance'),
                    'integrity': lock_integrity(APPROVED),
                }

                self.assertRefusedBecause(
                    lock_validation([APPROVED], lock),
                    f'{APPROVED} has invalid npm installation path',
                    'a registry package outside node_modules was counted as approved')

    def test_registry_casing_does_not_relax_url_structure(self):
        valid_url = github_packages_url('@verJSON/Compliance')
        for resolved in (valid_url.replace('https:', 'http:', 1),
                         valid_url + '?download=1', valid_url + '#archive',
                         valid_url.replace('/download/', '/download/extra/', 1),
                         valid_url.replace('@verJSON', '%40verJSON', 1)):
            with self.subTest(resolved=resolved):
                lock = npm_lock(APPROVED)
                lock['packages'][f'node_modules/{APPROVED}']['resolved'] = resolved

                self.assertRefusedBecause(
                    lock_validation([APPROVED], lock),
                    'invalid GitHub Packages download URL',
                    'a malformed registry URL was admitted with mixed casing')

    def test_registry_casing_keeps_integrity_and_digest_checks(self):
        lock = npm_lock(APPROVED)
        lock['packages'][f'node_modules/{APPROVED}']['resolved'] = (
            github_packages_url('@verJSON/Compliance'))
        invalid_integrity = json.loads(json.dumps(lock))
        invalid_integrity['packages'][f'node_modules/{APPROVED}']['integrity'] = (
            'sha512-invalid')
        self.assertRefusedBecause(
            lock_validation([APPROVED], invalid_integrity),
            'requires one exact sha512 lock integrity',
            'mixed URL casing bypassed the lock integrity check')

        conflicting_digest = json.loads(json.dumps(lock))
        conflicting_digest['packages'][f'node_modules/{APPROVED}']['integrity'] = (
            lock_integrity(OUTSIDE_THE_ALLOWLIST))
        nested_manifests = json.dumps(
            [{'path': 'tools', 'approvedPackages': [APPROVED], 'scriptPlan': []}])
        self.assertRefusedBecause(
            lock_validation([APPROVED], lock,
                            nested={'tools': conflicting_digest},
                            nested_manifests=nested_manifests),
            'repeats one download URL with different integrity',
            'the same registry URL carried conflicting payload digests')

    def test_a_lane_with_no_internal_dependencies_is_acquirable(self):
        self.assertAdmitted(
            lock_validation([], npm_lock()),
            'an empty approved set is valid when the lock has no internal '
            'downloads, and an adopter that acquires only public packages '
            'must still reach its build')

    def test_a_package_outside_the_allowlist_is_refused_by_name(self):
        """Refused *for that reason*, not merely non-zero.

        A validator that rejected the synthetic lock over its shape would
        satisfy a bare exit-status assertion while proving nothing about the
        allowlist, and the fixture would then be free to drift into
        malformedness without any test noticing.
        """
        refusal = lock_validation(
            [APPROVED], npm_lock(APPROVED, OUTSIDE_THE_ALLOWLIST))
        self.assertNotEqual(
            0, refusal.status,
            'an internal package the caller never approved was acquired with '
            'the package credential')
        self.assertIn(
            OUTSIDE_THE_ALLOWLIST, refusal.output,
            'the lane refused the lock without naming the unapproved package, '
            'so this case no longer proves the allowlist is what refused it')

    def test_an_allowlist_entry_absent_from_the_lock_is_refused(self):
        """The allowlist is exact in both directions: an entry the lock does
        not contain is an authorization nothing accounts for."""
        self.assertRefusedBecause(
            lock_validation([APPROVED, OUTSIDE_THE_ALLOWLIST],
                            npm_lock(APPROVED)),
            f'absent from lock: {OUTSIDE_THE_ALLOWLIST}',
            'a package was authorized for this call without appearing in the '
            'lock, so the approved set no longer describes what is acquired')

    def test_an_internal_package_not_pinned_to_github_packages_is_refused(self):
        """An internal name resolved somewhere else is the substitution the
        allowlist cannot see: the name is approved, the download is not."""
        lock = npm_lock(APPROVED)
        lock['packages'][f'node_modules/{APPROVED}']['resolved'] = (
            'https://registry.npmjs.org/@verjson/compliance/-/compliance-1.0.0.tgz')
        self.assertRefusedBecause(
            lock_validation([APPROVED], lock),
            f'{APPROVED} is not pinned to its GitHub Packages download URL',
            'an approved internal package was accepted from a registry the '
            'lane does not route its scopes to')

    def test_a_nested_manifest_does_not_inherit_the_root_allowlist(self):
        """`secretless-nested-manifests` authorizes per manifest. A package
        approved for the root and acquired by a nested manifest is outside
        *that* manifest's allowlist, and the contract says so explicitly."""
        nested_manifests = json.dumps(
            [{'path': 'tools', 'approvedPackages': [], 'scriptPlan': []}])
        self.assertRefusedBecause(
            lock_validation([APPROVED], npm_lock(APPROVED),
                            nested={'tools': npm_lock(APPROVED)},
                            nested_manifests=nested_manifests),
            f'unapproved GitHub Packages download in tools: {APPROVED}',
            'a nested manifest acquired an internal package approved only for '
            'the root manifest, so per-manifest authorization is not the '
            'boundary the contract documents')

    def test_a_nested_manifest_with_its_own_approval_is_acquirable(self):
        nested_manifests = json.dumps(
            [{'path': 'tools', 'approvedPackages': [APPROVED],
              'scriptPlan': []}])
        self.assertAdmitted(
            lock_validation([APPROVED], npm_lock(APPROVED),
                            nested={'tools': npm_lock(APPROVED)},
                            nested_manifests=nested_manifests),
            'a nested manifest that approved its own internal package was '
            'refused, so the refusal above proves only that nested manifests '
            'never work')


@contextlib.contextmanager
def contract_whose_acquisition_refuses():
    """The contract, with the secretless acquisition job refusing its call.

    The real refusals — an untrusted event, a package outside the allowlist —
    happen inside a `run:` script, and `model.py` deliberately declines to
    infer a verdict from one. `TheTwoSecretlessLanesAdmitDisjointEvents` and
    `TheAllowlistIsExactlyWhatTheLaneMayAcquire` establish by execution *that*
    those calls are refused; what remains is what the rest of the contract does
    once the job carrying that refusal concludes unsuccessfully.

    So the boundary step's script is replaced by the bare non-zero exit those
    refusals produce. Nothing else is touched: the guards, the job graph, and
    every other step are the published ones, so the failure propagates exactly
    as it would in an adopter's run.
    """
    contract = yaml.safe_load(CONTRACT.read_text(encoding='utf-8'))
    steps = contract['jobs']['acquire-secretless-dependencies']['steps']
    refusals = [step for step in steps if step.get('name') == BOUNDARY_STEP]
    if len(refusals) != 1:
        raise AssertionError(
            f'{CONTRACT.name} no longer declares exactly one {BOUNDARY_STEP!r} '
            'step, so this mutation no longer models a refused acquisition')
    refusals[0]['run'] = 'exit 1\n'
    with tempfile.TemporaryDirectory() as scratch:
        mutant = Path(scratch) / CONTRACT.name
        mutant.write_text(yaml.safe_dump(contract, sort_keys=False),
                          encoding='utf-8')
        yield mutant


class ARefusedAcquisitionCannotReportSuccess(unittest.TestCase):
    """A lane that declines to do work must not publish a check claiming it did.

    This is `Verjson/verjson-ci#184` in the secretless lanes: there the cause
    was a deferral, here it is a refused acquisition, and the failure mode is
    identical — `build-test` is a required context, it runs `if: always()`, and
    it would report SUCCESS having executed no test, lint, type check, or
    contract guard. Requirement 2 of Verjson/.github#1369 asks for exactly
    this: the refusal must be a failure, not a silent skip.
    """

    def test_the_refusal_reaches_the_required_check_as_a_failure(self):
        with contract_whose_acquisition_refuses() as mutant:
            for scenario in (SECRETLESS_PR, TRUSTED_REF_PUSH):
                with self.subTest(scenario=scenario.name):
                    outcomes = outcomes_for(mutant, SECRETLESS_ADOPTER, scenario)
                    self.assertEqual(
                        'failure',
                        outcomes['acquire-secretless-dependencies'].conclusion,
                        'the mutation no longer models a refused acquisition')
                    self.assertTrue(
                        outcomes['build-test'].ran,
                        'the required context stopped reporting, which wedges '
                        'the head on a permanently unsatisfied check (#191)')
                    self.assertEqual(
                        'failure', outcomes['build-test'].conclusion,
                        'the secretless lane refused to acquire its '
                        'dependencies and the required check still reported a '
                        'conclusion a merge gate accepts — Verjson/verjson-ci'
                        '#184 in the secretless lane')

    def test_no_work_step_runs_once_the_acquisition_is_refused(self):
        """The complement: failing the check is not enough if the lane went on
        to run PR-controlled code against dependencies nothing authorized."""
        with contract_whose_acquisition_refuses() as mutant:
            for scenario in (SECRETLESS_PR, TRUSTED_REF_PUSH):
                with self.subTest(scenario=scenario.name):
                    build_test = outcomes_for(mutant, SECRETLESS_ADOPTER,
                                              scenario)['build-test']
                    executed = sorted(WORK_STEPS & set(build_test.executed_steps))
                    self.assertEqual(
                        [], executed,
                        'the lane ran work steps after its dependency '
                        f'acquisition was refused: {executed}')
                    self.assertEqual(
                        [], steps_given_the_package_credential(build_test),
                        'a refused secretless acquisition let a later step '
                        'fall back to the credentialed path')

    def test_the_unrefused_lane_is_the_control(self):
        """Without this, every assertion above is satisfied by a contract whose
        secretless lane fails or executes nothing under all conditions."""
        for scenario in (SECRETLESS_PR, TRUSTED_REF_PUSH):
            with self.subTest(scenario=scenario.name):
                outcomes = outcomes_for(CONTRACT, SECRETLESS_ADOPTER, scenario)
                self.assertEqual(
                    'success',
                    outcomes['acquire-secretless-dependencies'].conclusion)
                self.assertEqual('success', outcomes['build-test'].conclusion)
                self.assertEqual(
                    SECRETLESS,
                    WORK_STEPS & set(outcomes['build-test'].executed_steps),
                    'the published secretless lane no longer executes its '
                    'script plan, so the refusal assertions above cannot tell '
                    'a refusal from the lane\'s normal behavior')


class BothSecretlessLanesTakeTheSameAcquisitionPath(unittest.TestCase):
    """Requirement 3 of Verjson/.github#1369: what distinguishes the lanes.

    The trusted-ref lane is not a second, weaker implementation of the
    secretless path — it is the same acquisition, transfer, scrub, and script
    plan, admitted for a different set of heads. The event boundary is the only
    difference, and `TheTwoSecretlessLanesAdmitDisjointEvents` is where that
    difference is asserted; here it is that nothing else differs.
    """

    def test_neither_lane_enters_the_credentialed_install(self):
        for scenario in (SECRETLESS_PR, TRUSTED_REF_PUSH):
            with self.subTest(scenario=scenario.name):
                outcomes = outcomes_for(CONTRACT, SECRETLESS_ADOPTER, scenario)
                self.assertTrue(
                    outcomes['acquire-secretless-dependencies'].ran,
                    'a secretless lane skipped the acquisition job, so its '
                    'dependencies were never authorized against the allowlist')
                executed = set(outcomes['build-test'].executed_steps)
                self.assertNotIn(
                    'npm ci', executed,
                    'the secretless lane ran the credentialed install, which '
                    'is handed NODE_AUTH_TOKEN in the same job as '
                    'PR-controlled code')

    def test_both_lanes_execute_the_same_work(self):
        evidence = [
            WORK_STEPS & set(outcomes_for(CONTRACT, SECRETLESS_ADOPTER,
                                          scenario)['build-test'].executed_steps)
            for scenario in (SECRETLESS_PR, TRUSTED_REF_PUSH)]
        self.assertEqual(
            evidence[0], evidence[1],
            'the two secretless lanes no longer verify the same thing, so one '
            'of them is a weaker path to the same required check')

    def test_the_credentialed_adopter_still_takes_the_credentialed_path(self):
        """The control on both assertions above: an adopter opting into neither
        lane must skip the acquisition job and run the credentialed install."""
        outcomes = outcomes_for(CONTRACT, CREDENTIALED_ADOPTER, SECRETLESS_PR)
        self.assertFalse(
            outcomes['acquire-secretless-dependencies'].ran,
            'the acquisition job ran for an adopter that opted into neither '
            'secretless lane, so `ran` no longer distinguishes the paths')
        self.assertIn(
            'npm ci', set(outcomes['build-test'].executed_steps),
            'the credentialed lane no longer runs `npm ci`, so asserting its '
            'absence in the secretless lane proves nothing')


if __name__ == '__main__':
    unittest.main()
