import pytest

from trader.lab.script_guard import ScriptValidationError, validate_script

LEGIT = """
from trader.lab.indicators import ema
from trader.lab.runtime import STLRuntime


async def on_bar(stl, params):
    bars = await stl.get_bars(params["symbol"], tf=5, n=30)
    closes = [b.close for b in bars]
    fast = ema(closes, 9)
    if fast[-1] > fast[-2]:
        await stl.place_order(params["symbol"], "buy", 1, bars[-1].close)
"""

LEGIT_LIBRARY = """
from trader.lab.strategies.library import make_on_bar
on_bar = make_on_bar('rsi_trend')
"""


def test_legit_strategy_passes():
    validate_script(LEGIT)


def test_legit_library_passes():
    validate_script(LEGIT_LIBRARY)


@pytest.mark.parametrize("code", [
    "import os",
    "import subprocess",
    "import sys",
    "from os import environ",
    "import socket",
    "open('/etc/passwd')",
    "__import__('os')",
    "x = ().__class__.__bases__",
    "eval('1+1')",
    "exec('y=1')",
    "data = ().__class__.__subclasses__()",
    "import trader.api.app",
    "from trader.config import Settings",
])
def test_exploits_rejected(code):
    with pytest.raises(ScriptValidationError):
        validate_script(code)


def test_syntax_error_rejected():
    with pytest.raises(ScriptValidationError):
        validate_script("def (:")


@pytest.mark.parametrize("code", [
    "from trader.lab.backtest import os",
    "from trader.lab.iss_loader import httpx",
    "from trader.lab.scheduler import _os",
    "from trader.lab.runtime import asyncio",
    "import trader.lab.backtest\nx = trader.lab.backtest.os",
    "x = foo.subprocess",
    "from typing import get_type_hints",
    "import typing\ntyping.get_type_hints(f)",
    "from typing import ForwardRef",
    "from functools import singledispatch",
    "import typing\ntyping._eval_type",
    "def f(x: \"__import__('os')\"): pass",
    "def f() -> \"import os\": pass",
    "y: \"__import__('os')\" = 1",
])
def test_bypasses_rejected(code):
    with pytest.raises(ScriptValidationError):
        validate_script(code)


def test_legit_fields_pass():
    validate_script("from trader.lab.indicators import ema\nx = bar.time + p.random\n"
                    "def f(a: 'float') -> 'int': return 1\n")
