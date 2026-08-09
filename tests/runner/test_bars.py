from robot_runner.bars import BarBuilder, pick_price


def test_pick_price_fallback_chain():
    assert pick_price(100.0, 99.0, 101.0) == 100.0      # last wins
    assert pick_price(0.0, 87500.0, 87570.0) == 87535.0  # mid when no last
    assert pick_price(0.0, 87510.0, 0.0) == 87510.0      # bid-only feed
    assert pick_price(0.0, 0.0, 87570.0) == 87570.0      # ask-only
    assert pick_price(0.0, 0.0, 0.0) == 0.0              # nothing usable


def test_bar_builder_aggregates_minutes():
    b = BarBuilder()
    t0 = 1_751_500_020_000  # ms epoch, mid-minute
    m = 60_000
    # minute 0: three ticks
    b.on_tick(t0, 100.0)
    b.on_tick(t0 + 10_000, 105.0)
    b.on_tick(t0 + 30_000, 99.0)
    # minute 1 opens -> minute 0 closes
    b.on_tick(t0 + m, 101.0)
    bars = b.bars()
    assert len(bars) == 1
    bar = bars[0]
    assert bar.time == (t0 // m) * 60  # unix seconds, minute-truncated
    assert (bar.open, bar.high, bar.low, bar.close) == (100.0, 105.0, 99.0, 99.0)
    # forming minute is NOT visible
    assert b.bars(1)[-1].time == bar.time


def test_bar_builder_gap_no_synthetic_and_caps():
    b = BarBuilder(max_bars=2)
    t0 = 1_751_500_000_000
    m = 60_000
    for i in range(4):
        b.on_tick(t0 + i * 2 * m, 100.0 + i)  # every other minute (gaps)
    assert len(b.bars()) == 2  # capped, no synthetic gap bars


def test_bar_builder_ignores_bad_and_late_ticks():
    b = BarBuilder()
    t0 = 1_751_500_000_000
    b.on_tick(t0, 0.0)          # zero price ignored
    b.on_tick(t0, 100.0)
    b.on_tick(t0 + 60_000, 101.0)   # closes minute 0
    b.on_tick(t0 - 60_000, 999.0)   # late tick from the past — ignored
    bars = b.bars()
    assert len(bars) == 1 and bars[0].close == 100.0


# ── traded-метка: что панель имеет право нарисовать ────────────────────────────
# Лента молчит >TAPE_PRIORITY_MS -> бары строятся из ЗАМЕРШЕЙ котировки. На
# стоящей бирже так набегают сотни плоских минут, и виджет-график всю ночь
# рисовал свечи (08.08.2026). Стратегия их видит как раньше, человек — нет.

def test_traded_bars_hides_quote_only_minutes():
    b = BarBuilder()
    t0 = 1_751_500_000_000
    m = 60_000
    b.on_trade(t0, 100.0, 3)                 # минута 0 — настоящая
    b.on_tick(t0 + m, 100.0)                 # минута 1 — только котировка
    b.on_tick(t0 + 2 * m, 100.0)             # минута 2 — только котировка
    b.on_trade(t0 + 3 * m, 101.0, 1)         # минута 3 — снова сделка
    b.on_tick(t0 + 4 * m, 101.0)             # закрывает минуту 3
    assert [bar.time for bar in b.bars()] == [t0 // m * 60 + i * 60 for i in range(4)]
    assert [bar.close for bar in b.traded_bars()] == [100.0, 101.0]
    assert all(bar.traded for bar in b.traded_bars())


def test_tick_after_trade_keeps_the_minute_real():
    """Минута, начатая сделкой и дополненная котировкой, остаётся настоящей:
    метка только взводится, поздний тик факт сделки не стирает."""
    b = BarBuilder()
    t0 = 1_751_500_020_000                   # ровно граница минуты
    b.on_trade(t0, 100.0, 2)
    b.on_tick(t0 + 35_000, 100.5)            # та же минута, лента молчит 35 с (>30 с)
    b.on_tick(t0 + 60_000, 101.0)            # закрывает минуту 0
    assert len(b.traded_bars()) == 1
    assert b.traded_bars()[0].high == 100.5  # котировка бар дополнила


def test_trade_after_tick_promotes_the_minute():
    """Обратный порядок: минуту начал тик, сделка пришла позже — бар настоящий."""
    b = BarBuilder()
    t0 = 1_751_500_020_000                   # ровно граница минуты
    b.on_tick(t0, 100.0)
    b.on_trade(t0 + 20_000, 100.4, 5)        # та же минута
    b.on_trade(t0 + 60_000, 101.0, 1)        # закрывает минуту 0
    assert [bar.traded for bar in b.bars()] == [True]


def test_traded_flag_survives_persistence_and_legacy_rows():
    b = BarBuilder()
    t0 = 1_751_500_000_000
    m = 60_000
    b.on_trade(t0, 100.0, 3)
    b.on_tick(t0 + m, 100.0)
    b.on_tick(t0 + 2 * m, 100.0)             # закрывает минуту 1
    rows = b.to_rows()
    assert [r[6] for r in rows] == [1, 0]

    restored = BarBuilder()
    restored.seed(rows)
    assert [bar.time for bar in restored.traded_bars()] == [rows[0][0]]

    # Строки СТАРОГО формата (6 колонок) считаем настоящими: иначе рестарт на
    # старом state разом спрятал бы у панели всю историю.
    legacy = BarBuilder()
    legacy.seed([[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows])
    assert len(legacy.traded_bars()) == 2
