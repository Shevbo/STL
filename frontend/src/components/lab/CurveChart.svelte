<!-- Кривая доходности кампании: площадь от нулевой оси, как в TSLab.
     ВЫШЕ нуля мягкий зелёный, НИЖЕ мягкий красный, смена строго на y=0.

     Миниатюра и большой график — один компонент, режимом `full`. Не EquityChart:
     тот рисует несколько роботов по журналу algo_trades на uPlot с осью ГО справа,
     а здесь нужна ОДНА кривая с жёсткой сменой цвета на нуле, чего градиентом не
     получить без размазанного перехода.

     Геометрия считается в $lib/campaign-showcase (разрез кривой в точках
     пересечения нуля) и покрыта тестами: «зелёное под нулём» там невозможно. -->
<script lang="ts">
  import { curveGeometry, fmtPnl, niceTicks, toMs } from '$lib/campaign-showcase';

  // overlay — вторая серия («купил и держи») на ТОЙ ЖЕ шкале; h — высота большого
  // графика (разность стратегии и «купил и держи» рисуется ниже и ниже ростом).
  let { points = null, unit = '', full = false, emptyText = 'кривой нет', overlay = null,
        overlayLabel = '', h = 300 }: {
    points?: [number, number][] | null; unit?: string; full?: boolean; emptyText?: string;
    overlay?: [number, number][] | null; overlayLabel?: string; h?: number;
  } = $props();

  // Миниатюра: фиксированный viewBox, растягивается на ширину карточки.
  const TW = 240, TH = 64;
  let boxW = $state(720);
  const W = $derived(full ? Math.max(320, boxW) : TW);
  const H = $derived(full ? h : TH);
  const PAD = $derived(full ? { l: 62, r: 14, t: 12, b: 26 } : { l: 0, r: 0, t: 4, b: 4 });

  const geo = $derived(curveGeometry(points, W, H, PAD, overlay));
  const last = $derived(points && points.length ? points[points.length - 1][1] : null);

  const fmtDay = (ms: number) => new Date(ms).toLocaleDateString('ru-RU',
    { timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: '2-digit' });

  const yTicks = $derived(geo ? niceTicks(geo.ymin, geo.ymax, 5) : []);
  const xTicks = $derived.by(() => {
    if (!geo) return [] as number[];
    const n = Math.max(2, Math.min(6, Math.floor(W / 130)));
    return Array.from({ length: n + 1 }, (_, i) => geo.xmin + (geo.xmax - geo.xmin) * i / n);
  });

  // Подсказка по наведению: ближайшая ТОЧКА ДАННЫХ, а не интерполяция — человек
  // читает значение, которое действительно есть в отчёте.
  let hover = $state<{ x: number; y: number; ts: number; v: number } | null>(null);
  function onMove(e: PointerEvent) {
    if (!full || !geo || !points) return;
    const r = (e.currentTarget as SVGElement).getBoundingClientRect();
    const px = (e.clientX - r.left) * (W / r.width);
    let best = points[0], bd = Infinity;
    for (const p of points) {
      const d = Math.abs(geo.x(p[0]) - px);
      if (d < bd) { bd = d; best = p; }
    }
    hover = { x: geo.x(best[0]), y: geo.y(best[1]), ts: toMs(best[0]), v: best[1] };
  }
</script>

{#if !geo}
  <div class="cc-empty" class:full style:height={full ? '120px' : `${TH}px`}>
    <span>{emptyText}</span>
  </div>
{:else}
  <div class="cc" class:full bind:clientWidth={boxW}>
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio={full ? 'xMidYMid meet' : 'none'}
         width="100%" height={full ? H : TH} role="img"
         aria-label={`кривая доходности, итог ${fmtPnl(last, unit)}`}
         onpointermove={onMove} onpointerleave={() => (hover = null)}>
      {#if full}
        <!-- Сетка едва заметная: подсказывает масштаб, но не спорит с кривой. -->
        {#each yTicks as t (t)}
          <line class="grid" x1={PAD.l} x2={W - PAD.r} y1={geo.y(t)} y2={geo.y(t)} />
          <text class="tick" x={PAD.l - 8} y={geo.y(t) + 4} text-anchor="end">{fmtPnl(t, unit, false)}</text>
        {/each}
        {#each xTicks as t (t)}
          <text class="tick" x={geo.x(t)} y={H - 8} text-anchor="middle">{fmtDay(t)}</text>
        {/each}
      {/if}
      {#each geo.segs as s, i (i)}
        {#if s.sign !== 0}
          <path class={s.sign > 0 ? 'area pos' : 'area neg'} d={s.area} />
        {/if}
      {/each}
      <!-- «Купил и держи»: тонкая нейтральная линия поверх заливки, на той же шкале. -->
      {#if full && geo.extraLine}
        <path class="hold" d={geo.extraLine} />
      {/if}
      <!-- Нулевая ось поверх заливки: смена цвета читается именно на ней. -->
      <line class="zero" x1={PAD.l} x2={W - PAD.r} y1={geo.y0} y2={geo.y0} />
      {#each geo.segs as s, i (i)}
        <path class={s.sign > 0 ? 'ln pos' : s.sign < 0 ? 'ln neg' : 'ln flat'} d={s.line} />
      {/each}
      {#if hover}
        <line class="cross" x1={hover.x} x2={hover.x} y1={PAD.t} y2={H - PAD.b} />
        <circle class={hover.v >= 0 ? 'dot pos' : 'dot neg'} cx={hover.x} cy={hover.y} r="3.5" />
      {/if}
    </svg>
    {#if full && geo.extraLine && overlayLabel}
      <span class="legend"><i class="sw-hold"></i>{overlayLabel}</span>
    {/if}
    {#if !full}
      <span class="corner" class:pos={(last ?? 0) > 0} class:neg={(last ?? 0) < 0}>{fmtPnl(last, unit)}</span>
    {/if}
    {#if full && hover}
      <div class="tip" style:left={`${Math.min(Math.max(hover.x / W * 100, 12), 88)}%`}>
        <b>{fmtDay(hover.ts)}</b>
        <span class:pos={hover.v > 0} class:neg={hover.v < 0}>{fmtPnl(hover.v, unit)}</span>
      </div>
    {/if}
  </div>
{/if}

<style>
  /* Токены темы: тёмная по умолчанию (вся SPA тёмная), светлая — по системной.
     Зелёный и красный мягкие, приглушённые, заливка 30-35% (заказ оператора). */
  .cc, .cc-empty {
    --pos: #6fa77a; --neg: #c47a7a; --pos-fill: rgba(111,167,122,.32); --neg-fill: rgba(196,122,122,.32);
    --axis: #5a5f78; --grid: rgba(150,160,190,.10); --tick: #8a90a8; --flat: #8a90a8; --hold: #b9a46a;
    --tip-bg: #1a1a2e; --tip-bd: #2d2d4a; --ink: #e2e6f0;
    position: relative; width: 100%;
  }
  @media (prefers-color-scheme: light) {
    .cc, .cc-empty {
      --pos: #4f8a5a; --neg: #b25f5f; --pos-fill: rgba(79,138,90,.26); --neg-fill: rgba(178,95,95,.26);
      --axis: #8b8f9c; --grid: rgba(60,70,100,.10); --tick: #6a6f80; --flat: #6a6f80; --hold: #9a7f2a;
      --tip-bg: #fff; --tip-bd: #d6d8e0; --ink: #222630;
    }
  }
  .cc svg { display: block; }
  .area.pos { fill: var(--pos-fill); }
  .area.neg { fill: var(--neg-fill); }
  .ln { fill: none; stroke-width: 1.6; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
  .ln.pos { stroke: var(--pos); }
  .ln.neg { stroke: var(--neg); }
  .ln.flat { stroke: var(--flat); }
  .hold { fill: none; stroke: var(--hold); stroke-width: 1.4; stroke-dasharray: 5 3;
          vector-effect: non-scaling-stroke; }
  .legend { position: absolute; left: 70px; top: 2px; display: inline-flex; align-items: center; gap: 6px;
            font: 10px/1 ui-monospace, Consolas, monospace; color: var(--tick); }
  .sw-hold { display: inline-block; width: 18px; border-top: 2px dashed var(--hold); }
  .zero { stroke: var(--axis); stroke-width: 1; vector-effect: non-scaling-stroke; }
  .grid { stroke: var(--grid); stroke-width: 1; vector-effect: non-scaling-stroke; }
  .cross { stroke: var(--axis); stroke-width: 1; stroke-dasharray: 3 3; vector-effect: non-scaling-stroke; }
  .dot.pos { fill: var(--pos); }
  .dot.neg { fill: var(--neg); }
  .tick { fill: var(--tick); font: 10px ui-monospace, Consolas, monospace; }
  .corner { position: absolute; right: 6px; top: 3px; font: 600 11px/1 ui-monospace, Consolas, monospace;
            color: var(--tick); background: transparent; white-space: nowrap; }
  .corner.pos { color: var(--pos); }
  .corner.neg { color: var(--neg); }
  .tip { position: absolute; top: 4px; transform: translateX(-50%); pointer-events: none;
         background: var(--tip-bg); border: 1px solid var(--tip-bd); border-radius: 4px;
         padding: 3px 8px; font: 11px/1.4 ui-monospace, Consolas, monospace; color: var(--ink);
         display: flex; gap: 8px; white-space: nowrap; }
  .tip .pos { color: var(--pos); } .tip .neg { color: var(--neg); }
  .cc-empty { display: flex; align-items: center; justify-content: center; text-align: center;
              border: 1px dashed var(--axis); border-radius: 4px; padding: 6px 10px;
              color: var(--tick); font-size: 11px; line-height: 1.35; }
</style>
