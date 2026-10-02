"""Уборка после торгового дня: дубль уходит, настоящая сделка остаётся."""
import json
from trader.quik import cleanup


def _write(p, rows):
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                 encoding="utf-8")


def _tr(num, day_ts=1, qty=1):
    return {"num": num, "ts_ms": day_ts, "sec": "RIZ6", "side": "buy",
            "qty": qty, "price": 85000, "tag": ""}


def test_duplicate_of_an_earlier_day_is_removed(tmp_path, monkeypatch):
    """ДАТА СДЕЛКИ У QUIK — ТОРГОВЫЙ ДЕНЬ, и вечерняя сессия датируется следующим:
    авто-восстановление вливает те же филлы во второй файл. 02.10.2026 так
    накопилось 143 повтора в дневном файле и 1311 по всей истории на 1288
    уникальных сделок — почти половина журнала."""
    d = tmp_path / "data" / "trades"
    d.mkdir(parents=True)
    _write(d / "2026-10-01.jsonl", [_tr("A"), _tr("B")])
    _write(d / "2026-10-02.jsonl", [_tr("B"), _tr("C")])   # B — вечер первого дня
    monkeypatch.chdir(tmp_path)
    assert cleanup.dedup_trades(apply=True) == 1
    day2 = [json.loads(x) for x in (d / "2026-10-02.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [t["num"] for t in day2] == ["C"], "повтор убран, своя сделка осталась"
    day1 = [json.loads(x) for x in (d / "2026-10-01.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert [t["num"] for t in day1] == ["A", "B"], "ПЕРВОЕ вхождение не трогаем"


def test_a_repeat_inside_one_file_is_removed_too(tmp_path, monkeypatch):
    d = tmp_path / "data" / "trades"
    d.mkdir(parents=True)
    _write(d / "2026-10-02.jsonl", [_tr("A"), _tr("A"), _tr("B")])
    monkeypatch.chdir(tmp_path)
    assert cleanup.dedup_trades(apply=True) == 1
    got = [json.loads(x)["num"] for x in (d / "2026-10-02.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert got == ["A", "B"]


def test_rows_without_a_number_and_broken_lines_survive(tmp_path, monkeypatch):
    """Опознать строку без номера нечем — не трогаем. Битую строку тоже: потерять
    запись о сделке дороже, чем оставить мусор."""
    d = tmp_path / "data" / "trades"
    d.mkdir(parents=True)
    p = d / "2026-10-02.jsonl"
    p.write_text(json.dumps(_tr("A")) + "\n{не json\n" + json.dumps({"sec": "RIZ6"}) + "\n"
                 + json.dumps(_tr("A")) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cleanup.dedup_trades(apply=True) == 1
    lines = [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 3, "убран только настоящий повтор"
    assert any("не json" in x for x in lines)


def test_without_apply_nothing_is_written(tmp_path, monkeypatch):
    d = tmp_path / "data" / "trades"
    d.mkdir(parents=True)
    p = d / "2026-10-02.jsonl"
    _write(p, [_tr("A"), _tr("A")])
    before = p.read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert cleanup.dedup_trades(apply=False) == 1
    assert p.read_text(encoding="utf-8") == before, "без --apply файл не меняется"


def test_a_file_that_grew_while_reading_is_skipped(tmp_path, monkeypatch):
    """Сторож пишет в файл на ходу. Перезапись вслепую потеряла бы свежие сделки —
    это дороже любого дубля, поэтому такой файл пропускается с сообщением."""
    d = tmp_path / "data" / "trades"
    d.mkdir(parents=True)
    p = d / "2026-10-02.jsonl"
    _write(p, [_tr("A"), _tr("A")])
    monkeypatch.chdir(tmp_path)
    real_size = cleanup.os.path.getsize
    calls = {"n": 0}

    def fake_size(path):
        calls["n"] += 1
        return real_size(path) + (999 if calls["n"] > 1 else 0)

    monkeypatch.setattr(cleanup.os.path, "getsize", fake_size)
    before = p.read_text(encoding="utf-8")
    cleanup.dedup_trades(apply=True)
    assert p.read_text(encoding="utf-8") == before, "растущий файл не перезаписан"


def test_book_is_not_touched_while_the_service_runs(tmp_path, monkeypatch):
    """Книга правится только при остановленной службе: процесс держит её в памяти
    и перезапишет файл своим состоянием на первом сохранении."""
    (tmp_path / "data").mkdir()
    book = tmp_path / "data" / "smart_orders.json"
    rows = [{"so_id": "a", "code": "RIU6", "status": "cancelled"},
            {"so_id": "b", "code": "RIZ6", "status": "armed"}]
    book.write_text(json.dumps({"orders": rows}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cleanup, "_service_running", lambda name="x": True)
    assert cleanup.archive_book(apply=True) == 0
    assert json.loads(book.read_text(encoding="utf-8"))["orders"] == rows


def test_book_archives_dead_contracts_when_the_service_is_down(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    book = tmp_path / "data" / "smart_orders.json"
    live = {"so_id": "b", "code": "RIZ6", "status": "armed"}
    dead = {"so_id": "a", "code": "RIU6", "status": "cancelled"}
    # живая заявка на ИСТЁКШЕМ контракте в архив НЕ уходит: это расхождение,
    # о котором надо знать, а не мусор
    live_dead_code = {"so_id": "c", "code": "RIU6", "status": "native"}
    book.write_text(json.dumps({"orders": [dead, live, live_dead_code]}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cleanup, "_service_running", lambda name="x": False)
    assert cleanup.archive_book(apply=True) == 1
    kept = json.loads(book.read_text(encoding="utf-8"))["orders"]
    assert [o["so_id"] for o in kept] == ["b", "c"]


# --------------------------------------------------------------------------
# КОРЕНЬ дублей: дедуп при старте читал ТОЛЬКО сегодняшний файл.
# --------------------------------------------------------------------------


def test_seen_spans_previous_days_not_only_today(tmp_path, monkeypatch):
    """ПОЧЕМУ ДУБЛИ ВОЗНИКАЛИ ВООБЩЕ. Кольцо сделок у агента держит 500 последних
    и переживает смену суток, а `_load_seen` читал только сегодняшний файл. После
    рестарта в новом дне старые сделки из кольца выглядели новыми и писались
    заново: 02.10.2026 так накопилось 1311 повторов на 1288 уникальных сделок, в
    одном этом дне 143 на шесть рестартов.
    """
    from trader.quik import truth

    d = tmp_path / "trades"
    d.mkdir()
    day_ms = 1790900000000                      # 02.10.2026
    yesterday = truth.journal_path(day_ms - 86_400_000, str(d))
    today = truth.journal_path(day_ms, str(d))
    with open(yesterday, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"num": "OLD1"}) + "\n")
        fh.write(json.dumps({"num": "OLD2"}) + "\n")
    with open(today, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"num": "NEW1"}) + "\n")

    seen = truth._load_seen(day_ms, str(d))
    assert seen == {"OLD1", "OLD2", "NEW1"}, (
        "вчерашние номера обязаны попасть в seen, иначе кольцо запишет их заново")

    # и сделка из кольца, уже лежащая во ВЧЕРАШНЕМ файле, второй раз не пишется
    status = {"quik": {"trades": [
        {"num": "OLD2", "ts_ms": day_ms - 3600_000, "sec": "RIZ6", "side": "buy",
         "qty": 1, "price": 85000},
        {"num": "NEW2", "ts_ms": day_ms, "sec": "RIZ6", "side": "sell",
         "qty": 1, "price": 85100},
    ]}}
    added = truth.append_trades(status, seen, day_ms, str(d))
    assert added == 1, "повтор из кольца не должен попасть в журнал"
    rows = [json.loads(x) for x in open(today, encoding="utf-8") if x.strip()]
    assert [r["num"] for r in rows] == ["NEW1", "NEW2"]
