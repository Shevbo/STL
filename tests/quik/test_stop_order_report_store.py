"""Зеркало нативных стоп-заявок QUIK в STL (docs/design/execution-module.md, этап 1).

Агент шлёт StopOrderReport двух видов: полный снимок таблицы stop_orders и одиночное
событие OnStopOrder. Снимок заменяет таблицу, события копятся в ограниченном кольце.
Поля QUIK приходят как есть: номер стоп-заявки ~1.9e18 обязан дожить строкой.
"""

from trader.quik.pb.shectory.quik.v1 import quik_agent_pb2 as pb
from trader.quik.store import QuikAgentStore


def test_table_replaces_and_events_ring():
    s = QuikAgentStore()
    s.set_stop_order_report("9618", True, [{"order_num": "1925040213634112099"}], 100)
    s.set_stop_order_report("9618", True, [{"order_num": "2"}, {"order_num": "3"}], 200)
    for i in range(QuikAgentStore._STOP_EVENTS_KEEP + 5):
        s.set_stop_order_report("9618", False, [{"n": str(i)}], 300 + i)
    so = s.stop_orders("9618")
    assert [r["order_num"] for r in so["table"]] == ["2", "3"]
    assert so["table_received_ms"] == 200
    assert len(so["events"]) == QuikAgentStore._STOP_EVENTS_KEEP
    assert so["events"][-1]["fields"]["n"] == str(QuikAgentStore._STOP_EVENTS_KEEP + 4)


def test_report_proto_round_trip_keeps_big_numbers_exact():
    rep = pb.StopOrderReport(is_table=False, received_at_unix_ms=5,
                             rows=[pb.StopOrderRow(fields={"order_num": "1925040213634112099"})])
    wire = pb.AgentMessage(stop_order_report=rep).SerializeToString()
    back = pb.AgentMessage.FromString(wire)
    assert back.WhichOneof("payload") == "stop_order_report"
    s = QuikAgentStore()
    r = back.stop_order_report
    s.set_stop_order_report("9618", r.is_table, [dict(x.fields) for x in r.rows], r.received_at_unix_ms)
    assert s.stop_orders("9618")["events"][0]["fields"]["order_num"] == "1925040213634112099"
