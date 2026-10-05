"""Фиксированный тест-гейт стратегий рабочего места (лежит в main; код из ветки не исполняется, кроме
самого файла стратегии). Воркер запускает его так: WB_STRATEGY_FILE=<путь> WB_STRATEGY_SYMBOL=...
WB_STRATEGY_PARAMS=<json> WB_WORKER_FILE=<scripts/workbench_worker.py> pytest <этот файл>.
Без WB_STRATEGY_FILE пропускается."""
import importlib.util
import json
import os
import sys

import pytest

F = os.environ.get("WB_STRATEGY_FILE")


@pytest.mark.skipif(not F, reason="WB_STRATEGY_FILE не задан")
@pytest.mark.parametrize("gate", ["import", "smoke", "no_lookahead"])
def test_wb_strategy(gate):
    spec = importlib.util.spec_from_file_location("wbw", os.environ["WB_WORKER_FILE"])
    w = importlib.util.module_from_spec(spec)
    sys.modules["wbw"] = w
    spec.loader.exec_module(w)
    r = w.probe(gate, F, os.environ["WB_STRATEGY_SYMBOL"], json.loads(os.environ["WB_STRATEGY_PARAMS"]))
    assert r["ok"] is True, r
