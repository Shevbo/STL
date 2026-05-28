from trader.lab.models import Robot, BacktestRun


def test_robot_defaults():
    r = Robot(
        id="abc",
        user_email="a@b.com",
        stl_link_id="link1",
        name="Test",
        script_code="async def on_bar(stl, p): pass",
    )
    assert r.deployed is False
    assert r.schedule == "*/5 * * * *"
    assert r.params_json == {}
    assert r.state_json == {}


def test_backtest_run_status():
    run = BacktestRun(
        id="run1",
        robot_id="r1",
        params_grid={"ema_fast": [5, 10]},
        date_from="2026-01-01T00:00:00Z",
        date_to="2026-03-01T00:00:00Z",
    )
    assert run.status == "pending"
