<!-- Умные ручные заявки: что заявка делает, чем сработает, что произойдёт после
     взвода и что уже стоит. Один экран вместо восьми полей без подписей.

     Взвод только в два клика: сначала фраза «что произойдёт», потом кнопка. -->
<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { fetchWithAuth } from '$lib/fetch-auth';
  import { downloadCSV } from '$lib/csv';
  import { fmtPrice } from '$lib/format';
  import { smartOrdersStore, type SmartOrder } from '$lib/stores/smart-orders.svelte';
  import SmartOrderSchematic from './SmartOrderSchematic.svelte';
  import {
    KINDS, KIND_BY_ID, COMMON_FACTS, STATUS_RU, conditionText, fmtWhen, fmtPts,
    fmtRub, ocoFact, preview, tillFact, type Kind, type Side,
  } from '$lib/smart-order-help';

  let { symbol = '' }: { symbol?: string } = $props();

  let kind = $state<Kind>('sl');
  let side = $state<Side>('sell');
  let qty = $state(1);
  let code = $state('');
  let trigger = $state('');
  let trailOffset = $state('');
  let slOffset = $state('');          // защитный стоп после входа, пункты (0 = без стопа)
  let tpOffset = $state('');          // тейк после входа, пункты (0 = без тейка)
  let watchId = $state('');
  let childPrice = $state('');
  let ocoGroup = $state('');
  let tillLocal = $state('');          // datetime-local, пусто = бессрочно
  let confirming = $state(false);
  let msg = $state('');
  let msgKind = $state<'ok' | 'err'>('ok');

  let tick = $state<{ last: number; bid: number; ask: number } | null>(null);
  let pointValue = $state(0);
  let timers: Array<ReturnType<typeof setInterval>> = [];
  let unsub: (() => void) | null = null;

  const meta = $derived(KIND_BY_ID[kind]);
  const price = $derived(tick?.last || 0);
  const orders = $derived(smartOrdersStore.all);
  const armed = $derived(orders.filter((o) => o.status === 'armed'));
  const history = $derived(orders.filter((o) => o.status !== 'armed').slice(-30).reverse());
  const goodTillMs = $derived(tillLocal ? new Date(tillLocal).getTime() : 0);

  const pv = $derived.by(() => {
    const p = preview({
      kind, side, qty, code,
      trigger: parseFloat(trigger) || 0,
      trailOffset: parseFloat(trailOffset) || 0,
      watchId: watchId.trim(), childPrice: parseFloat(childPrice) || 0,
      price, pointValue,
    });
    if (!p.error && goodTillMs && goodTillMs <= Date.now()) {
      return { ...p, error: 'Срок «действует до» уже прошёл.' };
    }
    return p;
  });

  // Ценовая рейка: где сейчас цена, где сработает и сколько между ними.
  const rail = $derived.by(() => {
    const t = parseFloat(trigger) || 0;
    if (kind === 'on_fill' || !(price > 0) || !(t > 0)) return null;
    const lo = Math.min(price, t), hi = Math.max(price, t);
    const pad = Math.max((hi - lo) * 0.45, hi * 0.0004);
    const top = hi + pad, bot = lo - pad;
    const y = (v: number) => 12 + (top - v) / (top - bot) * 116;
    return { yPrice: y(price), yTrig: y(t), trig: t, gap: Math.abs(t - price),
             above: t > price };
  });

  async function loadTick() {
    if (!code) { tick = null; return; }
    try {
      const r = await fetchWithAuth(`/api/v1/quik/tick/${encodeURIComponent(code)}`);
      tick = r.ok ? await r.json() : null;
    } catch { /* следующий тик перезапросит */ }
  }

  async function loadPointValue() {
    try {
      const r = await fetchWithAuth('/api/v1/quik/params');
      if (!r.ok) return;
      const d = await r.json();
      const row = (d?.rows || []).find((x: any) => x.code === code);
      const step = Number(row?.price_step || 0), cost = Number(row?.step_cost || 0);
      // ₽ за пункт. Без него считаем в пунктах и рублями НЕ врём.
      pointValue = step > 0 && cost > 0 ? cost / step : 0;
    } catch { pointValue = 0; }
  }

  async function arm() {
    msg = '';
    const body = {
      kind, code, side, qty: Math.floor(qty),
      trigger_price: parseFloat(trigger) || 0,
      trail_offset: parseFloat(trailOffset) || 0,
      sl_offset: parseFloat(slOffset) || 0,
      tp_offset: parseFloat(tpOffset) || 0,
      watch_client_id: watchId.trim(),
      child_price: parseFloat(childPrice) || 0,
      oco_group: ocoGroup.trim(),
      good_till_ms: goodTillMs,
    };
    try {
      const res = await fetchWithAuth('/api/v1/quik/smart-orders', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) { msgKind = 'err'; msg = d?.detail || `HTTP ${res.status}`; return; }
      msgKind = 'ok';
      msg = `Заявка ${d.so_id} взведена. Сторож следит.`;
      trigger = ''; trailOffset = ''; slOffset = ''; tpOffset = ''; watchId = ''; childPrice = '';
      confirming = false;
      await smartOrdersStore.refresh();
    } catch (e: any) { msgKind = 'err'; msg = e?.message || 'ошибка'; }
  }

  async function cancel(soId: string) {
    try {
      const res = await fetchWithAuth(`/api/v1/quik/smart-orders/${soId}`, { method: 'DELETE' });
      if (!res.ok) { msgKind = 'err'; msg = (await res.json().catch(() => ({})))?.detail || `HTTP ${res.status}`; }
      await smartOrdersStore.refresh();
    } catch { /* список обновится опросом */ }
  }

  // Перевзвести: та же заявка ещё раз. Нужно, когда дочерняя простая заявка не
  // дожила до исполнения (QUIK чистит неисполненные на границе сессии).
  function rearm(o: SmartOrder) {
    kind = o.kind; side = o.side; qty = o.qty; code = o.code;
    trigger = o.trigger_price ? String(o.trigger_price) : '';
    trailOffset = o.trail_offset ? String(o.trail_offset) : '';
    slOffset = o.sl_offset ? String(o.sl_offset) : '';
    tpOffset = o.tp_offset ? String(o.tp_offset) : '';
    watchId = o.watch_client_id || '';
    childPrice = o.child_price ? String(o.child_price) : '';
    ocoGroup = o.oco_group || '';
    confirming = false;
    msgKind = 'ok';
    msg = 'Параметры подставлены — проверьте и взведите заново.';
    document.querySelector('.so-form')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  $effect(() => { if (code) { loadTick(); loadPointValue(); } });
  // Смена стороны/типа меняет смысл введённого уровня — подтверждение сбрасываем.
  $effect(() => { void kind; void side; void trigger; void qty; confirming = false; });

  onMount(() => {
    code = (symbol || '').split('@')[0];
    unsub = smartOrdersStore.subscribe(2000);
    timers = [setInterval(loadTick, 2000)];
  });
  onDestroy(() => { unsub?.(); for (const t of timers) clearInterval(t); });
</script>

<div class="so">
  <!-- 1. Тип заявки -->
  <div class="so-kinds">
    {#each KINDS as k}
      <button class="so-kind" class:on={kind === k.id} style="--accent:{k.color}"
              onclick={() => kind = k.id}>
        <span class="so-kind-tag">{k.short}</span>
        <span class="so-kind-name">{k.name}</span>
        <span class="so-kind-ess">{k.essence}</span>
      </button>
    {/each}
  </div>

  <div class="so-main">
    <!-- 2. Как это работает -->
    <section class="so-explain">
      <div class="so-h">Как это работает</div>
      <SmartOrderSchematic {kind} {side} trigger={parseFloat(trigger) || 0}
                           trailOffset={parseFloat(trailOffset) || 0} {price} watchId={watchId} />
      <ol class="so-algo">
        {#each meta.algorithm as step}<li>{step}</li>{/each}
      </ol>
      <div class="so-h">Условия и хранение</div>
      <dl class="so-facts">
        {#each COMMON_FACTS as f}
          <dt class:warn={f.warn}>{f.label}</dt>
          <dd class:warn={f.warn}>{f.text}</dd>
        {/each}
        <dt>Срок</dt><dd>{tillFact(goodTillMs)}</dd>
        <dt>Связка</dt><dd>{ocoFact(ocoGroup.trim())}</dd>
      </dl>
    </section>

    <!-- 3. Параметры -->
    <section class="so-form">
      <div class="so-h">Параметры</div>

      <div class="so-sides">
        <button class="so-side buy" class:on={side === 'buy'} onclick={() => side = 'buy'}>Купить</button>
        <button class="so-side sell" class:on={side === 'sell'} onclick={() => side = 'sell'}>Продать</button>
      </div>

      <div class="so-fields">
        <label class="so-f">
          <span>Инструмент</span>
          <input class="so-in" bind:value={code} placeholder="RIU6" spellcheck="false" />
        </label>
        <label class="so-f">
          <span>Контрактов</span>
          <input class="so-in" type="number" min="1" step="1" bind:value={qty} />
        </label>
        {#each meta.fields as f}
          <label class="so-f">
            <span>{f.label}</span>
            {#if f.key === 'trigger_price'}
              <input class="so-in" type="number" step="any" bind:value={trigger} placeholder="0" />
            {:else if f.key === 'trail_offset'}
              <input class="so-in" type="number" step="any" bind:value={trailOffset} placeholder="0" />
            {:else if f.key === 'sl_offset'}
              <input class="so-in" type="number" step="any" min="0" bind:value={slOffset} placeholder="0 — без стопа" />
            {:else if f.key === 'tp_offset'}
              <input class="so-in" type="number" step="any" min="0" bind:value={tpOffset} placeholder="0 — без тейка" />
            {:else if f.key === 'watch_client_id'}
              <input class="so-in text" bind:value={watchId} placeholder="client_id" spellcheck="false" />
            {:else}
              <input class="so-in" type="number" step="any" bind:value={childPrice} placeholder="по рынку" />
            {/if}
            <em>{f.hint}</em>
          </label>
        {/each}
      </div>

      {#if rail}
        <!-- Ценовая рейка: расстояние до срабатывания видно глазом, а не текстом. -->
        <div class="so-rail" style="--accent:{meta.color}">
          <svg viewBox="0 0 200 140" role="img" aria-label="Расстояние до срабатывания">
            <line class="axis" x1="26" y1="10" x2="26" y2="130" />
            <line class="span" x1="26" y1={rail.yPrice} x2="26" y2={rail.yTrig} />
            <line class="now" x1="14" y1={rail.yPrice} x2="86" y2={rail.yPrice} />
            <text class="nowt" x="92" y={rail.yPrice + 4}>сейчас {price.toLocaleString('ru-RU')}</text>
            <line class="trg" x1="14" y1={rail.yTrig} x2="86" y2={rail.yTrig} />
            <text class="trgt" x="92" y={rail.yTrig + 4}>сработает {rail.trig.toLocaleString('ru-RU')}</text>
            <text class="gapt" x="30" y={(rail.yPrice + rail.yTrig) / 2 + 4}>
              {fmtPts(rail.gap)}{pointValue ? ' · ' + fmtRub(rail.gap * pointValue * Math.max(1, qty)) : ''}
            </text>
          </svg>
        </div>
      {/if}

      <div class="so-fields">
        <label class="so-f wide">
          <span>Действует до</span>
          <input class="so-in text" type="datetime-local" bind:value={tillLocal} />
          <em>пусто — бессрочно</em>
        </label>
        <label class="so-f wide">
          <span>Связка OCO</span>
          <input class="so-in text" bind:value={ocoGroup} placeholder="напр. bracket-1" spellcheck="false" />
          <em>одинаковое имя = сработала одна, остальные снялись</em>
        </label>
      </div>
    </section>
  </div>

  <!-- 4. Что произойдёт -->
  <section class="so-preview" class:bad={!!pv.error} style="--accent:{meta.color}">
    <div class="so-h">Что произойдёт</div>
    {#if pv.error}
      <p class="so-sent bad">{pv.error}</p>
    {:else}
      <p class="so-sent">{pv.sentence}</p>
      {#if pv.distance}<p class="so-dist">{pv.distance}</p>{/if}
    {/if}
    <div class="so-act">
      {#if confirming && !pv.error}
        <span class="so-ask">Взводим?</span>
        <button class="so-btn go" onclick={arm}>Да, взвести</button>
        <button class="so-btn" onclick={() => confirming = false}>Отмена</button>
      {:else}
        <button class="so-btn arm" disabled={!!pv.error} onclick={() => confirming = true}>
          Взвести заявку
        </button>
      {/if}
      {#if msg}<span class="so-msg" class:err={msgKind === 'err'}>{msg}</span>{/if}
    </div>
  </section>

  <!-- 5. Взведённые -->
  <section class="so-list">
    <div class="so-h">
      Взведённые заявки ({armed.length})
      <button class="so-csv" onclick={() => downloadCSV(orders, 'smart_orders.csv')}
              title="выгрузить книгу в CSV">CSV</button>
      {#if smartOrdersStore.error}<span class="so-stale">книга не обновилась: {smartOrdersStore.error}</span>{/if}
    </div>
    {#if !armed.length}
      <p class="so-empty">Взведённых заявок нет. Сторож ничего не ждёт.</p>
    {/if}
    {#each armed as o (o.so_id)}
      <article class="so-card" style="--accent:{KIND_BY_ID[o.kind].color}">
        <div class="so-c-head">
          <span class="so-c-tag">{KIND_BY_ID[o.kind].short}</span>
          <b class="so-c-code">{o.code}</b>
          <span class="so-c-side" class:buy={o.side === 'buy'}>{o.side === 'buy' ? 'ПОКУПКА' : 'ПРОДАЖА'} {o.qty}</span>
          <span class="so-c-cond">{conditionText(o)}</span>
          <span class="so-c-sp"></span>
          <span class="so-c-status">{STATUS_RU[o.status] ?? o.status}</span>
          <button class="so-btn sm" onclick={() => cancel(o.so_id)}>Снять</button>
        </div>
        {#if o.kind === 'trail_tp'}
          {@const fire = o.side === 'buy' ? o.peak + o.trail_offset : o.peak - o.trail_offset}
          <div class="so-c-track" class:active={o.activated}>
            {#if o.activated}
              ● АКТИВНА · слежение от пика <b>{fmtPrice(o.peak)}</b>
              · сделка при цене {o.side === 'buy' ? '≥' : '≤'}
              <b class="fire">{fmtPrice(fire)}</b>
              (откат {o.trail_offset} п. от пика)
            {:else}
              ○ ждёт пробоя уровня <b>{fmtPrice(o.trigger_price)}</b>,
              затем встанет в слежение и выкупит на откате {o.trail_offset} п.
            {/if}
            {#if o.sl_offset || o.tp_offset}
              <div class="so-c-sl">после входа автоматически встанут:
                {#if o.sl_offset}<b>стоп {o.sl_offset} п.</b>{/if}{#if o.sl_offset && o.tp_offset} и {/if}{#if o.tp_offset}<b>тейк {o.tp_offset} п.</b>{/if}
                от цены сделки{#if o.sl_offset && o.tp_offset}, в одной связке — сработает один, второй снимется{/if}</div>
            {/if}
          </div>
        {/if}
        <div class="so-c-facts">
          <span>id {o.so_id}</span>
          <span>взведена {fmtWhen(o.created_ms)}</span>
          <span>{o.good_till_ms ? 'до ' + fmtWhen(o.good_till_ms) : 'бессрочно'}</span>
          {#if o.oco_group}<span>связка {o.oco_group}</span>{/if}
          <span>хранится на STL · сторож раз в секунду</span>
        </div>
        {#if o.note}<div class="so-c-note">{o.note}</div>{/if}
      </article>
    {/each}

    {#if history.length}
      <details class="so-hist">
        <summary>Отработавшие и снятые ({history.length})</summary>
        {#each history as o (o.so_id)}
          <article class="so-card done" style="--accent:{KIND_BY_ID[o.kind].color}">
            <div class="so-c-head">
              <span class="so-c-tag">{KIND_BY_ID[o.kind].short}</span>
              <b class="so-c-code">{o.code}</b>
              <span class="so-c-side" class:buy={o.side === 'buy'}>{o.side === 'buy' ? 'ПОКУПКА' : 'ПРОДАЖА'} {o.qty}</span>
              <span class="so-c-cond">{conditionText(o)}</span>
              <span class="so-c-sp"></span>
              <span class="so-c-status" class:warn={o.status === 'orphaned' || o.status === 'error'}>
                {STATUS_RU[o.status] ?? o.status}
              </span>
              {#if o.status === 'orphaned' || o.status === 'expired'}
                <button class="so-btn sm" onclick={() => rearm(o)}>Перевзвести</button>
              {/if}
            </div>
            <div class="so-c-facts">
              {#if o.fired_ms}<span>сработала {fmtWhen(o.fired_ms)}</span>{/if}
              {#if o.fired_client_id}<span>заявка {o.fired_client_id}</span>{/if}
            </div>
            {#if o.note}<div class="so-c-note">{o.note}</div>{/if}
          </article>
        {/each}
      </details>
    {/if}
  </section>
</div>

<style>
  /* Ширину ограничиваем: на широком мониторе строка объяснения иначе
     растягивается на полтора метра и перестаёт читаться. */
  .so { padding: 10px 14px 16px; color: #d7dbe8; font-size: 12px; overflow: auto;
        height: 100%; max-width: 1180px; }
  .so-h {
    display: flex; align-items: center; gap: 8px;
    font-size: 10px; letter-spacing: .16em; text-transform: uppercase;
    color: #8a90a8; margin: 0 0 6px;
  }
  .so-h::after { content: ''; flex: 1; height: 1px; background: #2d2d4a; }

  /* выбор типа */
  .so-kinds { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 8px; }
  .so-kind {
    text-align: left; background: #14142a; border: 1px solid #2d2d4a; border-left: 3px solid var(--accent);
    padding: 8px 10px; cursor: pointer; color: #b9bfd4; display: grid; gap: 3px;
  }
  .so-kind:hover { background: #1b1b34; }
  .so-kind.on { background: #1b1b34; border-color: var(--accent); color: #e8e8f0; }
  .so-kind-tag { font: 600 10px/1 Consolas, monospace; letter-spacing: .1em; color: var(--accent); }
  .so-kind-name { font-size: 14px; color: #e8e8f0; }
  .so-kind-ess { font-size: 11px; color: #8a90a8; line-height: 1.35; }

  .so-main { display: grid; grid-template-columns: minmax(300px, 1fr) minmax(300px, 360px); gap: 20px; margin-top: 14px; }
  @media (max-width: 900px) { .so-main { grid-template-columns: 1fr; } }

  /* объяснение */
  .so-algo { margin: 10px 0 14px; padding-left: 18px; line-height: 1.55; }
  .so-algo li { margin-bottom: 3px; }
  .so-facts { margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: 3px 10px; line-height: 1.45; }
  .so-facts dt { color: #8a90a8; white-space: nowrap; }
  .so-facts dd { margin: 0; color: #b9bfd4; }
  .so-facts dt.warn, .so-facts dd.warn { color: #e0a53c; }

  /* параметры: крупно */
  .so-sides { display: flex; gap: 6px; margin-bottom: 10px; }
  .so-side {
    flex: 1; padding: 9px 0; font-size: 13px; letter-spacing: .06em; cursor: pointer;
    background: #14142a; border: 1px solid #2d2d4a; color: #8a90a8;
  }
  .so-side.on.buy { background: #123a22; border-color: #2ecc71; color: #7ef0a6; }
  .so-side.on.sell { background: #3a1616; border-color: #ff6b5a; color: #ff9d90; }
  .so-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .so-f { display: grid; gap: 2px; }
  .so-f.wide { grid-column: 1 / -1; }
  .so-f > span { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: #8a90a8; }
  .so-f > em { font-size: 10px; color: #6f7590; font-style: normal; }
  .so-in {
    background: #0e0e1e; border: 1px solid #2d2d4a; color: #e8e8f0;
    font: 20px/1.2 Consolas, 'Cascadia Mono', monospace; font-variant-numeric: tabular-nums;
    padding: 5px 8px; width: 100%;
  }
  .so-in.text { font-size: 13px; }
  .so-in:focus { outline: none; border-color: #4a4a7a; background: #12122a; }

  /* ценовая рейка */
  .so-rail { margin: 12px 0; }
  .so-rail svg { width: 100%; height: auto; max-height: 150px; }
  .axis { stroke: #2d2d4a; stroke-width: 1; }
  .span { stroke: var(--accent); stroke-width: 3; opacity: .35; }
  .now { stroke: #e8e8f0; stroke-width: 1.4; }
  .trg { stroke: var(--accent); stroke-width: 1.6; stroke-dasharray: 5 3; }
  .nowt { fill: #e8e8f0; font: 12px Consolas, monospace; }
  .trgt { fill: var(--accent); font: 12px Consolas, monospace; }
  .gapt { fill: #b9bfd4; font: 11px Consolas, monospace; }

  /* что произойдёт */
  .so-preview {
    margin-top: 14px; padding: 10px 12px; background: #14142a;
    border: 1px solid #2d2d4a; border-left: 3px solid var(--accent);
  }
  .so-preview.bad { border-left-color: #f44336; }
  .so-sent { margin: 0 0 4px; font-size: 15px; line-height: 1.45; color: #e8e8f0; }
  .so-sent.bad { color: #ff9d90; font-size: 13px; }
  .so-dist { margin: 0; color: #8a90a8; }
  .so-act { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
  .so-ask { color: #e0a53c; }
  .so-btn {
    background: #1a1a2e; border: 1px solid #2d2d4a; color: #b9bfd4;
    padding: 7px 14px; cursor: pointer; font-size: 12px;
  }
  .so-btn:hover:not(:disabled) { color: #e8e8f0; border-color: #4a4a7a; }
  .so-btn:disabled { opacity: .45; cursor: not-allowed; }
  .so-btn.arm { border-color: var(--accent); color: #e8e8f0; }
  .so-btn.go { background: #123a22; border-color: #2ecc71; color: #7ef0a6; }
  .so-btn.sm { padding: 3px 9px; font-size: 11px; }
  .so-msg { color: #7ef0a6; }
  .so-msg.err { color: #ff9d90; }

  /* список */
  .so-list { margin-top: 16px; }
  .so-empty { color: #6f7590; margin: 4px 0 0; }
  .so-csv { background: none; border: 1px solid #2d2d4a; color: #8a90a8; font-size: 10px; padding: 1px 6px; cursor: pointer; }
  .so-stale { color: #e0a53c; text-transform: none; letter-spacing: 0; }
  .so-card {
    background: #14142a; border: 1px solid #2d2d4a; border-left: 3px solid var(--accent);
    padding: 7px 10px; margin-bottom: 6px;
  }
  .so-card.done { opacity: .72; }
  .so-c-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .so-c-tag { font: 600 10px/1 Consolas, monospace; letter-spacing: .1em; color: var(--accent); }
  .so-c-code { font-size: 14px; color: #e8e8f0; }
  .so-c-side { color: #ff9d90; font-size: 11px; letter-spacing: .06em; }
  .so-c-side.buy { color: #7ef0a6; }
  .so-c-cond { color: #b9bfd4; font-family: Consolas, monospace; }
  .so-c-sp { flex: 1; }
  .so-c-track { margin: 6px 0 2px; padding: 5px 9px; border-radius: 5px; font-size: 12px;
    color: #9aa0b4; background: #14142400; border: 1px dashed #33335a; }
  .so-c-track.active { color: #d7c4ff; background: #2a1a4a55; border: 1px solid #7a5cff88; }
  .so-c-track b { color: #cfe; }
  .so-c-track.active b { color: #e6d8ff; }
  .so-c-track .fire { color: #b98cff; }
  .so-c-sl { margin-top: 3px; color: #9aa0b4; font-size: 11px; }
  .so-c-sl b { color: #ffb3a7; }
  .so-c-status { color: #e0a53c; font-size: 11px; }
  .so-c-status.warn { color: #ff9d90; }
  .so-c-facts { display: flex; gap: 12px; flex-wrap: wrap; color: #6f7590; font-size: 11px; margin-top: 3px; }
  .so-c-note { color: #8a90a8; font-size: 11px; margin-top: 3px; }
  .so-hist { margin-top: 10px; }
  .so-hist summary { cursor: pointer; color: #8a90a8; font-size: 11px; margin-bottom: 6px; }
</style>
