#!/usr/bin/env python3
"""
Fix conflicting absolute imports in grpcio-generated _pb2_grpc.py files.

grpcio-tools emits: import grpc.tradeapi.v1.marketdata.X_pb2 as alias
which conflicts with: import grpc  (the grpcio package)

Replacement:  from . import X_pb2 as alias
(safe because _pb2_grpc.py only ever imports _pb2 from its own directory)
"""
import re
import sys
from pathlib import Path


def fix_file(path: Path) -> bool:
    text = path.read_text()
    new_text = re.sub(
        r"^import (grpc\.tradeapi\.\S+) as (\S+)",
        lambda m: f"from . import {m.group(1).split('.')[-1]} as {m.group(2)}",
        text,
        flags=re.MULTILINE,
    )
    if new_text != text:
        path.write_text(new_text)
        return True
    return False


if __name__ == "__main__":
    gen_root = Path(sys.argv[1])
    fixed = [p for p in gen_root.rglob("*_pb2_grpc.py") if fix_file(p)]
    for p in fixed:
        print(f"Fixed: {p}")
    print(f"Done — {len(fixed)} file(s) patched.")
