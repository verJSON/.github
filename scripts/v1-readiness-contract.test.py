#!/usr/bin/env python3
"""Guard the evidence-bearing invariants in the organization v1 readiness contract."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/v1-readiness/README.md"
SECTION_RE = re.compile(
    r"^## (?P<number>\d+)\.[^\n]*\n(?P<body>.*?)(?=^## \d+\.|\Z)",
    re.MULTILINE | re.DOTALL,
)

REQUIREMENTS: dict[int, tuple[tuple[str, str], ...]] = {
    4: (
        (
            "vacuous case is explicit",
            r"no relevant sibling dependency or range in the candidate migration.*passes vacuously",
        ),
        ("vacuous case forbids an artificial leg", r"do not add an artificial exact-version leg"),
        (
            "every relevant range requires a compatibility leg",
            r"declares any relevant sibling dependency or peer range in that migration.*at least one compatibility leg must exercise it",
        ),
        ("zero applicable legs fail", r"zero legs is fail, not vacuous"),
    ),
    5: (
        ("all generated artifacts share one SHA", r"all .*generated.*at one immutable contract sha"),
        ("the floor comes from the capability registry", r"config/capability-floors\.json"),
        ("the chosen SHA descends each floor", r"each introduced_at commit is an ancestor"),
        ("newer capability-bearing descendants pass", r"newer immutable descendant.*is conformant"),
        ("the emitted context is required", r"active ruleset that actually applies.*requires that literal context"),
        ("presence alone is not enforcement", r"green or merely present check is not enforcement"),
    ),
    6: (
        ("grace-period fragments are inspected directly", r"inspect the files directly during the grace period"),
        ("published packages require a snapshot", r"package with any published version.*has at least one immutable"),
        ("missing snapshots fail", r"published versions with no snapshot are fail"),
        ("the v1 fragment is major", r"v1\.0\.0.*explicitly declares impact: major"),
        ("the wrong requested version is rejected", r"wrong requested version is rejected"),
        ("exactly v1 is accepted", r"exactly v1\.0\.0 is accepted"),
    ),
    7: (
        ("npm starts from an explicit dispatch", r"npm publication path starts only from workflow_dispatch.*version resolved from the selected fragments"),
        ("merge and branch pushes cannot publish npm", r"no merge, branch-push, or other push event can publish"),
        ("a disjoint non-npm tag lane is allowed", r"independent non-npm language lane does not fail this item solely.*tag-push event"),
        ("the non-npm namespace states a version", r"explicit, disjoint namespace that states the exact version"),
        ("the non-npm lane cannot publish npm", r"must be unable to publish the @verjson/\* npm package"),
        ("the non-npm lane has its own guard", r"own rehearsed guard that rejects"),
    ),
    8: (
        ("published deprecations are in scope", r"known deprecation, end-of-support, or maintenance-only marker.*published dependency"),
        ("support markers are addressed or justified", r"either addressed before the cut or carries an explicit, evidence-backed.*justification"),
        ("a green vulnerability audit is insufficient", r"green npm audit.*does not satisfy this check"),
    ),
}


def normalize(text: str) -> str:
    return " ".join(text.replace("`", "").replace("**", "").split()).lower()


def sections(text: str) -> dict[int, str]:
    return {
        int(match.group("number")): normalize(match.group("body"))
        for match in SECTION_RE.finditer(text)
    }


def contract_problems(text: str) -> list[str]:
    parsed = sections(text)
    problems: list[str] = []
    for number, requirements in REQUIREMENTS.items():
        body = parsed.get(number)
        if body is None:
            problems.append(f"item {number} is missing")
            continue
        for label, pattern in requirements:
            if re.search(pattern, body) is None:
                problems.append(f"item {number}: {label}")
    return problems


class V1ReadinessContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = CONTRACT.read_text(encoding="utf-8")

    def test_contract_encodes_feedback_invariants(self) -> None:
        self.assertEqual([], contract_problems(self.text))

    def test_ambiguous_or_fail_open_mutations_are_rejected(self) -> None:
        mutations = {
            "artificial compatibility leg": self.text.replace(
                "passes vacuously: record that reason and do not add an artificial exact-version\n"
                "      leg.",
                "requires an artificial exact-version compatibility leg.",
                1,
            ),
            "relevant range with zero legs": self.text.replace(
                "If a package declares any relevant sibling dependency or peer range in that migration, at least\n"
                "      one compatibility leg must exercise it. This remains true whether the range already\n"
                "      admits `1.0.0` or still FAILs item 3; zero legs is FAIL, not vacuous.",
                "Only a range already widened under item 3 needs a compatibility leg.",
                1,
            ),
            "magic changelog pin": self.text.replace(
                "any newer immutable descendant that carries every applicable\n"
                "      capability is conformant.",
                "only one literal SHA is conformant.",
                1,
            ),
            "present but unenforced check": self.text.replace(
                "A green or merely present check is not\n"
                "      enforcement; record the live ruleset evidence.",
                "A present check is sufficient evidence.",
                1,
            ),
            "missing snapshot passes": self.text.replace(
                "Published versions with no snapshot are **FAIL**;",
                "Published versions with no snapshot may pass;",
                1,
            ),
            "wrong v1 version accepted": self.text.replace(
                "a wrong requested version is rejected",
                "a wrong requested version is accepted",
                1,
            ),
            "branch push publishes npm": self.text.replace(
                "No merge, branch-push, or other `push` event can publish",
                "A merge or branch-push event can publish",
                1,
            ),
            "non-npm exception removed": self.text.replace(
                "An independent non-npm language lane does not fail this item solely because it",
                "Every independent non-npm language lane fails this item when it",
                1,
            ),
            "deprecated dependency hidden by audit": self.text.replace(
                "A green `npm audit`\n      or empty Dependabot list does not satisfy this check.",
                "A green `npm audit` is sufficient.",
                1,
            ),
        }

        for label, mutant in mutations.items():
            with self.subTest(label=label):
                self.assertNotEqual(self.text, mutant, "mutation fixture did not change the contract")
                self.assertTrue(contract_problems(mutant), "fail-open mutation survived")


if __name__ == "__main__":
    unittest.main()
