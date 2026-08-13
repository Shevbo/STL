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
    KINDS, KIND_BY_ID, COMMON_FACTS, STATUS_RU, codeSuggestions, conditionText,
    closingSide, fmtWhen, fmtPts, fmtRub, manualPositions, ocoFact, preview,
    shortCodes, tillFact, type Kind, type OpenPos, type Side,
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
  // Подтягивающая после сделки. Со стопом НЕСОВМЕСТИМА (движок вернёт 422),
  // поэтому в форме это переключатель, а не третье независимое поле: сработает
  // ближний из двух, дальний останется взведён и откроет обратную позицию.
  let trailAfter = $state('');
  let afterMode = $state<'sl' | 'trail'>('sl');
  let watchId = $state('');
  let childPrice = $state('');
  let ocoGroup = $state('');
  let tillLocal = $state('');          // datetime-local, пусто = бессрочно
  let confirming = $state(false);
  let msg = $state('');
  let msgKind = $state<'ok' | 'err'>('ok');

  let tick = $state<{ last: number; bid: number; ask: number } | null>(null);
  let pointValue = $state(0);
  let feedCodes = $state<string[]>([]);   // все коды из QLua-фида (хвост подсказок)
  // Открытые позиции счёта с выделенной РУЧНОЙ частью. Без них форма спрашивала
  // инструмент, сторону и объём отдельно, и на вопрос «а где выбрать свою
  // позицию, к которой подтянуть стоп» ответить было нечем (оператор, 12.08).
  let positions = $state<OpenPos[]>([]);
  let timers: Array<ReturnType<typeof setInterval>> = [];
  let unsub: (() => void) | null = null;

  const meta = $derived(KIND_BY_ID[kind]);
  const price = $derived(tick?.last || 0);
  const orders = $derived(smartOrdersStore.all);
  const armed = $derived(orders.filter((o) => o.status === 'armed'));
  // Двузначный код связки — тот же, что стоит в метке на графике. Он и связывает
  // карточку с линией: so_id в подпись на линии не влезает, а по цвету семь
  // заявок не различить. У защитных детей код РОДИТЕЛЯ: одна связка — один номер.
  const codes = $derived(shortCodes(armed));
  const history = $derived(orders.filter((o) => o.status !== 'armed').slice(-30).reverse());
  const goodTillMs = $derived(tillLocal ? new Date(tillLocal).getTime() : 0);

  const pv = $derived.by(() => {
    const p = preview({
      kind, side, qty, code,
      trigger: parseFloat(trigger) || 0,
      trailOffset: parseFloat(trailOffset) || 0,
      watchId: watchId.trim(), childPrice: parseFloat(childPrice) || 0,
      slOffset: afterMode === 'sl' ? parseFloat(slOffset) || 0 : 0,
      tpOffset: parseFloat(tpOffset) || 0,
      trailAfter: afterMode === 'trail' ? parseFloat(trailAfter) || 0 : 0,
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

  async function loadPositions() {
    try {
      const [st, mir] = await Promise.all([
        fetchWithAuth('/api/v1/quik/agent-local-status'),
        fetchWithAuth('/api/v1/quik/robots-mirror'),
      ]);
      if (!st.ok || !mir.ok) return;          // молчим: следующий опрос повторит
      positions = manualPositions(await st.json(), await mir.json());
    } catch { /* позиции не обязательны для формы */ }
  }

  /** Подставить закрытие ЭТОЙ позиции: инструмент, сторона закрытия и объём. */
  function pickPosition(p: OpenPos) {
    code = p.code;
    side = closingSide(p.manual);
    qty = Math.abs(p.manual);
    loadTick();
  }

  async function loadPointValue() {
    try {
      const r = await fetchWithAuth('/api/v1/quik/params');
      if (!r.ok) return;
      const d = await r.json();
      const rows = d?.rows || [];
      feedCodes = rows.map((x: any) => String(x.code || '')).filter(Boolean);
      const row = rows.find((x: any) => x.code === code);
      const step = Number(row?.price_step || 0), cost = Number(row?.step_cost || 0);
      // ₽ за пункт. Без него считаем в пунктах и рублями НЕ врём.
      pointValue = step > 0 && cost > 0 ? cost / step : 0;
    } catch { pointValue = 0; }
  }

  // Подсказки инструмента: частые из книги, затем остальные коды фида.
  const codeOptions = $derived(codeSuggestions(orders, feedCodes));

  async function arm() {
    msg = '';
    const body = {
      kind, code, side, qty: Math.floor(qty),
      // У подтягивающей уровня активации НЕТ: движок вернёт 422, если его
      // прислать. Поля в форме тоже нет, но страховка нужна на случай
      // перевзвода из истории, где trigger_price мог остаться от другого типа.
      trigger_price: kind === 'trail_sl' ? 0 : parseFloat(trigger) || 0,
      trail_offset: parseFloat(trailOffset) || 0,
      sl_offset: afterMode === 'sl' ? parseFloat(slOffset) || 0 : 0,
      tp_offset: parseFloat(tpOffset) || 0,
      trail_after: afterMode === 'trail' ? parseFloat(trailAfter) || 0 : 0,
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
      // Снятые чужие стопы называем ПОИМЁННО: молча снять защиту с позиции
      // оператора нельзя, он должен видеть, что именно исчезло.
      const gone = (d.superseded || []).length;
      msg = `Заявка ${d.so_id} взведена. Сторож следит.`
        + (gone ? ` Сняты прежние стопы этой позиции: ${d.superseded.join(', ')}.` : '');
      trigger = ''; trailOffset = ''; slOffset = ''; tpOffset = ''; trailAfter = '';
      watchId = ''; childPrice = '';
      confirming = false;
      await smartOrdersStore.refresh();
    } catch (e: any) { msgKind = 'err'; msg = e?.message || 'ошибка'; }
  }

  async function cancel(soId: string): Promise<boolean> {
    try {
      const res = await fetchWithAuth(`/api/v1/quik/smart-orders/${soId}`, { method: 'DELETE' });
      if (!res.ok) { msgKind = 'err'; msg = (await res.json().catch(() => ({})))?.detail || `HTTP ${res.status}`; }
      await smartOrdersStore.refresh();
      return res.ok;
    } catch { return false; /* список обновится опросом */ }
  }

  // Перевзвести: та же заявка ещё раз. Нужно, когда дочерняя простая заявка не
  // дожила до исполнения (QUIK чистит неисполненные на границе сессии).
  function rearm(o: SmartOrder) {
    kind = o.kind; side = o.side; qty = o.qty; code = o.code;
    trigger = o.trigger_price ? String(o.trigger_price) : '';
    trailOffset = o.trail_offset ? String(o.trail_offset) : '';
    slOffset = o.sl_offset ? String(o.sl_offset) : '';
    tpOffset = o.tp_offset ? String(o.tp_offset) : '';
    trailAfter = (o as any).trail_after ? String((o as any).trail_after) : '';
    afterMode = (o as any).trail_after ? 'trail' : 'sl';
    watchId = o.watch_client_id || '';
    childPrice = o.child_price ? String(o.child_price) : '';
    ocoGroup = o.oco_group || '';
    // Срок тоже восстанавливаем: «до конца сессии» — часть смысла заявки.
    tillLocal = o.good_till_ms
      ? new Date(o.good_till_ms - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16)
      : '';
    confirming = false;
    msgKind = 'ok';
    msg = 'Параметры подставлены — проверьте и взведите заново.';
    document.querySelector('.so-form')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // «Изменить» = снять + подставить в форму + взвести заново РУКАМИ. Никакого
  // редактирования взведённой заявки на месте: пока оператор думает, сторож
  // ничего не сторожит, и это видно по статусу (заявка ушла в «снятые»).
  async function edit(o: SmartOrder) {
    if (!(await cancel(o.so_id))) return;   // не снялась — форму не трогаем
    rearm(o);
    msg = `Заявка ${o.so_id} СНЯТА, сторож её больше не ждёт. Поправьте параметры и взведите заново.`;
  }

  $effect(() => { if (code) { loadTick(); loadPointValue(); } });
  // Смена стороны/типа меняет смысл введённого уровня — подтверждение сбрасываем.
  $effect(() => { void kind; void side; void trigger; void qty; confirming = false; });

  onMount(() => {
    code = (symbol || '').split('@')[0];
    unsub = smartOrdersStore.subscribe(2000);
    timers = [setInterval(loadTick, 2000), setInterval(loadPositions, 5000)];
    loadPointValue();   // и без выбранного кода: наполняет подсказки инструментов
    loadPositions();
  });
  onDestroy(() => { unsub?.(); for (const t of timers) clearInterval(t); });
</script>

<div class="so">
  <!-- 0. ОТКРЫТЫЕ ПОЗИЦИИ. Умная заявка закрывает РУЧНУЮ часть позиции: она
       манульного класса, роботы её не видят, и подставлять нетто счёта нельзя —
       в нём сидят контракты роботов, а их закрытие увело бы робота в минус за
       его спиной. Поэтому рядом с каждой строкой видно, сколько держат роботы. -->
  {#if positions.length}
    <div class="so-pos">
      <span class="so-pos-t">Открытые позиции</span>
      {#each positions as p}
        <button class="so-pos-b" class:flat={!p.manual} disabled={!p.manual}
                onclick={() => pickPosition(p)}
                title={p.manual
                  ? `подставить закрытие: ${p.manual > 0 ? 'продать' : 'купить'} ${Math.abs(p.manual)}`
                  : 'вся позиция принадлежит роботам — умной заявкой её не трогаем'}>
          <b>{p.code}</b>
          <span class="n" class:pos={p.manual > 0} class:neg={p.manual < 0}>
            {p.manual > 0 ? '+' : ''}{p.manual}</span>
          <span class="sub">
            {#if p.robots}роботы {p.robots > 0 ? '+' : ''}{p.robots} из {p.net > 0 ? '+' : ''}{p.net}{:else}руками{/if}
            {#if p.avg} · средняя {fmtPrice(p.avg)}{/if}
          </span>
        </button>
      {/each}
    </div>
  {/if}

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
          <input class="so-in" bind:value={code} placeholder="RIU6" spellcheck="false"
                 list="so-code-list" autocomplete="off" />
          <!-- datalist: подсказки по частоте использования, ручной ввод не отменяет -->
          <datalist id="so-code-list">
            {#each codeOptions as c (c)}<option value={c}></option>{/each}
          </datalist>
        </label>
        <label class="so-f">
          <span>Контрактов</span>
          <input class="so-in" type="number" min="1" step="1" bind:value={qty} />
        </label>
        <!-- Блок «после сделки»: ОДИН из двух защитников, не оба. Два стопа на
             одной позиции — не двойная защита: сработает ближний, дальний
             останется взведён и следующим ходом откроет обратную позицию. -->
        <div class="so-f so-after">
          <span>Защита после сделки</span>
          <div class="so-seg" role="group" aria-label="Защита после сделки">
            <button type="button" class:on={afterMode === 'sl'}
                    onclick={() => afterMode = 'sl'}>Стоп</button>
            <button type="button" class:on={afterMode === 'trail'}
                    onclick={() => afterMode = 'trail'}>Подтягивающая</button>
          </div>
          <em>вместе они запрещены: сработает ближний, дальний откроет обратную позицию. Тейк сочетается с любым</em>
        </div>
        {#each meta.fields as f}
          <label class="so-f">
            <span>{f.label}</span>
            {#if f.key === 'trigger_price'}
              <input class="so-in" type="number" step="any" bind:value={trigger} placeholder="0" />
            {:else if f.key === 'trail_offset'}
              <input class="so-in" type="number" step="any" bind:value={trailOffset} placeholder="0" />
            {:else if f.key === 'sl_offset'}
              <input class="so-in" type="number" step="any" min="0" bind:value={slOffset}
                     disabled={afterMode !== 'sl'} placeholder="0 — без стопа" />
            {:else if f.key === 'trail_after'}
              <input class="so-in" type="number" step="any" min="0" bind:value={trailAfter}
                     disabled={afterMode !== 'trail'} placeholder="0 — выключена" />
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
      {#if armed.length && smartOrdersStore.paused}
        <span class="so-stale">биржа не торгует ({smartOrdersStore.session.phase || 'нет данных'})
          — сторож ждёт открытия и ничего не отправит</span>
      {/if}
    </div>
    {#if !armed.length}
      <p class="so-empty">Взведённых заявок нет. Сторож ничего не ждёт.</p>
    {/if}
    {#each armed as o (o.so_id)}
      <article class="so-card" style="--accent:{KIND_BY_ID[o.kind].color}">
        <div class="so-c-head">
          <span class="so-c-num" title="номер связки: этим же номером заявка подписана на графике">{codes[o.so_id]}</span>
          <span class="so-c-tag">{KIND_BY_ID[o.kind].short}</span>
          <b class="so-c-code">{o.code}</b>
          <span class="so-c-side" class:buy={o.side === 'buy'}>{o.side === 'buy' ? 'ПОКУПКА' : 'ПРОДАЖА'} {o.qty}</span>
          <span class="so-c-cond">{conditionText(o)}</span>
          <span class="so-c-sp"></span>
          <span class="so-c-status">{STATUS_RU[o.status] ?? o.status}</span>
          <button class="so-btn sm" title="снять заявку, подставить её параметры в форму и взвести заново"
                  onclick={() => edit(o)}>Изменить</button>
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
          </div>
        {/if}
        <!-- Защитная пара — у ЛЮБОГО типа (09.08.2026): любая умная заявка только
             входит и после срабатывания забывает про позицию. Блок вынесен из
             ветки следящей, иначе у остальных типов заказанные стоп и тейк
             оставались бы невидимыми на карточке. -->
        {#if o.sl_offset || o.tp_offset}
          <div class="so-c-sl">после сделки автоматически встанут:
            {#if o.sl_offset}<b>стоп {o.sl_offset} п.</b>{/if}{#if o.sl_offset && o.tp_offset} и {/if}{#if o.tp_offset}<b>тейк {o.tp_offset} п.</b>{/if}
            от её цены{#if o.sl_offset && o.tp_offset}, в одной связке — сработает один, второй снимется{/if}</div>
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
  /* Позиции: строка кнопок над формой. Ручной остаток — крупно, доля роботов —
     мелко рядом: закрывать чужие контракты оператор не должен даже случайно. */
  .so-pos { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
  .so-pos-t { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: #8a90a8; }
  .so-pos-b { display: inline-flex; align-items: baseline; gap: 8px; cursor: pointer;
    background: #12122a; border: 1px solid #2d2d4a; border-radius: 6px; padding: 6px 12px;
    color: #cfd4e6; font: 12px/1.2 system-ui, sans-serif; }
  .so-pos-b:hover { border-color: #4c6fa8; }
  .so-pos-b.flat { opacity: .45; cursor: default; }
  .so-pos-b b { font-family: Consolas, monospace; font-size: 13px; color: #e8e8f0; }
  .so-pos-b .n { font: 700 15px/1 Consolas, monospace; }
  .so-pos-b .n.pos { color: #2ecc71; } .so-pos-b .n.neg { color: #ff6b5a; }
  .so-pos-b .sub { font-size: 10px; color: #7f86a0; }

  .so-f { display: grid; gap: 2px; }
  .so-f.wide { grid-column: 1 / -1; }
  .so-f > span { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: #8a90a8; }
  .so-f > em { font-size: 10px; color: #6f7590; font-style: normal; }
  /* Переключатель «стоп / подтягивающая»: одно из двух, физически не даёт
     заполнить оба поля сразу. */
  .so-after { grid-column: 1 / -1; }
  .so-seg { display: inline-flex; border: 1px solid #2d2d4a; border-radius: 6px; overflow: hidden; }
  .so-seg button { background: #0e0e1e; border: 0; color: #8a90a8; cursor: pointer;
    font: 600 12px/1 system-ui, sans-serif; padding: 8px 14px; }
  .so-seg button + button { border-left: 1px solid #2d2d4a; }
  .so-seg button:hover { color: #dfe6ff; }
  .so-seg button.on { background: #1b1b34; color: #e8e8f0; }
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
  /* Номер связки: моноширинный, чтобы двузначные не плясали по ширине. */
  .so-c-num {
    font: 600 12px/1 ui-monospace, Menlo, Consolas, monospace;
    color: #0f0f1e; background: var(--accent); border-radius: 3px;
    padding: 2px 4px; flex-shrink: 0;
  }
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

  /* ══ ТЕЛЕФОН ═══════════════════════════════════════════════════════════════
     Экран умных заявок — это деньги и стопы: читать его надо без прищура. На
     телефоне всё в одну колонку, шрифт крупнее, поля и кнопки под палец (36 px —
     минимум, ниже промахиваешься), карточки заявок разворачиваются вертикально.
     Длинный скролл здесь лучше, чем плотная сетка: заявок редко больше десятка. */
  @media (max-width: 820px) {
    .so { padding: 12px 12px 24px; font-size: 15px; max-width: none; height: auto; }
    .so-h { font-size: 12px; letter-spacing: .1em; }
    .so-kinds { grid-template-columns: 1fr; gap: 10px; }
    .so-kind { padding: 12px 14px; }
    .so-kind-name { font-size: 17px; }
    .so-kind-ess { font-size: 14px; }
    .so-main { grid-template-columns: 1fr; gap: 16px; }
    .so-facts { grid-template-columns: 1fr; gap: 1px 0; }
    .so-facts dt { margin-top: 8px; }
    .so-algo { font-size: 14px; line-height: 1.6; }
    /* Поля ввода: 16px обязателен — на меньшем iOS сам зумит страницу при фокусе. */
    input, button { font-size: 16px; }
    input { min-height: 38px; }
    .so-btn, .so-csv { min-height: 40px; padding: 0 14px; }
    /* Карточка заявки: шапка в столбец, чтобы длинные условия не резались. */
    .so-card { padding: 12px; }
    .so-c-head { flex-wrap: wrap; gap: 6px; }
    .so-c-cond, .so-c-facts { font-size: 14px; line-height: 1.5; }
  }
</style>
