"""Почта окон: локальный слепок и хук.

Служба чинит ровно один отказ: письмо, пришедшее в СЕРЕДИНЕ сессии, окно не
видело часами (11.08.2026 — 7 и 30 часов при работающих окнах). Проверяем то,
из-за чего это чинилось так, а не иначе: хук не ходит в сеть, печатает новое
один раз, при пустом ящике молчит, а протухший синк не выдаёт за пустой ящик.
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


@pytest.fixture
def mail(tmp_path, monkeypatch):
    monkeypatch.setenv("STL_DEVMAIL_ROOT", str(tmp_path))
    import devmail_spool
    import devmail_hook
    importlib.reload(devmail_spool)
    importlib.reload(devmail_hook)
    return devmail_spool, devmail_hook


def _inbox(*msgs, board=None, prompt="ты окно"):
    return {"agent": "real-trade", "count": len(msgs), "messages": list(msgs),
            "prompt": prompt, "board": board or []}


def _msg(mid="1786:aa", frm="backtests", topic="прогон", body="готово", **kw):
    m = {"id": mid, "from": frm, "topic": topic, "body": body, "age_h": 0.1}
    m.update(kw)
    return m


def test_empty_box_prints_nothing(mail):
    spool, hook = mail
    spool.save_state("real-trade", _inbox())
    assert hook.build_output("real-trade", spool.load_state("real-trade"), "UserPromptSubmit") == ""


def test_new_message_printed_in_full_once(mail):
    spool, hook = mail
    spool.save_state("real-trade", _inbox(_msg(body="прогон 42 готов")))
    st = spool.load_state("real-trade")
    first = hook.build_output("real-trade", st, "UserPromptSubmit")
    assert "прогон 42 готов" in first and "1786:aa" in first
    # второй ввод оператора: то же письмо целиком НЕ печатаем
    second = hook.build_output("real-trade", st, "UserPromptSubmit")
    assert "прогон 42 готов" not in second


def test_pending_reminder_is_one_line_and_rate_limited(mail):
    spool, hook = mail
    spool.save_state("real-trade", _inbox(_msg()))
    st = spool.load_state("real-trade")
    hook.build_output("real-trade", st, "UserPromptSubmit")          # показали
    nag = hook.build_output("real-trade", st, "SessionStart")        # напоминание
    assert nag.count("\n") == 0 and "ждут ack" in nag
    assert hook.build_output("real-trade", st, "UserPromptSubmit") == ""  # не долбим


def test_stale_sync_is_not_reported_as_empty_box(mail):
    spool, hook = mail
    spool.save_state("real-trade", _inbox(_msg()))
    p = spool.state_file("real-trade")
    d = json.loads(p.read_text(encoding="utf-8"))
    d["updated_ms"] = int((time.time() - spool.STALE_SYNC_S - 60) * 1000)
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    out = hook.build_output("real-trade", spool.load_state("real-trade"), "SessionStart")
    assert "НЕ СИНХРОНИЗИРУЕТСЯ" in out


def test_missing_state_warns_not_silently_ok(mail):
    spool, hook = mail
    out = hook.build_output("real-trade", None, "SessionStart")
    assert "НЕ СИНХРОНИЗИРУЕТСЯ" in out


def test_foreign_stale_box_visible_to_working_window(mail):
    spool, hook = mail
    board = [{"agent": "ui-ux", "unread": 3, "oldest_age_h": 9.4, "stale": True}]
    spool.save_state("real-trade", _inbox(board=board))
    out = hook.build_output("real-trade", spool.load_state("real-trade"), "SessionStart")
    assert "ui-ux" in out and "скажи оператору" in out


def test_prompt_only_on_session_start(mail):
    """Микропромпт нужен свежей сессии, а на каждом вводе это чистый расход."""
    spool, hook = mail
    spool.save_state("real-trade", _inbox(_msg(), prompt="ТЫ ОКНО REAL-TRADE"))
    st = spool.load_state("real-trade")
    assert "ТЫ ОКНО REAL-TRADE" not in hook.build_output("real-trade", st, "UserPromptSubmit")
    spool.mark_shown("real-trade", [])
    (spool.ROOT / "real-trade.shown.json").write_text("[]", encoding="utf-8")
    assert "ТЫ ОКНО REAL-TRADE" in hook.build_output("real-trade", st, "SessionStart")


def test_hook_never_touches_network(mail):
    """Гарантия дешевизны хука: он вообще ничего не импортирует из сети.

    Проверяем импорты по AST, а не подстрокой: слово ssh законно встречается в
    комментарии про то, почему сети здесь нет.
    """
    import ast
    src = (Path(__file__).resolve().parents[2] / "scripts" / "devmail_hook.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & {"subprocess", "urllib", "httpx", "requests", "socket", "http"})
