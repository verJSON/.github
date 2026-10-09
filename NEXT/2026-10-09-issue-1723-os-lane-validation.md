---
date: 2026-10-09
issue: 1723
impact: patch
title: Validate pinned OS runner lanes before release snapshots
---
Generated release callers now reject trusted macOS and Windows lane values that
use the wrong OS family, rolling `latest` segments, or padded labels before the
snapshot job can run. Contract tests execute the exact generated preflight for
valid, invalid, empty, and malformed selectors while preserving the supported
array shape and acquisition/build matrix pairing.
