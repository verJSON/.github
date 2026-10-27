#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/../.." && pwd)"
workflow="$root/.github/workflows/verjson-ci.yml"
adoption="$root/docs/verjson-ci-adoption.md"
fixture="$root/scripts/ci-gate/fixtures/verjson-ci-reusable-c9084daca387849c09f1d97bccf8ac311ff11615.yml"
provenance="$root/scripts/ci-gate/fixtures/verjson-ci-reusable-c9084daca387849c09f1d97bccf8ac311ff11615.json"

python3 - "$workflow" "$adoption" "$fixture" "$provenance" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

workflow_path, adoption_path, fixture_path, provenance_path = map(Path, sys.argv[1:])
workflow_text = workflow_path.read_text(encoding="utf-8")
adoption_text = adoption_path.read_text(encoding="utf-8")
fixture_text = fixture_path.read_text(encoding="utf-8")
provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
EXPECTED_JOBS = {"ci", "reject-public-profile", "reject-profile", "candidate"}
REMOTE = "Verjson/verjson-ci/.github/workflows/reusable-ci.yml@c9084daca387849c09f1d97bccf8ac311ff11615"


def parse_workflow(text: str) -> dict:
    return yaml.load(text, Loader=yaml.BaseLoader)


def on_section(document: dict) -> dict:
    return document.get("on", document.get(True, {}))


def validate(workflow: str, adoption: str, fixture: str, source_provenance: dict) -> None:
    assert source_provenance == {
        "repository": "Verjson/verjson-ci",
        "path": ".github/workflows/reusable-ci.yml",
        "commit": "c9084daca387849c09f1d97bccf8ac311ff11615",
        "sha256": "51aa1dc4fb34a04a5ce939a5740e2654f1a82d10733406c16c267bc81029f3ef",
        "retrieved": "2026-10-27",
        "retrieval": "Authenticated GitHub Contents API",
    }
    assert hashlib.sha256(fixture.encode("utf-8")).hexdigest() == source_provenance["sha256"]
    assert "GitHub organizations" in adoption
    assert "GitLab installations" in adoption
    assert "$CI_SERVER_FQDN/" in adoption
    assert "CI_PROJECT_ID" in adoption
    assert "GitHub token/OIDC" in adoption
    assert "GitLab job token/OIDC" in adoption

    source = parse_workflow(fixture)
    source_call = on_section(source)["workflow_call"]
    assert set(source_call["inputs"]) == {"profile", "image", "config", "result_copy", "scenario"}
    source_jobs = source["jobs"]
    source_ci = source_jobs["ci"]
    source_steps = source_ci["steps"]

    document = parse_workflow(workflow)
    triggers = on_section(document)
    assert set(triggers) == {"workflow_call"}
    inputs = triggers["workflow_call"]["inputs"]
    assert set(inputs) == {"profile", "image", "config", "scenario"}
    assert inputs["image"]["required"] == "true"
    assert inputs["profile"]["default"] == "public-v1"
    assert "secrets" not in triggers["workflow_call"]
    assert document["permissions"] == {"contents": "read"}

    jobs = document["jobs"]
    assert set(jobs) == EXPECTED_JOBS
    assert "result_copy" not in workflow
    assert "actions: write" not in workflow
    assert "github.token" not in workflow

    ci = jobs["ci"]
    assert "inputs.profile == 'public-v1'" in ci["if"]
    selector = ci["runs-on"]
    source_selector = source_ci["runs-on"].removeprefix("${{ ").removesuffix(" }}")
    assert selector == "ubuntu-24.04"
    assert "ubuntu-24.04" in source_selector

    steps = ci["steps"]
    assert steps[0]["uses"] == source_steps[0]["uses"]
    assert steps[0]["with"]["persist-credentials"] == "false"

    source_validate = source_steps[1]
    local_validate = steps[1]
    assert local_validate["name"] == source_validate["name"]
    assert local_validate["env"] == source_validate["env"]
    assert local_validate["run"] == source_validate["run"]

    source_run = source_steps[2]["run"]
    local_run = steps[2]["run"]
    assert source_run.replace(
        '--env VERJSON_CI_SCENARIO="${{ inputs.scenario }}"',
        '--env VERJSON_CI_SCENARIO="$VERJSON_CI_SCENARIO"',
    ) == local_run
    assert steps[2]["env"] == {
        "VERJSON_CI_CONFIG": "${{ inputs.config }}",
        "VERJSON_CI_IMAGE": "${{ inputs.image }}",
        "VERJSON_CI_SCENARIO": "${{ inputs.scenario }}",
    }
    assert "${{ inputs.scenario }}" not in local_run

    source_upload = source_steps[4]
    local_upload = steps[3]
    assert local_upload["uses"] == "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
    assert local_upload["if"] == source_upload["if"]
    assert local_upload["with"] == source_upload["with"]

    reject_public = jobs["reject-public-profile"]
    assert "github.event.repository.private != true" in reject_public["if"]
    assert "inputs.profile != 'public-v1'" in reject_public["if"]
    assert reject_public["runs-on"] == "ubuntu-24.04"
    assert "exit 2" in reject_public["steps"][0]["run"]

    reject_profile = jobs["reject-profile"]
    expected_candidate_rejection = (
        "${{ inputs.profile != 'public-v1' && "
        "(inputs.profile != 'approved-candidate-v1' || "
        "vars.VERJSON_CANDIDATE_WORKERS_ENABLED != 'true' || "
        "github.repository != 'Verjson/verjson-ci' || "
        "github.ref != 'refs/heads/main' || "
        "github.event.repository.private != true) }}"
    )
    assert reject_profile["if"] == expected_candidate_rejection

    def candidate_profile_rejected(profile, flag, repository, ref, is_private):
        return profile != "public-v1" and (
            profile != "approved-candidate-v1"
            or flag != "true"
            or repository != "Verjson/verjson-ci"
            or ref != "refs/heads/main"
            or is_private is not True
        )

    assert candidate_profile_rejected(
        "approved-candidate-v1", "true", "Other/private-ci", "refs/heads/main", True
    )
    assert not candidate_profile_rejected(
        "approved-candidate-v1", "true", "Verjson/verjson-ci", "refs/heads/main", True
    )
    assert reject_profile["runs-on"] == "ubuntu-24.04"
    assert "exit 2" in reject_profile["steps"][0]["run"]

    candidate = jobs["candidate"]
    assert candidate["uses"] == REMOTE
    source_candidate_if = source_jobs["candidate"]["if"].removeprefix("${{ ").removesuffix(" }}")
    candidate_if = candidate["if"].removeprefix("${{ ").removesuffix(" }}")
    assert candidate_if == source_candidate_if + " && github.event.repository.private == true"
    assert "vars.VERJSON_CANDIDATE_WORKERS_ENABLED" in candidate_if
    assert candidate["with"] == {
        "profile": "${{ inputs.profile }}",
        "image": "${{ inputs.image }}",
        "config": "${{ inputs.config }}",
        "scenario": "${{ inputs.scenario }}",
    }
    assert candidate["permissions"] == {"actions": "read", "contents": "read"}


validate(workflow_text, adoption_text, fixture_text, provenance)
print("ok - public-v1 adapter matches its pinned source with only reviewed safety changes")
print("ok - public PR paths are hosted and unsupported public profiles fail explicitly")
print("ok - protected candidate delegation is private, main-branch dispatch only")
print("ok - adopter boundary remains provider-neutral")

mutants = {
    "mutable package ref": (
        workflow_text.replace(REMOTE, "Verjson/verjson-ci/.github/workflows/reusable-ci.yml@main"),
        adoption_text,
        fixture_text,
        provenance,
    ),
    "forwarded secrets": (workflow_text.replace("  permissions:\n", "  secrets: inherit\n\n  permissions:\n"), adoption_text, fixture_text, provenance),
    "missing required image": (workflow_text.replace("        required: true\n        type: string\n      config:", "        required: false\n        type: string\n      config:"), adoption_text, fixture_text, provenance),
    "unsafe public runner": (workflow_text.replace("    runs-on: ubuntu-24.04\n", "    runs-on: ${{ fromJSON(vars.CI_LANE_UNTRUSTED) }}\n", 1), adoption_text, fixture_text, provenance),
    "unknown visibility trusted": (workflow_text.replace("github.event.repository.private != true", "github.event.repository.private == false"), adoption_text, fixture_text, provenance),
    "direct scenario interpolation": (workflow_text.replace('--env VERJSON_CI_SCENARIO="$VERJSON_CI_SCENARIO"', '--env VERJSON_CI_SCENARIO="${{ inputs.scenario }}"'), adoption_text, fixture_text, provenance),
    "public candidate delegation": (workflow_text.replace("github.event.repository.private == true", "github.event.repository.private != true"), adoption_text, fixture_text, provenance),
    "private noncanonical candidate has no rejection": (
        workflow_text.replace(
            "github.repository != 'Verjson/verjson-ci'",
            "github.repository == 'Verjson/verjson-ci'",
            1,
        ),
        adoption_text,
        fixture_text,
        provenance,
    ),
    "exposed result copy": (workflow_text.replace("      scenario:\n", "      result_copy:\n        required: false\n        type: string\n      scenario:\n"), adoption_text, fixture_text, provenance),
    "unsafe sibling job": (workflow_text.replace(
        "  candidate:\n",
        "  unsafe-sibling:\n    runs-on: ${{ vars.CI_LANE_TRUSTED }}\n    steps:\n      - run: echo unsafe\n\n  candidate:\n",
    ), adoption_text, fixture_text, provenance),
    "fixture digest drift": (workflow_text, adoption_text, fixture_text + "# mutation\n", provenance),
    "provider-specific workflow claim": (workflow_text, adoption_text.replace("GitLab installations", "GitLab only"), fixture_text, provenance),
}

for name, (workflow_mutant, adoption_mutant, fixture_mutant, provenance_mutant) in mutants.items():
    try:
        validate(workflow_mutant, adoption_mutant, fixture_mutant, provenance_mutant)
    except (AssertionError, KeyError, TypeError, yaml.YAMLError):
        print(f"ok - {name} fails closed")
    else:
        raise AssertionError(f"{name} passed")
PY
