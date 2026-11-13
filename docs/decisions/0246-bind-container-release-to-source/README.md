# 0246 — Ecosystem source-bound container release proposal (relocated)

- **Date:** 2026-10-28
- **Status:** Relocated proposal; not accepted
- **Issue:** [verJSON/.github#1949](https://github.com/VerJSON/.github/issues/1949)
- **Category:** release identity and provenance

The proposed cross-repository governing decision is in [verjson-docs ADR 0015, draft PR #35](https://github.com/VerJSON/verjson-docs/pull/35), under the published [decision-placement standard](https://github.com/VerJSON/verjson-docs/blob/782362bfca9eaf89ede2c8be770e708d45b0831d/governance/decision-placement.md). This path remains a stable pointer to the proposal and its history. Neither proposal is ratified. No workflow, registry, signer, Git ref, credential, or release behavior changes through this pointer.

The [full proposal at commit eb18c96c](https://github.com/VerJSON/.github/blob/eb18c96c2dbed3252c0d09166f608a1802e3f02a/docs/decisions/0246-bind-container-release-to-source/README.md) preserves the original source, fragment, artifact, and recovery design. The [2026-11-13 ARB record](https://github.com/VerJSON/.github/pull/1950#issuecomment-6856199213) approved a prerequisite slate, not ADR 0246 or release authority; it also records the decision-placement finding and the corrected limit of the current tag-signing evidence.

The interim source-equality guard from #1952 is distinct from authenticated dispatch, complete fragment selection, post-snapshot receipt issuance, and safe registry/Git recovery. The #1961 inspector grants no publication permission. Those remaining requirements are proposed in ecosystem ADR 0015 and need owner ratification and integration evidence before implementation can claim them.
