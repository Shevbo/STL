<!-- frontend/src/components/ChartsGrid.svelte
  One frame, every instrument that matters: a compact chart for each symbol that is
  currently IN A POSITION or has WORKING ORDERS (Finam open orders + QUIK orders). The
  set updates live as positions/orders change; an empty set shows a hint. -->
<script lang="ts">
  import { onDestroy } from 'svelte';
  import { positionsStore } from '$lib/stores/positions.svelte';
  import { ordersStore } from '$lib/stores/orders.svelte';
  import { instrumentStore } from '$lib/stores/instrument.svelte';
  import { smartOrdersStore } from '$lib/stores/smart-orders.svelte';
  import MiniChart from './MiniChart.svelte';
  import Splitter from './lab/Splitter.svelte';
  import { TF_BUTTONS } from '$lib/chart-time';

  type Entry = {
    symbol: string;
    label: string;
    badge: string;
    badgeKind: 'long' | 'short' | 'neutral';
  };

  // QUIK working orders are polled here (their codes -> chart symbols are CODE@RTSX).
  let quikCodes = $state<string[]>([]);
  async function loadQuik() {
    try {
      const r = await fetch('/api/v1/quik/orders/working', { credentials: 'include' });
      if (!r.ok) return;
      const rows: { code: string; state: string }[] = (await r.json()).orders ?? [];
      const active = rows.filter((o) => ['pending', 'active', 'partial'].includes(o.state));
      quikCodes = [...new Set(active.map((o) => o.code))];
    } catch { /* keep previous */ }
  }
  $effect(() => {
    loadQuik();
    const t = setInterval(loadQuik, 4000);
    return () => clearInterval(t);
  });

  // Книгу умных заявок опрашиваем ЗДЕСЬ, один раз на всю сетку (у стора общий
  // таймер со счётчиком подписчиков). Подписка в каждом MiniChart дала бы N
  // подписок на один и тот же опрос.
  const unsubSmart = smartOrdersStore.subscribe(2000);
  onDestroy(unsubSmart);

  function tickerOf(symbol: string): string {
    const found = instrumentStore.list.find((i) => i.symbol === symbol);
    return found?.ticker || symbol.split('@')[0] || symbol;
  }

  // Build the deduped entry list: positions first (with side/qty), then Finam orders,
  // then QUIK orders. A symbol already shown gets its badge enriched, not duplicated.
  let entries = $derived.by<Entry[]>(() => {
    const map = new Map<string, Entry>();

    for (const p of positionsStore.all) {
      if (!p.symbol || !p.quantity || p.side === 'flat') continue;
      map.set(p.symbol, {
        symbol: p.symbol,
        label: tickerOf(p.symbol),
        badge: `${p.side === 'short' ? 'Short' : 'Long'} ${Math.abs(p.quantity)}`,
        badgeKind: p.side === 'short' ? 'short' : 'long',
      });
    }

    const orderCount = new Map<string, number>();
    for (const o of ordersStore.all) {
      if (!o.symbol) continue;
      orderCount.set(o.symbol, (orderCount.get(o.symbol) ?? 0) + 1);
    }
    for (const [symbol, n] of orderCount) {
      const e = map.get(symbol);
      if (e) e.badge += ` · ${n} заяв.`;
      else map.set(symbol, { symbol, label: tickerOf(symbol), badge: `${n} заяв.`, badgeKind: 'neutral' });
    }

    for (const code of quikCodes) {
      const symbol = code.includes('@') ? code : `${code}@RTSX`;
      const e = map.get(symbol);
      if (e) { if (!e.badge.includes('QUIK')) e.badge += ' · QUIK'; }
      else map.set(symbol, { symbol, label: tickerOf(symbol), badge: 'QUIK заяв.', badgeKind: 'neutral' });
    }

    // Умные заявки — ЧЕТВЁРТЫЙ источник. Взведённая умная заявка не создаёт
    // работающего ордера на бирже, пока не сработает, поэтому по трём источникам
    // выше её инструмент не попадал сюда вовсе: при семи взведённых заявках экран
    // писал «нет открытых позиций и активных заявок» (09.08.2026). Уровни рисует
    // сам MiniChart.
    const smartCount = new Map<string, number>();
    for (const o of smartOrdersStore.armed) {
      if (!o.code) continue;
      const symbol = o.code.includes('@') ? o.code : `${o.code}@RTSX`;
      smartCount.set(symbol, (smartCount.get(symbol) ?? 0) + 1);
    }
    for (const [symbol, n] of smartCount) {
      const e = map.get(symbol);
      if (e) e.badge += ` · ${n} умных`;
      else map.set(symbol, { symbol, label: tickerOf(symbol), badge: `${n} умных`, badgeKind: 'neutral' });
    }

    return [...map.values()];
  });

  // Высота графиков тянется мышкой за полосу под сеткой и запоминается.
  // Границы: ниже 120 свечей не разобрать, выше 900 сетка перестаёт быть сеткой.
  const CHART_MIN = 120, CHART_MAX = 900, CHART_DEF = 200;
  let chartH = $state(CHART_DEF);

  // Таймфрейм сетки. Запоминаем: оператор возвращается к тем же графикам, и
  // сбрасывать его на 5 минут при каждом открытии фрейма незачем.
  const TF_KEY = 'stl.ordersChartTf';
  let tf = $state(read(TF_KEY, 5));
  function setTf(v: number) {
    tf = v;
    try { localStorage.setItem(TF_KEY, String(v)); } catch { /* приватный режим */ }
  }
  function read(key: string, def: number): number {
    try {
      const v = Number(localStorage.getItem(key));
      return TF_BUTTONS.some((b) => b.value === v) ? v : def;
    } catch { return def; }
  }
</script>

<div class="grid-frame">
  <div class="gf-head">
    <span class="gf-title">Графики: позиции и заявки</span>
    <span class="gf-count">{entries.length}</span>
    <span class="gf-sp"></span>
    <!-- Таймфрейм ОДИН на всю сетку: инструменты в ней сравнивают между собой,
         и разные кадры у соседних графиков сравнение ломают. -->
    <span class="gf-tf">
      {#each TF_BUTTONS as b}
        <button class:on={tf === b.value} onclick={() => setTf(b.value)}>{b.label}</button>
      {/each}
    </span>
  </div>
  {#if !entries.length}
    <div class="gf-empty">Нет открытых позиций, активных и умных заявок.</div>
  {:else}
    <div class="gf-grid" style="--mini-h: {chartH}px">
      {#each entries as e (e.symbol)}
        <MiniChart symbol={e.symbol} label={e.label} badge={e.badge} badgeKind={e.badgeKind} {tf} />
      {/each}
    </div>
    <!-- Тянущаяся высота графиков. Ручку делаем ВИДИМОЙ: штатный Splitter
         прозрачен до наведения, и оператор её просто не нашёл. Полоса с
         засечками и подписью — единственное место фрейма, которое выглядит как
         «за это тянут». Двойной клик возвращает исходные 200. -->
    <div class="gf-resize" title="Потяните вверх или вниз — высота графиков; двойной клик — сброс">
      <Splitter dir="h" bind:size={chartH} min={CHART_MIN} max={CHART_MAX} def={CHART_DEF}
                storageKey="stl.ordersChartH" />
      <span class="gf-resize-hint">высота графиков · {chartH} px</span>
    </div>
  {/if}
</div>

<style>
  .grid-frame { display: flex; flex-direction: column; height: 100%; background: #14142a; overflow: hidden; }
  .gf-head {
    display: flex; align-items: center; gap: 8px; flex-shrink: 0;
    padding: 4px 8px; border-bottom: 1px solid #2d2d4a; font-size: 12px;
  }
  .gf-title { color: #9ab; font-weight: 600; }
  .gf-count {
    background: #1a1a2e; color: #cde; border: 1px solid #2d2d4a; border-radius: 10px;
    padding: 0 8px; font-size: 11px;
  }
  .gf-empty { padding: 16px; color: #667; font-size: 12px; }
  .gf-sp { flex: 1; }
  /* Полоса «тянуть высоту». Штатный сплиттер прозрачен до наведения — здесь он
     обязан быть заметен сам по себе, иначе функции как будто нет. */
  .gf-resize {
    flex-shrink: 0; position: relative; height: 14px; cursor: row-resize;
    background: #1a1a2e; border-top: 1px solid #2d2d4a;
    display: flex; align-items: center; justify-content: center;
  }
  .gf-resize:hover { background: #21213a; }
  .gf-resize :global(.split.h) {
    position: absolute; inset: 0; height: auto; background: transparent;
  }
  .gf-resize :global(.split.h):hover, .gf-resize :global(.split.h.dragging) {
    background: #43c46322;
  }
  /* Засечки: три полоски по центру — общепринятый знак «меня можно тянуть». */
  .gf-resize::before {
    content: ''; width: 26px; height: 4px; border-radius: 2px;
    background: repeating-linear-gradient(#4a4a6a 0 1px, transparent 1px 2px);
    pointer-events: none;
  }
  .gf-resize-hint {
    position: absolute; right: 8px; color: #667; font-size: 10px;
    pointer-events: none; white-space: nowrap;
  }
  .gf-tf { display: flex; gap: 2px; }
  .gf-tf button {
    background: #1a1a2e; color: #9ab; border: 1px solid #2d2d4a; border-radius: 3px;
    padding: 1px 7px; font-size: 11px; cursor: pointer; line-height: 1.6;
  }
  .gf-tf button:hover { color: #cde; }
  .gf-tf button.on { background: #2d2d4a; color: #fff; border-color: #43c463; }
  .gf-grid {
    flex: 1; min-height: 0; overflow: auto;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 8px; padding: 8px;
  }
  /* Высоту задаёт переменная: её крутит сплиттер под сеткой. */
  .gf-grid :global(.mini) { height: var(--mini-h, 200px); }
</style>
