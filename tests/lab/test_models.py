from trader.lab.models import Robot


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
