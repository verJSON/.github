"""Semantic and mutation contract for the protected release App canary."""

from __future__ import annotations

import copy
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import yaml


ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/release-app-canary.yml"
TOKEN_ACTION = (
    "actions/create-github-app-token@"
    "bcd2ba49218906704ab6c1aa796996da409d3eb1"
)
CHECKOUT_ACTION = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"


def triggers(document: dict) -> object:
    return document.get("on", document.get(True))


def validate(document: dict, raw: str) -> list[str]:
    problems: list[str] = []
    trigger = triggers(document)
    if not isinstance(trigger, dict) or set(trigger) != {"workflow_dispatch"}:
        problems.append("canary is not manual-only")
    elif (trigger["workflow_dispatch"] or {}).get("inputs", {}) != {"release_environment": {"description": "Main-only environment containing the release App private key", "required": True, "type": "string", "default": "release-app"}}:
        problems.append("workflow_dispatch exposes user-controlled inputs")
    if document.get("permissions") != {"actions": "read", "contents": "read"}:
        problems.append("workflow GITHUB_TOKEN is not contents-read-only")

    job = (document.get("jobs") or {}).get("canary") or {}
    expected_runner = "${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') }}"
    if job.get("runs-on") != expected_runner:
        problems.append("canary does not route through the trusted organization lane and fallback")
    if job.get("permissions") != {"contents": "read"}:
        problems.append("job GITHUB_TOKEN is not contents-read-only")
    steps = job.get("steps") or []
    by_id = {step.get("id"): step for step in steps if step.get("id")}
    mint = by_id.get("release-app-token") or {}
    push = by_id.get("push") or {}
    checkout = next(
        (step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")),
        {},
    )
    if checkout.get("uses") != CHECKOUT_ACTION:
        problems.append("checkout pin changed")
    checkout_with = checkout.get("with") or {}
    if checkout_with.get("ref") != "${{ github.sha }}" or checkout_with.get("persist-credentials") is not False:
        problems.append("checkout is not bound to the executing revision without credentials")
    if mint.get("uses") != TOKEN_ACTION:
        problems.append("token action pin changed")
    expected_mint = {
        "client-id": "${{ vars.RELEASE_APP_CLIENT_ID }}",
        "private-key": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-contents": "write",
    }
    if mint.get("with") != expected_mint:
        problems.append("App token is not exact current-repository contents-write scope")

    guard = next((step for step in steps if step.get("name") == "Require the default-branch revision"), {})
    guard_run = guard.get("run") or ""
    guard_env = guard.get("env") or {}
    if guard_env != {
        "DISPATCH_REF": "${{ github.ref }}",
        "DEFAULT_BRANCH": "${{ github.event.repository.default_branch }}",
    } or '"refs/heads/$DEFAULT_BRANCH"' not in guard_run:
        problems.append("canary is not bound to the default-branch dispatch revision")

    push_env = push.get("env") or {}
    push_run = push.get("run") or ""
    if push_env.get("CANARY_BRANCH_REF") != "refs/heads/develop":
        problems.append("protected canary branch is not fixed to develop")
    version = str(push_env.get("CANARY_VERSION") or "")
    if version != "v0.0.1-release-app-canary.${{ github.run_id }}.${{ github.run_attempt }}":
        problems.append("canary tag is not a fixed run-unique SemVer prerelease")
    required_push_fragments = (
        'ls-remote --refs "$remote_url" "$CANARY_BRANCH_REF" "$tag_ref"',
        'release_cli="$RUNNER_TEMP/release-app-canary-changelog-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT.py"',
        'git --no-replace-objects -C "$GITHUB_WORKSPACE"',
        'show "$GITHUB_SHA:scripts/changelog.py" > "$release_cli"',
        'python3 -I "$release_cli" release',
        'fragment_id="$(date -u +%Y%m%dT%H%M%SZ)"',
        "-issue-${fragment_id}-release-app-canary.md",
        "id: $fragment_id",
        'push --atomic origin',
        '"$release_commit:$CANARY_BRANCH_REF"',
        '"$tag_ref"',
        'echo "pushed=true"',
        '"${tag_ref}^{}"',
        'test "$remote_branch_commit" = "$release_commit"',
        'test "$remote_tag_object" = "$tag_object"',
        'test "$remote_tag_commit" = "$release_commit"',
        'ls-remote "$remote_url" "$CANARY_BRANCH_REF" "$tag_ref" "${tag_ref}^{}"',
    )
    for fragment in required_push_fragments:
        if fragment not in push_run:
            problems.append(f"push proof lacks {fragment}")

    receipt = next((step for step in steps if step.get("name") == "Retain the canary receipt"), {})
    receipt_run = receipt.get("run") or ""
    receipt_env = receipt.get("env") or {}
    expected_receipt_env = {
        "APP_SLUG": "${{ steps.release-app-token.outputs.app-slug }}",
        "INSTALLATION_ID": "${{ steps.release-app-token.outputs.installation-id }}",
        "RELEASE_COMMIT": "${{ steps.push.outputs.release-commit }}",
        "CANARY_VERSION": "${{ steps.push.outputs.version }}",
    }
    if receipt_env != expected_receipt_env:
        problems.append("receipt does not bind App identity and release outputs")
    for fragment in (
        "$GITHUB_STEP_SUMMARY",
        "actions/runs/$GITHUB_RUN_ID/attempts/$GITHUB_RUN_ATTEMPT",
        "main-protection",
        "18098028",
        "refs/heads/develop",
        "does not exercise the default branch ref itself",
    ):
        if fragment not in receipt_run:
            problems.append(f"retained receipt lacks {fragment}")

    cleanup = next(
        (step for step in steps if step.get("name") == "Delete only this run's verified canary refs"),
        {},
    )
    cleanup_run = cleanup.get("run") or ""
    if str(cleanup.get("if") or "") != "always() && steps.push.outputs.pushed == 'true'":
        problems.append("cleanup is not gated on this run recording a successful push")
    cleanup_env = cleanup.get("env") or {}
    if cleanup_env.get("RELEASE_COMMIT") != "${{ steps.push.outputs.release-commit }}" or cleanup_env.get(
        "TAG_OBJECT"
    ) != "${{ steps.push.outputs.tag-object }}":
        problems.append("cleanup ownership is not bound to this run's commit and tag object")
    for fragment in (
        '"$remote_branch_commit" != "$RELEASE_COMMIT"',
        '"$remote_tag_object" != "$TAG_OBJECT"',
        '"$remote_tag_commit" != "$RELEASE_COMMIT"',
        'ls-remote "$remote_url" "$branch_ref" "$tag_ref" "${tag_ref}^{}"',
        "refusing cleanup",
        'push --atomic "$remote_url"',
        '":$branch_ref"',
        '":$tag_ref"',
        'ls-remote --refs "$remote_url" "$branch_ref" "$tag_ref"',
        "$GITHUB_STEP_SUMMARY",
    ):
        if fragment not in cleanup_run:
            problems.append(f"ownership-checked atomic cleanup lacks {fragment}")
    if "${{ inputs." in raw.replace("${{ inputs.release_environment }}", "") or "repository:" in raw or "CANARY_BRANCH_REF: ${{" in raw:
        problems.append("canary exposes a user-controlled target")
    return problems


def git(*args: str, cwd: pathlib.Path | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def prove_peeled_annotated_tag_query() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        remote = root / "remote.git"
        local = root / "local"
        git("init", "--bare", "-q", str(remote))
        git("init", "-q", "-b", "canary", str(local))
        git("config", "user.name", "Canary Test", cwd=local)
        git("config", "user.email", "canary@example.test", cwd=local)
        (local / "receipt.txt").write_text("canary\n", encoding="utf-8")
        git("add", "receipt.txt", cwd=local)
        git("commit", "-qm", "seed canary", cwd=local)
        commit = git("rev-parse", "HEAD", cwd=local)
        tag = "v0.0.1-release-app-canary.1.1"
        tag_ref = f"refs/tags/{tag}"
        git("tag", "-a", tag, "-m", "canary", cwd=local)
        tag_object = git("rev-parse", f"{tag}^{{tag}}", cwd=local)
        git("remote", "add", "origin", str(remote), cwd=local)
        git("push", "-q", "--atomic", "origin", "HEAD:refs/heads/develop", tag_ref, cwd=local)

        patterns = ("refs/heads/develop", tag_ref, f"{tag_ref}^{{}}")
        exact = git("ls-remote", str(remote), *patterns).splitlines()
        refs_only = git("ls-remote", "--refs", str(remote), *patterns).splitlines()
        mapping = {ref: oid for oid, ref in (line.split("\t", 1) for line in exact)}
        refs_only_mapping = {
            ref: oid for oid, ref in (line.split("\t", 1) for line in refs_only)
        }
        assert mapping["refs/heads/develop"] == commit
        assert mapping[tag_ref] == tag_object
        assert mapping[f"{tag_ref}^{{}}"] == commit
        assert f"{tag_ref}^{{}}" not in refs_only_mapping
    print("ok - ls-remote without --refs returns annotated tag object and peeled commit")
    print("ok - old ls-remote --refs form suppresses the peeled annotated-tag line")


def prove_cli_materializes_from_bound_object(push_run: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        workspace = root / "workspace"
        runner_temp = root / "runner-temp"
        canary_root = runner_temp / "release-app-canary-1-1"
        workspace.mkdir()
        runner_temp.mkdir()
        git("init", "-q", "-b", "main", str(workspace))
        git("config", "user.name", "Canary Test", cwd=workspace)
        git("config", "user.email", "canary@example.test", cwd=workspace)
        cli = workspace / "scripts/changelog.py"
        cli.parent.mkdir()
        cli.write_bytes((ROOT / "scripts/changelog.py").read_bytes())
        git("add", "scripts/changelog.py", cwd=workspace)
        git("commit", "-qm", "seed bound release CLI", cwd=workspace)
        bound_sha = git("rev-parse", "HEAD", cwd=workspace)
        cli.write_text("raise SystemExit('poisoned replacement executed')\n", encoding="utf-8")
        git("add", "scripts/changelog.py", cwd=workspace)
        git("commit", "-qm", "seed poisoned replacement", cwd=workspace)
        poisoned_sha = git("rev-parse", "HEAD", cwd=workspace)
        git("reset", "--hard", "-q", bound_sha, cwd=workspace)
        git("replace", bound_sha, poisoned_sha, cwd=workspace)
        assert "poisoned replacement" in git("show", f"{bound_sha}:scripts/changelog.py", cwd=workspace)
        cli.unlink()
        assert not cli.exists()

        fragment = "NEXT/2026-08-13-issue-20260813T000000Z-release-app-canary.md"
        (canary_root / "NEXT").mkdir(parents=True)
        git("init", "-q", "-b", "canary", str(canary_root))
        git("config", "user.name", "Canary Test", cwd=canary_root)
        git("config", "user.email", "canary@example.test", cwd=canary_root)
        (canary_root / fragment).write_text(
            "---\n"
            "date: 2026-08-13\n"
            "id: 20260813T000000Z\n"
            "title: Prove object-backed release CLI execution\n"
            "impact: patch\n"
            "---\n\n"
            "Exercise the canonical release path.\n",
            encoding="utf-8",
        )
        git("add", fragment, cwd=canary_root)
        git("commit", "-qm", "seed canary", cwd=canary_root)

        lines = push_run.splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip().startswith("release_cli="))
        end = next(
            i
            for i, line in enumerate(lines[start:], start=start)
            if line.strip() == '--fragment "$fragment"'
        )
        materialize_and_release = "\n".join(lines[start : end + 1])
        environment = os.environ.copy()
        environment.update(
            {
                "GITHUB_WORKSPACE": str(workspace),
                "GITHUB_SHA": bound_sha,
                "GITHUB_RUN_ID": "1",
                "GITHUB_RUN_ATTEMPT": "1",
                "RUNNER_TEMP": str(runner_temp),
                "CANARY_VERSION": "v0.0.1-release-app-canary.1.1",
                "canary_root": str(canary_root),
                "fragment": fragment,
            }
        )
        subprocess.run(
            ["bash", "-euo", "pipefail", "-c", materialize_and_release],
            check=True,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert (canary_root / "CHANGELOG/v0.0.1-release-app-canary.1.1.md").is_file()
        assert not (canary_root / fragment).exists()
        git("rev-parse", "v0.0.1-release-app-canary.1.1^{tag}", cwd=canary_root)
    print("ok - bound git object ignores replacement refs when the workspace file is missing")


def rejected(label: str, document: dict, raw: str) -> None:
    if not validate(document, raw):
        raise AssertionError(f"mutation survived: {label}")
    print(f"ok - rejects {label}")


def main() -> int:
    raw = WORKFLOW.read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    problems = validate(document, raw)
    if problems:
        print("FAIL - " + "; ".join(problems))
        return 1
    print("ok - protected release App canary contract is bounded and recoverable")
    prove_peeled_annotated_tag_query()

    job = document["jobs"]["canary"]
    steps = job["steps"]
    mint_index = next(i for i, step in enumerate(steps) if step.get("id") == "release-app-token")
    push_index = next(i for i, step in enumerate(steps) if step.get("id") == "push")
    checkout_index = next(
        i for i, step in enumerate(steps) if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    guard_index = next(i for i, step in enumerate(steps) if step.get("name") == "Require the default-branch revision")
    cleanup_index = next(
        i for i, step in enumerate(steps) if step.get("name") == "Delete only this run's verified canary refs"
    )
    receipt_index = next(i for i, step in enumerate(steps) if step.get("name") == "Retain the canary receipt")
    prove_cli_materializes_from_bound_object(steps[push_index]["run"])

    mutants: list[tuple[str, dict, str]] = []
    mutant = copy.deepcopy(document)
    triggers(mutant)["push"] = {"branches": ["main"]}
    mutants.append(("a push trigger", mutant, raw))
    mutant = copy.deepcopy(document)
    triggers(mutant)["workflow_dispatch"] = {"inputs": {"ref": {"required": True}}}
    mutants.append(("a user-controlled dispatch input", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["runs-on"] = "ubuntu-24.04"
    mutants.append(("a literal hosted runner outside the organization lane contract", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][mint_index]["uses"] = "actions/create-github-app-token@v3"
    mutants.append(("a mutable token action ref", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][checkout_index]["uses"] = "actions/checkout@main"
    mutants.append(("a mutable checkout action ref", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][guard_index]["env"]["DEFAULT_BRANCH"] = "main"
    mutants.append(("a canary not bound to repository default-branch metadata", mutant, raw))
    for key in ("owner", "repositories", "permission-contents"):
        mutant = copy.deepcopy(document)
        del mutant["jobs"]["canary"]["steps"][mint_index]["with"][key]
        mutants.append((f"token mint without {key}", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][push_index]["env"]["CANARY_BRANCH_REF"] = "refs/heads/main"
    mutants.append(("a canary branch other than fixed develop", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][push_index]["env"]["CANARY_VERSION"] = "${{ inputs.version }}"
    mutants.append(("a user-controlled or non-unique canary tag", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][push_index]["run"] = mutant["jobs"]["canary"]["steps"][push_index][
        "run"
    ].replace('$GITHUB_SHA:scripts/changelog.py', 'HEAD:scripts/changelog.py')
    mutants.append(("release CLI materialization not bound to the dispatch SHA", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][push_index]["run"] = mutant["jobs"]["canary"]["steps"][push_index][
        "run"
    ].replace("git --no-replace-objects", "git")
    mutants.append(("release CLI materialization that honors persistent replacement refs", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][push_index]["run"] = mutant["jobs"]["canary"]["steps"][push_index][
        "run"
    ].replace('python3 -I "$release_cli" release', 'python3 -I "$GITHUB_WORKSPACE/scripts/changelog.py" release')
    mutants.append(("release CLI execution from the persistent workspace", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][push_index]["run"] = mutant["jobs"]["canary"]["steps"][push_index][
        "run"
    ].replace("push --atomic origin", "push origin")
    mutants.append(("a non-atomic canary push", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][push_index]["run"] = mutant["jobs"]["canary"]["steps"][push_index][
        "run"
    ].replace(
        'ls-remote "$remote_url" "$CANARY_BRANCH_REF" "$tag_ref" "${tag_ref}^{}"',
        'ls-remote --refs "$remote_url" "$CANARY_BRANCH_REF" "$tag_ref" "${tag_ref}^{}"',
    )
    mutants.append(("push verification that suppresses the peeled tag line", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][cleanup_index]["if"] = "always()"
    mutants.append(("cleanup without successful-push ownership gate", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][cleanup_index]["run"] = mutant["jobs"]["canary"]["steps"][cleanup_index][
        "run"
    ].replace('"$remote_tag_object" != "$TAG_OBJECT"', '"$remote_tag_object" != ""')
    mutants.append(("cleanup without exact tag-object ownership", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][cleanup_index]["run"] = mutant["jobs"]["canary"]["steps"][cleanup_index][
        "run"
    ].replace(
        'ls-remote "$remote_url" "$branch_ref" "$tag_ref" "${tag_ref}^{}"',
        'ls-remote --refs "$remote_url" "$branch_ref" "$tag_ref" "${tag_ref}^{}"',
    )
    mutants.append(("cleanup ownership query that suppresses the peeled tag line", mutant, raw))
    mutant = copy.deepcopy(document)
    mutant["jobs"]["canary"]["steps"][receipt_index]["run"] = "echo receipt"
    mutants.append(("a non-retained or incomplete receipt", mutant, raw))

    for label, mutant, mutant_raw in mutants:
        rejected(label, mutant, mutant_raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
