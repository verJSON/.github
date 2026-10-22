#!/usr/bin/env python3
"""Behavioral contract for the runner-lane catalog (ADR 0240, extends 0234/0238).

The frozen LEGACY_* strings below are the pre-profile spellings, kept here as an
independent oracle: the catalog and the analyzer allowlist are generated from one
source, so agreement between them proves consistency, not safety. Equivalence to
these literals, and the negative scenarios, are what prove behavior.

The evaluator is Node's `vm`, the subset the existing hosted-lane tests use. It
compares strings case-sensitively where Actions does not, so every context here
spells the owner exactly as the expression does.
"""

from __future__ import annotations

import itertools
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import runner_lane_catalog as catalog  # noqa: E402

PERSISTENT = '["self-hosted","general"]'
EPHEMERAL = '["ubuntu-24.04"]'
LEGACY_CALLER_RUNNER = (
    "inputs.runner != '' && fromJSON(inputs.runner) || github.repository_owner != 'Verjson' "
    "&& 'ubuntu-24.04' || github.event.repository.private == true && "
    "fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') || "
    "fromJSON(vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]')"
)
LEGACY_SECRETLESS_FIRST = (
    "(inputs.secretless-pr || inputs.secretless-trusted-ref) && "
    "fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]') || " + LEGACY_CALLER_RUNNER
)

EVALUATOR = r"""
const vm = require('node:vm');
const cases = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const results = cases.map(([body, context]) => {
  const sandbox = vm.createContext(Object.assign(Object.create(null), context, {
    fromJSON: (v) => JSON.parse(v),
  }));
  const out = vm.runInContext(`(${body.replace(/inputs\.([A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)+)/g, "inputs['$1']")})`, sandbox, { timeout: 5000 });
  return out === undefined ? null : out;
});
process.stdout.write(JSON.stringify(results));
"""
_script = Path(tempfile.mkdtemp()) / "eval.js"
_script.write_text(EVALUATOR)


def context_for(**ctx) -> dict:
    return {
        "github": {"repository_owner": ctx.get("owner", "Verjson"),
                   "event": {"repository": {"private": ctx.get("private")}}},
        "inputs": {"runner": ctx.get("runner", ""),
                   "secretless-pr": ctx.get("secretless_pr", False),
                   "secretless-trusted-ref": ctx.get("secretless_ref", False)},
        "vars": dict(ctx.get("vars", {})),
    }


def resolve_many(cases: list[tuple[str, dict]]) -> list:
    """Evaluate every (expression, context kwargs) pair in one node process."""
    payload = json.dumps([[e, context_for(**c)] for e, c in cases])
    done = subprocess.run(["node", str(_script)], input=payload,
                          capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def resolve(expression: str, **ctx) -> object:
    return resolve_many([(expression, ctx)])[0]


def lane_variable_sets():
    names = ("CI_LANE_TRUSTED", "CI_LANE_UNTRUSTED", "CI_LANE_FALLBACK")
    for present in itertools.product((False, True), repeat=3):
        yield {n: PERSISTENT for n, on in zip(names, present) if on}
    yield {"CI_LANE_TRUSTED": PERSISTENT, "CI_LANE_UNTRUSTED": EPHEMERAL, "CI_LANE_FALLBACK": PERSISTENT}


FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def test_builtin_profile_is_identical_to_the_legacy_spelling() -> None:
    profiles = ({}, {"CI_PROFILE_OWNER": ""}, {"CI_PROFILE_OWNER": "Verjson"})
    contexts = []
    for shape, legacy in (("caller-runner", LEGACY_CALLER_RUNNER),
                          ("secretless-first", LEGACY_SECRETLESS_FIRST)):
        for owner, private, runner, lanes, profile, sl_pr, sl_ref in itertools.product(
            ("Verjson", "acme"), (True, False, None), ("", PERSISTENT, EPHEMERAL),
            list(lane_variable_sets()), profiles, (False, True), (False, True),
        ):
            if shape == "caller-runner" and (sl_pr or sl_ref):
                continue
            # A non-Verjson owner under the default profile is the foreign case.
            contexts.append((shape, legacy, dict(
                owner=owner, private=private, runner=runner, secretless_pr=sl_pr,
                secretless_ref=sl_ref, vars={**lanes, **profile})))
    new = resolve_many([(catalog.expression(s), c) for s, _, c in contexts])
    old = resolve_many([(legacy, c) for _, legacy, c in contexts])
    check(f"built-in profile equals legacy over {len(contexts)} cases",
          [(s, c) for (s, _, c), n, o in zip(contexts, new, old) if n != o], [])


def test_secretless_modes_beat_a_persistent_runner_input() -> None:
    for sl in ({"secretless_pr": True}, {"secretless_ref": True}):
        for profile in ({}, {"CI_PROFILE_OWNER": "acme"}):
            owner = "acme" if profile else "Verjson"
            got = resolve(catalog.expression("secretless-first"), owner=owner, private=True,
                          runner=PERSISTENT, vars={"CI_LANE_TRUSTED": PERSISTENT, **profile}, **sl)
            check(f"secretless precedence {sl} {profile}", got, ["ubuntu-24.04"])


def test_foreign_owner_without_a_profile_is_hosted() -> None:
    got = resolve(catalog.expression("caller-runner"), owner="acme", private=True,
                  vars={"CI_LANE_TRUSTED": PERSISTENT})
    check("foreign owner hosted", got, "ubuntu-24.04")


def test_custom_profile_uses_its_own_lanes() -> None:
    vars_ = {"CI_PROFILE_OWNER": "acme", "CI_LANE_TRUSTED": '["acme-trusted"]',
             "CI_LANE_UNTRUSTED": '["acme-ephemeral"]'}
    expr = catalog.expression("caller-runner")
    check("custom private", resolve(expr, owner="acme", private=True, vars=vars_), ["acme-trusted"])
    check("custom public", resolve(expr, owner="acme", private=False, vars=vars_), ["acme-ephemeral"])


def test_custom_profile_never_sends_untrusted_events_to_the_fallback() -> None:
    # Adversarial finding (gpt-6-astra, gpt-6.1-sol): profile ownership is not
    # evidence that a public or unclassified event is trusted. Every shape.
    vars_ = {"CI_PROFILE_OWNER": "acme", "CI_LANE_FALLBACK": PERSISTENT}
    for shape in catalog.SHAPES:
        for private, secretless in itertools.product((False, None), (False, True)):
            got = resolve(catalog.expression(shape), owner="acme", private=private,
                          secretless_pr=secretless, vars=vars_)
            check(f"custom fallback {shape} private={private} secretless={secretless}",
                  got, ["ubuntu-24.04"])


def test_secretless_beats_a_runner_input_under_a_custom_profile_with_a_fallback() -> None:
    vars_ = {"CI_PROFILE_OWNER": "acme", "CI_LANE_FALLBACK": PERSISTENT, "CI_LANE_TRUSTED": PERSISTENT}
    for flag in ("secretless_pr", "secretless_ref"):
        got = resolve(catalog.expression("secretless-first"), owner="acme", private=True,
                      runner=PERSISTENT, vars=vars_, **{flag: True})
        check(f"secretless custom {flag}", got, ["ubuntu-24.04"])


def test_a_profile_that_does_not_match_the_owner_stays_hosted() -> None:
    got = resolve(catalog.expression("caller-runner"), owner="acme", private=True,
                  vars={"CI_PROFILE_OWNER": "other", "CI_LANE_TRUSTED": PERSISTENT})
    check("mismatched profile hosted", got, "ubuntu-24.04")


def test_generator_check_rejects_a_hand_edited_runs_on() -> None:
    gen = ROOT / "scripts/gen-runner-lane.py"
    with tempfile.TemporaryDirectory() as tmp:
        workflow = Path(tmp) / "w.yml"
        stamped = "jobs:\n  a:\n    # runner-lane: caller-runner\n" + catalog.runs_on_line("caller-runner", "    ") + "\n"
        for text, want in ((stamped, 0),
                           (stamped.replace("inputs.runner != ''", "inputs.runner == ''"), 1),
                           ("jobs:\n  a:\n    # runner-lane: nope\n    runs-on: x\n", 1),
                           ("jobs:\n  a:\n    # runner-lane: caller-runner\n    steps: []\n", 1),
                           ("jobs:\n  a:\n    # runner-lane:\n    runs-on: self-hosted\n", 1),
                           ("jobs:\n  a:\n    # runner-lane: caller-runner # why\n    runs-on: self-hosted\n", 1)):
            workflow.write_text(text)
            code = subprocess.run([sys.executable, str(gen), "--check", tmp],
                                  capture_output=True, text=True).returncode
            check(f"generator --check on {text.splitlines()[-1][:40]!r}", code, want)


def test_generator_checks_yaml_suffix_workflows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "w.yaml").write_text(
            "jobs:\n  a:\n    # runner-lane: caller-runner\n    runs-on: self-hosted\n")
        code = subprocess.run([sys.executable, str(ROOT / "scripts/gen-runner-lane.py"), "--check", tmp],
                              capture_output=True, text=True).returncode
        check("generator --check sees .yaml", code, 1)


def test_repository_workflows_are_stamped() -> None:
    code = subprocess.run([sys.executable, str(ROOT / "scripts/gen-runner-lane.py"), "--check"],
                          capture_output=True, text=True)
    check("repository stamped", code.returncode, 0)


def test_analyzer_allowlist_accepts_every_catalog_shape() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "hosted_selector_policy", ROOT / "scripts/ci-gate/hosted-selector-policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for shape in catalog.SHAPES:
        check(f"allowlist has {shape}",
              " ".join(catalog.expression(shape).split()) in module.REVIEWED_SELECTOR_EXPRESSIONS, True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    for failure in FAILURES[:20]:
        print("FAIL", failure, file=sys.stderr)
    print(f"{len(FAILURES)} failure(s)")
    sys.exit(1 if FAILURES else 0)
