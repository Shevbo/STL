"""Statistical models for team-46 — pure Python, no numpy/scipy/hmmlearn/arch.

- CUSUMDetector: exact port of go-bot/internal/risk/cusum.go (Page 1954 test).
- garch11_forecast: GARCH(1,1) volatility, MLE-fit via a hand-rolled Nelder-Mead.
- hmm_regime: 4-state Gaussian HMM (Baum-Welch) → regime label + probability,
  matching the ML-service contract states trend_up/trend_down/flat/panic.
- conformal_interval: split-conformal price interval (lower/upper/ci_pct).

HMM/GARCH/conformal are reconstructions from the gRPC contract + standard methods
(the original Python ML service is not in the repo); CUSUM is a 1:1 port.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# 1-min bars: trading days × hours × minutes. Matches features.realized_vol.
BARS_PER_YEAR_1M = 252 * 6.5 * 60


# ════════════════════════════════════════════════════════════════════════════
#  CUSUM — exact port of risk/cusum.go
# ════════════════════════════════════════════════════════════════════════════

class CUSUMDetector:
    """Page (1954) cumulative-sum test for drift between actual and expected P&L.
    k = 0.5σ (allowance), h = 5σ (halt threshold)."""

    def __init__(self, sigma_pnl: float) -> None:
        self.pos = 0.0
        self.neg = 0.0
        self.sigma = sigma_pnl
        self.k = 0.5 * sigma_pnl
        self.h = 5.0 * sigma_pnl

    def update(self, actual_pnl: float, expected_pnl: float) -> bool:
        """Feed one (actual − expected) deviation. True when |CUSUM| > h."""
        dev = actual_pnl - expected_pnl
        self.pos = max(0.0, self.pos + dev - self.k)
        self.neg = max(0.0, self.neg - dev - self.k)
        return self.pos > self.h or self.neg > self.h

    def reset(self) -> None:
        self.pos = 0.0
        self.neg = 0.0

    def recalibrate(self, sigma_pnl: float) -> None:
        if sigma_pnl <= 0:
            return
        self.sigma = sigma_pnl
        self.k = 0.5 * sigma_pnl
        self.h = 5.0 * sigma_pnl


# ════════════════════════════════════════════════════════════════════════════
#  helpers
# ════════════════════════════════════════════════════════════════════════════

def _log_returns(closes: list[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _var(xs: list[float], mu: float | None = None) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs) if mu is None else mu
    return sum((x - m) ** 2 for x in xs) / len(xs)


def _nelder_mead(f, x0: list[float], step: float = 0.1,
                 max_iter: int = 400, tol: float = 1e-8) -> list[float]:
    """Minimise f over R^n. Standard Nelder-Mead simplex, pure Python."""
    n = len(x0)
    alpha, gamma, rho, sigma = 1.0, 2.0, 0.5, 0.5
    simplex = [list(x0)]
    for i in range(n):
        x = list(x0)
        x[i] += step if x[i] == 0 else step * (1 + abs(x[i]))
        simplex.append(x)
    fvals = [f(p) for p in simplex]
    for _ in range(max_iter):
        order = sorted(range(n + 1), key=lambda i: fvals[i])
        simplex = [simplex[i] for i in order]
        fvals = [fvals[i] for i in order]
        if abs(fvals[-1] - fvals[0]) <= tol * (abs(fvals[0]) + tol):
            break
        centroid = [sum(simplex[i][j] for i in range(n)) / n for j in range(n)]
        # reflection
        xr = [centroid[j] + alpha * (centroid[j] - simplex[-1][j]) for j in range(n)]
        fr = f(xr)
        if fvals[0] <= fr < fvals[-2]:
            simplex[-1], fvals[-1] = xr, fr
            continue
        if fr < fvals[0]:  # expansion
            xe = [centroid[j] + gamma * (xr[j] - centroid[j]) for j in range(n)]
            fe = f(xe)
            simplex[-1], fvals[-1] = (xe, fe) if fe < fr else (xr, fr)
            continue
        # contraction
        xc = [centroid[j] + rho * (simplex[-1][j] - centroid[j]) for j in range(n)]
        fc = f(xc)
        if fc < fvals[-1]:
            simplex[-1], fvals[-1] = xc, fc
            continue
        # shrink
        for i in range(1, n + 1):
            simplex[i] = [simplex[0][j] + sigma * (simplex[i][j] - simplex[0][j]) for j in range(n)]
            fvals[i] = f(simplex[i])
    best = min(range(n + 1), key=lambda i: fvals[i])
    return simplex[best]


# ════════════════════════════════════════════════════════════════════════════
#  GARCH(1,1)
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class GARCHResult:
    omega: float
    alpha: float
    beta: float
    forecast_vol: float   # annualised 1-step volatility forecast
    sigma2_next: float     # next-step variance (per-bar)


def _garch_nll(params: list[float], r2: list[float], var0: float) -> float:
    """Negative Gaussian log-likelihood of GARCH(1,1) given squared returns.
    Parameters are mapped through softplus/sigmoid so the optimiser is
    unconstrained while ω>0 and 0≤α, β and α+β<1 hold."""
    om = math.log1p(math.exp(min(params[0], 30)))         # softplus → ω>0
    a = 1.0 / (1.0 + math.exp(-params[1]))                 # sigmoid → (0,1)
    b = 1.0 / (1.0 + math.exp(-params[2]))
    s = a + b
    if s >= 0.999:                                         # keep stationary
        a *= 0.999 / s
        b *= 0.999 / s
    sigma2 = var0
    nll = 0.0
    for x2 in r2:
        if sigma2 <= 1e-300:
            sigma2 = 1e-300
        nll += 0.5 * (math.log(2 * math.pi * sigma2) + x2 / sigma2)
        sigma2 = om + a * x2 + b * sigma2
    return nll


def garch11_forecast(returns: list[float], bars_per_year: float = BARS_PER_YEAR_1M) -> GARCHResult | None:
    """Fit GARCH(1,1) by MLE and return the annualised 1-step vol forecast."""
    if len(returns) < 30:
        return None
    mu = _mean(returns)
    r = [x - mu for x in returns]
    r2 = [x * x for x in r]
    var0 = _var(returns)
    if var0 <= 0:
        return None
    best = _nelder_mead(lambda p: _garch_nll(p, r2, var0), [0.0, -2.0, 1.5])
    omega = math.log1p(math.exp(min(best[0], 30)))
    alpha = 1.0 / (1.0 + math.exp(-best[1]))
    beta = 1.0 / (1.0 + math.exp(-best[2]))
    s = alpha + beta
    if s >= 0.999:
        alpha *= 0.999 / s
        beta *= 0.999 / s
    # roll variance to the last bar, then forecast one step
    sigma2 = var0
    for x2 in r2:
        sigma2 = omega + alpha * x2 + beta * sigma2
    sigma2_next = max(sigma2, 1e-300)
    forecast_vol = math.sqrt(sigma2_next * bars_per_year)
    return GARCHResult(omega=omega, alpha=alpha, beta=beta,
                       forecast_vol=forecast_vol, sigma2_next=sigma2_next)


# ════════════════════════════════════════════════════════════════════════════
#  Gaussian HMM regime
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class HMMResult:
    state: str          # "trend_up" | "trend_down" | "flat" | "panic"
    probability: float  # posterior of the current state at the last bar


def _gauss_pdf(x: float, mu: float, var: float) -> float:
    var = max(var, 1e-12)
    return math.exp(-0.5 * (x - mu) ** 2 / var) / math.sqrt(2 * math.pi * var)


def hmm_regime(returns: list[float], n_states: int = 4, n_iter: int = 40) -> HMMResult | None:
    """Fit a Gaussian HMM (Baum-Welch) on returns; label the most-likely current
    state as trend_up/trend_down/flat/panic by its mean and variance."""
    x = returns
    n = len(x)
    if n < 20:
        return None
    K = n_states
    # ── init from return quantiles so states start separated ──
    srt = sorted(x)
    mus, vars = [], []
    for k in range(K):
        lo = srt[k * n // K]
        hi = srt[min((k + 1) * n // K, n) - 1]
        bucket = [v for v in x if lo <= v <= hi] or [srt[k * n // K]]
        m = _mean(bucket)
        mus.append(m)
        vars.append(max(_var(bucket, m), 1e-10))
    A = [[(0.9 if i == j else 0.1 / (K - 1)) for j in range(K)] for i in range(K)]
    pi = [1.0 / K] * K

    gamma = [[0.0] * K for _ in range(n)]
    for _ in range(n_iter):
        # ── forward (scaled) ──
        alpha = [[0.0] * K for _ in range(n)]
        c = [0.0] * n
        for k in range(K):
            alpha[0][k] = pi[k] * _gauss_pdf(x[0], mus[k], vars[k])
        c[0] = sum(alpha[0]) or 1e-300
        alpha[0] = [a / c[0] for a in alpha[0]]
        for t in range(1, n):
            for k in range(K):
                s = sum(alpha[t - 1][j] * A[j][k] for j in range(K))
                alpha[t][k] = s * _gauss_pdf(x[t], mus[k], vars[k])
            c[t] = sum(alpha[t]) or 1e-300
            alpha[t] = [a / c[t] for a in alpha[t]]
        # ── backward (scaled) ──
        beta = [[0.0] * K for _ in range(n)]
        beta[n - 1] = [1.0] * K
        for t in range(n - 2, -1, -1):
            for k in range(K):
                beta[t][k] = sum(
                    A[k][j] * _gauss_pdf(x[t + 1], mus[j], vars[j]) * beta[t + 1][j]
                    for j in range(K)
                ) / c[t + 1]
        # ── gamma / xi and re-estimate ──
        for t in range(n):
            tot = sum(alpha[t][k] * beta[t][k] for k in range(K)) or 1e-300
            for k in range(K):
                gamma[t][k] = alpha[t][k] * beta[t][k] / tot
        new_A = [[0.0] * K for _ in range(K)]
        for i in range(K):
            denom = sum(gamma[t][i] for t in range(n - 1)) or 1e-300
            for j in range(K):
                num = 0.0
                for t in range(n - 1):
                    num += (alpha[t][i] * A[i][j]
                            * _gauss_pdf(x[t + 1], mus[j], vars[j])
                            * beta[t + 1][j] / c[t + 1])
                new_A[i][j] = num / denom
        A = new_A
        pi = list(gamma[0])
        for k in range(K):
            w = sum(gamma[t][k] for t in range(n)) or 1e-300
            mk = sum(gamma[t][k] * x[t] for t in range(n)) / w
            vk = sum(gamma[t][k] * (x[t] - mk) ** 2 for t in range(n)) / w
            mus[k] = mk
            vars[k] = max(vk, 1e-10)

    # ── label states: max-variance = panic; of the rest, by mean ──
    order_var = sorted(range(K), key=lambda k: vars[k])
    panic = order_var[-1]
    rest = [k for k in order_var if k != panic]
    by_mean = sorted(rest, key=lambda k: mus[k])
    labels = {by_mean[0]: "trend_down", by_mean[-1]: "trend_up"}
    for k in by_mean[1:-1]:
        labels[k] = "flat"
    labels[panic] = "panic"

    cur = max(range(K), key=lambda k: gamma[n - 1][k])
    return HMMResult(state=labels[cur], probability=gamma[n - 1][cur])


# ════════════════════════════════════════════════════════════════════════════
#  Conformal interval
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ConformalResult:
    lower: float
    upper: float
    ci_pct: float   # interval width as a fraction of price


def conformal_interval(closes: list[float], horizon: int = 1, ci: float = 0.9) -> ConformalResult | None:
    """Split-conformal price interval `horizon` bars ahead at coverage `ci`.

    Nonconformity score = |price_{t+h} − price_t| over the calibration history;
    the (1−α) empirical quantile (with the conformal +1 correction) is the
    symmetric half-width applied to the last price."""
    n = len(closes)
    if n < horizon + 10 or not (0 < ci < 1):
        return None
    scores = [abs(closes[t + horizon] - closes[t]) for t in range(n - horizon)]
    scores.sort()
    m = len(scores)
    rank = math.ceil((m + 1) * ci)
    idx = min(rank, m) - 1
    q = scores[idx]
    price = closes[-1]
    lower = price - q
    upper = price + q
    ci_pct = (upper - lower) / price if price else 0.0
    return ConformalResult(lower=lower, upper=upper, ci_pct=ci_pct)
