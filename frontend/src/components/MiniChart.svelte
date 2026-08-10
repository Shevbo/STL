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
  import { KINDS, preview } from '$lib/smart-order-help';
  import { draftBody, kindFromEvent, levelAt, priceSteps, quantize, sideFor } from '$lib/chart-orders';
  import { mskTickFormatter, mskCrosshairFormatter, tfName } from '$lib/chart-time';

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

  // Карта таймфреймов ОБЩАЯ с большим графиком ($lib/chart-time): здешняя копия
  // была урезанной — не хватало M15/H1/H4, а 30м и 1ч сваливались в M15, и по
  // одной и той же кнопке два экрана показывали разные свечи.

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

  // ── Заявки мышкой прямо на графике ──────────────────────────────────────
  // Ни один жест сам по себе заявку НЕ ставит: всё кончается подтверждением с
  // готовой фразой «что произойдёт». Правый клик — меню со всеми типами,
  // Shift/Ctrl/Alt + клик — сразу нужный тип, перетаскивание уровня — перенос.
  let step = $state(0);
  let menu = $state<{ x: number; y: number; price: number } | null>(null);
  let ask = $state<any>(null);        // подтверждение: черновик или перенос
  let busy = $state(false);
  let err = $state('');
  let lastQty = 1;

  // Цену НИКОГДА не округляем до целого: у BR шаг 0.01. Только гасим
  // float-пыль — то же правило, что в панели компаньона.
  const px = (v: number) => Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 6 });

  // Фраза «что произойдёт» — из того же движка, что и форма заявок: два разных
  // текста об одном действии однажды разойдутся.
  const what = (a2: any) => preview({
    kind: a2.kind, side: a2.side, qty: a2.qty, code: a2.code,
    trigger: a2.kind === 'on_fill' ? 0 : a2.price,
    childPrice: a2.kind === 'on_fill' ? a2.price : 0,
    trailOffset: 0, watchId: '', slOffset: 0, tpOffset: 0,
    price: marketPrice(),
  });

  // Шаг цены инструмента: эффектом, а не разово — код инструмента у графика
  // может смениться, и шаг обязан поехать за ним.
  $effect(() => {
    const c = code;
    priceSteps(fetchWithAuth).then((m) => { step = m[c] ?? 0; });
  });

  const priceAt = (y: number): number => {
    const p = series ? Number(series.coordinateToPrice(y)) : 0;
    return p > 0 ? quantize(p, step) : 0;
  };
  const marketPrice = () => lastClose || quote?.last || quote?.bid || 0;

  /** Уровни С КООРДИНАТАМИ — для захвата мышкой. Пересчитываем на каждый
   *  pointerdown: график живой, пиксели уезжают каждую секунду. */
  function levelsNow(): Array<{ y: number; o: any; lv: any }> {
    if (!series) return [];
    const out: Array<{ y: number; o: any; lv: any }> = [];
    for (const o of smartHere) {
      for (const lv of smartLevels(o)) {
        // Тянуть можно только СВОЙ уровень заявки, не расчётные производные:
        // у пика и защитных детей своей цены в книге нет, двигать нечего.
        if (lv.dim || !(lv.price > 0)) continue;
        const y = Number(series.priceToCoordinate(lv.price));
        if (Number.isFinite(y)) out.push({ y, o, lv });
      }
    }
    return out;
  }

  function openMenu(e: MouseEvent) {
    e.preventDefault();
    const price = priceAt(e.offsetY);
    if (!(price > 0)) return;
    menu = { x: e.offsetX, y: e.offsetY, price };
  }

  function draftFor(kind: any, price: number) {
    const side = sideFor(kind, price, marketPrice());
    return { mode: 'new', kind, code, price, qty: lastQty, side: side ?? 'sell', sideKnown: !!side };
  }

  function onChartPointerDown(e: PointerEvent) {
    if (e.button !== 0) return;
    menu = null;
    // 1) взялись за существующий уровень -> перенос
    const hit = levelAt(e.offsetY, levelsNow());
    if (hit >= 0) {
      const { o, lv } = levelsNow()[hit];
      drag = { o, from: lv.price, y0: e.offsetY, moved: false };
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      return;
    }
    // 2) модификатор -> новая заявка
    const kind = kindFromEvent(e);
    if (!kind) return;
    const price = priceAt(e.offsetY);
    if (price > 0) ask = draftFor(kind, price);
  }

  let drag: { o: any; from: number; y0: number; moved: boolean } | null = null;

  function onChartPointerMove(e: PointerEvent) {
    if (!drag) return;
    if (Math.abs(e.offsetY - drag.y0) > 3) drag.moved = true;
  }

  function onChartPointerUp(e: PointerEvent) {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (!d.moved) return;                       // клик, а не перенос
    const to = priceAt(e.offsetY);
    if (!(to > 0) || to === d.from) return;
    ask = { mode: 'move', o: d.o, from: d.from, price: to, kind: d.o.kind,
            code: d.o.code, side: d.o.side, qty: d.o.qty, sideKnown: true };
  }

  async function confirm() {
    if (!ask || busy) return;
    busy = true; err = '';
    try {
      // ПЕРЕНОС = снять и взвести заново: атомарного «подвинуть» у движка нет.
      // Порядок именно такой (сначала снять), иначе на бирже на мгновение
      // окажутся ДВА уровня и оба могут сработать. Если второй шаг не удался,
      // говорим об этом громко: заявки больше нет, и молчать тут нельзя.
      if (ask.mode === 'move') {
        const del = await fetchWithAuth(`/api/v1/quik/smart-orders/${ask.o.so_id}`, { method: 'DELETE' });
        if (!del.ok) { err = 'не удалось снять прежнюю заявку — ничего не менял'; return; }
      }
      // При ПЕРЕНОСЕ пересобираем тело по полям, а не шлём объект заявки
      // целиком: в нём есть so_id и статус, которым в запросе не место. И
      // главное — СОБСТВЕННЫЕ настройки заявки обязаны уехать вместе с ней:
      // отступ следящей и блоки «после сделки». Потеряй их здесь, и перенос
      // молча превратил бы следящую в обычную, а защитную пару стёр.
      const o = ask.o;
      const body = ask.mode === 'move'
        ? {
            kind: o.kind, code: o.code, side: o.side, qty: o.qty,
            trigger_price: o.kind === 'on_fill' ? 0 : ask.price,
            child_price: o.kind === 'on_fill' ? ask.price : 0,
            trail_offset: Number(o.trail_offset || 0),
            sl_offset: Number(o.sl_offset || 0),
            tp_offset: Number(o.tp_offset || 0),
            watch_client_id: String(o.watch_client_id || ''),
            oco_group: String(o.oco_group || ''),
            good_till_ms: Number(o.good_till_ms || 0),
          }
        : draftBody({ kind: ask.kind, code: ask.code, side: ask.side, qty: ask.qty, price: ask.price });
      const res = await fetchWithAuth('/api/v1/quik/smart-orders', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        err = (ask.mode === 'move' ? 'ЗАЯВКА СНЯТА, но не взведена заново: ' : '')
          + (d?.detail || `HTTP ${res.status}`);
        return;
      }
      if (ask.mode === 'new') lastQty = ask.qty;
      ask = null;
      await smartOrdersStore.refresh();
    } catch (e2) {
      err = String(e2);
    } finally {
      busy = false;
    }
  }

  async function loadHistory(attempt = 0) {
    loading = true; failed = false;
    try {
      const tfLabel = tfName(tf);
      const r = await fetchWithAuth(
        `/api/v1/chart/bars?symbol=${encodeURIComponent(symbol)}&tf=${tfLabel}`,
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

  // Меню закрывается кликом куда угодно: висящее поверх графика меню, которое
  // не уходит, читается как зависший интерфейс.
  $effect(() => {
    if (!menu) return;
    const off = () => { menu = null; };
    window.addEventListener('pointerdown', off, { capture: true });
    return () => window.removeEventListener('pointerdown', off, { capture: true });
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
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="mc-chart" bind:this={el}
       oncontextmenu={openMenu}
       onpointerdown={onChartPointerDown}
       onpointermove={onChartPointerMove}
       onpointerup={onChartPointerUp}>
    {#if loading}
      <div class="mc-ov"><span class="mc-spin"></span> загрузка…</div>
    {:else if failed}
      <button class="mc-ov mc-retry" onclick={() => loadHistory()}>нет данных · повторить</button>
    {/if}

    {#if menu}
      <!-- Меню по правой кнопке: все четыре типа с уже подставленной ценой и
           стороной. Сторона выводится из того, ВЫШЕ или НИЖЕ рынка кликнули —
           правило взято из движка, а не придумано. -->
      <div class="mc-menu" style="left:{menu.x}px; top:{menu.y}px">
        <div class="mc-menu-h">{px(menu.price)}</div>
        {#each KINDS as k}
          {@const sd = sideFor(k.id, menu.price, marketPrice())}
          <button onclick={() => { ask = draftFor(k.id, menu.price); menu = null; }}>
            {k.name}
            <i class:buy={sd === 'buy'}>{sd ? (sd === 'buy' ? '▲ покупка' : '▼ продажа') : 'сторону выбрать'}</i>
          </button>
        {/each}
        <div class="mc-menu-f">Shift условная · Ctrl лимитная · Alt следящая</div>
      </div>
    {/if}
  </div>

  {#if ask}
    <!-- Подтверждение. Ни один жест не ставит заявку сам: здесь видно тип,
         сторону, цену, объём и фразу «что произойдёт» из того же движка. -->
    <div class="mc-ask">
      <div class="mc-ask-h">
        {ask.mode === 'move' ? 'Перенести заявку' : 'Новая ' + KIND_BY_ID[ask.kind].name.toLowerCase()}
        <span class="mc-ask-code">{ask.code}</span>
      </div>
      {#if ask.mode === 'move'}
        <div class="mc-ask-row">с <b>{px(ask.from)}</b> на <b>{px(ask.price)}</b></div>
        <div class="mc-ask-warn">
          Атомарного «подвинуть» у сторожа нет: заявка будет СНЯТА и взведена заново.
        </div>
      {:else}
        <div class="mc-ask-row">
          <label>сторона
            <select bind:value={ask.side}>
              <option value="sell">▼ продажа</option>
              <option value="buy">▲ покупка</option>
            </select>
          </label>
          <label>объём <input type="number" min="1" step="1" bind:value={ask.qty} /></label>
          <span>по <b>{px(ask.price)}</b></span>
        </div>
        {@const pv = what(ask)}
        <div class="mc-ask-what">{pv.sentence}{pv.distance ? ' · ' + pv.distance : ''}</div>
        {#if pv.error}<div class="mc-ask-err">{pv.error}</div>{/if}
      {/if}
      {#if err}<div class="mc-ask-err">{err}</div>{/if}
      <div class="mc-ask-btns">
        <button onclick={() => { ask = null; err = ''; }}>Отмена</button>
        <button class="ok" disabled={busy} onclick={confirm}>
          {busy ? 'отправляю…' : (ask.mode === 'move' ? 'Перенести' : 'Взвести')}
        </button>
      </div>
    </div>
  {/if}
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
  /* ── Заявки мышкой: меню и подтверждение ─────────────────────────────── */
  .mc-chart { position: relative; }
  .mc-menu {
    position: absolute; z-index: 30; min-width: 190px;
    background: #14142a; border: 1px solid #3a3a5c; border-radius: 4px;
    box-shadow: 0 6px 20px #0008; padding: 3px; font-size: 12px;
  }
  .mc-menu-h { padding: 2px 8px 4px; color: #cde; font-weight: 600;
    border-bottom: 1px solid #2d2d4a; margin-bottom: 3px; }
  .mc-menu button {
    display: flex; width: 100%; gap: 8px; align-items: baseline;
    background: none; border: 0; color: #cde; text-align: left;
    padding: 4px 8px; border-radius: 3px; cursor: pointer; font-size: 12px;
  }
  .mc-menu button:hover { background: #2d2d4a; }
  .mc-menu i { margin-left: auto; font-style: normal; color: #ff6b5a; font-size: 11px; }
  .mc-menu i.buy { color: #2ecc71; }
  .mc-menu-f { padding: 4px 8px 2px; color: #667; font-size: 10px;
    border-top: 1px solid #2d2d4a; margin-top: 3px; }

  .mc-ask {
    position: absolute; z-index: 40; left: 8px; right: 8px; bottom: 8px;
    background: #14142a; border: 1px solid #43c463; border-radius: 4px;
    padding: 8px 10px; font-size: 12px; color: #cde;
    box-shadow: 0 8px 24px #000a;
  }
  .mc-ask-h { font-weight: 600; margin-bottom: 5px; }
  .mc-ask-code { color: #9ab; font-weight: 400; margin-left: 6px; }
  .mc-ask-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .mc-ask-row label { display: inline-flex; gap: 5px; align-items: center; color: #9ab; }
  .mc-ask-row input, .mc-ask-row select {
    background: #0f0f1e; color: #cde; border: 1px solid #2d2d4a; border-radius: 3px;
    padding: 2px 5px; font-size: 12px; width: 84px;
  }
  .mc-ask-what { margin-top: 6px; color: #9ab; line-height: 1.4; }
  /* Перенос не атомарен — предупреждение обязано быть видно ДО нажатия. */
  .mc-ask-warn { margin-top: 6px; color: #d99a3c; line-height: 1.4; }
  .mc-ask-err { margin-top: 6px; color: #ff6b5a; line-height: 1.4; }
  .mc-ask-btns { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }
  .mc-ask-btns button {
    background: #1a1a2e; color: #cde; border: 1px solid #2d2d4a; border-radius: 3px;
    padding: 3px 12px; font-size: 12px; cursor: pointer;
  }
  .mc-ask-btns .ok { border-color: #43c463; color: #fff; }
  .mc-ask-btns button:disabled { opacity: .5; cursor: default; }

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
