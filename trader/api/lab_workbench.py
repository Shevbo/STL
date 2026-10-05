"""Рабочее место бэктеста: редакции карточки, очередь воркера, приёмка.

Заказ оператора 04.10.2026 (спека docs/backtest-workbench-spec.md): на карточке
кампании оператор просит модель внести правку в стратегию, получает НОВУЮ РЕДАКЦИЮ
(ветка git + тесты + diff), смотрит и принимает. Правку кода делает не этот процесс:
отдельный воркер на smain с чистым клоном репозитория. Здесь только очередь, статусы
и приёмка.

ГРАНИЦЫ ЭТОГО МОДУЛЯ (решения согласованы с окном backtests 04.10.2026):

* Воркер ходит СЮДА по HTTP с отдельным токеном (claim, heartbeat, report), а не в
  Postgres: реквизиты базы не уезжают к процессу, исполняющему текст, написанный
  моделью. Токен позволяет ровно эти три действия.
* API НИЧЕГО НЕ МЕРЖИТ И НЕ РЕЛИЗИТ. «Принять» переводит ready → accepted и пишет, кто
  и что принял. Слияние wb/<card>/<rev> в main делает окно backtests по явной команде
  оператора, релиз — по обычным правилам (рестарты только real-trade).
* Расчёт НИКОГДА не здесь (правило изоляции): ни правка кода, ни прогон.

ЖИВОСТЬ ВОРКЕРА — ЧАСТЬ КОНТРАКТА. Без воркера редакция встала бы в queued и висела
вечно, выглядя как «работает» (тот же класс ошибок, что молчащее зеркало терминала,
выглядящее как флэт). Поэтому воркер раз в 30 с шлёт heartbeat, `alive` = последний
моложе 90 с, создание редакции без живого воркера отклоняется, а редакция в работе без
воркера сама уходит в failed.

Все правила (переходы, ограничения, таймауты) живут в `Service` и не знают, где лежат
данные; хранилище — одна из двух реализаций (`PgStore` в бою, `MemStore` в тестах) с
тем же набором примитивов внутри транзакции.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from trader.auth.guard import require_auth

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/lab/workbench", tags=["lab-workbench"])

# ── Правила ─────────────────────────────────────────────────────────────────
#: draft оставлен в перечне ради совместимости со спекой, но не создаётся: редакция
#: рождается сразу в очереди.
STATUSES = ("draft", "queued", "working", "gates", "ready", "failed", "accepted")
OPEN = ("queued", "working", "gates")          # редакция «в работе»: новую поверх не создать

# (из, в) → кому разрешено. Воркер не может поставить accepted, оператор не может
# поставить working: роль проверяется ТАБЛИЦЕЙ, а не вежливостью вызывающего.
TRANSITIONS: dict[tuple[str, str], frozenset[str]] = {
    ("queued", "working"): frozenset({"worker"}),
    ("working", "gates"): frozenset({"worker"}),
    ("working", "failed"): frozenset({"worker", "operator", "system"}),
    ("gates", "ready"): frozenset({"worker"}),
    ("gates", "failed"): frozenset({"worker", "operator", "system"}),
    ("queued", "failed"): frozenset({"operator", "system"}),
    ("ready", "accepted"): frozenset({"operator"}),
}

WORKER_ALIVE_SEC = 90          # heartbeat раз в 30 с; моложе 90 — жив
WORKING_STALE_SEC = 180        # редакция в работе без отчёта и без живого воркера → failed
WORKING_MAX_SEC = 2 * 3600     # никакая правка не должна идти дольше двух часов

MESSAGE_MAX = 4000             # сообщение оператора: чужой для воркера текст, режем сразу
SCRIPT_MAX = 256 * 1024        # исходник стратегии в params.script_code (договорённость с backtests)
COMBOS_MAX = 2000              # комбинаций на один прогон; больше — отказ, а не тихое урезание
RUNNABLE = ("ready", "accepted")
RUNS_KEEP = 50                 # сколько прогонов помнит редакция
LOG_MAX = 1_000_000            # лог редакции (дописывается отчётами воркера)
DIFF_MAX = 1_000_000
GATES_MAX = 64_000

COLUMNS = frozenset({
    "id", "card", "rev", "parent", "message", "status", "change_note", "log", "diff",
    "diff_sha", "gates", "params", "code_ref", "created_by", "created_at", "updated_at",
    "claimed_by", "claimed_at", "accepted_by", "accepted_at", "runs",
})


class WorkbenchError(Exception):
    """Нарушение правила: код — для экрана и воркера, текст — для человека."""

    def __init__(self, status: int, code: str, text: str):
        super().__init__(text)
        self.status, self.code, self.text = status, code, text


def now_ms() -> int:
    return int(time.time() * 1000)


def worker_state(w: dict[str, Any] | None, now: int) -> dict[str, Any]:
    """Живой ли воркер. `None` — воркер не появлялся НИ РАЗУ: это не «упал», а «нет»."""
    if not w:
        return {"alive": False, "age_s": None, "busy_with": None, "version": None, "worker_id": None}
    age = max(0, (now - int(w["last_seen_ms"])) / 1000.0)
    return {"alive": age < WORKER_ALIVE_SEC, "age_s": round(age, 1),
            "busy_with": w.get("busy_with"), "version": w.get("version"),
            "worker_id": w.get("worker_id")}


# ── Сервис: все правила в одном месте ───────────────────────────────────────
class Service:
    def __init__(self, store: Any):
        self.store = store

    # -- служебное
    @staticmethod
    def _fail(r: dict[str, Any], why: str, now: int) -> dict[str, Any]:
        return {"status": "failed", "updated_at": now,
                "log": _cap((r.get("log") or "") + f"\n[STL] {why}", LOG_MAX)}

    async def _sweep(self, tx: Any, now: int) -> None:
        """Редакции, которые никто не ведёт, уходят в failed, а не висят «в работе».

        Вызывается ЛЕНИВО на каждом обращении: фоновой задачи нет, а видеть зависшее
        нужно ровно тогда, когда на него смотрят."""
        w = worker_state(await tx.worker(), now)
        for r in await tx.open_rows():
            if r["status"] not in ("working", "gates"):
                continue
            idle = (now - int(r["updated_at"])) / 1000.0
            started = (now - int(r.get("claimed_at") or r["updated_at"])) / 1000.0
            if started > WORKING_MAX_SEC:
                await tx.update(r["id"], self._fail(r, "превышено время работы воркера (2 ч)", now))
            elif not w["alive"] and idle > WORKING_STALE_SEC:
                await tx.update(r["id"], self._fail(
                    r, "воркер пропал: нет heartbeat, редакция не завершена", now))

    # -- операторские действия
    async def status(self, now: int | None = None) -> dict[str, Any]:
        now = now or now_ms()
        async with self.store.tx() as tx:
            await self._sweep(tx, now)
            return {"worker": worker_state(await tx.worker(), now)}

    async def list_revisions(self, card: str, now: int | None = None) -> list[dict[str, Any]]:
        now = now or now_ms()
        async with self.store.tx() as tx:
            await self._sweep(tx, now)
            return [_public(r, full=False) for r in await tx.rows(card)]

    async def get(self, card: str, rev: int, now: int | None = None) -> dict[str, Any]:
        now = now or now_ms()
        async with self.store.tx() as tx:
            await self._sweep(tx, now)
            for r in await tx.rows(card):
                if r["rev"] == rev:
                    return _public(r, full=True)
        raise WorkbenchError(404, "no_revision", "Нет такой редакции.")

    async def create(self, card: str, message: str, parent: int | None, actor: str,
                     now: int | None = None, base_rev: int = 0) -> dict[str, Any]:
        """`base_rev` — редакция карточки в витрине (rev сборщика, «ред. 1» = результат
        кампании). Нумерация у карточки ОДНА (решение backtests 04.10.2026): таблица
        начинает с base_rev + 1, а parent первой равен base_rev."""
        now = now or now_ms()
        text = (message or "").strip()
        if not text:
            raise WorkbenchError(422, "empty_message", "Сообщение пустое: что изменить?")
        if len(text) > MESSAGE_MAX:
            raise WorkbenchError(422, "message_too_long", f"Сообщение длиннее {MESSAGE_MAX} знаков.")
        async with self.store.tx(write=True) as tx:
            await self._sweep(tx, now)
            w = worker_state(await tx.worker(), now)
            # Без живого воркера редакция встала бы в очередь и висела «в работе».
            if not w["alive"]:
                since = ("воркер ещё ни разу не выходил на связь" if w["age_s"] is None
                         else f"воркер не отвечает {int(w['age_s'] // 60)} мин")
                raise WorkbenchError(409, "worker_down", since)
            rows = await tx.rows(card)
            busy = [r for r in rows if r["status"] in OPEN]
            # Одна рабочая редакция на карточку: иначе две модели правят одну ветку.
            if busy:
                raise WorkbenchError(
                    409, "busy", f"В карточке уже есть редакция {busy[0]['rev']} в работе "
                                 f"({busy[0]['status']}).")
            latest = max([base_rev, *(r["rev"] for r in rows)])
            if parent is not None and parent != latest:
                raise WorkbenchError(
                    409, "stale_parent", f"Редакция строится от последней ({latest or 'нет'}), "
                                         f"а указана {parent}: страница устарела.")
            row = {"card": card, "rev": latest + 1, "parent": latest or None, "message": text,
                   "runs": None,
                   "status": "queued", "change_note": None, "log": "", "diff": None,
                   "diff_sha": None, "gates": None, "params": None, "code_ref": None,
                   "created_by": actor, "created_at": now, "updated_at": now,
                   "claimed_by": None, "claimed_at": None, "accepted_by": None, "accepted_at": None}
            rid = await tx.insert(row)
            log.info("workbench.created", card=card, rev=row["rev"], by=actor)
            return _public({**row, "id": rid}, full=False)

    async def cancel(self, card: str, rev: int, actor: str, now: int | None = None) -> dict[str, Any]:
        now = now or now_ms()
        async with self.store.tx(write=True) as tx:
            r = await self._need(tx, card, rev)
            self._allow(r["status"], "failed", "operator")
            await tx.update(r["id"], self._fail(r, f"отменено оператором ({actor})", now))
            return _public({**r, "status": "failed"}, full=False)

    async def accept(self, card: str, rev: int, diff_sha: str, actor: str,
                     now: int | None = None) -> dict[str, Any]:
        now = now or now_ms()
        async with self.store.tx(write=True) as tx:
            r = await self._need(tx, card, rev)
            self._allow(r["status"], "accepted", "operator")
            # Принимается ровно тот diff, который оператор видел: клиент присылает его
            # хеш, и расхождение — отказ, а не молчаливая приёмка чего-то другого.
            if not diff_sha or not hmac.compare_digest(str(diff_sha).encode("utf-8"),
                                                       str(r.get("diff_sha") or "").encode("utf-8")):
                raise WorkbenchError(409, "diff_changed", "Хеш diff не совпал с принимаемым: обновите страницу.")
            await tx.update(r["id"], {"status": "accepted", "accepted_by": actor,
                                      "accepted_at": now, "updated_at": now})
            log.info("workbench.accepted", card=card, rev=rev, by=actor, diff_sha=diff_sha,
                     code_ref=r.get("code_ref"))
            return _public({**r, "status": "accepted", "accepted_by": actor, "accepted_at": now}, full=False)

    async def runnable(self, card: str, rev: int, now: int | None = None) -> dict[str, Any]:
        """Редакция, которую можно запускать, с её параметрами прогона (или отказ)."""
        now = now or now_ms()
        async with self.store.tx() as tx:
            r = await self._need(tx, card, rev)
            if r["status"] not in RUNNABLE:
                raise WorkbenchError(409, "not_runnable",
                                     f"Запускать можно только готовую или принятую редакцию, сейчас: {r['status']}.")
            return dict(r)

    async def add_run(self, card: str, rev: int, run_id: str, actor: str,
                      now: int | None = None) -> list[dict[str, Any]]:
        now = now or now_ms()
        async with self.store.tx(write=True) as tx:
            r = await self._need(tx, card, rev)
            runs = list(r.get("runs") or [])
            runs.append({"run_id": run_id, "by": actor, "at": now})
            runs = runs[-RUNS_KEEP:]
            await tx.update(r["id"], {"runs": runs, "updated_at": now})
            return runs

    # -- действия воркера
    async def heartbeat(self, worker_id: str, version: str, busy_with: int | None,
                        now: int | None = None) -> dict[str, Any]:
        now = now or now_ms()
        async with self.store.tx(write=True) as tx:
            await tx.set_worker({"worker_id": worker_id, "version": version,
                                 "busy_with": busy_with, "last_seen_ms": now})
            await self._sweep(tx, now)
        return {"ok": True}

    async def claim(self, worker_id: str, now: int | None = None) -> dict[str, Any] | None:
        now = now or now_ms()
        async with self.store.tx(write=True) as tx:
            await tx.set_worker({"worker_id": worker_id, "version": None, "busy_with": None,
                                 "last_seen_ms": now}, keep_version=True)
            await self._sweep(tx, now)
            queued = sorted((r for r in await tx.open_rows() if r["status"] == "queued"),
                            key=lambda r: r["id"])
            if not queued:
                return None
            r = queued[0]
            # Ровно одна работающая на воркера: пока старая не закончена, новую не отдаём.
            if any(x["status"] in ("working", "gates") and x.get("claimed_by") == worker_id
                   for x in await tx.open_rows()):
                return None
            self._allow("queued", "working", "worker")
            await tx.update(r["id"], {"status": "working", "claimed_by": worker_id,
                                      "claimed_at": now, "updated_at": now})
            # parent_params — от них модель правит (просьба backtests 05.10.2026). Родитель —
            # rev сборщика вне нашей таблицы: оба None, база тогда в card_ctx.base_params.
            parent_ref = parent_params = None
            if r["parent"]:
                for p in await tx.rows(r["card"]):
                    if p["rev"] == r["parent"]:
                        parent_ref, parent_params = p.get("code_ref"), p.get("params")
            return {"id": r["id"], "card": r["card"], "rev": r["rev"], "parent": r["parent"],
                    "message": r["message"], "parent_code_ref": parent_ref,
                    "parent_params": parent_params}

    async def report(self, rid: int, worker_id: str, status: str | None, log_append: str | None,
                     diff: str | None, gates: dict | None, params: dict | None,
                     code_ref: str | None, change_note: str | None,
                     now: int | None = None) -> dict[str, Any]:
        now = now or now_ms()
        async with self.store.tx(write=True) as tx:
            r = await tx.row(rid)
            if r is None:
                raise WorkbenchError(404, "no_revision", "Нет такой редакции.")
            if r.get("claimed_by") != worker_id:
                # Чужую редакцию воркер править не вправе: токен один на воркера, но
                # claim выдаёт задание конкретному worker_id.
                raise WorkbenchError(403, "not_yours", "Редакция выдана другому воркеру.")
            if r["status"] in ("failed", "accepted", "ready"):
                # Оператор мог отменить редакцию, пока воркер работал: ему нужно узнать
                # об этом и остановиться, а не дописывать в закрытое.
                raise WorkbenchError(409, "closed", f"Редакция уже {r['status']}: отчёт не принят.")
            upd: dict[str, Any] = {"updated_at": now}
            if log_append:
                upd["log"] = _cap((r.get("log") or "") + log_append, LOG_MAX)
            if change_note is not None:
                upd["change_note"] = change_note[:500]
            if params is not None:
                upd["params"] = params
            if code_ref is not None:
                upd["code_ref"] = code_ref[:200]
            if diff is not None:
                upd["diff"] = diff[:DIFF_MAX]
                upd["diff_sha"] = hashlib.sha256(upd["diff"].encode("utf-8")).hexdigest()
            if gates is not None:
                upd["gates"] = gates
            if status is not None and status != r["status"]:
                self._allow(r["status"], status, "worker")
                if status == "ready":
                    # «Готово» не бывает при красных воротах или без результата: только
                    # ради этого ворота и существуют.
                    g = gates if gates is not None else r.get("gates")
                    d = upd.get("diff", r.get("diff"))
                    ref = upd.get("code_ref", r.get("code_ref"))
                    if not g or not all(isinstance(v, dict) and v.get("ok") is True for v in g.values()):
                        raise WorkbenchError(422, "gates_not_green", "Ворота не зелёные или не заданы.")
                    if not d or not ref:
                        raise WorkbenchError(422, "no_result", "Для ready нужны diff и code_ref.")
                upd["status"] = status
            await tx.update(rid, upd)
            return {"ok": True, "status": upd.get("status", r["status"])}

    # -- вспомогательное
    async def _need(self, tx: Any, card: str, rev: int) -> dict[str, Any]:
        for r in await tx.rows(card):
            if r["rev"] == rev:
                return r
        raise WorkbenchError(404, "no_revision", "Нет такой редакции.")

    @staticmethod
    def _allow(frm: str, to: str, actor: str) -> None:
        who = TRANSITIONS.get((frm, to))
        if who is None:
            raise WorkbenchError(409, "bad_transition", f"Переход {frm} → {to} не предусмотрен.")
        if actor not in who:
            raise WorkbenchError(403, "forbidden_transition", f"{actor} не вправе переводить {frm} → {to}.")


def combos_of(params: dict[str, Any]) -> int:
    """Сколько комбинаций в прогоне: список наборов или произведение длин сетки."""
    sets = params.get("param_sets")
    if isinstance(sets, list):
        return max(1, len(sets))
    grid = params.get("params_grid")
    if isinstance(grid, dict) and grid:
        n = 1
        for v in grid.values():
            n *= len(v) if isinstance(v, list) and v else 1
        return n
    return 1


def build_run_body(params: dict[str, Any] | None) -> dict[str, Any]:
    """Тело POST /api/v1/backtest/run из params редакции (формат — спека backtests 04.10.2026).

    Сервер НЕ ДОВЕРЯЕТ ВОРКЕРУ: исходник ещё раз проходит validate_script, размер и число
    комбинаций проверяются здесь, а не принимаются на слово."""
    p = dict(params or {})
    code = p.get("script_code")
    if not isinstance(code, str) or not code.strip():
        raise WorkbenchError(422, "no_script", "У редакции нет исходника стратегии (params.script_code).")
    if len(code.encode("utf-8")) > SCRIPT_MAX:
        raise WorkbenchError(422, "script_too_big", f"Исходник больше {SCRIPT_MAX // 1024} КБ.")
    from trader.lab.script_guard import ScriptValidationError, validate_script
    try:
        validate_script(code)
    except ScriptValidationError as exc:
        raise WorkbenchError(422, "script_rejected", f"Исходник не прошёл проверку: {exc}") from exc
    for k in ("symbol", "date_from", "date_to"):
        if not isinstance(p.get(k), str) or not p[k].strip():
            raise WorkbenchError(422, "no_window", f"В параметрах прогона нет поля {k}.")
    n = combos_of(p)
    if n > COMBOS_MAX:
        raise WorkbenchError(422, "too_many_combos", f"Комбинаций {n}, предел {COMBOS_MAX}.")
    body: dict[str, Any] = {
        "scriptCode": code, "baseParams": dict(p.get("base_params") or {}),
        "symbol": p["symbol"], "dateFrom": p["date_from"], "dateTo": p["date_to"],
        "engine": "remote",
        # Прогон запускает оператор кнопкой: за ним сидит человек, вперёд фоновых кампаний.
        "priority": 100,
    }
    if isinstance(p.get("param_sets"), list):
        body["paramSets"] = p["param_sets"]
    elif isinstance(p.get("params_grid"), dict):
        body["paramsGrid"] = p["params_grid"]
    return body


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[STL] лог обрезан"


def _public(r: dict[str, Any], full: bool) -> dict[str, Any]:
    """Редакция для экрана. log, diff и message — ЧУЖОЙ текст (его писала модель или
    оператор для модели): отдаются только как текст, интерпретировать их нельзя."""
    out = {k: r.get(k) for k in ("id", "card", "rev", "parent", "status", "change_note",
                                 "code_ref", "created_by", "created_at", "updated_at",
                                 "accepted_by", "accepted_at", "diff_sha", "message")}
    out["runs"] = list(r.get("runs") or [])
    if full:
        # params без исходника: script_code — до 256 КБ кода, экрану он не нужен, а лог и
        # diff его уже показывают. Наличие и размер отдаём явно.
        prm = dict(r.get("params") or {})
        code = prm.pop("script_code", None)
        out.update({"log": r.get("log") or "", "diff": r.get("diff"), "gates": r.get("gates"),
                    "params": prm or None,
                    "script_bytes": len(code.encode("utf-8")) if isinstance(code, str) else None})
    return out


# ── Хранилища ───────────────────────────────────────────────────────────────
class MemStore:
    """В памяти: для тестов и как эталон поведения примитивов."""

    def __init__(self) -> None:
        self.rows_: dict[int, dict[str, Any]] = {}
        self.worker_: dict[str, Any] | None = None
        self.seq = 0
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def tx(self, write: bool = False) -> AsyncIterator["MemStore._Tx"]:
        async with self._lock:
            yield MemStore._Tx(self)

    class _Tx:
        def __init__(self, s: "MemStore") -> None:
            self.s = s

        async def worker(self) -> dict[str, Any] | None:
            return dict(self.s.worker_) if self.s.worker_ else None

        async def set_worker(self, w: dict[str, Any], keep_version: bool = False) -> None:
            if keep_version and self.s.worker_ and not w.get("version"):
                w = {**w, "version": self.s.worker_.get("version")}
            self.s.worker_ = dict(w)

        async def rows(self, card: str) -> list[dict[str, Any]]:
            return sorted((dict(r) for r in self.s.rows_.values() if r["card"] == card),
                          key=lambda r: r["rev"])

        async def open_rows(self) -> list[dict[str, Any]]:
            return [dict(r) for r in self.s.rows_.values() if r["status"] in OPEN]

        async def row(self, rid: int) -> dict[str, Any] | None:
            r = self.s.rows_.get(rid)
            return dict(r) if r else None

        async def insert(self, row: dict[str, Any]) -> int:
            self.s.seq += 1
            self.s.rows_[self.s.seq] = {**row, "id": self.s.seq}
            return self.s.seq

        async def update(self, rid: int, fields: dict[str, Any]) -> None:
            assert set(fields) <= COLUMNS, set(fields) - COLUMNS
            self.s.rows_[rid].update(fields)


_DDL = (
    """CREATE TABLE IF NOT EXISTS workbench_revisions (
         id BIGSERIAL PRIMARY KEY,
         card TEXT NOT NULL,
         rev INT NOT NULL,
         parent INT,
         message TEXT NOT NULL,
         status TEXT NOT NULL,
         change_note TEXT,
         log TEXT NOT NULL DEFAULT '',
         diff TEXT,
         diff_sha TEXT,
         gates JSONB,
         params JSONB,
         code_ref TEXT,
         created_by TEXT NOT NULL,
         created_at BIGINT NOT NULL,
         updated_at BIGINT NOT NULL,
         claimed_by TEXT,
         claimed_at BIGINT,
         accepted_by TEXT,
         accepted_at BIGINT,
         runs JSONB,
         UNIQUE (card, rev))""",
    # Колонка прогонов добавлена после первой версии таблицы; на уже созданной её не было бы.
    "ALTER TABLE workbench_revisions ADD COLUMN IF NOT EXISTS runs JSONB",
    """CREATE TABLE IF NOT EXISTS workbench_worker (
         singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
         worker_id TEXT,
         version TEXT,
         busy_with BIGINT,
         last_seen_ms BIGINT NOT NULL)""",
)
#: Один замок на все изменения: поток редкий (оператор кликает, воркер раз в 30 с), а
#: гонка за claim или за «одну рабочую на карточку» — единственное, что тут может
#: сломаться. Сериализуем целиком и не изобретаем построчные блокировки.
_LOCK_KEY = 7_204_001


class PgStore:
    """Postgres. Ровно те же примитивы, что у MemStore, поверх одной транзакции."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def ensure_schema(self) -> None:
        for ddl in _DDL:
            await self.pool.execute(ddl)

    @asynccontextmanager
    async def tx(self, write: bool = False) -> AsyncIterator["PgStore._Tx"]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # И чтение под замком: _sweep внутри чтения тоже пишет.
                await conn.execute("SELECT pg_advisory_xact_lock($1)", _LOCK_KEY)
                yield PgStore._Tx(conn)

    class _Tx:
        def __init__(self, conn: Any) -> None:
            self.c = conn

        async def worker(self) -> dict[str, Any] | None:
            r = await self.c.fetchrow("SELECT * FROM workbench_worker")
            return dict(r) if r else None

        async def set_worker(self, w: dict[str, Any], keep_version: bool = False) -> None:
            version = w.get("version")
            await self.c.execute(
                """INSERT INTO workbench_worker (singleton, worker_id, version, busy_with, last_seen_ms)
                   VALUES (TRUE, $1, $2, $3, $4)
                   ON CONFLICT (singleton) DO UPDATE SET worker_id = EXCLUDED.worker_id,
                     version = CASE WHEN $5 THEN COALESCE(EXCLUDED.version, workbench_worker.version)
                                    ELSE EXCLUDED.version END,
                     busy_with = EXCLUDED.busy_with, last_seen_ms = EXCLUDED.last_seen_ms""",
                w["worker_id"], version, w.get("busy_with"), int(w["last_seen_ms"]), bool(keep_version))

        async def rows(self, card: str) -> list[dict[str, Any]]:
            return [dict(r) for r in await self.c.fetch(
                "SELECT * FROM workbench_revisions WHERE card = $1 ORDER BY rev", card)]

        async def open_rows(self) -> list[dict[str, Any]]:
            return [dict(r) for r in await self.c.fetch(
                "SELECT * FROM workbench_revisions WHERE status = ANY($1::text[])", list(OPEN))]

        async def row(self, rid: int) -> dict[str, Any] | None:
            r = await self.c.fetchrow("SELECT * FROM workbench_revisions WHERE id = $1", rid)
            return dict(r) if r else None

        async def insert(self, row: dict[str, Any]) -> int:
            cols = [k for k in row if k != "id"]
            assert set(cols) <= COLUMNS, set(cols) - COLUMNS
            marks = ", ".join(f"${i + 1}" for i in range(len(cols)))
            return await self.c.fetchval(
                f"INSERT INTO workbench_revisions ({', '.join(cols)}) VALUES ({marks}) RETURNING id",
                *[row[k] for k in cols])

        async def update(self, rid: int, fields: dict[str, Any]) -> None:
            # Имена колонок — только из белого списка: значения идут параметрами, а имена
            # собираются в строку, и без проверки это инъекция.
            assert set(fields) <= COLUMNS, set(fields) - COLUMNS
            keys = list(fields)
            sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(keys))
            await self.c.execute(f"UPDATE workbench_revisions SET {sets} WHERE id = $1",
                                 rid, *[fields[k] for k in keys])


# ── Доступ ──────────────────────────────────────────────────────────────────
def _operators() -> frozenset[str]:
    """Кто вправе принимать. Список из окружения; пусто — НИКТО (закрыто по умолчанию):
    приёмка ведёт к релизу, и «по умолчанию все» здесь недопустимо."""
    raw = os.environ.get("STL_WORKBENCH_OPERATORS", "")
    return frozenset(x.strip().lower() for x in raw.split(",") if x.strip())


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _operator(request: Request) -> str:
    email = _auth(request)
    ops = _operators()
    if not ops:
        raise HTTPException(status_code=503, detail=(
            "Список операторов приёмки не задан (STL_WORKBENCH_OPERATORS): принимать некому."))
    if email.lower() not in ops:
        raise HTTPException(status_code=403, detail="Принимать редакции может только оператор.")
    return email


def _worker(authorization: str | None) -> None:
    """Токен воркера. Не настроен — ручки воркера закрыты, а не открыты."""
    token = os.environ.get("WORKBENCH_WORKER_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="Токен воркера не настроен.")
    got = (authorization or "")[7:] if (authorization or "").startswith("Bearer ") else ""
    if not got or not hmac.compare_digest(got.encode(), token.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _svc(request: Request) -> Service:
    store = getattr(request.app.state, "workbench_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Рабочее место недоступно: нет базы.")
    return Service(store)


def _wrap(exc: WorkbenchError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "text": exc.text})


# ── Ручки оператора ─────────────────────────────────────────────────────────
class CreateBody(BaseModel):
    message: str = Field(max_length=MESSAGE_MAX * 2)    # точный предел — в сервисе, с понятным текстом
    parent: int | None = None


class AcceptBody(BaseModel):
    diff_sha: str = Field(max_length=100)


@router.get("/status")
async def status(request: Request):
    _auth(request)
    out = await _svc(request).status()
    out["operators_configured"] = bool(_operators())
    return out


@router.get("/cards/{slug}/revisions")
async def list_revisions(slug: str, request: Request):
    _auth(request)
    _slug(slug)
    try:
        return {"revisions": await _svc(request).list_revisions(slug)}
    except WorkbenchError as e:
        raise _wrap(e) from e


@router.get("/cards/{slug}/revisions/{rev}")
async def get_revision(slug: str, rev: int, request: Request):
    _auth(request)
    _slug(slug)
    try:
        return await _svc(request).get(slug, rev)
    except WorkbenchError as e:
        raise _wrap(e) from e


def _card_meta(slug: str) -> dict[str, Any]:
    """Карточка из витрины: её kind и rev сборщика. Нет карточки — нечего и править.

    Рабочее место только для карточек «логика + инструмент» (kind optimizer); карточки
    исследований охватывают несколько инструментов, и правка «стратегии карточки» у них
    не определена (решение backtests 04.10.2026)."""
    from trader.api import lab_showcase
    try:
        data, _ = lab_showcase._read(lab_showcase.DIR / f"{slug}.json")
    except FileNotFoundError:
        raise WorkbenchError(404, "no_card", "Нет такой карточки в витрине.") from None
    except (OSError, ValueError) as exc:
        raise WorkbenchError(502, "card_unreadable", f"Карточка не читается: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkbenchError(502, "card_unreadable", "Карточка не объект.")
    return data


@router.post("/cards/{slug}/revisions")
async def create_revision(slug: str, body: CreateBody, request: Request):
    actor = _auth(request)
    _slug(slug)
    try:
        meta = _card_meta(slug)
        if (meta.get("kind") or "research") != "optimizer":
            raise WorkbenchError(409, "multi_instrument",
                                 "Карточка охватывает несколько инструментов: правка стратегии здесь "
                                 "не определена. Рабочее место — для карточек «логика + инструмент».")
        base = meta.get("rev")
        base_rev = int(base) if isinstance(base, (int, float)) and base >= 0 else 0
        return await _svc(request).create(slug, body.message, body.parent, actor, base_rev=base_rev)
    except WorkbenchError as e:
        raise _wrap(e) from e


@router.post("/cards/{slug}/revisions/{rev}/cancel")
async def cancel_revision(slug: str, rev: int, request: Request):
    actor = _auth(request)
    _slug(slug)
    try:
        return await _svc(request).cancel(slug, rev, actor)
    except WorkbenchError as e:
        raise _wrap(e) from e


@router.post("/cards/{slug}/revisions/{rev}/accept")
async def accept_revision(slug: str, rev: int, body: AcceptBody, request: Request):
    actor = _operator(request)
    _slug(slug)
    try:
        return await _svc(request).accept(slug, rev, body.diff_sha, actor)
    except WorkbenchError as e:
        raise _wrap(e) from e


@router.post("/cards/{slug}/revisions/{rev}/run")
async def run_revision(slug: str, rev: int, request: Request):
    """Прогон редакции на i9: ОБЫЧНЫЙ /api/v1/backtest/run (решение backtests 04.10.2026).

    Код редакции едет в теле задания, поэтому ветку wb/* не надо доставлять на i9 и в
    main до приёмки. Запускает только оператор: прогон тратит i9, а за кнопкой сидит
    человек. Повторный run — новый прогон, старые остаются."""
    actor = _operator(request)
    _slug(slug)
    svc = _svc(request)
    enqueue = getattr(request.app.state, "enqueue_backtest", None)
    if enqueue is None:
        raise HTTPException(status_code=503, detail={"code": "no_queue", "text": "Очередь прогонов недоступна."})
    try:
        r = await svc.runnable(slug, rev)
        body = build_run_body(r.get("params"))
    except WorkbenchError as e:
        raise _wrap(e) from e
    # no_cache: кэш одиночных прогонов не сравнивает код стратегии, а у редакции он другой
    # при тех же параметрах — без флага вернулся бы результат СТАРОГО кода.
    out = await enqueue(body, request, no_cache=True)
    run_id = str((out or {}).get("run_id") or "")
    if not run_id:
        raise HTTPException(status_code=502, detail={"code": "no_run_id", "text": "Очередь не вернула id прогона."})
    runs = await svc.add_run(slug, rev, run_id, actor)
    log.info("workbench.run", card=slug, rev=rev, run_id=run_id, by=actor)
    return {"run_id": run_id, "engine": (out or {}).get("engine"), "runs": runs}


_SLUG_OK = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")


def _slug(slug: str) -> None:
    """Slug карточки — как у витрины: латиница, цифры, дефис. Он попадёт в имя ветки
    wb/<card>/<rev>, поэтому проверяем жёстко, а не «что пришло»."""
    if not slug or len(slug) > 128 or slug[0] == "-" or set(slug) - _SLUG_OK:
        raise HTTPException(status_code=404, detail="Нет такой карточки.")


# ── Ручки воркера ───────────────────────────────────────────────────────────
class HeartbeatBody(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    version: str = Field(default="", max_length=64)
    busy_with: int | None = None


class ClaimBody(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)


class ReportBody(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    status: str | None = Field(default=None, max_length=16)
    log_append: str | None = Field(default=None, max_length=LOG_MAX)
    diff: str | None = Field(default=None, max_length=DIFF_MAX)
    gates: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    code_ref: str | None = Field(default=None, max_length=200)
    change_note: str | None = Field(default=None, max_length=500)


@router.post("/worker/heartbeat")
async def worker_heartbeat(body: HeartbeatBody, request: Request,
                           authorization: str | None = Header(default=None)):
    _worker(authorization)
    return await _svc(request).heartbeat(body.worker_id, body.version, body.busy_with)


@router.post("/worker/claim")
async def worker_claim(body: ClaimBody, request: Request,
                       authorization: str | None = Header(default=None)):
    _worker(authorization)
    job = await _svc(request).claim(body.worker_id)
    if job is None:
        return Response(status_code=204)
    job["card_ctx"] = _card_ctx(job["card"])
    return job


def _card_ctx(slug: str) -> dict[str, Any] | None:
    """Контекст карточки для модели: workbench_base из <slug>.json витрины {strategy, symbol,
    date_from, date_to, base_params, script_code, point_value}. Нет его или карточки —
    None, задание всё равно отдаём: воркер сам завершит его failed с причиной (так
    договорились с backtests 05.10.2026), а не повиснет в queued."""
    try:
        base = _card_meta(slug).get("workbench_base")
    except WorkbenchError:
        return None
    return base if isinstance(base, dict) else None


@router.post("/worker/revisions/{rid}/report")
async def worker_report(rid: int, body: ReportBody, request: Request,
                        authorization: str | None = Header(default=None)):
    _worker(authorization)
    if body.status is not None and body.status not in STATUSES:
        raise HTTPException(status_code=422, detail={"code": "bad_status", "text": "Неизвестный статус."})
    if body.gates is not None and len(str(body.gates)) > GATES_MAX:
        raise HTTPException(status_code=422, detail={"code": "gates_too_big", "text": "Ворота слишком велики."})
    try:
        return await _svc(request).report(
            rid, body.worker_id, body.status, body.log_append, body.diff, body.gates,
            body.params, body.code_ref, body.change_note)
    except WorkbenchError as e:
        raise _wrap(e) from e
