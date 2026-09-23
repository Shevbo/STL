"""Снимок правды: возраст честный, позиция разложена, сделки не дублируются.

Главное здесь — не красота данных, а невозможность выдать старое за текущее:
23.09.2026 отчёт оператору назвал открытым шорт, закрытый полутора часами ранее."""

import json

from trader.quik import truth

NOW = 1_790_160_000_000


def _status(**kw):
    base = {
        "_received_at_ms": NOW - 1500,
        "health": {"positions": [{"sec": "RIZ6", "net": -40, "avg": 86500.0,
                                  "varmargin": 12000.0}]},
        "robots": [{"id": "agent-macd-RIZ6-v1", "symbol": "RIZ6", "mode": "real",
                    "position": -10, "avg_price": 86400.0}],
        "quik": {"trades": [
            {"num": "1", "ts_ms": NOW - 60_000, "sec": "RIZ6", "side": "S", "qty": 10,
             "price": 86320.0, "order_num": "77", "tag": ""},
            {"num": "2", "ts_ms": NOW - 30_000, "sec": "RIZ6", "side": "S", "qty": 10,
             "price": 86420.0, "order_num": "78", "tag": "rr:agent-macd-RIZ6-v1"},
        ]},
    }
    base.update(kw)
    return base


def test_position_is_split_between_robots_and_the_hand():
    t = truth.build(_status(), [{"last_seen_age_ms": 800}], [], NOW)
    assert t["stale"] is False
    pos = t["positions"][0]
    assert (pos["net"], pos["robots"], pos["manual"]) == (-40, -10, -30)
    assert t["trades_today"]["by_owner"] == {"manual": 1, "robot": 1}


def test_old_mirror_is_marked_stale_not_served_as_now():
    t = truth.build(_status(_received_at_ms=NOW - 45_000), [], [], NOW)
    assert t["stale"] is True and t["age_ms"] == 45_000


def test_no_mirror_at_all_is_stale_and_empty():
    t = truth.build(None, [], [], NOW)
    assert t["stale"] is True and t["positions"] == [] and t["age_ms"] == -1


def test_robot_position_missing_from_the_account_table_is_shown_not_hidden():
    st = _status(health={"positions": []})
    t = truth.build(st, [], [], NOW)
    assert t["positions"] == [{"sec": "RIZ6", "net": 0, "avg": None, "varmargin": None,
                               "robots": -10, "manual": 10}]


def test_journal_appends_once_per_trade(tmp_path):
    seen: set[str] = set()
    d = str(tmp_path)
    assert truth.append_trades(_status(), seen, NOW, d) == 2
    assert truth.append_trades(_status(), seen, NOW, d) == 0   # тот же ринг — не дубль
    rows = [json.loads(x) for x in open(truth.journal_path(NOW, d), encoding="utf-8")]
    assert [r["owner"] for r in rows] == ["manual", "robot"]


def test_journal_survives_restart_by_reading_back_its_own_day(tmp_path):
    d = str(tmp_path)
    truth.append_trades(_status(), set(), NOW, d)
    assert truth.append_trades(_status(), truth._load_seen(NOW, d), NOW, d) == 0
