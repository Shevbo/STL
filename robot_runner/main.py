"""robot-runner entrypoint. Supervised by the quik-agent; do not run two copies.

Zero-touch: connects to the agent's loopback bridge with endless backoff (may
start before the agent), receives its robots via the control stream (persisted
specs are replayed by the agent on every connect), trades through the bridge.
"""

import argparse
import asyncio
import sys

from robot_runner.bridge_client import BridgeClient
from robot_runner.host import RobotHost


def main() -> None:
    # stdout/stderr are PIPES to the agent (FileTee -> runner.log). On a RU
    # Windows VDS a pipe defaults to cp1251 STRICT, so any log line with a
    # character outside cp1251 (e.g. '→' in the FILL message) raised
    # UnicodeEncodeError — which killed the whole runner on every REAL fill
    # (2026-07-13: book frozen at 10:15, strategy re-emitted all day). Force
    # UTF-8 so no log line can ever throw.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError):
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="127.0.0.1:50071")
    ap.add_argument("--data", default="robots")
    args = ap.parse_args()

    async def amain():
        bridge = BridgeClient(args.bridge)
        await bridge.start()
        host = RobotHost(bridge, args.data)
        await host.run()

    asyncio.run(amain())


if __name__ == "__main__":
    main()
