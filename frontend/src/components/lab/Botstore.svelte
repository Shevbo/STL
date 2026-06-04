<!-- Botstore.svelte — two panels:
     LEFT  = tested catalog (robots found/optimized in background campaigns)
     RIGHT = robots installed on the platform (real DB robots)
     Middle action: "установить на платформу" (create a robot from the template +
     best params). Installed robots have "удалить с платформы". -->
<script lang="ts">
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import BacktestChart from './BacktestChart.svelte';

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
  const fmtNum = (v: any, d = 2) => v != null ? Number(v).toFixed(d) : '—';
  const fmtDate = (v: any) => v ? new Date(v).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

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
    if (!tmpl) { notice = `Нет шаблона стратегии для ${robot.name}`; return; }
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

  function symbolOf(r: any) {
    const pj = r.params_json;
    return (typeof pj === 'object' ? pj?.symbol : null) ?? '';
  }

  // ── Detail tab: all tested instruments × params for one strategy ──────────────
  let detail = $state<any | null>(null);          // {id, name, rows, period, schema}
  let detailLoading = $state(false);
  let sortBy = $state<{ col: string; dir: 1 | -1 }>({ col: 'score', dir: -1 });

  function tmplOf(id: string) { return strategies.find(s => s.id === id); }

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
      };
      sortBy = { col: 'score', dir: -1 };
    } catch (e) {
      notice = 'Ошибка загрузки деталей: ' + String(e);
    }
    detailLoading = false;
  }
  function closeDetail() { detail = null; }

  // rows grouped by instrument, each group sorted by the active column
  let bySymbol = $derived.by(() => {
    const m: Record<string, any[]> = {};
    for (const r of (detail?.rows ?? [])) (m[r.symbol] ??= []).push(r);
    return m;
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

  const toISO = (d: Date) => d.toISOString();
  function yesterday() { const d = new Date(); d.setDate(d.getDate() - 1); return d; }
  function daysAgo(n: number) { const d = new Date(); d.setDate(d.getDate() - n); return d; }

  async function openChart(symbol: string, params: any) {
    if (!detail) return;
    chartErr = ''; chartLoading = true; chart = null;
    try {
      const tmpl = tmplOf(detail.id);
      if (!tmpl) throw new Error('Нет шаблона стратегии для прогона');
      // Test window = campaign start … YESTERDAY (always pull fresh data to date).
      const dateFrom = detail.period?.date_from ?? toISO(daysAgo(95));
      const dateTo = toISO(yesterday());
      const res = await fetchWithAuth('/api/v1/backtest/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scriptCode: tmpl.script_code,
          baseParams: { ...params, symbol },
          symbol, paramsGrid: {}, engine: 'local',
          dateFrom, dateTo,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const { run_id } = await res.json();
      let result: any = null;
      for (let i = 0; i < 150; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const sr = await fetchWithAuth(`/api/v1/backtest/${run_id}/status`);
        const sd = await sr.json();
        if (sd.status === 'done') {
          const rr = await fetchWithAuth(`/api/v1/backtest/${run_id}/results`);
          const rows = rr.ok ? await rr.json() : [];
          result = rows[0] ?? null;
          break;
        }
        if (sd.status === 'failed') throw new Error(sd.error_msg || 'Бэктест завершился ошибкой');
      }
      if (!result) throw new Error('Бэктест не вернул результат (таймаут)');
      // point_value → ruble PnL on the chart. Fetched AFTER the run, which populates
      // instrument_meta (via ISS spec), so it reliably has the value.
      let pv = 1;
      try {
        const mr = await fetchWithAuth(`/api/v1/instruments/${encodeURIComponent(symbol)}/meta`);
        if (mr.ok) { const m = await mr.json(); pv = m?.point_value || 1; }
      } catch { /* default 1 */ }
      chart = { symbol, params, result, pointValue: pv, dateFrom, dateTo };
    } catch (e) {
      chartErr = String(e);
    }
    chartLoading = false;
  }
  function closeChart() { chart = null; chartErr = ''; }

  $effect(() => { load(); });
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
                 role="button" tabindex="0" title="Открыть детали тестирования"
                 onclick={() => { selectedCat = robot.id; openDetail(robot); }}
                 onkeydown={(e) => e.key === 'Enter' && (selectedCat = robot.id, openDetail(robot))}>
              <div class="cc-top">
                <span class="cc-name">{robot.name}</span>
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
              <div class="cc-foot">
                <span class="cc-run">прогон {fmtDate(robot.last_run)}</span>
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
                <span class="ic-name">{r.name}</span>
                <span class="ic-badge" class:on={r.deployed}>{r.deployed ? 'LIVE' : 'остановлен'}</span>
              </div>
              <div class="ic-meta">
                <span class="ic-inst">{symbolOf(r)}</span>
                <span class="ic-sched">окно {r.schedule}</span>
              </div>
              <div class="ic-params">{JSON.stringify(r.params_json)}</div>
              <div class="ic-foot">
                <button class="ic-remove" disabled={busy} onclick={() => remove(r)}>Удалить с платформы</button>
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
        <span class="dp-title">{detail.name}</span>
        <span class="dp-sub">
          инструментов: {Object.keys(bySymbol).length} · вариантов: {detail.rows.length}
          {#if detail.period}· период {fmtDate(detail.period.date_from)} — {fmtDate(detail.period.date_to)}{/if}
        </span>
      </div>
      <div class="dp-hint">Клик по строке — открыть бэктест на этом инструменте с этими параметрами (данные догружаются по вчерашний день).</div>
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
          {#each Object.entries(bySymbol) as [sym, rows]}
            <div class="dp-inst">
              <div class="dp-inst-head">
                <span class="dp-inst-sym">{sym}</span>
                <span class="dp-inst-cnt">{rows.length} вар.</span>
              </div>
              <div class="dp-table-wrap">
                <table class="dp-table">
                  <thead>
                    <tr>
                      {#each paramCols as col}
                        <th class="num" title={descOf(col)} onclick={() => setSort(col)}>{labelOf(col)}{sortBy.col === col ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      {/each}
                      <th class="num" onclick={() => setSort('total_return')}>Доходность{sortBy.col === 'total_return' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('net_profit')}>Прибыль ₽{sortBy.col === 'net_profit' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('max_drawdown')}>Просадка{sortBy.col === 'max_drawdown' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('sharpe')}>Sharpe{sortBy.col === 'sharpe' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('recovery_factor')}>RF{sortBy.col === 'recovery_factor' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('total_trades')}>Сделок{sortBy.col === 'total_trades' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                      <th class="num" onclick={() => setSort('win_rate')}>Win%{sortBy.col === 'win_rate' ? (sortBy.dir === 1 ? ' ▲' : ' ▼') : ''}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each sortRows(rows) as r}
                      <tr class="dp-row" class:cand={r.candidate}
                          onclick={() => openChart(sym, r.params)} title="Открыть бэктест">
                        {#each paramCols as col}
                          <td class="num">{fmtNum(r.params?.[col], 0)}</td>
                        {/each}
                        <td class="num" class:pos={r.total_return > 0} class:neg={r.total_return < 0}>{fmtPct(r.total_return)}</td>
                        <td class="num" class:pos={r.net_profit > 0} class:neg={r.net_profit < 0}>{fmtMoney(r.net_profit)}</td>
                        <td class="num">{fmtPct(r.max_drawdown)}</td>
                        <td class="num">{fmtNum(r.sharpe)}</td>
                        <td class="num">{fmtNum(r.recovery_factor)}</td>
                        <td class="num">{r.total_trades ?? '—'}</td>
                        <td class="num">{fmtPct(r.win_rate)}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
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
            Бэктест · {chart?.symbol ?? ''}
            {#if chart}<span class="cm-params">{JSON.stringify(chart.params)}</span>{/if}
          </span>
          <button class="cm-close" onclick={closeChart}>✕</button>
        </div>
        <div class="cm-body">
          {#if chartLoading}
            <div class="bs-msg">Прогоняю бэктест (данные по вчерашний день)…</div>
          {:else if chartErr}
            <div class="bs-msg err">{chartErr}</div>
          {:else if chart}
            <BacktestChart
              result={chart.result}
              symbol={chart.symbol}
              dateFrom={chart.dateFrom}
              dateTo={chart.dateTo}
              pointValue={chart.pointValue}
              defaultInterval={60}
              taker={true}
            />
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

  .bs-msg { font-size: 12px; color: #666; padding: 20px; text-align: center; }
  .bs-msg.err { color: #f44336; }

  .bs-cols { display: flex; gap: 10px; flex: 1; min-height: 0; }
  .bs-col { flex: 1; display: flex; flex-direction: column; min-width: 0; border: 1px solid #1e1e3a; border-radius: 5px; overflow: hidden; }
  .bs-col-head { font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; padding: 7px 10px; background: #0f0f1e; border-bottom: 1px solid #1e1e3a; flex-shrink: 0; }
  .bs-list { flex: 1; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 7px; }
  .bs-empty { font-size: 11px; color: #555; padding: 16px; text-align: center; font-style: italic; }

  /* catalog card */
  .cat-card { background: #0f0f1e; border: 1px solid #2d2d4a; border-radius: 4px; padding: 8px 10px; cursor: pointer; }
  .cat-card:hover { border-color: #3d3d5a; }
  .cat-card.sel { border-color: #4caf5066; background: #0c160c; }
  .cc-top { display: flex; justify-content: space-between; align-items: baseline; }
  .cc-name { font-size: 12px; color: #ccc; font-weight: 600; }
  .cc-variants { font-size: 10px; color: #6aa8ff; }
  .cc-metrics { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; font-size: 11px; color: #999; }
  .cc-inst { color: #6aa8ff; font-family: monospace; }
  .cc-dd, .cc-rf { color: #777; font-size: 10px; }
  .cc-params { margin-top: 4px; font-family: monospace; font-size: 9px; color: #666; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cc-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 6px; }
  .cc-run { font-size: 9px; color: #555; }
  .cc-install { padding: 3px 9px; background: #4caf5018; border: 1px solid #4caf5066; color: #4caf50; border-radius: 3px; font-size: 10px; cursor: pointer; white-space: nowrap; }
  .cc-install:hover { background: #4caf5030; }
  .cc-install:disabled { opacity: 0.5; cursor: default; }

  /* installed card */
  .inst-card { background: #0f0f1e; border: 1px solid #2d2d4a; border-radius: 4px; padding: 8px 10px; }
  .ic-top { display: flex; align-items: center; gap: 7px; }
  .ic-dot { width: 6px; height: 6px; border-radius: 50%; background: #333; flex-shrink: 0; }
  .ic-dot.live { background: #4caf50; box-shadow: 0 0 4px #4caf5088; }
  .ic-name { flex: 1; font-size: 12px; color: #ccc; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ic-badge { font-size: 9px; color: #666; }
  .ic-badge.on { color: #4caf50; }
  .ic-meta { display: flex; gap: 10px; margin-top: 4px; font-size: 10px; color: #888; }
  .ic-inst { color: #6aa8ff; font-family: monospace; }
  .ic-params { margin-top: 4px; font-family: monospace; font-size: 9px; color: #666; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ic-foot { display: flex; justify-content: flex-end; margin-top: 6px; }
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
  .ps-key { font-family: monospace; font-size: 9px; color: #567; margin-left: 6px; }
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

  /* backtest window modal */
  .chart-modal { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 40; padding: 24px; }
  .cm-box { width: min(1280px, 96vw); height: min(86vh, 900px); background: #0a0a15; border: 1px solid #2d2d4a; border-radius: 6px; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 10px 40px rgba(0,0,0,0.6); }
  .cm-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 12px; border-bottom: 1px solid #1e1e3a; flex-shrink: 0; }
  .cm-title { font-size: 12px; color: #ccc; font-weight: 600; display: flex; align-items: baseline; gap: 8px; min-width: 0; }
  .cm-params { font-family: monospace; font-size: 10px; color: #666; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cm-close { width: 26px; height: 26px; background: #1a1a2e; border: 1px solid #2d2d4a; color: #aaa; border-radius: 3px; font-size: 13px; cursor: pointer; flex-shrink: 0; }
  .cm-close:hover { color: #f44336; border-color: #f4433655; }
  .cm-body { flex: 1; min-height: 0; position: relative; }
</style>
