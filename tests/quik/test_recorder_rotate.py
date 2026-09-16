"""Ротация архива рынка: сжатие атомарно, хвосты умершего процесса подбираются.

16.09.2026 в архиве нашлись два следа падений: reply-2026-08-12.jsonl.gz обрезан на
середине сжатия (нечитаем целиком), а весь 14.08 остался лежать текстом на 88 МБ -
процесс умер на границе суток, и ротация про эти файлы больше не знала. earlyoom на
хостере убивает uvicorn по SIGKILL, так что «умер на середине» здесь обычное дело.
"""
import gzip
import os

from trader.quik.recorder import MarketRecorder


def _rec(tmp_path):
    r = MarketRecorder(str(tmp_path))
    assert r.enabled
    return r


def test_compress_is_atomic_and_leaves_no_tmp(tmp_path):
    src = tmp_path / "tick-2026-08-12.jsonl"
    src.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
    _rec(tmp_path)._compress(str(src))
    gz = tmp_path / "tick-2026-08-12.jsonl.gz"
    assert gz.exists() and not src.exists()
    assert not list(tmp_path.glob("*.tmp"))
    with gzip.open(gz, "rt", encoding="utf-8") as f:
        assert len(f.readlines()) == 2


def test_sweep_compresses_old_days_and_keeps_today(tmp_path):
    r = _rec(tmp_path)
    today = r._utc_day()
    (tmp_path / "book-2026-08-14.jsonl").write_text('{"b":1}\n', encoding="utf-8")
    (tmp_path / "tick-2026-08-14.jsonl").write_text('{"t":1}\n', encoding="utf-8")
    (tmp_path / f"tick-{today}.jsonl").write_text('{"t":2}\n', encoding="utf-8")

    assert r.sweep_leftovers() == 2
    assert (tmp_path / "book-2026-08-14.jsonl.gz").exists()
    assert (tmp_path / "tick-2026-08-14.jsonl.gz").exists()
    assert (tmp_path / f"tick-{today}.jsonl").exists()          # сегодняшний не трогаем
    assert not (tmp_path / f"tick-{today}.jsonl.gz").exists()


def test_truncated_gz_from_a_dead_process_is_rewritten(tmp_path):
    # ровно случай reply-2026-08-12: исходник цел, .gz обрезан на середине
    src = tmp_path / "reply-2026-08-12.jsonl"
    src.write_text('{"r":1}\n{"r":2}\n{"r":3}\n', encoding="utf-8")
    gz = tmp_path / "reply-2026-08-12.jsonl.gz"
    gz.write_bytes(gzip.compress(b'{"r":1}\n')[:12])            # обрубок
    try:
        with gzip.open(gz, "rb") as f:
            f.read()
        raise AssertionError("подготовленный файл обязан быть битым")
    except Exception:
        pass

    assert _rec(tmp_path).sweep_leftovers() == 1
    with gzip.open(gz, "rt", encoding="utf-8") as f:
        assert len(f.readlines()) == 3                          # архив снова целый
    assert not src.exists()


def test_disabled_recorder_sweeps_nothing(tmp_path):
    os.environ.pop("QUIK_RECORD_DIR", None)
    assert MarketRecorder("").sweep_leftovers() == 0
