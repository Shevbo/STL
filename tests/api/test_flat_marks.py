"""Момент флэта: записываем только то, что действительно знаем.

Правило родилось 02.10.2026: расхождение журнала с позицией нельзя было ни
доказать, ни опровергнуть, потому что момента нуля не хранил никто. Запись
такого момента осмысленна ровно настолько, насколько ей можно верить, поэтому
проверяется здесь не «пишет», а «НЕ пишет, когда не знает».
"""

import json

from trader.api import flat_marks


def _truth(positions, **kw):
    base = {"ts_ms": 1_000, "pos_age_ms": 500, "link_age_ms": 200, "stale": False,
            "positions": positions}
    base.update(kw)
    return base


def test_transition_to_zero_is_recorded_once():
    seen = {}
    # первый кадр: позиция есть — запоминаем, но не пишем
    assert flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 4}]), seen, 1785) == []
    # закрылись — вот это момент
    lines = flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 0}]), seen, 1785)
    assert [ln["sec"] for ln in lines] == ["RIZ6"]
    assert lines[0]["build_rev"] == 1785          # на чём основан ноль
    assert lines[0]["pos_age_ms"] == 500          # и насколько свежим был кадр
    # дальше флэт длится — второй строки быть не должно, иначе за час простоя
    # файл вырастет на тысячи записей об одном событии
    for _ in range(5):
        assert flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 0}]), seen, 1785) == []


def test_zero_on_the_very_first_frame_is_not_a_moment():
    """Ноль на первом кадре после старта — не переход, а неизвестность.

    Когда позиция обнулилась, мы не знаем; записав время старта службы, мы
    выдали бы его за момент сделки.
    """
    assert flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 0}]), {}, 1) == []


def test_reopening_and_closing_again_gives_a_second_moment():
    seen = {}
    flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 2}]), seen, 1)
    assert len(flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 0}]), seen, 1)) == 1
    flat_marks.transitions(_truth([{"sec": "RIZ6", "net": -3}]), seen, 1)
    assert len(flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 0}]), seen, 1)) == 1


def test_stale_frame_proves_nothing_and_is_not_written():
    """СТАРЫЙ КАДР С НУЛЯМИ ЗНАЧИТ «НЕ ЗНАЮ», А НЕ «ФЛЭТ».

    Ложный ноль хуже отсутствия записи: он сделал бы «считаемым» окно журнала,
    которое считать нельзя, и расхождение после этого объявили бы пропажей
    сделок.
    """
    seen = {"RIZ6": False}
    for bad in ({"pos_age_ms": 60_000}, {"link_age_ms": 99_000},
                {"stale": True, "stale_why": "зеркало молчит"},
                {"pos_age_ms": None}, {"link_age_ms": None}):
        assert flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 0}], **bad), seen, 1) == []
    assert seen == {"RIZ6": False}      # и память негодный кадр не трогает
    assert flat_marks.frame_usable({}) == "кадра нет"


def test_memory_survives_a_blind_spell_without_duplicating_the_moment():
    """Молчание зеркала между двумя нулями не порождает второй записи."""
    seen = {}
    flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 5}]), seen, 1)
    assert len(flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 0}]), seen, 1)) == 1
    flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 0}], pos_age_ms=50_000), seen, 1)
    assert flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 0}]), seen, 1) == []


def test_each_instrument_counted_on_its_own():
    seen = {}
    flat_marks.transitions(_truth([{"sec": "RIZ6", "net": 1}, {"sec": "GZZ6", "net": -2}]), seen, 1)
    lines = flat_marks.transitions(
        _truth([{"sec": "RIZ6", "net": 0}, {"sec": "GZZ6", "net": -2}]), seen, 1)
    assert [ln["sec"] for ln in lines] == ["RIZ6"]


def test_append_writes_one_json_line_per_moment(tmp_path, monkeypatch):
    monkeypatch.setattr(flat_marks, "PATH", tmp_path / "sub" / "flat.jsonl")
    flat_marks.append([{"ts_ms": 1, "sec": "RIZ6"}, {"ts_ms": 2, "sec": "GZZ6"}])
    rows = [json.loads(x) for x in (tmp_path / "sub" / "flat.jsonl").read_text("utf-8").splitlines()]
    assert [r["sec"] for r in rows] == ["RIZ6", "GZZ6"]
