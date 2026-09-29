"""Отчёт по гипотезе «тихая лента + импульс» и по пересечениям средних.

Берёт результат trader/lab/impulse_news.run (с i9: agent_tasks.result по id задачи,
либо json-файл) и архив новостей Интерфакса (~/news-archive/interfax-YYYY-MM-DD.jsonl),
считает всё по определениям, зафиксированным в докстринге trader/lab/impulse_news.py.
Здесь только лёгкий join и бутстрап, тяжёлого счёта нет.

Время: бары МСК-как-UTC, у новостей ts_unix настоящий UTC, поэтому к новостям +3 часа.
Покрытие новостями = дни, за которые есть файл архива; импульс вне покрытия в долю
«тихих» не входит.

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
    evs = res["events"]
    covered = set(days)
    act = activity(news, days) if days else {}
    print(f"Символ {res.get('symbol')}: баров {res['n_bars']}, окно событий "
          f"{datetime.fromtimestamp(res['lo'], timezone.utc):%Y-%m-%d}.."
          f"{datetime.fromtimestamp(res['hi'], timezone.utc):%Y-%m-%d}")
    print(f"Покрытие новостей: {len(days)} дн. "
          f"({min(days) if days else '-'}..{max(days) if days else '-'}), заголовков {len(news)}")
    print(f"\n== Слой 1: импульсы ==\nИмпульсов в окне: {len(evs)}")
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
    print("\nПродолжение в медианах 5-барного хода, среднее [95% бутстрап]:")
    for name, g in groups.items():
        print(f"  {name}")
        for h in (5, 15, 60):
            print(f"    +{h:>2} мин: {ci([x[f'f{h}'] for x in g])}")

    print("\nРазрез по кратности 5-барного объёма к медиане (макс. по срабатываниям события):")
    for name, a, b in (("3-5x", 3, 5), ("5-10x", 5, 10), (">10x", 10, float("inf"))):
        g = [e for e in evs if a <= e["vol_x"] < b]
        n3, q3, na3, qa3 = quiet_share(g, news, covered, act)
        mx = sorted(e["max_min_vol_x"] for e in g if e.get("max_min_vol_x") is not None)
        print(f"  {name}: событий {len(g)}; тихих {_pct(q3, n3)}, в активные часы {_pct(qa3, na3)}; "
              f"макс. минутный объём внутри (кратность медианы): медиана "
              f"{mx[len(mx) // 2] if mx else '-'}, максимум {mx[-1] if mx else '-'}")
        for h in (5, 15, 60):
            print(f"    +{h:>2} мин: {ci([x[f'f{h}'] for x in g])}")

    print("\nКонтроль сдвинутой ленты (сдвиг по кругу на k покрытых дней, 5 розыгрышей):")
    print(f"  настоящая лента: тихих {_pct(q, n)}, в активные часы {_pct(qa, na)}")
    rng = random.Random(20260929)
    for _ in range(5):
        if len(days) < 2:
            print("  покрытых дней меньше двух, сдвиг невозможен")
            break
        k = rng.randint(1, len(days) - 1) if len(days) < 11 else rng.randint(5, len(days) - 5)
        sn = shifted(news, days, k)
        n2, q2, na2, qa2 = quiet_share(evs, sn, covered, activity(sn, days))
        print(f"  k={k:3d}: тихих {_pct(q2, n2)}, в активные часы {_pct(qa2, na2)}")

    print("\n== Слой 2: пересечения средних ==")
    for key, L in res.get("cross_layers", {}).items():
        hz, bk = L["horizons"], L["back_min"]
        print(f"\n[{key}] M{L['tf']} SMA{L['fast']}/SMA{L['slow']}: пересечений {len(L['crosses'])}, "
              f"ложных сближений {len(L['near'])}, случайных баров {len(L['random'])}")
        for name, g in (("пересечения", L["crosses"]), ("ложные сближения", L["near"]),
                        ("случайные бары", L["random"])):
            print(f"  {name}:" + ("  (отбор смотрит на 30 баров вперёд: ход ПОСЛЕ смещён "
                                  "отбором, сравнимо только ДО)" if name == "ложные сближения" else ""))
            print(f"    до,  -{bk} мин, по направлению: {ci([x['b'] for x in g])}")
            print(f"    до,  -{bk} мин, по модулю:      "
                  f"{ci([abs(x['b']) for x in g if x['b'] is not None])}")
            for h in hz:
                v = [x[f"f{h}"] for x in g]
                print(f"    после +{h} мин, по направлению: {ci(v)}")
                print(f"    после +{h} мин, по модулю:      {ci([abs(x) for x in v if x is not None])}")
            print(f"    импульс слоя 1 начался в 30 мин после: {ci([x['imp30'] for x in g])}")


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
