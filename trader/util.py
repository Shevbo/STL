"""Small shared helpers used across trader subpackages."""
from decimal import Decimal


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
