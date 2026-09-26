"""Доходность ручной торговли: круги — деньги, открытое — отдельно, робот — мимо."""

import datetime
import json

from trader.quik import manual_pnl

PV = {"RIZ6": 1.0}          # ₽ за пункт, ровное число — проверяем арифметику, не курс
DAY = 1_790_150_000_000     # 23.09.2026, МСК


ROBOT = "lxk22tsffsxiiotb8kmp"
TAGS = {"smart": "stl-so-x", "manual": "", "robot": ROBOT, "broker": "}SЖЮqXдD"}


def _t(ts, side, qty, price, owner="smart", sec="RIZ6", num="1"):
    """Строка журнала сделок. `owner` здесь — роль, из которой берётся ТЕГ:
    канал считается из тега, как в бою, а не из записанного поля."""
    return {"num": num, "ts_ms": ts, "sec": sec, "side": side, "qty": qty,
            "price": price, "owner": owner, "tag": TAGS[owner],
            "channel": {"smart": "smart", "manual": "quik", "broker": "broker",
                        "robot": "robot"}[owner], "order_num": f"o{num}"}


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


def test_split_between_three_channels():
    trades = [_t(DAY, "buy", 10, 85000, owner="manual"),
              _t(DAY + 60_000, "sell", 10, 85500, owner="smart", num="2"),
              _t(DAY + 120_000, "buy", 2, 85300, owner="broker", num="3")]
    out = manual_pnl.summarize(trades, PV)
    ch = {r["channel"]: r for r in out["by_channel"]}
    # Реализация приписана ЗАКРЫВАЮЩЕЙ сделке: её сделала умная заявка.
    assert ch["smart"]["gross_rub"] == 5000.0 and ch["quik"]["gross_rub"] == 0.0
    assert ch["quik"]["lots"] == 10 and ch["broker"]["lots"] == 2
    assert all(r["orders"] == 1 for r in ch.values()) and out["orders"] == 3
    assert {r["source"] for r in out["by_source"]} == {"quik", "smart", "broker"}


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
    rows = manual_pnl.read_trades([day], str(tmp_path), {ROBOT})
    assert [r["channel"] for r in rows] == ["quik"]


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


def test_channel_is_recomputed_from_the_tag_not_trusted_from_the_row(tmp_path):
    """Строки, записанные до исправления классификации, несут прежний ответ:
    роботная сделка лежит с owner=manual. Тег — факт, owner — суждение."""
    day = "2026-09-23"
    stale = {**_t(DAY, "buy", 10, 85000, owner="robot"), "owner": "manual",
             "channel": "quik"}
    (tmp_path / f"{day}.jsonl").write_text(
        json.dumps(stale, ensure_ascii=False) + chr(10), encoding="utf-8")
    assert manual_pnl.read_trades([day], str(tmp_path), {ROBOT}) == []


def test_by_day_gives_the_bars_for_a_week():
    trades = [_t(DAY, "buy", 10, 85000), _t(DAY + 60_000, "sell", 10, 85500, num="2"),
              _t(DAY + 86_400_000, "buy", 10, 85000, num="3"),
              _t(DAY + 86_460_000, "sell", 10, 84900, num="4")]
    days = manual_pnl.summarize(trades, PV)["by_day"]
    assert [d["date"] for d in days] == ["2026-09-23", "2026-09-24"]
    assert days[0]["gross_rub"] == 5000.0 and days[1]["gross_rub"] == -1000.0
    assert all(d["net_rub"] < d["gross_rub"] for d in days)


def _ms(y, mo, d, h, mi=0):
    return int(datetime.datetime(y, mo, d, h, mi, tzinfo=manual_pnl.MSK).timestamp() * 1000)


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def test_day_starts_at_07_msk_and_yesterday_evening_stays_out(tmp_path):
    """QUIK датирует вечерку следующим торговым днём: сделки 24.09 вечером лежат в
    файле 25.09. Утром они показывались в «итоге за день» как сегодняшние."""
    _write(tmp_path / "2026-09-25.jsonl", [
        # вечерняя сессия 24.09, в файле 25.09 — круг на +1000 ₽, в окно НЕ входит
        _t(_ms(2026, 9, 24, 19, 0), "buy", 10, 85000, owner="manual"),
        _t(_ms(2026, 9, 24, 19, 30), "sell", 10, 85100, owner="manual", num="2"),
        # утро 25.09 — круг на −500 ₽, это и есть «день»
        _t(_ms(2026, 9, 25, 9, 30), "buy", 10, 85200, owner="manual", num="3"),
        _t(_ms(2026, 9, 25, 9, 40), "sell", 10, 85150, owner="manual", num="4"),
    ])
    now = datetime.datetime(2026, 9, 25, 10, 0, tzinfo=manual_pnl.MSK)
    out = manual_pnl.report("day", PV, {}, now=now, directory=str(tmp_path))
    assert out["fills"] == 2 and out["gross_rub"] == -500.0
    assert out["from_ms"] == _ms(2026, 9, 25, 7) and out["to_ms"] == _ms(2026, 9, 25, 10)
    assert out["from"] == "2026-09-25"


def test_day_includes_this_evening_filed_under_tomorrows_date(tmp_path):
    """Обратная сторона той же датировки: сегодняшний вечер лежит в файле ЗАВТРА.
    Читать только файл сегодняшней даты значит терять свежие сделки."""
    _write(tmp_path / "2026-09-26.jsonl", [
        _t(_ms(2026, 9, 25, 19, 0), "buy", 10, 85000, owner="manual"),
        _t(_ms(2026, 9, 25, 19, 10), "sell", 10, 85300, owner="manual", num="2"),
    ])
    now = datetime.datetime(2026, 9, 25, 20, 0, tzinfo=manual_pnl.MSK)
    out = manual_pnl.report("day", PV, {}, now=now, directory=str(tmp_path))
    assert out["fills"] == 2 and out["gross_rub"] == 3000.0


def test_night_before_seven_still_reports_the_previous_trading_day(tmp_path):
    _write(tmp_path / "2026-09-26.jsonl", [
        _t(_ms(2026, 9, 25, 19, 0), "buy", 10, 85000, owner="manual"),
        _t(_ms(2026, 9, 25, 19, 10), "sell", 10, 85300, owner="manual", num="2"),
    ])
    now = datetime.datetime(2026, 9, 26, 3, 0, tzinfo=manual_pnl.MSK)
    out = manual_pnl.report("day", PV, {}, now=now, directory=str(tmp_path))
    assert out["from_ms"] == _ms(2026, 9, 25, 7) and out["fills"] == 2


def test_week_keeps_calendar_days_and_still_names_its_bounds(tmp_path):
    now = datetime.datetime(2026, 9, 25, 10, 0, tzinfo=manual_pnl.MSK)
    out = manual_pnl.report("week", PV, {}, now=now, directory=str(tmp_path))
    assert out["from"] == "2026-09-19" and out["to"] == "2026-09-25"
    assert out["from_ms"] == _ms(2026, 9, 19, 0) and out["to_ms"] == _ms(2026, 9, 25, 10)


# ---- «открыто» = позиция СЧЁТА, а не остаток окна ----
# 26.09.2026 экран показал открытый шорт RIZ6 -4 со средней 85565.4167, когда на
# счёте был FLAT: вчерашний лонг закрывался внутри дня четырьмя продажами, и
# остаток окна оказался ровно -4.

def _closing_sells_of_a_position_opened_before_the_window():
    return [_t(DAY + i * 60_000, "sell", 1, 85565 + i, num=str(i)) for i in range(4)]


def test_open_shows_the_account_position_not_the_window_residual():
    trades = _closing_sells_of_a_position_opened_before_the_window()
    out = manual_pnl.summarize(trades, PV, {"RIZ6": 85600.0}, account_positions={})
    assert out["open"] == [] and out["open_source"] == "account"
    # Остаток окна не выброшен: по нему считается признак неполного журнала.
    assert [r["position"] for r in out["window_residual"]] == [-4]


def test_account_position_without_a_window_average_gives_null_not_a_made_up_price():
    """Позиция набрана до начала окна: средней у неё нет, и выдумывать нельзя."""
    out = manual_pnl.summarize([], PV, {"RIZ6": 85600.0},
                               account_positions={"RIZ6": -4})
    assert out["open"] == [{"symbol": "RIZ6", "position": -4, "avg_price": None,
                            "last": 85600.0, "point_value": 1.0,
                            "unrealized_rub": None}]


def test_window_average_of_the_opposite_side_is_not_borrowed():
    """На счёте лонг, в окне остался шорт: это средняя ЧУЖОЙ позиции."""
    out = manual_pnl.summarize(_closing_sells_of_a_position_opened_before_the_window(),
                               PV, {"RIZ6": 85600.0}, account_positions={"RIZ6": 2})
    assert out["open"][0]["avg_price"] is None
    assert out["open"][0]["unrealized_rub"] is None


def test_window_average_of_the_same_side_is_used():
    trades = [_t(DAY, "buy", 10, 85000), _t(DAY + 60_000, "sell", 10, 85500),
              _t(DAY + 120_000, "buy", 5, 85400, num="3")]
    out = manual_pnl.summarize(trades, PV, {"RIZ6": 85600.0},
                               account_positions={"RIZ6": 5})
    assert out["open"] == [{"symbol": "RIZ6", "position": 5, "avg_price": 85400.0,
                            "last": 85600.0, "point_value": 1.0,
                            "unrealized_rub": 1000.0}]


def test_unknown_account_position_says_so_instead_of_claiming_flat():
    out = manual_pnl.summarize(_closing_sells_of_a_position_opened_before_the_window(),
                               PV, {"RIZ6": 85600.0})
    assert out["open_source"] == "window" and out["open"] == out["window_residual"]


def test_journal_row_carries_the_order_number(tmp_path, monkeypatch):
    """Оператор сверяет ленту с терминалом ПО НОМЕРУ ЗАЯВКИ — без него строка
    ленты не сверяема ни с чем."""
    from trader.api import quik_manual
    from trader.quik import so_journal

    _write(tmp_path / "2026-09-25.jsonl", [_t(_ms(2026, 9, 25, 9, 30), "buy", 1, 85000,
                                              owner="manual", num="7")])
    monkeypatch.setattr(manual_pnl, "TRADES_DIR", str(tmp_path))
    monkeypatch.setattr(so_journal, "DIR", str(tmp_path / "events"))
    rows = quik_manual.feed_rows(["2026-09-25"], set())
    assert [r["order_num"] for r in rows if r["type"] == "trade"] == ["o7"]
