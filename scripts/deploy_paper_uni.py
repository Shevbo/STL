"""Постановка четырёх кандидатов UNI в БУМАГУ. Заказ оператора 18.08.2026.

Кандидаты отобраны ночным перебором: конфиг обязан быть плюсовым на ОБОИХ окнах RI
(рост RIH6 ноя-янв и падение RIU6 май-авг) при числе сделок не меньше 40, ранжирование
по СЛАБОМУ ЗВЕНУ — худшему из двух окон, а не по сумме.

Торгуемый инструмент RIU6: склейка нужна для ОЦЕНКИ, торговать её нельзя, у неё нет
стакана. Расписание и связка как у остальных бумажных роботов стенда.
"""
import json
import os
import sys

import httpx

from trader.auth.portal import make_session_token

API = "http://127.0.0.1:8000"
EMAIL = "bshevelev75@gmail.com"
LINK = "stl-finam-forts-01"
SYMBOL = "RIU6"
SCHEDULE = "07:00-23:50"

NAMES = {
    "shectory_2ema": ("UNI-2ema-RIU6", "UNI · Двойная EMA · RIU6"),
    "macd_cross": ("UNI-macdcross-RIU6", "UNI · MACD Cross · RIU6"),
    "triple_sma": ("UNI-3sma-RIU6", "UNI · Тройная SMA · RIU6"),
    "macd_shectory1": ("UNI-macds1-RIU6", "UNI · MACD Shectory1 · RIU6"),
    # Вторая тройка, заказ оператора 21.08: ещё три РАЗНЫХ стратегии из того же
    # отбора «плюс на обоих окнах RI».
    "keltner_bo": ("UNI-kelt-RIU6", "UNI · Keltner BO · RIU6"),
    "bollinger_bo": ("UNI-bollbo-RIU6", "UNI · Bollinger BO · RIU6"),
    "roc": ("UNI-roc-RIU6", "UNI · ROC · RIU6"),
}


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/uni_cfgs.json"
    raw = json.load(open(src, encoding="utf-8"))
    # /tmp/cands.json несёт [strategy, params], /tmp/uni_cfgs.json — просто params
    cfgs = {k: (v[1] if isinstance(v, list) else v) for k, v in raw.items()}
    token = make_session_token(EMAIL, os.environ["SHECTORY_AUTH_BRIDGE_SECRET"])
    hdr = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=API, headers=hdr, timeout=30) as c:
        for sid, params in cfgs.items():
            rid, name = NAMES[sid]
            body = {
                "id": rid, "name": name, "userEmail": EMAIL, "stlLinkId": LINK,
                "scriptCode": ("from trader.lab.strategies.library import make_on_bar; "
                               f"on_bar = make_on_bar('{sid}')"),
                "paramsJson": {**params, "symbol": SYMBOL},
                "schedule": SCHEDULE,
            }
            r = c.post("/api/v1/robots", json=body)
            if r.status_code == 409:
                print(f"{rid}: уже существует, обновляю параметры")
                c.put(f"/api/v1/robots/{rid}",
                      json={"paramsJson": body["paramsJson"], "name": name,
                            "scriptCode": body["scriptCode"], "schedule": SCHEDULE})
            elif r.status_code >= 300:
                print(f"{rid}: ОШИБКА создания {r.status_code} {r.text[:200]}")
                continue
            d = c.post(f"/api/v1/robots/{rid}/deploy")
            print(f"{rid}: создан, deploy {d.status_code} {d.text[:120]}")


if __name__ == "__main__":
    sys.exit(main())
