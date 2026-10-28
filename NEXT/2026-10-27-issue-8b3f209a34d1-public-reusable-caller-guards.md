---
date: 2026-10-27
id: 8b3f209a34d1
impact: patch
title: Harden public reusable workflow caller guards
---
Public reusable workflows now reject all five PR-related events from public
repositories before privileged work can succeed by skipping. The isolation
scanner evaluates those same events through nested local calls, public `verjson-ci`
uses a hosted runner unconditionally, and post-merge reconciliation accepts only
the exact canonical legacy/current dispatcher paths during migration. Bootstrap
validation recognizes a finite set of historical generated callers only for the
protected base; proposed callers must use the current generator. See #1831.
