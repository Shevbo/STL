# Recent

## 2026-09-29
Deployed L2 resub (4x load reduction); fixed double-guard smart orders (1256 tests) + guaranteed execution impl; SMS monitoring 24/7; critical QUIK fixes; rev 1790712787 (1319 tests).

## 2026-09-28
Real-trade rev 1790586989 deployed (pong/book_codes, usopen params); tracked lxk22 stop escalation (1%→83896); flip_close_loss ban −25.3k gain.

## 2026-09-27
Debug entry_reasons loss (_EXTRA_KEYS); fixed Lua subscriptions (once per lifetime); rev 1790457130 L2 resub w/ auto-catch deployment.

## Identity Candidates
- IDENTITY CANDIDATE: Building state-tracking infrastructure (truth.py, pos.py) to diagnose and prevent production issues (STL stops, silent restarts).
- IDENTITY CANDIDATE: Handling production incidents pragmatically (manual closes + data recovery) while building preventive infrastructure (archive watchdog, stream persistence).