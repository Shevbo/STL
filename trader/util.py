"""Small shared helpers used across trader subpackages."""
import json
from decimal import Decimal


def i9_hb_view(raw: "str | None", now_ts: float, stale_after: float = 15.0) -> "dict | None":
    """Turn the stored i9 heartbeat JSON into a monitor view: passthrough metrics plus a
    server-computed age_sec/stale. None when the i9 has never reported (or the value is
    unparseable). `raw` is the agent_control(key='i9_heartbeat') value written by POST
    /api/v1/agent/heartbeat (it carries a `_recv_ts` server receive time)."""
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except Exception:
        return None
    ts = float(d.get("_recv_ts") or 0)
    age = max(0.0, now_ts - ts) if ts else None
    return {
        "cpu_pct": d.get("cpu_pct"),
        "per_core": d.get("per_core") or [],
        "cpu_count": d.get("cpu_count"),
        "workers": d.get("workers"),
        "priority": d.get("priority"),
        "leaders": d.get("leaders") or [],
        "ram_pct": d.get("ram_pct"),
        "ram_used_mb": d.get("ram_used_mb"),
        "ram_total_mb": d.get("ram_total_mb"),
        "version": d.get("version"),
        "has_psutil": bool(d.get("psutil")),
        "activity": d.get("activity") or {"state": "?"},
        "agent_id": d.get("agent_id"),
        "age_sec": round(age) if age is not None else None,
        "stale": (age is None) or (age > stale_after),
    }


def unwrap_decimal(obj, *, as_float: bool = False):
    """Unwrap a Finam decimal value from its many shapes into one number.

    Finam returns money/price as either a JSON wrapper ({"value": "123.4"}), a proto
    message with a `.value` field, or a bare scalar. This collapses three near-identical
    helpers (pos._dec, ws_hub._dec_field, grpc bar_from_proto.flt). Missing/empty -> 0.
    Returns Decimal by default, or float when as_float=True.
    """
    if isinstance(obj, dict):
        raw = obj.get("value", "0")
    elif hasattr(obj, "value"):
        raw = obj.value
    else:
        raw = obj
    if raw is None or raw == "":
        raw = "0"
    val = Decimal(str(raw))
    return float(val) if as_float else val
