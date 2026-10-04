"""Рабочее место бэктеста: правила редакций, очередь воркера, приёмка.

Заказ оператора 04.10.2026, договорённости с окном backtests. Главное, что стережёт
этот файл, — не «ручки отвечают», а ПРАВИЛА, ради которых вся схема придумана:
воркер не принимает редакцию, оператор не ставит «в работе», без живого воркера
редакция не создаётся и не висит, «готово» не бывает при красных воротах, а принять
можно только тот diff, который оператор видел.
"""
import asyncio
import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trader.api import lab_workbench as wb
from trader.api.lab_workbench import MemStore, Service, WorkbenchError
from trader.auth.portal import make_session_token

SECRET = "test-bridge-secret"
T0 = 1_790_000_000_000
TOKEN = "worker-secret-token"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def svc():
    return Service(MemStore())


async def _alive(svc, now=T0):
    await svc.heartbeat("w1", "v1", None, now=now)


async def _ready(svc, card="c", now=T0, message="добавь фильтр"):
    """Редакция, дошедшая до ready: путь, который проходит воркер."""
    await _alive(svc, now)
    r = await svc.create(card, message, None, "op@x", now=now)
    job = await svc.claim("w1", now=now + 1000)
    assert job["id"] == r["id"]
    await svc.report(job["id"], "w1", "gates", "правка...\n", "diff --git a b", {"pytest": {"ok": True}},
                     {"step": 90}, "wb/c/1@abc", "добавлен фильтр", now=now + 2000)
    await svc.report(job["id"], "w1", "ready", None, None, None, None, None, None, now=now + 3000)
    return r


def expect(code, coro):
    with pytest.raises(WorkbenchError) as e:
        run(coro)
    assert e.value.code == code, e.value.text
    return e.value


# ── Живой воркер ─────────────────────────────────────────────────────────────
def test_without_a_worker_a_revision_cannot_be_created(svc):
    """Редакция в очереди без воркера висела бы вечно и выглядела как «работает»."""
    e = expect("worker_down", svc.create("c", "правь", None, "op@x", now=T0))
    assert "ни разу" in e.text


def test_a_silent_worker_blocks_creation_and_says_for_how_long(svc):
    run(_alive(svc, T0))
    e = expect("worker_down", svc.create("c", "правь", None, "op@x", now=T0 + 7 * 60_000))
    assert "7 мин" in e.text


def test_worker_state_alive_below_90_seconds():
    assert wb.worker_state({"last_seen_ms": T0}, T0 + 89_000)["alive"] is True
    assert wb.worker_state({"last_seen_ms": T0}, T0 + 90_000)["alive"] is False
    assert wb.worker_state(None, T0)["alive"] is False
    assert wb.worker_state(None, T0)["age_s"] is None       # «не появлялся» ≠ «упал»


# ── Создание ─────────────────────────────────────────────────────────────────
def test_create_queues_the_revision_from_the_latest(svc):
    run(_alive(svc))
    r = run(svc.create("c", "  добавь фильтр  ", None, "op@x", now=T0))
    assert (r["rev"], r["parent"], r["status"], r["message"]) == (1, None, "queued", "добавь фильтр")


def test_one_working_revision_per_card(svc):
    """Две модели правят одну ветку — это и запрещено."""
    run(_alive(svc))
    run(svc.create("c", "а", None, "op@x", now=T0))
    e = expect("busy", svc.create("c", "б", None, "op@x", now=T0 + 1))
    assert "редакция 1" in e.text
    # а на ДРУГОЙ карточке — можно
    assert run(svc.create("d", "в", None, "op@x", now=T0 + 2))["rev"] == 1


def test_a_new_revision_builds_on_the_latest_and_stale_parent_is_refused(svc):
    run(_ready(svc))
    e = expect("stale_parent", svc.create("c", "ещё", 5, "op@x", now=T0 + 10_000))
    assert "последней" in e.text
    r = run(svc.create("c", "ещё", 1, "op@x", now=T0 + 10_000))
    assert (r["rev"], r["parent"]) == (2, 1)


@pytest.mark.parametrize("msg", ["", "   ", "x" * 4001])
def test_message_is_validated(svc, msg):
    run(_alive(svc))
    assert expect("empty_message" if not msg.strip() else "message_too_long",
                  svc.create("c", msg, None, "op@x", now=T0))


# ── Воркер и переходы ────────────────────────────────────────────────────────
def test_claim_hands_out_the_oldest_and_only_once(svc):
    run(_alive(svc))
    run(svc.create("a", "первая", None, "op@x", now=T0))
    run(svc.create("b", "вторая", None, "op@x", now=T0 + 1))
    j = run(svc.claim("w1", now=T0 + 10))
    assert j["card"] == "a" and j["message"] == "первая" and j["parent_code_ref"] is None
    # воркер занят первой — вторую ему не отдаём, пока не закончит
    assert run(svc.claim("w1", now=T0 + 20)) is None


def test_two_workers_cannot_take_the_same_revision(svc):
    run(_alive(svc))
    run(svc.create("a", "задача", None, "op@x", now=T0))
    assert run(svc.claim("w1", now=T0 + 10)) is not None
    assert run(svc.claim("w2", now=T0 + 11)) is None


def test_parallel_claims_do_not_double_issue():
    """Гонка за claim — единственное, что тут может сломаться."""
    async def main():
        s = Service(MemStore())
        await _alive(s)
        await s.create("a", "задача", None, "op@x", now=T0)
        res = await asyncio.gather(*[s.claim(f"w{i}", now=T0 + 10) for i in range(8)])
        return [r for r in res if r is not None]
    assert len(asyncio.run(main())) == 1


def test_a_worker_cannot_report_on_someone_elses_revision(svc):
    run(_alive(svc))
    r = run(svc.create("a", "задача", None, "op@x", now=T0))
    run(svc.claim("w1", now=T0 + 10))
    expect("not_yours", svc.report(r["id"], "w2", None, "вмешательство", None, None, None, None, None, now=T0 + 20))


def test_ready_requires_green_gates_and_a_result(svc):
    run(_alive(svc))
    r = run(svc.create("a", "задача", None, "op@x", now=T0))
    run(svc.claim("w1", now=T0 + 10))
    run(svc.report(r["id"], "w1", "gates", None, None, {"pytest": {"ok": True}, "ruff": {"ok": False}},
                   None, None, None, now=T0 + 20))
    expect("gates_not_green", svc.report(r["id"], "w1", "ready", None, "diff", None, None, "ref", None, now=T0 + 30))
    # зелёные, но результата нет
    run(svc.report(r["id"], "w1", None, None, None, {"pytest": {"ok": True}}, None, None, None, now=T0 + 40))
    expect("no_result", svc.report(r["id"], "w1", "ready", None, None, None, None, None, None, now=T0 + 50))


def test_gates_with_non_dict_values_are_not_green(svc):
    """Ворота — словари {ok: bool}. «ok: "true"» строкой зелёным не считается."""
    run(_alive(svc))
    r = run(svc.create("a", "задача", None, "op@x", now=T0))
    run(svc.claim("w1", now=T0 + 10))
    run(svc.report(r["id"], "w1", "gates", None, "d", {"pytest": "ok", "ruff": {"ok": "true"}},
                   None, "ref", None, now=T0 + 20))
    expect("gates_not_green", svc.report(r["id"], "w1", "ready", None, None, None, None, None, None, now=T0 + 30))


def test_illegal_transitions_are_refused_by_the_table(svc):
    run(_alive(svc))
    r = run(svc.create("a", "задача", None, "op@x", now=T0))
    # воркер не может перескочить из queued сразу в ready
    run(svc.claim("w1", now=T0 + 10))
    expect("bad_transition", svc.report(r["id"], "w1", "accepted", None, None, None, None, None, None, now=T0 + 20))
    expect("bad_transition", svc.report(r["id"], "w1", "ready", None, None, None, None, None, None, now=T0 + 20))


def test_ready_revision_is_frozen_for_the_worker(svc):
    r = run(_ready(svc))
    expect("closed", svc.report(r["id"], "w1", None, "дописываю", None, None, None, None, None, now=T0 + 9000))


def test_operator_cancel_tells_the_worker_to_stop(svc):
    run(_alive(svc))
    r = run(svc.create("a", "задача", None, "op@x", now=T0))
    run(svc.claim("w1", now=T0 + 10))
    run(svc.cancel("a", 1, "op@x", now=T0 + 20))
    # воркер узнаёт об отмене при ближайшем отчёте и обязан остановиться
    expect("closed", svc.report(r["id"], "w1", None, "ещё лог", None, None, None, None, None, now=T0 + 30))
    assert run(svc.get("a", 1, now=T0 + 40))["status"] == "failed"
    assert "отменено оператором" in run(svc.get("a", 1, now=T0 + 40))["log"]


# ── Таймауты ─────────────────────────────────────────────────────────────────
def test_working_revision_without_a_worker_fails_instead_of_hanging(svc):
    run(_alive(svc))
    run(svc.create("a", "задача", None, "op@x", now=T0))
    run(svc.claim("w1", now=T0 + 10))
    # воркер замолчал: через 4 минуты редакция уходит в failed с причиной
    got = run(svc.get("a", 1, now=T0 + 4 * 60_000))
    assert got["status"] == "failed"
    assert "воркер пропал" in got["log"]


def test_a_live_worker_keeps_its_revision_working(svc):
    run(_alive(svc))
    run(svc.create("a", "задача", None, "op@x", now=T0))
    run(svc.claim("w1", now=T0 + 10))
    run(svc.heartbeat("w1", "v1", 1, now=T0 + 3 * 60_000))
    assert run(svc.get("a", 1, now=T0 + 3 * 60_000 + 1))["status"] == "working"


def test_nothing_runs_longer_than_two_hours(svc):
    run(_alive(svc))
    run(svc.create("a", "задача", None, "op@x", now=T0))
    run(svc.claim("w1", now=T0 + 10))
    run(svc.heartbeat("w1", "v1", 1, now=T0 + 2 * 3600_000 + 60_000))      # воркер жив, но завис
    got = run(svc.get("a", 1, now=T0 + 2 * 3600_000 + 61_000))
    assert got["status"] == "failed" and "2 ч" in got["log"]


# ── Приёмка ──────────────────────────────────────────────────────────────────
def test_accept_requires_the_diff_the_operator_saw(svc):
    run(_ready(svc))
    sha = hashlib.sha256(b"diff --git a b").hexdigest()
    expect("diff_changed", svc.accept("c", 1, "0" * 64, "op@x", now=T0 + 9000))
    expect("diff_changed", svc.accept("c", 1, "", "op@x", now=T0 + 9000))
    r = run(svc.accept("c", 1, sha, "op@x", now=T0 + 9000))
    assert r["status"] == "accepted" and r["accepted_by"] == "op@x"


def test_only_a_ready_revision_can_be_accepted(svc):
    run(_alive(svc))
    run(svc.create("c", "задача", None, "op@x", now=T0))
    expect("bad_transition", svc.accept("c", 1, "x", "op@x", now=T0 + 10))


def test_worker_and_operator_roles_are_enforced_by_the_table():
    """Воркер не может принимать, оператор не может ставить «в работе»."""
    assert wb.TRANSITIONS[("ready", "accepted")] == {"operator"}
    assert wb.TRANSITIONS[("queued", "working")] == {"worker"}
    for (_, to), who in wb.TRANSITIONS.items():
        if to == "accepted":
            assert "worker" not in who
        if to in ("working", "gates", "ready"):
            assert "operator" not in who


def test_log_is_capped(svc):
    run(_alive(svc))
    r = run(svc.create("a", "задача", None, "op@x", now=T0))
    run(svc.claim("w1", now=T0 + 10))
    for i in range(3):
        run(svc.report(r["id"], "w1", None, "x" * 600_000, None, None, None, None, None, now=T0 + 20 + i))
    log = run(svc.get("a", 1, now=T0 + 30))["log"]
    assert len(log) < wb.LOG_MAX + 100 and "лог обрезан" in log


# ── HTTP: доступ ─────────────────────────────────────────────────────────────
class _Settings:
    shectory_auth_bridge_secret = SECRET


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WORKBENCH_WORKER_TOKEN", TOKEN)
    monkeypatch.setenv("STL_WORKBENCH_OPERATORS", "boss@x")
    app = FastAPI()
    app.include_router(wb.router)
    app.state.settings = _Settings()
    app.state.workbench_store = MemStore()
    return TestClient(app)


def op(email="boss@x"):
    return {"Authorization": "Bearer " + make_session_token(email, SECRET)}


WK = {"Authorization": "Bearer " + TOKEN}
BASE = "/api/v1/lab/workbench"


def test_worker_endpoints_reject_an_operator_session_and_vice_versa(client):
    # сессия оператора — не токен воркера
    assert client.post(f"{BASE}/worker/heartbeat", json={"worker_id": "w1"}, headers=op()).status_code == 401
    # токен воркера — не сессия оператора
    assert client.get(f"{BASE}/status", headers=WK).status_code == 401
    assert client.post(f"{BASE}/worker/heartbeat", json={"worker_id": "w1"}).status_code == 401


def test_unconfigured_worker_token_closes_the_worker_endpoints(client, monkeypatch):
    monkeypatch.delenv("WORKBENCH_WORKER_TOKEN")
    assert client.post(f"{BASE}/worker/heartbeat", json={"worker_id": "w1"}, headers=WK).status_code == 503


def test_unconfigured_operator_list_means_nobody_can_accept(client, monkeypatch):
    """Приёмка ведёт к релизу: «по умолчанию все» здесь недопустимо."""
    monkeypatch.delenv("STL_WORKBENCH_OPERATORS")
    r = client.post(f"{BASE}/cards/c/revisions/1/accept", json={"diff_sha": "x"}, headers=op())
    assert r.status_code == 503


def test_a_non_operator_session_cannot_accept(client):
    r = client.post(f"{BASE}/cards/c/revisions/1/accept", json={"diff_sha": "x"}, headers=op("someone@x"))
    assert r.status_code == 403


def test_full_flow_over_http(client):
    # воркер вышел на связь
    assert client.post(f"{BASE}/worker/heartbeat", json={"worker_id": "w1", "version": "v1"}, headers=WK).status_code == 200
    st = client.get(f"{BASE}/status", headers=op()).json()
    assert st["worker"]["alive"] is True and st["operators_configured"] is True
    # оператор просит правку
    created = client.post(f"{BASE}/cards/c/revisions", json={"message": "добавь фильтр"}, headers=op())
    assert created.status_code == 200 and created.json()["status"] == "queued"
    # воркер забирает и сдаёт
    job = client.post(f"{BASE}/worker/claim", json={"worker_id": "w1"}, headers=WK).json()
    assert job["message"] == "добавь фильтр"
    rid = job["id"]
    rep = client.post(f"{BASE}/worker/revisions/{rid}/report", headers=WK, json={
        "worker_id": "w1", "status": "gates", "diff": "diff --git a b", "code_ref": "wb/c/1@abc",
        "gates": {"pytest": {"ok": True}}, "log_append": "готово\n"})
    assert rep.status_code == 200
    assert client.post(f"{BASE}/worker/revisions/{rid}/report", headers=WK,
                       json={"worker_id": "w1", "status": "ready"}).status_code == 200
    # оператор смотрит и принимает
    got = client.get(f"{BASE}/cards/c/revisions/1", headers=op()).json()
    assert got["status"] == "ready" and got["diff"] == "diff --git a b"
    sha = got["diff_sha"]
    assert client.post(f"{BASE}/cards/c/revisions/1/accept", json={"diff_sha": "bad"}, headers=op()).status_code == 409
    ok = client.post(f"{BASE}/cards/c/revisions/1/accept", json={"diff_sha": sha}, headers=op())
    assert ok.status_code == 200 and ok.json()["status"] == "accepted"


def test_no_content_when_the_queue_is_empty(client):
    client.post(f"{BASE}/worker/heartbeat", json={"worker_id": "w1"}, headers=WK)
    assert client.post(f"{BASE}/worker/claim", json={"worker_id": "w1"}, headers=WK).status_code == 204


@pytest.mark.parametrize("bad", ["..", "A-B", "-x", "a.b", "x" * 200, "a b"])
def test_card_slug_is_validated(client, bad):
    """Slug попадёт в имя ветки wb/<card>/<rev>: проверяем жёстко."""
    r = client.get(f"{BASE}/cards/{bad}/revisions", headers=op())
    assert r.status_code in (404, 422)


def test_run_is_honestly_not_implemented(client):
    r = client.post(f"{BASE}/cards/c/revisions/1/run", headers=op())
    assert r.status_code == 501
    assert "формат задания" in r.json()["detail"]["text"]


def test_no_store_is_503_not_500(client):
    client.app.state.workbench_store = None
    assert client.get(f"{BASE}/status", headers=op()).status_code == 503


def test_error_carries_a_code_and_a_text_for_the_screen(client):
    r = client.post(f"{BASE}/cards/c/revisions", json={"message": "правь"}, headers=op())
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "worker_down" and "воркер" in d["text"]


# ── PgStore: то, что можно проверить без базы ───────────────────────────────
class _Conn:
    """Записывает запросы. Настоящей базы в тестах нет: проверяем то, что ломается
    тихо и до первого боевого вызова не видно, — число параметров и имена колонок."""

    def __init__(self):
        self.sql: list[tuple[str, tuple]] = []

    async def execute(self, sql, *args):
        self.sql.append((sql, args))

    async def fetchval(self, sql, *args):
        self.sql.append((sql, args))
        return 42

    async def fetchrow(self, sql, *args):
        self.sql.append((sql, args))
        return None

    async def fetch(self, sql, *args):
        self.sql.append((sql, args))
        return []


def _placeholders(sql):
    import re
    return sorted({int(x) for x in re.findall(r"\$(\d+)", sql)})


def test_pg_insert_has_one_placeholder_per_value_and_only_known_columns():
    conn = _Conn()
    row = {"card": "c", "rev": 1, "parent": None, "message": "m", "status": "queued",
           "change_note": None, "log": "", "diff": None, "diff_sha": None, "gates": None,
           "params": None, "code_ref": None, "created_by": "op", "created_at": 1, "updated_at": 1,
           "claimed_by": None, "claimed_at": None, "accepted_by": None, "accepted_at": None}
    rid = run(wb.PgStore._Tx(conn).insert(row))
    sql, args = conn.sql[0]
    assert rid == 42
    assert _placeholders(sql) == list(range(1, len(args) + 1))
    assert len(args) == len(row)


def test_pg_update_builds_numbered_assignments_from_whitelisted_names():
    conn = _Conn()
    run(wb.PgStore._Tx(conn).update(7, {"status": "working", "claimed_by": "w1", "updated_at": 5}))
    sql, args = conn.sql[0]
    assert args == (7, "working", "w1", 5)
    assert _placeholders(sql) == [1, 2, 3, 4]
    assert "WHERE id = $1" in sql


def test_pg_update_refuses_a_column_name_outside_the_whitelist():
    """Имена колонок собираются в строку, значения идут параметрами: без проверки
    имени это инъекция."""
    conn = _Conn()
    with pytest.raises(AssertionError):
        run(wb.PgStore._Tx(conn).update(1, {"status = 'accepted', code_ref": "x"}))
    assert conn.sql == []


def test_pg_worker_upsert_passes_all_five_parameters():
    conn = _Conn()
    run(wb.PgStore._Tx(conn).set_worker(
        {"worker_id": "w1", "version": "v", "busy_with": None, "last_seen_ms": 9}, keep_version=True))
    sql, args = conn.sql[0]
    assert _placeholders(sql) == [1, 2, 3, 4, 5]
    assert args == ("w1", "v", None, 9, True)


def test_pg_every_mutating_transaction_takes_the_advisory_lock():
    class _Pool:
        def __init__(self):
            self.conn = _Conn()

        def acquire(self):
            pool = self

            class _A:
                async def __aenter__(self_):
                    return pool.conn

                async def __aexit__(self_, *a):
                    return False
            return _A()

    class _ConnTx(_Conn):
        def transaction(self):
            class _T:
                async def __aenter__(s):
                    return s

                async def __aexit__(s, *a):
                    return False
            return _T()

    pool = _Pool()
    pool.conn = _ConnTx()

    async def go():
        async with wb.PgStore(pool).tx(write=True):
            pass
    run(go())
    assert "pg_advisory_xact_lock" in pool.conn.sql[0][0]
    assert pool.conn.sql[0][1] == (wb._LOCK_KEY,)


def test_ddl_declares_the_unique_card_rev_and_the_singleton_worker():
    ddl = " ".join(wb._DDL)
    assert "UNIQUE (card, rev)" in ddl
    assert "singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton)" in ddl
