"""API качества роботов: доля выигранных кругов, recovery factor, серия для графика.

Хит-парад ранжирует по финрезу, и мелкий по объёму робот там не виден, хотя его
качество может быть выше — такой кандидат на увеличение объёма, а крупный с низким
RF кандидат на выключение (задача оператора 25.09.2026). Считается по журналу
algo_trades той же методикой, что в бэктесте (trader/quik/robot_stats.py).

История берётся ЦЕЛИКОМ: period=all и bucket=day|week|month дают график за всю
жизнь робота. Скользящие окна day/week/month остаются для сводки «за период».
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from trader.auth.guard import require_auth
from trader.quik import robot_stats

router = APIRouter(prefix="/api/v1/quik", tags=["quik-robot-stats"])

_FIELDS = "robot_id, mode, ts_ms, symbol, side, qty, pnl_net_rub, pos_after"


def _auth(request: Request) -> str:
    return require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)


def _pool(request: Request):
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="Журнал недоступен: нет БД")
    return pool


async def collect(pool, period: str, bucket: str, mode: str | None,
                  robot_id: str | None) -> list[dict[str, Any]]:
    lo, hi = robot_stats.period_bounds(period)
    where = ["ts_ms >= $1", "ts_ms <= $2"]
    args: list[Any] = [lo, hi]
    if mode in ("real", "paper"):
        args.append(mode)
        where.append(f"mode = ${len(args)}")
    if robot_id:
        args.append(robot_id)
        where.append(f"robot_id = ${len(args)}")
    rows = await pool.fetch(
        f"SELECT {_FIELDS} FROM algo_trades WHERE {' AND '.join(where)} ORDER BY ts_ms",
        *args)
    return robot_stats.by_robot([dict(r) for r in rows], period, bucket)


@router.get("/robot-stats")
async def robot_stats_endpoint(request: Request, period: str = "all",
                               bucket: str = "day", mode: str = "real",
                               robot_id: str | None = None):
    """Качество каждого робота за период плюс серия для графика."""
    _auth(request)
    if period not in robot_stats.PERIODS:
        raise HTTPException(status_code=422,
                            detail=f"period должен быть одним из {robot_stats.PERIODS}")
    if bucket not in robot_stats.BUCKETS:
        raise HTTPException(status_code=422,
                            detail=f"bucket должен быть одним из {robot_stats.BUCKETS}")
    robots = await collect(_pool(request), period, bucket,
                           None if mode == "all" else mode, robot_id)
    lo, hi = robot_stats.period_bounds(period)
    return {"period": period, "bucket": bucket, "mode": mode,
            "from_ms": lo, "to_ms": hi, "count": len(robots), "robots": robots}
