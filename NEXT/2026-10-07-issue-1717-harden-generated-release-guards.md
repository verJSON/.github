---
date: 2026-10-07
issue: 1717
impact: patch
title: Harden generated release guards
---

Generated release callers pass ShellCheck, and the consumer contract test now rejects skipped or non-blocking verification and checkout steps that persist credentials.

The verifier pins its generated command and selected-version condition. Its PATH is captured before lifecycle scripts and isolated during verification; pre-credential step fields and command bodies are pinned, and workflow, verify-job, and dependency-install environments are allowlisted. Credentialed npm installation rejects workspace-root `.npmrc` files and symlinks and pins its working directory. Unsupported or ambiguous environment mappings and explicit YAML mapping keys fail closed. Checkout inputs are read from top-level YAML mappings, including inline and quoted forms, so nested text cannot satisfy the credential check.
