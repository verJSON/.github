#!/usr/bin/env python3
"""Every shell character-class guard holds in every locale.

Bash `[[ =~ ]]`, `[[ == ]]`/`case` globs, and `grep` evaluate bracket ranges
and POSIX classes through the locale. Under en_US.UTF-8 `[a-z]` admits `é` and
fullwidth `ａ`, `[0-9]` admits `٣`, and a guard on an organization variable,
a dispatch input, or a pull-request field then hands that value to a consumer
that reads it differently. The fix is one rule, applied everywhere: a bracket
expression lists its ASCII characters literally (`[abcdefghijklmnopqrstuvwxyz]`),
never as a range or a POSIX class other than whitespace.

`settable_value_guards.py` finds the guards in every workflow, composite
action, generated caller, and generator script. It is subject-agnostic, so a
guard on a shell copy, a GITHUB_OUTPUT or GITHUB_ENV hand-off, or an API-read
value is held to the rule without tracing where the value came from. A guard
shape it cannot read is reported as unclassifiable, and an unclassifiable guard
fails unless it is reviewed below.
"""

from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
# Sibling helpers, imported without leaving bytecode a later run could reuse.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ascii_digit_locales  # noqa: E402
import settable_value_guards  # noqa: E402

NON_ASCII_DIGITS = ("\u0663", "\uff13", "\u0661\u0660")
NON_ASCII_PROBES = ("\u00e9", "\uff41", "\uff21", "\u0663", "\uff13", "\u00a0")

# The reviewed inventory: every guard the scanner finds, in the repository and
# in the generator's HEAD output. A change is a review point. The public
# verjson-ci adapter adds one input guard to the prior reviewed inventory.
GUARD_COUNT = 514
# Shapes the scanner cannot judge, each with the reason it is safe.
# Each unreadable guard reviewed by its exact text at its site: an edit to the
# pattern, including a range added around a reviewed expansion, changes the
# key and fails, and an entry that no longer matches a guard fails too.
REASON_C = "CONTRACT_REF is the contract SHA the generator validated as 40 lowercase hex and embedded. "
REASON_A = "accepted is generated_set_check argument 5; every call's literal argument is itself scanned as an argument guard. "
REASON_P = "consumer_pattern is stamp_before argument 2; every call's literal argument is itself scanned as an argument guard. "
REASON_W = "reusable_workflow is one of two literal workflow file names assigned just above. "
REASON_K = "key is a manifest key the same function first requires to match a spelled-out identifier class. "
REASON_L = "the two legacy token names are fixed literals, split only to keep them out of secret scanners. "
REASON_GEN_REF = "ref is substituted at generation; the generator first requires it to be 40 lowercase hex. "
REASON_GEN_SHA = "the value is substituted at generation from sha256sum output: 64 lowercase hex. "
REASON_GEN_CLEANUP = "the value is substituted at generation from a jq boolean check: true or false. "
REASON_H = "hosted_image is assigned two fixed literals, the standard image shape and the same shape with a closed core-count suffix; no variable data reaches it (ADR 0238). "
CT = "<gen-changelog-caller contract-test>"
REVIEWED_UNCLASSIFIABLE = {
    '.github/workflows/node-ci-protected.yml regex "$SECRETLESS_BUILD_LANE" ^\\[${hosted_image}\\]$':
        REASON_H,
    '.github/workflows/node-ci-protected.yml regex "$SECRETLESS_BUILD_LANE" ^${hosted_image}$':
        REASON_H,
    '.github/workflows/node-ci.yml regex "$SECRETLESS_BUILD_LANE" ^\\[${hosted_image}\\]$':
        REASON_H,
    '.github/workflows/node-ci.yml regex "$SECRETLESS_BUILD_LANE" ^${hosted_image}$':
        REASON_H,
    "scripts/gen-container-candidate.sh <heredoc> grep <grep> '# Contract: ⟦gen:ref⟧'":
        REASON_GEN_REF,
    "scripts/gen-container-candidate.sh <heredoc> grep <grep> '# Contract: ⟦gen:ref⟧'":
        REASON_GEN_REF,
    "scripts/gen-container-candidate.sh <heredoc> grep <grep> '# Contract: ⟦gen:ref⟧'":
        REASON_GEN_REF,
    "scripts/gen-container-candidate.sh <heredoc> grep <grep> 'uses: Verjson/.github/.github/workflows/container-candidate.yml@⟦gen:ref⟧'":
        REASON_GEN_REF,
    "scripts/gen-container-candidate.sh <heredoc> grep <grep> 'uses: Verjson/.github/.github/workflows/container-candidate-publish.yml@⟦gen:ref⟧'":
        REASON_GEN_REF,
    "scripts/gen-container-candidate.sh <heredoc> grep <grep> 'contract-ref: ⟦gen:ref⟧'":
        REASON_GEN_REF,
    "scripts/gen-container-candidate.sh <heredoc> grep <grep> 'cleanup-unused-docker-images: ⟦gen:cleanup_unused_docker_images⟧'":
        REASON_GEN_CLEANUP,
    "scripts/gen-container-candidate.sh <heredoc> grep <grep> 'acquisition-sha256: ⟦gen:acquisition_sha256⟧'":
        REASON_GEN_SHA,
    "scripts/gen-container-candidate.sh <heredoc> grep <grep> 'transfer-sha256: ⟦gen:transfer_sha256⟧'":
        REASON_GEN_SHA,
    "scripts/gen-container-candidate.sh <heredoc> grep <grep> 'disk-sha256: ⟦gen:disk_sha256⟧'":
        REASON_GEN_SHA,
    "scripts/gen-container-candidate.sh <heredoc> grep <grep> 'retry-sha256: ⟦gen:retry_sha256⟧'":
        REASON_GEN_SHA,
    "scripts/gen-container-candidate.sh <heredoc> grep <grep> 'provenance-sha256: ⟦gen:provenance_sha256⟧'":
        REASON_GEN_SHA,
    '.github/workflows/node-ci-protected.yml grep <grep> "^${key}="':
        REASON_K,
    '.github/workflows/node-ci.yml grep <grep> "^${key}="':
        REASON_K,
    '<gen-changelog-caller contract-test> grep <grep> "$accepted"':
        REASON_A,
    '<gen-changelog-caller contract-test> grep <grep> "$consumer_pattern"':
        REASON_P,
    '<gen-changelog-caller contract-test> grep <grep> "CONTRACT_REF=\\"$CONTRACT_REF\\""':
        REASON_C,
    '<gen-changelog-caller contract-test> grep <grep> "^      contract_ref: $CONTRACT_REF$"':
        REASON_C,
    '<gen-changelog-caller contract-test> grep <grep> "^    uses: verJSON/\\\\.github/\\\\.github/workflows/generated-artifacts\\\\.yml@$CONTRACT_REF$"':
        REASON_C,
    '<gen-changelog-caller contract-test> grep <grep> "^ +contract_ref: $CONTRACT_REF$"':
        REASON_C,
    '<gen-changelog-caller contract-test> grep <grep> "^ +uses: verJSON/\\.github/\\.github/workflows/$reusable_workflow@$CONTRACT_REF$"':
        REASON_C + REASON_W,
    '<gen-changelog-caller contract-test> grep <grep> "^# Generated by verJSON/\\.github scripts/gen-changelog-caller\\.sh release-propose $CONTRACT_REF --autonomy (propose|dispatch)$"':
        REASON_C,
    '<gen-changelog-caller contract-test> grep <grep> "^[[:space:]]+contract_ref:[[:space:]]*${CONTRACT_REF}[[:space:]]*$"':
        REASON_C,
    '<gen-changelog-caller contract-test> grep <grep> "changelog-release.yml@$CONTRACT_REF"':
        REASON_C,
    '<gen-changelog-caller contract-test> grep <grep> "gen-changelog-caller.sh release-artifact $CONTRACT_REF"':
        REASON_C,
    '<gen-changelog-caller contract-test> grep <grep> "gen-changelog-caller.sh release-node $CONTRACT_REF"':
        REASON_C,
    '<gen-changelog-caller contract-test> grep <grep> "gen-changelog-caller.sh release-snapshot $CONTRACT_REF"':
        REASON_C,
    'scripts/gen-container-release.sh <heredoc> grep <grep> "$legacy_release_token|$legacy_org_release_token"':
        REASON_L,
}


# Files generated at an older pinned contract. They carry the guards that
# contract emitted, and regenerating them is the pin bump's job, not this one's.
GENERATED_AT_PIN = {
    ".github/workflows/release.yml": (
        "this repository's own release caller, generated by gen-changelog-caller.sh "
        "at its pinned contract; the generator at HEAD is held to the rule below"
    ),
}
GENERATOR_OUTPUTS = {
    "release-node": [],
    "release-artifact": ["--build-runner", "ubuntu-24.04"],
    "release-snapshot": [],
    "release-propose": ["--autonomy", "propose"],
    "workflow": [],
    "generated-artifacts": [],
    "pr-gate": [],
}

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
    "1000", "1000.00", str(2**64), "1.\u0663", "1\u0663", *NON_ASCII_DIGITS,
)
# Letter-class guards on dispatch inputs and organization variables named in
# review, with the values each must accept and refuse in every locale.
LETTER_SITES = (
    (".github/workflows/node-release.yml", "SCOPE", ("@verjson", "@acme-co"),
     ("@v\u00e9rjson", "@\uff56erjson", "@verjson\u0663", "@Verjson")),
    (".github/workflows/node-release.yml", "PREFIX", ("v", "cli-v", "schema-v"),
     ("cl\u00e9-v", "\uff43li-v", "CLI-v")),
    (".github/workflows/gate-rearm.yml", "APP_SLUG", ("verjson-ai-review",),
     ("v\u00e9rjson", "\uff56erjson", "Verjson")),
    (".github/workflows/ai-promotion-retry.yml", "EXPECTED_APP_SLUG", ("verjson-ai-review",),
     ("v\u00e9rjson", "\uff56erjson")),
    (".github/workflows/renovate-compatibility-canary.yml", "PACKAGE",
     ("@verjson/core", "left-pad"), ("@verjson/c\u0153ur", "\uff4ceft-pad", "@Verjson/core")),
)


def guards_in(script, variable):
    return re.findall(
        r'"\$\{?' + re.escape(variable) + r'\}?" =~ (\S+?)(?= \]\])', script
    )


def generated_outputs(contract_sha):
    """Yield (label, text) for each workflow the generator emits at HEAD."""
    for mode, arguments in GENERATOR_OUTPUTS.items():
        text = subprocess.check_output(
            ["bash", "scripts/gen-changelog-caller.sh", mode, contract_sha, *arguments],
            cwd=ROOT, text=True,
        )
        yield f"<gen-changelog-caller {mode}>", text


def full_inventory(contract_sha):
    guards = list(settable_value_guards.scan_repository(ROOT))
    for label, text in generated_outputs(contract_sha):
        guards.extend(settable_value_guards.scan_workflow_text(label, text))
    contract_test = subprocess.check_output(
        ["bash", "scripts/gen-changelog-caller.sh", "contract-test", contract_sha],
        cwd=ROOT, text=True,
    )
    guards.extend(settable_value_guards.guards_in_script(
        "<gen-changelog-caller contract-test>",
        settable_value_guards.shell_outside_heredocs(contract_test),
    ))
    return guards


def bash_matches(pattern, value, locale, mode="regex"):
    script = (
        're=$2; [[ $1 =~ $re ]]' if mode == "regex"
        else 'case $1 in $2) exit 0 ;; esac; exit 1'
    )
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c", script, "probe", value, pattern],
        env={"PATH": "/usr/bin:/bin", **locale},
        capture_output=True, check=False, timeout=10,
    )
    return result.returncode == 0


def awk_program(block, variable):
    programs = re.findall(r"awk -v b=\"\$" + re.escape(variable) + r"\" '([^']*)'", block)
    if len(programs) != 1:
        raise AssertionError(f"expected one awk check for {variable}, found {len(programs)}")
    return programs[0]


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


class SettableValueGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.locale_build = tempfile.TemporaryDirectory(prefix="settable-guard-locales-")
        cls.locales, cls.missing_locale = ascii_digit_locales.guard_locales(cls.locale_build.name)
        cls.contract_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        cls.inventory = full_inventory(cls.contract_sha)

    @classmethod
    def tearDownClass(cls):
        cls.locale_build.cleanup()

    def skip_if_hazard_locale_missing(self):
        if self.missing_locale:
            self.skipTest(self.missing_locale)

    def test_the_scanner_reads_every_guard_shape(self):
        workflow = yaml.safe_dump({
            "on": {"workflow_dispatch": {"inputs": {"name": {"type": "string"}}}},
            "jobs": {"j": {"runs-on": "ubuntu-24.04", "steps": [
                {"id": "one", "env": {"NAME": "${{ inputs.name }}"},
                 "run": 'echo "copy=$NAME" >> "$GITHUB_OUTPUT"'},
                {"env": {"FROM_OUTPUT": "${{ steps.one.outputs.copy }}"}, "run": "\n".join((
                    '[[ "$FROM_OUTPUT" =~ ^[a-z]+$ ]]',
                    "[[ $FROM_OUTPUT =~ ^[0-9]+$ ]]",
                    'copy="$FROM_OUTPUT"',
                    "[[ $copy == [A-Z]* ]]",
                    'case "$copy" in',
                    "  [a-z]*) ;;",
                    "  *) exit 1 ;;",
                    "esac",
                    "grep -qE '^[a-z0-9]+$' <<<\"$copy\"",
                    "printf '%s' \"$copy\" | grep -E '[[:alnum:]]'",
                    "held='^[0-9]+$'",
                    "[[ $copy =~ $held ]]",
                    "[[ $copy =~ $unknown ]]",
                    "grep -qF '[a-z]' <<<\"$copy\"",
                    "[[ $copy =~ ^[abc][[:space:]]*$ ]]",
                    "part='x[0123456789]{2}'",
                    "[[ $copy =~ ^\\[${part}\\]$ ]]",
                    "loose='x[0-9]{2}'",
                    "[[ $copy =~ ^${loose}$ ]]",
                    # Multi-line tests: && / || continuations and backslashes.
                    '[[ "$copy" =~ ^[a-z0-9-]+$ &&',
                    '   "$copy" == x ]]',
                    '[[ "$copy" =~ ^[abc]+$ ||',
                    '   "$copy" =~ ^[0-9]+$ ]]',
                    '[[ "$copy" =~ ^[abc]+$ \\',
                    '   && "$copy" =~ ^[A-Z]+$ ]]',
                    # A held fragment that is appended to or reassigned is not a literal.
                    "grown='^[abc]'",
                    "grown+='[0-9]'",
                    "[[ $copy =~ $grown ]]",
                    "moved='^[abc]$'",
                    'moved="$copy"',
                    "[[ $copy =~ $moved ]]",
                    # grep spellings that still take a pattern.
                    "grep -E -- '^[a-z]+$' <<<\"$copy\"",
                    "grep -m1 -E '[0-9]' <<<\"$copy\"",
                    "grep --regexp='[a-z]' <<<\"$copy\"",
                    "grep -e '[abc]' -e '[0-9]' <<<\"$copy\"",
                    # Re-review: double-quoted regex text is still regex text.
                    'grep -qE "^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$" <<<"$copy"',
                    'grep -qE "^[abc]+$" <<<"$copy"',
                    'grep -c "^${copy}=[a-z]" file',
                    'grep -c "^${copy}=" file',
                    "[[ $copy =~ ^\"[a-z]\"$ ]]",
                    # grep -f reads its patterns from a file this scanner cannot see.
                    "grep -f patterns.txt <<<\"$copy\"",
                    "grep -qE --file=patterns.txt <<<\"$copy\"",
                    # ANSI-C quoting hides a bracket behind an escape.
                    "grep -qE $'^\\x5bA-Za-z0-9._-\\x5d+$' <<<\"$copy\"",
                    # A helper that greps a pattern its callers pass in.
                    "matches() {",
                    '  local value="$1" accepted="${2:-}"',
                    '  printf %s "$value" | grep -qxE "$accepted"',
                    "}",
                    "matches \"$copy\" 'pr-gate|renderer'",
                    "matches \"$copy\" \\",
                    "  '[a-z-]+'",
                    'matches "$copy" "$copy"',
                ))},
            ]}},
        })
        found = [
            (guard.kind, guard.verdict)
            for guard in settable_value_guards.scan_workflow_text("<fixture>", workflow)
        ]
        self.assertEqual(found, [
            ("regex", "collation"),
            ("regex", "collation"),
            ("glob", "collation"),
            ("regex", "collation"),
            ("regex", "unclassifiable"),
            ("regex", "canonical"),
            ("regex", "canonical"),
            ("regex", "collation"),
            ("regex", "collation"),
            ("regex", "canonical"),
            ("regex", "collation"),
            ("regex", "canonical"),
            ("regex", "collation"),
            ("regex", "unclassifiable"),
            ("regex", "unclassifiable"),
            ("regex", "collation"),
            ("case", "collation"),
            ("grep", "collation"),
            ("grep", "collation"),
            ("grep", "collation"),
            ("grep", "collation"),
            ("grep", "collation"),
            ("grep", "canonical"),
            ("grep", "collation"),
            ("grep", "collation"),
            ("grep", "canonical"),
            ("grep", "collation"),
            ("grep", "unclassifiable"),
            ("grep", "unclassifiable"),
            ("grep", "unclassifiable"),
            ("grep", "unclassifiable"),
            ("grep", "unclassifiable"),
            ("argument", "canonical"),
            ("argument", "collation"),
            ("argument", "unclassifiable"),
        ])

    def test_generator_heredocs_are_scanned_as_the_text_they_emit(self):
        generator = "\n".join((
            "#!/usr/bin/env bash",
            "cat <<EOF",
            "      - run: |",
            '          [[ "\\$REF" =~ ^[0-9a-f]{40}\\$ ]]',
            "          [[ \"\\$NAME\" =~ ^[abc]+\\$ ]]",
            "          [[ \"\\$NAME\" =~ ^${pattern}\\$ ]]",
            "EOF",
            "cat <<'PY'",
            "case \"$x\" in [a-z]*) ;; esac",
            "PY",
        ))
        found = [
            (guard.pattern, guard.verdict)
            for guard in settable_value_guards.scan_generator_heredocs("<g>", generator)
        ]
        self.assertEqual(found, [
            ("^[0-9a-f]{40}$", "collation"),
            ("^[abc]+$", "canonical"),
            ("^\u27e6gen:pattern\u27e7$", "unclassifiable"),
            ("[a-z]*", "collation"),
        ])

    def scan_script(self, *lines):
        return [
            (guard.kind, guard.verdict, guard.pattern)
            for guard in settable_value_guards.guards_in_script("<fixture>", "\n".join(lines))
        ]

    def test_a_wrapper_forwarding_all_arguments_makes_the_inner_call_unclassifiable(self):
        found = self.scan_script(
            "matches() {",
            '  local value="$1" accepted="${2:-}"',
            '  printf %s "$value" | grep -qxE "$accepted"',
            "}",
            'wrap() { matches "$@"; }',
            "wrap \"$copy\" '[a-z-]+'",
        )
        self.assertIn(("argument", "unclassifiable", '"$@"'), found)
        self.assertNotIn("canonical", {verdict for kind, verdict, _ in found if kind == "argument"})

    def test_a_one_line_function_with_a_bare_positional_pattern_checks_its_callers(self):
        found = self.scan_script(
            "repo_ok() { [[ \"$TARGET_REPO\" =~ $1 ]]; }; repo_ok '^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$'",
        )
        self.assertEqual(found, [
            ("regex", "unclassifiable", "$1"),
            ("argument", "collation", "'^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$'"),
        ])

    def test_every_parameter_expansion_is_an_expansion(self):
        for expansion in ("$1", "${1}", "${10}", "$@", "$*", "${@:2}", "$#", "$-", "$?"):
            with self.subTest(expansion=expansion):
                self.assertEqual(
                    settable_value_guards.judge_regex(f'"^{expansion}$"')[0], "unclassifiable"
                )

    def test_the_function_keyword_form_is_recognized(self):
        found = self.scan_script(
            "function fn_form {",
            '  [[ "$copy" =~ $1 ]]',
            "}",
            "fn_form '^[abc]+$'",
            "function fn_paren() { [[ \"$copy\" =~ $1 ]]; }",
            "fn_paren '^[a-z]+$'",
        )
        self.assertEqual(found, [
            ("regex", "unclassifiable", "$1"),
            ("regex", "unclassifiable", "$1"),
            ("argument", "canonical", "'^[abc]+$'"),
            ("argument", "collation", "'^[a-z]+$'"),
        ])

    def test_shift_or_set_in_a_pattern_function_makes_its_pattern_unreviewable(self):
        for mutation in ("shift", 'set -- "$copy"', "set -- x y"):
            with self.subTest(mutation=mutation):
                found = self.scan_script(
                    "check() {",
                    f"  {mutation}",
                    '  [[ "$copy" =~ $1 ]]',
                    "}",
                    "check x '^[abc]$'",
                )
                self.assertEqual(found, [("regex", "unreviewable", "$1")])

    def test_a_parameter_pattern_outside_any_recognized_function_is_unreviewable(self):
        self.assertEqual(
            self.scan_script('[[ "$copy" =~ $1 ]]'),
            [("regex", "unreviewable", "$1")],
        )

    def test_a_missing_hazard_locale_fails_under_ci_instead_of_skipping(self):
        never = lambda environment: False  # noqa: E731 - the hazard never reproduces
        with tempfile.TemporaryDirectory(prefix="ci-locale-") as temporary:
            locales, missing = ascii_digit_locales.guard_locales(
                temporary, environ={}, probe=never
            )
            self.assertEqual(set(locales), {"C.UTF-8"})
            self.assertIsNotNone(missing)
            with self.assertRaisesRegex(AssertionError, "en_US.UTF-8"):
                ascii_digit_locales.guard_locales(
                    temporary, environ={"CI": "true"}, probe=never
                )

    def test_the_guard_inventory_is_the_reviewed_one(self):
        self.assertEqual(
            len(self.inventory), GUARD_COUNT,
            "the shell guard inventory changed; review each new guard and update GUARD_COUNT",
        )

    def test_no_guard_uses_a_collation_ordered_class(self):
        offenders = [
            guard.key for guard in self.inventory
            if guard.verdict == "collation" and guard.source not in GENERATED_AT_PIN
        ]
        self.assertEqual(offenders, [])

    def test_no_guard_is_unreviewable(self):
        self.assertEqual(
            [guard.key for guard in self.inventory if guard.verdict == "unreviewable"], []
        )

    def test_every_unclassifiable_guard_is_reviewed_by_its_exact_text(self):
        unclassifiable = {
            guard.key for guard in self.inventory if guard.verdict == "unclassifiable"
        }
        self.assertEqual(unclassifiable - set(REVIEWED_UNCLASSIFIABLE), set(), "unreviewed")
        self.assertEqual(set(REVIEWED_UNCLASSIFIABLE) - unclassifiable, set(), "stale review")

    def test_every_guard_class_decides_the_same_way_in_every_locale(self):
        expressions = set()
        for guard in self.inventory:
            if guard.verdict != "canonical" or guard.source in GENERATED_AT_PIN:
                continue
            literal = (
                guard.resolved if guard.resolved is not None
                else settable_value_guards.strip_quoted(guard.pattern) or ""
            )
            expressions.update(settable_value_guards.bracket_expressions(literal) or [])
        self.assertTrue(expressions)
        baseline = self.locales["C.UTF-8"]
        for expression in sorted(expressions):
            mode = "glob" if expression.startswith("[!") else "regex"
            pattern = expression if mode == "glob" else "^" + expression.replace("\\$", "$") + "$"
            for label, locale in self.locales.items():
                for probe in NON_ASCII_PROBES:
                    with self.subTest(expression=expression, locale=label, probe=probe):
                        self.assertEqual(
                            bash_matches(pattern, probe, locale, mode),
                            bash_matches(pattern, probe, baseline, mode),
                        )
        self.skip_if_hazard_locale_missing()

    def test_letter_guards_on_settable_values_hold_in_every_locale(self):
        for path, subject, accepted, refused in LETTER_SITES:
            text = (ROOT / path).read_text(encoding="utf-8")
            patterns = {
                guard.pattern for guard in settable_value_guards.scan_workflow_text(path, text)
                if guard.kind == "regex" and guard.subject.strip('"').lstrip("$").strip("{}") == subject
            }
            self.assertTrue(patterns, f"no guard on {subject} in {path}")
            for pattern in patterns:
                for label, locale in self.locales.items():
                    for value in accepted:
                        with self.subTest(site=f"{path} {subject}", locale=label, value=value):
                            self.assertTrue(bash_matches(pattern, value, locale))
                    for value in refused:
                        with self.subTest(site=f"{path} {subject}", locale=label, value=value):
                            self.assertFalse(bash_matches(pattern, value, locale))
        self.skip_if_hazard_locale_missing()

    def test_the_budget_awk_check_compares_numerically(self):
        # The regex already refuses non-numbers, so this runs the awk check on
        # its own: a string comparison would accept "abc" and "\u0663".
        for path, variable in BUDGET_SITES:
            program = awk_program(guard_block((ROOT / path).read_text(encoding="utf-8"), variable), variable)
            for label, locale in self.locales.items():
                for value, expected in (
                    ("abc", False), ("\u0663", False), ("0.00", False), ("-1", False),
                    ("", False), ("0.01", True), ("1", True), ("999.99", True),
                ):
                    with self.subTest(site=f"{path} {variable}", locale=label, value=value):
                        result = subprocess.run(
                            ["awk", "-v", f"b={value}", program],
                            env={"PATH": "/usr/bin:/bin", **locale},
                            capture_output=True, check=False, timeout=10,
                        )
                        self.assertEqual(result.returncode == 0, expected)
        self.skip_if_hazard_locale_missing()

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
