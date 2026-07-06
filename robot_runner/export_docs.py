"""Export strategies_doc.json for the agent's local status/showcase page.

The Go agent (quik_agent/internal/status/strategies.go) serves this file's
content verbatim from GET /strategy/{id} — it only cares about the file being
a JSON object keyed by strategy id. The schema per id is:
    {"title": str, "doc": str, "params": {name: default}}

Single source of truth is trader/lab/strategies/library.py's REGISTRY (the
same registry the backtester, the STL API and the runner's on_bar all read),
so this file can never drift from what a strategy actually does.

title: STRATEGY_DESC has no separate "title" field (its value is the full
doc), so title = REGISTRY[rid]["name"] (the human display name already used
by list_strategies() for the API/UI, e.g. "Fair Value Gap (ICT)").
doc: the full STRATEGY_DESC entry (may be Russian or English), passed through
verbatim. Falls back to the empty string if a strategy has no prose yet (all
registered strategies have one today).
params: REGISTRY[rid]["default_params"] (key -> default), the same dict
list_strategies() exposes as "default_params" via the API.
"""
from __future__ import annotations

import json
import sys

from trader.lab.strategies.library import REGISTRY, STRATEGY_DESC


def build_docs() -> dict:
    """id -> {"title": str, "doc": str, "params": {name: default}}, one entry
    per strategy in REGISTRY (the backtester/runner/API's shared registry)."""
    docs: dict[str, dict] = {}
    for rid, spec in REGISTRY.items():
        docs[rid] = {
            "title": spec["name"],
            "doc": STRATEGY_DESC.get(rid, ""),
            "params": dict(spec["default_params"]),
        }
    return docs


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m robot_runner.export_docs <out.json>", file=sys.stderr)
        return 2
    out_path = argv[1]
    docs = build_docs()
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(docs)} strategies -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
