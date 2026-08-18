"""Очередь задач для автономных исполнителей: скучный фундамент, без моделей.

ЗАЧЕМ ИМЕННО ТАК. 17.08.2026 автономный работник уже был — почтовый автоответчик,
и он сжёг пятичасовое окно тарифа за ночь: 247 запусков модели, один раз в 2.9
минуты, без единого человека. Рядом месяцами живёт ДРУГОЙ автономный работник —
агент перебора на i9: он забирает задание из очереди, отчитывается и при пустой
очереди не стоит НИЧЕГО. Разница не в модели и не в промпте, а в ТРИГГЕРЕ:
«пришло сообщение» самовозбуждается, «есть задание с владельцем и бюджетом» —
нет. Эта очередь повторяет второй путь.

ЧЕГО ЗДЕСЬ НЕТ НАМЕРЕННО: ни одного вызова модели. Это склад заданий и счётчик
расхода. Исполнитель — отдельная программа, и она обязана быть заменяемой:
сегодня Claude, завтра другая модель, послезавтра человек.

ТРИ ПРЕДОХРАНИТЕЛЯ, каждый из вчерашнего инцидента:
  бюджет задачи   исполнитель, исчерпав его, ОСТАНАВЛИВАЕТСЯ и пишет, сколько
                  успел, а не «старается уложиться»;
  дневной потолок claim перестаёт выдавать задачи, когда суммарный расход за
                  сутки превысил потолок — петля без бюджета невозможна;
  рубильник       ключ в agent_control, читается ПЕРЕД выдачей задачи. Живёт вне
                  машины исполнителя: вчера бот крутился на Windows-VM и гасился
                  только оттуда.

Транспорт — CLI поверх ssh, как у почты окон: на машине исполнителя может не быть
доступа к БД, но ssh к хостеру есть у всех.

    python scripts/taskq.py init
    python scripts/taskq.py add --zone lab --title "..." --body "..." --budget 50000
    python scripts/taskq.py list
    python scripts/taskq.py claim --agent dev1 --zone lab
    python scripts/taskq.py beat --id 12 --agent dev1 --tokens 1200
    python scripts/taskq.py done --id 12 --agent dev1 --tokens 4300 --result "..."
    python scripts/taskq.py spend
    python scripts/taskq.py stop   # рубильник
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import asyncpg

# Уровень модели, который задача ТРЕБУЕТ. Смысл поля не в экономии, а в защите:
# механическую задачу не жалко отдать дешёвой модели, а задачу, двигающую живую
# торговлю, нельзя молча подменить — результат будет выглядеть правдоподобно, и
# проверить его будет некому.
TIERS = ("mechanical", "standard", "trading")
ZONES = ("lab", "api", "frontend", "agent", "infra", "any")
STALE_CLAIM_SEC = 900          # не бьётся сердце 15 минут — задача возвращается в очередь

DDL = """
CREATE TABLE IF NOT EXISTS agent_tasks (
    id            BIGSERIAL PRIMARY KEY,
    zone          TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT NOT NULL,
    tier          TEXT NOT NULL DEFAULT 'standard',
    priority      INT  NOT NULL DEFAULT 0,
    budget_tokens BIGINT NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'queued',
    claimed_by    TEXT,
    claimed_at    TIMESTAMPTZ,
    heartbeat_at  TIMESTAMPTZ,
    tokens_spent  BIGINT NOT NULL DEFAULT 0,
    result        TEXT,
    created_by    TEXT NOT NULL DEFAULT 'operator',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS agent_tasks_pick ON agent_tasks (status, priority DESC, id);
"""


def dsn() -> str:
    return os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql")


async def _ctl(c, key: str, default: str = "") -> str:
    v = await c.fetchval("SELECT value FROM agent_control WHERE key=$1", key)
    return v if v is not None else default


def budget_left(spent_day: int, cap_day: int) -> int:
    """Сколько ещё можно потратить за сутки. cap_day<=0 — потолка нет."""
    if cap_day <= 0:
        return sys.maxsize
    return max(0, cap_day - spent_day)


def may_claim(disabled: str, spent_day: int, cap_day: int) -> str:
    """Пустая строка = можно выдавать задачу, иначе причина отказа.

    Проверка вынесена отдельной чистой функцией, потому что это единственное
    место, где решается «тратить или не тратить», и оно обязано проверяться
    тестом, а не надеждой.
    """
    if str(disabled).strip() in ("1", "true", "yes"):
        return "очередь остановлена рубильником (taskq_disabled)"
    if budget_left(spent_day, cap_day) <= 0:
        return f"дневной потолок исчерпан: потрачено {spent_day:,} из {cap_day:,}"
    return ""


async def cmd_init(a) -> int:
    c = await asyncpg.connect(dsn())
    try:
        await c.execute(DDL)
    finally:
        await c.close()
    print("таблица agent_tasks готова")
    return 0


async def cmd_add(a) -> int:
    if a.zone not in ZONES:
        sys.exit(f"зона «{a.zone}»: допустимо {', '.join(ZONES)}")
    if a.tier not in TIERS:
        sys.exit(f"уровень «{a.tier}»: допустимо {', '.join(TIERS)}")
    c = await asyncpg.connect(dsn())
    try:
        tid = await c.fetchval(
            "INSERT INTO agent_tasks(zone,title,body,tier,priority,budget_tokens,created_by)"
            " VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING id",
            a.zone, a.title, a.body, a.tier, a.priority, a.budget, a.by)
    finally:
        await c.close()
    print(f"задача #{tid} поставлена: [{a.zone}/{a.tier}] {a.title} | бюджет {a.budget:,}")
    return 0


async def cmd_list(a) -> int:
    c = await asyncpg.connect(dsn())
    try:
        rows = await c.fetch(
            "SELECT id,zone,tier,status,priority,budget_tokens,tokens_spent,claimed_by,"
            "       title, extract(epoch from (now()-created_at))::int age"
            "  FROM agent_tasks"
            " WHERE ($1='' OR zone=$1) AND ($2='' OR status=$2)"
            " ORDER BY status, priority DESC, id LIMIT $3", a.zone, a.status, a.limit)
    finally:
        await c.close()
    if not rows:
        print("задач нет")
        return 0
    for r in rows:
        who = f" <- {r['claimed_by']}" if r["claimed_by"] else ""
        print(f"#{r['id']:<4} {r['status']:<8} {r['zone']:<8} {r['tier']:<10} "
              f"бюджет {r['budget_tokens']:>8,} потрачено {r['tokens_spent']:>8,} "
              f"{r['age'] // 3600}ч{who}  {r['title'][:60]}")
    return 0


async def cmd_claim(a) -> int:
    """Атомарная выдача задачи. Печатает JSON — это ПАКЕТ ВВОДА исполнителя.

    Пакет самодостаточен намеренно: исполнитель обязан начинать с чтения, а не с
    воспоминаний. Тогда смена модели, сессии или машины не меняет ничего.
    """
    c = await asyncpg.connect(dsn())
    try:
        spent = await c.fetchval(
            "SELECT coalesce(sum(tokens_spent),0) FROM agent_tasks"
            " WHERE claimed_at > now() - interval '1 day'") or 0
        cap = int(await _ctl(c, "taskq_budget_day", "0") or 0)
        why = may_claim(await _ctl(c, "taskq_disabled", "0"), int(spent), cap)
        if why:
            print(json.dumps({"task": None, "reason": why}, ensure_ascii=False))
            return 0
        # Возврат зависших: исполнитель мог умереть, и задача не должна пропасть.
        await c.execute(
            "UPDATE agent_tasks SET status='queued', claimed_by=NULL, claimed_at=NULL"
            " WHERE status='claimed' AND heartbeat_at < now() - make_interval(secs => $1)",
            STALE_CLAIM_SEC)
        row = await c.fetchrow(
            "UPDATE agent_tasks SET status='claimed', claimed_by=$1,"
            "       claimed_at=now(), heartbeat_at=now()"
            " WHERE id = (SELECT id FROM agent_tasks"
            "              WHERE status='queued' AND ($2='' OR zone=$2 OR zone='any')"
            "              ORDER BY priority DESC, id"
            "              FOR UPDATE SKIP LOCKED LIMIT 1)"
            " RETURNING id,zone,title,body,tier,budget_tokens", a.agent, a.zone)
    finally:
        await c.close()
    if row is None:
        print(json.dumps({"task": None, "reason": "очередь пуста"}, ensure_ascii=False))
        return 0
    print(json.dumps({"task": dict(row), "budget_day_left": budget_left(int(spent), cap)},
                     ensure_ascii=False))
    return 0


async def cmd_beat(a) -> int:
    c = await asyncpg.connect(dsn())
    try:
        row = await c.fetchrow(
            "UPDATE agent_tasks SET heartbeat_at=now(),"
            "       tokens_spent=GREATEST(tokens_spent,$3)"
            " WHERE id=$1 AND claimed_by=$2 AND status='claimed'"
            " RETURNING budget_tokens, tokens_spent", a.id, a.agent, a.tokens)
    finally:
        await c.close()
    if row is None:
        print(json.dumps({"ok": False, "reason": "задача не твоя или уже закрыта"},
                         ensure_ascii=False))
        return 1
    over = row["budget_tokens"] and row["tokens_spent"] >= row["budget_tokens"]
    print(json.dumps({"ok": True, "stop": bool(over),
                      "left": max(0, row["budget_tokens"] - row["tokens_spent"])},
                     ensure_ascii=False))
    return 0


async def cmd_done(a) -> int:
    st = "failed" if a.fail else "done"
    c = await asyncpg.connect(dsn())
    try:
        row = await c.fetchrow(
            "UPDATE agent_tasks SET status=$4, finished_at=now(), result=$5,"
            "       tokens_spent=GREATEST(tokens_spent,$3)"
            " WHERE id=$1 AND claimed_by=$2 RETURNING id", a.id, a.agent, a.tokens, st, a.result)
    finally:
        await c.close()
    if row is None:
        print("задача не твоя или её нет")
        return 1
    print(f"задача #{a.id} закрыта как {st}, потрачено {a.tokens:,}")
    return 0


async def cmd_spend(a) -> int:
    c = await asyncpg.connect(dsn())
    try:
        rows = await c.fetch(
            "SELECT claimed_by, count(*) n, sum(tokens_spent) t"
            "  FROM agent_tasks WHERE claimed_at > now() - make_interval(hours => $1)"
            " GROUP BY 1 ORDER BY t DESC NULLS LAST", a.hours)
        cap = int(await _ctl(c, "taskq_budget_day", "0") or 0)
        disabled = await _ctl(c, "taskq_disabled", "0")
    finally:
        await c.close()
    total = sum(int(r["t"] or 0) for r in rows)
    print(f"за {a.hours} ч: задач {sum(r['n'] for r in rows)}, токенов {total:,}"
          f" | дневной потолок {cap:,}" if cap else
          f"за {a.hours} ч: задач {sum(r['n'] for r in rows)}, токенов {total:,}"
          " | дневного потолка НЕТ")
    for r in rows:
        print(f"  {r['claimed_by'] or '(никто)':<14} задач {r['n']:>3} токенов {int(r['t'] or 0):>10,}")
    if str(disabled).strip() in ("1", "true", "yes"):
        print("  ВНИМАНИЕ: очередь остановлена рубильником")
    return 0


async def cmd_switch(a) -> int:
    val = "1" if a.cmd == "stop" else "0"
    c = await asyncpg.connect(dsn())
    try:
        await c.execute(
            "INSERT INTO agent_control(key,value) VALUES('taskq_disabled',$1)"
            " ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", val)
    finally:
        await c.close()
    print("очередь ОСТАНОВЛЕНА" if val == "1" else "очередь запущена")
    return 0


async def cmd_budget(a) -> int:
    c = await asyncpg.connect(dsn())
    try:
        await c.execute(
            "INSERT INTO agent_control(key,value) VALUES('taskq_budget_day',$1)"
            " ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", str(a.tokens))
    finally:
        await c.close()
    print(f"дневной потолок: {a.tokens:,} токенов" if a.tokens > 0 else "дневной потолок снят")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Очередь задач автономных исполнителей")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("add")
    p.add_argument("--zone", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--tier", default="standard")
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--budget", type=int, default=50_000)
    p.add_argument("--by", default="operator")
    p = sub.add_parser("list")
    p.add_argument("--zone", default="")
    p.add_argument("--status", default="")
    p.add_argument("--limit", type=int, default=40)
    p = sub.add_parser("claim")
    p.add_argument("--agent", required=True)
    p.add_argument("--zone", default="")
    p = sub.add_parser("beat")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--tokens", type=int, default=0)
    p = sub.add_parser("done")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--tokens", type=int, default=0)
    p.add_argument("--result", default="")
    p.add_argument("--fail", action="store_true")
    p = sub.add_parser("spend")
    p.add_argument("--hours", type=int, default=24)
    sub.add_parser("stop")
    sub.add_parser("start")
    p = sub.add_parser("budget")
    p.add_argument("tokens", type=int)
    a = ap.parse_args()
    fn = {"init": cmd_init, "add": cmd_add, "list": cmd_list, "claim": cmd_claim,
          "beat": cmd_beat, "done": cmd_done, "spend": cmd_spend,
          "stop": cmd_switch, "start": cmd_switch, "budget": cmd_budget}[a.cmd]
    return asyncio.run(fn(a))


if __name__ == "__main__":
    raise SystemExit(main())
