---
date: 2026-11-04
id: 20261104T225628Z
refs: 1997
impact: patch
title: Run the container coexistence suite once per CI group run
---
`container-contract-coexistence.test.sh` regenerates all three container contracts. It ran from both `container-candidate-contract.test.sh` and `container-release-contract.test.sh`, which share the `changelog-release-2` CI group, so each run of that group ran it twice. It now runs once, from the release suite, which saves about 0.5 seconds per run of the group. No assertion is removed or weakened.
