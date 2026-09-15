"""Маршруты STL для нативных стоп-заявок QUIK и тревог (исполнительный модуль).

Этап 1: без этих маршрутов серия S1 на GZ не стартует. Держим именно то, что
маршрут обещает: все проверки лимитов срабатывают ДО агента, стоп-заявка считается
в дневной лимит, снятие проходит при единственном условии — мастер-флаге, а поля
QUIK уходят агенту как есть, без переименования. Для тревог — подтверждение снимает
флэш, неизвестная тревога даёт 404.
"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import trader.quik  # noqa: F401 — pb import path
from trader.api.quik_alerts import AlertBook
from trader.api.quik_alerts import router as alerts_router
from trader.api.quik_orders import router as orders_router
from trader.auth.portal import make_session_token
from trader.quik.alerts import SEVERITY_CRITICAL
from trader.quik.store import QuikAgentStore

SECRET = "test-portal-secret"
AGENT = "WIN-QUIK01"


class _OrderStore:
    def __init__(self, blocked=False, placed=0):
        self.blocked, self.placed = blocked, placed

    def is_blocked(self, agent):
        return self.blocked

    def placed_today(self, agent):
        return self.placed

    def record_placement(self, agent):
        self.placed += 1

    def trans_replies(self, agent_id=None):
        return list(getattr(self, "replies", []))


class _Server:
    def __init__(self):
        self.sent = []

    def enqueue_order(self, agent, msg):
        self.sent.append((agent, msg))


def _app(**limits):
    lim = dict(quik_trading_enabled=True, quik_max_contracts_per_order=2,
               quik_max_working_contracts=2, quik_price_collar_frac=0.002,
               quik_instrument_whitelist="GZU6", quik_daily_order_cap=50)
    lim.update(limits)
    app = FastAPI()
    app.include_router(orders_router)
    app.include_router(alerts_router)
    app.state.settings = SimpleNamespace(shectory_auth_bridge_secret=SECRET, **lim)
    app.state.quik_order_store = _OrderStore()
    app.state.quik_server = _Server()
    app.state.quik_store = QuikAgentStore()
    app.state.quik_alert_book = AlertBook()
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer " + make_session_token("op@stl", SECRET)
    return app, client


def _place(client, **over):
    body = dict(client_id="so:0123456789", code="GZU6", side="sell", quantity=1,
                fields={"STOP_ORDER_KIND": "SIMPLE_STOP_ORDER", "STOPPRICE": "12300"},
                agent_id=AGENT)
    body.update(over)
    return client.post("/api/v1/quik/orders/stop-place", json=body)


def test_stop_place_sends_fields_as_is_and_counts_in_daily_cap():
    app, client = _app()
    r = _place(client)
    assert r.status_code == 200, r.text
    agent, msg = app.state.quik_server.sent[0]
    assert agent == AGENT
    pso = msg.place_stop_order
    assert pso.code == "GZU6" and pso.quantity == 1
    assert dict(pso.fields) == {"STOP_ORDER_KIND": "SIMPLE_STOP_ORDER", "STOPPRICE": "12300"}
    assert app.state.quik_order_store.placed == 1


def test_stop_place_rejected_before_agent_by_each_limit():
    for limits, over, needle in (
        (dict(quik_trading_enabled=False), {}, "отключена"),
        ({}, dict(code="RIU6"), "белом списке"),
        ({}, dict(quantity=3), "превышает лимит на заявку"),
        (dict(quik_daily_order_cap=0), {}, ""),
    ):
        app, client = _app(**limits)
        r = _place(client, **over)
        assert r.status_code == 422, (limits, over, r.text)
        assert needle in r.json()["detail"]
        assert app.state.quik_server.sent == []         # до агента не дошло


def test_stop_place_blocked_by_kill_switch():
    app, client = _app()
    app.state.quik_order_store.blocked = True
    assert _place(client).status_code == 409
    assert app.state.quik_server.sent == []


def test_stop_kill_needs_only_master_flag():
    app, client = _app(quik_instrument_whitelist="")    # белый список пуст — снятию не мешает
    r = client.post("/api/v1/quik/orders/stop-kill",
                    json={"client_id": "s1-1", "stop_order_num": "777", "code": "GZU6",
                          "agent_id": AGENT})
    assert r.status_code == 200, r.text
    assert app.state.quik_server.sent[0][1].kill_stop_order.stop_order_num == "777"

    app2, client2 = _app(quik_trading_enabled=False)
    r2 = client2.post("/api/v1/quik/orders/stop-kill",
                      json={"client_id": "s1-1", "stop_order_num": "777", "code": "GZU6",
                            "agent_id": AGENT})
    assert r2.status_code == 422


def test_stop_orders_mirror_empty_when_agent_silent():
    _, client = _app()
    r = client.get("/api/v1/quik/orders/stop-orders")
    assert r.status_code == 200
    assert r.json() == {"table": [], "table_received_ms": 0, "events": []}


def test_alert_ack_stops_flash_and_unknown_is_404():
    app, client = _app()
    app.state.quik_alert_book.add({"severity": SEVERITY_CRITICAL, "code": "EXEC_STALL",
                                   "message": "доводка встала", "raised_at_unix_ms": 42}, AGENT)
    assert client.get("/api/v1/quik/alerts").json()["flash"][0]["code"] == "EXEC_STALL"
    assert client.post("/api/v1/quik/alerts/ack",
                       json={"code": "EXEC_STALL", "raised_at": 42}).status_code == 200
    assert client.get("/api/v1/quik/alerts").json()["flash"] == []
    assert client.post("/api/v1/quik/alerts/ack",
                       json={"code": "EXEC_STALL", "raised_at": 42}).status_code == 404


def test_routes_require_auth():
    app, client = _app()
    client.headers.pop("Authorization")
    assert _place(client).status_code in (401, 403)
    assert client.get("/api/v1/quik/alerts").status_code in (401, 403)
    assert app.state.quik_server.sent == []


def test_stop_client_id_generated_when_empty_and_format_enforced():
    app, client = _app()
    r = _place(client, client_id="")
    assert r.status_code == 200, r.text
    cid = r.json()["client_id"]
    assert cid.startswith("so:") and len(cid) == 13      # влезает в brokerref QUIK (20)
    assert app.state.quik_server.sent[0][1].place_stop_order.client_id == cid

    for bad in ("s1-1", "so:XYZ", "so:0123456789abcdef"):
        app2, client2 = _app()
        r2 = _place(client2, client_id=bad)
        assert r2.status_code == 422, bad
        assert app2.state.quik_server.sent == []


def test_stop_order_num_stays_a_string():
    # Номер стоп-заявки QUIK ~1.9e18: числом в JSON он потерял бы последние цифры.
    app, client = _app()
    num = "1900000000000000123"
    r = client.post("/api/v1/quik/orders/stop-kill",
                    json={"client_id": "so:0123456789", "stop_order_num": num, "code": "GZU6",
                          "agent_id": AGENT})
    assert r.status_code == 200, r.text
    assert app.state.quik_server.sent[0][1].kill_stop_order.stop_order_num == num


def test_trans_replies_filtered_by_client_id():
    # Ответ терминала — единственное место, где на серии видно, принята ли стоп-заявка.
    app, client = _app()
    app.state.quik_order_store.replies = [
        {"client_id": "so:0123456789", "trans_id": 7, "result_code": 3,
         "text": "стоп-заявка принята", "ts_unix_ms": 2, "agent_id": AGENT},
        {"client_id": "so:aaaaaaaaaa", "trans_id": 8, "result_code": 4,
         "text": "отвергнута", "ts_unix_ms": 1, "agent_id": AGENT},
    ]
    r = client.get("/api/v1/quik/orders/trans-replies", params={"client_id": "so:0123456789"})
    assert r.status_code == 200, r.text
    assert [x["text"] for x in r.json()["replies"]] == ["стоп-заявка принята"]
    assert len(client.get("/api/v1/quik/orders/trans-replies").json()["replies"]) == 2
