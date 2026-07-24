<script lang="ts">
  // Схема на каждый тип умной заявки: форма статична и всегда читается, числа
  // живые (текущая цена, уровень, отступ). Тот же приём, что в схемах стратегий:
  // рисунок объясняет механику, подписи под ним — конкретику этой заявки.
  import { KIND_BY_ID, fmtPts, type Kind, type Side } from '$lib/smart-order-help';

  let { kind, side = 'sell', trigger = 0, trailOffset = 0, price = 0, watchId = '' }:
    { kind: Kind; side?: Side; trigger?: number; trailOffset?: number;
      price?: number; watchId?: string } = $props();

  const meta = $derived(KIND_BY_ID[kind]);
  const num = (n: number) => Number(n).toLocaleString('ru-RU', { maximumFractionDigits: 6 });
  // У продажи защитный уровень СНИЗУ (лонг), у покупки СВЕРХУ (шорт) — схема
  // переворачивается, иначе она объясняет чужой случай.
  const flip = $derived(side === 'buy');
</script>

<figure class="so-schem" style="--accent:{meta.color}">
  {#if kind === 'sl' || kind === 'tp'}
    <!-- Цена идёт к уровню и пересекает его. Для SL уровень против позиции,
         для TP по направлению — форма одна, разворачивается по стороне. -->
    <svg viewBox="0 0 320 150" role="img" aria-label="Схема {meta.name}">
      <g transform={flip ? 'translate(0,150) scale(1,-1)' : ''}>
        <path class="px" d={kind === 'sl'
          ? 'M14 34 L60 46 L104 30 L150 58 L196 74 L240 100 L300 122'
          : 'M14 122 L60 108 L104 118 L150 84 L196 70 L240 44 L300 26'} />
        <line class="lvl" x1="8" y1={kind === 'sl' ? 100 : 44} x2="312" y2={kind === 'sl' ? 100 : 44} />
        <circle class="hit" cx="240" cy={kind === 'sl' ? 100 : 44} r="5" />
      </g>
      <text class="lbl" x="12" y={kind === 'sl' ? (flip ? 44 : 94) : (flip ? 106 : 38)}>уровень</text>
      <text class="hitlbl" x="248" y={kind === 'sl' ? (flip ? 56 : 118) : (flip ? 118 : 62)}>здесь сработает</text>
      <text class="mini" x="312" y="146" text-anchor="end">время →</text>
    </svg>
    <figcaption>
      {#if kind === 'sl'}
        Цена идёт против позиции. Как только она {side === 'sell' ? 'опустится до' : 'поднимется до'}
        <b>{trigger > 0 ? num(trigger) : 'уровня'}</b>, сторож выставляет заявку и убыток перестаёт расти.
      {:else}
        Цена идёт в сторону прибыли. На отметке <b>{trigger > 0 ? num(trigger) : 'цели'}</b>
        сторож фиксирует результат, не дожидаясь вас.
      {/if}
    </figcaption>

  {:else if kind === 'trail_tp'}
    <!-- Пик подтягивается за ценой, линия отката идёт следом ступеньками и
         никогда не опускается. Выход в точке касания. -->
    <svg viewBox="0 0 320 150" role="img" aria-label="Схема скользящего стопа">
      <g transform={flip ? 'translate(0,150) scale(1,-1)' : ''}>
        <path class="px" d="M14 120 L54 104 L92 108 L130 74 L168 80 L206 44 L232 52 L258 40 L300 96" />
        <path class="trail" d="M14 140 L54 124 L92 124 L130 94 L168 94 L206 64 L232 64 L258 60 L300 60" />
        <circle class="peak" cx="258" cy="40" r="4.5" />
        <circle class="hit" cx="292" cy="60" r="5" />
        <line class="gap" x1="258" y1="40" x2="258" y2="60" />
      </g>
      <text class="lbl" x="200" y={flip ? 126 : 34}>пик</text>
      <text class="lbl2" x="14" y={flip ? 22 : 148}>линия отката идёт за пиком</text>
      <text class="hitlbl" x="290" y={flip ? 106 : 78} text-anchor="end">откат — выход</text>
    </svg>
    <figcaption>
      Пик подтягивается за ценой и назад не сдаёт. Выход в момент, когда цена
      отошла от него на <b>{trailOffset > 0 ? fmtPts(trailOffset) : 'заданный отступ'}</b>.
      {#if trigger > 0}Слежение включится на <b>{num(trigger)}</b>.{:else}Слежение начинается сразу.{/if}
    </figcaption>

  {:else}
    <!-- Событие, а не уровень: исполнение одной заявки ставит другую. -->
    <svg viewBox="0 0 320 150" role="img" aria-label="Схема заявки по исполнению">
      <rect class="box" x="16" y="46" width="112" height="58" rx="2" />
      <text class="boxt" x="72" y="70" text-anchor="middle">заявка,</text>
      <text class="boxt" x="72" y="88" text-anchor="middle">за которой следим</text>
      <path class="arrow" d="M136 75 L182 75" />
      <polygon class="arrowhead" points="182,69 196,75 182,81" />
      <text class="mini" x="166" y="64" text-anchor="middle">исполнилась</text>
      <rect class="box accent" x="200" y="46" width="106" height="58" rx="2" />
      <text class="boxt" x="253" y="70" text-anchor="middle">ставится</text>
      <text class="boxt" x="253" y="88" text-anchor="middle">эта заявка</text>
    </svg>
    <figcaption>
      Уровня цены нет. Сторож ждёт события: как только
      <b>{watchId ? watchId.slice(0, 22) : 'указанная заявка'}</b> отчитается об исполнении,
      он немедленно ставит вашу.
    </figcaption>
  {/if}
</figure>

<style>
  .so-schem { margin: 0; }
  svg { width: 100%; height: auto; max-height: 190px; display: block; }
  .px { fill: none; stroke: #8f96b3; stroke-width: 1.6; }
  .lvl { stroke: var(--accent); stroke-width: 1.6; stroke-dasharray: 6 4; }
  .trail { fill: none; stroke: var(--accent); stroke-width: 1.6; stroke-dasharray: 2 3; }
  .hit { fill: var(--accent); }
  .peak { fill: #e8e8f0; }
  .gap { stroke: var(--accent); stroke-width: 1; stroke-dasharray: 2 2; }
  .box { fill: #1b1b34; stroke: #2d2d4a; }
  .box.accent { stroke: var(--accent); }
  .arrow { stroke: #8f96b3; stroke-width: 1.6; }
  .arrowhead { fill: #8f96b3; }
  .lbl, .lbl2, .boxt { fill: #b9bfd4; font: 12px 'Segoe UI', sans-serif; }
  .lbl2 { fill: var(--accent); }
  .hitlbl { fill: var(--accent); font: 12px 'Segoe UI', sans-serif; }
  .mini { fill: #7a819b; font: 11px 'Segoe UI', sans-serif; }
  figcaption {
    color: #b9bfd4; font-size: 12px; line-height: 1.5; margin-top: 6px;
    border-left: 2px solid var(--accent); padding-left: 8px;
  }
  figcaption b { color: #e8e8f0; font-weight: 600; }
</style>
