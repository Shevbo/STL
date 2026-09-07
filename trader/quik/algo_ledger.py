"""Algo-trade ledger: the authoritative per-fill accounting journal for robots.

Operator's spec (2026-07-22): полный учёт алготорговли — порядковый номер, id
робота, дата-время, id сделки, инструмент, кол-во, направление, лимит/по рынку,
финрез gross, финрез net (за вычетом комиссии). Отчёты и графики строятся по
этой таблице. Unlike an account-equity delta this excludes manual terminal
trading and deposits/withdrawals by construction.

Sources (both already mirrored in-process, no extra agent traffic):
  - REAL robots: tagged QUIK trades from the agent-local-status snapshot
    (``quik.trades``) — the exchange FACT, carries the QUIK trade_num. Runner
    fills for real robots are deliberately NOT ingested (double counting).
  - PAPER robots: runner ``recent_fills`` with status "paper" from the robots
    mirror (no QUIK trade exists for paper).

P&L attribution replays fills per robot with the SAME signed-space avg-cost
semantics as robot_runner/runtime.py (partial reduce keeps the avg). Gross is
points x point_value; commission via trader.lab.commission (taker: real orders
go marketable, paper follows the backtest taker convention).

Seeding: a robot first seen with an already-open position gets its current
mirror position/avg recorded in ``algo_ledger_state``; only fills NEWER than
the seed moment are ingested (older history is already inside the seeded
position and cannot be attributed fill-by-fill).

Known gap (documented, rare): record-fill-agent manual records for a REAL robot
carry no tagged QUIK trade and are not ingested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from trader.lab.commission import commission_for

_MSK_OFFSET_MS = 3 * 3600 * 1000

DDL = [
    """CREATE TABLE IF NOT EXISTS algo_trades (
        seq            BIGSERIAL PRIMARY KEY,
        robot_id       TEXT NOT NULL,
        mode           TEXT NOT NULL,
        ts_ms          BIGINT NOT NULL,
        trade_num      TEXT,
        order_num      TEXT,
        symbol         TEXT NOT NULL,
        side           TEXT NOT NULL,
        qty            BIGINT NOT NULL,
        price          DOUBLE PRECISION NOT NULL,
        order_kind     TEXT NOT NULL,
        point_value    DOUBLE PRECISION NOT NULL,
        pnl_gross_rub  DOUBLE PRECISION NOT NULL,
        commission_rub DOUBLE PRECISION NOT NULL,
        pnl_net_rub    DOUBLE PRECISION NOT NULL,
        pos_after      BIGINT NOT NULL,
        avg_after      DOUBLE PRECISION NOT NULL,
        dedup_key      TEXT NOT NULL UNIQUE
    )""",
    "CREATE INDEX IF NOT EXISTS algo_trades_robot_ts ON algo_trades (robot_id, ts_ms)",
    "CREATE INDEX IF NOT EXISTS algo_trades_ts ON algo_trades (ts_ms)",
    """CREATE TABLE IF NOT EXISTS algo_ledger_state (
        robot_id     TEXT PRIMARY KEY,
        position     BIGINT NOT NULL,
        avg_price    DOUBLE PRECISION NOT NULL,
        seeded_at_ms BIGINT NOT NULL
    )""",
    # Взвешенное время входа открытой позиции — опора для скальперской скидки на
    # закрывающем филле (07.09.2026, см. price_row). ADD COLUMN IF NOT EXISTS, а не
    # только CREATE TABLE: на уже существующей таблице второе не добавит колонку.
    "ALTER TABLE algo_ledger_state ADD COLUMN IF NOT EXISTS entry_ts_ms BIGINT NOT NULL DEFAULT 0",
]


def apply_fill(pos: int, avg: float, entry_ts_ms: int, delta: int, price: float,
              ts_ms: int) -> tuple[int, float, int, float]:
    """Signed-space avg-cost replay of ONE fill. delta is +qty (buy) / -qty (sell).
    Returns (new_pos, new_avg, new_entry_ts_ms, realized_points). Mirrors
    robot_runner/runtime.py: extending averages in, a partial reduce KEEPS the avg,
    a flip realizes the whole old position and opens the remainder at the fill price.

    entry_ts_ms — ВЗВЕШЕННОЕ время входа открытой позиции, тот же приём, каким уже
    взвешивается avg: он копит доли ПО ОБЪЁМУ у каждого добора. Опора для
    скальперской скидки в price_row (07.09.2026) — без него закрывающий филл не
    знает, был ли вход в том же торговом дне."""
    new = pos + delta
    if pos == 0 or (pos > 0) == (delta > 0):  # open / extend
        tot = abs(pos) + abs(delta)
        avg = (avg * abs(pos) + price * abs(delta)) / tot
        entry_ts_ms = int((entry_ts_ms * abs(pos) + ts_ms * abs(delta)) / tot)
        return new, avg, entry_ts_ms, 0.0
    sign = 1 if pos > 0 else -1
    if abs(delta) <= abs(pos):  # reduce (partial keeps avg + entry_ts) or full close
        realized = (price - avg) * abs(delta) * sign
        return (new, (0.0 if new == 0 else avg),
                (0 if new == 0 else entry_ts_ms), realized)
    realized = (price - avg) * abs(pos) * sign  # flip: close all, remainder opens here
    return new, price, ts_ms, realized


@dataclass
class RawFill:
    robot_id: str
    mode: str  # "real" | "paper"
    ts_ms: int
    trade_num: str | None
    order_num: str | None
    symbol: str
    side: str  # "buy" | "sell"
    qty: int
    price: float
    dedup_key: str


def point_values(params: dict | None) -> dict[str, float]:
    """symbol -> ₽/point from the QLua params feed ({rows:[{code, price_step,
    step_cost, coef}]}). coef is the agent-precomputed step_cost/price_step."""
    out: dict[str, float] = {}
    for r in (params or {}).get("rows", []):
        coef = float(r.get("coef") or 0)
        if coef <= 0:
            step, cost = float(r.get("price_step") or 0), float(r.get("step_cost") or 0)
            coef = cost / step if step > 0 else 0.0
        if coef > 0:
            out[r.get("code", "")] = coef
    return out


def _robot_modes(mirror: dict | None) -> dict[str, str]:
    out = {}
    for r in (mirror or {}).get("robots", []):
        rid = r.get("robot_id")
        if rid:
            out[rid] = "paper" if r.get("paper") else "real"
    return out


def collect_fills(mirror: dict | None, status: dict | None) -> list[RawFill]:
    """Normalize both sources into RawFills, oldest first. Mirror int64s arrive as
    strings (protobuf MessageToDict); QUIK trades arrive typed from the status JSON."""
    modes = _robot_modes(mirror)
    fills: list[RawFill] = []

    # REAL: tagged QUIK trades (exchange fact). Tag is the robot id truncated to
    # QUIK's 20-char brokerref (see agent recon.quikTag) — match by that prefix.
    by_tag = {rid[:20]: rid for rid, m in modes.items() if m == "real"}
    for t in ((status or {}).get("quik") or {}).get("trades", []):
        rid = by_tag.get(t.get("tag") or "")
        if rid is None:
            continue  # untagged (manual), "recon", or unknown robot
        side = t.get("side") or ""
        if side not in ("buy", "sell"):
            continue  # old Lua build without side — cannot attribute direction
        num = str(t.get("num") or "")
        if not num:
            continue
        fills.append(RawFill(
            robot_id=rid, mode="real", ts_ms=int(t.get("ts_ms") or 0),
            trade_num=num, order_num=str(t.get("order_num") or "") or None,
            symbol=t.get("sec") or "", side=side,
            qty=int(t.get("qty") or 0), price=float(t.get("price") or 0),
            dedup_key=f"q:{num}"))

    # PAPER: runner fills with status "paper". Real-robot runner fills are skipped
    # (the QUIK fact above covers them; ingesting both would double-count).
    for r in (mirror or {}).get("robots", []):
        rid = r.get("robot_id")
        if not rid or modes.get(rid) != "paper":
            continue
        for f in r.get("recent_fills", []):
            if (f.get("status") or "") != "paper":
                continue
            side_pb = (f.get("side") or "").upper()
            side = "buy" if side_pb.endswith("BUY") else "sell" if side_pb.endswith("SELL") else ""
            if not side:
                continue
            ts = int(f.get("ts_unix_ms") or 0)
            qty = int(f.get("qty") or 0)
            price = float(f.get("price") or 0)
            oid = str(f.get("order_id") or "")
            fills.append(RawFill(
                robot_id=rid, mode="paper", ts_ms=ts, trade_num=None,
                order_num=oid or None, symbol=f.get("symbol") or r.get("symbol") or "",
                side=side, qty=qty, price=price,
                dedup_key=f"p:{rid}:{oid}:{ts}:{side}:{qty}:{price:g}"))

    fills = [f for f in fills if f.qty > 0 and f.price > 0 and f.symbol]
    fills.sort(key=lambda f: (f.ts_ms, f.dedup_key))
    return fills


def backfill_real_tail(rid: str, fallback_symbol: str, recent_fills: list[dict],
                       seeded_at_ms: int, seed_pos: int, pv: float) -> list[dict] | None:
    """Rows for a REAL robot's PRE-SEED history, replayed from flat through the
    runner's persisted fills tail. The runner resets the tail on the paper->real
    arming flip, so for a robot with under 200 real fills the tail IS its complete
    real-money history. Replayed from pos=0 it must land EXACTLY on the position
    the ledger seeded with — otherwise the tail is incomplete (200-cut or a
    missed fill) and per-fill history cannot be trusted: return None, backfill
    nothing. Post-seed fills are excluded (already ledgered from the QUIK fact)."""
    fills: list[RawFill] = []
    for f in recent_fills:
        if (f.get("status") or "") != "filled":
            continue
        ts = int(f.get("ts_unix_ms") or 0)
        if ts <= 0 or ts > seeded_at_ms:
            continue
        side_pb = (f.get("side") or "").upper()
        side = "buy" if side_pb.endswith("BUY") else "sell" if side_pb.endswith("SELL") else ""
        qty, price = int(f.get("qty") or 0), float(f.get("price") or 0)
        if not side or qty <= 0 or price <= 0:
            continue
        oid = str(f.get("order_id") or "")
        fills.append(RawFill(
            robot_id=rid, mode="real", ts_ms=ts, trade_num=None,
            order_num=oid or None, symbol=f.get("symbol") or fallback_symbol,
            side=side, qty=qty, price=price,
            dedup_key=f"bf:{rid}:{oid}:{ts}:{side}:{qty}:{price:g}"))
    fills.sort(key=lambda f: (f.ts_ms, f.dedup_key))
    pos, avg, entry_ts = 0, 0.0, 0
    rows: list[dict] = []
    for f in fills:
        row = price_row(f, pos, avg, entry_ts, pv)
        pos, avg, entry_ts = row["pos_after"], row["avg_after"], row["entry_ts_after"]
        rows.append(row)
    if pos != seed_pos:
        return None
    return rows


_LOG_FILL = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[FILL\] (buy|sell) (\d+) @ ([\d.]+)"
    r"(?: \(([^)]*)\))? .*? позиция (-?\d+) @ ([\d.]+), реализовано (-?[\d.]+) п\.")
_LOG_ARM = re.compile(r"\[LIFECYCLE\] АРМИНГ paper->РЕАЛ")


def parse_runner_log(text: str) -> list[dict]:
    """REAL-money fills from a per-robot runner log (docs/logs.zip).

    The runner logs every fill as
        `TS [FILL] side qty @ price [(status)] -> позиция POS @ AVG, реализовано R п.`
    A PAPER fill carries a "(paper)" status; a REAL fill has no parenthetical at all —
    that is the only marker distinguishing them. `реализовано` is the runner's own
    CUMULATIVE realized in POINTS, so each fill's own P&L is the delta from the
    previous line; the position/avg after the fill are taken verbatim rather than
    recomputed (the runner's arithmetic is the authority).

    An `АРМИНГ paper->РЕАЛ` line resets realized to 0 AND discards the prior real
    history (the runner does exactly this), so anything before the LAST arming is
    dropped. When a log starts mid-history (no arming line, realized already
    non-zero), that opening amount lands on the first logged fill — the running
    TOTAL stays exact, only that one fill's share is overstated.
    """
    out: list[dict] = []
    prev_realized = 0.0
    for line in text.splitlines():
        if _LOG_ARM.search(line):
            out.clear()
            prev_realized = 0.0
            continue
        m = _LOG_FILL.match(line)
        if not m:
            continue
        ts, side, qty, price, status, pos, avg, realized = m.groups()
        if status:                       # "(paper)" and friends -> not real money
            continue
        realized = float(realized)
        out.append({
            "ts": ts, "side": side, "qty": int(qty), "price": float(price),
            "pos_after": int(pos), "avg_after": float(avg),
            "realized_cum": realized, "gross_points": realized - prev_realized,
        })
        prev_realized = realized
    return out


def price_row(f: RawFill, pos: int, avg: float, entry_ts_ms: int,
             pv: float) -> dict[str, Any]:
    """Apply one fill to (pos, avg) and price its P&L. Returns the DB row dict
    plus the advanced state under 'pos_after'/'avg_after'/'entry_ts_after'.

    ИЗДЕРЖКИ (07.09.2026). До этой правки каждый филл стоил ПОЛНУЮ тейкерскую ставку
    независимо от дня и от того, закрылся ли круг внутри сессии — оператор заметил
    сам: «комиссия сегодня осталась высокой как на выходных», хотя день был будний.
    Причина: trader.lab.commission получил скальперскую скидку и удвоение выходного
    06.09, но ЭТОТ вызывающий (реальный журнал алготорговли) не передавал ни
    scalper=, ни ts= — оставался на плоской модели молча, день за днём, будни и
    выходные одинаково. Отсюда и наблюдение: «сегодня как на выходных» было буквально
    точным описанием бага, а не совпадением.

    СКИДКА ЗАДНИМ ЧИСЛОМ НЕВОЗМОЖНА. Журнал пишется в реальном времени: комиссия
    ВХОДНОГО филла фиксируется ДО того, как станет известно, закроется ли круг в тот
    же день. Поэтому скидка применяется только к ЗАКРЫВАЮЩЕМУ филлу и только на его
    СОБСТВЕННОЙ комиссии — настоящая биржевая скидка касается обеих ног круга, здесь
    экономия занижена, но никогда не переоценена. Той же логике в trader/lab/backtest.py
    ретроспектива доступна (бэктест видит всю историю сразу), поэтому там скидка
    честно делится на обе ноги — расхождение между бэктестом и этим журналом отсюда,
    а не из ошибки.

    МСК-СМЕЩЕНИЕ ОБЯЗАТЕЛЬНО. f.ts_ms — ИСТИННЫЙ UTC с агента, а не барная стенка
    бэктеста (см. «Strategy time semantics» в CLAUDE.md); is_weekend() документирует
    именно это требование к вызывающему.
    """
    delta = f.qty if f.side == "buy" else -f.qty
    is_close = pos != 0 and (pos > 0) != (delta > 0)      # закрывающая (или частичная)
    new_pos, new_avg, new_entry_ts, realized_pts = apply_fill(
        pos, avg, entry_ts_ms, delta, f.price, f.ts_ms)
    gross = realized_pts * pv
    msk_ts = (f.ts_ms + _MSK_OFFSET_MS) / 1000.0
    scalper = is_close and msk_date(entry_ts_ms) == msk_date(f.ts_ms)
    # Real robot orders go MARKETABLE (taker); paper follows the backtest taker
    # convention. Both therefore price the MOEX fee + broker fee.
    comm = commission_for(f.symbol, f.price, f.qty, pv, taker=True,
                          scalper=scalper, ts=msk_ts)
    return {
        "robot_id": f.robot_id, "mode": f.mode, "ts_ms": f.ts_ms,
        "trade_num": f.trade_num, "order_num": f.order_num, "symbol": f.symbol,
        "side": f.side, "qty": f.qty, "price": f.price,
        "order_kind": "market" if f.mode == "real" else "limit",
        "point_value": pv, "pnl_gross_rub": round(gross, 2),
        "commission_rub": round(comm, 2), "pnl_net_rub": round(gross - comm, 2),
        "pos_after": new_pos, "avg_after": new_avg, "entry_ts_after": new_entry_ts,
        "dedup_key": f.dedup_key,
    }


def msk_date(ts_ms: int) -> str:
    """Epoch-ms -> MSK calendar date 'YYYY-MM-DD' (fixed +3h, MSK has no DST)."""
    import datetime
    return datetime.datetime.fromtimestamp(
        (ts_ms + _MSK_OFFSET_MS) / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")


def drop_already_ledgered(existing: list[dict], rows: list[dict],
                          tol_ms: int = 2000) -> list[dict]:
    """Filter out rows the ledger ALREADY holds under a different dedup_key.

    The two backfills key rows differently (`lg:` replays the runner's text log,
    `bf:` replays the status tail, `q:` is the live QUIK ingest), so the UNIQUE
    dedup_key cannot see that they describe the SAME fill. On 2026-07-24 that put
    one boundary fill in twice for every real robot: the chart replay then read
    the second copy as an add-on and every later fill's role was wrong (an OPEN
    drawn as AVR) while the replayed position drifted by that quantity.

    Match = same side+qty, timestamps within tol_ms, price equal to 0.01%. Each
    existing row is CONSUMED by at most one candidate, so two genuinely separate
    identical fills in the same second still both survive.
    """
    pool_ = [dict(e) for e in existing]
    used = [False] * len(pool_)
    out = []
    for r in rows:
        hit = -1
        for i, e in enumerate(pool_):
            if used[i] or e.get("side") != r.get("side") or int(e.get("qty", 0)) != int(r.get("qty", 0)):
                continue
            if abs(int(e.get("ts_ms", 0)) - int(r.get("ts_ms", 0))) > tol_ms:
                continue
            ep, rp = float(e.get("price", 0)), float(r.get("price", 0))
            if abs(ep - rp) > max(1e-9, abs(rp) * 1e-4):
                continue
            hit = i
            break
        if hit >= 0:
            used[hit] = True
            continue
        out.append(r)
    return out


# ---- DB layer ----

async def ensure_tables(pool) -> None:
    async with pool.acquire() as conn:
        for stmt in DDL:
            await conn.execute(stmt)


async def ingest_once(pool, store, now_ms: int) -> int:
    """One ingest pass: seed unseen robots, insert new fills, advance state.
    Returns the number of newly inserted ledger rows."""
    mirror = store.robot_report(None)
    status = store.agent_status(None)
    pv_map = point_values(store.params(None))
    if not mirror:
        return 0
    fills = collect_fills(mirror, status)

    inserted = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            states = {r["robot_id"]: dict(r) for r in await conn.fetch(
                "SELECT robot_id, position, avg_price, seeded_at_ms, entry_ts_ms "
                "FROM algo_ledger_state")}
            # Seed robots we see for the first time with their CURRENT book: the
            # open position predates the ledger and cannot be attributed per-fill.
            # The seed moment is the MIRROR's receipt time, not now: a fill landing
            # in the report gap is not yet inside the seeded position and must
            # still be ingested (ts > seed) rather than silently skipped.
            #
            # entry_ts_ms СЕЕТСЯ ТЕМ ЖЕ seed_ms: подлинное время входа сеянной
            # позиции неизвестно (она открылась ДО первого запуска журнала), а
            # seed_ms — консервативная оценка. Если позиция закроется в тот же
            # день — скидка применится, возможно ошибочно; если позже — нет,
            # и это чаще правда. Ошибка живёт максимум один раз на робота.
            seed_ms = int(mirror.get("received_at_ms") or now_ms)
            for r in mirror.get("robots", []):
                rid = r.get("robot_id")
                if not rid or rid in states:
                    continue
                st = {"robot_id": rid, "position": int(r.get("position") or 0),
                      "avg_price": float(r.get("avg_price") or 0),
                      "seeded_at_ms": seed_ms, "entry_ts_ms": seed_ms}
                await conn.execute(
                    "INSERT INTO algo_ledger_state(robot_id,position,avg_price,"
                    "seeded_at_ms,entry_ts_ms) VALUES($1,$2,$3,$4,$5) "
                    "ON CONFLICT (robot_id) DO NOTHING",
                    rid, st["position"], st["avg_price"], st["seeded_at_ms"],
                    st["entry_ts_ms"])
                states[rid] = st

            for f in fills:
                st = states.get(f.robot_id)
                if st is None or f.ts_ms <= st["seeded_at_ms"]:
                    continue  # pre-seed history lives inside the seeded position
                pv = pv_map.get(f.symbol)
                if not pv:
                    continue  # params not seen yet (60s cadence) — retry next pass
                row = price_row(f, int(st["position"]), float(st["avg_price"]),
                                int(st.get("entry_ts_ms") or st["seeded_at_ms"]), pv)
                got = await conn.fetchrow(
                    """INSERT INTO algo_trades (robot_id,mode,ts_ms,trade_num,order_num,
                         symbol,side,qty,price,order_kind,point_value,pnl_gross_rub,
                         commission_rub,pnl_net_rub,pos_after,avg_after,dedup_key)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                       ON CONFLICT (dedup_key) DO NOTHING RETURNING seq""",
                    row["robot_id"], row["mode"], row["ts_ms"], row["trade_num"],
                    row["order_num"], row["symbol"], row["side"], row["qty"],
                    row["price"], row["order_kind"], row["point_value"],
                    row["pnl_gross_rub"], row["commission_rub"], row["pnl_net_rub"],
                    row["pos_after"], row["avg_after"], row["dedup_key"])
                if got is None:
                    continue  # already ledgered
                inserted += 1
                st["position"], st["avg_price"], st["entry_ts_ms"] = (
                    row["pos_after"], row["avg_after"], row["entry_ts_after"])
                await conn.execute(
                    "UPDATE algo_ledger_state SET position=$2, avg_price=$3, "
                    "entry_ts_ms=$4 WHERE robot_id=$1",
                    f.robot_id, row["pos_after"], row["avg_after"],
                    row["entry_ts_after"])
    return inserted
