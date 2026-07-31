"""Запись филлов team-46 в live_trades: уникальный order_id, роль, вес, время филла.

Ровно эти четыре вещи ломали стенд: константный order_id схлопывал таблицу в одну
строку событий, отсутствие веса делало любую доходность вымыслом, а now() вместо
времени филла склеивал батч в одну секунду.
"""
import asyncio
import datetime

from trader.lab.ai46 import portfolio as PF
from trader.lab.ai46.execution import PaperFill
from trader.lab.ai46.service import Ai46Service


class _StubPool:
    def __init__(self):
        self.rows = []

    async def executemany(self, _sql, rows):
        self.rows.extend(rows)

    async def execute(self, *_a, **_k):
        return "OK"


def _svc(pool):
    return Ai46Service(pool, lambda: None, ["SiU6"], llm_enabled=False, order_flow_live=False)


def test_persist_encodes_role_weight_and_fill_time():
    pool = _StubPool()
    svc = _svc(pool)
    t0 = 1_780_000_000.0
    svc.runner.exec.fills = [
        PaperFill(t0, "SiU6", "sell", 0.015, 80000.0, "open"),
        PaperFill(t0 + 900, "SiU6", "buy", 0.015, 79200.0, "close_soft"),
    ]
    asyncio.run(svc._persist_fills())

    assert len(pool.rows) == 2
    order_ids = [r[6] for r in pool.rows]
    assert len(set(order_ids)) == 2, "order_id обязан быть уникальным на филл"
    metas = [PF.parse_meta(o) for o in order_ids]
    assert [m["kind"] for m in metas] == ["open", "close_soft"]
    assert all(abs(m["size_pct"] - 0.015) < 1e-9 for m in metas)
    # время строки = время филла, а не момент записи
    assert pool.rows[0][8] == datetime.datetime.fromtimestamp(t0, tz=datetime.timezone.utc)
    assert pool.rows[1][8] - pool.rows[0][8] == datetime.timedelta(seconds=900)
    # и разметка витрины по этим строкам даёт настоящую доходность сделки
    trades = [{"time": int(r[8].timestamp()), "symbol": r[2], "side": r[3],
               "price": float(r[5]), "order_id": r[6]} for r in pool.rows]
    s = PF.enrich(trades)
    assert s["closes"] == 1 and abs(trades[1]["ret_pct"] - 1.0) < 1e-9


def test_seq_survives_a_service_restart():
    """Счётчик _persisted обнуляется рестартом — order_id всё равно не должен совпасть."""
    pool = _StubPool()
    f = PaperFill(1_780_000_000.0, "SiU6", "sell", 0.02, 80000.0, "open")
    a = _svc(pool)
    a.runner.exec.fills = [f]
    asyncio.run(a._persist_fills())
    b = _svc(pool)                                    # «перезапуск»: счётчик снова 0
    b.runner.exec.fills = [PaperFill(f.time + 60, "SiU6", "buy", 0.02, 80100.0, "close_soft")]
    asyncio.run(b._persist_fills())
    assert pool.rows[0][6] != pool.rows[1][6]
