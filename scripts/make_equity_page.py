"""Собирает статичную страницу с кривой доходности прогона и ШВАМИ СКЛЕЙКИ.

Зачем отдельная страница, а не ссылка в Ботстор: на длинном прогоне по склейке
главное — не итоговое число, а форма кривой И места, где склейка врёт. Склейка
непрерывного контракта не выравнивает базис: на смене переднего контракта цена
скачет, и позиция, пережившая шов, получает фантомный результат. На RI за
2025-11..2026-08 таких швов три, худший — 01.07.2026 на −13 690 пунктов, это
около 23 000 ₽ на контракт и порядка 550 000 ₽ на полной позиции. Кривая без
отметок швов читается как факт, хотя в этих точках она вымысел.

Страница статична намеренно: её кладут в раздачу nginx и дают ссылкой, она не
зависит ни от живого API, ни от сессии.

ЗАПУСК НА ХОСТЕРЕ:
    PYTHONPATH=. $PY scripts/make_equity_page.py --run camp-...-RI --out equity-ri.html
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date, datetime, timezone

import asyncpg

SEAM_JUMP = 0.015          # относительный разрыв между соседними барами = шов


async def load_run(dsn: str, run_id: str) -> dict:
    c = await asyncpg.connect(dsn)
    try:
        row = await c.fetchrow(
            "SELECT r.id, r.symbol, r.date_from, r.date_to, b.equity_curve, b.params, "
            "       b.net_profit, b.total_trades, b.peak_contracts, b.recovery_factor, "
            "       b.max_drawdown "
            "  FROM backtest_results b JOIN backtest_runs r ON r.id = b.run_id "
            " WHERE r.id = $1 ORDER BY b.net_profit DESC NULLS LAST LIMIT 1", run_id)
    finally:
        await c.close()
    if row is None:
        raise SystemExit(f"прогон {run_id} не найден или ещё без результата")
    d = dict(row)
    for k in ("equity_curve", "params"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k] or "[]" if k == "equity_curve" else "{}")
    return d


async def find_seams(symbol: str, d_from: date, d_to: date) -> list[dict]:
    """Швы склейки: разрывы цены между соседними барами. Считаем по тем же барам,
    что видел бэктест, а не по календарю экспираций: склейка выбирает передний
    контракт своим правилом, и фактический шов бывает не в день экспирации."""
    from trader.lab.iss_loader import load_bars_iss
    bars = await load_bars_iss(symbol, d_from, d_to, 1)
    out = []
    for i in range(1, len(bars)):
        p, c = bars[i - 1].close, bars[i].close
        if p and abs(c - p) / p > SEAM_JUMP:
            out.append({"t": bars[i].time, "from": p, "to": c,
                        "pts": round(c - p), "pct": round((c - p) / p * 100, 2)})
    return out


HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
 :root{--bg:#0a0e17;--panel:#0f1524;--bd:#1e2a44;--ink:#d7e0f0;--dim:#7386a8;
       --grn:#43c463;--red:#ff5c5c;--amb:#f5a623;--mono:ui-monospace,Consolas,monospace}
 *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
   font:14px/1.55 system-ui,Segoe UI,sans-serif;padding:20px}
 h1{font-size:17px;margin:0 0 4px}.sub{color:var(--dim);font-size:12px;margin-bottom:16px}
 .row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
 .card{background:var(--panel);border:1px solid var(--bd);border-radius:8px;padding:10px 14px;min-width:150px}
 .lbl{font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--dim)}
 .val{font-size:22px;font-weight:700;font-family:var(--mono)}
 .val.pos{color:var(--grn)}.val.neg{color:var(--red)}
 canvas{width:100%%;height:420px;background:var(--panel);border:1px solid var(--bd);border-radius:8px}
 .warn{background:#2a1c0d;border:1px solid #5a3c12;border-radius:8px;padding:12px 14px;margin:14px 0}
 .warn b{color:var(--amb)}
 table{width:100%%;border-collapse:collapse;font-size:13px;margin-top:8px}
 th,td{text-align:left;padding:5px 8px;border-bottom:1px solid #131c30}
 th{color:var(--dim);font-size:10px;text-transform:uppercase}
 td.num{text-align:right;font-family:var(--mono)}
</style></head><body>
<h1>%(title)s</h1>
<div class="sub">%(subtitle)s</div>
<div class="row">%(cards)s</div>
<canvas id="c" width="1400" height="420"></canvas>
%(seamblock)s
<script>
const CURVE=%(curve)s, SEAMS=%(seams)s;
const cv=document.getElementById('c'), ctx=cv.getContext('2d');
function draw(){
  const W=cv.width,H=cv.height,P=46;
  ctx.clearRect(0,0,W,H);
  if(!CURVE.length){ctx.fillStyle='#7386a8';ctx.font='14px sans-serif';
    ctx.fillText('кривая пуста',P,H/2);return;}
  const xs=CURVE.map(p=>p[0]), ys=CURVE.map(p=>p[1]);
  const x0=Math.min(...xs),x1=Math.max(...xs);
  let y0=Math.min(...ys),y1=Math.max(...ys); if(y0===y1){y0-=1;y1+=1;}
  const X=t=>P+(t-x0)/(x1-x0||1)*(W-P*1.5), Y=v=>H-P-(v-y0)/(y1-y0||1)*(H-P*1.8);
  // нулевая линия
  ctx.strokeStyle='#1e2a44';ctx.lineWidth=1;ctx.beginPath();
  ctx.moveTo(P,Y(0));ctx.lineTo(W-P/2,Y(0));ctx.stroke();
  // ШВЫ рисуем ПОД кривой, чтобы их было видно, но они не спорили с ней
  ctx.setLineDash([4,4]);
  SEAMS.forEach(s=>{const x=X(s.t*1000);
    ctx.strokeStyle='#f5a623';ctx.beginPath();ctx.moveTo(x,P/2);ctx.lineTo(x,H-P);ctx.stroke();
    ctx.fillStyle='#f5a623';ctx.font='11px sans-serif';
    ctx.fillText(s.pct+'%%',x+4,P/2+12);});
  ctx.setLineDash([]);
  // кривая
  ctx.strokeStyle=ys[ys.length-1]>=0?'#43c463':'#ff5c5c';ctx.lineWidth=1.6;
  ctx.beginPath();CURVE.forEach((p,i)=>{const x=X(p[0]),y=Y(p[1]);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
  ctx.stroke();
  // подписи оси
  ctx.fillStyle='#7386a8';ctx.font='11px sans-serif';
  const fmt=t=>new Date(t).toISOString().slice(0,10);
  ctx.fillText(fmt(x0),P,H-P+16); ctx.fillText(fmt(x1),W-P-70,H-P+16);
  ctx.fillText(Math.round(y1).toLocaleString('ru-RU')+' ₽',4,Y(y1)+4);
  ctx.fillText(Math.round(y0).toLocaleString('ru-RU')+' ₽',4,Y(y0)+4);
}
draw();
</script></body></html>"""


def card(lbl: str, val: str, cls: str = "") -> str:
    return (f'<div class="card"><div class="lbl">{lbl}</div>'
            f'<div class="val {cls}">{val}</div></div>')


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True, help="имя файла в frontend/dist")
    ap.add_argument("--no-seams", action="store_true", help="не ходить в ISS за швами")
    args = ap.parse_args()

    dsn = os.environ["LAB_DB_URL"].replace("postgresql+asyncpg", "postgresql")
    d = await load_run(dsn, args.run)
    curve = d.get("equity_curve") or []
    seams = [] if args.no_seams else await find_seams(
        d["symbol"], d["date_from"], d["date_to"])

    net = float(d.get("net_profit") or 0)
    cards = "".join([
        card("Чистый результат", f"{net:+,.0f} ₽".replace(",", " "),
             "pos" if net >= 0 else "neg"),
        card("Сделок", f"{d.get('total_trades') or 0}"),
        card("Пик позиции", f"{d.get('peak_contracts') or 0} к."),
        card("Recovery factor", f"{float(d.get('recovery_factor') or 0):.2f}"),
        card("Просадка по закрытым", f"{float(d.get('max_drawdown') or 0) * 100:.1f}%"),
        card("Швов склейки", f"{len(seams)}", "neg" if seams else ""),
    ])
    seamblock = ""
    if seams:
        rows = "".join(
            f"<tr><td>{datetime.fromtimestamp(s['t'], timezone.utc):%Y-%m-%d %H:%M}</td>"
            f"<td class='num'>{s['from']:,.0f}</td><td class='num'>{s['to']:,.0f}</td>"
            f"<td class='num'>{s['pts']:+,}</td><td class='num'>{s['pct']:+.2f}%</td></tr>"
            .replace(",", " ") for s in seams)
        seamblock = (
            '<div class="warn"><b>Кривая содержит швы склейки.</b> Непрерывный контракт '
            'сшит из разных серий, и базис между ними не выравнивается: на смене '
            'переднего контракта цена скачет, а позиция, пережившая шов, получает '
            'фантомную прибыль или убыток. Отмечены пунктиром.'
            '<table><thead><tr><th>Момент</th><th>Цена до</th><th>Цена после</th>'
            '<th>Пунктов</th><th>%</th></tr></thead><tbody>'
            + rows + '</tbody></table></div>')

    html = HTML % {
        "title": f"Кривая доходности · {d['symbol']}",
        "subtitle": (f"{d['id']} · период {d['date_from']} → {d['date_to']} · "
                     f"параметры: {json.dumps(d.get('params') or {}, ensure_ascii=False)}"),
        "cards": cards, "seamblock": seamblock,
        "curve": json.dumps(curve), "seams": json.dumps(seams),
    }
    path = os.path.join("frontend", "dist", args.out)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"готово: {path} · точек кривой {len(curve)} · швов {len(seams)}")


if __name__ == "__main__":
    asyncio.run(main())
