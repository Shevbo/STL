"""Разножка помечает ТОЛЬКО доборы, а не вход по сигналу и не разворот.

Гейты gap_ok/in_cooldown стоят в make_on_bar исключительно на ветке усреднения
(31.07.2026: «разножка не гейтит вход с нуля»). explain помечал заблокированным
ЛЮБОЙ order с entry=true, включая разворот в шорт, — и при разборе «почему робот
не шортит» это уводило прямо в ложный след (05.08.2026).
"""
from robot_runner.explain import entry_block


def test_gap_message_when_close_to_last_entry():
    st = {"gap_ref": 89780}
    msg = entry_block(89690, {"min_gap_pts": 100}, st, 0)
    assert "разножка" in msg and "90" in msg


def test_no_message_when_far_enough():
    assert entry_block(89600, {"min_gap_pts": 100}, {"gap_ref": 89780}, 0) == ""


def test_filter_off_never_blocks():
    assert entry_block(89690, {"min_gap_pts": 0}, {"gap_ref": 89780}, 0) == ""


def test_no_previous_entry_never_blocks():
    assert entry_block(89690, {"min_gap_pts": 100}, {}, 0) == ""
