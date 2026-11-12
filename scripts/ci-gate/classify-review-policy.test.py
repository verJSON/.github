#!/usr/bin/env python3
import json
import subprocess
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("classify-review-policy.py")


def classify(*files: tuple[str, str]) -> dict[str, str]:
    payload = [{"filename": filename, "status": status} for filename, status in files]
    result = subprocess.run(
        ["python3", str(SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class ReviewPolicyClassificationTest(unittest.TestCase):
    def assert_lane(self, expected: str, *files: tuple[str, str]) -> None:
        self.assertEqual(classify(*files)["lane"], expected)

    def test_ordinary_code_requires_ai_review(self) -> None:
        self.assert_lane("ai", ("src/service.ts", "modified"))

    def test_executable_dependency_pin_and_manifest_require_ai_review(self) -> None:
        self.assert_lane("ai", (".github/workflows/ci.yml", "modified"))
        self.assert_lane("ai", ("package.json", "modified"), ("package-lock.json", "modified"))

    def test_generated_lockfile_only_change_may_skip_ai_review(self) -> None:
        self.assert_lane("fast", ("package-lock.json", "modified"))
        self.assert_lane("fast", ("services/api/go.sum", "modified"))

    def test_non_agent_documentation_may_skip_ai_review(self) -> None:
        self.assert_lane("fast", ("README.md", "modified"), ("docs/usage.md", "added"))
        self.assert_lane("fast", ("NEXT/2026-08-12-issue-767-review-policy.md", "added"))

    def test_agent_instructions_prompts_and_skills_require_ai_review(self) -> None:
        for filename in (
            "CLAUDE.md",
            "AGENTS.md",
            ".github/copilot-instructions.md",
            "rules/security.md",
            "skills/reviewer/SKILL.md",
            "SKILL.md",
            "review-prompt.md",
            "instructions.md",
            "prompts/code-review.md",
        ):
            with self.subTest(filename=filename):
                self.assert_lane("ai", (filename, "modified"))

    def test_deleting_code_or_agent_instructions_still_requires_ai_review(self) -> None:
        self.assert_lane("ai", ("src/obsolete.ts", "removed"))
        self.assert_lane("ai", ("CLAUDE.md", "removed"))

    def test_mixed_lockfile_and_code_change_requires_ai_review(self) -> None:
        self.assert_lane("ai", ("pnpm-lock.yaml", "modified"), ("src/index.ts", "modified"))

    def test_malformed_input_fails_closed(self) -> None:
        result = subprocess.run(
            ["python3", str(SCRIPT)],
            input='[{"status":"modified"}]',
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("each file requires non-empty filename and status strings", result.stderr)


if __name__ == "__main__":
    unittest.main()
