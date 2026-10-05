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

    def __init__(self, raw=RAW_OK, ruff_rc=0, probe_ok=True, unshare_rc=0):
        self.calls, self.raw, self.ruff_rc, self.probe_ok = [], raw, ruff_rc, probe_ok
        self.unshare_rc, self.status_out = unshare_rc, ""
        self.meta = []   # (сырая команда, cwd, timeout, env)

    @staticmethod
    def norm(cmd):
        """git без -c-флагов и без префикса unshare: сценарий смотрит на суть команды."""
        c = list(cmd)
        if c[:3] == ["unshare", "-rn", "--"] and len(c) > 3:
            c = c[3:]
        if c[:1] == ["git"]:
            i = 1
            while c[i:i + 1] == ["-c"] and c[i + 1].split("=")[0] in ("core.fsmonitor", "core.hooksPath"):
                i += 2
            c = ["git"] + c[i:]
        return c

    def __call__(self, cmd, cwd=None, timeout=0, env=None):
        self.meta.append((list(cmd), cwd, timeout, env))
        raw_cmd, cmd = list(cmd), self.norm(cmd)
        self.calls.append(cmd)
        assert env is None or "WORKBENCH_WORKER_TOKEN" not in env
        if raw_cmd[:1] == ["unshare"] and raw_cmd[-1] == "true":
            return self.unshare_rc, ""
        if cmd[:3] == ["git", "worktree", "add"]:
            Path(cmd[5]).mkdir(parents=True, exist_ok=True)
            (Path(cmd[5]) / ".git").write_bytes(b"gitdir: /x/.git/worktrees/card-x-2\n")
        if cmd[:2] == ["git", "status"]:
            return 0, self.status_out
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "abc1234def\n"
        if cmd[:2] == ["git", "show"]:
            return 0, "def test_x():\n    pass\n"
        if cmd[:3] == ["git", "diff", "--cached"] and "--raw" in cmd:
            return 0, self.raw
        if cmd[:3] == ["git", "diff", "--cached"]:
            return 0, "diff --git a/x b/x\n+x\n"
        if "ruff" in cmd:
            return self.ruff_rc, "ruff out"
        if "--probe" in cmd:
            return 0, "шум\n" + json.dumps({"ok": self.probe_ok}) + "\n"
        return 0, ""

    def pushed(self):
        return [c for c in self.calls if c[:2] == ["git", "push"]]

    def gate_runs(self):
        return [m for m in self.meta if "ruff" in m[0] or "pytest" in m[0] or "--probe" in m[0]]


def make(tmp_path, api=None, sh=None, claude=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (tmp_path / "worker.py").write_text("# worker\n", encoding="utf-8")
    cfg = ww.Config(api_base="http://x", token=TOKEN, repo=str(tmp_path / "repo"), worker_id="w1",
                    heartbeat_s=0.05, home=str(home), worker_file=str(tmp_path / "worker.py"))
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


def test_model_cannot_write_tests(tmp_path):
    raw = RAW_OK + ":000000 100644 0 b A\ttests/lab/wb/test_card_x.py\n"
    w, api, sh = make(tmp_path, sh=FakeSh(raw=raw))
    w.handle(job())
    assert last(api)["status"] == "failed" and "разрешённые пути" in last(api)["log_append"]
    assert not sh.pushed() and not sh.gate_runs()


def _rewrite(code):
    """Фейковая модель: переписывает файл стратегии в worktree."""
    def claude(cwd, *a):
        (Path(cwd) / "trader/lab/strategies/wb/card_x.py").write_text(code, encoding="utf-8")
        return True, ANSWER
    return claude


def test_script_guard_first_nothing_else_runs(tmp_path):
    w, api, sh = make(tmp_path, claude=_rewrite("import os\nasync def on_bar(stl, params):\n    pass\n"))
    w.handle(job())
    f = last(api)
    assert f["status"] == "failed" and f["gates"]["script_guard"]["ok"] is False
    assert [k for k, g in f["gates"].items() if g["ok"] is not False] == []
    assert list(f["gates"]) == list(ww.GATE_NAMES)
    assert not sh.gate_runs() and not sh.pushed()          # ни ruff, ни pytest, ни import кода модели


def test_pytest_gate_is_fixed_main_test_only(tmp_path):
    w, api, sh = make(tmp_path)
    w.handle(job())
    assert ["git", "show", "origin/main:tests/lab/test_wb_strategy_gate.py"] in sh.calls
    runs = [m for m in sh.meta if "pytest" in m[0]]
    assert len(runs) == 1
    cmd, cwd, _, env = runs[0]
    assert Path(cmd[cmd.index("pytest") + 1]).name == "test_wb_strategy_gate.py"
    assert "wb-gate-" in cmd[cmd.index("pytest") + 1] and str(cwd) not in cmd[cmd.index("pytest") + 1]
    assert env["WB_STRATEGY_FILE"].endswith("trader/lab/strategies/wb/card_x.py") or "card_x.py" in env["WB_STRATEGY_FILE"]
    assert not any("test_card_x" in " ".join(c) for c in sh.calls)


def test_gates_sandboxed_each_own_process_with_timeout(tmp_path, monkeypatch):
    for k in ("WORKBENCH_WORKER_TOKEN", "SSH_AUTH_SOCK", "ANTHROPIC_API_KEY", "HTTPS_PROXY", "GITHUB_TOKEN"):
        monkeypatch.setenv(k, "secret-" + k)
    w, api, sh = make(tmp_path)
    w.handle(job())
    runs = sh.gate_runs()
    probes = [m for m in runs if "--probe" in m[0]]
    assert sorted(m[0][m[0].index("--probe") + 1] for m in probes) == ["import", "no_lookahead", "smoke"]
    assert len(runs) == 5                                   # ruff + pytest + 3 probe, каждое отдельно
    wt = str(tmp_path / "stl-workbench-wt" / "card-x-2")
    for cmd, cwd, timeout, env in runs:
        assert cwd == wt and 0 < timeout <= 600
        assert set(env) <= {"PATH", "HOME", "TMPDIR", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "SYSTEMROOT",
                            "LANG", "WB_STRATEGY_FILE", "WB_STRATEGY_SYMBOL", "WB_STRATEGY_PARAMS",
                            "WB_WORKER_FILE"}
        assert env["HOME"] != str(Path.home()) and "wb-gate-" in env["HOME"]
        assert not any("secret-" in v for v in env.values())
    assert not Path(runs[0][3]["HOME"]).exists()            # временная папка убрана


def test_gate_test_file_in_main_runs_real_engine(tmp_path):
    import subprocess
    gate = Path(__file__).with_name("test_wb_strategy_gate.py")
    good = tmp_path / "good.py"
    good.write_text("async def on_bar(stl, params):\n    pass\n", encoding="utf-8")
    env = {**__import__("os").environ, "WB_STRATEGY_FILE": str(good), "WB_STRATEGY_SYMBOL": "RIZ6",
           "WB_STRATEGY_PARAMS": '{"symbol": "RIZ6"}', "WB_WORKER_FILE": str(Path(ww.__file__))}
    r = subprocess.run([sys.executable, "-m", "pytest", str(gate), "-q", "-p", "no:cacheprovider"],
                       env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-500:]
    good.write_text("import os\nasync def on_bar(stl, params):\n    pass\n", encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "pytest", str(gate), "-q", "-p", "no:cacheprovider"],
                       env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode != 0


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
    for gate in ("import", "smoke", "no_lookahead"):
        r = ww.probe(gate, str(ok), "RIZ6", {"symbol": "RIZ6"})
        assert r["ok"] is True, r
    bad = tmp_path / "bad.py"
    bad.write_text(ok.read_text().replace("get_bars(s, 1, 5)", "get_bars(s, 1, 5)") +
                   "    rt = stl\n    n = len(rt._bars)\n    if n % 3 == 0 and rt._cursor > 0 and pos.side == 'flat':\n"
                   "        await stl.place_order(s, 'buy', 1, 1.0)\n", encoding="utf-8")
    assert ww.probe("no_lookahead", str(bad), "RIZ6", {"symbol": "RIZ6"})["ok"] is False


# ── правки по проверке безопасности (fable) ──────────────────────────────────
def test_claude_cmd_no_bare_allowed_tools():
    cmd = ww.claude_cmd("/bin/claude", "p", "sonnet")
    assert "--allowedTools" not in cmd and "--allowed-tools" not in cmd
    assert cmd[cmd.index("--tools") + 1] == "Read,Edit,Write"
    assert cmd[cmd.index("--setting-sources") + 1] == "user" and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"


def test_probe_model_writes_home_file_caught(tmp_path):
    """«Модель» пишет ~/wb_probe: новый файл в HOME ловится снимком."""
    def claude(cwd, *a):
        (tmp_path / "home" / "wb_probe").write_text("x", encoding="utf-8")
        return True, ANSWER
    w, api, sh = make(tmp_path, claude=claude)
    w.handle(job())
    assert last(api)["status"] == "failed" and "ТРЕВОГА" in last(api)["log_append"]
    assert "ls:" in last(api)["log_append"] and not sh.pushed()


def test_probe_model_edits_worker_caught(tmp_path):
    def claude(cwd, *a):
        (tmp_path / "worker.py").write_text("# backdoor\n", encoding="utf-8")
        return True, ANSWER
    w, api, sh = make(tmp_path, claude=claude)
    w.handle(job())
    assert last(api)["status"] == "failed" and "ТРЕВОГА" in last(api)["log_append"] and "worker" in last(api)["log_append"]
    assert not sh.pushed() and not sh.gate_runs()


def test_model_edits_secret_files_and_clone_caught(tmp_path):
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    (tmp_path / "home" / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    holder = {}

    def claude(cwd, *a):
        (tmp_path / "home" / ".claude" / "settings.json").write_text('{"hooks": 1}', encoding="utf-8")
        holder["sh"].status_out = " M scripts/workbench_worker.py\n"
        return True, ANSWER
    w, api, sh = make(tmp_path, claude=claude)
    holder["sh"] = sh
    w.handle(job())
    log = last(api)["log_append"]
    assert last(api)["status"] == "failed" and ".claude/settings.json" in log and "clone_status" in log
    assert not sh.pushed()


def test_wt_dotgit_swap_caught(tmp_path):
    def claude(cwd, *a):
        (Path(cwd) / ".git").write_bytes(b"gitdir: /evil\n")
        return True, ANSWER
    w, api, sh = make(tmp_path, claude=claude)
    w.handle(job())
    assert last(api)["status"] == "failed" and "wt/.git подменён" in last(api)["log_append"]
    assert not sh.pushed()


def test_git_in_worktree_explicit_dirs_and_safe_flags(tmp_path):
    w, api, sh = make(tmp_path)
    w.handle(job())
    wt = str(tmp_path / "stl-workbench-wt" / "card-x-2")
    in_wt = [m for m in sh.meta if m[1] == wt and m[0][0] == "git"]
    assert in_wt
    for cmd, cwd, _, env in in_wt:
        assert env["GIT_DIR"] == "/x/.git/worktrees/card-x-2" and env["GIT_WORK_TREE"] == wt
    for cmd, *_ in [m for m in sh.meta if m[0][0] == "git"]:
        assert ["-c", "core.fsmonitor="] == cmd[1:3] and ["-c", "core.hooksPath=/dev/null"] == cmd[3:5], cmd


def test_ruff_with_dash_P(tmp_path):
    w, api, sh = make(tmp_path)
    w.handle(job())
    ruff = [m[0] for m in sh.meta if "ruff" in m[0]][0]
    assert ruff[1] == "-P"


def test_token_in_script_code_blocks_report_and_push(tmp_path):
    w, api, sh = make(tmp_path, claude=_rewrite(f"# {TOKEN}\nasync def on_bar(stl, params):\n    pass\n"))
    w.handle(job())
    f = last(api)
    assert f["status"] == "failed" and "params" not in f
    assert not sh.pushed()
    assert TOKEN not in json.dumps(api.reports, ensure_ascii=False)


def test_report_drops_field_with_token(tmp_path):
    w, api, sh = make(tmp_path)
    w._report(7, params={"script_code": TOKEN}, code_ref="x")
    rid, f = api.reports[-1]
    assert "params" not in f and f["status"] == "failed" and f["code_ref"] == "x"


def test_model_code_gates_wrapped_in_unshare(tmp_path):
    w, api, sh = make(tmp_path)
    w.handle(job())
    for cmd, *_ in sh.gate_runs():
        if "ruff" in cmd:
            assert cmd[0] != "unshare"
        else:
            assert cmd[:3] == ["unshare", "-rn", "--"], cmd


def test_no_unshare_means_model_code_gates_not_run(tmp_path):
    w, api, sh = make(tmp_path, sh=FakeSh(unshare_rc=1))
    w.handle(job())
    f = last(api)
    assert f["status"] == "failed" and not sh.pushed()
    for k in ("import", "pytest", "no_lookahead", "smoke"):
        assert f["gates"][k]["ok"] is False and "unshare" in f["gates"][k]["note"]
    assert [m for m in sh.gate_runs() if "ruff" not in m[0]] == []


def test_clone_updated_ff_only_before_job_and_dirty_fails(tmp_path):
    w, api, sh = make(tmp_path)
    w.handle(job())
    names = [c[:3] for c in sh.calls]
    assert ["git", "fetch", "origin"] in names
    i = sh.calls.index(["git", "merge", "--ff-only", "origin/main"])
    assert i < [c[:3] for c in sh.calls].index(["git", "worktree", "add"])
    dirty = FakeSh()
    dirty.status_out = " M x\n"
    w, api, sh = make(tmp_path, sh=dirty)
    w.handle(job())
    assert last(api)["status"] == "failed" and "грязный" in last(api)["log_append"]
    assert ["git", "worktree", "add"] not in [c[:3] for c in sh.calls]
