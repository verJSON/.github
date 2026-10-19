---
date: 2026-10-19
id: 18810edd
impact: patch
title: Pin the generated release snapshot and build jobs and job set, and keep the credential guards closed for large job blocks
---

The generated adopter contract test now pins the release callers' snapshot job, `release-artifact` build job, and job set to the generator's own definitions. It also refuses YAML that its parser-free scanner cannot read the way Actions would. A hand-edited caller can no longer forward a credential, add a step, change an input or add a job. It can no longer hide an expression behind a YAML escape, tag, anchor, or line-break character, or in an oversized job block (ADR 0099's 2026-10-19 amendment).

The snapshot and build jobs are generator-determined. The suite compares them with the generator's definitions field by field and step by step, run bodies included. The build job's runner matrix is the one variable part, and each entry must match the generator's runner grammar and dependency index. YAML comments, blank lines, and one uniform reindent are ignored. A `#` line inside a block scalar is content and is compared. The job ids must be exactly the generator's set for the caller's mode. Duplicate keys and job-level `environment` are refused.

The file-wide scanner refuses backslashes outside block scalars and comments. It also refuses quoted scalars or flow collections left open at a line end, doubled single quotes, tags, anchors, aliases, document markers, tabs in indentation, block-scalar indentation indicators, and characters that `str.splitlines()` and YAML 1.2 read differently. What remains differs from its decoded value only by quotes, indentation and line folding, so every expression and context name Actions sees is in the text the expression scan reads. That scan covers the verify, build and acquisition jobs, and treats an object filter over `github` as a read of the whole context.

The generator now fails, with no output, when a function it interpolates into a caller fails. Bash used to ignore that status inside a here-document. The two size guards capture each job block before grepping it, so SIGPIPE cannot turn a match into a pass. Generated callers are byte-identical before and after.

Residual: the `publish` job holds publication credentials by design and keeps its existing per-field checks.
