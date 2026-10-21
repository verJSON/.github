#!/usr/bin/env python3
from __future__ import annotations

import base64
import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "renovate_changelog", ROOT / "scripts/renovate-changelog.py"
)
assert SPEC and SPEC.loader
renovate_changelog = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = renovate_changelog
SPEC.loader.exec_module(renovate_changelog)


HEAD = "a" * 40
TREE = "b" * 40
BLOB = "c" * 40
NEW_TREE = "d" * 40
NEW_COMMIT = "e" * 40
REPOSITORY = "Verjson/example"
PR_NUMBER = 263
BODY = """This PR contains the following updates:

| Package | Change |
|---|---|
| [ip-address](https://redirect.github.com/ip-num/ip-num) | [`10.4.0` → `10.5.0`](https://renovatebot.com/diffs/npm/ip-address/10.4.0/10.5.0) |
"""
PNPM_PACKAGE_MANAGER_BODY = """This PR contains the following updates:

| Package | Change | [Age](https://docs.renovatebot.com/merge-confidence/) | [Adoption](https://docs.renovatebot.com/merge-confidence/) | [Passing](https://docs.renovatebot.com/merge-confidence/) | [Confidence](https://docs.renovatebot.com/merge-confidence/) |
|---|---|---|---|---|---|
| [pnpm](https://pnpm.io) ([source](https://redirect.github.com/pnpm/pnpm/tree/HEAD/pnpm11/pnpm)) | [`11.22.0+sha512.1ff870c4c6133dfd88fb2afc46dd13d47f09c9794b438c6fdb47ca98caf3bc16381ee0be93a091b8e3824cf01f889f46d7d9e20910fb0be1ab0fb5baa80dd621` → `11.23.0`](https://renovatebot.com/diffs/npm/pnpm/11.22.0/11.23.0) | ![age](https://developer.mend.io/api/mc/badges/age/npm/pnpm/11.23.0?slim=true) | ![adoption](https://developer.mend.io/api/mc/badges/adoption/npm/pnpm/11.23.0?slim=true) | ![passing](https://developer.mend.io/api/mc/badges/compatibility/npm/pnpm/11.22.0/11.23.0?slim=true) | ![confidence](https://developer.mend.io/api/mc/badges/confidence/npm/pnpm/11.22.0/11.23.0?slim=true) |
"""
# Verbatim body of Verjson/AiB#231 ("chore(deps): lock file maintenance"): the
# weekly lockFileMaintenance class carries a two-column `Update`/`Change` table
# with no `Package` column at all (Verjson/.github#958).
LOCK_FILE_BODY = """This PR contains the following updates:

| Update | Change |
|---|---|
| lockFileMaintenance | All locks refreshed |
"""
NEO4J_DIGEST_BODY = """This PR contains the following updates:

| Package | Update | Change |
|---|---|---|
| neo4j | digest | `f79a46a` → `0ddfa71` |
"""

# Update table of Verjson/verjson-ci#290 ("chore(deps): update node.js to v26.12.0"):
# one image bumped in two Dockerfiles, so Renovate lists the alpine3.23 transition
# once per file/stage and the rows differ only in the Type column.
VERJSON_CI_290_BODY = """This PR contains the following updates:

| Package | Type | Update | Change |
|---|---|---|---|
| [node](https://hub.docker.com/_/node) ([source](https://redirect.github.com/nodejs/node)) | final | minor | `26.10.0-alpine` → `26.12.0-alpine` |
| [node](https://hub.docker.com/_/node) ([source](https://redirect.github.com/nodejs/node)) | final | minor | `26.10.0-alpine3.23` → `26.12.0-alpine3.23` |
| [node](https://hub.docker.com/_/node) ([source](https://redirect.github.com/nodejs/node)) | stage | minor | `26.10.0-alpine3.23` → `26.12.0-alpine3.23` |

---
"""


class FakeClient:
    def __init__(self, responses: dict[tuple[str, str], list[Any]], pages: list[Any] | None = None):
        self.responses = {key: list(values) for key, values in responses.items()}
        self.page_values = list(pages or [])
        self.calls: list[tuple[str, str, Any]] = []

    def request(self, method: str, path: str, payload: Any = None) -> Any:
        self.calls.append((method, path, payload))
        key = (method, path)
        if key not in self.responses or not self.responses[key]:
            raise AssertionError(f"unexpected API call: {method} {path}")
        value = self.responses[key].pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def pages(self, path: str) -> list[Any]:
        self.calls.append(("PAGES", path, None))
        return list(self.page_values)


def pull_request(
    *,
    body: str = BODY,
    author: str = "app/renovate",
    head_repository: str = REPOSITORY,
    head_ref: str = "renovate/ip-address-10.x",
    head_sha: str = HEAD,
    base_ref: str = "main",
) -> dict[str, Any]:
    return {
        "number": PR_NUMBER,
        "state": "open",
        "merged": False,
        "user": {"login": author},
        "body": body,
        "head": {
            "ref": head_ref,
            "sha": head_sha,
            "repo": {"full_name": head_repository},
        },
        "base": {"ref": base_ref},
    }


class RenovateTableTests(unittest.TestCase):
    def test_strictly_parses_the_documented_renovate_update_row(self) -> None:
        self.assertEqual(
            (
                renovate_changelog.Update(
                    package="ip-address", from_version="10.4.0", to_version="10.5.0"
                ),
            ),
            renovate_changelog.parse_updates(BODY),
        )

    def test_parses_escaped_union_ranges_without_creating_table_cells(self) -> None:
        body = BODY.replace(
            "`10.4.0` → `10.5.0`",
            r"`^0.1.3 \|\| ^1.0.0` → `^0.2.0 \|\| ^1.1.0`",
        )

        self.assertEqual(
            renovate_changelog.Update(
                package="ip-address",
                from_version="^0.1.3 || ^1.0.0",
                to_version="^0.2.0 || ^1.1.0",
            ),
            renovate_changelog.parse_updates(body)[0],
        )

    def test_parses_escaped_comparator_ranges_unioned_with_carets(self) -> None:
        # Verjson/.github#1602: a spaced comparator range as one union alternative.
        comparator_union = r"`>=0.2.2 <0.4.0 \|\| ^1.0.0 \|\| ^0.4.0`"
        fixtures = (
            (
                rf"`>=0.2.2 <0.4.0 \|\| ^1.0.0` → {comparator_union}",
                ">=0.2.2 <0.4.0 || ^1.0.0",
                ">=0.2.2 <0.4.0 || ^1.0.0 || ^0.4.0",
            ),
            (
                rf"`1.0.0` → {comparator_union}",
                "1.0.0",
                ">=0.2.2 <0.4.0 || ^1.0.0 || ^0.4.0",
            ),
            (
                rf"{comparator_union} → `1.1.0`",
                ">=0.2.2 <0.4.0 || ^1.0.0 || ^0.4.0",
                "1.1.0",
            ),
        )
        for change, from_version, to_version in fixtures:
            with self.subTest(change=change):
                body = BODY.replace("`10.4.0` → `10.5.0`", change)

                self.assertEqual(
                    renovate_changelog.Update(
                        package="ip-address",
                        from_version=from_version,
                        to_version=to_version,
                    ),
                    renovate_changelog.parse_updates(body)[0],
                )

    def test_rejects_unescaped_union_pipes_as_table_delimiters(self) -> None:
        body = BODY.replace("`10.4.0` → `10.5.0`", "`^0.1.3 || ^1.0.0` → `^0.2.0`")

        with self.assertRaises(renovate_changelog.AutomationError):
            renovate_changelog.parse_updates(body)

    def test_rejects_malformed_escapes_and_non_union_pipe_values(self) -> None:
        fixtures = (
            r"`^0.1.3 \q ^1.0.0` → `^0.2.0`",
            "`^0.1.3 \\` → `^0.2.0`",
            r"`^0.1.3 \| ^1.0.0` → `^0.2.0`",
            r"`^0.1.3 \|\|^1.0.0` → `^0.2.0`",
            r"`^0.1.3 \|\| ${{ secrets.TOKEN }}` → `^0.2.0`",
        )
        for change in fixtures:
            with self.subTest(change=change):
                body = BODY.replace("`10.4.0` → `10.5.0`", change)
                with self.assertRaises(renovate_changelog.AutomationError):
                    renovate_changelog.parse_updates(body)

    def test_parses_integrity_qualified_package_manager_pnpm_row(self) -> None:
        update = renovate_changelog.parse_updates(PNPM_PACKAGE_MANAGER_BODY)[0]
        self.assertEqual("pnpm", update.package)
        self.assertEqual("11.23.0", update.to_version)
        self.assertTrue(update.from_version.startswith("11.22.0+sha512."))

    def test_rejects_malformed_or_non_pnpm_integrity_qualified_versions(self) -> None:
        fixtures = (
            PNPM_PACKAGE_MANAGER_BODY.replace("+sha512.", "+sha256.", 1),
            PNPM_PACKAGE_MANAGER_BODY.replace("dd621`", "dd62`", 1),
            PNPM_PACKAGE_MANAGER_BODY.replace("dd621`", "dd62A`", 1),
            PNPM_PACKAGE_MANAGER_BODY.replace("[pnpm]", "[npm]", 1),
        )
        for body in fixtures:
            with self.subTest(body=body):
                with self.assertRaisesRegex(
                    renovate_changelog.AutomationError, "unsafe version"
                ):
                    renovate_changelog.parse_updates(body)

    def test_rejects_short_malformed_pnpm_integrity_pins_in_either_version(self) -> None:
        fixtures = (
            PNPM_PACKAGE_MANAGER_BODY.replace(
                "11.22.0+sha512.1ff870c4c6133dfd88fb2afc46dd13d47f09c9794b438c6fdb47ca98caf3bc16381ee0be93a091b8e3824cf01f889f46d7d9e20910fb0be1ab0fb5baa80dd621",
                "11.22.0+sha512.deadbeef",
            ),
            PNPM_PACKAGE_MANAGER_BODY.replace(
                "11.23.0`", "11.23.0+sha512.deadbeef`"
            ),
        )
        for body in fixtures:
            with self.subTest(body=body):
                with self.assertRaisesRegex(
                    renovate_changelog.AutomationError, "unsafe version"
                ):
                    renovate_changelog.parse_updates(body)

    def test_recognizes_the_lock_file_maintenance_table_shape(self) -> None:
        updates = renovate_changelog.parse_updates(LOCK_FILE_BODY)
        self.assertEqual(renovate_changelog.LOCK_FILE_MAINTENANCE, updates)

    def test_accepts_lock_file_change_wording_that_renovate_may_reword(self) -> None:
        body = LOCK_FILE_BODY.replace("All locks refreshed", "All lock files refreshed")
        self.assertEqual(
            renovate_changelog.LOCK_FILE_MAINTENANCE,
            renovate_changelog.parse_updates(body),
        )

    def test_ignores_an_unrelated_two_column_table_in_a_package_body(self) -> None:
        quoted = BODY + "\n| Update | Change |\n|---|---|\n| Docs | Rewritten |\n"
        self.assertEqual(
            (
                renovate_changelog.Update(
                    package="ip-address", from_version="10.4.0", to_version="10.5.0"
                ),
            ),
            renovate_changelog.parse_updates(quoted),
        )

    def test_rejects_lock_file_tables_outside_the_documented_shape(self) -> None:
        row = LOCK_FILE_BODY.splitlines()[-1] + "\n"
        fixtures = (
            ("a second update table", LOCK_FILE_BODY + "\n" + BODY),
            ("a repeated maintenance row", LOCK_FILE_BODY + row),
            (
                "an unexpected update type",
                LOCK_FILE_BODY.replace("lockFileMaintenance", "rangeStrategy"),
            ),
            (
                "a marked-up change cell",
                LOCK_FILE_BODY.replace(
                    "All locks refreshed", "[All locks refreshed](https://attacker.invalid)"
                ),
            ),
            ("an empty change cell", LOCK_FILE_BODY.replace("All locks refreshed", "")),
            (
                "an extra column",
                LOCK_FILE_BODY.replace("| Update | Change |", "| Update | Change | Notes |"),
            ),
            (
                "renamed columns",
                LOCK_FILE_BODY.replace("| Update | Change |", "| Type | Result |"),
            ),
        )
        for description, body in fixtures:
            with self.subTest(description=description):
                with self.assertRaises(renovate_changelog.AutomationError):
                    renovate_changelog.parse_updates(body)

    def test_rejects_a_header_row_with_no_lines_after_it(self) -> None:
        # #971: a body whose final line is a bare header row has no separator
        # row to read; that must fail closed as AutomationError, not raise an
        # unhandled IndexError past the AutomationError boundary.
        truncated = "This PR contains the following updates:\n\n| Package | Change |"
        with self.assertRaises(renovate_changelog.AutomationError):
            renovate_changelog.parse_updates(truncated)

    def test_accepts_bare_docker_image_digest_row(self) -> None:
        self.assertEqual(
            (
                renovate_changelog.Update(
                    package="neo4j", from_version="f79a46a", to_version="0ddfa71"
                ),
            ),
            renovate_changelog.parse_updates(NEO4J_DIGEST_BODY),
        )

        longest_name = "n" * 214
        body = NEO4J_DIGEST_BODY.replace("| neo4j |", f"| {longest_name} |")
        self.assertEqual(longest_name, renovate_changelog.parse_updates(body)[0].package)

    def test_rejects_ambiguous_or_malformed_package_tables(self) -> None:
        duplicate = BODY + "\n" + BODY
        malformed_change = BODY.replace(" → ", " -> ")
        for body in (duplicate, malformed_change):
            with self.subTest(body=body):
                with self.assertRaises(renovate_changelog.AutomationError):
                    renovate_changelog.parse_updates(body)

    def test_bare_package_cells_fail_closed_on_unsafe_or_ambiguous_text(self) -> None:
        fixtures = (
            "neo4j enterprise",
            "neo4j\tenterprise",
            "**neo4j**",
            "`neo4j`",
            "[neo4j](https://example.invalid",
            "neo4j](https://attacker.invalid)",
            "neo4j<script>",
            "neo4j|attacker",
            "n" * 215,
        )
        for package_cell in fixtures:
            with self.subTest(package_cell=package_cell):
                body = NEO4J_DIGEST_BODY.replace("| neo4j |", f"| {package_cell} |")
                with self.assertRaises(renovate_changelog.AutomationError):
                    renovate_changelog.parse_updates(body)

    def test_reports_the_invalid_update_cell(self) -> None:
        invalid_package = NEO4J_DIGEST_BODY.replace("| neo4j |", "| **neo4j** |")
        invalid_change = NEO4J_DIGEST_BODY.replace(" → ", " -> ")
        with self.assertRaisesRegex(
            renovate_changelog.AutomationError, "unsupported package cell"
        ):
            renovate_changelog.parse_updates(invalid_package)
        with self.assertRaisesRegex(
            renovate_changelog.AutomationError, "unsupported change cell"
        ):
            renovate_changelog.parse_updates(invalid_change)

    def test_grouped_docker_variants_preserve_both_transitions_in_the_fragment(self) -> None:
        body = """| Package | Type | Update | Change |
|---|---|---|---|
| [node](https://hub.docker.com/_/node) ([source](https://redirect.github.com/nodejs/node)) | final | minor | `24.19.0-alpine` → `24.20.0-alpine` |
| [node](https://hub.docker.com/_/node) ([source](https://redirect.github.com/nodejs/node)) |  | minor | `24.19.0-bookworm` → `24.20.0-bookworm` |
"""
        updates = renovate_changelog.parse_updates(body)
        self.assertEqual(updates, (
            renovate_changelog.Update("node", "24.19.0-alpine", "24.20.0-alpine"),
            renovate_changelog.Update("node", "24.19.0-bookworm", "24.20.0-bookworm"),
        ))
        _, fragment = renovate_changelog.fragment_for(229, updates, dt.date(2026, 9, 10))
        self.assertIn("`node` from `24.19.0-alpine` to `24.20.0-alpine`", fragment)
        self.assertIn("`node` from `24.19.0-bookworm` to `24.20.0-bookworm`", fragment)
        for duplicate in (body.splitlines()[-1], body.splitlines()[-1].replace(
            "[node](https://hub.docker.com/_/node) ([source](https://redirect.github.com/nodejs/node))", "node"
        )):
            with self.subTest(duplicate=duplicate):
                self.assertEqual(
                    renovate_changelog.parse_updates(body + duplicate + "\n"), updates
                )

    def test_identical_rows_from_one_image_in_several_files_collapse_to_one_transition(
        self,
    ) -> None:
        updates = renovate_changelog.parse_updates(VERJSON_CI_290_BODY)
        self.assertEqual(updates, (
            renovate_changelog.Update("node", "26.10.0-alpine", "26.12.0-alpine"),
            renovate_changelog.Update("node", "26.10.0-alpine3.23", "26.12.0-alpine3.23"),
        ))
        _, fragment = renovate_changelog.fragment_for(290, updates, dt.date(2026, 10, 21))
        self.assertEqual(fragment.count("from `26.10.0-alpine3.23` to `26.12.0-alpine3.23`"), 1)
        self.assertEqual(fragment.count("from `26.10.0-alpine` to `26.12.0-alpine`"), 1)

    def test_an_identical_package_row_collapses_into_the_single_update_fragment(self) -> None:
        row = BODY.splitlines()[-1]
        updates = renovate_changelog.parse_updates(BODY + row + "\n")
        self.assertEqual(updates, (
            renovate_changelog.Update("ip-address", "10.4.0", "10.5.0"),
        ))
        _, fragment = renovate_changelog.fragment_for(PR_NUMBER, updates, dt.date(2026, 10, 21))
        self.assertIn("title: Update dependency ip-address from 10.4.0 to 10.5.0\n", fragment)

    def test_three_identical_rows_collapse_to_one_transition(self) -> None:
        repeated = VERJSON_CI_290_BODY.replace(
            "\n\n---", "\n" + VERJSON_CI_290_BODY.splitlines()[-3] + "\n\n---"
        )
        self.assertEqual(repeated.count("`26.10.0-alpine3.23` → `26.12.0-alpine3.23`"), 3)
        self.assertEqual(
            renovate_changelog.parse_updates(repeated),
            renovate_changelog.parse_updates(VERJSON_CI_290_BODY),
        )

    def test_rows_differing_only_in_cell_padding_collapse(self) -> None:
        padded = "|   ip-address   |   `10.4.0` → `10.5.0`   |"
        self.assertEqual(
            renovate_changelog.parse_updates(BODY + padded + "\n"),
            (renovate_changelog.Update("ip-address", "10.4.0", "10.5.0"),),
        )

    def test_case_variant_packages_and_versions_stay_distinct_transitions(self) -> None:
        body = """| Package | Change |
|---|---|
| github.com/sirupsen/logrus | `v1.9.0` → `v1.9.3` |
| github.com/Sirupsen/logrus | `v1.9.0` → `v1.9.3` |
| github.com/sirupsen/logrus | `V1.9.0` → `v1.9.3` |
"""
        self.assertEqual(renovate_changelog.parse_updates(body), (
            renovate_changelog.Update("github.com/sirupsen/logrus", "v1.9.0", "v1.9.3"),
            renovate_changelog.Update("github.com/Sirupsen/logrus", "v1.9.0", "v1.9.3"),
            renovate_changelog.Update("github.com/sirupsen/logrus", "V1.9.0", "v1.9.3"),
        ))

    def test_same_package_with_a_different_from_or_to_is_never_collapsed(self) -> None:
        body = """| Package | Change |
|---|---|
| node | `26.10.0-alpine` → `26.12.0-alpine` |
| node | `26.10.0-alpine` → `27.0.0-alpine` |
| node | `26.11.0-alpine` → `26.12.0-alpine` |
| node | `26.10.0-alpine` → `26.12.0-alpine` |
"""
        updates = renovate_changelog.parse_updates(body)
        self.assertEqual(updates, (
            renovate_changelog.Update("node", "26.10.0-alpine", "26.12.0-alpine"),
            renovate_changelog.Update("node", "26.10.0-alpine", "27.0.0-alpine"),
            renovate_changelog.Update("node", "26.11.0-alpine", "26.12.0-alpine"),
        ))
        _, fragment = renovate_changelog.fragment_for(290, updates, dt.date(2026, 10, 21))
        self.assertIn("title: Update 3 dependencies with Renovate\n", fragment)

    def test_a_non_adjacent_repeat_keeps_other_packages_in_first_seen_order(self) -> None:
        body = """| Package | Change |
|---|---|
| ip-address | `10.4.0` → `10.5.0` |
| neo4j | `f79a46a` → `0ddfa71` |
| ip-address | `10.4.0` → `10.5.0` |
"""
        self.assertEqual(renovate_changelog.parse_updates(body), (
            renovate_changelog.Update("ip-address", "10.4.0", "10.5.0"),
            renovate_changelog.Update("neo4j", "f79a46a", "0ddfa71"),
        ))

    def test_the_row_bound_counts_repeated_rows_before_collapsing(self) -> None:
        row = BODY.splitlines()[-1]
        limit = renovate_changelog.MAX_UPDATES
        at_limit = BODY + (row + "\n") * (limit - 1)
        self.assertEqual(len(renovate_changelog.parse_updates(at_limit)), 1)
        with self.assertRaisesRegex(
            renovate_changelog.AutomationError, f"exceeds {limit} rows"
        ):
            renovate_changelog.parse_updates(at_limit + row + "\n")

    def test_rejects_version_text_that_could_inject_markdown(self) -> None:
        injected = BODY.replace(
            "`10.5.0`", "`10.5.0](https://attacker.invalid)`", 1
        )
        with self.assertRaisesRegex(renovate_changelog.AutomationError, "unsafe version"):
            renovate_changelog.parse_updates(injected)

    def test_accepts_common_range_prefixes_from_grouped_renovate_updates(self) -> None:
        grouped = BODY.replace("`10.4.0`", "`^10.4.0`").replace(
            "`10.5.0`", "`^10.5.0`"
        )
        update = renovate_changelog.parse_updates(grouped)[0]
        self.assertEqual(("^10.4.0", "^10.5.0"), (update.from_version, update.to_version))

    def test_accepts_a_package_cell_with_renovates_default_source_link(self) -> None:
        # Verbatim from live PR bodies (Verjson/.github#925): Renovate appends
        # ` ([source](…))` to the Package cell whenever a package's sourceUrl
        # differs from its homepage, which is the default presentation, not an
        # opt-in.
        for package_cell, expected_name in (
            (
                "[vitest](https://vitest.dev) "
                "([source](https://redirect.github.com/vitest-dev/vitest/tree/HEAD/packages/vitest))",
                "vitest",
            ),
            (
                "[@types/node](https://redirect.github.com/DefinitelyTyped/DefinitelyTyped/tree/HEAD/types/node) "
                "([source](https://redirect.github.com/DefinitelyTyped/DefinitelyTyped/tree/HEAD/types/node))",
                "@types/node",
            ),
        ):
            with self.subTest(package_cell=package_cell):
                body = BODY.replace(
                    "[ip-address](https://redirect.github.com/ip-num/ip-num)", package_cell
                )
                update = renovate_changelog.parse_updates(body)[0]
                self.assertEqual(expected_name, update.package)

    def test_accepts_renovates_decorative_package_link_lists(self) -> None:
        package_cells = (
            "[ruff](https://docs.astral.sh/ruff) "
            "([source](https://redirect.github.com/astral-sh/ruff), "
            "[changelog](https://redirect.github.com/astral-sh/ruff/blob/main/CHANGELOG.md))",
            "[ruff](https://docs.astral.sh/ruff) "
            "([source](https://redirect.github.com/astral-sh/ruff)) "
            "([changelog](https://redirect.github.com/astral-sh/ruff/blob/main/CHANGELOG.md))",
        )
        for package_cell in package_cells:
            with self.subTest(package_cell=package_cell):
                body = BODY.replace(
                    "[ip-address](https://redirect.github.com/ip-num/ip-num)",
                    package_cell,
                )
                self.assertEqual("ruff", renovate_changelog.parse_updates(body)[0].package)

    def test_rejects_malformed_decorative_package_link_lists(self) -> None:
        package_cells = (
            "[ruff](https://docs.astral.sh/ruff) "
            "([source](https://redirect.github.com/astral-sh/ruff), attacker)",
            "[ruff](https://docs.astral.sh/ruff) "
            "([source](https://redirect.github.com/astral-sh/ruff); "
            "[changelog](https://redirect.github.com/astral-sh/ruff/blob/main/CHANGELOG.md))",
            "[ruff](https://docs.astral.sh/ruff) "
            "([source](https://redirect.github.com/astral-sh/ruff), "
            "[changelog](http://attacker.invalid/changelog))",
            "[ruff](https://docs.astral.sh/ruff) "
            "([source](https://redirect.github.com/astral-sh/ruff), "
            "[changelog](https://redirect.github.com/astral-sh/ruff)) trailing",
        )
        for package_cell in package_cells:
            with self.subTest(package_cell=package_cell):
                body = BODY.replace(
                    "[ip-address](https://redirect.github.com/ip-num/ip-num)",
                    package_cell,
                )
                with self.assertRaisesRegex(
                    renovate_changelog.AutomationError, "unsupported package cell"
                ):
                    renovate_changelog.parse_updates(body)


class AdmissionAndPlanningTests(unittest.TestCase):
    def test_accepts_only_same_repository_renovate_branches_and_authors(self) -> None:
        fixtures = (
            (pull_request(author="attacker"), "approved Renovate App"),
            (pull_request(head_repository="attacker/example"), "cross-repository"),
            (pull_request(head_ref="feature/not-renovate"), "renovate/"),
            (pull_request(head_sha="f" * 40), "triggering event"),
        )
        for document, message in fixtures:
            with self.subTest(message=message):
                with self.assertRaisesRegex(renovate_changelog.AutomationError, message):
                    renovate_changelog.admit_pull_request(
                        document, REPOSITORY, PR_NUMBER, HEAD, "main"
                    )

    def test_noops_when_the_pr_already_adds_a_canonical_fragment(self) -> None:
        path = "NEXT/2026-08-15-issue-263-existing.md"
        content = """---
date: 2026-08-15
issue: 263
impact: patch
title: Document dependency update
---

Document the dependency update.
"""
        encoded_content = base64.b64encode(content.encode()).decode()
        client = FakeClient(
            {
                ("GET", f"repos/{REPOSITORY}/pulls/{PR_NUMBER}"): [pull_request()],
                (
                    "GET",
                    f"repos/{REPOSITORY}/contents/{path}?ref={HEAD}",
                ): [
                    {
                        "type": "file",
                        "encoding": "base64",
                        "content": "\n".join(
                            encoded_content[index : index + 60]
                            for index in range(0, len(encoded_content), 60)
                        ),
                    }
                ],
            },
            pages=[{"filename": path, "status": "added"}],
        )
        result = renovate_changelog.plan(
            client, REPOSITORY, PR_NUMBER, HEAD, "main", dt.date(2026, 8, 16)
        )
        self.assertFalse(result["required"])
        self.assertNotIn("fragment_content", result)

    def test_synthesizes_a_fragment_when_an_added_fragment_omits_impact(self) -> None:
        path = "NEXT/2026-08-15-issue-263-existing.md"
        content = """---
date: 2026-08-15
issue: 263
title: Document dependency update
---

Document the dependency update.
"""
        client = FakeClient(
            {
                ("GET", f"repos/{REPOSITORY}/pulls/{PR_NUMBER}"): [pull_request()],
                ("GET", f"repos/{REPOSITORY}/contents/{path}?ref={HEAD}"): [
                    {
                        "type": "file",
                        "encoding": "base64",
                        "content": base64.b64encode(content.encode()).decode(),
                    }
                ],
            },
            pages=[{"filename": path, "status": "added"}],
        )
        result = renovate_changelog.plan(
            client, REPOSITORY, PR_NUMBER, HEAD, "main", dt.date(2026, 8, 16)
        )
        self.assertTrue(result["required"])
        self.assertIn("impact: patch", result["fragment_content"])

    def test_accepts_a_slash_containing_base_branch_from_the_event_and_api(self) -> None:
        client = FakeClient(
            {
                ("GET", f"repos/{REPOSITORY}/pulls/{PR_NUMBER}"): [
                    pull_request(base_ref="release/1.x")
                ]
            },
            pages=[],
        )
        result = renovate_changelog.plan(
            client,
            REPOSITORY,
            PR_NUMBER,
            HEAD,
            "release/1.x",
            dt.date(2026, 8, 16),
        )
        self.assertTrue(result["required"])

    def test_builds_a_valid_issue_bound_fragment_from_the_update_table(self) -> None:
        client = FakeClient(
            {("GET", f"repos/{REPOSITORY}/pulls/{PR_NUMBER}"): [pull_request()]},
            pages=[],
        )
        result = renovate_changelog.plan(
            client, REPOSITORY, PR_NUMBER, HEAD, "main", dt.date(2026, 8, 16)
        )
        self.assertTrue(result["required"])
        self.assertEqual(
            "NEXT/2026-08-16-issue-263-renovate-dependencies.md",
            result["fragment_path"],
        )
        self.assertIn("dependency ip-address", result["fragment_content"])
        self.assertIn("from `10.4.0` to `10.5.0`", result["fragment_content"])


    def test_builds_a_fixed_fragment_for_a_lock_file_maintenance_pull_request(self) -> None:
        client = FakeClient(
            {
                ("GET", f"repos/{REPOSITORY}/pulls/{PR_NUMBER}"): [
                    pull_request(
                        body=LOCK_FILE_BODY, head_ref="renovate/lock-file-maintenance"
                    )
                ]
            },
            pages=[],
        )
        result = renovate_changelog.plan(
            client, REPOSITORY, PR_NUMBER, HEAD, "main", dt.date(2026, 8, 16)
        )
        self.assertTrue(result["required"])
        self.assertEqual([], result["updates"])
        self.assertEqual(
            "NEXT/2026-08-16-issue-263-renovate-lock-file-maintenance.md",
            result["fragment_path"],
        )
        self.assertIn(
            "title: Refresh all lock file dependencies", result["fragment_content"]
        )
        self.assertIn(
            "Refresh all lock file dependencies with Renovate lock file maintenance.",
            result["fragment_content"],
        )


class GitDataWriteTests(unittest.TestCase):
    def required_plan(self) -> dict[str, Any]:
        client = FakeClient(
            {("GET", f"repos/{REPOSITORY}/pulls/{PR_NUMBER}"): [pull_request()]},
            pages=[],
        )
        return renovate_changelog.plan(
            client, REPOSITORY, PR_NUMBER, HEAD, "main", dt.date(2026, 8, 16)
        )

    def read_client(self) -> FakeClient:
        fragment_path = "NEXT/2026-08-16-issue-263-renovate-dependencies.md"
        return FakeClient(
            {
                ("GET", f"repos/{REPOSITORY}/pulls/{PR_NUMBER}"): [pull_request()],
                (
                    "GET",
                    f"repos/{REPOSITORY}/contents/{fragment_path}?ref={HEAD}",
                ): [renovate_changelog.GitHubApiError("GET", fragment_path, 404)],
            },
            pages=[],
        )

    def write_client(self, ref_sha: str = HEAD) -> FakeClient:
        return FakeClient(
            {
                (
                    "GET",
                    f"repos/{REPOSITORY}/git/ref/heads/renovate%2Fip-address-10.x",
                ): [{"object": {"type": "commit", "sha": ref_sha}}],
                ("GET", f"repos/{REPOSITORY}/git/commits/{HEAD}"): [
                    {"tree": {"sha": TREE}}
                ],
                ("POST", f"repos/{REPOSITORY}/git/blobs"): [{"sha": BLOB}],
                ("POST", f"repos/{REPOSITORY}/git/trees"): [{"sha": NEW_TREE}],
                ("POST", f"repos/{REPOSITORY}/git/commits"): [{"sha": NEW_COMMIT}],
                (
                    "PATCH",
                    f"repos/{REPOSITORY}/git/refs/heads/renovate%2Fip-address-10.x",
                ): [{"object": {"sha": NEW_COMMIT}}],
            },
            pages=[],
        )

    def test_creates_a_blob_tree_commit_and_non_force_exact_head_ref_update(self) -> None:
        read_client = self.read_client()
        write_client = self.write_client()
        result = renovate_changelog.apply(
            read_client, write_client, self.required_plan()
        )
        self.assertIn(NEW_COMMIT, result)
        self.assertTrue(any("/pulls/" in path for _, path, _ in read_client.calls))
        self.assertFalse(any("/pulls/" in path for _, path, _ in write_client.calls))
        mutations = [
            call for call in write_client.calls if call[0] in {"POST", "PATCH"}
        ]
        self.assertEqual(
            ["POST", "POST", "POST", "PATCH"], [method for method, _, _ in mutations]
        )
        self.assertEqual({"sha": NEW_COMMIT, "force": False}, mutations[-1][2])
        self.assertEqual([HEAD], mutations[-2][2]["parents"])

    def test_writes_the_fixed_fragment_for_a_lock_file_maintenance_branch(self) -> None:
        head_ref = "renovate/lock-file-maintenance"
        # Pinned spelling of the same ref as ONE path segment (#1458).
        encoded_head_ref = "renovate%2Flock-file-maintenance"
        fragment_path = "NEXT/2026-08-16-issue-263-renovate-lock-file-maintenance.md"
        document = pull_request(body=LOCK_FILE_BODY, head_ref=head_ref)
        planned = renovate_changelog.plan(
            FakeClient(
                {("GET", f"repos/{REPOSITORY}/pulls/{PR_NUMBER}"): [document]}, pages=[]
            ),
            REPOSITORY,
            PR_NUMBER,
            HEAD,
            "main",
            dt.date(2026, 8, 16),
        )
        read_client = FakeClient(
            {
                ("GET", f"repos/{REPOSITORY}/pulls/{PR_NUMBER}"): [document],
                ("GET", f"repos/{REPOSITORY}/contents/{fragment_path}?ref={HEAD}"): [
                    renovate_changelog.GitHubApiError("GET", fragment_path, 404)
                ],
            },
            pages=[],
        )
        write_client = FakeClient(
            {
                ("GET", f"repos/{REPOSITORY}/git/ref/heads/{encoded_head_ref}"): [
                    {"object": {"type": "commit", "sha": HEAD}}
                ],
                ("GET", f"repos/{REPOSITORY}/git/commits/{HEAD}"): [
                    {"tree": {"sha": TREE}}
                ],
                ("POST", f"repos/{REPOSITORY}/git/blobs"): [{"sha": BLOB}],
                ("POST", f"repos/{REPOSITORY}/git/trees"): [{"sha": NEW_TREE}],
                ("POST", f"repos/{REPOSITORY}/git/commits"): [{"sha": NEW_COMMIT}],
                ("PATCH", f"repos/{REPOSITORY}/git/refs/heads/{encoded_head_ref}"): [
                    {"object": {"sha": NEW_COMMIT}}
                ],
            },
            pages=[],
        )
        self.assertEqual(
            f"created {fragment_path} at {NEW_COMMIT}",
            renovate_changelog.apply(read_client, write_client, planned),
        )

    def test_refuses_to_write_when_the_branch_moved(self) -> None:
        read_client = self.read_client()
        write_client = self.write_client(ref_sha="f" * 40)
        with self.assertRaisesRegex(renovate_changelog.AutomationError, "moved"):
            renovate_changelog.apply(
                read_client, write_client, self.required_plan()
            )
        self.assertFalse(
            any(method in {"POST", "PATCH"} for method, _, _ in write_client.calls)
        )

    def test_refuses_a_canonical_but_tampered_fragment_plan(self) -> None:
        read_client = self.read_client()
        write_client = self.write_client()
        planned = self.required_plan()
        planned["fragment_content"] = planned["fragment_content"].replace(
            "Update `ip-address`", "Update `other-package`"
        )
        with self.assertRaisesRegex(
            renovate_changelog.AutomationError, "admitted Renovate updates"
        ):
            renovate_changelog.apply(read_client, write_client, planned)
        self.assertFalse(
            any(method in {"POST", "PATCH"} for method, _, _ in write_client.calls)
        )


if __name__ == "__main__":
    unittest.main()
