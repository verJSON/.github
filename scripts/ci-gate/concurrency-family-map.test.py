#!/usr/bin/env python3
"""Derive the merge gate's concurrency families from the workflows themselves.

`scripts/assert-mergeable-head.sh` sets aside a cancelled run displaced from a
declared concurrency group by a later successful run of ANOTHER workflow in that
group (ADR 0226, amendment 2026-10-15). Which workflows share a group is never
inferred from names at runtime: it is the checked-in `CONCURRENCY_FAMILIES` map,
and this test rebuilds that map from the workflow and generator sources and fails
when the two disagree in either direction.

A family is a REUSABLE workflow whose workflow-level concurrency group is keyed
only on the pull request number, so every caller resolves it to the same group;
`cancel-in-progress: false` makes GitHub replace a pending run rather than cancel a
running one. Its members are the declaring workflow and every generated caller
that does nothing but call it.
"""
from pathlib import Path
import json
import re
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPT = ROOT / "scripts" / "assert-mergeable-head.sh"
DECLARING_REPOSITORY = "verjson/.github"
GROUP_KEY = "github.event.pull_request.number"
PR_KEYED_GROUP = re.compile(r"^([A-Za-z0-9_.-]+)\$\{\{ " + re.escape(GROUP_KEY) + r" \}\}$")
DESTINATION = re.compile(r">\s*(\.github/workflows/[^\s]+\.ya?ml)\s*$", re.MULTILINE)
CONTRACT_SHA = "0123456789abcdef0123456789abcdef01234567"


def load(text):
    return yaml.load(text, Loader=yaml.BaseLoader)


def triggers(doc):
    on = doc.get("on", {})
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    return set(on)


def concurrency_group(doc):
    concurrency = doc.get("concurrency")
    if concurrency is None:
        return None, None
    if isinstance(concurrency, str):
        return concurrency, None
    return concurrency.get("group"), concurrency.get("cancel-in-progress")


def checked_in_map():
    lines = [
        line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.startswith("readonly CONCURRENCY_FAMILIES=")
    ]
    assert len(lines) == 1, "exactly one CONCURRENCY_FAMILIES definition"
    match = re.fullmatch(r"readonly CONCURRENCY_FAMILIES='(.*)'", lines[0])
    assert match, "CONCURRENCY_FAMILIES is one single-quoted JSON literal"
    return json.loads(match.group(1))


def declaring_workflows():
    """Every reusable workflow declaring a caller-independent, PR-keyed group."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        doc = load(path.read_text(encoding="utf-8"))
        group, cancel = concurrency_group(doc)
        if group is None or "workflow_call" not in triggers(doc):
            continue
        match = PR_KEYED_GROUP.match(group)
        if match is None or cancel != "false":
            continue
        found.append((path, doc, match.group(1)))
    return found


def generator_outputs(declaring_path):
    """Render every caller generator that references the declaring workflow."""
    outputs = []
    for generator in sorted((ROOT / "scripts").glob("gen-*.sh")):
        if declaring_path not in generator.read_text(encoding="utf-8"):
            continue
        for args in ([CONTRACT_SHA], ["--local"]):
            result = subprocess.run(
                ["bash", str(generator), *args], capture_output=True, text=True, cwd=ROOT,
            )
            if result.returncode == 0:
                outputs.append((generator.name, result.stdout))
                break
        else:
            raise AssertionError(f"{generator.name} renders with neither a contract SHA nor --local")
    return outputs


def calls_declarer(uses, declaring_path):
    return uses == f"./{declaring_path}" or (
        uses.lower().startswith(f"{DECLARING_REPOSITORY}/{declaring_path}@".lower())
        and uses.split("@", 1)[0].split("/", 2)[2] == declaring_path
    )


class ConcurrencyFamilyMapTests(unittest.TestCase):
    def derived_families(self):
        families = []
        for path, doc, prefix in declaring_workflows():
            declaring_path = str(path.relative_to(ROOT))
            events = triggers(doc) - {"workflow_call"}
            self.assertEqual(len(events), 1, f"{declaring_path} must declare exactly one event besides workflow_call")
            event = next(iter(events))
            callers = {}
            sources = generator_outputs(declaring_path)
            sources += [
                (candidate.name, candidate.read_text(encoding="utf-8"))
                for candidate in sorted(WORKFLOWS.glob("*.y*ml"))
                if candidate != path and any(
                    calls_declarer(job.get("uses", ""), declaring_path)
                    for job in load(candidate.read_text(encoding="utf-8")).get("jobs", {}).values()
                    if isinstance(job, dict)
                )
            ]
            for source, text in sources:
                caller = load(text)
                with self.subTest(caller=source):
                    jobs = caller.get("jobs", {})
                    # Exactly one job joins the family by calling the declaring workflow. The
                    # generated gate re-arm caller adds a guard job ahead of it (ADR 0255,
                    # amendment 2026-11-06); any such other job calls no workflow and holds no
                    # group, so it can neither join this family nor displace a member of it.
                    calling = [(job_id, job) for job_id, job in jobs.items()
                               if isinstance(job, dict) and calls_declarer(job.get("uses", ""), declaring_path)]
                    self.assertEqual(len(calling), 1,
                                     f"{source} must call {declaring_path} from exactly one job")
                    ((arm_id, job),) = calling
                    others = {job_id: other for job_id, other in jobs.items() if other is not job}
                    for other in others.values():
                        self.assertNotIn("uses", other, f"{source} must call no other workflow")
                        self.assertNotIn("concurrency", other, f"{source} must hold no group outside the family")
                    self.assertNotIn("steps", job, f"{source} must only call the declaring workflow")
                    self.assertTrue(calls_declarer(job.get("uses", ""), declaring_path),
                                    f"{source} must call {declaring_path}")
                    own_group, _ = concurrency_group(caller)
                    self.assertIn(own_group, (None, group_of(doc)),
                                  f"{source} must not declare a different workflow-level group")
                    self.assertEqual(triggers(caller) - {"workflow_call"}, {event},
                                     f"{source} must trigger only on the family event")
                    destinations = DESTINATION.findall(text)
                    self.assertEqual(len(set(destinations)), 1, f"{source} must name one destination path")
                    entry = {"name": caller["name"], "path": destinations[0]}
                    if others:
                        # A guard job gating the calling job declares the caller's yield
                        # shape (ADR 0226 amendment 2026-11-12): the merge gate leaves a run
                        # whose suite is exactly the guard's SUCCESS and the calling job's
                        # SKIPPED out of the newest-family-run selection, matching both
                        # checks by name, so the map must carry the exact job ids.
                        self.assertEqual(len(others), 1, f"{source} may gate its call by one guard job only")
                        ((guard_id, guard),) = others.items()
                        self.assertIn(job.get("needs"), (guard_id, [guard_id]),
                                      f"{source} must make its calling job need the guard")
                        self.assertIn(f"needs.{guard_id}.outputs.", job.get("if", ""),
                                      f"{source} must gate its calling job on the guard's output")
                        for named in (guard, job):
                            self.assertNotIn("name", named,
                                             f"{source} must leave its yield jobs named by their ids, as their checks are")
                        entry["yield"] = {"guard": guard_id, "arm": arm_id}
                    key = (entry["name"], entry["path"])
                    self.assertEqual(callers.get(key, entry), entry, f"{source} disagrees with another source on {key}")
                    callers[key] = entry
            families.append({
                "group_prefix": prefix,
                "group_key": GROUP_KEY,
                "event": event,
                "declared_by": {
                    "repository": DECLARING_REPOSITORY,
                    "path": declaring_path,
                    "name": doc["name"],
                },
                "callers": [callers[key] for key in sorted(callers)],
            })
        return families

    def test_checked_in_map_equals_the_families_the_workflows_declare(self):
        checked_in = [
            {**family, "callers": sorted(family["callers"], key=lambda c: (c["name"], c["path"]))}
            for family in checked_in_map()
        ]
        self.assertEqual(checked_in, self.derived_families())

    def test_the_ai_review_arm_family_is_derived(self):
        # Pins the derivation itself, so a regression that derives nothing cannot
        # agree with an emptied map.
        (family,) = self.derived_families()
        self.assertEqual(family["group_prefix"], "ai-review-arm-")
        self.assertEqual(family["declared_by"]["path"], ".github/workflows/gate-rearm.yml")
        self.assertEqual(family["declared_by"]["name"], "AI review authorization arm")
        self.assertEqual(family["event"], "pull_request_target")
        self.assertEqual(family["callers"], [
            {"name": "AI review label re-arm", "path": ".github/workflows/ai-review-label-rearm.yml"},
            {"name": "AI review lifecycle re-arm", "path": ".github/workflows/ai-review-lifecycle-rearm.yml"},
            {"name": "gate re-arm", "path": ".github/workflows/gate-rearm.yml",
             "yield": {"guard": "guard", "arm": "rearm"}},
        ])


def group_of(doc):
    return concurrency_group(doc)[0]


if __name__ == "__main__":
    unittest.main()
