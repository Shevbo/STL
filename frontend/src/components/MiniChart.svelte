<!-- frontend/src/components/MiniChart.svelte
  Compact single-instrument candlestick used by ChartsGrid to show every instrument
  that is in a position or has working orders, all in one frame. History via the proven
  REST path (/api/v1/chart/bars); live last-price nudges the last candle from the quote
  store. No controls, no tick strip — just a small chart + a header chip. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import { quotesStore } from '$lib/stores/quotes.svelte';
  import { smartOrdersStore } from '$lib/stores/smart-orders.svelte';
  import { KIND_BY_ID, LABEL_TEXT_COLOR, shortCodes, smartLegend, smartLevels, softColor } from '$lib/smart-order-help';
  import { mskTickFormatter, mskCrosshairFormatter } from '$lib/chart-time';

  let {
    symbol,
    label = '',
    badge = '',
    badgeKind = 'neutral',
    tf = 5,
  }: {
    symbol: string;
    label?: string;
    badge?: string;
    badgeKind?: 'long' | 'short' | 'neutral';
    tf?: number;
  } = $props();

  const TF_NAMES: Record<number, string> = {
    1: 'TIME_FRAME_M1', 5: 'TIME_FRAME_M5', 15: 'TIME_FRAME_M15',
    11: 'TIME_FRAME_M15', 12: 'TIME_FRAME_M15', 19: 'TIME_FRAME_D',
  };

  let el: HTMLDivElement;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let chart: any = null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let series: any = null;
  let ready = $state(false);
  let loading = $state(true);   // true until the first bars are drawn (shows an overlay)
  let failed = $state(false);   // fetch returned nothing → show a retry hint
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let bars: any[] = [];
  let lastClose = $state<number | null>(null);

  let quote = $derived(quotesStore.get(symbol));

  // Умные заявки этого инструмента. Мини-график их не знал вовсе, и фрейм
  // «Позиции и заявки» показывал пустоту при семи взведённых заявках
  // (09.08.2026). Уровни и подписи считает ОБЩАЯ smartLevels — та же, что у
  // большого графика: две копии означали бы две правды об одних деньгах.
  const code = $derived((symbol || '').split('@')[0]);
  const smartHere = $derived(smartOrdersStore.armed.filter((o) => o.code === code));
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const smartLines = new Map<string, any>();
  // Легенда показывает ТОЛЬКО нарисованные сейчас типы: постоянный список
  // стилей превращается в шум, который перестают читать.
  const legend = $derived(smartLegend(smartHere));

  async function loadHistory(attempt = 0) {
    loading = true; failed = false;
    try {
      const tfName = TF_NAMES[tf] ?? 'TIME_FRAME_M5';
      const r = await fetchWithAuth(
        `/api/v1/chart/bars?symbol=${encodeURIComponent(symbol)}&tf=${tfName}`,
      );
      const data = r.ok ? await r.json() : null;
      if (!Array.isArray(data) || !data.length) {
        // No data yet (slow Finam / transient): retry a couple of times, then show a hint.
        if (attempt < 3) { setTimeout(() => loadHistory(attempt + 1), 1500); return; }
        loading = false; failed = true; return;
      }
      bars = data.map((b: Record<string, number>) => ({
        time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
      }));
      if (series) {
        series.setData(bars);
        chart.timeScale().fitContent();
        lastClose = bars[bars.length - 1].close;
      }
      loading = false; failed = false;
    } catch {
      if (attempt < 3) { setTimeout(() => loadHistory(attempt + 1), 1500); return; }
      loading = false; failed = true;
    }
  }

  // nudge the last candle's close from the live quote (same as the main chart)
  $effect(() => {
    // Котировку читаем ДО выхода: с `!series` первым эффект на старте уходил
    // без зависимостей и последняя свеча никогда не двигалась вживую.
    const q = quote;
    if (!series || !q || !bars.length) return;
    const price = q.last || q.bid || 0;
    if (!price) return;
    const last = bars[bars.length - 1];
    series.update({
      time: last.time, open: last.open,
      high: Math.max(last.high, price), low: Math.min(last.low, price), close: price,
    });
    lastClose = price;
  });

  // Линии умных заявок. Двигаем существующую через applyOptions, а не
  // пересоздаём: у следящей уровень ползёт за пиком каждую секунду, и
  // пересоздание давало бы мигание.
  $effect(() => {
    // Книгу читаем ДО любого выхода. $effect подписывается только на то, что
    // РЕАЛЬНО прочитал за прогон: с `if (!series) return` первым, на старте
    // (series ещё null, график создаётся после await) эффект выходил, не
    // коснувшись smartHere, оставался без зависимостей и не запускался больше
    // никогда — заявки на графике не появлялись вовсе (09.08.2026).
    const orders = smartHere;
    if (!ready || !series) return;
    const want = new Map<string, { price: number; title: string; color: string; style: number; dim?: boolean }>();
    // Двузначный код связки в начале подписи: so_id в метку не влезает, а без
    // него заявки на графике неразличимы. У защитных детей код РОДИТЕЛЯ —
    // «47» на входе и «47» на его стопе читаются как одна связка.
    const codes = shortCodes(orders);
    for (const o of orders) {
      const m = KIND_BY_ID[o.kind];
      const tag = codes[o.so_id] ? codes[o.so_id] + ' ' : '';
      for (const lv of smartLevels(o)) {
        if (lv.price > 0) {
          want.set(lv.key, { ...lv, title: tag + lv.title,
                             color: lv.color ?? m.color, style: m.lineStyle });
        }
      }
    }
    for (const [key, line] of smartLines) {
      if (!want.has(key)) { series.removePriceLine(line); smartLines.delete(key); }
    }
    for (const [key, lv] of want) {
      const opts = {
        // Полупрозрачны ТРИ вещи, а не одна: сама линия, ПЛАШКА бирки на шкале
        // и ТЕКСТ в ней. Прозрачной линии мало — глухая бирка закрывала колонку
        // цен и время под ней (оператор, 09.08.2026).
        price: lv.price,
        // Линия НАСЫЩЕННАЯ: прятать её незачем, плашка теперь прозрачна сама
        // по себе. Вспомогательные чуть тише и тоньше, но видимы.
        color: softColor(lv.color, lv.dim ? 0.3 : 0.08, lv.dim ? 0.8 : 1),
        lineWidth: lv.dim ? 1 : 2,
        lineStyle: lv.dim ? 1 : lv.style,
        title: lv.title,
        // axisLabelVisible ОБЯЗАН быть true. В lightweight-charts подпись на
        // линии и ценник в шкале живут в одной ветке: `if (!labelVisible)
        // return` стоит ДО отрисовки подписи (CustomPriceLinePriceAxisView).
        // Выключив ценник у вспомогательных уровней, я заодно погасил их
        // подписи — на графике остались волосяные линии без единого слова.
        axisLabelVisible: true,
        // ПЛАШКА ОТДЕЛЬНО ОТ ЛИНИИ. Фон подписи берётся из axisLabelColor и
        // только при его отсутствии — из color. Значит линию можно оставить
        // насыщенной и толстой, а плашку сделать полупрозрачной: сквозь неё
        // видно свечи, и она больше не закрывает график.
        axisLabelColor: softColor(lv.color, 0.55, lv.dim ? 0.3 : 0.42),
        // Цвет текста задаём ЯВНО: иначе библиотека подбирает его по яркости
        // плашки и на светлом тоне ставит тёмный — нечитаемо на тёмной канве.
        axisLabelTextColor: LABEL_TEXT_COLOR,
      };
      const existing = smartLines.get(key);
      if (existing) existing.applyOptions(opts);
      else smartLines.set(key, series.createPriceLine(opts));
    }
  });

  onMount(async () => {
    const { createChart } = await import('lightweight-charts');
    if (!el) return;
    chart = createChart(el, {
      width: el.clientWidth || 260,
      height: el.clientHeight || 150,
      // fontSize НЕ трогаем: библиотека держит ОДИН размер на всю канву, и
      // уменьшение ради подписей заявок заодно ужимало цены и шкалу времени.
      layout: { background: { color: '#0f0f1e' }, textColor: '#778' },
      grid: { vertLines: { color: '#1a1a2e' }, horzLines: { color: '#1a1a2e' } },
      localization: { timeFormatter: mskCrosshairFormatter },
      timeScale: { borderColor: '#2d2d4a', timeVisible: true, rightOffset: 4, tickMarkFormatter: mskTickFormatter },
      rightPriceScale: { borderColor: '#2d2d4a', autoScale: true },
      crosshair: { mode: 1 },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true },
    });
    series = chart.addCandlestickSeries({
      upColor: '#4caf50', downColor: '#f44336',
      borderUpColor: '#4caf50', borderDownColor: '#f44336',
      wickUpColor: '#4caf50', wickDownColor: '#f44336',
    });
    ready = true;
    const ro = new ResizeObserver(() => {
      if (!chart || !el) return;
      chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
      // The chart may have been created before the grid cell had its final size (0 →
      // fallback), which framed the candles into the wrong box; reframe once sized.
      if (bars.length) chart.timeScale().fitContent();
    });
    ro.observe(el);
    roRef = ro;
    loadHistory();
  });

  // reload when the symbol or timeframe changes (grid can re-key, but be safe)
  let loadedKey = '';
  $effect(() => {
    const key = `${symbol}@${tf}`;
    if (!ready || key === loadedKey) return;
    loadedKey = key;
    loadHistory();
  });

  let roRef: ResizeObserver | null = null;
  onDestroy(() => { roRef?.disconnect(); chart?.remove(); });
</script>

<div class="mini">
  <div class="mini-head">
    <span class="mc-label">{label || symbol}</span>
    {#if badge}
      <span class="mc-badge" class:long={badgeKind === 'long'} class:short={badgeKind === 'short'}>{badge}</span>
    {/if}
    {#if lastClose !== null}<span class="mc-px">{lastClose.toLocaleString('ru-RU')}</span>{/if}
  </div>
  <div class="mc-chart" bind:this={el}>
    {#if loading}
      <div class="mc-ov"><span class="mc-spin"></span> загрузка…</div>
    {:else if failed}
      <button class="mc-ov mc-retry" onclick={() => loadHistory()}>нет данных · повторить</button>
    {/if}
  </div>
  {#if legend.length}
    <!-- Легенда ПОД графиком отдельной строкой. Накладкой поверх канвы её не
         было видно вовсе: lightweight-charts добавляет свой canvas в контейнер
         ПОСЛЕ svelte-детей и закрывал её собой. Здесь названы типы (цвет +
         штрих), поэтому на самих линиях остались только сторона и объём. -->
    <div class="mc-legend">
      {#each legend as l}
        <span class="mc-l">
          <svg viewBox="0 0 18 8" aria-hidden="true">
            <line x1="1" y1="4" x2="17" y2="4" stroke={l.color} stroke-width="2"
                  stroke-dasharray={l.style === 3 ? '2 3' : l.style === 2 ? '5 3' : '0'} />
          </svg>{l.text}
        </span>
      {/each}
      <span class="mc-l muted">▲ покупка · ▼ продажа · «к» — контракты; тонкая линия — вспомогательный уровень</span>
    </div>
  {/if}
</div>

<style>
  .mini {
    display: flex; flex-direction: column; min-width: 0; min-height: 0;
    background: #0f0f1e; border: 1px solid #2d2d4a; border-radius: 4px; overflow: hidden;
  }
  .mini-head {
    display: flex; align-items: center; gap: 6px; flex-shrink: 0;
    padding: 2px 6px; background: #1a1a2e; border-bottom: 1px solid #2d2d4a; font-size: 11px;
  }
  .mc-label { color: #9ab; font-weight: 600; }
  .mc-badge {
    font-size: 10px; padding: 0 5px; border-radius: 3px; color: #0d0d1c; font-weight: 700;
    background: #6aa8ff;
  }
  .mc-badge.long { background: #4caf50; }
  .mc-badge.short { background: #f44336; }
  .mc-px { margin-left: auto; color: #cde; font-size: 11px; }
  .mc-chart { flex: 1; min-height: 0; position: relative; }
  .mc-ov {
    position: absolute; inset: 0; z-index: 2; display: flex; gap: 6px;
    align-items: center; justify-content: center; font-size: 11px; color: #778;
    background: #0f0f1ecc; border: none; font-family: inherit;
  }
  .mc-retry { cursor: pointer; color: #9ab; }
  .mc-retry:hover { color: #cde; }
  /* Легенда: строка ПОД графиком. Шрифт держим на 10px — это интерфейсный
     минимум оператора, ниже читать нечем. */
  .mc-legend {
    flex-shrink: 0; display: flex; flex-wrap: wrap; gap: 2px 10px;
    padding: 3px 6px; background: #0f0f1e; border-top: 1px solid #1a1a2e;
    font-size: 12px; color: #9aa0b4; line-height: 1.3;
  }
  .mc-l { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
  .mc-l svg { width: 18px; height: 8px; flex-shrink: 0; }
  .mc-l.muted { color: #6f7590; }
  .mc-spin {
    width: 12px; height: 12px; border-radius: 50%;
    border: 2px solid #2d2d4a; border-top-color: #6aa8ff; animation: mcspin 0.8s linear infinite;
  }
  @keyframes mcspin { to { transform: rotate(360deg); } }
</style>
