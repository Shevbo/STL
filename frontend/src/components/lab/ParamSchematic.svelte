<script lang="ts">
  // Static illustrative SVG per concept + LIVE numeric annotations (operator's
  // choice: «статичная схема + живые числа»). Shape is fixed and always legible;
  // the numbers (points, ATR, price) come from live context. Font ≥ 12px.
  let { kind, atr = 0, price = 0, tpMult = 0, stepMult = 0, fracPct = 0 }:
    { kind: 'fvg' | 'atr' | 'ladder'; atr?: number; price?: number;
      tpMult?: number; stepMult?: number; fracPct?: number } = $props();

  const pts = (n: number) => Math.round(n).toLocaleString('ru-RU') + ' п.';
</script>

<div class="schem">
{#if kind === 'fvg'}
  <!-- three M1 candles: [-3] позапрошлый, [-2] разрыв, [-1] подтверждение -->
  <svg viewBox="0 0 300 150" role="img" aria-label="Схема Fair Value Gap">
    <!-- bar -3 -->
    <line x1="55" y1="95" x2="55" y2="125" class="wick" />
    <rect x="45" y="100" width="20" height="18" class="down" />
    <text x="55" y="140" class="lbl">бар −3</text>
    <!-- bar -2 (gap/impulse) -->
    <line x1="130" y1="30" x2="130" y2="95" class="wick" />
    <rect x="120" y="45" width="20" height="42" class="up" />
    <text x="130" y="140" class="lbl">бар −2</text>
    <!-- bar -1 (confirmation) -->
    <line x1="215" y1="18" x2="215" y2="60" class="wick" />
    <rect x="205" y="25" width="20" height="30" class="up thick" />
    <text x="215" y="140" class="lbl">бар −1 (тек.)</text>
    <!-- gap band: between high[-3] (y=100) and low[-1] (y=60) -->
    <rect x="40" y="60" width="230" height="40" class="gap" />
    <line x1="40" y1="100" x2="270" y2="100" class="ref" />
    <text x="272" y="103" class="mini" text-anchor="start">high −3</text>
    <line x1="40" y1="60" x2="270" y2="60" class="ref" />
    <text x="272" y="63" class="mini" text-anchor="start">low −1</text>
    <text x="152" y="82" class="note" text-anchor="middle">разрыв: low −1 &gt; high −3</text>
    <!-- body bracket on bar -1 -->
    <text x="235" y="42" class="mini" text-anchor="start">тело</text>
  </svg>
  <div class="cap">
    Бычий FVG: <b>low текущего бара выше high позапрошлого</b> (разрыв) И тело
    текущей 1-мин свечи ≥ порога.
    {#if price > 0 && fracPct > 0}<br><b class="live">тело ≥ {pts(price * fracPct / 100)}</b> при цене {Math.round(price).toLocaleString('ru-RU')} ({fracPct.toFixed(2)}%){/if}
  </div>

{:else if kind === 'atr'}
  <svg viewBox="0 0 300 150" role="img" aria-label="Схема ATR">
    <line x1="150" y1="20" x2="150" y2="130" class="wick" />
    <rect x="135" y="55" width="30" height="45" class="up" />
    <line x1="60" y1="20" x2="240" y2="20" class="ref" />
    <line x1="60" y1="130" x2="240" y2="130" class="ref" />
    <text x="250" y="24" class="mini" text-anchor="start">High</text>
    <text x="250" y="134" class="mini" text-anchor="start">Low</text>
    <path d="M 95 20 L 85 20 L 85 130 L 95 130" class="brace" />
    <text x="78" y="78" class="note" text-anchor="middle" transform="rotate(-90 78 78)">размах бара</text>
  </svg>
  <div class="cap">
    ATR = средний размах одного 1-мин бара за период (метод Уайлдера).
    {#if atr > 0}<br><b class="live">сейчас ≈ {pts(atr)}</b> за бар{/if}
  </div>

{:else if kind === 'ladder'}
  <svg viewBox="0 0 300 160" role="img" aria-label="Схема тейка и усреднения">
    <!-- entry -->
    <line x1="30" y1="80" x2="270" y2="80" class="entry" />
    <text x="30" y="74" class="mini">средняя входа</text>
    <!-- TP above -->
    <line x1="30" y1="30" x2="270" y2="30" class="tp" />
    <text x="30" y="24" class="mini tpc">тейк-профит ↑</text>
    <path d="M 260 80 L 260 30" class="arr tpc" marker-end="url(#a-tp)" />
    <!-- averaging below -->
    <line x1="30" y1="120" x2="270" y2="120" class="avg" />
    <text x="30" y="135" class="mini avgc">докупка (усреднение) ↓</text>
    <path d="M 260 80 L 260 120" class="arr avgc" marker-end="url(#a-av)" />
    <defs>
      <marker id="a-tp" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" class="tpc-fill" /></marker>
      <marker id="a-av" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto">
        <path d="M0,0 L8,4 L0,8 Z" class="avgc-fill" /></marker>
    </defs>
  </svg>
  <div class="cap">
    Тейк — выше средней на tp_atr×ATR; докупка — ниже на avg_step_atr×ATR (для лонга).
    {#if atr > 0}
      <br>
      {#if tpMult > 0}<b class="live tpc">тейк {tpMult.toFixed(1)}×ATR = {pts(tpMult * atr)}</b>{/if}
      {#if tpMult > 0 && stepMult > 0} · {/if}
      {#if stepMult > 0}<b class="live avgc">шаг {stepMult.toFixed(1)}×ATR = {pts(stepMult * atr)}</b>{/if}
    {/if}
  </div>
{/if}
</div>

<style>
  .schem { background: var(--sch-bg, #0e0e1c); border: 1px solid var(--sch-line, #22224a);
    border-radius: 6px; padding: 8px 10px; margin-top: 6px; }
  svg { width: 100%; max-width: 320px; height: auto; display: block; }
  .cap { font-size: 12px; line-height: 1.5; color: var(--sch-cap, #9aa); margin-top: 4px; }
  .cap b { color: var(--sch-ink, #dde); }
  .live { font-family: Consolas, "SF Mono", monospace; }
  .up { fill: #1b7a52; stroke: #2ee6a6; }
  .up.thick { stroke-width: 2; }
  .down { fill: #7a1b3a; stroke: #ff5c8a; }
  .wick { stroke: #667; stroke-width: 2; }
  .gap { fill: #7ab8ff18; stroke: #7ab8ff55; stroke-dasharray: 3 3; }
  .ref { stroke: #445; stroke-width: 1; stroke-dasharray: 2 3; }
  .entry { stroke: #99a; stroke-width: 2; }
  .tp { stroke: #2ee6a6; stroke-width: 2; }
  .avg { stroke: #ff5c8a; stroke-width: 2; }
  .arr { stroke-width: 1.5; fill: none; }
  .tpc { color: #2ee6a6; } .tpc, text.tpc { fill: #2ee6a6; stroke: #2ee6a6; }
  .avgc { color: #ff5c8a; } .avgc, text.avgc { fill: #ff5c8a; stroke: #ff5c8a; }
  .tpc-fill { fill: #2ee6a6; stroke: none; }
  .avgc-fill { fill: #ff5c8a; stroke: none; }
  text { font-size: 12px; fill: var(--sch-ink, #ccd); font-family: "Segoe UI", sans-serif; }
  text.lbl { font-size: 12px; fill: #889; text-anchor: middle; }
  text.mini { font-size: 11px; fill: #99a; stroke: none; }
  text.note { font-size: 12px; fill: #7ab8ff; stroke: none; }
  .brace { fill: none; stroke: #778; stroke-width: 1.5; }
  @media (prefers-color-scheme: light) {
    .schem { --sch-bg: #f3f4f8; --sch-line: #d5d9e6; --sch-cap: #556; --sch-ink: #1a1e28; }
    text { fill: #1a1e28; }
  }
</style>
