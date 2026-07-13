<script lang="ts">
  import { downloadCSV } from '$lib/csv';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import BacktestChart from './BacktestChart.svelte';
  import ParamHelp from './ParamHelp.svelte';
  import MustDescription from './MustDescription.svelte';
  import { helpFor } from '$lib/strategy-help';
  import { toFills, commissionBreakdown } from '../../lib/lab-analytics';

  // «?» help panels per param in the sweep form. Lab has no live robot, so the
  // points-conversion context is zero (formula + illustrative schematic shown);
  // live «×ATR = N пунктов» is on the robot card where a real ATR exists.
  let helpOpen = $state<Record<string, boolean>>({});
  const helpCtx = { price: 0, atr: 0 };

  // «История прогонов»: every saved sweep campaign, viewable right here in the Lab
  // (operator couldn't find where the run history lived). Lazy-loaded on first open.
  let labCampaigns = $state<any[]>([]);
  let showLabHistory = $state(false);
  async function toggleLabHistory() {
    showLabHistory = !showLabHistory;
    if (showLabHistory && labCampaigns.length === 0) {
      try {
        const r = await fetchWithAuth('/api/v1/lab/campaigns');
        if (r.ok) labCampaigns = await r.json();
      } catch { /* history is auxiliary */ }
    }
  }

  // Run-history table: sortable + filterable. All client-side (<=100 campaigns).
  const HIST_COLS = [
    { key: 'campaign', label: 'ID прогона', sort: true },
    { key: 'last_at', label: 'Дата', sort: true },
    { key: 'strategy', label: 'Стратегия / робот', sort: true },
    { key: 'symbol', label: 'Контракт', sort: true },
    { key: 'period', label: 'Период бэктеста', sort: false },
    { key: 'max_pos', label: 'Макс. поз.', sort: true },
    { key: 'max_go', label: 'Макс. ГО', sort: true },
    { key: 'leader_net', label: 'P&L лидера', sort: true },
    { key: 'leader_rf', label: 'RF лидера', sort: true },
    { key: 'leader_trades', label: 'Сделок', sort: true },
    { key: 'doc', label: 'Описание', sort: false },
    { key: 'params', label: 'Параметры лидера', sort: false },
  ];
  let histSort = $state<{ key: string; dir: number }>({ key: 'last_at', dir: -1 });
  let histFilter = $state('');
  let histWinOnly = $state(false);
  let histExpanded = $state<string | null>(null);

  function histSortBy(key: string) {
    if (histSort.key === key) histSort = { key, dir: -histSort.dir };
    else histSort = { key, dir: (key === 'campaign' || key === 'strategy' || key === 'symbol') ? 1 : -1 };
  }
  const histVal = (c: any, k: string) => {
    switch (k) {
      case 'campaign': return c.campaign || '';
      case 'last_at': return c.last_at || '';
      case 'strategy': return c.strategy || '';
      case 'symbol': return c.leader_symbol || (c.symbols || [])[0] || '';
      case 'max_pos': return c.max_pos ?? -1;
      case 'max_go': return c.max_go ?? -1;
      case 'leader_net': return c.leader_net ?? -Infinity;
      case 'leader_rf': return c.leader_rf ?? -Infinity;
      case 'leader_trades': return c.leader_trades ?? -1;
      default: return '';
    }
  };
  const histRows = $derived.by(() => {
    const q = histFilter.trim().toLowerCase();
    let rows = labCampaigns.filter((c: any) => {
      if (histWinOnly && !((c.leader_net ?? 0) > 0)) return false;
      if (!q) return true;
      return (c.campaign || '').toLowerCase().includes(q)
        || (c.strategy || '').toLowerCase().includes(q)
        || ((c.symbols || []).join(' ') + ' ' + (c.leader_symbol || '')).toLowerCase().includes(q);
    });
    const { key, dir } = histSort;
    return rows.sort((a: any, b: any) => {
      const x = histVal(a, key), y = histVal(b, key);
      if (typeof x === 'number' && typeof y === 'number') return (x - y) * dir;
      return String(x).localeCompare(String(y), 'ru') * dir;
    });
  });
  const histMaxAbsNet = $derived(Math.max(1, ...labCampaigns.map((c: any) => Math.abs(c.leader_net ?? 0))));

  const fmtRub = (v: any) => v == null ? '—' : Math.round(v).toLocaleString('ru-RU') + ' ₽';
  const fmtRf = (v: any) => v == null ? '—' : Number(v).toFixed(2);
  const fmtDay = (iso: any) => iso ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' }) : '—';
  const robName = (s: any) => !s ? '—' : (String(s).endsWith('__inv') ? String(s).slice(0, -5) + ' (контр)' : s);
  const paramsShort = (p: any) => {
    if (!p) return '';
    const e = Object.entries(p).filter(([k]) => k !== 'symbol');
    return e.slice(0, 4).map(([k, v]) => `${k}:${v}`).join('  ') + (e.length > 4 ? '  …' : '');
  };
  function histCsv() {
    const cols = ['campaign', 'last_at', 'strategy', 'leader_symbol', 'date_from', 'date_to', 'max_pos', 'max_go', 'leader_net', 'leader_rf', 'leader_trades'];
    const head = ['ID прогона', 'Дата', 'Стратегия', 'Контракт', 'Период с', 'Период по', 'Макс.поз', 'Макс.ГО', 'P&L лидера', 'RF лидера', 'Сделок', 'Параметры'];
    const esc = (v: any) => { const s = v == null ? '' : String(v); return /[";\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    const lines = [head.join(';')];
    for (const c of histRows) lines.push([...cols.map((k) => esc(c[k])), esc(JSON.stringify(c.leader_params || {}))].join(';'));
    const blob = new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = 'run-history.csv'; a.click(); URL.revokeObjectURL(a.href);
  }

  // ── Strategy catalog (botstore + installed robots merged) ──────────────
  let catalog = $state<any[]>([]);
  let installed = $state<any[]>([]);
  let instruments = $state<any[]>([]);
  let selectedStrategyId = $state('');
  let selectedStrategy = $derived(catalog.find((s: any) => s.id === selectedStrategyId) ?? null);

  // ── Parameter form ─────────────────────────────────────────────────────
  let paramValues = $state<Record<string, any>>({});
  let sweepRanges = $state<Record<string, { from: number; to: number; step: number }>>({});
  let dateFrom = $state('2026-03-02');
  // named sweep -> becomes a Botstore campaign (camp-<date>-<slug>); empty = bare run
  let campaignName = $state('');
  let lastCampaign = $state('');
  let dateTo = $state('2026-05-24');
  let engine = $state<'auto' | 'local' | 'remote'>('local');
  let strategyInfo = $state<any | null>(null);  // popover: show desc for a strategy

  // ── Sweep rounds ───────────────────────────────────────────────────────
  // maxLocal = cap on the small VDS (one subprocess, serial — keep modest so a run
  // finishes in minutes). maxRemote = cap on the i9 agent (16 workers — handles 10x).
  const ROUNDS = [
    { id: 'r0', label: 'R0 Random Explore', desc: 'Случайный поиск по сетке', maxLocal: 80, maxRemote: 5000 },
    { id: 'r1', label: 'R1 RF×Return Refine', desc: 'Уточнение лучших', maxLocal: 60, maxRemote: 5000 },
    { id: 'r2', label: 'R2 RF×Return Refine', desc: 'Финальный отбор', maxLocal: 40, maxRemote: 2500 },
  ];
  let activeRound = $state(0);
  let roundResults = $state<any[][]>([[], [], []]);
  let running = $state(false);
  let runPhase = $state('');
  let error = $state('');
  let polling = $state<any>(null);

  // ── Leader ──────────────────────────────────────────────────────────────
  let leaderId = $state<string | null>(null);
  let leaderResult = $state<any | null>(null);
  let chartLoading = $state(false);
  let chartPoll = $state<any>(null);

  // ── Helpers ─────────────────────────────────────────────────────────────
  const fmtMoney = (v: number | null | undefined) =>
    v != null ? (v >= 0 ? '+' : '') + Math.round(v).toLocaleString('ru') + ' ₽' : '—';
  const fmtPct = (v: number | null | undefined) =>
    v != null ? (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%' : '—';
  const fmtD = (v: number | null | undefined) => v != null ? v.toFixed(2) : '—';
  const fmtN = (v: number | null | undefined) => v != null ? Math.round(v) : 0;

  function cartesian(arrays: number[][]): number[][] {
    if (!arrays.length) return [[]];
    return arrays.reduce((acc, cur) => acc.flatMap(a => cur.map(c => [...a, c])), [[]] as number[][]);
  }

  function comboCount(): number {
    let n = 1;
    const schema = selectedStrategy?.params_schema ?? [];
    for (const p of schema) {
      if (p.type !== 'number' || p.key === 'symbol') continue;
      const r = sweepRanges[p.key];
      if (r && r.from !== r.to && r.step > 0) {
        n *= Math.floor((r.to - r.from) / r.step) + 1;
      }
    }
    return n;
  }

  // Ranking score "прибыль × recovery factor". Naive net×RF is broken because for a
  // losing robot RF = net/dd is also negative, so net×RF = net²/dd > 0 → catastrophes
  // rank at the top. Winners: net × max(RF, 0) (reward profit AND recovery). Losers
  // (net ≤ 0): score = net itself, so they sort strictly below every winner.
  function profitRF(r: any): number {
    const np = r.net_profit ?? r.total_return ?? 0;
    if (np <= 0) return np;
    const rf = r.recovery_factor ?? 0;
    return np * Math.max(rf, 0.01);
  }

  function leaderboard(): any[] {
    const all = roundResults.flat().filter(r => r?.result);
    return all.sort((a, b) => profitRF(b.result) - profitRF(a.result));
  }

  // ── Load catalog (botstore + installed robots) ─────────────────────────
  async function loadCatalog() {
    try {
      const [bs, robs] = await Promise.all([
        fetchWithAuth('/api/v1/botstore').then(r => r.ok ? r.json() : null),
        fetchWithAuth('/api/v1/robots').then(r => r.ok ? r.json() : []),
      ]);
      installed = robs ?? [];
      const botCatalog = (bs?.catalog ?? []).map((c: any) => ({
        id: c.id,
        name: c.name,
        description: c.description ?? '',
        source: 'store',
        source_url: c.source ?? '',
        scriptCode: null, // will be resolved from strategies endpoint
        params_schema: [],
        results: c.results ?? [],
        sweep: c.sweep,
      }));
      // Merge installed robots: try to match to a botstore catalog entry via
      // the strategy id embedded in script_code (e.g. make_on_bar('bollinger_bo_m1')).
      for (const r of (robs ?? [])) {
        const code = r.script_code ?? '';
        // Try library pattern:  make_on_bar('xxx')
        let stratId = '';
        const m1 = code.match(/make_on_bar\('([a-z0-9_]+)'\)/);
        if (m1) stratId = m1[1];
        // Try standalone pattern: from trader.lab.strategies.xxx import
        if (!stratId) {
          const m2 = code.match(/from trader\.lab\.strategies\.([a-z0-9_]+) import/);
          if (m2) stratId = m2[1];
        }
        const catEntry = botCatalog.find((c: any) => c.id === stratId);
        if (catEntry) {
          // Enrich the botstore entry with installed runtime data
          catEntry.scriptCode = r.script_code;
          catEntry.robotId = r.id;
          catEntry.source = 'both';
          catEntry.params_json = r.params_json;
        } else if (stratId) {
          // Installed robot has a known strategy but not in botstore catalog
          botCatalog.push({
            id: stratId, name: r.name,
            description: '', source: 'installed',
            scriptCode: r.script_code, robotId: r.id,
            params_schema: [], params_json: r.params_json,
            results: [], sweep: undefined, source_url: '',
          });
        }
        // If no stratId match (custom script), skip — not useful for backtest lab
      }
      catalog = botCatalog;
      // Restore previous session: strategy, params, ranges, results
      const restored = restoreBtlState();
      if (restored && selectedStrategyId) {
        const s = catalog.find((c: any) => c.id === selectedStrategyId);
        if (s) await selectStrategy(s, true);  // keepState=true — don't clear results
      } else if (catalog.length) {
        selectStrategy(catalog[0]);
      }
    } catch { catalog = []; }
  }

  async function loadInstruments() {
    try {
      const r = await fetchWithAuth('/api/v1/forts-instruments');
      instruments = r.ok ? await r.json() : [];
    } catch { instruments = []; }
  }

  // Load full strategy details (params_schema) when a catalog entry is selected
  async function selectStrategy(s: any, keepState = false) {
    selectedStrategyId = s.id;
    if (!keepState) {
      paramValues = {};
      sweepRanges = {};
      roundResults = [[], [], []];
      leaderId = null; leaderResult = null;
    }
    error = '';

    // 1) If we have an installed robot, seed params from its params_json
    const pj = (s.source === 'installed' || s.source === 'both') && typeof s.params_json === 'object'
      ? s.params_json : null;

    // 2) Load full template from /api/v1/strategies (for store + both)
    if (s.source === 'store' || s.source === 'both') {
      try {
        const r = await fetchWithAuth('/api/v1/strategies');
        const strats = r.ok ? await r.json() : [];
        const tmpl = strats.find((t: any) => t.id === s.id);
        if (tmpl) {
          if (!s.scriptCode) s.scriptCode = tmpl.script_code;
          s.description = tmpl.description ?? s.description;
          s.source_url = tmpl.source;
          s.params_schema = (tmpl.params_schema ?? []).map((p: any) => ({ ...p }));
          // Fill defaults from template
          for (const p of s.params_schema) {
            paramValues[p.key] = pj?.[p.key] ?? tmpl.default_params?.[p.key] ?? p.default;
          }
        }
      } catch { /* keep partial */ }
    }

    // 3) Fallback: build minimal schema from installed params_json
    if (!s.params_schema?.length && pj) {
      const keys = Object.keys(pj);
      s.params_schema = keys.filter(k => k !== 'symbol').map(k => ({
        key: k, label: k, type: typeof pj[k] === 'number' ? 'number' : 'text',
        default: pj[k], min: undefined, max: undefined, desc: '', hint: '',
      }));
      if (pj.symbol) {
        s.params_schema.unshift({ key: 'symbol', label: 'Инструмент', type: 'text', default: pj.symbol, desc: '', hint: '' });
      }
      for (const p of s.params_schema) {
        paramValues[p.key] = pj[p.key] ?? p.default;
      }
    }

    // 4) Seed sweep ranges for numeric params — sensible defaults, don't overwrite restored state
    for (const p of s.params_schema ?? []) {
      if (p.type !== 'number' || p.key === 'symbol') continue;
      if (keepState && sweepRanges[p.key]) continue; // keep restored ranges
      const d = paramValues[p.key] ?? p.default;
      const lo = p.min ?? 1;
      const hi = p.max ?? 9999;
      // Pick a reasonable sub-range around the default, within min/max
      const half = Math.max(1, Math.round((hi - lo) * 0.25));
      const from = Math.max(lo, d - half);
      const to = Math.min(hi, d + half);
      // Step: aim for ~5-8 points in the range
      const span = to - from;
      const step = span <= 5 ? 1 : span <= 20 ? Math.max(1, Math.round(span / 5)) : Math.max(1, Math.round(span / 8));
      sweepRanges[p.key] = { from, to, step };
    }
  }

  // ── Run sweep round ────────────────────────────────────────────────────
  async function runRound(ri: number) {
    error = '';
    running = true;
    activeRound = ri;
    const sym = paramValues.symbol || 'RIM6';
    const schema = selectedStrategy?.params_schema ?? [];
    // Build param combos from sweep ranges
    const dims: Record<string, number[]> = {};
    for (const p of schema) {
      if (p.type !== 'number' || p.key === 'symbol') {
        dims[p.key] = [paramValues[p.key]];
        continue;
      }
      const r = sweepRanges[p.key];
      if (r && r.from !== r.to && r.step > 0) {
        const vals: number[] = [];
        for (let x = r.from; x <= r.to; x += r.step) vals.push(x);
        dims[p.key] = vals;
      } else {
        dims[p.key] = [paramValues[p.key] ?? p.default];
      }
    }
    const keys = Object.keys(dims);
    let combos = cartesian(keys.map(k => dims[k]));
    // VDS runs serially in one subprocess → small cap; i9 has 16 workers → large.
    const maxC = engine === 'local' ? ROUNDS[ri].maxLocal : ROUNDS[ri].maxRemote;
    // R0: random shuffle + cap; R1/R2: take all (already refined)
    if (ri === 0 && combos.length > maxC) {
      // Fisher-Yates shuffle then slice
      for (let i = combos.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [combos[i], combos[j]] = [combos[j], combos[i]];
      }
      combos = combos.slice(0, maxC);
    } else if (combos.length > maxC) {
      combos = combos.slice(0, maxC);
    }
    const paramSets = combos.map(c => {
      const p: Record<string, any> = {};
      keys.forEach((k, i) => p[k] = c[i]);
      if (!p.symbol) p.symbol = sym;
      return p;
    });

    const scriptCode = selectedStrategy?.scriptCode ?? selectedStrategy?.script_code;
    const body: any = {
      symbol: sym,
      dateFrom: new Date(dateFrom).toISOString(),
      dateTo: new Date(dateTo).toISOString(),
      paramSets,
      engine,
      robotId: selectedStrategy?.robotId || (installed[0]?.id ?? ''),
    };
    if (campaignName.trim()) body.campaign = campaignName.trim();
    if (scriptCode) {
      body.scriptCode = scriptCode;
      body.baseParams = { symbol: sym };
    }
    const engLabel = engine === 'auto' ? 'авто' : engine === 'remote' ? 'i9' : 'VDS';
    runPhase = `${ROUNDS[ri].label}: ${paramSets.length} вариантов → ${engLabel}…`;
    try {
      const res = await fetchWithAuth('/api/v1/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      if (data.campaign) lastCampaign = data.campaign;
      const actualEng = data.engine === 'remote' ? 'i9 (очередь)' : data.engine === 'local' ? 'VDS' : data.engine;
      runPhase = `${ROUNDS[ri].label}: ${paramSets.length} вариантов · ${actualEng}…`;
      await pollRun(data.run_id, ri, paramSets);
    } catch (e: any) {
      error = String(e);
      running = false;
    }
  }

  async function pollRun(runId: string, ri: number, paramSets: any[]) {
    let attempts = 0;
    const MAX_ATTEMPTS = 1800; // 45 min: an i9 sweep keeps running server-side; do not abandon it early
    polling = setInterval(async () => {
      attempts++;
      if (attempts > MAX_ATTEMPTS) {
        clearInterval(polling); polling = null;
        runPhase = `${ROUNDS[ri].label}: таймаут (>15 мин) — проверь агента или запусти локально`;
        error = 'Таймаут ожидания. Если движок "Авто" или "Мощный хост" — агент i9 может быть неактивен. Переключи на VDS (сервер).';
        running = false;
        return;
      }
      try {
        const sr = await fetchWithAuth(`/api/v1/backtest/${runId}/status`);
        if (!sr.ok) return;
        const st = await sr.json();
        const pct = st.progress_pct ?? (st.status === 'done' ? 100 : (st.status === 'queued' ? 0 : Math.min(99, attempts * 2)));
        const statusLabel = st.status === 'queued' ? 'в очереди (ждёт агента)' :
                            st.status === 'running' ? 'считается' :
                            st.status === 'pending' ? 'запускается' : (st.status ?? '?');
        runPhase = `${ROUNDS[ri].label}: ${statusLabel} · ${pct}% [${Math.floor(attempts/40)}м]`;
        if (st.status === 'done' || st.status === 'failed') {
          clearInterval(polling);
          polling = null;
          if (st.status === 'failed') {
            error = `Расчёт не удался: ${st.error_msg ?? 'неизвестная ошибка'}`;
            runPhase = `${ROUNDS[ri].label}: ошибка`;
            running = false;
            return;
          }
          // full=1 so the BEST row carries its trades/equity (the ingest keeps them
          // only for the top combo); without it every row is metrics-only and the
          // leader chart shows «0 сделок» + a needless VDS re-run that can diverge.
          const rr = await fetchWithAuth(`/api/v1/backtest/${runId}/results?full=1`);
          if (rr.ok) {
            const rd = await rr.json();
            const items = (Array.isArray(rd) ? rd : (rd.results ?? rd.combos ?? [])).map((c: any) => ({
              params: c.params ?? {},
              result: c,
            }));
            roundResults[ri] = items;
            const lb = [...items].sort((a, b) => profitRF(b.result) - profitRF(a.result));
            if (lb.length && profitRF(lb[0].result) > 0) {
              selectLeader(lb[0]);   // auto-show best; fetches trades if stripped
            }
          }
          runPhase = `${ROUNDS[ri].label}: готово — ${roundResults[ri].length} результатов`;
          running = false;
        }
      } catch { /* keep polling */ }
    }, 1500);
  }

  function stopRun() {
    if (polling) { clearInterval(polling); polling = null; }
    running = false;
    runPhase = 'Остановлено';
  }

  function clearRounds() {
    roundResults = [[], [], []];
    leaderId = null; leaderResult = null;
    runPhase = ''; error = '';
  }

  // Click a hit-parade row → show its chart. Sweep results keep trades only for the
  // best combo (to dodge nginx 413), so for any row without trades we run ONE fast
  // single backtest on the VDS to get full trades + equity + fresh net_profit/RF.
  // rub per price point for the leader chart: from the result row when the engine
  // stored it, else the instrument meta. Without it the chart replays fills with
  // pointValue=1 and «Результат» shows POINTS labelled as rubles (never matches
  // the engine's net in the table) — same bug family as the robot-card badge.
  let leaderPV = $state(1);
  async function resolveLeaderPV(row: any) {
    const pv = Number(row?.result?.point_value ?? 0);
    if (pv > 0) { leaderPV = pv; return; }
    try {
      const sym = paramValues.symbol ?? row?.params?.symbol ?? '';
      const mr = await fetchWithAuth('/api/v1/instruments/' + encodeURIComponent(sym) + '/meta');
      if (mr.ok) { const m = await mr.json(); leaderPV = m?.point_value || 1; return; }
    } catch { /* keep 1 */ }
    leaderPV = 1;
  }

  async function selectLeader(row: any) {
    leaderId = JSON.stringify(row.params);
    await resolveLeaderPV(row);
    const hasTrades = Array.isArray(row.result?.trades) && row.result.trades.length > 0;
    if (hasTrades) { leaderResult = row; return; }
    // Run a single backtest for these exact params.
    if (chartPoll) { clearInterval(chartPoll); chartPoll = null; }
    chartLoading = true;
    leaderResult = row;   // show metrics immediately; chart fills once trades arrive
    const sym = row.params.symbol || paramValues.symbol || 'RIM6';
    const scriptCode = selectedStrategy?.scriptCode ?? selectedStrategy?.script_code;
    const body: any = {
      symbol: sym,
      dateFrom: new Date(dateFrom).toISOString(),
      dateTo: new Date(dateTo).toISOString(),
      paramSets: [{ ...row.params, symbol: sym }],
      // 'auto' → the same host that ran the sweep (i9), which HAS the bars. The VDS
      // ohlcv_bars store keeps the continuous contract as «RI» ending mid-June, so a
      // 'local' re-run of «RIU6» over a July window found 0 bars → 0 trades (the empty
      // leader chart). i9 resolves the real bars and matches the hit-parade numbers.
      engine: 'auto',
      robotId: selectedStrategy?.robotId || (installed[0]?.id ?? ''),
    };
    if (scriptCode) { body.scriptCode = scriptCode; body.baseParams = { symbol: sym }; }
    try {
      const res = await fetchWithAuth('/api/v1/backtest/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      const { run_id } = await res.json();
      let attempts = 0;
      chartPoll = setInterval(async () => {
        attempts++;
        if (attempts > 120) { clearInterval(chartPoll); chartPoll = null; chartLoading = false; return; }
        try {
          const sr = await fetchWithAuth(`/api/v1/backtest/${run_id}/status`);
          if (!sr.ok) return;
          const st = await sr.json();
          if (st.status === 'done' || st.status === 'failed') {
            clearInterval(chartPoll); chartPoll = null;
            if (st.status === 'done') {
              const rr = await fetchWithAuth(`/api/v1/backtest/${run_id}/results?full=1`);
              if (rr.ok) {
                const rd = await rr.json();
                const full = (Array.isArray(rd) ? rd : (rd.results ?? []))[0];
                if (full) leaderResult = { params: row.params, result: full };
              }
            }
            chartLoading = false;
          }
        } catch { /* keep polling */ }
      }, 1500);
    } catch {
      chartLoading = false;
    }
  }

  // ── Persist sweep state across reloads ───────────────────────────────────
  const LS_BTL = 'btl_state';
  function saveBtlState() {
    try {
      const st = { selectedStrategyId, paramValues, sweepRanges, dateFrom, dateTo, engine, roundResults, activeRound, campaignName };
      localStorage.setItem(LS_BTL, JSON.stringify(st));
    } catch {}
  }
  function restoreBtlState() {
    try {
      const raw = localStorage.getItem(LS_BTL);
      if (!raw) return;
      const st = JSON.parse(raw);
      if (st.selectedStrategyId) selectedStrategyId = st.selectedStrategyId;
      if (st.paramValues) paramValues = st.paramValues;
      if (st.sweepRanges) sweepRanges = st.sweepRanges;
      if (st.campaignName) campaignName = st.campaignName;
      if (st.dateFrom) dateFrom = st.dateFrom;
      if (st.dateTo) dateTo = st.dateTo;
      if (st.engine) engine = st.engine;
      if (st.roundResults) roundResults = st.roundResults;
      if (st.activeRound != null) activeRound = st.activeRound;
      // Re-select leader from restored results (fetches trades if stripped)
      const lb = leaderboard();
      if (lb.length && profitRF(lb[0].result) > 0) selectLeader(lb[0]);
      return true;
    } catch { return false; }
  }

  // Save state whenever it changes
  $effect(() => {
    // Trigger reactivity on all tracked state
    void (selectedStrategyId, paramValues, sweepRanges, dateFrom, dateTo, engine, roundResults.length);
    if (catalog.length) saveBtlState();
  });

  // ── Init ────────────────────────────────────────────────────────────────
  $effect(() => { loadCatalog(); loadInstruments(); });
</script>

<div class="btl">
  <!-- ── LEFT: Config panel ──────────────────────────────────────────── -->
  <div class="btl-config">
    <div class="btl-section">
      <div class="btl-sec-title">Стратегия</div>
      <div class="btl-cat-list">
        {#each catalog as s}
          {@const active = selectedStrategyId === s.id}
          <button class="btl-cat-card" class:active
                  onclick={() => selectStrategy(s)}>
            <div class="btl-cat-name">
              <span>{s.name}</span>
              <span class="btl-cat-actions">
                {#if s.sweep?.pct > 0}
                  <span class="btl-cat-sweep">перебор {s.sweep.pct}%</span>
                {/if}
                {#if s.description}
                  <span class="btl-cat-i" role="button" tabindex="0" title="Описание стратегии"
                        onclick={(e) => { e.stopPropagation(); strategyInfo = strategyInfo?.id === s.id ? null : s; }}
                        onkeydown={(e) => (e.key === 'Enter') && (e.stopPropagation(), strategyInfo = strategyInfo?.id === s.id ? null : s)}
                  >ⓘ</span>
                {/if}
              </span>
            </div>
            {#if s.description}<div class="btl-cat-desc">{s.description.slice(0, 100)}{s.description.length > 100 ? '…' : ''}</div>{/if}
            {#if s.source === 'store' && s.results?.length}
              <div class="btl-cat-top3">
                {#each s.results.slice(0, 3) as r}
                  <span class="btl-t3sym">{r.symbol}</span>
                  <span class="btl-t3pnl" class:pos={r.net_profit > 0} class:neg={r.net_profit < 0}>{fmtMoney(r.net_profit)}</span>
                {/each}
              </div>
            {/if}
            {#if s.source === 'installed'}<span class="btl-cat-badge">установлен</span>{/if}
          </button>
        {/each}
      </div>
      <!-- Strategy info popover — rendered OUTSIDE the scrollable list -->
      {#if strategyInfo}
        <div class="btl-info-box">
          <div class="btl-info-head">
            <span class="btl-info-title">{strategyInfo.name}</span>
            <button class="btl-info-close" onclick={() => strategyInfo = null}>✕</button>
          </div>
          <div class="btl-info-body">{strategyInfo.description}</div>
          {#if strategyInfo.source_url}
            <a class="btl-info-link" href={strategyInfo.source_url} target="_blank" rel="noopener">Источник на GitHub ↗</a>
          {/if}
          {#if strategyInfo.source === 'installed'}
            <span class="btl-info-link">Установленный робот — скрипт загружен из кода</span>
          {:else if strategyInfo.source === 'both'}
            <span class="btl-info-link">Установлен на платформе + есть в каталоге</span>
          {:else if !strategyInfo.source_url}
            <span class="btl-info-link">Стратегия из библиотеки</span>
          {/if}
        </div>
      {/if}
    </div>

    {#if selectedStrategy}
      <div class="btl-section">
        <div class="btl-sec-title">
          Параметры
          {#if selectedStrategy.source_url}
            <a class="btl-src-link" href={selectedStrategy.source_url} target="_blank" rel="noopener">источник ↗</a>
          {/if}
        </div>
        <MustDescription strategyId={selectedStrategyId} params={paramValues} symbol={paramValues.symbol} />
        {#each (selectedStrategy.params_schema ?? []) as p}
          {@const isSymbol = p.key === 'symbol'}
          {@const help = helpFor(selectedStrategyId, p.key)}
          <div class="btl-pf">
            <div class="btl-pf-head">
              {#if help}
                <button class="btl-pf-q" class:on={helpOpen[p.key]}
                        title="пояснение со схемой" onclick={() => helpOpen[p.key] = !helpOpen[p.key]}>?</button>
              {/if}
              <span class="btl-pf-label"><code class="btl-pf-key">{p.key}</code>{#if help?.title} · {help.title}{/if}</span>
              {#if !help && (p.desc || p.hint)}
                <span class="btl-pf-i" title={p.desc || p.hint}>ⓘ</span>
              {/if}
            </div>
            {#if help && helpOpen[p.key]}
              <ParamHelp {help} value={Number(paramValues[p.key] ?? p.default) || 0} ctx={helpCtx} />
            {:else if !help && (p.desc || p.hint)}
              <div class="btl-pf-desc">{p.desc || p.hint}</div>
            {/if}
            {#if isSymbol}
              <select bind:value={paramValues[p.key]} class="btl-inp btl-sel">
                {#each instruments as inst}
                  <option value={inst.symbol}>{inst.symbol} — {inst.name}</option>
                {/each}
              </select>
            {:else if p.type === 'number'}
              {@const r = sweepRanges[p.key] ?? {}}
              <div class="btl-range">
                <input type="number" class="btl-inp btl-rng" min={p.min} max={p.max}
                       value={paramValues[p.key] ?? p.default}
                       onchange={(e) => paramValues[p.key] = Number(e.currentTarget.value)}
                       title="Значение (если не перебирается)" />
                <span class="btl-rng-lbl">от</span>
                <input type="number" class="btl-inp btl-rng" min={p.min} max={p.max}
                       value={r.from ?? paramValues[p.key] ?? p.default}
                       onchange={(e) => {sweepRanges[p.key] = {...sweepRanges[p.key], from: Number(e.currentTarget.value)}; sweepRanges = sweepRanges;}}
                       title="Начало диапазона перебора" />
                <span class="btl-rng-lbl">до</span>
                <input type="number" class="btl-inp btl-rng" min={p.min} max={p.max}
                       value={r.to ?? paramValues[p.key] ?? p.default}
                       onchange={(e) => {sweepRanges[p.key] = {...sweepRanges[p.key], to: Number(e.currentTarget.value)}; sweepRanges = sweepRanges;}}
                       title="Конец диапазона перебора" />
                <span class="btl-rng-lbl">шаг</span>
                <input type="number" class="btl-inp btl-rng btl-step" min="1"
                       value={r.step ?? 1}
                       onchange={(e) => {sweepRanges[p.key] = {...sweepRanges[p.key], step: Math.max(1, Number(e.currentTarget.value))}; sweepRanges = sweepRanges;}}
                       title="Шаг перебора" />
              </div>
            {:else}
              <input type="text" class="btl-inp" bind:value={paramValues[p.key]} placeholder={String(p.default)} />
            {/if}
          </div>
        {/each}
      </div>

      <div class="btl-section">
        <div class="btl-sec-title">Период бэктеста</div>
        <div class="btl-dates">
          <input type="date" bind:value={dateFrom} class="btl-inp" />
          <span class="btl-date-dash">—</span>
          <input type="date" bind:value={dateTo} class="btl-inp" />
        </div>
      </div>

      <div class="btl-section">
        <div class="btl-sec-title">Имя перебора</div>
        <input class="btl-inp" style="width:220px" placeholder="латиницей/цифрами, напр. winC"
               bind:value={campaignName}
               title="С именем перебор станет кампанией: результаты попадут в Botstore (чип на карточке стратегии) и по ссылке /?campaign=..." />
        {#if lastCampaign}
          <div class="btl-camp-link">кампания: <a href={'/?campaign=' + encodeURIComponent(lastCampaign)} target="_blank">{lastCampaign}</a></div>
        {/if}
      </div>

      <div class="btl-section">
        <div class="btl-sec-title">Движок</div>
        <div class="btl-engines">
          {#each [['auto','Авто (i9 если жив)'],['local','VDS (сервер)'],['remote','Мощный хост (i9)']] as [v, label]}
            <button class="btl-eng" class:active={engine === v} onclick={() => engine = v as any}>{label}</button>
          {/each}
        </div>
      </div>

      <!-- Combo count + run -->
      <div class="btl-actions">
        <div class="btl-cc">Комбинаций в сетке: <b>{comboCount()}</b></div>
        <div class="btl-rounds">
          {#each ROUNDS as rd, i}
            <button class="btl-rbtn" disabled={running || comboCount() === 0}
                    class:current={activeRound === i && running}
                    class:done={roundResults[i].length > 0}
                    onclick={() => runRound(i)}>
              <span class="btl-rbtn-lbl">{rd.label}</span>
              <span class="btl-rbtn-desc">{rd.desc}</span>
              {#if roundResults[i].length}
                <span class="btl-rbtn-n">{roundResults[i].length} рез.</span>
              {/if}
            </button>
          {/each}
        </div>
        {#if running}
          <button class="btl-stop" onclick={stopRun}>⏹ Стоп</button>
        {/if}
        <button class="btl-clear" onclick={clearRounds}>Очистить</button>
      </div>
    {/if}
  </div>

  <!-- ── RIGHT: Results ───────────────────────────────────────────────── -->
  <div class="btl-results">
    <!-- История прогонов: все сохранённые кампании перебора (без повторного прогона) -->
    <div class="btl-history">
      <button class="btl-hist-btn" onclick={toggleLabHistory}
              title="все сохранённые прогоны перебора — открой любой хит-парад без повторного расчёта">
        📊 История прогонов{#if labCampaigns.length} ({labCampaigns.length}){/if}
        <span class="btl-hist-chev">{showLabHistory ? '▴' : '▾'}</span>
      </button>
      {#if showLabHistory}
        {#if labCampaigns.length === 0}
          <div class="btl-hist-empty">Сохранённых прогонов пока нет. Задай «Имя перебора» и запусти R0 — кампания сохранится сюда.</div>
        {:else}
          <div class="btl-hist-tools">
            <input class="btl-hist-search" placeholder="фильтр: ID, стратегия, контракт…" bind:value={histFilter} />
            <label class="btl-hist-chk"><input type="checkbox" bind:checked={histWinOnly} /> только прибыльные</label>
            <span class="btl-hist-count">{histRows.length} из {labCampaigns.length}</span>
            <button class="btl-hist-csv" onclick={histCsv}>Выгрузить в CSV</button>
          </div>
          <div class="btl-hist-scroll">
            <table class="btl-ht">
              <thead>
                <tr>
                  {#each HIST_COLS as col}
                    <th class:ht-sortable={col.sort} class:ht-active={histSort.key === col.key}
                        onclick={() => col.sort && histSortBy(col.key)}
                        title={col.sort ? 'сортировать' : ''}>
                      {col.label}{#if col.sort}<span class="ht-ar">{histSort.key === col.key ? (histSort.dir > 0 ? '▲' : '▼') : '↕'}</span>{/if}
                    </th>
                  {/each}
                </tr>
              </thead>
              <tbody>
                {#each histRows as c (c.campaign)}
                  <tr class="ht-row">
                    <td class="mono ht-id"><a href={'/?campaign=' + encodeURIComponent(c.campaign)} target="_blank" rel="noopener" title="открыть витрину прогона">{c.campaign}</a></td>
                    <td class="ht-day">{fmtDay(c.last_at)}</td>
                    <td class="ht-strat">{robName(c.strategy)}</td>
                    <td class="mono">{c.leader_symbol ?? (c.symbols ?? []).join(',')}</td>
                    <td class="mono ht-period">{fmtDay(c.date_from)}—{fmtDay(c.date_to)}</td>
                    <td class="mono ht-num">{c.max_pos ?? '—'}</td>
                    <td class="mono ht-num">{c.max_go != null ? Math.round(c.max_go).toLocaleString('ru-RU') : '—'}</td>
                    <td class="mono ht-pnl" class:pos={(c.leader_net ?? 0) > 0} class:neg={(c.leader_net ?? 0) < 0}>
                      <span class="ht-bar" style="width:{Math.round(90 * Math.abs(c.leader_net ?? 0) / histMaxAbsNet)}%"></span>
                      <span class="ht-pnlv">{fmtRub(c.leader_net)}</span>
                    </td>
                    <td class="mono ht-num">{fmtRf(c.leader_rf)}</td>
                    <td class="mono ht-num">{c.leader_trades ?? '—'}</td>
                    <td class="ht-doc-cell">{#if c.strategy_base}<a class="ht-doc" href={'/?strategy=' + encodeURIComponent(c.strategy_base)} target="_blank" rel="noopener" title="описание стратегии">описание ↗</a>{:else}—{/if}</td>
                    <td class="ht-params">
                      <button class="ht-pexp mono" onclick={() => histExpanded = histExpanded === c.campaign ? null : c.campaign}>
                        <span class="ht-pchev">{histExpanded === c.campaign ? '▴' : '▾'}</span>{paramsShort(c.leader_params)}
                      </button>
                      {#if histExpanded === c.campaign}<div class="ht-pfull mono">{JSON.stringify(c.leader_params)}</div>{/if}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      {/if}
    </div>

    {#if running || runPhase}
      <div class="btl-progress">
        <div class="btl-prog-bar">
          <div class="btl-prog-fill" style="width:{(() => {
            const all = roundResults.flat().length;
            return Math.min(100, Math.max(0, running ? 50 : (all > 0 ? 100 : 0)));
          })()}%"></div>
        </div>
        <div class="btl-prog-text">{runPhase}</div>
      </div>
    {/if}

    {#if error}
      <div class="btl-error">{error}</div>
    {/if}

    {#if leaderboard().length}
      {@const lb = leaderboard()}
      <div class="btl-section">
        <div class="btl-sec-title">
          🏆 Хит-парад
          <span class="btl-sec-sub">сортировка: прибыль × recovery factor ↓</span>
          <button class="btl-csv" onclick={() => downloadCSV(
            leaderboard().map((r: any) => ({ ...(r.params ?? {}), ...Object.fromEntries(Object.entries(r.result ?? {}).filter(([k]) => k !== 'trades' && k !== 'equity_curve')) })),
            'backtest-' + (campaignName || 'sweep'))}>Выгрузить в CSV</button>
        </div>
        <div class="btl-leader-wrap">
          <table class="btl-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Инстр.</th>
                <th>Параметры</th>
                <th>Чистая прибыль</th>
                <th class="th-hl">% год (ГО)</th>
                <th class="th-hl">% год (без пл.)</th>
                <th>Доходность</th>
                <th>Шарп</th>
                <th>RF</th>
                <th>Просадка</th>
                <th>Сделок</th>
                <th title="пиковая одновременная позиция (контрактов) за бэктест — сверяй с лимитами агента и ГО">Макс. поз.</th>
              </tr>
            </thead>
            <tbody>
              {#each lb.slice(0, 50) as row, i}
                {@const r = row.result}
                {@const isLeader = JSON.stringify(row.params) === leaderId}
                <tr class="btl-row" class:btl-leader={isLeader}
                    onclick={() => selectLeader(row)}>
                  <td class="btl-rank">{i + 1}</td>
                  <td class="btl-sym">{paramValues.symbol ?? r.symbol ?? '—'}</td>
                  <td class="btl-params">{Object.entries(row.params).filter(([k]) => k !== 'symbol').map(([k,v]) => `${k}=${v}`).join(', ')}</td>
                  <td class="btl-num" class:pos={r.net_profit > 0} class:neg={r.net_profit < 0}>{fmtMoney(r.net_profit)}</td>
                  <td class="btl-num btl-ann" class:pos={(r.ann_return_go ?? 0) > 0} class:neg={(r.ann_return_go ?? 0) < 0}>{fmtPct(r.ann_return_go)}</td>
                  <td class="btl-num btl-ann" class:pos={(r.ann_return_full ?? 0) > 0} class:neg={(r.ann_return_full ?? 0) < 0}>{fmtPct(r.ann_return_full)}</td>
                  <td class="btl-num" class:pos={r.total_return > 0} class:neg={r.total_return < 0}>{fmtPct(r.total_return)}</td>
                  <td class="btl-num">{fmtD(r.sharpe)}</td>
                  <td class="btl-num">{fmtD(r.recovery_factor)}</td>
                  <td class="btl-num" class:neg={(r.max_drawdown ?? 0) > 0.1}>{fmtPct(r.max_drawdown)}</td>
                  <td class="btl-num">{fmtN(r.total_trades)}</td>
                  <td class="btl-num" class:neg={(r.peak_contracts ?? 0) > 20}>{r.peak_contracts ?? '—'}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </div>
    {/if}

    {#if leaderResult?.result}
      <div class="btl-section">
        <div class="btl-sec-title">
          📈 Лидер: прибыль×RF
          {#if chartLoading}<span class="btl-sec-sub">загрузка сделок для графика…</span>{/if}
        </div>
        <div class="btl-chart-wrap">
          {#key JSON.stringify(leaderResult.params)}
            <BacktestChart
              result={leaderResult.result}
              symbol={paramValues.symbol ?? (leaderResult.result.params?.symbol) ?? 'RIM6'}
              dateFrom={dateFrom}
              dateTo={dateTo}
              pointValue={leaderPV}
            />
          {/key}
        </div>
      </div>
    {/if}

    {#if !leaderboard().length && !running}
      <div class="btl-empty">
        <div class="btl-empty-icon">🧪</div>
        <div class="btl-empty-text">Выбери стратегию, настрой диапазоны параметров<br>и запусти R0 → R1 → R2 перебор</div>
      </div>
    {/if}
  </div>
</div>

<style>
  .btl { display: flex; height: 100%; overflow: hidden; gap: 0; }

  /* ── Config panel ──────────────────────────────────────────────────── */
  .btl-config {
    width: 420px; flex-shrink: 0; overflow-y: auto; overflow-x: hidden;
    background: #0c0c1a; border-right: 1px solid #1e1e3a;
    display: flex; flex-direction: column; gap: 1px;
    padding-bottom: 20px;
  }
  .btl-section { padding: 14px 16px; border-bottom: 1px solid #15152a; }
  .btl-sec-title {
    font-size: 12px; color: #4caf50; text-transform: uppercase; letter-spacing: 1.5px;
    margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center;
  }
  .btl-sec-sub { font-size: 11px; color: #667; text-transform: none; letter-spacing: 0; }
  .btl-src-link { font-size: 11px; color: #5a8aba; text-decoration: none; }
  .btl-src-link:hover { color: #6aafff; }

  /* Strategy cards */
  .btl-cat-list { display: flex; flex-direction: column; gap: 5px; max-height: 260px; overflow-y: auto; }
  .btl-cat-card {
    background: #0a0a18; border: 1px solid #1a1a32; border-radius: 5px;
    padding: 10px 12px; cursor: pointer; text-align: left; width: 100%;
    transition: border-color 0.15s, background 0.15s;
  }
  .btl-cat-card:hover { border-color: #2a3a5a; }
  .btl-cat-card.active { border-color: #4caf5066; background: #0a1a0f; }
  .btl-cat-name { font-size: 13px; color: #ddd; font-weight: 600; display: flex; justify-content: space-between; align-items: center; }
  .btl-cat-actions { display: flex; align-items: center; gap: 5px; flex-shrink: 0; }
  .btl-cat-sweep { font-size: 10px; color: #4caf50; background: #4caf5018; padding: 2px 6px; border-radius: 3px; }
  .btl-cat-i { font-size: 13px; color: #5a8aba; cursor: pointer; padding: 2px 4px; }
  .btl-cat-i:hover { color: #8acaff; }
  .btl-cat-desc { font-size: 11px; color: #778; margin-top: 4px; line-height: 1.4; }
  /* Strategy info box (below list, full-width) */
  .btl-info-box { margin-top: 10px; padding: 14px 16px; background: #0a0f1e; border: 1px solid #2a4a6a; border-radius: 5px; }
  .btl-info-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .btl-info-title { font-size: 14px; color: #4caf50; font-weight: 600; }
  .btl-info-close { background: none; border: none; color: #667; cursor: pointer; font-size: 16px; padding: 0 4px; }
  .btl-info-close:hover { color: #f44336; }
  .btl-info-body { font-size: 12px; color: #bbc; line-height: 1.7; white-space: pre-line; }
  .btl-info-link { font-size: 11px; color: #5a8aba; margin-top: 10px; display: inline-block; text-decoration: none; }
  .btl-info-link:hover { color: #8acaff; }
  .btl-cat-top3 { display: flex; gap: 14px; margin-top: 5px; }
  .btl-t3sym { font-size: 11px; color: #999; }
  .btl-t3pnl { font-size: 11px; font-family: monospace; }
  .btl-t3pnl.pos { color: #4caf50; }
  .btl-t3pnl.neg { color: #f44336; }
  .btl-cat-badge { font-size: 10px; color: #4caf50; background: #4caf5018; padding: 2px 6px; border-radius: 3px; margin-top: 5px; display: inline-block; }

  /* Param fields */
  .btl-pf { margin-bottom: 12px; }
  .btl-pf-head { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
  .btl-pf-label { font-size: 12px; color: #bbb; font-weight: 500; }
  .btl-pf-key { font-family: Consolas, "SF Mono", monospace; font-size: 11px; color: #ffca7a;
    background: #2a1e0a; border: 1px solid #b8860b55; border-radius: 3px; padding: 1px 5px; }
  .btl-pf-i { font-size: 12px; color: #5a8aba; cursor: help; }
  .btl-pf-i:hover { color: #8acaff; }
  .btl-pf-q { width: 20px; height: 20px; flex-shrink: 0; border-radius: 50%;
    background: #16204a; border: 1px solid #2d4a7a; color: #7ab8ff; cursor: pointer;
    font-size: 12px; font-weight: 700; line-height: 1; padding: 0; }
  .btl-pf-q:hover { background: #1d2a5a; }
  .btl-pf-q.on { background: #2d4a7a; color: #fff; }
  .btl-pf-desc { font-size: 11px; color: #7a9abb; line-height: 1.55; margin-bottom: 4px; white-space: pre-line; }

  /* Inputs */
  .btl-inp {
    background: #0a0a18; border: 1px solid #1e1e3a; color: #ddd;
    padding: 5px 8px; font-size: 12px; border-radius: 4px; width: 100%;
  }
  .btl-inp:focus { border-color: #4caf5066; outline: none; }
  .btl-sel { cursor: pointer; }
  .btl-range { display: flex; align-items: center; gap: 4px; }
  .btl-rng { width: 68px; }
  .btl-step { width: 50px; }
  .btl-rng-lbl { font-size: 10px; color: #667; min-width: 18px; text-align: center; }

  /* Dates */
  .btl-dates { display: flex; align-items: center; gap: 10px; }
  .btl-dates .btl-inp { width: auto; flex: 1; }
  .btl-date-dash { color: #667; }

  /* Engines */
  .btl-engines { display: flex; gap: 5px; }
  .btl-eng {
    flex: 1; padding: 7px 6px; font-size: 11px; cursor: pointer; border-radius: 4px;
    background: #0a0a18; border: 1px solid #1e1e3a; color: #999; text-align: center;
    transition: all 0.15s;
  }
  .btl-eng:hover { border-color: #3a3a5a; color: #bbb; }
  .btl-eng.active { border-color: #4caf5066; color: #4caf50; background: #0a1a0f; }

  /* Actions */
  .btl-actions { padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
  .btl-cc { font-size: 13px; color: #bbb; }
  .btl-cc b { color: #4caf50; font-family: monospace; font-size: 16px; }
  .btl-rounds { display: flex; flex-direction: column; gap: 5px; }
  .btl-rbtn {
    padding: 10px 12px; border-radius: 5px; cursor: pointer; text-align: left; border: 1px solid #1e1e3a;
    background: #0a0a18; transition: all 0.15s; display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px;
  }
  .btl-rbtn:hover:not(:disabled) { border-color: #4caf5066; background: #0a1a0f; }
  .btl-rbtn.current { border-color: #4caf50; background: #0a1a0f; animation: pulse 1.5s infinite; }
  .btl-rbtn.done { border-color: #2a4a2a; opacity: 0.8; }
  .btl-rbtn:disabled { opacity: 0.4; cursor: not-allowed; }
  .btl-rbtn-lbl { font-size: 12px; color: #4caf50; font-weight: 600; }
  .btl-rbtn-desc { font-size: 10px; color: #778; }
  .btl-rbtn-n { font-size: 10px; color: #4caf50; margin-left: auto; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.7} }
  .btl-stop { padding: 7px 14px; background: #2a0a0a; border: 1px solid #f44336; color: #f44336; border-radius: 4px; cursor: pointer; font-size: 12px; }
  .btl-clear { padding: 5px 10px; background: transparent; border: 1px solid #2a2a3a; color: #778; border-radius: 4px; cursor: pointer; font-size: 11px; }

  /* ── Results panel ──────────────────────────────────────────────────── */
  .btl-results { flex: 1; overflow-y: auto; min-width: 0; padding: 14px; display: flex; flex-direction: column; gap: 14px; }
  .btl-history { border: 1px solid #24406a; border-radius: 8px; background: #0c1424; padding: 8px 12px; }
  .btl-hist-btn { background: transparent; border: none; color: #7ab8ff; cursor: pointer;
    font-size: 13px; font-weight: 600; padding: 2px 0; display: flex; align-items: center; gap: 6px; }
  .btl-hist-chev { color: #567; }
  .btl-hist-empty { font-size: 12px; color: #789; padding: 6px 2px; }
  .btl-hist-tools { display: flex; align-items: center; gap: 10px; margin: 10px 0 6px; flex-wrap: wrap; }
  .btl-hist-search { background: #0a0f1c; border: 1px solid #24406a; border-radius: 5px; color: #cde; font-size: 12px; padding: 5px 9px; min-width: 220px; flex: 1; }
  .btl-hist-search:focus { outline: none; border-color: #3a7; }
  .btl-hist-chk { display: flex; align-items: center; gap: 5px; font-size: 12px; color: #9ab; cursor: pointer; user-select: none; }
  .btl-hist-count { font-size: 11px; color: #678; margin-left: auto; font-variant-numeric: tabular-nums; }
  .btl-hist-csv { background: #13233a; border: 1px solid #2a4a6a; color: #9cf; border-radius: 5px; font-size: 11px; padding: 5px 10px; cursor: pointer; }
  .btl-hist-csv:hover { background: #1a3050; }
  .btl-hist-scroll { overflow: auto; max-height: 440px; border: 1px solid #16233c; border-radius: 6px; }
  .btl-ht { width: 100%; border-collapse: collapse; font-size: 12px; }
  .btl-ht thead th { position: sticky; top: 0; z-index: 1; background: #0e1830; color: #9ab; font-weight: 600; text-align: right; padding: 7px 10px; white-space: nowrap; border-bottom: 1px solid #24406a; user-select: none; }
  .btl-ht thead th:nth-child(-n+5), .btl-ht thead th:nth-child(11), .btl-ht thead th:nth-child(12) { text-align: left; }
  .btl-ht th.ht-sortable { cursor: pointer; }
  .btl-ht th.ht-sortable:hover { color: #cde; }
  .btl-ht th.ht-active { color: #7fd7b0; }
  .ht-ar { color: #567; margin-left: 3px; font-size: 10px; }
  .btl-ht th.ht-active .ht-ar { color: #7fd7b0; }
  .btl-ht td { text-align: right; padding: 6px 10px; color: #cde; border-bottom: 1px solid #12203a; white-space: nowrap; font-variant-numeric: tabular-nums; vertical-align: top; }
  .btl-ht td:nth-child(-n+5), .btl-ht td:nth-child(11), .btl-ht td:nth-child(12) { text-align: left; }
  .ht-row:hover td { background: #0f1c34; }
  .ht-id a { color: #8acaff; text-decoration: none; font-size: 11px; }
  .ht-id a:hover { text-decoration: underline; }
  .ht-strat { color: #dfe; }
  .ht-period { color: #9ab; font-size: 11px; }
  .ht-num { color: #bcd; }
  .ht-pnl { position: relative; min-width: 128px; }
  .ht-bar { position: absolute; right: 8px; top: 4px; bottom: 4px; border-radius: 2px; opacity: .20; }
  .ht-pnl.pos .ht-bar { background: #00e676; }
  .ht-pnl.neg .ht-bar { background: #ff5c72; }
  .ht-pnlv { position: relative; font-weight: 600; }
  .ht-pnl.pos .ht-pnlv { color: #37e28f; }
  .ht-pnl.neg .ht-pnlv { color: #ff6b80; }
  .ht-doc { color: #7fd7b0; text-decoration: none; font-size: 11px; }
  .ht-doc:hover { text-decoration: underline; }
  .ht-params { max-width: 360px; }
  .ht-pexp { background: transparent; border: none; color: #99a; font-size: 11px; cursor: pointer; padding: 0; text-align: left; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 350px; display: block; }
  .ht-pexp:hover { color: #cde; }
  .ht-pchev { color: #567; margin-right: 3px; }
  .ht-pfull { margin-top: 4px; color: #bcd; font-size: 11px; white-space: normal; word-break: break-word; max-width: 360px; line-height: 1.5; background: #0a1120; padding: 5px 7px; border-radius: 4px; }
  .btl-progress { margin-bottom: 6px; }
  .btl-prog-bar { height: 4px; background: #1a1a32; border-radius: 2px; overflow: hidden; margin-bottom: 6px; }
  .btl-prog-fill { height: 100%; background: #4caf50; transition: width 0.5s; border-radius: 2px; }
  .btl-prog-text { font-size: 13px; color: #4caf50; font-weight: 600; }
  .btl-error { padding: 10px 14px; background: #1a0808; border: 1px solid #f4433644; color: #f44336; border-radius: 5px; font-size: 12px; }

  /* Leaderboard table */
  .btl-leader-wrap { overflow-x: auto; max-height: 360px; overflow-y: auto; }
  .btl-csv { margin-left: auto; background: #16162c; border: 1px solid #2d2d4a; color: #cde;
    padding: 3px 10px; border-radius: 4px; cursor: pointer; font-size: 11px; }
  .btl-csv:hover { border-color: #4caf50; }
  .btl-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  .btl-table th {
    position: sticky; top: 0; background: #0c0c1a; color: #667;
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; padding: 7px 10px;
    text-align: left; border-bottom: 1px solid #1e1e3a;
  }
  .btl-table td { padding: 7px 10px; border-bottom: 1px solid #111128; color: #999; }
  .btl-row { cursor: pointer; transition: background 0.1s; }
  .btl-row:hover { background: #0a0a18; }
  .btl-leader { background: #0a1a0f !important; border-left: 3px solid #4caf50; }
  .btl-rank { color: #667; font-family: monospace; width: 28px; }
  .btl-sym { color: #4caf50; font-family: monospace; font-weight: 600; }
  .btl-params { font-family: monospace; font-size: 10px; max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .btl-num { font-family: monospace; text-align: right; }
  .btl-num.pos { color: #4caf50; }
  .btl-num.neg { color: #f44336; }
  .btl-score { font-weight: 700; font-size: 12px; }
  .btl-score.pos { color: #4caf50; }
  .btl-ann { font-weight: 700; font-size: 12px; }
  .btl-ann.pos { color: #00e676; text-shadow: 0 0 6px #00e67644; }
  .btl-ann.neg { color: #ff5252; text-shadow: 0 0 6px #ff525244; }
  .th-hl { color: #00e676; }

  /* Chart */
  .btl-chart-wrap { height: 68vh; min-height: 440px; }

  /* Empty */
  .btl-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 14px; color: #556; }
  .btl-empty-icon { font-size: 56px; opacity: 0.4; }
  .btl-empty-text { font-size: 15px; text-align: center; line-height: 1.6; }
</style>
