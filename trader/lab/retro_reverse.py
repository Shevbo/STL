"""Ретро-реверс: подбор алгоритма движения цены и оценка совпадения 0-100%.

Проект и правила чтения: docs/retro-reverse-design.md. Коротко о главном:

- «Алгоритм» = правило, дающее прогноз следующего шага в трёх состояниях
  (вверх/вниз/на месте). Первый класс — цепь Маркова порядка k по квантованным
  приращениям: подгонка это подсчёт частот, подгонять под результат нечем.
- Оценка НЕ доля совпадений, а превосходство над наивной базой, приведённое к
  0-100. Сырая доля льстит: «завтра как сегодня» даёт 99%, «всегда вверх» на
  растущей неделе 55-60%, «на месте» при широком пороге — долю флэтов.
- Порог квантования и база берутся ИЗ ОКНА ПОДГОНКИ. Взять их из всей истории —
  заглядывание в будущее.
- Любая оценка сравнивается с оценкой на ПЕРЕМЕШАННОМ ряде той же длины.
  Содержательной считается только та, что выше 95-го процентиля шума.
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass

UP, FLAT, DOWN = 1, 0, -1


def quantize(closes: list[float], thr_frac: float = 0.5) -> tuple[list[int], float]:
    """Приращения -> символы. Порог = thr_frac от СРЕДНЕГО |приращения| окна.

    Порог именно относительный: на минутках RI и на минутках GD абсолютные
    величины различаются в сотни раз, а доля флэтов должна оставаться сравнимой.
    Возвращает символы и сам порог, чтобы его можно было применить к окну
    проверки БЕЗ пересчёта по будущим данным.
    """
    d = [b - a for a, b in zip(closes, closes[1:])]
    if not d:
        return [], 0.0
    thr = thr_frac * (sum(abs(x) for x in d) / len(d))
    return [UP if x > thr else (DOWN if x < -thr else FLAT) for x in d], thr


def apply_threshold(closes: list[float], thr: float) -> list[int]:
    """Квантование окна проверки порогом, посчитанным на окне подгонки."""
    return [UP if (b - a) > thr else (DOWN if (b - a) < -thr else FLAT)
            for a, b in zip(closes, closes[1:])]


@dataclass
class Markov:
    """Цепь порядка k: какой символ чаще следует за цепочкой из k предыдущих."""
    k: int
    table: dict[tuple[int, ...], int]
    fallback: int

    def predict(self, hist: list[int]) -> int:
        if self.k == 0:
            return self.fallback
        key = tuple(hist[-self.k:])
        return self.table.get(key, self.fallback)


def fit(symbols: list[int], k: int) -> Markov:
    """Подгонка = подсчёт частот. Ничего не оптимизируется."""
    fallback = Counter(symbols).most_common(1)[0][0] if symbols else FLAT
    if k == 0:
        return Markov(0, {}, fallback)
    cnt: dict[tuple[int, ...], Counter] = defaultdict(Counter)
    for i in range(k, len(symbols)):
        cnt[tuple(symbols[i - k:i])][symbols[i]] += 1
    table = {key: c.most_common(1)[0][0] for key, c in cnt.items()}
    return Markov(k, table, fallback)


def accuracy(model: Markov, symbols: list[int]) -> float:
    """Доля верных прогнозов на ПОСЛЕДОВАТЕЛЬНОСТИ, которую модель не видела."""
    if len(symbols) <= model.k:
        return 0.0
    hit = tot = 0
    for i in range(model.k, len(symbols)):
        hit += int(model.predict(symbols[:i]) == symbols[i])
        tot += 1
    return hit / tot if tot else 0.0


def score(model: Markov, test: list[int], base_symbol: int) -> float:
    """Оценка 0-100: превосходство над наивной базой окна подгонки.

    База = «всегда самый частый символ окна подгонки». Тогда «нет умения» это
    ноль, а не доля флэтов и не доля роста. Хуже базы = ноль: алгоритм, который
    проигрывает константе, это отсутствие алгоритма.
    """
    if len(test) <= model.k:
        return 0.0
    acc = accuracy(model, test)
    tail = test[model.k:]
    base = sum(1 for s in tail if s == base_symbol) / len(tail)
    if base >= 1.0:
        return 0.0                      # база безошибочна: мерить нечего
    return max(0.0, min(1.0, (acc - base) / (1.0 - base))) * 100.0


def walk(closes: list[float], fit_len: int, test_len: int, k: int,
         thr_frac: float = 0.5, step: int | None = None) -> list[float]:
    """Скольжение: подгонка на fit_len барах, оценка на следующих test_len.

    Возвращает список оценок — РАСПРЕДЕЛЕНИЕ, а не одно число: одна оценка на
    одном окне ничего не значит, у неё нет ни ошибки, ни сравнения с шумом.
    """
    step = step or test_len
    out: list[float] = []
    i = 0
    while i + fit_len + test_len + 1 <= len(closes):
        f = closes[i:i + fit_len + 1]
        t = closes[i + fit_len:i + fit_len + test_len + 1]
        sym, thr = quantize(f, thr_frac)
        model = fit(sym, k)
        base_symbol = Counter(sym).most_common(1)[0][0] if sym else FLAT
        out.append(score(model, apply_threshold(t, thr), base_symbol))
        i += step
    return out


def noise_floor(closes: list[float], fit_len: int, test_len: int, k: int,
                thr_frac: float = 0.5, draws: int = 20,
                seed: int = 20260929) -> list[float]:
    """Уровень шума: то же самое на ПЕРЕМЕШАННЫХ приращениях.

    Распределение шагов сохранено, порядок разрушен — значит любая оценка здесь
    получена без закономерности. Её 95-й процентиль и есть порог, выше которого
    настоящая оценка становится содержательной.
    """
    d = [b - a for a, b in zip(closes, closes[1:])]
    rng = random.Random(seed)
    out: list[float] = []
    for _ in range(draws):
        rng.shuffle(d)
        px, cur = [closes[0]], closes[0]
        for x in d:
            cur += x
            px.append(cur)
        out.extend(walk(px, fit_len, test_len, k, thr_frac))
    return out


def _survival_row(weeks: int, real: list[float], floor: list[float]) -> dict:
    """Свёртка распределения real/floor в одну строку отчёта. Вынесена из
    survival(), чтобы survival_segmented() мог объединить оценки НЕСКОЛЬКИХ
    сегментов (после дыры экспирации) до подсчёта медианы/перцентиля — иначе
    каждый сегмент считался бы отдельной строкой и терял сравнимость."""
    if not real:
        return {"weeks": weeks, "n": 0}
    real_s = sorted(real)
    floor_s = sorted(floor) if floor else [0.0]
    p95 = floor_s[min(len(floor_s) - 1, int(0.95 * len(floor_s)))]
    med = real_s[len(real_s) // 2]
    return {"weeks": weeks, "n": len(real), "median": med,
            "best": real_s[-1], "noise_p95": p95,
            "above_noise": sum(1 for x in real if x > p95),
            "verdict": "содержательно" if med > p95 else "в пределах шума"}


def survival(closes: list[float], weeks: tuple[int, ...] = (1, 2, 4, 8, 16),
             bars_per_week: int = 5 * 14 * 60, test_weeks: int = 1,
             k: int = 2, thr_frac: float = 0.5, draws: int = 10) -> list[dict]:
    """Выживаемость: как ведёт себя оценка при удлинении окна подгонки.

    Окно проверки ФИКСИРОВАНО (по умолчанию неделя), чтобы числа были сравнимы
    между шагами. Для каждой длины считается и распределение оценок, и уровень
    шума на перемешанном ряде — читать их можно только вместе.
    """
    test_len = test_weeks * bars_per_week
    out = []
    for w in weeks:
        fit_len = w * bars_per_week
        real = walk(closes, fit_len, test_len, k, thr_frac)
        floor = noise_floor(closes, fit_len, test_len, k, thr_frac, draws=draws) if real else []
        out.append(_survival_row(w, real, floor))
    return out


def survival_segmented(segments: list[list[float]], weeks: tuple[int, ...] = (1, 2, 4, 8, 16),
                        bars_per_week: int = 5 * 14 * 60, test_weeks: int = 1,
                        k: int = 2, thr_frac: float = 0.5, draws: int = 10) -> list[dict]:
    """Как survival(), но по НЕСКОЛЬКИМ непрерывным сегментам одного ряда:
    walk() режет окно по числу баров, а не по времени, поэтому один сегмент
    нужен на каждый непрерывный кусок (см. split_segments) — иначе окно
    перескочит дыру после экспирации. Оценки и уровень шума объединяются по
    сегментам ДО подсчёта медианы/перцентиля для каждой длины окна."""
    test_len = test_weeks * bars_per_week
    out = []
    for w in weeks:
        fit_len = w * bars_per_week
        real: list[float] = []
        floor: list[float] = []
        for closes in segments:
            r = walk(closes, fit_len, test_len, k, thr_frac)
            if r:
                real.extend(r)
                floor.extend(noise_floor(closes, fit_len, test_len, k, thr_frac, draws=draws))
        out.append(_survival_row(w, real, floor))
    return out


def split_segments(rows: list[list], gap_days: float = 3.0) -> list[list[list]]:
    """Режет ряд баров на непрерывные сегменты по времени.

    rows — [[time, open, high, low, close, volume], ...], отсортированы по
    time (unix-секунды). Разрыв между соседними барами БОЛЬШЕ gap_days суток —
    граница сегмента (экспирационные дыры ряда — 12-18 суток). rows должны
    прийти отсортированными; функция режет по факту порядка, не сортирует.
    """
    if not rows:
        return []
    gap_sec = gap_days * 86400
    segments = [[rows[0]]]
    for prev, cur in zip(rows, rows[1:]):
        if cur[0] - prev[0] > gap_sec:
            segments.append([])
        segments[-1].append(cur)
    return segments


def _bars_per_week(rows: list[list]) -> int:
    """Медианное число баров на календарную (ISO) неделю по факту ряда —
    устойчивее среднего к урезанным первой/последней неделе покрытия."""
    from datetime import datetime, timezone
    weeks: dict[tuple[int, int], int] = defaultdict(int)
    for r in rows:
        iso = datetime.fromtimestamp(r[0], tz=timezone.utc).isocalendar()
        weeks[(iso[0], iso[1])] += 1
    counts = sorted(weeks.values())
    return counts[len(counts) // 2] if counts else 0


def _load_bars(symbol_key: str) -> list[list]:
    """Тот же путь, что у агента (opt_agent._bars_for / impulse_news._load_bars):
    готовая склейка хостера GET /api/v1/agent/bars/<key> = agent_bars/<key>.json."""
    import os
    import httpx
    api = os.environ.get("STL_API", "https://stl.shectory.ru").rstrip("/")
    r = httpx.get(f"{api}/api/v1/agent/bars/{symbol_key}",
                  headers={"X-Agent-Token": os.environ.get("OPT_AGENT_TOKEN", "")}, timeout=300)
    r.raise_for_status()
    return r.json().get("rows") or []


def run(symbol_key, ks=(1, 2, 3), thrs=(0.5, 1.0), weeks=(1, 2, 4, 8, 16),
        test_weeks: int = 1, draws: int = 10) -> dict:
    """Точка входа для задачи агента (agent_tasks, kind='task'). Очередь фанит
    args по элементам списка на отдельные вызовы func(a) — один запуск на весь
    ряд оформляется как args=[[symbol_key, ks, thrs, weeks]], поэтому этот
    run() принимает и такой упакованный список одним аргументом (как
    trader/lab/impulse_news.run).

    Грузит ряд по symbol_key тем же каналом, что агент, режет на непрерывные
    сегменты (split_segments, дыра > 3 суток), считает bars_per_week как
    медиану по ряду и прогоняет survival_segmented по сетке (k, thr_frac).
    """
    if isinstance(symbol_key, (list, tuple)):
        symbol_key, ks, thrs, weeks = symbol_key
    rows = sorted(_load_bars(symbol_key), key=lambda r: r[0])
    if not rows:
        return {"error": f"нет баров для {symbol_key}", "symbol": symbol_key}
    seg_rows = split_segments(rows)
    segments = [[r[4] for r in seg] for seg in seg_rows]
    bpw = _bars_per_week(rows)
    combos = []
    for k in ks:
        for t in thrs:
            combos.append({"k": k, "thr_frac": t,
                           "survival": survival_segmented(segments, tuple(weeks), bpw,
                                                           test_weeks, k, t, draws)})
    return {"symbol": symbol_key, "n_bars": len(rows), "bars_per_week": bpw,
            "n_segments": len(segments), "segment_lens": [len(s) for s in segments],
            "segment_spans": [[seg[0][0], seg[-1][0]] for seg in seg_rows],
            "combos": combos}


def demo() -> None:
    """Самопроверка на синтетике: детерминированный ряд, шум, тренд."""
    # 1) Детерминированная пила с периодом 4 — алгоритм обязан её взять.
    saw = []
    px = 100.0
    for i in range(4000):
        px += (10.0 if i % 4 in (0, 1) else -10.0)
        saw.append(px)
    s = walk(saw, 500, 200, k=3, thr_frac=0.5)
    assert s and min(s) > 90, f"пила не распознана: {s[:3]}"

    # 2) Чистый шум — оценка обязана быть около нуля.
    rng = random.Random(1)
    noise = [100.0]
    for _ in range(4000):
        noise.append(noise[-1] + rng.gauss(0, 10))
    s = walk(noise, 500, 200, k=2, thr_frac=0.5)
    assert sum(s) / len(s) < 20, f"на шуме оценка слишком высока: {sum(s)/len(s):.1f}"

    # 3) Тренд без структуры: «всегда вверх» НЕ должно давать высокой оценки —
    #    именно это и есть ловушка сырой доли совпадений.
    trend = [100.0 + 0.5 * i + rng.gauss(0, 5) for i in range(4000)]
    s = walk(trend, 500, 200, k=0, thr_frac=0.5)
    assert sum(s) / len(s) < 20, f"тренд принят за алгоритм: {sum(s)/len(s):.1f}"
    print("самопроверка ретро-реверса пройдена")


if __name__ == "__main__":
    demo()
