"""Треугольник MX против RI × Si: знаки ног и нейтральность корзины.

Денежный путь с тремя ногами легко перепутать: длинный спред = длинный MX и короткие
RI и Si. Если синтетика (RI × Si) и MX сдвинулись одинаково, корзина обязана остаться
около нуля; если MX подорожал относительно синтетики, длинный спред в плюсе.
"""
from scripts.triangle_arb import close_trade

RI0, SI0, MX0 = 84000.0, 85000.0, 230000.0


def _entry():
    usd = SI0 / 1000.0
    return {"i": 0, "dev": 0.0, "sd": 0.001, "ts": 1789000000, "mx": MX0, "ri": RI0, "si": SI0,
            "n_mx": 1.0, "n_ri": MX0 / (RI0 * 0.02 * usd), "n_si": MX0 / SI0, "usd": usd}


def test_basket_is_neutral_when_index_moves_consistently():
    """Индекс вырос на 1% в долларах и доллар на 1%: MX растёт ~2%, синтетика тоже."""
    ri1, si1 = RI0 * 1.01, SI0 * 1.01
    mx1 = MX0 * 1.01 * 1.01
    _, _, _, gross = close_trade(_entry(), mx1, ri1, si1, pos=1, cost_mult=0.0)
    assert abs(gross) < 0.002 * MX0, f"корзина не нейтральна: {gross:.0f} ₽"


def test_long_spread_profits_when_mx_rich_moves_up():
    _, _, _, gross = close_trade(_entry(), MX0 * 1.005, RI0, SI0, pos=1, cost_mult=0.0)
    assert gross > 0.004 * MX0


def test_short_spread_is_mirror():
    long_ = close_trade(_entry(), MX0 * 1.005, RI0, SI0, pos=1, cost_mult=0.0)[3]
    short = close_trade(_entry(), MX0 * 1.005, RI0, SI0, pos=-1, cost_mult=0.0)[3]
    assert abs(long_ + short) < 1e-6


def test_costs_are_charged_on_all_legs_both_sides():
    _, _, net, gross = close_trade(_entry(), MX0, RI0, SI0, pos=1, cost_mult=1.0)
    assert gross == 0.0 and net < -100          # полспреда трёх ног дважды + сбор
