#!/usr/bin/env python3
"""The release-caller credential-isolation test runs as one actions-ci row per
part, each selecting it with RELEASE_CALLER_ISOLATION_PART. This pins, cheaply,
that the parts partition the test's own case space: their union is every case,
no two overlap, and none is empty. It also pins that an unset part runs every
case, that an unknown part exits 2 and runs nothing, and that the manifest runs
each part exactly once and has no unselected full-run row."""

from __future__ import annotations

import importlib.util
import itertools
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
TEST = HERE / "release-caller-credential-isolation.test.py"
MANIFEST = HERE / "actions-ci-groups.tsv"
COMMAND = "python3 scripts/release-caller-credential-isolation.test.py"
failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    print(f"{'ok  ' if condition else 'FAIL'} - {label}")
    failures += 0 if condition else 1


spec = importlib.util.spec_from_file_location("release_caller_credential_isolation", TEST)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

cases = module.case_space()
parts = module.parts()
check(bool(cases), f"the test defines a non-empty case space ({len(cases)} cases)")
check(len(parts) >= 2, f"the case space is split into at least two parts ({sorted(parts)})")
for name, selected in sorted(parts.items()):
    check(bool(selected), f"part {name} selects at least one case")
union = set().union(*parts.values()) if parts else set()
check(union == cases, "the parts together select every case"
      + "".join(f" (missing {case})" for case in sorted(cases - union))
      + "".join(f" (unknown {case})" for case in sorted(union - cases)))
for (left, left_cases), (right, right_cases) in itertools.combinations(sorted(parts.items()), 2):
    overlap = left_cases & right_cases
    check(not overlap, f"parts {left} and {right} share no case"
          + "".join(f" ({case})" for case in sorted(overlap)))
check(module.selected_cases("all") == cases, "an unset part runs every case")
check(module.selected_cases("no-such-part") is None, "an unknown part selects nothing")

completed = subprocess.run(
    [sys.executable, str(TEST)], capture_output=True, text=True,
    env={**os.environ, module.PART_VARIABLE: "no-such-part"},
)
check(completed.returncode == 2 and not completed.stdout.strip(),
      f"{module.PART_VARIABLE}='no-such-part' exits 2 and runs no case (rc={completed.returncode})")

rows = [line.rstrip("\n").split("\t", 1)[-1] for line in MANIFEST.read_text().splitlines()
        if line and not line.startswith("#")]
for name in sorted(parts):
    wanted = f"{module.PART_VARIABLE}={name} {COMMAND}"
    check(sum(row.endswith(wanted) for row in rows) == 1, f"the manifest runs part {name} once")
check(not any(row.endswith(COMMAND) and f"{module.PART_VARIABLE}=" not in row for row in rows),
      "the manifest has no unselected full-run row")
listed = {row.rsplit(f"{module.PART_VARIABLE}=", 1)[1].split()[0] for row in rows
          if row.endswith(COMMAND) and f"{module.PART_VARIABLE}=" in row}
check(listed == set(parts), f"the manifest runs only known parts ({sorted(listed)})")

sys.exit(1 if failures else 0)
