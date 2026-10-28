#!/usr/bin/env python3
"""Stamp every `# runner-lane: <shape>` marked `runs-on:` line from the catalog.

    gen-runner-lane.py [--check] [workflow-dir]

A marker comment sits on the line directly above `runs-on:`. Without --check the
line is rewritten in place; with --check a stale or hand-edited line fails with
exit 1 and nothing is written. An unknown shape or a marker not followed by
`runs-on:` also fails: a marker that silently stamps nothing is the drift this
generator exists to prevent (ADR 0240).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runner_lane_catalog as catalog  # noqa: E402

MARKER_LINE = re.compile(rf"^(\s*)#\s*{re.escape(catalog.MARKER)}\s*(\S+)\s*$")
RUNS_ON_LINE = re.compile(r"^(\s*)runs-on:")
JOB_LINE = re.compile(r"^  ([A-Za-z0-9_-]+):$")


def _job_name_at(lines: list[str], index: int) -> str | None:
    """Return the nearest jobs-level key above a lane marker."""
    jobs_start = next(
        (position for position, line in enumerate(lines[:index]) if line == "jobs:"),
        None,
    )
    if jobs_start is None:
        return None
    job_name = None
    for line in lines[jobs_start + 1 : index]:
        if line and not line[0].isspace():
            job_name = None
        match = JOB_LINE.fullmatch(line)
        if match:
            job_name = match.group(1)
    return job_name


def validate_fixed_hosted_sites(sources: dict[Path, bytes]) -> list[str]:
    """Bind the exact hosted profile bytes to their single canonical site."""
    expected = catalog.FIXED_HOSTED_JOB_SITES
    found: dict[tuple[str, str], int] = {}
    problems: list[str] = []
    for path, raw in sources.items():
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{path.name}: workflow source is not UTF-8")
            continue
        lines = text.splitlines()
        for index, line in enumerate(lines):
            match = MARKER_LINE.fullmatch(line)
            if match is None or match.group(2) != catalog.FIXED_HOSTED_PROFILE_ACCEPTANCE:
                continue
            site = (path.name, _job_name_at(lines, index))
            if site not in expected:
                problems.append(f"{path.name}:{index + 1}: fixed hosted runner lane not authorized {site!r}")
                continue
            found[site] = found.get(site, 0) + 1
        if path.name == catalog.FIXED_HOSTED_PROFILE_ACCEPTANCE_WORKFLOW:
            try:
                catalog.validate_fixed_hosted_profile_bytes(path, raw)
            except ValueError as error:
                problems.append(f"{path.name}: {error}")
    for site in sorted(expected):
        count = found.get(site, 0)
        if count != 1:
            problems.append(
                f"{site[0]}: expected one fixed hosted marker in job {site[1]!r}, found {count}"
            )
    return problems

def stamp(text: str, name: str) -> tuple[str, list[str]]:
    lines = text.split("\n")
    problems: list[str] = []
    for index, line in enumerate(lines):
        match = MARKER_LINE.match(line)
        if not match:
            if catalog.MARKER in line and line.lstrip().startswith("#"):
                problems.append(f"{name}:{index + 1}: malformed runner-lane marker")
            continue
        indent, shape = match.groups()
        target = lines[index + 1] if index + 1 < len(lines) else ""
        if shape not in catalog.SHAPES:
            problems.append(f"{name}:{index + 1}: unknown runner-lane shape {shape!r}")
        elif not RUNS_ON_LINE.match(target) or RUNS_ON_LINE.match(target).group(1) != indent:
            problems.append(f"{name}:{index + 2}: runner-lane marker must precede a runs-on: line")
        else:
            lines[index + 1] = catalog.runs_on_line(shape, indent)
    return "\n".join(lines), problems


def main(argv: list[str]) -> int:
    check = "--check" in argv
    args = [arg for arg in argv if arg != "--check"]
    root = Path(args[0]) if args else Path(__file__).resolve().parents[1] / ".github/workflows"
    failed = False
    workflow_paths = sorted([*root.glob("*.yml"), *root.glob("*.yaml")])
    workflow_sources: dict[Path, bytes] = {}
    for path in workflow_paths:
        try:
            if path.name == catalog.FIXED_HOSTED_PROFILE_ACCEPTANCE_WORKFLOW:
                raw = catalog.read_fixed_hosted_profile_source(path)
            else:
                raw = path.read_bytes()
        except (OSError, ValueError) as error:
            print(f"{path.name}: cannot read workflow source: {error}", file=sys.stderr)
            failed = True
            continue
        workflow_sources[path] = raw
    for problem in validate_fixed_hosted_sites(workflow_sources):
        print(problem, file=sys.stderr)
        failed = True
    for path, raw in workflow_sources.items():
        try:
            original = raw.decode("utf-8")
        except UnicodeDecodeError:
            print(f"{path.name}: workflow source is not UTF-8", file=sys.stderr)
            failed = True
            continue
        if catalog.MARKER not in original:
            continue
        stamped, problems = stamp(original, path.name)
        for problem in problems:
            print(problem, file=sys.stderr)
        failed = failed or bool(problems)
        if stamped != original:
            if check:
                print(f"{path.name}: runs-on is stale; run scripts/gen-runner-lane.py", file=sys.stderr)
                failed = True
            elif not problems:
                path.write_text(stamped)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
