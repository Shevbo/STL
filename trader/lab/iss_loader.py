"""
MOEX ISS async loader for FORTS futures minute bars.

Mirrors the core logic of docs/Source_update.ps1:
- Fetch candles for a specific contract (e.g. RIM6) directly.
- For a base code (e.g. RI), enumerate all series, determine the front
  contract per calendar day, and return continuous bars without duplication.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from trader.lab.runtime import Bar

_ISS_BASE = "https://iss.moex.com/iss"
_MONTH_LETTERS = "FGHJKMNQUVXZ"  # Jan-Dec per FORTS convention
_PAGE = 500


async def top_instruments(n: int, always: list[str] | None = None) -> list[str]:
    """Top-n FORTS futures by today's turnover (front contract per asset), via ISS.

    Front contracts of `always` assets (e.g. RTS/RI) are force-included. Shared by
    the campaign-enqueue scripts (was copy-pasted in optimize_adaptive + enqueue_campaign)."""
    url = ("https://iss.moex.com/iss/engines/futures/markets/forts/securities.json"
           "?iss.meta=off&iss.only=securities,marketdata"
           "&securities.columns=SECID,ASSETCODE,LASTTRADEDATE"
           "&marketdata.columns=SECID,VALTODAY")
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "STL/1.0"}) as c:
        j = (await c.get(url)).json()
    sec, md = j.get("securities", {}), j.get("marketdata", {})
    turn = {dict(zip(md["columns"], r)).get("SECID"): (dict(zip(md["columns"], r)).get("VALTODAY") or 0)
            for r in md.get("data", [])}
    by_asset: dict = {}
    for row in sec.get("data", []):
        d = dict(zip(sec["columns"], row))
        sid, asset, ltd = d.get("SECID"), d.get("ASSETCODE"), d.get("LASTTRADEDATE")
        if not sid or not asset:
            continue
        vt = turn.get(sid, 0) or 0
        cur = by_asset.get(asset)
        if cur is None:
            by_asset[asset] = {"front": sid, "ltd": ltd, "turn": vt}
        else:
            cur["turn"] += vt
            if ltd and (cur["ltd"] is None or ltd < cur["ltd"]):
                cur["front"], cur["ltd"] = sid, ltd
    ranked = sorted(by_asset.values(), key=lambda x: x["turn"], reverse=True)
    syms = [a["front"] for a in ranked[:n] if a["turn"] > 0]
    for asset in (always or []):
        a = by_asset.get(asset)
        if a and a["front"] not in syms:
            syms.append(a["front"])
    return syms


def _to_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _quarter_start(d: date) -> date:
    """First day of the quarter containing d."""
    q0 = (d.month - 1) // 3
    return date(d.year, 1 + 3 * q0, 1)


def _month_last(year: int, month: int) -> date:
    # First day of next month minus one day
    if month == 12:
        return date(year + 1, 1, 1) - timedelta(days=1)
    return date(year, month + 1, 1) - timedelta(days=1)


class IssLoader:
    def __init__(self, engine: str = "futures", market: str = "forts") -> None:
        self._engine = engine
        self._market = market
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "IssLoader":
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "STL-IssLoader/1.0", "Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    # ── low-level ISS calls ──────────────────────────────────────────────────

    async def _get(self, url: str) -> dict:
        assert self._client, "Use as async context manager"
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp.json()

    async def _candles_page(
        self, secid: str, interval: int, from_date: str, till_date: str, start: int
    ) -> list[list]:
        url = (
            f"{_ISS_BASE}/engines/{self._engine}/markets/{self._market}"
            f"/securities/{secid}/candles.json"
            f"?iss.meta=off&interval={interval}&from={from_date}&till={till_date}&start={start}"
        )
        j = await self._get(url)
        return j.get("candles", {}).get("data") or []

    async def _candles_all(
        self, secid: str, interval: int, from_date: str, till_date: str
    ) -> list[list]:
        rows: list[list] = []
        start = 0
        while True:
            page = await self._candles_page(secid, interval, from_date, till_date, start)
            rows.extend(page)
            if len(page) < _PAGE:
                break
            start += len(page)
        return rows

    async def _has_candles(
        self, secid: str, interval: int, from_date: str, till_date: str
    ) -> bool:
        page = await self._candles_page(secid, interval, from_date, till_date, 0)
        return len(page) > 0

    async def get_security_meta(self, secid: str) -> dict | None:
        """Return {LastTrade, LastDel, ShortName} or None if not listed."""
        url = (
            f"{_ISS_BASE}/engines/{self._engine}/markets/{self._market}"
            f"/securities/{secid}.json"
            "?iss.meta=off&iss.only=securities"
            "&securities.columns=LASTTRADEDATE,LASTDELDATE,SHORTNAME"
        )
        try:
            j = await self._get(url)
            data = j.get("securities", {}).get("data") or []
            cols = j.get("securities", {}).get("columns") or []
            if not data:
                return None
            row = data[0]
            m = dict(zip(cols, row))
            lts, lds = str(m.get("LASTTRADEDATE", "") or ""), str(m.get("LASTDELDATE", "") or "")
            if not lts or not lds:
                return None
            return {
                "LastTrade": _to_date(lts),
                "LastDel": _to_date(lds),
                "ShortName": str(m.get("SHORTNAME") or ""),
            }
        except Exception:
            return None

    # ── row → Bar conversion ─────────────────────────────────────────────────

    @staticmethod
    def _row_to_bar(row: list) -> Bar | None:
        """ISS candles columns: open, close, high, low, value, volume, begin, end"""
        try:
            begin_str = str(row[6])
            dt = datetime.strptime(begin_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return Bar(
                time=int(dt.timestamp()),
                open=float(row[0]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[1]),
                volume=int(row[5]),
            )
        except Exception:
            return None

    # ── public API: single contract ──────────────────────────────────────────

    async def fetch_contract_bars(
        self,
        secid: str,
        date_from: date,
        date_to: date,
        interval: int = 1,
    ) -> list[Bar]:
        """Fetch minute bars for a specific contract (e.g. RIM6)."""
        rows = await self._candles_all(
            secid, interval,
            date_from.strftime("%Y-%m-%d"),
            date_to.strftime("%Y-%m-%d"),
        )
        bars = [b for r in rows if (b := self._row_to_bar(r)) is not None]
        bars.sort(key=lambda b: b.time)
        return bars

    # ── public API: continuous (front-contract roll) ─────────────────────────

    async def fetch_continuous_bars(
        self,
        base_code: str,
        date_from: date,
        date_to: date,
        interval: int = 1,
    ) -> list[Bar]:
        """
        Fetch continuous minute bars for a base code (e.g. RI).
        For each calendar day selects the front contract (min LastDel
        among those still trading), mirrors Source_update.ps1 logic.
        """
        min_year = max(1, date_from.year - 2)
        max_year = date_to.year + 1

        # 1. Build candidate series list
        plans: list[dict] = []
        for letter in _MONTH_LETTERS:
            del_month = _MONTH_LETTERS.index(letter) + 1
            for digit in range(10):
                secid = f"{base_code}{letter}{digit}"
                meta = await self.get_security_meta(secid)
                # Which years match this digit?
                years = [y for y in range(min_year, max_year + 1) if y % 10 == digit]
                for year in sorted(years, reverse=True):
                    if meta and meta["LastDel"].year != year:
                        continue
                    win = self._contract_window(year, del_month, meta)
                    if win is None:
                        continue
                    rng_from = max(date_from, win["from"])
                    rng_till = min(date_to, win["to"])
                    if rng_from > rng_till:
                        continue
                    # Quick probe: does ISS have data in this range?
                    if not await self._has_candles(
                        secid, interval,
                        rng_from.strftime("%Y-%m-%d"),
                        rng_till.strftime("%Y-%m-%d"),
                    ):
                        continue
                    last_trade = meta["LastTrade"] if meta else win["to"]
                    last_del = meta["LastDel"] if meta else win["to"]
                    plans.append({
                        "secid": secid,
                        "trade_from": win["from"],
                        "last_trade": last_trade,
                        "last_del": last_del,
                        "fetch_from": rng_from,
                        "fetch_till": rng_till,
                    })
                    break

        if not plans:
            return []

        # 2. Build day → front-contract map
        primary_by_day: dict[date, dict] = {}
        cur = date_from
        while cur <= date_to:
            candidates = [
                p for p in plans
                if p["trade_from"] <= cur <= p["last_trade"]
            ]
            if candidates:
                front = min(candidates, key=lambda p: p["last_del"])
                primary_by_day[cur] = front
            cur += timedelta(days=1)

        # 3. Fetch only front-contract segments
        all_bars: list[Bar] = []
        fetched_secids: set[str] = set()

        for plan in plans:
            secid = plan["secid"]
            # Collect days where this plan is the front
            front_days = [
                d for d, fp in primary_by_day.items()
                if fp["secid"] == secid
            ]
            if not front_days:
                continue

            # Merge into contiguous segments
            front_days.sort()
            segments: list[tuple[date, date]] = []
            seg_start = front_days[0]
            seg_end = front_days[0]
            for d in front_days[1:]:
                if (d - seg_end).days <= 1:
                    seg_end = d
                else:
                    segments.append((seg_start, seg_end))
                    seg_start = seg_end = d
            segments.append((seg_start, seg_end))

            for seg_from, seg_till in segments:
                rows = await self._candles_all(
                    secid, interval,
                    seg_from.strftime("%Y-%m-%d"),
                    seg_till.strftime("%Y-%m-%d"),
                )
                for row in rows:
                    bar = self._row_to_bar(row)
                    if bar is None:
                        continue
                    bar_date = datetime.fromtimestamp(bar.time, tz=timezone.utc).date()
                    if primary_by_day.get(bar_date, {}).get("secid") != secid:
                        continue
                    all_bars.append(bar)
            fetched_secids.add(secid)

        all_bars.sort(key=lambda b: b.time)
        return all_bars

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _contract_window(year: int, del_month: int, meta: dict | None) -> dict | None:
        """Compute the active trading window for a contract, mirrors PS script logic."""
        try:
            del_first = date(year, del_month, 1)
        except ValueError:
            return None
        q_start = _quarter_start(del_first)
        ext_back_dt = del_first - timedelta(days=1)
        ext_back = date(ext_back_dt.year, ext_back_dt.month, 1)
        # Go back 10 months
        back_month = del_month - 10
        back_year = year
        while back_month <= 0:
            back_month += 12
            back_year -= 1
        ext_back = min(ext_back, date(back_year, back_month, 1))
        active_from = min(q_start, ext_back)
        active_to = _month_last(year, del_month)

        if meta:
            if meta["LastDel"].year != year:
                return None
            active_to = meta["LastTrade"]
            ld = meta["LastDel"]
            ld_back_m = ld.month - 10
            ld_back_y = ld.year
            while ld_back_m <= 0:
                ld_back_m += 12
                ld_back_y -= 1
            ext_from_del = date(ld_back_y, ld_back_m, 1)
            q_ld = _quarter_start(ld)
            active_from = min(active_from, ext_from_del, q_ld)

        return {"from": active_from, "to": active_to}


def is_specific_contract(symbol: str) -> bool:
    """True if symbol looks like a specific contract (e.g. RIM6, SIM6)."""
    if len(symbol) < 3:
        return False
    # Last two chars: letter + digit
    return symbol[-1].isdigit() and symbol[-2].isalpha() and symbol[-2].upper() in _MONTH_LETTERS


async def load_bars_iss(
    symbol: str,
    date_from: date,
    date_to: date,
    interval: int = 1,
) -> list[Bar]:
    """
    High-level entry point.
    - Specific contract (RIM6): fetch directly.
    - Base code (RI): fetch continuous with roll logic.
    """
    async with IssLoader() as loader:
        if is_specific_contract(symbol):
            return await loader.fetch_contract_bars(symbol, date_from, date_to, interval)
        else:
            return await loader.fetch_continuous_bars(symbol, date_from, date_to, interval)


async def fetch_contract_spec(symbol: str) -> dict | None:
    """
    Fetch FORTS contract spec from MOEX ISS (free, no auth).
    Returns dict with real ruble economics:
      initial_margin  – ГО per 1 contract, RUB
      min_step        – price step in index points
      step_price      – RUB value of one min_step
      point_value     – RUB per 1 index point = step_price / min_step
      name, lot
    point_value is the key fix: backtest PnL must be (exit-entry) * point_value
    to be in RUBLES, not raw index points.
    """
    url = (
        "https://iss.moex.com/iss/engines/futures/markets/forts/securities/"
        f"{symbol}.json?iss.meta=off&iss.only=securities"
    )
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": "STL-IssLoader/1.0", "Accept": "application/json"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            j = resp.json()
        data = j.get("securities", {}).get("data") or []
        cols = j.get("securities", {}).get("columns") or []
        if not data:
            return None
        d = dict(zip(cols, data[0]))

        def _f(key):
            v = d.get(key)
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        min_step = _f("MINSTEP")
        step_price = _f("STEPPRICE")
        point_value = (step_price / min_step) if (min_step and step_price) else None
        return {
            "symbol": symbol,
            "name": d.get("SHORTNAME") or d.get("SECNAME") or symbol,
            "ticker": d.get("SECID") or symbol,
            "lot": _f("LOTVOLUME") or 1.0,
            "min_step": min_step,
            "step_price": step_price,
            "point_value": point_value,
            "initial_margin": _f("INITIALMARGIN"),
            "last_price": _f("LASTSETTLEPRICE") or _f("PREVPRICE"),
            "raw": d,
        }
    except Exception:
        return None
