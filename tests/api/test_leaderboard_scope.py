"""Служебные прогоны не предлагаются оператору ни лампой, ни витриной.

01.10.2026 оператор увидел в компаньоне «кандидат: supertrend RIU6 +63 740 ₽,
RF 4.8». Это была строка ПЛАЦЕБО-гейта UNI-kelt — 50 случайных трендовых
векторов, кампания camp-20261001-gatekelt1001g6supw3005. Лампа отработала
честно по своему правилу; беда в том, что случайный вектор, выигравший в
лотерею, на экране неотличим от находки.
"""

from trader.api.leaderboard_scope import SQL_NOT_SERVICE, is_service_campaign


def test_service_prefixes_are_recognised():
    assert is_service_campaign("camp-20261001-gatekelt1001g6supw3005")
    assert is_service_campaign("camp-20261001-plc-random-50")
    assert is_service_campaign("camp-20260930-exec-cost-calib")


def test_a_real_campaign_is_not_hidden():
    """Прятать живые находки страшнее, чем показать служебную строку."""
    assert not is_service_campaign("camp-20261001-shectory1w")
    assert not is_service_campaign("camp-20260731-gdhonest")
    assert not is_service_campaign(None)
    assert not is_service_campaign("")


def test_prefix_counts_only_right_after_the_date():
    """Поиск подстроки где угодно поймал бы живую кампанию со словом внутри."""
    assert not is_service_campaign("camp-20261001-macd-exec-window")
    assert not is_service_campaign("gate-20261001-something")   # не наш формат имени


def test_sql_and_python_agree_on_the_same_names():
    """Предикат один на оба запроса — но проверяем, что он тот же по смыслу.

    SQL-условие исполняет Postgres, питоновское — тесты; разойдясь, они дадут
    разный список кандидатов в лампе и в витрине, и поймать это будет нечем.
    """
    assert "gate|plc|exec" in SQL_NOT_SERVICE
    assert "campaign_run IS NULL" in SQL_NOT_SERVICE      # имени нет — не прячем
    assert "!~*" in SQL_NOT_SERVICE                       # регистронезависимо
