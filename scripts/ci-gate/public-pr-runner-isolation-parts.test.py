#!/usr/bin/env python3
"""The runner-isolation behavioral test runs as two actions-ci rows, each
selecting a part with RUNNER_ISOLATION_TEST_PART. This pins, cheaply, that the
parts partition its checks: every module-level statement that can report a
check (call ok or fail, directly or through a helper) is selected by exactly
one part, and the definitions the probes part replays report nothing. It also
pins that an unknown part fails rather than running nothing, and that the
manifest runs both parts and no unselected full run.

RUNNER_ISOLATION_PARTS_EMPIRICAL=1 also runs the full test and both parts
(several minutes) and compares the reported check labels as multisets."""

from __future__ import annotations

import ast
import collections
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEST = os.path.join(HERE, "public-pr-runner-isolation.test.py")
MANIFEST = os.path.join(HERE, "..", "actions-ci-groups.tsv")
PARTS = ("core", "probes")
DEFINITIONS = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)
failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'ok  ' if condition else 'FAIL'} - {label}")
    failures += 0 if condition else 1


def called_names(node: ast.AST) -> set[str]:
    return {call.func.id for call in ast.walk(node) if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)}


with open(TEST, encoding="utf-8") as handle:
    source = handle.read()
lines = source.split("\n")
tree = ast.parse(source)
names = {
    node.targets[0].id: ast.literal_eval(node.value)
    for node in tree.body
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id in ("PROBES_BEGIN", "PROBES_END")
}
check(set(names) == {"PROBES_BEGIN", "PROBES_END"}
      and lines.count(names.get("PROBES_BEGIN")) == 1 and lines.count(names.get("PROBES_END")) == 1,
      "the test file carries exactly one probes-part begin and end marker")
begin = lines.index(names["PROBES_BEGIN"]) + 1 if lines.count(names.get("PROBES_BEGIN")) == 1 else 0
end = lines.index(names["PROBES_END"]) + 1 if lines.count(names.get("PROBES_END")) == 1 else 0

# Functions that report a check: those calling ok or fail, to a fixpoint.
functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
reporting = {"ok", "fail"}
while True:
    grown = reporting | {name for name, node in functions.items() if called_names(node) & reporting}
    if grown == reporting:
        break
    reporting = grown
reporting -= {"run_part"}  # the selector, never replayed

selector_conditions = next(
    (ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
     and [ast.unparse(target) for target in node.targets] == ["SELECTOR_CONDITIONS"]), ())
selectors = [node for node in tree.body if isinstance(node, ast.If) and ast.unparse(node.test) in selector_conditions]
check(len(selector_conditions) == 2 and len(selectors) == 2,
      "the test file has exactly the two selector `if` statements SELECTOR_CONDITIONS names")
# Any other reference to TEST_PART could drop checks from both parts (round 14).
part_definition = [node for node in tree.body if isinstance(node, ast.Assign)
                   and [ast.unparse(target) for target in node.targets] == ["TEST_PART"]]
others = [node for node in tree.body if node not in selectors and node not in part_definition
          and any(isinstance(name, ast.Name) and name.id == "TEST_PART" for name in ast.walk(node))]
check(len(part_definition) == 1 and not others, "TEST_PART is referenced only by its definition and the selectors"
      + "".join(f" (line {node.lineno} also does)" for node in others))
# A part's exit status is `failures`: rebinding it anywhere but its one
# definition and fail() could hide a failure in the replayed part (round 14).
stores = [name for name in ast.walk(tree) if isinstance(name, ast.Name) and name.id == "failures"
          and isinstance(name.ctx, ast.Store)]
fail_function = next((node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "fail"), None)
allowed = {id(node.targets[0]) for node in tree.body if isinstance(node, ast.Assign)
           and [ast.unparse(target) for target in node.targets] == ["failures"]}
inside_fail = {id(name) for name in ast.walk(fail_function)} if fail_function else set()
rebinding = [name for name in stores if id(name) not in allowed and id(name) not in inside_fail]
declarers = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node is not fail_function
             and any(isinstance(statement, ast.Global) and "failures" in statement.names for statement in ast.walk(node))]
check(len(allowed) == 1 and not rebinding and not declarers,
      "the failure counter is bound only by its definition and fail()"
      + "".join(f" (line {node.lineno} also does)" for node in rebinding + declarers))
statements = [node for node in tree.body if node not in selectors]
inside = [node for node in statements if begin < node.lineno < end]
outside = [node for node in statements if not begin < node.lineno < end]
check(bool(inside) and any(called_names(node) & reporting for node in inside),
      f"the probes block holds checks ({len(inside)} statements)")


def eager_parts(node: ast.AST) -> list[ast.AST]:
    """What runs when a definition is executed, not when it is called: a
    function's decorators and defaults; a class's decorators, bases and body,
    recursively (round 14); an assignment's whole statement."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [*node.decorator_list, *node.args.defaults, *[d for d in node.args.kw_defaults if d]]
    if isinstance(node, ast.ClassDef):
        return [*node.decorator_list, *node.bases, *node.keywords,
                *[part for statement in node.body for part in eager_parts(statement)]]
    return [node]


replayed = [node for node in outside if isinstance(node, DEFINITIONS)
            and any(called_names(part) & reporting for part in eager_parts(node))]
check(not replayed, "no definition the probes part replays reports a check"
      + "".join(f" (line {node.lineno} does)" for node in replayed))
straddling = [node for node in statements
              if node.lineno <= begin <= (node.end_lineno or node.lineno) or node.lineno <= end <= (node.end_lineno or node.lineno)]
check(not straddling, "no module-level statement straddles a part marker")

for part, code in (("no-such-part", 2), ("", 2)):
    env = {**os.environ, "RUNNER_ISOLATION_TEST_PART": part}
    completed = subprocess.run([sys.executable, TEST], capture_output=True, text=True, env=env)
    reported = [line for line in completed.stdout.splitlines() if line.startswith(("ok   - ", "FAIL - "))]
    check(completed.returncode == code and not reported,
          f"RUNNER_ISOLATION_TEST_PART={part!r} exits {code} and runs no check")

with open(MANIFEST, encoding="utf-8") as handle:
    rows = [line.rstrip("\n").split("\t", 1)[-1] for line in handle if not line.startswith("#")]
for part in PARTS:
    wanted = f"RUNNER_ISOLATION_TEST_PART={part} python3 scripts/ci-gate/public-pr-runner-isolation.test.py"
    check(sum(row.endswith(wanted) for row in rows) == 1, f"the manifest runs the {part} part once")
check(not any(row.endswith("python3 scripts/ci-gate/public-pr-runner-isolation.test.py")
              and "RUNNER_ISOLATION_TEST_PART=" not in row for row in rows),
      "the manifest has no unselected full-run row")

if os.environ.get("RUNNER_ISOLATION_PARTS_EMPIRICAL") == "1":
    def labels(part: str | None) -> tuple[int, collections.Counter]:
        env = {key: value for key, value in os.environ.items() if key != "RUNNER_ISOLATION_TEST_PART"}
        if part:
            env["RUNNER_ISOLATION_TEST_PART"] = part
        completed = subprocess.run([sys.executable, TEST], capture_output=True, text=True, env=env)
        return completed.returncode, collections.Counter(
            line[7:] for line in completed.stdout.splitlines() if line.startswith(("ok   - ", "FAIL - ")))
    full_code, full = labels(None)
    runs = {part: labels(part) for part in PARTS}
    union = runs["core"][1] + runs["probes"][1]
    overlap = runs["core"][1] & runs["probes"][1]
    check(full_code == 0 and all(code == 0 for code, _ in runs.values()) and union == full and not overlap,
          f"empirically the parts partition the full run ({sum(full.values())} checks: core "
          f"{sum(runs['core'][1].values())}, probes {sum(runs['probes'][1].values())}, overlap {sum(overlap.values())})")

sys.exit(1 if failures else 0)
