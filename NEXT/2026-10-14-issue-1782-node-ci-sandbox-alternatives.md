---
date: 2026-10-14
issue: 1782
impact: patch
title: Let Node CI sandboxes run alternatives-managed system tools
---
The credentialless Node CI sandboxes start from an empty root and bind `/usr`, but on Debian and Ubuntu `/usr/bin/awk` (also `cc`, `c++`, `which`, and other names) is a symlink into `/etc/alternatives`. Since #1729 moved consumer scripts, lifecycle rebuilds, and compatibility lanes into those sandboxes, these names dangled and scripts calling `awk` failed with `command not found`. The canonical and protected Node CI workflows now bind `/etc/alternatives` read-only in every such sandbox. That directory holds only symlinks, so the sandboxes expose no new credentials or writable paths.
