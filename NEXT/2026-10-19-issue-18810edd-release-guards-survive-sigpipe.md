---
date: 2026-10-19
id: 18810edd
impact: patch
title: Pin the generated release snapshot inputs exactly and keep the credential guards closed for large job blocks
---

The generated adopter contract test now pins the snapshot job's `changelog-release.yml` inputs to the generator's mapping, key for key and value for value. It also no longer lets a large job block hide a forwarded credential. Previously a hand-edited caller could change those inputs and still pass, by forwarding a credential through an unlisted spelling or by passing an unverified version or prefix. And when a credential reference came early in a job block bigger than a pipe, the snapshot guard accepted it and the build-job guard was bypassed (ADR 0099's 2026-10-19 amendment).

The two size guards now capture the comment-stripped block before grepping it, so SIGPIPE can no longer turn a match into a pass. The release-shape expression scan, which covers the snapshot, build, verify, and acquisition jobs, now reads raw lines. A `#` inside a block scalar, a multi-line quoted scalar, or after an escaped quote no longer hides an expression from it. The scan also treats an object filter over the `github` context as a whole-context read. All three release caller modes write the snapshot inputs from one generator definition, and the contract test pins that same definition.

Residual: the scan is textual and does not decode YAML escapes. A double-quoted escape that decodes to an expression opener still passes it in the scanned jobs. The pinned snapshot inputs are not affected. Pinning the build job whole would close the residual there.
