"""Time-sliced backtest scoring: an in-sample/out-of-sample split and per-window
consistency, computed from a run's CLOSED round-trips. Pure — no I/O, no clock —
so it is unit-tested without running a backtest. A config that only prints in one
window (the GDU6 curve-fit) scores low on windows_profitable and degrade."""
from __future__ import annotations


def window_metrics(pairs: list[dict], span: tuple[float, float],
                   is_frac: float = 0.7, splits: int = 4) -> dict:
    t0, t1 = span
    total = t1 - t0
    if total <= 0:
        return {"net_is": 0.0, "net_oos": 0.0, "degrade": None,
                "windows_profitable": 0, "windows_total": splits}

    boundary = t0 + total * is_frac
    net_is = sum(p["pnl"] for p in pairs if p["time"] < boundary)
    net_oos = sum(p["pnl"] for p in pairs if p["time"] >= boundary)

    is_secs = total * is_frac
    oos_secs = total * (1.0 - is_frac)
    is_rate = (net_is / is_secs) if is_secs > 0 else 0.0
    oos_rate = (net_oos / oos_secs) if oos_secs > 0 else 0.0
    degrade = round(oos_rate / is_rate, 6) if is_rate > 0 else None

    win = total / splits
    sums = [0.0] * splits
    for p in pairs:
        idx = min(splits - 1, int((p["time"] - t0) / win))
        sums[idx] += p["pnl"]
    windows_profitable = sum(1 for s in sums if s > 0)

    return {"net_is": net_is, "net_oos": net_oos, "degrade": degrade,
            "windows_profitable": windows_profitable, "windows_total": splits}
