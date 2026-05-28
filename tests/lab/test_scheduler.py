import asyncio
import pytest
from trader.lab.scheduler import RobotScheduler
from trader.lab.models import Robot


def make_robot(schedule="* * * * *", deployed=True) -> Robot:
    return Robot(
        id="r1", user_email="a@b.com", stl_link_id="l1",
        name="Test", script_code="async def on_bar(s,p): pass",
        schedule=schedule, deployed=deployed,
    )


@pytest.mark.asyncio
async def test_scheduler_starts_deployed_robot():
    scheduler = RobotScheduler(db_pool=None)
    robot = make_robot()
    called = []

    async def fake_run(r):
        called.append(r.id)

    scheduler._run_robot_task = fake_run
    await scheduler._on_robot_deployed(robot)
    assert robot.id in scheduler._tasks


@pytest.mark.asyncio
async def test_scheduler_stops_robot():
    scheduler = RobotScheduler(db_pool=None)
    robot = make_robot()
    task = asyncio.create_task(asyncio.sleep(100))
    scheduler._tasks[robot.id] = task
    await scheduler.stop_robot(robot.id)
    assert robot.id not in scheduler._tasks
    assert task.cancelled()
