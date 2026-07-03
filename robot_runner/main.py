"""robot-runner entrypoint. Supervised by the quik-agent; do not run two copies.

Zero-touch: connects to the agent's loopback bridge with endless backoff (may
start before the agent), receives its robots via the control stream (persisted
specs are replayed by the agent on every connect), trades through the bridge.
"""

import argparse
import asyncio

from robot_runner.bridge_client import BridgeClient
from robot_runner.host import RobotHost


def main() -> None:
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
