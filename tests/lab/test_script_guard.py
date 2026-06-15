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
