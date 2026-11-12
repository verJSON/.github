#!/usr/bin/env python3
"""Behavioral tests for scripts/capability-floor-audit.py.

Runs the script as a subprocess (matching this repo's audit-script test
style) against real commits already reachable in this checkout -- the same
toquorum stale/current gate-rearm.yml pins reported live in issue #933 -- so
the test doubles as a regression check that the shipped
config/capability-floors.json actually classifies that real incident
correctly, not just synthetic fixtures.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/capability-floor-audit.py"
DEFAULT_CAPABILITIES = ROOT / "config/capability-floors.json"

# Real Verjson/.github commits (see issue #933's comment thread and
# config/capability-floors.json):
#   INTRODUCED  - 23f6418... "feat: make AI review advisory and opt-in
#                  (#732) (#734)" (2026-08-10) - first commit accepting
#                  "deepseek" as a review provider.
#   STALE_PIN   - a6b3ccc... (2026-08-10, #722) - toquorum's original
#                  gate-rearm.yml pin; same day as INTRODUCED but an earlier,
#                  sibling commit, so NOT its ancestor. This is the exact pin
#                  that failed closed with "unsupported review provider".
#   CURRENT_PIN - c4127043... (2026-08-18, #702/#934) - toquorum's pin after
#                  the fix landed in toquorum#636; a descendant of INTRODUCED.
INTRODUCED = "23f641822d1fdf4787a46f0b55f24a755b8a73ae"
STALE_PIN = "a6b3ccc0590f4fcfdacd7818279ab3eea6b30155"
CURRENT_PIN = "c4127043f16fbcfd64f701797ccf0f11c9077317"
UNKNOWN_SHA = "0" * 40
# Lifecycle re-arm receipts (#2000):
#   LIFECYCLE_FLOOR     - 010fa39... "verify lifecycle re-arm receipts"
#                          (2026-09-30, first released in v4.0.0) - the first
#                          arm that source-binds a lifecycle caller's delivery.
#   LIFECYCLE_STALE_PIN - 0f8aead... its parent. A lifecycle caller the current
#                          generator pins here passes the authorization audit's
#                          completeness check but reaches an arm that binds only
#                          the label caller's WORKFLOW_REF.
LIFECYCLE_GENERATOR = "scripts/gen-ai-review-lifecycle-rearm-caller.sh"
LIFECYCLE_FLOOR = "010fa39da9e718e59a53344e2a7d5575a8319a64"
LIFECYCLE_STALE_PIN = "0f8aead1065df13ef8cf232f7f2527c7d35da8a1"

failures = []


def check(name: str, condition: bool) -> None:
    if not condition:
        failures.append(name)


def write_json(directory: Path, name: str, payload) -> Path:
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)

    # --- Real-incident regression: shipped config classifies the actual
    # toquorum pins from issue #933 correctly. ---
    real_pins = write_json(
        tmp_path,
        "real-pins.json",
        [
            {"repo": "Verjson/toquorum", "generator": "scripts/gen-gate-rearm-caller.sh", "pinned_sha": STALE_PIN},
            {"repo": "Verjson/toquorum", "generator": "scripts/gen-gate-rearm-caller.sh", "pinned_sha": CURRENT_PIN},
        ],
    )
    result = run(["--pins", str(real_pins)])
    check("shipped config run exits 0 without --fail-on-stale", result.returncode == 0)
    report = json.loads(result.stdout) if result.returncode == 0 else {}
    check("shipped config flags any_stale for the pre-fix pin", report.get("any_stale") is True)
    stale_shas = {row["pinned_sha"] for row in report.get("stale", [])}
    current_shas = {row["pinned_sha"] for row in report.get("current", [])}
    check("toquorum's original pin is reported stale", STALE_PIN in stale_shas)
    check("toquorum's post-fix pin is reported current", CURRENT_PIN in current_shas)
    check("post-fix pin is not also reported stale", CURRENT_PIN not in stale_shas)
    check("pre-fix pin is not also reported current", STALE_PIN not in current_shas)
    stale_row = next((row for row in report.get("stale", []) if row["pinned_sha"] == STALE_PIN), None)
    check("stale row is present for the pre-fix pin", stale_row is not None)
    if stale_row is not None:
        check("stale row names the capability", stale_row["capability_id"] == "ai-review-deepseek-provider")
        check(
            "stale row surfaces the org variable this capability assumes",
            stale_row["assumed_by_org_variable"] == {"name": "AI_REVIEW_PRIMARY_PROVIDER", "value": "deepseek"},
        )

    # --- The shipped config floors the lifecycle re-arm caller at the arm
    # that first verifies its receipts (#2000): a pin before it is stale. ---
    lifecycle_pins = write_json(
        tmp_path,
        "lifecycle-pins.json",
        [
            {"repo": "Verjson/verjson-authn", "generator": LIFECYCLE_GENERATOR, "pinned_sha": LIFECYCLE_STALE_PIN},
            {"repo": "Verjson/verjson-authn", "generator": LIFECYCLE_GENERATOR, "pinned_sha": LIFECYCLE_FLOOR},
        ],
    )
    lifecycle_result = run(["--pins", str(lifecycle_pins)])
    lifecycle_report = json.loads(lifecycle_result.stdout) if lifecycle_result.returncode == 0 else {}
    lifecycle_rows = {
        (row["pinned_sha"], row["capability_id"], status)
        for status in ("stale", "current")
        for row in lifecycle_report.get(status, [])
    }
    check(
        "a lifecycle pin before the receipt-verifying arm is stale and one at it is current",
        lifecycle_rows == {
            (LIFECYCLE_STALE_PIN, "ai-review-lifecycle-receipt-binding", "stale"),
            (LIFECYCLE_FLOOR, "ai-review-lifecycle-receipt-binding", "current"),
        },
    )

    # --- --fail-on-stale flips the exit code without changing the report. ---
    fail_result = run(["--pins", str(real_pins), "--fail-on-stale"])
    check("--fail-on-stale exits 1 when stale pins are present", fail_result.returncode == 1)
    if fail_result.returncode == 1:
        check("--fail-on-stale still emits the report on stdout", json.loads(fail_result.stdout)["any_stale"] is True)

    # --- A pin naming an untracked generator is skipped, not an error. ---
    untracked_pins = write_json(
        tmp_path,
        "untracked-pins.json",
        [{"repo": "Verjson/some-repo", "generator": "scripts/gen-nothing-caller.sh", "pinned_sha": CURRENT_PIN}],
    )
    untracked_result = run(["--pins", str(untracked_pins)])
    check("untracked-generator run exits 0", untracked_result.returncode == 0)
    untracked_report = json.loads(untracked_result.stdout) if untracked_result.returncode == 0 else {}
    check("untracked generator produces no stale/current rows", not untracked_report.get("stale") and not untracked_report.get("current"))
    check("untracked generator is not treated as unresolvable", not untracked_report.get("unresolvable"))

    # --- An unresolvable pinned_sha is reported, not crashed on. ---
    unresolvable_pins = write_json(
        tmp_path,
        "unresolvable-pins.json",
        [{"repo": "Verjson/ghost", "generator": "scripts/gen-gate-rearm-caller.sh", "pinned_sha": UNKNOWN_SHA}],
    )
    unresolvable_result = run(["--pins", str(unresolvable_pins)])
    check("unresolvable-sha run exits 0 without --fail-on-stale", unresolvable_result.returncode == 0)
    unresolvable_report = json.loads(unresolvable_result.stdout) if unresolvable_result.returncode == 0 else {}
    check("unresolvable sha is reported", unresolvable_report.get("unresolvable") and unresolvable_report["unresolvable"][0]["pinned_sha"] == UNKNOWN_SHA)
    check("unresolvable sha sets any_stale", unresolvable_report.get("any_stale") is True)
    unresolvable_fail = run(["--pins", str(unresolvable_pins), "--fail-on-stale"])
    check("--fail-on-stale exits 1 on an unresolvable pin too", unresolvable_fail.returncode == 1)

    # --- Malformed capabilities config is rejected with a diagnostic, not a
    # traceback, and never silently reports "no findings". ---
    bad_capabilities = write_json(
        tmp_path,
        "bad-capabilities.json",
        {"schema_version": 1, "capabilities": [{"id": "x", "description": "y", "introduced_at": "not-a-sha", "generators": ["scripts/gen-gate-rearm-caller.sh"]}]},
    )
    bad_result = run(["--pins", str(real_pins), "--capabilities", str(bad_capabilities)])
    check("invalid introduced_at sha fails with exit 2", bad_result.returncode == 2)
    check("invalid introduced_at sha reports a diagnostic", "introduced_at" in bad_result.stderr)

    # --- A capability whose introduced_at commit doesn't exist is rejected. ---
    unknown_commit_capabilities = write_json(
        tmp_path,
        "unknown-commit-capabilities.json",
        {
            "schema_version": 1,
            "capabilities": [
                {
                    "id": "x",
                    "description": "y",
                    "introduced_at": UNKNOWN_SHA,
                    "generators": ["scripts/gen-gate-rearm-caller.sh"],
                }
            ],
        },
    )
    unknown_commit_result = run(["--pins", str(real_pins), "--capabilities", str(unknown_commit_capabilities)])
    check("capability with an unreachable introduced_at commit fails", unknown_commit_result.returncode == 2)
    check("unreachable introduced_at diagnostic names the commit", UNKNOWN_SHA in unknown_commit_result.stderr)

    # --- A pin whose generator does not exist as a file is fine (pins are not
    # required to name a generator that exists locally); but a capability
    # naming a nonexistent generator script is rejected. ---
    ghost_generator_capabilities = write_json(
        tmp_path,
        "ghost-generator-capabilities.json",
        {
            "schema_version": 1,
            "capabilities": [
                {
                    "id": "x",
                    "description": "y",
                    "introduced_at": INTRODUCED,
                    "generators": ["scripts/gen-does-not-exist-caller.sh"],
                }
            ],
        },
    )
    ghost_generator_result = run(["--pins", str(real_pins), "--capabilities", str(ghost_generator_capabilities)])
    check("capability naming a nonexistent generator file fails", ghost_generator_result.returncode == 2)

    # --- Malformed pins file is rejected. ---
    bad_pins = write_json(tmp_path, "bad-pins.json", [{"repo": "not a valid repo name", "generator": "x", "pinned_sha": CURRENT_PIN}])
    bad_pins_result = run(["--pins", str(bad_pins)])
    check("invalid repo shape in pins file fails with exit 2", bad_pins_result.returncode == 2)

    # --- The shipped config file itself must load cleanly (schema + every
    # introduced_at commit + every generator file all resolve in this
    # checkout). ---
    empty_pins = write_json(tmp_path, "empty-pins.json", [])
    shipped_result = run(["--pins", str(empty_pins), "--capabilities", str(DEFAULT_CAPABILITIES)])
    check("shipped config/capability-floors.json loads with zero pins", shipped_result.returncode == 0)
    shipped_capabilities = json.loads(DEFAULT_CAPABILITIES.read_text(encoding="utf-8"))["capabilities"]
    check(
        "shipped config reports every registered capability",
        shipped_result.returncode == 0 and
        json.loads(shipped_result.stdout)["capabilities_checked"] == len(shipped_capabilities),
    )
    check(
        "shipped config registers the generated changelog PR-gate floor",
        any(item["id"] == "changelog-generated-pr-gate" for item in shipped_capabilities),
    )

if failures:
    print(f"FAILED ({len(failures)}):")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)

print(f"OK: capability-floor-audit.test.py ({SCRIPT})")
