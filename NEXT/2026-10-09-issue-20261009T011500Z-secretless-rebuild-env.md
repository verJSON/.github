---
date: 2026-10-09
id: 20261009T011500Z
impact: patch
title: Allow ONNX Runtime binary skip in secretless rebuilds
---
The reusable Node CI workflow allows only `ONNXRUNTIME_NODE_INSTALL=skip` when
`onnxruntime-node` is explicitly approved for credentialless lifecycle rebuild.
It replaces Bash and the validator before starting npm or Corepack, and removes
credentials, raw caller input, and GitHub Actions command-file paths from the
package-manager environment. Regression coverage exercises npm and pnpm and
proves lifecycle code cannot use `GITHUB_ENV` to inject `BASH_ENV` into a later
token-bearing step.
