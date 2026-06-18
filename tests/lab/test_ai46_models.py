"""Tests for the team-46 statistical models (pure Python)."""
import math
import random

from trader.lab.ai46 import models as M


# ── CUSUM (exact port of risk/cusum.go) ───────────────────────────────────────

def test_cusum_no_drift_no_trigger():
    c = M.CUSUMDetector(sigma_pnl=1.0)  # k=0.5, h=5
    triggered = any(c.update(0.0, 0.0) for _ in range(100))
    assert not triggered
    assert c.pos == 0.0 and c.neg == 0.0


def test_cusum_positive_drift_triggers():
    c = M.CUSUMDetector(sigma_pnl=1.0)  # each +1 dev adds (1-0.5)=0.5 to pos; >5 after 11
    fired_at = None
    for i in range(1, 30):
        if c.update(1.0, 0.0):
            fired_at = i
            break
    assert fired_at == 11          # 11×0.5 = 5.5 > h=5
    assert c.neg == 0.0


def test_cusum_negative_drift_triggers_lower():
    c = M.CUSUMDetector(sigma_pnl=2.0)  # k=1, h=10; -3 dev adds (3-1)=2 to neg → >10 after 6
    fired = [c.update(-3.0, 0.0) for _ in range(6)]
    assert fired[-1] is True
    c.reset()
    assert c.pos == 0.0 and c.neg == 0.0


# ── GARCH(1,1) ────────────────────────────────────────────────────────────────

def _garch_series(n, omega, alpha, beta, seed):
    rng = random.Random(seed)
    s2 = omega / max(1e-9, 1 - alpha - beta)
    out = []
    for _ in range(n):
        r = rng.gauss(0, 1) * math.sqrt(s2)
        out.append(r)
        s2 = omega + alpha * r * r + beta * s2
    return out


def test_garch_forecast_sane_and_stationary():
    r = _garch_series(800, omega=1e-6, alpha=0.08, beta=0.9, seed=1)
    closes = [100.0]
    for x in r:
        closes.append(closes[-1] * math.exp(x))
    res = M.garch11_forecast(r, bars_per_year=M.BARS_PER_YEAR_1M)
    assert res is not None
    assert res.forecast_vol > 0 and math.isfinite(res.forecast_vol)
    assert 0 <= res.alpha and 0 <= res.beta and res.alpha + res.beta < 1.0
    assert res.omega > 0


def test_garch_higher_vol_series_higher_forecast():
    calm = _garch_series(600, omega=1e-7, alpha=0.05, beta=0.9, seed=2)
    wild = _garch_series(600, omega=1e-5, alpha=0.15, beta=0.8, seed=3)
    fc_calm = M.garch11_forecast(calm).forecast_vol
    fc_wild = M.garch11_forecast(wild).forecast_vol
    assert fc_wild > fc_calm


# ── HMM regime ────────────────────────────────────────────────────────────────

def test_hmm_detects_panic_regime_at_end():
    rng = random.Random(7)
    calm = [rng.gauss(0, 0.0008) for _ in range(220)]
    panic = [rng.gauss(0, 0.02) for _ in range(70)]   # recent regime: high variance
    res = M.hmm_regime(calm + panic)
    assert res is not None
    assert res.state == "panic"
    assert 0.0 <= res.probability <= 1.0


def test_hmm_short_series_none():
    assert M.hmm_regime([0.0] * 5) is None


# ── Conformal interval ────────────────────────────────────────────────────────

def test_conformal_brackets_price_and_covers():
    rng = random.Random(11)
    closes = [100.0]
    for _ in range(500):
        closes.append(closes[-1] * math.exp(rng.gauss(0, 0.001)))
    res = M.conformal_interval(closes, horizon=1, ci=0.9)
    assert res is not None
    assert res.lower < closes[-1] < res.upper
    assert res.ci_pct > 0
    # empirical coverage of the half-width over the calibration scores ≈ ci
    q = (res.upper - res.lower) / 2
    scores = [abs(closes[t + 1] - closes[t]) for t in range(len(closes) - 1)]
    cov = sum(1 for s in scores if s <= q) / len(scores)
    assert cov >= 0.85


def test_conformal_wider_with_horizon():
    rng = random.Random(13)
    closes = [100.0]
    for _ in range(500):
        closes.append(closes[-1] * math.exp(rng.gauss(0, 0.001)))
    w1 = M.conformal_interval(closes, horizon=1).ci_pct
    w10 = M.conformal_interval(closes, horizon=10).ci_pct
    assert w10 > w1
