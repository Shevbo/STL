"""Tests for the team-46 paper execution + risk."""
from trader.lab.ai46.execution import PaperExecutor, RiskManager


def test_paper_long_profit():
    ex = PaperExecutor()
    ex.set_price("RIU6", 100.0)
    ex.enter_long("RIU6", "contrarian", 0.1, 0.015, 0.025)
    ex.set_price("RIU6", 101.0)                      # +1% — до тейка 2.5% не дошло
    ex.close_soft("RIU6", "contrarian")              # выход по таймеру удержания
    assert [f.kind for f in ex.fills] == ["open", "close_soft"]
    assert abs(ex.realized_pnl - (0.01 * 0.1)) < 1e-9


def test_paper_short_profit_on_drop():
    ex = PaperExecutor()
    ex.set_price("X", 100.0)
    ex.enter_short("X", "contrarian", 0.1, 0.015, 0.02)
    ex.set_price("X", 99.0)
    ex.close_hard("X", "contrarian")
    assert ex.realized_pnl > 0
    assert "X" not in ex.positions


def test_take_profit_closes_the_position():
    """Тейк, заданный сессией при входе, обязан СРАБОТАТЬ. Раньше _Pos хранил
    stop_pct/take_pct, но их никто не читал — выход был только по таймеру."""
    ex = PaperExecutor()
    ex.set_price("A", 100.0)
    ex.enter_long("A", "contrarian", 0.1, 0.015, 0.025)
    ex.set_price("A", 102.6)                          # +2.6% > тейк 2.5%
    assert "A" not in ex.positions
    assert [f.kind for f in ex.fills] == ["open", "close_take"]
    assert abs(ex.realized_pnl - (0.026 * 0.1)) < 1e-9
    ex.close_soft("A", "contrarian")                  # таймер сессии уже опоздал
    assert len(ex.fills) == 2                         # повторного филла нет


def test_stop_loss_closes_the_position_both_sides():
    ex = PaperExecutor()
    ex.set_price("L", 100.0)
    ex.enter_long("L", "contrarian", 0.1, 0.015, 0.025)
    ex.set_price("L", 98.4)                           # −1.6% < стоп −1.5%
    assert [f.kind for f in ex.fills] == ["open", "close_stop"]
    assert ex.realized_pnl < 0

    ex2 = PaperExecutor()
    ex2.set_price("S", 100.0)
    ex2.enter_short("S", "contrarian", 0.1, 0.015, 0.02)
    ex2.set_price("S", 101.6)                         # шорт против нас на 1.6%
    assert [f.kind for f in ex2.fills] == ["open", "close_stop"]
    assert ex2.realized_pnl < 0


def test_zero_levels_disable_the_check():
    """Уровни 0 = выключены (риск-тесты входят с 0/0) — позиция живёт до таймера."""
    ex = PaperExecutor()
    ex.set_price("A", 100.0)
    ex.enter_long("A", "contrarian", 0.1, 0, 0)
    ex.set_price("A", 300.0)
    assert "A" in ex.positions


def test_regime_role_contrarian():
    rm = RiskManager(PaperExecutor())
    rm.set_regime("trend_down")
    assert rm.regime_role("contrarian") == (True, 1.0)
    rm.set_regime("panic")
    assert rm.regime_role("contrarian") == (True, 0.5)
    rm.set_regime("trend_up")
    assert rm.regime_role("contrarian") == (True, 0.5)
    rm.set_regime("flat")
    assert rm.regime_role("contrarian") == (True, 1.0)


def test_approve_exposure_and_position_caps():
    ex = PaperExecutor()
    rm = RiskManager(ex, max_positions=2, max_exposure=0.30)
    for t in ("A", "B", "C"):
        ex.set_price(t, 100)
    assert rm.approve_for_event("A", "contrarian", "buy", 0.1, "e")
    ex.enter_long("A", "contrarian", 0.1, 0, 0)
    assert not rm.approve_for_event("A", "contrarian", "buy", 0.1, "e")   # already open
    assert rm.approve_for_event("B", "contrarian", "buy", 0.1, "e")
    ex.enter_long("B", "contrarian", 0.25, 0, 0)                          # exposure 0.35
    assert not rm.approve_for_event("C", "contrarian", "buy", 0.1, "e")   # exposure > 0.30


def test_halt_blocks_approval():
    ex = PaperExecutor()
    rm = RiskManager(ex, sigma_pnl=1.0)
    rm.halted = True
    assert not rm.approve_for_event("A", "contrarian", "buy", 0.01, "e")
