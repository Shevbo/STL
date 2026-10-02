"""flex_range: синтетика. День с 07:00: 300 тихих баров, импульс вверх 10 баров по +3, затем боковик у пика."""
from datetime import datetime, timezone

from trader.lab.footprints import flex_range as fr

DAY0 = int(datetime(2026, 9, 2, tzinfo=timezone.utc).timestamp())


def _day(closes, start=420):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append([DAY0 + (start + i) * 60, prev, max(prev, c) + 1, min(prev, c) - 1, c, 1])
        prev = c
    return out


def _impulse_day(tail):
    cl = [1000 + (1 if i % 2 else -1) for i in range(300)]
    cl += [cl[-1] + 3 * k for k in range(1, 11)]
    peak = cl[-1]
    cl += [peak - 4] + tail
    return _day(cl)


def test_event_found_and_does_not_look_ahead():
    rows = _impulse_day([1030 + (i % 2) for i in range(50)])
    evs = fr.events_of_day(rows)
    assert len(evs) == 1 and evs[0]["side"] == 1 and 3 <= evs[0]["H"] <= 25
    e = evs[0]
    part = fr.events_of_day(rows[:e["i_conf"] + 1])
    assert len(part) == 1 and {k: part[0][k] for k in ("t0", "H", "P", "atr", "i_conf")} == {k: e[k] for k in ("t0", "H", "P", "atr", "i_conf")}
    # будущее после i_conf на событие и границы полос не влияет
    rows2 = rows[:e["i_conf"] + 1] + [[r[0], 5, 6, 4, 5, 1] for r in rows[e["i_conf"] + 1:]]
    e2 = fr.events_of_day(rows2)[0]
    assert e2["H"] == e["H"] and fr.band("P2", 3, e2["P"], rows2[e2["t0"]][4], 0, e2["atr"]) == \
        fr.band("P2", 3, e["P"], rows[e["t0"]][4], 0, e["atr"])


def test_bands():
    assert fr.band("P1", 0.5, 1000.0, 990.0, 20.0, 2.0) == (990.0, 1010.0)
    assert fr.band("P2", 3, 1000.0, 990.0, 20.0, 2.0) == (984.0, 996.0)


def test_length_and_censoring():
    rows = _impulse_day([1030 + (i % 2) for i in range(50)])
    t0 = fr.events_of_day(rows)[0]["t0"]
    L, obs, _m = fr.length_after(rows, t0, 900, 1100)          # полоса шире всего движения: цензура до конца дня
    assert obs is False and L == len(rows) - 1 - t0
    rows[t0 + 7][4] = 2000
    L, obs, _m = fr.length_after(rows, t0, 900, 1100)
    assert obs is True and L == 7


def test_km_median_and_stats():
    assert fr.km_median([(1, True), (2, True), (3, True), (4, True)]) == 2
    assert fr.km_median([(1, True), (2, False), (3, True), (5, True)]) == 3
    assert fr.km_median([(1, False), (2, False)]) is None
    assert fr.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    s = fr.slope([1, 2, 4, 8, 16], [1, 4, 16, 64, 256])
    assert abs(s - 2.0) < 1e-9


def test_control_uses_same_day_and_hour_pool():
    import random
    rows = _impulse_day([1030 + (i % 2) for i in range(300)])
    evs = fr.events_of_day(rows)
    ces = fr.control_events(rows, evs, {"all": [4.0], 10: [7.0]}, random.Random(1))
    assert len(ces) == 1 and ces[0]["H"] in (4.0, 7.0) and 60 <= ces[0]["t0"] < len(rows) - 1


def test_analyze_tf_smoke_and_aggregation_axis():
    import random
    rnd = random.Random(8)
    rows = []
    for d in range(4):
        cl = [1000 + rnd.gauss(0, 2) for _ in range(300)] + [1000 + 15 * k for k in range(1, 9)] + [1115 - 40 * k for k in range(1, 5)]
        cl += [1000 + rnd.gauss(0, 2) for _ in range(500)]
        day = _day([round(c) for c in cl])
        rows += [[r[0] + 86400 * d] + r[1:] for r in day]
    res = fr.analyze_tf(rows, 5, draws=2)
    assert res["tf"] == 5 and "variants" in res
