#!/usr/bin/env python3

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("changelog.py")
SPEC = importlib.util.spec_from_file_location("changelog", MODULE_PATH)
assert SPEC and SPEC.loader
changelog = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = changelog
SPEC.loader.exec_module(changelog)


def run(repo: Path, *args: str) -> str:
    return subprocess.run(
        args,
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def fragment(
    root: Path,
    name: str,
    *,
    date: str = "2026-07-30",
    issue: str | None = "249",
    title: str = "Contract",
    identity_id: str | None = None,
    refs: str | None = None,
    summary: str | None = None,
    component: str | None = None,
    impact: str | None = None,
    body: str = "Body.",
) -> Path:
    path = root / "NEXT" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"date: {date}"]
    if identity_id is not None:
        lines.append(f"id: {identity_id}")
    elif issue is not None:
        lines.append(f"issue: {issue}")
    if refs is not None:
        lines.append(f"refs: {refs}")
    if summary is not None:
        lines.append(f"summary: {summary}")
    if component is not None:
        lines.append(f"component: {component}")
    if impact is not None:
        lines.append(f"impact: {impact}")
    lines.append(f"title: {title}")
    front = "\n".join(lines)
    path.write_text(f"---\n{front}\n---\n\n{body}\n", encoding="utf-8")
    return path


# A real lead is wrapped prose, not one line — the median across the release
# that prompted #426 was six lines. A split on the newline rather than the
# blank line would keep only "The lead paragraph," and pass a one-line fixture.
DIARY = """The lead paragraph,
which is what a release note needs.

## Why

A long argument nobody reading release notes asked for.

- a list
- another item
"""


class ChangelogContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def init_git(self) -> None:
        run(self.root, "git", "init", "-q")
        run(self.root, "git", "config", "user.name", "Test")
        run(self.root, "git", "config", "user.email", "test@example.com")

    def commit_all(self, message: str) -> None:
        run(self.root, "git", "add", ".")
        run(self.root, "git", "commit", "-qm", message)

    # --- refs: linkage separated from identity (#316) --------------------------
    # Identity must stay unique, so only one entry per issue can carry `issue:`.
    # Before `refs`, every other entry for that issue silently lost its release
    # back-link, because only issue-form identities render `#n`.

    def test_refs_render_back_links_for_entries_that_do_not_own_the_issue(self) -> None:
        fragment(
            self.root,
            "2026-07-30-issue-20260730T090000Z-transport.md",
            identity_id="20260730T090000Z",
            refs="13",
            title="Gateway transport",
        )
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn("_Date: 2026-07-30; id:20260730T090000Z; refs #13_", rendered)

    def test_refs_accepts_several_issues_and_normalizes_hashes(self) -> None:
        fragment(
            self.root,
            "2026-07-30-issue-249-many.md",
            issue="249",
            refs="#13, 14",
            title="Several",
        )
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn("_Date: 2026-07-30; issue #249; refs #13, #14_", rendered)

    def test_fragment_without_refs_renders_exactly_as_before(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-plain.md", issue="249")
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn("_Date: 2026-07-30; issue #249_", rendered)
        self.assertNotIn("refs", rendered)

    def test_issue_like_identity_explains_the_id_key(self) -> None:
        fragment(
            self.root,
            "2026-07-30-issue-20260801T184500Z-identity.md",
            issue="20260801T184500Z",
        )
        with self.assertRaisesRegex(changelog.ChangelogError, "move it to the id key"):
            list(changelog.fragments(self.root))

    def test_hex_identity_explains_the_id_key(self) -> None:
        fragment(
            self.root,
            "2026-07-30-issue-abc123-identity.md",
            issue="abc123",
        )
        with self.assertRaisesRegex(changelog.ChangelogError, "move it to the id key"):
            list(changelog.fragments(self.root))

    # YAML requires a quoted scalar wherever a value contains `: `, so every
    # `Fix: …` title is quoted by anyone who checks their fragment parses. The
    # line-wise parser kept the quotes, so those fragments — the correct ones —
    # rendered `## 'Fix: …'` into an immutable released snapshot (#420).
    def test_single_quoted_title_renders_without_its_quotes(self) -> None:
        fragment(
            self.root, "2026-07-30-issue-249-single.md", title="'Fix: the thing'"
        )
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn("## Fix: the thing\n", rendered)
        self.assertNotIn("'Fix", rendered)

    def test_double_quoted_title_renders_without_its_quotes(self) -> None:
        fragment(
            self.root, "2026-07-30-issue-249-double.md", title='"Fix: the thing"'
        )
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn("## Fix: the thing\n", rendered)
        self.assertNotIn('"Fix', rendered)

    def test_unquoted_title_containing_a_colon_is_unchanged(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-bare.md", title="Fix: the thing")
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn("## Fix: the thing\n", rendered)

    def test_doubled_quote_inside_a_single_quoted_title_becomes_one_quote(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-esc.md", title="'It''s fixed'")
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn("## It's fixed\n", rendered)

    def test_escaped_quote_inside_a_double_quoted_title_survives(self) -> None:
        fragment(
            self.root, "2026-07-30-issue-249-dq.md", title='"Say \\"go\\" once"'
        )
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn('## Say "go" once\n', rendered)

    # A title that merely opens and closes with a quote character is not a
    # quoted scalar. Stripping by position rather than by structure would eat
    # real characters and silently corrupt the heading.
    # The parser leaves this shape alone rather than corrupting it, and
    # `validate` then refuses it: a snapshot is immutable, so ambiguous quoting
    # has to fail while the fragment is still editable (#425).
    def test_title_that_only_begins_and_ends_with_quotes_is_rejected(self) -> None:
        path = fragment(self.root, "2026-07-30-issue-249-pair.md", title='"a" and "b"')
        with self.assertRaises(changelog.ChangelogError) as caught:
            changelog.load_canonical(path)
        self.assertIn("not a single quoted scalar", str(caught.exception))

    def test_lone_quote_inside_a_single_quoted_title_is_rejected(self) -> None:
        path = fragment(self.root, "2026-07-30-issue-249-lone.md", title="'a' or 'b'")
        with self.assertRaises(changelog.ChangelogError) as caught:
            changelog.load_canonical(path)
        self.assertIn("not a single quoted scalar", str(caught.exception))

    def test_the_rejection_names_both_ways_out(self) -> None:
        # The fix is not obvious from the symptom, so the error has to carry it.
        path = fragment(self.root, "2026-07-30-issue-249-how.md", title="'a' or 'b'")
        with self.assertRaises(changelog.ChangelogError) as caught:
            changelog.load_canonical(path)
        message = str(caught.exception)
        self.assertIn("remove the outer pair", message)
        self.assertIn("escape the interior", message)

    # The rejection must not swallow titles that merely contain quotes, nor the
    # correctly-quoted ones the parser already resolves — those are the spelling
    # the contract wants, and failing them would re-invert the incentive #420
    # fixed.
    def test_a_title_with_interior_quotes_is_accepted(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-mid.md", title='Say "go" once')
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn('## Say "go" once\n', rendered)

    def test_a_title_that_only_starts_with_a_quote_is_accepted(self) -> None:
        # Both ends have to match for this to be quoting at all. Checking only
        # the opening character would reject ordinary prose that happens to
        # begin with a quoted word.
        fragment(self.root, "2026-07-30-issue-249-open.md", title='"go" is the fix')
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn('## "go" is the fix\n', rendered)

    def test_a_title_that_only_ends_with_a_quote_is_accepted(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-close.md", title='the fix is "go"')
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn('## the fix is "go"\n', rendered)

    def test_a_correctly_quoted_title_is_still_accepted(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-ok.md", title="'Fix: the thing'")
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn("## Fix: the thing\n", rendered)

    def test_a_title_that_resolves_to_quotes_is_accepted(self) -> None:
        # `'''a'''` is a valid scalar denoting `'a'`. The author said what they
        # meant, so the result keeping its quotes is not ambiguity.
        fragment(self.root, "2026-07-30-issue-249-nest.md", title="'''a'''")
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn("## 'a'\n", rendered)

    # Quoting is legal on every scalar, not just the one that exposed the bug.
    # A quoted identity used to reach `int()` with its quotes still attached.
    def test_quoted_issue_number_still_identifies_the_entry(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-qid.md", issue="'249'")
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn("_Date: 2026-07-30; issue #249_", rendered)

    # Every repository pins its own contract SHA, so rejecting an unknown key
    # makes each future metadata addition breaking: the fragment fails in every
    # repository that has not bumped, and again on any backward pin (#424).
    def test_unknown_metadata_warns_instead_of_failing(self) -> None:
        path = self.root / "NEXT" / "2026-07-30-issue-249-fwd.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\ndate: 2026-07-30\nissue: 249\ntitle: Forward\n"
            "future-field: a key this contract does not know\n---\n\nBody.\n",
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            entry = changelog.load_canonical(path)
        self.assertEqual(entry.metadata["title"], "Forward")
        self.assertIn("future-field", stderr.getvalue())

    def test_an_unknown_key_does_not_become_a_renderable_field(self) -> None:
        # Tolerating the key must not mean honouring it. An older contract that
        # silently rendered a field it does not understand would be worse than
        # one that rejected it.
        path = self.root / "NEXT" / "2026-07-30-issue-249-ignored.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\ndate: 2026-07-30\nissue: 249\ntitle: Forward\n"
            "future-field: SHOULD-NOT-APPEAR\n---\n\nBody.\n",
            encoding="utf-8",
        )
        with contextlib.redirect_stderr(io.StringIO()):
            rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertNotIn("SHOULD-NOT-APPEAR", rendered)

    def test_a_typo_in_a_required_key_still_fails(self) -> None:
        # The relaxation must not swallow the failures it was never meant to
        # cover: `titel:` leaves `title:` absent, which is still an error.
        path = self.root / "NEXT" / "2026-07-30-issue-249-typo.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\ndate: 2026-07-30\nissue: 249\ntitel: Typo\n---\n\nBody.\n",
            encoding="utf-8",
        )
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(changelog.ChangelogError) as caught:
                changelog.load_canonical(path)
        self.assertIn("title is required", str(caught.exception))

    def test_a_fragment_with_only_known_keys_warns_about_nothing(self) -> None:
        path = fragment(self.root, "2026-07-30-issue-249-clean.md")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            changelog.load_canonical(path)
        self.assertEqual(stderr.getvalue(), "")

    def test_refs_may_not_repeat_the_entry_own_issue(self) -> None:
        path = fragment(
            self.root, "2026-07-30-issue-249-self.md", issue="249", refs="249"
        )
        with self.assertRaises(changelog.ChangelogError) as caught:
            changelog.load_canonical(path)
        self.assertIn("own issue #249", str(caught.exception))

    def test_refs_rejects_duplicates_and_non_numbers(self) -> None:
        for value, expected in (("13, 13", "more than once"), ("abc", "positive issue numbers")):
            path = fragment(
                self.root, "2026-07-30-issue-249-bad.md", issue="249", refs=value
            )
            with self.assertRaises(changelog.ChangelogError) as caught:
                changelog.load_canonical(path)
            self.assertIn(expected, str(caught.exception))

    def test_refs_does_not_create_an_identity_so_two_entries_may_share_one_issue(self) -> None:
        # The whole point: eight fragments can be work on one issue without
        # colliding, which duplicate `issue:` identities would.
        fragment(
            self.root, "2026-07-30-issue-13-owner.md", issue="13", title="Owner"
        )
        fragment(
            self.root,
            "2026-07-30-issue-20260730T090000Z-sibling.md",
            identity_id="20260730T090000Z",
            refs="13",
            title="Sibling",
        )
        rendered = changelog.render(list(changelog.fragments(self.root)))
        self.assertIn("_Date: 2026-07-30; issue #13_", rendered)
        self.assertIn("_Date: 2026-07-30; id:20260730T090000Z; refs #13_", rendered)

    def test_refs_is_validated_at_validate_time_not_only_at_release(self) -> None:
        fragment(
            self.root, "2026-07-30-issue-249-late.md", issue="249", refs="nope"
        )
        with self.assertRaises(changelog.ChangelogError):
            list(changelog.fragments(self.root))

    def test_filename_and_metadata_must_match(self) -> None:
        fragment(
            self.root,
            "2026-07-30-issue-250-contract.md",
            issue="249",
        )

        with self.assertRaisesRegex(changelog.ChangelogError, "does not match"):
            changelog.fragments(self.root)

    def test_duplicate_identity_across_canonical_and_legacy_is_rejected(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        legacy = self.root / "CHANGELOG-unreleased"
        legacy.mkdir()
        (legacy / "old.md").write_text(
            "---\ndate: 2026-07-29\nissue: 249\ntitle: Old entry\n---\n\nBody.\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(changelog.ChangelogError, "duplicate identity issue:249"):
            changelog.fragments(self.root, "CHANGELOG-unreleased")

    def test_historical_next_prose_does_not_infer_duplicate_issue_identity(self) -> None:
        next_dir = self.root / "NEXT"
        next_dir.mkdir()
        (next_dir / "2026-07-01-first.md").write_text(
            "# First\n\nFollow-up context from #64.\n", encoding="utf-8"
        )
        (next_dir / "2026-07-02-second.md").write_text(
            "# Second\n\nAlso references #64.\n", encoding="utf-8"
        )

        entries = changelog.fragments(self.root, allow_legacy_next=True)

        self.assertEqual(
            {"legacy-file:2026-07-01-first.md", "legacy-file:2026-07-02-second.md"},
            {entry.identity for entry in entries},
        )

    def test_render_order_uses_metadata_not_slug_allocation(self) -> None:
        fragment(
            self.root,
            "2026-07-29-issue-300-zzz.md",
            date="2026-07-29",
            issue="300",
            title="Older",
        )
        fragment(
            self.root,
            "2026-07-30-issue-200-aaa.md",
            issue="200",
            title="Newer",
        )

        rendered = changelog.render(changelog.fragments(self.root))

        self.assertLess(rendered.index("## Newer"), rendered.index("## Older"))

    # --- identity is a key, not a spelling (#434) -------------------------------
    # `identity` is lower-cased so two spellings of one hexadecimal id cannot
    # become two entries. That is right for comparison and wrong for the page:
    # a timestamp identity is ISO-8601, where `T` and `Z` are literals.

    def test_a_timestamp_identity_keeps_its_iso_8601_letters(self) -> None:
        fragment(
            self.root,
            "2026-08-05-issue-20260805T000000Z-issueless.md",
            identity_id="20260805T000000Z",
            issue=None,
            date="2026-08-05",
        )

        rendered = changelog.render(list(changelog.fragments(self.root)))

        self.assertIn("id:20260805T000000Z", rendered)
        self.assertNotIn("20260805t000000z", rendered)

    def test_a_hexadecimal_identity_renders_as_written(self) -> None:
        fragment(
            self.root,
            "2026-08-05-issue-ABC123-hex.md",
            identity_id="ABC123",
            issue=None,
            date="2026-08-05",
        )

        rendered = changelog.render(list(changelog.fragments(self.root)))

        self.assertIn("id:ABC123", rendered)

    def test_two_spellings_of_one_id_are_still_one_identity(self) -> None:
        # The whole reason the key is normalised. Rendering the author's
        # spelling must not buy a cosmetic fix with a duplicate-entry bug.
        fragment(self.root, "2026-08-05-issue-ABC123-upper.md",
                 identity_id="ABC123", issue=None, date="2026-08-05")
        fragment(self.root, "2026-08-05-issue-abc123-lower.md",
                 identity_id="abc123", issue=None, date="2026-08-05")

        with self.assertRaisesRegex(changelog.ChangelogError, "duplicate identity"):
            changelog.fragments(self.root)

    def test_an_issue_identity_still_renders_as_a_hash_reference(self) -> None:
        fragment(self.root, "2026-08-05-issue-249-plainer.md", issue="249", date="2026-08-05")

        rendered = changelog.render(list(changelog.fragments(self.root)))

        self.assertIn("_Date: 2026-08-05; issue #249_", rendered)

    # --- two audiences, two renderings (#426) ----------------------------------
    # The org convention asks a fragment to carry its rationale, so one renderer
    # shipped the engineering diary as release notes: 174 KB for 62 entries in
    # the first release cut under this contract, and a released snapshot cannot
    # be edited afterwards.

    def test_a_released_snapshot_keeps_the_lead_and_drops_the_argument(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-diary.md", body=DIARY)

        released = changelog.render(changelog.fragments(self.root), released=True)

        self.assertIn("The lead paragraph,\nwhich is what a release note needs.", released)
        self.assertNotIn("## Why", released)
        self.assertNotIn("- a list", released)

    def test_the_running_log_still_carries_the_whole_body(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-diary.md", body=DIARY)

        rendered = changelog.render(changelog.fragments(self.root))

        self.assertIn("## Why", rendered)
        self.assertIn("- a list", rendered)

    def test_a_lead_that_merely_looks_like_a_heading_is_left_whole(self) -> None:
        # The lead is a plain blank-line split on purpose. Detecting block types
        # to find "real" prose flagged 7 of 62 entries that open like this one,
        # none of which is a heading in CommonMark.
        fragment(
            self.root,
            "2026-07-30-issue-249-hash.md",
            body="#79 threaded its own reply, so the poller never woke.\n\n## Why\n\nLong.\n",
        )

        released = changelog.render(changelog.fragments(self.root), released=True)

        self.assertIn("#79 threaded its own reply, so the poller never woke.", released)
        self.assertNotIn("## Why", released)

    def test_summary_overrides_the_lead_for_the_released_form(self) -> None:
        fragment(
            self.root,
            "2026-07-30-issue-249-diary.md",
            summary="Poller now wakes on threaded replies.",
            body=DIARY,
        )

        released = changelog.render(changelog.fragments(self.root), released=True)

        self.assertIn("Poller now wakes on threaded replies.", released)
        self.assertNotIn("The lead paragraph", released)

    def test_folded_summary_joins_indented_continuation_lines(self) -> None:
        path = fragment(self.root, "2026-07-30-issue-249-folded.md", body=DIARY)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "title: Contract",
                "summary: >-\n"
                "  Poller now wakes on threaded\n"
                "  replies.\n"
                "title: Contract",
            ),
            encoding="utf-8",
        )

        released = changelog.render(changelog.fragments(self.root), released=True)

        self.assertIn("Poller now wakes on threaded replies.", released)
        self.assertNotIn("The lead paragraph", released)

    def test_folded_summary_preserves_paragraph_breaks(self) -> None:
        path = fragment(self.root, "2026-07-30-issue-249-paragraphs.md", body=DIARY)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "title: Contract",
                "summary: >-\n"
                "  Poller now wakes.\n"
                "\n"
                "  Threaded replies are handled.\n"
                "title: Contract",
            ),
            encoding="utf-8",
        )

        entry = changelog.fragments(self.root)[0]

        self.assertEqual(
            "Poller now wakes.\nThreaded replies are handled.",
            entry.metadata["summary"],
        )

    def test_literal_summary_preserves_indented_continuation_lines(self) -> None:
        path = fragment(self.root, "2026-07-30-issue-249-literal.md", body=DIARY)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "title: Contract",
                "summary: |-\n"
                "  Poller now wakes.\n"
                "  Threaded replies are handled.\n"
                "title: Contract",
            ),
            encoding="utf-8",
        )

        released = changelog.render(changelog.fragments(self.root), released=True)

        self.assertIn("Poller now wakes.\nThreaded replies are handled.", released)

    def test_keep_chomping_preserves_trailing_summary_lines(self) -> None:
        path = fragment(self.root, "2026-07-30-issue-249-keep.md", body=DIARY)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "title: Contract",
                "summary: >+\n"
                "  Poller now wakes.\n"
                "\n"
                "\n"
                "title: Contract",
            ),
            encoding="utf-8",
        )

        entry = changelog.fragments(self.root)[0]

        self.assertEqual("Poller now wakes.\n\n\n", entry.metadata["summary"])

    def test_summary_block_ends_at_the_next_metadata_key(self) -> None:
        path = fragment(self.root, "2026-07-30-issue-249-next-key.md", body=DIARY)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "title: Contract",
                "summary: >\n"
                "  Poller now wakes on threaded replies.\n"
                "title: Contract",
            ),
            encoding="utf-8",
        )

        entry = changelog.fragments(self.root)[0]

        self.assertEqual("Poller now wakes on threaded replies.\n", entry.metadata["summary"])
        self.assertEqual("Contract", entry.metadata["title"])

    def test_summary_block_without_an_indented_continuation_is_rejected(self) -> None:
        path = fragment(self.root, "2026-07-30-issue-249-empty-block.md", body=DIARY)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "title: Contract",
                "summary: >-\ntitle: Contract",
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "summary block scalar requires an indented continuation",
        ):
            changelog.fragments(self.root)

    def test_unindented_summary_continuation_is_rejected(self) -> None:
        path = fragment(self.root, "2026-07-30-issue-249-unindented.md", body=DIARY)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "title: Contract",
                "summary: >-\nPoller now wakes.\ntitle: Contract",
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "summary block scalar requires an indented continuation",
        ):
            changelog.fragments(self.root)

    def test_malformed_summary_block_indicator_is_rejected(self) -> None:
        fragment(
            self.root,
            "2026-07-30-issue-249-bad-indicator.md",
            summary=">-unexpected",
            body=DIARY,
        )

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "invalid summary block scalar indicator",
        ):
            changelog.fragments(self.root)

    def test_single_line_summary_behavior_is_unchanged(self) -> None:
        path = fragment(
            self.root,
            "2026-07-30-issue-249-single-line.md",
            summary="Poller: now wakes on threaded replies.",
            body=DIARY,
        )

        entry = changelog.fragments(self.root)[0]

        self.assertEqual(
            "Poller: now wakes on threaded replies.",
            entry.metadata["summary"],
        )

    def test_summary_does_not_touch_the_running_log(self) -> None:
        # `summary` is a release-note override, not a replacement for the diary.
        fragment(
            self.root,
            "2026-07-30-issue-249-diary.md",
            summary="Poller now wakes on threaded replies.",
            body=DIARY,
        )

        rendered = changelog.render(changelog.fragments(self.root))

        self.assertIn("## Why", rendered)
        self.assertNotIn("Poller now wakes on threaded replies.", rendered)

    def test_a_summary_that_is_an_empty_quoted_scalar_is_rejected(self) -> None:
        # `summary:` with nothing after it never reaches validation — the line
        # parser rejects a blank value. `summary: ''` does: it is a well-formed
        # line whose scalar resolves to nothing, and it would silently blank the
        # entry's only released text.
        fragment(self.root, "2026-07-30-issue-249-blank.md", summary="''", body=DIARY)

        with self.assertRaisesRegex(changelog.ChangelogError, "summary must not be empty"):
            changelog.fragments(self.root)

    def test_an_ambiguously_quoted_summary_is_rejected_by_name(self) -> None:
        # It reaches the immutable snapshot exactly as the title does, so it is
        # held to the same standard, and the error says which field is wrong.
        fragment(
            self.root,
            "2026-07-30-issue-249-quoted.md",
            summary='"go" is now "vet"',
        )

        with self.assertRaisesRegex(changelog.ChangelogError, "summary opens and closes"):
            changelog.fragments(self.root)

    def test_release_writes_the_released_form_not_the_diary(self) -> None:
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-diary.md", body=DIARY)
        self.commit_all("initial")

        changelog.release(self.root, "v1.0.0", [])

        snapshot = (self.root / "CHANGELOG/v1.0.0.md").read_text(encoding="utf-8")
        self.assertIn("The lead paragraph,\nwhich is what a release note needs.", snapshot)
        self.assertNotIn("## Why", snapshot)
        self.assertIn("_Date: 2026-07-30; issue #249_", snapshot)

    def test_release_commit_never_executes_a_repository_pre_commit_hook(self) -> None:
        # release() runs with a short-lived, credential-minting App token in its
        # environment (container-release.yml). A pre-commit hook is an untracked,
        # unreviewed side channel that must never observe it (#1203). HOME is
        # pinned to this test's own temp dir so an ambient global core.hooksPath
        # (or any other global git config) can't mask a real local-hook escape —
        # a stray global override on the host once did exactly that, silently.
        with mock.patch.dict(os.environ, {"HOME": str(self.root)}):
            self.init_git()
            fragment(self.root, "2026-07-30-issue-249-default.md")
            self.commit_all("initial")
            hooks_dir = self.root / ".git" / "hooks"
            hooks_dir.mkdir(exist_ok=True)
            marker = self.root / "pre-commit-ran"
            hook = hooks_dir / "pre-commit"
            hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
            hook.chmod(0o755)

            changelog.release(self.root, "v1.0.0", [])

            self.assertFalse(marker.exists(), "release() executed a repository pre-commit hook")

    def test_default_render_selects_only_unscoped_fragments(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-default.md", title="Default")
        fragment(
            self.root,
            "2026-07-30-issue-250-python.md",
            issue="250",
            title="Python",
            component="python",
        )

        rendered = changelog.render_next(self.root)

        self.assertIn("## Default", rendered)
        self.assertNotIn("## Python", rendered)

    def test_component_render_selects_only_that_component(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-default.md", title="Default")
        fragment(
            self.root,
            "2026-07-30-issue-250-python.md",
            issue="250",
            title="Python",
            component="python",
        )
        fragment(
            self.root,
            "2026-07-30-issue-251-node.md",
            issue="251",
            title="Node",
            component="node",
        )

        rendered = changelog.render_next(self.root, component="python")

        self.assertIn("## Python", rendered)
        self.assertNotIn("## Default", rendered)
        self.assertNotIn("## Node", rendered)

    def test_component_names_are_bounded_lowercase_identifiers(self) -> None:
        for index, bad in enumerate(("", ".", "..", "../python", "Python", "python/pkg", "a" * 65)):
            with self.subTest(component=bad):
                fragment(
                    self.root,
                    f"2026-07-30-issue-{300 + index}-bad-component.md",
                    issue=str(300 + index),
                    component=bad,
                )
                with self.assertRaisesRegex(changelog.ChangelogError, "component"):
                    changelog.fragments(self.root)
                for path in (self.root / "NEXT").glob("*.md"):
                    path.unlink()

        fragment(
            self.root,
            "2026-07-30-issue-399-valid-component.md",
            issue="399",
            component="python.worker_v2",
        )
        self.assertEqual(
            "python.worker_v2",
            changelog.fragments(self.root)[0].metadata["component"],
        )

    def test_default_render_fails_empty_when_only_scoped_fragments_exist(self) -> None:
        scoped = fragment(
            self.root,
            "2026-07-30-issue-250-python.md",
            issue="250",
            component="python",
        )

        with self.assertRaisesRegex(changelog.ChangelogError, "no unreleased fragments"):
            changelog.render_next(self.root)

        self.assertTrue(scoped.exists())

    def test_default_release_consumes_only_unscoped_fragments(self) -> None:
        self.init_git()
        plain = fragment(self.root, "2026-07-30-issue-249-default.md", title="Default")
        scoped = fragment(
            self.root,
            "2026-07-30-issue-250-python.md",
            issue="250",
            title="Python",
            component="python",
        )
        self.commit_all("fragments")

        changelog.release(self.root, "v1.0.0", [])

        self.assertFalse(plain.exists())
        self.assertTrue(scoped.exists())
        self.assertIn("## Default", (self.root / "CHANGELOG/v1.0.0.md").read_text())
        self.assertNotIn("## Python", (self.root / "CHANGELOG/v1.0.0.md").read_text())

    def test_component_release_consumes_only_matching_fragments(self) -> None:
        self.init_git()
        plain = fragment(self.root, "2026-07-30-issue-249-default.md", title="Default")
        python = fragment(
            self.root,
            "2026-07-30-issue-250-python.md",
            issue="250",
            title="Python",
            component="python",
        )
        node = fragment(
            self.root,
            "2026-07-30-issue-251-node.md",
            issue="251",
            title="Node",
            component="node",
        )
        self.commit_all("fragments")

        changelog.release(self.root, "python-v1.0.0", [], component="python")

        self.assertTrue(plain.exists())
        self.assertFalse(python.exists())
        self.assertTrue(node.exists())
        snapshot = (self.root / "CHANGELOG/python-v1.0.0.md").read_text()
        self.assertIn("## Python", snapshot)
        self.assertNotIn("## Default", snapshot)
        self.assertNotIn("## Node", snapshot)

    def test_explicit_selection_cannot_cross_component_boundaries(self) -> None:
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-default.md", title="Default")
        fragment(
            self.root,
            "2026-07-30-issue-250-python.md",
            issue="250",
            title="Python",
            component="python",
        )
        self.commit_all("fragments")

        with self.assertRaisesRegex(changelog.ChangelogError, "component"):
            changelog.release(
                self.root,
                "python-v1.0.0",
                [
                    "2026-07-30-issue-249-default.md",
                    "2026-07-30-issue-250-python.md",
                ],
                component="python",
            )

    def test_empty_component_stream_fails_without_consuming_other_streams(self) -> None:
        self.init_git()
        scoped = fragment(
            self.root,
            "2026-07-30-issue-250-python.md",
            issue="250",
            component="python",
        )
        self.commit_all("fragments")

        with self.assertRaisesRegex(changelog.ChangelogError, "selected no fragments"):
            changelog.release(self.root, "node-v1.0.0", [], component="node")

        self.assertTrue(scoped.exists())

    def test_legacy_impact_defaults_to_patch_and_does_not_change_rendered_notes(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-default.md")

        entries = changelog.fragments(self.root)

        self.assertEqual("patch", changelog.release_impact(entries))
        self.assertNotIn("impact", changelog.render(entries))

    def test_invalid_release_impact_is_rejected_during_fragment_validation(self) -> None:
        fragment(
            self.root,
            "2026-07-30-issue-249-invalid-impact.md",
            impact="breaking",
        )

        with self.assertRaisesRegex(changelog.ChangelogError, "impact"):
            changelog.fragments(self.root)

    def test_new_fragment_requires_an_explicit_impact_with_permitted_values(self) -> None:
        self.init_git()
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        fragment(self.root, "2026-07-30-issue-249-missing-impact.md")
        self.commit_all("new fragment")

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "impact is required.*major, minor, or patch",
        ):
            changelog.validate_new_fragment_impacts(self.root, base, "HEAD")

    def test_new_fragment_with_explicit_impact_is_accepted(self) -> None:
        self.init_git()
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        fragment(
            self.root,
            "2026-07-30-issue-249-explicit-impact.md",
            impact="minor",
        )
        self.commit_all("new fragment")

        changelog.validate_new_fragment_impacts(self.root, base, "HEAD")

    def test_existing_fragment_keeps_its_legacy_patch_fallback(self) -> None:
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-legacy-impact.md")
        self.commit_all("legacy fragment")
        base = run(self.root, "git", "rev-parse", "HEAD")
        self.root.joinpath("README.md").write_text("change\n", encoding="utf-8")
        self.commit_all("unrelated change")

        changelog.validate_new_fragment_impacts(self.root, base, "HEAD")
        self.assertEqual("patch", changelog.release_impact(changelog.fragments(self.root)))

    def test_next_rename_to_a_different_identity_requires_explicit_impact(self) -> None:
        self.init_git()
        original = fragment(
            self.root,
            "2026-07-30-issue-249-legacy-impact.md",
        )
        self.commit_all("legacy fragment")
        base = run(self.root, "git", "rev-parse", "HEAD")
        destination = self.root / "NEXT/2026-07-30-issue-250-new-identity.md"
        original.rename(destination)
        destination.write_text(
            destination.read_text(encoding="utf-8").replace("issue: 249", "issue: 250"),
            encoding="utf-8",
        )
        self.commit_all("rename to a different fragment identity")
        self.assertTrue(
            run(
                self.root,
                "git",
                "diff",
                "--find-renames",
                "--name-status",
                f"{base}...HEAD",
            ).startswith("R"),
            "fixture must exercise Git's rename classification",
        )

        with self.assertRaisesRegex(changelog.ChangelogError, "impact is required"):
            changelog.validate_new_fragment_impacts(self.root, base, "HEAD")

    def test_same_identity_slug_rename_keeps_legacy_impact_compatibility(self) -> None:
        self.init_git()
        original = fragment(
            self.root,
            "2026-07-30-issue-249-old-slug.md",
        )
        self.commit_all("legacy fragment")
        base = run(self.root, "git", "rev-parse", "HEAD")
        original.rename(self.root / "NEXT/2026-07-30-issue-249-clearer-slug.md")
        self.commit_all("rename fragment slug")

        changelog.validate_new_fragment_impacts(self.root, base, "HEAD")

    def test_rename_from_noncanonical_next_subdirectory_is_a_new_fragment(self) -> None:
        self.init_git()
        original = fragment(
            self.root,
            "2026-07-30-issue-249-nested.md",
        )
        nested = self.root / "NEXT/archive/2026-07-30-issue-249-nested.md"
        nested.parent.mkdir()
        original.rename(nested)
        self.commit_all("noncanonical nested entry")
        base = run(self.root, "git", "rev-parse", "HEAD")
        nested.rename(self.root / "NEXT/2026-07-30-issue-249-nested.md")
        self.commit_all("move entry into canonical NEXT")

        with self.assertRaisesRegex(changelog.ChangelogError, "impact is required"):
            changelog.validate_new_fragment_impacts(self.root, base, "HEAD")

    def test_rename_from_issue_to_same_spelling_id_is_a_new_fragment(self) -> None:
        self.init_git()
        original = fragment(
            self.root,
            "2026-07-30-issue-123456-old-slug.md",
            issue="123456",
        )
        self.commit_all("legacy issue fragment")
        base = run(self.root, "git", "rev-parse", "HEAD")
        destination = self.root / "NEXT/2026-07-30-issue-123456-new-slug.md"
        original.rename(destination)
        destination.write_text(
            destination.read_text(encoding="utf-8").replace(
                "issue: 123456",
                "id: 123456",
            ),
            encoding="utf-8",
        )
        self.commit_all("rename issue fragment to id fragment")

        with self.assertRaisesRegex(changelog.ChangelogError, "impact is required"):
            changelog.validate_new_fragment_impacts(self.root, base, "HEAD")

    def test_missing_impact_migration_deadline_is_validated_and_expires(self) -> None:
        self.assertFalse(changelog.impact_migration_window_active("1970-01-01"))
        with self.assertRaisesRegex(changelog.ChangelogError, "YYYY-MM-DD"):
            changelog.impact_migration_window_active("tomorrow")

    def test_validate_command_requires_impact_for_fragments_added_since_base(self) -> None:
        self.init_git()
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        fragment(self.root, "2026-07-30-issue-249-command-impact.md")
        self.commit_all("new fragment")

        completed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "validate",
                "--repo-root",
                str(self.root),
                "--base",
                base,
                "--head",
                "HEAD",
            ],
            cwd=self.root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(1, completed.returncode)
        self.assertIn("impact is required", completed.stderr)
        self.assertIn("major, minor, or patch", completed.stderr)

    def test_validate_command_allows_legacy_branch_fragments_during_migration_window(self) -> None:
        self.init_git()
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        fragment(self.root, "2026-07-30-issue-249-grace.md")
        self.commit_all("legacy branch fragment")

        completed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "validate",
                "--repo-root",
                str(self.root),
                "--base",
                base,
                "--allow-missing-impact-through",
                "9999-12-31",
            ],
            cwd=self.root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_required_checks_enforce_new_fragment_impact_after_one_shared_grace_date(self) -> None:
        repository = MODULE_PATH.parent.parent
        workflows = [
            repository / ".github/workflows/changelog-validate.yml",
            repository / ".github/workflows/generated-artifacts.yml",
        ]

        for workflow in workflows:
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                self.assertIn('args+=(--base "$BASE_SHA" --head "$HEAD_SHA")', text)
                self.assertIn(
                    "--allow-missing-impact-through 2026-08-29",
                    text,
                )

    def test_mixed_release_impacts_choose_the_highest_selected_impact(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-patch.md", impact="patch")
        fragment(
            self.root,
            "2026-07-30-issue-250-major.md",
            issue="250",
            impact="major",
        )
        fragment(
            self.root,
            "2026-07-30-issue-251-minor.md",
            issue="251",
            impact="minor",
        )

        self.assertEqual("major", changelog.release_impact(changelog.fragments(self.root)))

    def test_next_version_matches_the_version_release_accepts_without_using_git(self) -> None:
        self.init_git()
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v1.2.3.md").write_text("previous\n", encoding="utf-8")
        fragment(
            self.root,
            "2026-07-30-issue-249-feature.md",
            impact="minor",
        )
        self.commit_all("release candidate")

        with mock.patch.object(
            changelog,
            "git",
            side_effect=AssertionError("next-version must not invoke git"),
        ):
            version = changelog.next_version(self.root, [])

        self.assertEqual("v1.3.0", version)
        selected = changelog.select_release_fragments(self.root, [])
        changelog.validate_release_bump(self.root, version, selected)
        with self.assertRaisesRegex(changelog.ChangelogError, "require a minor bump"):
            changelog.validate_release_bump(self.root, "v1.2.4", selected)

    def test_release_plan_derives_one_version_and_preview_from_one_selection(self) -> None:
        self.init_git()
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v1.2.3.md").write_text("previous\n", encoding="utf-8")
        selected = fragment(
            self.root,
            "2026-07-30-issue-249-feature.md",
            impact="minor",
            body="Selected release note.\n\nImplementation detail.\n",
        )
        fragment(
            self.root,
            "2026-07-30-issue-250-deferred.md",
            issue="250",
            impact="major",
            body="Deferred note.\n",
        )
        self.commit_all("release candidate")

        plan = changelog.release_plan(self.root, "", [selected.name])

        self.assertTrue(plan["selected"])
        self.assertEqual("v1.3.0", plan["version"])
        self.assertEqual("v1.2.3", plan["previous_release"])
        self.assertEqual("minor", plan["bump"])
        self.assertIn("Selected release note.", plan["preview"])
        self.assertNotIn("Deferred note.", plan["preview"])
        self.assertIn(selected.name, plan["fragments"])
        self.assertIsNotNone(plan["selection_digest"])

    def test_release_plan_validates_explicit_version_using_the_release_rule(self) -> None:
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v3.4.5.md").write_text("previous\n", encoding="utf-8")
        fragment(self.root, "2026-07-30-issue-249-feature.md", impact="minor")

        with self.assertRaisesRegex(changelog.ChangelogError, "require a minor bump"):
            changelog.release_plan(self.root, "v3.4.6", [])

        plan = changelog.release_plan(self.root, "v3.5.0", [])
        self.assertEqual("v3.5.0", plan["version"])

    def test_release_plan_accepts_an_explicit_bootstrap_version(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-first-release.md", impact="major")

        plan = changelog.release_plan(self.root, "v0.1.0", [])

        self.assertEqual("v0.1.0", plan["version"])
        self.assertIsNone(plan["previous_release"])
        self.assertEqual("bootstrap", plan["bump"])

    def test_release_plan_reports_an_empty_stream_without_mutating_it(self) -> None:
        scoped = fragment(
            self.root,
            "2026-07-30-issue-249-python.md",
            component="python",
            impact="minor",
        )

        plan = changelog.release_plan(self.root, "", [])

        self.assertFalse(plan["selected"])
        self.assertIsNone(plan["version"])
        self.assertTrue(scoped.exists())

    def test_release_plan_cli_emits_the_read_only_json_plan(self) -> None:
        self.init_git()
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v2.0.0.md").write_text("previous\n", encoding="utf-8")
        fragment(self.root, "2026-07-30-issue-249-fix.md", impact="patch")
        self.commit_all("release candidate")
        before_head = run(self.root, "git", "rev-parse", "HEAD")

        output = run(
            self.root,
            sys.executable,
            str(MODULE_PATH),
            "release-plan",
            "--repo-root",
            str(self.root),
        )
        plan = json.loads(output)

        self.assertEqual("v2.0.1", plan["version"])
        self.assertEqual("patch", plan["bump"])
        self.assertEqual(before_head, run(self.root, "git", "rev-parse", "HEAD"))
        self.assertTrue((self.root / "NEXT/2026-07-30-issue-249-fix.md").exists())
        self.assertFalse((snapshots / "v2.0.1.md").exists())

    def test_next_version_cli_is_read_only_even_with_a_dirty_tree(self) -> None:
        self.init_git()
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v4.5.6.md").write_text("previous\n", encoding="utf-8")
        selected = fragment(
            self.root,
            "2026-07-30-issue-249-fix.md",
            impact="patch",
        )
        self.commit_all("release candidate")
        selected.write_text(selected.read_text(encoding="utf-8") + "\nDirty.\n")
        before_head = run(self.root, "git", "rev-parse", "HEAD")
        before_diff = run(self.root, "git", "diff")
        before_tags = run(self.root, "git", "tag", "--list")

        output = run(
            self.root,
            sys.executable,
            str(MODULE_PATH),
            "next-version",
            "--repo-root",
            str(self.root),
        )

        self.assertEqual("v4.5.7", output)
        self.assertEqual(before_head, run(self.root, "git", "rev-parse", "HEAD"))
        self.assertEqual(before_diff, run(self.root, "git", "diff"))
        self.assertEqual(before_tags, run(self.root, "git", "tag", "--list"))
        self.assertTrue(selected.exists())
        self.assertFalse((snapshots / "v4.5.7.md").exists())

    def test_next_version_honors_component_and_repeated_fragment_selection(self) -> None:
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v9.9.9.md").write_text("default\n", encoding="utf-8")
        (snapshots / "py-v2.4.0.md").write_text("python\n", encoding="utf-8")
        fragment(
            self.root,
            "2026-07-30-issue-249-default.md",
            impact="major",
        )
        patch = fragment(
            self.root,
            "2026-07-30-issue-250-python-fix.md",
            issue="250",
            component="python",
            impact="patch",
        )
        minor = fragment(
            self.root,
            "2026-07-30-issue-251-python-feature.md",
            issue="251",
            component="python",
            impact="minor",
        )
        fragment(
            self.root,
            "2026-07-30-issue-252-python-breaking.md",
            issue="252",
            component="python",
            impact="major",
        )

        output = run(
            self.root,
            sys.executable,
            str(MODULE_PATH),
            "next-version",
            "--repo-root",
            str(self.root),
            "--component",
            "python",
            "--prefix",
            "py-v",
            "--fragment",
            f"NEXT/{patch.name}",
            "--fragment",
            minor.name,
        )

        self.assertEqual("py-v2.5.0", output)

    def test_released_preview_honors_the_same_fragment_selection_as_release(self) -> None:
        selected = fragment(
            self.root,
            "2026-07-30-issue-250-selected.md",
            issue="250",
            body="Selected release note.\n",
        )
        fragment(
            self.root,
            "2026-07-30-issue-251-deferred.md",
            issue="251",
            body="Deferred release note.\n",
        )

        output = run(
            self.root,
            sys.executable,
            str(MODULE_PATH),
            "render-next",
            "--repo-root",
            str(self.root),
            "--as-released",
            "--fragment",
            f"NEXT/{selected.name}",
        )

        self.assertIn("Selected release note.", output)
        self.assertNotIn("Deferred release note.", output)

    def test_released_preview_rejects_duplicate_fragment_selection(self) -> None:
        selected = fragment(self.root, "2026-07-30-issue-250-selected.md", issue="250")

        completed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "render-next",
                "--repo-root",
                str(self.root),
                "--fragment",
                selected.name,
                "--fragment",
                f"NEXT/{selected.name}",
            ],
            cwd=self.root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(1, completed.returncode)
        self.assertIn("selected fragment was repeated", completed.stderr)

    def test_selection_digest_is_empty_for_an_empty_default_or_component_stream(self) -> None:
        fragment(
            self.root,
            "2026-07-30-issue-250-python.md",
            issue="250",
            component="python",
        )

        default_digest = run(
            self.root,
            sys.executable,
            str(MODULE_PATH),
            "selection-digest",
            "--repo-root",
            str(self.root),
        )
        missing_component_digest = run(
            self.root,
            sys.executable,
            str(MODULE_PATH),
            "selection-digest",
            "--repo-root",
            str(self.root),
            "--component",
            "worker",
            "--prefix",
            "worker-v",
        )

        self.assertEqual("", default_digest)
        self.assertEqual("", missing_component_digest)

    def test_selection_digest_normalizes_fragment_order_and_binds_stream_inputs(self) -> None:
        first = fragment(
            self.root,
            "2026-07-30-issue-250-python.md",
            issue="250",
            component="python",
        )
        second = fragment(
            self.root,
            "2026-07-30-issue-251-python.md",
            issue="251",
            component="python",
        )

        def digest(prefix: str, names: list[str]) -> str:
            arguments = [
                sys.executable,
                str(MODULE_PATH),
                "selection-digest",
                "--repo-root",
                str(self.root),
                "--component",
                "python",
                "--prefix",
                prefix,
            ]
            for name in names:
                arguments.extend(("--fragment", name))
            return run(self.root, *arguments)

        forward = digest("python-v", [first.name, second.name])
        reverse = digest("python-v", [second.name, first.name])

        self.assertRegex(forward, r"^[0-9a-f]{64}$")
        self.assertEqual(forward, reverse)
        self.assertNotEqual(forward, digest("v", [first.name, second.name]))
        self.assertNotEqual(forward, digest("python-v", [first.name]))

    def test_selection_digest_rejects_noncanonical_version_namespaces(self) -> None:
        fragment(self.root, "2026-07-30-issue-250-default.md", issue="250")

        for prefix in ("Python-v", "-python-v", "python", "python-v-"):
            with self.subTest(prefix=prefix):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(MODULE_PATH),
                        "selection-digest",
                        "--repo-root",
                        str(self.root),
                        "--prefix",
                        prefix,
                    ],
                    cwd=self.root,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertNotEqual(0, completed.returncode)
                self.assertTrue(completed.stderr.strip())

    def test_duplicate_fragment_selectors_fail_before_release_mutates_the_tree(self) -> None:
        self.init_git()
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v1.0.0.md").write_text("previous\n", encoding="utf-8")
        selected = fragment(self.root, "2026-07-30-issue-249-fix.md")
        self.commit_all("release candidate")
        before_head = run(self.root, "git", "rev-parse", "HEAD")
        before_tree = run(self.root, "git", "write-tree")
        before_tags = run(self.root, "git", "tag", "--list")

        for command in (
            ["next-version"],
            ["release", "--version", "v1.0.1"],
        ):
            with self.subTest(command=command[0]):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(MODULE_PATH),
                        *command,
                        "--repo-root",
                        str(self.root),
                        "--fragment",
                        selected.name,
                        "--fragment",
                        f"NEXT/{selected.name}",
                    ],
                    cwd=self.root,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                self.assertEqual(1, completed.returncode)
                self.assertIn("selected fragment was repeated", completed.stderr)
                self.assertEqual(before_head, run(self.root, "git", "rev-parse", "HEAD"))
                self.assertEqual(before_tree, run(self.root, "git", "write-tree"))
                self.assertEqual(before_tags, run(self.root, "git", "tag", "--list"))
                self.assertEqual("", run(self.root, "git", "status", "--porcelain"))
                self.assertTrue(selected.exists())
                self.assertFalse((snapshots / "v1.0.1.md").exists())

    def test_next_version_keeps_component_and_version_namespace_independent(self) -> None:
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v8.0.0.md").write_text("default namespace\n", encoding="utf-8")
        (snapshots / "worker-v2.0.0.md").write_text(
            "worker namespace\n",
            encoding="utf-8",
        )
        fragment(
            self.root,
            "2026-07-30-issue-250-python.md",
            issue="250",
            component="python",
            impact="patch",
        )

        self.assertEqual(
            "v8.0.1",
            changelog.next_version(self.root, [], component="python"),
        )
        self.assertEqual(
            "worker-v2.0.1",
            changelog.next_version(
                self.root,
                [],
                component="python",
                prefix="worker-v",
            ),
        )

    def test_next_version_preserves_release_selection_diagnostics(self) -> None:
        scoped = fragment(
            self.root,
            "2026-07-30-issue-250-python.md",
            issue="250",
            component="python",
        )

        for arguments, expected in (
            ([], "release selected no fragments"),
            (["--fragment", scoped.name], "belongs to component python"),
            (["--fragment", "../missing.md"], "must not traverse directories"),
        ):
            with self.subTest(arguments=arguments):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(MODULE_PATH),
                        "next-version",
                        "--repo-root",
                        str(self.root),
                        *arguments,
                    ],
                    cwd=self.root,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(1, completed.returncode)
                self.assertEqual("", completed.stdout)
                self.assertTrue(completed.stderr.startswith("changelog: "))
                self.assertIn(expected, completed.stderr)

    def test_next_version_refuses_to_invent_a_first_release_baseline(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-first.md", impact="major")

        completed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "next-version",
                "--repo-root",
                str(self.root),
            ],
            cwd=self.root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(1, completed.returncode)
        self.assertEqual("", completed.stdout)
        self.assertIn("without a previous release", completed.stderr)

    def test_next_version_rejects_an_invalid_version_prefix(self) -> None:
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v1.0.0.md").write_text("previous\n", encoding="utf-8")
        fragment(self.root, "2026-07-30-issue-249-next.md")

        for invalid in ("../python-v", "v1", "python-v1", "Python-v"):
            with self.subTest(prefix=invalid):
                with self.assertRaisesRegex(changelog.ChangelogError, "version prefix"):
                    changelog.next_version(self.root, [], prefix=invalid)

    def test_next_version_preserves_invalid_impact_diagnostics(self) -> None:
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v1.0.0.md").write_text("previous\n", encoding="utf-8")
        fragment(
            self.root,
            "2026-07-30-issue-249-invalid.md",
            impact="breaking",
        )

        completed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "next-version",
                "--repo-root",
                str(self.root),
            ],
            cwd=self.root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(1, completed.returncode)
        self.assertIn("impact must be one of", completed.stderr)

    def test_invalid_semver_prerelease_and_build_identifiers_fail_without_mutation(self) -> None:
        self.init_git()
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v1.0.0.md").write_text("previous\n", encoding="utf-8")
        selected = fragment(self.root, "2026-07-30-issue-249-next.md")
        self.commit_all("release candidate")
        before_head = run(self.root, "git", "rev-parse", "HEAD")
        before_tree = run(self.root, "git", "write-tree")

        for invalid in (
            "v1.0.1-01",
            "v1.0.1-..",
            "v1.0.1-alpha..1",
            "v1.0.1-alpha_1",
            "v1.0.1+..",
            "v1.0.1+build..1",
        ):
            with self.subTest(version=invalid):
                with self.assertRaisesRegex(changelog.ChangelogError, "SemVer"):
                    changelog.release(self.root, invalid, [])
                self.assertEqual(before_head, run(self.root, "git", "rev-parse", "HEAD"))
                self.assertEqual(before_tree, run(self.root, "git", "write-tree"))
                self.assertEqual("", run(self.root, "git", "status", "--porcelain"))
                self.assertTrue(selected.exists())
                self.assertFalse((snapshots / f"{invalid}.md").exists())

    def test_invalid_semver_snapshots_cannot_become_a_release_baseline(self) -> None:
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        fragment(self.root, "2026-07-30-issue-249-next.md")

        for invalid in (
            "v1.2.3-01",
            "v1.2.3-..",
            "v1.2.3+..",
        ):
            with self.subTest(version=invalid):
                path = snapshots / f"{invalid}.md"
                path.write_text("invalid baseline\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    changelog.ChangelogError,
                    "without a previous release",
                ):
                    changelog.next_version(self.root, [])
                path.unlink()

    def test_valid_semver_prerelease_and_build_history_is_preserved(self) -> None:
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v1.2.3-rc.1+build.007.md").write_text(
            "previous\n",
            encoding="utf-8",
        )
        fragment(self.root, "2026-07-30-issue-249-next.md")

        selected = changelog.select_release_fragments(self.root, [])

        self.assertEqual("v1.2.4", changelog.next_version(self.root, []))
        changelog.validate_release_bump(
            self.root,
            "v1.2.4-rc.1+build.007",
            selected,
        )

    def test_help_names_next_version_as_read_only_and_documents_its_selectors(self) -> None:
        top_level = run(self.root, sys.executable, str(MODULE_PATH), "--help")
        command = run(
            self.root,
            sys.executable,
            str(MODULE_PATH),
            "next-version",
            "--help",
        )

        self.assertIn("next-version", top_level)
        self.assertIn("without changing the", top_level)
        self.assertIn("repository", top_level)
        self.assertIn("--component", command)
        self.assertIn("--fragment", command)
        self.assertIn("--prefix", command)

    def test_render_next_can_show_the_released_shape_before_it_is_immutable(self) -> None:
        fragment(self.root, "2026-07-30-issue-249-diary.md", body=DIARY)

        preview = run(
            self.root, sys.executable, str(MODULE_PATH), "render-next",
            "--repo-root", str(self.root), "--as-released",
        )
        diary = run(
            self.root, sys.executable, str(MODULE_PATH), "render-next",
            "--repo-root", str(self.root),
        )

        self.assertNotIn("## Why", preview)
        self.assertIn("## Why", diary)

    def test_release_creates_one_snapshot_consumes_fragment_and_tags_commit(self) -> None:
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("initial")

        changelog.release(self.root, "v1.0.0", [])

        snapshot = self.root / "CHANGELOG" / "v1.0.0.md"
        self.assertTrue(snapshot.is_file())
        self.assertFalse((self.root / "NEXT/2026-07-30-issue-249-contract.md").exists())
        self.assertEqual(run(self.root, "git", "rev-parse", "HEAD"), run(self.root, "git", "rev-list", "-n", "1", "v1.0.0"))
        self.assertEqual("", run(self.root, "git", "status", "--porcelain"))

    def test_release_does_not_commit_untracked_workspace_cache(self) -> None:
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("initial")
        cache = self.root / ".verjson-changelog-tools.random"
        cache.mkdir()
        (cache / "resolver.py").write_text("untrusted cache residue\n", encoding="utf-8")

        changelog.release(self.root, "v1.0.0", [])

        committed = run(self.root, "git", "show", "--pretty=", "--name-only", "HEAD")
        self.assertNotIn(cache.name, committed)
        self.assertIn(f"?? {cache.name}/", run(self.root, "git", "status", "--porcelain"))

    def test_release_refuses_to_overwrite_snapshot(self) -> None:
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        snapshot = self.root / "CHANGELOG" / "v1.0.0.md"
        snapshot.parent.mkdir()
        snapshot.write_text("released\n", encoding="utf-8")
        self.commit_all("initial")

        with self.assertRaisesRegex(changelog.ChangelogError, "already exists"):
            changelog.release(self.root, "v1.0.0", [])

    def test_release_refuses_an_existing_tag_before_mutating_the_repository(self) -> None:
        self.init_git()
        selected = fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("initial")
        run(self.root, "git", "tag", "-a", "v1.0.0", "-m", "Existing release")
        initial_head = run(self.root, "git", "rev-parse", "HEAD")
        initial_tree = run(self.root, "git", "rev-parse", "HEAD^{tree}")
        initial_fragment = selected.read_bytes()

        with self.assertRaisesRegex(changelog.ChangelogError, "tag already exists"):
            changelog.release(self.root, "v1.0.0", [])

        self.assertEqual(initial_head, run(self.root, "git", "rev-parse", "HEAD"))
        self.assertEqual(initial_tree, run(self.root, "git", "write-tree"))
        self.assertEqual(
            initial_head,
            run(self.root, "git", "rev-list", "-n", "1", "v1.0.0"),
        )
        self.assertEqual(initial_fragment, selected.read_bytes())
        self.assertFalse((self.root / "CHANGELOG/v1.0.0.md").exists())
        self.assertFalse((self.root / "CHANGELOG.md").exists())
        self.assertEqual("", run(self.root, "git", "status", "--porcelain"))

    def test_release_refuses_a_version_smaller_than_selected_impact_without_mutation(self) -> None:
        self.init_git()
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v1.2.3.md").write_text("previous\n", encoding="utf-8")
        selected = fragment(
            self.root,
            "2026-07-30-issue-249-breaking.md",
            impact="major",
        )
        self.commit_all("breaking fragment")

        with self.assertRaisesRegex(changelog.ChangelogError, "require a major bump"):
            changelog.release(self.root, "v1.2.4", [])

        self.assertTrue(selected.exists())
        self.assertFalse((snapshots / "v1.2.4.md").exists())
        self.assertEqual("", run(self.root, "git", "status", "--porcelain"))

    def test_zero_major_versions_follow_normal_semver_axes(self) -> None:
        self.init_git()
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v0.4.2.md").write_text("previous\n", encoding="utf-8")
        fragment(
            self.root,
            "2026-07-30-issue-249-breaking.md",
            impact="major",
        )
        self.commit_all("breaking fragment")

        changelog.release(self.root, "v1.0.0", [])

        self.assertTrue((snapshots / "v1.0.0.md").exists())

    def test_minor_impact_on_zero_major_increments_the_minor_axis(self) -> None:
        self.init_git()
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v0.4.2.md").write_text("previous\n", encoding="utf-8")
        fragment(
            self.root,
            "2026-07-30-issue-249-feature.md",
            impact="minor",
        )
        self.commit_all("feature fragment")

        changelog.release(self.root, "v0.5.0", [])

        self.assertTrue((snapshots / "v0.5.0.md").exists())

    def test_subset_impact_ignores_unselected_higher_impact_fragments(self) -> None:
        self.init_git()
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v0.0.0.md").write_text("previous\n", encoding="utf-8")
        patch = fragment(
            self.root,
            "2026-07-30-issue-249-fix.md",
            impact="patch",
        )
        major = fragment(
            self.root,
            "2026-07-30-issue-250-breaking.md",
            issue="250",
            impact="major",
        )
        self.commit_all("mixed impacts")

        changelog.release(self.root, "v0.0.1", [patch.name])

        self.assertFalse(patch.exists())
        self.assertTrue(major.exists())

    def test_first_release_accepts_any_semver_core(self) -> None:
        self.init_git()
        fragment(
            self.root,
            "2026-07-30-issue-249-breaking.md",
            impact="major",
        )
        self.commit_all("first release")

        changelog.release(self.root, "v0.3.0", [])

        self.assertTrue((self.root / "CHANGELOG/v0.3.0.md").exists())

    def test_release_accepts_a_next_relative_selected_fragment(self) -> None:
        """#328: release callers forward the repository-relative path they were given.

        `changelog-release.yml` passes each newline entry through unchanged, and
        callers supply `NEXT/<file>.md`. Indexing selection by basename alone
        rejected that with "selected fragment does not exist", which reads as a
        missing fragment rather than a path-shape mismatch — the diagnosis that
        cost a real release dry-run (verjson-customer-lifecycle#4, run
        30770166825).
        """
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        fragment(self.root, "2026-07-31-issue-250-other.md", date="2026-07-31", issue="250", title="Other")
        self.commit_all("initial")

        changelog.release(self.root, "v1.0.0", ["NEXT/2026-07-30-issue-249-contract.md"])

        snapshot = (self.root / "CHANGELOG" / "v1.0.0.md").read_text(encoding="utf-8")
        self.assertIn("249", snapshot)
        # Selection still selects: the unnamed fragment must survive, or this
        # passes by releasing everything regardless of what was asked for.
        self.assertNotIn("250", snapshot)
        self.assertTrue((self.root / "NEXT/2026-07-31-issue-250-other.md").exists())

    def test_release_accepts_a_bare_selected_basename(self) -> None:
        """The pre-existing spelling keeps working — #328 adds a form, not swaps one."""
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("initial")

        changelog.release(self.root, "v1.0.0", ["2026-07-30-issue-249-contract.md"])

        self.assertIn("249", (self.root / "CHANGELOG" / "v1.0.0.md").read_text(encoding="utf-8"))

    def test_release_refuses_selected_paths_outside_the_unreleased_dir(self) -> None:
        """Traversal and foreign directories are refused, not reduced to a basename.

        Normalising by taking the basename unconditionally would make
        `../../elsewhere/<name>.md` select the fragment that happens to share its
        name — a value pointing outside `NEXT/` is a caller bug, and silently
        honouring it is how a release consumes a fragment nobody selected.
        """
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("initial")

        for bad, expected in (
            ("../2026-07-30-issue-249-contract.md", "traverse"),
            ("../../NEXT/2026-07-30-issue-249-contract.md", "traverse"),
            ("elsewhere/2026-07-30-issue-249-contract.md", "bare filename"),
            ("NEXT/nested/2026-07-30-issue-249-contract.md", "bare filename"),
            ("/abs/2026-07-30-issue-249-contract.md", "repository-relative"),
            ("", "empty"),
        ):
            with self.subTest(selected=bad):
                with self.assertRaisesRegex(changelog.ChangelogError, expected):
                    changelog.release(self.root, "v1.0.0", [bad])
                self.assertFalse(
                    (self.root / "CHANGELOG" / "v1.0.0.md").exists(),
                    "a refused selection must not have written a snapshot",
                )

    def test_release_still_reports_a_genuinely_missing_fragment(self) -> None:
        """The original error survives for its original cause, now unambiguously."""
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("initial")

        for missing in ("2026-07-30-issue-999-absent.md", "NEXT/2026-07-30-issue-999-absent.md"):
            with self.subTest(selected=missing):
                with self.assertRaisesRegex(changelog.ChangelogError, "does not exist"):
                    changelog.release(self.root, "v1.0.0", [missing])

    def test_feature_pr_cannot_edit_aggregate_or_consume_fragment(self) -> None:
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        (self.root / "CHANGELOG.md").write_text("authored\n", encoding="utf-8")
        (self.root / "NEXT/2026-07-30-issue-249-contract.md").unlink()
        self.commit_all("feature")

        with self.assertRaisesRegex(changelog.ChangelogError, "generated aggregates"):
            changelog.check_pr(self.root, base, "HEAD")

    def test_feature_pr_cannot_consume_fragment_by_rename(self) -> None:
        self.init_git()
        original = fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        destination = self.root / "archive.md"
        original.rename(destination)
        self.commit_all("rename")

        with self.assertRaisesRegex(changelog.ChangelogError, "cannot consume"):
            changelog.check_pr(self.root, base, "HEAD")

    def test_feature_pr_cannot_consume_a_component_scoped_fragment(self) -> None:
        self.init_git()
        scoped = fragment(
            self.root,
            "2026-07-30-issue-390-python.md",
            issue="390",
            component="python",
        )
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        scoped.unlink()
        self.commit_all("consume scoped fragment")

        with self.assertRaisesRegex(changelog.ChangelogError, "cannot consume"):
            changelog.check_pr(self.root, base, "HEAD")

    def test_feature_pr_can_rename_fragment_within_next(self) -> None:
        self.init_git()
        original = fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        destination = self.root / "NEXT/2026-07-30-issue-249-canonical-contract.md"
        original.rename(destination)
        self.commit_all("rename")

        changelog.check_pr(self.root, base, "HEAD")

    def test_dependency_changes_require_a_new_fragment_across_supported_stacks(self) -> None:
        dependency_paths = (
            "package.json",
            "apps/api/package-lock.json",
            "pnpm-lock.yaml",
            "web/yarn.lock",
            "pyproject.toml",
            "requirements.txt",
            "requirements-dev.txt",
            "poetry.lock",
            "Pipfile",
            "Pipfile.lock",
            "uv.lock",
            "services/worker/go.mod",
            "services/worker/go.sum",
            "crates/core/Cargo.toml",
            "Cargo.lock",
            "infra/.terraform.lock.hcl",
            "Dockerfile",
            "images/Dockerfile.pwsh",
            "images/worker.Dockerfile",
            "Containerfile",
            "images/Containerfile.build.ci",
            "images/worker.Containerfile",
            "docker-compose.yml",
            "docker-compose.override.yaml",
            "infra/compose.yaml",
            "infra/compose.prod.yml",
        )
        for dependency_path in dependency_paths:
            with self.subTest(path=dependency_path):
                original_root = self.root
                with tempfile.TemporaryDirectory() as temporary:
                    try:
                        self.root = Path(temporary)
                        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
                        self.init_git()
                        self.commit_all("base")
                        base = run(self.root, "git", "rev-parse", "HEAD")
                        path = self.root / dependency_path
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text("dependency update\n", encoding="utf-8")
                        self.commit_all("renovate dependency update")

                        with self.assertRaisesRegex(
                            changelog.ChangelogError,
                            "dependency manifests or lockfiles require a new NEXT fragment",
                        ):
                            changelog.check_pr(self.root, base, "HEAD")
                    finally:
                        self.root = original_root

    def test_non_dependency_change_requires_a_new_fragment(self) -> None:
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        runbook = self.root / "docs" / "runbook.md"
        runbook.parent.mkdir(parents=True, exist_ok=True)
        runbook.write_text("Documented behavior.\n", encoding="utf-8")
        self.commit_all("document behavior")

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "release-relevant changes require a new NEXT fragment",
        ):
            changelog.check_pr(self.root, base, "HEAD")

    def test_non_dependency_change_with_a_new_valid_fragment_is_accepted(self) -> None:
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        runbook = self.root / "docs" / "runbook.md"
        runbook.parent.mkdir(parents=True, exist_ok=True)
        runbook.write_text("Documented behavior.\n", encoding="utf-8")
        fragment(
            self.root,
            "2026-07-30-issue-249-contract.md",
            impact="patch",
        )
        self.commit_all("document behavior with fragment")

        changelog.check_pr(self.root, base, "HEAD")

    def test_non_dependency_change_cannot_delete_its_fragment_later(self) -> None:
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        runbook = self.root / "docs" / "runbook.md"
        runbook.parent.mkdir(parents=True, exist_ok=True)
        runbook.write_text("Documented behavior.\n", encoding="utf-8")
        added = fragment(
            self.root,
            "2026-07-30-issue-249-contract.md",
            impact="patch",
        )
        self.commit_all("document behavior with fragment")
        added.unlink()
        self.commit_all("remove fragment")

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "release-relevant changes require a new NEXT fragment",
        ):
            changelog.check_pr(self.root, base, "HEAD")

    def test_test_only_change_does_not_require_a_fragment(self) -> None:
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        fixture = self.root / "tests" / "fixtures" / "response.json"
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text('{"ok":true}\n', encoding="utf-8")
        self.commit_all("test fixture")

        changelog.check_pr(self.root, base, "HEAD")

    def test_executable_github_yaml_takes_precedence_over_test_exemptions(self) -> None:
        paths = (
            ".github/workflows/ci.test.yml",
            ".github/workflows/release.spec.yaml",
            ".github/actions/test/action.yml",
            "tests/action.yml",
            "packages/foo/tests/action.yaml",
        )

        for changed_path in paths:
            with self.subTest(path=changed_path):
                original_root = self.root
                try:
                    with tempfile.TemporaryDirectory() as directory:
                        self.root = Path(directory)
                        self.root.joinpath("README.md").write_text(
                            "base\n", encoding="utf-8"
                        )
                        self.init_git()
                        self.commit_all("base")
                        base = run(self.root, "git", "rev-parse", "HEAD")
                        changed = self.root / changed_path
                        changed.parent.mkdir(parents=True, exist_ok=True)
                        changed.write_text("name: executable\n", encoding="utf-8")
                        self.commit_all("add executable github yaml")

                        with self.assertRaisesRegex(
                            changelog.ChangelogError,
                            "release-relevant changes require a new NEXT fragment",
                        ):
                            changelog.check_pr(self.root, base, "HEAD")
                finally:
                    self.root = original_root

    def test_production_source_change_requires_a_new_fragment(self) -> None:
        """#1324: a tools-only source change landed undocumented in the running log."""
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        source = self.root / "tools" / "subscriber-gateway" / "gateway.mjs"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("export const control = true;\n", encoding="utf-8")
        self.commit_all("change production source")

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "production source changes require a new NEXT fragment",
        ):
            changelog.check_pr(self.root, base, "HEAD")

    def test_production_source_change_with_a_new_valid_fragment_is_accepted(self) -> None:
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        source = self.root / "tools" / "subscriber-gateway" / "gateway.mjs"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("export const control = true;\n", encoding="utf-8")
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("change production source with a fragment")

        changelog.check_pr(self.root, base, "HEAD")

    def test_production_source_change_rejects_an_invalid_added_fragment(self) -> None:
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        source = self.root / "tools" / "gateway.mjs"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("export const control = true;\n", encoding="utf-8")
        # The only added path that is a fragment by policy yet never a valid
        # one: the archive is deliberately skipped by the enumerator.
        invalid = self.root / "NEXT" / "0000-archive.md"
        invalid.parent.mkdir(parents=True, exist_ok=True)
        invalid.write_text("no front matter\n", encoding="utf-8")
        self.commit_all("change production source with an invalid fragment")

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "production source changes require a new valid NEXT fragment",
        ):
            changelog.check_pr(self.root, base, "HEAD")

    def test_production_source_classification_covers_every_supported_stack(self) -> None:
        for path in (
            "tools/subscriber-gateway/gateway.mjs",
            "src/index.ts",
            "src/component.tsx",
            "lib/adapter.js",
            "lib/legacy.cjs",
            "scripts/deploy.sh",
            "scripts/deploy.bash",
            "scripts/deploy.ps1",
            "scripts/changelog.py",
            "cmd/server/main.go",
            "crates/core/src/lib.rs",
            "app/models/user.rb",
            "src/main/java/App.java",
        ):
            with self.subTest(path=path):
                self.assertTrue(changelog.is_production_source(path))

    def test_production_source_classification_covers_the_remaining_org_stacks(self) -> None:
        """#1324 review: `.mts`/`.cts` are the exact stack the @verjson/* packages ship."""
        for path in (
            "packages/core/src/index.mts",
            "packages/core/src/legacy.cts",
            "src/Verjson.Cloud/Program.cs",
            "db/migrations/0007_add_subscriptions.sql",
            "infra/network.tf",
            "android/app/src/main/Gateway.kt",
            "ios/Gateway/Client.swift",
            "web/public/index.php",
            "native/src/bridge.c",
            "native/src/bridge.cpp",
            "native/include/bridge.h",
            "web/src/Panel.vue",
            "web/src/Panel.svelte",
            "tools/Verjson.Deploy.psm1",
        ):
            with self.subTest(path=path):
                self.assertTrue(changelog.is_production_source(path))

    def test_every_supported_suffix_requires_a_fragment(self) -> None:
        """The classifier and check-pr cannot drift: every suffix is exercised end to end."""
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        sources = sorted(
            f"src/module{suffix}" for suffix in changelog.PRODUCTION_SOURCE_SUFFIXES
        )
        for relative in sources:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("production\n", encoding="utf-8")
        self.commit_all("change every supported production stack")

        with self.assertRaises(changelog.ChangelogError) as raised:
            changelog.check_pr(self.root, base, "HEAD")
        for relative in sources:
            with self.subTest(path=relative):
                self.assertIn(relative, str(raised.exception))

    def test_production_source_exemptions_are_explicit(self) -> None:
        for path in (
            "README.md",
            "docs/decisions/0038-canonical-changelog-contract/README.md",
            "docs/examples/bootstrap.sh",
            "NEXT/2026-07-30-issue-249-contract.md",
            "CHANGELOG/v1.0.0.md",
            "scripts/changelog.test.py",
            "scripts/ci-gate/native-automerge.test.sh",
            "scripts/render_changelog_adoption_issue_test.py",
            "tests/test_guidance_size.py",
            "src/__tests__/adapter.js",
            "src/adapter.spec.ts",
            "package.json",
            "Dockerfile",
            ".github/workflows/ai-review-merge.yml",
        ):
            with self.subTest(path=path):
                self.assertFalse(changelog.is_production_source(path))

    def test_test_exemption_is_anchored_to_a_test_root(self) -> None:
        """#1324 review: `tests`/`spec` at any depth exempted whole production subtrees."""
        for path in (
            "tests/guidance.py",
            "test/guidance.py",
            "spec/guidance.rb",
            "packages/api/tests/handler.ts",
            "apps/web/test/handler.ts",
            "src/__tests__/adapter.js",
            "src/deep/nested/__mocks__/client.ts",
        ):
            with self.subTest(exempt=path):
                self.assertFalse(changelog.is_production_source(path))
        for path in (
            "tools/test_runner.py",
            "packages/api/spec/handler.ts",
            "services/gateway/spec/handler.rb",
            "a/b/c/tests/helper.py",
            "src/testing/harness.ts",
        ):
            with self.subTest(production=path):
                self.assertTrue(changelog.is_production_source(path))

    def test_production_tooling_named_test_requires_a_fragment(self) -> None:
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        source = self.root / "tools" / "test_runner.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("RUNNER = True\n", encoding="utf-8")
        self.commit_all("change production tooling whose name begins with test_")

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "production source changes require a new NEXT fragment",
        ):
            changelog.check_pr(self.root, base, "HEAD")

    def test_workflow_definition_change_requires_a_fragment(self) -> None:
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        workflow = self.root / ".github" / "workflows" / "container-candidate.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("name: candidate\n", encoding="utf-8")
        self.commit_all("renovate action pin bump")

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "release-relevant changes require a new NEXT fragment",
        ):
            changelog.check_pr(self.root, base, "HEAD")

    def test_root_action_definition_requires_a_fragment(self) -> None:
        """#1324 review: a published action's entrypoint lives outside `.github/`."""
        for filename in ("action.yml", "action.yaml"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                original_root = self.root
                try:
                    self.root = Path(temporary)
                    self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
                    self.init_git()
                    self.commit_all("base")
                    base = run(self.root, "git", "rev-parse", "HEAD")
                    self.root.joinpath(filename).write_text(
                        "runs:\n  using: composite\n", encoding="utf-8"
                    )
                    self.commit_all("change the published action entrypoint")

                    with self.assertRaisesRegex(
                        changelog.ChangelogError,
                        "release-relevant changes require a new NEXT fragment",
                    ):
                        changelog.check_pr(self.root, base, "HEAD")
                finally:
                    self.root = original_root

    def test_nested_action_definition_is_release_relevant_config(self) -> None:
        self.assertTrue(changelog.is_release_relevant_change("vendor/action.yml"))

    def test_invalid_fragment_does_not_satisfy_a_config_change(self) -> None:
        """#1324 review: gating on added fragments let an unparseable one buy silence."""
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        workflow = self.root / ".github" / "workflows" / "container-candidate.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("name: candidate\n", encoding="utf-8")
        invalid = self.root / "NEXT" / "0000-archive.md"
        invalid.parent.mkdir(parents=True, exist_ok=True)
        invalid.write_text("no front matter\n", encoding="utf-8")
        self.commit_all("workflow change with an invalid fragment")

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "release-relevant changes require a new valid NEXT fragment",
        ):
            changelog.check_pr(self.root, base, "HEAD")

    def test_workflow_failure_is_not_downgraded_under_actions(self) -> None:
        """#1324 review: a bare stderr line from a step that exits 0 is invisible."""
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        workflow = self.root / ".github" / "workflows" / "container-candidate.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("name: candidate\n", encoding="utf-8")
        self.commit_all("renovate action pin bump")
        step_summary = self.root / "step-summary.md"
        step_summary.write_text("", encoding="utf-8")

        out = io.StringIO()
        err = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_STEP_SUMMARY": str(step_summary),
            },
        ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaisesRegex(
                changelog.ChangelogError,
                "release-relevant changes require a new NEXT fragment",
            ):
                changelog.check_pr(self.root, base, "HEAD")

        self.assertEqual("", out.getvalue())
        self.assertEqual("", err.getvalue())
        summary_text = step_summary.read_text(encoding="utf-8")
        self.assertEqual("", summary_text)

    def test_workflow_definition_change_with_a_fragment_reports_nothing(self) -> None:
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        workflow = self.root / ".github" / "workflows" / "container-candidate.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("name: candidate\n", encoding="utf-8")
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("documented workflow change")

        captured = io.StringIO()
        with mock.patch.dict(
            os.environ, {"GITHUB_ACTIONS": "false"}
        ), contextlib.redirect_stderr(captured):
            changelog.check_pr(self.root, base, "HEAD")
        self.assertEqual("", captured.getvalue())

    def test_ignore_lists_beside_a_dockerfile_are_not_dependency_manifests(self) -> None:
        """Reproduced in Verjson/verjson-ci: a build-context exclusion list pins nothing."""
        for path in (
            ".dockerignore",
            "deploy/.dockerignore",
            "deploy/subscriber-gateway/Dockerfile.dockerignore",
            "deploy/Containerfile.containerignore",
            "deploy/Dockerfile.gitignore",
        ):
            with self.subTest(path=path):
                self.assertFalse(changelog.is_dependency_file(path))

    def test_dockerfile_variants_remain_dependency_manifests(self) -> None:
        for path in (
            "Dockerfile",
            "Containerfile",
            "images/Dockerfile.pwsh",
            "images/worker.Dockerfile",
            "images/Containerfile.build.ci",
        ):
            with self.subTest(path=path):
                self.assertTrue(changelog.is_dependency_file(path))

    def test_dockerignore_is_not_a_dependency_but_still_requires_release_context(self) -> None:
        self.root.joinpath("Dockerfile").write_text("FROM node:22\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        ignore = self.root / "deploy" / "subscriber-gateway" / "Dockerfile.dockerignore"
        ignore.parent.mkdir(parents=True, exist_ok=True)
        ignore.write_text("node_modules\n", encoding="utf-8")
        self.root.joinpath(".dockerignore").write_text(".git\n", encoding="utf-8")
        self.commit_all("exclude build context noise")

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "release-relevant changes require a new NEXT fragment",
        ):
            changelog.check_pr(self.root, base, "HEAD")

    def test_dependency_change_with_a_new_valid_fragment_is_accepted(self) -> None:
        self.root.joinpath("package.json").write_text('{"version":"1.0.0"}\n', encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        self.root.joinpath("package.json").write_text('{"version":"1.0.1"}\n', encoding="utf-8")
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("dependency update with fragment")

        changelog.check_pr(self.root, base, "HEAD")

    def test_image_digest_update_requires_and_accepts_a_new_fragment(self) -> None:
        for filename, original, updated in (
            ("Dockerfile", "FROM node@sha256:" + "a" * 64 + "\n", "FROM node@sha256:" + "b" * 64 + "\n"),
            ("docker-compose.yml", "services:\n  api:\n    image: node@sha256:" + "a" * 64 + "\n",
             "services:\n  api:\n    image: node@sha256:" + "b" * 64 + "\n"),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                original_root = self.root
                try:
                    self.root = Path(temporary)
                    self.root.joinpath(filename).write_text(original, encoding="utf-8")
                    self.init_git()
                    self.commit_all("base image")
                    base = run(self.root, "git", "rev-parse", "HEAD")
                    self.root.joinpath(filename).write_text(updated, encoding="utf-8")
                    self.commit_all("image digest update")
                    with self.assertRaisesRegex(changelog.ChangelogError, "require a new NEXT fragment"):
                        changelog.check_pr(self.root, base, "HEAD")
                    fragment(self.root, "2026-07-30-issue-249-contract.md")
                    self.commit_all("record verified image change")
                    changelog.check_pr(self.root, base, "HEAD")
                finally:
                    self.root = original_root

    def test_dependency_change_cannot_reuse_an_existing_fragment(self) -> None:
        self.root.joinpath("package.json").write_text('{"version":"1.0.0"}\n', encoding="utf-8")
        existing = fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        self.root.joinpath("package.json").write_text('{"version":"1.0.1"}\n', encoding="utf-8")
        existing.write_text(existing.read_text(encoding="utf-8") + "\nMore.\n", encoding="utf-8")
        self.commit_all("dependency update reusing old fragment")

        with self.assertRaisesRegex(changelog.ChangelogError, "new NEXT fragment"):
            changelog.check_pr(self.root, base, "HEAD")

    def test_similar_names_are_not_dependencies_but_require_release_context(self) -> None:
        false_positives = (
            "package.json.md",
            "package-lock.yaml",
            "requirements.md",
            "Cargo.lock.backup",
            "terraform.lock.hcl",
            "docs/go.mod.md",
            "docs/Dockerfile.md",
            "docs/Dockerfile.MD",
            "Dockerfile.prod.markdown",
            "Dockerfile.backup",
            "Containerfile.dev.bak",
            "Dockerfile-guide",
            "notDockerfile",
            "docker-compose.yml.md",
            "docker-compose.production.yaml.backup",
            "docker-compose-guide.yaml",
            "compose-notes.yml",
            "deployment.yaml",
        )
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.init_git()
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        for relative in false_positives:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("not a dependency boundary\n", encoding="utf-8")
        self.commit_all("documentation and similarly named files")

        with self.assertRaisesRegex(
            changelog.ChangelogError,
            "release-relevant changes require a new NEXT fragment",
        ):
            changelog.check_pr(self.root, base, "HEAD")

    def test_check_pr_fails_closed_on_an_empty_revision(self) -> None:
        """An empty revision is refused, not silently reinterpreted as HEAD.

        `git diff "...HEAD"` resolves the blank side to HEAD, so an empty
        `--base` compares HEAD against itself: every requirement this gate
        exists to enforce evaluates over an empty diff and the gate exits 0.
        A caller that passed an unset variable must learn that, not be told
        the pull request is clean. The whitespace-only cases were already red
        before the guard, with a raw `fatal: ambiguous argument` from Git; the
        guard replaces that with a diagnosis. Only the two truly empty cases
        were silent passes.
        """
        self.init_git()
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.commit_all("base")
        self.root.joinpath("package.json").write_text("{}\n", encoding="utf-8")
        self.root.joinpath("CHANGELOG.md").write_text("authored\n", encoding="utf-8")
        self.commit_all("changes that a resolved base would refuse")

        for base, head in (("", "HEAD"), ("   ", "HEAD"), ("HEAD~1", ""), ("HEAD~1", "  ")):
            with self.subTest(base=base, head=head):
                with self.assertRaisesRegex(changelog.ChangelogError, "empty"):
                    changelog.check_pr(self.root, base, head)

    def test_validate_fails_closed_on_an_empty_base(self) -> None:
        """The sibling gate shares the shape: a blank `--base` is not "omitted".

        `validate --base ''` used to be indistinguishable from omitting the
        option, so a caller whose BASE_SHA was unset skipped the new-fragment
        `impact` requirement entirely and exited 0. Omitting it stays
        legitimate; passing an empty value does not.
        """
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-contract.md")
        self.commit_all("base")

        completed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "validate",
                "--repo-root",
                str(self.root),
                "--base",
                "",
            ],
            check=False,
            text=True,
            capture_output=True,
        )

        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("--base is empty", completed.stderr)

    def test_a_resolvable_revision_still_passes_silently(self) -> None:
        """The control side: the guard refuses blanks without refusing real work.

        A pull request that needs no fragment must still exit 0 with nothing on
        either stream, and `validate` with `--base` genuinely omitted must stay
        the legitimate skip it has always been.
        """
        self.init_git()
        fragment(self.root, "2026-07-30-issue-249-contract.md", impact="patch")
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.commit_all("base")
        base = run(self.root, "git", "rev-parse", "HEAD")
        self.root.joinpath("README.md").write_text("documentation only\n", encoding="utf-8")
        fragment(
            self.root,
            "2026-07-31-issue-250-documentation.md",
            date="2026-07-31",
            issue="250",
            impact="patch",
        )
        self.commit_all("documentation")

        for argv in (
            ["check-pr", "--base", base, "--head", "HEAD"],
            ["validate", "--base", base, "--head", "HEAD"],
            ["validate"],
        ):
            with self.subTest(argv=argv):
                completed = subprocess.run(
                    [sys.executable, str(MODULE_PATH), argv[0], "--repo-root", str(self.root), *argv[1:]],
                    check=False,
                    text=True,
                    capture_output=True,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr, "")

    def test_an_option_shaped_revision_is_refused_before_git_sees_it(self) -> None:
        """A revision that begins with a dash is an option to Git, not a revision.

        `argparse` rejects `--base --output=x` but passes `--base=--output=x`
        straight through, and `git diff` then consumes it as an option: it
        writes the named file, reports no changes, and the gate exits 0 over an
        empty diff — the same fail-open as a blank revision, reached through a
        value that is not blank. The refusal must come from this boundary, so
        Git is never handed the value at all.
        """
        self.init_git()
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.commit_all("base")
        self.root.joinpath("CHANGELOG.md").write_text("authored\n", encoding="utf-8")
        self.commit_all("a change this gate exists to refuse")
        marker = self.root / "written-by-git.txt"

        for option in ("--base", "--head"):
            with self.subTest(option=option):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(MODULE_PATH),
                        "check-pr",
                        "--repo-root",
                        str(self.root),
                        f"{option}=--output={marker}",
                        "--head" if option == "--base" else "--base",
                        "HEAD~1",
                    ],
                    check=False,
                    text=True,
                    capture_output=True,
                )

                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertIn("looks like an option", completed.stderr)
                self.assertFalse(marker.exists(), "git was handed the option anyway")

    def test_check_pr_fails_closed_on_malformed_revision_input(self) -> None:
        self.init_git()
        self.root.joinpath("README.md").write_text("base\n", encoding="utf-8")
        self.commit_all("base")

        with self.assertRaises(changelog.ChangelogError):
            changelog.check_pr(self.root, "not-an-api-sha", "HEAD")

    def test_released_snapshots_use_natural_version_order(self) -> None:
        snapshots = self.root / "CHANGELOG"
        snapshots.mkdir()
        (snapshots / "v1.9.0.md").write_text("nine\n", encoding="utf-8")
        (snapshots / "v1.10.0.md").write_text("ten\n", encoding="utf-8")
        (snapshots / "v1.10.0-rc.1.md").write_text("candidate\n", encoding="utf-8")

        rendered = changelog.render_released(self.root)

        self.assertLess(rendered.index("# v1.10.0"), rendered.index("# v1.9.0"))
        self.assertLess(
            rendered.index("# v1.10.0"),
            rendered.index("# v1.10.0-rc.1"),
        )

    def test_release_workflow_serializes_and_does_not_cancel(self) -> None:
        workflow = (
            MODULE_PATH.parent.parent / ".github/workflows/changelog-release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "group: ${{ (github.event_name == 'pull_request' || "
            "github.event_name == 'pull_request_review' || "
            "github.event_name == 'pull_request_review_comment' || "
            "github.event_name == 'merge_group' || "
            "github.event_name == 'pull_request_target') && "
            "github.event.repository.private != true && "
            "format('public-rejected-{0}-{1}-{2}', github.repository, github.run_id, github.run_attempt) || "
            "format('changelog-release-{0}', github.repository) }}",
            workflow,
        )
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("contract_ref:", workflow)
        self.assertIn("ref: ${{ inputs.contract_ref }}", workflow)
        self.assertIn("COMPONENT: ${{ inputs.component }}", workflow)
        self.assertIn("selection_digest:", workflow)
        self.assertIn("PREFIX: ${{ inputs.prefix }}", workflow)
        self.assertIn("selection-digest --repo-root", workflow)
        self.assertIn("EXPECTED_SELECTOR_DIGEST", workflow)
        self.assertIn('args+=(--component "$COMPONENT")', workflow)
        self.assertNotIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("inputs.runner != ''", workflow)

        validation_workflow = (
            MODULE_PATH.parent.parent / ".github/workflows/changelog-validate.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("runs-on: ubuntu-latest", validation_workflow)
        self.assertIn("inputs.runner != ''", validation_workflow)

    def test_release_snapshots_the_dispatch_commit_not_the_branch_head(self) -> None:
        """The snapshot must take the commit the caller verified.

        Checking out ``github.event.repository.default_branch`` re-reads the
        branch head here, at snapshot time. A caller's ``verify`` job holds that
        window open for its whole suite run, and the ``cancel-in-progress:
        false`` concurrency group holds it open again behind a queued release,
        so anything merged in between used to be tagged, pushed and published
        with nothing having checked it (#463, #464).
        """
        workflow = (
            MODULE_PATH.parent.parent / ".github/workflows/changelog-release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("ref: ${{ github.sha }}", workflow)
        # Not just "the pin is present": the branch-head re-read must be absent,
        # or a second checkout step reinstating it passes the assertion above.
        # Scoped to the `ref:` key, because DEFAULT_BRANCH legitimately binds
        # the same expression for the guard and the push refspec below.
        self.assertNotIn("ref: ${{ github.event.repository.default_branch }}", workflow)
        self.assertIn(
            "DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}",
            workflow,
        )
        # The push still targets the branch by name, which is what makes the pin
        # fail closed: from the dispatch commit this refspec is non-fast-forward
        # once the branch has moved, so a raced release tags nothing.
        self.assertIn('"$release_commit:refs/heads/$DEFAULT_BRANCH"', workflow)
        # The checkout is detached at the dispatch commit, so nothing may derive
        # the branch from HEAD.
        self.assertNotIn("git symbolic-ref", workflow)


if __name__ == "__main__":
    unittest.main()
