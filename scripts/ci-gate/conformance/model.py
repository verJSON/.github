#!/usr/bin/env python3
"""Statically model what a reusable workflow does for one synthetic adopter.

A reusable workflow is only ever exercised by real callers, after publication,
so a contract defect reaches every adopter before anyone sees it. That is how
`Verjson/verjson-ci#184` shipped: `build-test` reported SUCCESS on a deferred
head having executed no test, lint, type check, or contract guard.

This module evaluates the contract's job and step guards against a scenario's
input bindings and reports, per job, whether it ran and whether any step that
does real work ran. The properties asserted on top of that model live in
conformance.test.py; nothing here decides what is acceptable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from expressions import Evaluator
from yaml_documents import load_yaml_document


@dataclass(frozen=True)
class Scenario:
    """One synthetic adopter's call into the contract.

    `bindings` supplies every context value the contract's guards read. A guard
    reading something unbound raises rather than defaulting, so adding a guard
    upstream surfaces here as a failing scenario instead of a silent change in
    what the harness believes executed.
    """

    name: str
    description: str
    bindings: dict[str, object]
    functions: dict[str, object] = field(default_factory=dict)


@dataclass
class JobOutcome:
    name: str
    ran: bool
    executed_steps: list[str] = field(default_factory=list)
    skipped_steps: list[str] = field(default_factory=list)
    # The definitions of the executed steps, in execution order and parallel to
    # `executed_steps`. A property about what a step is *given* — a credential
    # in `env:`, a token in `with:` — cannot be stated over labels, and
    # re-deriving it by matching a label back to the contract is ambiguous the
    # moment two steps share a label.
    executed_step_definitions: list[dict] = field(default_factory=list)
    terminates_unsuccessfully: bool = False

    @property
    def conclusion(self) -> str:
        """The conclusion this job contributes to a caller's check rollup.

        The three values a merge predicate has to tell apart: `skipped` and
        `success` are both things a naive all-SUCCESS assertion accepts, and
        `failure` is the only one that makes a deferred head distinguishable
        from a verified one.
        """
        if not self.ran:
            return 'skipped'
        return 'failure' if self.terminates_unsuccessfully else 'success'


# The guards that still run a step after an earlier step failed. GitHub drops
# the implicit `success() &&` when the expression names *any* status check
# function, `success()` included — so `success() || github.event_name == 'push'`
# runs after a failure and is correctly evaluated false by `_evaluator`.
# Everything else is skipped from that point on, and a model that keeps counting
# later steps as executed manufactures exactly the evidence of execution this
# harness exists to demand — the harness's own #184.
_RUNS_AFTER_FAILURE = re.compile(r'\b(?:always|success|failure|cancelled)\s*\(')

# A heredoc body is written at column 0 of the parsed block scalar, so the
# column test below cannot see that it is data being fed to another program
# rather than shell the runner executes. `node-ci.yml` has ~18 of them; a
# `cat > script.sh <<'SH'` carrying a column-0 `exit 1` would otherwise model
# a healthy lane as red — the indented-`exit` defect returning by another door.
_HEREDOC = re.compile(r"""<<-?\s*['"]?(?P<delimiter>[A-Za-z_][A-Za-z0-9_]*)['"]?""")


def _terminates_unsuccessfully(step: dict) -> bool:
    """Whether running this step concludes its job as a failure.

    Only an `exit <non-zero>` at column 0 of the parsed block scalar, outside
    any open heredoc, counts. An indented one sits inside a bash conditional
    and fires on a branch nothing here can see, so treating it as certain would
    report every guarded error path as a failure and model the healthy lane as
    red. Deliberate unconditional failure is how ADR 0178's deferral makes
    itself visible in a check rollup rather than in an annotation an opt-in
    script has to read, so the model has to represent it — a model in which
    every job that runs succeeds cannot express the difference the whole
    conformance matrix is about.

    This is a claim about shell structure inferred from layout, so it is a
    heuristic: `set -e` propagation, traps, and an `exit` reached through a
    function are all outside what it can see. It is deliberately biased toward
    *not* reporting failure, because a spurious failure is what modelled the
    healthy lane as red.
    """
    heredoc: str | None = None
    for line in str(step.get('run', '')).splitlines():
        if heredoc is not None:
            if line.strip() == heredoc:
                heredoc = None
            continue
        opened = _HEREDOC.search(line)
        if opened:
            heredoc = opened.group('delimiter')
            continue
        if line.startswith((' ', '\t')):
            continue
        line = line.strip()
        if line.startswith('exit ') and line[5:].strip().isdigit():
            # The first unconditional `exit` decides the script's status: a
            # later one is unreachable. A column-0 `exit 0` therefore means
            # this step succeeds, not that the scan found nothing.
            return line[5:].strip() != '0'
    return False


def _step_label(step: dict) -> str:
    return step.get('name') or step.get('uses') or str(step.get('run', ''))[:60]


def model_workflow(path: Path, scenario: Scenario) -> dict[str, JobOutcome]:
    """Return one outcome per job in `path` under `scenario`.

    Job results feed later guards, so jobs are walked in declaration order and
    each job's result is bound before the next job's guard is evaluated. The
    contract declares its jobs in dependency order; a workflow that does not is
    a defect this harness should surface rather than tolerate.
    """
    workflow = load_yaml_document(path)
    bindings = dict(scenario.bindings)
    outcomes: dict[str, JobOutcome] = {}

    for job_name, job in workflow['jobs'].items():
        # `needs:` is a scalar when there is one dependency and a list when
        # there are several. Iterating the scalar walks its characters.
        needs = job.get('needs') or []
        dependencies = [needs] if isinstance(needs, str) else list(needs)
        for dependency in dependencies:
            if dependency not in outcomes:
                raise ValueError(
                    f'{path.name}: job {job_name!r} needs {dependency!r}, '
                    'which is declared after it')
        results = [outcomes[dependency].conclusion for dependency in dependencies]

        guard = job.get('if')
        if guard is None:
            # GitHub skips an unguarded job whose dependencies did not all
            # succeed. Modelling it as running regardless is how a harness
            # lets `if: always()` be deleted from a required job — which is
            # precisely the defect class this matrix exists to catch.
            ran = all(result == 'success' for result in results)
        else:
            ran = _evaluator(bindings, scenario, results, failed=False).evaluate(guard)
        outcome = JobOutcome(name=job_name, ran=ran)

        if ran:
            for step in job.get('steps') or []:
                guard = step.get('if')
                evaluator = _evaluator(
                    bindings, scenario, results,
                    failed=outcome.terminates_unsuccessfully)
                # Evaluate before applying the after-failure skip, never
                # instead of it: skipping first would let a guard that reads an
                # unbound context escape the evaluator entirely, which is the
                # fail-closed property this module is built on.
                runs = evaluator.evaluate(guard) and (
                    not outcome.terminates_unsuccessfully
                    or bool(guard and _RUNS_AFTER_FAILURE.search(str(guard))))
                if runs:
                    outcome.executed_steps.append(_step_label(step))
                    outcome.executed_step_definitions.append(step)
                    if _terminates_unsuccessfully(step):
                        outcome.terminates_unsuccessfully = True
                else:
                    outcome.skipped_steps.append(_step_label(step))

        outcomes[job_name] = outcome
        bindings[f'needs.{job_name}.result'] = outcome.conclusion

    return outcomes


def _evaluator(bindings: dict, scenario: Scenario, results: list[str],
               *, failed: bool) -> Evaluator:
    """An evaluator whose status functions mean what they mean at this point.

    `success()`/`failure()` read the dependencies at job level and the steps so
    far at step level. Binding them at all is what keeps a guard using them
    from raising a bare `NameError` instead of the module's typed errors.
    """
    return Evaluator(bindings, functions={
        'always': lambda: True,
        'success': lambda: not failed and all(r == 'success' for r in results),
        # Direct `needs` only; GitHub's job-level `failure()` is transitive
        # over ancestors. The contract is two levels deep and uses no job-level
        # `failure()`, so the difference cannot bite here.
        'failure': lambda: failed or any(r == 'failure' for r in results),
        'cancelled': lambda: False,
        **scenario.functions,
    })
