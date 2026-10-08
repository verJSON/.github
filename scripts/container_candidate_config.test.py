import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "docs/decisions/0078-container-release-and-runner-deployment-contract/candidate-config.schema.json"
PUBLISHER = ROOT / ".github/workflows/container-candidate-publish.yml"
FIXTURE = ROOT / "scripts/fixtures/container-candidate/single.json"


class ContainerCandidateConfigTests(unittest.TestCase):
    def test_cleanup_setting_is_optional_boolean_and_defaults_off(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        setting = schema["properties"]["cleanupUnusedDockerImages"]
        self.assertEqual("boolean", setting["type"])
        self.assertIs(setting["default"], False)

        config = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertFalse(config.get("cleanupUnusedDockerImages", False))
        config["cleanupUnusedDockerImages"] = True
        self.assertIsInstance(config["cleanupUnusedDockerImages"], bool)

    def test_publisher_validates_and_exports_cleanup_setting(self) -> None:
        workflow = PUBLISHER.read_text(encoding="utf-8")
        self.assertIn('if has("cleanupUnusedDockerImages") then (.cleanupUnusedDockerImages | type == "boolean")', workflow)
        self.assertIn('.cleanupUnusedDockerImages // false', workflow)
        self.assertIn("cleanup-unused-docker-images:", workflow)
        for path in (
            ROOT / ".github/workflows/container-candidate.yml",
            PUBLISHER,
        ):
            text = path.read_text(encoding="utf-8")
            self.assertIn("cleanup-unused-docker-images:", text)
            self.assertIn("type: boolean", text)


if __name__ == "__main__":
    unittest.main()
