# 0082 — Receipt-bound provider and budget policy

- **Date:** 2026-08-08
- **Status:** Accepted
- **Issue:** [#655](https://github.com/Verjson/.github/issues/655)
- **Amends:** [ADR 0080](../0080-one-automatic-paid-ai-review-per-head/README.md)

## Context

One automatic paid review per head bounds call count but does not let operators
choose different cost policies for automatic review and an intentional second
opinion. Reading organization variables only when the model starts would also
let a mid-run edit change the authorized provider or ceiling.

Anthropic supplies a native dollar budget. The OpenAI Responses API supplies a
token cap, not a native dollar cap, so a dollar ceiling needs a conservative,
versioned conversion. The [Luna model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-luna),
[API pricing](https://developers.openai.com/api/docs/pricing), and
[Codex GitHub integration](https://developers.openai.com/codex/github-action)
are the upstream references.

## Decision

The trusted arm selects primary organization variables for a new head and
secondary variables only for a maintainer-applied `re-review`. The primary
defaults retain the existing Anthropic classifier (`anthropic`, `auto`,
`auto`). An absent secondary policy fails before dispatch. Provider, model,
budget, and pricing-table version are written into the immutable exact-head arm
receipt as one canonical policy object, forwarded as one dispatch input, and
verified before the model runs. An explicit re-review additionally binds the
requesting actor and their `maintain` or `admin` permission. The verifier
requires that permission again immediately before model execution, so a
revocation between arm and spend fails closed.

Anthropic continues to receive its native `--max-budget-usd`. The only supported
OpenAI policy is `gpt-5.6-luna` on explicit re-review. It performs one tool-free
Responses request with strict structured output and no retry, continuation, or
fallback. Pricing table `openai-luna-long-context-2026-08-08` uses Luna's
uncached long-context rates of $0.40/MTok input and $1.80/MTok output (the
documented multiplier above 272K input tokens). Complete input UTF-8 bytes conservatively
bound input tokens; the remaining budget determines `max_output_tokens`.
Budgets unable to cover 1,024 output tokens fail before the network. Reported
usage must remain within the preflight input/output and dollar envelope.
The request embeds bounded UTF-8 `pr.json` metadata and the complete filtered
`pr.diff` as JSON-encoded untrusted data in a user-role item. A separate
developer-role item explicitly forbids following any payload instructions,
role claims, delimiters, or approval requests. PR-controlled bytes never enter
the trusted instruction item. The final serialized role-separated envelope is
what the preflight prices. A response is evidence only when the top-level response,
single assistant message, and output item are completed for the exact requested
model, contain no refusal/error/incomplete state, and pass local strict schema
and verdict-semantic validation.

Credentials remain step-scoped: the Anthropic action receives only Anthropic
credentials and the trusted OpenAI client receives only `OPENAI_API_KEY`.
Unknown providers, models, pricing versions, malformed budgets/responses, or
usage evidence fail closed.

### 2026-08-24 correction ([#1030](https://github.com/Verjson/.github/issues/1030))

The immutable `a6b3ccc0590f4fcfdacd7818279ab3eea6b30155` generated caller and reusable
review workflow accept only the original six-field envelope, but the arm at that same
revision emitted the later authority/fallback envelope. That internally inconsistent
pin cannot bootstrap its own generated-caller upgrade.

The current trusted arm may project its policy back to the six-field schema only when
the target default branch contains the byte-for-byte generated caller whose SHA-256 is
`39d9325e5a13b6f1cafb3237a920d1737f9e74b905e70e3ec0fcd5955a7679d6`.
The exception accepts only ordinary Anthropic policy with human merge authority, no
fallback, and no explicit re-review. Any byte change, different policy, missing caller,
or read failure keeps the current schema or fails before dispatch. The exact caller
digest, protected default-branch contents, trusted arm revision, and App-owned
exact-head receipt are jointly the compatibility boundary; PR-controlled bytes cannot
select it. This is a recovery bridge for one immutable generated artifact, not schema
negotiation and not a PAT or check-forging path.

### 2026-08-08 correction ([#657](https://github.com/Verjson/.github/issues/657))

The canonical JSON object is serialized with sorted keys and no insignificant
whitespace, then crosses Actions outputs and `workflow_dispatch` only as an
unpadded base64url envelope. Every trusted consumer rejects padding, non-URL
alphabet bytes, oversized envelopes, duplicate/extra/missing/non-string fields,
and non-canonical JSON or encoding before it reads any policy value. The receipt
binds that exact opaque envelope. This corrects the original transport choice:
raw JSON interpolated into a generated shell command can lose its quotes even
when the JSON itself was canonical at the producer.

## Consequences

Operators can deliberately buy a differently modeled second opinion without
ordinary CI or automation selecting it. Adding a model or changing rates
requires a code-reviewed pricing-table version rather than optimistic caller
inputs. The byte bound can underutilize a budget for non-ASCII or token-dense
input; that conservatism is the cost of a hard ceiling without a tokenizer.

## 2026-10-15 refinement — budgets are ASCII amounts in every locale

This ADR requires malformed budgets to fail closed. The budget guards in `ai-review-merge.yml` and `gate-rearm.yml` used `[1-9]`/`[0-9]` bash ranges, which follow locale collation. Under en_US.UTF-8 they admitted Arabic-Indic and fullwidth digits, and the follow-up `awk 'b > 0'` then compared the value as a string and accepted it. All four guards now spell `^(0|[123456789][0123456789]{0,2})(\.[0123456789]{1,2})?$`, which caps a budget at 999.99, and compare `b + 0 > 0` numerically. `scripts/ci-gate/settable-numeric-guards.test.py` runs each workflow's own guard block under C.UTF-8 and en_US.UTF-8. The same change enumerates the AI review App, installation, check, and pull-request ID guards. `gate-rearm.yml` runs from organization ruleset 20722935's stored SHA, so its half of this fix takes effect only after that ruleset is rotated to the merge commit. Recorded in [ADR 0220](../0220-confine-release-credentials-to-dependency-acquisition/README.md)'s 2026-10-15 refinement with the rest of the defect class.
