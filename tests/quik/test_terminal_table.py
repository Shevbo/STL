"""Таблица заявок QUIK как ЕДИНСТВЕННЫЙ правдивый ответ «что живо в терминале».

Жалоба оператора 01.10.2026 одним письмом: «не видишь активные заявки в квике вне
зависимости от их природы, не можешь их снять и пугаешь меня амнезией если будет
перезагрузка». Три косяка — один корень: STL судил о живых заявках по своему
складу в ПАМЯТИ, а не по таблице терминала.

Главное, что проверяется здесь: ПУСТАЯ таблица и ОТСУТСТВИЕ таблицы — разные
ответы. Подмена второго первым и есть механизм, которым STL принимал собственную
слепоту за флэт и ставил дубль к живой заявке.
"""
from trader.quik import terminal

NOW = 1_790_800_000_000
MIN = 60_000


class Store:
    def __init__(self, orders=None, age_ms=0, has_quik=True, status=True):
        self._orders, self._age = orders, age_ms
        self._has_quik, self._status = has_quik, status

    def agent_status(self, agent=None):
        if not self._status:
            return None
        st = {"_received_at_ms": NOW - self._age}
        if self._has_quik:
            st["quik"] = {"orders": self._orders if self._orders is not None else []}
        return st


def _row(num="1", sec="RIZ6", side="buy", price=85000.0, qty=1, balance=None,
         active=True, tag=""):
    return {"num": num, "sec": sec, "side": side, "price": price, "qty": qty,
            "balance": qty if balance is None else balance, "active": active,
            "tag": tag, "ts_ms": NOW}


# ---- «не знаю» против «ничего нет» -------------------------------------------

def test_empty_table_is_an_answer_but_a_missing_mirror_is_not():
    """ГЛАВНЫЙ ИНВАРИАНТ. Пустая таблица = «в терминале ничего», и по ней можно
    принимать решения. Нет зеркала = «не знаю», и ставить нельзя."""
    assert terminal.fresh(Store([]), now_ms=NOW) is True
    assert terminal.rows(Store([]), ) == []

    assert terminal.fresh(None, now_ms=NOW) is False
    assert terminal.fresh(Store(status=False), now_ms=NOW) is False
    assert terminal.fresh(object(), now_ms=NOW) is False, "объект без зеркала — тоже «не знаю»"
    # старая сборка агента: таблицы в зеркале нет вовсе, а зеркало есть
    assert terminal.fresh(Store(has_quik=False), now_ms=NOW) is False
    # все они отдают ПУСТОЙ список — поэтому судить по нему о рынке запрещено
    assert terminal.rows(None) == [] and terminal.rows(Store(status=False)) == []


def test_a_frozen_mirror_is_not_fresh():
    """Зеркало живо, но не двигалось: сборщик статуса на агенте замер. Такая
    таблица описывает прошлое, и ставить по ней нельзя."""
    assert terminal.fresh(Store([], age_ms=2 * MIN), now_ms=NOW) is True
    assert terminal.fresh(Store([], age_ms=10 * MIN), now_ms=NOW) is False


# ---- природа заявки: ярлык, а не фильтр --------------------------------------

def test_every_order_is_returned_whatever_its_origin():
    """Компаньон выбрасывал роботные, recon и stl-so* строки ДО показа, и живых
    заявок в QUIK у него не бывало в принципе. Заявка торгует деньгами — она
    обязана быть видна, кто бы её ни поставил."""
    store = Store([
        _row(num="1", tag=""),                       # руками в терминале
        _row(num="2", tag="stl-so-abcdef0123"),      # умная заявка
        _row(num="3", tag="lxk22robotid"),           # робот
        _row(num="4", tag="recon"),                  # выравнивание
        _row(num="5", tag="}S4XдD"),                 # приложение брокера
    ])
    got = {r["num"]: r["origin"] for r in terminal.rows(store, None, {"lxk22robotid"})}
    assert got == {"1": "manual", "2": "smart", "3": "robot",
                   "4": "recon", "5": "external"}
    assert len(terminal.rows(store)) == 5, "ни одна строка не отфильтрована"


def test_so_id_survives_the_truncated_brokerref():
    """В brokerref QUIK ровно 20 символов, а client_id уровня выглядит как
    `so:<so_id>:<уровень>:<соль>` — в тег влезает so_id и огрызок уровня. so_id
    (10 hex) влезает всегда, по нему и опознаём."""
    assert terminal.so_id_of("stl-so-41bf3af0dd") == "41bf3af0dd"
    assert terminal.so_id_of("stl-so-41bf3af0dd:1") == "41bf3af0dd"
    assert terminal.so_id_of("stl-so-41bf3af0dd:12") == "41bf3af0dd"   # ровно 20
    # ФОРМЫ, СНЯТЫЕ С ЖИВОЙ ТАБЛИЦЫ 01.10.2026 (сетка 41bf3af0dd, 25 заявок в
    # рынке): имя уровня в client_id это "gp1"/"gm4", и в 20 символов влезают его
    # первые два знака. Угадывать такое нельзя — проверено по факту.
    assert terminal.so_id_of("stl-so-41bf3af0dd:gp") == "41bf3af0dd"
    assert terminal.so_id_of("stl-so-41bf3af0dd:gm") == "41bf3af0dd"
    assert terminal.so_id_of("") == "" and terminal.so_id_of("recon") == ""
    assert terminal.so_id_of("lxk22robotid") == ""


def test_state_is_spelled_out_for_the_operator():
    rows = terminal.rows(Store([
        _row(num="1", active=True),
        _row(num="2", qty=5, balance=0, active=False),
        _row(num="3", qty=5, balance=5, active=False),
        _row(num="4", qty=5, balance=2, active=False),
    ]))
    assert {r["num"]: r["state"] for r in rows} == {
        "1": "активна", "2": "исполнена", "3": "снята", "4": "снята, исполнено 3"}
    assert {r["num"]: r["filled"] for r in rows} == {"1": 0, "2": 5, "3": 0, "4": 3}


def test_active_comes_first():
    rows = terminal.rows(Store([
        _row(num="old", active=False), _row(num="live", active=True)]))
    assert [r["num"] for r in rows] == ["live", "old"]


# ---- подхват уровня по цене --------------------------------------------------

def test_by_smart_order_groups_only_live_rows():
    store = Store([
        _row(num="1", tag="stl-so-aaaaaaaaaa", price=85400.0),
        _row(num="2", tag="stl-so-aaaaaaaaaa", price=85500.0, active=False),
        _row(num="3", tag="stl-so-bbbbbbbbbb", price=85400.0),
        _row(num="4", tag=""),
    ])
    got = terminal.by_smart_order(store)
    assert sorted(got) == ["aaaaaaaaaa", "bbbbbbbbbb"]
    assert [r["num"] for r in got["aaaaaaaaaa"]] == ["1"], "снятая строка не живая"


def test_level_is_matched_by_price_within_half_a_step():
    """Уровень опознаётся по ЦЕНЕ: client_id в brokerref не влезает. Допуск —
    половина шага, потому что QUIK отдаёт цену как double."""
    live = [_row(num="1", side="buy", price=85400.0),
            _row(num="2", side="sell", price=85600.0)]
    assert terminal.find_level(live, 85400.0, 10.0)["num"] == "1"
    assert terminal.find_level(live, 85404.0, 10.0)["num"] == "1", "полшага — та же"
    assert terminal.find_level(live, 85410.0, 10.0) is None, "шаг в сторону — другой уровень"
    assert terminal.find_level(live, 85600.0, 10.0, "sell")["num"] == "2"
    assert terminal.find_level(live, 85600.0, 10.0, "buy") is None, "сторона не та"
    assert terminal.find_level([], 85400.0, 10.0) is None


def test_garbage_rows_do_not_crash_the_table():
    """Зеркало приезжает по сети: строка может быть не словарём, а число —
    строкой. Упасть здесь значит ослепнуть ровно там, где слепота стоит денег."""
    store = Store([None, "мусор", 42, {"num": "1", "qty": "нет", "balance": None,
                                       "price": "", "active": 1, "tag": None}])
    rows = terminal.rows(store)
    assert len(rows) == 1
    assert rows[0]["qty"] == 0 and rows[0]["balance"] == 0 and rows[0]["price"] == 0.0
    assert rows[0]["active"] is True and rows[0]["origin"] == "manual"
