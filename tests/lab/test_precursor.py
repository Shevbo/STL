"""precursor: синтетика. Предвестник вставлен -> лифт; случайный ряд -> лифта нет; окно кончается до старта."""
import random

from trader.lab.footprints import precursor as pc

DAY0 = 1788307200          # 2026-09-02 00:00 (МСК-стенка как UTC)


def _series(seed: int, days: int = 15, precursor: bool = True, per_day: int = 5):
    """Минутные бары [ts,o,h,l,c,v]. Рывки по 8 баров +-3 (>8 ATR), перед ними (precursor=True) 10 баров
    ровного роста с растущим объёмом; иначе перед рывком тот же шум, что везде."""
    r = random.Random(seed)
    rows, evs = [], []
    for d in range(days):
        base = DAY0 + d * 86400 + 7 * 3600
        starts = sorted(r.sample(range(100, 900, 60), per_day))
        starts = [s for k, s in enumerate(starts) if k == 0 or s - starts[k - 1] > 60]
        px, plan = 100000.0, {}
        for s in starts:
            side = r.choice((1, -1))
            for k in range(10):                        # окно предвестника
                plan[s - 10 + k] = ("pre", side, k)
            for k in range(8):
                plan[s + k] = ("imp", side, k)
            for k in range(3):                         # откат подтверждения
                plan[s + 8 + k] = ("back", side, k)
            evs.append((d, s, side))
        for i in range(1000):
            kind, side, k = plan.get(i, ("noise", 0, 0))
            if kind == "imp":
                inc, vol = 3.0 * side, 400
            elif kind == "back":
                inc, vol = -2.0 * side, 200
            elif kind == "pre" and precursor:
                inc, vol = 0.4 * side, 100 + 80 * k
            else:
                inc, vol = r.gauss(0, 1.0), 100 + r.randint(-10, 10)
            o = px
            c = o + inc
            g = abs(r.gauss(0, 0.3))
            rows.append([base + i * 60, o, max(o, c) + g, min(o, c) - g, c, vol])
            px = c
    return rows, evs


def test_window_ends_before_impulse_start_no_lookahead():
    rows, _ = _series(1, days=2)
    bars = pc.tf_bars(rows[:1000], 1)
    evs = pc.events_of_day(bars, dict(pc.DEFAULTS))
    assert evs, "рывки в синтетике должны находиться"
    for e in evs:
        q = e["s"] - 1
        a = pc.digitize(bars, q, 10)
        fut = [b[:] for b in bars]
        for b in fut[e["s"]:]:                          # портим ВСЁ с бара старта и позже
            b[1:] = [x * 7 + 123 for x in b[1:5]] + [b[5] * 9]
        assert pc.digitize(fut, q, 10) == a             # окно не зависит от будущего
    assert pc.label_at([(50, 1), (70, -1)], 49, 3) == 1
    assert pc.label_at([(50, 1)], 50, 3) == 0           # старт на самом q уже не «после»
    assert pc.label_at([(50, 1)], 40, 3) == 0           # дальше h


def test_tf_aggregation_closed_buckets_only_inside_day():
    rows, _ = _series(2, days=1)
    b5 = pc.tf_bars(rows, 5)
    assert b5[0][0] % 300 == 0 and b5[0][5] == sum(r[5] for r in rows[:5])
    assert b5[0][2] == max(r[2] for r in rows[:5]) and b5[0][4] == rows[4][4]


def _cfg(res, method, h):
    return [c for c in res["configs"] if c["method"] == method and c["h"] == h and c["N"] == 10 and c["tf"] == 1][0]


def test_inserted_precursor_gives_lift_and_direction():
    rows, _ = _series(3, precursor=True)
    res = pc.analyze(rows, {"tfs": [1], "Ns": [10], "hs": [3, 10], "draws": 20, "boot": 100})
    for method in ("M1", "M2"):
        c = _cfg(res, method, 10)
        assert c["n_sig"] > 0 and c["lift"] and c["lift"] >= 3 and c["lift_ci"][0] > 1, (method, c)
    c1 = _cfg(res, "M1", 10)
    assert c1["dir_acc"] is not None and c1["dir_acc"] >= 0.9, c1
    assert c1["p3_lift"][0] >= 19 and c1["completeness"] and c1["completeness"] > 0.5, c1


def test_random_series_gives_no_lift():
    rows, _ = _series(4, precursor=False)
    res = pc.analyze(rows, {"tfs": [1], "Ns": [10], "hs": [3, 10], "draws": 20, "boot": 100})
    for method in ("M1", "M2"):
        c = _cfg(res, method, 10)
        passed = c.get("lift") and c["lift"] >= 2 and c["lift_ci"][0] > 1
        assert not passed, (method, c)


def test_logit_recovers_signal_and_rms_distance():
    r = random.Random(0)
    X = [[r.gauss(0, 1), r.gauss(0, 1)] for _ in range(600)]
    y = [1 if x[0] + 0.2 * r.gauss(0, 1) > 0.5 else 0 for x in X]
    w = pc.fit_logit(X, y)
    assert w[1] > 1.0 and abs(w[2]) < 0.6
    assert pc.predict_logit(w, [2.0, 0.0]) > pc.predict_logit(w, [-2.0, 0.0])
    assert pc.rms_dist([0, 0, 0, 0], [1, 1, 1, 1], 2.0) == 1.0
    assert pc.rms_dist([0, 0, 0, 0], [5, 5, 5, 5], 1.0) is None
