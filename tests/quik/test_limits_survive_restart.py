"""Рестарт не обнуляет пределы (оператор, 02.10.2026).

Дневной счётчик постановок и объём в работе жили в памяти процесса: каждый рестарт
STL заново открывал 500 заявок дня, а предел объёма в работе видел ноль при
стоящих в QUIK заявках. 02.10 рестартов было девять.
"""
import pytest

from trader.quik.orders import OrderStore


@pytest.mark.parametrize("before", [1, 7, 499])
def test_daily_counter_survives_a_restart(tmp_path, before):
    path = str(tmp_path / "c.json")
    a = OrderStore(counters_path=path)
    for _ in range(before):
        a.record_placement("9618")
    b = OrderStore(counters_path=path)            # «рестарт»: новый процесс, тот же файл
    assert b.placed_today("9618") == before, "рестарт обнулил дневной счётчик"
    b.record_placement("9618")
    assert OrderStore(counters_path=path).placed_today("9618") == before + 1


def test_without_a_file_nothing_is_persisted(tmp_path):
    a = OrderStore()
    a.record_placement("9618")
    assert OrderStore().placed_today("9618") == 0


def test_resting_orders_count_after_restart_from_the_terminal_table():
    """Склад пуст (рестарт), в QUIK стоит 100 контрактов наших заявок — предел
    обязан видеть 100, а не ноль."""
    st = OrderStore()
    st.set_resting_provider(lambda agent: [("n1", 60), ("n2", 40)])
    assert st.working_contracts("9618") == 100


def test_in_flight_and_terminal_orders_are_united_not_double_counted():
    st = OrderStore()
    st.register_pending("9618", "c1", "RIZ6", "buy", 85000.0, 5)   # в пути, номера нет
    st.register_pending("9618", "c2", "RIZ6", "buy", 84900.0, 3)
    with st._lock:                                               # c2 уже в таблице как n2
        st._bucket("9618").orders["c2"].order_id = "n2"
        st._bucket("9618").orders["c2"].state = "active"
    st.set_resting_provider(lambda agent: [("n2", 3), ("n9", 10)])  # n9 — подхваченная
    assert st.working_contracts("9618") == 5 + 3 + 10


def test_unknown_terminal_falls_back_to_the_store():
    st = OrderStore()
    st.register_pending("9618", "c1", "RIZ6", "buy", 85000.0, 5)
    st.set_resting_provider(lambda agent: None)                  # зеркало молчит
    assert st.working_contracts("9618") == 5
