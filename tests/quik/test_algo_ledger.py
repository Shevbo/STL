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
