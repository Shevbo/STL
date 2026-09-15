"""Команды нативных стоп-заявок STL -> агент (этап 1 docs/design/execution-module.md).

Номер стоп-заявки ~1.9e18 обязан уйти строкой без округления; значения полей QUIK
всегда текст (sendTransaction принимает только строки).
"""

from trader.quik import orders as o
from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as pb


def test_place_stop_order_round_trip():
    msg = o.build_place_stop_order(
        "so:0123456789", "GZU6", "sell", 1,
        {"STOP_ORDER_KIND": "SIMPLE_STOP_ORDER", "STOPPRICE": 12340, "PRICE": "12300"})
    back = pb.OrchestratorMessage.FromString(msg.SerializeToString())
    assert back.WhichOneof("payload") == "place_stop_order"
    p = back.place_stop_order
    assert (p.client_id, p.code, p.side, p.quantity) == ("so:0123456789", "GZU6", pb.SIDE_SELL, 1)
    assert dict(p.fields) == {"STOP_ORDER_KIND": "SIMPLE_STOP_ORDER", "STOPPRICE": "12340", "PRICE": "12300"}


def test_kill_stop_order_keeps_number_exact():
    msg = o.build_kill_stop_order("so:1", 1925040213634112099, "GZU6")
    back = pb.OrchestratorMessage.FromString(msg.SerializeToString())
    assert back.WhichOneof("payload") == "kill_stop_order"
    assert back.kill_stop_order.stop_order_num == "1925040213634112099"
    assert back.kill_stop_order.code == "GZU6"
