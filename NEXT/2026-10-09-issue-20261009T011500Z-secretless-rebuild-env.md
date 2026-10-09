---
date: 2026-10-09
id: 20261009T011500Z
impact: patch
title: Allow ONNX Runtime binary skip in secretless rebuilds
---
The reusable Node CI workflow allows only `ONNXRUNTIME_NODE_INSTALL=skip` when
`onnxruntime-node` is explicitly approved for credentialless lifecycle rebuild.
The raw input is removed before npm runs, so caller values cannot pass through
to lifecycle code.
