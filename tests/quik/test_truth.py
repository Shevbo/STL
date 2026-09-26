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
             "price": 86420.0, "order_num": "78", "tag": "agent-macd-RIZ6-v1"},
        ]},
    }
    base.update(kw)
    return base


def test_paper_robots_do_not_move_the_account_split():
    """Бумажной позиции на счёте нет: сложи её в разбивку — и «рука» соврёт."""
    st = _status()
    st["robots"] = st["robots"] + [{"id": "paper-fvg", "symbol": "RIZ6",
                                    "mode": "paper", "position": 5}]
    pos = truth.build(st, [{"last_seen_age_ms": 500}], [], NOW)["positions"][0]
    assert (pos["robots"], pos["manual"]) == (-10, -30)
    assert len(truth.build(st, [], [], NOW)["robots"]) == 2   # показываем оба


def test_position_is_split_between_robots_and_the_hand():
    t = truth.build(_status(), [{"last_seen_age_ms": 800}], [], NOW,
                    robot_ids={"agent-macd-RIZ6-v1"})
    assert t["stale"] is False
    pos = t["positions"][0]
    assert (pos["net"], pos["robots"], pos["manual"]) == (-40, -10, -30)
    assert t["trades_today"]["by_owner"] == {"manual": 1, "robot": 1}


def test_quiet_mirror_with_a_live_link_is_not_stale():
    """Агент шлёт статус только при ИЗМЕНЕНИИ: тишина в спокойный час — норма."""
    t = truth.build(_status(_received_at_ms=NOW - 45_000),
                    [{"last_seen_age_ms": 900}], [], NOW)
    assert t["stale"] is False and t["age_ms"] == 45_000


def test_silent_link_is_stale_however_fresh_the_mirror_looks():
    t = truth.build(_status(), [{"last_seen_age_ms": 30_000}], [], NOW)
    assert t["stale"] is True and "молчит" in t["stale_why"]


def test_live_link_with_a_frozen_status_builder_is_stale_too():
    """Канал жив, а зеркало стоит часами — это поломка сборщика, не тишина."""
    t = truth.build(_status(_received_at_ms=NOW - 600_000),
                    [{"last_seen_age_ms": 900}], [], NOW)
    assert t["stale"] is True and "не обновлялся" in t["stale_why"]


def test_no_mirror_at_all_is_stale_and_empty():
    t = truth.build(None, [{"last_seen_age_ms": 500}], [], NOW)
    assert t["stale"] is True and t["positions"] == [] and t["age_ms"] == -1


def test_robot_position_missing_from_the_account_table_is_shown_not_hidden():
    st = _status(health={"positions": []})
    t = truth.build(st, [{"last_seen_age_ms": 500}], [], NOW)
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


def test_owner_reads_the_brokerref_quik_actually_stores():
    """Тег в таблице QUIK — не client_id: 20 символов, ID робота как есть.

    До 23.09 классификация ждала префиксов rr:/so: и записывала в «руку»
    ВСЕ роботные сделки — первый же живой снимок показал 100 ручных из 100."""
    assert truth.owner("agent-macdshort-RIU6") == "robot"
    assert truth.owner("lxk22tsffsxiiotb8kmp") == "robot"
    assert truth.owner("stl-so-1521ee8cd8") == "smart"
    assert truth.owner("recon") == "recon"
    assert truth.owner("") == "manual" and truth.owner(None) == "manual"


class _SO:
    def __init__(self, **kw):
        self.__dict__.update({"so_id": "x", "kind": "trail_tp", "code": "RIZ6",
                              "side": "sell", "qty": 20, "status": "armed",
                              "trigger_price": 85790.0, "trail_offset": 220.0,
                              "activated": False, "peak": 0.0, "native_state": "",
                              "parent_id": ""})
        self.__dict__.update(kw)


def test_watch_view_explains_why_an_armed_order_did_not_fire():
    ticks = {"RIZ6": {"last": 85680.0, "received_at_unix_ms": NOW - 600}}
    ext = {"RIZ6": {"hi": 85700.0, "lo": 85500.0, "since_ms": NOW - 600_000}}
    w = truth.watch_view([_SO()], ticks, ext, True, NOW)[0]
    assert w["distance"] == 110.0 and w["watcher_blind"] is False
    assert (w["lo_since"], w["hi_since"]) == (85500.0, 85700.0)


def test_watch_view_calls_a_dead_frame_blindness():
    ticks = {"RIZ6": {"last": 85680.0, "received_at_unix_ms": NOW - 45_000}}
    assert truth.watch_view([_SO()], ticks, {}, True, NOW)[0]["watcher_blind"] is True
    # Биржа закрыта — сторож тоже не действует, и это должно быть видно.
    fresh = {"RIZ6": {"last": 85680.0, "received_at_unix_ms": NOW - 500}}
    assert truth.watch_view([_SO()], fresh, {}, False, NOW)[0]["watcher_blind"] is True


def test_extremes_follow_the_same_frame_the_watcher_judges_by():
    ext: dict = {}
    truth.track_extremes(ext, {"RIZ6"}, {"RIZ6": {"last": 85600.0}}, NOW)
    truth.track_extremes(ext, {"RIZ6"}, {"RIZ6": {"last": 85830.0}}, NOW + 1000)
    truth.track_extremes(ext, {"RIZ6"}, {"RIZ6": {"last": 85500.0}}, NOW + 2000)
    assert (ext["RIZ6"]["hi"], ext["RIZ6"]["lo"]) == (85830.0, 85500.0)
    truth.track_extremes(ext, set(), {}, NOW + 3000)      # заявок нет — след снят
    assert ext == {}


def test_unknown_tag_is_the_brokers_app_not_a_robot():
    """Приложение брокера метит сделки своими тегами ("}S…XдD"). Без реестра
    роботов они звались роботными, и 69 сделок оператора 24.09 ушли не туда."""
    assert truth.owner("}SЖЮqXдD", {"lxk22tsffsxiiotb8kmp"}) == "external"
    assert truth.owner("lxk22tsffsxiiotb8kmp", {"lxk22tsffsxiiotb8kmpQQQ"}) == "robot"
    assert truth.channel("", set()) == "quik"
    assert truth.channel("stl-so-abc", set()) == "smart"
    assert truth.channel("}SЖЮqXдD", set()) == "broker"
    assert truth.channel("recon", set()) == "recon"


def test_registry_round_trips_through_disk(tmp_path):
    p = str(tmp_path / "robot_ids.json")
    truth.save_robot_ids({"a", "b"}, p)
    assert truth.load_robot_ids(p) == {"a", "b"}
    assert truth.load_robot_ids(str(tmp_path / "missing.json")) == set()


# ---- kill-switch агента в снимке ----
# 26.09.2026 агент час отклонял каждую заявку обоих реальных роботов, а STL считал
# торговлю разрешённой: блокировка не публиковалась нигде.

def test_agent_block_is_visible_in_the_snapshot():
    t = truth.build(_status(), [{"last_seen_age_ms": 500}], [], NOW,
                    limits={"blocked": True, "received_at_ms": NOW - 20_000})
    assert t["agent_blocked"] is True and t["limits_age_ms"] == 20_000


def test_missing_block_field_is_unknown_not_allowed():
    """Старый агент поля не присылает: «не знаю» и «разрешено» — разные утверждения."""
    t = truth.build(_status(), [{"last_seen_age_ms": 500}], [], NOW,
                    limits={"trading_enabled": True})
    assert t["agent_blocked"] is None and t["limits_age_ms"] == -1
    assert truth.build(_status(), [], [], NOW)["agent_blocked"] is None


def test_unblocked_agent_says_so():
    t = truth.build(_status(), [{"last_seen_age_ms": 500}], [], NOW,
                    limits={"blocked": False, "received_at_ms": NOW - 1000})
    assert t["agent_blocked"] is False
