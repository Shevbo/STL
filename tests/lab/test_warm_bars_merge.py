"""Прогрев баров робота не имеет права срезать историю контракта.

13.09.2026: scripts/warm_robot_bars.py в 05:00 перезаписал agent_bars/RIU6.json окном
в 75 дней, стерев голову полного ряда, который туда же положил rf_fetch_contracts.py.
"""
import asyncio
import json
from types import SimpleNamespace

import scripts.warm_robot_bars as w


def _bar(t: int, px: float = 100.0):
    return SimpleNamespace(time=t, open=px, high=px, low=px, close=px, volume=1)


def test_warm_keeps_older_history_and_refreshes_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "OUT_DIR", str(tmp_path))
    path = tmp_path / "RIU6.json"
    # Полная история: бары 1..10, хвост 8..10 со старыми ценами.
    path.write_text(json.dumps({"rows": [[t, 1, 1, 1, 1, 1] for t in range(1, 11)]}))

    async def fake_iss(symbol, lo, hi, interval=1):
        return [_bar(t, 200.0) for t in (8, 9, 10, 11)]     # окно прогрева короче

    monkeypatch.setattr(w, "load_bars_iss", fake_iss)
    asyncio.run(w.warm_one("RIU6", None, None))

    rows = json.loads(path.read_text())["rows"]
    assert [r[0] for r in rows] == list(range(1, 12))       # голова цела, хвост дописан
    assert [r[4] for r in rows if r[0] >= 8] == [200.0] * 4  # хвост из свежей выгрузки
    assert rows[0][4] == 1                                   # старые бары не тронуты
