#!/usr/bin/env python3
import copy
import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/runner-canary.yml"
EXPECTED_SHA256 = "601371372d2a8e70e95e15355540b906981d31d616d1625484394c99fa11ad8b"
IMMUTABLE_TAG = re.compile(r"^runner-canary-v[0-9]+\.[0-9]+\.[0-9]+$")


def load_workflow(path: Path = WORKFLOW) -> dict:
    with path.open(encoding="utf-8") as source:
        return yaml.safe_load(source)


def validate(workflow: dict) -> None:
    dispatch = workflow.get(True, workflow.get("on", {})).get("workflow_dispatch", {})
    expected_inputs = {
        "runner_name", "runner_id", "runner_label", "transaction_nonce",
        "release_manifest", "release_variant", "image_digest",
    }
    if set(dispatch.get("inputs", {})) != expected_inputs:
        raise AssertionError("dispatch inputs do not exactly bind the runner transaction")
    if not all(spec == {"required": True, "type": "string"} for spec in dispatch["inputs"].values()):
        raise AssertionError("every runner transaction input must be a required string")
    if workflow.get("permissions") != {"actions": "read", "contents": "read"}:
        raise AssertionError("workflow permissions exceed the receipt boundary")

    canary = workflow["jobs"]["canary"]
    if canary.get("environment") != "runner-fleet-production":
        raise AssertionError("canary must use the protected fleet environment")
    if canary.get("runs-on") != ["self-hosted", "${{ inputs.runner_label }}"]:
        raise AssertionError("canary must route only through the transaction-unique label")
    if canary.get("timeout-minutes") != 30:
        raise AssertionError("canary runtime must remain bounded")
    postgres = canary["services"]["postgres"]
    if not re.fullmatch(r"postgres@sha256:[0-9a-f]{64}", postgres.get("image", "")):
        raise AssertionError("database service image must be digest pinned")

    run_steps = [step for step in canary["steps"] if "run" in step]
    if len(run_steps) != 1:
        raise AssertionError("canary must have exactly one admission script")
    script = run_steps[0]["run"]
    required_guards = (
        'test "$RUNNER_NAME" = "$EXPECTED_RUNNER"', "pwsh --version",
        "postgres@sha256:", "docker network create", "curlimages/curl@sha256:",
        "nginx@sha256:", "FROM alpine@sha256:", "trap cleanup EXIT",
        "trap 'interrupted 130' INT", "trap 'interrupted 143' TERM",
        "trap 'record_cleanup_signal 130' INT", "trap 'record_cleanup_signal 143' TERM",
        "trap '' INT TERM", "final_signal_status=$cleanup_signal_status",
        "docker rm -f", "docker network rm", 'docker image rm "$build_image"',
        'docker container ls -a --filter "name=^/${sibling}$"',
        'docker network ls --filter "name=^${network}$"',
        'docker image ls --filter "reference=${build_image}"',
        'if [ "$final_status" -ne 0 ]; then',
        'test "$free_bytes" -ge 10737418240', 'test "$free_inodes" -ge 10',
        'contract:"verjson-runner-promotion/2"',
        '--arg workflowRef "$GITHUB_REF_NAME"',
        'headBranch:$workflowRef',
        'workflowPath:".github/workflows/runner-canary.yml"',
        'artifact:{name:"runner-promotion-receipt",contentDigest:""}',
    )
    missing = [guard for guard in required_guards if guard not in script]
    if missing:
        raise AssertionError(f"canary admission guards missing: {missing}")
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)

    upload_steps = [step for step in canary["steps"] if "uses" in step]
    if len(upload_steps) != 1:
        raise AssertionError("canary must publish exactly one receipt artifact")
    upload = upload_steps[0]
    if not re.fullmatch(r"actions/upload-artifact@[0-9a-f]{40}", upload["uses"]):
        raise AssertionError("receipt upload action must be commit pinned")
    if upload.get("with") != {
        "name": "runner-promotion-receipt", "path": "runner-promotion-receipt.json",
        "if-no-files-found": "error", "retention-days": 7,
    }:
        raise AssertionError("receipt artifact identity or retention widened")


def run_cleanup_harness(
    terminal_command: str,
    *,
    fail_cleanup_query: bool = False,
    wrong_cleanup_target: str | None = None,
) -> tuple[int, list[str], list[str]]:
    admission = load_workflow()["jobs"]["canary"]["steps"][0]["run"]
    start = admission.index("cleanup_signal_status=0")
    trap_line = "trap 'interrupted 143' TERM"
    end = admission.index(trap_line, start) + len(trap_line)
    cleanup_contract = admission[start:end]
    if wrong_cleanup_target is not None:
        commands = {
            "sibling": ('docker rm -f "$sibling"', 'docker rm -f "wrong-sibling"'),
            "network": (
                'docker network rm "$network"',
                'docker network rm "wrong-network"',
            ),
            "image": (
                'docker image rm "$build_image"',
                'docker image rm "wrong-image"',
            ),
        }
        original, mutation = commands[wrong_cleanup_target]
        mutated_contract = cleanup_contract.replace(original, mutation, 1)
        if mutated_contract == cleanup_contract:
            raise AssertionError(f"cleanup command not found for {wrong_cleanup_target}")
        cleanup_contract = mutated_contract
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        bin_dir = root / "bin"
        resources = root / "resources"
        capacity_state = root / "capacity-state"
        bin_dir.mkdir()
        resources.mkdir()
        capacity_state.mkdir(mode=0o700)
        capacity_id = "a" * 64
        capacity_cidfile = capacity_state / "container.cid"
        capacity_cidfile.write_text(capacity_id, encoding="utf-8")
        for resource in ("sibling", "capacity", "network", "image"):
            (resources / resource).touch()
        docker = bin_dir / "docker"
        docker.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$MOCK_DOCKER_LOG"
if [ "$#" -eq 3 ] && [ "$1" = rm ] && [ "$2" = -f ] && [ "$3" = "$MOCK_SIBLING" ]; then
  rm -f "$MOCK_RESOURCES/sibling"
elif [ "$#" -eq 3 ] && [ "$1" = rm ] && [ "$2" = -f ] && [ "$3" = "$MOCK_CAPACITY_ID" ]; then
  rm -f "$MOCK_RESOURCES/capacity"
elif [ "$#" -eq 8 ] && [ "$1" = container ] && [ "$2" = ls ] && [ "$3" = -a ] &&
  [ "$4" = --no-trunc ] && [ "$5" = --filter ] && [ "$6" = "id=$MOCK_CAPACITY_ID" ] &&
  [ "$7" = --format ] && [ "$8" = '{{.ID}}' ]; then
  [ "${MOCK_FAIL_CLEANUP_QUERY:-0}" != 1 ] || exit 2
  if [ -e "$MOCK_RESOURCES/capacity" ]; then printf '%s\n' "$MOCK_CAPACITY_ID"; fi
elif [ "$#" -eq 7 ] && [ "$1" = container ] && [ "$2" = ls ] && [ "$3" = -a ] &&
  [ "$4" = --filter ] && [ "$5" = "name=^/$MOCK_SIBLING$" ] && [ "$6" = --format ] &&
  [ "$7" = '{{.Names}}' ]; then
  if [ -e "$MOCK_RESOURCES/sibling" ]; then printf '%s\n' "$MOCK_SIBLING"; fi
elif [ "$#" -eq 3 ] && [ "$1" = network ] && [ "$2" = rm ] && [ "$3" = "$MOCK_NETWORK" ]; then
  rm -f "$MOCK_RESOURCES/network"
elif [ "$#" -eq 6 ] && [ "$1" = network ] && [ "$2" = ls ] && [ "$3" = --filter ] &&
  [ "$4" = "name=^$MOCK_NETWORK$" ] && [ "$5" = --format ] && [ "$6" = '{{.Name}}' ]; then
  if [ -e "$MOCK_RESOURCES/network" ]; then printf '%s\n' "$MOCK_NETWORK"; fi
elif [ "$#" -eq 3 ] && [ "$1" = image ] && [ "$2" = rm ] && [ "$3" = "$MOCK_IMAGE" ]; then
  rm -f "$MOCK_RESOURCES/image"
elif [ "$#" -eq 6 ] && [ "$1" = image ] && [ "$2" = ls ] && [ "$3" = --filter ] &&
  [ "$4" = "reference=$MOCK_IMAGE" ] && [ "$5" = --format ] &&
  [ "$6" = '{{.Repository}}:{{.Tag}}' ]; then
  if [ -e "$MOCK_RESOURCES/image" ]; then printf '%s\n' "$MOCK_IMAGE"; fi
else
  exit 64
fi
""",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        harness = f"""set -Eeuo pipefail
network=network
sibling=sibling
capacity_id={capacity_id}
capacity_owned=1
capacity_owner={'b' * 32}
capacity_state={capacity_state}
capacity_cidfile={capacity_cidfile}
build_image=image
{cleanup_contract}
{terminal_command}
"""
        log = root / "docker.log"
        environment = os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "MOCK_CAPACITY_ID": capacity_id,
            "MOCK_DOCKER_LOG": str(log),
            "MOCK_FAIL_CLEANUP_QUERY": "1" if fail_cleanup_query else "0",
            "MOCK_IMAGE": "image",
            "MOCK_NETWORK": "network",
            "MOCK_RESOURCES": str(resources),
            "MOCK_SIBLING": "sibling",
        }
        result = subprocess.run(
            ["bash"], input=harness, text=True, env=environment, cwd=root, check=False
        )
        survivors = sorted(path.name for path in resources.iterdir())
        if capacity_state.exists():
            survivors.append(capacity_state.name)
        calls = log.read_text(encoding="utf-8").splitlines()
        return result.returncode, survivors, calls


class RunnerCanaryContractTests(unittest.TestCase):
    def test_published_workflow_is_exact_reviewed_cli_contract(self) -> None:
        self.assertEqual(EXPECTED_SHA256, hashlib.sha256(WORKFLOW.read_bytes()).hexdigest())
        validate(load_workflow())

    def test_mutable_dependencies_and_target_widening_fail_closed(self) -> None:
        original = load_workflow()
        mutations = []
        mutable_service = copy.deepcopy(original)
        mutable_service["jobs"]["canary"]["services"]["postgres"]["image"] = "postgres:17-alpine"
        mutations.append(mutable_service)
        widened_target = copy.deepcopy(original)
        widened_target["jobs"]["canary"]["runs-on"] = ["self-hosted", "general"]
        mutations.append(widened_target)
        mutable_action = copy.deepcopy(original)
        mutable_action["jobs"]["canary"]["steps"][-1]["uses"] = "actions/upload-artifact@v4"
        mutations.append(mutable_action)
        missing_identity = copy.deepcopy(original)
        missing_identity["jobs"]["canary"]["steps"][0]["run"] = missing_identity["jobs"]["canary"]["steps"][0]["run"].replace(
            'test "$RUNNER_NAME" = "$EXPECTED_RUNNER"\n', ""
        )
        mutations.append(missing_identity)
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index), self.assertRaises(AssertionError):
                validate(mutation)

    def test_publication_ref_is_semver_and_not_a_branch_or_moving_major(self) -> None:
        self.assertTrue(IMMUTABLE_TAG.fullmatch("runner-canary-v1.0.0"))
        for rejected in ("main", "runner-canary-v1", "runner-canary-v1.0", "runner-canary-v1.0.0-rc.1"):
            with self.subTest(ref=rejected):
                self.assertIsNone(IMMUTABLE_TAG.fullmatch(rejected))

    def test_capacity_probe_keeps_the_reviewed_trust_boundary(self) -> None:
        script = load_workflow()["jobs"]["canary"]["steps"][0]["run"]
        required = (
            "mktemp -d -p",
            "verjson.runner-canary-owner",
            "timeout --signal=KILL 30s",
            "--network none",
            "--read-only --cap-drop ALL",
            "stable_empty",
            "docker container ls -a --no-trunc",
            "docker rm -f \"$capacity_id\"",
            "test \"$free_bytes\" -ge 10737418240",
            "test \"$free_inodes\" -ge 10",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, script)
        self.assertNotIn("/var/lib/docker", script)
        self.assertNotIn("type=bind", script)

    def test_int_and_term_preserve_status_and_cleanup_owned_resources(self) -> None:
        for signal, expected in (("INT", 130), ("TERM", 143)):
            with self.subTest(signal=signal):
                status, survivors, calls = run_cleanup_harness(f"kill -{signal} $$")
                self.assertEqual(expected, status)
                self.assertEqual([], survivors)
                self.assertTrue(any(call.startswith("rm -f") for call in calls))
                self.assertTrue(any(call.startswith("network rm") for call in calls))
                self.assertTrue(any(call.startswith("image rm") for call in calls))

    def test_cleanup_query_failure_does_not_mask_the_terminal_status(self) -> None:
        status, survivors, _ = run_cleanup_harness("exit 42", fail_cleanup_query=True)

        self.assertEqual(42, status)
        self.assertEqual([], survivors)

    def test_cleanup_failure_is_terminal_after_an_otherwise_successful_run(self) -> None:
        status, survivors, _ = run_cleanup_harness("exit 0", fail_cleanup_query=True)

        self.assertEqual(1, status)
        self.assertEqual([], survivors)

    def test_wrong_cleanup_targets_leave_owned_resources_and_fail(self) -> None:
        for resource in ("sibling", "network", "image"):
            with self.subTest(resource=resource):
                status, survivors, calls = run_cleanup_harness(
                    "exit 0", wrong_cleanup_target=resource
                )
                self.assertEqual(1, status)
                self.assertEqual([resource], survivors)
                self.assertIn(f"wrong-{resource}", "\n".join(calls))

if __name__ == "__main__":
    unittest.main()
