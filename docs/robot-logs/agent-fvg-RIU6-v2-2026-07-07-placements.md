# agent-fvg-RIU6-v2 — order placements 2026-07-07 (QUIK message log, verbatim)

Source: the QUIK terminal message window (`[shectory_trade] place ...` lines from the
Lua bridge), captured by the operator 2026-07-08. These are the robot's order
PLACEMENTS for the full 07.07.2026 session — recorded here because the runner's
persisted fill history was truncated to 20 entries by the pre-1783516899 runner
(fixed: full 200-tail since commit 8e855e2), so the showcase lost most of this day.

NOTE: a `place` line is a transaction SENT to QUIK, not a fill. Fills for these are
in the QUIK trades table (Таблица сделок) for account 763J576. Same-second pairs at
one price are the FVG reversal mechanic: close `abs(cur)` + open `base_unit`
(trader/lab/strategies/library.py:74-76), not duplicates. Unfilled limits from this
day were day-expired by QUIK at session close (that expiry, invisible to the old
runner, produced the phantom-book incident fixed in fd72a8c/8e855e2).

| # | time (MSK) | trans_id | side | price | qty |
|---|-----------|----------|------|-------|-----|
| 1 | 9:55:01 | 1 | S | 88050 | 1 |
| 2 | 10:52:01 | 2 | B | 86330 | 1 |
| 3 | 10:53:01 | 3 | S | 86250 | 1 |
| 4 | 11:18:01 | 4 | B | 86270 | 1 |
| 5 | 11:18:01 | 5 | B | 86270 | 1 |
| 6 | 11:22:05 | 6 | S | 86080 | 1 |
| 7 | 11:22:05 | 7 | S | 86080 | 1 |
| 8 | 12:03:04 | 8 | B | 86510 | 1 |
| 9 | 12:03:04 | 9 | B | 86510 | 1 |
| 10 | 12:17:01 | 10 | S | 86910 | 1 |
| 11 | 12:17:01 | 11 | S | 86910 | 1 |
| 12 | 12:29:01 | 12 | B | 87510 | 1 |
| 13 | 12:29:01 | 13 | B | 87510 | 1 |
| 14 | 13:08:01 | 14 | S | 87620 | 1 |
| 15 | 13:08:01 | 15 | S | 87620 | 1 |
| 16 | 13:58:01 | 16 | B | 88210 | 1 |
| 17 | 13:58:01 | 17 | B | 88210 | 1 |
| 18 | 14:05:03 | 18 | S | 87810 | 1 |
| 19 | 14:05:03 | 19 | S | 87810 | 1 |
| 20 | 14:58:01 | 20 | B | 87770 | 1 |
| 21 | 14:58:01 | 21 | B | 87770 | 1 |
| 22 | 15:02:01 | 22 | S | 87300 | 1 |
| 23 | 15:02:01 | 23 | S | 87300 | 1 |
| 24 | 15:07:08 | 24 | B | 87600 | 1 |
| 25 | 15:07:08 | 25 | B | 87600 | 1 |
| 26 | 15:34:02 | 26 | S | 86900 | 1 |
| 27 | 15:34:02 | 27 | S | 86900 | 1 |
| 28 | 15:49:02 | 28 | B | 87420 | 1 |
| 29 | 15:49:02 | 29 | B | 87420 | 1 |
| 30 | 16:58:01 | 30 | S | 87030 | 1 |
| 31 | 16:58:01 | 31 | S | 87030 | 1 |
| 32 | 17:27:01 | 32 | B | 87650 | 1 |
| 33 | 17:27:01 | 33 | B | 87650 | 1 |
| 34 | 18:44:01 | 34 | B | 88350 | 1 |
| 35 | 20:04:21 | 35 | S | 88520 | 1 |

All: SPBFUT RIU6, account 763J576, limit orders at the strategy's bar close
(pre-marketable-fix behaviour; marketable pricing shipped 2026-07-08, fd72a8c).

Reading: 35 placements = 1 opener + 16 reversal pairs + 2 singles. The single #34
(18:44) and #35 (20:04) bracket the evening stall; unfilled ones from the tail are
the day-expired orders later seen as phantoms. For per-fill P&L reconstruction pull
the QUIK trades table for 07.07 and join on price/time.
