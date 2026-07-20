<!-- Live Robots tab: roster blotter (sort/filter) + robot dossier (описание / тесты /
     торговля). Metrics (P&L, RF, trades) are computed client-side from the showcase
     fills, exactly like the Showcase/RobotWindow screens. -->
<script lang="ts">
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import { toFills, rolledPnl } from '../../lib/lab-analytics';
  import RobotIdentity from './RobotIdentity.svelte';
  import RobotEditor from './RobotEditor.svelte';
  import RobotWindow from './RobotWindow.svelte';
  import Showcase from './Showcase.svelte';
  import LiveScreen from './LiveScreen.svelte';

  let robots = $state<any[]>([]);          // full robot rows (script_code, version, state)
  let showcase = $state<any[]>([]);        // same robots + fills → client-side metrics
  let strategies = $state<any[]>([]);      // strategy templates (description, schema)
  let selected = $state<any | null>(null);
  let loading = $state(true);
  let mode = $state<'list' | 'edit' | 'new'>('list');
  let windowRobotId = $state<string | null>(null);  // "Открыть стенд" → detail window
  let view = $state<'robots' | 'showcase' | 'live'>('robots');

  const EXECUTED = new Set(['paper', 'filled', 'submitted', 'executed']);

  async function load() {
    loading = true;
    const [rr, sc, st] = await Promise.all([
      fetchWithAuth('/api/v1/robots').then((r) => (r.ok ? r.json() : [])),
      fetchWithAuth('/api/v1/robots/showcase').then((r) => (r.ok ? r.json() : [])),
      strategies.length ? Promise.resolve(strategies)
        : fetchWithAuth('/api/v1/strategies').then((r) => (r.ok ? r.json() : [])),
    ]);
    robots = rr; showcase = sc; strategies = st;
    loading = false;
    if (selected) selected = robots.find((r) => r.id === selected!.id) ?? null;
  }

  // ── Client-side metrics from showcase fills (P&L in ₽, RF, trades, win%) ─────────
  // RF uses the closed-trade equity curve (the app's convention: it ignores open-
  // position drawdown, same as every other screen — keep it consistent).
  function metricsOf(id: string) {
    const sc = showcase.find((s) => s.id === id);
    const empty = { net: 0, trades: 0, winRate: 0, rf: null as number | null, maxDD: 0, position: 0, symbol: '', paper: true, hasData: false };
    if (!sc) return empty;
    const fills = toFills((sc.trades ?? []).filter((t: any) => EXECUTED.has(t.status)));
    const pvMap = sc.point_values && Object.keys(sc.point_values).length ? sc.point_values : (sc.point_value || 1);
    const rolled = rolledPnl(fills, pvMap, false, { bucketSecs: 60 });
    let cum = 0, peak = 0, maxDD = 0, wins = 0, n = 0;
    for (const e of rolled.events) if (e.close) {
      cum += e.close.pnl; peak = Math.max(peak, cum); maxDD = Math.max(maxDD, peak - cum);
      if (e.close.pnl > 0) wins++; n++;
    }
    return { net: rolled.net, trades: n, winRate: n ? wins / n : 0,
      rf: maxDD > 0 ? rolled.net / maxDD : null, maxDD, position: rolled.position,
      symbol: sc.symbol, paper: sc.paper, hasData: n > 0 };
  }
  let metricsById = $derived.by(() => {
    const m = new Map<string, ReturnType<typeof metricsOf>>();
    for (const r of robots) m.set(r.id, metricsOf(r.id));
    return m;
  });

  // ── Roster: search + sort + filter ──────────────────────────────────────────────
  let search = $state('');
  let sortKey = $state<'net' | 'rf' | 'trades' | 'name' | 'symbol'>('net');
  let fltMode = $state<'all' | 'paper' | 'real'>('all');
  let fltStatus = $state<'all' | 'live' | 'off'>('all');
  let fltSym = $state('');

  let instruments = $derived(
    [...new Set(robots.map((r) => metricsById.get(r.id)?.symbol || r.params_json?.symbol || '').filter(Boolean))].sort()
  );
  let roster = $derived.by(() => {
    let arr = robots.map((r) => ({ r, m: metricsById.get(r.id)! }));
    const q = search.trim().toLowerCase();
    if (q) arr = arr.filter(({ r, m }) => (r.name || '').toLowerCase().includes(q) || (m?.symbol || '').toLowerCase().includes(q));
    if (fltSym) arr = arr.filter(({ m }) => m?.symbol === fltSym);
    if (fltMode !== 'all') arr = arr.filter(({ m }) => (fltMode === 'paper' ? m?.paper : !m?.paper));
    if (fltStatus !== 'all') arr = arr.filter(({ r }) => (fltStatus === 'live' ? r.deployed : !r.deployed));
    const cmp: Record<string, (a: any, b: any) => number> = {
      net: (a, b) => (b.m?.net ?? 0) - (a.m?.net ?? 0),
      rf: (a, b) => (b.m?.rf ?? -1e9) - (a.m?.rf ?? -1e9),
      trades: (a, b) => (b.m?.trades ?? 0) - (a.m?.trades ?? 0),
      name: (a, b) => (a.r.name || '').localeCompare(b.r.name || ''),
      symbol: (a, b) => (a.m?.symbol || '').localeCompare(b.m?.symbol || ''),
    };
    return [...arr].sort(cmp[sortKey]);
  });
  let maxAbsNet = $derived(Math.max(1, ...robots.map((r) => Math.abs(metricsById.get(r.id)?.net ?? 0))));

  // ── Strategy template behind the selected robot (matched like the backend) ───────
  function stratIdOf(code: string): string {
    if (!code) return '';
    let m = code.match(/make_on_bar\(\s*['"]([a-z0-9_]+)['"]/);
    if (m) return m[1];
    m = code.match(/trader\.lab\.strategies\.([a-z0-9_]+)\s+import/);
    if (m) return m[1];
    return '';
  }
  let selStratId = $derived(selected ? stratIdOf(selected.script_code) : '');
  let selTemplate = $derived.by(() => {
    if (!selected) return null;
    if (selStratId) { const t = strategies.find((s) => s.id === selStratId); if (t) return t; }
    const code = selected.script_code || '';
    return [...strategies.filter((s) => s.id && code.includes(s.id))].sort((a, b) => b.id.length - a.id.length)[0] ?? null;
  });
  let selMetrics = $derived(selected ? metricsById.get(selected.id) : null);
  let selSymbol = $derived(selMetrics?.symbol || selected?.params_json?.symbol || '');
  let effStratId = $derived(selStratId || selTemplate?.id || '');

  // ── Backtest run history for the selected robot's strategy ───────────────────────
  let btResults = $state<any | null>(null);
  let btLoading = $state(false);
  let btStrat = '';
  $effect(() => {
    const sid = effStratId;
    if (!sid) { btResults = null; btStrat = ''; return; }
    if (sid === btStrat) return;
    btStrat = sid; btLoading = true; btResults = null;
    fetchWithAuth(`/api/v1/botstore/${encodeURIComponent(sid)}/results`)
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => { btResults = j; btLoading = false; })
      .catch(() => { btLoading = false; });
  });
  let btRows = $derived.by(() => {
    const rows = btResults?.rows ?? [];
    const forSym = rows.filter((r: any) => r.symbol === selSymbol);
    const base = forSym.length ? forSym : rows;
    return [...base].sort((a: any, b: any) => (b.net_profit ?? -1e18) - (a.net_profit ?? -1e18)).slice(0, 8);
  });
  let btSymScoped = $derived((btResults?.rows ?? []).some((r: any) => r.symbol === selSymbol));
  let btPeriodDF = $derived(btResults?.period?.date_from ?? null);
  let btPeriodDT = $derived(btResults?.period?.date_to ?? null);

  // ── Actions ──────────────────────────────────────────────────────────────────────
  async function deploy(id: string) {           // start STL paper simulation (scheduler)
    await fetchWithAuth(`/api/v1/robots/${id}/deploy`, { method: 'POST' });
    await load();
  }
  async function undeploy(id: string) {         // stop STL paper simulation
    await fetchWithAuth(`/api/v1/robots/${id}/undeploy`, { method: 'POST' });
    await load();
  }
  async function removeRobot(id: string) {
    if (!confirm('Удалить робота из реестра? Все сделки и метрики будут удалены безвозвратно.')) return;
    await fetchWithAuth(`/api/v1/robots/${id}`, { method: 'DELETE' });
    selected = null;
    await load();
  }
  // Deploy onto the QUIK-side agent as PAPER (real-ready, top "Витрина" table). Same
  // deploy-agent path as the robot window / agent screen — armed to real ONLY from the
  // VDS console. This is NOT the STL paper sim above.
  let agentBusy = $state(false);
  let agentMsg = $state('');
  async function deployAgent() {
    const sid = effStratId;
    if (!selected || !sid) { agentMsg = 'Не удалось определить strategy_id — на агент идут только библиотечные/standalone стратегии.'; return; }
    const p = selected.params_json ?? {};
    const maxPos = Math.max(1, Number(p.avg_max ?? p.qty ?? 1) || 1);
    if (!confirm(`Развернуть «${selected.name}» НА АГЕНТ в PAPER (real-ready)?\n` +
      `Стратегия ${sid} · ${selSymbol}. Появится в Витрине (верхняя таблица «QUIK/АГЕНТ»).\n` +
      `Реал включается ТОЛЬКО с консоли VDS (FLAT + ввод ID).`)) return;
    const newId = `${sid}-${selSymbol}-p${Date.now().toString(36)}`.replace(/[^A-Za-z0-9_-]/g, '');
    agentBusy = true; agentMsg = '';
    try {
      const d = await fetchWithAuth(`/api/v1/quik/robots/${encodeURIComponent(newId)}/deploy-agent`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy_id: sid, params: { ...p, symbol: selSymbol }, symbol: selSymbol,
          schedule: selected.schedule || '09:00-23:55', max_position: maxPos, paper: true }),
      });
      if (!d.ok) throw new Error(`HTTP ${d.status}`);
      agentMsg = `Развёрнут на агенте: «${newId}» — смотри Витрину.`;
    } catch (e) { agentMsg = 'Ошибка деплоя на агент: ' + String(e).slice(0, 80); }
    finally { agentBusy = false; }
  }

  function onSaved(r: any) { mode = 'list'; load(); selected = r; }
  function pick(r: any) { selected = r; mode = 'list'; agentMsg = ''; }

  // ── Resizable roster / dossier splitter ──────────────────────────────────────────
  let rosterW = $state(272);
  let drag: { x: number; w: number } | null = null;
  function startDrag(e: PointerEvent) {
    drag = { x: e.clientX, w: rosterW };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    e.preventDefault();
  }
  function onMove(e: PointerEvent) { if (drag) rosterW = Math.min(560, Math.max(210, drag.w + (e.clientX - drag.x))); }
  function onUp() { drag = null; window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp); }

  const fmtRub = (v: number) => (v > 0 ? '+' : '') + Math.round(v).toLocaleString('ru-RU') + ' ₽';
  const fmtRf = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2));
  const fmtPct = (v: number) => Math.round(v * 100) + '%';
  const fmtDate = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' }) : '—');
  const fmtDT = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—');
  const paramLine = (p: any) => Object.entries(p || {}).filter(([k]) => k !== 'symbol').map(([k, v]) => `${k}=${v}`).join('  ');

  $effect(() => { load(); });
</script>

<div class="live-robots">
  <div class="tab-bar">
    <button class="tab-btn" class:active={view === 'robots'} onclick={() => (view = 'robots')}>Роботы</button>
    <button class="tab-btn showcase-tab" class:active={view === 'showcase'} onclick={() => (view = 'showcase')}>Витрина</button>
    <button class="tab-btn live-tab" class:active={view === 'live'} onclick={() => (view = 'live')}>LIVE</button>
  </div>

  {#if view === 'live'}
    <div class="showcase-wrap"><LiveScreen onOpen={(id) => (windowRobotId = id)} /></div>
  {:else if view === 'showcase'}
    <div class="showcase-wrap"><Showcase /></div>
  {:else}
    <div class="robots-inner">
      <!-- ══ Roster blotter ══ -->
      <aside class="roster" style="width:{rosterW}px">
        <div class="roster-top">
          <div class="roster-head">
            <span class="eyebrow">Роботы · {roster.length}</span>
            <button class="new-btn" title="Создать робота" onclick={() => { selected = null; mode = 'new'; }}>+</button>
          </div>
          <input class="search" placeholder="Поиск: имя, инструмент" bind:value={search} />
          <div class="ctl-row">
            <label class="ctl">
              <span>Сортировка</span>
              <select bind:value={sortKey}>
                <option value="net">P&amp;L ↓</option>
                <option value="rf">RF ↓</option>
                <option value="trades">Сделок ↓</option>
                <option value="name">Имя</option>
                <option value="symbol">Инструмент</option>
              </select>
            </label>
          </div>
          <div class="chips">
            <button class="chip" class:on={fltMode === 'all'} onclick={() => (fltMode = 'all')}>все</button>
            <button class="chip paper" class:on={fltMode === 'paper'} onclick={() => (fltMode = 'paper')}>paper</button>
            <button class="chip real" class:on={fltMode === 'real'} onclick={() => (fltMode = 'real')}>реал</button>
            <span class="chip-sep"></span>
            <button class="chip" class:on={fltStatus === 'all'} onclick={() => (fltStatus = 'all')}>любой</button>
            <button class="chip live" class:on={fltStatus === 'live'} onclick={() => (fltStatus = 'live')}>LIVE</button>
            <button class="chip" class:on={fltStatus === 'off'} onclick={() => (fltStatus = 'off')}>off</button>
          </div>
          {#if instruments.length > 1}
            <div class="chips sym-chips">
              <button class="chip" class:on={fltSym === ''} onclick={() => (fltSym = '')}>инстр: все</button>
              {#each instruments as s}
                <button class="chip sym" class:on={fltSym === s} onclick={() => (fltSym = fltSym === s ? '' : s)}>{s}</button>
              {/each}
            </div>
          {/if}
        </div>

        <div class="roster-rows">
          {#if loading}<div class="muted pad">Загрузка…</div>{/if}
          {#each roster as { r, m } (r.id)}
            <button class="rrow" class:sel={selected?.id === r.id && mode === 'list'} onclick={() => pick(r)} ondblclick={() => (windowRobotId = r.id)}>
              <span class="led" class:live={r.deployed}></span>
              <span class="rrow-main">
                <span class="rrow-top">
                  <RobotIdentity name={r.name} id={r.id} size="chip" />
                  {#if m?.symbol}<span class="tag">{m.symbol}</span>{/if}
                </span>
                <span class="rrow-bar">
                  <span class="bar-track">
                    <span class="bar-fill" class:pos={m?.net >= 0} class:neg={m?.net < 0}
                          style="width:{Math.min(100, (Math.abs(m?.net ?? 0) / maxAbsNet) * 100)}%"></span>
                  </span>
                  <span class="rnet mono" class:pos={m?.net >= 0} class:neg={m?.net < 0}>{m?.hasData ? fmtRub(m.net) : '—'}</span>
                </span>
              </span>
              <span class="rmode" class:real={m && !m.paper}>{m?.paper === false ? 'РЕАЛ' : 'pp'}</span>
            </button>
          {/each}
          {#if !loading && roster.length === 0}<div class="muted pad">Ничего не найдено под фильтр.</div>{/if}
        </div>
      </aside>

      <!-- drag handle -->
      <div class="vsplit" role="separator" aria-orientation="vertical" title="Потяните — ширина списка" onpointerdown={startDrag}></div>

      <!-- ══ Dossier ══ -->
      <section class="dossier">
        {#if mode === 'new'}
          <RobotEditor onSaved={onSaved} onClose={() => (mode = 'list')} />
        {:else if mode === 'edit' && selected}
          <RobotEditor robot={selected} onSaved={onSaved} onClose={() => (mode = 'list')} />
        {:else if selected}
          <!-- Identity header -->
          <header class="dh">
            <div class="dh-id">
              <span class="dh-led" class:live={selected.deployed}></span>
              <div class="dh-titles">
                <h2 class="dh-name"><RobotIdentity name={selected.name} id={selected.id} size="title" /></h2>
                <div class="dh-sub">
                  {#if selTemplate}<span class="dh-strat">{selTemplate.name}</span>{/if}
                  {#if selSymbol}<span class="tag lg">{selSymbol}</span>{/if}
                  <span class="dh-badge" class:real={selMetrics && !selMetrics.paper}>{selMetrics?.paper === false ? 'РЕАЛ' : 'PAPER'}</span>
                  <span class="dh-badge dim" class:on={selected.deployed}>{selected.deployed ? 'LIVE на STL' : 'остановлен'}</span>
                  <span class="dh-badge dim">окно {selected.schedule}</span>
                  <span class="dh-badge dim">v{selected.version ?? 1}</span>
                </div>
              </div>
            </div>
            <div class="dh-stats">
              <div class="stat"><span class="stat-l">P&amp;L (paper·STL)</span><b class="stat-v mono" class:pos={(selMetrics?.net ?? 0) >= 0} class:neg={(selMetrics?.net ?? 0) < 0}>{selMetrics?.hasData ? fmtRub(selMetrics.net) : '—'}</b></div>
              <div class="stat"><span class="stat-l">RF</span><b class="stat-v mono">{fmtRf(selMetrics?.rf)}</b></div>
              <div class="stat"><span class="stat-l">Сделок</span><b class="stat-v mono">{selMetrics?.trades ?? 0}</b></div>
              <div class="stat"><span class="stat-l">Win</span><b class="stat-v mono">{selMetrics?.trades ? fmtPct(selMetrics.winRate) : '—'}</b></div>
              <div class="stat"><span class="stat-l">Позиция</span><b class="stat-v mono">{selMetrics?.position ?? 0}</b></div>
            </div>
          </header>

          <!-- Actions -->
          <div class="actions">
            <button class="abtn edit" onclick={() => (mode = 'edit')} title="Изменить параметры и расписание">✏ Настройки</button>
            {#if selected.deployed}
              <button class="abtn stop" onclick={() => undeploy(selected.id)} title="Остановить симуляцию на STL">⏹ Остановить (STL)</button>
            {:else}
              <button class="abtn go" onclick={() => deploy(selected.id)} title="Запустить бумажную симуляцию в планировщике STL (без QUIK)">▶ Запустить (paper·STL)</button>
            {/if}
            <button class="abtn agent" disabled={agentBusy} onclick={deployAgent} title="Развернуть на QUIK-агент как PAPER — real-ready, армится с консоли VDS">{agentBusy ? '…' : '🛰 На агент (paper)'}</button>
            <button class="abtn stand" onclick={() => (windowRobotId = selected.id)} title="Полный стенд робота: график, сделки, латентность">🖥 Открыть стенд</button>
            <button class="abtn del" onclick={() => removeRobot(selected.id)} title="Удалить робота из реестра">🗑 Удалить</button>
            {#if agentMsg}<span class="amsg">{agentMsg}</span>{/if}
          </div>

          {#if selected.retire_comment}
            <div class="retire"><span class="retire-l">На доработку</span><span class="retire-t">{selected.retire_comment}</span></div>
          {/if}

          <!-- ── 1. ОПИСАНИЕ ── -->
          <div class="sec sec-desc">
            <div class="sec-head"><span class="sec-tick"></span><span class="sec-title">Описание</span><span class="sec-note">что делает робот</span></div>
            {#if selTemplate?.description}
              <p class="desc">{selTemplate.description}</p>
            {:else}
              <p class="desc muted">Описание стратегии не найдено (нестандартный скрипт).</p>
            {/if}
            <div class="params">
              {#each Object.entries(selected.params_json ?? {}) as [k, v]}
                {@const ps = (selTemplate?.params_schema ?? []).find((p: any) => p.key === k)}
                <div class="prow">
                  <span class="pk">{ps?.label ?? k}<span class="pk-key">{k}</span></span>
                  <span class="pv mono">{v}</span>
                  {#if ps?.desc}<span class="pd">{ps.desc}</span>{/if}
                </div>
              {/each}
            </div>
            <details class="script-d">
              <summary>Скрипт стратегии</summary>
              <pre class="script">{selected.script_code}</pre>
            </details>
          </div>

          <!-- ── 2. ТЕСТЫ ── -->
          <div class="sec sec-test">
            <div class="sec-head"><span class="sec-tick"></span><span class="sec-title">Тесты в бэктесте</span>
              <span class="sec-note">где и на чём проверялся</span>
              <a class="sec-link" href="/?lab=botstore" title="Открыть Botstore — полная история прогонов">Botstore ↗</a>
            </div>
            {#if btLoading}
              <div class="muted pad">Загрузка прогонов…</div>
            {:else if btRows.length}
              <div class="test-meta">
                <span class="tm">Период: <b class="mono">{fmtDate(btPeriodDF)} — {fmtDate(btPeriodDT)}</b></span>
                <span class="tm">Прогонов у стратегии: <b class="mono">{btResults?.rows?.length ?? 0}</b></span>
                <span class="tm">{btSymScoped ? `по ${selSymbol}` : 'по всем инструментам (нет прогона под этот контракт)'}</span>
              </div>
              <div class="test-tbl-wrap">
                <table class="test-tbl">
                  <thead><tr><th>Инстр.</th><th class="num">P&amp;L ₽</th><th class="num">RF</th><th class="num">Сделок</th><th class="num">Win</th><th class="num">Просадка</th><th>Параметры</th></tr></thead>
                  <tbody>
                    {#each btRows as row}
                      <tr>
                        <td class="tag-cell">{row.symbol}</td>
                        <td class="num" class:pos={row.net_profit > 0} class:neg={row.net_profit < 0}>{Math.round(row.net_profit ?? 0).toLocaleString('ru-RU')}</td>
                        <td class="num">{fmtRf(row.recovery_factor)}</td>
                        <td class="num">{row.total_trades ?? '—'}</td>
                        <td class="num">{row.win_rate != null ? Math.round(row.win_rate * 100) + '%' : '—'}</td>
                        <td class="num">{row.max_drawdown != null ? Math.round(row.max_drawdown * 100) + '%' : '—'}</td>
                        <td class="pcell mono" title={paramLine(row.params)}>{paramLine(row.params)}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            {:else}
              <p class="muted pad">Прогонов в бэктесте для стратегии «{effStratId || '—'}» не найдено. Запусти сборку в Backtest Lab / Botstore.</p>
            {/if}
          </div>

          <!-- ── 3. ТОРГОВЛЯ ── -->
          <div class="sec sec-trade" class:real={selMetrics && !selMetrics.paper}>
            <div class="sec-head"><span class="sec-tick"></span><span class="sec-title">Торговля</span>
              <span class="sec-note">как запускался и результат</span>
              <button class="sec-link btn" onclick={() => (windowRobotId = selected.id)}>Полный стенд ↗</button>
            </div>
            <div class="trade-grid">
              <div class="tinfo"><span class="ti-l">Режим</span><b class="ti-v" class:real={selMetrics && !selMetrics.paper}>{selMetrics?.paper === false ? 'РЕАЛ (STL)' : 'PAPER (STL-симуляция)'}</b></div>
              <div class="tinfo"><span class="ti-l">Статус</span><b class="ti-v" class:on={selected.deployed}>{selected.deployed ? 'работает' : 'остановлен'}</b></div>
              <div class="tinfo"><span class="ti-l">Запущен</span><b class="ti-v mono">{fmtDT(selected.deployed_at)}</b></div>
              <div class="tinfo"><span class="ti-l">Результат</span><b class="ti-v mono" class:pos={(selMetrics?.net ?? 0) >= 0} class:neg={(selMetrics?.net ?? 0) < 0}>{selMetrics?.hasData ? fmtRub(selMetrics.net) : '—'}</b></div>
              <div class="tinfo"><span class="ti-l">Сделок / Win</span><b class="ti-v mono">{selMetrics?.trades ?? 0} / {selMetrics?.trades ? fmtPct(selMetrics.winRate) : '—'}</b></div>
              <div class="tinfo"><span class="ti-l">Позиция сейчас</span><b class="ti-v mono">{selMetrics?.position ?? 0} к</b></div>
            </div>
            <p class="trade-hint">
              Это бумажная торговля в планировщике STL (для сравнения). Чтобы вести к реалу — «🛰 На агент (paper)»:
              робот встанет в Витрину (верхняя таблица «QUIK/АГЕНТ») и его можно армить в реал с консоли VDS.
            </p>
          </div>
        {:else}
          <div class="empty">
            <div class="empty-icon">🤖</div>
            <div class="empty-text">Выберите робота слева или создайте нового</div>
            <button class="new-btn-big" onclick={() => (mode = 'new')}>Создать робота</button>
          </div>
        {/if}
      </section>
    </div>
  {/if}
</div>

{#if windowRobotId}
  <RobotWindow robotId={windowRobotId} onClose={() => (windowRobotId = null)} />
{/if}

<style>
  /* ── tokens ────────────────────────────────────────────────────────────────────
     data face = monospace (trading-desk vernacular, tabular numerals align);
     prose face = system sans at 13px/1.65 for comfortable description reading. */
  .live-robots {
    --bg: #0a0a12; --panel: #0d0d17; --raise: #12121f;
    --bd: #22223a; --hair: #1a1a2e;
    --ink: #d9d9e6; --ink2: #9a9ab0; --lbl: #61617a;
    --green: #43c463; --cyan: #4dd0e1; --red: #ff5757; --amber: #f5a623;
    --mono: ui-monospace, "Cascadia Code", "JetBrains Mono", Consolas, monospace;
    display: flex; flex-direction: column; height: 100%; overflow: hidden;
    color: var(--ink); font-size: 13px;
  }
  .mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
  .muted { color: var(--ink2); }
  .pad { padding: 12px 14px; font-size: 12px; }

  /* tabs */
  .tab-bar { display: flex; border-bottom: 1px solid var(--bd); flex-shrink: 0; }
  .tab-btn { padding: 7px 15px; font-size: 11px; color: #666; background: none; border: none; border-bottom: 2px solid transparent; cursor: pointer; transition: color .15s; }
  .tab-btn:hover { color: #aaa; }
  .tab-btn.active { color: #ccc; border-bottom-color: var(--green); }
  .showcase-tab.active { border-bottom-color: #00e676; color: #00e676; }
  .live-tab.active { border-bottom-color: var(--red); color: var(--red); }

  .showcase-wrap { flex: 1; min-height: 0; overflow: hidden; }
  .robots-inner { display: flex; flex: 1; min-height: 0; overflow: hidden; }

  /* ── roster ── */
  .roster { display: flex; flex-direction: column; flex-shrink: 0; min-height: 0; background: var(--panel); border-right: 1px solid var(--bd); }
  .roster-top { padding: 10px 10px 8px; border-bottom: 1px solid var(--hair); display: flex; flex-direction: column; gap: 7px; flex-shrink: 0; }
  .roster-head { display: flex; align-items: center; justify-content: space-between; }
  .eyebrow { font-size: 10px; letter-spacing: .8px; text-transform: uppercase; color: var(--lbl); }
  .new-btn { background: #43c46320; border: 1px solid #43c46366; color: var(--green); font-size: 15px; width: 22px; height: 22px; border-radius: 4px; cursor: pointer; line-height: 1; }
  .search { background: var(--bg); border: 1px solid var(--bd); border-radius: 5px; color: var(--ink); font-size: 12px; padding: 5px 8px; outline: none; }
  .search:focus { border-color: #43c46366; }
  .ctl-row { display: flex; gap: 6px; }
  .ctl { display: flex; align-items: center; gap: 6px; font-size: 10px; color: var(--lbl); text-transform: uppercase; letter-spacing: .5px; flex: 1; }
  .ctl select { flex: 1; background: var(--bg); border: 1px solid var(--bd); border-radius: 5px; color: var(--ink); font-size: 12px; padding: 4px 6px; text-transform: none; letter-spacing: 0; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .chip { font-size: 10.5px; padding: 3px 8px; border-radius: 10px; border: 1px solid var(--bd); background: var(--bg); color: var(--ink2); cursor: pointer; }
  .chip:hover { border-color: #3a3a55; color: var(--ink); }
  .chip.on { background: #43c46318; border-color: #43c46377; color: var(--green); }
  .chip.paper.on { background: #f5a62318; border-color: #f5a62377; color: var(--amber); }
  .chip.real.on { background: #ff575718; border-color: #ff575777; color: var(--red); }
  .chip.live.on { background: #43c46318; border-color: #43c46377; color: var(--green); }
  .chip.sym.on { background: #4dd0e118; border-color: #4dd0e177; color: var(--cyan); }
  .chip-sep { width: 1px; height: 14px; background: var(--bd); margin: 0 2px; }
  .sym-chips { max-height: 46px; overflow-y: auto; }

  .roster-rows { flex: 1; overflow-y: auto; min-height: 0; }
  .rrow { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; padding: 8px 10px; background: none; border: none; border-bottom: 1px solid var(--hair); cursor: pointer; color: inherit; }
  .rrow:hover { background: #14141f; }
  .rrow.sel { background: #0e1a10; box-shadow: inset 2px 0 0 var(--green); }
  .led { width: 7px; height: 7px; border-radius: 50%; background: #333; flex-shrink: 0; }
  .led.live { background: var(--green); box-shadow: 0 0 5px #43c46399; }
  .rrow-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
  .rrow-top { display: flex; align-items: center; gap: 6px; min-width: 0; }
  .rname { font-size: 12.5px; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .tag { font-size: 9.5px; font-family: var(--mono); color: var(--cyan); background: #4dd0e114; border: 1px solid #4dd0e133; border-radius: 3px; padding: 1px 4px; flex-shrink: 0; }
  .tag.lg { font-size: 11px; padding: 2px 7px; }
  .rrow-bar { display: flex; align-items: center; gap: 6px; }
  .bar-track { flex: 1; height: 5px; background: #16162a; border-radius: 3px; overflow: hidden; }
  .bar-fill { display: block; height: 100%; border-radius: 3px; }
  .bar-fill.pos { background: linear-gradient(90deg, #2f8f49, var(--green)); }
  .bar-fill.neg { background: linear-gradient(90deg, #a83636, var(--red)); }
  .rnet { font-size: 11px; min-width: 74px; text-align: right; }
  .rnet.pos, .pos { color: var(--green); }
  .rnet.neg, .neg { color: var(--red); }
  .rmode { font-size: 9px; font-family: var(--mono); color: var(--lbl); flex-shrink: 0; width: 26px; text-align: right; }
  .rmode.real { color: var(--red); }

  /* splitter */
  .vsplit { width: 6px; cursor: col-resize; flex-shrink: 0; background: transparent; }
  .vsplit:hover { background: #43c46333; }

  /* ── dossier ── */
  .dossier { flex: 1; min-width: 0; overflow-y: auto; padding: 14px 18px 40px; }

  .dh { display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; flex-wrap: wrap; padding-bottom: 12px; border-bottom: 1px solid var(--hair); }
  .dh-id { display: flex; gap: 10px; align-items: flex-start; min-width: 0; }
  .dh-led { width: 9px; height: 9px; border-radius: 50%; background: #333; margin-top: 5px; flex-shrink: 0; }
  .dh-led.live { background: var(--green); box-shadow: 0 0 6px #43c463aa; }
  .dh-name { margin: 0; font-size: 16px; font-weight: 650; color: var(--ink); line-height: 1.2; }
  .dh-sub { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 6px; }
  .dh-strat { font-size: 11px; color: var(--green); font-weight: 600; }
  .dh-badge { font-size: 9.5px; padding: 2px 7px; border-radius: 3px; letter-spacing: .4px; background: #f5a62316; color: var(--amber); border: 1px solid #f5a62344; }
  .dh-badge.real { background: #ff575716; color: var(--red); border-color: #ff575744; }
  .dh-badge.dim { background: var(--raise); color: var(--ink2); border-color: var(--bd); }
  .dh-badge.dim.on { color: var(--green); border-color: #43c46344; }
  .dh-stats { display: flex; gap: 16px; flex-wrap: wrap; }
  .stat { display: flex; flex-direction: column; gap: 2px; }
  .stat-l { font-size: 9.5px; text-transform: uppercase; letter-spacing: .5px; color: var(--lbl); }
  .stat-v { font-size: 15px; font-weight: 650; color: var(--ink); }

  .actions { display: flex; flex-wrap: wrap; gap: 7px; align-items: center; padding: 12px 0; }
  .abtn { font-size: 11.5px; padding: 5px 11px; border-radius: 5px; cursor: pointer; border: 1px solid; background: var(--raise); border-color: var(--bd); color: var(--ink2); }
  .abtn:hover { color: var(--ink); border-color: #3a3a55; }
  .abtn.go { background: #0d1f10; border-color: #43c46355; color: var(--green); }
  .abtn.stop { background: #1f0d0d; border-color: #ff575755; color: var(--red); }
  .abtn.agent { background: #0d1a1f; border-color: #4dd0e155; color: var(--cyan); }
  .abtn.stand { background: var(--raise); border-color: #3a3a55; color: var(--ink); }
  .abtn.del { border-color: #ff575733; color: #ff575788; }
  .abtn.del:hover { color: var(--red); border-color: #ff575766; }
  .abtn:disabled { opacity: .55; cursor: default; }
  .amsg { font-size: 11px; color: var(--cyan); }

  .retire { display: flex; gap: 10px; align-items: baseline; background: #1a1000; border: 1px solid #f5a62344; border-radius: 5px; padding: 8px 12px; margin-bottom: 12px; }
  .retire-l { font-size: 9.5px; text-transform: uppercase; letter-spacing: .5px; color: var(--amber); flex-shrink: 0; }
  .retire-t { font-size: 12.5px; color: #ffcf8a; line-height: 1.5; }

  /* sections */
  .sec { margin-top: 16px; background: var(--panel); border: 1px solid var(--bd); border-radius: 8px; padding: 13px 15px; }
  .sec-head { display: flex; align-items: center; gap: 9px; margin-bottom: 10px; }
  .sec-tick { width: 3px; height: 15px; border-radius: 2px; background: var(--green); }
  .sec-test .sec-tick { background: var(--cyan); }
  .sec-trade .sec-tick { background: var(--amber); }
  .sec-trade.real .sec-tick { background: var(--red); }
  .sec-title { font-size: 12px; font-weight: 650; text-transform: uppercase; letter-spacing: .6px; color: var(--ink); }
  .sec-note { font-size: 10.5px; color: var(--lbl); }
  .sec-link { margin-left: auto; font-size: 11px; color: var(--cyan); text-decoration: none; border: 1px solid #4dd0e133; padding: 2px 8px; border-radius: 4px; background: none; cursor: pointer; }
  .sec-link:hover { background: #4dd0e114; }

  /* description prose — the comfortable-reading zone */
  .desc { font-size: 13px; line-height: 1.65; color: var(--ink); white-space: pre-wrap; margin: 0 0 12px; max-width: 78ch; }
  .params { display: flex; flex-direction: column; gap: 2px; margin-bottom: 10px; }
  .prow { display: grid; grid-template-columns: minmax(120px, 200px) 90px 1fr; gap: 10px; align-items: baseline; padding: 4px 8px; border-radius: 4px; }
  .prow:nth-child(odd) { background: var(--bg); }
  .pk { font-size: 12px; color: var(--ink); display: flex; flex-direction: column; }
  .pk-key { font-size: 9.5px; color: var(--lbl); font-family: var(--mono); }
  .pv { font-size: 12.5px; color: var(--green); text-align: right; }
  .pd { font-size: 11px; color: var(--ink2); line-height: 1.4; }
  .script-d { margin-top: 4px; }
  .script-d summary { font-size: 10.5px; text-transform: uppercase; letter-spacing: .5px; color: var(--lbl); cursor: pointer; }
  .script { background: #060610; border: 1px solid var(--bd); border-radius: 5px; padding: 11px; font-size: 11px; color: var(--ink2); overflow: auto; line-height: 1.5; white-space: pre; margin-top: 6px; resize: vertical; min-height: 44px; max-height: 320px; }

  /* tests */
  .test-meta { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 9px; }
  .tm { font-size: 11.5px; color: var(--ink2); }
  .tm b { color: var(--cyan); }
  .test-tbl-wrap { overflow-x: auto; }
  .test-tbl { width: 100%; border-collapse: collapse; font-size: 11.5px; }
  .test-tbl th { text-align: left; font-size: 9.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--lbl); padding: 4px 8px; border-bottom: 1px solid var(--bd); font-weight: 500; }
  .test-tbl th.num, .test-tbl td.num { text-align: right; font-family: var(--mono); }
  .test-tbl td { padding: 5px 8px; border-bottom: 1px solid var(--hair); color: var(--ink); }
  .tag-cell { font-family: var(--mono); color: var(--cyan); font-size: 11px; }
  .pcell { color: var(--ink2); font-size: 10.5px; max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* trade */
  .trade-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; }
  .tinfo { background: var(--bg); border: 1px solid var(--hair); border-radius: 5px; padding: 8px 10px; }
  .ti-l { display: block; font-size: 9.5px; text-transform: uppercase; letter-spacing: .5px; color: var(--lbl); margin-bottom: 3px; }
  .ti-v { font-size: 13px; color: var(--ink); }
  .ti-v.on { color: var(--green); }
  .ti-v.real { color: var(--red); }
  .trade-hint { font-size: 11.5px; line-height: 1.55; color: var(--ink2); margin: 10px 0 0; max-width: 82ch; }

  /* empty */
  .empty { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 12px; color: var(--ink2); }
  .empty-icon { font-size: 42px; opacity: .8; }
  .empty-text { font-size: 13px; }
  .new-btn-big { padding: 8px 20px; background: #43c46318; border: 1px solid #43c46366; color: var(--green); cursor: pointer; border-radius: 5px; font-size: 12px; }

  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
