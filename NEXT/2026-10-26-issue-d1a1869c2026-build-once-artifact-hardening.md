---
date: 2026-10-26
id: d1a1869c2026
refs: 1869
impact: patch
title: Harden same-run build artifact verification and selection
---
Part of #1869. Harden the canonical same-run ArtifactSet parser against GNU sparse members, sparse or size-overriding PAX records, malformed framing, hidden padding records, and excessive extension chains. Caller selection now uses exact JSON booleans, the always-run aggregate rejects missing or malformed evidence, and consumer TestReceipts require both verified artifacts and successful candidate-plan execution. The checked-in policy remains version 1 with no repositories; this change does not enroll an adopter or claim an outside-Verjson canary or runner-minute savings.
