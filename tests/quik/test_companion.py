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

from trader.api import quik_companion
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


def test_star_annual_is_linear_and_shares_the_printed_go():
    """Годовая кандидата: линейно и от ТОГО ЖЕ ГО, что печатается рядом.

    Живой случай 14.08.2026: +65 723 ₽ на ГО 251 435 ₽ за 14 дней. Сложная
    формула базы давала 45 897% годовых — панель обещала 458 концов за год по
    двум неделям истории.
    """
    from datetime import date

    from trader.api.quik_companion import _star_return

    r = _star_return(65723, 251435, date(2026, 7, 16), date(2026, 7, 30))
    assert r["period_return_pct"] == 26
    assert r["ann_go_pct"] == 681                      # 26.14% × 365/14
    assert r["ann_go_pct"] < 1000                      # не сложные проценты

    # Нет ГО или нет окна — молчим обеими цифрами, а не показываем ноль.
    assert _star_return(65723, None, date(2026, 7, 16), date(2026, 7, 30)) == {
        "ann_go_pct": None, "period_return_pct": None}
    assert _star_return(65723, 251435, None, None) == {
        "ann_go_pct": None, "period_return_pct": None}
    assert _star_return(65723, 251435, date(2026, 7, 16), date(2026, 7, 16)) == {
        "ann_go_pct": None, "period_return_pct": None}
    # Пустая прибыль — НЕИЗВЕСТНО, а не измеренный ноль.
    assert _star_return(None, 251435, date(2026, 7, 16), date(2026, 7, 30)) == {
        "ann_go_pct": None, "period_return_pct": None}
    # А настоящий ноль остаётся нулём: строка отработала и не заработала.
    assert _star_return(0, 251435, date(2026, 7, 16), date(2026, 7, 30)) == {
        "ann_go_pct": 0, "period_return_pct": 0}


def test_vm_splits_between_robots_and_manual_and_sums_to_account(monkeypatch):
    """ВМ счёта обязана складываться из «сегодня» роботов и «Итога ручных».

    04.09.2026 они не сходились: ВМ была +16 248 ₽, роботы вместе давали +5 074 ₽,
    а остальное сделал оператор своими руками — и показать это было негде, поэтому
    разница читалась как ошибка учёта робота. Агент считает разбивку (блок `day`),
    панель обязана взять её как есть, а не пересчитывать по-своему.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {
            "runner_healthy": True,
            "money": {"limit": 1.0, "varmargin": 16_247.89, "ts_comission": -1863.27,
                      "age_ms": 100},
            "positions": [{"sec": "RIU6", "net": -2, "avg": 82_426.0,
                           "varmargin": 16_247.89}],
        },
        "robots": [{"id": "lxk22", "symbol": "RIU6", "mode": "real", "paused": False,
                    "position": -1, "avg_price": 82_650.0}],
        "day": {
            "ok": True, "from_ms": 1, "sum_rub": 16_247.89, "quik_vm": 16_247.89,
            "residual": 0.0,
            "classes": [
                {"key": "lxk22", "kind": "robot", "sec": "RIU6", "vm_rub": 5_074.0,
                 "fills": 116, "lots": 232, "net_end": -1, "net_start": 0},
                {"key": "terminal", "kind": "terminal", "sec": "RIU6",
                 "vm_rub": 8_029.0, "fills": 1, "lots": 8, "net_end": 0},
                {"key": "smart", "kind": "smart", "sec": "RIU6", "vm_rub": 3_144.89,
                 "fills": 2, "lots": 8, "net_end": 0},
            ],
        },
    }), 0)
    app.state.quik_store = store
    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()

    robot = next(r for r in body["robots"] if r["id"] == "lxk22")
    assert robot["today_total"] == 5_074.0        # доля робота, а не наша арифметика
    manual = body["orders"]["today"]
    assert manual["total"] == pytest.approx(11_173.89)
    assert [r["kind"] for r in manual["rows"]] == ["terminal", "smart"]
    assert robot["today_total"] + manual["total"] == pytest.approx(
        body["account"]["varmargin"])
    assert body["account"]["vm_check"]["ok"] is True


def test_armed_smart_orders_survive_the_snapshot_cut(monkeypatch):
    """Взведённые заявки не вытесняются сегодняшней историей.

    17.09.2026 оператор: панель показывала 2 следящие заявки, а в книге их было 4.
    Список уходил в панель в порядке книги и резался двадцатой строкой, поэтому
    сработавшие и снятые за день выталкивали живые. Плюс счётчик активных: без него
    панель с выключенным фильтром считала видимые строки и повторяла ту же обрезку.
    """
    import time
    from types import SimpleNamespace

    now = int(time.time() * 1000)

    def _so(so_id, kind, status, created):
        return SimpleNamespace(
            so_id=so_id, kind=kind, status=status, code="RIZ6", side="sell", qty=1,
            trigger_price=90000.0, trail_offset=50.0, activated=False, peak=0.0,
            created_ms=created, fired_ms=created, fired_price=0.0, fired_qty=0,
            sl_offset=0.0, tp_offset=0.0, tp_trail=0.0, parent_id="")

    # Сначала 24 сегодняшние отработавшие, ЗАТЕМ 4 взведённые — прежний код резал
    # список до двадцати строк и живые в панель не попадали вовсе.
    book = [_so(f"done{i}", "trail_tp", "fired", now - 1000) for i in range(24)]
    book += [_so(f"live{i}", "trail_tp", "armed", now) for i in range(4)]

    client, _ = _client(monkeypatch)
    client.app.state.smart_orders = SimpleNamespace(orders=book)
    snap = client.get("/api/v1/quik/companion/snapshot", headers=_operator_headers())
    assert snap.status_code == 200, snap.text
    orders = snap.json()["orders"]

    armed = [o for o in orders["smart"] if o["status"] == "armed"]
    assert len(armed) == 4, "живые заявки обрезаны историей"
    assert orders["counts_active"]["trail_tp"] == 4      # столько их в книге
    assert orders["counts"]["trail_tp"] == 28            # весь день, до обрезки
    assert len(orders["smart"]) == 20                    # потолок панели не вырос


def test_native_orders_count_as_live_in_the_snapshot(monkeypatch):
    """Заявка под охраной терминала (native) — живая.

    real-trade 17.09.2026: после входа STL ставит нативную стоп-заявку QUIK на всю
    защитную связку, и такая защита переживает падение STL. Делить список по одному
    `armed` значит уронить действующую защиту в историю: за обрезку в 20 строк, из
    счётчика живых и из выдачи вовсе, если она взведена не сегодня.
    """
    import time
    from types import SimpleNamespace

    now = int(time.time() * 1000)
    old = now - 3 * 24 * 3600 * 1000          # позавчерашняя: по дате отсеялась бы

    def _so(so_id, status, created, native_state="", num=""):
        return SimpleNamespace(
            so_id=so_id, kind="sl", status=status, code="RIZ6", side="sell", qty=1,
            trigger_price=87000.0, trail_offset=0.0, activated=False, peak=0.0,
            created_ms=created, fired_ms=created, fired_price=0.0, fired_qty=0,
            sl_offset=0.0, tp_offset=0.0, tp_trail=0.0, parent_id="p",
            native_state=native_state, native_stop_num=num)

    book = [_so(f"done{i}", "fired", now - 1000) for i in range(24)]
    book += [_so("nat", "native", old, "live", 1900000000000000123)]

    client, _ = _client(monkeypatch)
    client.app.state.smart_orders = SimpleNamespace(orders=book)
    snap = client.get("/api/v1/quik/companion/snapshot", headers=_operator_headers())
    assert snap.status_code == 200, snap.text
    orders = snap.json()["orders"]

    nat = [o for o in orders["smart"] if o["status"] == "native"]
    assert nat, "заявка под охраной терминала не доехала до панели"
    assert orders["counts_active"]["sl"] == 1          # живая, хоть и не armed
    # Номер стоп-заявки QUIK ~1.9e18: строкой, иначе JSON потеряет последние цифры.
    assert nat[0]["native_stop_num"] == "1900000000000000123"
    assert nat[0]["native_state"] == "live"


def test_each_half_of_the_position_carries_its_own_average(monkeypatch):
    """Средняя у роботной и ручной половины РАЗНАЯ, и одна на двоих врёт.

    29.09.2026 на счёте стояло «Роботы +5 · Ручные −40»: половины в разные
    стороны. Средняя QUIK по нетто (83 007) — точка безубытка всей позиции, а не
    цена входа ни одной из половин, и печатать её как «среднюю» значит подсунуть
    оператору число, по которому он не может считать ни ту, ни другую.

    Роботная считается по собственным ценам входа раннеров: Σ(поз×вход)/Σпоз.
    Ручная берётся ГОТОВОЙ из журнала ручной торговли — второй расчёт был бы
    третьей версией одной цифры, а журнал и панель обязаны говорить одно.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {
            "runner_healthy": True,
            "money": {"limit": 1.0, "varmargin": -37_663.0, "age_ms": 100},
            "positions": [{"sec": "RIZ6", "net": -35, "avg": 83_007.0,
                           "varmargin": -31_163.0}],
        },
        "robots": [
            {"id": "lxk22", "symbol": "RIZ6", "mode": "real", "paused": False,
             "position": 6, "avg_price": 82_810.0},
            {"id": "macdshort", "symbol": "RIZ6", "mode": "real", "paused": False,
             "position": -1, "avg_price": 82_730.0},
            # Флэт входа не имеет: его ноль не должен ни съехать в числитель,
            # ни утянуть знаменатель.
            {"id": "usopen", "symbol": "RIZ6", "mode": "real", "paused": False,
             "position": 0, "avg_price": 0.0},
            # Бумажный робот на том же инструменте в реальную среднюю не входит.
            {"id": "paper", "symbol": "RIZ6", "mode": "paper", "paused": False,
             "position": 99, "avg_price": 99_999.0},
        ],
    }), 0)
    app.state.quik_store = store
    monkeypatch.setattr(quik_companion, "_manual_block",
                        lambda _store: {"open": [{"symbol": "RIZ6", "position": -40,
                                                  "avg_price": 82_984.4}]})
    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()

    pos = next(p for p in body["positions"] if p["sec"] == "RIZ6")
    assert pos["robot_net"] == 5 and pos["manual_net"] == -40
    # (6×82810 − 1×82730) / 5 — безубыток роботной половины, а не средняя QUIK.
    assert pos["robot_avg"] == pytest.approx(82_826.0)
    assert pos["robot_avg"] != pos["avg"]
    assert pos["manual_avg"] == pytest.approx(82_984.4)   # как в журнале, без своего счёта


def test_a_half_without_a_known_entry_says_none_not_zero(monkeypatch):
    """Не знаем вход — None. Ноль на экране читается как «вошли по нулю».

    У ручной половины средней может не быть честно: остаток окна журнала бывает
    противоположного знака, и manual_pnl в этом случае отдаёт None, а не среднюю
    ЧУЖОЙ позиции. Панель обязана промолчать, а не подставить ноль.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {
            "runner_healthy": True,
            "money": {"limit": 1.0, "varmargin": -1.0, "age_ms": 100},
            "positions": [{"sec": "RIZ6", "net": -40, "avg": 83_007.0,
                           "varmargin": -1.0}],
        },
        # Роботов на инструменте нет вовсе — роботной средней быть неоткуда.
        # Робот в инструменте ЕСТЬ, но входа своего не знает (avg_price пуст), а
        # журнал не видел набора позиции. Тогда неизвестны обе средние — и ни
        # одну нельзя подменить средней QUIK: она про всю позицию целиком.
        "robots": [{"id": "lxk22", "symbol": "RIZ6", "mode": "real", "paused": False,
                    "position": -5, "avg_price": 0.0}],
    }), 0)
    app.state.quik_store = store
    monkeypatch.setattr(quik_companion, "_manual_block",
                        lambda _store: {"open": [{"symbol": "RIZ6", "position": -35,
                                                  "avg_price": None}]})
    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()

    pos = next(p for p in body["positions"] if p["sec"] == "RIZ6")
    assert pos["robot_avg"] is None
    assert pos["manual_avg"] is None
    assert pos["manual_avg_why"]          # молчать нельзя: сказано, ЧЕГО не знаем


def test_ema_series_is_aligned_to_the_bars_the_panel_gets():
    """Серия раннера кладётся на ОТДАННЫЕ бары, а не уезжает своей длиной.

    30.09.2026 оператор не видел кривых EMA у MACD-роботов. Причина: раннер
    считает серию на своём хвосте в 200 баров и шлёт её целиком, панель просит
    30, и длины не сходились — клиент не рисовал НИЧЕГО и молча. Выравниваем по
    ВРЕМЕНИ БАРА: позиции после выброса неторговых минут съезжают, а съехавшая
    EMA — линия, похожая на правду и ею не являющаяся.
    """
    raw = [{"t": 100 + i, "c": 1.0} for i in range(200)]
    series = {"fast": [float(i) for i in range(200)],
              "slow": [float(i) / 2 for i in range(200)]}
    out_tail = raw[-30:]
    got = quik_companion._align_series(raw, series, out_tail)
    assert len(got["fast"]) == len(out_tail) == 30
    assert got["fast"][0] == 170.0 and got["fast"][-1] == 199.0
    assert got["slow"][-1] == 99.5


def test_a_restored_bar_gets_no_invented_ema():
    """Бара, которого раннер не считал, в серии нет — там None.

    Панель разорвёт линию, а не проведёт её через выдуманную точку. Дыры в
    хвосте закрываются кэшем инструмента (restored), и EMA робота там не
    существует: он на этих минутах ничего не считал.
    """
    raw = [{"t": 100 + i, "c": 1.0} for i in range(5)]
    series = {"fast": [1.0, 2.0, 3.0, 4.0, 5.0]}
    out_tail = [{"t": 101}, {"t": 999, "restored": True}, {"t": 104}]
    got = quik_companion._align_series(raw, series, out_tail)
    assert got["fast"] == [2.0, None, 5.0]


def test_a_series_of_unknown_length_is_dropped_not_guessed():
    """Длины не сошлись — молчим, а не подгоняем срезом.

    Срез означал бы «какому бару какое значение, мы не знаем, но нарисуем»:
    кривая сдвинулась бы на неизвестное число минут и выглядела бы настоящей.
    """
    raw = [{"t": 100 + i, "c": 1.0} for i in range(10)]
    assert quik_companion._align_series(raw, {"fast": [1.0, 2.0]}, raw[-3:]) is None
    assert quik_companion._align_series(raw, None, raw[-3:]) is None
    assert quik_companion._align_series(raw, {"fast": [1.0] * 10}, []) is None


def test_average_price_sits_on_the_instrument_grid():
    """Средняя округляется до ШАГА ЦЕНЫ инструмента (просьба оператора 30.09.2026).

    Средняя это результат деления, и на экране она выглядела как
    84722.777777778: цены такой не бывает, а глазом её сравнивают с ценами,
    которые бывают. Дробный шаг при этом к целым не округляем — у BR он 0.01.
    """
    assert quik_companion._snap_price(84_722.7777, 10) == 84_720
    assert quik_companion._snap_price(84_725.0, 10) == 84_730       # к ближайшему, не вниз
    assert quik_companion._snap_price(95.174, 0.01) == 95.17
    assert quik_companion._snap_price(95.176, 0.01) == 95.18


def test_an_unknown_step_leaves_the_price_alone():
    """Шага не знаем — цену НЕ трогаем.

    Округлить «на всякий случай» к целым значило бы испортить инструменты с
    дробным шагом; а None остаётся None: «не знаю» не превращается в ноль.
    """
    assert quik_companion._snap_price(95.174, 0) == 95.174
    assert quik_companion._snap_price(None, 10) is None
    assert quik_companion._snap_price(0, 10) == 0


def test_manual_average_equals_the_quik_one_when_no_robots_hold_the_symbol(monkeypatch):
    """Роботов в инструменте нет — ручное это ВСЯ позиция, и средняя QUIK её же.

    Журнал знает среднюю только для позиции, набранной внутри окна; набранную
    раньше он честно отдаёт как None, и оператор видел пустое место. Но когда
    роботов нет, выдумывать нечего: средняя счёта И ЕСТЬ ручная средняя.
    Смешивать её с ценами входа раннеров при живых роботах по-прежнему нельзя —
    у QUIK своя база.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {
            "runner_healthy": True,
            "money": {"limit": 1.0, "varmargin": -1.0, "age_ms": 100},
            "positions": [{"sec": "RIZ6", "net": -43, "avg": 84_805.4, "varmargin": -1.0}],
        },
        "robots": [],
    }), 0)
    app.state.quik_store = store
    monkeypatch.setattr(quik_companion, "_manual_block",
                        lambda _store: {"open": [{"symbol": "RIZ6", "position": -43,
                                                  "avg_price": None}]})
    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    pos = next(p for p in body["positions"] if p["sec"] == "RIZ6")
    assert pos["manual_avg"] == pytest.approx(84_805.4)
    assert pos["manual_avg_why"] == ""
    # И ОБЯЗАТЕЛЬНО ПОМЕЧЕНА ИСТОЧНИКОМ. Это средняя счёта, а она пересчитывается
    # к клиринговой цене: real-trade 30.09.2026 показал позицию RIZ6 −43, которая
    # не менялась и по которой не было сделок, а средняя за ночь уехала с 83370
    # на 84920 — 1550 пунктов чистого пересчёта. Подписать её «ценой входа»
    # значит повторить смешение баз, на котором горели с ВМ.
    assert pos["manual_avg_src"] == "quik"


def test_manual_average_stays_unknown_while_robots_hold_the_same_symbol(monkeypatch):
    """Роботы в инструменте есть — ручную среднюю НЕ выводим вычитанием.

    Средняя QUIK относится ко ВСЕЙ позиции и живёт на своей базе; цены входа
    раннеров — на своей. Разность двух баз дала бы число, похожее на правду.
    Вместо него говорим, ЧЕГО не знаем: пустое место читается как поломка.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {
            "runner_healthy": True,
            "money": {"limit": 1.0, "varmargin": -1.0, "age_ms": 100},
            "positions": [{"sec": "RIZ6", "net": -61, "avg": 84_781.0, "varmargin": -1.0}],
        },
        "robots": [{"id": "lxk22", "symbol": "RIZ6", "mode": "real", "paused": False,
                    "position": -18, "avg_price": 84_722.0}],
    }), 0)
    app.state.quik_store = store
    monkeypatch.setattr(quik_companion, "_manual_block",
                        lambda _store: {"open": [{"symbol": "RIZ6", "position": -43,
                                                  "avg_price": None}]})
    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    pos = next(p for p in body["positions"] if p["sec"] == "RIZ6")
    assert pos["manual_avg"] is None
    assert pos["manual_avg_src"] == ""
    assert "роботами" in pos["manual_avg_why"]


def test_every_order_kind_in_the_book_gets_counted(monkeypatch):
    """Счётчики видов берутся ИЗ КНИГИ, а не из списка в коде.

    Коридор, треугольник и радиацию завели 29.09–30.09.2026, а перечисление
    видов в снапшоте осталось прежним: их заявки не считались вовсе, и на панели
    они молча падали в чужую группу. Оператор 01.10: «новых умных заявок в
    компаньоне нет» — они были, просто не под своим именем.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {"runner_healthy": True,
                   "money": {"limit": 1.0, "varmargin": 0.0, "age_ms": 100},
                   "positions": []},
        "robots": [],
    }), 0)
    app.state.quik_store = store

    now = int(time.time() * 1000)

    class _SO:
        def __init__(self, kind, **kw):
            self.so_id = f"id-{kind}"
            self.kind, self.code, self.side, self.qty = kind, "RIZ6", "sell", 1
            self.status, self.created_ms, self.fired_ms = "armed", now, 0
            self.trigger_price = self.trail_offset = 0.0
            self.activated, self.peak = False, 0.0
            self.fired_price = self.fired_qty = 0
            self.sl_offset = self.tp_offset = self.tp_trail = 0.0
            self.parent_id = ""
            self.c_t1_ms = self.c_t2_ms = 0
            self.c_p1 = self.c_p2 = self.c_low = self.c_low2 = 0.0
            self.c_stop_pts = self.c_flips = self.c_flips_max = self.c_pos = 0
            self.c_done = self.g_done = False
            self.g_step = self.g_buys = self.g_sells = self.g_lot = 0
            self.g_base = self.g_stop_pts = self.g_pos = 0
            for k, v in kw.items():
                setattr(self, k, v)

    class _Book:
        orders = [_SO("corridor", c_t1_ms=now - 60_000, c_p1=84_000.0,
                      c_t2_ms=now, c_p2=84_100.0, c_low=83_000.0, c_stop_pts=50),
                  _SO("triangle", c_t1_ms=now - 60_000, c_p1=84_000.0,
                      c_t2_ms=now, c_p2=84_100.0, c_low=83_000.0, c_low2=83_500.0),
                  _SO("grid", g_step=50, g_buys=3, g_sells=2, g_lot=2, g_base=84_000.0)]

    app.state.smart_orders = _Book()
    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    counts = body["orders"]["counts"]
    for kind in ("corridor", "triangle", "grid"):
        assert counts.get(kind) == 1, (kind, counts)
    # И старые виды не исчезли из счётчиков, даже когда их нет в книге: группа с
    # нулём должна оставаться на экране, оператор помнит её МЕСТО.
    for kind in ("sl", "tp", "trail_tp", "trail_sl", "on_fill"):
        assert kind in counts


def test_figure_and_grid_carry_their_own_fields_to_the_panel(monkeypatch):
    """У фигуры и сетки уровень срабатывания не один — панели нужны их поля.

    Без них строка про коридор сообщает ровно ничего: «уровень —». Стенки
    считает ДВИЖОК (c_now), как и для SPA: повторять геометрию на панели нельзя,
    две реализации одной прямой расходятся.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {"runner_healthy": True,
                   "money": {"limit": 1.0, "varmargin": 0.0, "age_ms": 100},
                   "positions": []},
        "robots": [],
    }), 0)
    app.state.quik_store = store
    now = int(time.time() * 1000)

    class _SO:
        def __init__(self, kind, **kw):
            self.so_id, self.kind, self.code = f"id-{kind}", kind, "RIZ6"
            self.side, self.qty, self.status = "sell", 1, "armed"
            self.created_ms, self.fired_ms = now, 0
            self.trigger_price = self.trail_offset = 0.0
            self.activated, self.peak = False, 0.0
            self.fired_price = self.fired_qty = 0
            self.sl_offset = self.tp_offset = self.tp_trail = 0.0
            self.parent_id = ""
            self.c_t1_ms = now - 3_600_000
            self.c_t2_ms = now
            self.c_p1, self.c_p2, self.c_low, self.c_low2 = 84_000.0, 84_600.0, 83_000.0, 0.0
            self.c_stop_pts, self.c_flips, self.c_flips_max, self.c_pos = 40, 0, 0, -2
            self.c_done = self.g_done = False
            self.g_step = self.g_buys = self.g_sells = self.g_lot = 0
            self.g_base = self.g_stop_pts = self.g_pos = 0
            self.g_avg = self.c_avg = 0.0
            self.exit_only = False
            for k, v in kw.items():
                setattr(self, k, v)

    class _Book:
        orders = [_SO("corridor"),
                  _SO("grid", g_step=50, g_buys=3, g_sells=2, g_lot=2,
                      g_base=84_000.0, g_stop_pts=100, g_pos=4,
                      g_avg=85_510.0, exit_only=True)]

    app.state.smart_orders = _Book()
    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    smart = {o["kind"]: o for o in body["orders"]["smart"]}

    corr = smart["corridor"]
    assert corr["c_now"]["top"] > corr["c_now"]["low"]
    assert corr["c_now"]["width"] == pytest.approx(1000.0)   # ширина в первой точке
    assert corr["c_stop_pts"] == 40 and corr["c_pos"] == -2

    grid = smart["grid"]
    assert grid["g_step"] == 50 and grid["g_buys"] == 3 and grid["g_lot"] == 2
    assert grid["g_stop_pts"] == 100 and grid["g_pos"] == 4
    # РЕЖИМ «ТОЛЬКО НА ВЫХОД» и средняя его позиции (real-trade 02.10.2026). В
    # этом режиме фигура намеренно ничего не ставит, пока цена хуже средней; без
    # этих полей панель рисует её так же, как рабочую — «взведена» и тишина, то
    # есть ровно как сломанную.
    assert grid["exit_only"] is True
    assert grid["g_avg"] == pytest.approx(85_510.0)
    assert smart["corridor"]["exit_only"] is False


def test_each_order_gets_its_own_pnl():
    """P&L СВОЕЙ заявки: фикс по её кругам и переоценка её остатка.

    Заказ оператора 01.10.2026. Многоразовая заявка (коридор, сетка) за свою
    жизнь и покупает, и продаёт — её фикс определён полностью. У одноразовой
    закрывать нечего, и фикс честно ноль: приписать ей чужое закрытие было бы
    выдумкой.
    """
    from trader.api.order_pnl import pnl_by_order

    t0 = 1_790_000_000_000
    fills = [
        # Коридор: купил 2 по 84 000, продал 2 по 84 100 — круг закрыт.
        {"tag": "stl-so-aaa", "sec": "RIZ6", "side": "buy", "qty": 2,
         "price": 84_000, "ts_ms": t0},
        {"tag": "stl-so-aaa", "sec": "RIZ6", "side": "sell", "qty": 2,
         "price": 84_100, "ts_ms": t0 + 1000},
        # И снова купил 1 — позиция открыта, её переоценим по рынку.
        {"tag": "stl-so-aaa", "sec": "RIZ6", "side": "buy", "qty": 1,
         "price": 84_050, "ts_ms": t0 + 2000},
        # Одноразовая: только продала, закрывать нечего.
        {"tag": "stl-so-bbb", "sec": "RIZ6", "side": "sell", "qty": 3,
         "price": 84_200, "ts_ms": t0 + 3000},
    ]
    out = pnl_by_order(fills, {"RIZ6": 84_300.0}, {"RIZ6": 2.0})

    a = out["aaa"]
    assert a["fix_pts"] == pytest.approx(200.0)      # 2 × 100 пунктов
    assert a["fix_rub"] == pytest.approx(400.0)      # ×2 ₽/пункт
    assert a["pos"] == 1 and a["avg"] == pytest.approx(84_050.0)
    assert a["vm_pts"] == pytest.approx(250.0)       # (84 300 − 84 050) × 1
    assert a["priced"] is True

    b = out["bbb"]
    assert b["fix_pts"] == 0.0                       # закрывать было нечего
    assert b["pos"] == -3
    assert b["vm_pts"] == pytest.approx(-300.0)      # шорт против себя


def test_child_legs_belong_to_their_parent_order():
    """Суффикс ноги не делает новую заявку.

    Тег ребёнка бывает `stl-so-<id>:lo` / `:gm` — это та же заявка, другая её
    нога. Не отрезав суффикс, одна заявка распалась бы на несколько строк, и
    каждая показала бы кусок её же результата.
    """
    from trader.api.order_pnl import order_key, pnl_by_order

    assert order_key({"tag": "stl-so-4e99b8c5ef:lo"}) == "4e99b8c5ef"
    assert order_key({"tag": "stl-so-4e99b8c5ef"}) == "4e99b8c5ef"
    # Терминальная заявка опознаётся своим номером.
    assert order_key({"tag": "", "order_num": "19250402"}) == "19250402"

    t0 = 1_790_000_000_000
    out = pnl_by_order([
        {"tag": "stl-so-x", "sec": "RIZ6", "side": "buy", "qty": 1, "price": 100, "ts_ms": t0},
        {"tag": "stl-so-x:lo", "sec": "RIZ6", "side": "sell", "qty": 1, "price": 110,
         "ts_ms": t0 + 1},
    ], {"RIZ6": 110.0}, {"RIZ6": 1.0})
    assert list(out) == ["x"]
    assert out["x"]["fix_pts"] == pytest.approx(10.0)


def test_points_are_not_rubles_when_the_coefficient_is_unknown():
    """Нет ₽/пункт — рублей НЕТ, а не ноль рублей."""
    from trader.api.order_pnl import pnl_by_order

    out = pnl_by_order(
        [{"tag": "stl-so-z", "sec": "XXZ9", "side": "buy", "qty": 1, "price": 100,
          "ts_ms": 1},
         {"tag": "stl-so-z", "sec": "XXZ9", "side": "sell", "qty": 1, "price": 120,
          "ts_ms": 2}],
        {"XXZ9": 120.0}, {})
    z = out["z"]
    assert z["fix_pts"] == pytest.approx(20.0)
    assert z["fix_rub"] is None and z["priced"] is False


def test_an_unknown_price_leaves_the_mark_to_market_unknown():
    """Цены нет — переоценки нет. Ноль читался бы как «в нуле»."""
    from trader.api.order_pnl import pnl_by_order

    out = pnl_by_order(
        [{"tag": "stl-so-q", "sec": "RIZ6", "side": "buy", "qty": 1, "price": 100, "ts_ms": 1}],
        {}, {"RIZ6": 1.0})
    assert out["q"]["vm_pts"] is None and out["q"]["vm_rub"] is None


def test_order_pnl_uses_the_params_handed_to_it(monkeypatch):
    """₽/пункт берётся из фида, прочитанного ОДИН раз на запрос.

    01.10.2026 p&l заявок приезжал с priced=false, пока соседний блок в том же
    запросе получал те же строки и округлял по ним средние: два чтения одного
    фида разошлись между собой. Теперь источник один и передаётся явно — чем бы
    ни была причина расхождения, второго источника больше нет.
    """
    called = {}

    def _fake(fills, last, pv):
        called["pv"] = pv
        return {"x": {"fix_rub": 1.0}}

    import trader.api.order_pnl as _op
    monkeypatch.setattr(_op, "pnl_by_order", _fake)
    monkeypatch.setattr("trader.quik.manual_pnl.read_trades", lambda *a, **k: [])

    out = quik_companion._orders_pnl(
        None, 0, None,
        {"rows": [{"code": "RIZ6", "price_step": 10.0, "step_cost": 16.71176}]})
    assert out == {"x": {"fix_rub": 1.0}}
    assert called["pv"]["RIZ6"] == pytest.approx(1.671176)


def test_order_pnl_survives_a_missing_params_feed(monkeypatch):
    """Фида нет — считаем в пунктах, а не падаем и не выдаём рубли."""
    monkeypatch.setattr("trader.quik.manual_pnl.read_trades", lambda *a, **k: [
        {"tag": "stl-so-a", "sec": "RIZ6", "side": "buy", "qty": 1, "price": 100, "ts_ms": 1},
        {"tag": "stl-so-a", "sec": "RIZ6", "side": "sell", "qty": 1, "price": 110, "ts_ms": 2},
    ])
    out = quik_companion._orders_pnl(None, 0, None, {})
    assert out["a"]["fix_pts"] == pytest.approx(10.0)
    assert out["a"]["fix_rub"] is None and out["a"]["priced"] is False


def test_order_pnl_takes_the_mirror_from_the_caller(monkeypatch):
    """Зеркало читается ОДИН раз на запрос, а не ещё раз внутри помощника.

    store.x(None) мигает: в сторе живут ДВЕ записи агента — служебная, созданная
    до Register, и настоящая под host_name. _pick(None) отдаёт агента, только
    пока РОВНО ОДИН из них зелёный; на переподключении зелёных ноль, и тот же
    вызов возвращает None (разбор real-trade 02.10.2026). Поэтому повторное
    чтение внутри обработчика может ответить иначе, чем первое, и ответ разъедется
    сам с собой.
    """
    seen = {}

    def _boom():
        seen["reread"] = True
        raise AssertionError("помощник не должен читать зеркало сам")

    class _Store:
        def agent_status(self, _a=None):
            _boom()

        def params(self, _a=None):
            return {}

    monkeypatch.setattr("trader.quik.manual_pnl.read_trades", lambda *a, **k: [])
    out = quik_companion._orders_pnl(
        _Store(), 0, None, {"rows": []},
        {"robots": [{"id": "lxk22"}]})          # зеркало передано снаружи
    assert out == {}
    assert "reread" not in seen


def test_pnl_cache_does_not_renew_itself(monkeypatch):
    """Кэш p&l живёт 10 с ОТ РАСЧЁТА, а не от последнего обращения.

    02.10.2026: метка времени переписывалась на каждом запросе, в том числе
    когда отдавалось старое значение. Первый расчёт после рестарта попал на ещё
    пустой фид параметров — и p&l всех заявок навсегда остался в ПУНКТАХ вместо
    рублей, хотя ₽/пункт появился через секунды. Самопродлевающийся кэш не
    устаревает никогда, то есть это не кэш, а запись набело.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {"runner_healthy": True,
                   "money": {"limit": 1.0, "varmargin": 0.0, "age_ms": 100},
                   "positions": []},
        "robots": [],
    }), 0)
    app.state.quik_store = store

    calls = {"n": 0}
    real = quik_companion._orders_pnl

    def _counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(quik_companion, "_orders_pnl", _counting)
    monkeypatch.setattr("trader.quik.manual_pnl.read_trades", lambda *a, **k: [])

    client = TestClient(app)
    for _ in range(3):
        client.get("/api/v1/quik/companion/snapshot", headers=_operator_headers())
    assert calls["n"] == 3                      # помощник зовётся каждый раз

    # А вот МЕТКА кэша после второго и третьего запроса не должна сдвинуться:
    # иначе значение, посчитанное в первый раз, не устареет никогда.
    first = app.state._order_pnl_cache[0]
    client.get("/api/v1/quik/companion/snapshot", headers=_operator_headers())
    assert app.state._order_pnl_cache[0] == first


def test_an_unknown_split_is_not_reported_as_all_manual(monkeypatch):
    """Зеркало не принесло списка роботов — разбивки НЕТ, а не «роботы 0».

    Сумма роботных позиций равна нулю и когда роботы вне рынка, и когда мы о них
    ничего не слышали. Во втором случае «Роботы 0 · Ручные −17» объявляет ВСЮ
    позицию ручной — ложь по умолчанию того же сорта, что пустое поле вместо
    нуля (правило real-trade, разбор позиции 02.10.2026).
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {"runner_healthy": True,
                   "money": {"limit": 1.0, "varmargin": 0.0, "age_ms": 100},
                   "positions": [{"sec": "RIZ6", "net": -17, "avg": 85_858.0,
                                  "varmargin": 0.0}]},
        # ключа "robots" НЕТ — зеркало о роботах не рассказало
    }), 0)
    app.state.quik_store = store
    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    pos = next(p for p in body["positions"] if p["sec"] == "RIZ6")
    assert pos["net"] == -17                 # нетто знаем всегда
    assert pos["robot_net"] is None          # а чьё оно — нет
    assert pos["manual_net"] is None


def test_an_empty_robot_list_is_a_real_answer(monkeypatch):
    """Пустой СПИСОК роботов — это «роботов нет», и разбивка известна.

    Путать его с отсутствием ключа нельзя: иначе честный флэт роботов выглядел
    бы как потеря связи, и оператор перестал бы верить прочерку.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {"runner_healthy": True,
                   "money": {"limit": 1.0, "varmargin": 0.0, "age_ms": 100},
                   "positions": [{"sec": "RIZ6", "net": -17, "avg": 85_858.0,
                                  "varmargin": 0.0}]},
        "robots": [],
    }), 0)
    app.state.quik_store = store
    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    pos = next(p for p in body["positions"] if p["sec"] == "RIZ6")
    assert pos["robot_net"] == 0
    assert pos["manual_net"] == -17


def test_journal_average_is_dropped_when_the_journal_misses_fills(monkeypatch):
    """Журнал разошёлся со счётом по инструменту — его средняя НЕ показывается.

    02.10.2026 оператор сверил два экрана: компаньон писал ручную среднюю
    85 480, QUIK по той же позиции — 85 690. Журнал в тот момент насчитал по
    RIZ6 на три контракта больше, чем лежало на счёте, и его средняя была ценой
    ДРУГОЙ позиции — похожей, но не этой. Обе цифры выглядели одинаково
    уверенно, и в этом был весь вред.

    ПРИЧИНУ расхождения экран не называет: real-trade разобрал журнал по
    номерам сделок (повторов ноль) и показал, что сумма журнала вообще не
    обязана равняться позиции — сойтись она может только там, где окно
    начинается с ФЛЭТА по инструменту, а наше ведётся с 23.09.

    Расхождение в контрактах — это недоверие к ЦЕНЕ, а не к количеству:
    количество и так берётся у счёта. Поэтому журнальную среднюю гасим и
    уходим на среднюю счёта (роботов в инструменте нет), подписав источник.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {
            "runner_healthy": True,
            "money": {"limit": 1.0, "varmargin": -1.0, "age_ms": 100},
            "positions": [{"sec": "RIZ6", "net": 35, "avg": 85_690.0, "varmargin": 1.0}],
        },
        "robots": [],
    }), 0)
    app.state.quik_store = store
    monkeypatch.setattr(quik_companion, "_manual_block",
                        lambda _store: {"open": [{"symbol": "RIZ6", "position": 35,
                                                  "avg_price": 85_480.0}],
                                        "open_vs_account": {"RIZ6": 3}})
    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    pos = next(p for p in body["positions"] if p["sec"] == "RIZ6")
    assert pos["manual_avg"] == pytest.approx(85_690.0)   # цифра счёта, не журнала
    assert pos["manual_avg_src"] == "quik"
    assert pos["manual_journal_div"] == 3                 # и расхождение названо
    assert pos["manual_avg_why"] == ""


def test_journal_average_survives_when_the_journal_agrees_with_the_account(monkeypatch):
    """Сошёлся по количеству — журнальную среднюю показываем как раньше.

    Правило бьёт по НЕДОВЕРИЮ, а не по журналу вообще: цена входа оператора
    однородна с роботной и нужна ровно там, где журналу можно верить.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _Settings()
    app.state.db_pool = FakePool()
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {
            "runner_healthy": True,
            "money": {"limit": 1.0, "varmargin": -1.0, "age_ms": 100},
            "positions": [{"sec": "RIZ6", "net": 35, "avg": 85_690.0, "varmargin": 1.0}],
        },
        "robots": [],
    }), 0)
    app.state.quik_store = store
    monkeypatch.setattr(quik_companion, "_manual_block",
                        lambda _store: {"open": [{"symbol": "RIZ6", "position": 35,
                                                  "avg_price": 85_480.0}],
                                        "open_vs_account": {}})
    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    pos = next(p for p in body["positions"] if p["sec"] == "RIZ6")
    assert pos["manual_avg"] == pytest.approx(85_480.0)
    assert pos["manual_avg_src"] == "journal"
    assert pos["manual_journal_div"] == 0


class _LimitSettings(_Settings):
    """Настройки с пределами: файл создаётся из них при первом чтении."""

    quik_trading_enabled = True
    quik_max_contracts_per_order = 75
    quik_max_working_contracts = 250
    quik_price_collar_frac = 0.002
    quik_daily_order_cap = 500
    quik_instrument_whitelist = "RIZ6,GZZ6"


def _limits_file(monkeypatch, tmp_path):
    """Пределы в tmp: тест не имеет права править боевой data/quik_limits.json."""
    from trader.quik import settings_file
    monkeypatch.setattr(settings_file, "PATH", str(tmp_path / "quik_limits.json"))
    monkeypatch.setattr(settings_file, "_cache", None, raising=False)
    monkeypatch.setattr(settings_file, "_cache_mtime", -1.0, raising=False)


def test_snapshot_carries_limits_and_their_consumption(monkeypatch, tmp_path):
    """Пределы живой торговли и сколько из них израсходовано (оператор 02.10.2026).

    Предел, которого не видно, замечают в момент отказа: дневной кап 50 однажды
    молча заморозил ВСЕ заявки роботов, включая выходы, и нашли это по логу
    раннера на VDS, а не на экране.

    Счётчики живут в памяти ЭТОГО процесса и обнуляются рестартом, поэтому в
    снимке едет и время начала счёта: «12 из 500» без него читается как «за
    день», а в день с шестью рестартами это разные числа.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _LimitSettings()
    app.state.db_pool = FakePool()
    _limits_file(monkeypatch, tmp_path)
    store = QuikAgentStore()
    store.set_agent_status("A1", json.dumps({
        "agent": {"version": "x", "link_up": True},
        "health": {"runner_healthy": True,
                   "money": {"limit": 1.0, "varmargin": 0.0, "age_ms": 100},
                   "positions": []},
        "robots": [],
    }), 0)
    # Агентский бэкстоп жёстче нашего по одному полю: панель обязана показать ЕГО.
    store.set_limits_state("A1", {"max_contracts_per_order": 50})
    app.state.quik_store = store
    from types import SimpleNamespace as _NS
    app.state.quik_order_store = _NS(
        placed_today=lambda _a: 12, working_contracts=lambda _a: 24)

    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    lim = body["limits"]
    assert lim["placed_today"] == 12 and lim["working_contracts"] == 24
    # Пик объёма одной заявки считается по таблице терминала; зеркала в этом
    # тесте нет, значит пик НЕИЗВЕСТЕН — и это не ноль: ноль читался бы как
    # «крупных заявок сегодня не было», то есть как спокойствие.
    assert lim["peak_order_qty"] is None
    assert lim["daily_order_cap"] > 0 and lim["max_working_contracts"] > 0
    assert lim["counted_since_ms"] > 0            # с какого момента счёт
    assert lim["agent"]["max_contracts_per_order"] == 50


def test_limits_consumption_is_unknown_not_zero_without_the_order_store(monkeypatch, tmp_path):
    """Склада заявок нет — расход НЕИЗВЕСТЕН, а не ноль.

    Ноль читается как «не торговали», и на упёршемся пределе это ровно та
    ошибка, которая стоит заявок.
    """
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(companion_router)
    app.state.settings = _LimitSettings()
    app.state.db_pool = FakePool()
    _limits_file(monkeypatch, tmp_path)
    app.state.quik_store = QuikAgentStore()
    body = TestClient(app).get("/api/v1/quik/companion/snapshot",
                               headers=_operator_headers()).json()
    assert body["limits"]["placed_today"] is None
    assert body["limits"]["working_contracts"] is None
