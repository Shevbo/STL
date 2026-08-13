---
name: reference-daily-order-cap-trap
description: daily_order_cap silently froze ALL robot orders incl. exits (17-lot long could not TP); cap raised to 500 both sides; counter now in status; continuous watchdog SMS deployed
metadata: 
  node_type: memory
  type: reference
  originSessionId: 7525bce9-2c09-4975-8992-6c434c6ffd84
  modified: 2026-07-21T18:16:42.216Z
---

2026-07-21 19:36: все роботы агента молча перестали торговать — исчерпался
`daily_order_cap` (агентский гард). 17-лотовый лонг НЕ МОГ взять тейк на бычьей
игле; реальные позиции 2.5 часа без возможности выхода. Оператор в ярости, прав.

**Механика:** effective cap = min(agent_config backstop, STL push); счётчик
`placedToday` В ПАМЯТИ агента — сбрасывается на рестарте агента и в MSK-полночь
(rollDay). Гард-реджект НЕ доходит до QUIK → в trans/таблицах ТИШИНА, никакого
следа. Дефолт был 50 с ОБЕИХ сторон (trader/config.py `quik_daily_order_cap` и
agent config.go), а один every-bar MACD-робот (lxk22) сжигал ~50 за ~3 часа.
Диагноз по тишине: trans молчит после N:NN при want!=None у роботов = похоже на
cap; докательство — счёт tagged-ордеров в quik.orders ring с момента рестарта
агента (uptime_sec → рестарт-время).

**Фикс (всё live):**
- STL: `QUIK_DAILY_ORDER_CAP=500` в `~/.shectory_trade.env` хостера (иначе push=50
  затянет обратно; порядок: рестарт STL ПЕРВЫМ, потом агент).
- Агент: дефолт config.go 50→500 (rev 1784657607); срочный сброс счётчика =
  просто рестарт агента (republish тем же деревом → новый rev → self-update).
- Наблюдаемость: `health.daily_orders_used/daily_orders_cap` в agent-local-status
  (Guard.DailyOrderState → Manager → status.go healthJSON).

**Непрерывный мониторинг (после «где блять твой мониторинг???»):**
`smain` cron `*/10 * * * * ~/bin/stl-watchdog.sh` (гейт 06:55-23:55 МСК внутри,
выходные включены — FORTS торгует). Тянет `hoster:~/stl-watchdog-probe.sh`,
который печатает "key|проблема" строки (пусто = ок): api_down / link_down /
runner_sick / cap_near(85%) / cap_full / hb_<robot> stale / ord_<robot>
orders-orphan. SMS только при проблеме, кулдаун 60 мин на ключ, одно
«восстановилось» при очистке. Состояние `~/.stl-watchdog/`. Селфтест:
`WATCHDOG_TEST=1 ~/bin/stl-watchdog.sh`. Канал тот же garden-шлюз, что и
утренние SMS [[project-morning-sms-status]].

**Урок:** любой молчаливый гард на пути реальных денег обязан (а) орать в
статус/лог при срабатывании, (б) быть виден в мониторинге ДО исчерпания.
См. [[reference-robot-perorder-cap-trap]] — та же семья (cap-ловушки эффективных
минимумов двух сторон).
