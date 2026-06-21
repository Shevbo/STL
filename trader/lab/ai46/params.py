"""Tunable parameters for team-46, defaults equal to the hardcoded live constants.

Live code paths use these defaults, so behaviour is unchanged. The backtest sweep
varies them to find a commission-aware, less-overtrading configuration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from trader.lab.ai46 import contrarian as C
from trader.lab.ai46 import detector as DET


@dataclass
class BotParams:
    # ── detector triggers ───────────────────────────────────────────────────
    ofi_thr: float = 0.7                 # |OFI| anomaly threshold
    vol_thr: float = 3.0                 # volume_ratio spike threshold
    shock_z: float = 2.0                 # price-shock sigma threshold
    cooldown: float = DET._EMIT_COOLDOWN  # per (ticker,type) emit cooldown, s
    # ── contrarian session ──────────────────────────────────────────────────
    min_agreement: float = C.MIN_AGREEMENT
    long_ofi_boost: float = C.LONG_OFI_BOOST
    monitoring_dur: float = C.MONITORING_DUR
    primary_hold: float = C.PRIMARY_HOLD
    wait_reversal: float = C.WAIT_REVERSAL
    reversal_hold: float = C.REVERSAL_HOLD
    reversal_sigs: int = C.REVERSAL_SIGS
    size_base: float = C.SIZE_BASE
    # ── risk ────────────────────────────────────────────────────────────────
    max_positions: int = 5
    max_exposure: float = 0.30

    def as_dict(self) -> dict:
        return asdict(self)
