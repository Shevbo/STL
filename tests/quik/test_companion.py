"""STL Companion: pairing, token scope, and the snapshot's watch verdicts.

The security claim this file defends: a companion device token opens EXACTLY ONE
door (GET /snapshot) and nothing else in the app. If that ever stops being true,
`test_companion_token_is_useless_on_other_routes` fails.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trader.api.quik_companion import _watch_runner, _with_control
from trader.api.quik_companion import router as companion_router
from trader.api.quik_robots import router as quik_robots_router
from trader.auth.portal import make_session_token
from trader.quik.store import QuikAgentStore

SECRET = "test-bridge-secret"
ACCOUNT = "WIN10-HYPERV\\admin"


class _Settings:
    shectory_auth_bridge_secret = SECRET


class FakePool:
    """Enough asyncpg surface for the companion module: agent_control as a dict,
    every other SELECT answers empty."""

    def __init__(self):
        self.kv: dict[str, str] = {}

    async def execute(self, sql: str, *args):
        if sql.startswith("INSERT INTO agent_control"):
            self.kv[args[0]] = args[1]
        elif sql.startswith("DELETE FROM agent_control"):
            self.kv.pop(args[0], None)
        return "OK"

    async def fetchval(self, sql: str, *args):
        if "FROM agent_control WHERE key=$1" in sql:
            return self.kv.get(args[0])
        return None

    async def fetch(self, sql: str, *args):
        if "FROM agent_control" in sql and "LIKE" in sql:
            pats = [a.rstrip("%") for a in args]
            return [{"key": k, "value": v} for k, v in self.kv.items()
                    if any(k.startswith(p) for p in pats)]
        return []


def _client(monkeypatch) -> tuple[TestClient, FakePool]:
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.include_router(quik_robots_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    app.state.quik_store = QuikAgentStore()
    app.state.quik_server = None
    return TestClient(app), app.state.db_pool


def _operator_headers() -> dict:
    return {"Authorization": "Bearer " + make_session_token("op@example.com", SECRET)}


def _mint_code(client: TestClient, account: str = ACCOUNT) -> str:
    r = client.post("/api/v1/quik/companion/pairing-code",
                    json={"account": account}, headers=_operator_headers())
    assert r.status_code == 200, r.text
    return r.json()["code"]


def _pair(client: TestClient, code: str, account: str = ACCOUNT):
    return client.post("/api/v1/quik/companion/pair",
                       json={"code": code, "account": account, "machine": "WIN10-HYPERV"})


def test_pair_then_read_snapshot(monkeypatch):
    client, _ = _client(monkeypatch)
    r = _pair(client, _mint_code(client))
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert token.startswith("stlc_")

    snap = client.get("/api/v1/quik/companion/snapshot",
                      headers={"Authorization": "Bearer " + token})
    assert snap.status_code == 200, snap.text
    body = snap.json()
    assert set(body) >= {"account", "positions", "robots", "watch", "alerts"}


def test_pairing_code_is_single_use_and_account_bound(monkeypatch):
    client, _ = _client(monkeypatch)

    # A code issued for one account never pairs another, even with the right code.
    code = _mint_code(client)
    assert _pair(client, code, account="OTHERPC\\bob").status_code == 403
    # ...and that attempt burned the code, so the rightful owner cannot reuse it.
    assert _pair(client, code, account=ACCOUNT).status_code == 403

    # A fresh code works exactly once.
    code = _mint_code(client)
    assert _pair(client, code).status_code == 200
    assert _pair(client, code).status_code == 403


def test_expired_code_is_refused(monkeypatch):
    client, pool = _client(monkeypatch)
    code = _mint_code(client)
    rec = json.loads(pool.kv["companion:code:" + code])
    rec["expires_ms"] = int(time.time() * 1000) - 1
    pool.kv["companion:code:" + code] = json.dumps(rec)
    assert _pair(client, code).status_code == 403


def test_revoked_companion_loses_access(monkeypatch):
    client, _ = _client(monkeypatch)
    token = _pair(client, _mint_code(client)).json()["token"]
    hdr = {"Authorization": "Bearer " + token}
    assert client.get("/api/v1/quik/companion/snapshot", headers=hdr).status_code == 200

    devices = client.get("/api/v1/quik/companion/devices", headers=_operator_headers()).json()
    dev_id = devices["devices"][0]["id"]
    assert client.post("/api/v1/quik/companion/revoke", json={"id": dev_id},
                       headers=_operator_headers()).status_code == 200
    assert client.get("/api/v1/quik/companion/snapshot", headers=hdr).status_code == 401


def test_companion_token_is_useless_on_other_routes(monkeypatch):
    """The whole safety story: the token authenticates the snapshot and NOTHING
    else — not a read of the robot mirror, not any control route."""
    client, _ = _client(monkeypatch)
    token = _pair(client, _mint_code(client)).json()["token"]
    hdr = {"Authorization": "Bearer " + token}

    assert client.get("/api/v1/quik/robots-mirror", headers=hdr).status_code == 401
    assert client.get("/api/v1/quik/agent-local-status", headers=hdr).status_code == 401
    assert client.post("/api/v1/quik/robots/x/pause-agent", json={},
                       headers=hdr).status_code == 401
    # Unauthenticated is unauthenticated.
    assert client.get("/api/v1/quik/companion/snapshot").status_code == 401
    assert client.get("/api/v1/quik/companion/devices", headers=hdr).status_code == 401


def test_snapshot_reads_the_agent_mirror_shape(monkeypatch):
    """The mirror is the agent's own status JSON (agent/health/robots/recon) with
    _received_at_ms added at the TOP level — NOT wrapped in a "status" key. Read
    it wrong and every block renders empty while the API still answers 200, so
    this pins the shape with a realistic snapshot."""
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {
            "runner_healthy": True, "exchange_lag_ms": 700, "pong_age_ms": 900,
            "money": {"limit": 1_842_500.0, "used": 412_300.0, "planned": 1_430_200.0,
                      "varmargin": 12_480.0, "ts_comission": -1204.0, "age_ms": 900},
            "positions": [{"sec": "RIU6", "net": 2, "avg": 89_120.0, "varmargin": -3015.0},
                          {"sec": "GZU6", "net": 0, "avg": 0.0, "varmargin": None}],
        },
        "robots": [{"id": "agent-macd-RIU6-lxk22", "symbol": "RIU6", "mode": "real",
                    "paused": False, "position": 2, "pnl_rub": 24_180.0,
                    "float_rub": -3015.0}],
    }), 0)
    app.state.quik_store = store
    client = TestClient(app)

    body = client.get("/api/v1/quik/companion/snapshot",
                      headers=_operator_headers()).json()
    assert body["account"]["has_data"] is True
    assert body["account"]["used"] == 412_300.0
    # Flat instruments are dropped; the open one keeps its ВМ.
    assert [p["sec"] for p in body["positions"]] == ["RIU6"]
    assert body["positions"][0]["varmargin"] == -3015.0
    # Робот в реале по зеркалу -> показан со статусом «реал» (real_net из журнала,
    # тут журнал пуст -> None; важно, что робот попал в список как реальный).
    assert len(body["robots"]) == 1
    assert body["robots"][0]["state"] == "реал"
    assert "real_net" in body["robots"][0]
    assert body["agent_seen_ms"] > 0
    assert body["watch"]["runner"]["ok"] is True


def test_operator_can_read_snapshot_from_a_browser(monkeypatch):
    """The panel must be openable at /companion.html in a normal STL session, or
    fixing its layout would mean rebuilding the exe every time."""
    client, _ = _client(monkeypatch)
    r = client.get("/api/v1/quik/companion/snapshot", headers=_operator_headers())
    assert r.status_code == 200


@pytest.mark.parametrize("health,expect_ok,needle", [
    ({"runner_healthy": True, "exchange_lag_ms": 800, "pong_age_ms": 1200}, True, ""),
    ({"runner_healthy": False}, False, "раннер"),
    ({"runner_healthy": True, "exchange_lag_ms": 300_000}, False, "лента"),
    ({"runner_healthy": True, "pong_age_ms": 400_000}, False, "QUIK"),
    ({"runner_healthy": True, "daily_orders_used": 470, "daily_orders_cap": 500}, False, "лимит"),
    # vdsguard reports quik_state, not state — a wrong key here would silently
    # disable the check, so the field name is pinned by this case.
    ({"runner_healthy": True, "vds": {"quik_state": "HUNG"}}, False, "QUIK-гард"),
    ({"runner_healthy": True, "vds": {"quik_state": "DISABLED"}}, True, ""),
    ({"runner_healthy": True, "vds": {"quik_state": "OK", "low_memory": True}},
     False, "мало памяти"),
])
def test_watch_runner_verdicts(health, expect_ok, needle):
    now = 1_700_000_000_000
    out = _watch_runner(health, now - 5_000, now)
    assert out["ok"] is expect_ok
    assert needle in "; ".join(out["issues"])


def test_watch_runner_flags_a_dead_agent_link():
    now = 1_700_000_000_000
    out = _watch_runner({"runner_healthy": True}, now - 600_000, now)
    assert out["ok"] is False
    assert "нет связи с агентом" in "; ".join(out["issues"])


def test_every_alert_gets_a_control_note():
    """Оператор просил: каждая тревога заканчивается пометкой — рассосётся само
    или требует его включения."""
    assert "исправится автоматически" in _with_control("робот ПОСТАВЛЕН НА ПАУЗУ", False)
    assert "ТРЕБУЕТ" in _with_control("дневной лимит заявок 490/500", True)
    assert "ТРЕБУЕТ" in _with_control("на VDS мало памяти", True)
    # хорошая новость — без пометки
    assert _with_control("восстановилось: все проверки в норме", None) == \
        "восстановилось: все проверки в норме"
    # неизвестная тревога — считаем под наблюдением, а не молчим
    assert "контроле" in _with_control("что-то странное", None)


def test_stale_tape_is_silent_when_the_market_is_closed():
    """Гвоздь всей задачи: замершая лента при ЗАКРЫТОМ рынке — норма, не авария.
    При открытом (или неизвестном) рынке — по-прежнему тревога."""
    now = 1_700_000_000_000
    health = {"runner_healthy": True, "exchange_lag_ms": 8 * 3600 * 1000}
    # Рынок закрыт по ISS — лаг ленты НЕ поднимаем.
    closed = _watch_runner(health, now - 5_000, now, {"open": False})
    assert closed["ok"] is True
    assert "лента" not in "; ".join(closed["issues"])
    # Рынок открыт — лаг ленты это авария (окно всех сделок умерло).
    opened = _watch_runner(health, now - 5_000, now, {"open": True})
    assert opened["ok"] is False
    assert "лента отстаёт" in "; ".join(opened["issues"])
    # ISS недоступен (None) — трактуем защитно, тревога остаётся.
    unknown = _watch_runner(health, now - 5_000, now, {"open": None})
    assert "лента отстаёт" in "; ".join(unknown["issues"])


def test_exit_only_is_a_flag_next_to_state_not_a_new_state(monkeypatch):
    """«Только на выход» — режим ПОВЕРХ реала (робот закрывает свою позицию и
    новых не берёт). Панель делит роботов на активных и выведенных сравнением
    state с 'реал'/'пауза', поэтому отдельной строки state тут быть не должно:
    такой робот молча уехал бы в «выведены из реала»."""
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {"runner_healthy": True},
        "robots": [
            {"id": "r-exit", "symbol": "RIU6", "mode": "real", "paused": False,
             "params_json": '{"qty": 1, "exit_only": true}'},
            {"id": "r-plain", "symbol": "RIU6", "mode": "real", "paused": False,
             "params_json": '{"qty": 1}'},
            {"id": "r-broken", "symbol": "RIU6", "mode": "real", "paused": False,
             "params_json": "не json"},
        ],
    }), 0)
    app.state.quik_store = store

    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    by_id = {r["id"]: r for r in body["robots"]}
    assert by_id["r-exit"]["exit_only"] is True
    assert by_id["r-exit"]["state"] == "реал", "состояние остаётся реалом"
    assert by_id["r-plain"]["exit_only"] is False
    assert by_id["r-broken"]["exit_only"] is False, "битый params_json не должен ронять панель"


def test_mode_and_pause_are_separate_fields(monkeypatch):
    """«пауза» сама по себе не говорит, реал это или бумага: оператор не мог понять
    по панели, чем робот рискует. Режим и пауза — ОТДЕЛЬНЫЕ поля рядом со state
    (строки state трогать нельзя, по ним панель делит активных и выведенных)."""
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {"runner_healthy": True},
        "robots": [
            {"id": "r-real-paused", "symbol": "BRU6", "mode": "real", "paused": True},
            {"id": "r-real-live", "symbol": "BRU6", "mode": "real", "paused": False},
            {"id": "r-paper", "symbol": "BRU6", "mode": "paper", "paused": False},
        ],
    }), 0)
    app.state.quik_store = store

    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    by_id = {r["id"]: r for r in body["robots"]}
    assert by_id["r-real-paused"]["mode"] == "real"
    assert by_id["r-real-paused"]["paused"] is True
    assert by_id["r-real-paused"]["state"] == "пауза", "деление активных/выведенных не менять"
    assert by_id["r-real-live"]["paused"] is False
    # Чисто бумажный робот без реальной истории в панель не попадает вовсе (панель
    # только про реальные деньги) — режим показываем тем, кто в списке есть.
    assert "r-paper" not in by_id


class _LedgerPool(FakePool):
    """FakePool + одна строка пожизненной статистики робота из algo_trades,
    чтобы «пик ГО» и «доходность в год» реально считались."""

    def __init__(self, *, peak: int, net: float, first_ts: int):
        super().__init__()
        self.row = {"robot_id": "r1", "net": net, "trades": 10, "last_ts": first_ts,
                    "first_ts": first_ts, "qty": 10, "peak": peak}

    async def fetch(self, sql: str, *args):
        if "FROM algo_trades" in sql and "min(ts_ms)" in sql:
            return [self.row]
        return await super().fetch(sql, *args)


def test_margin_multiplier_lifts_peak_go_and_lowers_annual_return(monkeypatch):
    """Фид агента отдаёт БИРЖЕВОЕ ГО (BUYDEPO), а брокер списывает своё, кратно
    большее (RIU6 30.07.2026: биржа 22 375 ₽, счёт 53 672 ₽ = 2.4x). Без
    множителя «пик ГО» занижен, а доходность в год завышена ровно во столько же
    раз — деньги считаются по бирже, а рискует счёт."""
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    day = 86_400_000
    first_ts = int(time.time() * 1000) - 30 * day        # 30 дней торговли

    def snap(mult: float) -> dict:
        app = FastAPI()
        app.include_router(companion_router)
        settings = _Settings()
        settings.quik_margin_multiplier = mult
        app.state.settings = settings
        app.state.db_pool = _LedgerPool(peak=10, net=100_000.0, first_ts=first_ts)
        store = QuikAgentStore()
        store.set_agent_status("A1", json.dumps({
            "agent": {"version": "x", "link_up": True},
            "health": {"runner_healthy": True,
                       "params": [{"code": "RIU6", "margin": 22_375.0}]},
            "robots": [{"id": "r1", "symbol": "RIU6", "mode": "real", "paused": False,
                        "position": 0}],
        }), 0)
        app.state.quik_store = store
        body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                                   headers=_operator_headers()).json()
        return {r["id"]: r for r in body["robots"]}["r1"]

    plain, lifted = snap(1.0), snap(2.4)

    # Пик ГО = биржевое × множитель × пик контрактов.
    assert plain["max_go"] == pytest.approx(22_375.0 * 10)
    assert lifted["max_go"] == pytest.approx(22_375.0 * 2.4 * 10)

    # Годовая падает ровно во столько же раз: тот же фикс к втрое большему ГО.
    assert plain["ann_pct"] is not None and lifted["ann_pct"] is not None
    assert lifted["ann_pct"] == pytest.approx(plain["ann_pct"] / 2.4)


def test_margin_multiplier_defaults_to_exchange_margin(monkeypatch):
    """Без настройки поведение прежнее: ГО = биржевое (множитель 1)."""
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()          # атрибута quik_margin_multiplier нет вовсе
    app.state.db_pool = _LedgerPool(peak=4, net=1_000.0,
                                    first_ts=int(time.time() * 1000) - 10 * 86_400_000)
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {"runner_healthy": True, "params": [{"code": "RIU6", "margin": 22_375.0}]},
        "robots": [{"id": "r1", "symbol": "RIU6", "mode": "real", "paused": False}],
    }), 0)
    app.state.quik_store = store

    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    assert {r["id"]: r for r in body["robots"]}["r1"]["max_go"] == pytest.approx(22_375.0 * 4)


def test_lamp_rejects_candidates_whose_warmup_outlives_the_runner_tail():
    """Лампа «кандидат» не предлагает строку, которую нельзя запустить роботом.

    Раннер персистит 600 закрытых баров: конфиг с прогревом 1600 после каждого
    рестарта агента слеп ~17 часов. В бэктесте цифра честная, в бою недостижима.
    Формула прогрева берётся у самой стратегии — бланкетный «4×max периодов»
    завышал бы её SMA-семейству и вовсе не видел pivot (2200 при любых полях).
    """
    from trader.api.quik_companion import _warmup_fits

    assert _warmup_fits("shectory_2ema", {"ema1": 60, "ema2": 20}) is True   # 240
    assert _warmup_fits("shectory_2ema", {"ema1": 60, "ema2": 400}) is False  # 1600
    # контр-стратегия судится по своей базе
    assert _warmup_fits("shectory_2ema__inv", json.dumps({"ema1": 3, "ema2": 400})) is False
    assert _warmup_fits("macd_cross", {"fast": 12, "slow": 26, "signal": 9}) is True
    assert _warmup_fits("pivot_reversal", {}) is False        # 2200 всегда
    # судим только то, о чём знаем: модуль вне реестра и битые params пропускаем
    assert _warmup_fits("us_open_fvg", {"open_hour": 16}) is True
    assert _warmup_fits("shectory_2ema", "не json") is True
    assert _warmup_fits(None, {}) is True


# ── лампа кандидата: размер позиции не должен выглядеть как заслуга ──────────

def test_star_rejects_a_row_the_account_cannot_carry():
    """15.08.2026 наверх лампы вышла triple_sma RIU6 с 1 013 943 ₽ — и это был
    qty=19 при пике 35 контрактов. При честном одном контракте тот же конфиг
    даёт 274k на своём контракте и МИНУС 27k на соседнем в том же окне.

    Лампа сортирует по net, а net растёт линейно с числом контрактов, поэтому
    отбор обязан проверять деньги: полный набор строки должен влезать в половину
    свободного ГО счёта, считая по ГО СЧЁТА (биржевое × множитель брокера).
    """
    from trader.api.quik_companion import _account_margin, _margin_fits, _max_position

    ri_margin = 22187.11                      # биржевое ГО RIU6 на 15.08.2026
    fat = {"qty": 19, "avg_max": 20, "bet_max": 10}
    assert _max_position(fat) == 29           # qty+bet_max хуже, чем avg_max
    assert not _margin_fits(fat, ri_margin)

    lean = {"qty": 1, "avg_max": 4}
    assert _max_position(lean) == 4
    assert _margin_fits(lean, ri_margin)
    assert _account_margin(lean, ri_margin) == round(4 * ri_margin * 2.4)


def test_star_does_not_judge_what_it_cannot_measure():
    """Экономика инструмента неизвестна — строку не режем: молча выбросить её
    из-за отсутствующего числа хуже, чем показать оператору как есть."""
    from trader.api.quik_companion import _account_margin, _margin_fits

    assert _account_margin({"qty": 99}, None) is None
    assert _margin_fits({"qty": 99}, None) is True
    assert _margin_fits({"qty": 99}, 0) is True


def test_max_position_survives_broken_params():
    from trader.api.quik_companion import _max_position

    assert _max_position({}) == 1
    assert _max_position({"qty": "x", "avg_max": None}) == 1
    assert _max_position('{"qty": 2, "avg_max": 7}') == 7      # params строкой из БД
