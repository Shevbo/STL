"""Отчёт по гипотезе «тихая лента + импульс» и по пересечениям средних.

Берёт результат trader/lab/impulse_news.run (с i9: agent_tasks.result по id задачи,
либо json-файл) и архив новостей Интерфакса (~/news-archive/interfax-YYYY-MM-DD.jsonl),
считает всё по определениям, зафиксированным в докстринге trader/lab/impulse_news.py.
Здесь только лёгкий join и бутстрап, тяжёлого счёта нет.

Время: бары МСК-как-UTC, у новостей ts_unix настоящий UTC, поэтому к новостям +3 часа.
Покрытие новостями = дни, за которые есть файл архива; импульс вне покрытия в долю
«тихих» не входит.

ПРАВКИ ПО ИТОГАМ НЕЗАВИСИМОЙ ПРОВЕРКИ 29.09.2026 (imp-ri-1):
- продолжение (f5/f15/f60 в events/events_novol) уже посчитано модулем от ПЕРВОГО
  срабатывания цепочки, не от конца - здесь только разрез и бутстрап;
- события с дырой в ленте у первого срабатывания (gap0) исключаются перед всеми
  статистиками, причины считаются отдельно (split_by_gap);
- контроль сдвинутой ленты - только сдвиги, кратные 7 дням (день недели сохранён);
- разрез по объёму (vol_x) уже от первого срабатывания, max_min_vol_x - справочно;
- пересечения средних: «до» для реальных пересечений не сравнивается со случайными
  (тавтология отбора), «после» сравнивается со случайными, перевзвешенными под
  часовое распределение пересечений (reweight_sample);
- добавлена группа «ценовые импульсы без объёма» (events_novol).

    python scripts/impulse_news_report.py --task-id imp-ri-1
    python scripts/impulse_news_report.py --file events.json --news-dir ~/news-archive
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import glob
import json
import os
import random
from collections import defaultdict
from datetime import date, datetime, timezone

MSK = 3 * 3600
WIN_BEFORE, WIN_AFTER = 600, 60          # окно новостей: начало - 10 мин .. начало + 1 мин
WIN_MIN = 11
B = 1000
NIGHT_GAP_SEC = 600                      # порог «разрыв у первого срабатывания» (10 мин)
ROLL_GAP_SEC = 86400                     # отдельно считаем дыру ролла (>1 суток)
SHIFT_STEPS = tuple(range(7, 71, 7))     # контроль ленты: 10 сдвигов, кратных 7 дням


def load_result(task_id: str | None, path: str | None) -> dict:
    if path:
        with open(path, encoding="utf-8") as f:
            res = json.load(f)
    else:
        import asyncpg

        async def _get():
            c = await asyncpg.connect(os.environ["LAB_DB_URL"])
            try:
                return await c.fetchval("SELECT result::text FROM agent_tasks WHERE id=$1", task_id)
            finally:
                await c.close()
        res = json.loads(asyncio.run(_get()) or "null")
    if isinstance(res, list):          # агент кладёт список результатов по юнитам
        res = res[0]
    if not res or res.get("error"):
        raise SystemExit(f"нет результата: {res and res.get('error')}")
    return res


def load_news(news_dir: str, skip_last: bool = False) -> tuple[list[int], list[date]]:
    """(отсортированные метки новостей МСК-как-UTC, покрытые дни).

    skip_last: пока сбор архива идёт, самый свежий файл может быть недописан.
    """
    ts, days = [], []
    files = sorted(glob.glob(os.path.join(os.path.expanduser(news_dir), "interfax-*.jsonl")))
    for p in files[:-1] if skip_last else files:
        days.append(date.fromisoformat(os.path.basename(p)[9:19]))
        with open(p, encoding="utf-8") as f:
            ts += [json.loads(x)["ts_unix"] + MSK for x in f if x.strip()]
    return sorted(ts), days


def _day(t: int) -> date:
    return datetime.fromtimestamp(t, timezone.utc).date()


def _count(news: list[int], a: int, b: int) -> int:
    return bisect.bisect_right(news, b) - bisect.bisect_left(news, a)


def activity(news: list[int], days: list[date]) -> dict:
    """(день недели, час) -> ожидаемое число новостей на 11-минутное окно."""
    cnt: dict = defaultdict(int)
    for t in news:
        d = datetime.fromtimestamp(t, timezone.utc)
        cnt[(d.weekday(), d.hour)] += 1
    ndays: dict = defaultdict(int)
    for d in days:
        ndays[d.weekday()] += 1
    return {(w, h): cnt[(w, h)] / (ndays[w] * 60) * WIN_MIN
            for w in range(7) for h in range(24) if ndays[w]}


def _key(t: int) -> tuple[int, int]:
    d = datetime.fromtimestamp(t, timezone.utc)
    return d.weekday(), d.hour


def ci(vals: list[float], seed: int = 1) -> str:
    vals = [v for v in vals if v is not None]
    if not vals:
        return "n=0"
    rng = random.Random(seed)
    n = len(vals)
    means = sorted(sum(rng.choices(vals, k=n)) / n for _ in range(B))
    return f"{sum(vals) / n:+.3f} [{means[int(.025 * B)]:+.3f}; {means[int(.975 * B) - 1]:+.3f}] n={n}"


def pts(g: list[dict], h: int) -> list[float]:
    """Продолжение +h в ПУНКТАХ: f{h} (в медианах) x med (медиана первого срабатывания)."""
    return [x[f"f{h}"] * x["med"] for x in g if x.get(f"f{h}") is not None and x.get("med")]


def split_by_gap(evs: list[dict]) -> tuple[list[dict], int, int]:
    """(чистые, искл. разрыв 10мин..1сутки, искл. дыра ролла >1сутки) по полю gap0.

    gap0 (из trader/lab/impulse_news.py) - секунд между баром t-5 и баром t первого
    срабатывания; в норме 300. Окно продолжения, начавшееся с дырой в ленте (ночной
    перерыв, выходной, ролл), не описывает реальный ход - это заглядывание в разрыв,
    а не измерение импульса.
    """
    clean, gap_n, roll_n = [], 0, 0
    for e in evs:
        g = e.get("gap0", 0)
        if g > ROLL_GAP_SEC:
            roll_n += 1
        elif g > NIGHT_GAP_SEC:
            gap_n += 1
        else:
            clean.append(e)
    return clean, gap_n, roll_n


def reweight_sample(rnd: list[dict], cross: list[dict], seed: int = 1,
                    n: int | None = None) -> list[dict]:
    """Случайные точки, передискретизированные под часовое распределение пересечений.

    Пересечения кластеруются по часам активности рынка иначе, чем весь пул случайных
    баров (ночь/обед реже дают пересечение) - сравнение «после» с сырым random молча
    сравнивает разные времена суток. Берём случайные точки с заменой: час каждой -
    случайный час ИЗ пересечений (то есть с их же частотой по часам), внутри часа -
    случайная точка исходного пула того же часа.
    """
    by_h: dict[int, list[dict]] = defaultdict(list)
    for r in rnd:
        by_h[datetime.fromtimestamp(r["time"], timezone.utc).hour].append(r)
    hours = [datetime.fromtimestamp(x["time"], timezone.utc).hour for x in cross]
    if not hours:
        return []
    rng = random.Random(seed)
    out = []
    for _ in range(n or len(rnd) or len(cross)):
        pool = by_h.get(rng.choice(hours))
        if pool:
            out.append(rng.choice(pool))
    return out


def quiet_share(evs, news, covered: set, act: dict) -> tuple[int, int, int, int]:
    """(покрытых, тихих, покрытых в активные часы, тихих в активные часы)."""
    n = q = na = qa = 0
    for e in evs:
        s = e["start_time"]
        if _day(s - WIN_BEFORE) not in covered or _day(s + WIN_AFTER) not in covered:
            continue
        quiet = _count(news, s - WIN_BEFORE, s + WIN_AFTER) == 0
        n += 1
        q += quiet
        if act.get(_key(s), 0) >= 1:
            na += 1
            qa += quiet
    return n, q, na, qa


def shifted(news: list[int], days: list[date], k: int) -> list[int]:
    """Лента со сдвигом на k покрытых дней по кругу; время суток сохраняется."""
    idx = {d: i for i, d in enumerate(days)}
    out = []
    for t in news:
        i = idx.get(_day(t))
        if i is None:
            continue
        nd = days[(i + k) % len(days)]
        out.append(t + (nd - days[i]).days * 86400)
    return sorted(out)


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} = {100 * a / b:.1f}%" if b else f"{a}/0"


def report(res: dict, news: list[int], days: list[date]) -> None:
    covered = set(days)
    act = activity(news, days) if days else {}
    print(f"Символ {res.get('symbol')}: баров {res['n_bars']}, окно событий "
          f"{datetime.fromtimestamp(res['lo'], timezone.utc):%Y-%m-%d}.."
          f"{datetime.fromtimestamp(res['hi'], timezone.utc):%Y-%m-%d}")
    print(f"Покрытие новостей: {len(days)} дн. "
          f"({min(days) if days else '-'}..{max(days) if days else '-'}), заголовков {len(news)}")

    evs_raw = res["events"]
    evs, gap_n, roll_n = split_by_gap(evs_raw)
    novol_raw = res.get("events_novol", [])
    novol, novol_gap_n, novol_roll_n = split_by_gap(novol_raw)
    print(f"\n== Слой 1: импульсы ==\nИмпульсов в окне: {len(evs_raw)}; после исключений: {len(evs)} "
          f"(разрыв {NIGHT_GAP_SEC // 60}-{ROLL_GAP_SEC // 60} мин: {gap_n}, "
          f"дыра ролла >{ROLL_GAP_SEC // 86400} сут: {roll_n})")
    print(f"Ценовых импульсов без объёма: {len(novol_raw)}; после исключений: {len(novol)} "
          f"(разрыв: {novol_gap_n}, дыра ролла: {novol_roll_n})")
    n, q, na, qa = quiet_share(evs, news, covered, act)
    print(f"На покрытых днях: {n}; тихий фон всего: {_pct(q, n)}; "
          f"в часы с ожиданием >= 1 новости на окно: {_pct(qa, na)}")

    print("\nЧас МСК | импульсов (покр.) | тихих | ожид. новостей на 11 мин (ср. по дням недели)")
    by_h: dict = defaultdict(lambda: [0, 0])
    for e in evs:
        s = e["start_time"]
        if _day(s - WIN_BEFORE) in covered and _day(s + WIN_AFTER) in covered:
            h = datetime.fromtimestamp(s, timezone.utc).hour
            by_h[h][0] += 1
            by_h[h][1] += _count(news, s - WIN_BEFORE, s + WIN_AFTER) == 0
    for h in range(24):
        exp = [act[(w, h)] for w in range(7) if (w, h) in act]
        if by_h[h][0] or exp:
            print(f"  {h:02d}    | {by_h[h][0]:5d} | {by_h[h][1]:5d} | "
                  f"{(sum(exp) / len(exp)) if exp else 0:.2f}")

    # Группы для продолжения от ПЕРВОГО срабатывания (см. trader/lab/impulse_news.py):
    # тихие / тихие,активные часы / с новостями (item 1), случайные минуты (база),
    # ценовые без объёма (item 6, та же методика исключений).
    groups: dict = {"тихие": [], "тихие, активные часы": [], "с новостями": []}
    for e in evs:
        s = e["start_time"]
        if _day(s - WIN_BEFORE) not in covered or _day(s + WIN_AFTER) not in covered:
            continue
        if _count(news, s - WIN_BEFORE, s + WIN_AFTER):
            groups["с новостями"].append(e)
        else:
            groups["тихие"].append(e)
            if act.get(_key(s), 0) >= 1:
                groups["тихие, активные часы"].append(e)
    groups["все импульсы (без учёта новостей)"] = evs
    rnd = [dict(r, f5=r["d5"] * r["f5"] if r["f5"] is not None else None,
                f15=r["d5"] * r["f15"] if r["f15"] is not None else None,
                f60=r["d5"] * r["f60"] if r["f60"] is not None else None)
           for r in res["random"] if not r["in_imp"] and r["d5"]]
    groups["случайные минуты (по знаку хода за 5 баров)"] = rnd
    groups["ценовые импульсы без объёма"] = novol
    print("\nПродолжение от первого срабатывания в медианах 5-барного хода и в пунктах, "
          "среднее [95% бутстрап]:")
    for name, g in groups.items():
        print(f"  {name}")
        for h in (5, 15, 60):
            print(f"    +{h:>2} мин: {ci([x[f'f{h}'] for x in g])}   пт: {ci(pts(g, h))}")

    print("\nРазрез по кратности 5-барного объёма к медиане у ПЕРВОГО срабатывания "
          "(макс. минута внутри события - справочно):")
    for name, a, b in (("3-5x", 3, 5), ("5-10x", 5, 10), (">10x", 10, float("inf"))):
        g = [e for e in evs if a <= e["vol_x"] < b]
        n3, q3, na3, qa3 = quiet_share(g, news, covered, act)
        mx = sorted(e["max_min_vol_x"] for e in g if e.get("max_min_vol_x") is not None)
        print(f"  {name}: событий {len(g)}; тихих {_pct(q3, n3)}, в активные часы {_pct(qa3, na3)}; "
              f"макс. минутный объём внутри (кратность медианы, справочно): медиана "
              f"{mx[len(mx) // 2] if mx else '-'}, максимум {mx[-1] if mx else '-'}")
        for h in (5, 15, 60):
            print(f"    +{h:>2} мин: {ci([x[f'f{h}'] for x in g])}   пт: {ci(pts(g, h))}")

    print(f"\nКонтроль сдвинутой ленты (сдвиг кратен 7 дням, день недели сохранён; "
          f"{len(SHIFT_STEPS)} розыгрышей k={SHIFT_STEPS[0]}..{SHIFT_STEPS[-1]}):")
    print(f"  настоящая лента: тихих {_pct(q, n)}, в активные часы {_pct(qa, na)}")
    shares = []
    for k in SHIFT_STEPS:
        if len(days) < 2:
            print("  покрытых дней меньше двух, сдвиг невозможен")
            break
        sn = shifted(news, days, k)
        n2, q2, na2, qa2 = quiet_share(evs, sn, covered, activity(sn, days))
        shares.append(100 * q2 / n2 if n2 else 0.0)
        print(f"  k={k:3d}: тихих {_pct(q2, n2)}, в активные часы {_pct(qa2, na2)}")
    if shares:
        print(f"  доля тихих в контроле: мин {min(shares):.1f}%, макс {max(shares):.1f}%")

    print("\n== Слой 2: пересечения средних ==")
    for key, L in res.get("cross_layers", {}).items():
        hz, bk = L["horizons"], L["back_min"]
        xs, nm, rn = L["crosses"], L["near"], L["random"]
        print(f"\n[{key}] M{L['tf']} SMA{L['fast']}/SMA{L['slow']}: пересечений {len(xs)}, "
              f"ложных сближений {len(nm)}, случайных баров {len(rn)}")
        for name, g in (("пересечения", xs), ("ложные сближения", nm), ("случайные бары", rn)):
            print(f"  {name}:" + ("  (отбор смотрит на 30 баров вперёд: ход ПОСЛЕ смещён "
                                  "отбором, сравнимо только ДО)" if name == "ложные сближения" else ""))
            if name == "пересечения":
                b = [x["b"] for x in g if x["b"] is not None]
                print(f"    до, -{bk} мин: {sum(b) / len(b):+.3f} n={len(b)} "
                      "(справочно, по построению - пересечение уже отобрано по ходу до, "
                      "со случайными не сравнивать)")
            else:
                print(f"    до,  -{bk} мин, по направлению: {ci([x['b'] for x in g])}")
                print(f"    до,  -{bk} мин, по модулю:      "
                      f"{ci([abs(x['b']) for x in g if x['b'] is not None])}")
            for h in hz:
                v = [x[f"f{h}"] for x in g]
                print(f"    после +{h} мин, по направлению: {ci(v)}")
                print(f"    после +{h} мин, по модулю:      {ci([abs(x) for x in v if x is not None])}")
            print(f"    импульс слоя 1 начался в 30 мин после: {ci([x['imp30'] for x in g])}")
        # Item 5: честное сравнение "после" - не с сырыми случайными точками (тавтология
        # выше по часам), а со случайными, перевзвешенными под часовое распределение
        # пересечений (reweight_sample).
        rw = reweight_sample(rn, xs)
        print("  пересечения vs случайные, перевзвешенные по часам суток пересечений:")
        for h in hz:
            print(f"    после +{h} мин: {ci([x[f'f{h}'] for x in rw])}")
        imp_x = sum(x["imp30"] for x in xs) / len(xs) if xs else 0.0
        imp_rw = sum(x["imp30"] for x in rw) / len(rw) if rw else 0.0
        print(f"    доля импульсов после (imp30): пересечения {imp_x:.3f} против "
              f"перевзвешенных случайных {imp_rw:.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id")
    ap.add_argument("--file")
    ap.add_argument("--news-dir", default="~/news-archive")
    ap.add_argument("--skip-last", action="store_true", help="не брать последний файл архива")
    a = ap.parse_args()
    if not (a.task_id or a.file):
        ap.error("нужен --task-id или --file")
    news, days = load_news(a.news_dir, a.skip_last)
    report(load_result(a.task_id, a.file), news, days)


if __name__ == "__main__":
    main()
