---
date: 2026-10-19
id: 18810edd
impact: patch
title: Pin the generated release snapshot, build and publish jobs, workflow keys and job set, and keep the credential guards closed for large job blocks
---

The generated adopter contract test now pins the release callers' snapshot job, `release-artifact` build job, `publish` job, workflow keys and job set to the generator's own definitions. It refuses YAML that its parser-free scanner cannot read the way Actions would. A hand-edited caller can no longer change the snapshot, build or publish job, add a workflow key, or add a job. Outside the existing approved steps, a read of the token or `secrets` context at workflow scope or in the verify, build or acquisition jobs is refused, including one in a bare `if:` condition. YAML the scanner cannot follow is refused too, so a comment, an escape, a block scalar or an oversized job block can no longer hide such a read (ADR 0099's 2026-10-19 amendment).

The snapshot, build and publish jobs are generator-determined. They are compared field by field and step by step, run bodies included. The build job's runner matrix is one variable part, and its entries must match the generator's runner grammar and dependency index. The other is `release-node`'s `package-dirs` publish input, which must equal the caller's pinned package-directory selection. YAML comments, blank lines and one uniform reindent are ignored. A `#` line inside a block scalar is content and is compared. Workflow keys must be exactly `name`, `run-name`, `concurrency`, `on`, `permissions` and `jobs`, compared decoded so that `"env":` and `env :` are refused. Job ids must be exactly the generator's set for the caller's mode. Duplicate keys and job-level `environment` are refused.

The file-wide scanner refuses the following:

- backslashes outside block scalars and comments;
- quoted scalars or flow collections left open at a line end;
- doubled single quotes;
- tags, anchors and aliases;
- document markers;
- tabs in indentation;
- block-scalar indentation indicators;
- a block-scalar header with no key or sequence entry on its own line;
- an expression in a mapping key or flow collection;
- an expression a scalar leaves open;
- characters that `str.splitlines()` and YAML 1.2 read differently.

A block scalar's body is the lines indented past the key or sequence entry its header follows, quoted keys included. Whether an expression closed is recorded where the scan finds its `}}`, not inferred from the text ending in braces.

The expression scan reads the file with every YAML comment blanked to spaces, including a comment after a block-scalar header. An earlier version read the comments too, so a quote inside a comment could hide the next line's value from it. Workflow scope is every line outside the jobs block, before or after it. Every `if:` value is read as expression code, because Actions evaluates it with or without `${{ }}`. The scan treats an object filter over `github` as a read of the whole context.

The generator now fails, with no output, when a function it interpolates into a caller fails. Bash used to ignore that status inside a here-document. The two size guards capture each job block before grepping it, so SIGPIPE cannot turn a match into a pass. Generated callers are byte-identical before and after.

Residual: these controls cover context reads, the workflow keys, the job set and the pinned jobs, not every route by which a hand-edited caller could pass a credential.
