"""filter_stats несёт ЧЕМ робот вышел и сколько раз запрет удержал убыточный
переворот. Приказ оператора «убыток закрывает ТОЛЬКО стоп, а не переворот
сигнала» иначе снаружи не проверить: 28.09.2026 пришлось доказывать арифметикой
по ленте, что выход лестницы на −32.5 тыс был стопом, а не переворотом.
"""
import json

from robot_runner.host import _with_filter_stats


class _RT:
    def __init__(self, st):
        self._st = st

    def state_snapshot(self):
        return dict(self._st)

    def get_state(self, k, d=None):
        return self._st.get(k, d)


def test_exits_and_flip_skips_ride_along():
    sig = json.dumps({"want": -1})
    d = json.loads(_with_filter_stats(
        _RT({"exit_sl": 3, "exit_flip": 1, "exit_tp": 0, "flip_skips": 7,
             "dv_skips": 2, "filter_saved_pts": -9055.0}), sig))
    fs = d["filter_stats"]
    assert fs["flip_skips"] == 7
    assert fs["exits"] == {"sl": 3, "flip": 1}      # нулевую причину не показываем
    assert fs["saved_pts"] == -9055.0
    assert d["want"] == -1                          # исходный отчёт не портим


def test_exits_alone_are_enough_and_empty_stays_empty():
    sig = json.dumps({"want": 0})
    assert json.loads(_with_filter_stats(_RT({"exit_sl": 1}), sig))["filter_stats"]["exits"] == {"sl": 1}
    assert "filter_stats" not in json.loads(_with_filter_stats(_RT({}), sig))


def test_broken_state_never_breaks_the_report():
    class _Bad(_RT):
        def state_snapshot(self):
            raise RuntimeError("state gone")
    sig = json.dumps({"want": 1})
    assert _with_filter_stats(_Bad({"dv_skips": 1}), sig) == sig
