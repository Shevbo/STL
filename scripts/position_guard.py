"""Сверка ПОЗИЦИИ робота с журналом — автомат вместо оператора с экселем.

ЗАЧЕМ. 17.08.2026 после зависания VDS и перезапуска QUIK раннер поднял веру из
замёрзшего файла состояния: он считал себя во флэте, а на бирже висел шорт 9
контрактов. Робот торговал от ложной базы весь вечер и сам бы этот шорт не
закрыл никогда — он его не видел. Нашёл расхождение ЧЕЛОВЕК, посчитав купли и
продажи в экселе; после ручной правки веры робот закрыл шорт в ту же минуту.

Почему этого не поймала штатная сверка: recon по построению НЕ сравнивает
позицию ни с чем (internal/recon/recon.go: «Position is CONTEXTUAL only … NEVER
reconciled against account-net»). Он сверяет заявки и сделки, и в тот вечер
честно сказал «сделки не сходятся» — но с ПУСТЫМ планом, а доктрина «пустой план
= расхождения нет» учила это игнорировать.

ПОЧЕМУ ЖУРНАЛ — ГОДНЫЙ СУДЬЯ. `algo_trades.pos_after` считается САМИМ журналом
как нарастающая сумма филлов (trader/quik/algo_ledger.py), а не берётся из
отчёта раннера. У журнала есть память, которую раннер потерял: в тот вечер он
держал −9, пока карточка показывала 0.

ЧЕГО ЭТОТ СТОРОЖ НЕ ДЕЛАЕТ. Ничего не закрывает, не паузит и не правит веру.
Автоматика, торгующая по ошибочным данным, хуже её отсутствия: на счёте живут
три робота и ручная книга оператора, и «выровнять» позицию автоматом значит
торговать против человека. Сторож только КРИЧИТ.

Печатает строки «ключ|текст» — формат сторожа хостера (~/stl-watchdog-probe.sh),
который уже умеет слать SMS с кулдауном по ключу.

    PYTHONPATH=. python scripts/position_guard.py            # на хостере
    PYTHONPATH=. python scripts/position_guard.py --json     # для отладки
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

STATE_PATH = os.path.expanduser("~/.stl-position-guard.json")
API = "http://localhost:8000"


def believed_positions(mirror: dict) -> dict[str, int]:
    """Во что верят РЕАЛЬНЫЕ роботы. Бумажные не трогаем: у них нет биржи."""
    out: dict[str, int] = {}
    for r in mirror.get("robots") or []:
        if r.get("paper"):
            continue
        rid = str(r.get("robot_id") or "")
        if rid:
            out[rid] = int(r.get("position") or 0)
    return out


def compare(believed: dict[str, int], ledger: dict[str, int],
            prev: dict) -> tuple[list[tuple[str, str]], dict]:
    """Расхождения + новое состояние.

    Тревога поднимается только со ВТОРОГО подряд замера с тем же расхождением:
    между филлом и его попаданием в журнал есть секунды, и мгновенный снимок
    ловил бы их как ошибку. Повторяемость отличает настоящее расхождение от
    гонки.
    """
    now_bad = {}
    for rid, bel in sorted(believed.items()):
        if rid not in ledger:
            continue                      # робот ещё не торговал — сверять не с чем
        led = ledger[rid]
        if bel != led:
            now_bad[rid] = [bel, led]
    problems = []
    was = prev.get("bad") or {}
    for rid, (bel, led) in now_bad.items():
        if was.get(rid) == [bel, led]:
            problems.append((
                "poscheck",
                f"РАСХОЖДЕНИЕ ПОЗИЦИИ у {rid}: робот верит {bel:+d}, журнал сделок "
                f"даёт {led:+d} (разница {led - bel:+d}). Робот торгует от ложной "
                f"базы и сам это не исправит. Проверь позицию в QUIK и поправь веру "
                f"через локальную страницу агента."))
    return problems, {"bad": now_bad, "ts": int(time.time())}


def _get(path: str, token: str) -> dict:
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    token = os.environ.get("TK", "")
    if not token:
        from trader.auth.portal import make_session_token
        token = make_session_token("bshevelev75@gmail.com",
                                   os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    try:
        mirror = _get("/api/v1/quik/robots-mirror", token)
    except Exception as exc:                          # noqa: BLE001
        print(f"poscheck_down|сторож позиции не смог прочитать зеркало: {exc}")
        return 0
    believed = believed_positions(mirror)
    if not believed:
        return 0                                      # реальных роботов нет — молчим

    import asyncio

    import asyncpg

    async def ledger() -> dict[str, int]:
        dsn = os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql")
        c = await asyncpg.connect(dsn)
        try:
            rows = await c.fetch(
                "SELECT DISTINCT ON (robot_id) robot_id, pos_after "
                "  FROM algo_trades WHERE mode='real' AND robot_id = ANY($1::text[]) "
                " ORDER BY robot_id, ts_ms DESC, seq DESC", list(believed))
        finally:
            await c.close()
        return {r["robot_id"]: int(r["pos_after"]) for r in rows}

    led = asyncio.run(ledger())
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:                                 # noqa: BLE001
        prev = {}
    problems, state = compare(believed, led, prev)
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:                                 # noqa: BLE001
        pass
    if args.json:
        print(json.dumps({"believed": believed, "ledger": led,
                          "problems": problems}, ensure_ascii=False, indent=1))
        return 0
    for key, text in problems:
        print(f"{key}|{text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
