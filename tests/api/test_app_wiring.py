"""Проверка ВЫЗОВОВ в lifespan, а не только классов, которые они собирают.

01.10.2026 параметр `interface` попал в вызов PositionsClient, который такого
аргумента не принимает, — объявлен он был у WsHub. Старт падал с TypeError,
systemd крутил рестарт по кругу, STL лежал 3 минуты 40 секунд в торговое время.
Живая торговля не прерывалась (роботы исполняются на агенте), потеря была в
наблюдении.

Тридцать четыре теста WsHub при этом были ЗЕЛЁНЫЕ: они проверяют сам класс, а
падал его ВЫЗОВ в app.py, которого тесты не касались. Отсюда этот файл: он
читает app.py как текст программы и сверяет каждый именованный аргумент с
настоящей подписью того, кого зовут.

Почему не «запустить lifespan целиком»: он поднимает пул БД, gRPC-сервер и
потоки котировок, и в тестах это стенд, а не проверка. Разбор вызова ловит
ровно тот класс ошибки, который случился, и ничего не поднимает.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from trader.api.ws_hub import WsHub
from trader.pos.client import PositionsClient

APP = pathlib.Path(inspect.getfile(WsHub)).parent / "app.py"

# Кого сверяем. Список намеренно короткий: это конструкторы, которые собираются
# в lifespan из общего набора переменных (base_url, get_token, account_id), и
# именно их легко перепутать между собой — что и случилось.
WATCHED = {"WsHub": WsHub, "PositionsClient": PositionsClient}


def _calls(name: str) -> list[ast.Call]:
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name]


@pytest.mark.parametrize("name", sorted(WATCHED))
def test_every_keyword_in_app_py_exists_in_the_callee(name: str):
    params = inspect.signature(WATCHED[name]).parameters
    has_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    calls = _calls(name)
    assert calls, f"{name} в app.py не вызывается — проверка потеряла предмет"
    for call in calls:
        for kw in call.keywords:
            if kw.arg is None or has_kwargs:        # **kwargs — сверять нечего
                continue
            assert kw.arg in params, (
                f"app.py:{call.lineno}: {name}(...) получает «{kw.arg}», "
                f"которого у него нет. Так 01.10.2026 упал старт STL."
            )


def test_the_poll_gate_is_actually_wired_to_the_switch():
    """Гейт парковки должен приехать ИМЕННО в WsHub, а не просто быть в файле.

    Опрос позиций живёт в WsHub; параметр, оставленный у соседнего конструктора,
    выглядит как сделанная работа и не гасит ни одного запроса.
    """
    kw = {k.arg for call in _calls("WsHub") for k in call.keywords}
    assert "interface" in kw
