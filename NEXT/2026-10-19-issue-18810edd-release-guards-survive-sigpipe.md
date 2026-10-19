---
date: 2026-10-19
id: 18810edd
impact: patch
title: Pin the generated release snapshot and build jobs whole and keep the credential guards closed for large job blocks
---

The generated adopter contract test now pins the release callers' snapshot job and `release-artifact` build job whole, against the same generator definitions that write them. It also refuses YAML whose decoded value could differ from its raw text. A hand-edited caller can no longer forward a credential, add a step, or change an input in those jobs, however it is spelled. It also can no longer hide a credential reference in an oversized job block (ADR 0099's 2026-10-19 amendment).

Both jobs are generator-determined, so they are compared field by field and step by step, with run bodies included. The build job's runner matrix is the one variable part, and each entry must match the generator's runner grammar. YAML comments and blank lines are ignored. A `#` line inside a block scalar is content and is compared. One uniform indentation shift of the job body is tolerated. Generated callers are byte-identical before and after this change. The expression scan reads raw lines in the verify, snapshot, build, and acquisition jobs, and treats an object filter over the `github` context as a read of the whole context. A file-wide parse rule refuses backslashes outside block scalars and comments, and refuses quoted scalars left open across lines, so escapes cannot spell an expression the scan misses. The two size guards capture each job block before grepping it, so SIGPIPE cannot turn a match into a pass.

Residual: the `publish` job holds publication credentials by design and keeps its existing per-field checks, so it is not pinned whole.
