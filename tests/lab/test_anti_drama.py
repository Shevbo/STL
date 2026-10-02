"""anti_drama: синтетика. Импульс вверх до пика 1050, H=100, вход шортом по open 1000 (+ -5 полспреда)."""
import random
from datetime import datetime, timezone

from trader.lab.footprints import anti_drama as ad
from trader.lab.footprints.flex_range import aggregate_tf

DAY0 = int(datetime(2026, 9, 2, tzinfo=timezone.utc).timestamp())
INST = {"pv": 2.0, "tick": 1.0, "half": 5.0, "margin": 1000.0}


def _rows(spec, start=600, day=0):
    out = []
    for k, (o, h, lw, c) in enumerate(spec):
        out.append([DAY0 + day * 86400 + (start + k) * 60, o, h, lw, c, 1])
    return out


def test_fade_take_profit_and_costs():
    rows = _rows([(1000, 1000, 1000, 1000), (1000, 1001, 949, 960)] + [(960, 960, 960, 960)] * 5)
    t = ad.trade(rows, 0, -1, 1050, 100, 0.5, 1.0, 60, INST)               # tp 950, stop 1150
    assert t["kind"] == "tp" and t["entry"] == 995 and t["pnl_pts"] == 45 and t["tp_fill"]
    assert ad.net(t, "RIU6", INST, "maker") > ad.net(t, "RIU6", INST, "taker")


def test_fade_stop_beyond_extreme():
    rows = _rows([(1000, 1000, 1000, 1000), (1000, 1160, 1000, 1100)] + [(1100, 1100, 1100, 1100)] * 3)
    t = ad.trade(rows, 0, -1, 1050, 100, 0.5, 1.0, 60, INST)
    assert t["kind"] == "sl" and t["pnl_pts"] == -1 * (1155 - 995)          # стоп 1150 + полспреда


def test_time_exit_and_session_flat_on_short_weekend_session():
    rows = _rows([(1000, 1000, 1000, 1000)] * 40)
    t = ad.trade(rows, 0, -1, 1050, 100, 0.5, 1.0, 15, INST)
    assert t["kind"] == "T" and t["exit_ts"] == rows[15][0] and t["pnl_pts"] == -10
    short = _rows([(1000, 1000, 1000, 1000)] * 8)                          # короткая сессия: день кончается раньше T
    t = ad.trade(short, 0, -1, 1050, 100, 0.5, 1.0, 240, INST)
    assert t["kind"] == "eod" and t["exit_ts"] == short[-1][0]


def test_mirror_levels_symmetric():
    rows = _rows([(1000, 1000, 1000, 1000), (1000, 1051, 1000, 1040)] + [(1040, 1040, 1040, 1040)] * 3)
    t = ad.trade(rows, 0, -1, 1050, 100, 0.5, 1.0, 60, INST, mirror=True)   # продолжение: покупка, tp 1050, стоп 1000 - 150
    assert t["side"] == 1 and t["entry"] == 1005 and t["kind"] == "tp" and t["pnl_pts"] == 1050 - 1005


def test_entry_is_first_m1_after_tf_bar_close_and_events_no_lookahead():
    rnd = random.Random(1)
    px, spec = 1000.0, []
    for i in range(500):
        o = px
        px += rnd.gauss(0, 1) if i < 300 else (12 if i < 312 else -2)
        spec.append((round(o), round(max(o, px)) + 1, round(min(o, px)) - 1, round(px)))
    rows = _rows(spec, start=420)
    evs, agg, _ = ad.events_tf(rows, 5, 3, 150.0)
    assert evs
    e = evs[0]
    di = ad.day_index(rows)
    trs = ad.real_trades(di, evs, agg, 15, {"r": 0.5, "sl": 1.0, "T": 60}, INST)
    assert trs and trs[0]["entry_ts"] == agg[trs[0]["i_tf"]][6] + 60
    cut = [r for r in rows if r[0] <= agg[e["i"]][6]]
    evs2, agg2, _ = ad.events_tf(cut, 5, 3, 150.0)
    assert evs2 and evs2[0] == e                                                 # событие определяется барами до его закрытия
    assert all(t["exit_ts"] <= di["days"][t["day"]][-1][0] for t in trs)       # позиция на конец дня 0


def test_random_control_same_hour_decile_and_against_last_move():
    rnd = random.Random(2)
    rows, px = [], 1000.0
    for d in range(3):
        for k in range(600):
            o = px
            px += rnd.gauss(0, 2)
            rows.append([DAY0 + d * 86400 + (420 + k) * 60, o, max(o, px) + 1, min(o, px) - 1, px, 1])
    di = ad.day_index(rows)
    agg = aggregate_tf(rows, 5)
    pools = ad.atr_pools(di, agg, 5)
    trades = [{"i_tf": 80, "H": 50.0, "day": 0}]
    got = ad.random_trades(di, pools, trades, agg, {"r": 0.5, "sl": 1.0, "T": 60}, INST, 15, random.Random(1))
    assert got and got[0]["entry_ts"] % 86400 // 3600 == (agg[80][6] % 86400) // 3600
    k, i = di["where"][got[0]["entry_ts"] - 60]
    r = di["days"][k]
    move = r[i][4] - r[i - 15][4]
    assert got[0]["side"] == (-1 if move > 0 else 1)
