#!/usr/bin/env python3
"""Generated callers move by regeneration; their action pins move in the generators.

Renovate edited action pins inside adopters' generated `release.yml`
(Verjson/verjson-agents#419, #420, #421), which breaks the adopter's
byte-faithfulness test and recurs after every repin (#1812). The cure has two
halves that must stay complete as the generators grow:

* the generators' own pins are Renovate-managed here, through a regex custom
  manager in `renovate.json`, grouped with this repository's workflow digests;
* adopters fence the generated caller paths through the organization preset,
  which extends `renovate/generated-callers.json` from this repository.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
RENOVATE = ROOT / "renovate.json"
CHANGELOG_GENERATOR = ROOT / "scripts/gen-changelog-caller.sh"
PIN_DECLARATION = re.compile(
    r"^release_[a-z_]+='(?P<uses>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?)"
    r"@(?P<sha>[0-9a-f]{40}) # (?P<tag>v[0-9][0-9A-Za-z.+-]*)'$",
    re.MULTILINE,
)
THIRD_PARTY_PIN = re.compile(
    r"(?P<owner>[A-Za-z0-9_.-]+)/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?@[0-9a-f]{40}\b"
)
# Pins Renovate cannot read, each with the reason it is not moved here.
UNMANAGED_GENERATOR_PINS = {
    (
        "scripts/gen-node-required-workflow.py",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    ): (
        "The CLI Projects admission caller is accepted byte-exactly (ADR 0212), and its "
        "checkout pins carry no version comment, so the github-actions manager skips "
        "them as unversioned in every repository that holds the output. Giving them a "
        "comment changes the admitted bytes fleet-wide and is follow-up work."
    ),
}


def renovate_pattern(pattern: str) -> re.Pattern[str]:
    """Translate Renovate's RE2 named groups to Python's spelling."""
    return re.compile(pattern.replace("(?<", "(?P<"))


def manager_file_matches(manager: dict, relative_path: str) -> bool:
    for pattern in manager.get("managerFilePatterns", []):
        if not (pattern.startswith("/") and pattern.endswith("/")):
            raise AssertionError(f"managerFilePatterns entry {pattern!r} must be a regex")
        if re.search(pattern[1:-1].replace("\\/", "/"), relative_path):
            return True
    return False


def generator_pin_managers(config: dict) -> list[dict]:
    return [
        manager
        for manager in config.get("customManagers", [])
        if manager.get("customType") == "regex"
        and manager_file_matches(manager, "scripts/gen-changelog-caller.sh")
    ]


class GeneratorPinManager(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(RENOVATE.read_text(encoding="utf-8"))
        self.generator = CHANGELOG_GENERATOR.read_text(encoding="utf-8")

    def test_one_custom_manager_reads_every_generator_pin_declaration(self) -> None:
        managers = generator_pin_managers(self.config)
        self.assertEqual(len(managers), 1, "one regex manager must own the generator pins")
        manager = managers[0]
        self.assertEqual(manager.get("datasourceTemplate"), "github-tags")
        # The github-actions manager's own versioning, so a `# v7` floating tag
        # keeps its precision and only the digest moves, exactly as it does in
        # this repository's workflows.
        self.assertEqual(manager.get("versioningTemplate"), "github-actions")

        declared = [
            (match["uses"], match["sha"], match["tag"])
            for match in PIN_DECLARATION.finditer(self.generator)
        ]
        self.assertGreaterEqual(len(declared), 6, "the release pin declarations moved")
        extracted = []
        for pattern in manager.get("matchStrings", []):
            for match in renovate_pattern(pattern).finditer(self.generator):
                extracted.append((match["depName"], match["currentDigest"], match["currentValue"]))
        expected = [
            (uses if uses.count("/") == 1 else "/".join(uses.split("/")[:2]), sha, tag)
            for uses, sha, tag in declared
        ]
        self.assertEqual(sorted(extracted), sorted(expected))

    def test_no_generator_carries_a_pin_the_manager_cannot_move(self) -> None:
        # A second spelling of a declared pin is the copy Renovate leaves behind:
        # its PR would then fail this repository's own contract test, and the
        # generator would stop moving at all.
        manager = generator_pin_managers(self.config)[0]
        patterns = [renovate_pattern(pattern) for pattern in manager["matchStrings"]]
        unmanaged = []
        for generator in sorted((ROOT / "scripts").glob("gen-*")):
            relative = generator.relative_to(ROOT).as_posix()
            if not manager_file_matches(manager, relative):
                continue
            text = generator.read_text(encoding="utf-8")
            spans = [
                match.span()
                for pattern in patterns
                for match in pattern.finditer(text)
            ]
            for match in THIRD_PARTY_PIN.finditer(text):
                if match["owner"].casefold() == "verjson":
                    continue
                if any(start <= match.start() and match.end() <= end for start, end in spans):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                if (relative, match.group(0)) in UNMANAGED_GENERATOR_PINS:
                    continue
                unmanaged.append(f"{relative}:{line}: {match.group(0)}")
        self.assertEqual(unmanaged, [])

    def test_every_recorded_unmanaged_pin_still_exists(self) -> None:
        for relative, pin in UNMANAGED_GENERATOR_PINS:
            self.assertIn(pin, (ROOT / relative).read_text(encoding="utf-8"), relative)


if __name__ == "__main__":
    unittest.main(verbosity=2)
