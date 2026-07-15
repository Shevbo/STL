import grpc
import pytest

from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as pb
from trader.quik.pb.shectory.quik.v1 import runner_bridge_pb2 as rb
from trader.quik.pb.shectory.quik.v1 import runner_bridge_pb2_grpc as rbg

from robot_runner.bridge_client import BridgeClient


class _Svc(rbg.RunnerBridgeServicer):
    def __init__(self):
        self.placed = []

    async def PlaceRunnerOrder(self, request, context):
        self.placed.append(request)
        ok = request.code != "REJECTME"
        return rb.BridgeAck(ok=ok, error="" if ok else "guard says no")


@pytest.mark.asyncio
async def test_place_order_maps_and_raises():
    svc = _Svc()
    server = grpc.aio.server()
    rbg.add_RunnerBridgeServicer_to_server(svc, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        cli = BridgeClient(f"127.0.0.1:{port}")
        await cli.start()
        await cli.place_order(client_id="rr:r1:1", code="RIU6", side="buy",
                              price=89000, qty=1)
        assert svc.placed[0].side == pb.SIDE_BUY
        assert svc.placed[0].quantity == 1
        assert svc.placed[0].client_id == "rr:r1:1"
        with pytest.raises(RuntimeError):
            await cli.place_order(client_id="rr:r1:2", code="REJECTME", side="sell",
                                  price=1, qty=1)
        assert svc.placed[1].side == pb.SIDE_SELL
    finally:
        await server.stop(None)
