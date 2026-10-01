---
name: reference-quik-stop-order-fields
description: "Поля таблицы стоп-заявок нашего QUIK — operation НЕТ, сторона в flags бит 0x4 (проверено филлами), balance живой стопы врёт нулём"
metadata:
  node_type: memory
  type: reference
  originSessionId: cc985e8e-4291-4a58-8606-cd08213d5363
  modified: 2026-10-01T18:31:25.137Z
---

В таблице стоп-заявок НАШЕГО терминала поля `operation` (и любого другого со стороной) нет вовсе — сторона лежит в `flags`, бит 0x4: стоит = продажа, снят = покупка. Выверено фактом 01.10.2026, а не по аналогии с таблицей заявок: у сработавшей стопы 1012527937 `flags=24` (бит снят) и её `linkedorder` дал три сделки **buy** по 86210; у живой 1012532699 `flags=29` (бит стоит) — продажа. `stopflags` для стороны не годится, у обеих строк он равен 32. Пара одна, поэтому при расхождении с терминалом верить терминалу: механика нативных стопов у нас выверена не полностью.

Полный список полей живой строки (выгрузка 01.10.2026, непустые): `account, activation_date_time_ms, alltrade_num, brokerref, class_code, client_code, condition, condition_class_code, condition_price, condition_price2, condition_sec_code, condition_seccode, expiry, filled_qty, firmid, flags, linkedorder, offset, order_date_time_ms, order_num, orderdate, ordernum, ordertime, price, qty, sec_code, seccode, spread, stop_order_type, stopflags, trans_id, uid`.

**Номера `stop_order_num` в таблице НЕТ** — он в `order_num`/`ordernum`.

**`balance` живой стопы равен нулю** и ему верить нельзя: подставив его, экран напишет «осталось 0» у заявки, которая стережёт позицию. У живой строки остаток = весь `qty`; `balance` осмыслен только у сработавшей, вместе с `filled_qty`.

**Живость читается не по наличию строки:** таблица хранит ВСЕ стоп-заявки дня. Признаки смерти — `withdraw_datetime_ms` (снята), `activation_date_time_ms` или `linkedorder` (сработала). 29.09.2026 на этом сразу выросла ложная тревога по двум мёртвым записям.

**Цена срабатывания (`condition_price`) и цена самой заявки (`price`) нужны ОБЕ**, плюс `offset`/`spread`: их разрыв и есть причина, по которой 29.09.2026 нативный стоп на 70 контрактов сработал и умер с нулём исполнения, пока рынок шёл 690 пунктов за минуту. Показав одно из двух, спрячешь причину.

Снимается стоп **другой командой QUIK** — `KILL_STOP_ORDER` против `KILL_ORDER`. Перепутав команду, получишь тихий неуспех: заявка останется стеречь, а ручка отчитается об успехе. Поэтому строка несёт вид (`kind: "stop"`).

Связано: [[reference-order-truth-terminal-table]], [[reference-native-custody-tradeoff]], [[feedback-native-quik-observers]], [[reference-smart-order-double-custody]].
