#!/usr/bin/env python3
"""
Fix conflicting absolute imports in grpcio-generated _pb2_grpc.py files.

grpcio-tools emits either:
  import grpc.tradeapi.v1.marketdata.X_pb2 as alias
or (newer versions):
  from grpc.tradeapi.v1.marketdata import X_pb2 as alias

Both conflict with: import grpc  (the grpcio package)

Replacement:  from . import X_pb2 as alias
(safe because _pb2_grpc.py only ever imports _pb2 from its own directory)
"""
import re
import sys
from pathlib import Path

# Pattern 1 (older grpcio-tools): import grpc.tradeapi.* as alias
_PATTERN_IMPORT = re.compile(
    r"^import (grpc\.tradeapi\.\S+) as (\S+)",
    flags=re.MULTILINE,
)

# Pattern 2 (newer grpcio-tools): from grpc.tradeapi.* import X_pb2 as alias
_PATTERN_FROM = re.compile(
    r"^from grpc\.tradeapi\.\S+ import (\S+) as (\S+)",
    flags=re.MULTILINE,
)


def fix_file(path: Path) -> bool:
    text = path.read_text()

    # Apply pattern 1
    new_text = _PATTERN_IMPORT.sub(
        lambda m: f"from . import {m.group(1).split('.')[-1]} as {m.group(2)}",
        text,
    )

    # Apply pattern 2
    new_text = _PATTERN_FROM.sub(
        lambda m: f"from . import {m.group(1)} as {m.group(2)}",
        new_text,
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
