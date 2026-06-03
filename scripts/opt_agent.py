#!/usr/bin/env python3
"""
Optimization AGENT — runs on a powerful host (e.g. the Windows i9 / 128GB box) and
offloads parameter sweeps from the small shared VDS.

Pull model (no inbound ports needed on this host):
  loop:
    POST /api/v1/agent/claim   -> get next queued remote run (or 204 = idle)
    fetch bars from MOEX ISS (free), expand the param grid
    run all combos across a ProcessPoolExecutor (all CPU cores)
    POST /api/v1/agent/result  -> write results, mark run done

Auth: header X-Agent-Token must match the server's OPT_AGENT_TOKEN.

Run:
  set STL_API=https://stl.shectory.ru
  set OPT_AGENT_TOKEN=<same secret as server>
  poetry run python scripts/opt_agent.py            # default: cores-2 workers
  poetry run python scripts/opt_agent.py --workers 18 --poll 5
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import os
import socket
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402


# ── worker (separate process) ─────────────────────────────────────────────────
def _run_chunk(args: tuple) -> list[dict]:
    """Run a chunk of param-sets in a worker process. Self-contained: rebuilds the
    strategy module from script_code, runs each combo, returns serializable rows."""
    import asyncio as _asyncio
    import types as _types
    from trader.lab.backtest import run_single_backtest, _demote_to_background
    from trader.lab.runtime import Bar

    script_code, bars_data, symbol, param_sets, point_value = args
    _demote_to_background()  # be a polite background citizen on the shared host too
    bars = [Bar(**b) for b in bars_data]
    mod = _types.ModuleType("robot_script")
    exec(compile(script_code, "<robot>", "exec"), mod.__dict__)

    async def _all():
        out = []
        for ps in param_sets:
            try:
                r = await run_single_backtest(mod, bars, symbol, ps, point_value=point_value)
                out.append({"ok": True, "params": ps, "result": r})
            except Exception as exc:  # noqa: BLE001
                out.append({"ok": False, "params": ps, "error": str(exc)})
        return out

    return _asyncio.run(_all())


def _chunked(seq: list, n: int) -> list[list]:
    if n <= 0:
        return [seq]
    size = max(1, (len(seq) + n - 1) // n)
    return [seq[i:i + size] for i in range(0, len(seq), size)]


# ── agent ─────────────────────────────────────────────────────────────────────
class Agent:
    def __init__(self, api: str, token: str, workers: int, poll: float):
        self.api = api.rstrip("/")
        self.token = token
        self.workers = workers
        self.poll = poll
        self.agent_id = f"{socket.gethostname()}:{os.getpid()}"
        self.h = {"X-Agent-Token": token, "Content-Type": "application/json"}

    async def claim(self, client: httpx.AsyncClient):
        r = await client.post(f"{self.api}/api/v1/agent/claim",
                              json={"agent_id": self.agent_id}, headers=self.h, timeout=30)
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    async def post_result(self, client: httpx.AsyncClient, payload: dict):
        r = await client.post(f"{self.api}/api/v1/agent/result",
                              json=payload, headers=self.h, timeout=120)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _parse_date(s: str) -> date:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()

    def _expand(self, base_params: dict, grid: dict) -> list[dict]:
        keys = list(grid.keys())
        values = [grid[k] if isinstance(grid[k], list) else [grid[k]] for k in keys]
        combos = list(itertools.product(*values))
        return [{**base_params, **dict(zip(keys, c))} for c in combos]

    async def process(self, client: httpx.AsyncClient, job: dict, pool: ProcessPoolExecutor):
        from trader.lab.iss_loader import load_bars_iss

        run_id = job["run_id"]
        symbol = job["symbol"]
        try:
            d_from = self._parse_date(job["date_from"])
            d_to = self._parse_date(job["date_to"])
            bars = await load_bars_iss(symbol, d_from, d_to, interval=1)
            if not bars:
                await self.post_result(client, {"run_id": run_id, "error": f"no bars for {symbol}"})
                return
            bars_data = [{"time": b.time, "open": b.open, "high": b.high,
                          "low": b.low, "close": b.close, "volume": b.volume} for b in bars]

            param_sets = self._expand(job["base_params"], job["params_grid"])
            print(f"[{run_id}] {symbol} {len(bars)} bars × {len(param_sets)} combos "
                  f"on {self.workers} workers", flush=True)

            chunks = _chunked(param_sets, self.workers)
            args = [(job["script_code"], bars_data, symbol, ch, job["point_value"]) for ch in chunks]
            loop = asyncio.get_event_loop()
            t0 = time.time()
            futs = [loop.run_in_executor(pool, _run_chunk, a) for a in args]
            chunk_results = await asyncio.gather(*futs)
            results = [r for chunk in chunk_results for r in chunk]
            dt = time.time() - t0
            ok = sum(1 for r in results if r.get("ok"))
            print(f"[{run_id}] done {ok}/{len(results)} in {dt:.1f}s "
                  f"({len(results)/dt:.0f} combos/s)", flush=True)
            await self.post_result(client, {"run_id": run_id, "results": results})
        except Exception as exc:  # noqa: BLE001
            print(f"[{run_id}] FAILED: {exc}", flush=True)
            try:
                await self.post_result(client, {"run_id": run_id, "error": str(exc)})
            except Exception:
                pass

    async def run(self):
        print(f"agent {self.agent_id} → {self.api}  workers={self.workers}  poll={self.poll}s",
              flush=True)
        with ProcessPoolExecutor(max_workers=self.workers) as pool:
            async with httpx.AsyncClient() as client:
                idle_note = True
                while True:
                    try:
                        job = await self.claim(client)
                    except Exception as exc:  # noqa: BLE001
                        print(f"claim error: {exc}", flush=True)
                        await asyncio.sleep(self.poll)
                        continue
                    if job is None:
                        if idle_note:
                            print("idle… waiting for jobs", flush=True)
                            idle_note = False
                        await asyncio.sleep(self.poll)
                        continue
                    idle_note = True
                    await self.process(client, job, pool)


def main():
    ap = argparse.ArgumentParser(description="Shectory LAB optimization agent")
    ap.add_argument("--api", default=os.environ.get("STL_API", "https://stl.shectory.ru"))
    ap.add_argument("--token", default=os.environ.get("OPT_AGENT_TOKEN", ""))
    ap.add_argument("--workers", type=int,
                    default=int(os.environ.get("OPT_AGENT_WORKERS", "0")) or max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--poll", type=float, default=float(os.environ.get("OPT_AGENT_POLL", "5")))
    args = ap.parse_args()
    if not args.token:
        print("ERROR: set OPT_AGENT_TOKEN (env) or --token", file=sys.stderr)
        sys.exit(2)
    asyncio.run(Agent(args.api, args.token, args.workers, args.poll).run())


if __name__ == "__main__":
    main()
