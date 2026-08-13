---
name: sms-gateway-wg
description: "Утренние SMS молчат => проверять WireGuard-хэндшейк телефона-шлюза (sms-gate.app), а не крон; сбой отправки никем не алертится"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 084290f9-36c9-43b9-bf30-ec1652477e19
  modified: 2026-07-27T13:20:23.100Z
---

2026-07-27: не пришли SMS 06:50 и 07:03. Крон и текст были В ПОРЯДКЕ.

**Диагностический путь (по нему идти сразу):**
1. `ssh smain 'tail ~/stl-morning-sms.log'` — строки есть, но `http=000 state=?`
   (у здоровых дней `http=202 state=Pending`). `000` = curl не получил ответ
   (exit 28, таймаут), то есть шлюз недоступен, а не логика виновата.
2. Шлюз — телефон с sms-gate.app, доступный ТОЛЬКО через WireGuard (приватный
   IP:8080, URL в keymaster `garden_sms_gateway_url`).
3. `sudo wg show wg0 dump` на smain: у пира шлюза смотреть latest handshake.
   27.07 хэндшейк был 26.07 11:22 = телефон отвалился ~24 ч назад (нет интернета/
   сна/приложение убито). Правит только оператор физически.

**Расписание (крон на smain, MSK):** будни 06:50 ready + 07:03 takeoff,
выходные 09:50 + 10:03 (`stl-morning-sms.sh ready|takeoff`).

**Дыра ЗАКРЫТА 27.07 16:19.** Оба скрипта на smain получили ВТОРОЙ канал —
Telegram через relay Lineman: `POST http://127.0.0.1:9090/api/tg/send`, тело
`{"account":"klod","chat_id":...,"text":...}`, без авторизации (гейт по сети:
loopback/WG/Tailscale), токен бота у нас не хранится. Конфиг — `~/.stl_tg.env`
(URL/account/chat_id, не секреты).
- недоставленная SMS (http не 2xx) дублируется в ТГ обоими скриптами;
- `stl-watchdog.sh` сам следит за возрастом WG-хэндшейка шлюза (порог 30 мин;
  keepalive идёт каждые 25 с) и поднимает проблему `sms_gateway`.
Проверено сквозняком: SMS http=000 -> ТГ http=200.
ЛИМИТЫ relay: 1 сообщение / 15 с на аккаунт; ОДИНАКОВЫЙ текст в течение 60 с
глушится (200 + `dedup:true`) — в каждом алерте должно быть время/уникальная часть.
Бэкапы прежних версий: `~/bin/*.sh.bak-<epoch>` на smain.

Связано: [[morning-sms-status]], [[federation-access]].
