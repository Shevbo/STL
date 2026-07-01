"""Lightweight QA checklist web form (GET /qa) + persistence.

A single self-contained page: a NUMBERED checklist of operator actions with an
acceptance verdict (ОК / Ошибка / Пропуск) and a free comment per step. Verdicts are
saved server-side (data/qa_results.json) so a run survives reloads and can be exported.

No trading logic here. The mutating endpoints are portal-authenticated (the operator's
STL session cookie); the HTML shell is served without auth and fetches state with
credentials.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from trader.auth.guard import require_auth

router = APIRouter(tags=["qa"])

_RESULTS_PATH = Path(__file__).resolve().parents[2] / "data" / "qa_results.json"
_LOCK = threading.Lock()

# Numbered checklist. Grouped for readability; `n` is the global step number the operator
# sees. Edit here to add/adjust steps. Each id is stable (results key), so renumbering the
# display order never loses saved verdicts.
CHECKLIST: list[dict] = [
    {"group": "Графики"},
    {"id": "chart_live_default", "n": 1,
     "text": "Главный график при загрузке показывает ЖИВОЙ контракт (RIU6/GZU6/…), не пустой."},
    {"id": "chart_tf_switch", "n": 2,
     "text": "Переключение ТФ (1м/5м/15м/30м/1ч/2ч/4ч/Д): график рисуется на КАЖДОМ, не пусто."},
    {"id": "chart_symbol_switch", "n": 3,
     "text": "Смена инструмента в дропдауне графика: история грузится, свечи появляются."},
    {"id": "chart_msk", "n": 4,
     "text": "Ось времени графика — МСК (совпадает со стаканом/терминалом, не UTC−3)."},
    {"id": "chart_dead_contract", "n": 5,
     "text": "Робот на истёкшем (июньском) контракте: график авто-катит на живой сентябрьский, не пусто."},
    {"id": "charts_grid", "n": 6,
     "text": "Фрейм «Графики поз./заявок»: по графику на каждый инструмент в позиции/с заявками; бейдж стороны/кол-ва."},
    {"id": "chart_cache", "n": 7,
     "text": "Повторная загрузка графика быстрая; при медленном Finam — «Загрузка истории…», не мистическая пустота."},

    {"group": "Сессия / навигация"},
    {"id": "f5_no_login", "n": 8,
     "text": "F5 на залогиненном НЕ выкидывает на экран логина (виден сплэш «Проверка сессии…»)."},

    {"group": "Стакан"},
    {"id": "book_no_flicker", "n": 9,
     "text": "Стакан QUIK-инструмента (SRU6/GZU6): цифры не прыгают, спрэд стабильный (нет мигания Finam↔QUIK)."},

    {"group": "Заявки QUIK"},
    {"id": "order_place_ok", "n": 10,
     "text": "Постановка лимитки в пределах коллара: статус active, БЕЗ ложного «ОТКЛОНЕНА»."},
    {"id": "order_text_readable", "n": 11,
     "text": "Текст ответа QUIK читаемый (кириллица), не кракозябры ◇◇◇."},
    {"id": "order_cancel", "n": 12,
     "text": "Отмена заявки из UI: снимается в STL И подтверждается в QUIK."},
    {"id": "order_reject_reason", "n": 13,
     "text": "При отклонении причина видна в колонке «Текст» + сообщение под тикетом."},
    {"id": "whitelist_sync", "n": 14,
     "text": "Строка «whitelist синхронизирован с агентом: …» зелёная (STL = агент)."},
    {"id": "reconcile_phantom", "n": 15,
     "text": "Заявка, которую QUIK не подтвердил, через ~20с помечается expired/«завис», НЕ висит фантомом (сверка)."},

    {"group": "Визуализация заявки"},
    {"id": "orderviz", "n": 16,
     "text": "При активной заявке — авто-фрейм OrderViz со стаканом+графиком, подсветка/пульс уровня заявки."},
]

_STEP_IDS = {row["id"] for row in CHECKLIST if "id" in row}
_VALID_STATUS = {"ok", "fail", "skip", "pending"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    with _LOCK:
        try:
            return json.loads(_RESULTS_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — first run / missing / corrupt -> empty
            return {}


def _save(data: dict) -> None:
    with _LOCK:
        _RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RESULTS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class QAResult(BaseModel):
    step_id: str
    status: str          # ok | fail | skip | pending
    comment: str = ""
    tester: str = ""


@router.get("/api/v1/qa/state")
async def qa_state(request: Request):
    """Checklist + saved verdicts for the form to render."""
    require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
    return {"checklist": CHECKLIST, "results": _load()}


@router.post("/api/v1/qa/result")
async def qa_result(body: QAResult, request: Request):
    """Upsert one step's verdict + comment (auto-saved on change)."""
    email = require_auth(request.app.state.settings.shectory_auth_bridge_secret, request)
    if body.step_id not in _STEP_IDS:
        raise HTTPException(status_code=404, detail="unknown step_id")
    if body.status not in _VALID_STATUS:
        raise HTTPException(status_code=422, detail="bad status")
    data = _load()
    data[body.step_id] = {
        "status": body.status,
        "comment": body.comment,
        "tester": body.tester or email,
        "updated_at": _now_iso(),
    }
    _save(data)
    return {"ok": True}


@router.get("/qa", response_class=HTMLResponse)
async def qa_page() -> str:
    """Self-contained QA form (no build step, dark theme matching STL)."""
    return _PAGE


_PAGE = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>STL — QA чеклист</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; background:#0f0f1e; color:#ccd; font:14px/1.5 'JetBrains Mono',Consolas,monospace; }
  header { position:sticky; top:0; z-index:5; background:#14142a; border-bottom:1px solid #2d2d4a;
           padding:10px 16px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  h1 { font-size:16px; margin:0; color:#9ab; }
  .prog { font-size:13px; color:#8fb; }
  .prog b { color:#4caf50; }
  input.tester { background:#0f0f1e; color:#cde; border:1px solid #2d2d4a; border-radius:4px; padding:4px 8px; font:inherit; }
  main { max-width:1000px; margin:0 auto; padding:16px; }
  .grp { color:#7788aa; font-weight:700; text-transform:uppercase; font-size:12px; letter-spacing:.06em;
         margin:22px 0 8px; border-bottom:1px solid #23233f; padding-bottom:4px; }
  .step { background:#14142a; border:1px solid #23233f; border-left:3px solid #33334d; border-radius:6px;
          padding:10px 12px; margin:8px 0; }
  .step.ok   { border-left-color:#4caf50; }
  .step.fail { border-left-color:#f44336; }
  .step.skip { border-left-color:#ffb300; }
  .step-head { display:flex; gap:10px; align-items:flex-start; }
  .num { flex:0 0 26px; height:24px; border-radius:12px; background:#23233f; color:#9ab; font-weight:700;
         display:flex; align-items:center; justify-content:center; font-size:12px; }
  .txt { flex:1; }
  .btns { display:flex; gap:6px; margin-top:8px; flex-wrap:wrap; }
  .btns button { background:#1a1a2e; color:#aab; border:1px solid #2d2d4a; border-radius:4px;
                 padding:3px 12px; cursor:pointer; font:inherit; font-size:12px; }
  .btns button:hover { border-color:#6aa8ff88; }
  .btns button.on-ok   { background:#1f3a1f; border-color:#4caf50; color:#8f8; }
  .btns button.on-fail { background:#3a1f1f; border-color:#f44336; color:#f99; }
  .btns button.on-skip { background:#3a331f; border-color:#ffb300; color:#fd7; }
  textarea { width:100%; margin-top:8px; background:#0f0f1e; color:#cde; border:1px solid #2d2d4a;
             border-radius:4px; padding:6px 8px; font:inherit; font-size:13px; resize:vertical; min-height:34px; }
  .meta { font-size:11px; color:#667; margin-top:4px; min-height:14px; }
  .bar { display:flex; gap:10px; margin-left:auto; }
  .bar button { background:#1a1a2e; color:#cde; border:1px solid #2d2d4a; border-radius:4px; padding:5px 12px; cursor:pointer; font:inherit; font-size:12px; }
  .bar button:hover { border-color:#6aa8ff88; }
  #note { color:#f99; font-size:12px; }
</style></head>
<body>
<header>
  <h1>STL · QA чеклист</h1>
  <span class="prog" id="prog">—</span>
  <label style="font-size:12px;color:#889">тестер <input class="tester" id="tester" placeholder="имя/почта"></label>
  <span id="note"></span>
  <div class="bar">
    <button onclick="copySummary()">Копировать сводку</button>
    <button onclick="exportJson()">Экспорт JSON</button>
  </div>
</header>
<main id="list">загрузка…</main>
<script>
const LS_TESTER = 'stl_qa_tester';
let CHECK = [], RES = {};
const el = document.getElementById.bind(document);
const tester = el('tester');
tester.value = localStorage.getItem(LS_TESTER) || '';
tester.oninput = () => localStorage.setItem(LS_TESTER, tester.value);

async function load() {
  try {
    const r = await fetch('/api/v1/qa/state', { credentials:'include' });
    if (r.status === 401) { el('note').textContent = 'Войдите в STL (нужна сессия) — приёмка не сохранится.'; }
    const d = await r.json();
    CHECK = d.checklist || []; RES = d.results || {};
    render();
  } catch(e) { el('list').textContent = 'Ошибка загрузки: ' + e; }
}

function steps() { return CHECK.filter(x => x.id); }
function updateProg() {
  const s = steps();
  const done = s.filter(x => (RES[x.id]||{}).status === 'ok').length;
  const fail = s.filter(x => (RES[x.id]||{}).status === 'fail').length;
  el('prog').innerHTML = `принято <b>${done}</b>/${s.length}` + (fail?` · <span style="color:#f99">ошибок ${fail}</span>`:'');
}

function render() {
  const root = el('list'); root.innerHTML = '';
  for (const row of CHECK) {
    if (row.group) { const h = document.createElement('div'); h.className='grp'; h.textContent=row.group; root.appendChild(h); continue; }
    const cur = RES[row.id] || { status:'pending', comment:'' };
    const d = document.createElement('div'); d.className = 'step ' + (cur.status==='pending'?'':cur.status); d.id = 'step-'+row.id;
    d.innerHTML = `
      <div class="step-head">
        <div class="num">${row.n}</div>
        <div class="txt">${row.text}</div>
      </div>
      <div class="btns">
        <button data-s="ok">✔ ОК</button>
        <button data-s="fail">✕ Ошибка</button>
        <button data-s="skip">– Пропуск</button>
      </div>
      <textarea placeholder="комментарий (что увидел, шаги воспроизведения, замечания)">${(cur.comment||'').replace(/</g,'&lt;')}</textarea>
      <div class="meta"></div>`;
    const btns = d.querySelectorAll('.btns button');
    btns.forEach(b => {
      if (b.dataset.s === cur.status) b.classList.add('on-'+cur.status);
      b.onclick = () => setStatus(row.id, b.dataset.s);
    });
    const ta = d.querySelector('textarea');
    let t; ta.oninput = () => { clearTimeout(t); t = setTimeout(() => save(row.id, null, ta.value), 500); };
    root.appendChild(d);
    renderMeta(row.id);
  }
  updateProg();
}

function renderMeta(id) {
  const d = el('step-'+id); if(!d) return; const m = d.querySelector('.meta'); const r = RES[id];
  m.textContent = r && r.updated_at ? `${r.status.toUpperCase()} · ${r.tester||''} · ${new Date(r.updated_at).toLocaleString('ru-RU')}` : '';
}

function setStatus(id, status) {
  const cur = RES[id] || {}; save(id, status, cur.comment || '');
}

async function save(id, status, comment) {
  const cur = RES[id] || { status:'pending', comment:'' };
  const body = { step_id:id, status: status || cur.status || 'pending', comment: comment!=null?comment:(cur.comment||''), tester: tester.value };
  RES[id] = { ...cur, status: body.status, comment: body.comment, tester: body.tester, updated_at: new Date().toISOString() };
  const d = el('step-'+id);
  if (d) { d.className = 'step ' + (body.status==='pending'?'':body.status);
    d.querySelectorAll('.btns button').forEach(b => { b.className=''; if(b.dataset.s===body.status) b.classList.add('on-'+body.status); }); }
  renderMeta(id); updateProg();
  try {
    const r = await fetch('/api/v1/qa/result', { method:'POST', credentials:'include',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    if (!r.ok) el('note').textContent = 'Не сохранено ('+r.status+') — проверь логин.'; else el('note').textContent='';
  } catch(e) { el('note').textContent = 'Сеть: не сохранено.'; }
}

function summary() {
  const lines = ['# STL QA — ' + new Date().toLocaleString('ru-RU'), 'Тестер: ' + (tester.value||'—'), ''];
  for (const row of CHECK) {
    if (row.group) { lines.push('', '## ' + row.group); continue; }
    const r = RES[row.id] || {}; const mark = {ok:'[x] ОК',fail:'[!] ОШИБКА',skip:'[-] пропуск'}[r.status] || '[ ] —';
    lines.push(`${row.n}. ${mark} — ${row.text}` + (r.comment?`\\n    · ${r.comment}`:''));
  }
  return lines.join('\\n');
}
function copySummary(){ navigator.clipboard.writeText(summary()).then(()=>el('note').textContent='Сводка скопирована.'); }
function exportJson(){ const blob=new Blob([JSON.stringify({tester:tester.value,results:RES},null,2)],{type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='stl-qa-'+Date.now()+'.json'; a.click(); }

load();
</script>
</body></html>"""
