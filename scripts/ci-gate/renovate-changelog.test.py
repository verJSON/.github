#!/usr/bin/env python3
from __future__ import annotations

import ast
import copy
import pathlib
import sys

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/renovate-changelog.yml"
HELPER = ROOT / "scripts/renovate-changelog.py"
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
APP_TOKEN = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"


def trigger(document: dict) -> object:
    return document.get("on", document.get(True))


def validate(document: dict, helper_text: str) -> list[str]:
    problems: list[str] = []
    workflow_call = trigger(document)
    if not isinstance(workflow_call, dict) or set(workflow_call) != {"workflow_call"}:
        problems.append("workflow is not reusable-only")
        return problems
    call = workflow_call["workflow_call"] or {}
    expected_inputs = {"contract_ref", "release_app_client_id", "release_environment"}
    expected_secrets = set()
    if set((call.get("inputs") or {})) != expected_inputs:
        problems.append("workflow_call inputs drifted")
    if set((call.get("secrets") or {})) != expected_secrets:
        problems.append("workflow_call secrets drifted")
    expected_permissions = {"contents": "read", "pull-requests": "read"}
    if document.get("permissions") != {**expected_permissions, "actions": "read"}:
        problems.append("workflow GITHUB_TOKEN lacks its exact read-only scopes")

    jobs = document.get("jobs") or {}
    if set(jobs) != {"reject-public-pull-request-caller", "app-key-policy", "attribute"}:
        problems.append("workflow must contain exactly the public-call guard, policy, and attribution jobs")
        return problems
    reject_public = jobs["reject-public-pull-request-caller"]
    expected_public_call_guard = (
        "${{ (github.event_name == 'pull_request' || "
        "github.event_name == 'pull_request_review' || "
        "github.event_name == 'pull_request_review_comment' || "
        "github.event_name == 'merge_group' || "
        "github.event_name == 'pull_request_target') && "
        "github.event.repository.private != true }}"
    )
    if reject_public.get("if") != expected_public_call_guard:
        problems.append("public-call guard does not reject all five public events")
    if reject_public.get("runs-on") != "ubuntu-24.04" or reject_public.get("permissions") != {}:
        problems.append("public-call guard must use a permissionless hosted runner")
    reject_steps = reject_public.get("steps") or []
    if len(reject_steps) != 1 or "exit 1" not in reject_steps[0].get("run", ""):
        problems.append("public-call guard does not fail explicitly")
    job = jobs["attribute"]
    expected_runner = "${{ fromJSON(vars.CI_LANE_TRUSTED || vars.CI_LANE_FALLBACK || '[\"ubuntu-24.04\"]') }}"
    if job.get("runs-on") != expected_runner:
        problems.append("attribution does not use the trusted lane")
    if job.get("permissions") != expected_permissions:
        problems.append("job GITHUB_TOKEN lacks its exact read-only scopes")
    steps = job.get("steps") or []
    by_id = {step.get("id"): step for step in steps if step.get("id")}
    plan = by_id.get("plan") or {}
    mint = by_id.get("release-app-token") or {}
    checkout = next(
        (step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")),
        {},
    )
    apply_step = next(
        (step for step in steps if step.get("name") == "Add the fragment through the Git Data API"),
        {},
    )
    pin = next(
        (step for step in steps if step.get("name") == "Require an immutable contract revision"),
        {},
    )
    pin_env = pin.get("env") or {}
    pin_run = pin.get("run") or ""
    if (
        pin_env.get("CONTRACT_REF") != "${{ inputs.contract_ref }}"
        or pin_env.get("EXECUTING_WORKFLOW_SHA") != "${{ job.workflow_sha }}"
        or '[[ "$CONTRACT_REF" == "$EXECUTING_WORKFLOW_SHA" ]]' not in pin_run
    ):
        problems.append("contract input is not bound to the executing workflow SHA")
    if checkout.get("uses") != CHECKOUT:
        problems.append("trusted contract checkout is not pinned")
    if checkout.get("with") != {
        "repository": "Verjson/.github",
        "ref": "${{ job.workflow_sha }}",
        "path": ".renovate-changelog-contract-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}",
        "persist-credentials": False,
    }:
        problems.append("checkout can resolve consumer or pull-request code")
    plan_run = plan.get("run") or ""
    if plan.get("env", {}).get("GH_TOKEN") != "${{ github.token }}":
        problems.append("planning does not use the read-only Actions token")
    if '"$CONTRACT_PATH/scripts/renovate-changelog.py" plan' not in plan_run:
        problems.append("planning does not execute the trusted helper")
    if mint.get("uses") != APP_TOKEN:
        problems.append("release App token action is not immutable")
    if mint.get("if") != "steps.plan.outputs.required == 'true'":
        problems.append("release App token is minted for no-op runs")
    if mint.get("with") != {
        "client-id": "${{ inputs.release_app_client_id }}",
        "private-key": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-contents": "write",
    }:
        problems.append("release App token is not current-repository Contents-write only")
    if apply_step.get("if") != "steps.plan.outputs.required == 'true'":
        problems.append("write step does not honor the plan")
    apply_env = apply_step.get("env") or {}
    if apply_env.get("GH_READ_TOKEN") != "${{ github.token }}":
        problems.append("write step does not retain the PR-read Actions token")
    if apply_env.get("GH_TOKEN") != "${{ steps.release-app-token.outputs.token }}":
        problems.append("write step does not use the scoped App token")
    if '"$CONTRACT_PATH/scripts/renovate-changelog.py" apply' not in (apply_step.get("run") or ""):
        problems.append("write step does not execute the trusted helper")
    if steps.index(plan) > steps.index(mint) or steps.index(mint) > steps.index(apply_step):
        problems.append("secret mint or write occurs before admission")
    cleanup = next(
        (step for step in steps if step.get("name") == "Remove the isolated trusted contract checkout"),
        {},
    )
    cleanup_run = cleanup.get("run") or ""
    if (
        cleanup.get("if") != "always()"
        or '[[ "$CONTRACT_PATH" =~ ^\\.renovate-changelog-contract-' not in cleanup_run
        or 'rm -rf -- "${checkout_root:?}"' not in cleanup_run
    ):
        problems.append("isolated trusted checkout is not cleaned on every outcome")

    tree = ast.parse(helper_text)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    if imported & {"subprocess", "shlex"}:
        problems.append("helper imports a shell execution surface")
    forbidden_names = {"eval", "exec", "compile"}
    forbidden_attributes = {"system", "popen"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
                forbidden = forbidden_names
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
                forbidden = forbidden_attributes
            else:
                forbidden = set()
            if name in forbidden:
                problems.append(f"helper calls forbidden execution primitive {name}")
    return problems


def mutation(document: dict, description: str, mutate) -> int:
    changed = copy.deepcopy(document)
    mutate(changed)
    if validate(changed, HELPER.read_text(encoding="utf-8")):
        print(f"ok - rejects {description}")
        return 0
    print(f"FAIL - accepts {description}", file=sys.stderr)
    return 1


def main() -> int:
    raw = WORKFLOW.read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    helper = HELPER.read_text(encoding="utf-8")
    problems = validate(document, helper)
    for problem in problems:
        print(f"FAIL - {problem}", file=sys.stderr)
    failures = len(problems)
    if not problems:
        print("ok - trusted workflow admits, plans, mints, and writes in that order")
        print("ok - workflow never checks out or executes pull-request code")
        print("ok - write authority is a current-repository Contents-write App token")
        print("ok - helper exposes no shell execution primitive")

    def checkout_head(changed: dict) -> None:
        checkout = changed["jobs"]["attribute"]["steps"][1]
        checkout["with"]["repository"] = "${{ github.repository }}"
        checkout["with"]["ref"] = "${{ github.event.pull_request.head.sha }}"

    def broad_token(changed: dict) -> None:
        mint = changed["jobs"]["attribute"]["steps"][4]
        mint["with"].pop("repositories")

    def write_actions_token(changed: dict) -> None:
        changed["jobs"]["attribute"]["steps"][5]["env"]["GH_TOKEN"] = "${{ github.token }}"

    def mixed_contract_pin(changed: dict) -> None:
        changed["jobs"]["attribute"]["steps"][1]["with"]["ref"] = "${{ inputs.contract_ref }}"

    def mint_before_plan(changed: dict) -> None:
        steps = changed["jobs"]["attribute"]["steps"]
        steps[2], steps[4] = steps[4], steps[2]

    failures += mutation(document, "a pull-request-head checkout", checkout_head)
    failures += mutation(document, "an organization-wide App token", broad_token)
    failures += mutation(document, "a write through GITHUB_TOKEN", write_actions_token)
    failures += mutation(document, "a mixed reusable-workflow contract pin", mixed_contract_pin)
    failures += mutation(document, "secret minting before admission", mint_before_plan)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
