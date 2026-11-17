#!/usr/bin/env python3
"""Fail-closed tests for the read-only agent-delivery claim ledger (#2212)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Static imports so ADR 0193 counts this test as an importer of the NON_GATE ledger.
import agent_delivery_claim as CLAIM
import agent_delivery_claim_ledger as MODULE

REPO = "Verjson/demo"
OTHER = "Verjson/other"
PR = 42
HEAD = "a" * 40
TRUSTED = "github-actions[bot]"
RUN_ID = "1234567890"
WORKFLOW = ".github/workflows/agent-delivery-claim.yml"


def trusted_comment(claim: CLAIM.Claim) -> dict:
    return {
        "id": 1,
        "body": CLAIM.format_claim_receipt(claim),
        "user": {"login": TRUSTED},
    }


class ClaimLedgerTest(unittest.TestCase):
    def claim(self, *, repo=REPO, pr=PR, head=HEAD, claim_id="1" * 32) -> CLAIM.Claim:
        return CLAIM.Claim(
            claim_id=claim_id,
            repository=repo,
            pull_request=pr,
            head_sha=head,
            status="claimed",
            run_id=RUN_ID,
            workflow_path=WORKFLOW,
        )

    def test_lists_trusted_active_claims_across_repositories(self):
        a = self.claim(claim_id="1" * 32)
        b = self.claim(repo=OTHER, pr=7, head="b" * 40, claim_id="2" * 32)
        claims = MODULE.list_active_claims(
            [
                (REPO, PR, [trusted_comment(a)]),
                (OTHER, 7, [trusted_comment(b)]),
            ]
        )
        self.assertEqual([c.claim_id for c in claims], ["1" * 32, "2" * 32])

    def test_ignores_untrusted_authors_and_cross_pr_forgeries(self):
        real = self.claim()
        forged = self.claim(repo=OTHER, pr=99, claim_id="9" * 32)
        comments = [
            {
                "body": CLAIM.format_claim_receipt(real),
                "user": {"login": "attacker"},
            },
            {
                "body": CLAIM.format_claim_receipt(forged),
                "user": {"login": TRUSTED},
            },
        ]
        claims = MODULE.list_active_claims([(REPO, PR, comments)])
        self.assertEqual(claims, [])

    def test_deduplicates_the_same_triple(self):
        claim = self.claim()
        comments = [trusted_comment(claim), trusted_comment(claim)]
        claims = MODULE.list_active_claims([(REPO, PR, comments)])
        self.assertEqual(len(claims), 1)

    def test_format_ledger_has_no_secrets_and_marks_visibility_only(self):
        body = MODULE.format_ledger([self.claim()])
        self.assertIn("Not a claim authority", body)
        self.assertIn(REPO, body)
        self.assertIn(RUN_ID, body)
        lowered = body.lower()
        for forbidden in ("token", "secret", "password", "authorization:", "bearer "):
            self.assertNotIn(forbidden, lowered)

    def test_malformed_source_repository_fails_closed(self):
        with self.assertRaisesRegex(Exception, r"repository identity is malformed"):
            MODULE.list_active_claims([("not a repo", PR, [])])


if __name__ == "__main__":
    unittest.main()
