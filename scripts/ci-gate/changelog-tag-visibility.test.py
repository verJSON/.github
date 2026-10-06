#!/usr/bin/env python3

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_WORKFLOWS = {
    ROOT / ".github/workflows/changelog-validate.yml": "changelog-validate.yml",
    ROOT / ".github/workflows/generated-artifacts.yml": "generated-artifacts.yml",
}


def checkout_consumer_step(text: str) -> str:
    match = re.search(
        r"^      - name: Check out consumer\n(?P<body>(?:^        .*\n|^          .*\n)+)",
        text,
        re.MULTILINE,
    )
    if match is None:
        raise AssertionError("workflow has no named consumer checkout")
    return match.group(0)


def validate_complete_tag_checkout(text: str) -> None:
    step = checkout_consumer_step(text)
    if not re.search(r"^          fetch-depth: 0$", step, re.MULTILINE):
        raise AssertionError("consumer checkout does not fetch complete history and tags")
    if not re.search(r"^          fetch-tags: true$", step, re.MULTILINE):
        raise AssertionError("consumer checkout does not explicitly fetch tags")
    if not re.search(r"^          persist-credentials: false$", step, re.MULTILINE):
        raise AssertionError("consumer checkout persists a credential")


class ChangelogTagVisibilityTests(unittest.TestCase):
    def test_every_runtime_check_pr_entrypoint_fetches_complete_tags(self) -> None:
        discovered = {}
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            if "check-pr" in text:
                discovered[path] = path.name
        self.assertEqual(RUNTIME_WORKFLOWS, discovered)
        for path in discovered:
            with self.subTest(path=path.name):
                validate_complete_tag_checkout(path.read_text(encoding="utf-8"))

    def test_missing_or_shallow_fetch_depth_fails_the_contract(self) -> None:
        for path in RUNTIME_WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            attacks = (
                text.replace("          fetch-depth: 0\n", "", 1),
                text.replace("          fetch-depth: 0", "          fetch-depth: 1", 1),
                text.replace("          fetch-tags: true\n", "", 1),
                text.replace("          fetch-tags: true", "          fetch-tags: false", 1),
            )
            for index, attack in enumerate(attacks):
                with self.subTest(path=path.name, attack=index):
                    with self.assertRaises(AssertionError):
                        validate_complete_tag_checkout(attack)

    def test_generators_route_both_caller_modes_to_reviewed_entrypoints(self) -> None:
        ref = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        generator = ROOT / "scripts/gen-changelog-caller.sh"
        for mode in ("workflow", "generated-artifacts"):
            output = subprocess.run(
                ["bash", str(generator), mode, ref],
                cwd=ROOT,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout
            with self.subTest(mode=mode):
                self.assertIn(
                    f"uses: verJSON/.github/.github/workflows/generated-artifacts.yml@{ref}",
                    output,
                )
                self.assertNotIn("scripts/changelog.py check-pr", output)


if __name__ == "__main__":
    unittest.main()
