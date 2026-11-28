---
date: 2026-11-28
id: 20261128T061642Z
refs: 1954
title: Require operation proof before signing release snapshot receipts
impact: patch
---

Require the protected issuer to match a verified atomic push to its recorded
intent and a fresh policy before signing. An unresolved push or a preexisting
tag leaves issuance held. Part of #1954.
