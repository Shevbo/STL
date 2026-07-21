import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "queue_campaign", pathlib.Path("scripts/queue_campaign.py"))
qc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qc)


def test_rejects_fast_ge_slow():
    assert qc.valid_macd_config({"fast": 12, "slow": 26}) is True
    assert qc.valid_macd_config({"fast": 26, "slow": 26}) is False   # fast==slow
    assert qc.valid_macd_config({"fast": 30, "slow": 26}) is False   # inverted
    assert qc.valid_macd_config({"signal": 9}) is True               # not a macd config
