import asyncio
import json
import types
from datetime import datetime, time, timezone, timedelta
from typing import Any

import structlog

log = structlog.get_logger()

# Paper robots are light (ISS fetch shared per symbol via the bar cache + a signal
# compute per minute), so many can run in parallel. Default 50 so a full showcase
# of paper robots all tick at once. Override with LAB_MAX_ROBOTS.
import os as _os
_MAX_ACTIVE_ROBOTS = int(_os.environ.get("LAB_MAX_ROBOTS", "50"))
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
        self._robot_states: dict[str, dict] = {}  # in-memory robot state across ticks
        # Compiled module cache: robot.id -> (script_hash, module). Validating +
        # compiling the script every minute tick is wasted work; the code never
        # changes between ticks. Re-compiles only when the script text changes.
        self._compiled: dict[str, tuple[int, types.ModuleType]] = {}
        # Время последнего бара, на котором робот УЖЕ отработал. Пусто = робота
        # ещё не видели в этой жизни процесса (см. _bar_gate).
        self._last_bar: dict[str, int] = {}

    def _bar_gate(self, robot_id: str, newest: int | None) -> bool:
        """Отработан ли уже этот бар. Правило одно: торгуем только на НОВОМ баре.

        Планировщик тикает по настенным часам раз в минуту, а бары приходят с
        биржи. Когда торгов нет, get_bars отдаёт последний доступный бар, и без
        этой проверки стратегия пересчитывалась на нём снова и снова и могла
        выставить заявку: 02.08.2026 бумажный «2EMA · MXU6» продал в воскресенье
        19:23 МСК по бару сорокачасовой давности — биржа в те выходные MXU6 не
        торговала вовсе.

        Судим ПО ДАННЫМ, а не по календарю: FORTS торгует и в выходные (25-26.07
        по MXU6 было 475 и 533 бара, те сделки законны), поэтому «суббота» ничего
        не значит, а «нового бара нет» значит всё. Заодно правило закрывает обрыв
        котировок среди буднего дня и повтор бара после рестарта.

        Нет бара (ISS молчит) — тоже не исполняем: без данных робот не торгует,
        следующая минута попробует снова.

        ПЕРВАЯ встреча робота бар только ЗАПОМИНАЕТ. Иначе рестарт STL в выходной
        сразу отработал бы по пятничному бару — ровно тот случай, который чиним.
        """
        if newest is None:
            return False
        seen = self._last_bar.get(robot_id)
        self._last_bar[robot_id] = newest
        if seen is None:
            log.info("lab.scheduler.bar_seeded", robot_id=robot_id, bar=newest)
            return False
        return newest > seen

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
        # Seed in-memory state from DB so the robot remembers its trend across ticks.
        s = robot.state_json if isinstance(robot.state_json, dict) else {}
        self._robot_states[robot.id] = s
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
        # Снятый робот забывается ЦЕЛИКОМ. Иначе повторный деплой поднимал робота
        # с чужой памятью прошлой жизни: состояние стратегии не перечитывалось из
        # базы (сброс робота «на старт» не срабатывал), а _last_bar считал его уже
        # виденным — то есть защита «первый тик только запоминает бар» на редеплое
        # не работала.
        self._robot_states.pop(robot_id, None)
        self._compiled.pop(robot_id, None)
        self._last_bar.pop(robot_id, None)
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
        from trader.lab.script_guard import validate_script
        # Follow the front contract across expiry so the robot never freezes on a
        # dead contract (paper robots only; real robots are rolled by a human).
        try:
            await self._maybe_roll(robot)
        except Exception as exc:
            log.warning("lab.roll_failed", robot_id=robot.id, error=str(exc))
        # Validate + compile once per script version, reuse the module across ticks.
        script_hash = hash(robot.script_code)
        cached = self._compiled.get(robot.id)
        if cached is None or cached[0] != script_hash:
            validate_script(robot.script_code)
            mod = types.ModuleType("robot_script")
            exec(compile(robot.script_code, f"<robot:{robot.id}>", "exec"), mod.__dict__)
            self._compiled[robot.id] = (script_hash, mod)
        else:
            mod = cached[1]
        state = robot.state_json if isinstance(robot.state_json, dict) else {}
        # Restore previous in-memory state so the robot remembers its trend/position
        # across ticks. Without this, every tick starts with amnesia → repeated entries.
        prev_state = self._robot_states.get(robot.id, state)
        paper = not bool(state.get("live_real", False))
        runtime = LiveRuntime(
            robot_id=robot.id, pool=self._pool,
            tx_client=self._tx_client, pos_client=self._pos_client,
            paper=paper, initial_state=prev_state,
            # Бар, на котором робот уже торговал: рантайм не даст выставить заявку,
            # пока не придёт более свежий (см. LiveRuntime._bar_is_stale).
            acted_bar=self._last_bar.get(robot.id),
        )
        if hasattr(mod, "on_bar"):
            await mod.on_bar(runtime, robot.params_json)
        if runtime.newest_bar is not None:
            self._last_bar[robot.id] = runtime.newest_bar
        await runtime.flush_state()
        # Update in-memory state so the next tick sees the fresh trend/position state.
        self._robot_states[robot.id] = runtime._state

    async def _maybe_roll(self, robot) -> None:
        """Roll a PAPER robot to the current front contract when its specific
        contract is no longer the front (e.g. after expiry). Flat + fresh state.
        Real robots are never auto-rolled (arming real money is human-initiated)."""
        from trader.lab.contract_roll import front_contract, base_of
        params = robot.params_json if isinstance(robot.params_json, dict) else {}
        symbol = params.get("symbol")
        if not symbol or base_of(symbol) is None:
            return  # base-code symbol (already rolls via continuous bars) or missing
        state = robot.state_json if isinstance(robot.state_json, dict) else {}
        if bool(state.get("live_real", False)):
            return  # never auto-roll a REAL robot
        front = await front_contract(symbol)
        if not front or front == symbol:
            return
        # ROLL: new symbol, flat, fresh strategy state (preserve only live_real).
        new_params = {**params, "symbol": front}
        new_state = {"live_real": bool(state.get("live_real", False))}
        robot.params_json = new_params
        robot.state_json = new_state
        self._robot_states[robot.id] = dict(new_state)
        if self._pool is not None:
            async with self._pool.acquire() as conn:
                # ОБЪЕКТЫ, не json.dumps: пул держит jsonb-кодек и сериализует сам.
                # Каждый ролл с dumps добавлял ЛИШНИЙ слой кодировки, и после
                # второго ролла _row_to_robot разворачивал только один — стратегия
                # получала СТРОКУ вместо параметров и падала каждую минуту, а
                # _maybe_roll видел {} и больше не роллил. Так восемь бумажных
                # роботов молча встали на истёкших контрактах (02.08.2026).
                await conn.execute(
                    "UPDATE robots SET params_json=$1, state_json=$2 WHERE id=$3",
                    new_params, new_state, robot.id,
                )
        log.info("lab.roll", robot_id=robot.id, old=symbol, new=front)

    async def stop_all(self) -> None:
        for robot_id in list(self._tasks):
            await self.stop_robot(robot_id)


def _unwrap_json(v, limit: int = 4) -> dict:
    """Развернуть значение до dict, сколько бы слоёв кодировки на нём ни было.

    Один json.loads не спасал: перезапись параметров через json.dumps на пуле с
    jsonb-кодеком добавляла слой на КАЖДОМ ролле, и у роботов, роллившихся дважды,
    после одного loads оставалась строка. Она уходила в стратегию вместо словаря
    (params.get -> AttributeError каждую минуту). Записывающая сторона починена,
    но старые строки надо уметь прочитать."""
    for _ in range(limit):
        if isinstance(v, dict):
            return v
        if not isinstance(v, str):
            return {}
        try:
            v = json.loads(v)
        except (TypeError, ValueError):
            return {}
    return v if isinstance(v, dict) else {}


def _row_to_robot(row) -> Any:
    from trader.lab.models import Robot
    return Robot(
        id=row["id"],
        user_email=row["user_email"],
        stl_link_id=row["stl_link_id"],
        name=row["name"],
        script_code=row["script_code"],
        params_json=_unwrap_json(row["params_json"]),
        state_json=_unwrap_json(row["state_json"]),
        schedule=row["schedule"],
        deployed=row["deployed"],
    )
