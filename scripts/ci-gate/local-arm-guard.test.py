#!/usr/bin/env python3
"""Executes the local arm guard (ADR 0255 amendment 2026-11-06, F-1R) against a stubbed gh.

The guard decides whether a repository's generated `gate re-arm` caller still runs its
local arm. It yields (local_arm=false) only on a well-typed rules response naming the
organization's required arm; every other outcome keeps today's behavior (local_arm=true).
Each scenario feeds the real helper one rules response and asserts the decision it wrote
to GITHUB_OUTPUT and the reason it logged.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts/local-arm-guard.sh"
REPOSITORY = "verJSON/verjson-ai"
ORG_ARM_REPOSITORY_ID = 1269388380
ORG_ARM_PATH = ".github/workflows/gate-rearm.yml"

# Records every call; answers the rules read with the scenario's body and exit status.
GH_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >>"$STUB_DIR/calls"
cat "$STUB_DIR/body"
[ ! -s "$STUB_DIR/stderr" ] || cat "$STUB_DIR/stderr" >&2
exit "$(cat "$STUB_DIR/status")"
"""


def arm_rule(repository_id=ORG_ARM_REPOSITORY_ID, path=ORG_ARM_PATH):
    """The live verjson-ai shape of ruleset 20722935's rule, read 2026-11-06."""
    return {
        "type": "workflows",
        "ruleset_id": 20722935,
        "ruleset_source_type": "Organization",
        "ruleset_source": "verJSON",
        "parameters": {
            "do_not_enforce_on_create": True,
            "workflows": [{
                "path": path,
                "ref": "refs/heads/main",
                "repository_id": repository_id,
                "sha": "24db445fe410df7bfbd740d8766b2d9ad8e73915",
            }],
        },
    }


def run_guard(body, status=0, event_name="pull_request_target", action="synchronize",
              base_ref="main", stderr=""):
    """Runs the helper; returns (outputs, stdout, gh calls)."""
    with tempfile.TemporaryDirectory() as tmp:
        stub_dir = Path(tmp)
        bin_dir = stub_dir / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(GH_STUB, encoding="utf-8")
        gh.chmod(0o755)
        (stub_dir / "body").write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
        (stub_dir / "status").write_text(str(status), encoding="utf-8")
        (stub_dir / "stderr").write_text(stderr, encoding="utf-8")
        (stub_dir / "calls").write_text("", encoding="utf-8")
        output = stub_dir / "github_output"
        output.write_text("", encoding="utf-8")
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": tmp,
            "STUB_DIR": str(stub_dir),
            "GITHUB_OUTPUT": str(output),
            "GH_TOKEN": "stub-token",
            "EVENT_NAME": event_name,
            "EVENT_ACTION": action,
            "REPOSITORY": REPOSITORY,
            "BASE_REF": base_ref,
        }
        # The generated step declares `shell: bash`, which Actions runs as this command.
        completed = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(GUARD)],
                                   env=env, capture_output=True, text=True, check=False)
        assert completed.returncode == 0, (completed.returncode, completed.stdout, completed.stderr)
        lines = output.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1, f"the guard must write exactly one decision, wrote {lines!r}"
        outputs = dict(line.split("=", 1) for line in lines)
        calls = (stub_dir / "calls").read_text(encoding="utf-8").splitlines()
        return outputs, completed.stdout, calls


def test_a_well_typed_organization_arm_rule_yields_the_local_arm():
    outputs, stdout, _ = run_guard([arm_rule()])
    assert outputs == {"local_arm": "false"}, (outputs, stdout)


def test_a_ref_whose_rules_name_no_organization_arm_keeps_the_local_arm():
    required_checks = {"type": "required_status_checks", "ruleset_id": 1,
                       "parameters": {"required_status_checks": [{"context": "ci"}]}}
    outputs, stdout, _ = run_guard([required_checks])
    assert outputs == {"local_arm": "true"}, (outputs, stdout)
    assert f"no workflows rule names {ORG_ARM_PATH}" in stdout, stdout


def test_the_arm_path_from_another_repository_keeps_the_local_arm():
    outputs, stdout, _ = run_guard([arm_rule(repository_id=42)])
    assert outputs == {"local_arm": "true"}, (outputs, stdout)
    assert f"names {ORG_ARM_PATH} from repository 42, not the organization arm" in stdout, stdout
    # The organization arm beside another repository's workflow at the same path is
    # ambiguous: a run reports its path but not the repository its workflow came from.
    shared = arm_rule()
    shared["parameters"]["workflows"].append({"path": ORG_ARM_PATH, "repository_id": 42, "ref": "refs/heads/main"})
    outputs, stdout, _ = run_guard([shared])
    assert outputs == {"local_arm": "true"}, (outputs, stdout)
    assert f"names {ORG_ARM_PATH} from repository 42, not the organization arm" in stdout, stdout


def test_a_failed_rules_read_keeps_the_local_arm_and_names_the_failure():
    outputs, stdout, _ = run_guard("", status=1, stderr="HTTP 403: Resource not accessible by integration")
    assert outputs == {"local_arm": "true"}, (outputs, stdout)
    assert f"could not read repos/{REPOSITORY}/rules/branches/main" in stdout, stdout
    assert "HTTP 403: Resource not accessible by integration" in stdout, stdout


def test_a_rules_body_that_is_not_json_keeps_the_local_arm():
    outputs, stdout, _ = run_guard("<html>bad gateway</html>")
    assert outputs == {"local_arm": "true"}, (outputs, stdout)
    assert "the rules response is not JSON" in stdout, stdout


def test_a_rules_body_that_is_not_an_array_keeps_the_local_arm():
    for body in ({"message": "Not Found"}, None, ""):
        outputs, stdout, _ = run_guard(body)
        assert outputs == {"local_arm": "true"}, (body, outputs, stdout)
        assert "the rules response is not a JSON array" in stdout, (body, stdout)


def malformed(mutate):
    rule = arm_rule()
    mutate(rule)
    return rule


def test_a_rule_the_guard_cannot_read_keeps_the_local_arm_beside_a_well_typed_arm_rule():
    unreadable = {
        "a non-object rule": "workflows",
        "a string ruleset id": malformed(lambda rule: rule.update(ruleset_id="20722935")),
        "no workflows list": malformed(lambda rule: rule["parameters"].update(workflows=None)),
        "an empty workflows list": malformed(lambda rule: rule["parameters"].update(workflows=[])),
        "a non-object workflow entry": malformed(lambda rule: rule["parameters"]["workflows"].append(ORG_ARM_PATH)),
        "a string repository id": malformed(
            lambda rule: rule["parameters"]["workflows"][0].update(repository_id=str(ORG_ARM_REPOSITORY_ID))),
        "a non-string path": malformed(lambda rule: rule["parameters"]["workflows"][0].update(path=None)),
    }
    for shape, rule in unreadable.items():
        outputs, stdout, _ = run_guard([arm_rule(), rule])
        assert outputs == {"local_arm": "true"}, (shape, outputs, stdout)
        assert "the rules response carries a rule the guard cannot read" in stdout, (shape, stdout)


def test_only_head_transition_pull_request_target_events_consult_the_rules():
    # The label and lifecycle callers reach this workflow through workflow_call, where
    # the event is the caller's; their path stays unchanged (ARB-G1-2026-11-06).
    for event_name, action in (("pull_request_target", "labeled"), ("pull_request_target", "edited"),
                               ("pull_request_target", "ready_for_review"), ("pull_request", "synchronize"),
                               ("workflow_dispatch", "")):
        outputs, stdout, calls = run_guard([arm_rule()], event_name=event_name, action=action)
        assert outputs == {"local_arm": "true"}, (event_name, action, outputs, stdout)
        assert f"{event_name}/{action} is not a head transition the organization arm receives" in stdout, stdout
        assert calls == [], (event_name, action, calls)
    for action in ("opened", "reopened", "synchronize"):
        outputs, stdout, calls = run_guard([arm_rule()], action=action)
        assert outputs == {"local_arm": "false"}, (action, outputs, stdout)


def test_the_base_ref_is_read_as_one_encoded_path_segment_across_every_page():
    outputs, stdout, calls = run_guard([arm_rule()], base_ref="release/1.x")
    assert outputs == {"local_arm": "false"}, (outputs, stdout)
    assert calls == [f"api --paginate repos/{REPOSITORY}/rules/branches/release%2F1.x?per_page=100"], calls


def test_an_arm_rule_on_a_later_page_is_read():
    first_page = [{"type": "required_status_checks", "ruleset_id": 1,
                   "parameters": {"required_status_checks": [{"context": "ci"}]}}] * 100
    outputs, stdout, _ = run_guard(json.dumps(first_page) + json.dumps([arm_rule()]))
    assert outputs == {"local_arm": "false"}, (outputs, stdout)


def test_a_logged_reason_cannot_start_a_workflow_command():
    _, stdout, _ = run_guard("", status=1, stderr="HTTP 502\n::error::forged\r::warning::forged")
    assert not any(line.startswith("::") for line in stdout.splitlines()), stdout
    assert "::error::forged" in stdout, stdout


def run_test(named_test):
    name, test = named_test
    try:
        test()
    except Exception as error:  # noqa: BLE001 - report every failing behavior, not only the first
        return f"FAIL {name}: {type(error).__name__}: {error}"
    return f"ok   {name}"


def main():
    tests = [(name, value) for name, value in globals().items() if name.startswith("test_") and callable(value)]
    results = [run_test(test) for test in tests]
    print("\n".join(results))
    failures = sum(result.startswith("FAIL") for result in results)
    if failures:
        raise SystemExit(f"{failures} of {len(tests)} local arm guard behaviors failed")
    print(f"all {len(tests)} local arm guard behaviors passed")


if __name__ == "__main__":
    main()
