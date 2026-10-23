#!/usr/bin/env python3
"""Execute node-ci's secretless hosted-lane routing decision against stubbed inputs.

Both secretless modes run a lifecycle rebuild and candidate script sandbox that
exit unless the runner is GitHub-hosted, so `build-test` takes the hosted
untrusted lane ahead of a caller's `runner` input. `eligibility` keeps the
caller's (self-hosted) route and must refuse a secretless call early, before
dependency acquisition, only when that hosted lane is not actually hosted.

Each scenario evaluates every eligibility guard step's `if:` and `env:` with the
same expression subset GitHub Actions uses, then runs the step's `run:` script,
so the verdict is the workflow's own, not a re-implementation of it.

Locale: the guard runs under the locale this host's runtime already has, the way
a runner executes it, and again under a collating UTF-8 locale when one is
installed. The harness never requests a locale the host lacks, because bash
warns at startup (`setlocale: LC_ALL: cannot change locale`) for any missing
locale, whatever the script does. Hosted runners do not ship en_US.UTF-8, so the
collating pass runs only where it can. The structural check that the guard
spells out its character classes holds on every host.
"""

from __future__ import annotations

import copy
import functools
import json
import re
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ROOT / ".github/workflows/node-ci.yml",
    ROOT / ".github/workflows/node-ci-protected.yml",
)

EVALUATOR = r"""
const vm = require('node:vm');
const [, , raw, contextJson] = process.argv;
const context = JSON.parse(contextJson);
const body = raw.trim().replace(/^\$\{\{/, '').replace(/\}\}$/, '')
  .replace(/inputs\.([A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)+)/g, "inputs['$1']");
const sandbox = vm.createContext(Object.assign(Object.create(null), context, {
  fromJSON: (value) => JSON.parse(value),
}));
const resolved = vm.runInContext(`(${body})`, sandbox, { timeout: 5000 });
process.stdout.write(JSON.stringify(resolved === undefined ? null : resolved));
"""


def evaluate(expression: str, context: dict) -> object:
    # Memoized: scenarios and mutants repeat the same few expressions, and a
    # node start per evaluation pushed this suite past CI's 60s row budget.
    return json.loads(_evaluate(expression, json.dumps(context, sort_keys=True)))


@functools.lru_cache(maxsize=None)
def _evaluate(expression: str, context_json: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(EVALUATOR)
        script = handle.name
    try:
        result = subprocess.run(
            ["node", script, expression, context_json],
            capture_output=True, text=True, check=True,
        )
    finally:
        os.unlink(script)
    return result.stdout


def interpolate(value: object, context: dict) -> str:
    text = str(value)
    if text.startswith("${{") and text.endswith("}}"):
        resolved = evaluate(text, context)
        return "" if resolved is None else str(resolved)
    return text


def load(workflow: Path) -> dict:
    return yaml.safe_load(workflow.read_text(encoding="utf-8"))


def eligibility_guards(document: dict) -> list[dict]:
    steps = document["jobs"]["eligibility"]["steps"]
    # The stability-days `check` step calls the GitHub API; every step before
    # it is a local guard that must decide from inputs and variables alone.
    guards = []
    for step in steps:
        if step.get("id") == "check":
            break
        guards.append(step)
    return guards


# The runtime's own locale variables, passed through as a runner would. Nothing
# here is invented: an unset variable stays unset.
RUNTIME_LOCALE = ("LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_COLLATE")
# Locales whose collation folds non-ASCII digits into a [0-9] range.
COLLATING_LOCALES = ("en_US.UTF-8", "en_US.utf8")


def runtime_locale_env() -> dict[str, str]:
    return {name: os.environ[name] for name in RUNTIME_LOCALE if name in os.environ}


def installed_collating_locale() -> dict[str, str] | None:
    """A collating locale bash can switch to without a startup warning, if any."""
    for candidate in COLLATING_LOCALES:
        probe = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", ":"],
            env={"PATH": os.environ["PATH"], "LANG": candidate, "LC_ALL": candidate},
            capture_output=True, text=True,
        )
        if probe.returncode == 0 and not probe.stderr:
            return {"LANG": candidate, "LC_ALL": candidate}
    return None


LOCALES = (("runtime", runtime_locale_env()),) + (
    (("collating", installed_collating_locale()),)
    if installed_collating_locale() is not None else ()
)


def run_eligibility_guards(document: dict, scenario: dict,
                           locale: dict[str, str]) -> tuple[int, str]:
    context = {
        "inputs": {
            "secretless-pr": scenario["mode"] == "pr",
            "secretless-trusted-ref": scenario["mode"] == "trusted-ref",
            "secretless-compatibility-ranges": scenario.get("ranges", ""),
        },
        "runner": {"environment": scenario["eligibility_runner"]},
        "vars": {"CI_LANE_UNTRUSTED": scenario["untrusted_lane"]},
        "github": {"repository_owner": scenario.get("owner", "Verjson")},
    }
    for step in eligibility_guards(document):
        condition = step.get("if")
        if condition is not None and not evaluate(str(condition), context):
            continue
        env = {"PATH": os.environ["PATH"], **locale}
        for name, value in (step.get("env") or {}).items():
            env[name] = interpolate(value, context)
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
            env=env, capture_output=True, text=True,
        )
        if result.returncode != 0:
            return result.returncode, result.stdout + result.stderr
    return 0, ""


COMPAT_RANGES = '{"package":"@verjson/cli-projects","ranges":[">=1.0.0 <2.0.0"],"script":"compat"}'

SCENARIOS = (
    {
        "label": "trusted-ref compatibility on a self-hosted eligibility runner proceeds when the build lane is hosted",
        "mode": "trusted-ref", "ranges": COMPAT_RANGES,
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04"]',
        "admitted": True,
    },
    {
        "label": "trusted-ref is refused before acquisition when the build lane is self-hosted, even from a hosted eligibility runner",
        "mode": "trusted-ref", "ranges": COMPAT_RANGES,
        "eligibility_runner": "github-hosted", "untrusted_lane": '["self-hosted","general"]',
        "admitted": False,
    },
    {
        "label": "secretless-pr is refused before acquisition when the build lane is self-hosted",
        "mode": "pr",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["self-hosted","general"]',
        "admitted": False,
    },
    {
        "label": "secretless-pr proceeds on the hosted build lane",
        "mode": "pr",
        "eligibility_runner": "github-hosted", "untrusted_lane": '["ubuntu-24.04"]',
        "admitted": True,
    },
    {
        "label": "trusted-ref without compatibility ranges is guarded too, because every secretless build needs a hosted runner",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["self-hosted","general"]',
        "admitted": False,
    },
    {
        "label": "an unset lane variable takes the portable hosted default and proceeds",
        "mode": "trusted-ref", "ranges": COMPAT_RANGES,
        "eligibility_runner": "self-hosted", "untrusted_lane": "",
        "admitted": True,
    },
    {
        "label": "a lane given as a bare JSON string of a hosted image proceeds",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '"ubuntu-24.04"',
        "admitted": True,
    },
    {
        "label": "ubuntu-latest is a standard hosted Linux image and proceeds",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-latest"]',
        "admitted": True,
    },
    {
        "label": "ubuntu-22.04 matches the YY.MM image shape and proceeds",
        "mode": "pr",
        "eligibility_runner": "github-hosted", "untrusted_lane": '["ubuntu-22.04"]',
        "admitted": True,
    },
    {
        "label": "a version without the YY.MM dot is refused",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-2404"]',
        "admitted": False,
    },
    {
        "label": "a point-release suffix is refused: hosted labels name YY.MM only",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04.1"]',
        "admitted": False,
    },
    {
        "label": "a trailing non-digit after YY.MM is refused",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-99.99x"]',
        "admitted": False,
    },
    {
        "label": "a one-digit month is refused",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.4"]',
        "admitted": False,
    },
    {
        "label": "a bare-dot version is refused",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-."]',
        "admitted": False,
    },
    {
        "label": 'a vetted larger x64 hosted label (ubuntu-24.04-4-cores) proceeds so an adopter can trade rate for wall time',
        "mode": "trusted-ref", "owner": "adopter",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04-4-cores"]',
        "admitted": True,
    },
    {
        "label": 'ubuntu-latest-8-cores, a vetted larger label on the latest image, proceeds',
        "mode": "trusted-ref", "owner": "adopter",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-latest-8-cores"]',
        "admitted": True,
    },
    {
        "label": 'a bare JSON string of a vetted larger label proceeds',
        "mode": "trusted-ref", "owner": "adopter",
        "eligibility_runner": "self-hosted", "untrusted_lane": '"ubuntu-22.04-64-cores"',
        "admitted": True,
    },
    {
        "label": 'an unvetted core count is refused',
        "mode": "trusted-ref", "owner": "adopter",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04-3-cores"]',
        "admitted": False,
    },
    {
        "label": 'a zero-padded core count is refused',
        "mode": "trusted-ref", "owner": "adopter",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04-04-cores"]',
        "admitted": False,
    },
    {
        "label": 'a larger label with the arm suffix is refused',
        "mode": "trusted-ref", "owner": "adopter",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04-4-cores-arm"]',
        "admitted": False,
    },
    {
        "label": 'a larger label with an arbitrary suffix is refused',
        "mode": "trusted-ref", "owner": "adopter",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04-4-cores-gpu"]',
        "admitted": False,
    },
    {
        "label": 'a larger label without the dash before cores is refused',
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04-4cores"]',
        "admitted": False,
    },
    {
        "label": 'a larger label mixed with a self-hosted label is refused',
        "mode": "trusted-ref", "owner": "adopter",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04-4-cores","self-hosted"]',
        "admitted": False,
    },
    {
        "label": 'an adopter lane of 16 cores proceeds',
        "mode": "trusted-ref", "owner": 'adopter',
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04-16-cores"]',
        "admitted": True,
    },
    {
        "label": 'an adopter lane of 32 cores proceeds',
        "mode": "trusted-ref", "owner": 'adopter',
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-latest-32-cores"]',
        "admitted": True,
    },
    {
        "label": "the organization's own lane refuses a larger label: a label cannot prove hosted origin and the reconciler pins the standard image",
        "mode": "trusted-ref", "owner": 'Verjson',
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04-4-cores"]',
        "admitted": False,
    },
    {
        "label": 'a self-hosted runner carrying only a larger-size label is refused for the organization',
        "mode": "trusted-ref", "owner": 'Verjson',
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-latest-8-cores"]',
        "admitted": False,
    },
    {
        "label": "the organization's canonical-case login (verJSON) still refuses a larger label",
        "mode": "trusted-ref", "owner": "verJSON",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04-4-cores"]',
        "admitted": False,
    },
    {
        "label": "an all-caps organization login still refuses a larger label",
        "mode": "pr", "owner": "VERJSON",
        "eligibility_runner": "github-hosted", "untrusted_lane": '["ubuntu-latest-8-cores"]',
        "admitted": False,
    },
    {
        "label": "the arm64 image is refused: the sandbox provisioning is proven on the standard x64 image only",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04-arm"]',
        "admitted": False,
    },
    {
        "label": "a hosted image mixed with a self-hosted label is refused",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-24.04","self-hosted"]',
        "admitted": False,
    },
    {
        "label": "a self-hosted runner merely labelled like a hosted image is refused",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["self-hosted","ubuntu-24.04"]',
        "admitted": False,
    },
    {
        "label": "a hosted non-Linux image is refused, because the sandbox is Linux-only",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["macos-15"]',
        "admitted": False,
    },
    {
        "label": "a hosted Windows image is refused",
        "mode": "pr",
        "eligibility_runner": "github-hosted", "untrusted_lane": '["windows-latest"]',
        "admitted": False,
    },
    {
        "label": "an empty selector array is refused",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": "[]",
        "admitted": False,
    },
    {
        "label": "a non-JSON lane value is refused rather than guessed at",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": "ubuntu-24.04",
        "admitted": False,
    },
    {
        "label": "a lane value smuggling a second line is refused",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted",
        "untrusted_lane": '["ubuntu-24.04"]\n::warning::injected',
        "admitted": False,
    },
    {
        "label": "an image name with a unicode lookalike digit is refused",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["ubuntu-２4.04"]',
        "admitted": False,
    },
    {
        "label": "whitespace inside the selector is refused: the reconciled hosted value is exact bytes (ADR 0147)",
        "mode": "trusted-ref",
        "eligibility_runner": "self-hosted", "untrusted_lane": '[ "ubuntu-24.04" ]',
        "admitted": False,
    },
    {
        "label": "a credentialed (non-secretless) call is not subject to the hosted-lane guard",
        "mode": "",
        "eligibility_runner": "self-hosted", "untrusted_lane": '["self-hosted","general"]',
        "admitted": True,
    },
)

SECRETLESS_CONDITION = "inputs.secretless-pr || inputs.secretless-trusted-ref"
DIAGNOSTIC = (
    "::error::secretless build-test requires a GitHub-hosted Linux runner, but "
    "CI_LANE_UNTRUSTED does not select a single standard GitHub-hosted Linux image"
)


def hosted_lane_guard(document: dict) -> dict:
    matches = [
        step for step in eligibility_guards(document)
        if step.get("name") == "Require a GitHub-hosted secretless build lane"
    ]
    assert len(matches) == 1, f"expected one hosted-lane guard, found {len(matches)}"
    return matches[0]


def binding_violations(document: dict) -> list[str]:
    """The guard must judge exactly the lane and condition build-test routes on."""
    guard = hosted_lane_guard(document)
    lane = guard.get("env", {}).get("SECRETLESS_BUILD_LANE", "")
    if not (lane.startswith("${{") and lane.endswith("}}")):
        return [f"guard lane is not an expression: {lane!r}"]
    lane_expression = lane[3:-2].strip()
    expected_prefix = (
        f"${{{{ ({SECRETLESS_CONDITION}) && needs.eligibility.result == 'success' "
        f"&& fromJSON({lane_expression}) || ({SECRETLESS_CONDITION}) && 'ubuntu-24.04' || "
    )
    problems = []
    if not str(document["jobs"]["build-test"]["runs-on"]).startswith(expected_prefix):
        problems.append("build-test does not route secretless calls on the guarded lane first")
    if guard.get("if") != SECRETLESS_CONDITION:
        problems.append(f"guard condition drifted: {guard.get('if')!r}")
    if "inputs.runner" in str(document["jobs"]["build-test"]["runs-on"]).split(" || ")[0]:
        problems.append("the secretless term consults the caller's runner input")
    # Bracket ranges such as [0-9] follow the locale's collation (under
    # en_US.UTF-8 they match fullwidth digits), and pinning a locale inside the
    # step is itself locale-dependent. Classes are spelled out instead.
    run = str(guard.get("run", ""))
    ranges = RANGE_EXPRESSION.findall(run)
    if ranges:
        problems.append(f"guard uses locale-dependent bracket ranges: {ranges}")
    if re.search(r"\bLC_[A-Z]+=|\bLANG=", run):
        problems.append("guard sets a locale instead of spelling out its classes")
    return problems


RANGE_EXPRESSION = re.compile(r"\[\^?[^\]\s]*[0-9A-Za-z]-[0-9A-Za-z][^\]\s]*\]")


def scenario_failures(document: dict, name: str) -> list[str]:
    failures = []
    for (locale_name, locale), scenario in (
            (locale, scenario) for locale in LOCALES for scenario in SCENARIOS):
        status, output = run_eligibility_guards(document, scenario, locale)
        label = f"{name} [{locale_name} locale]: {scenario['label']}"
        if (status == 0) != scenario["admitted"]:
            failures.append(f"{label} (exit {status}: {output.strip()})")
        elif not scenario["admitted"] and output.strip() != DIAGNOSTIC:
            failures.append(f"{label}: unexpected diagnostic {output.strip()!r}")
    return failures


def mutants(document: dict):
    """Each mutant breaks one decision; the suite must notice every one."""
    def mutate(edit):
        clone = copy.deepcopy(document)
        edit(clone)
        return clone

    def guard_of(clone):
        return hosted_lane_guard(clone)

    yield "guard only covers trusted-ref", mutate(
        lambda d: guard_of(d).update({"if": "inputs.secretless-trusted-ref"}))
    yield "guard reverts to the eligibility runner proxy", mutate(
        lambda d: guard_of(d).update({"if": f"({SECRETLESS_CONDITION}) && runner.environment != 'github-hosted'"}))
    yield "guard reads the trusted lane", mutate(
        lambda d: guard_of(d)["env"].update({"SECRETLESS_BUILD_LANE": "${{ vars.CI_LANE_TRUSTED || '[\"ubuntu-24.04\"]' }}"}))
    yield "guard regex drops its anchors", mutate(
        lambda d: guard_of(d).update({"run": guard_of(d)["run"].replace("^${hosted_image}$", "${hosted_image}")}))
    yield "guard reverts to a collating [0-9] range", mutate(
        lambda d: guard_of(d).update({"run": guard_of(d)["run"].replace("[0123456789]", "[0-9]")}))
    yield "guard accepts any hosted image family", mutate(
        lambda d: guard_of(d).update({"run": guard_of(d)["run"].replace("ubuntu-(latest|", "[a-z]+-(latest|")}))
    yield "guard loosens the image version shape", mutate(
        lambda d: guard_of(d).update({"run": guard_of(d)["run"].replace(
            "[0123456789]{2}\\.[0123456789]{2}", "[0123456789.]+")}))
    yield "guard echoes the configured value", mutate(
        lambda d: guard_of(d).update({"run": guard_of(d)["run"].replace(
            "Linux image\"", "Linux image: ${SECRETLESS_BUILD_LANE}\"")}))
    yield "build-test routes only secretless-pr to the hosted lane", mutate(
        lambda d: d["jobs"]["build-test"].update({"runs-on": d["jobs"]["build-test"]["runs-on"].replace(
            f"({SECRETLESS_CONDITION})", "inputs.secretless-pr")}))
    yield "build-test lets the runner input win first", mutate(
        lambda d: d["jobs"]["build-test"].update({"runs-on": "${{ inputs.runner != '' && fromJSON(inputs.runner) || "
                                                  + d["jobs"]["build-test"]["runs-on"][4:]}))


def main() -> int:
    failures = 0
    for workflow in WORKFLOWS:
        document = load(workflow)
        problems = scenario_failures(document, workflow.name) + [
            f"{workflow.name}: {problem}" for problem in binding_violations(document)
        ]
        for problem in problems:
            print(f"not ok - {problem}")
        failures += len(problems)
        print(f"ok - {workflow.name}: {len(SCENARIOS)} eligibility scenarios under "
              f"{'/'.join(name for name, _ in LOCALES)} locale(s) and the build-lane binding"
              if not problems else f"not ok - {workflow.name}: {len(problems)} problem(s)")
        for label, mutant in mutants(document):
            try:
                caught = scenario_failures(mutant, workflow.name) or binding_violations(mutant)
            except AssertionError as error:
                caught = [str(error)]
            if caught:
                print(f"ok - {workflow.name}: mutant detected — {label}")
            else:
                failures += 1
                print(f"not ok - {workflow.name}: mutant survived — {label}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
