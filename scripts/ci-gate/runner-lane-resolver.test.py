#!/usr/bin/env python3
"""Behavioral contract for the runner-lane catalog (ADR 0240, extends 0234/0238).

The frozen LEGACY_* strings below are the pre-profile spellings, kept here as an
independent oracle: the catalog and the analyzer allowlist are generated from one
source, so agreement between them proves consistency, not safety. Equivalence to
these literals, and the negative scenarios, are what prove behavior.

The evaluator uses Node's `vm` with Actions-compatible case-insensitive string
comparisons and JSON truthiness for this catalog's expression subset.
"""

from __future__ import annotations

import itertools
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

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
# Pre-profile selector retained as the migration-compatibility oracle when
# both secretless inputs are false. The fail-closed prefix has its own oracle.
LEGACY_SECRETLESS_FIRST = (
    "(inputs.secretless-pr || inputs.secretless-trusted-ref) && "
    "fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]') || " + LEGACY_CALLER_RUNNER
)
FAIL_CLOSED_SECRETLESS_PREFIX = (
    "(inputs.secretless-pr || inputs.secretless-trusted-ref) && "
    "needs.eligibility.result == 'success' && "
    "fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]') || "
    "(inputs.secretless-pr || inputs.secretless-trusted-ref) && 'ubuntu-24.04'"
)

EVALUATOR = r"""
const vm = require('node:vm');
const cases = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const actionsEqual = (left, right) => typeof left === 'string' && typeof right === 'string'
  ? left.toLowerCase() === right.toLowerCase()
  : left === right;
const actionsNotEqual = (left, right) => !actionsEqual(left, right);
const results = cases.map(([body, context]) => {
  const sandbox = vm.createContext(Object.assign(Object.create(null), context, {
    fromJSON: (value) => JSON.parse(value),
    actionsEqual,
    actionsNotEqual,
  }));
  const expression = body
    .replace(/inputs\.runner != ''/g, "actionsNotEqual(inputs.runner, '')")
    .replace(/github\.repository_owner != 'Verjson'/g, "actionsNotEqual(github.repository_owner, 'Verjson')")
    .replace(/github\.repository_owner != \(vars\.CI_PROFILE_OWNER \|\| 'Verjson'\)/g, "actionsNotEqual(github.repository_owner, (vars.CI_PROFILE_OWNER || 'Verjson'))")
    .replace(/\(vars\.CI_PROFILE_OWNER \|\| 'Verjson'\) != 'Verjson'/g, "actionsNotEqual((vars.CI_PROFILE_OWNER || 'Verjson'), 'Verjson')")
  .replace(/github\.event_name == 'pull_request'/g, "actionsEqual(github.event_name, 'pull_request')")
  .replace(/github\.event_name == 'pull_request_review'/g, "actionsEqual(github.event_name, 'pull_request_review')")
  .replace(/github\.event_name == 'pull_request_review_comment'/g, "actionsEqual(github.event_name, 'pull_request_review_comment')")
  .replace(/github\.event_name == 'merge_group'/g, "actionsEqual(github.event_name, 'merge_group')")
  .replace(/github\.event_name == 'pull_request_target'/g, "actionsEqual(github.event_name, 'pull_request_target')")
    .replace(/github\.event\.repository\.private == true/g, "actionsEqual(github.event.repository.private, true)")
    .replace(/github\.event\.repository\.private != true/g, "actionsNotEqual(github.event.repository.private, true)")
    .replace(/needs\.eligibility\.result == 'success'/g, "actionsEqual(needs.eligibility.result, 'success')")
    .replace(/inputs\.([A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)+)/g, "inputs['$1']");
  if (/(?:==|!=|[<>])/.test(expression)) {
    throw new Error('unmodeled comparison operator in runner expression');
  }
  const out = vm.runInContext(`(${expression})`, sandbox, { timeout: 5000 });
  return out === undefined ? null : out;
});
process.stdout.write(JSON.stringify(results));
"""
_script = Path(tempfile.mkdtemp()) / "eval.js"
_script.write_text(EVALUATOR)


def context_for(**ctx) -> dict:
    eligibility = {}
    if "eligibility_result" in ctx:
        eligibility["result"] = ctx["eligibility_result"]
    return {
        "github": {"event_name": ctx.get("event_name", "push"),
                   "repository_owner": ctx.get("owner", "Verjson"),
                   "event": {"repository": {"private": ctx.get("private")}}},
        "inputs": {"runner": ctx.get("runner", ""),
                   "secretless-pr": ctx.get("secretless_pr", False),
                   "secretless-trusted-ref": ctx.get("secretless_ref", False)},
        "needs": {"eligibility": eligibility},
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
    profiles = ({}, {"CI_PROFILE_OWNER": ""}, {"CI_PROFILE_OWNER": "Verjson"},
                {"CI_PROFILE_OWNER": "verjson"})
    contexts = []
    for shape, legacy in (
        ("caller-runner", LEGACY_CALLER_RUNNER),
        ("secretless-first", LEGACY_SECRETLESS_FIRST),
        ("secretless-first-legacy-tail", LEGACY_SECRETLESS_FIRST),
    ):
        for owner, private, runner, lanes, profile, sl_pr, sl_ref in itertools.product(
            ("Verjson", "VERJSON", "acme"), (True, False, None), ("", PERSISTENT, EPHEMERAL),
            list(lane_variable_sets()), profiles, (False, True), (False, True),
        ):
            if sl_pr or sl_ref:
                continue
            # A non-Verjson owner under the default profile is the foreign case.
            contexts.append((shape, legacy, dict(
                owner=owner, private=private, runner=runner, secretless_pr=sl_pr,
                secretless_ref=sl_ref, vars={**lanes, **profile})))
    new = resolve_many([(catalog.expression(s), c) for s, _, c in contexts])
    old = resolve_many([(legacy, c) for _, legacy, c in contexts])
    check(f"built-in profile equals legacy over {len(contexts)} cases",
          [(s, c) for (s, _, c), n, o in zip(contexts, new, old) if n != o], [])


def test_secretless_routes_fail_closed_for_falsy_lanes_and_ineligible_results() -> None:
    shapes = (
        ("secretless-first", catalog._CALLER_RUNNER_PROFILE_TAIL),
        ("secretless-first-legacy-tail", LEGACY_CALLER_RUNNER),
    )
    cases = (
        ("unset lane", {}, "success", ["ubuntu-24.04"]),
        ("empty lane", {"CI_LANE_UNTRUSTED": ""}, "success", ["ubuntu-24.04"]),
        ("configured lane", {"CI_LANE_UNTRUSTED": '["ubuntu-22.04"]'},
         "success", ["ubuntu-22.04"]),
        ("JSON false lane", {"CI_LANE_UNTRUSTED": "false"}, "success", "ubuntu-24.04"),
        ("JSON zero lane", {"CI_LANE_UNTRUSTED": "0"}, "success", "ubuntu-24.04"),
        ("JSON null lane", {"CI_LANE_UNTRUSTED": "null"}, "success", "ubuntu-24.04"),
        ('JSON empty-string lane', {"CI_LANE_UNTRUSTED": '\"\"'},
         "success", "ubuntu-24.04"),
        ("malformed lane with failed eligibility", {"CI_LANE_UNTRUSTED": "not-json"},
         "failure", "ubuntu-24.04"),
        ("failed eligibility", {"CI_LANE_UNTRUSTED": EPHEMERAL},
         "failure", "ubuntu-24.04"),
        ("skipped eligibility", {"CI_LANE_UNTRUSTED": EPHEMERAL},
         "skipped", "ubuntu-24.04"),
        ("null eligibility result", {"CI_LANE_UNTRUSTED": EPHEMERAL},
         None, "ubuntu-24.04"),
        ("case-insensitive success", {"CI_LANE_UNTRUSTED": EPHEMERAL},
         "SUCCESS", ["ubuntu-24.04"]),
    )

    for shape, tail in shapes:
        expression = catalog.expression(shape)
        expected = (
            f"{catalog.PUBLIC_PR_HOSTED} || "
            f"({FAIL_CLOSED_SECRETLESS_PREFIX} || {tail})"
        )
        check(f"{shape} keeps the frozen fail-closed prefix and expected tail",
              expression, expected)
        for secretless_flag in ("secretless_pr", "secretless_ref"):
            for name, variables, eligibility_result, want in cases:
                got = resolve(
                    expression,
                    runner=PERSISTENT,
                    eligibility_result=eligibility_result,
                    vars=variables,
                    **{secretless_flag: True},
                )
                check(f"{shape} {secretless_flag} {name}", got, want)

            missing_result = resolve(
                expression,
                runner=PERSISTENT,
                vars={"CI_LANE_UNTRUSTED": EPHEMERAL},
                **{secretless_flag: True},
            )
            check(f"{shape} {secretless_flag} missing eligibility result",
                  missing_result, "ubuntu-24.04")

        non_secretless = resolve(
            expression,
            runner=PERSISTENT,
            vars={"CI_LANE_UNTRUSTED": EPHEMERAL},
        )
        check(f"{shape} leaves the non-secretless runner unchanged",
              non_secretless, ["self-hosted", "general"])


def test_custom_profile_secretless_failures_stay_hosted() -> None:
    variables = {
        "CI_PROFILE_OWNER": "acme",
        "CI_LANE_UNTRUSTED": PERSISTENT,
        "CI_LANE_FALLBACK": PERSISTENT,
    }
    for eligibility_result in ("failure", "skipped"):
        got = resolve(
            catalog.expression("secretless-first"),
            owner="acme",
            private=False,
            runner=PERSISTENT,
            secretless_pr=True,
            eligibility_result=eligibility_result,
            vars=variables,
        )
        check(f"custom profile {eligibility_result} eligibility stays hosted",
              got, "ubuntu-24.04")


def test_public_pull_request_events_preempt_every_caller_lane() -> None:
    variables = {
        "CI_PROFILE_OWNER": "Verjson",
        "CI_LANE_TRUSTED": PERSISTENT,
        "CI_LANE_UNTRUSTED": PERSISTENT,
        "CI_LANE_FALLBACK": PERSISTENT,
    }
    for shape in catalog.SHAPES:
        expression = catalog.expression(shape)
        for event_name in catalog.PUBLIC_PR_EVENTS:
            for private in (False, None):
                got = resolve(
                    expression,
                    event_name=event_name,
                    owner="Verjson",
                    private=private,
                    runner=PERSISTENT,
                    secretless_pr=True,
                    eligibility_result="success",
                    vars=variables,
                )
                check(f"{shape} public {event_name} private={private}", got, "ubuntu-24.04")

    private_run = resolve(
        catalog.expression("caller-runner"),
        event_name="pull_request",
        owner="Verjson",
        private=True,
        runner=PERSISTENT,
        vars=variables,
    )
    check("private pull_request keeps configured caller lane", private_run, ["self-hosted", "general"])


def test_evaluator_rejects_unmodeled_comparison_operators() -> None:
    cases = (
        ("equality", "github.event.repository.visibility == 'public'"),
        ("inequality", "inputs.timeout-minutes != '10'"),
        ("less than", "inputs.timeout-minutes < 10"),
        ("less than or equal", "inputs.timeout-minutes <= 10"),
        ("greater than", "inputs.timeout-minutes > 10"),
        ("greater than or equal", "inputs.timeout-minutes >= 10"),
    )
    for label, expression in cases:
        try:
            resolve(expression)
        except subprocess.CalledProcessError as error:
            check(f"evaluator rejects unmodeled {label} comparison",
                  "unmodeled comparison operator" in (error.stderr or ""), True)
        else:
            check(f"evaluator rejects unmodeled {label} comparison", False, True)


def _runner_lane_site(source: str, job_name: str) -> tuple[str, str] | None:
    lines = source.splitlines()
    jobs_start = next((index for index, line in enumerate(lines) if line == "jobs:"), None)
    if jobs_start is None:
        return None
    job_start = next((index for index in range(jobs_start + 1, len(lines))
                      if lines[index] == f"  {job_name}:"), None)
    if job_start is None:
        return None
    job_end = next((index for index in range(job_start + 1, len(lines))
                    if re.fullmatch(r"  [A-Za-z0-9_-]+:", lines[index])), len(lines))
    job_lines = lines[job_start + 1:job_end]
    runs_on = [index for index, line in enumerate(job_lines)
               if re.match(r"^    runs-on:", line)]
    if len(runs_on) != 1 or runs_on[0] == 0:
        return None
    marker = re.fullmatch(r"    # runner-lane: (\S+)", job_lines[runs_on[0] - 1])
    if marker is None:
        return None
    return marker.group(1), job_lines[runs_on[0]].removeprefix("    runs-on: ")


def _runner_lane_site_matches(source: str, job_name: str, shape: str) -> bool:
    site = _runner_lane_site(source, job_name)
    expected = (shape, catalog.runs_on_line(shape, "    ").strip().removeprefix("runs-on: "))
    return site == expected


def test_node_ci_runner_lane_roles_are_explicit_on_both_workflows() -> None:
    roles = {"eligibility": "caller-runner", "build-test": "secretless-first"}
    workflow_paths = (
        ROOT / ".github/workflows/node-ci.yml",
        ROOT / ".github/workflows/node-ci-protected.yml",
    )
    for path in workflow_paths:
        source = path.read_text()
        document = yaml.safe_load(source) or {}
        jobs = document.get("jobs", {})
        for job_name, shape in roles.items():
            check(f"{path.name} {job_name} marker and selector",
                  _runner_lane_site_matches(source, job_name, shape), True)
            check(f"{path.name} {job_name} YAML selector",
                  jobs[job_name].get("runs-on"),
                  "$" + "{{ " + catalog.expression(shape) + " }}")
    workflow_dir = ROOT / ".github/workflows"
    legacy_tail_sites = {
        path.name
        for path in {*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml")}
        if "# runner-lane: secretless-first-legacy-tail" in path.read_text()
    }
    check("no workflow retains a legacy-tail site", legacy_tail_sites, set())


def test_runner_lane_site_rejects_missing_misplaced_and_wrong_role_markers() -> None:
    valid = (
        "jobs:\n  eligibility:\n    # runner-lane: caller-runner\n"
        + catalog.runs_on_line("caller-runner", "    ") + "\n"
        + "  build-test:\n    # runner-lane: secretless-first\n"
        + catalog.runs_on_line("secretless-first", "    ") + "\n"
    )
    mutations = (
        (valid.replace("    # runner-lane: caller-runner\n", "", 1), "eligibility"),
        (valid.replace("    # runner-lane: caller-runner\n    runs-on:",
                       "    # runner-lane: caller-runner\n    steps: []\n    runs-on:", 1),
         "eligibility"),
        (valid.replace("    # runner-lane: caller-runner", "    # runner-lane: secretless-first", 1),
         "eligibility"),
        (valid.replace("    # runner-lane: secretless-first", "    # runner-lane: caller-runner", 1),
         "build-test"),
    )
    expected_roles = {"eligibility": "caller-runner", "build-test": "secretless-first"}
    for source, job_name in mutations:
        check(f"reject invalid {job_name} runner-lane marker",
              _runner_lane_site_matches(source, job_name, expected_roles[job_name]), False)


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


def test_custom_profile_shapes_do_not_send_untrusted_events_to_the_fallback() -> None:
    # The legacy-tail shape preserves built-in fallback semantics for migration;
    # current Node CI uses the profile-aware shapes and strict custom untrusted lane.
    vars_ = {"CI_PROFILE_OWNER": "acme", "CI_LANE_FALLBACK": PERSISTENT}
    for shape in ("caller-runner", "secretless-first"):
        for private in (False, None):
            got = resolve(
                catalog.expression(shape),
                owner="acme",
                private=private,
                secretless_pr=False,
                eligibility_result="success",
                vars=vars_,
            )
            check(f"custom fallback {shape} private={private}", got, ["ubuntu-24.04"])


def test_secretless_beats_a_runner_input_under_a_custom_profile_with_a_fallback() -> None:
    vars_ = {"CI_PROFILE_OWNER": "acme", "CI_LANE_FALLBACK": PERSISTENT, "CI_LANE_TRUSTED": PERSISTENT}
    for flag in ("secretless_pr", "secretless_ref"):
        got = resolve(catalog.expression("secretless-first"), owner="acme", private=True,
                      runner=PERSISTENT, vars=vars_, eligibility_result="success",
                      **{flag: True})
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
        expression = " ".join(catalog.expression(shape).split())
        allowed = (
            expression in module.REVIEWED_SELECTOR_EXPRESSIONS
            or module.is_public_pr_hosted_wrapper(
                expression, module.REVIEWED_SELECTOR_EXPRESSIONS
            )
        )
        check(f"allowlist has {shape}",
              allowed, True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    for failure in FAILURES[:20]:
        print("FAIL", failure, file=sys.stderr)
    print(f"{len(FAILURES)} failure(s)")
    sys.exit(1 if FAILURES else 0)
