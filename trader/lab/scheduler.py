import asyncio
import types
from datetime import datetime, timezone
from typing import Any

import structlog
from croniter import croniter

log = structlog.get_logger()

_MAX_ACTIVE_ROBOTS = 1  # MVP v1: one robot at a time


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
            self._cron_loop(robot), name=f"robot-{robot.id}"
        )
        self._tasks[robot.id] = task
        log.info("lab.scheduler.robot_started", robot_id=robot.id)

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

    async def _cron_loop(self, robot) -> None:
        cron = croniter(robot.schedule, datetime.now(timezone.utc))
        while True:
            next_run = cron.get_next(datetime)
            now = datetime.now(timezone.utc)
            wait = (next_run - now).total_seconds()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                await self._run_robot_task(robot)
            except Exception as exc:
                log.error("lab.scheduler.robot_error",
                          robot_id=robot.id, error=str(exc))

    async def _run_robot_task(self, robot) -> None:
        from trader.lab.runtime import LiveRuntime
        mod = types.ModuleType("robot_script")
        exec(compile(robot.script_code, f"<robot:{robot.id}>", "exec"), mod.__dict__)
        runtime = LiveRuntime(
            robot_id=robot.id, pool=self._pool,
            tx_client=self._tx_client,
            pos_client=self._pos_client,
        )
        if hasattr(mod, "on_bar"):
            await mod.on_bar(runtime, robot.params_json)
        await runtime.flush_state()

    async def stop_all(self) -> None:
        for robot_id in list(self._tasks):
            await self.stop_robot(robot_id)


def _row_to_robot(row) -> Any:
    from trader.lab.models import Robot
    import json
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
