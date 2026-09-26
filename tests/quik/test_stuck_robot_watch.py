"""Сторож застрявшего робота: агент блокирует заявки, а робот сидит в позиции.

26.09.2026 агент час отклонял каждую заявку обоих реальных роботов, закрывавших
шорты, а STL считал торговлю разрешённой."""

from trader.quik.stuck_robot_watch import SILENT_SEC, stuck

NOW = 1_790_400_000_000


def _robot(**kw):
    base = {"id": "agent-macd-RIZ6-v1", "symbol": "RIZ6", "mode": "real",
            "position": -10, "paused": False,
            "recent_fills": [{"ts_unix_ms": NOW - (SILENT_SEC + 600) * 1000}]}
    base.update(kw)
    return base


def test_blocked_agent_with_a_robot_in_position_raises():
    rows = stuck([_robot()], agent_blocked=True, market_open=True, now_ms=NOW)
    assert [(r["id"], r["position"]) for r in rows] == [("agent-macd-RIZ6-v1", -10)]
    assert rows[0]["idle_sec"] >= SILENT_SEC


def test_no_block_no_alarm_however_long_the_robot_is_silent():
    """Робот в позиции без сделок — норма: сигнала нет. Аварией это делает блокировка."""
    assert stuck([_robot()], agent_blocked=False, market_open=True, now_ms=NOW) == []


def test_unknown_block_is_not_an_alarm():
    """blocked=None — «не знаю»: будить оператора нечем, но и «разрешено» не следует."""
    assert stuck([_robot()], agent_blocked=None, market_open=True, now_ms=NOW) == []


def test_flat_robot_is_not_stuck():
    assert stuck([_robot(position=0)], agent_blocked=True, market_open=True,
                 now_ms=NOW) == []


def test_paused_and_paper_robots_are_out_of_scope():
    assert stuck([_robot(paused=True), _robot(mode="paper", id="paper-fvg")],
                 agent_blocked=True, market_open=True, now_ms=NOW) == []


def test_closed_market_is_quiet():
    assert stuck([_robot()], agent_blocked=True, market_open=False, now_ms=NOW) == []


def test_unknown_session_is_treated_defensively():
    assert stuck([_robot()], agent_blocked=True, market_open=None, now_ms=NOW) != []


def test_a_fresh_fill_means_the_robot_still_trades():
    rows = stuck([_robot(recent_fills=[{"ts_unix_ms": NOW - 60_000}])],
                 agent_blocked=True, market_open=True, now_ms=NOW)
    assert rows == []


def test_no_fills_at_all_counts_as_silence():
    """Пустой список филлов при открытой позиции — не свежая работа, а её отсутствие."""
    rows = stuck([_robot(recent_fills=[])], agent_blocked=True, market_open=True,
                 now_ms=NOW)
    assert rows and rows[0]["idle_sec"] == SILENT_SEC
