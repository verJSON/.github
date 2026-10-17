"""Semantic and mutation contract for release authorization App token minting."""

from __future__ import annotations

import copy
import pathlib
import re
import sys

import yaml


ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/changelog-release.yml"
TOKEN_ACTION = (
    "actions/create-github-app-token@"
    "bcd2ba49218906704ab6c1aa796996da409d3eb1"
)


def workflow_call(document: dict) -> dict:
    return document.get("on", document.get(True))["workflow_call"]


def validate(document: dict, raw: str) -> list[str]:
    problems: list[str] = []
    call = workflow_call(document)
    inputs = call.get("inputs") or {}
    secrets = call.get("secrets") or {}
    release = document["jobs"]["release"]
    steps = release.get("steps") or []
    mint = next((step for step in steps if step.get("id") == "release-app-token"), {})
    checkout = next(
        (step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")),
        {},
    )

    if document.get("permissions") != {"actions": "read", "contents": "read"}:
        problems.append("workflow GITHUB_TOKEN is not contents-read-only")
    if release.get("permissions") != {"contents": "read"}:
        problems.append("release-job GITHUB_TOKEN is not contents-read-only")
    if not (inputs.get("release_app_client_id") or {}).get("required"):
        problems.append("release_app_client_id is not required")
    if secrets or not (inputs.get("release_environment") or {}).get("required"):
        problems.append("release must require an environment instead of forwarded App keys")
    if mint.get("uses") != TOKEN_ACTION:
        problems.append("token action pin changed")
    expected_inputs = {
        "client-id": "${{ inputs.release_app_client_id }}",
        "private-key": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
        "owner": "${{ github.repository_owner }}",
        "repositories": "${{ github.event.repository.name }}",
        "permission-contents": "write",
    }
    if mint.get("with") != expected_inputs:
        problems.append("mint is not constrained to current-repository contents-write")
    if (checkout.get("with") or {}).get("token") != "${{ steps.release-app-token.outputs.token }}":
        problems.append("checkout does not persist the minted token")
    if "ORG_ADMIN_TOKEN" in raw or "secrets.push_token" in raw:
        problems.append("temporary broad release credential remains")
    if not re.search(
        r'\[\[ -z "\$RELEASE_APP_CLIENT_ID" \|\| "\$RELEASE_APP_CLIENT_ID" =~ \^\[0123456789\]\+\$ \]\]', raw
    ):
        problems.append("App client ID guard is absent")
    return problems


def require_rejected(label: str, document: dict, raw: str) -> None:
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
    print("ok - release token is pinned and repository-scoped")

    call = workflow_call(document)
    release = document["jobs"]["release"]
    steps = release["steps"]
    mint_index = next(i for i, step in enumerate(steps) if step.get("id") == "release-app-token")
    checkout_index = next(
        i for i, step in enumerate(steps) if str(step.get("uses", "")).startswith("actions/checkout@")
    )

    mutations = []

    mutant = copy.deepcopy(document)
    mutant["permissions"]["contents"] = "write"
    mutations.append(("a write-capable workflow GITHUB_TOKEN", mutant, raw))

    mutant = copy.deepcopy(document)
    mutant["jobs"]["release"]["permissions"]["contents"] = "write"
    mutations.append(("a write-capable release-job GITHUB_TOKEN", mutant, raw))

    mutable = copy.deepcopy(document)
    mutable["jobs"]["release"]["steps"][mint_index]["uses"] = "actions/create-github-app-token@v3"
    mutations.append(("a mutable token-action ref", mutable, raw))

    for key in ("owner", "repositories", "permission-contents"):
        mutant = copy.deepcopy(document)
        del mutant["jobs"]["release"]["steps"][mint_index]["with"][key]
        mutations.append((f"minting without {key}", mutant, raw))

    mutant = copy.deepcopy(document)
    mint_inputs = mutant["jobs"]["release"]["steps"][mint_index]["with"]
    mint_inputs["app-id"] = mint_inputs.pop("client-id")
    mutations.append(("the deprecated numeric app-id action input", mutant, raw))

    mutant = copy.deepcopy(document)
    mutant["jobs"]["release"]["steps"][mint_index]["with"]["permission-pull-requests"] = "write"
    mutations.append(("an extra App-token permission", mutant, raw))

    mutant = copy.deepcopy(document)
    mutant["jobs"]["release"]["steps"][checkout_index]["with"]["token"] = "${{ github.token }}"
    mutations.append(("checkout with GITHUB_TOKEN", mutant, raw))

    mutant = copy.deepcopy(document)
    workflow_call(mutant).setdefault("secrets", {})["push_token"] = {"required": True}
    mutations.append(("the temporary push_token secret", mutant, raw + "\nsecrets.push_token"))

    guardless = raw.replace(
        '[[ -z "$RELEASE_APP_CLIENT_ID" || "$RELEASE_APP_CLIENT_ID" =~ ^[0123456789]+$ ]]',
        "true",
    )
    mutations.append(("a missing App client ID guard", copy.deepcopy(document), guardless))

    for label, mutant, mutant_raw in mutations:
        require_rejected(label, mutant, mutant_raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
