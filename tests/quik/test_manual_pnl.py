"""Доходность ручной торговли: круги — деньги, открытое — отдельно, робот — мимо."""

import datetime
import json

from trader.quik import manual_pnl

PV = {"RIZ6": 1.0}          # ₽ за пункт, ровное число — проверяем арифметику, не курс
DAY = 1_790_150_000_000     # 23.09.2026, МСК


def _t(ts, side, qty, price, owner="smart", sec="RIZ6", num="1"):
    return {"num": num, "ts_ms": ts, "sec": sec, "side": side, "qty": qty,
            "price": price, "owner": owner, "tag": "stl-so-x" if owner == "smart" else ""}


def test_closed_round_is_money_and_open_tail_is_not():
    trades = [_t(DAY, "buy", 10, 85000), _t(DAY + 60_000, "sell", 10, 85500),
              _t(DAY + 120_000, "buy", 5, 85400)]
    out = manual_pnl.summarize(trades, PV, {"RIZ6": 85600.0})
    assert out["gross_rub"] == 5000.0          # 500 пунктов × 10 контрактов × 1 ₽
    assert out["commission_rub"] > 0 and out["net_rub"] < out["gross_rub"]
    assert out["open"] == [{"symbol": "RIZ6", "position": 5, "avg_price": 85400.0,
                            "last": 85600.0, "point_value": 1.0,
                            "unrealized_rub": 1000.0}]


def test_unknown_price_does_not_invent_a_revaluation():
    out = manual_pnl.summarize([_t(DAY, "buy", 5, 85400)], PV, {})
    assert out["open"][0]["unrealized_rub"] is None


def test_split_between_the_hand_and_the_smart_orders():
    trades = [_t(DAY, "buy", 10, 85000, owner="manual"),
              _t(DAY + 60_000, "sell", 10, 85500, owner="smart")]
    out = manual_pnl.summarize(trades, PV)
    src = {r["source"]: r for r in out["by_source"]}
    # Реализация приписана ЗАКРЫВАЮЩЕЙ сделке: её сделала умная заявка.
    assert src["smart"]["gross_rub"] == 5000.0 and src["manual"]["gross_rub"] == 0.0
    assert src["manual"]["lots"] == 10 and src["smart"]["lots"] == 10


def test_missing_point_value_is_flagged_not_silently_zero():
    out = manual_pnl.summarize([_t(DAY, "buy", 1, 85000),
                                _t(DAY + 1000, "sell", 1, 85100)], {})
    assert out["priced"] is False and out["gross_rub"] == 0.0
    assert out["by_symbol"][0]["realized_points"] == 100.0


def test_robot_fills_are_not_manual(tmp_path):
    day = "2026-09-23"
    (tmp_path / f"{day}.jsonl").write_text("\n".join(
        json.dumps(r, ensure_ascii=False) for r in [
            _t(DAY, "buy", 10, 85000, owner="robot"),
            _t(DAY + 1000, "sell", 10, 85500, owner="robot"),
            _t(DAY + 2000, "buy", 1, 85000, owner="manual", num="2"),
        ]), encoding="utf-8")
    rows = manual_pnl.read_trades([day], str(tmp_path))
    assert [r["owner"] for r in rows] == ["manual"]


def test_report_says_when_the_period_is_wider_than_the_data(tmp_path):
    day = "2026-09-23"
    (tmp_path / f"{day}.jsonl").write_text(
        json.dumps(_t(DAY, "buy", 1, 85000), ensure_ascii=False) + "\n", encoding="utf-8")
    now = datetime.datetime(2026, 9, 24, 12, 0, tzinfo=manual_pnl.MSK)
    month = manual_pnl.report("month", PV, {}, now=now, directory=str(tmp_path))
    assert month["partial"] is True and month["coverage_from"] == day
    assert month["from"] == "2026-08-26" and month["to"] == "2026-09-24"
    today = manual_pnl.report("day", PV, {}, now=now, directory=str(tmp_path))
    assert today["from"] == today["to"] == "2026-09-24" and today["fills"] == 0
