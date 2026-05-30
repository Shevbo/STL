def ema(prices: list[float], period: int) -> list[float]:
    if len(prices) < period:
        raise ValueError(f"Need at least {period} prices for ema(period={period})")
    k = 2.0 / (period + 1)
    result = [0.0] * len(prices)
    result[period - 1] = sum(prices[:period]) / period
    for i in range(period, len(prices)):
        result[i] = prices[i] * k + result[i - 1] * (1 - k)
    return result


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
