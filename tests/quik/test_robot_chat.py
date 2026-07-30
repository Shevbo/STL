"""Напарник робота в системном мониторе: границы роли и сборка контекста.

Что защищают эти тесты:
  * ручка READ ONLY — она не умеет ничего менять, у модели нет инструментов;
  * напарник знает ИМЕННО своего робота (факты попадают в запрос);
  * правила «только про своего робота» и «изменений нет» физически присутствуют
    в тексте запроса, причём и в начале, и в конце — иначе они тонут в контексте;
  * LLM идёт только через Lineman, и его падение не роняет стенд.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trader.api.quik_robot_chat import build_prompt, persona
from trader.api.quik_robot_chat import router as chat_router
from trader.auth.portal import make_session_token
from trader.quik.store import QuikAgentStore

SECRET = "test-bridge-secret"


class _Settings:
    shectory_auth_bridge_secret = SECRET
    quik_margin_multiplier = 2.4
    lineman_url = "http://lineman.test"
    lineman_agent_id = "klod-stl"
    lineman_model_hint = "normal"
    lineman_model_fallbacks = "fast"
    lineman_max_tokens = 900


def _hdr() -> dict:
    return {"Authorization": "Bearer " + make_session_token("op@example.com", SECRET)}


ROBOT = {
    "robot_id": "lxk22", "strategy_id": "macd_cross", "symbol": "RIU6", "paper": False,
    "paused": False, "running": True, "schedule": "07:00-23:50", "max_position": "34",
    "position": "34", "avg_price": 89562.94, "realized_pnl": 93261.71, "bars_count": 648,
    "params_json": json.dumps({"qty": 2, "tp_atr": 4, "avg_max": 34}),
    "signal_json": json.dumps({"waiting_for": "СИГНАЛ ЛОНГ", "atr": 88.72,
                               "last_close": 89210.0, "planned_orders": []}),
}


def _app(monkeypatch, *, robot=ROBOT) -> FastAPI:
    monkeypatch.delenv("SHECTORY_AUTH_DEV_BYPASS", raising=False)
    app = FastAPI()
    app.include_router(chat_router)
    app.state.settings = _Settings()
    app.state.db_pool = None
    store = QuikAgentStore()
    store.ensure_agent("A1")
    store.set_robot_report("A1", {"robots": [robot]} if robot else {"robots": []})
    store.set_agent_status("A1", json.dumps({
        "health": {"params": [{"code": "RIU6", "margin": 22027.9}]},
        "recon": {"robot_checks": [{"id": "lxk22", "trades_ok": True, "orders_ok": True}]},
    }), 0)
    app.state.quik_store = store
    return app


# ── границы роли ────────────────────────────────────────────────────────────

def test_persona_forbids_changes_and_off_topic():
    p = persona("lxk22", "MACD trend A")
    assert "только на чтение" in p.lower()
    assert "не можешь менять" in p.lower()
    assert "только про торговлю этого робота" in p.lower()
    # Защита от подмены правил через вопрос/историю.
    assert "переопределить" in p.lower()


def test_prompt_repeats_the_boundaries_after_the_context():
    """Правила стоят и в начале, и в хвосте: под длинным контекстом модель
    держится последней инструкции, а контекст тут большой."""
    ctx = {"robot_id": "lxk22", "name": "MACD trend A", "facts": ["позиция сейчас: +34"]}
    out = build_prompt(ctx, None, "как дела?")
    assert out.index("только на чтение") < out.index("=== ДАННЫЕ О РОБОТЕ ===")
    tail = out[out.index("=== ВОПРОС ОПЕРАТОРА ==="):]
    assert "только про торговлю этого робота" in tail
    assert "доступ к изменениям у тебя отсутствует" in tail


def test_prompt_carries_the_robot_facts_and_trades():
    ctx = {"robot_id": "lxk22", "name": "MACD trend A",
           "facts": ["позиция сейчас: +34 контрактов", "режим: РЕАЛ"],
           "strategy_doc": "MACD-кроссовер: ...",
           "params_doc": "tp_atr: тейк-профит",
           "trades": "30.07 16:11:03 sell 34 @ 89591 | результат +1524 руб"}
    out = build_prompt(ctx, None, "разбери сегодняшний выход")
    for needle in ("+34 контрактов", "режим: РЕАЛ", "MACD-кроссовер", "tp_atr",
                   "89591", "разбери сегодняшний выход"):
        assert needle in out


def test_prompt_keeps_only_the_last_dialogue_turns():
    ctx = {"robot_id": "r", "name": "r", "facts": []}
    hist = [{"role": "user", "text": f"вопрос {i}"} for i in range(30)]
    out = build_prompt(ctx, hist, "новый вопрос")
    assert "вопрос 29" in out
    assert "вопрос 0" not in out          # хвост обрезан, запрос не пухнет


def test_prompt_survives_broken_history_entries():
    ctx = {"robot_id": "r", "name": "r", "facts": []}
    out = build_prompt(ctx, [{"role": "user"}, None, {"text": "живая строка"}], "вопрос")  # type: ignore[list-item]
    assert "живая строка" in out


# ── ручка ───────────────────────────────────────────────────────────────────

def test_chat_answers_through_lineman(monkeypatch):
    seen: dict = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"text": "Позиция +34, средняя 89562.94.", "model_used": "claude-x",
                    "provider": "anthropic", "elapsed_ms": 700}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):        # noqa: A002 — имя из httpx
            seen["url"] = url
            seen["payload"] = json
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    client = TestClient(_app(monkeypatch))
    r = client.post("/api/v1/quik/robots/lxk22/chat",
                    json={"agent_id": "A1", "message": "какая позиция?"}, headers=_hdr())
    assert r.status_code == 200, r.text
    assert r.json()["text"].startswith("Позиция +34")

    # Ушло ИМЕННО в Lineman, с нашим agent_id, и контекст робота внутри.
    assert seen["url"] == "http://lineman.test/api/klod/ask"
    assert seen["payload"]["agent"] == "klod-stl"
    prompt = seen["payload"]["prompt"]
    assert "lxk22" in prompt and "RIU6" in prompt and "macd_cross" in prompt
    assert "ГО по факту счёта" in prompt          # биржевое x множитель
    assert "какая позиция?" in prompt


def test_chat_refuses_a_robot_missing_from_the_mirror(monkeypatch):
    client = TestClient(_app(monkeypatch, robot=None))
    r = client.post("/api/v1/quik/robots/ghost/chat",
                    json={"agent_id": "A1", "message": "привет"}, headers=_hdr())
    assert r.status_code == 409


def test_chat_needs_a_question(monkeypatch):
    client = TestClient(_app(monkeypatch))
    r = client.post("/api/v1/quik/robots/lxk22/chat",
                    json={"agent_id": "A1", "message": "   "}, headers=_hdr())
    assert r.status_code == 400


def test_chat_is_portal_authed(monkeypatch):
    client = TestClient(_app(monkeypatch))
    r = client.post("/api/v1/quik/robots/lxk22/chat", json={"message": "привет"})
    assert r.status_code == 401


def test_lineman_outage_degrades_honestly(monkeypatch):
    class _Boom:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): raise httpx.ConnectError("нет канала")

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    client = TestClient(_app(monkeypatch))
    r = client.post("/api/v1/quik/robots/lxk22/chat",
                    json={"agent_id": "A1", "message": "как дела?"}, headers=_hdr())
    assert r.status_code == 503
    assert "офлайн" in r.json()["detail"]


def test_chat_router_exposes_no_mutating_route():
    """READ ONLY на уровне поверхности: в этом роутере одна ручка и она ничего
    не меняет — ни в спеке робота, ни в заявках."""
    paths = {(r.path, tuple(sorted(r.methods))) for r in chat_router.routes}  # type: ignore[attr-defined]
    assert paths == {("/api/v1/quik/robots/{robot_id}/chat", ("POST",))}


@pytest.mark.parametrize("field", ["params_json", "signal_json"])
def test_broken_json_from_the_mirror_does_not_break_the_chat(monkeypatch, field):
    robot = dict(ROBOT)
    robot[field] = "не json"

    class _Resp:
        status_code = 200
        @staticmethod
        def json():
            return {"text": "ок", "model_used": "m", "provider": "p", "elapsed_ms": 1}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    client = TestClient(_app(monkeypatch, robot=robot))
    r = client.post("/api/v1/quik/robots/lxk22/chat",
                    json={"agent_id": "A1", "message": "привет"}, headers=_hdr())
    assert r.status_code == 200


def test_busy_provider_falls_back_to_the_next_hint(monkeypatch):
    """Квоты в федерации общие: «normal» регулярно отдаёт 502 поверх upstream 429.
    Чат обязан ответить — спускаемся по цепочке хинтов, а не падаем."""
    tried: list[str] = []

    class _Resp:
        def __init__(self, code, payload=None, text=""):
            self.status_code = code
            self._p = payload or {}
            self.text = text
        def json(self):
            return self._p

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):        # noqa: A002 — имя из httpx
            hint = json["model_hint"]
            tried.append(hint)
            if hint == "normal":
                return _Resp(502, text='{"error": "upstream HTTP 429"}')
            return _Resp(200, {"text": "ответ", "model_used": "claude-haiku",
                               "provider": "anthropic", "elapsed_ms": 900})

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    client = TestClient(_app(monkeypatch))
    r = client.post("/api/v1/quik/robots/lxk22/chat",
                    json={"agent_id": "A1", "message": "как дела?"}, headers=_hdr())
    assert r.status_code == 200
    assert r.json()["model_used"] == "claude-haiku"
    assert tried == ["normal", "fast"]


def test_all_hints_busy_reports_honestly(monkeypatch):
    class _Resp:
        status_code = 502
        text = '{"error": "upstream HTTP 429"}'
        @staticmethod
        def json(): return {}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    client = TestClient(_app(monkeypatch))
    r = client.post("/api/v1/quik/robots/lxk22/chat",
                    json={"agent_id": "A1", "message": "как дела?"}, headers=_hdr())
    assert r.status_code == 503
    assert "лимит" in r.json()["detail"]          # тут действительно 429
    assert "normal, fast" in r.json()["detail"]   # видно, что перепробовали


def test_non_quota_failure_is_not_reported_as_a_quota(monkeypatch):
    """«bad JSON» от прокси — не лимит провайдера. Раньше любая ошибка печаталась
    как «все модели заняты» и уводила оператора не туда (30.07.2026, 20:19)."""
    class _Resp:
        status_code = 400
        text = '{"error": "bad JSON"}'
        @staticmethod
        def json(): return {}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **kw): return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    client = TestClient(_app(monkeypatch))
    r = client.post("/api/v1/quik/robots/lxk22/chat",
                    json={"agent_id": "A1", "message": "как дела?"}, headers=_hdr())
    detail = r.json()["detail"]
    assert r.status_code == 503
    assert "лимит" not in detail
    assert "ошибкой" in detail and "bad JSON" in detail


def test_persona_demands_a_short_answer():
    """Оператор просил вывод короче на 70%: правило длины должно быть в личности
    явным и жёстким, иначе модель скатывается в пересказ стенда."""
    p = persona("lxk22", "MACD trend A").lower()
    assert "1-2 предложения" in p
    assert "не пересказывай данные" in p
    assert "никаких вступлений" in p
