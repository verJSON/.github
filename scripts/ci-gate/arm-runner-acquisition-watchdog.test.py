#!/usr/bin/env python3
"""Executes the arm's runner-acquisition watchdog step against stubbed gh, date, and sleep.

Each scenario feeds the real step script a sequence of Actions jobs-API snapshots and a
controllable clock, then asserts which run-cancellation calls it made and how it exited.
"""
import concurrent.futures
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zlib

import yaml

ROOT = Path(__file__).resolve().parents[2]
ARM = ROOT / ".github/workflows/gate-rearm.yml"
KEY_POLICY = ROOT / ".github/workflows/app-key-environment.yml"
WATCHDOG = "runner-acquisition-watchdog"
START = int(datetime(2026, 10, 5, 18, 2, 30, tzinfo=timezone.utc).timestamp())
REPOSITORY = "Verjson/verjson-payments"
RUN_ID = "37352915856"

# Every call is logged with the stubbed clock. Any request outside this run's own attempt
# listing and its two cancellation endpoints is recorded as a violation that fails the scenario.
GH_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s %s\n' "$(cat "$STUB_DIR/clock")" "$*" >>"$STUB_DIR/calls"
own_run="repos/$STUB_REPOSITORY/actions/runs/$STUB_RUN_ID"
if [ "$1 $2" = "api --method" ]; then
  case "$#:$3 $4" in
    "4:POST $own_run/cancel"|"4:POST $own_run/force-cancel") ;;
    *) printf '%s\n' "$*" >>"$STUB_DIR/violations" ;;
  esac
  exit 0
fi
if [ "$#" -ne 2 ] || [ "$1" != api ] || [ "$2" != "$own_run/attempts/$STUB_RUN_ATTEMPT/jobs?per_page=100" ]; then
  printf '%s\n' "$*" >>"$STUB_DIR/violations"
fi
index=$(cat "$STUB_DIR/polls")
printf '%s\n' $((index + 1)) >"$STUB_DIR/polls"
count=$(cat "$STUB_DIR/count")
[ "$index" -lt "$count" ] || index=$((count - 1))
snapshot=$(cat "$STUB_DIR/snapshot-$index.json")
if [ "$snapshot" = '"error"' ]; then
  echo 'HTTP 502: Server Error' >&2
  exit 1
fi
if [ "$snapshot" = '"malformed"' ]; then
  printf '<html>bad gateway</html>\n'
  exit 0
fi
printf '%s\n' "$snapshot"
"""

DATE_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
[ "$*" = "-u +%s" ] || { echo "unexpected date arguments: $*" >&2; exit 2; }
cat "$STUB_DIR/clock"
"""

SLEEP_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
[[ "$1" =~ ^[1-9][0-9]*$ ]] || { echo "unexpected sleep argument: $1" >&2; exit 2; }
printf '%s\n' $(( $(cat "$STUB_DIR/clock") + $1 )) >"$STUB_DIR/clock"
printf 'sleep %s\n' "$1" >>"$STUB_DIR/calls"
"""


def iso(seconds_after_start):
    return datetime.fromtimestamp(START + seconds_after_start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


MISSING = object()


def job(name, status, created_offset=0, labels=("self-hosted", "general")):
    listed = {"id": zlib.crc32(name.encode()) + 1, "name": name, "status": status,
              "labels": None if labels is None else list(labels),
              "runner_name": None if status == "queued" else "gha-general-1"}
    if created_offset is not MISSING:
        listed["created_at"] = iso(created_offset)
    return listed


def snapshot(*jobs):
    """One attempt's listing, which always includes the polling watchdog itself."""
    prefix = jobs[0]["name"][:-len("event-policy")]
    listed = [*jobs, job(prefix + WATCHDOG, "in_progress", 3, ("ubuntu-24.04",))]
    return {"total_count": len(listed), "jobs": listed}


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def watchdog_job():
    return load(ARM)["jobs"][WATCHDOG]


ESCALATION_GUARD = "${{ always() && steps.detect.outputs.stalled == 'true' }}"
RUNNER_GUARD = '[ "${RUNNER_ENVIRONMENT:-}" = github-hosted ] || {'


HOSTED_LANE = '["ubuntu-24.04"]'
EXPRESSION_TOKEN = re.compile(r"\s*(\|\||&&|==|!=|\(|\)|,|'(?:[^']|'')*'|[A-Za-z_][A-Za-z0-9_.-]*)")


def evaluate(expression, context):
    """Evaluate a GitHub Actions expression over the subset these jobs use.

    Models the documented semantics that matter here: an unset `vars` entry is the empty
    string, `||`/`&&` return an operand, string `==` ignores case, and `contains` is a
    case-insensitive substring test. Any other construct is rejected rather than guessed.
    """
    body = re.fullmatch(r"\$\{\{ (.+) \}\}", expression.strip(), re.S)
    text = body[1] if body else expression
    tokens, position = [], 0
    while position < len(text):
        match = EXPRESSION_TOKEN.match(text, position)
        assert match and match.end() > position, f"unmodelled expression syntax at {text[position:]!r}"
        tokens.append(match[1])
        position = match.end()
        if not text[position:].strip():
            break
    cursor = [0]

    def peek():
        return tokens[cursor[0]] if cursor[0] < len(tokens) else None

    def take(expected=None):
        token = peek()
        assert expected is None or token == expected, (expected, token, text)
        cursor[0] += 1
        return token

    def truthy(value):
        return value not in ("", None, False, 0)

    def primary():
        token = take()
        if token == "(":
            value = disjunction()
            take(")")
            return value
        if token.startswith("'"):
            return token[1:-1].replace("''", "'")
        if peek() == "(":
            take("(")
            arguments = [disjunction()]
            while peek() == ",":
                take(",")
                arguments.append(disjunction())
            take(")")
            assert token == "contains", f"unmodelled function {token}"
            haystack, needle = arguments
            return str(needle).lower() in str(haystack).lower()
        if token.startswith("vars."):
            return context.get(token, "")
        assert token in context, f"unmodelled context {token}"
        return context[token]

    def comparison():
        value = primary()
        if peek() in ("==", "!="):
            operator = take()
            other = primary()
            equal = (str(value).lower() == str(other).lower()
                     if isinstance(value, str) and isinstance(other, str) else value == other)
            return equal if operator == "==" else not equal
        return value

    def conjunction():
        value = comparison()
        while peek() == "&&":
            take("&&")
            other = comparison()
            value = other if truthy(value) else value
        return value

    def disjunction():
        value = conjunction()
        while peek() == "||":
            take("||")
            other = conjunction()
            value = value if truthy(value) else other
        return value

    result = disjunction()
    assert cursor[0] == len(tokens), f"trailing expression tokens in {text!r}"
    return truthy(result)


def resolved_lane(runs_on, context):
    """The label array string a `fromJSON(<chain>)` runs-on selects, before decoding."""
    chain = re.fullmatch(r"\$\{\{ fromJSON\((.+)\) \}\}", runs_on)
    assert chain, runs_on
    # `a || b || 'literal'` selects the first non-empty variable, else the literal.
    for candidate in (context.get(name, "") for name in re.findall(r"vars\.[A-Z_]+", chain[1])):
        if candidate:
            return candidate
    literal = re.findall(r"'([^']*)'", chain[1])
    assert len(literal) == 1, runs_on
    return literal[0]


def resolve(value, runner_environment, outputs):
    """Evaluate the step-env expressions the watchdog uses; anything else is unmodelled."""
    expression = re.fullmatch(r"\$\{\{ (.+?) \}\}", str(value))
    if not expression:
        return str(value)
    if expression[1] == "runner.environment":
        return runner_environment
    output = re.fullmatch(r"steps\.([a-z-]+)\.outputs\.([a-z_]+)", expression[1])
    assert output, f"unmodelled step expression {value}"
    return outputs.get(output[1], {}).get(output[2], "")


class JobResult:
    def __init__(self, failed, stdout, stderr):
        self.returncode = 1 if failed else 0
        self.stdout = stdout
        self.stderr = stderr


def run_watchdog(snapshots, runner_environment="github-hosted", preset_outputs=None):
    """Run the job's steps in order, honoring each step's `if` the way the runner would.

    `preset_outputs` runs every step unconditionally with those step outputs, to exercise a
    step's own guard in isolation.
    """
    job_definition = watchdog_job()
    with tempfile.TemporaryDirectory() as directory:
        stub_dir = Path(directory)
        bin_dir = stub_dir / "bin"
        bin_dir.mkdir()
        for name, body in (("gh", GH_STUB), ("date", DATE_STUB), ("sleep", SLEEP_STUB)):
            (bin_dir / name).write_text(body, encoding="utf-8")
            (bin_dir / name).chmod(0o755)
        for index, listing in enumerate(snapshots):
            (stub_dir / f"snapshot-{index}.json").write_text(json.dumps(listing), encoding="utf-8")
        (stub_dir / "count").write_text(f"{len(snapshots)}\n", encoding="utf-8")
        (stub_dir / "polls").write_text("0\n", encoding="utf-8")
        (stub_dir / "clock").write_text(f"{START}\n", encoding="utf-8")
        (stub_dir / "calls").write_text("", encoding="utf-8")
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("GITHUB_", "RUNNER_"))}
        env.update({key: str(value) for key, value in job_definition["env"].items()})
        env.update({
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STUB_DIR": str(stub_dir),
            "TARGET_REPO": REPOSITORY,
            "ARM_RUN_ID": RUN_ID,
            "ARM_RUN_ATTEMPT": "1",
            "STUB_REPOSITORY": REPOSITORY,
            "STUB_RUN_ID": RUN_ID,
            "STUB_RUN_ATTEMPT": "1",
        })
        failed = False
        stdout = stderr = ""
        outputs = dict(preset_outputs or {})
        for step in job_definition["steps"]:
            condition = step.get("if")
            if preset_outputs is not None:
                pass
            elif condition is not None:
                assert condition == ESCALATION_GUARD, condition
                if outputs.get("detect", {}).get("stalled") != "true":
                    continue
            elif failed:
                continue
            output_path = stub_dir / f"output-{step.get('id', 'step')}"
            output_path.write_text("", encoding="utf-8")
            step_env = dict(env, GITHUB_OUTPUT=str(output_path))
            step_env.update({key: resolve(value, runner_environment, outputs)
                             for key, value in (step.get("env") or {}).items()})
            completed = subprocess.run(["bash", "-c", step["run"]], env=step_env, check=False,
                                       capture_output=True, text=True, timeout=120)
            stdout += completed.stdout
            stderr += completed.stderr
            failed = failed or completed.returncode != 0
            if "id" in step:
                outputs[step["id"]] = dict(line.split("=", 1) for line in
                                           output_path.read_text(encoding="utf-8").splitlines() if "=" in line)
        calls = (stub_dir / "calls").read_text(encoding="utf-8").splitlines()
        elapsed = int((stub_dir / "clock").read_text(encoding="utf-8")) - START
        violations = stub_dir / "violations"
        assert not violations.exists(), f"requests outside this run: {violations.read_text()}"
    return JobResult(failed, stdout, stderr), calls, elapsed


def posts(calls):
    """(stubbed clock, full API path) for each POST, in order."""
    return [(int(call.split()[0]), call.split()[-1]) for call in calls if " api --method POST " in call]


def cancellations(calls):
    return [path.rsplit("/", 1)[-1] for _, path in posts(calls)]


def env_seconds(name):
    return int(watchdog_job()["env"][name])


def limit_seconds():
    return int(watchdog_job()["env"]["ACQUISITION_LIMIT_SECONDS"])


def healthy_runs(prefix=""):
    n = lambda name: f"{prefix}{name}"
    return [
        snapshot(job(n("event-policy"), "completed", labels=("ubuntu-24.04",)),
                 job(n("app-key-policy / validate"), "in_progress", 5, ("ubuntu-24.04",))),
        snapshot(job(n("event-policy"), "completed", labels=("ubuntu-24.04",)),
                 job(n("app-key-policy / validate"), "completed", 5, ("ubuntu-24.04",)),
                 job(n("app-key-policy / validate-ai-review-key"), "in_progress", 20)),
        snapshot(job(n("event-policy"), "completed", labels=("ubuntu-24.04",)),
                 job(n("app-key-policy / validate"), "completed", 5, ("ubuntu-24.04",)),
                 job(n("app-key-policy / validate-ai-review-key"), "completed", 20),
                 job(n("arm"), "in_progress", 40)),
    ]


def ghost_key_validation(prefix=""):
    n = lambda name: f"{prefix}{name}"
    return [snapshot(job(n("event-policy"), "completed", labels=("ubuntu-24.04",)),
                     job(n("app-key-policy / validate"), "completed", 5, ("ubuntu-24.04",)),
                     job(n("app-key-policy / validate-ai-review-key"), "queued", 0))]


def test_admitted_repository_whose_jobs_acquire_runners_exits_without_cancelling():
    completed, calls, _ = run_watchdog(healthy_runs())
    assert completed.returncode == 0, completed.stderr
    assert cancellations(calls) == [], calls


def test_reusable_caller_prefixed_job_names_are_recognized():
    completed, calls, _ = run_watchdog(healthy_runs("rearm / "))
    assert completed.returncode == 0, completed.stderr
    assert cancellations(calls) == [], calls


def test_unreachable_lane_cancels_then_force_cancels_and_names_the_stalled_job():
    completed, calls, elapsed = run_watchdog(ghost_key_validation())
    assert completed.returncode != 0
    (cancelled_at, cancel), (forced_at, force) = posts(calls)
    assert cancel == f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/cancel", calls
    assert force == f"repos/{REPOSITORY}/actions/runs/{RUN_ID}/force-cancel", calls
    assert forced_at - cancelled_at >= env_seconds("CANCEL_GRACE_SECONDS"), calls
    assert "::error title=Runner acquisition stalled::" in completed.stdout, completed.stdout
    assert "app-key-policy / validate-ai-review-key" in completed.stdout, completed.stdout
    assert "self-hosted,general" in completed.stdout, completed.stdout
    assert limit_seconds() <= elapsed <= limit_seconds() + 300, elapsed


def test_a_cancel_that_takes_effect_is_not_escalated_over_the_failure_reporting_arm():
    limit = limit_seconds()
    polls_to_detect = limit // int(watchdog_job()["env"]["POLL_INTERVAL_SECONDS"]) + 1
    # The ordinary cancel cleared the stalled job, and the always() arm job was then queued to
    # complete the authorization check as failed. Force-cancelling it would orphan that check.
    after_cancel = snapshot(job("event-policy", "completed", labels=("ubuntu-24.04",)),
                            job("app-key-policy / validate", "completed", 5, ("ubuntu-24.04",)),
                            job("app-key-policy / validate-ai-review-key", "completed", 0),
                            job("arm", "queued", limit))
    completed, calls, _ = run_watchdog(ghost_key_validation() * polls_to_detect + [after_cancel])
    assert completed.returncode != 0
    assert cancellations(calls) == ["cancel"], calls


def test_an_unobservable_listing_after_cancel_is_not_escalated():
    polls_to_detect = limit_seconds() // int(watchdog_job()["env"]["POLL_INTERVAL_SECONDS"]) + 1
    completed, calls, _ = run_watchdog(ghost_key_validation() * polls_to_detect + ["error"])
    assert completed.returncode != 0
    assert cancellations(calls) == ["cancel"], calls


def test_force_cancel_targets_only_the_job_detected_as_stalled():
    polls_to_detect = limit_seconds() // env_seconds("POLL_INTERVAL_SECONDS") + 1
    # The detected job cleared; an unrelated job is queued at re-check time and is old enough to
    # look stalled on its own. Escalation is bound to the detected job, so it does not fire.
    other_queued = snapshot(job("event-policy", "completed", labels=("ubuntu-24.04",)),
                            job("app-key-policy / validate", "completed", 5, ("ubuntu-24.04",)),
                            job("app-key-policy / validate-ai-review-key", "completed", 0),
                            job("arm", "queued", 0))
    completed, calls, _ = run_watchdog(ghost_key_validation() * polls_to_detect + [other_queued])
    assert completed.returncode != 0
    assert cancellations(calls) == ["cancel"], calls


def test_watchdog_is_dormant_off_github_hosted_runners():
    # ADR 0147 lets the untrusted lane roll back to the persistent pool; the write-scoped token
    # must then never be used there. Both steps are checked, each in isolation.
    for runner_environment in ("self-hosted", ""):
        for preset in (None, {"detect": {"stalled": "true", "stalled_job_id": "7"}}):
            completed, calls, _ = run_watchdog(ghost_key_validation(), runner_environment, preset)
            assert completed.returncode == 0, (runner_environment, preset, completed.stderr)
            assert calls == [], (runner_environment, preset, calls)
            assert "::notice::" in completed.stdout, completed.stdout
    for step in watchdog_job()["steps"]:
        assert step["env"]["RUNNER_ENVIRONMENT"] == "${{ runner.environment }}", step["name"]
        assert step["run"].splitlines()[0].startswith(RUNNER_GUARD), step["name"]


def test_job_is_issued_a_token_only_when_its_lane_resolves_to_the_hosted_runner():
    # ADR 0147's hosted value is the only lane on which the job, and so its write-scoped
    # token, may exist; anything else, including a whitespace variant, skips it.
    watchdog = watchdog_job()
    admitted = {"needs.event-policy.outputs.run_control_plane": "true"}
    lanes = {
        "unset": ({}, True),
        "exact hosted value": ({"vars.CI_LANE_UNTRUSTED": HOSTED_LANE}, True),
        "self-hosted array": ({"vars.CI_LANE_UNTRUSTED": '["self-hosted","general"]'}, False),
        "whitespace variant": ({"vars.CI_LANE_UNTRUSTED": '[ "ubuntu-24.04" ]'}, False),
        "hosted label beside a self-hosted one": ({"vars.CI_LANE_UNTRUSTED": '["self-hosted","ubuntu-24.04"]'}, False),
        "fallback variable set": ({"vars.CI_LANE_FALLBACK": '["self-hosted","general"]'}, True),
    }
    for name, (variables, expected) in lanes.items():
        context = dict(admitted, **variables)
        runs = evaluate(watchdog["if"], context)
        assert runs is expected, (name, watchdog["if"])
        # The admission and the placement read the same chain: the job runs only where the
        # lane it would be placed on is exactly the hosted value.
        assert runs is (resolved_lane(watchdog["runs-on"], context) == HOSTED_LANE), name
        held = dict(context, **{"needs.event-policy.outputs.run_control_plane": "false"})
        assert evaluate(watchdog["if"], held) is False, name


def test_job_without_created_at_is_never_aged_into_a_stall():
    undated = snapshot(job("event-policy", "completed", labels=("ubuntu-24.04",)),
                       job("app-key-policy / validate", "completed", 5, ("ubuntu-24.04",)),
                       job("app-key-policy / validate-ai-review-key", "queued", MISSING))
    polls = (limit_seconds() + 120) // env_seconds("POLL_INTERVAL_SECONDS")
    completed, calls, _ = run_watchdog([undated] * polls + [healthy_runs()[-1]])
    assert completed.returncode == 0, completed.stderr
    assert cancellations(calls) == [], calls


def test_null_labels_do_not_disturb_a_healthy_run():
    unlabelled = healthy_runs()[-1]
    unlabelled["jobs"][0]["labels"] = None
    completed, calls, _ = run_watchdog(healthy_runs()[:-1] + [unlabelled])
    assert completed.returncode == 0, completed.stderr
    assert cancellations(calls) == [], calls


def test_a_stalled_job_with_null_labels_is_still_cancelled():
    stalled = ghost_key_validation()[0]
    stalled["jobs"][2]["labels"] = None
    completed, calls, _ = run_watchdog([stalled])
    assert completed.returncode != 0
    assert cancellations(calls) == ["cancel", "force-cancel"], calls
    assert "app-key-policy / validate-ai-review-key" in completed.stdout, completed.stdout


def test_stalled_job_in_a_reusable_caller_run_is_cancelled():
    completed, calls, _ = run_watchdog(ghost_key_validation("rearm / "))
    assert completed.returncode != 0
    assert cancellations(calls) == ["cancel", "force-cancel"], calls


def test_stalled_arm_job_after_key_policy_is_cancelled():
    stuck_arm = snapshot(job("event-policy", "completed", labels=("ubuntu-24.04",)),
                         job("app-key-policy / validate", "completed", 5, ("ubuntu-24.04",)),
                         job("app-key-policy / validate-ai-review-key", "completed", 20),
                         job("arm", "queued", 40))
    completed, calls, elapsed = run_watchdog([stuck_arm])
    assert completed.returncode != 0
    assert cancellations(calls) == ["cancel", "force-cancel"], calls
    assert "::error title=Runner acquisition stalled::" in completed.stdout
    assert elapsed >= limit_seconds() + 40, elapsed


def test_a_queue_shorter_than_the_limit_is_tolerated():
    limit = limit_seconds()
    still_queued = ghost_key_validation()[0]
    acquired = healthy_runs()[-1]
    # One poll before the limit the job is still queued; the next poll observes acquisition.
    polls_before_limit = (limit - 1) // int(watchdog_job()["env"]["POLL_INTERVAL_SECONDS"])
    completed, calls, elapsed = run_watchdog([still_queued] * polls_before_limit + [acquired])
    assert completed.returncode == 0, completed.stderr
    assert cancellations(calls) == [], calls
    assert elapsed < limit, elapsed


def test_environment_approval_wait_is_not_a_runner_stall():
    waiting = snapshot(job("event-policy", "completed", labels=("ubuntu-24.04",)),
                       job("app-key-policy / validate", "completed", 5, ("ubuntu-24.04",)),
                       job("app-key-policy / validate-ai-review-key", "waiting", 0))
    polls = (limit_seconds() + 120) // int(watchdog_job()["env"]["POLL_INTERVAL_SECONDS"])
    completed, calls, _ = run_watchdog([waiting] * polls + [healthy_runs()[-1]])
    assert completed.returncode == 0, completed.stderr
    assert cancellations(calls) == [], calls


def test_unreadable_jobs_api_never_cancels_a_healthy_run():
    polls = (limit_seconds() + 120) // int(watchdog_job()["env"]["POLL_INTERVAL_SECONDS"])
    completed, calls, _ = run_watchdog(["error", "malformed"] * polls + [healthy_runs()[-1]])
    assert completed.returncode == 0, completed.stderr
    assert cancellations(calls) == [], calls


def test_watchdog_holds_no_secret_and_cannot_veto_a_merge():
    arm = load(ARM)
    watchdog = arm["jobs"][WATCHDOG]
    rendered = yaml.safe_dump(watchdog)
    assert watchdog["needs"] == "event-policy"
    # Admitted on exactly the deliveries that admit the key policy, and never more.
    for run_control_plane in ("true", "false"):
        context = {"needs.event-policy.outputs.run_control_plane": run_control_plane}
        assert evaluate(watchdog["if"], context) is evaluate(arm["jobs"]["app-key-policy"]["if"], context)
    # Same lane as event-policy (no environment or repository secret), never the trusted lane it watches.
    assert watchdog["runs-on"] == arm["jobs"]["event-policy"]["runs-on"]
    assert watchdog["runs-on"] != arm["jobs"]["arm"]["runs-on"]
    assert watchdog["permissions"] == {"actions": "write"}
    assert "environment" not in watchdog
    assert "secrets." not in rendered
    assert "actions/checkout" not in rendered
    assert watchdog["continue-on-error"] is True
    for name in ("arm", "app-key-policy"):
        needs = arm["jobs"][name]["needs"]
        assert WATCHDOG not in ([needs] if isinstance(needs, str) else needs), name


def test_watchdog_outlives_two_sequential_acquisitions_and_the_jobs_it_waits_behind():
    key_policy = load(KEY_POLICY)
    env = watchdog_job()["env"]
    upstream_minutes = sum(key_policy["jobs"][name]["timeout-minutes"] for name in ("validate", "validate-ai-review-key"))
    worst_case = (2 * int(env["ACQUISITION_LIMIT_SECONDS"]) + int(env["CANCEL_GRACE_SECONDS"])
                  + 60 * upstream_minutes + 2 * int(env["POLL_INTERVAL_SECONDS"]))
    assert int(env["ACQUISITION_LIMIT_SECONDS"]) >= 600
    assert watchdog_job()["timeout-minutes"] * 60 >= worst_case, worst_case


def run_test(named_test):
    name, test = named_test
    try:
        test()
    except Exception as error:  # noqa: BLE001 - report every failing behavior, not only the first
        return f"FAIL {name}: {type(error).__name__}: {error}"
    return f"ok   {name}"


def main():
    tests = [(name, value) for name, value in globals().items() if name.startswith("test_") and callable(value)]
    # Each scenario polls a stubbed clock through the real limit; they share nothing, so run them
    # concurrently to stay well inside the actions-ci command budget.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tests)) as pool:
        results = list(pool.map(run_test, tests))
    print("\n".join(results))
    failures = sum(result.startswith("FAIL") for result in results)
    if failures:
        raise SystemExit(f"{failures} of {len(tests)} watchdog behaviors failed")
    print(f"all {len(tests)} watchdog behaviors passed")


if __name__ == "__main__":
    main()
