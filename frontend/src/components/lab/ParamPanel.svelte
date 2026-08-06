<!-- ParamPanel: развёрнутый вид параметров робота.
     Альтернатива плотному списку (ParamEditor) — переключается кнопкой в шапке.

     Зачем: в списке 19 полей подписи обрезаны до «Разножка: мин…», «Тейк-профит
     ×A…», значения набраны в 10px, а бинарные показаны текстом `true`. На живых
     деньгах это чтение с догадкой.

     Три решения, каждое от предметной области, а не от вкуса:
     1. ПЕРЕВОД В ЕДИНИЦЫ ИНСТРУМЕНТА под каждым значением — «0.4×ATR ≈ 6 пунктов».
        Функция перевода уже была в strategy-help, но пряталась за кликом по «?».
        Ради неё панель и существует: сырое число 4 не говорит ничего.
     2. ГРУППЫ ПО СМЫСЛУ (сигнал / объём / выходы / частота / разрешения) — порядок
        вопросов, которые оператор задаёт роботу.
     3. ПЕРЕКЛЮЧАТЕЛЬ вместо текстового `true` и КРУПНЫЕ стрелки с автоповтором.
-->
<script lang="ts">
  import { helpFor, type LiveCtx } from '$lib/strategy-help';
  import { BINARY, BOUNDING, groupFields, stepFor, type Field } from '$lib/param-groups';

  let { strategyId, schema, values = $bindable({}), ctx, disabledKeys = [], baseline = null,
        wide = false, ranges = $bindable(null) }:
    { strategyId: string; schema: Field[]; values: Record<string, any>;
      ctx: LiveCtx; disabledKeys?: string[];
      /** Широкий лист: группы встают в несколько колонок, а не в одну кишку. */
      wide?: boolean;
      /** Диапазоны перебора {key:{from,to,step}}. Переданы — под каждым числовым
       *  значением появляется строка «от / до / шаг»: Лаборатория перебирает, а не
       *  ставит одно число, и ей нужен ТОТ ЖЕ фрейм, а не своя таблица полей. */
      ranges?: Record<string, { from: number; to: number; step: number }> | null;
      /** Параметры, которые СЕЙЧАС стоят у робота — для строки «что изменится». */
      baseline?: Record<string, any> | null } = $props();

  let groups = $derived(groupFields(schema));
  const isBinary = (f: Field) => BINARY.has(f.key) || (f as any).type === 'bool';

  /** Что уедет роботу при сохранении. Оператор меняет два поля, а уезжает весь
      набор — 05.08.2026 вместе с qty и avg_max молча слетели exit_only и
      allow_short, и заметили это только по открытым позициям. */
  let pending = $derived.by(() => {
    if (!baseline) return [] as { key: string; from: any; to: any }[];
    const out: { key: string; from: any; to: any }[] = [];
    for (const k of Object.keys(values)) {
      const a = baseline[k], b = values[k];
      if (String(a ?? '') !== String(b ?? '')) out.push({ key: k, from: a, to: b });
    }
    return out;
  });
  const shown = (v: any) => (v === true ? 'да' : v === false ? 'нет' : v === undefined ? '—' : String(v));
  let flash = $state<string | null>(null);      // ключ, чей перевод только что изменился

  const num = (v: any) => (Number.isFinite(Number(v)) ? Number(v) : 0);
  const isOn = (k: string) => {
    const v = values[k];
    return v === true || v === 1 || v === '1' || v === 'true';
  };

  function clamp(f: Field, v: number): number {
    if (f.min != null) v = Math.max(f.min, v);
    if (f.max != null) v = Math.min(f.max, v);
    return v;
  }
  function bump(f: Field, dir: 1 | -1) {
    if (disabledKeys.includes(f.key)) return;
    values[f.key] = clamp(f, num(values[f.key]) + dir * stepFor(f));
    flash = f.key;
    setTimeout(() => { if (flash === f.key) flash = null; }, 420);
  }
  function toggle(f: Field) {
    if (disabledKeys.includes(f.key)) return;
    values[f.key] = isOn(f.key) ? 0 : 1;
    flash = f.key;
    setTimeout(() => { if (flash === f.key) flash = null; }, 420);
  }

  // Зажатая стрелка повторяется: дотянуть 0 -> 200 отдельными кликами нельзя.
  let held: ReturnType<typeof setInterval> | null = null;
  function hold(f: Field, dir: 1 | -1) {
    bump(f, dir);
    const start = setTimeout(() => { held = setInterval(() => bump(f, dir), 70); }, 380);
    held = start as unknown as ReturnType<typeof setInterval>;
  }
  function release() { if (held) { clearTimeout(held); clearInterval(held); held = null; } }

  /** Подпись под значением: перевод в единицы инструмента, иначе диапазон. */
  function translate(f: Field): string {
    const h = helpFor(strategyId, f.key);
    const live = h?.live?.(num(values[f.key]), ctx);
    if (live) return live;
    if (isBinary(f)) return isOn(f.key) ? 'включено' : 'выключено';
    // Значение ВНЕ схемы — не молчим. Живой робот вполне может нести avg_max=17
    // при схеме 1…10 (оператор выставил руками), и подпись «допустимо 1…10» рядом
    // с числом 17 читается как «всё в порядке», хотя перебор такую ось не тронет.
    const v = num(values[f.key]);
    if (f.min != null && f.max != null && (v < f.min || v > f.max))
      return `${v} — ВНЕ схемы (${f.min}…${f.max}); робот работает, перебор эту ось не тронет`;
    if (f.min != null && f.max != null) return `допустимо ${f.min}…${f.max}`;
    return h?.short ?? '';
  }
  /** Сколько значений даст ось. Оператор задаёт от/до/шаг, а в голове держит
      «во сколько раз вырастет сетка» — показываем прямо здесь. */
  function rangeCount(f: Field): string {
    const r = ranges?.[f.key];
    if (!r) return '';
    const a = Number(r.from), b = Number(r.to), s = Math.max(1, Number(r.step) || 1);
    if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return 'ось выключена';
    const n = Math.floor((b - a) / s) + 1;
    return n <= 1 ? 'одно значение' : `${n} значений`;
  }
  /** Значение робота вне диапазона схемы — законное состояние (оператор выставил
      руками), но молчать о нём нельзя: перебор такую ось не тронет. */
  function outside(f: Field): boolean {
    if (isBinary(f) || f.min == null || f.max == null) return false;
    const v = num(values[f.key]);
    return v < (f.min as number) || v > (f.max as number);
  }
  const tip = (f: Field) => {
    const h = helpFor(strategyId, f.key);
    return [h?.title, h?.what || h?.short].filter(Boolean).join(' — ') || f.label;
  };
</script>

<div class="pp" class:wide>
  {#if pending.length}
    <div class="pp-diff" role="status">
      <b>Уедет роботу:</b>
      {#each pending as c (c.key)}<span class="pp-chg"><code>{c.key}</code> {shown(c.from)} → <b>{shown(c.to)}</b></span>{/each}
    </div>
  {/if}
  {#each groups as { group, fields } (group.id)}
    <section class="pp-g">
      <header class="pp-gh">
        <h4>{group.title}</h4>
        <span>{group.note}</span>
      </header>

      {#each fields as f (f.key)}
        {@const off = disabledKeys.includes(f.key) || f.type === 'text'}
        <div class="pp-row" class:bound={BOUNDING.has(f.key)} class:off>
          <div class="pp-name">
            <span class="pp-label" title={tip(f)}>{f.label}</span>
            <code class="pp-key">{f.key}</code>{#if (f as any).extra}<span class="pp-tag"
              title="служебный флаг: в схему стратегии не входит и в перебор не идёт, но на робота влияет">служебный</span>{/if}
          </div>

          {#if isBinary(f)}
            <button class="pp-sw" class:on={isOn(f.key)} disabled={off}
                    role="switch" aria-checked={isOn(f.key)} aria-label={f.label}
                    onclick={() => toggle(f)}>
              <span class="pp-knob"></span>
              <span class="pp-swtxt">{isOn(f.key) ? 'да' : 'нет'}</span>
            </button>
          {:else if off}
            <div class="pp-val"><output class="pp-num">{values[f.key] ?? '—'}</output></div>
          {:else}
            <div class="pp-val">
              <button class="pp-arr" aria-label="уменьшить {f.label}"
                      onmousedown={() => hold(f, -1)} onmouseup={release} onmouseleave={release}
                      onclick={(e) => e.preventDefault()}>−</button>
              <input class="pp-num" type="number" inputmode="numeric"
                     min={f.min} max={f.max} aria-label={f.label}
                     bind:value={values[f.key]} />
              <button class="pp-arr" aria-label="увеличить {f.label}"
                      onmousedown={() => hold(f, 1)} onmouseup={release} onmouseleave={release}
                      onclick={(e) => e.preventDefault()}>+</button>
            </div>
          {/if}

          <p class="pp-tr" class:flash={flash === f.key}
             class:outside={outside(f)}>{translate(f)}</p>

          {#if ranges?.[f.key] && !off && !isBinary(f)}
            <!-- Диапазон перебора той же строки. Пустой = ось не перебирается,
                 значение берётся сверху: так видно, ЧТО именно уйдёт в сетку. -->
            <div class="pp-rng">
              <label><span>от</span><input type="number" min={f.min} max={f.max}
                     bind:value={ranges[f.key].from} /></label>
              <label><span>до</span><input type="number" min={f.min} max={f.max}
                     bind:value={ranges[f.key].to} /></label>
              <label><span>шаг</span><input type="number" min="1"
                     bind:value={ranges[f.key].step} /></label>
              <span class="pp-rng-n">{rangeCount(f)}</span>
            </div>
          {/if}
        </div>
      {/each}
    </section>
  {/each}
</div>

<style>
  /* Панель — вставка приборного вида: глубже и синее общего фона стенда, чтобы
     читалась как отдельный инструмент, а не как ещё одна таблица. */
  .pp { --ink: #0a1020; --rail: #1c2a46; --text: #dfe8f7; --mute: #7c8cab;
        --live: #46c46a; --bound: #f0a83c;
        background: var(--ink); padding: 2px 0 10px; }

  /* Что уедет роботу. Показываем ДО сохранения и по всем ключам, а не только по
     тем, что оператор трогал руками. */
  .pp-diff { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px;
    margin: 8px 10px; padding: 8px 11px; background: #1d1607; border: 1px solid #6b5220;
    border-radius: 4px; font: 400 12px/1.5 system-ui, sans-serif; color: #f0d9a8; }
  .pp-diff b { color: #ffe9bd; }
  .pp-chg code { font: 400 12px/1 ui-monospace, Consolas, monospace; color: #d8b877; }
  .pp-tag { margin-left: 6px; padding: 1px 5px; border: 1px solid #4a3a6a; border-radius: 3px;
    font: 600 9px/1.5 system-ui, sans-serif; text-transform: uppercase; letter-spacing: .07em;
    color: #a68fd0; cursor: help; }

  .pp-g { border-top: 1px solid var(--rail); }
  .pp-g:first-child { border-top: 0; }
  /* Широкий лист: группы — колонки. Одна вертикальная кишка на 19 параметров
     нечитаема, а группы независимы и прекрасно встают рядом. */
  .pp.wide { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 0 18px; align-items: start; padding: 0 0 4px; }
  .pp.wide .pp-diff { grid-column: 1 / -1; }
  .pp.wide .pp-g { border-top: 0; border-bottom: 1px solid var(--rail); }
  .pp.wide .pp-row { padding-left: 4px; padding-right: 4px; }
  .pp-gh { display: flex; align-items: baseline; gap: 10px; padding: 12px 14px 6px; }
  .pp-gh h4 { margin: 0; font: 600 12px/1 system-ui, sans-serif; letter-spacing: .14em;
    text-transform: uppercase; color: var(--text); }
  .pp-gh span { font: 400 11px/1 system-ui, sans-serif; color: var(--mute); }

  /* Строка: имя слева, крупное значение справа, перевод — во всю ширину снизу.
     Перевод не жмётся в остаток строки: он и есть то, что надо прочитать. */
  .pp-row { display: grid; grid-template-columns: 1fr auto; align-items: center;
    gap: 4px 12px; padding: 8px 14px; border-top: 1px solid #131c30; }
  .pp-row.off { opacity: .55; }
  /* Ограничивающие параметры помечены слева: в одном столбце соседствуют
     «добирай больше» и «стой дольше», на глаз они неразличимы. */
  .pp-row.bound { box-shadow: inset 3px 0 0 -1px var(--bound); }

  .pp-name { min-width: 0; }
  .pp-label { display: block; font: 600 14px/1.25 system-ui, sans-serif; color: var(--text);
    cursor: help; }
  .pp-key { font: 400 10px/1.4 ui-monospace, Consolas, monospace; color: #4d5f80; }

  .pp-val { display: flex; align-items: center; gap: 2px; }
  /* Крупные стрелки: цель мыши 34px, а не 10px-спиннер браузера. */
  .pp-arr { width: 34px; height: 34px; flex: none; background: #101a2e;
    border: 1px solid var(--rail); color: #9fb4d6; font: 300 20px/1 system-ui, sans-serif;
    cursor: pointer; user-select: none; }
  .pp-arr:first-child { border-radius: 4px 0 0 4px; }
  .pp-arr:last-child { border-radius: 0 4px 4px 0; }
  .pp-arr:hover { background: #16233c; color: #dceaff; border-color: #2c4570; }
  .pp-arr:active { background: #1d2f4f; }
  /* Значение — машинным начертанием с табличными цифрами: при шаге стрелкой
     разряды не должны прыгать. */
  .pp-num { width: 96px; height: 34px; text-align: center; background: #060b16;
    border: 1px solid var(--rail); border-left: 0; border-right: 0; color: #ffffff;
    font: 600 22px/1 ui-monospace, Consolas, monospace; font-variant-numeric: tabular-nums;
    -moz-appearance: textfield; }
  .pp-num::-webkit-outer-spin-button, .pp-num::-webkit-inner-spin-button {
    -webkit-appearance: none; margin: 0; }
  .pp-num:focus { outline: 2px solid #3d6ea8; outline-offset: -2px; }
  output.pp-num { display: grid; place-items: center; border: 1px solid var(--rail); }

  .pp-sw { display: flex; align-items: center; gap: 9px; height: 34px; padding: 0 12px 0 6px;
    background: #060b16; border: 1px solid var(--rail); border-radius: 4px; cursor: pointer; }
  .pp-knob { width: 34px; height: 18px; border-radius: 9px; background: #1a2740;
    border: 1px solid #2b3f63; position: relative; transition: background .14s; }
  .pp-knob::after { content: ''; position: absolute; top: 2px; left: 2px; width: 12px; height: 12px;
    border-radius: 50%; background: #7c8cab; transition: transform .14s, background .14s; }
  .pp-sw.on .pp-knob { background: #14361f; border-color: #2f7a45; }
  .pp-sw.on .pp-knob::after { transform: translateX(16px); background: var(--live); }
  .pp-swtxt { font: 600 14px/1 system-ui, sans-serif; color: var(--mute); }
  .pp-sw.on .pp-swtxt { color: var(--live); }
  .pp-sw:disabled { cursor: default; }

  /* Подпись экрана: параметр, сказанный в единицах инструмента. */
  .pp-tr { grid-column: 1 / -1; margin: 0; font: 400 12px/1.4 system-ui, sans-serif;
    color: var(--mute); }
  .pp-row.bound .pp-tr { color: #c79b5e; }
  .pp-tr.outside { color: #f0a83c; }
  .pp-tr.flash { color: var(--text); }

  /* Диапазон перебора: подчинён значению, но читаем. Поля узкие — числа тут
     двух-трёхзначные, а широкие поля создают ложное ощущение главного. */
  .pp-rng { grid-column: 1 / -1; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    margin-top: 4px; padding-top: 6px; border-top: 1px dashed #16233c; }
  .pp-rng label { display: flex; align-items: center; gap: 5px; }
  .pp-rng span { font: 600 11px/1 system-ui, sans-serif; color: var(--mute); }
  .pp-rng input { width: 66px; height: 26px; text-align: center; background: #060b16;
    border: 1px solid var(--rail); border-radius: 3px; color: #cfe2ff;
    font: 500 13px/1 ui-monospace, Consolas, monospace; font-variant-numeric: tabular-nums; }
  .pp-rng input:focus { outline: 2px solid #3d6ea8; outline-offset: -2px; }
  .pp-rng-n { margin-left: auto; font: 600 11px/1 system-ui, sans-serif; color: #7aa2d0; }

  @media (prefers-reduced-motion: reduce) {
    .pp-knob, .pp-knob::after { transition: none; }
    .pp-tr.flash { color: var(--mute); }
  }
  @media (max-width: 620px) {
    .pp-row { grid-template-columns: 1fr; }
    .pp-val, .pp-sw { justify-self: start; }
  }
</style>
