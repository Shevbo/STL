<script lang="ts">
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import BacktestChart from './BacktestChart.svelte';
  import { toFills, commissionBreakdown } from '../../lib/lab-analytics';

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
  let dateTo = $state('2026-05-24');
  let engine = $state<'auto' | 'local' | 'remote'>('auto');
  let strategyInfo = $state<any | null>(null);  // popover: show desc for a strategy

  // ── Sweep rounds ───────────────────────────────────────────────────────
  const ROUNDS = [
    { id: 'r0', label: 'R0 Random Explore', desc: 'Случайный поиск по всей сетке', max: 500 },
    { id: 'r1', label: 'R1 RF×Return Refine', desc: 'Уточнение лучших RF×Return моделью', max: 300 },
    { id: 'r2', label: 'R2 RF×Return Refine', desc: 'Финальный отбор по RF×Return', max: 200 },
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

  function profitRF(r: any): number {
    return (r.net_profit ?? r.total_return ?? 0) * (r.recovery_factor ?? 1);
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
      // Merge installed robots as catalog entries too
      const instEntries = installed.map((r: any) => ({
        id: r.id,
        name: r.name,
        description: 'Установленный робот',
        source: 'installed',
        scriptCode: r.script_code,
        params_schema: [],
        robotId: r.id,
        params_json: r.params_json,
      }));
      catalog = [...instEntries, ...botCatalog];
      if (catalog.length && !selectedStrategyId) selectStrategy(catalog[0]);
    } catch { catalog = []; }
  }

  async function loadInstruments() {
    try {
      const r = await fetchWithAuth('/api/v1/forts-instruments');
      instruments = r.ok ? await r.json() : [];
    } catch { instruments = []; }
  }

  // Load full strategy details (params_schema) when a catalog entry is selected
  async function selectStrategy(s: any) {
    selectedStrategyId = s.id;
    paramValues = {};
    sweepRanges = {};
    roundResults = [[], [], []];
    leaderId = null; leaderResult = null; error = '';
    // Load params schema from strategies endpoint or from installed robot
    if (s.source === 'installed') {
      // Build schema from installed robot's params_json keys
      const pj = typeof s.params_json === 'object' ? s.params_json : {};
      const keys = Object.keys(pj);
      s.params_schema = keys.filter(k => k !== 'symbol').map(k => ({
        key: k, label: k, type: typeof pj[k] === 'number' ? 'number' : 'text',
        default: pj[k], min: undefined, max: undefined, desc: '', hint: '',
      }));
      // Add symbol if present
      if (pj.symbol) {
        s.params_schema.unshift({ key: 'symbol', label: 'Инструмент', type: 'text', default: pj.symbol, desc: '', hint: '' });
      }
      for (const p of s.params_schema) {
        paramValues[p.key] = pj[p.key] ?? p.default;
      }
    } else {
      // Load full strategy template
      try {
        const r = await fetchWithAuth('/api/v1/strategies');
        const strats = r.ok ? await r.json() : [];
        const tmpl = strats.find((t: any) => t.id === s.id);
        if (tmpl) {
          s.scriptCode = tmpl.script_code;
          s.description = tmpl.description ?? s.description;
          s.params_schema = tmpl.params_schema ?? [];
          s.source_url = tmpl.source;
          for (const p of s.params_schema) {
            paramValues[p.key] = tmpl.default_params?.[p.key] ?? p.default;
          }
        }
      } catch { /* keep empty */ }
    }
    // Seed sweep ranges for numeric params
    for (const p of s.params_schema ?? []) {
      if (p.type === 'number' && p.key !== 'qty' && p.key !== 'symbol') {
        const d = paramValues[p.key] ?? p.default;
        sweepRanges[p.key] = { from: d, to: d, step: 1 };
      }
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
    const maxC = ROUNDS[ri].max;
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
    if (scriptCode) {
      body.scriptCode = scriptCode;
      body.baseParams = { symbol: sym };
    }
    runPhase = `${ROUNDS[ri].label}: ${paramSets.length} вариантов → отправка…`;
    try {
      const res = await fetchWithAuth('/api/v1/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      runPhase = `${ROUNDS[ri].label}: расчёт ${paramSets.length} вариантов (${data.engine})…`;
      await pollRun(data.run_id, ri, paramSets);
    } catch (e: any) {
      error = String(e);
      running = false;
    }
  }

  async function pollRun(runId: string, ri: number, paramSets: any[]) {
    let attempts = 0;
    polling = setInterval(async () => {
      attempts++;
      try {
        const sr = await fetchWithAuth(`/api/v1/backtest/${runId}/status`);
        if (!sr.ok) return;
        const st = await sr.json();
        const pct = st.progress_pct ?? (st.done ? 100 : Math.min(99, attempts * 3));
        runPhase = `${ROUNDS[ri].label}: ${st.status ?? 'расчёт'} · ${pct}% · попытка ${attempts}`;
        if (st.status === 'done' || st.status === 'failed') {
          clearInterval(polling);
          polling = null;
          // Fetch results
          const rr = await fetchWithAuth(`/api/v1/backtest/${runId}/results`);
          if (rr.ok) {
            const rd = await rr.json();
            // API returns flat array of result rows. Each row has params + metrics + trades.
            const items = (Array.isArray(rd) ? rd : (rd.results ?? rd.combos ?? [])).map((c: any) => ({
              params: c.params ?? {},
              result: c,
            }));
            roundResults[ri] = items;
            // Auto-select leader
            const lb = [...items].sort((a, b) => profitRF(b.result) - profitRF(a.result));
            if (lb.length && profitRF(lb[0].result) > 0) {
              leaderId = JSON.stringify(lb[0].params);
              leaderResult = lb[0];
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
          {:else if strategyInfo.source === 'installed'}
            <span class="btl-info-link">Установленный робот — стратегия загружена из скрипта</span>
          {:else}
            <span class="btl-info-link">Стратегия из библиотеки — без внешнего источника</span>
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
        {#each (selectedStrategy.params_schema ?? []) as p}
          {@const isSymbol = p.key === 'symbol'}
          <div class="btl-pf">
            <div class="btl-pf-head">
              <span class="btl-pf-label">{p.label}</span>
              {#if p.desc || p.hint}
                <span class="btl-pf-i" title={p.desc || p.hint}>ⓘ</span>
              {/if}
            </div>
            {#if p.desc || p.hint}
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
        </div>
        <div class="btl-leader-wrap">
          <table class="btl-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Инстр.</th>
                <th>Параметры</th>
                <th>Чистая прибыль</th>
                <th>Доходность</th>
                <th>Шарп</th>
                <th>RF</th>
                <th>Просадка</th>
                <th>Сделок</th>
                <th>Прибыль×RF</th>
              </tr>
            </thead>
            <tbody>
              {#each lb.slice(0, 50) as row, i}
                {@const r = row.result}
                {@const isLeader = JSON.stringify(row.params) === leaderId}
                <tr class="btl-row" class:btl-leader={isLeader}
                    onclick={() => { leaderId = JSON.stringify(row.params); leaderResult = row; }}>
                  <td class="btl-rank">{i + 1}</td>
                  <td class="btl-sym">{paramValues.symbol ?? r.symbol ?? '—'}</td>
                  <td class="btl-params">{Object.entries(row.params).filter(([k]) => k !== 'symbol').map(([k,v]) => `${k}=${v}`).join(', ')}</td>
                  <td class="btl-num" class:pos={r.net_profit > 0} class:neg={r.net_profit < 0}>{fmtMoney(r.net_profit)}</td>
                  <td class="btl-num" class:pos={r.total_return > 0} class:neg={r.total_return < 0}>{fmtPct(r.total_return)}</td>
                  <td class="btl-num">{fmtD(r.sharpe)}</td>
                  <td class="btl-num">{fmtD(r.recovery_factor)}</td>
                  <td class="btl-num" class:neg={(r.max_drawdown ?? 0) > 0.1}>{fmtPct(r.max_drawdown)}</td>
                  <td class="btl-num">{fmtN(r.total_trades)}</td>
                  <td class="btl-num btl-score" class:pos={profitRF(r) > 0}>{fmtMoney(profitRF(r))}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      </div>
    {/if}

    {#if leaderResult}
      <div class="btl-section">
        <div class="btl-sec-title">📈 Лидер: прибыль×RF</div>
        <div class="btl-chart-wrap">
          <BacktestChart
            result={leaderResult}
            symbol={paramValues.symbol ?? 'RIM6'}
            dateFrom={dateFrom}
            dateTo={dateTo}
          />
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
    width: 380px; flex-shrink: 0; overflow-y: auto; overflow-x: hidden;
    background: #0c0c1a; border-right: 1px solid #1e1e3a;
    display: flex; flex-direction: column; gap: 1px;
    padding-bottom: 20px;
  }
  .btl-section { padding: 12px 14px; border-bottom: 1px solid #15152a; }
  .btl-sec-title {
    font-size: 10px; color: #4caf50; text-transform: uppercase; letter-spacing: 1px;
    margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;
  }
  .btl-sec-sub { font-size: 9px; color: #556; text-transform: none; letter-spacing: 0; }
  .btl-src-link { font-size: 9px; color: #4a6a8a; text-decoration: none; }
  .btl-src-link:hover { color: #6aafff; }

  /* Strategy cards */
  .btl-cat-list { display: flex; flex-direction: column; gap: 4px; max-height: 220px; overflow-y: auto; }
  .btl-cat-card {
    background: #0a0a18; border: 1px solid #1a1a32; border-radius: 4px;
    padding: 8px 10px; cursor: pointer; text-align: left; width: 100%;
    transition: border-color 0.15s, background 0.15s;
    position: relative;
  }
  .btl-cat-card:hover { border-color: #2a3a5a; }
  .btl-cat-card.active { border-color: #4caf5066; background: #0a1a0f; }
  .btl-cat-name { font-size: 12px; color: #ccc; font-weight: 600; display: flex; justify-content: space-between; align-items: center; }
  .btl-cat-actions { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
  .btl-cat-sweep { font-size: 8px; color: #4caf50; background: #4caf5018; padding: 1px 5px; border-radius: 3px; }
  .btl-cat-i { font-size: 10px; color: #4a6a8a; cursor: pointer; padding: 1px 3px; }
  .btl-cat-i:hover { color: #6aafff; }
  .btl-cat-desc { font-size: 9px; color: #667; margin-top: 3px; line-height: 1.3; }
  /* Strategy info box (below list, full-width) */
  .btl-info-box { margin-top: 8px; padding: 12px 14px; background: #0a0f1e; border: 1px solid #2a4a6a; border-radius: 4px; }
  .btl-info-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .btl-info-title { font-size: 12px; color: #4caf50; font-weight: 600; }
  .btl-info-close { background: none; border: none; color: #556; cursor: pointer; font-size: 14px; padding: 0 4px; }
  .btl-info-close:hover { color: #f44336; }
  .btl-info-body { font-size: 10px; color: #aab; line-height: 1.6; white-space: pre-line; }
  .btl-info-link { font-size: 9px; color: #4a6a8a; margin-top: 8px; display: inline-block; text-decoration: none; }
  .btl-info-link:hover { color: #6aafff; }
  .btl-cat-top3 { display: flex; gap: 12px; margin-top: 4px; }
  .btl-t3sym { font-size: 9px; color: #888; }
  .btl-t3pnl { font-size: 9px; font-family: monospace; }
  .btl-t3pnl.pos { color: #4caf50; }
  .btl-t3pnl.neg { color: #f44336; }
  .btl-cat-badge { font-size: 8px; color: #4caf50; background: #4caf5018; padding: 1px 5px; border-radius: 3px; margin-top: 4px; display: inline-block; }

  /* Param fields */
  .btl-pf { margin-bottom: 10px; }
  .btl-pf-head { display: flex; align-items: center; gap: 4px; margin-bottom: 2px; }
  .btl-pf-label { font-size: 11px; color: #aaa; }
  .btl-pf-i { font-size: 10px; color: #4a6a8a; cursor: help; }
  .btl-pf-i:hover { color: #6aafff; }
  .btl-pf-desc { font-size: 9px; color: #7a9abb; line-height: 1.5; margin-bottom: 3px; white-space: pre-line; }

  /* Inputs */
  .btl-inp {
    background: #0a0a18; border: 1px solid #1e1e3a; color: #ccc;
    padding: 4px 6px; font-size: 11px; border-radius: 3px; width: 100%;
  }
  .btl-inp:focus { border-color: #4caf5066; outline: none; }
  .btl-sel { cursor: pointer; }
  .btl-range { display: flex; align-items: center; gap: 3px; }
  .btl-rng { width: 62px; }
  .btl-step { width: 44px; }
  .btl-rng-lbl { font-size: 8px; color: #556; text-transform: uppercase; min-width: 16px; text-align: center; }

  /* Dates */
  .btl-dates { display: flex; align-items: center; gap: 8px; }
  .btl-dates .btl-inp { width: auto; flex: 1; }
  .btl-date-dash { color: #555; }

  /* Engines */
  .btl-engines { display: flex; gap: 4px; }
  .btl-eng {
    flex: 1; padding: 6px 4px; font-size: 10px; cursor: pointer; border-radius: 3px;
    background: #0a0a18; border: 1px solid #1e1e3a; color: #888; text-align: center;
    transition: all 0.15s;
  }
  .btl-eng:hover { border-color: #3a3a5a; color: #aaa; }
  .btl-eng.active { border-color: #4caf5066; color: #4caf50; background: #0a1a0f; }

  /* Actions */
  .btl-actions { padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
  .btl-cc { font-size: 12px; color: #aaa; }
  .btl-cc b { color: #4caf50; font-family: monospace; font-size: 14px; }
  .btl-rounds { display: flex; flex-direction: column; gap: 4px; }
  .btl-rbtn {
    padding: 8px 10px; border-radius: 4px; cursor: pointer; text-align: left; border: 1px solid #1e1e3a;
    background: #0a0a18; transition: all 0.15s; display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px;
  }
  .btl-rbtn:hover:not(:disabled) { border-color: #4caf5066; background: #0a1a0f; }
  .btl-rbtn.current { border-color: #4caf50; background: #0a1a0f; animation: pulse 1.5s infinite; }
  .btl-rbtn.done { border-color: #2a4a2a; opacity: 0.8; }
  .btl-rbtn:disabled { opacity: 0.4; cursor: not-allowed; }
  .btl-rbtn-lbl { font-size: 11px; color: #4caf50; font-weight: 600; }
  .btl-rbtn-desc { font-size: 9px; color: #667; }
  .btl-rbtn-n { font-size: 9px; color: #4caf50; margin-left: auto; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.7} }
  .btl-stop { padding: 6px 12px; background: #2a0a0a; border: 1px solid #f44336; color: #f44336; border-radius: 4px; cursor: pointer; font-size: 11px; }
  .btl-clear { padding: 4px 8px; background: transparent; border: 1px solid #2a2a3a; color: #666; border-radius: 3px; cursor: pointer; font-size: 10px; }

  /* ── Results panel ──────────────────────────────────────────────────── */
  .btl-results { flex: 1; overflow-y: auto; min-width: 0; padding: 12px; display: flex; flex-direction: column; gap: 12px; }
  .btl-progress { margin-bottom: 4px; }
  .btl-prog-bar { height: 3px; background: #1a1a32; border-radius: 2px; overflow: hidden; margin-bottom: 4px; }
  .btl-prog-fill { height: 100%; background: #4caf50; transition: width 0.5s; border-radius: 2px; }
  .btl-prog-text { font-size: 12px; color: #4caf50; font-weight: 600; }
  .btl-error { padding: 8px 12px; background: #1a0808; border: 1px solid #f4433644; color: #f44336; border-radius: 4px; font-size: 11px; }

  /* Leaderboard table */
  .btl-leader-wrap { overflow-x: auto; max-height: 320px; overflow-y: auto; }
  .btl-table { width: 100%; border-collapse: collapse; font-size: 10px; }
  .btl-table th {
    position: sticky; top: 0; background: #0c0c1a; color: #556;
    font-size: 9px; text-transform: uppercase; letter-spacing: 0.5px; padding: 6px 8px;
    text-align: left; border-bottom: 1px solid #1e1e3a;
  }
  .btl-table td { padding: 5px 8px; border-bottom: 1px solid #111128; color: #888; }
  .btl-row { cursor: pointer; transition: background 0.1s; }
  .btl-row:hover { background: #0a0a18; }
  .btl-leader { background: #0a1a0f !important; border-left: 2px solid #4caf50; }
  .btl-rank { color: #556; font-family: monospace; width: 24px; }
  .btl-sym { color: #4caf50; font-family: monospace; font-weight: 600; }
  .btl-params { font-family: monospace; font-size: 9px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .btl-num { font-family: monospace; text-align: right; }
  .btl-num.pos { color: #4caf50; }
  .btl-num.neg { color: #f44336; }
  .btl-score { font-weight: 700; font-size: 11px; }
  .btl-score.pos { color: #4caf50; }

  /* Chart */
  .btl-chart-wrap { height: 360px; }

  /* Empty */
  .btl-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; gap: 12px; color: #445; }
  .btl-empty-icon { font-size: 48px; opacity: 0.4; }
  .btl-empty-text { font-size: 13px; text-align: center; line-height: 1.5; }
</style>
