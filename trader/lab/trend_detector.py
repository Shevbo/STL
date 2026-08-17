"""Детектор режима рынка: падение / рост / боковик на окне 4-12 недель.

ЗАЧЕМ. Замеры перебора упираются в одно и то же: строка, заработавшая на падении,
теряет на росте, и наоборот. На падении 12-14.08 из 1800 комбинаций реестра ни одна
не обогнала проданный и не тронутый контракт, а разрез по сторонам дал 97 плюсовых
из 107 у «только шорт» против 0 из 97 у «только лонг» — заработал ЗАПРЕТ СТОРОНЫ, а
не конфиг. Значит выбирать надо не строку на все времена, а режим, под который строка
и включается.

ЧЕМ ЭТО НЕ ЯВЛЯЕТСЯ. В `library.py` уже есть гейт режима (`reg_n`/`reg_band`/
`reg_mode`) — он внутри стратегии гейтит СТОРОНУ по средней, бар за баром. Здесь
уровень другой: классифицируется ОКНО ЦЕЛИКОМ, и ответ нужен для выбора самой
стратегии, а не стороны внутри неё. Одно другое не заменяет.

КАК СЧИТАЕТСЯ. Два независимых числа, и обоим есть чем возразить друг другу:

  drift  — ход от начала к концу окна, доля цены. Отвечает «куда пришли».
  ER     — Kaufman efficiency ratio: |конец - начало| / сумма |приращений|.
           Отвечает «дошли или проболтались». Рынок, прошедший 10% зигзагом,
           и рынок, прошедший те же 10% прямо, — это два РАЗНЫХ режима, а по
           одному только drift они неотличимы.

Боковик объявляется, если ход меньше `min_drift` ЛИБО дорога слишком извилистая
(ER ниже своего шумового пола). Оба порога — калибровочные ручки, а не константы: у RI своя
амплитуда, у BR своя, и подбирать их придётся по инструменту.

УВЕРЕННОСТЬ — не выдуманная вероятность, а доля согласия. Окно режется на `parts`
равных кусков, каждый классифицируется отдельно, и уверенность = доля кусков,
согласных с общим ответом. Тренд, который держится в 5 частях из 5, и тренд,
собранный одним рывком в последней части, дают разную уверенность — а это ровно
то различие, из-за которого стратегию включать или не включать.

Возвращается ещё и `t` — t-статистика наклона регрессии по логарифму цены. Она НЕ
участвует в решении (её порог зависит от автокорреляции минутных баров, и честно
откалибровать его на одном инструменте нельзя), но её полезно видеть рядом: расхождение
между «уверенно по частям» и «наклон в пределах шума» — повод не доверять окну.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

RISE, FALL, FLAT = "rise", "fall", "flat"

# Пороги по умолчанию подобраны на RI M1 (окна 4-12 недель 2026 года). Для другого
# инструмента их надо пересчитать: 3% хода для RI — это заметное движение, для
# валютной пары — шум. Ручка оставлена намеренно, «универсальной константы» здесь нет.
MIN_DRIFT = 0.03      # 3% хода за окно
# ПОРОГ ER — НЕ ЧИСЛО, А КОЭФФИЦИЕНТ НАД ШУМОМ. У случайного блуждания из n точек
# ER не ноль: E|S_n| = sigma*sqrt(2n/pi), длина пути = n*sigma*sqrt(2/pi), их отношение
# = 1/sqrt(n). То есть на 120 точках шум сам по себе даёт 0.09, а на 20 точках — 0.22.
# Фиксированный порог 0.15 означал бы на длинном окне «строго», а на четырёхнедельном —
# «пропускай что угодно»: чистый шум прошёл бы как тренд. Поэтому порог считается как
# ER_K / sqrt(n) и меряет ПРЕВЫШЕНИЕ над шумовым полом, одинаково на любой длине.
# Значение 2.2 ИЗМЕРЕНО, а не выбрано: на случайных блужданиях (300 прогонов, длины
# 20 и 120 точек, sigma шага 0.4-0.6%) оно даёт ~6% ложных объявлений тренда против
# 20% у 1.6, и при этом не теряет ни одного настоящего тренда — у чистой дороги ER 0.94,
# то есть запас над порогом четырёхкратный. Медиана ER блуждания при n=120 вышла 0.079
# против теоретических 1/sqrt(120)=0.091, формула подтвердилась на данных.
ER_K = 2.2
PARTS = 5             # на сколько кусков режем окно для оценки согласия
# ER СЧИТАЕТСЯ НА ПРОРЕЖЕННОЙ СЕРИИ, и это не оптимизация, а условие осмысленности.
# Длина пути растёт с числом точек: у одного и того же движения на 120 точках ER ~0.5,
# а на 40 000 минутных баров тот же тренд даёт ~0.02, потому что путь набит минутным
# шумом. Порог, откалиброванный на одной длине окна, на другой означал бы совсем другое,
# поэтому серия сначала приводится к фиксированному числу точек, и только потом меряется.
POINTS = 120


@dataclass
class Regime:
    state: str          # rise | fall | flat
    confidence: float   # доля кусков окна, согласных с ответом (0..1)
    drift: float        # ход за окно, доля цены
    er: float           # efficiency ratio 0..1
    t: float            # t-статистика наклона log-регрессии (справочно)
    bars: int
    parts: list[str]    # состояние каждого куска, в порядке времени

    def as_dict(self) -> dict:
        return {"state": self.state, "confidence": round(self.confidence, 3),
                "drift": round(self.drift, 5), "er": round(self.er, 4),
                "t": round(self.t, 2), "bars": self.bars, "parts": self.parts}


def _closes(series) -> list[float]:
    """Принимает и список Bar, и готовый список цен."""
    out = []
    for x in series:
        c = getattr(x, "close", None)
        out.append(float(x if c is None else c))
    return out


def _er(closes: list[float]) -> float:
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path <= 0:
        return 0.0
    return abs(closes[-1] - closes[0]) / path


def _slope_t(closes: list[float]) -> float:
    """t-статистика наклона регрессии log(price) по номеру бара.

    Минутные бары автокоррелированы, поэтому число само по себе завышено — сравнивать
    его между окнами РАЗНОЙ длины нельзя. Держим справочно.
    """
    n = len(closes)
    if n < 3:
        return 0.0
    ys = [math.log(c) for c in closes if c > 0]
    if len(ys) != n:
        return 0.0
    mx = (n - 1) / 2.0
    my = sum(ys) / n
    sxx = sum((i - mx) ** 2 for i in range(n))
    sxy = sum((i - mx) * (ys[i] - my) for i in range(n))
    if sxx <= 0:
        return 0.0
    b = sxy / sxx
    resid = [ys[i] - (my + b * (i - mx)) for i in range(n)]
    dof = n - 2
    s2 = sum(r * r for r in resid) / dof
    se = math.sqrt(s2 / sxx) if s2 > 0 else 0.0
    if se <= 0:                      # идеальная прямая: остатков нет
        # Ноль здесь означал бы «наклона нет» ровно у САМОГО сильного тренда — это
        # прямо противоположно смыслу метрики (нашёл рецензент 17.08). Отдаём знак
        # наклона бесконечностью: ошибка оценки нулевая, значимость неограниченная.
        return math.copysign(math.inf, b) if b else 0.0
    return b / se


def er_floor(n: int, k: float = ER_K) -> float:
    """Порог ER для серии из n точек: k / sqrt(n) — коэффициент над шумовым полом."""
    return k / math.sqrt(n) if n > 1 else 1.0


def _classify(closes: list[float], min_drift: float, k: float = ER_K) -> tuple[str, float, float]:
    drift = (closes[-1] - closes[0]) / closes[0] if closes[0] else 0.0
    er = _er(closes)
    # Порог считается по ДЛИНЕ ЭТОЙ серии, а не окна: куски окна короче, и мерить их
    # порогом целого означало бы пропускать шум как согласие.
    if abs(drift) < min_drift or er < er_floor(len(closes), k):
        return FLAT, drift, er
    return (RISE if drift > 0 else FALL), drift, er


def _drift_state(closes: list[float], min_drift: float) -> str:
    """Куда шёл кусок окна. Только ход, без гейта извилистости — см. detect_regime."""
    d = (closes[-1] - closes[0]) / closes[0] if closes[0] else 0.0
    if abs(d) < min_drift:
        return FLAT
    return RISE if d > 0 else FALL


def _thin(closes: list[float], points: int) -> list[float]:
    """Привести серию к <= points точкам. Последняя цена сохраняется всегда: окно
    заканчивается там, где оно заканчивается, а не на ближайшей удобной точке."""
    if points <= 0 or len(closes) <= points:
        return closes
    step = len(closes) / points
    out = [closes[int(i * step)] for i in range(points)]
    if out[-1] != closes[-1]:
        out.append(closes[-1])
    return out


def detect_regime(series, min_drift: float = MIN_DRIFT, er_k: float = ER_K,
                  parts: int = PARTS, points: int = POINTS) -> Regime:
    """Классифицировать окно целиком. `series` — бары или готовые цены закрытия."""
    raw = _closes(series)
    if len(raw) < 2 or raw[0] <= 0:
        return Regime(FLAT, 0.0, 0.0, 0.0, 0.0, len(raw), [])
    n_raw = len(raw)
    closes = _thin(raw, points)
    state, drift, er = _classify(closes, min_drift, er_k)

    # Согласие частей. Порог хода для куска делится на число кусков: за пятую часть
    # окна честно требовать пятую часть движения, иначе любой кусок объявится боковиком
    # и уверенность окажется нулевой у самого чистого тренда.
    # Куски строятся ВСТЫК и покрывают серию до последней точки: при step из
    # целочисленного деления хвост в 1-2 точки иначе выпадал бы из оценки целиком,
    # а именно в хвосте живёт «весь ход одним рывком в конце» (нашёл рецензент).
    step = max(2, math.ceil(len(closes) / parts))
    chunks = [closes[i:i + step + 1] for i in range(0, len(closes) - 1, step)]
    # Кусок судится ТОЛЬКО по ходу, без гейта ER. Гейт ER — вопрос всего окна («дошли
    # или проболтались»), а от куска мы спрашиваем другое: «шёл ли он туда же». Порог
    # ER на куске из 24 точек равен 0.45, и под него не проходит ни одна часть даже
    # 15-процентного ралли — уверенность обнулялась у самых чистых трендов (поймано на
    # реальном росте RIU6 17-30.07: drift +15.7%, ER 0.71, а уверенность выходила 0.00).
    chunk_states = [_drift_state(ch, min_drift / parts) for ch in chunks if len(ch) >= 2]
    agree = sum(1 for s in chunk_states if s == state) / len(chunk_states) if chunk_states else 0.0
    return Regime(state, agree, drift, er, _slope_t(closes), n_raw, chunk_states)


def demo() -> None:
    """Самопроверка на синтетике: три режима должны различаться. Без фреймворка."""
    import random
    rnd = random.Random(7)

    up = [100 * (1.0006 ** i) + rnd.uniform(-0.2, 0.2) for i in range(400)]
    down = [100 * (0.9994 ** i) + rnd.uniform(-0.2, 0.2) for i in range(400)]
    flat = [100 + math.sin(i / 9) * 1.5 + rnd.uniform(-0.4, 0.4) for i in range(400)]
    # Боковик с БОЛЬШИМ размахом, но без хода: drift мал, ER мал — тоже боковик.
    wide = [100 + math.sin(i / 40) * 12 + rnd.uniform(-1, 1) for i in range(400)]
    # Рывок в самом конце: ход есть, но согласия частей нет — уверенность обязана упасть.
    spike = [100 + rnd.uniform(-0.3, 0.3) for i in range(340)] + \
            [100 + (i - 340) * 0.12 for i in range(340, 400)]

    assert detect_regime(up).state == RISE
    assert detect_regime(down).state == FALL
    assert detect_regime(flat).state == FLAT
    assert detect_regime(wide).state == FLAT
    r_up, r_spike = detect_regime(up), detect_regime(spike)
    assert r_up.confidence == 1.0, r_up.as_dict()
    assert r_spike.confidence < 0.5, r_spike.as_dict()
    # ER отличает прямую дорогу от зигзага с тем же итогом.
    assert _er(up) > 0.4 > _er(flat), (_er(up), _er(flat))
    # Прореживание обязано СОХРАНЯТЬ ответ: длина серии не режим. Дорога, снятая
    # в 20 раз чаще и с шумом, — тот же режим. Дублировать точки для такой проверки
    # нельзя: приращения нулевые, путь не меняется, и проверка стала бы тавтологией.
    dense = []
    for a, b in zip(up, up[1:]):
        dense += [a + (b - a) * j / 20 + rnd.uniform(-0.25, 0.25) for j in range(20)]
    dense.append(up[-1])
    assert detect_regime(dense).state == detect_regime(up).state == RISE
    # Случайное блуждание — не тренд ни на какой длине окна.
    walk, px = [], 100.0
    for _ in range(120):
        px *= 1 + rnd.gauss(0, 0.004)
        walk.append(px)
    assert detect_regime(walk).state == FLAT, detect_regime(walk).as_dict()
    print("trend_detector demo ok:", detect_regime(up).as_dict())
    print("                      ", detect_regime(down).as_dict())
    print("                      ", detect_regime(spike).as_dict())


if __name__ == "__main__":
    demo()
