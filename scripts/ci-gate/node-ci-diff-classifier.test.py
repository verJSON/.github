#!/usr/bin/env python3
"""Execute node-ci's diff-classified script-plan selection against a stubbed GitHub API.

`secretless-ci-non-app-script-plan` lets a pull request that changes only
non-application paths run a lighter plan inside the same `build-test` job, so the
required `<lane> / build-test` context still reports (ADR 0238). The selection
must fail closed: every doubt leaves the caller's full plan in force.

Each scenario runs the workflow's own classify step with a stub `gh` that serves
a canned compare response, then reads the step's `non-app` output.
"""

from __future__ import annotations

import copy
import json
import os
import stat
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
STEP_ID = "classify-diff"
SHA_A = "a" * 40
SHA_B = "b" * 40


def build_test_steps(document: dict) -> list[dict]:
    return document["jobs"]["build-test"]["steps"]


def classify_step(document: dict) -> dict:
    matches = [s for s in build_test_steps(document) if s.get("id") == STEP_ID]
    assert len(matches) == 1, f"expected one {STEP_ID} step, found {len(matches)}"
    return matches[0]


def run_classifier(document: dict, *, files: list[str] | None, paths: str = "",
                   base: str = SHA_A, head: str = SHA_B, api_fails: bool = False,
                   api_garbage: bool = False, expect_failure: bool = False) -> str:
    """Return the `non-app` output the step wrote (empty string if none)."""
    step = classify_step(document)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        output = tmp_path / "output"
        output.write_text("")
        gh = bin_dir / "gh"
        if api_fails:
            body = "#!/usr/bin/env bash\nexit 1\n"
        elif api_garbage:
            body = "#!/usr/bin/env bash\necho 'not json'\n"
        else:
            lines = "\n".join(json.dumps(name) for name in (files or []))
            payload = tmp_path / "payload"
            payload.write_text(lines + ("\n" if lines else ""))
            body = f"#!/usr/bin/env bash\ncat {payload}\n"
        gh.write_text(body)
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GITHUB_OUTPUT": str(output),
            "RUNNER_TEMP": str(tmp_path),
            "GH_TOKEN": "stub",
            "REPOSITORY": "Verjson/example",
            "BASE_SHA": base,
            "HEAD_SHA": head,
            "NON_APP_PATHS": paths,
        }
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
            env=env, capture_output=True, text=True,
        )
        if expect_failure:
            assert result.returncode != 0, "a malformed SHA must fail the step"
        else:
            assert result.returncode == 0, f"step must never fail the job: {result.stderr}"
        written = output.read_text()
    values = [line.split("=", 1)[1] for line in written.splitlines() if line.startswith("non-app=")]
    assert len(values) <= 1, f"non-app written more than once: {values}"
    return values[0] if values else ""


SCENARIOS = (
    ("a docs-only diff takes the light plan", {"files": ["docs/guide.md", "docs/a/b.png"]}, "true"),
    ("a NEXT fragment alone takes the light plan", {"files": ["NEXT/2026-10-22-issue-1-x.md"]}, "true"),
    ("a top-level Markdown file takes the light plan", {"files": ["README.md"]}, "true"),
    ("a nested Markdown file is app content until declared", {"files": ["src/content/post.md"]}, "false"),
    ("an app path forces the full plan", {"files": ["docs/a.md", "src/app/page.tsx"]}, "false"),
    ("a declared prefix is non-app", {"files": ["docker/Dockerfile"], "paths": '["docker/"]'}, "true"),
    ("a declared prefix covers other ecosystems' manifests: it asserts nothing under it feeds the Node build", {"files": ["python/requirements.txt", "docker/Dockerfile"], "paths": '["python/","docker/"]'}, "true"),
    ("a declared prefix does not cover a sibling name", {"files": ["docker-compose.yml"], "paths": '["docker/"]'}, "false"),
    ("package.json is never non-app even under a declared prefix", {"files": ["docs/package.json"], "paths": '["docs/"]'}, "false"),
    ("a lockfile is never non-app", {"files": ["docs/x.md", "package-lock.json"]}, "false"),
    ("a pnpm lockfile is never non-app", {"files": ["docs/pnpm-lock.yaml"]}, "false"),
    (".npmrc is never non-app", {"files": ["docs/.npmrc"]}, "false"),
    ("a workflow file is never non-app", {"files": [".github/workflows/ci.yml"]}, "false"),
    ("a declared .github prefix is rejected", {"files": [".github/x.md"], "paths": '[".github/"]'}, "false"),
    ("case variants of denied names are denied", {"files": ["docs/Package.JSON"]}, "false"),
    ("git metadata files are never non-app", {"files": [".gitmodules"]}, "false"),
    ("git metadata under a declared prefix is never non-app", {"files": ["docker/.gitattributes"], "paths": '["docker/"]'}, "false"),
    ("toolchain pins are never non-app", {"files": ["docs/.nvmrc"]}, "false"),
    ("a rename out of an app path is judged by both names", {"files": ["docs/new.md", "src/old.md"]}, "false"),
    ("an empty file list is unclassified", {"files": []}, "false"),
    ("a traversal path is unclassified", {"files": ["docs/../src/a.ts"]}, "false"),
    ("an absolute path is unclassified", {"files": ["/docs/a.md"]}, "false"),
    ("a path with a control character is unclassified", {"files": ["docs/a\nb.md"]}, "false"),
    ("an API failure falls back to the full plan", {"files": None, "api_fails": True}, "false"),
    ("a non-JSON API reply falls back to the full plan", {"files": None, "api_garbage": True}, "false"),
    ("a truncated list at the API cap falls back to the full plan", {"files": [f"docs/{n}.md" for n in range(3000)]}, "false"),
    ("a malformed base SHA fails the step without selecting the light plan", {"files": ["docs/a.md"], "base": "main", "expect_failure": True}, ""),
    ("a malformed head SHA fails the step without selecting the light plan", {"files": ["docs/a.md"], "head": SHA_B + "; id", "expect_failure": True}, ""),
    ("a non-JSON path list falls back to the full plan", {"files": ["docs/a.md"], "paths": "docker/"}, "false"),
    ("a path list with a traversal prefix falls back", {"files": ["docs/a.md"], "paths": '["../x/"]'}, "false"),
    ("a path list with a non-string falls back", {"files": ["docs/a.md"], "paths": "[1]"}, "false"),
    ("more than 32 declared prefixes fall back", {"files": ["docs/a.md"], "paths": json.dumps([f"p{n}/" for n in range(33)])}, "false"),
    ("a declared prefix lacking its trailing slash falls back", {"files": ["docker/a"], "paths": '["docker"]'}, "false"),
)


def structural_problems(document: dict) -> list[str]:
    problems = []
    step = classify_step(document)
    plan_steps = [s for s in build_test_steps(document)
                  if s.get("id") == "node-ci-script-plan"]
    if len(plan_steps) != 1:
        return [f"expected one plan step, found {len(plan_steps)}"]
    steps = build_test_steps(document)
    if steps.index(step) > steps.index(plan_steps[0]):
        problems.append("the classifier runs after the plan step")
    expected = ("${{ steps.classify-diff.outputs.non-app == 'true' && "
                "inputs.secretless-ci-non-app-script-plan || inputs.secretless-ci-script-plan }}")
    plan_input = (plan_steps[0].get("with") or {}).get("script-plan")
    if plan_input != expected:
        problems.append(f"plan step does not select via the classifier: {plan_input!r}")
    condition = str(step.get("if", ""))
    for required in ("inputs.secretless-pr", "github.event_name == 'pull_request'",
                     "inputs.secretless-ci-non-app-script-plan != ''"):
        if required not in condition:
            problems.append(f"classifier condition lacks {required!r}")
    if "inputs.secretless-trusted-ref" in condition:
        problems.append("trusted-ref calls must never take a reduced plan")
    if "${{" in str(step.get("run", "")):
        problems.append("the classifier script interpolates an expression")
    plan_env = plan_steps[0].get("env") or {}
    if any("token" in name.lower() and plan_env[name] != "" for name in plan_env):
        problems.append("the plan step receives a credential")
    return problems


def main() -> int:
    failures: list[str] = []
    for workflow in WORKFLOWS:
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        try:
            problems = structural_problems(document)
        except AssertionError as error:
            problems = [str(error)]
        failures += [f"{workflow.name}: {p}" for p in problems]
        if problems:
            continue
        for label, kwargs, expected in SCENARIOS:
            got = run_classifier(document, **kwargs)
            if got != expected:
                failures.append(f"{workflow.name}: {label}: expected {expected!r}, got {got!r}")
        print(f"ok - {workflow.name}: {len(SCENARIOS)} classification scenarios and the plan binding")
    for failure in failures:
        print(f"FAIL - {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
