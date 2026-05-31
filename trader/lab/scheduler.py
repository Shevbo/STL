import asyncio
import json
import types
from datetime import datetime, time, timezone, timedelta
from typing import Any

import structlog

log = structlog.get_logger()

_MAX_ACTIVE_ROBOTS = 1   # MVP v1: one robot at a time
_TICK_SECONDS = 60       # robot wakes once per minute bar
_MSK = timezone(timedelta(hours=3))  # Moscow time, no DST since 2014


def _parse_window(schedule: str | None) -> tuple[time, time]:
    """Parse 'HH:MM-HH:MM' trading window. Defaults to 09:00-23:55."""
    if schedule and "-" in schedule:
        try:
            a, b = schedule.split("-", 1)
            ah, am = (int(x) for x in a.strip().split(":"))
            bh, bm = (int(x) for x in b.strip().split(":"))
            return time(ah, am), time(bh, bm)
        except Exception:
            pass
    return time(9, 0), time(23, 55)


def _within_window(now_msk: datetime, win_from: time, win_to: time) -> bool:
    t = now_msk.timetz().replace(tzinfo=None)
    if win_from <= win_to:
        return win_from <= t <= win_to
    # Overnight window (e.g. 23:00-02:00)
    return t >= win_from or t <= win_to


class RobotScheduler:
    def __init__(self, db_pool, tx_client=None, pos_client=None) -> None:
        self._pool = db_pool
        self._tx_client = tx_client
        self._pos_client = pos_client
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        """Load deployed robots from DB and start them."""
        if self._pool is None:
            return
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM robots WHERE deployed = true")
        for row in rows:
            robot = _row_to_robot(row)
            await self._on_robot_deployed(robot)

    async def _on_robot_deployed(self, robot) -> None:
        if len(self._tasks) >= _MAX_ACTIVE_ROBOTS:
            log.warning("lab.scheduler.max_robots_reached", robot_id=robot.id)
            return
        task = asyncio.create_task(
            self._window_loop(robot), name=f"robot-{robot.id}"
        )
        self._tasks[robot.id] = task
        win_from, win_to = _parse_window(robot.schedule)
        log.info("lab.scheduler.robot_started", robot_id=robot.id,
                 window=f"{win_from}-{win_to}")

    async def deploy_robot(self, robot) -> None:
        if robot.id in self._tasks:
            return
        await self._on_robot_deployed(robot)

    async def stop_robot(self, robot_id: str) -> None:
        task = self._tasks.pop(robot_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        log.info("lab.scheduler.robot_stopped", robot_id=robot_id)

    async def _window_loop(self, robot) -> None:
        """
        Tick once per minute. Run the robot's on_bar only when the current
        Moscow time is inside the robot's trading window. Outside the window
        the robot stays idle (does not trade).
        """
        win_from, win_to = _parse_window(robot.schedule)
        while True:
            now_msk = datetime.now(_MSK)
            if _within_window(now_msk, win_from, win_to):
                try:
                    await self._run_robot_task(robot)
                except Exception as exc:
                    log.error("lab.scheduler.robot_error",
                              robot_id=robot.id, error=str(exc))
            # Sleep to the next minute boundary
            await asyncio.sleep(_TICK_SECONDS)

    async def _run_robot_task(self, robot) -> None:
        """Execute one robot tick (one bar)."""
        from trader.lab.runtime import LiveRuntime  # avoid import cycle
        mod = types.ModuleType("robot_script")
        exec(compile(robot.script_code, f"<robot:{robot.id}>", "exec"), mod.__dict__)
        # paper by default; real trading only when state_json.live_real is true
        state = robot.state_json if isinstance(robot.state_json, dict) else {}
        paper = not bool(state.get("live_real", False))
        runtime = LiveRuntime(
            robot_id=robot.id, pool=self._pool,
            tx_client=self._tx_client, pos_client=self._pos_client,
            paper=paper,
        )
        if hasattr(mod, "on_bar"):
            await mod.on_bar(runtime, robot.params_json)
        await runtime.flush_state()

    async def stop_all(self) -> None:
        for robot_id in list(self._tasks):
            await self.stop_robot(robot_id)


def _row_to_robot(row) -> Any:
    from trader.lab.models import Robot
    return Robot(
        id=row["id"],
        user_email=row["user_email"],
        stl_link_id=row["stl_link_id"],
        name=row["name"],
        script_code=row["script_code"],
        params_json=row["params_json"] if isinstance(row["params_json"], dict)
                    else json.loads(row["params_json"]),
        state_json=row["state_json"] if isinstance(row["state_json"], dict)
                   else json.loads(row["state_json"]),
        schedule=row["schedule"],
        deployed=row["deployed"],
    )
