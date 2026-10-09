---
date: 2026-10-09
id: 20261009T011500Z
impact: patch
title: Allow safe environment overrides for secretless lifecycle rebuilds
---
The reusable Node CI workflow now accepts bounded, validated environment values
for credentialless lifecycle-package rebuilds while rejecting credential,
npm-config, shell, and process-control variables. Consumers can skip optional
ONNX Runtime CUDA binary downloads without adding a repository `.npmrc`.
