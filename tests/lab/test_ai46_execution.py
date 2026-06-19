"""Tests for the team-46 paper execution + risk."""
from trader.lab.ai46.execution import PaperExecutor, RiskManager


def test_paper_long_profit():
    ex = PaperExecutor()
    ex.set_price("RIU6", 100.0)
    ex.enter_long("RIU6", "contrarian", 0.1, 0.015, 0.025)
    ex.set_price("RIU6", 110.0)
    ex.close_soft("RIU6", "contrarian")
    assert ex.realized_pnl > 0                       # +10% × 0.1 size
    assert [f.kind for f in ex.fills] == ["open", "close_soft"]
    assert abs(ex.realized_pnl - (0.10 * 0.1)) < 1e-9


def test_paper_short_profit_on_drop():
    ex = PaperExecutor()
    ex.set_price("X", 100.0)
    ex.enter_short("X", "contrarian", 0.1, 0.015, 0.02)
    ex.set_price("X", 90.0)
    ex.close_hard("X", "contrarian")
    assert ex.realized_pnl > 0
    assert "X" not in ex.positions


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


def test_cusum_halt_blocks_approval():
    ex = PaperExecutor()
    rm = RiskManager(ex, sigma_pnl=1.0)   # k=0.5, h=5
    for _ in range(20):
        rm.record_pnl(-3.0, 0.0)          # sustained loss drift → halt
    assert rm.halted
    assert not rm.approve_for_event("A", "contrarian", "buy", 0.01, "e")
