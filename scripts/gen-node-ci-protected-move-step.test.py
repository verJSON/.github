#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts/gen-node-ci-protected.py"


def load_generator():
    spec = importlib.util.spec_from_file_location("gen_node_ci_protected", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MoveStepBeforeGuardBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.module = load_generator()

    def test_a_non_step_bullet_at_six_spaces_inside_the_moving_step_does_not_truncate_it(self):
        # The moving step's own run: block happens to emit a line that is
        # indented exactly six spaces and starts with "- ", but is not a
        # step marker (no "name:"/"uses:" key). The real next step follows
        # further down; the boundary scan must not stop early on it.
        document = (
            "jobs:\n"
            "  example:\n"
            "    steps:\n"
            "      - name: Guard step\n"
            "        run: guard\n"
            "      - name: Before step\n"
            "        run: before\n"
            "      - name: Move me\n"
            "        run: |\n"
            "          cat <<'YAML'\n"
            "      - some: embedded-fixture-line\n"
            "          YAML\n"
            "      - name: Trailing step\n"
            "        run: trailing\n"
        )
        result = self.module.move_step_before_guard(
            document, "Move me", "Before step", "Guard step"
        )
        # Exact equality, not membership: the old boundary scan matched the
        # embedded "- some: ..." bullet as a false step marker, truncating
        # "Move me" mid-heredoc and stranding its closing "YAML" marker as an
        # orphaned line after "Before step" instead of moving with its step.
        self.assertEqual(
            result,
            "jobs:\n"
            "  example:\n"
            "    steps:\n"
            "      - name: Move me\n"
            "        run: |\n"
            "          cat <<'YAML'\n"
            "      - some: embedded-fixture-line\n"
            "          YAML\n"
            "      - name: Guard step\n"
            "        run: guard\n"
            "      - name: Before step\n"
            "        run: before\n"
            "      - name: Trailing step\n"
            "        run: trailing\n",
        )

    def test_a_real_step_starting_with_id_or_run_is_still_recognized_as_the_boundary(self):
        # Not every step's first key is "name:" or "uses:" — this repo's own
        # generated workflow has real steps opening with "- id: " and
        # "- run: ". The boundary scan must recognize those too instead of
        # scanning past them.
        document = (
            "jobs:\n"
            "  example:\n"
            "    steps:\n"
            "      - name: Guard step\n"
            "        run: guard\n"
            "      - name: Before step\n"
            "        run: before\n"
            "      - name: Move me\n"
            "        run: move\n"
            "      - id: check\n"
            "        run: echo check\n"
        )
        result = self.module.move_step_before_guard(
            document, "Move me", "Before step", "Guard step"
        )
        self.assertEqual(
            result,
            "jobs:\n"
            "  example:\n"
            "    steps:\n"
            "      - name: Move me\n"
            "        run: move\n"
            "      - name: Guard step\n"
            "        run: guard\n"
            "      - name: Before step\n"
            "        run: before\n"
            "      - id: check\n"
            "        run: echo check\n",
        )

    def test_no_step_marker_after_the_moving_step_fails_closed(self):
        document = (
            "jobs:\n"
            "  example:\n"
            "    steps:\n"
            "      - name: Guard step\n"
            "        run: guard\n"
            "      - name: Before step\n"
            "        run: before\n"
            "      - name: Move me\n"
            "        run: |\n"
            "          echo done\n"
        )
        with self.assertRaises(SystemExit) as raised:
            self.module.move_step_before_guard(
                document, "Move me", "Before step", "Guard step"
            )
        self.assertEqual(
            raised.exception.code,
            "protected node-ci step ordering boundary drifted: no step marker found "
            "after 'Move me' (before 'Before step', guard 'Guard step')",
        )


if __name__ == "__main__":
    unittest.main()
