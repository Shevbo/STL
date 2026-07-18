import types
import pytest
from trader.lab.scheduler import RobotScheduler
from trader.lab import contract_roll as cr


def _robot(symbol, live_real=False):
    return types.SimpleNamespace(
        id="r1",
        params_json={"symbol": symbol, "qty": 1, "tp_atr": 60},
        state_json={"live_real": live_real, "trend": "up", "position": 3},
    )


@pytest.mark.asyncio
async def test_roll_switches_symbol_and_resets_state(monkeypatch):
    async def fake_front(sym, today=None):
        return "RIU6"
    monkeypatch.setattr(cr, "front_contract", fake_front)
    sch = RobotScheduler(db_pool=None)
    r = _robot("RIM6")
    await sch._maybe_roll(r)
    assert r.params_json["symbol"] == "RIU6"          # rolled
    assert r.params_json["qty"] == 1                  # other params kept
    assert r.state_json == {"live_real": False}       # strategy state wiped
    assert sch._robot_states["r1"] == {"live_real": False}


@pytest.mark.asyncio
async def test_no_roll_when_already_front(monkeypatch):
    async def fake_front(sym, today=None):
        return "RIU6"
    monkeypatch.setattr(cr, "front_contract", fake_front)
    sch = RobotScheduler(db_pool=None)
    r = _robot("RIU6")
    await sch._maybe_roll(r)
    assert r.params_json["symbol"] == "RIU6"
    assert r.state_json["trend"] == "up"              # untouched


@pytest.mark.asyncio
async def test_never_roll_real_robot(monkeypatch):
    async def fake_front(sym, today=None):
        return "RIU6"
    monkeypatch.setattr(cr, "front_contract", fake_front)
    sch = RobotScheduler(db_pool=None)
    r = _robot("RIM6", live_real=True)
    await sch._maybe_roll(r)
    assert r.params_json["symbol"] == "RIM6"          # real robot NOT rolled
