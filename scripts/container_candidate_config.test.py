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

    def test_post_publish_smoke_is_optional_object(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        setting = schema["properties"]["postPublishSmoke"]
        self.assertEqual("object", setting["type"])
        self.assertEqual(["variant", "command"], setting["required"])
        self.assertIs(setting["additionalProperties"], False)

        config = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertNotIn("postPublishSmoke", config)

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

    def test_publisher_validates_and_exports_post_publish_smoke(self) -> None:
        workflow = PUBLISHER.read_text(encoding="utf-8")
        self.assertIn('has("postPublishSmoke")', workflow)
        self.assertIn("post-publish-smoke:", workflow)
        self.assertIn("published candidate digest is missing", workflow)
        self.assertIn("post-publish smoke command is missing", workflow)
        self.assertIn("IMAGE_REF=", workflow)
        inputs = workflow.split("workflow_call:", 1)[1]
        self.assertIn("post-publish-smoke:", inputs)
        self.assertIn("type: boolean", inputs)


if __name__ == "__main__":
    unittest.main()
