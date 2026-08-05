"""Приоритет задания в очереди движка (POST /api/v1/backtest/run -> backtest_runs.priority).

Ручной прогон с экрана обязан идти вперёд фоновых кампаний; клиентский мусор в поле
не должен ломать сортировку очереди (claim ORDER BY priority DESC, ...).
"""
from trader.util import QUEUE_PRIORITY_MANUAL, queue_priority


def test_no_field_means_background():
    # Скрипты кампаний (queue_campaign.py, optimize_adaptive) поля не шлют.
    assert queue_priority(None) == 0
    assert queue_priority(0) == 0
    assert queue_priority("") == 0


def test_manual_screen_run_jumps_the_queue():
    assert queue_priority(QUEUE_PRIORITY_MANUAL) == QUEUE_PRIORITY_MANUAL
    assert queue_priority("100") == QUEUE_PRIORITY_MANUAL
    assert QUEUE_PRIORITY_MANUAL > 0        # иначе весь смысл теряется


def test_out_of_range_is_clamped_not_trusted():
    assert queue_priority(10**9) == QUEUE_PRIORITY_MANUAL
    assert queue_priority(-5) == 0


def test_garbage_never_raises():
    for junk in ("abc", [], {}, object(), float("nan")):
        assert queue_priority(junk) == 0


def test_agent_manual_threshold_matches_server():
    """Резервный воркер i9 отличает ручной прогон от кампании по своей константе.
    Разъедутся — оператор снова будет ждать конца чужой кампании (05.08.2026)."""
    import pathlib
    import re
    src = pathlib.Path(__file__).parents[2] / "scripts" / "opt_agent.py"
    m = re.search(r"^MANUAL_PRIORITY\s*=\s*(\d+)", src.read_text(encoding="utf-8"), re.M)
    assert m, "MANUAL_PRIORITY пропал из opt_agent.py"
    assert int(m.group(1)) == QUEUE_PRIORITY_MANUAL
