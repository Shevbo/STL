"""
Queue a full sweep campaign on continuous FORTS symbols for all library strategies.

Fetches strategy list from the API, builds param grids (same formula as the
Optimizer UI: step = max(1, round((max-min)/4))), and submits remote sweep jobs
for each strategy x symbol pair.

Usage:
    python scripts/queue_campaign.py [options]

Options:
    --dry-run           Print what would be queued without submitting
    --symbols RI,BR,Si  Override the default symbol list (comma-separated)
    --strategies cci,roc  Only queue specific strategy IDs (comma-separated)
    --date-from YYYY-MM-DD  Default: 2024-01-01
    --date-to   YYYY-MM-DD  Default: 2026-06-13
    --include-avg-params    Also sweep avg/tp params (huge combo explosion, off by default)
    --api URL               Override API base URL

Auth: OPT_AGENT_TOKEN env var (or Windows registry HKCU\\Environment\\OPT_AGENT_TOKEN).
API:  STL_API env var, default https://stl.shectory.ru
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time

import httpx

# ── continuous FORTS base codes (no expiry suffix) ───────────────────────────
DEFAULT_SYMBOLS = [
    "RI", "BR", "Si", "GZ", "GD", "MX", "NG", "SF",
    "PD", "BT", "NA", "BM", "MM", "CC", "CR", "ED", "Eu", "SR", "SS", "SV",
]

# Params managed by the averaging/TP layer — excluded from sweep by default to
# keep combo counts manageable. Pinned at their off-defaults instead.
AVG_PARAM_KEYS = {"avg_max", "avg_step_atr", "tp_atr", "avg_atr_n"}

# ── helpers ──────────────────────────────────────────────────────────────────

def _token() -> str:
    tok = os.environ.get("OPT_AGENT_TOKEN", "")
    if not tok:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                tok, _ = winreg.QueryValueEx(k, "OPT_AGENT_TOKEN")
        except Exception:
            pass
    if not tok:
        sys.exit("OPT_AGENT_TOKEN not found in env or registry")
    return tok


MAX_COMBOS = 5000  # upper bound per job; coarsen grid if exceeded


def _expand(lo: float, hi: float, step: float) -> list[float]:
    """Generate values from lo to hi inclusive, stepping by step."""
    vals: list[float] = []
    v = float(lo)
    while v <= hi + 1e-9:
        vals.append(round(v, 6))
        v += step
    return vals


def _build_grid(schema: list[dict], include_avg: bool,
                pins: dict[str, float] | None = None) -> dict[str, list]:
    """Build paramsGrid capped at MAX_COMBOS per job.

    Start with ~10 values per param (step=(max-min)/9). If the cartesian
    product exceeds MAX_COMBOS, double the step uniformly and retry.
    Pinned params (--pin key=value) are fixed to one value and freed from
    the grid — e.g. qty only scales P&L linearly; sweeping it wastes an
    axis the coarsener then steals from the signal-shaping params.
    """
    params: list[tuple[str, float, float]] = []
    fixed: dict[str, list] = {}
    pins = pins or {}

    for p in schema:
        key = p.get("key", "")
        if p.get("type") != "number":
            continue
        if key == "symbol":
            continue
        if key in pins:
            fixed[key] = [pins[key]]
            continue
        if not include_avg and key in AVG_PARAM_KEYS:
            continue
        lo = float(p.get("min", p.get("default", 0)))
        hi = float(p.get("max", p.get("default", 0)))
        if lo >= hi:
            fixed[key] = [float(p.get("default", lo))]
        else:
            params.append((key, lo, hi))

    if not params:
        return fixed

    divisor = 9.0
    while True:
        grid = dict(fixed)
        for key, lo, hi in params:
            step = max(1.0, round((hi - lo) / divisor))
            grid[key] = _expand(lo, hi, step)
        combos = math.prod(len(v) for v in grid.values())
        if combos <= MAX_COMBOS or divisor <= 1.0:
            break
        divisor = max(1.0, divisor - 1.0)

    return grid


def _combo_count(grid: dict[str, list]) -> int:
    n = 1
    for vals in grid.values():
        n *= len(vals)
    return n


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Queue continuous-symbol sweep campaign")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--strategies", default="")
    ap.add_argument("--date-from", default="2026-01-01")
    ap.add_argument("--date-to", default="2026-06-13")
    ap.add_argument("--no-skip", action="store_true", help="Queue all pairs even if already tested")
    ap.add_argument("--include-avg-params", action="store_true")
    ap.add_argument("--pin", action="append", default=[], metavar="KEY=VALUE",
                    help="Fix a numeric param to one value (repeatable), e.g. --pin qty=1")
    ap.add_argument("--api", default="")
    args = ap.parse_args()

    pins: dict[str, float] = {}
    for spec in args.pin:
        k, _, v = spec.partition("=")
        if not k or not v:
            sys.exit(f"bad --pin {spec!r}, expected KEY=VALUE")
        pins[k.strip()] = float(v)

    api_base = args.api or os.environ.get("STL_API", "https://stl.shectory.ru")
    token = _token()
    headers = {"X-Agent-Token": token, "Content-Type": "application/json"}

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or DEFAULT_SYMBOLS
    only_strats = {s.strip() for s in args.strategies.split(",") if s.strip()}

    date_from = f"{args.date_from}T00:00:00"
    date_to = f"{args.date_to}T23:59:59"

    print(f"API: {api_base}")
    print(f"Symbols ({len(symbols)}): {', '.join(symbols)}")
    print(f"Date range: {date_from} — {date_to}")
    print(f"Avg params: {'included' if args.include_avg_params else 'fixed at defaults'}")
    print()

    with httpx.Client(base_url=api_base, headers=headers, timeout=30) as client:
        resp = client.get("/api/v1/strategies")
        resp.raise_for_status()
        strategies = resp.json()

        # Fetch already-tested (strategy, symbol) pairs to skip
        done: set[tuple[str, str]] = set()
        if not args.no_skip:
            try:
                r2 = client.get("/api/v1/agent/done-pairs")
                r2.raise_for_status()
                for p in r2.json():
                    done.add((p["strategy"], p["symbol"]))
                print(f"Already in leaderboard: {len(done)} (strategy, symbol) pairs — will skip")
            except Exception as exc:
                print(f"Warning: could not fetch done-pairs ({exc}) — will queue all")

    if only_strats:
        strategies = [s for s in strategies if s["id"] in only_strats]
        print(f"Filtered to {len(strategies)} strategies: {', '.join(s['id'] for s in strategies)}")

    print(f"Total strategies: {len(strategies)}")
    print()

    jobs: list[dict] = []
    for strat in strategies:
        sid = strat["id"]
        schema = strat.get("params_schema", [])
        script_code = strat["script_code"]
        defaults = strat.get("default_params", {})

        grid = _build_grid(schema, args.include_avg_params, pins)

        # Build baseParams: symbol placeholder + all non-swept numeric defaults + avg defaults
        base_params = dict(defaults)
        # Ensure avg params are pinned off when not sweeping them
        if not args.include_avg_params:
            base_params.setdefault("avg_max", 1)
            base_params.setdefault("avg_step_atr", 0)
            base_params.setdefault("tp_atr", 0)
            base_params.setdefault("avg_atr_n", 14)

        combos_per_sym = _combo_count(grid)

        for sym in symbols:
            if (sid, sym) in done:
                continue
            sym_grid = {**grid, "symbol": [sym]}
            total_combos = combos_per_sym  # symbol is 1 value, doesn't multiply

            jobs.append({
                "strategy": sid,
                "symbol": sym,
                "combos": total_combos,
                "payload": {
                    "scriptCode": script_code,
                    "baseParams": {**base_params, "symbol": sym},
                    "paramsGrid": sym_grid,
                    "symbol": sym,
                    "dateFrom": date_from,
                    "dateTo": date_to,
                    "engine": "remote",
                },
            })

    total_combos = sum(j["combos"] for j in jobs)
    print(f"Jobs to queue: {len(jobs)}  total combos: {total_combos:,}")
    print()

    if args.dry_run:
        # Print a summary table
        for j in sorted(jobs, key=lambda x: (x["strategy"], x["symbol"])):
            print(f"  {j['strategy']:<20} {j['symbol']:<6} {j['combos']:>6} combos")
        print()
        print("Dry run — nothing submitted.")
        return

    confirm = input(f"Submit {len(jobs)} jobs ({total_combos:,} combos total)? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    submitted = 0
    errors = 0
    with httpx.Client(base_url=api_base, headers=headers, timeout=60) as client:
        for i, job in enumerate(jobs, 1):
            try:
                resp = client.post("/api/v1/backtest/run", json=job["payload"])
                if resp.status_code in (200, 201, 202):
                    run_id = resp.json().get("runId", "?")
                    print(f"[{i}/{len(jobs)}] queued {job['strategy']} x {job['symbol']}"
                          f"  {job['combos']:,} combos  run={run_id}")
                    submitted += 1
                else:
                    print(f"[{i}/{len(jobs)}] FAIL {job['strategy']} x {job['symbol']}"
                          f"  HTTP {resp.status_code}: {resp.text[:120]}")
                    errors += 1
            except Exception as exc:
                print(f"[{i}/{len(jobs)}] ERROR {job['strategy']} x {job['symbol']}: {exc}")
                errors += 1

            # Throttle slightly so we don't hammer the VDS with 400 rapid inserts
            if i % 10 == 0:
                time.sleep(0.5)

    print()
    print(f"Done: {submitted} submitted, {errors} errors.")


if __name__ == "__main__":
    main()
