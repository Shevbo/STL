"""Двойная охрана: терминал и STL стерегут ОДНУ заявку — и исполняют её дважды.

29.09.2026, реальные деньги. Стоп оператора на продажу 40 RIZ6 был отправлен в
терминал в 06:45, до начала торгов. Подтверждение не пришло за 20 секунд, STL
объявил «терминал не принял» и взял охрану себе. Терминал заявку ПРИНЯЛ. В 09:32
стоп сработал в терминале на 40 контрактов, а сторож STL, ничего о нём не зная,
секундой позже выставил свою заявку — ещё 40. Позиция оператора перевернулась с
+40 в −40 без его решения. При отмене та же слепота оставила стоп-заявку живой:
книга писала «отменена», QUIK показывал «активна».

Лечение в трёх местах, и все три проверяются здесь: сторож не стреляет поверх
живой записи, охрана возвращается терминалу при позднем подтверждении, отмена
снимает запись по ФАКТУ таблицы, а не по статусу книги.
"""
from trader.api.quik_smart_orders import _track_native
from trader.quik.smart_orders import SmartOrder, SmartOrderBook, new_id

NOW = 1_790_663_000_000


class FakeStore:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def stop_orders(self, agent=None):
        return {"table": self.rows}

    def agent_status(self, agent=None):
        return {"quik": {"trades": []}}


def _row(so_id, num="310501606", **dead):
    """Живая строка таблицы стоп-заявок QUIK. Номер лежит в order_num —
    поля stop_order_num в таблице нет вовсе."""
    row = {"brokerref": f"stl-so-{so_id}", "order_num": num, "ordernum": num,
           "seccode": "RIZ6", "withdraw_datetime_ms": "0",
           "activation_date_time_ms": "0", "linkedorder": "0"}
    row.update(dead)
    return row


def _standalone(tmp_path, **kw):
    book = SmartOrderBook(str(tmp_path / "book.json"))
    so = SmartOrder(so_id=new_id(), kind="sl", code="RIZ6", side="sell", qty=40,
                    trigger_price=82840, created_ms=NOW, **kw)
    book.orders.append(so)
    return book, so


def test_late_confirmation_returns_custody_to_terminal(tmp_path):
    """Терминал подтвердил позже таймаута — охрана обязана вернуться ему."""
    book, so = _standalone(tmp_path, status="armed", native_state="failed",
                           native_ms=NOW - 600_000)
    _track_native(book, FakeStore([_row(so.so_id)]), "9618", NOW)
    assert so.native_state == "live", "запись в терминале есть — охрана не может быть у STL"
    assert so.status == "native"
    assert so.native_stop_num == "310501606"


def test_no_row_keeps_custody_with_stl(tmp_path):
    """Записи нет — STL остаётся сторожем: иначе позиция осталась бы голой."""
    book, so = _standalone(tmp_path, status="armed", native_state="failed",
                           native_ms=NOW - 600_000)
    _track_native(book, FakeStore([]), "9618", NOW)
    assert (so.native_state, so.status) == ("failed", "armed")


def test_fire_is_blocked_while_our_native_row_is_alive(tmp_path):
    """Сторож не стреляет поверх живой записи терминала — источник двойной продажи."""
    import trader.api.quik_smart_orders as m
    book, so = _standalone(tmp_path, status="armed", native_state="failed")
    native_rows = m._stop_rows_live(FakeStore([_row(so.so_id)]), "9618")
    assert so.so_id in native_rows
    # блок в _watch_once читает ровно это множество: id заявки или её ребёнка
    blocked = so.so_id in native_rows or any(
        c.parent_id == so.so_id and c.so_id in native_rows for c in book.orders)
    assert blocked is True
    # чужая запись не блокирует
    other = m._stop_rows_live(FakeStore([_row("deadbeef01")]), "9618")
    assert (so.so_id in other) is False


def test_audit_sees_both_directions(tmp_path):
    """Сверка книги с терминалом: обе стороны расхождения и молчание, когда сходится."""
    from trader.api.quik_smart_orders import _audit_book_vs_terminal
    book, so = _standalone(tmp_path, status="armed", native_state="")
    # 1. сходится: записи нет, книга стережёт сама — молчим
    assert _audit_book_vs_terminal(book, {}) == []
    # 2. книга сняла заявку, а в терминале она жива — случай 29.09
    so.status = "cancelled"
    msgs = _audit_book_vs_terminal(book, {so.so_id: _row(so.so_id)})
    assert len(msgs) == 1 and "ЖИВА" in msgs[0] and "310501606" in msgs[0]
    # 3. запись под чужим тегом: книга о ней не знает вовсе
    msgs = _audit_book_vs_terminal(book, {"deadbeef01": _row("deadbeef01", "999")})
    assert any("в книге такой заявки нет" in m for m in msgs)
    # 4. книга думает, что охраняет терминал, а записи нет — позиция без сторожа
    so.status = "native"
    msgs = _audit_book_vs_terminal(book, {})
    assert len(msgs) == 1 and "без сторожа" in msgs[0]
    # 5. та же заявка и живая запись — расхождения нет
    assert _audit_book_vs_terminal(book, {so.so_id: _row(so.so_id)}) == []


def test_dead_rows_are_not_custody(tmp_path):
    """Таблица QUIK хранит ВСЕ стоп-заявки дня. Сработавшая и снятая ничего не
    стерегут: принять их за живую охрану значит и заглушить сторожа, и поднять
    ложную тревогу — ровно это случилось в первый же час после выкладки."""
    import trader.api.quik_smart_orders as m
    live = _row("aaaaaaaa01")
    withdrawn = _row("aaaaaaaa02", "310501606", withdraw_datetime_ms="1790674445000")
    executed = _row("aaaaaaaa03", "310501605", activation_date_time_ms="1790663538000",
                    linkedorder="1925040256583954673")
    store = FakeStore([live, withdrawn, executed])
    assert set(m._stop_rows_live(store, "9618")) == {"aaaaaaaa01"}
    # а «все строки» обязаны остаться полными: по сработавшей записи разбирается,
    # какая нога связки исполнилась (её linkedorder ведёт к сделке)
    assert set(m._stop_rows_by_tag(store, "9618")) == {
        "aaaaaaaa01", "aaaaaaaa02", "aaaaaaaa03"}
    assert m._stop_num(live) == "310501606"


def test_an_oco_pair_is_guarded_by_one_stop_order(tmp_path):
    """СВЯЗКА OCO ОХРАНЯЕТСЯ ОДНОЙ СТОП-ЗАЯВКОЙ QUIK, и тег у неё — одной из ног.

    Ложная тревога 01.10.2026 сразу после рестарта: «c21c114afa числится под охраной
    терминала, а записи в таблице стоп-заявок нет: позиция без сторожа». На деле стоп
    и следящий тейк от входа 34593ce9df — связка (oco_group br:34593ce9df), и терминал
    держал её ОДНОЙ строкой 1012532699 под тегом stl-so-ae6731eec7, что прямо написано
    в примечании самого тейка. Проверка знала про дочерние заявки и не знала про
    сиблингов, поэтому вторая нога всегда выглядела беззащитной.

    Ложная тревога про ОТСУТСТВИЕ защиты учит не верить тревогам — а ложный SMS по
    умной заявке 01.10 уже был.
    """
    from trader.api.quik_smart_orders import _audit_book_vs_terminal
    book = SmartOrderBook(str(tmp_path / "oco.json"))
    sl = SmartOrder(so_id="aae6731ee7", kind="sl", code="RIZ6", side="sell", qty=1,
                    trigger_price=85100, created_ms=NOW, status="native",
                    parent_id="34593ce9df", oco_group="br:34593ce9df")
    tp = SmartOrder(so_id="c21c114afa", kind="trail_tp", code="RIZ6", side="sell", qty=1,
                    trigger_price=86500, created_ms=NOW, status="native",
                    parent_id="34593ce9df", oco_group="br:34593ce9df")
    book.orders += [sl, tp]

    # терминал держит связку ОДНОЙ строкой под тегом первой ноги
    rows = {sl.so_id: _row(sl.so_id)}
    assert _audit_book_vs_terminal(book, rows) == [], (
        "вторая нога связки охраняется той же строкой — тревоги быть не должно")

    # а вот когда строки нет ВООБЩЕ — обе ноги действительно без сторожа
    msgs = _audit_book_vs_terminal(book, {})
    assert len(msgs) == 2 and all("без сторожа" in m for m in msgs)


def test_a_lone_native_order_without_a_row_still_screams(tmp_path):
    """Послабление не должно глушить настоящий случай: заявка БЕЗ связки и без
    строки в терминале — это позиция без сторожа, и молчать нельзя."""
    from trader.api.quik_smart_orders import _audit_book_vs_terminal
    book, so = _standalone(tmp_path, status="native", native_state="")
    assert so.oco_group == ""
    msgs = _audit_book_vs_terminal(book, {})
    assert len(msgs) == 1 and "без сторожа" in msgs[0]


def test_the_holder_follows_the_row_not_the_list_order(tmp_path):
    """ДУБЛЬ ЗАЩИТЫ НА ЖИВЫХ ДЕНЬГАХ, 02.10.2026, рестарт в 08:00.

    Связка OCO охраняется ОДНОЙ стоп-заявкой QUIK, и тег у неё — одной из ног.
    Держатель же выбирался первым подходящим из kids, то есть по порядку в книге.
    Не совпало — строка считалась ПРОПАВШЕЙ: в 08:01:15 журнал написал «стоп-заявка
    снялась по сроку, сделок нет», в 08:01:16 «защита отдана терминалу», и в QUIK
    оказались ДВЕ записи с одним тегом stl-so-ae6731eec7, одинаковые до копейки:
    qty 1, срабатывание 86500, заявка по 85080. Сработали бы обе — продали бы 2
    контракта вместо 1.

    Здесь порядок ног СПЕЦИАЛЬНО обратный тегу записи: тейк идёт первым, а запись
    принадлежит стопу. Падает на старом коде.
    """
    book = SmartOrderBook(str(tmp_path / "oco.json"))
    parent = SmartOrder(so_id="34593ce9df", kind="trail_tp", code="RIZ6", side="buy",
                        qty=1, created_ms=NOW, status="fired",
                        native_state="live", native_seen_ms=NOW - 60_000)
    tp = SmartOrder(so_id="c21c114afa", kind="trail_tp", code="RIZ6", side="sell", qty=1,
                    trigger_price=86500, created_ms=NOW, status="native",
                    parent_id="34593ce9df", oco_group="br:34593ce9df")
    sl = SmartOrder(so_id="ae6731eec7", kind="sl", code="RIZ6", side="sell", qty=1,
                    trigger_price=85100, created_ms=NOW, status="native",
                    parent_id="34593ce9df", oco_group="br:34593ce9df")
    book.orders += [parent, tp, sl]        # тейк ПЕРВЫМ, запись принадлежит стопу

    # flags=29 — снято с ЖИВОЙ стопы 310530617 02.10.2026: бит 0 = заявка активна.
    store = FakeStore([_row("ae6731eec7", num="310530617", flags="29")])
    failed = _track_native(book, store, "9618", NOW)

    assert parent.native_state == "live", (
        "запись в терминале ЕСТЬ — охрану забирать и передавать заново нельзя")
    assert [c.status for c in (tp, sl)] == ["native", "native"],         "обе ноги остаются под охраной терминала"
    assert parent not in failed, "передача заново = вторая стоп-заявка в QUIK"


def test_a_genuinely_missing_row_still_returns_custody(tmp_path):
    """Послабление узкое: записи нет НИ ПО ОДНОЙ ноге — охрана честно возвращается
    STL. Иначе 23.09.2026 повторится: следящая продажа 30 контрактов пропала в
    терминале, книга похоронила её в orphaned, и заявки не стало нигде."""
    book = SmartOrderBook(str(tmp_path / "gone.json"))
    parent = SmartOrder(so_id="34593ce9df", kind="trail_tp", code="RIZ6", side="buy",
                        qty=1, created_ms=NOW, status="fired",
                        native_state="live", native_seen_ms=NOW - 60_000)
    sl = SmartOrder(so_id="ae6731eec7", kind="sl", code="RIZ6", side="sell", qty=1,
                    trigger_price=85100, created_ms=NOW, status="native",
                    parent_id="34593ce9df", oco_group="br:34593ce9df")
    book.orders += [parent, sl]
    failed = _track_native(book, FakeStore([]), "9618", NOW)
    assert sl.status == "armed", "записи нет нигде — защиту ведёт STL"
    assert parent in failed


def test_a_live_row_wins_over_a_dead_one_with_the_same_tag(tmp_path):
    """ЖИВАЯ ЗАЩИТА, ПОХОРОНЕННАЯ СЛОВАРЁМ, 02.10.2026.

    Словарь по тегу сворачивал строки «последняя побеждает». В QUIK оказались две
    строки с одним тегом stl-so-ae6731eec7 (дубль), я снял одну, у снятой flags стал
    30 — бит живости снят, — и словарь оставил ИМЕННО ЕЁ. Код прочитал «стоп-заявка
    снята в терминале» (журнал 08:02:47, flags=30), пометил обе ноги cancelled, а
    выжившая 310530617 продолжила стеречь уже никем не управляемой. Сверка этого не
    увидела: она читает тот же словарь.

    Живая строка отвечает на вопрос «что в терминале», мёртвая — только когда живых
    нет (по её linkedorder разбирается, какая нога исполнилась).
    """
    import trader.api.quik_smart_orders as m
    dead = _row("ae6731eec7", num="310530756", flags="30",
                withdraw_datetime_ms="1790917367000")
    live = _row("ae6731eec7", num="310530617", flags="29")

    # мёртвая идёт ПОСЛЕ живой — порядок, при котором «последняя побеждает» врёт
    got = m._stop_rows_by_tag(FakeStore([live, dead]), "9618")
    assert got["ae6731eec7"]["order_num"] == "310530617", (
        "словарь оставил снятую строку — живая защита объявлена снятой")
    # и обратный порядок даёт тот же ответ
    got = m._stop_rows_by_tag(FakeStore([dead, live]), "9618")
    assert got["ae6731eec7"]["order_num"] == "310530617"
    # живых нет — мёртвая остаётся, по ней разбирают, какая нога исполнилась
    only_dead = m._stop_rows_by_tag(FakeStore([dead]), "9618")
    assert only_dead["ae6731eec7"]["order_num"] == "310530756"


def test_the_audit_sees_a_live_row_the_book_calls_cancelled(tmp_path):
    """Сверка обязана кричать, когда книга считает заявку снятой, а в терминале она
    ЖИВА: именно такой орфан 02.10 стерёг позицию, которой уже не было, и при
    срабатывании ОТКРЫЛ бы шорт вместо закрытия лонга."""
    from trader.api.quik_smart_orders import _audit_book_vs_terminal
    book, so = _standalone(tmp_path, status="cancelled", native_state="")
    msgs = _audit_book_vs_terminal(book, {so.so_id: _row(so.so_id, flags="29")})
    assert len(msgs) == 1 and "ЖИВА" in msgs[0] and "cancelled" in msgs[0]
