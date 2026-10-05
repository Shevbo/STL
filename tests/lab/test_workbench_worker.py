"""Воркер рабочего места: без сети, git и claude (транспорты подменены)."""
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "workbench_worker", Path(__file__).resolve().parents[2] / "scripts" / "workbench_worker.py")
ww = importlib.util.module_from_spec(_spec)
sys.modules["workbench_worker"] = ww
_spec.loader.exec_module(ww)

TOKEN = "tok-SECRET-0123456789abcdef"
GOOD_JSON = ('```json\n{"base_params": {"a": 1}, "param_sets": [{"a": 2}], "symbol": "RIZ6", '
             '"date_from": "2026-01-01", "date_to": "2026-02-01"}\n```')
ANSWER = "Добавил фильтр\nподробности\n" + GOOD_JSON
RAW_OK = ":100644 100644 aaaaaaa bbbbbbb M\ttrader/lab/strategies/wb/card_x.py\n"
PROBE_OK = json.dumps({k: {"ok": True} for k in ("script_guard", "import", "no_lookahead", "smoke")})


class FakeApi:
    def __init__(self, close_on_report=None, close_always=False):
        self.reports, self.beats = [], []
        self.close_on_report, self.close_always, self.n = close_on_report, close_always, 0

    def heartbeat(self, version, busy_with):
        self.beats.append((version, busy_with))

    def claim(self):
        return None

    def report(self, rid, **f):
        self.n += 1
        if self.close_always or self.close_on_report == self.n:
            raise ww.Closed()
        self.reports.append((rid, f))


class FakeSh:
    """run(cmd, cwd, timeout, env): сценарий по началу команды, всё записывается."""

    def __init__(self, raw=RAW_OK, ruff_rc=0, probe=PROBE_OK):
        self.calls, self.raw, self.ruff_rc, self.probe = [], raw, ruff_rc, probe

    def __call__(self, cmd, cwd=None, timeout=0, env=None):
        self.calls.append(list(cmd))
        assert env is None or "WORKBENCH_WORKER_TOKEN" not in env
        if cmd[:3] == ["git", "worktree", "add"]:
            Path(cmd[5]).mkdir(parents=True, exist_ok=True)
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "abc1234def\n"
        if cmd[:3] == ["git", "diff", "--cached"] and "--raw" in cmd:
            return 0, self.raw
        if cmd[:3] == ["git", "diff", "--cached"]:
            return 0, "diff --git a/x b/x\n+x\n"
        if "ruff" in cmd:
            return self.ruff_rc, "ruff out"
        if "--probe" in cmd:
            return 0, "шум\n" + self.probe + "\n"
        return 0, ""

    def pushed(self):
        return [c for c in self.calls if c[:2] == ["git", "push"]]


def make(tmp_path, api=None, sh=None, claude=None):
    cfg = ww.Config(api_base="http://x", token=TOKEN, repo=str(tmp_path / "repo"), worker_id="w1",
                    heartbeat_s=0.05)
    (tmp_path / "repo").mkdir(exist_ok=True)
    api = api or FakeApi()
    sh = sh or FakeSh()
    claude = claude or (lambda *a, **k: (True, ANSWER))
    return ww.Worker(cfg, api, run=sh, claude=claude, version="v1"), api, sh


def job(**kw):
    j = {"id": 7, "card": "card-x", "rev": 2, "parent": 1, "message": "добавь фильтр", "parent_code_ref": None,
         "card_ctx": {"strategy": "s", "symbol": "RIZ6", "date_from": "2026-01-01", "date_to": "2026-02-01",
                      "base_params": {"a": 1}, "script_code": "async def on_bar(stl, params):\n    pass\n",
                      "point_value": 1.0}}
    j.update(kw)
    return j


def last(api):
    return api.reports[-1][1]


def test_card_ctx_null_failed(tmp_path):
    w, api, sh = make(tmp_path)
    w.handle(job(card_ctx=None))
    assert last(api)["status"] == "failed" and "нет контекста карточки" in last(api)["log_append"]
    assert not sh.pushed()


def test_happy_path_gates_bool_and_push(tmp_path):
    w, api, sh = make(tmp_path)
    w.handle(job())
    f = last(api)
    assert f["status"] == "ready" and f["code_ref"] == "wb/card-x/2@abc1234def"
    assert list(f["gates"]) == list(ww.GATE_NAMES)
    assert all(g["ok"] is True for g in f["gates"].values())
    assert f["params"]["script_code"].startswith("async def on_bar") and f["params"]["symbol"] == "RIZ6"
    assert f["change_note"] == "Добавил фильтр"
    assert sh.pushed() == [["git", "push", "origin", "refs/heads/wb/card-x/2:refs/heads/wb/card-x/2"]]
    assert api.reports[0][1]["status"] == "gates"


def test_red_gate_failed_no_push(tmp_path):
    w, api, sh = make(tmp_path, sh=FakeSh(ruff_rc=1))
    w.handle(job())
    f = last(api)
    assert f["status"] == "failed" and f["gates"]["ruff"]["ok"] is False and not sh.pushed()


def test_path_policy_blocks_foreign_file(tmp_path):
    w, api, sh = make(tmp_path, sh=FakeSh(raw=RAW_OK + ":100644 100644 a b M\ttrader/lab/runtime.py\n"))
    w.handle(job())
    assert last(api)["status"] == "failed" and "разрешённые пути" in last(api)["log_append"]
    assert not sh.pushed()


def test_symlink_blocked():
    with pytest.raises(ww.Failed):
        ww.check_paths(":100644 120000 a b A\tx.py\n", {"x.py"})


def test_refspec_forbids_main():
    for bad in ("main", "wb/x", "wb/../main/1", "wb/x/1/../../main", "refs/heads/main", "wb/X/1"):
        with pytest.raises(ww.Failed):
            ww.push_refspec(bad)
    assert ww.push_refspec("wb/a-b/3") == "refs/heads/wb/a-b/3:refs/heads/wb/a-b/3"


def test_closed_on_report_stops_without_push(tmp_path):
    w, api, sh = make(tmp_path, api=FakeApi(close_on_report=1))  # первый report (gates) = 409 closed
    w.handle(job())
    assert not sh.pushed() and api.reports == []
    assert any(c[:4] == ["git", "worktree", "remove", "--force"] for c in sh.calls)


def test_cancel_during_model_kills_and_no_push(tmp_path):
    def claude(cwd, prompt, cancel, *a):
        assert cancel.wait(2)       # отмену выставляет heartbeat-поток
        raise ww.Closed()
    w, api, sh = make(tmp_path, api=FakeApi(close_always=True), claude=claude)
    th = threading.Thread(target=w.beat_loop, daemon=True)
    th.start()
    w.handle(job())
    w.stop.set()
    assert not sh.pushed()


def test_heartbeat_runs_while_model_works(tmp_path):
    def claude(*a):
        time.sleep(0.4)
        return True, ANSWER
    w, api, sh = make(tmp_path, claude=claude)
    th = threading.Thread(target=w.beat_loop, daemon=True)
    th.start()
    w.handle(job())
    w.stop.set()
    assert len(api.beats) >= 3 and (("v1", 7) in api.beats)


@pytest.mark.parametrize("answer", ["правка\nбез json", "правка\n```json\n{битый\n```",
                                    'п\n```json\n{"base_params": {}, "symbol": "X"}\n```'])
def test_bad_final_json_failed(tmp_path, answer):
    w, api, sh = make(tmp_path, claude=lambda *a: (True, answer))
    w.handle(job())
    assert last(api)["status"] == "failed" and not sh.pushed()


def test_need_shared_module_failed(tmp_path):
    w, api, sh = make(tmp_path, claude=lambda *a: (True, "NEED_SHARED_MODULE: library.py\n" + GOOD_JSON))
    w.handle(job())
    assert "передано окну backtests" in last(api)["log_append"] and not sh.pushed()


def test_secret_not_in_log(tmp_path, capsys):
    def claude(*a):
        return True, f"ответ с {TOKEN} и Bearer {TOKEN}\n" + GOOD_JSON
    w, api, sh = make(tmp_path, claude=claude, sh=FakeSh(ruff_rc=1))
    w.handle(job())
    blob = capsys.readouterr().out + json.dumps(api.reports, ensure_ascii=False)
    assert TOKEN not in blob and "***" in blob


def test_validate_run_limits():
    d = json.loads(GOOD_JSON.strip("`json\n"))
    assert ww.validate_run(d)["param_sets"] == [{"a": 2}]
    big = {**d, "params_grid": {"a": list(range(50)), "b": list(range(50))}}
    del big["param_sets"]
    with pytest.raises(ww.Failed):
        ww.validate_run(big)


def test_card_py_name():
    assert ww.card_py_name("ri-macd-1") == "ri_macd_1" and ww.card_py_name("1abc") == "c_1abc"


def test_probe_real_engine(tmp_path, monkeypatch):
    """Движок на синтетике: стратегия-эхо без заглядывания проходит, с заглядыванием в будущее нет."""
    pytest.importorskip("trader.lab.backtest")
    ok = tmp_path / "ok.py"
    ok.write_text(
        "async def on_bar(stl, params):\n"
        "    s = params['symbol']\n"
        "    bars = await stl.get_bars(s, 1, 5)\n"
        "    pos = await stl.get_position(s)\n"
        "    if len(bars) == 5 and bars[-1].close > bars[-2].close > bars[-3].close and pos.side != 'long':\n"
        "        await stl.place_order(s, 'buy', 1, bars[-1].close)\n"
        "    elif len(bars) == 5 and bars[-1].close < bars[-2].close and pos.side == 'long':\n"
        "        await stl.place_order(s, 'sell', pos.quantity, bars[-1].close)\n", encoding="utf-8")
    r = ww.probe(str(ok), "RIZ6", {"symbol": "RIZ6"})
    assert all(r[k]["ok"] is True for k in ("script_guard", "import", "smoke", "no_lookahead")), r
    bad = tmp_path / "bad.py"
    bad.write_text(ok.read_text().replace("get_bars(s, 1, 5)", "get_bars(s, 1, 5)") +
                   "    rt = stl\n    n = len(rt._bars)\n    if n % 3 == 0 and rt._cursor > 0 and pos.side == 'flat':\n"
                   "        await stl.place_order(s, 'buy', 1, 1.0)\n", encoding="utf-8")
    assert ww.probe(str(bad), "RIZ6", {"symbol": "RIZ6"})["no_lookahead"]["ok"] is False
