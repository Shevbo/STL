"""Indicator library for LAB strategies. Pure numeric, no Bar import."""
import math


def sma(prices: list[float], period: int) -> float:
    if len(prices) < period:
        raise ValueError(f"need {period} prices")
    return sum(prices[-period:]) / period


def ema(prices: list[float], period: int) -> list[float]:
    if len(prices) < period:
        raise ValueError(f"Need at least {period} prices for ema(period={period})")
    k = 2.0 / (period + 1)
    result = [0.0] * len(prices)
    result[period - 1] = sum(prices[:period]) / period
    for i in range(period, len(prices)):
        result[i] = prices[i] * k + result[i - 1] * (1 - k)
    return result


def ema_last(prices: list[float], period: int) -> float:
    return ema(prices, period)[-1]


def rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        raise ValueError(f"Need at least {period + 1} prices for rsi(period={period})")
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Average True Range (Wilder smoothing)."""
    n = len(closes)
    if n < period + 1 or len(highs) != n or len(lows) != n:
        raise ValueError(f"Need at least {period + 1} bars for atr(period={period})")
    trs = []
    for i in range(1, n):
        h, low, pc = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    avg = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        avg = (avg * (period - 1) + trs[i]) / period
    return avg


def stdev(prices: list[float], period: int) -> float:
    if len(prices) < period:
        raise ValueError(f"need {period} prices")
    w = prices[-period:]
    m = sum(w) / period
    return math.sqrt(sum((x - m) ** 2 for x in w) / period)


def bollinger(prices: list[float], period: int = 20, mult: float = 2.0) -> tuple[float, float, float]:
    """Returns (lower, mid, upper)."""
    mid = sma(prices, period)
    sd = stdev(prices, period)
    return mid - mult * sd, mid, mid + mult * sd


def macd(prices: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float]:
    """Returns (macd_line, signal_line) at the last bar."""
    if len(prices) < slow + signal:
        raise ValueError("need more prices for macd")
    ef = ema(prices, fast)
    es = ema(prices, slow)
    macd_series = [ef[i] - es[i] for i in range(slow - 1, len(prices))]
    sig = ema(macd_series, signal)
    return macd_series[-1], sig[-1]


def stochastic(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """%K stochastic oscillator (0..100)."""
    if len(closes) < period:
        raise ValueError("need more bars for stochastic")
    hh = max(highs[-period:])
    ll = min(lows[-period:])
    if hh == ll:
        return 50.0
    return 100.0 * (closes[-1] - ll) / (hh - ll)


def cci(highs: list[float], lows: list[float], closes: list[float], period: int = 20) -> float:
    """Commodity Channel Index."""
    if len(closes) < period:
        raise ValueError("need more bars for cci")
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    w = tp[-period:]
    m = sum(w) / period
    md = sum(abs(x - m) for x in w) / period
    if md == 0:
        return 0.0
    return (tp[-1] - m) / (0.015 * md)


def momentum(prices: list[float], period: int = 10) -> float:
    if len(prices) < period + 1:
        raise ValueError("need more prices for momentum")
    return prices[-1] - prices[-1 - period]


def roc(prices: list[float], period: int = 10) -> float:
    """Rate of change %."""
    if len(prices) < period + 1:
        raise ValueError("need more prices for roc")
    base = prices[-1 - period]
    if base == 0:
        return 0.0
    return 100.0 * (prices[-1] - base) / base


def williams_r(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Williams %R (-100..0)."""
    if len(closes) < period:
        raise ValueError("need more bars for williams_r")
    hh = max(highs[-period:])
    ll = min(lows[-period:])
    if hh == ll:
        return -50.0
    return -100.0 * (hh - closes[-1]) / (hh - ll)


def donchian(highs: list[float], lows: list[float], period: int) -> tuple[float, float]:
    """Returns (lower, upper) Donchian channel over `period` bars."""
    if len(highs) < period:
        raise ValueError("need more bars for donchian")
    return min(lows[-period:]), max(highs[-period:])


def keltner(highs: list[float], lows: list[float], closes: list[float],
            ema_period: int = 20, atr_period: int = 10, mult: float = 2.0) -> tuple[float, float, float]:
    """Returns (lower, mid, upper) Keltner channel."""
    mid = ema_last(closes, ema_period)
    a = atr(highs, lows, closes, atr_period)
    return mid - mult * a, mid, mid + mult * a
