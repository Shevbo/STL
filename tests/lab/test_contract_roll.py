from datetime import date
import pytest
from trader.lab import contract_roll as cr


def test_base_of():
    assert cr.base_of("RIM6") == "RI"
    assert cr.base_of("BRN6") == "BR"
    assert cr.base_of("SiU6") == "Si"
    assert cr.base_of("GZU6") == "GZ"
    assert cr.base_of("RI") is None        # base code, not a specific contract
    assert cr.base_of("") is None


@pytest.mark.asyncio
async def test_front_contract_picks_nearest_future(monkeypatch):
    # Mocked ISS security table: (SECID, LASTTRADEDATE)
    rows = [
        ("RIM6", "2026-06-18"),   # expired
        ("RIU6", "2026-09-17"),   # front (nearest future)
        ("RIZ6", "2026-12-17"),   # back month
        ("BRU6", "2026-08-31"),   # different series
    ]
    async def fake_secs():
        return rows
    monkeypatch.setattr(cr, "_securities", fake_secs)
    got = await cr.front_contract("RIM6", today=date(2026, 7, 18))
    assert got == "RIU6"


@pytest.mark.asyncio
async def test_front_contract_none_when_no_future(monkeypatch):
    async def fake_secs():
        return [("RIM6", "2026-06-18")]   # only expired
    monkeypatch.setattr(cr, "_securities", fake_secs)
    assert await cr.front_contract("RIM6", today=date(2026, 7, 18)) is None
    # non-specific symbol → None regardless
    assert await cr.front_contract("RI", today=date(2026, 7, 18)) is None


def test_fills_to_rows_maps_and_marks_sim():
    from datetime import datetime
    fills = [
        {"side": "buy",  "price": 88000.0, "qty": 1, "time": 1_760_000_000},
        {"side": "sell", "price": 88100.0, "qty": 1, "time": 1_760_000_060},
    ]
    rows = cr.fills_to_rows("robot-x", "RIU6", fills)
    assert len(rows) == 2
    # tuple order: (id, robot_id, symbol, side, qty, price, order_id, status, ts)
    r0 = rows[0]
    assert r0[1] == "robot-x" and r0[2] == "RIU6" and r0[3] == "buy" and r0[4] == 1
    assert float(r0[5]) == 88000.0
    assert r0[6].startswith("sim-")
    assert r0[7] == "paper"
    # ISS bar time (MSK-wall-as-UTC) shifted -3h to true UTC, naive
    assert r0[8] == datetime.utcfromtimestamp(1_760_000_000 - 3 * 3600)
    assert r0[8].tzinfo is None
    assert rows[0][6] != rows[1][6]          # unique sim ids
