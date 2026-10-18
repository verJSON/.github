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

statements = [node for node in tree.body
              if not (isinstance(node, ast.If) and "TEST_PART" in ast.unparse(node.test))]
inside = [node for node in statements if begin < node.lineno < end]
outside = [node for node in statements if not begin < node.lineno < end]
check(bool(inside) and any(called_names(node) & reporting for node in inside),
      f"the probes block holds checks ({len(inside)} statements)")
replayed = [node for node in outside if isinstance(node, DEFINITIONS) and not isinstance(node, ast.FunctionDef)
            and not isinstance(node, ast.ClassDef) and called_names(node) & reporting]
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
