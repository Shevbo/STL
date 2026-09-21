"""Время и производительность перебора — справочно, рядом с прогоном.

Запрос оператора 21.09.2026: засекать время перебора и сообщать производительность,
можно хранить вместе с прогоном.

Считает по самим заданиям (`backtest_runs`: claimed_at / finished_at) и по числу
посчитанных наборов (`optimization_leaderboard`). Печатает и, с --append,
дописывает строку в docs/sweep-timings.md — чтобы цифра жила в репозитории и её
видели остальные окна.

    PYTHONPATH=. python scripts/sweep_timing.py --base all0921 [--append]
"""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone

import asyncpg

DOC = "docs/sweep-timings.md"


async def collect(base: str) -> dict:
    c = await asyncpg.connect(os.environ["LAB_DB_URL"])
    runs = await c.fetch(
        "SELECT id, status, claimed_at, finished_at, agent_id FROM backtest_runs "
        "WHERE id LIKE $1", f"%{base}%")
    combos = await c.fetchval(
        "SELECT count(*) FROM optimization_leaderboard WHERE campaign_run LIKE $1",
        f"%{base}%")
    await c.close()

    done = [r for r in runs if r["status"] == "done"]
    claimed = [r["claimed_at"] for r in runs if r["claimed_at"]]
    finished = [r["finished_at"] for r in runs if r["finished_at"]]
    secs = [(r["finished_at"] - r["claimed_at"]).total_seconds()
            for r in done if r["claimed_at"] and r["finished_at"]]
    t0 = min(claimed) if claimed else None
    t1 = max(finished) if finished else None
    wall = (t1 - t0).total_seconds() if (t0 and t1) else 0.0
    return {"jobs": len(runs), "done": len(done),
            "queued": sum(1 for r in runs if r["status"] == "queued"),
            "running": sum(1 for r in runs if r["status"] == "running"),
            "failed": sum(1 for r in runs if r["status"] == "failed"),
            "combos": combos, "t0": t0, "t1": t1, "wall_s": wall,
            "job_secs": secs,
            "agents": sorted({r["agent_id"] for r in runs if r["agent_id"]})}


def render(base: str, d: dict) -> str:
    wall_min = d["wall_s"] / 60.0
    per_min = d["combos"] / wall_min if wall_min > 0 else 0.0
    mean_job = sum(d["job_secs"]) / len(d["job_secs"]) if d["job_secs"] else 0.0
    left = d["queued"] + d["running"]
    eta = (left * mean_job / 60.0) if (left and mean_job) else 0.0
    return (f"{base}: заданий {d['jobs']} (готово {d['done']}, в работе {d['running']}, "
            f"в очереди {d['queued']}, упало {d['failed']}), наборов посчитано "
            f"{d['combos']}\n"
            f"  время: с {str(d['t0'])[:19]} по {str(d['t1'])[:19]} = "
            f"{wall_min:.1f} мин\n"
            f"  производительность: {per_min:.0f} наборов/мин"
            + (f", в среднем {mean_job:.0f} с на задание" if mean_job else "")
            + (f", осталось ~{eta:.0f} мин" if eta else "")
            + f"\n  агент: {', '.join(d['agents']) or 'нет'}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--append", action="store_true",
                    help="дописать строку в docs/sweep-timings.md")
    a = ap.parse_args()

    d = await collect(a.base)
    text = render(a.base, d)
    print(text)
    if a.append and d["combos"]:
        wall_min = d["wall_s"] / 60.0
        per_min = d["combos"] / wall_min if wall_min else 0
        line = (f"| {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} | {a.base} | "
                f"{d['combos']} | {d['jobs']} | {wall_min:.1f} | {per_min:.0f} | "
                f"{', '.join(d['agents'])} |\n")
        head = ("# Время переборов (справочно)\n\n"
                "Пишет scripts/sweep_timing.py --append. Нужен для планирования: по\n"
                "производительности видно, влезает ли задуманная сетка в ночь.\n\n"
                "| когда (UTC) | кампания | наборов | заданий | минут | наборов/мин | агент |\n"
                "|---|---|---|---|---|---|---|\n")
        if not os.path.exists(DOC):
            with open(DOC, "w", encoding="utf-8") as f:
                f.write(head)
        with open(DOC, "a", encoding="utf-8") as f:
            f.write(line)
        print(f"  записано в {DOC}")


if __name__ == "__main__":
    asyncio.run(main())
