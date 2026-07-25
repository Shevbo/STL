<!-- Botstore.svelte — two panels:
     LEFT  = tested catalog (robots found/optimized in background campaigns)
     RIGHT = robots installed on the platform (real DB robots)
     Middle action: "установить на платформу" (create a robot from the template +
     best params). Installed robots have "удалить с платформы". -->
<script lang="ts">
  import { downloadCSV } from '$lib/csv';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import RobotIdentity from './RobotIdentity.svelte';
  import BacktestChart from './BacktestChart.svelte';
  import MustDescription from './MustDescription.svelte';
  import { nameFor, docBase, behaviorFor } from '$lib/strategy-help';

  let loading = $state(true);
  let error = $state('');
  let initialEquity = $state(100000);
  let catalog = $state<any[]>([]);     // tested strategies (left)
  let installed = $state<any[]>([]);   // platform robots (right)
  let strategies = $state<any[]>([]);  // templates (for script_code/schema)
  let stlLinks = $state<any[]>([]);
  let selectedCat = $state<string | null>(null);  // selected catalog strategy id
  let busy = $state(false);
  let notice = $state('');

  const fmtPct = (v: any) => v != null ? (v * 100).toFixed(2) + '%' : '—';
  const fmtMoney = (v: any) => v != null ? Math.round(v).toLocaleString('ru-RU') + ' ₽' : '—';
  const fmtMoneyShort = (v: any) => {
    if (v == null) return '—';
    const a = Math.abs(v), s = v < 0 ? '-' : '+';
    if (a >= 1000) return s + (a / 1000).toFixed(a >= 10000 ? 0 : 1) + 'k ₽';
    return s + Math.round(a) + ' ₽';
  };
  const fmtNum = (v: any, d = 2) => v != null ? Number(v).toFixed(d) : '—';
  const fmtDate = (v: any) => v ? new Date(v).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
  // Machine time: seconds → compact "Xч Yм" / "Yм" / "Zс".
  const fmtDur = (secs: any) => {
    const s = Math.round(Number(secs) || 0);
    if (s <= 0) return '0с';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h > 0) return `${h}ч${m ? ' ' + m + 'м' : ''}`;
    if (m > 0) return `${m}м`;
    return `${s}с`;
  };
  // Combined machine time across i9 + VDS fallback, with a per-source breakdown.
  const machineLabel = (sw: any) => {
    if (!sw) return '—';
    const i9 = sw.machine_secs_i9 || 0, vds = sw.machine_secs_vds || 0;
    const parts: string[] = [];
    if (i9) parts.push(`i9 ${fmtDur(i9)}`);
    if (vds) parts.push(`VDS ${fmtDur(vds)}`);
    return parts.length ? parts.join(' · ') : '0с';
  };

  function best(robot: any) {
    const rs = robot.results ?? [];
    return rs.length ? rs[0] : null;
  }
  let totalVariants = $derived(catalog.reduce((s, r) => s + (r.variants_tested || 0), 0));

  async function load() {
    loading = true; error = '';
    try {
      const [bs, robs, strs, links] = await Promise.all([
        fetchWithAuth('/api/v1/botstore').then(r => r.ok ? r.json() : null),
        fetchWithAuth('/api/v1/robots').then(r => r.ok ? r.json() : []),
        fetchWithAuth('/api/v1/strategies').then(r => r.ok ? r.json() : []),
        fetchWithAuth('/api/v1/stl-links').then(r => r.ok ? r.json() : []),
      ]);
      if (bs) {
        initialEquity = bs.initial_equity ?? 100000;
        catalog = (bs.catalog ?? []).filter((c: any) => (c.results?.length ?? 0) > 0);
      }
      installed = robs ?? [];
      strategies = strs ?? [];
      stlLinks = links ?? [];
    } catch (e) {
      error = String(e);
    }
    loading = false;
  }

  // Install a tested strategy onto the platform: create a robot from its template
  // (script_code + schema) seeded with the best params found.
  async function install(robot: any) {
    const b = best(robot);
    const tmpl = strategies.find(s => s.id === robot.id);
    if (!tmpl) { notice = `Нет шаблона стратегии для "${robot.name}" (id=${robot.id}). Обновлений в списке стратегий: ${strategies.length}.`; return; }
    if (!stlLinks.length) { notice = 'Нет STL Link (коннектора счёта). Создайте его сначала.'; return; }
    busy = true; notice = '';
    try {
      const params = b?.params ? { ...b.params } : { ...(tmpl.default_params ?? {}) };
      if (b?.symbol) params.symbol = b.symbol;
      const body = {
        userEmail: 'admin',
        stlLinkId: stlLinks[0]?.id ?? '',
        name: `${robot.name} (${params.symbol ?? ''})`.trim(),
        scriptCode: tmpl.script_code,
        paramsJson: params,
        schedule: '09:00-23:55',
      };
      const res = await fetchWithAuth('/api/v1/robots', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      notice = `Установлен на платформу: ${body.name}`;
      await load();
    } catch (e) {
      notice = 'Ошибка установки: ' + String(e);
    }
    busy = false;
  }

  async function remove(r: any) {
    if (!confirm(`Удалить робота «${r.name}» с платформы? Его сделки и метрики будут удалены.`)) return;
    busy = true; notice = '';
    try {
      const res = await fetchWithAuth(`/api/v1/robots/${r.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(await res.text());
      notice = `Удалён с платформы: ${r.name}`;
      await load();
    } catch (e) {
      notice = 'Ошибка удаления: ' + String(e);
    }
    busy = false;
  }

  async function toggleDeploy(r: any) {
    busy = true; notice = '';
    const action = r.deployed ? 'undeploy' : 'deploy';
    try {
      const res = await fetchWithAuth(`/api/v1/robots/${r.id}/${action}`, { method: 'POST' });
      if (!res.ok) throw new Error(await res.text());
      notice = r.deployed ? `Остановлен: ${r.name}` : `Запущен: ${r.name}`;
      await load();
    } catch (e) {
      notice = 'Ошибка: ' + String(e);
    }
    busy = false;
  }

  function symbolOf(r: any) {
    const pj = r.params_json;
    return (typeof pj === 'object' ? pj?.symbol : null) ?? '';
  }

  // ── Detail tab: all tested instruments × params for one strategy ──────────────
  let detail = $state<any | null>(null);          // {id, name, rows, period, schema, sweep, top3}
  let detailLoading = $state(false);
  let sortBy = $state<{ col: string; dir: 1 | -1 }>({ col: 'recovery_factor_mtm_oos', dir: -1 });
  let expanded = $state<Set<string>>(new Set());   // instrument symbols shown in full
  const COLLAPSED_ROWS = 5;
  function toggleExpand(sym: string) {
    const s = new Set(expanded);
    s.has(sym) ? s.delete(sym) : s.add(sym);
    expanded = s;
  }

  function tmplOf(id: string) {
    const t = strategies.find((s) => s.id === id);
    if (t) return t;
    // Контр-стратегия <base>__inv: своего шаблона в списке нет, но это та же
    // логика с инвертированным сигналом. Синтезируем шаблон из базы — бэкенд
    // make_on_bar('<base>__inv') сам инвертирует. Так лидер-контра открывается
    // пересчётом, а не упирается в «шаблона нет».
    if (id && id.endsWith('__inv')) {
      const base = strategies.find((s) => s.id === id.slice(0, -'__inv'.length));
      if (base && String(base.script_code || '').includes('make_on_bar')) {
        return { ...base, id,
          script_code: `from trader.lab.strategies.library import make_on_bar; on_bar = make_on_bar('${id}')` };
      }
    }
    return undefined;
  }

  async function openDetail(robot: any) {
    detailLoading = true; notice = ''; detail = null;
    try {
      const res = await fetchWithAuth(`/api/v1/botstore/${encodeURIComponent(robot.id)}/results`);
      const data = res.ok ? await res.json() : { rows: [], period: null };
      const tmpl = tmplOf(robot.id);
      detail = {
        id: robot.id, name: robot.name,
        rows: data.rows ?? [], period: data.period,
        schema: tmpl?.params_schema ?? [],
        sweep: data.sweep ?? robot.sweep ?? null,
        top3: data.top3 ?? robot.top3 ?? [],
        campaigns: data.campaigns ?? null,
      };
      sortBy = { col: 'recovery_factor_mtm_oos', dir: -1 };   // default: out-of-sample robustness
      expanded = new Set();
    } catch (e) {
      notice = 'Ошибка загрузки деталей: ' + String(e);
    }
    detailLoading = false;
  }
  function closeDetail() { detail = null; }
  // campaigns of the open strategy card, newest first: from the endpoint's explicit
  // list (rows are capped per-symbol and may not contain every campaign).
  const detailCampaigns = $derived(detail?.campaigns
    ?? [...new Set((detail?.rows ?? []).map((r: any) => r.campaign_run).filter(Boolean))].sort().reverse());

  // Instrument tables: groups ordered top→bottom by best financial result (net_profit),
  // rows within each ordered by the active sort column (default net_profit desc).
  let instrumentTables = $derived.by(() => {
    const groups: Record<string, any[]> = {};
    for (const r of (detail?.rows ?? [])) (groups[r.symbol] ??= []).push(r);
    const arr = Object.entries(groups).map(([sym, rows]) => ({
      sym,
      rows: sortRows(rows),
      best: Math.max(...rows.map(r => (r.net_profit ?? -Infinity))),
    }));
    arr.sort((a, b) => b.best - a.best);
    return arr;
  });
  let paramCols = $derived.by(() => {
    const set = new Set<string>();
    for (const r of (detail?.rows ?? []))
      for (const k of Object.keys(r.params ?? {})) if (k !== 'symbol') set.add(k);
    return [...set];
  });
  function schemaOf(key: string) {
    return (detail?.schema ?? []).find((s: any) => s.key === key) ?? {};
  }
  function labelOf(key: string) { return schemaOf(key)?.label || key; }
  function descOf(key: string) { const s = schemaOf(key); return s?.desc || s?.hint || ''; }

  // Per-parameter sweep spec, derived from the ACTUAL tested values across all
  // instruments: range (от…до), typical step, and how many distinct values ran.
  let paramSpecs = $derived.by(() => {
    const rows = detail?.rows ?? [];
    return paramCols.map(key => {
      const vals = [...new Set(rows.map(r => Number(r.params?.[key])).filter(v => Number.isFinite(v)))].sort((a, b) => a - b);
      const sc = schemaOf(key);
      let step: number | null = null;   // smallest positive gap between tested values
      for (let i = 1; i < vals.length; i++) {
        const d = vals[i] - vals[i - 1];
        if (d > 0 && (step == null || d < step)) step = d;
      }
      return {
        key, label: sc?.label || key, desc: sc?.desc || sc?.hint || '',
        min: vals.length ? vals[0] : sc?.min, max: vals.length ? vals[vals.length - 1] : sc?.max,
        step, count: vals.length, values: vals,
      };
    });
  });
  function cellVal(r: any, col: string) {
    return (r.params && col in r.params) ? r.params[col] : r[col];
  }
  function sortRows(rows: any[]) {
    const { col, dir } = sortBy;
    return [...rows].sort((a, b) => {
      const av = cellVal(a, col), bv = cellVal(b, col);
      const an = av == null ? -Infinity : Number(av);
      const bn = bv == null ? -Infinity : Number(bv);
      return (an - bn) * dir;
    });
  }
  function setSort(col: string) {
    sortBy = sortBy.col === col ? { col, dir: (sortBy.dir === 1 ? -1 : 1) } : { col, dir: -1 };
  }

  // ── Backtest window: fresh single run for one (instrument, params) ────────────
  let chart = $state<any | null>(null);   // {symbol, params, result, pointValue, dateFrom, dateTo}
  let chartLoading = $state(false);
  let chartErr = $state('');
  let chartJob = $state<any | null>(null);     // {symbol, params} shown during the run
  let chartStatus = $state<any | null>(null);  // live: {runner, status, elapsed, vds_load, agent_alive, vds_down}

  const toISO = (d: Date) => d.toISOString();
  function yesterday() { const d = new Date(); d.setDate(d.getDate() - 1); return d; }
  function daysAgo(n: number) { const d = new Date(); d.setDate(d.getDate() - n); return d; }
  const ruStatus = (s: string) => ({ queued: 'в очереди', pending: 'запуск', running: 'считается', done: 'готово', failed: 'ошибка' } as any)[s] || s || '…';

  // One run+poll attempt on a given engine. Returns the result row, sets chartStatus
  // live, throws on failure (the agent's error text bubbles up).
  async function _runAndPoll(engine: string, scriptCode: string, params: any, symbol: string,
                             dateFrom: string, dateTo: string, t0: number) {
    const res = await fetchWithAuth('/api/v1/backtest/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scriptCode, baseParams: { ...params, symbol }, symbol, paramsGrid: {}, engine, dateFrom, dateTo }),
    });
    if (!res.ok) throw new Error(await res.text());
    const { run_id } = await res.json();
    for (let i = 0; i < 180; i++) {           // up to 6 min
      await new Promise(r => setTimeout(r, 2000));
      let sd: any;
      try {
        const sr = await fetchWithAuth(`/api/v1/backtest/${run_id}/status`);
        if (!sr.ok) throw new Error('status ' + sr.status);
        sd = await sr.json();
      } catch {
        chartStatus = { ...chartStatus, vds_down: true, elapsed: Math.round((Date.now() - t0) / 1000) };
        continue;
      }
      chartStatus = {
        runner: sd.runner, status: sd.status, elapsed: Math.round((Date.now() - t0) / 1000),
        vds_load: sd.vds_load, agent_alive: sd.agent_alive, vds_down: false,
      };
      if (sd.status === 'done') {
        // full=1: bare /results strips trades/equity (sweep fast-path) — the chart needs the fills
        const rr = await fetchWithAuth(`/api/v1/backtest/${run_id}/results?full=1`);
        const rows = rr.ok ? await rr.json() : [];
        const result = rows[0] ?? null;
        if (!result) throw new Error('Прогон завершён, но результат пуст');
        return result;
      }
      if (sd.status === 'failed') throw new Error(sd.error_msg || 'Бэктест завершился ошибкой (см. логи VDS)');
    }
    throw new Error('Бэктест не вернул результат за 6 мин (таймаут ожидания)');
  }

  // opts {strategyId, dateFrom, dateTo}: campaign rows open a chart WITHOUT a catalog
  // detail card — strategy + the sweep's own date window come from the row itself.
  async function openChart(symbol: string, params: any, opts: any = null) {
    if (!detail && !opts) return;
    chartErr = ''; chartLoading = true; chart = null;
    chartJob = { symbol, params };
    chartStatus = { runner: '…', status: 'queued', elapsed: 0, vds_load: null, agent_alive: null, vds_down: false };
    const t0 = Date.now();
    try {
      // Reproduce the EXACT leaderboard window so the chart MATCHES the table number.
      const dateFrom = opts?.dateFrom ? opts.dateFrom + 'T00:00:00' : (detail?.period?.date_from ?? toISO(daysAgo(95)));
      const dateTo = opts?.dateTo ? opts.dateTo + 'T23:59:59' : (detail?.period?.date_to ?? toISO(yesterday()));
      // Prefer a pre-computed stored result (top-N backfill) — opens from the DB with NO
      // template and NO re-run. Matched by rank (row position in the net-desc best list).
      // Counter campaigns (strategy "<id>__inv") have no template at all, so this is their
      // ONLY path — it MUST run before the template lookup below.
      if (opts?.campaign && opts?.rank != null) {
        try {
          const q = `campaign_run=${encodeURIComponent(opts.campaign)}&rank=${opts.rank}`;
          const cr = await fetchWithAuth(`/api/v1/lab/campaign-result?${q}`);
          if (cr.ok) {
            const stored = await cr.json();
            if (stored && Array.isArray(stored.trades) && stored.trades.length) {
              let pv0 = stored.point_value;
              if (!pv0) {
                try { const mr = await fetchWithAuth(`/api/v1/instruments/${encodeURIComponent(symbol)}/meta`); if (mr.ok) pv0 = (await mr.json())?.point_value || 1; } catch { pv0 = 1; }
              }
              chart = { symbol, params, result: stored, pointValue: pv0 || 1, dateFrom, dateTo, opts };
              chartLoading = false; chartStatus = null;
              return;
            }
          }
        } catch { /* fall through to a live re-run */ }
      }
      // A live re-run needs the strategy template. Counter variants have none -> clear msg.
      const tmpl = tmplOf(opts?.strategyId ?? detail?.id);
      if (!tmpl) throw new Error('Сохранённый результат ещё не готов (идёт бэкфилл топ-3), а пересчёт контр-стратегии из витрины недоступен. Обнови через минуту.');
      const sc = tmpl.script_code;
      // Контр-стратегии (__inv) считаем на VDS напрямую: у i9 синхронизируемая
      // руками копия без свежей логики __inv (падает KeyError). VDS = код STL,
      // где инверсия уже есть. Обычные стратегии — как раньше, приоритет i9.
      const isInv = String(opts?.strategyId ?? detail?.id ?? '').endsWith('__inv');
      let result: any;
      try {
        // Prefer the i9 agent (keeps load off the VDS); backend picks remote if alive.
        result = await _runAndPoll(isInv ? 'local' : 'auto', sc, params, symbol, dateFrom, dateTo, t0);
      } catch (e1) {
        // i9 flaps its corporate network (ISS unreachable → "All connection attempts
        // failed"). Fall back to the VDS, which has cached bars + reliable ISS.
        const msg = String(e1);
        const netFail = /connection|All connection attempts|ISS|timeout|таймаут|connect/i.test(msg);
        if (chartStatus?.runner === 'i9' || netFail) {
          chartStatus = { ...chartStatus, runner: 'VDS (фолбэк)', status: 'queued', vds_down: false };
          result = await _runAndPoll('local', sc, params, symbol, dateFrom, dateTo, t0);
        } else {
          throw e1;
        }
      }
      let pv = 1;
      try {
        const mr = await fetchWithAuth(`/api/v1/instruments/${encodeURIComponent(symbol)}/meta`);
        if (mr.ok) { const m = await mr.json(); pv = m?.point_value || 1; }
      } catch { /* default 1 */ }
      chart = { symbol, params, result, pointValue: pv, dateFrom, dateTo, opts };
    } catch (e) {
      chartErr = String(e);
    }
    chartLoading = false;
    chartStatus = null;
  }
  function closeChart() { chart = null; chartErr = ''; chartJob = null; chartStatus = null; }

  // ── Launch a backtested config into trading (Paper / Live) ───────────────
  let launchOpen = $state(false);
  let launchMode = $state<'paper' | 'live'>('paper');
  let launchName = $state('');
  let launchBusy = $state(false);
  let launchMsg = $state('');
  let launchOk = $state(false);
  let launchParams = $state<Record<string, any>>({});
  let launchSid = $state('');
  let launchSymbol = $state('');
  const launchSchema = $derived(
    (strategies.find((s) => s.id === launchSid || s.id === launchSid.replace(/__inv$/, ''))?.params_schema ?? [])
  );
  // Editable fields: prefer the schema (labels/min/max), else the raw param keys.
  const launchFields = $derived(
    launchSchema.length
      ? launchSchema.filter((p: any) => p.key !== 'symbol')
      : Object.keys(launchParams).filter((k) => k !== 'symbol')
          .map((k) => ({ key: k, label: k, type: typeof launchParams[k] === 'number' ? 'number' : 'text', min: undefined, max: undefined, hint: '' }))
  );
  // Behavior reflects the EDITED params so the operator sees what GO will actually do.
  const launchBehavior = $derived(behaviorFor(launchSid, { ...launchParams, symbol: launchSymbol }, launchSymbol));
  // The QUIK agent's runner only knows base library strategies — counter (__inv) and the
  // standalone us_open module can't run there, so Live-on-agent is gated to library bases.
  const canLiveAgent = $derived(!!launchSid && !launchSid.endsWith('__inv') && launchSid !== 'us_open_fvg');

  function scriptCodeFor(sid: string): string | null {
    if (!sid) return null;
    if (sid.endsWith('__inv')) {
      const base = sid.slice(0, -5);
      return "from trader.lab.strategies.library import REGISTRY, make_on_bar\n"
        + `_b = REGISTRY['${base}']\n`
        + "def _inv(bars, p):\n    w = _b['signal'](bars, p)\n    return (-w) if w in (1, -1) else w\n"
        + `REGISTRY['${base}__inv'] = dict(_b, id='${base}__inv', signal=_inv)\n`
        + `on_bar = make_on_bar('${base}__inv')`;
    }
    return strategies.find((s) => s.id === sid)?.script_code ?? null;
  }
  function openLaunch() {
    if (!chart) return;
    openLaunchFor(chart.opts?.strategyId ?? detail?.id ?? '', chart.symbol, chart.params);
  }
  // Open the launch panel from any card/row (showcase or sweep run), pre-filled + Paper.
  function openLaunchFor(sid: string, symbol: string, params: any) {
    launchSid = sid; launchSymbol = symbol;
    launchName = `${nameFor(sid)} · ${symbol}`.slice(0, 64);
    launchParams = { ...(params ?? {}) };   // editable copy — change before GO
    launchMode = 'paper'; launchMsg = ''; launchOk = false; launchOpen = true;
  }
  async function doLaunch() {
    if (!launchSid || !launchSymbol) return;
    const sc = scriptCodeFor(launchSid);
    if (!sc) { launchMsg = 'Не удалось собрать код стратегии для запуска.'; return; }
    if (!stlLinks.length) { launchMsg = 'Нет STL Link (коннектора счёта) — создай его на вкладке подключения.'; return; }
    if (launchMode === 'live' && !canLiveAgent) { launchMsg = 'На агенте (Live) доступны только базовые библиотечные стратегии. Контр- и us_open запускай в Paper.'; return; }
    // Coerce numeric fields (inputs give strings) and pin the backtested symbol.
    const params: Record<string, any> = { ...launchParams, symbol: launchSymbol };
    for (const p of launchSchema) if (p.type === 'number' && params[p.key] !== undefined && params[p.key] !== '') params[p.key] = Number(params[p.key]);
    const maxPos = Math.max(1, Number(params.avg_max ?? params.qty ?? 1) || 1);
    launchBusy = true; launchMsg = ''; launchOk = false;
    try {
      const res = await fetchWithAuth('/api/v1/robots', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ userEmail: 'admin', stlLinkId: stlLinks[0]?.id ?? '',
          name: launchName || `${nameFor(launchSid)} · ${launchSymbol}`,
          scriptCode: sc, paramsJson: params, schedule: '09:00-23:55' }),
      });
      if (!res.ok) throw new Error(await res.text());
      const { id } = await res.json();
      if (launchMode === 'paper') {
        const d = await fetchWithAuth(`/api/v1/robots/${id}/deploy`, { method: 'POST' });
        if (!d.ok) throw new Error(await d.text());
        launchMsg = `Запущен в PAPER: «${launchName}». Смотри его в Live Robots / витрине.`;
        await load();
      } else {
        const d = await fetchWithAuth(`/api/v1/quik/robots/${id}/deploy-agent`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ strategy_id: launchSid, params, symbol: launchSymbol,
            schedule: '09:00-23:55', max_position: maxPos, paper: true }),
        });
        if (!d.ok) throw new Error(await d.text());
        launchMsg = `Развёрнут на агенте в PAPER: «${launchName}». РЕАЛЬНЫЕ ДЕНЬГИ включаются ТОЛЬКО на консоли агента (робот FLAT + ввод его ID) — из GUI реал не армится, это правило безопасности.`;
      }
      launchOk = true;
    } catch (e) {
      launchMsg = 'Ошибка запуска: ' + String(e);
    }
    launchBusy = false;
  }

  // ── Agent activity panel (background optimizer status) ───────────────────────
  let activity = $state<any | null>(null);
  let activityTimer: any = null;
  let pauseBusy = $state(false);
  const fmtClock = (iso: any) => iso ? new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
  async function loadActivity() {
    try {
      const r = await fetchWithAuth('/api/v1/agent/activity');
      if (r.ok) activity = await r.json();
    } catch { /* keep last */ }
  }
  async function togglePause(engine: string, pause: boolean) {
    pauseBusy = true;
    try {
      const ep = pause ? '/api/v1/agent/pause' : '/api/v1/agent/resume';
      await fetchWithAuth(ep, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ engine }),
      });
      await loadActivity();
    } catch { /* keep current state */ }
    pauseBusy = false;
  }
  // map a strategy id (macd_cross) to its robot display name
  function robotName(stratId: string) {
    return catalog.find((r: any) => r.id === stratId)?.name ?? stratId;
  }

  $effect(() => {
    load();
    loadActivity();
    loadCampaignsList();
    activityTimer = setInterval(loadActivity, 5000);
    // Deep-link: /?campaign=camp-YYYYMMDD-... opens the campaign detail panel directly.
    const wanted = new URLSearchParams(window.location.search).get('campaign');
    if (wanted) openCampaign(wanted);
    return () => clearInterval(activityTimer);
  });

  // ── Campaign detail modal: params, ranges, sampling method, return×RF heatmap ──
  // Перечень проведённых переборов (все кампании, схлопнутые строки)
  let campaignsList = $state<any[]>([]);
  async function loadCampaignsList() {
    try {
      const r = await fetchWithAuth('/api/v1/lab/campaigns');
      if (r.ok) campaignsList = await r.json();
    } catch { /* list is auxiliary */ }
  }
  let campaign = $state<any | null>(null);
  let campaignLoading = $state(false);
  let heatCanvas = $state<HTMLCanvasElement | null>(null);
  async function openCampaign(id: string) {
    if (!id) return;
    campaignLoading = true; campaign = null; notice = '';
    try {
      const r = await fetchWithAuth(`/api/v1/agent/campaign?id=${encodeURIComponent(id)}`);
      if (r.ok) campaign = await r.json();
      else notice = 'Кампания недоступна';
    } catch (e) { notice = 'Ошибка загрузки кампании: ' + String(e); }
    campaignLoading = false;
  }
  function closeCampaign() { campaign = null; }

  function heatColor(n: number, mx: number) {
    if (n <= 0) return '#0c0c18';
    const t = Math.log(1 + n) / Math.log(1 + mx);     // log scale: counts span orders
    const r = Math.round(18 + t * 36), g = Math.round(36 + t * 184), b = Math.round(54 + t * 56);
    return `rgb(${r},${g},${b})`;
  }
  function drawHeatmap(cv: HTMLCanvasElement, c: any) {
    const GW = c.grid_w, GH = c.grid_h, mx = c.max_count || 1;
    const cell = 22, padL = 46, padB = 30, padT = 6, padR = 6;
    cv.width = padL + GW * cell + padR;
    cv.height = padT + GH * cell + padB;
    const ctx = cv.getContext('2d'); if (!ctx) return;
    ctx.clearRect(0, 0, cv.width, cv.height);
    for (let row = 0; row < GH; row++)
      for (let col = 0; col < GW; col++) {
        ctx.fillStyle = heatColor(c.grid[row][col], mx);
        ctx.fillRect(padL + col * cell, padT + row * cell, cell - 1, cell - 1);
      }
    ctx.fillStyle = '#8aa'; ctx.font = '9px sans-serif';
    const [rlo, rhi] = c.return_range ?? [0, 1], [flo, fhi] = c.rf_range ?? [0, 1];
    ctx.textAlign = 'center';
    for (let t = 0; t <= 4; t++)
      ctx.fillText((rlo + (rhi - rlo) * t / 4 >= 0 ? '+' : '') + ((rlo + (rhi - rlo) * t / 4) * 100).toFixed(0) + '%',
                   padL + GW * cell * t / 4, cv.height - 16);
    ctx.fillText('доходность →', padL + GW * cell / 2, cv.height - 3);
    ctx.textAlign = 'right';
    for (let t = 0; t <= 4; t++)
      ctx.fillText((fhi - (fhi - flo) * t / 4).toFixed(1), padL - 5, padT + GH * cell * t / 4 + 3);
    ctx.save(); ctx.translate(9, padT + GH * cell / 2); ctx.rotate(-Math.PI / 2);
    ctx.textAlign = 'center'; ctx.fillText('Recovery Factor →', 0, 0); ctx.restore();
  }
  $effect(() => {
    if (campaign?.grid?.length && heatCanvas) drawHeatmap(heatCanvas, campaign);
  });
</script>

<div class="bs-root">
  <div class="bs-preamble">
    <div class="bs-title">Botstore — каталог и установка роботов</div>
    <div class="bs-note">
      Слева — что найдено и протестировано на истории (вариантов: <b>{totalVariants.toLocaleString('ru-RU')}</b>).
      Справа — роботы, установленные на платформе. Доходность от первоначальных инвестиций
      <b>{initialEquity.toLocaleString('ru-RU')} ₽</b>, в рублях по реальной стоимости пункта и ГО (MOEX ISS).
      Результаты — in-sample, без walk-forward: шортлист для форвард-теста, не для слепого запуска.
    </div>
    {#if notice}<div class="bs-notice">{notice}</div>{/if}
  </div>

  <!-- Agent / background-optimizer live status (so you can watch the headless agent) -->
  {#if campaignsList.length}
    <div class="camp-list">
      <div class="cl-head">
        <span class="cl-title">Проведённые переборы ({campaignsList.length})</span>
        <button class="cl-csv" onclick={() => downloadCSV(campaignsList, 'campaigns')}>Выгрузить в CSV</button>
      </div>
      <div class="cl-rows">
        {#each campaignsList as c}
          <button class="cl-row" onclick={() => openCampaign(c.campaign)}
                  title="Открыть кампанию: карта плотности, лучшие наборы, график по клику">
            <span class="mono cl-id">{c.campaign}</span>
            <span class="cl-meta">{(c.strategies ?? []).join(', ')} · {(c.symbols ?? []).join(', ')}</span>
            <span class="cl-meta">{c.combos?.toLocaleString('ru-RU')} комб. · кандидатов {c.candidates?.toLocaleString('ru-RU')}</span>
            <span class="cl-meta">{c.date_from ?? '—'} — {c.date_to ?? '—'}</span>
          </button>
        {/each}
      </div>
    </div>
  {/if}

  {#if activity}
    <div class="ag-panel">
      <div class="ag-row">
        <span class="ag-dot" class:on={activity.online}></span>
        <span class="ag-title">Агент i9: <b class={activity.online ? 'pos' : 'neg'}>{activity.online ? 'онлайн' : 'офлайн'}</b></span>
        {#if activity.agent_id}<span class="ag-sub">{activity.agent_id}</span>{/if}
        <span class="ag-sub">посл. активность {fmtClock(activity.last_seen)}</span>
        {#if activity.vds_fallback}<span class="ag-badge">VDS-фолбэк активен</span>{/if}
        <span class="ag-ctrl">
          <button class="ag-btn start" class:dim={activity.paused_remote}
                  disabled={pauseBusy} onclick={() => togglePause('remote', false)}
                  title="Возобновить перебор на i9">▶</button>
          <button class="ag-btn stop" class:dim={!activity.paused_remote}
                  disabled={pauseBusy} onclick={() => togglePause('remote', true)}
                  title="Приостановить перебор на i9">⏹</button>
        </span>
        <span class="ag-sub ag-right" title="loadavg ХОСТЕРА (не i9) · пропускная способность">хостер load {activity.vds_load ?? '—'} · {activity.throughput_per_min} задач/мин</span>
      </div>

      <!-- What the i9 CPU is actually doing (from the agent heartbeat) -->
      {#if activity.i9}
        <div class="ag-i9" class:stale={activity.i9.stale}>
          <div class="i9-head">
            <span class="i9-label">CPU i9</span>
            {#if activity.i9.cpu_pct != null}
              <span class="i9-cpu-num mono" class:hot={activity.i9.cpu_pct >= 85}>{activity.i9.cpu_pct}%</span>
            {:else}
              <span class="i9-warn">нет psutil — на i9: <span class="mono">pip install psutil</span></span>
            {/if}
            <span class="i9-sub mono" title="воркеры / всего ядер">{activity.i9.workers ?? '?'}w · {activity.i9.cpu_count ?? '?'} ядер</span>
            {#if activity.i9.ram_pct != null}<span class="i9-sub" title="{activity.i9.ram_used_mb}/{activity.i9.ram_total_mb} МБ">RAM {activity.i9.ram_pct}%</span>{/if}
            {#if activity.i9.version}<span class="i9-sub dim">v{activity.i9.version}</span>{/if}
            <span class="i9-act ag-right" title="что CPU делает прямо сейчас">
              {#if activity.i9.activity?.state === 'job'}● считает <b class="mono">{activity.i9.activity.symbol}</b> · {activity.i9.activity.combos} комб.
              {:else if activity.i9.activity?.state === 'task'}● задача <span class="mono">{activity.i9.activity.func}</span>
              {:else if activity.i9.activity?.state === 'idle'}○ простаивает
              {:else}{activity.i9.activity?.state ?? '—'}{/if}
            </span>
            {#if activity.i9.stale}<span class="i9-stale-b">нет пинга {activity.i9.age_sec ?? '∞'}с</span>{/if}
          </div>
          {#if activity.i9.cpu_pct != null}
            <div class="i9-cpubar"><div class="i9-cpubar-fill" class:hot={activity.i9.cpu_pct >= 85} style="width:{Math.min(100, activity.i9.cpu_pct)}%"></div></div>
          {/if}
          {#if activity.i9.per_core?.length}
            <div class="i9-cores">
              {#each activity.i9.per_core as c}
                <div class="i9-core" title="{c}%"><div class="i9-core-fill" style="height:{Math.max(3, Math.min(100, c))}%"></div></div>
              {/each}
            </div>
          {/if}
          {#if activity.i9.leaders?.length}
            <div class="i9-leaders">
              <span class="i9-sub">формирующийся хитпарад · клик → график + сделки + P&amp;L:</span>
              {#each activity.i9.leaders as L}
                <button class="i9-leader" onclick={() => openChart(L.symbol, L.params, { strategyId: L.strategy })}
                        title="Пересчитать эту комбо и открыть график со сделками и P&L">
                  <b class="mono">{nameFor(L.strategy)}·{L.symbol}</b>
                  <span class="mono" class:pos={L.net >= 0} class:neg={L.net < 0}>{L.net >= 0 ? '+' : ''}{(L.net ?? 0).toLocaleString('ru-RU')} ₽</span>
                  <span class="i9-sub">RF {L.rf == null ? '—' : (+L.rf).toFixed(2)} · {L.trades ?? '—'} сд.</span>
                </button>
              {/each}
            </div>
          {/if}
        </div>
      {:else}
        <div class="ag-row"><span class="ag-sub dim">CPU i9: телеметрии нет — обнови opt_agent на i9 (+ <span class="mono">pip install psutil</span>)</span></div>
      {/if}

      <div class="ag-row">
        <span class="ag-sub">VDS-воркеры:</span>
        <span class="ag-sub" class:pos={!activity.paused_local} class:neg={activity.paused_local}>
          {activity.paused_local ? 'на паузе' : 'активны'}
        </span>
        <span class="ag-ctrl">
          <button class="ag-btn start" class:dim={activity.paused_local}
                  disabled={pauseBusy} onclick={() => togglePause('local', false)}
                  title="Возобновить перебор на VDS">▶</button>
          <button class="ag-btn stop" class:dim={!activity.paused_local}
                  disabled={pauseBusy} onclick={() => togglePause('local', true)}
                  title="Приостановить перебор на VDS">⏹</button>
        </span>
      </div>
      {#if activity.last_runs?.length}
        <div class="ag-runs">
          <span class="ag-sub">последние прогоны:</span>
          {#each activity.last_runs.slice(0, 6) as lr}
            <span class="ag-run" title={lr.error ?? lr.id}>
              <span class="mono">{lr.id.slice(0, 10)}</span>
              · {lr.agent}
              · <span class="ag-st" class:ok={lr.status === 'done'} class:bad={lr.status === 'failed'}>{lr.status}</span>
              {#if lr.elapsed_sec != null}· {lr.elapsed_sec}с{/if}
              {#if lr.error}<span class="ag-err"> — {lr.error.slice(0, 90)}</span>{/if}
            </span>
          {/each}
        </div>
      {/if}

      {#if activity.campaign}
        <div class="ag-row">
          <button class="ag-camp" onclick={() => openCampaign(activity.campaign)}
                  title="Открыть детали кампании: параметры, диапазоны, метод выборки, тепловая карта">
            кампания {activity.campaign} <span class="ag-info">ⓘ детали</span>
          </button>
          {#if activity.current}
            <span class="ag-now" title="Что считается прямо сейчас">
              ● сейчас: <b>{robotName(activity.current.strategy)}</b> · <span class="mono">{activity.current.symbol}</span>
            </span>
          {/if}
          <div class="ag-prog ag-right-prog" title="готово {activity.counts.done} · ошибок {activity.counts.failed} · в очереди {activity.counts.queued} · считается {activity.counts.running}">
            <div class="ag-prog-bar" style="width:{activity.pct}%"></div>
            <span class="ag-prog-lbl">{activity.pct}% · done {activity.counts.done}/{(activity.counts.done||0)+(activity.counts.failed||0)+(activity.counts.queued||0)+(activity.counts.running||0)} · queued {activity.counts.queued} · running {activity.counts.running}</span>
          </div>
        </div>
      {/if}
      {#if activity.recent?.length}
        <div class="ag-recent">
          {#each activity.recent as r}
            <span class="ag-job" title="{r.agent} · {fmtClock(r.finished_at)}">
              <span class="ag-jst" class:pos={r.status==='done'} class:neg={r.status==='failed'}>{r.status==='done' ? '✓' : '✗'}</span>
              {r.strategy}·{r.symbol}
            </span>
          {/each}
        </div>
      {/if}
    </div>
  {/if}

  {#if loading}
    <div class="bs-msg">Загрузка…</div>
  {:else if error}
    <div class="bs-msg err">{error}</div>
  {:else}
    <div class="bs-cols">
      <!-- LEFT: tested catalog -->
      <div class="bs-col">
        <div class="bs-col-head">Протестировано ({catalog.length})</div>
        <div class="bs-list">
          {#if catalog.length === 0}
            <div class="bs-empty">Фоновые прогоны ещё не накопили результатов.</div>
          {/if}
          {#each catalog as robot}
            {@const b = best(robot)}
            <div class="cat-card" class:sel={selectedCat === robot.id}
                 class:sweeping={activity?.current?.strategy === robot.id}
                 role="button" tabindex="0" title="Открыть детали тестирования"
                 onclick={() => { selectedCat = robot.id; openDetail(robot); }}
                 onkeydown={(e) => e.key === 'Enter' && (selectedCat = robot.id, openDetail(robot))}>
              <div class="cc-top">
                <RobotIdentity name={robot.name} id={robot.id} size="row" />
                {#if activity?.current?.strategy === robot.id}
                  <span class="cc-sweeping" title="По этому роботу сейчас идёт перебор">● перебор {activity.current.symbol}</span>
                {/if}
                <span class="cc-variants">{(robot.variants_tested || 0).toLocaleString('ru-RU')} вар.</span>
              </div>
              <div class="cc-metrics">
                <span class="cc-inst">{b?.symbol ?? '—'}</span>
                <span class:pos={b?.total_return > 0} class:neg={b?.total_return < 0}>{fmtPct(b?.total_return)}</span>
                <span class:pos={b?.net_profit > 0} class:neg={b?.net_profit < 0}>{fmtMoney(b?.net_profit)}</span>
                <span class="cc-dd">просадка {fmtPct(b?.max_drawdown)}</span>
                <span class="cc-rf">RF {fmtNum(b?.recovery_factor)}</span>
              </div>
              {#if b?.params}<div class="cc-params">{JSON.stringify(b.params)}</div>{/if}

              <!-- adaptive sweep status: % complete + machine time -->
              {#if robot.sweep}
                <div class="cc-sweep">
                  <div class="cc-prog" title="Завершено {robot.sweep.finished}/{robot.sweep.total} задач последнего перебора">
                    <div class="cc-prog-bar" style="width:{robot.sweep.pct}%"></div>
                    <span class="cc-prog-lbl">перебор {robot.sweep.pct}%</span>
                  </div>
                  <span class="cc-mt" title="Машинное время перебора параметров">⏱ {machineLabel(robot.sweep)}</span>
                </div>
              {/if}

              <!-- hit-parade: top-3 instruments by net result -->
              {#if robot.top3?.length}
                <div class="cc-top3">
                  {#each robot.top3 as t, i}
                    <span class="cc-medal">{['🥇','🥈','🥉'][i]}</span>
                    <span class="cc-t3sym">{t.symbol}</span>
                    <span class="cc-t3pnl" class:pos={t.net_profit > 0} class:neg={t.net_profit < 0}>{fmtMoneyShort(t.net_profit)}</span>
                  {/each}
                </div>
              {/if}

              <div class="cc-foot">
                <span class="cc-run">перебор {fmtDate(robot.sweep?.last_run ?? robot.last_run)}</span>
                <button class="cc-install" disabled={busy} onclick={(e) => { e.stopPropagation(); install(robot); }}>
                  Установить на платформу →
                </button>
              </div>
            </div>
          {/each}
        </div>
      </div>

      <!-- RIGHT: installed robots -->
      <div class="bs-col">
        <div class="bs-col-head">Установлено на платформе ({installed.length})</div>
        <div class="bs-list">
          {#if installed.length === 0}
            <div class="bs-empty">Нет установленных роботов. Установите слева.</div>
          {/if}
          {#each installed as r}
            <div class="inst-card">
              <div class="ic-top">
                <span class="ic-dot" class:live={r.deployed}></span>
                <RobotIdentity name={r.name} id={r.id} size="row" />
                <span class="ic-badge" class:on={r.deployed}>{r.deployed ? 'LIVE' : 'остановлен'}</span>
              </div>
              <div class="ic-meta">
                <span class="ic-inst">{symbolOf(r)}</span>
                <span class="ic-sched">окно {r.schedule}</span>
              </div>
              <div class="ic-params">{JSON.stringify(r.params_json)}</div>
              <div class="ic-src" title={r.script_code}>{(r.script_code ?? '').slice(0, 80)}{(r.script_code ?? '').length > 80 ? '…' : ''}</div>
              <div class="ic-foot">
                <button class="ic-undeploy" disabled={busy} onclick={() => toggleDeploy(r)}
                        title={r.deployed ? 'Остановить робота' : 'Запустить робота'}>
                  {r.deployed ? '⏹ STOP' : '▶ START'}
                </button>
                <button class="ic-remove" disabled={busy} onclick={() => remove(r)}>🗑 Удалить</button>
              </div>
            </div>
          {/each}
        </div>
      </div>
    </div>
    <button class="bs-refresh" onclick={load} disabled={busy}>Обновить</button>
  {/if}

  <!-- DETAIL TAB: every tested instrument × param-combo for one strategy -->
  {#if detail}
    <div class="detail-pane">
      <div class="dp-head">
        <button class="dp-back" onclick={closeDetail}>← Назад к каталогу</button>
        <RobotIdentity name={detail.name} id={detail.id} size="title" />
        <span class="dp-sub">
          инструментов: {instrumentTables.length} · вариантов: {detail.rows.length}
          {#if detail.period}· период {fmtDate(detail.period.date_from)} — {fmtDate(detail.period.date_to)}{/if}
        </span>
      </div>

      <!-- campaigns this strategy was swept in: click opens the campaign panel
           (density map + best rows -> chart) — the self-serve path to sweep results -->
      {#if detailCampaigns.length}
        <div class="dp-camps">
          <button class="cl-csv" onclick={() => downloadCSV(detail.rows, detail.id + '-variants')}>Выгрузить в CSV</button>
          <span class="cmp-k">кампании перебора:</span>
          {#each detailCampaigns as c}
            <button class="ag-camp" onclick={() => openCampaign(c)}
                    title="Открыть кампанию: карта плотности, лучшие наборы, график по клику">
              {c}
            </button>
          {/each}
        </div>
      {/if}

      <!-- adaptive sweep status: % complete · machine time · last run · top-3 -->
      {#if detail.sweep || detail.top3?.length}
        <div class="dp-status">
          {#if detail.sweep}
            <div class="dps-item">
              <div class="dps-prog" title="Завершено {detail.sweep.finished}/{detail.sweep.total} задач">
                <div class="dps-prog-bar" style="width:{detail.sweep.pct}%"></div>
                <span class="dps-prog-lbl">перебор {detail.sweep.pct}%</span>
              </div>
            </div>
            <div class="dps-item"><span class="dps-k">машинное время</span><span class="dps-v">{machineLabel(detail.sweep)}</span></div>
            <div class="dps-item"><span class="dps-k">последний перебор</span><span class="dps-v">{fmtDate(detail.sweep.last_run)}</span></div>
          {/if}
          {#if detail.top3?.length}
            <div class="dps-top3">
              <span class="dps-k">топ инструментов:</span>
              {#each detail.top3 as t, i}
                <span class="dps-medal">{['🥇','🥈','🥉'][i]}</span>
                <span class="dps-t3sym">{t.symbol}</span>
                <span class="dps-t3pnl" class:pos={t.net_profit > 0} class:neg={t.net_profit < 0}>{fmtMoneyShort(t.net_profit)}</span>
              {/each}
            </div>
          {/if}
        </div>
      {/if}

      <div class="dp-hint">Клик по строке — открыть бэктест за тот же период перебора (число на графике совпадёт с таблицей). Таблицы и строки отсортированы по финрезу; каждая свёрнута до {COLLAPSED_ROWS} строк.</div>
      <div class="dp-body">
        {#if detailLoading}
          <div class="bs-msg">Загрузка деталей…</div>
        {:else if detail.rows.length === 0}
          <div class="bs-empty">Для этой стратегии ещё нет результатов кампаний.</div>
        {:else}
          <!-- Parameter glossary + sweep spec (from .. to .. step .. why) -->
          <div class="dp-spec">
            <div class="dp-spec-title">Параметры и сетка перебора</div>
            <div class="dp-spec-why">
              Диапазон каждого числового параметра берётся из схемы стратегии (мин…макс),
              разбивается на ~4–5 шагов; комбинации затем случайно прорежены до лимита на стратегию.
              Это грубый разведочный скан пространства параметров (in-sample), чтобы найти
              перспективные зоны для последующего точечного уточнения и форвард-теста — не готовая
              оптимизация. Каждый прогон считается с тейкерной комиссией (биржа + брокер 0,45 ₽).
            </div>
            <table class="dp-spec-table">
              <thead>
                <tr><th>Параметр</th><th>Что задаёт</th><th class="num">От</th><th class="num">До</th><th class="num">Шаг</th><th class="num">Значений</th></tr>
              </thead>
              <tbody>
                {#each paramSpecs as p}
                  <tr>
                    <td class="ps-name">{p.label}<span class="ps-key">{p.key}</span></td>
                    <td class="ps-desc">{p.desc || '—'}</td>
                    <td class="num">{p.min ?? '—'}</td>
                    <td class="num">{p.max ?? '—'}</td>
                    <td class="num">{p.step ?? '—'}</td>
                    <td class="num">{p.count || '—'}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
          {#each instrumentTables as { sym, rows, best }}
            <div class="dp-inst">
              <div class="dp-inst-head">
                <span class="dp-inst-sym">{sym}</span>
                <span class="dp-inst-cnt">{rows.length} вар.</span>
                <span class="dp-inst-best" class:pos={best > 0} class:neg={best < 0}>лучший {fmtMoneyShort(best)}</span>
              </div>
              <div class="dp-table-wrap">
                <table class="dp-table">
                  <thead>
                    <tr>
                      {#each paramCols as col}
                        <th class="num" title={descOf(col)} onclick={() => setSort(col)}>{labelOf(col)}{sortBy.col === col ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      {/each}
                      <th class="num th-hl" onclick={() => setSort('ann_return_go')}>% год (ГО){sortBy.col === 'ann_return_go' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num th-hl" onclick={() => setSort('ann_return_full')}>% год (без пл.){sortBy.col === 'ann_return_full' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('total_return')}>Доходность{sortBy.col === 'total_return' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('net_profit')}>Прибыль ₽{sortBy.col === 'net_profit' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('max_drawdown')}>Просадка{sortBy.col === 'max_drawdown' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('sharpe')}>Sharpe{sortBy.col === 'sharpe' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('recovery_factor')}>closed-trade RF{sortBy.col === 'recovery_factor' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num th-hl" onclick={() => setSort('recovery_factor_mtm_oos')} title="Recovery Factor на OOS-отрезке (mark-to-market) — основной критерий устойчивости">RF (OOS){sortBy.col === 'recovery_factor_mtm_oos' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('max_mae')} title="худшая просадка по открытой позиции (Maximum Adverse Excursion)">MAE, ₽{sortBy.col === 'max_mae' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('windows_profitable')} title="сколько окон walk-forward закрылись в плюс, из скольких">окна{sortBy.col === 'windows_profitable' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('degrade')} title="OOS-доходность в % от IS-доходности (100% = деградации нет)">degrade %{sortBy.col === 'degrade' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('total_trades')}>Сделок{sortBy.col === 'total_trades' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('win_rate')}>Win%{sortBy.col === 'win_rate' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each (expanded.has(sym) ? rows : rows.slice(0, COLLAPSED_ROWS)) as r}
                      <tr class="dp-row" class:cand={r.candidate}
                          onclick={() => openChart(sym, r.params)} title="Открыть бэктест">
                        {#each paramCols as col}
                          <td class="num">{fmtNum(r.params?.[col], 0)}</td>
                        {/each}
                        <td class="num ann" class:pos={(r.ann_return_go ?? 0) > 0} class:neg={(r.ann_return_go ?? 0) < 0}>{fmtPct(r.ann_return_go)}</td>
                        <td class="num ann" class:pos={(r.ann_return_full ?? 0) > 0} class:neg={(r.ann_return_full ?? 0) < 0}>{fmtPct(r.ann_return_full)}</td>
                        <td class="num" class:pos={r.total_return > 0} class:neg={r.total_return < 0}>{fmtPct(r.total_return)}</td>
                        <td class="num" class:pos={r.net_profit > 0} class:neg={r.net_profit < 0}>{fmtMoney(r.net_profit)}</td>
                        <td class="num">{fmtPct(r.max_drawdown)}</td>
                        <td class="num">{fmtNum(r.sharpe)}</td>
                        <td class="num">{fmtNum(r.recovery_factor)}</td>
                        <td class="num">{fmtNum(r.recovery_factor_mtm_oos)}</td>
                        <td class="num">{fmtMoney(r.max_mae)}</td>
                        <td class="num">{r.windows_profitable ?? '—'}/{r.windows_total ?? '—'}</td>
                        <td class="num">{fmtPct(r.degrade)}</td>
                        <td class="num">{r.total_trades ?? '—'}</td>
                        <td class="num">{fmtPct(r.win_rate)}</td>
                        <td class="dp-go">
                          <button class="dp-go-btn" title="скопировать параметры и запустить робота в Paper"
                            onclick={(e) => { e.stopPropagation(); openLaunchFor(r.strategy ?? detail.id, sym, r.params); }}>▶ в торговлю</button>
                        </td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
              {#if rows.length > COLLAPSED_ROWS}
                <button class="dp-expand" onclick={() => toggleExpand(sym)}>
                  {expanded.has(sym) ? '▴ свернуть' : `▾ показать все (${rows.length})`}
                </button>
              {/if}
            </div>
          {/each}
        {/if}
      </div>
    </div>
  {/if}

  <!-- BACKTEST WINDOW: fresh single run for the chosen (instrument, params) -->
  {#if chart || chartLoading || chartErr}
    <div class="chart-modal" role="dialog" tabindex="-1">
      <div class="cm-box">
        <div class="cm-head">
          <span class="cm-title">
            {#if chart?.opts?.strategyId}
              <b class="cm-strat">{nameFor(chart.opts.strategyId)}</b>
              <a class="cm-doc" href={'/?strategy=' + encodeURIComponent(docBase(chart.opts.strategyId))} target="_blank" rel="noopener" title="полное описание стратегии">описание ↗</a>
            {:else}Бэктест{/if}
            <span class="cm-sym">· {chart?.symbol ?? chartJob?.symbol ?? ''}</span>
          </span>
          <div class="cm-actions">
            {#if chart}<button class="cm-launch" onclick={openLaunch} title="создать робота из этого набора параметров и запустить в торговлю">▶ Запустить в торговлю</button>{/if}
            <button class="cm-close" onclick={closeChart}>✕</button>
          </div>
        </div>
        <div class="cm-body">
          {#if chartLoading}
            <div class="cm-status">
              <div class="cm-spin">Прогоняю бэктест…</div>
              <div class="cm-line">Инструмент: <b>{chartJob?.symbol}</b> · <span class="cm-p">{JSON.stringify(chartJob?.params)}</span></div>
              <div class="cm-line">Где считается: <b>{chartStatus?.runner ?? '…'}</b> · {ruStatus(chartStatus?.status)} · прошло {chartStatus?.elapsed ?? 0}с</div>
              <div class="cm-line" class:warn={chartStatus?.vds_down}>
                {#if chartStatus?.vds_down}
                  ⚠ VDS не отвечает на опрос (возможно перегрузка) — продолжаю ждать…
                {:else}
                  VDS жив · loadavg {chartStatus?.vds_load ?? '—'} · i9-агент {chartStatus?.agent_alive ? 'онлайн' : 'офлайн'}
                {/if}
              </div>
              <div class="cm-hint">Считается на i9 (если онлайн) или на VDS в фоне (низкий приоритет), важный хост не нагружается. Обычно 10–60с; первый прогон по некэшированному инструменту — до ~2.5 мин (тянет историю с ISS).</div>
            </div>
          {:else if chartErr}
            <div class="cm-status">
              <div class="bs-msg err">{chartErr}</div>
              <div class="cm-hint">Если это таймаут загрузки истории — инструмент просто не кэширован, попробуйте открыть ещё раз (данные уже подтянулись). VDS остаётся жив, перебор продолжается.</div>
            </div>
          {:else if chart}
            <BacktestChart
              result={chart.result}
              symbol={chart.symbol}
              dateFrom={chart.dateFrom}
              dateTo={chart.dateTo}
              pointValue={chart.pointValue}
              defaultInterval={60}
              taker={true}
              runParams={chart.params}
              paramSchema={detail?.schema ?? []}
              onRerun={(p) => openChart(chart.symbol, p, chart.opts)}
            />
          {/if}
        </div>
      </div>
    </div>
  {/if}

  <!-- LAUNCH INTO TRADING: create a robot from the chart's config and start it -->
  {#if launchOpen}
    <div class="lp-modal" role="dialog" tabindex="-1">
      <div class="lp-box">
        <div class="lp-head">
          <span class="lp-h-title">Запустить в торговлю</span>
          <button class="cm-close" onclick={() => launchOpen = false}>✕</button>
        </div>
        <div class="lp-body">
          <div class="lp-strat">{nameFor(launchSid)}<span class="lp-sym">· {launchSymbol}</span></div>
          {#if launchBehavior}
            <div class="lp-behavior">
              <div class="lp-b-label">Что будет делать робот</div>
              <p class="lp-b-text">{launchBehavior}</p>
            </div>
          {/if}
          <label class="lp-field"><span class="lp-k">Имя робота</span>
            <input class="lp-input" bind:value={launchName} maxlength="64" /></label>
          <div class="lp-field"><span class="lp-k">Режим</span>
            <div class="lp-modes">
              <label class="lp-mode" class:sel={launchMode === 'paper'}>
                <input type="radio" bind:group={launchMode} value="paper" /> Paper — симуляция на платформе</label>
              <label class="lp-mode" class:sel={launchMode === 'live'} class:off={!canLiveAgent}>
                <input type="radio" bind:group={launchMode} value="live" disabled={!canLiveAgent} /> Live — на торговом агенте</label>
            </div>
          </div>
          {#if !canLiveAgent}<div class="lp-note">Live-на-агенте доступен только для базовых библиотечных стратегий. Контр- и us_open — запускай в Paper.</div>{/if}
          <div class="lp-params"><span class="lp-k">Параметры <span class="lp-k-note">— меняй перед запуском</span></span>
            <div class="lp-pgrid">
              <div class="lp-prow"><label class="lp-plabel">инструмент</label><span class="lp-pfix">{chart?.symbol}</span></div>
              {#each launchFields as p}
                <div class="lp-prow">
                  <label class="lp-plabel" title={p.hint ?? ''}>{p.label ?? p.key}</label>
                  {#if p.type === 'number'}
                    <input class="lp-pinput" type="number" min={p.min} max={p.max} bind:value={launchParams[p.key]} />
                  {:else}
                    <input class="lp-pinput" type="text" bind:value={launchParams[p.key]} />
                  {/if}
                </div>
              {/each}
            </div>
            <div class="lp-hint">поведенческая модель выше обновляется под твои значения</div>
          </div>
          {#if launchMode === 'live'}
            <div class="lp-warn">⚠ Live: робот встанет на агента в <b>PAPER</b>. Реальные деньги включаются ТОЛЬКО на консоли агента (робот FLAT + ввод его ID). Из GUI реал не армится — правило безопасности.</div>
          {/if}
          {#if launchMsg}<div class="lp-msg" class:ok={launchOk}>{launchMsg}</div>{/if}
        </div>
        <div class="lp-foot">
          <button class="lp-cancel" onclick={() => launchOpen = false}>{launchOk ? 'Закрыть' : 'Отмена'}</button>
          {#if !launchOk}<button class="lp-go" disabled={launchBusy} onclick={doLaunch}>{launchBusy ? 'Запускаю…' : 'GO ▶'}</button>{/if}
        </div>
      </div>
    </div>
  {/if}

  <!-- CAMPAIGN DETAIL: meta, sampling method, return×RF heatmap, best combos -->
  {#if campaign || campaignLoading}
    <div class="chart-modal" role="dialog" tabindex="-1">
      <div class="cmp-box">
        <div class="cm-head">
          <span class="cm-title cm-title-big">
            Витрина кампании перебора параметров
            {#if campaign}<span class="cm-params cm-params-big">для {campaign.campaign}</span>{/if}
          </span>
          <button class="cm-close" onclick={closeCampaign}>✕</button>
        </div>
        <div class="cmp-body">
          {#if campaignLoading}
            <div class="bs-msg">Загрузка деталей кампании…</div>
          {:else if campaign && campaign.combos === 0}
            <div class="bs-msg">Пока нет результатов: кампания только стартовала.</div>
          {:else if campaign}
            <div class="cmp-meta">
              <div><span class="cmp-k">старт</span><span class="cmp-v">{fmtDate(campaign.started)}</span></div>
              <div><span class="cmp-k">комбинаций</span><span class="cmp-v">{campaign.combos.toLocaleString('ru-RU')}</span></div>
              <div><span class="cmp-k">раунды</span><span class="cmp-v">{campaign.rounds?.length ? campaign.rounds.join(' → ') : 'r0'}</span></div>
              <div><span class="cmp-k">стратегий</span><span class="cmp-v">{campaign.strategies.length}</span></div>
              <div><span class="cmp-k">инструментов</span><span class="cmp-v">{campaign.symbols.length}</span></div>
            </div>

            <div class="cmp-method">
              <b>Метод — адаптивный перебор «широко → точно»:</b>
              <div><b>r0 (разведка):</b> случайная выборка по широким диапазонам каждого параметра (Latin-подобный random search) — быстро покрывает всё пространство большим шагом, не застревая в одной зоне.</div>
              <div><b>r1–r2 (уточнение):</b> вокруг победителей r0 (отбор по Recovery Factor × доходность) окно диапазона сужается вдвое каждый раунд, шаг мельче — детальная проработка перспективных зон.</div>
              <div class="cmp-note">Случайная выборка в огромных диапазонах + сужение к лучшим точкам: тратим прогоны там, где результат, а не на сетку из заведомо плохих комбинаций.</div>
            </div>

            <div class="cmp-chips">
              <span class="cmp-k">роботы:</span>
              {#each campaign.strategies as s}<span class="cmp-chip">{robotName(s)}</span>{/each}
            </div>
            <div class="cmp-chips">
              <span class="cmp-k">инструменты:</span>
              {#each campaign.symbols as s}<span class="cmp-chip mono">{s}</span>{/each}
            </div>

            <div class="cmp-heat">
              <div class="cmp-h-title">Тепловая карта результатов: доходность × Recovery Factor
                <span class="cmp-sub2">(плотность {campaign.combos.toLocaleString('ru-RU')} комбинаций; ярче = больше перебранных вариантов в зоне; оси обрезаны по 2–98 перцентилю)</span>
              </div>
              <canvas bind:this={heatCanvas} class="cmp-canvas"></canvas>
              <div class="cmp-legend">
                <span>мало</span>
                <span class="cmp-grad"></span>
                <span>много комбинаций</span>
                <span class="cmp-hint2">правый-верх = высокая доходность + высокий RF (искомая зона)</span>
              </div>
            </div>

            {#if campaign.best?.length}
              <div class="cmp-best">
                <div class="cmp-h-title">Лучшие по финрезу <span class="cmp-sub2">клик по строке — прогон с графиком (цена + сделки + доходность)</span>
                  <button class="cl-csv" onclick={() => downloadCSV(campaign.best, campaign.campaign + '-best')}>Выгрузить в CSV</button></div>
                <table class="cmp-bt">
                  <thead><tr><th>робот</th><th>инстр.</th><th>финрез</th><th>доходн.</th><th>RF</th><th>сделок</th><th>параметры</th></tr></thead>
                  <tbody>
                    {#each campaign.best as b, i}
                      <tr class="cmp-click"
                          title="Открыть график этого набора: цена со сделками + кривая доходности"
                          onclick={() => { const o = { strategyId: b.strategy, dateFrom: b.date_from, dateTo: b.date_to, campaign: campaign.campaign, rank: i }; closeCampaign(); openChart(b.symbol, b.params, o); }}>
                        <td class="cmp-l">{robotName(b.strategy)}</td>
                        <td class="mono">{b.symbol}</td>
                        <td class:pos={b.net_profit > 0} class:neg={b.net_profit < 0}>{fmtMoney(b.net_profit)}</td>
                        <td class:pos={b.total_return > 0} class:neg={b.total_return < 0}>{fmtPct(b.total_return)}</td>
                        <td>{fmtNum(b.recovery_factor)}</td>
                        <td class="mono">{b.total_trades ?? '—'}</td>
                        <td class="cmp-params-cell mono">{JSON.stringify(b.params)}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {/if}
          {/if}
        </div>
      </div>
    </div>
  {/if}
</div>

<style>
  .bs-root { position: relative; display: flex; flex-direction: column; height: 100%; overflow: hidden; background: #0a0a15; padding: 12px; gap: 10px; }
  .bs-preamble { background: #0f0f1e; border: 1px solid #2d2d4a; border-radius: 5px; padding: 10px 12px; flex-shrink: 0; }
  .bs-title { font-size: 13px; color: #4caf50; font-weight: 600; margin-bottom: 6px; }
  .bs-note { font-size: 11px; color: #888; line-height: 1.5; }
  .bs-note b { color: #ccc; }
  .bs-notice { margin-top: 6px; font-size: 11px; color: #6aa8ff; }

  /* agent activity panel */
  .ag-panel { background: #0c1322; border: 1px solid #24406a; border-radius: 5px; padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; flex-shrink: 0; }
  .ag-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 11px; color: #9ab; }
  .ag-dot { width: 8px; height: 8px; border-radius: 50%; background: #f44336; box-shadow: 0 0 5px #f4433688; flex-shrink: 0; }
  .ag-dot.on { background: #4caf50; box-shadow: 0 0 5px #4caf5088; }
  .ag-title { font-size: 12px; color: #cde; }
  .ag-sub { font-size: 10px; color: #678; }
  .ag-right { margin-left: auto; }
  .ag-badge { font-size: 10px; color: #ffb86b; border: 1px solid #ffb86b55; border-radius: 3px; padding: 1px 6px; }
  .ag-prog { position: relative; flex: 1; min-width: 200px; height: 16px; display: flex; align-items: center; background: #0a1120; border: 1px solid #1a2a44; border-radius: 3px; overflow: hidden; }
  .ag-prog-bar { position: absolute; inset: 0 auto 0 0; background: #1f5e3a; }
  .ag-prog-lbl { position: relative; font-size: 10px; color: #cfe; line-height: 1; padding-left: 8px; white-space: nowrap; }
  /* i9 CPU telemetry */
  .ag-i9 { display: flex; flex-direction: column; gap: 5px; padding: 6px 8px; background: #0a1424; border: 1px solid #1c3a5e; border-radius: 4px; }
  .ag-i9.stale { opacity: 0.55; }
  .i9-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 11px; color: #9ab; }
  .i9-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.6px; color: #6a8cc0; font-weight: 700; }
  .i9-cpu-num { font-size: 15px; font-weight: 700; color: #6fd08a; }
  .i9-cpu-num.hot { color: #ff8a5c; }
  .i9-sub { font-size: 10px; color: #789; }
  .i9-sub.dim { color: #556; }
  .i9-warn { font-size: 10px; color: #ffb86b; }
  .i9-act { font-size: 11px; color: #cde; }
  .i9-stale-b { font-size: 10px; color: #ff8a80; border: 1px solid #ff8a8055; border-radius: 3px; padding: 1px 6px; }
  .i9-cpubar { height: 6px; background: #0f1a2e; border-radius: 3px; overflow: hidden; }
  .i9-cpubar-fill { height: 100%; background: linear-gradient(90deg, #2f8f49, #6fd08a); transition: width 0.5s; }
  .i9-cpubar-fill.hot { background: linear-gradient(90deg, #b5502a, #ff8a5c); }
  .i9-leaders { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; margin-top: 2px; }
  .i9-leader { display: inline-flex; align-items: center; gap: 6px; font-size: 10.5px; padding: 2px 8px;
    background: #0c1626; border: 1px solid #24406a; border-radius: 4px; color: #9ab; cursor: pointer; }
  .i9-leader:hover { border-color: #4dd0e1; background: #10203a; }
  .i9-leader .pos { color: #43c463; } .i9-leader .neg { color: #ff5c5c; }
  .i9-cores { display: flex; align-items: flex-end; gap: 2px; height: 22px; }
  .i9-core { width: 7px; height: 100%; background: #0f1a2e; border-radius: 2px; display: flex; align-items: flex-end; overflow: hidden; }
  .i9-core-fill { width: 100%; background: #4d9be6; border-radius: 2px; transition: height 0.5s; }
  .ag-recent { display: flex; flex-wrap: wrap; gap: 4px 8px; font-size: 10px; color: #89a; }
  .ag-runs { display: flex; flex-direction: column; gap: 2px; font-size: 10px; color: #89a; margin-top: 4px; }
  .ag-run { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ag-st.ok { color: #4caf50; } .ag-st.bad { color: #ff5c5c; font-weight: 700; }
  .ag-err { color: #ff8a80; }
  .ag-job { font-family: monospace; white-space: nowrap; }
  .ag-jst { font-weight: 700; }

  .bs-msg { font-size: 12px; color: #666; padding: 20px; text-align: center; }
  .bs-msg.err { color: #f44336; }

  .bs-cols { display: flex; gap: 10px; flex: 1; min-height: 0; }
  .bs-col { flex: 1; display: flex; flex-direction: column; min-width: 0; border: 1px solid #1e1e3a; border-radius: 5px; overflow: hidden; }
  .bs-col-head { font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; padding: 7px 10px; background: #0f0f1e; border-bottom: 1px solid #1e1e3a; flex-shrink: 0; }
  .bs-list { flex: 1; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 7px; }
  .bs-empty { font-size: 11px; color: #555; padding: 16px; text-align: center; font-style: italic; }

  /* catalog card */
  .cat-card { flex-shrink: 0; background: #0f0f1e; border: 1px solid #2d2d4a; border-radius: 4px; padding: 8px 10px; cursor: pointer; }
  .cat-card:hover { border-color: #3d3d5a; }
  .cat-card.sel { border-color: #4caf5066; background: #0c160c; }
  .cc-top { display: flex; justify-content: space-between; align-items: baseline; }
  .cc-name { font-size: 12px; color: #ccc; font-weight: 600; }
  .cc-variants { font-size: 10px; color: #6aa8ff; }
  .cc-metrics { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; font-size: 11px; color: #999; }
  .cc-inst { color: #6aa8ff; font-family: monospace; }
  .cc-dd, .cc-rf { color: #777; font-size: 10px; }
  .cc-params { margin-top: 4px; font-family: monospace; font-size: 10px; color: #666; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cc-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 6px; }
  .cc-run { font-size: 10px; color: #555; }
  .cc-install { padding: 3px 9px; background: #4caf5018; border: 1px solid #4caf5066; color: #4caf50; border-radius: 3px; font-size: 10px; cursor: pointer; white-space: nowrap; }
  .cc-install:hover { background: #4caf5030; }
  .cc-install:disabled { opacity: 0.5; cursor: default; }

  /* card: sweep status + hit-parade */
  /* .bs-list is a flex column → without this, cards compress and clip the progress
     bar / hit-parade. flex-shrink:0 lets each card take its natural height. */
  .cc-sweep { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
  .cc-prog { position: relative; flex: 1; height: 16px; display: flex; align-items: center; background: #15152a; border-radius: 3px; overflow: hidden; }
  .cc-prog-bar { position: absolute; inset: 0 auto 0 0; background: #1f5e3a; }
  .cc-prog-lbl { position: relative; font-size: 10px; color: #cfe; line-height: 1; padding-left: 6px; }
  .cc-mt { font-size: 10px; color: #89a; white-space: nowrap; }
  .cc-top3 { display: flex; flex-wrap: wrap; align-items: baseline; gap: 3px 5px; margin-top: 5px; font-size: 10px; }
  .cc-medal { font-size: 10px; }
  .cc-t3sym { color: #6aa8ff; font-family: monospace; }
  .cc-t3pnl { margin-right: 6px; font-variant-numeric: tabular-nums; }

  /* detail: status block */
  .dp-status { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 18px; padding: 8px 12px;
               background: #0c1322; border-bottom: 1px solid #1a2a44; flex-shrink: 0; }
  .dps-item { display: flex; align-items: center; gap: 6px; }
  .dps-k { font-size: 10px; color: #789; text-transform: uppercase; letter-spacing: .3px; }
  .dps-v { font-size: 12px; color: #cde; }
  .dps-prog { position: relative; width: 200px; height: 16px; display: flex; align-items: center; background: #15152a; border-radius: 3px; overflow: hidden; }
  .dps-prog-bar { position: absolute; inset: 0 auto 0 0; background: #1f5e3a; }
  .dps-prog-lbl { position: relative; font-size: 10px; color: #cfe; line-height: 1; padding-left: 8px; }
  .dps-top3 { display: flex; flex-wrap: wrap; align-items: baseline; gap: 3px 5px; font-size: 12px; }
  .dps-medal { font-size: 12px; }
  .dps-t3sym { color: #6aa8ff; font-family: monospace; }
  .dps-t3pnl { margin-right: 8px; font-variant-numeric: tabular-nums; }

  .dp-inst-best { font-size: 10px; margin-left: auto; font-variant-numeric: tabular-nums; }
  .dp-expand { width: 100%; padding: 5px; background: #0c0c18; border: none; border-top: 1px solid #14142a;
               color: #6aa8ff; font-size: 11px; cursor: pointer; }
  .dp-expand:hover { background: #12122a; color: #9cf; }

  /* installed card */
  .inst-card { flex-shrink: 0; background: #0f0f1e; border: 1px solid #2d2d4a; border-radius: 4px; padding: 8px 10px; }
  .ic-top { display: flex; align-items: center; gap: 7px; }
  .ic-dot { width: 6px; height: 6px; border-radius: 50%; background: #333; flex-shrink: 0; }
  .ic-dot.live { background: #4caf50; box-shadow: 0 0 4px #4caf5088; }
  .ic-name { flex: 1; font-size: 12px; color: #ccc; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ic-badge { font-size: 10px; color: #666; }
  .ic-badge.on { color: #4caf50; }
  .ic-meta { display: flex; gap: 10px; margin-top: 4px; font-size: 10px; color: #888; }
  .ic-inst { color: #6aa8ff; font-family: monospace; }
  .ic-params { margin-top: 4px; font-family: monospace; font-size: 10px; color: #666; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ic-src { margin-top: 2px; font-family: monospace; font-size: 10px; color: #445; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ic-foot { display: flex; justify-content: flex-end; gap: 6px; margin-top: 6px; }
  .ic-undeploy { padding: 3px 9px; background: #1a1a2e; border: 1px solid #4caf5066; color: #4caf50; border-radius: 3px; font-size: 10px; cursor: pointer; }
  .ic-undeploy:hover { background: #1a2a1a; }
  .ic-undeploy:disabled { opacity: 0.5; cursor: default; }
  .ic-remove { padding: 3px 9px; background: #1a0a0a; border: 1px solid #f4433655; color: #f44336; border-radius: 3px; font-size: 10px; cursor: pointer; }
  .ic-remove:hover { background: #2a1010; }
  .ic-remove:disabled { opacity: 0.5; cursor: default; }

  .pos { color: #4caf50; }
  .neg { color: #f44336; }
  .bs-refresh { align-self: flex-start; padding: 5px 14px; background: #1a1a2e; border: 1px solid #2d2d4a; color: #aaa; border-radius: 3px; font-size: 11px; cursor: pointer; flex-shrink: 0; }
  .bs-refresh:hover { color: #4caf50; border-color: #4caf5066; }
  .bs-refresh:disabled { opacity: 0.5; }

  /* detail tab (overlays the two columns) */
  .detail-pane { position: absolute; inset: 0; background: #0a0a15; display: flex; flex-direction: column; z-index: 20; }
  .dp-head { display: flex; align-items: baseline; gap: 12px; padding: 12px; border-bottom: 1px solid #1e1e3a; flex-shrink: 0; flex-wrap: wrap; }
  .dp-back { padding: 4px 12px; background: #1a1a2e; border: 1px solid #2d2d4a; color: #6aa8ff; border-radius: 3px; font-size: 11px; cursor: pointer; }
  .dp-back:hover { border-color: #6aa8ff66; }
  .dp-title { font-size: 14px; color: #4caf50; font-weight: 600; }
  .dp-sub { font-size: 11px; color: #888; }
  .dp-hint { font-size: 11px; color: #6aa8ff; padding: 6px 12px; background: #0c1020; border-bottom: 1px solid #1e1e3a; flex-shrink: 0; }
  .dp-body { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 16px; }

  .dp-spec { flex-shrink: 0; border: 1px solid #24406a; border-radius: 5px; background: #0c1322; overflow: hidden; }
  .dp-spec-title { font-size: 12px; color: #9cf; font-weight: 600; padding: 8px 10px; border-bottom: 1px solid #1a2a44; }
  .dp-spec-why { font-size: 11px; color: #89a; line-height: 1.55; padding: 8px 10px; border-bottom: 1px solid #14223a; }
  .dp-spec-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  .dp-spec-table th { text-align: left; padding: 5px 10px; color: #789; font-weight: 500; background: #0a1120; border-bottom: 1px solid #14223a; }
  .dp-spec-table th.num, .dp-spec-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .dp-spec-table td { padding: 5px 10px; color: #aaa; border-bottom: 1px solid #101a2e; vertical-align: top; }
  .dp-spec-table tr:last-child td { border-bottom: none; }
  .ps-name { color: #cde; white-space: nowrap; }
  .ps-key { font-family: monospace; font-size: 10px; color: #567; margin-left: 6px; }
  .ps-desc { color: #9ab; max-width: 520px; line-height: 1.4; }

  .dp-inst { flex-shrink: 0; border: 1px solid #1e1e3a; border-radius: 5px; overflow: hidden; }
  .dp-inst-head { display: flex; align-items: baseline; gap: 10px; padding: 7px 10px; background: #0f0f1e; border-bottom: 1px solid #1e1e3a; }
  .dp-inst-sym { font-family: monospace; font-size: 13px; color: #6aa8ff; font-weight: 600; }
  .dp-inst-cnt { font-size: 10px; color: #777; }
  .dp-table-wrap { overflow-x: auto; }
  .dp-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  .dp-table th { text-align: right; padding: 6px 10px; color: #888; font-weight: 500; background: #0c0c18; border-bottom: 1px solid #1e1e3a; cursor: pointer; white-space: nowrap; user-select: none; }
  .dp-table th:hover { color: #ccc; }
  .dp-table td { text-align: right; padding: 5px 10px; color: #aaa; border-bottom: 1px solid #14142a; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .dp-row { cursor: pointer; }
  .dp-row:hover td { background: #12122a; }
  .dp-row.cand td { background: #0c160c; }
  .dp-row.cand:hover td { background: #112811; }
  .dp-go { padding: 2px 6px; }
  .dp-go-btn { padding: 3px 8px; background: linear-gradient(180deg,#1c6b3a,#14522c); border: 1px solid #2c8a4e;
    color: #d6ffe4; border-radius: 4px; font-size: 11px; font-weight: 700; cursor: pointer; white-space: nowrap; }
  .dp-go-btn:hover { background: linear-gradient(180deg,#237e46,#186334); }
  .ann { font-weight: 700; font-size: 12px; }
  .ann.pos { color: #00e676; text-shadow: 0 0 6px #00e67644; }
  .ann.neg { color: #ff5252; text-shadow: 0 0 6px #ff525244; }
  .th-hl { color: #00e676; }

  /* backtest window modal */
  .chart-modal { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 40; padding: 24px; }
  .cm-box { width: min(1280px, 96vw); height: min(86vh, 900px); background: #0a0a15; border: 1px solid #2d2d4a; border-radius: 6px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 10px 40px rgba(0,0,0,0.6); }
  .cm-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 12px; border-bottom: 1px solid #1e1e3a; flex-shrink: 0; }
  .cm-title { font-size: 12px; color: #ccc; font-weight: 600; display: flex; align-items: baseline; gap: 8px; min-width: 0; }
  .cm-params { font-family: monospace; font-size: 10px; color: #666; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cm-title-big { font-size: 16px; color: #eaf2ff; font-weight: 700; letter-spacing: .2px; flex-wrap: wrap; }
  .cm-params-big { font-size: 13px; color: #7fd7b0; text-overflow: clip; white-space: normal; }
  .cm-close { width: 26px; height: 26px; background: #1a1a2e; border: 1px solid #2d2d4a; color: #aaa; border-radius: 3px; font-size: 13px; cursor: pointer; flex-shrink: 0; }
  .cm-close:hover { color: #f44336; border-color: #f4433655; }
  .cm-strat { font-size: 14px; color: #eaf2ff; font-weight: 700; }
  .cm-sym { font-family: monospace; font-size: 13px; color: #7fd7b0; }
  .cm-doc { font-size: 11px; color: #6aa8ff; text-decoration: none; }
  .cm-doc:hover { text-decoration: underline; }
  .cm-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  .cm-launch { padding: 6px 14px; background: linear-gradient(180deg,#1c6b3a,#14522c); border: 1px solid #2c8a4e;
    color: #d6ffe4; border-radius: 5px; font-size: 12px; font-weight: 700; cursor: pointer; white-space: nowrap; }
  .cm-launch:hover { background: linear-gradient(180deg,#237e46,#186334); }

  /* Launch-into-trading panel */
  .lp-modal { position: fixed; inset: 0; background: rgba(0,0,0,0.66); display: flex; align-items: center; justify-content: center; z-index: 60; padding: 24px; }
  .lp-box { width: min(560px, 96vw); max-height: 90vh; overflow-y: auto; background: #0e1220; border: 1px solid #2a3552; border-radius: 12px; box-shadow: 0 16px 48px rgba(0,0,0,0.6); }
  .lp-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 18px; border-bottom: 1px solid #1c2540; }
  .lp-h-title { font-size: 15px; font-weight: 700; color: #eaf2ff; }
  .lp-body { padding: 16px 18px; display: flex; flex-direction: column; gap: 14px; }
  .lp-strat { font-size: 17px; font-weight: 700; color: #eaf2ff; }
  .lp-sym { font-family: monospace; font-size: 14px; color: #7fd7b0; margin-left: 6px; }
  .lp-behavior { background: #172136; border-left: 3px solid #2f5c9e; border-radius: 0 8px 8px 0; padding: 11px 14px; }
  .lp-b-label { font-size: 11px; letter-spacing: .6px; text-transform: uppercase; color: #6aa8ff; font-weight: 600; margin-bottom: 5px; }
  .lp-b-text { margin: 0; font-size: 13.5px; line-height: 1.6; color: #e9edf6; }
  .lp-field { display: flex; flex-direction: column; gap: 6px; }
  .lp-k { font-size: 11px; letter-spacing: .5px; text-transform: uppercase; color: #7c88a3; font-weight: 600; }
  .lp-input { background: #0a0f1c; border: 1px solid #2a3552; border-radius: 6px; color: #dfe6f2; font-size: 13px; padding: 8px 10px; }
  .lp-input:focus { outline: none; border-color: #3a7; }
  .lp-modes { display: flex; flex-direction: column; gap: 6px; }
  .lp-mode { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #c5cddd; padding: 9px 12px;
    border: 1px solid #24304c; border-radius: 7px; cursor: pointer; }
  .lp-mode.sel { border-color: #3a7f57; background: #12261a; color: #e6ffee; }
  .lp-mode.off { opacity: .5; cursor: not-allowed; }
  .lp-note { font-size: 12px; color: #c9a24a; background: #221c0c; border-radius: 6px; padding: 8px 11px; }
  .lp-params { display: flex; flex-direction: column; gap: 7px; }
  .lp-k-note { text-transform: none; letter-spacing: 0; color: #66728c; font-weight: 400; }
  .lp-pgrid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px 12px;
    background: #0a0f1c; border: 1px solid #1a2338; border-radius: 8px; padding: 10px 12px; }
  .lp-prow { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
  .lp-plabel { font-size: 12px; color: #9aa6bd; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .lp-pinput { width: 76px; flex-shrink: 0; background: #12182a; border: 1px solid #2a3552; border-radius: 5px;
    color: #e6edf7; font-size: 12.5px; font-family: ui-monospace, monospace; padding: 4px 7px; text-align: right; }
  .lp-pinput:focus { outline: none; border-color: #3a7; }
  .lp-pfix { font-family: ui-monospace, monospace; font-size: 12.5px; color: #7fd7b0; }
  .lp-hint { font-size: 11px; color: #66728c; }
  .lp-warn { font-size: 12.5px; line-height: 1.55; color: #ffcf9e; background: #2a1608; border: 1px solid #7a4a1e; border-radius: 8px; padding: 11px 14px; }
  .lp-msg { font-size: 13px; line-height: 1.55; color: #ff9a8a; background: #1f0f0f; border-radius: 8px; padding: 11px 14px; }
  .lp-msg.ok { color: #9effc0; background: #0c2015; }
  .lp-foot { display: flex; justify-content: flex-end; gap: 10px; padding: 14px 18px; border-top: 1px solid #1c2540; }
  .lp-cancel { padding: 9px 16px; background: #171c2c; border: 1px solid #2a3552; color: #aeb8cc; border-radius: 6px; font-size: 13px; cursor: pointer; }
  .lp-go { padding: 9px 22px; background: linear-gradient(180deg,#1f7a42,#155a30); border: 1px solid #2c8a4e; color: #eafff1; border-radius: 6px; font-size: 14px; font-weight: 700; cursor: pointer; }
  .lp-go:hover { background: linear-gradient(180deg,#238a4b,#186334); }
  .lp-go:disabled { opacity: .6; cursor: default; }
  .cm-body { flex: 1; min-height: 0; position: relative; }
  .cm-status { display: flex; flex-direction: column; gap: 8px; padding: 28px; max-width: 720px; margin: 0 auto; }
  .cm-spin { font-size: 14px; color: #4caf50; font-weight: 600; }
  .cm-line { font-size: 12px; color: #aaa; }
  .cm-line b { color: #cfe; }
  .cm-line.warn { color: #ffb86b; }
  .cm-p { font-family: monospace; font-size: 11px; color: #789; }
  .cm-hint { font-size: 11px; color: #667; line-height: 1.5; margin-top: 4px; }

  .mono { font-family: monospace; }

  /* agent pause/resume buttons */
  .ag-ctrl { display: flex; gap: 2px; margin: 0 4px; }
  .ag-btn { width: 26px; height: 20px; padding: 0; border-radius: 3px; font-size: 10px;
            cursor: pointer; display: flex; align-items: center; justify-content: center;
            border: 1px solid transparent; line-height: 1; }
  .ag-btn.start { background: #1a3a1a; border-color: #2d5a2d; color: #4caf50; }
  .ag-btn.start:hover { background: #254a25; }
  .ag-btn.stop { background: #3a1a1a; border-color: #5a2d2d; color: #f44336; }
  .ag-btn.stop:hover { background: #4a1a1a; }
  .ag-btn.dim { opacity: 0.35; }
  .ag-btn:disabled { opacity: 0.25; cursor: default; }

  /* agent panel: clickable campaign + current target */
  .ag-camp { background: #102038; border: 1px solid #2a4a72; color: #bcd; font-size: 11px;
             padding: 2px 8px; border-radius: 4px; cursor: pointer; }
  .ag-camp:hover { background: #16294a; border-color: #3a5a90; }
  .ag-info { color: #6aa8ff; font-size: 10px; }
  .ag-now { font-size: 11px; color: #ffd27a; white-space: nowrap; }
  .ag-now b { color: #ffe9b0; }
  .ag-right-prog { margin-left: auto; }

  /* catalog card: highlight the robot currently being swept */
  .cat-card.sweeping { border-color: #ffb86b88; box-shadow: 0 0 0 1px #ffb86b44, 0 0 10px #ffb86b22; }
  .cc-sweeping { font-size: 10px; color: #ffb86b; border: 1px solid #ffb86b55; border-radius: 3px;
                 padding: 0 5px; white-space: nowrap; animation: cc-pulse 1.6s ease-in-out infinite; }
  @keyframes cc-pulse { 0%,100% { opacity: 0.55; } 50% { opacity: 1; } }

  .dp-camps { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 4px 0 8px; }
  .camp-list { border: 1px solid #1a2a44; border-radius: 6px; background: #0a1120; padding: 8px 10px; margin-bottom: 8px; }
  .cl-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .cl-title { font-size: 11px; color: #9ab; text-transform: uppercase; letter-spacing: .5px; }
  .cl-csv { margin-left: auto; background: #16162c; border: 1px solid #2d2d4a; color: #cde;
    padding: 3px 10px; border-radius: 4px; cursor: pointer; font-size: 11px; }
  .cl-csv:hover { border-color: #4caf50; }
  .cl-rows { display: flex; flex-direction: column; gap: 2px; max-height: 180px; overflow-y: auto; }
  .cl-row { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; text-align: left;
    background: transparent; border: none; border-top: 1px solid #111a2c; padding: 5px 4px;
    cursor: pointer; font-size: 11px; color: #9ab; width: 100%; }
  .cl-row:hover { background: #0f1830; }
  .cl-id { color: #7ab8ff; font-size: 11px; }
  .cl-meta { color: #789; font-size: 10px; }
  .cmp-click { cursor: pointer; }
  .cmp-click:hover td { background: rgba(122, 184, 255, .08); }
  /* campaign modal */
  .cmp-box { width: min(960px, 96vw); max-height: 90vh; background: #0a0a15; border: 1px solid #2d2d4a;
             border-radius: 6px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 10px 40px rgba(0,0,0,0.6); }
  .cmp-body { padding: 14px 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; }
  .cmp-meta { display: flex; flex-wrap: wrap; gap: 8px 22px; }
  .cmp-meta > div { display: flex; flex-direction: column; }
  .cmp-k { font-size: 10px; color: #678; text-transform: uppercase; letter-spacing: 0.04em; }
  .cmp-v { font-size: 13px; color: #cde; }
  .cmp-method { font-size: 12px; color: #9ab; line-height: 1.5; background: #0c1322; border: 1px solid #1a2a44;
                border-radius: 5px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; }
  .cmp-method b { color: #cde; }
  .cmp-note { color: #789; font-size: 11px; font-style: italic; margin-top: 2px; }
  .cmp-chips { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; font-size: 11px; }
  .cmp-chip { background: #141428; border: 1px solid #2a2a48; border-radius: 3px; padding: 1px 7px; color: #bcd; font-size: 10px; }
  .cmp-heat { display: flex; flex-direction: column; gap: 6px; }
  .cmp-h-title { font-size: 12px; color: #cde; font-weight: 600; }
  .cmp-sub2 { font-size: 10px; color: #678; font-weight: 400; }
  .cmp-canvas { background: #0c0c18; border: 1px solid #1e1e3a; border-radius: 4px; max-width: 100%; }
  .cmp-legend { display: flex; align-items: center; gap: 8px; font-size: 10px; color: #789; }
  .cmp-grad { width: 90px; height: 10px; border-radius: 2px;
              background: linear-gradient(90deg, #0c0c18, rgb(54,220,110)); border: 1px solid #1e1e3a; }
  .cmp-hint2 { margin-left: auto; color: #ffb86b; }
  .cmp-best { display: flex; flex-direction: column; gap: 6px; }
  .cmp-bt { width: 100%; border-collapse: collapse; font-size: 13px; }
  .cmp-bt th { text-align: right; padding: 6px 10px; color: #9ab; font-weight: 600; border-bottom: 1px solid #1e1e3a; }
  .cmp-bt th:first-child, .cmp-bt th:nth-child(2) { text-align: left; }
  .cmp-bt td { text-align: right; padding: 6px 10px; color: #cde; border-bottom: 1px solid #14142a; font-variant-numeric: tabular-nums; }
  .cmp-l { text-align: left !important; color: #cde; }
  .cmp-params-cell { text-align: left !important; color: #bcd; font-size: 12px; max-width: 520px; white-space: normal; word-break: break-word; line-height: 1.5; }
</style>
