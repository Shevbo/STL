"""Task 8: RunnerControl.fix_state — recon align forces a robot's believed book
to the QUIK fact. Uses the REAL runner_bridge pb (regenerated in Task 1 with
FixRobotState) so the oneof decode path is exercised end-to-end."""

import json

import pytest

from robot_runner.host import RobotHost
from tests.runner.test_host import FakeBridge, _deploy_rc
from trader.lab.runtime import Order
from trader.quik.pb.shectory.quik.v1 import runner_bridge_pb2 as rb


def _fix_rc(robot_id="r1", position=2, avg=89000.0, clear=True, note="recon"):
    return rb.RunnerControl(fix_state=rb.FixRobotState(
        robot_id=robot_id, set_position=position, set_avg_price=avg,
        clear_working=clear, note=note))


def _seed_divergent_belief(r):
    """Robot believes: long 1 @ 90000 with one working order QUIK does not have."""
    r.runtime._apply_fill("buy", 1, 90000.0, symbol="RIU6")
    cid = "rr:r1:1:phantom"
    r.runtime._orders[cid] = Order(order_id=cid, symbol="RIU6", side="buy",
                                   qty=1, price=90000.0, status="active")


@pytest.mark.asyncio
async def test_fix_state_overwrites_position_avg_and_working(tmp_path):
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    r = host.robots["r1"]
    _seed_divergent_belief(r)
    assert r.runtime.working_orders()          # phantom belief in place

    await host.handle_control(_fix_rc())

    assert r.runtime.signed_position() == 2
    assert r.runtime.avg_price() == 89000.0
    assert r.runtime.working_orders() == []    # clear_working dropped the phantom


@pytest.mark.asyncio
async def test_fix_state_note_lands_in_journal(tmp_path):
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    _seed_divergent_belief(host.robots["r1"])

    await host.handle_control(_fix_rc(note="recon"))

    journal = host.robots["r1"].runtime.fills_tail()[-20:]
    fix_entries = [f for f in journal if f["status"].startswith("fix_state")]
    assert fix_entries, f"no fix_state journal entry in {journal}"
    assert "recon" in fix_entries[-1]["status"]


@pytest.mark.asyncio
async def test_fix_state_persists_to_runner_state(tmp_path):
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    _seed_divergent_belief(host.robots["r1"])

    await host.handle_control(_fix_rc())

    # persisted immediately (no waiting for the next on_bar persist)
    with open(tmp_path / "runner_state.json", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["r1"]["position"] == 2
    assert saved["r1"]["avg"] == 89000.0

    # a fresh host resumes the FIXED state, journal entry included
    host2 = RobotHost(FakeBridge(), str(tmp_path))
    await host2.handle_control(_deploy_rc())
    r2 = host2.robots["r1"]
    assert r2.runtime.signed_position() == 2
    assert r2.runtime.avg_price() == 89000.0
    assert any(f["status"].startswith("fix_state") for f in r2.runtime.fills_tail()[-20:])


@pytest.mark.asyncio
async def test_fix_state_keeps_working_when_clear_false(tmp_path):
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    r = host.robots["r1"]
    _seed_divergent_belief(r)

    await host.handle_control(_fix_rc(position=1, avg=90000.0, clear=False))

    assert r.runtime.signed_position() == 1
    assert len(r.runtime.working_orders()) == 1


@pytest.mark.asyncio
async def test_fix_state_unknown_robot_is_noop(tmp_path):
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    before = host.robots["r1"].runtime.signed_position()
    await host.handle_control(_fix_rc(robot_id="ghost"))
    assert host.robots["r1"].runtime.signed_position() == before


@pytest.mark.asyncio
async def test_fix_state_pnl_correction(tmp_path):
    """set_pnl перезаписывает реализованное и комиссию (пункты) — счётчики
    раннера тоже бывают испорчены (2026-08-06: авто-хил влил вчерашние филлы).
    Без set_pnl нулевые поля P&L НЕ трогают (обычный align так и шлёт)."""
    host = RobotHost(FakeBridge(), str(tmp_path))
    await host.handle_control(_deploy_rc())
    r = host.robots["r1"]
    r.runtime._realized = 999.0
    r.runtime._commission = 111.0

    # Обычный align (set_pnl=False) — P&L нетронут.
    await host.handle_control(_fix_rc(position=1, avg=88000.0))
    assert r.runtime.realized_gross() == 999.0
    assert r.runtime.commission_points() == 111.0

    # Явная правка.
    rc = rb.RunnerControl(fix_state=rb.FixRobotState(
        robot_id="r1", set_position=7, set_avg_price=88964.0,
        clear_working=True, note="pnl fix",
        set_pnl=True, set_realized_gross_pts=146986.3,
        set_commission_pts=19246.7))
    await host.handle_control(rc)
    assert r.runtime.signed_position() == 7
    assert r.runtime.realized_gross() == pytest.approx(146986.3)
    assert r.runtime.commission_points() == pytest.approx(19246.7)
    assert r.runtime.realized_pnl() == pytest.approx(146986.3 - 19246.7)
