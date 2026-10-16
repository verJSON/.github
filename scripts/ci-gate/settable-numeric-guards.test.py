#!/usr/bin/env python3
"""Numeric guards on admin- or user-settable values must hold in every locale.

Bash `[[ =~ ]]` bracket ranges follow locale collation: under en_US.UTF-8,
`[1-9]` admits U+0663 ARABIC-INDIC DIGIT THREE and `[0-9]` admits fullwidth
digits. A guard on an organization variable or a dispatch input that passes
such a value hands it to a consumer that reads it differently: Python `int()`
accepts it, awk compares it as a string (`"٣" > 0` is true), and jq
rejects it. Every numeric guard on a settable value therefore enumerates its
digits; this test finds those guards structurally rather than by list, so a
new one is held to the same rule.

System-issued values (run, check, job, and artifact IDs read from the event or
the API, and counts the script computes) are out of scope: nobody can set them
to a non-ASCII digit.
"""

from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
# A sibling helper, imported without leaving bytecode a later run could reuse.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ascii_digit_locales  # noqa: E402

SETTABLE_SOURCE = re.compile(r"\b(?:vars|inputs)\.|github\.event\.inputs\.")
BRACKET = re.compile(r"\[\^?[^\]]*\]")
DIGIT_ONLY_BRACKET = re.compile(r"\[\^?(?:[0-9]|[0-9]-[0-9])+\]")
DIGIT_RANGE = re.compile(r"[0-9]-[0-9]")
NON_ASCII_DIGITS = ("٣", "３", "١٠")

# Guards whose subject is derived from a settable value inside the script, so
# no env binding names its source: the AI review budgets come from variables
# or a re-review request, and the package version from the dispatch input.
DERIVED_SETTABLE_GUARDS = {
    (".github/workflows/ai-review-merge.yml", "selected_budget"): 1,
    (".github/workflows/ai-review-merge.yml", "fallback_budget"): 1,
    (".github/workflows/gate-rearm.yml", "review_budget_usd"): 1,
    (".github/workflows/gate-rearm.yml", "fallback_budget_usd"): 1,
    (".github/workflows/node-release.yml", "package_version"): 2,
}
# The current inventory of directly bound settable numeric guards. A change is
# a review point: a new guard must also enumerate its digits.
DIRECT_SETTABLE_GUARD_COUNT = 26

# USD with at most two decimals and at most three integer digits (999.99).
BUDGET_GUARD = r"^(0|[123456789][0123456789]{0,2})(\.[0123456789]{1,2})?$"
BUDGET_SITES = (
    (".github/workflows/ai-review-merge.yml", "selected_budget"),
    (".github/workflows/ai-review-merge.yml", "fallback_budget"),
    (".github/workflows/gate-rearm.yml", "review_budget_usd"),
    (".github/workflows/gate-rearm.yml", "fallback_budget_usd"),
)
VALID_BUDGETS = ("0.5", "1", "12.5", "999.99", "0.01")
INVALID_BUDGETS = (
    "0", "0.00", "01", "-1", ".5", "1.", "1.234", "1e2", " 1", "1 ",
    "1000", "1000.00", str(2**64), "1.٣", "1٣", *NON_ASCII_DIGITS,
)


def guards_in(script, variable):
    return re.findall(
        r'"\$\{?' + re.escape(variable) + r'\}?" =~ (\S+?)(?= \]\])', script
    )


def is_numeric_guard(pattern):
    return DIGIT_ONLY_BRACKET.search(pattern) is not None


def digit_ranges(pattern):
    return [cls for cls in DIGIT_ONLY_BRACKET.findall(pattern) if DIGIT_RANGE.search(cls)]


def workflow_documents():
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        yield path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")
    for path in sorted((ROOT / ".github/actions").glob("*/action.yml")):
        yield path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")


def run_steps(document):
    """Yield (merged env, run script) for every run step, workflow or composite."""
    workflow_env = document.get("env") or {}
    jobs = list((document.get("jobs") or {}).values())
    if "runs" in document:
        jobs.append({"steps": document["runs"].get("steps") or []})
    for job in jobs:
        for step in job.get("steps") or []:
            if isinstance(step.get("run"), str):
                env = {**workflow_env, **(job.get("env") or {}), **(step.get("env") or {})}
                yield env, step["run"]


def direct_settable_numeric_guards():
    """Yield (path, variable, pattern) for numeric guards on vars/inputs-bound env."""
    for path, text in workflow_documents():
        document = yaml.safe_load(text) or {}
        for env, script in run_steps(document):
            for name, value in env.items():
                if not SETTABLE_SOURCE.search(str(value)):
                    continue
                for pattern in guards_in(script, name):
                    if is_numeric_guard(pattern):
                        yield path, name, pattern


def guard_block(text, variable):
    """Return the `if ...; then ... fi` block whose condition guards VARIABLE."""
    lines = text.splitlines()
    starts = [
        index for index, line in enumerate(lines)
        if line.lstrip().startswith("if ") and f'"${variable}" =~' in line
    ]
    if len(starts) != 1:
        raise AssertionError(f"expected one guard block for {variable}, found {len(starts)}")
    start = starts[0]
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next(
        index for index in range(start + 1, len(lines))
        if lines[index].strip() == "fi" and len(lines[index]) - len(lines[index].lstrip()) == indent
    )
    return "\n".join(line[indent:] for line in lines[start:end + 1])


def budget_accepted(block, variable, value, locale):
    """Run the workflow's own guard block on VALUE; True when it lets VALUE through."""
    script = f'check() {{\n{variable}="$1"\n{block}\nreturn 0\n}}\ncheck "$1"\n'
    environment = {
        "PATH": "/usr/bin:/bin",
        **locale,
    }
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c", script, "guard", value],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result.returncode == 0


class SettableNumericGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.locale_build = tempfile.TemporaryDirectory(prefix="settable-guard-locales-")
        cls.locales, cls.missing_locale = ascii_digit_locales.guard_locales(cls.locale_build.name)

    @classmethod
    def tearDownClass(cls):
        cls.locale_build.cleanup()

    def test_the_direct_guard_inventory_is_the_reviewed_one(self):
        sites = list(direct_settable_numeric_guards())
        self.assertEqual(
            len(sites), DIRECT_SETTABLE_GUARD_COUNT,
            "the settable numeric guard inventory changed; review each new guard "
            "and update DIRECT_SETTABLE_GUARD_COUNT:\n"
            + "\n".join(f"{path} {name} {pattern}" for path, name, pattern in sites),
        )

    def test_no_settable_numeric_guard_uses_a_collation_ordered_digit_range(self):
        offenders = [
            f"{path} {name} {pattern}"
            for path, name, pattern in direct_settable_numeric_guards()
            if digit_ranges(pattern)
        ]
        for (path, variable), expected in DERIVED_SETTABLE_GUARDS.items():
            patterns = guards_in((ROOT / path).read_text(encoding="utf-8"), variable)
            self.assertEqual(len(patterns), expected, f"{path} {variable} guard count")
            offenders += [f"{path} {variable} {p}" for p in patterns if digit_ranges(p)]
        self.assertEqual(offenders, [])

    def test_every_settable_digit_class_rejects_non_ascii_digits_in_every_locale(self):
        classes = {cls for _path, _name, pattern in direct_settable_numeric_guards()
                   for cls in DIGIT_ONLY_BRACKET.findall(pattern)}
        for (path, variable) in DERIVED_SETTABLE_GUARDS:
            for pattern in guards_in((ROOT / path).read_text(encoding="utf-8"), variable):
                classes.update(DIGIT_ONLY_BRACKET.findall(pattern))
        self.assertTrue(classes)
        for label, locale in self.locales.items():
            for digit_class in sorted(classes):
                for value in NON_ASCII_DIGITS[:2]:
                    with self.subTest(locale=label, digit_class=digit_class, value=value):
                        result = subprocess.run(
                            ["/bin/bash", "--noprofile", "--norc", "-c",
                             f'[[ "$1" =~ ^{digit_class}$ ]]', "probe", value],
                            env={"PATH": "/usr/bin:/bin", **locale},
                            capture_output=True,
                            check=False,
                            timeout=10,
                        )
                        self.assertNotEqual(result.returncode, 0)
        if self.missing_locale:
            self.skipTest(self.missing_locale)

    def test_every_ai_review_budget_guard_is_the_canonical_capped_guard(self):
        for path, variable in BUDGET_SITES:
            with self.subTest(site=f"{path} {variable}"):
                patterns = guards_in((ROOT / path).read_text(encoding="utf-8"), variable)
                self.assertEqual(patterns, [BUDGET_GUARD])

    def test_ai_review_budget_guards_accept_only_capped_ascii_amounts_in_every_locale(self):
        for path, variable in BUDGET_SITES:
            block = guard_block((ROOT / path).read_text(encoding="utf-8"), variable)
            for label, locale in self.locales.items():
                for value in VALID_BUDGETS:
                    with self.subTest(site=f"{path} {variable}", locale=label, value=value):
                        self.assertTrue(budget_accepted(block, variable, value, locale))
                for value in INVALID_BUDGETS:
                    with self.subTest(site=f"{path} {variable}", locale=label, value=value):
                        self.assertFalse(budget_accepted(block, variable, value, locale))
        if self.missing_locale:
            self.skipTest(self.missing_locale)

    def test_the_release_generator_refuses_a_non_ascii_node_version_in_every_locale(self):
        # Generation-time operator input, embedded into every adopter's caller.
        contract_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        for label, locale in self.locales.items():
            for value in ("\u0662\u0664", "2\u0664", "\uff12\uff14"):
                with self.subTest(locale=label, value=value):
                    result = subprocess.run(
                        ["bash", "scripts/gen-changelog-caller.sh", "release-node",
                         contract_sha, "--node-version", value],
                        cwd=ROOT,
                        env={"PATH": "/usr/bin:/bin", **locale},
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=60,
                    )
                    self.assertEqual(result.returncode, 2, result.stdout[-400:] + result.stderr)
                    self.assertIn("node version must be a numeric", result.stderr)
        if self.missing_locale:
            self.skipTest(self.missing_locale)


if __name__ == "__main__":
    unittest.main()
