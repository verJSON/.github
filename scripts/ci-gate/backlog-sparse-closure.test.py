#!/usr/bin/env python3
"""Every in-repository relay-backlog job checks out exactly what it runs.

ADR 0235 (round 6): a backlog row binds the bytes a job puts on disk, so a job
that runs this repository's code checks out an exact non-cone sparse list
instead of the whole tree. A list that omits a file the job needs breaks the
job; a list that names a file that no longer exists hides a rename. This test
derives what each job needs and requires its sparse list to hold all of it:

- every program its steps run (the checker's own `programs()` recognizer);
- each Python program's import closure, by `ast`, resolved the way the
  programs resolve it (their own directory, which they put on `sys.path`);
- each shell script's siblings reached through `$here/` or `$(dirname "$0")/`;
- every path the job materializes with `git show "$GITHUB_SHA:<path>"`.

It also checks the call forms the checker does not follow (ADR 0235's list):
no backlog job hands a literal tracked path to one of them.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
spec = importlib.util.spec_from_file_location("checker", Path(__file__).with_name("public-pr-runner-isolation.py"))
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

# The backlog jobs that check out this repository's own tree (not a caller's)
# and run its code. Each must carry an exact non-cone sparse list.
CONVERTED = {
    ("capability-floor-observe.yml", "observe"),
    ("dependency-supersession-observe.yml", "observe"),
    ("dependency-supersession-reconcile.yml", "reconcile"),
    ("release-app-canary.yml", "canary"),
    ("rework-reconcile.yml", "reconcile"),
}
# Data the programs read by a computed path, which no static rule derives;
# proven present by running each job's commands in a sparse checkout (#1827).
DATA = {
    ("capability-floor-observe.yml", "observe"): {
        "config/capability-floor-consumers.json",
        "config/capability-floors.json",
        "scripts/gen-ai-review-caller.sh",
        "scripts/gen-ai-review-label-rearm-caller.sh",
        "scripts/gen-changelog-caller.sh",
        "scripts/gen-gate-rearm-caller.sh",
        "scripts/gen-privileged-merge-caller.sh",
    },
    ("rework-reconcile.yml", "reconcile"): {".telemetry/rework-thresholds.json"},
}
SHELL_SIBLING = re.compile(r'(?:\$here|\$\{here\}|\$\(dirname "\$0"\)|\$\(dirname \$0\))/([A-Za-z0-9_.-]+)')
GIT_SHOW = re.compile(r'show\s+"?\$(?:\{GITHUB_SHA\}|GITHUB_SHA):([^"\s]+)"?')
TRACKED = set(
    subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True)
    .stdout.decode("utf-8", "surrogateescape").split("\0")
) - {""}
# Wrappers and forms the checker's recognizer does not follow (ADR 0235).
UNFOLLOWED = re.compile(
    r"(?:^|[;&|(`]|\$\()\s*(?:"
    r"source|\.|env(?:\s+-\S+|\s+\w+=\S*)*|nohup|sudo|timeout(?:\s+-\S+)*\s+\S+|xargs(?:\s+-\S+)*|eval|exec|"
    r"(?:ba|z|da)?sh(?:\s+-\w+)*?\s+-\w*c\w*|"
    r"(?:ba|z|da)?sh(?:\s+-\w+)*\s+-o\s+\S+(?:\s+-o\s+\S+|\s+-\w+)*"
    r")\s+[\"']?(?P<path>[\w./-]+)",
    re.M,
)
PIPE_INTO_SHELL = re.compile(r"\|\s*(?:ba|z|da)?sh\b")


def job(workflow: str, name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["jobs"][name]


def runs(document: dict) -> list[str]:
    return [checker.without_comments(step["run"]) for step in document.get("steps") or [] if "run" in step]


def sparse_list(document: dict) -> tuple[set[str], bool]:
    """The job's own-repository sparse list, and whether it is non-cone."""
    for step in document.get("steps") or []:
        if str(step.get("uses", "")).startswith("actions/checkout@") and "repository" not in (step.get("with") or {}):
            options = step.get("with") or {}
            listed = {line.strip() for line in str(options.get("sparse-checkout") or "").splitlines() if line.strip()}
            return listed, str(options.get("sparse-checkout-cone-mode", "true")).lower() == "false"
    return set(), False


def python_imports(path: str) -> set[str]:
    """Repository modules `path` imports, transitively, from its directory."""
    found: set[str] = set()
    queue = [path]
    while queue:
        current = queue.pop()
        tree = ast.parse((ROOT / current).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                candidate = os.path.join(os.path.dirname(current), *name.split(".")) + ".py"
                if candidate in TRACKED and candidate not in found:
                    found.add(candidate)
                    queue.append(candidate)
    return found


def needed(workflow: str, name: str) -> set[str]:
    document = job(workflow, name)
    texts = runs(document)
    programs = {
        os.path.normpath(program)
        for text in texts
        for program in checker.programs(text, str(ROOT))
        if os.path.normpath(program) in TRACKED
    }
    programs |= {match.group(1) for text in texts for match in GIT_SHOW.finditer(text)}
    queue = sorted(programs)
    while queue:
        program = queue.pop()
        if program.endswith(".py"):
            for module in python_imports(program) - programs:
                programs.add(module)
                queue.append(module)
        elif program.endswith(".sh"):
            content = (ROOT / program).read_text(encoding="utf-8")
            for sibling in SHELL_SIBLING.findall(content):
                path = os.path.join(os.path.dirname(program), sibling)
                if path in TRACKED and path not in programs:
                    programs.add(path)
                    queue.append(path)
    return programs | DATA.get((workflow, name), set())


class BacklogSparseClosureTests(unittest.TestCase):
    def test_converted_jobs_check_out_an_exact_non_cone_list(self):
        for workflow, name in sorted(CONVERTED):
            with self.subTest(f"{workflow}:{name}"):
                listed, non_cone = sparse_list(job(workflow, name))
                self.assertTrue(non_cone, "the sparse checkout must be non-cone, or parents' files land too")
                self.assertTrue(listed, "the job must carry a sparse-checkout list")
                self.assertEqual(sorted(listed - TRACKED), [], "listed paths that are not tracked files")

    def test_each_sparse_list_holds_every_program_import_and_datum(self):
        for workflow, name in sorted(CONVERTED):
            with self.subTest(f"{workflow}:{name}"):
                listed, _ = sparse_list(job(workflow, name))
                self.assertEqual(sorted(needed(workflow, name) - listed), [], "needed but not checked out")

    def test_each_sparse_list_names_nothing_the_job_does_not_need(self):
        for workflow, name in sorted(CONVERTED):
            with self.subTest(f"{workflow}:{name}"):
                listed, _ = sparse_list(job(workflow, name))
                self.assertEqual(sorted(listed - needed(workflow, name)), [], "checked out but not needed")

    def test_the_import_closure_is_derived_not_assumed(self):
        # discovery.py imports github_identity from its own directory.
        self.assertIn("scripts/github_identity.py", python_imports("scripts/capability-floor-discovery.py"))
        self.assertIn(
            "scripts/ci-telemetry/rework-classify.sh", needed("rework-reconcile.yml", "reconcile")
        )

    def test_release_verify_binds_every_script_it_runs(self):
        # The one full-checkout exception (Part of #1831). Independently of the
        # checker's derivation, read the scripts verify runs: the hook its step
        # runs through the run_clean wrapper, every program that hook runs, and
        # the pinned files it compares against. The checker must bind them all.
        hook = "scripts/release-verify.sh"
        self.assertIn(f"run_clean {hook}", "\n".join(runs(job("release.yml", "verify"))))
        text = (ROOT / hook).read_text(encoding="utf-8")
        runs_from_hook = {
            match.group(1)
            for match in re.finditer(r"(?m)^(?:node --test|python3|bash)\s+([\w./-]+)", text)
            if match.group(1) in TRACKED
        }
        compared = {path for path in re.findall(r"[\w./-]+", text) if path in TRACKED} - runs_from_hook
        expected = {hook} | runs_from_hook | compared
        self.assertGreaterEqual(len(runs_from_hook), 5)
        self.assertIn(".github/workflows/release.yml", compared)
        self.assertIn("scripts/gen-changelog-caller.sh", compared)
        bound = checker.tree_programs(job("release.yml", "verify"), str(ROOT))
        self.assertEqual(sorted(expected - set(bound)), [], "scripts verify runs that its row does not bind")
        self.assertLess(len(bound), 60, "the verify binding must stay a program set, not the tree")

    def test_no_backlog_job_hands_a_literal_tracked_path_to_an_unfollowed_form(self):
        offenders = []
        for (label, job_name) in sorted(checker.RELAY_BACKLOG):
            workflow = label.split(" ")[0].split(":")[0]
            callee = label.rsplit(" -> ", 1)[-1] if " -> " in label else workflow
            for text in runs(job(callee, job_name)):
                for match in UNFOLLOWED.finditer(text):
                    if os.path.normpath(match.group("path")) in TRACKED:
                        offenders.append(f"{label}:{job_name}: {match.group(0).strip()}")
                for line in text.splitlines():
                    if PIPE_INTO_SHELL.search(line):
                        tokens = [token for token in shlex.split(line, posix=True) if token in TRACKED]
                        offenders.extend(f"{label}:{job_name}: {line.strip()}" for _ in tokens[:1])
        self.assertEqual(offenders, [])

    def test_the_unfollowed_form_scan_matches_each_form(self):
        for text in (
            "source scripts/x.sh",
            ". scripts/x.sh",
            "env A=1 scripts/x.sh",
            "nohup scripts/x.sh",
            "sudo scripts/x.sh",
            "timeout 30 scripts/x.sh",
            "xargs -0 scripts/x.sh",
            "eval scripts/x.sh",
            "bash -o pipefail scripts/x.sh",
            'sh -c "scripts/x.sh"',
            "true && exec scripts/x.sh",
        ):
            with self.subTest(text):
                match = UNFOLLOWED.search(text)
                self.assertIsNotNone(match)
                self.assertEqual(match.group("path"), "scripts/x.sh")


if __name__ == "__main__":
    unittest.main()
