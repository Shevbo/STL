"""Окно прогрева macd_cross должно быть длиннее самой длинной EMA, иначе знак залипает.

Баг: прогрев считался как `slow + signal + 2`. У живого робота
lxk22tsffsxiiotb8kmpsato (RIU6, fast=57, slow=48, signal=10) это давало окно 60 —
короче самой длинной EMA(57). Она не успевала разойтись с сигнальной линией,
`sig_macd` возвращал +1 НАВСЕГДА, и робот за 6 суток реала сделал 778 закрытий
лонга и ноль шортов, торгуя вместо MACD односторонней усредняющей машиной.
Замер на RIU6 M1 за 01.06-31.07.2026: окно 60 -> 0 переворотов, окно 120 -> 1453.
"""
import math
from types import SimpleNamespace

from trader.lab.strategies.library import REGISTRY

LIVE = {"fast": 57, "slow": 48, "signal": 10, "qty": 2}


def _wave(n: int, period: int = 400) -> list:
    """Синусоида: знак MACD ОБЯЗАН перевернуться хотя бы раз за полный период."""
    return [SimpleNamespace(close=90000 + 500 * math.sin(2 * math.pi * i / period))
            for i in range(n)]


def test_macd_cross_window_outlives_longest_ema():
    spec = REGISTRY["macd_cross"]
    need = spec["warmup"](LIVE)
    assert need > max(LIVE["fast"], LIVE["slow"]) * 2, (
        f"окно {need} не длиннее самой длинной EMA — знак MACD залипнет")


def test_macd_cross_signal_flips_within_its_own_window():
    spec = REGISTRY["macd_cross"]
    need = spec["warmup"](LIVE)
    bars = _wave(need + 400)
    # Ровно то, что делает make_on_bar: signal() на последних `need` барах.
    seen = {spec["signal"](bars[i - need:i], LIVE) for i in range(need, len(bars))}
    assert seen == {1, -1}, f"сигнал не переворачивается на своём окне: {seen}"


def test_macd_fast_equals_slow_has_no_signal():
    """EMA(n)−EMA(n) ≡ 0 и сигнальная линия тоже 0, а `m > s` (0>0=False) отдавал
    ВЕЧНЫЙ ШОРТ. 8 бумажных роботов стояли на таком конфиге (05.08.2026)."""
    spec = REGISTRY["macd_cross"]
    p = {**LIVE, "fast": 20, "slow": 20}
    need = spec["warmup"](p)
    bars = _wave(need + 300)
    assert {spec["signal"](bars[i - need:i], p) for i in range(need, len(bars))} == {None}


def test_registry_warmup_covers_every_period():
    """Окно прогрева обязано покрывать САМЫЙ ДЛИННЫЙ период стратегии на любых
    допустимых схемой параметрах, иначе индикатор падает или знак залипает
    (ema_atr: схема пускала fast=40 при slow=15, окно slow+2=17 → ValueError)."""
    bad = []
    for sid, spec in REGISTRY.items():
        axes = {p["key"]: p for p in spec["params_schema"] if p.get("type") == "number"}
        # avg_atr_n сюда не входит: это период слоя усреднения, и его добирает сам
        # make_on_bar (`need = max(warmup(params), atr_n + 1)`), а не warmup стратегии.
        periods = [k for k in ("fast", "slow", "mid", "period", "atr_period", "ema_period")
                   if k in axes]
        if len(periods) < 2:
            continue
        # Враждебный угол: КАЖДЫЙ период по очереди на максимуме, остальные на
        # минимуме. Все-на-максимуме ничего не ловит — там перекос не возникает
        # (у ema_atr fast=40 < slow=120, а бьёт как раз fast=40 при slow=15).
        for long_key in periods:
            worst = {**spec["default_params"],
                     **{k: axes[k]["min"] for k in periods}, long_key: axes[long_key]["max"]}
            need, longest = int(spec["warmup"](worst)), max(int(worst[k]) for k in periods)
            if need < longest:
                bad.append(f"{sid} ({long_key}={worst[long_key]}): окно {need} < периода {longest}")
    assert not bad, "; ".join(bad)
