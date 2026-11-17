# ARB chair record — custody / credential ARB (2026-11-17)

## Standards grounding

- status: grounded
- config_sha: `8dad33f78f0cfe11382f099763b7f16a41cd1468`
- manifest_sha256:
  `b1c472121a393697ce47de490580fbf5c95a79569feed89847bbc1896447f2dc`

## Charter

- **Run id:** `arb-custody-cred-2185-2026-11-17`
- **Issues:**
  [verJSON/verjson-agent-identity#72](https://github.com/verJSON/verjson-agent-identity/issues/72),
  [verJSON/verjson-agent-identity#73](https://github.com/verJSON/verjson-agent-identity/issues/73),
  [verJSON/.github#2185](https://github.com/verJSON/.github/issues/2185)
- **Question:** Given landed OpenBao/archive packaging canaries, flip
  `keyCustodySupport`? Enable credential release? Drop
  `require_code_owner_review` under ADR 0271?
- **Pins:** identity `d3a4cd15cc766026d3203fdae802c47f6d7e587f`,
  infra `2708950744a877d049566ea4678ee1f388b660e4`,
  .github `ee9899ba3803ed95f5883bd65a30952777f4ce7b`
- **Roster:** Verified CLI empty this session (`discover_providers.py`
  roster). Advisory seats A (Composer), B (Grok), C (Grok) all returned
  durable ballots. Decision rule: unanimous among returned advisory seats
  (same pattern as ADR 0271 when CLI quorum unavailable).
- **Status:** `APPROVED_WITH_CONSTRAINTS`
- **Slate digest (SHA-256):**
  `0566372c5e9a0ce9d0403ec1068e0a0bdf903f37990fd7b56c1ae4606a7f0e8e`
  over:

  ```json
  {"issues":["verJSON/verjson-agent-identity#72","verJSON/verjson-agent-identity#73","verJSON/.github#2185"],"items":["S-1","S-2","S-3"],"run_id":"arb-custody-cred-2185-2026-11-17","status":"APPROVED_WITH_CONSTRAINTS"}
  ```

- **Ratification:** Owner ARB-delegation 2026-11-16/17 (ARB, not personal
  CODEOWNERS, is approval authority per ADR 0271 / ADR 0202).

## Adopted slate

- **S-1 flip_now:** Change identity-library `keyCustodySupport` to
  `supported` citing infra OpenBao 2.7.2 custody canary receipt.
  Credential release stays disabled. Track G-3 second-adapter
  conformance as follow-on
  [verJSON/verjson-agent-identity#75](https://github.com/verJSON/verjson-agent-identity/issues/75).
- **S-2 keep_disabled:** Do not enable credential release / HTTP ready
  until G-7 live runtime-issued acceptance, G-8 enforcement-point suite,
  and G-5 named production archive operators (+ durable receipt) are
  evidenced.
- **S-3 keep:** Keep `require_code_owner_review` on `main-protection`.
  ADR 0271 non-goals and ADR 0210 plumbing require an explicit successor
  ADR to drop it.

## Ballots (advisory)

| Seat | S-1 | S-2 | S-3 |
| --- | --- | --- | --- |
| A (Composer) | flip_now | keep_disabled | keep |
| B (Grok) | flip_now | keep_disabled | keep |
| C (Grok) | flip_now | keep_disabled | keep |

## Integration constraints

1. **scope:** G-3 full acceptance. **vulnerability:** Second custody
   adapter conformance still open after OpenBao packaging canary.
   **remediation_track:**
   [verJSON/verjson-agent-identity#75](https://github.com/verJSON/verjson-agent-identity/issues/75).
2. **scope:** Credential release
   ([verJSON/verjson-agent-identity#73](https://github.com/verJSON/verjson-agent-identity/issues/73)).
   **vulnerability:** G-5/G-7/G-8 gaps. **remediation_track:** Keep #73
   open with gate checklist; no enablement PR.

## Dissent

None. Advisory seats A/B/C unanimous on the adopted slate. Follow-ons
above are constraints, not blocking defects for archive publication.
Related identity PR:
[verJSON/verjson-agent-identity#74](https://github.com/verJSON/verjson-agent-identity/pull/74).
