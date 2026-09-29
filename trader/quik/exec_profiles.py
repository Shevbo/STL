"""Профили доведения умной заявки до исполнения — и ВХОДА, и ВЫХОДА.

Заявка ставит ЛИМИТ, и на быстром движении его не наливают: 29.09.2026 родной
стоп оператора на 70 RIZ6 сработал в 14:50:06 с лимитом в 30 пунктов от уровня,
рынок за минуту прошёл 690 пунктов, заявка умерла с нулём исполнения, позиция
осталась открытой. Отсюда трёхфазное доведение — постоять у планки, догнать
цену, добить по рынку.

Секунды у этих фаз не универсальны, и оператор это назвал первым: на тихом рынке
и малой позиции спешить незачем, можно ждать по три минуты и выйти по хорошей
цене; на быстром рынке и крупной позиции ждать нельзя вовсе. Поэтому не одни
зашитые числа, а ПРОФИЛИ, которые он меняет сам.

Три штатных (заказ оператора 29.09.2026):
  aggressive  10 с у планки + 10 с погони, дальше рынок — выход за 20 секунд;
  active      3 мин + 3 мин, дальше рынок — для тихого рынка и малого объёма;
  normal      доведения нет вовсе: заявка стоит лимитом, как было до 29.09.

«normal» оставлен намеренно и это НЕ то же самое, что нулевые фазы: ноль в
фазах означает «сразу по рынку», а normal — «никогда по рынку». Разница в том,
кто отвечает за неисполнение: в normal — человек, который так выбрал.

Профиль относится к ЛЮБОЙ заявке, а не только к защитной (уточнение оператора
29.09.2026): неисполненный вход так же врёт человеку, как неисполненный выход.
Он видит «сработала», а в рынке ничего нет — у коридора это означает остаться
без позиции ровно у той стенки, от которой он собирался торговать.
"""

from __future__ import annotations

import json
import os

import structlog

log = structlog.get_logger(__name__)

DEFAULT_PROFILE = "aggressive"

# hold_sec, chase_sec, chase_every_sec, market (добивать ли рыночной)
DEFAULTS: dict[str, dict] = {
    "aggressive": {"hold_sec": 10, "chase_sec": 10, "chase_every_sec": 2,
                   "market": True, "title": "агрессивный: 10 + 10 с, дальше рынок"},
    "active": {"hold_sec": 180, "chase_sec": 180, "chase_every_sec": 30,
               "market": True, "title": "активный: 3 + 3 мин, дальше рынок"},
    "normal": {"hold_sec": 0, "chase_sec": 0, "chase_every_sec": 0,
               "market": False, "title": "нормальный: заявка стоит лимитом, как было"},
}

_PATH = "data/exec_profiles.json"


def load(path: str = _PATH) -> dict[str, dict]:
    """Профили с диска поверх штатных. Файла нет или он битый — штатные.

    Настройка торговли не имеет права уронить торговлю, поэтому любая ошибка
    чтения это молчаливый возврат к умолчаниям, а не исключение наружу.
    """
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    try:
        if not os.path.exists(path):
            return out
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
        if not isinstance(saved, dict):
            return out
        for name, cfg in saved.items():
            if not isinstance(cfg, dict):
                continue
            base = out.get(name, {"title": name})
            out[name] = {
                "hold_sec": max(0, int(cfg.get("hold_sec", base.get("hold_sec", 0)) or 0)),
                "chase_sec": max(0, int(cfg.get("chase_sec", base.get("chase_sec", 0)) or 0)),
                "chase_every_sec": max(
                    0, int(cfg.get("chase_every_sec", base.get("chase_every_sec", 0)) or 0)),
                "market": bool(cfg.get("market", base.get("market", True))),
                "title": str(cfg.get("title") or base.get("title") or name),
            }
    except Exception as exc:  # noqa: BLE001 — настройка не роняет торговлю
        log.warning("exec_profiles.load_failed", path=path, error=str(exc))
    return out


def save(profiles: dict[str, dict], path: str = _PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(profiles, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def resolve(name: str, path: str = _PATH) -> dict:
    """Профиль по имени. Незнакомое имя — штатный по умолчанию, а не отказ:
    заявка важнее опечатки в профиле, и молчать об этом нельзя."""
    profiles = load(path)
    key = (name or "").strip() or DEFAULT_PROFILE
    if key not in profiles:
        log.warning("exec_profiles.unknown", name=name, used=DEFAULT_PROFILE)
        key = DEFAULT_PROFILE
    cfg = dict(profiles[key])
    cfg["name"] = key
    return cfg
