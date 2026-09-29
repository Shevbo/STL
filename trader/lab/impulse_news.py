"""Импульсы на минутках против новостного фона и пересечения средних.

ЗАЧЕМ. Гипотеза оператора 29.09.2026: если лента новостей тиха, а цена делает
мощный ход, доминирует манипулятор или алготрейдер, и закономерности надо искать
в таких импульсах. Второй слой (заказ того же дня): пересечение SMA50 и SMA200
на минутках будто бы ПРЕДШЕСТВУЕТ импульсу. Проект: docs/retro-reverse-design.md.

Этот модуль считает только то, что требует баров, и исполняется на i9 задачей
агента (module='trader.lab.impulse_news', func='run'). Сверка с новостями и вся
статистика лёгкие и живут в scripts/impulse_news_report.py.

ОПРЕДЕЛЕНИЯ ЗАФИКСИРОВАНЫ ДО ПРОСМОТРА ДАННЫХ, менять нельзя:

- Импульс на баре t: |close[t] - close[t-5]| >= 4 x медиана |close[i] - close[i-5]|
  по предыдущим 1440 барам, И сумма volume за t-4..t >= 3 x медиана 5-барных сумм
  по тем же 1440 барам. Начало импульса = бар t-5.
- Срабатывания, начавшиеся в пределах 10 минут друг от друга (цепочкой), склеиваются
  в одно событие: начало первое, размер и направление по максимальному |ходу|,
  конец = последний бар срабатывания в цепочке (продолжение считается ПОСЛЕ него,
  чтобы не засчитать сам импульс).
- Продолжение: знаковый ход в направлении события от конца через 5, 15 и 60 минут,
  в единицах медианного 5-барного хода на баре конца. Цена «через N минут» =
  close последнего бара с временем <= конец + N минут (через перерыв цена стоит).
- Пересечение (параметры таймфрейм, быстрая, медленная; CROSS_CONFIGS): бар, на
  котором сменился знак SMA(fast) - SMA(slow) по close. Направление +1, если быстрая
  ушла выше. Конфигурации: M1 50/200 (ходы 5/15/60, «до» 30 минут) и M15 10/50
  (ходы 5/15/60/240, «до» 120 минут). M15 склеивается из M1 от начала часа, close =
  close последней минуты; ходы в минутных барах от минуты закрытия бара
  пересечения. Плюс доля точек, после которых в течение 30 минут начинается импульс.
- «Ложные» точки: локальный минимум |SMA(fast) - SMA(slow)| в окне +-30 баров своего
  таймфрейма без смены знака там же; берутся самые близкие (по |разрыву| в медианах
  хода), столько же, сколько пересечений. Направление = знак разрыва на этом баре.
- Случайные точки (контроль): для слоя 1 случайные минуты, для пересечений случайные
  завершённые бары своего таймфрейма; фиксированное зерно.

ОГОВОРКА, которую нельзя терять: «тихий фон» = ноль заголовков Интерфакса в окне.
Это прокси, а не факт: иностранные провода, слухи, блочные сделки в ленту не
попадают. Метка «без новостей» не доказывает отсутствие информации.
"""
from __future__ import annotations

import bisect
import os
import random
from datetime import date, datetime, timezone

LOOKBACK = 1440
K_MOVE = 4.0
K_VOL = 3.0
MERGE_SEC = 600
HORIZONS = (5, 15, 60)
BACK_MIN = 30
IMPULSE_AFTER_SEC = 1800
SMA_FAST, SMA_SLOW = 50, 200
NEAR_W = 30


def _cols(bars: list) -> tuple[list[int], list[float], list[float]]:
    """Бары [time,o,h,l,c,v] или dict -> (время, close, volume)."""
    if bars and isinstance(bars[0], dict):
        return ([int(b["time"]) for b in bars], [float(b["close"]) for b in bars],
                [float(b["volume"]) for b in bars])
    return [int(b[0]) for b in bars], [float(b[4]) for b in bars], [float(b[5]) for b in bars]


def _median(s: list[float]) -> float:
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def medians(closes: list[float], vols: list[float], lookback: int = LOOKBACK):
    """По каждому бару t: ход за 5 баров, 5-барный объём и их медианы по [t-lookback, t-1].

    Медианы только по ПРОШЛЫМ барам: бар t в свою медиану не входит. Пока истории
    меньше lookback, медиана None. Скользящий отсортированный список, O(n x lookback)
    на сдвигах памяти, для года минуток это секунды.
    """
    n = len(closes)
    move = [None] * n
    v5 = [None] * n
    for i in range(5, n):
        move[i] = closes[i] - closes[i - 5]
        v5[i] = sum(vols[i - 4:i + 1])
    med_m: list = [None] * n
    med_v: list = [None] * n
    sm: list[float] = []
    sv: list[float] = []
    first = 5
    for t in range(first, n):
        if t - first >= lookback:
            med_m[t] = _median(sm)
            med_v[t] = _median(sv)
            old = t - lookback - 1
            if old >= first:
                del sm[bisect.bisect_left(sm, abs(move[old]))]
                del sv[bisect.bisect_left(sv, v5[old])]
        bisect.insort(sm, abs(move[t]))
        bisect.insort(sv, v5[t])
    return move, v5, med_m, med_v


def _detect(times, closes, vols, k_move=K_MOVE, k_vol=K_VOL, lookback=LOOKBACK,
            merge_sec=MERGE_SEC):
    move, v5, med_m, med_v = medians(closes, vols, lookback)
    events: list[dict] = []
    for t in range(len(closes)):
        mm, mv = med_m[t], med_v[t]
        if not mm or not mv:
            continue
        if abs(move[t]) >= k_move * mm and v5[t] >= k_vol * mv:
            s = t - 5
            e = events[-1] if events else None
            if e and times[s] - e["_last_start"] <= merge_sec:
                e["_last_start"] = times[s]
                e["end_idx"], e["end_time"] = t, times[t]
                e["n_det"] += 1
                size = abs(move[t]) / mm
                if size > e["size"]:
                    e.update(size=size, dir=1 if move[t] > 0 else -1, move_pts=move[t])
            else:
                events.append({"start_idx": s, "start_time": times[s], "end_idx": t,
                               "end_time": times[t], "dir": 1 if move[t] > 0 else -1,
                               "size": abs(move[t]) / mm, "move_pts": move[t],
                               "n_det": 1, "_last_start": times[s]})
    for e in events:
        e.pop("_last_start")
        e["med"] = med_m[e["end_idx"]]
    return events, med_m


def detect(bars: list, **kw) -> list[dict]:
    """Склеенные события-импульсы (см. определения в докстринге модуля)."""
    return _detect(*_cols(bars), **kw)[0]


def price_at(times: list[int], closes: list[float], ts: int):
    """Close последнего бара с временем <= ts; None за пределами ряда."""
    if ts > times[-1] or ts < times[0]:
        return None
    return closes[bisect.bisect_right(times, ts) - 1]


def continuation(times, closes, idx: int, direction: int, med: float,
                 horizons=HORIZONS) -> dict:
    """Ход от бара idx через h минут в направлении direction, в медианах хода."""
    out = {}
    for h in horizons:
        p = price_at(times, closes, times[idx] + h * 60)
        out[h] = None if p is None or not med else round(direction * (p - closes[idx]) / med, 4)
    return out


def sma_gap(closes: list[float], fast: int = SMA_FAST, slow: int = SMA_SLOW) -> list:
    """SMA(fast) - SMA(slow) по close; None, пока нет slow баров."""
    n = len(closes)
    out: list = [None] * n
    sf = ss = 0.0
    for i, c in enumerate(closes):
        sf += c
        ss += c
        if i >= fast:
            sf -= closes[i - fast]
        if i >= slow:
            ss -= closes[i - slow]
        if i >= slow - 1:
            out[i] = sf / fast - ss / slow
    return out


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def crosses(gap: list) -> list[tuple[int, int]]:
    """(бар, направление) каждой смены знака разрыва. Ноль знака не меняет."""
    out = []
    last = 0
    for i, g in enumerate(gap):
        if g is None:
            continue
        s = _sign(g)
        if s and last and s != last:
            out.append((i, s))
        if s:
            last = s
    return out


def near_misses(gap: list, med_m: list, cross_idx: list[int], w: int = NEAR_W) -> list[tuple[int, int]]:
    """Локальные минимумы |разрыва| без смены знака в +-w барах, самые близкие первыми.

    Близость меряется в медианах 5-барного хода, чтобы тихие и бурные недели были
    сравнимы. Вызывающий обрезает список до числа пересечений.
    """
    cset = sorted(cross_idx)
    ag = [abs(g) if g is not None else None for g in gap]
    cand = []
    for i in range(w, len(gap) - w):
        g = ag[i]
        if g is None or not med_m[i] or ag[i - w] is None:
            continue
        j = bisect.bisect_left(cset, i - w)
        if j < len(cset) and cset[j] <= i + w:
            continue
        if min(ag[i - w:i]) <= g or min(ag[i + 1:i + w + 1]) < g:
            continue
        cand.append((g / med_m[i], i, _sign(gap[i])))
    cand.sort()
    return [(i, s) for _, i, s in cand]


def _point_row(times, closes, med_m, starts: list[int], i: int, direction: int,
               horizons=HORIZONS, back_min: int = BACK_MIN) -> dict:
    """Ход вперёд/назад в минутных барах от бара i и «импульс начался в ближайшие 30 минут»."""
    med = med_m[i]
    row = {"time": times[i], "dir": direction, "med": round(med, 4)}
    for h, v in continuation(times, closes, i, direction, med, horizons).items():
        row[f"f{h}"] = v
    back = price_at(times, closes, times[i] - back_min * 60)
    row["b"] = None if back is None else round(direction * (closes[i] - back) / med, 4)
    j = bisect.bisect_left(starts, times[i])
    row["imp30"] = int(j < len(starts) and starts[j] <= times[i] + IMPULSE_AFTER_SEC)
    return row


def tf_ends(times: list[int], tf_min: int) -> list[int]:
    """Индексы последней минуты каждого tf-минутного бара (склейка от начала часа).

    Close такого бара = close этой минуты, поэтому пересечение на нём известно ровно
    в момент закрытия этой минуты: ходы после отсчитываются от неё, без заглядывания.
    Последний бар ряда не берётся, он может быть незавершённым.
    """
    if tf_min == 1:
        return list(range(len(times)))
    step = tf_min * 60
    out = [i for i in range(len(times) - 1) if times[i] // step != times[i + 1] // step]
    return out


def cross_layer(times, closes, med_m, starts, lo, hi, tf_min: int, fast: int, slow: int,
                horizons, back_min: int, n_random: int = 3000, seed: int = 20260929) -> dict:
    """Пересечения SMA(fast)/SMA(slow) на tf-минутных барах, ложные сближения, случайные бары.

    Средние считаются по close ЗАВЕРШЁННЫХ tf-баров; все ходы в минутных барах от
    минуты закрытия бара. Окно ложных точек = +-30 tf-баров. Случайные точки берутся
    из тех же завершённых tf-баров (для tf=1 это случайные минуты), направление =
    знак разрыва средних на них.
    """
    ends = tf_ends(times, tf_min)
    gap = sma_gap([closes[i] for i in ends], fast, slow)
    med_tf = [med_m[i] for i in ends]
    ok = [lo <= times[i] <= hi and med_m[i] for i in ends]

    def rows(pts):
        return [_point_row(times, closes, med_m, starts, ends[k], s, horizons, back_min)
                for k, s in sorted(pts)]

    all_x = crosses(gap)
    xs = [(k, s) for k, s in all_x if ok[k]]
    nm = [(k, s) for k, s in near_misses(gap, med_tf, [k for k, _ in all_x]) if ok[k]][:len(xs)]
    pool = [k for k in range(len(ends)) if ok[k] and gap[k]]
    rnd = random.Random(seed).sample(pool, min(n_random, len(pool)))
    return {"tf": tf_min, "fast": fast, "slow": slow, "horizons": list(horizons),
            "back_min": back_min, "crosses": rows(xs), "near": rows(nm),
            "random": rows([(k, _sign(gap[k])) for k in rnd])}


CROSS_CONFIGS = (
    # (таймфрейм мин, быстрая, медленная, горизонты мин, окно «до» мин)
    (1, 50, 200, (5, 15, 60), 30),
    (15, 10, 50, (5, 15, 60, 240), 120),
)


def analyze(bars: list, lo: int, hi: int, n_random: int = 3000, seed: int = 20260929) -> dict:
    """Все бары нужны целиком (прогрев медиан); точки отбираются по времени [lo, hi]."""
    times, closes, vols = _cols(bars)
    events, med_m = _detect(times, closes, vols)
    events = [e for e in events if lo <= e["start_time"] <= hi]
    for e in events:
        c = continuation(times, closes, e["end_idx"], e["dir"], e["med"])
        e.update(f5=c[5], f15=c[15], f60=c[60], size=round(e["size"], 3),
                 med=round(e["med"], 4))
    starts = [e["start_time"] for e in events]

    # Контроль слоя 1: случайные минуты с сырым ходом (направление d5 ставит отчёт)
    # и меткой «внутри импульса +-10 минут», такие отчёт выбрасывает.
    pool = [i for i in range(5, len(times)) if lo <= times[i] <= hi and med_m[i]]
    spans = sorted((e["start_time"] - MERGE_SEC, e["end_time"] + MERGE_SEC) for e in events)
    span_lo = [a for a, _ in spans]
    rand = []
    for i in sorted(random.Random(seed).sample(pool, min(n_random, len(pool)))):
        c = continuation(times, closes, i, 1, med_m[i])
        k = bisect.bisect_right(span_lo, times[i]) - 1
        rand.append({"time": times[i], "med": round(med_m[i], 4), "f5": c[5], "f15": c[15],
                     "f60": c[60], "d5": _sign(closes[i] - closes[i - 5]),
                     "in_imp": int(k >= 0 and times[i] <= spans[k][1])})

    layers = {f"m{tf}_{f}_{s}": cross_layer(times, closes, med_m, starts, lo, hi, tf, f, s,
                                            hz, bk, n_random, seed)
              for tf, f, s, hz, bk in CROSS_CONFIGS}
    return {"n_bars": len(times), "bars_from": times[0], "bars_to": times[-1],
            "lo": lo, "hi": hi,
            "events": [{k: v for k, v in e.items() if k not in ("start_idx", "end_idx")}
                       for e in events],
            "random": rand, "cross_layers": layers}


def _load_bars(symbol: str) -> list:
    """Тот же путь, что у агента (_bars_for): готовая склейка хостера по ключу."""
    import httpx
    api = os.environ.get("STL_API", "https://stl.shectory.ru").rstrip("/")
    r = httpx.get(f"{api}/api/v1/agent/bars/{symbol}",
                  headers={"X-Agent-Token": os.environ.get("OPT_AGENT_TOKEN", "")}, timeout=300)
    r.raise_for_status()
    return r.json().get("rows") or []


def _utc(d: str) -> int:
    x = date.fromisoformat(d)
    return int(datetime(x.year, x.month, x.day, tzinfo=timezone.utc).timestamp())


def run(symbol, date_from: str | None = None, date_to: str | None = None) -> dict:
    """Точка входа для задачи i9. Агент зовёт func(arg) одним аргументом, поэтому
    принимает и run(["RI", "2025-09-26", "2026-09-10"])."""
    if isinstance(symbol, (list, tuple)):
        symbol, date_from, date_to = symbol
    rows = sorted(_load_bars(symbol), key=lambda r: r[0])
    lo, hi = _utc(date_from), _utc(date_to) + 86399
    if not rows or rows[0][0] > lo - 86400 or rows[-1][0] < hi - 3 * 86400:
        return {"error": f"бары {symbol} не покрывают окно с прогревом: "
                         f"{rows[0][0] if rows else None}..{rows[-1][0] if rows else None}"}
    out = analyze(rows, lo, hi)
    out["symbol"] = symbol
    return out
