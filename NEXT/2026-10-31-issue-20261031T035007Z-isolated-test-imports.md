---
date: 2026-10-31
id: 20261031T035007Z
impact: patch
title: Resolve connector tests under isolated Python
---

Make the compatibility test modules import their checked-out helpers when
Python isolated mode omits the script directory. Part of #1966.
