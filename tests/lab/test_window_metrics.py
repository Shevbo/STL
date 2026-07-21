from trader.lab.window_metrics import window_metrics


def _pairs(*items):
    return [{"time": t, "pnl": p} for t, p in items]


def test_is_oos_split_by_time_70_30():
    # span 0..100; is boundary at 70. pairs at t=10 (+100 IS), t=80 (+30 OOS).
    m = window_metrics(_pairs((10, 100.0), (80, 30.0)), span=(0.0, 100.0), is_frac=0.7, splits=4)
    assert m["net_is"] == 100.0
    assert m["net_oos"] == 30.0
    # is_rate = 100/70, oos_rate = 30/30 -> degrade = (30/30)/(100/70) = 0.7
    assert m["degrade"] == round((30 / 30) / (100 / 70), 6)


def test_window_consistency_counts_profitable_windows():
    # span 0..100, 4 windows: [0,25) [25,50) [50,75) [75,100].
    # profits in windows 0 and 2, loss in window 1, nothing in window 3.
    m = window_metrics(_pairs((10, 50.0), (30, -20.0), (60, 40.0)),
                       span=(0.0, 100.0), is_frac=0.7, splits=4)
    assert m["windows_total"] == 4
    assert m["windows_profitable"] == 2


def test_degrade_none_when_is_flat():
    m = window_metrics(_pairs((80, 30.0)), span=(0.0, 100.0), is_frac=0.7, splits=4)
    assert m["net_is"] == 0.0
    assert m["degrade"] is None


def test_empty_pairs():
    m = window_metrics([], span=(0.0, 100.0))
    assert m == {"net_is": 0.0, "net_oos": 0.0, "degrade": None,
                 "windows_profitable": 0, "windows_total": 4}
