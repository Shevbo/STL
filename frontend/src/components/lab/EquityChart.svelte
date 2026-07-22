<!-- EquityChart — глобальный график доходности алготорговли по журналу algo_trades.
     Накопительный net по каждому роботу (свой цвет), суммарный — жирным, занятое ГО —
     янтарной заливкой по правой оси. Сигнатура: «лента дней» по нижней кромке —
     тепловая полоска дневного net (зелёный/красный), ритм прибыльных и убыточных
     дней виден без чтения осей. compact-режим (стенд робота): без таблицы, ниже. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '../../lib/fetch-auth';
  import { downloadCSV } from '../../lib/csv';
  import 'uplot/dist/uPlot.min.css';

  let { robotId = null, compact = false }: { robotId?: string | null; compact?: boolean } = $props();

  // Категориальная палитра под тёмный фон терминала (#0f0f1e).
  const ROBOT_COLORS = ['#35c9b0', '#e0a53c', '#a684e8', '#5aa2e8',
                        '#e8708a', '#a2c94d', '#4fc3d9', '#d98a4f'];
  const TOTAL_COLOR = '#e8e8f0';
  const GO_COLOR = '#8a6d1f';

  let periodDays = $state(30);
  let granularity = $state<'fill' | 'day'>('fill');
  let modeFilter = $state<'all' | 'real' | 'paper'>(robotId ? 'all' : 'real');
  let report = $state<any>(null);
  let error = $state<string | null>(null);
  let showTable = $state(!compact);

  let chartEl: HTMLDivElement;
  let plot: any = null;
  let UPlotCtor: any = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let ro: ResizeObserver | null = null;

  const fmtRub = (v: number | null | undefined, digits = 0) =>
    v == null ? '—' : (v >= 0 ? '+' : '') + v.toLocaleString('ru-RU', { maximumFractionDigits: digits });
  const fmtNum = (v: number | null | undefined) =>
    v == null ? '—' : v.toLocaleString('ru-RU', { maximumFractionDigits: 0 });

  function robotColor(i: number): string { return ROBOT_COLORS[i % ROBOT_COLORS.length]; }

  async function load() {
    try {
      const params = new URLSearchParams({ days: String(periodDays) });
      if (modeFilter !== 'all') params.set('mode', modeFilter);
      if (robotId) params.set('robot_id', robotId);
      const res = await fetchWithAuth(`/api/v1/quik/algo-report?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      report = await res.json();
      error = null;
      rebuild();
    } catch (e: any) {
      error = e?.message || 'ошибка загрузки';
    }
  }

  // «Лента дней»: draw-hook рисует тепловые ячейки дневного net по нижней кромке.
  const RIBBON_H = 7;
  function ribbonPlugin(dayNets: number[]) {
    const maxAbs = Math.max(1, ...dayNets.map((v) => Math.abs(v)));
    return {
      hooks: {
        draw: (u: any) => {
          const ctx: CanvasRenderingContext2D = u.ctx;
          const xs: number[] = u.data[0];
          if (!xs?.length) return;
          const pr = devicePixelRatio || 1;
          const y0 = u.bbox.top + u.bbox.height - RIBBON_H * pr;
          const half = xs.length > 1
            ? (u.valToPos(xs[1], 'x', true) - u.valToPos(xs[0], 'x', true)) / 2
            : u.bbox.width / 2;
          ctx.save();
          for (let i = 0; i < xs.length; i++) {
            const n = dayNets[i] ?? 0;
            if (n === 0) continue;
            const a = 0.25 + 0.75 * Math.min(1, Math.abs(n) / maxAbs);
            ctx.fillStyle = n > 0 ? `rgba(76,175,80,${a})` : `rgba(244,67,54,${a})`;
            const cx = u.valToPos(xs[i], 'x', true);
            ctx.fillRect(cx - half + pr, y0, half * 2 - pr * 2, RIBBON_H * pr);
          }
          ctx.restore();
        },
      },
    };
  }

  function rebuild() {
    if (!UPlotCtor || !chartEl || !report) return;
    plot?.destroy();
    plot = null;

    const days: any[] = report.days || [];
    if (!days.length) return;

    // «По сделкам»: кривая по тикам сделок (точный момент каждого филла), а не по
    // дневным отсечкам. Требует series_fills от бэкенда; иначе — дневной режим.
    const sf: Record<string, { ts_ms: number; net: number; cum_net: number }[]> =
      report.series_fills || {};
    const fillIds = Object.keys(sf).filter((k) => sf[k]?.length).sort();
    if (granularity === 'fill' && fillIds.length) {
      const tsSet = new Set<number>();
      for (const rid of fillIds) for (const p of sf[rid]) tsSet.add(p.ts_ms);
      const tsList = [...tsSet].sort((a, b) => a - b);
      const xIdx = new Map(tsList.map((t, i) => [t, i]));
      const seriesF: any[] = [{}];
      const dataF: (number | null)[][] = [tsList.map((t) => t / 1000)];
      const perTsNet = new Map<number, number>();
      for (const rid of fillIds)
        for (const p of sf[rid]) perTsNet.set(p.ts_ms, (perTsNet.get(p.ts_ms) || 0) + p.net);
      seriesF.push({
        label: 'Итог ₽', stroke: TOTAL_COLOR, width: 2,
        value: (_u: any, v: number | null) => (v == null ? '—' : fmtRub(v)),
      });
      let cumT = 0;
      dataF.push(tsList.map((t) => {
        cumT += perTsNet.get(t) || 0;
        return Math.round(cumT * 100) / 100;
      }));
      fillIds.forEach((rid, i) => {
        if (fillIds.length === 1 && !compact) return; // единственный робот дублирует итог
        seriesF.push({
          label: report.robots?.find((r: any) => r.robot_id === rid)?.name || rid.slice(0, 14),
          stroke: robotColor(i), width: 1, spanGaps: true,
          value: (_u: any, v: number | null) => (v == null ? '—' : fmtRub(v)),
        });
        const arr: (number | null)[] = new Array(tsList.length).fill(null);
        for (const p of sf[rid]) arr[xIdx.get(p.ts_ms)!] = p.cum_net;
        dataF.push(arr);
      });
      const hF = compact ? 180 : Math.max(220, (chartEl.parentElement?.clientHeight || 320) - 120);
      plot = new UPlotCtor(
        {
          width: chartEl.clientWidth || 600,
          height: hF,
          padding: [8, 8, 2, 0],
          series: seriesF,
          scales: { x: { time: true } },
          axes: [
            { stroke: '#888', grid: { stroke: '#1e1e3a' }, ticks: { stroke: '#2d2d4a' } },
            { stroke: '#888', grid: { stroke: '#1e1e3a' }, ticks: { stroke: '#2d2d4a' },
              size: 56, values: (_u: any, vs: number[]) => vs.map((v) => fmtNum(v)) },
          ],
          legend: { show: !compact },
          cursor: { points: { size: 5 } },
        },
        dataF as any,
        chartEl,
      );
      return;
    }
    const xs = days.map((d) => new Date(d.date + 'T00:00:00+03:00').getTime() / 1000);
    const dayNets = days.map((d) => d.net || 0);
    const dateIdx = new Map(days.map((d, i) => [d.date, i]));

    const robotIds: string[] = Object.keys(report.series || {}).sort();
    const series: any[] = [{}];
    const data: (number | null)[][] = [xs];

    const hasGO = days.some((d) => d.go_rub != null);
    if (hasGO) {
      series.push({
        label: 'ГО ₽', scale: 'go', stroke: GO_COLOR, fill: GO_COLOR + '2e', width: 1,
        value: (_u: any, v: number | null) => (v == null ? '—' : fmtNum(v)),
      });
      data.push(days.map((d) => d.go_rub));
    }
    series.push({
      label: 'Итог ₽', stroke: TOTAL_COLOR, width: 2,
      value: (_u: any, v: number | null) => (v == null ? '—' : fmtRub(v)),
    });
    data.push(days.map((d) => d.cum_net));
    robotIds.forEach((rid, i) => {
      if (robotIds.length === 1 && !compact) return; // единственный робот дублирует итог
      series.push({
        label: report.robots?.find((r: any) => r.robot_id === rid)?.name || rid.slice(0, 14),
        stroke: robotColor(i), width: 1, spanGaps: true,
        value: (_u: any, v: number | null) => (v == null ? '—' : fmtRub(v)),
      });
      const arr: (number | null)[] = new Array(xs.length).fill(null);
      for (const p of report.series[rid]) {
        const idx = dateIdx.get(p.date);
        if (idx != null) arr[idx] = p.cum_net;
      }
      data.push(arr);
    });

    const h = compact ? 180 : Math.max(220, (chartEl.parentElement?.clientHeight || 320) - 120);
    plot = new UPlotCtor(
      {
        width: chartEl.clientWidth || 600,
        height: h,
        padding: [8, 8, RIBBON_H + 2, 0],
        series,
        scales: { x: { time: true }, go: { range: (_u: any, _mn: number, mx: number) => [0, mx * 3 || 1] } },
        axes: [
          { stroke: '#888', grid: { stroke: '#1e1e3a' }, ticks: { stroke: '#2d2d4a' } },
          { stroke: '#888', grid: { stroke: '#1e1e3a' }, ticks: { stroke: '#2d2d4a' },
            size: 56, values: (_u: any, vs: number[]) => vs.map((v) => fmtNum(v)) },
          ...(hasGO ? [{ scale: 'go', side: 1, stroke: GO_COLOR, grid: { show: false },
                         size: 56, values: (_u: any, vs: number[]) => vs.map((v) => fmtNum(v)) }] : []),
        ],
        legend: { show: !compact },
        cursor: { points: { size: 5 } },
        plugins: [ribbonPlugin(dayNets)],
      },
      data as any,
      chartEl,
    );
  }

  function exportCSV() {
    if (!report) return;
    downloadCSV(report.days || [], `algo-report-${periodDays}d.csv`);
  }

  function exportTradesCSV() {
    const params = new URLSearchParams({ format: 'csv', limit: '50000' });
    if (modeFilter !== 'all') params.set('mode', modeFilter);
    if (robotId) params.set('robot_id', robotId);
    window.open(`/api/v1/quik/algo-trades?${params}`, '_blank');
  }

  function setPeriod(d: number) { periodDays = d; load(); }
  function setMode(m: 'all' | 'real' | 'paper') { modeFilter = m; load(); }

  onMount(async () => {
    const mod = await import('uplot');
    UPlotCtor = mod.default;
    await load();
    ro = new ResizeObserver(() => {
      if (plot && chartEl) plot.setSize({ width: chartEl.clientWidth, height: plot.height });
    });
    if (chartEl) ro.observe(chartEl);
    pollTimer = setInterval(load, 60_000);
  });
  onDestroy(() => {
    plot?.destroy();
    if (pollTimer) clearInterval(pollTimer);
    ro?.disconnect();
  });
</script>

<div class="eq-wrap" class:compact>
  <div class="eq-head">
    <div class="eq-kpis">
      <span class="eq-total" class:neg={(report?.total_net ?? 0) < 0}
            title="суммарный net-результат алготорговли за период по журналу сделок (за вычетом комиссий)">
        {fmtRub(report?.total_net)} ₽</span>
      <span class="eq-kpi" title="итоговый net к ПИКОВОМУ занятому ГО периода">
        доходность {report?.return_pct != null ? fmtRub(report.return_pct, 1) + '%' : '—'}</span>
      <span class="eq-kpi" title="пик суммарного занятого ГО (|позиция| × нач. маржа) за период">
        пик ГО {report?.peak_go_rub != null ? fmtNum(report.peak_go_rub) + ' ₽' : '—'}</span>
      <span class="eq-kpi" title="суммарная комиссия за период">
        комиссия {fmtNum(report?.robots?.reduce((s: number, r: any) => s + (r.commission || 0), 0))} ₽</span>
      <span class="eq-kpi">
        сделок {fmtNum(report?.days?.reduce((s: number, d: any) => s + (d.trades || 0), 0))}</span>
      {#if report && !report.margin_known}
        <span class="eq-kpi warn" title="агент ещё не передаёт начальную маржу (BUYDEPO) — ГО и доходность появятся после обновления агента">ГО: нет данных</span>
      {/if}
    </div>
    <div class="eq-controls">
      {#each [[7, '7д'], [30, '30д'], [90, '90д'], [365, 'год']] as [d, label]}
        <button class:on={periodDays === d} onclick={() => setPeriod(d as number)}>{label}</button>
      {/each}
      {#if !robotId}
        <span class="sep"></span>
        {#each [['real', 'реал'], ['paper', 'бумага'], ['all', 'все']] as [m, label]}
          <button class:on={modeFilter === m} onclick={() => setMode(m as any)}>{label}</button>
        {/each}
      {/if}
      <span class="sep"></span>
      <button class:on={granularity === 'fill'} title="кривая по тикам сделок"
              onclick={() => { granularity = 'fill'; rebuild(); }}>по сделкам</button>
      <button class:on={granularity === 'day'} title="кривая по дням + лента дневного net и ГО"
              onclick={() => { granularity = 'day'; rebuild(); }}>по дням</button>
      <span class="sep"></span>
      <button onclick={exportCSV} title="выгрузить дневной отчёт в CSV">CSV отчёт</button>
      <button onclick={exportTradesCSV} title="выгрузить полный журнал сделок в CSV">CSV журнал</button>
    </div>
  </div>

  {#if error}
    <div class="eq-empty">Журнал недоступен: {error}</div>
  {:else if report && !(report.days || []).length}
    <div class="eq-empty">Журнал пока пуст — сделки появятся здесь по мере торговли роботов
      (учёт начат {new Date().toLocaleDateString('ru-RU')}).</div>
  {/if}
  <div class="eq-chart" bind:this={chartEl}></div>

  {#if !compact && report?.robots?.length}
    <details class="eq-table" bind:open={showTable}>
      <summary>по роботам ({report.robots.length})</summary>
      <table>
        <thead><tr>
          <th></th><th>Робот</th><th>Режим</th><th>Инстр.</th><th>Сделок</th>
          <th>Контр.</th><th>Gross ₽</th><th>Комиссия ₽</th><th>Net ₽</th>
          <th title="пик занятого ГО за период">Пик ГО ₽</th>
          <th title="net / пик ГО">Доходн. %</th>
          <th title="фактический RF по журналу сделок: net / макс. реализованная просадка cum-net за период (просадка открытой позиции не учтена)">RF</th>
        </tr></thead>
        <tbody>
          {#each Object.keys(report.series || {}).sort() as rid, i}
            {@const r = report.robots.find((x: any) => x.robot_id === rid)}
            {#if r}
              <tr>
                <td><span class="swatch" style="background:{robotColor(i)}"></span></td>
                <td class="mono">{r.name || rid}</td>
                <td>{r.mode === 'real' ? 'реал' : r.mode === 'paper' ? 'бумага' : '—'}</td>
                <td class="mono">{r.symbol || '—'}</td>
                <td class="num">{fmtNum(r.trades)}</td>
                <td class="num">{fmtNum(r.contracts)}</td>
                <td class="num" class:neg={r.gross < 0}>{fmtRub(r.gross)}</td>
                <td class="num comm">−{fmtNum(r.commission)}</td>
                <td class="num" class:neg={r.net < 0}><b>{fmtRub(r.net)}</b></td>
                <td class="num">{r.peak_go_rub != null ? fmtNum(r.peak_go_rub) : '—'}</td>
                <td class="num" class:neg={(r.return_pct ?? 0) < 0}>
                  {r.return_pct != null ? fmtRub(r.return_pct, 1) + '%' : '—'}</td>
                <td class="num" class:neg={(r.rf ?? 0) < 0}
                    title={r.max_dd_rub != null ? 'макс. просадка ' + fmtNum(r.max_dd_rub) + ' ₽' : ''}>
                  {r.rf != null ? r.rf : '—'}</td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </details>
  {/if}
</div>

<style>
  .eq-wrap { display: flex; flex-direction: column; height: 100%; overflow: auto;
    background: #0f0f1e; color: #ccc; font-size: 11px; padding: 6px 8px; gap: 4px; }
  .eq-wrap.compact { padding: 4px; }
  .eq-head { display: flex; justify-content: space-between; align-items: baseline;
    flex-wrap: wrap; gap: 4px 12px; }
  .eq-kpis { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
  .eq-total { font-size: 18px; font-weight: 700; color: #4caf50;
    font-variant-numeric: tabular-nums; }
  .eq-total.neg { color: #f44336; }
  .compact .eq-total { font-size: 14px; }
  .eq-kpi { font-size: 10px; color: #9aa0b4; white-space: nowrap; }
  .eq-kpi.warn { color: #e0a53c; }
  .eq-controls { display: flex; align-items: center; gap: 3px; }
  .eq-controls button { font-size: 10px; padding: 2px 7px; background: #1a1a2e;
    color: #9aa0b4; border: 1px solid #2d2d4a; border-radius: 3px; cursor: pointer; }
  .eq-controls button:hover { color: #e8e8f0; }
  .eq-controls button.on { background: #2d2d4a; color: #e8e8f0; }
  .eq-controls .sep { width: 6px; }
  .eq-chart { flex: 1; min-height: 120px; }
  .eq-empty { color: #9aa0b4; font-size: 11px; padding: 12px 4px; }
  .eq-table summary { cursor: pointer; font-size: 10px; color: #9aa0b4; padding: 2px 0; }
  .eq-table table { width: 100%; border-collapse: collapse; font-size: 10px; }
  .eq-table th { text-align: right; color: #9aa0b4; font-weight: 400; padding: 2px 6px;
    border-bottom: 1px solid #2d2d4a; white-space: nowrap; }
  .eq-table th:nth-child(-n+4) { text-align: left; }
  .eq-table td { padding: 2px 6px; border-bottom: 1px solid #1e1e3a; white-space: nowrap; }
  .eq-table td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .eq-table td.neg { color: #f44336; }
  .eq-table td.comm { color: #7a8194; }
  .eq-table td.mono { font-family: monospace; }
  .swatch { display: inline-block; width: 8px; height: 8px; border-radius: 2px; }
  /* uPlot legend в терминальной стилистике */
  .eq-wrap :global(.u-legend) { font-size: 10px; color: #9aa0b4; }
  .eq-wrap :global(.u-legend .u-value) { font-variant-numeric: tabular-nums; }
</style>
