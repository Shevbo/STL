"""Добор дыры в журнале алготорговли: дедуп, позиция после добора, чужие сделки.

Дедуп тут не украшение: 24.07.2026 два бэкфилла записали один филл дважды, реплей
прочёл копию как долив и роли поехали OPEN->AVR. Поэтому повторный запуск обязан
быть пустым.
"""
import calendar
import importlib.util
import json
import pathlib
import time

_SPEC = importlib.util.spec_from_file_location(
    "backfill_ledger_gap",
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "backfill_ledger_gap.py")
bg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bg)

RID = "lxk22tsffsxiiotb8kmpsato"
PV = 1.5  # RI: шаг 10, цена шага 15


def _journal(num, ts_ms, side, qty, price, tag=RID[:20], sec="RIZ6"):
    return {"num": str(num), "ts_ms": ts_ms, "sec": sec, "side": side, "qty": qty,
            "price": price, "order_num": "o1", "tag": tag, "owner": "robot"}


def _ledgered(seq, ts_ms, side, qty, price, pos_after, avg_after, key=None):
    """Строка algo_trades в том виде, в каком её отдаёт SELECT в скрипте."""
    return {"seq": seq, "robot_id": RID, "mode": "real", "ts_ms": ts_ms,
            "trade_num": str(seq), "order_num": "o1", "symbol": "RIZ6", "side": side,
            "qty": qty, "price": price, "order_kind": "market", "point_value": PV,
            "pnl_gross_rub": 0.0, "commission_rub": 0.0, "pnl_net_rub": 0.0,
            "pos_after": pos_after, "avg_after": avg_after,
            "dedup_key": key or f"q:{seq}"}


def test_gap_fills_added_and_position_repaired():
    # Робот стоял в шорте -2 @85000 (до дыры), в дыре долил -3, после дыры закрылся
    # шестью покупками. Живой журнал знает только вход и выход: позиция после
    # закрытия у него +1 вместо нуля — ровно та ложь, которую чинит добор.
    ledger = [_ledgered(1, 1_000_000, "sell", 2, 85000.0, -2, 85000.0),
              _ledgered(2, 9_000_000, "buy", 5, 84000.0, 3, 84000.0)]
    gap = [_journal(77, 5_000_000, "sell", 3, 84500.0)]
    ins, upd, notes, final = bg.plan_robot(RID, gap, ledger, PV)

    assert [r["dedup_key"] for r in ins] == ["q:77"]
    assert ins[0]["pos_after"] == -5          # -2 долив -3
    assert final[0] == 0                       # закрытие теперь сходится в ноль
    assert [u["seq"] for u in upd] == [2]      # хвост пересчитан
    assert upd[0]["pos_after"] == 0
    # Выход из шорта -5 @84700 (средняя) по 84000 — прибыль, а не убыток от +1.
    assert upd[0]["pnl_gross_rub"] > 0


def test_second_run_adds_nothing():
    """Идемпотентность: ключ добора тот же `q:<номер>`, что у живого ингеста."""
    ledger = [_ledgered(1, 1_000_000, "sell", 2, 85000.0, -2, 85000.0)]
    gap = [_journal(77, 5_000_000, "sell", 3, 84500.0)]
    ins, upd, _, _ = bg.plan_robot(RID, gap, ledger, PV)
    assert len(ins) == 1

    ledgered_now = ledger + [_ledgered(2, 5_000_000, "sell", 3, 84500.0, -5, 84700.0,
                                       key="q:77")]
    ins2, upd2, _, final2 = bg.plan_robot(RID, gap, ledgered_now, PV)
    assert (ins2, upd2) == ([], [])
    assert final2[0] == -5


def test_same_fill_under_foreign_key_not_duplicated():
    # Тот же филл, уже лежащий под ключом прошлого бэкфилла (`bf:`): UNIQUE его не
    # видит, ловит только drop_already_ledgered.
    ledger = [_ledgered(1, 1_000_000, "sell", 2, 85000.0, -2, 85000.0),
              _ledgered(2, 5_000_500, "sell", 3, 84500.0, -5, 84700.0,
                        key=f"bf:{RID}:o1:5000500:sell:3:84500")]
    ins, upd, notes, _ = bg.plan_robot(
        RID, [_journal(77, 5_000_000, "sell", 3, 84500.0)], ledger, PV)
    assert ins == [] and upd == []
    assert notes and "другим ключом" in notes[0]


def test_foreign_trades_never_enter():
    """Ручные сделки оператора и умные заявки тегом робота не совпадают."""
    journal = [_journal(1, 5_000_000, "buy", 9, 85000.0, tag="}SЖЮqXдD"),
               _journal(2, 5_000_100, "buy", 20, 84640.0, tag="stl-so-812e6bff1a"),
               _journal(3, 5_000_200, "sell", 1, 84500.0, tag=RID[:20])]
    by_tag = {RID[:20]: RID}
    mine = [t for t in journal if by_tag.get(t["tag"])]
    assert [t["num"] for t in mine] == ["3"]
    ins, _, _, _ = bg.plan_robot(
        RID, mine, [_ledgered(1, 1_000_000, "sell", 2, 85000.0, -2, 85000.0)], PV)
    assert [r["qty"] for r in ins] == [1]


def test_gap_older_than_history_is_refused():
    # Позиция на начало дыры неизвестна — угадывать её в денежном журнале нельзя.
    ledger = [_ledgered(1, 9_000_000, "buy", 2, 85000.0, 2, 85000.0)]
    ins, upd, notes, _ = bg.plan_robot(
        RID, [_journal(77, 5_000_000, "sell", 3, 84500.0)], ledger, PV)
    assert ins == [] and upd == []
    assert notes and "пропуск" in notes[-1]


def test_read_journal_window_and_dedup(tmp_path):
    """Сделка лежит и в файле своего дня, и в следующем (перезапись хвоста ринга в
    полночь) — в план она обязана попасть один раз, и только из окна."""
    early = json.dumps(_journal(1, 1_790_332_000_000, "sell", 1, 85000.0))
    inside = json.dumps(_journal(2, 1_790_340_000_000, "sell", 2, 84500.0))
    (tmp_path / "2026-09-25.jsonl").write_text(f"{early}\n{inside}\n", encoding="utf-8")
    (tmp_path / "2026-09-26.jsonl").write_text(f"{inside}\n", encoding="utf-8")
    rows = bg.read_journal(1_790_335_000_000, 1_790_345_000_000, str(tmp_path))
    assert [r["num"] for r in rows] == ["2"]


def test_msk_ms_reads_operator_time():
    # Оператор задаёт окно в МСК; в БД время UTC — смещение обязано быть ровно 3 ч.
    assert bg.msk_str(bg.msk_ms("2026-09-25 13:00")) == "2026-09-25 13:00:00"
    assert bg.msk_ms("2026-09-25 13:00") - bg.msk_ms("2026-09-25") == 13 * 3600 * 1000
    assert bg.msk_ms("2026-09-25 13:00") == (
        calendar.timegm(time.strptime("2026-09-25 10:00", "%Y-%m-%d %H:%M")) * 1000)
