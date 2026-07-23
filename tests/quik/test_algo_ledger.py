"""Algo-trade ledger: signed-space P&L replay, source normalization, pricing."""
from trader.quik.algo_ledger import (
    RawFill,
    apply_fill,
    collect_fills,
    msk_date,
    point_values,
    price_row,
)


def test_apply_fill_open_extend_averages():
    pos, avg, real = apply_fill(0, 0.0, 2, 100.0)
    assert (pos, avg, real) == (2, 100.0, 0.0)
    pos, avg, real = apply_fill(pos, avg, 2, 110.0)
    assert (pos, avg, real) == (4, 105.0, 0.0)


def test_apply_fill_partial_reduce_keeps_avg():
    # The 2026-07 partial-reduce bug class: fewer contracts, SAME entry average.
    pos, avg, real = apply_fill(4, 105.0, -1, 120.0)
    assert (pos, avg) == (3, 105.0)
    assert real == 15.0
    # Later full close realizes against the ORIGINAL avg.
    pos, avg, real = apply_fill(pos, avg, -3, 100.0)
    assert (pos, avg) == (0, 0.0)
    assert real == -15.0


def test_apply_fill_short_and_flip():
    pos, avg, real = apply_fill(0, 0.0, -2, 200.0)
    assert (pos, avg, real) == (-2, 200.0, 0.0)
    # Flip -2 @200 -> +1 via buy 3 @190: realize 2x(200-190)=20, remainder opens at 190.
    pos, avg, real = apply_fill(pos, avg, 3, 190.0)
    assert (pos, avg, real) == (1, 190.0, 20.0)


def test_price_row_gross_net_commission():
    f = RawFill(robot_id="r1", mode="real", ts_ms=1_000, trade_num="t1",
                order_num="o1", symbol="RIU6", side="sell", qty=2, price=84000.0,
                dedup_key="q:t1")
    pv = 1.5  # RI: step 10, step cost 15 -> 1.5 ₽/point
    row = price_row(f, pos=2, avg=83000.0, pv=pv)
    assert row["pos_after"] == 0 and row["avg_after"] == 0.0
    assert row["pnl_gross_rub"] == round((84000 - 83000) * 2 * pv, 2)
    assert row["commission_rub"] > 0
    assert row["pnl_net_rub"] == round(row["pnl_gross_rub"] - row["commission_rub"], 2)
    assert row["order_kind"] == "market"


def _mirror():
    return {"received_at_ms": 500, "robots": [
        {"robot_id": "lxk22realbot", "paper": False, "symbol": "RIU6",
         "position": "15", "avg_price": 83000.0, "recent_fills": [
             {"order_id": "rr:x", "symbol": "RIU6", "side": "SIDE_BUY", "qty": "1",
              "price": 83000.0, "status": "filled", "ts_unix_ms": "900"}]},
        {"robot_id": "paperbot", "paper": True, "symbol": "BRU6",
         "position": "0", "avg_price": 0.0, "recent_fills": [
             {"order_id": "p1", "symbol": "BRU6", "side": "SIDE_SELL", "qty": "3",
              "price": 62.5, "status": "paper", "ts_unix_ms": "800"},
             {"order_id": "p2", "symbol": "BRU6", "side": "SIDE_BUY", "qty": "1",
              "price": 62.0, "status": "rejected", "ts_unix_ms": "850"}]},
    ]}


def _status():
    return {"quik": {"trades": [
        # tagged with the 20-char truncation of the robot id
        {"num": "111", "order_num": "222", "sec": "RIU6", "side": "buy",
         "price": 83500.0, "qty": 1, "tag": "lxk22realbot", "ts_ms": 900},
        {"num": "112", "order_num": "223", "sec": "RIU6", "side": "sell",
         "price": 83600.0, "qty": 2, "tag": "", "ts_ms": 901},        # manual
        {"num": "113", "order_num": "224", "sec": "RIU6", "side": "sell",
         "price": 83600.0, "qty": 1, "tag": "recon", "ts_ms": 902},   # align
    ]}}


def test_collect_fills_sources_and_filters():
    fills = collect_fills(_mirror(), _status())
    keys = [f.dedup_key for f in fills]
    # paper fill (ts 800) sorts before the real QUIK trade (ts 900)
    assert keys == ["p:paperbot:p1:800:sell:3:62.5", "q:111"]
    real = fills[1]
    assert (real.robot_id, real.mode, real.trade_num) == ("lxk22realbot", "real", "111")
    paper = fills[0]
    assert (paper.mode, paper.side, paper.qty) == ("paper", "sell", 3)
    # real robot's own runner fill (status filled) was NOT ingested: no double count
    assert not any(k.startswith("p:lxk22realbot") for k in keys)


def test_collect_fills_truncated_tag_matches():
    mirror = {"received_at_ms": 0, "robots": [
        {"robot_id": "agent-usopen-RIU6-v1-extra-long", "paper": False,
         "position": "0", "avg_price": 0.0, "recent_fills": []}]}
    status = {"quik": {"trades": [
        {"num": "9", "order_num": "9", "sec": "RIU6", "side": "buy", "price": 83000.0,
         "qty": 1, "tag": "agent-usopen-RIU6-v1-extra-long"[:20], "ts_ms": 5}]}}
    fills = collect_fills(mirror, status)
    assert len(fills) == 1 and fills[0].robot_id == "agent-usopen-RIU6-v1-extra-long"


def test_point_values_prefers_coef_and_falls_back():
    pv = point_values({"rows": [
        {"code": "RIU6", "coef": 1.5, "price_step": 10, "step_cost": 15},
        {"code": "BRU6", "coef": 0, "price_step": 0.01, "step_cost": 7.5},
        {"code": "BAD", "coef": 0, "price_step": 0, "step_cost": 0},
    ]})
    assert pv["RIU6"] == 1.5
    assert abs(pv["BRU6"] - 750.0) < 1e-9
    assert "BAD" not in pv


def test_msk_date_boundary():
    # 2026-07-21 23:59 MSK = 20:59 UTC; 2026-07-22 00:01 MSK = 21:01 UTC (21.07)
    assert msk_date(1784667540000) == "2026-07-21"
    assert msk_date(1784667660000) == "2026-07-22"


def test_backfill_real_tail_replays_complete_history():
    from trader.quik.algo_ledger import backfill_real_tail
    tail = [
        {"status": "filled", "ts_unix_ms": 100, "side": "SIDE_BUY", "qty": 2,
         "price": 83000.0, "order_id": "rr:r1:1:aa", "symbol": "RIU6"},
        {"status": "filled", "ts_unix_ms": 200, "side": "SIDE_SELL", "qty": 1,
         "price": 83100.0, "order_id": "rr:r1:2:bb", "symbol": "RIU6"},
        {"status": "paper", "ts_unix_ms": 150, "side": "SIDE_BUY", "qty": 9,
         "price": 1.0, "order_id": "x", "symbol": "RIU6"},          # not a real fill
        {"status": "filled", "ts_unix_ms": 900, "side": "SIDE_BUY", "qty": 5,
         "price": 84000.0, "order_id": "rr:r1:3:cc", "symbol": "RIU6"},  # post-seed
    ]
    rows = backfill_real_tail("r1", "RIU6", tail, seeded_at_ms=500, seed_pos=1, pv=1.5)
    assert rows is not None and len(rows) == 2
    assert [r["pos_after"] for r in rows] == [2, 1]
    # partial reduce: realized (83100-83000)*1 pt x 1.5 ₽/pt
    assert abs(rows[1]["pnl_gross_rub"] - 150.0) < 1e-9
    assert all(r["dedup_key"].startswith("bf:r1:") for r in rows)


def test_backfill_real_tail_refuses_incomplete_tail():
    from trader.quik.algo_ledger import backfill_real_tail
    tail = [{"status": "filled", "ts_unix_ms": 100, "side": "SIDE_BUY", "qty": 1,
             "price": 83000.0, "order_id": "o1", "symbol": "RIU6"}]
    # replay ends at pos=1 but the ledger seeded pos=3 -> tail is cut -> refuse
    assert backfill_real_tail("r1", "RIU6", tail, 500, seed_pos=3, pv=1.5) is None


def test_parse_runner_log_real_only_and_arming_reset():
    from trader.quik.algo_ledger import parse_runner_log
    log = """2026-07-14 15:59:06 [LIFECYCLE] деплой: стратегия=x режим=РЕАЛ max_pos=7 окно=09:00-23:55
2026-07-14 16:00:01 [FILL] buy 1 @ 100 → позиция 1 @ 100, реализовано 0 п.
2026-07-14 16:05:00 [FILL] sell 1 @ 110 → позиция 0 @ 0, реализовано 10 п.
2026-07-14 17:06:49 [LIFECYCLE] АРМИНГ paper->РЕАЛ: статистика обнулена
2026-07-14 18:40:01 [FILL] buy 3 @ 85990 (paper) → позиция 3 @ 85990, реализовано 0 п.
2026-07-14 18:41:01 [FILL] buy 3 @ 85990 → позиция 3 @ 85990, реализовано 0 п.
2026-07-14 20:45:09 [FILL] sell 3 @ 85560 → позиция 0 @ 0, реализовано -1290 п.
2026-07-14 21:00:00 [FILL] sell 2 @ 85000 → позиция -2 @ 85000, реализовано -1290 п.
"""
    fills = parse_runner_log(log)
    # arming discards the pre-arming real fills; the (paper) line is never real
    assert len(fills) == 3
    assert [f["ts"][-8:] for f in fills] == ["18:41:01", "20:45:09", "21:00:00"]
    # per-fill P&L is the delta of the runner's cumulative realized
    assert [f["gross_points"] for f in fills] == [0.0, -1290.0, 0.0]
    assert fills[1]["pos_after"] == 0 and fills[2]["pos_after"] == -2
    assert fills[2]["avg_after"] == 85000.0


def test_parse_runner_log_lumps_pre_log_realized_into_first_fill():
    from trader.quik.algo_ledger import parse_runner_log
    # log starts mid-history (no arming): realized already -6154 on the first fill
    log = ("2026-07-14 00:42:41 [FILL] sell 1 @ 88370 → позиция 0 @ 0, реализовано -6154 п.\n"
           "2026-07-14 01:00:00 [FILL] sell 1 @ 88000 → позиция -1 @ 88000, реализовано -6154 п.\n")
    fills = parse_runner_log(log)
    assert [f["gross_points"] for f in fills] == [-6154.0, 0.0]
    # running total stays exact even though the first fill absorbs the opening
    assert fills[-1]["realized_cum"] == -6154.0
