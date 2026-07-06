import json

from robot_runner.export_docs import build_docs


def test_build_docs_contains_fvg_with_doc_and_params():
    docs = build_docs()
    assert "fvg" in docs
    fvg = docs["fvg"]
    assert isinstance(fvg["title"], str) and fvg["title"]
    assert isinstance(fvg["doc"], str) and fvg["doc"]
    assert isinstance(fvg["params"], dict) and fvg["params"]
    # a known default param from the registry (trader/lab/strategies/library.py)
    assert fvg["params"]["min_frac"] == 5
    assert fvg["params"]["qty"] == 1


def test_build_docs_covers_every_registered_strategy():
    from trader.lab.strategies.library import REGISTRY

    docs = build_docs()
    assert set(docs.keys()) == set(REGISTRY.keys())
    for rid, entry in docs.items():
        assert set(entry.keys()) == {"title", "doc", "params"}


def test_build_docs_round_trips_through_json_file(tmp_path):
    out = tmp_path / "strategies_doc.json"
    docs = build_docs()
    out.write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")

    reloaded = json.loads(out.read_text(encoding="utf-8"))
    assert reloaded == docs
    assert "fvg" in reloaded
