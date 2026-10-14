#!/usr/bin/env python3
"""Bind a contract's `inputs.*` context from a synthetic adopter's caller.

The fixtures under `adopters/` are ordinary caller workflows — the same shape a
real repository commits. Deriving the input bindings from them rather than from
a hand-written dict is what makes removing or renaming a `workflow_call` input
fail here, at the hub, instead of in a red adopter build after publication.
"""

from __future__ import annotations

from pathlib import Path

from yaml_documents import load_yaml_document

ADOPTERS = Path(__file__).resolve().parent / 'adopters'


class AdopterContractMismatch(Exception):
    """The caller and the contract disagree about the input surface."""


def _declared_inputs(contract: dict) -> dict[str, dict]:
    # `on:` parses as the boolean True under YAML 1.1, which is why this reads
    # `contract[True]` rather than `contract['on']`.
    triggers = contract.get('on') or contract.get(True) or {}
    return (triggers.get('workflow_call') or {}).get('inputs') or {}


def bind_inputs(contract_path: Path, caller_path: Path,
                binds_as: str | None = None) -> dict[str, object]:
    """Return `inputs.<name>` bindings for every input the contract declares.

    `binds_as` names the contract the caller's `uses:` refers to, for a fixture
    contract no adopter names directly — a regression counter-example stands in
    for the real contract and must be bound with the real contract's inputs, or
    it silently runs on defaults and tests something other than the adopter.
    """
    contract = load_yaml_document(contract_path)
    caller = load_yaml_document(caller_path)
    declared = _declared_inputs(contract)

    supplied: dict[str, object] = {}
    for job in caller['jobs'].values():
        uses = job.get('uses') or ''
        wanted = binds_as or contract_path.name
        if not uses.endswith(wanted) and f'/{wanted}@' not in uses:
            continue
        supplied.update(job.get('with') or {})

    unknown = sorted(set(supplied) - set(declared))
    if unknown:
        raise AdopterContractMismatch(
            f'{caller_path.name} passes inputs {contract_path.name} does not declare: '
            f'{unknown}')

    bindings: dict[str, object] = {}
    for name, spec in declared.items():
        if name in supplied:
            bindings[f'inputs.{name}'] = supplied[name]
        elif 'default' in spec:
            bindings[f'inputs.{name}'] = spec['default']
        elif spec.get('required'):
            raise AdopterContractMismatch(
                f'{caller_path.name} omits required input {name!r} of {contract_path.name}')
        else:
            bindings[f'inputs.{name}'] = _implicit_default(contract_path, name, spec)

    return bindings


# GitHub's implicit value for an optional `workflow_call` input the caller
# omits and the contract gives no default. A `number` is 0, not the empty
# string an earlier version of this returned — that would have made an
# unsupplied numeric input compare unequal to every number a guard could test
# it against, and the guard would then be modelled as false for a reason the
# contract never expressed.
_IMPLICIT_DEFAULTS = {'boolean': False, 'number': 0, 'string': ''}


def _implicit_default(contract_path: Path, name: str, spec: dict) -> object:
    declared = spec.get('type')
    if declared not in _IMPLICIT_DEFAULTS:
        raise AdopterContractMismatch(
            f'{contract_path.name} declares optional input {name!r} with type '
            f'{declared!r} and no default; this harness does not know what '
            'value the runtime would supply, and guessing is how a model stops '
            'asserting anything')
    return _IMPLICIT_DEFAULTS[declared]


def callers_for(contract_path: Path) -> list[Path]:
    """Return the synthetic adopters that call `contract_path`.

    Fixtures are routed by the contract their `uses:` names rather than run
    against every contract: the protected variant declares required inputs the
    unprotected one does not, so a fixture written for one binds nothing for
    the other.
    """
    matched = []
    for caller_path in sorted(ADOPTERS.glob('*.yml')):
        caller = load_yaml_document(caller_path)
        for job in caller['jobs'].values():
            if f'/{contract_path.name}@' in (job.get('uses') or ''):
                matched.append(caller_path)
                break
    if not matched:
        raise AdopterContractMismatch(
            f'no synthetic adopter calls {contract_path.name}; a contract with '
            'no fixture is a contract this harness does not cover')
    return matched
