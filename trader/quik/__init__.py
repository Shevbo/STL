"""QUIK agent link (sprint02 Phase 1, read-only).

The generated gRPC stubs under ``trader/quik/pb`` import each other with the
proto package path ``shectory.quik.v1`` (matching the .proto package). To keep
that namespace importable without polluting the real ``sys.path`` config, we
append the ``pb`` directory to this package's import path here, the same trick
``trader/md/grpc_client.py`` uses for the Finam stubs.

After this module is imported, ``from shectory.quik.v1 import quik_agent_pb2``
resolves.
"""

import sys
from pathlib import Path

_PB_DIR = str(Path(__file__).parent / "pb")
if _PB_DIR not in sys.path:
    sys.path.insert(0, _PB_DIR)
