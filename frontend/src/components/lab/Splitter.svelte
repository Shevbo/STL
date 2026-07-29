<script module lang="ts">
  export function clampSize(v: number, lo: number, hi: number): number {
    return Math.min(hi, Math.max(lo, v));
  }

  /** Размер из localStorage. ОТСУТСТВУЮЩИЙ ключ — это не ноль: возвращаем то, что
      задал родитель. Раньше здесь было Number(null) === 0 -> isFinite(0) === true,
      и на чистом браузере каждый фрейм схлопывался до своего min (экран робота
      открывался колонками по 120-140 px с вертикальным текстом, найдено 29.07). */
  export function savedSize(raw: string | null, min: number, max: number, current: number): number {
    if (raw == null || raw === '') return current;
    const v = Number(raw);
    if (!Number.isFinite(v) || v <= 0) return current;
    return clampSize(v, min, max);
  }
</script>

<script lang="ts">
  let {
    dir = 'v',
    size = $bindable(),
    min = 60,
    max = 100000,
    def = null,
    invert = false,
    storageKey = null,
  }: { dir?: 'v' | 'h'; size: number; min?: number; max?: number; def?: number | null; invert?: boolean; storageKey?: string | null } = $props();

  let dragging = $state(false);
  let start = 0;   // clientX (v) or clientY (h) at pointerdown
  let base = 0;    // size at pointerdown

  if (storageKey) {
    try { size = savedSize(localStorage.getItem(storageKey), min, max, size); } catch {}
  }

  function persist() {
    if (storageKey) { try { localStorage.setItem(storageKey, String(Math.round(size))); } catch {} }
  }
  function down(e: PointerEvent) {
    dragging = true;
    start = dir === 'v' ? e.clientX : e.clientY;
    base = size;
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    e.preventDefault();
  }
  function move(e: PointerEvent) {
    if (!dragging) return;
    const delta = (dir === 'v' ? e.clientX : e.clientY) - start;
    size = clampSize(base + (invert ? -delta : delta), min, max);
  }
  function up(e: PointerEvent) {
    if (!dragging) return;
    dragging = false;
    (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
    persist();
  }
  function reset() { if (def != null) { size = clampSize(def, min, max); persist(); } }
</script>

<div
  class="split {dir}"
  class:dragging
  role="separator"
  aria-orientation={dir === 'v' ? 'vertical' : 'horizontal'}
  title="Потяните — размер; двойной клик — сброс"
  onpointerdown={down}
  onpointermove={move}
  onpointerup={up}
  ondblclick={reset}
></div>

<style>
  .split { flex-shrink: 0; background: transparent; touch-action: none; }
  .split.v { width: 6px; cursor: col-resize; }
  .split.h { height: 6px; cursor: row-resize; }
  .split:hover, .split.dragging { background: #43c46333; }
  .split.dragging { user-select: none; }
</style>
