"""Портфельный учёт team-46: разметка филлов и сводка в процентах."""
from trader.lab.ai46 import portfolio as PF


def test_demo_selfcheck():
    PF.demo()


def test_parse_meta_rejects_foreign_ids():
    assert PF.parse_meta("rr:bot-1:7:abcdef") is None
    assert PF.parse_meta("so:12") is None
    assert PF.parse_meta(None) is None
    assert PF.is_portfolio_fill("ai46") is True
    assert PF.is_portfolio_fill("ai46:0:open:300") is True


def test_short_loss_and_weight():
    # шорт 100 -> откуп 102 = -2% инструмента, вес 3% -> -0.06% портфеля
    trades = [
        {"time": 1, "symbol": "RIU6", "side": "sell", "price": 100, "order_id": "ai46:0:open:300"},
        {"time": 2, "symbol": "RIU6", "side": "buy", "price": 102, "order_id": "ai46:1:close_hard:300"},
    ]
    s = PF.enrich(trades)
    assert abs(trades[1]["ret_pct"] + 2.0) < 1e-9
    assert abs(trades[1]["port_pct"] + 0.06) < 1e-9
    assert s["closes"] == 1 and s["wins"] == 0 and s["win_rate"] == 0.0
    assert trades[1]["kind"] == "close_hard"


def test_positions_are_independent_per_instrument():
    # два инструмента в позиции ОДНОВРЕМЕННО — их филлы не должны спариваться крест-накрест
    trades = [
        {"time": 1, "symbol": "SiU6", "side": "buy", "price": 80000, "order_id": "ai46:0:open:100"},
        {"time": 2, "symbol": "BRU6", "side": "sell", "price": 70, "order_id": "ai46:1:open:100"},
        {"time": 3, "symbol": "SiU6", "side": "sell", "price": 80800, "order_id": "ai46:2:close_soft:100"},
        {"time": 4, "symbol": "BRU6", "side": "buy", "price": 69.3, "order_id": "ai46:3:close_soft:100"},
    ]
    s = PF.enrich(trades)
    assert abs(trades[2]["ret_pct"] - 1.0) < 1e-9      # SiU6 лонг +1%
    assert abs(trades[3]["ret_pct"] - 1.0) < 1e-9      # BRU6 шорт +1%
    assert s["closes"] == 2 and s["orphans"] == 0
    assert [x["symbol"] for x in s["by_symbol"]] in (["SiU6", "BRU6"], ["BRU6", "SiU6"])


def test_annualized_needs_three_days():
    day = 86400
    trades = [
        {"time": 0, "symbol": "SiU6", "side": "buy", "price": 100, "order_id": "ai46:0:open:100"},
        {"time": day, "symbol": "SiU6", "side": "sell", "price": 101, "order_id": "ai46:1:close_soft:100"},
    ]
    assert PF.enrich(trades)["ann_pct"] is None        # 1 день истории — не считаем
    trades[1]["time"] = 10 * day
    assert PF.enrich(trades)["ann_pct"] is not None
