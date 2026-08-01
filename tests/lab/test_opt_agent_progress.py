"""Живой прогресс перебора: свёртка очереди воркеров в счётчик + хит-парад.

Воркер шлёт строку на КАЖДОЕ комбо; агент складывает их в «посчитано N из M» и
топ-10 по прибыль×RF — то, что монитор LAB показывает во время прогона.
"""
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "opt_agent", pathlib.Path("scripts/opt_agent.py"))
oa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oa)


class FakeQ:
    """Очередь воркеров: отдаёт заготовленное, потом бросает (как пустая mp-очередь)."""
    def __init__(self, items):
        self.items = list(items)

    def get_nowait(self):
        if not self.items:
            raise RuntimeError("empty")
        return self.items.pop(0)


def _agent():
    a = oa.Agent.__new__(oa.Agent)          # без сети/пула — нам нужна только свёртка
    a._progress = {"done": 0, "top": []}
    a._activity = {"state": "job", "combos": 3}
    a._progress_q = None
    return a


def combo(net, rf=1.0, **params):
    return {"net": net, "rf": rf, "ann": None, "trades": 10, "params": params}


def test_rank_matches_the_lab_hit_parade():
    # Прибыль × RF; убыточные ранжируются самой прибылью (иначе net²/dd всплывает вверх).
    assert oa._profit_rf(combo(1000, 3)) == 3000
    assert oa._profit_rf(combo(-5000, -2)) == -5000      # катастрофа НЕ выше прибыли
    assert oa._profit_rf(combo(1000, 0)) == 10           # RF=0 -> пол 0.01, не ноль
    assert oa._profit_rf(combo(1000, 3)) > oa._profit_rf(combo(1000, 2))


def test_counts_every_combo_including_failed_ones():
    a = _agent()
    a._progress_q = FakeQ([combo(10), None, combo(20)])   # None = комбо упало
    a._drain_progress()
    assert a._progress["done"] == 3
    assert [t["net"] for t in a._progress["top"]] == [20, 10]


def test_top_is_capped_and_sorted():
    a = _agent()
    a._progress_q = FakeQ([combo(i * 100, 2) for i in range(1, 26)])
    a._drain_progress()
    assert a._progress["done"] == 25
    assert len(a._progress["top"]) == oa._LIVE_TOP
    assert a._progress["top"][0]["net"] == 2500          # лучший наверху
    assert a._progress["top"][-1]["net"] == 2500 - (oa._LIVE_TOP - 1) * 100


def test_progress_accumulates_across_drains():
    a = _agent()
    a._progress_q = FakeQ([combo(10)])
    a._drain_progress()
    a._progress_q = FakeQ([combo(50)])
    a._drain_progress()
    assert a._progress["done"] == 2
    assert a._progress["top"][0]["net"] == 50            # лидер сменился
    assert a._activity["done"] == 2                      # уедет heartbeat'ом в монитор


def test_no_queue_is_silent_not_fatal():
    a = _agent()
    a._drain_progress()                                   # Manager не поднялся
    assert a._progress == {"done": 0, "top": []}
