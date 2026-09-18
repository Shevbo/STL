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
    KINDS, KIND_BY_ID, COMMON_FACTS, STATUS_RU, afterFillFacts, afterFillPreview, codeSuggestions, conditionText,
    defaultCode, isLive, keyPrice,
    closingSide, fmtWhen, fmtPts, fmtRub, manualPositions, ocoFact, preview, protectionPair,
    shortCodes, sortBySideAndPrice, tillFact, type Kind, type OpenPos, type Side,
  } from '$lib/smart-order-help';

  let { symbol = '' }: { symbol?: string } = $props();

  let kind = $state<Kind>('sl');
  let side = $state<Side>('sell');
  let qty = $state(1);
  let code = $state('');
  // Оператор выбрал инструмент руками — больше не подставляем ничего сами.
  let codeTouched = $state(false);
  let codeOpen = $state(false);
  let trigger = $state('');
  let trailOffset = $state('');
  let slOffset = $state('');          // защитный стоп после входа, пункты (0 = без стопа)
  let tpOffset = $state('');          // тейк после входа, пункты (0 = без тейка)
  // Те же два блока, но ЦЕНОЙ УРОВНЯ (real-trade 18.09, sl_price/tp_price).
  // Пунктами не попасть в уровень, пока цена входа неизвестна: оператор видит
  // коридор и ставит тейк под его границей. Пункты и цена одного блока
  // взаимоисключающи — движок вернёт 422, поэтому поля гасят друг друга.
  let slPrice = $state('');
  let tpPrice = $state('');
  // Тейк после сделки: фиксированный или СЛЕДЯЩИЙ (real-trade 17.09, tp_trail). У
  // следящего tpOffset — уровень активации, tpTrail — откат от экстремума.
  let tpMode = $state<'fixed' | 'trail'>('fixed');
  let tpTrail = $state('');
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
  // «Как это работает» свёрнуто по умолчанию: оператор читает его раз, а место
  // под формой оно занимает всегда (просьба оператора 18.09).
  let explainOpen = $state(false);
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

  // Подсказки под «?»: у каждого варианта поля свой текст, потому что цена и
  // пункты — разные величины, и перепутать их стоило оператору тейка (18.09:
  // уровень 84700 в поле пунктов дал активацию 168210, позиция осталась без тейка).
  const HELP = {
    slPts: 'ПУНКТЫ от фактической цены входа, против позиции. Купили по 83510 со стопом 390 — стоп встанет на 83120. Не цена.',
    slPrice: 'ЦЕНА уровня. Стоп встанет ровно на ней, где бы ни случился вход. Вход уже за уровнем — стоп не ставится, придёт тревога.',
    tpPts: 'ПУНКТЫ от фактической цены входа, в пользу позиции. Купили по 83510 с тейком 700 — тейк на 84210. Не цена.',
    tpPrice: 'ЦЕНА уровня. Тейк встанет ровно на ней — так ставят под границу коридора, когда цена входа ещё неизвестна.',
    tpPtsTrail: 'ПУНКТЫ от входа до точки, где тейк начнёт следить за максимумом. Дальше он закроет позицию на откате.',
    tpPriceTrail: 'ЦЕНА уровня, с которого тейк начнёт следить за максимумом. Дальше он закроет позицию на откате.',
    tpTrail: 'ПУНКТЫ отката от лучшей достигнутой цены, на котором тейк закрывает позицию.',
  };
  // ЧИСЛО, А НЕ СТРОКА. `bind:value` на <input type="number"> кладёт в состояние
  // число, и `(v || '').trim()` падал с TypeError на первом же введённом
  // значении. Падение в $derived рвало реактивность всего экрана: переключатели
  // «не нажимались», заполненный стоп выглядел выключенным, а строка пары врала
  // «защиты нет» при заполненных полях (оператор, 18.09.2026).
  const tr = (v: unknown) => String(v ?? '').trim();
  const num = (v: unknown) => parseFloat(tr(v)) || 0;
  // ЗАПОЛНЕНО = положительное число, а не «непустая строка». Плейсхолдеры сами
  // зовут ввести «0 — без стопа»: набранный ноль гасил парное поле «ценой» и
  // считался защитой в итоговой строке (аудит 18.09.2026).
  const pos = (v: unknown) => num(v) > 0;

  const meta = $derived(KIND_BY_ID[kind]);
  const price = $derived(tick?.last || 0);
  const orders = $derived(smartOrdersStore.all);
  // Что получится при текущей цене: ошибку «цена в поле пунктов» видно ДО отправки.
  const levelPreview = $derived(afterFillPreview({
    side, price, slOffset, slPrice, tpOffset, tpPrice, tpMode, afterMode,
  }));
  // Пара защитников словами: стоп и тейк независимы, и это должно быть видно.
  const pairLine = $derived(protectionPair({
    afterMode, tpMode,
    hasStop: afterMode === 'sl' ? (pos(slOffset) || pos(slPrice)) : pos(trailAfter),
    hasTake: pos(tpOffset) || pos(tpPrice),
  }));
  // Живые — сторож STL (armed) И заявки под охраной терминала (native): последние
  // переживают падение STL, и в истории им не место (real-trade 17.09).
  const armed = $derived(orders.filter((o) => isLive(o.status)));
  // Двузначный код связки — тот же, что стоит в метке на графике. Он и связывает
  // карточку с линией: so_id в подпись на линии не влезает, а по цвету семь
  // заявок не различить. У защитных детей код РОДИТЕЛЯ: одна связка — один номер.
  const codes = $derived(shortCodes(armed));
  // Порядок: продажи сверху, покупки снизу, внутри стороны цена по убыванию
  // (просьба оператора 17.09). Список по времени взведения читался журналом:
  // что стоит над рынком, а что под ним, приходилось складывать в голове.
  const armedSorted = $derived(sortBySideAndPrice(armed));
  const history = $derived(orders.filter((o) => !isLive(o.status)).slice(-30).reverse());
  const goodTillMs = $derived(tillLocal ? new Date(tillLocal).getTime() : 0);

  const pv = $derived.by(() => {
    const p = preview({
      kind, side, qty, code,
      trigger: num(trigger),
      trailOffset: num(trailOffset),
      watchId: tr(watchId), childPrice: num(childPrice),
      slOffset: afterMode === 'sl' ? num(slOffset) : 0,
      slPrice: afterMode === 'sl' ? num(slPrice) : 0,
      tpOffset: num(tpOffset),
      tpPrice: num(tpPrice),
      tpTrail: tpMode === 'trail' ? num(tpTrail) : 0,
      trailAfter: afterMode === 'trail' ? num(trailAfter) : 0,
      price, pointValue,
    });
    // «Следящий» выбран, а откат не введён — движок поставит ОБЫЧНЫЙ тейк на
    // уровне активации и не возразит (smart_orders.py:169). Экран при этом
    // обещал слежение: три места говорили «следящий», а уезжал фиксированный
    // (аудит 18.09.2026). Раз обещание не выполнимо — до кнопки не пускаем.
    if (!p.error && tpMode === 'trail' && (pos(tpOffset) || pos(tpPrice)) && !pos(tpTrail)) {
      return { ...p, error: 'Следящий тейк: укажите откат от экстремума в пунктах.' };
    }
    if (!p.error && goodTillMs && goodTillMs <= Date.now()) {
      return { ...p, error: 'Срок «действует до» уже прошёл.' };
    }
    return p;
  });

  // Ценовая рейка: где сейчас цена, где сработает и сколько между ними.
  const rail = $derived.by(() => {
    const t = parseFloat(trigger) || 0;
    // Уровень рисуем только там, где поле уровня есть. Остаток trigger от
    // прошлого типа рисовал «сработает 83 000» под Подтягивающей, у которой
    // уровня активации нет вовсе (аудит 18.09.2026).
    if (!meta.fields.some((f) => f.key === 'trigger_price')) return null;
    if (!(price > 0) || !(t > 0)) return null;
    const lo = Math.min(price, t), hi = Math.max(price, t);
    const pad = Math.max((hi - lo) * 0.45, hi * 0.0004);
    const top = hi + pad, bot = lo - pad;
    const y = (v: number) => 12 + (top - v) / (top - bot) * 116;
    return { yPrice: y(price), yTrig: y(t), trig: t, gap: Math.abs(t - price),
             above: t > price };
  });

  async function loadTick() {
    if (!code) { tick = null; return; }
    // Инструмент могли сменить, пока запрос летел: ответ по ПРЕЖНЕМУ коду писать
    // нельзя — экран показал бы чужую котировку под новым инструментом, а рубли
    // считались бы из цены одного и коэффициента другого (аудит 18.09.2026).
    const asked = code;
    try {
      const r = await fetchWithAuth(`/api/v1/quik/tick/${encodeURIComponent(asked)}`);
      const t = r.ok ? await r.json() : null;
      if (asked === code) tick = t;
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

  // Поля делятся на две группы по смыслу: ЧЕМ заявка сработает и ЧТО встанет
  // после сделки. Одной плоской сеткой уровень срабатывания и защитная пара
  // читались одинаково, хотя это разные моменты времени.
  const TRIGGER_KEYS = ['trigger_price', 'trail_offset', 'watch_client_id', 'child_price'];
  const triggerFields = $derived(meta.fields.filter((f) => TRIGGER_KEYS.includes(f.key)));
  const afterFields = $derived(meta.fields.filter((f) => !TRIGGER_KEYS.includes(f.key)));

  async function arm() {
    msg = '';
    // ОТПРАВЛЯЕМ ТОЛЬКО ПОЛЯ СВОЕГО ТИПА. Состояние формы переживает смену типа,
    // и остатки уезжали молча: набранный в Следящей отступ возвращался с 422 на
    // Условной, где поля «отступ» нет вовсе, а блоки после сделки у
    // Подтягивающей движок отвергает целиком (smart_orders.py:135). Раньше
    // обнулялся один trigger_price — теперь гасим всё, чего нет в meta.fields.
    const mine = new Set(meta.fields.map((f) => f.key));
    const only = (key: string, v: number) => (mine.has(key) ? v : 0);
    const body = {
      kind, code, side, qty: Math.floor(qty),
      trigger_price: only('trigger_price', num(trigger)),
      trail_offset: only('trail_offset', num(trailOffset)),
      sl_offset: only('sl_offset', afterMode === 'sl' ? num(slOffset) : 0),
      sl_price: only('sl_offset', afterMode === 'sl' ? num(slPrice) : 0),
      tp_offset: only('tp_offset', num(tpOffset)),
      tp_price: only('tp_offset', num(tpPrice)),
      tp_trail: only('tp_offset', tpMode === 'trail' ? num(tpTrail) : 0),
      trail_after: only('trail_after', afterMode === 'trail' ? num(trailAfter) : 0),
      watch_client_id: mine.has('watch_client_id') ? tr(watchId) : '',
      child_price: only('child_price', num(childPrice)),
      oco_group: tr(ocoGroup),
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
      trigger = ''; trailOffset = ''; slOffset = ''; tpOffset = ''; trailAfter = ''; tpTrail = ''; tpMode = 'fixed';
      slPrice = ''; tpPrice = '';
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
    slPrice = (o as any).sl_price ? String((o as any).sl_price) : '';
    tpPrice = (o as any).tp_price ? String((o as any).tp_price) : '';
    tpTrail = o.tp_trail ? String(o.tp_trail) : '';
    tpMode = o.tp_trail ? 'trail' : 'fixed';
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
  $effect(() => {
    void kind; void side; void trigger; void qty; void code; void afterMode; void tpMode;
    confirming = false;
  });

  // Инструмент по умолчанию — САМЫЙ ИСПОЛЬЗУЕМЫЙ из книги (правило оператора).
  // Ставим в эффекте, а не в onMount: книга и фид приезжают асинхронно, и на
  // монтировании их ещё нет — до 18.09 из-за этого в поле попадал символ графика.
  $effect(() => {
    if (codeTouched || code) return;
    const d = defaultCode(orders, feedCodes, symbol);
    if (d) code = d;
  });

  onMount(() => {
    unsub = smartOrdersStore.subscribe(2000);
    timers = [setInterval(loadTick, 2000), setInterval(loadPositions, 5000)];
    loadPointValue();   // и без выбранного кода: наполняет подсказки инструментов
    loadPositions();
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

  <!-- 2. Как это работает — свёрнуто в строку: справка нужна редко, место под
       формой нужно всегда. -->
  <button class="so-fold" aria-expanded={explainOpen} onclick={() => explainOpen = !explainOpen}>
    <span class="so-fold-ar">{explainOpen ? '▾' : '▸'}</span>
    Как это работает
    <span class="so-fold-hint">{explainOpen ? 'свернуть' : 'раскрыть'}</span>
  </button>

  <div class="so-main" class:solo={!explainOpen}>
    {#if explainOpen}
    <section class="so-explain">
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
    {/if}

    <!-- 3. Параметры -->
    <section class="so-form">
      <div class="so-h">Параметры</div>

      <!-- ВЫБОР ПОЗИЦИИ СТОИТ ЗДЕСЬ, а не отдельной полосой наверху: он не
           «информация о счёте», а первое действие при закрытии — заполняет
           сторону, инструмент и объём, то есть три поля прямо под собой.
           Наверху его не находили («ГДЕ???», оператор 12.08). -->
      {#if positions.length}
        <div class="so-pick">
          <span class="so-pick-t">Закрыть свою позицию</span>
          {#each positions as p}
            <button class="so-pick-b" class:flat={!p.manual} disabled={!p.manual}
                    onclick={() => pickPosition(p)}
                    title={p.manual
                      ? `подставит ${p.manual > 0 ? 'продажу' : 'покупку'} ${Math.abs(p.manual)} по ${p.code}`
                      : 'вся позиция принадлежит роботам — умной заявкой её не трогаем'}>
              <span class="act">{p.manual > 0 ? 'Продать' : p.manual < 0 ? 'Купить' : '—'}
                {Math.abs(p.manual) || ''}</span>
              <b>{p.code}</b>
              <span class="sub">
                {#if p.robots}у вас {p.manual > 0 ? '+' : ''}{p.manual}, у роботов {p.robots > 0 ? '+' : ''}{p.robots}{:else}вся позиция ваша{/if}
                {#if p.avg} · средняя {fmtPrice(p.avg)}{/if}
              </span>
            </button>
          {/each}
        </div>
      {/if}

      <!-- ГРУППА 1: что и сколько. Сторона, инструмент и объём — один вопрос
           «какая сделка», и стоят они вместе. -->
      <div class="so-group">
        <div class="so-g-h">Сделка</div>
        <div class="so-sides">
          <button class="so-side buy" class:on={side === 'buy'} onclick={() => side = 'buy'}>Купить</button>
          <button class="so-side sell" class:on={side === 'sell'} onclick={() => side = 'sell'}>Продать</button>
        </div>

        <div class="so-fields">
        <!-- Здесь и ниже поля обёрнуты в div, а НЕ в label: внутри стоят кнопки
             («?», переключатели), а клик по кнопке внутри label браузер дублирует
             на связанный контрол — переключатели срабатывали через раз и
             «нажимались с третьей попытки» (оператор, 18.09.2026). -->
        <div class="so-f">
          <span>Инструмент</span>
          <!-- Свой список, а не datalist: браузерный фильтрует подсказки по уже
               введённому значению, и с заполненным полем оператор видел ровно одну
               строку — «список перестал работать» (18.09.2026). Ручной ввод остаётся. -->
          <div class="so-code" onfocusout={(e) => {
                 if (!e.currentTarget.contains(e.relatedTarget as Node)) codeOpen = false;
               }}>
            <input class="so-in" bind:value={code}
                   placeholder={codeOptions[0] || 'ждём список от агента'} spellcheck="false"
                   aria-label="Инструмент"
                   autocomplete="off" oninput={() => { codeTouched = true; codeOpen = true; }}
                   onfocus={() => codeOpen = true}
                   onkeydown={(e) => { if (e.key === 'Escape') codeOpen = false; }} />
            <button type="button" class="so-code-btn" tabindex="-1"
                    title="частые инструменты: сверху те, которыми торгуете чаще"
                    onclick={() => codeOpen = !codeOpen}>▾</button>
            {#if codeOpen && codeOptions.length}
              <ul class="so-code-list">
                {#each codeOptions.slice(0, 12) as c (c)}
                  <li><button type="button" class:on={c === code}
                              onclick={() => { code = c; codeTouched = true; codeOpen = false; }}>{c}</button></li>
                {/each}
              </ul>
            {/if}
          </div>
          {#if !codeOptions.length}
            <em>агент не прислал список инструментов — подставлять нечего. Истёкший
              контракт из истории заявок здесь не предлагаем: заявка на него ждала бы
              цену, которой больше нет</em>
          {/if}
        </div>
        <div class="so-f">
          <span>Контрактов</span>
          <input class="so-in" type="number" min="1" step="1" bind:value={qty} aria-label="Контрактов" />
        </div>
        </div>
      </div>

      <!-- ГРУППА 2: чем заявка сработает. Это МОМЕНТ ВХОДА, и он отделён от
           того, что встанет после него. -->
      <div class="so-group">
        <div class="so-g-h">Чем сработает</div>
        <div class="so-fields">
        {#each triggerFields as f}
          <div class="so-f">
            <span>{f.label}<button type="button" class="so-q" title={f.hint} aria-label={f.hint}>?</button></span>
            {#if f.key === 'trigger_price'}
              <input class="so-in" type="number" step="any" bind:value={trigger} placeholder="0" />
            {:else if f.key === 'trail_offset'}
              <div class="so-unit-wrap">
                <input class="so-in pts" type="number" step="any" bind:value={trailOffset} placeholder="0" />
                <span class="so-unit">п.</span>
              </div>
            {:else if f.key === 'watch_client_id'}
              <input class="so-in text" bind:value={watchId} placeholder="client_id" spellcheck="false"
                     aria-label={f.label} />
            {:else}
              <input class="so-in" type="number" step="any" bind:value={childPrice} placeholder="по рынку"
                     aria-label={f.label} />
            {/if}
            <em>{f.hint}</em>
          </div>
        {/each}
        </div>
      </div>

      <!-- ГРУППА 3: что встанет ПОСЛЕ сделки. Защита — ОДИН из двух
           защитников, не оба: два стопа на одной позиции не удваивают защиту,
           сработает ближний, а дальний откроет обратную позицию. -->
      {#if afterFields.length}
      <div class="so-group">
        <div class="so-g-h">Что встанет после сделки</div>
        <!-- Переключатель называет ВИД СТОПА, а не «стоп или что-то другое»:
             прежние «Стоп | Подтягивающая» читались как выбор между защитой и
             её отсутствием, и оператор не нашёл «стоп фиксированный + тейк
             следящий», хотя связка законна (18.09.2026). -->
        <div class="so-f so-after">
          <span>Стоп — какой</span>
          <div class="so-seg" role="group" aria-label="Вид стопа после сделки">
            <button type="button" class:on={afterMode === 'sl'}
                    onclick={() => afterMode = 'sl'}>Фиксированный</button>
            <button type="button" class:on={afterMode === 'trail'}
                    onclick={() => afterMode = 'trail'}>Подтягивающийся</button>
          </div>
          <em>два стопа сразу запрещены: сработает ближний, дальний откроет обратную позицию. Тейк — отдельная нога, он сочетается с любым стопом</em>
        </div>
        <div class="so-fields">
        {#each afterFields as f}
          <div class="so-f">
            <span>{f.label}<button type="button" class="so-q" title={f.hint} aria-label={f.hint}>?</button></span>
            {#if f.key === 'sl_offset'}
              <div class="so-two">
                <div class="so-col">
                  <b>в пунктах<button type="button" class="so-q" title={HELP.slPts} aria-label={HELP.slPts}>?</button></b>
                  <div class="so-unit-wrap">
                    <input class="so-in pts" type="number" step="any" min="0" bind:value={slOffset}
                           disabled={afterMode !== 'sl' || pos(slPrice)} placeholder="0 — без стопа" />
                    <span class="so-unit">п.</span>
                  </div>
                </div>
                <div class="so-col">
                  <b>ценой<button type="button" class="so-q" title={HELP.slPrice} aria-label={HELP.slPrice}>?</button></b>
                  <input class="so-in" type="number" step="any" min="0" bind:value={slPrice}
                         disabled={afterMode !== 'sl' || pos(slOffset)} placeholder="уровень" />
                </div>
              </div>
              {#if levelPreview.sl}<em class="so-calc">{levelPreview.sl}</em>{/if}
              {#if afterMode !== 'sl'}
                <button type="button" class="so-enable" onclick={() => afterMode = 'sl'}>
                  включить стоп вместо подтягивающей
                </button>
              {/if}
            {:else if f.key === 'trail_after'}
              <div class="so-unit-wrap">
                <input class="so-in pts" type="number" step="any" min="0" bind:value={trailAfter}
                       disabled={afterMode !== 'trail'} placeholder="0 — выключена" />
                <span class="so-unit">п.</span>
              </div>
              {#if afterMode !== 'trail'}
                <button type="button" class="so-enable" onclick={() => afterMode = 'trail'}>
                  включить подтягивающую вместо стопа
                </button>
              {/if}
            {:else if f.key === 'tp_offset'}
              <div class="so-seg" role="group" aria-label="Тейк после сделки">
                <button type="button" class:on={tpMode === 'fixed'} onclick={() => tpMode = 'fixed'}
                        title="тейк стоит на фиксированном расстоянии от цены входа">Фикс.</button>
                <button type="button" class:on={tpMode === 'trail'} onclick={() => tpMode = 'trail'}
                        title="тейк идёт за экстремумом и закрывает на откате">Следящий</button>
              </div>
              <div class="so-two">
                <div class="so-col">
                  <b>в пунктах<button type="button" class="so-q"
                      title={tpMode === 'trail' ? HELP.tpPtsTrail : HELP.tpPts}
                      aria-label={tpMode === 'trail' ? HELP.tpPtsTrail : HELP.tpPts}>?</button></b>
                  <div class="so-unit-wrap">
                    <input class="so-in pts" type="number" step="any" min="0" bind:value={tpOffset}
                           disabled={pos(tpPrice)}
                           placeholder={tpMode === 'trail' ? 'активация от входа' : '0 — без тейка'} />
                    <span class="so-unit">п.</span>
                  </div>
                </div>
                <div class="so-col">
                  <b>ценой<button type="button" class="so-q"
                      title={tpMode === 'trail' ? HELP.tpPriceTrail : HELP.tpPrice}
                      aria-label={tpMode === 'trail' ? HELP.tpPriceTrail : HELP.tpPrice}>?</button></b>
                  <input class="so-in" type="number" step="any" min="0" bind:value={tpPrice}
                         disabled={pos(tpOffset)} placeholder="уровень" />
                </div>
              </div>
              {#if tpMode === 'trail'}
                <div class="so-col">
                  <b>откат<button type="button" class="so-q" title={HELP.tpTrail} aria-label={HELP.tpTrail}>?</button></b>
                  <div class="so-unit-wrap">
                    <input class="so-in pts" type="number" step="any" min="0" bind:value={tpTrail}
                           placeholder="откат от экстремума" />
                    <span class="so-unit">п.</span>
                  </div>
                </div>
              {/if}
              {#if levelPreview.tp}<em class="so-calc">{levelPreview.tp}</em>{/if}
            {/if}
            <em>{(f.key === 'sl_offset' && afterMode !== 'sl')
                 || (f.key === 'trail_after' && afterMode !== 'trail')
                 ? 'выключено: вместе со вторым видом защиты нельзя (движок вернёт 422). '
                   + 'Кнопка выше переключает, какой из них ставим.'
                 : f.hint}</em>
          </div>
        {/each}
        </div>
        <p class="so-pair">{pairLine}</p>
      </div>
      {/if}

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
              {fmtPts(rail.gap)}{pointValue && qty > 0 ? ' · ' + fmtRub(rail.gap * pointValue * qty) : ''}
            </text>
          </svg>
        </div>
      {/if}

      <!-- ГРУППА 4: сколько заявка живёт и с кем связана. Ни то, ни другое не
           влияет на цену, поэтому они внизу и отдельно. -->
      <div class="so-group">
        <div class="so-g-h">Срок и связка</div>
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
    {#each armedSorted as o, i (o.so_id)}
      {#if i === 0 || armedSorted[i - 1].side !== o.side}
        <div class="so-side-h" class:buy={o.side === 'buy'}>
          {o.side === 'buy' ? 'ПОКУПКА' : 'ПРОДАЖА'}
          <span class="so-side-n">{armedSorted.filter((x) => x.side === o.side).length}</span>
        </div>
      {/if}
      <article class="so-card" style="--accent:{KIND_BY_ID[o.kind].color}">
        <div class="so-c-head">
          <span class="so-c-num" title="номер связки: этим же номером заявка подписана на графике">{codes[o.so_id]}</span>
          <span class="so-c-tag">{KIND_BY_ID[o.kind].short}</span>
          <b class="so-c-code">{o.code}</b>
          <span class="so-c-dir" class:buy={o.side === 'buy'}>{o.side === 'buy' ? 'ПОКУПКА' : 'ПРОДАЖА'}</span>
          <span class="so-c-qty" title="объём, контрактов">{o.qty}</span>
          <span class="so-c-px" title={keyPrice(o).label}><small>{keyPrice(o).label}</small>{keyPrice(o).price != null ? fmtPrice(keyPrice(o).price) : '—'}</span>
          <span class="so-c-cond">{conditionText(o)}</span>
          <span class="so-c-sp"></span>
          <span class="so-c-status" class:native={o.status === 'native'}
                class:warn={o.status === 'native' && o.native_state === 'failed'}
                title={o.status === 'native'
                  ? (o.native_state === 'failed'
                      ? 'терминал не принял стоп-заявку: защиту снова ведёт сторож STL'
                      : 'защита стоит нативной стоп-заявкой QUIK и переживёт падение STL')
                  : 'защиту ведёт сторож STL: пока STL лежит, заявка не сработает'}>
            {o.status === 'native' ? '🛡' : '⏱'} {STATUS_RU[o.status] ?? o.status}
          </span>
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
        <!-- Перечисление собирает ОДИН формулировщик: инлайн-разметка знала только
             про пункты и молча теряла блоки, заданные ценой уровня, и
             подтягивающую (оператор 18.09.2026, заявка 4117394fd0). -->
        {#if afterFillFacts(o)}
          <div class="so-c-sl">после сделки автоматически встанут: <b>{afterFillFacts(o)}</b></div>
        {/if}
        <div class="so-c-facts">
          {#if o.status === 'native'}
            {#if o.native_state === 'failed'}
              <span class="so-c-bad">терминал НЕ принял стоп-заявку — защиту ведёт STL</span>
            {:else if o.native_stop_num}
              <span>стоп-заявка QUIK {o.native_stop_num}</span>
            {/if}
          {/if}
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
              <span class="so-c-dir" class:buy={o.side === 'buy'}>{o.side === 'buy' ? 'ПОКУПКА' : 'ПРОДАЖА'}</span>
              <span class="so-c-qty" title="объём, контрактов">{o.qty}</span>
              <span class="so-c-px" title={keyPrice(o).label}><small>{keyPrice(o).label}</small>{keyPrice(o).price != null ? fmtPrice(keyPrice(o).price) : '—'}</span>
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
        height: 100%; max-width: 1480px; }
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

  .so-main { display: grid; grid-template-columns: minmax(300px, 1fr) minmax(300px, 380px); gap: 20px; margin-top: 12px; }
  /* Справка свёрнута — форма забирает ВСЮ ширину экранной формы (оператор
     18.09). Чтобы поля при этом не превращались в полосы во весь монитор,
     сетка внутри группы сама добирает колонки по ширине. */
  .so-main.solo { grid-template-columns: 1fr; }
  @media (max-width: 900px) { .so-main { grid-template-columns: 1fr; } }

  /* строка-сворачиватель справки */
  .so-fold {
    display: flex; align-items: center; gap: 8px; width: 100%; margin-top: 14px;
    background: none; border: 0; border-bottom: 1px solid #2d2d4a; cursor: pointer;
    padding: 0 0 6px; color: #8a90a8;
    font: 10px/1 system-ui, sans-serif; letter-spacing: .16em; text-transform: uppercase;
  }
  .so-fold:hover { color: #dfe6ff; border-color: #4a4a7a; }
  .so-fold-ar { color: #6f7590; font-size: 11px; }
  .so-fold-hint { margin-left: auto; letter-spacing: .08em; color: #6f7590; text-transform: none; }

  /* объяснение */
  .so-algo { margin: 10px 0 14px; padding-left: 18px; line-height: 1.55; }
  .so-algo li { margin-bottom: 3px; }
  .so-facts { margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: 3px 10px; line-height: 1.45; }
  .so-facts dt { color: #8a90a8; white-space: nowrap; }
  .so-facts dd { margin: 0; color: #b9bfd4; }
  .so-facts dt.warn, .so-facts dd.warn { color: #e0a53c; }

  /* параметры: крупно */
  /* Группы, а не одна сплошная сетка: «какая сделка», «чем сработает» и «что
     встанет после» — три разных вопроса, и глазу нужна граница между ними
     (просьба оператора 18.09). Воздух внутри группы меньше, чем между ними —
     на этом и держится группировка. */
  .so-group {
    background: #12122480; border: 1px solid #23233f; border-radius: 8px;
    padding: 12px 14px 14px; margin-bottom: 14px;
  }
  .so-group:last-child { margin-bottom: 0; }
  .so-g-h {
    font: 600 10px/1 system-ui, sans-serif; letter-spacing: .16em; text-transform: uppercase;
    color: #7f86a6; margin-bottom: 12px;
  }
  .so-sides { display: flex; gap: 8px; margin-bottom: 14px; }
  .so-side {
    flex: 1; padding: 11px 0; font-size: 13px; letter-spacing: .06em; cursor: pointer;
    background: #14142a; border: 1px solid #2d2d4a; border-radius: 6px; color: #8a90a8;
  }
  .so-side:hover { border-color: #4a4a7a; color: #dfe6ff; }
  .so-side.on.buy { background: #123a22; border-color: #2ecc71; color: #7ef0a6; }
  .so-side.on.sell { background: #3a1616; border-color: #ff6b5a; color: #ff9d90; }
  .so-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px 16px; }
  /* Выбор позиции внутри формы: кнопка называет ДЕЙСТВИЕ («Продать 13»), а не
     просто число — иначе она читается как справка о счёте и её не нажимают.
     Доля роботов подписана рядом: закрывать чужие контракты нельзя даже случайно. */
  .so-pick { display: grid; gap: 6px; margin-bottom: 12px; }
  .so-pick-t { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: #8a90a8; }
  .so-pick-b { display: flex; align-items: baseline; gap: 8px; cursor: pointer; text-align: left;
    background: #16243c; border: 1px solid #3a6ba8; border-radius: 6px; padding: 9px 12px;
    color: #dfe6ff; font: 13px/1.2 system-ui, sans-serif; width: 100%; }
  .so-pick-b:hover { background: #1c2f4e; border-color: #5b8fd6; }
  .so-pick-b.flat { background: #12122a; border-color: #2d2d4a; opacity: .5; cursor: default; }
  .so-pick-b .act { font-weight: 700; }
  .so-pick-b b { font-family: Consolas, monospace; font-size: 13px; }
  .so-pick-b .sub { margin-left: auto; font-size: 10px; color: #93a2c4; }

  .so-f { display: grid; gap: 5px; align-content: start; }
  .so-f.wide { grid-column: 1 / -1; }
  .so-f > span { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: #8a90a8;
    display: flex; align-items: center; gap: 5px; }
  .so-f > em { font-size: 10px; line-height: 1.4; color: #6f7590; font-style: normal; }
  /* Переключатель «стоп / подтягивающая»: одно из двух, физически не даёт
     заполнить оба поля сразу. */
  .so-after { margin-bottom: 14px; }
  /* Ширину берём У ЯЧЕЙКИ, а не у текста: форма — сетка в две колонки, и
     inline-flex с фиксированными отступами не ужимался, а обрезался по
     overflow — «Следящий» уезжал за край (оператор, 18.09.2026). */
  .so-seg { display: flex; width: 100%; border: 1px solid #2d2d4a; border-radius: 6px; overflow: hidden; }
  .so-seg button { flex: 1 1 0; min-width: 0; white-space: nowrap;
    background: #0e0e1e; border: 0; color: #8a90a8; cursor: pointer;
    font: 600 12px/1 system-ui, sans-serif; padding: 10px 6px; }
  .so-seg button + button { border-left: 1px solid #2d2d4a; }
  .so-seg button:hover { color: #dfe6ff; background: #17172e; }
  /* Выбранное положение видно ИЗДАЛЕКА. Прежние #1b1b34 на #14142a отличались
     на один тон: оператор не понимал, включён стоп или подтягивающая, и жал
     кнопку по второму разу (18.09.2026). */
  .so-seg button.on { background: #1d3557; color: #dfe6ff; box-shadow: inset 0 -2px 0 #4f8bd6; }
  .so-in {
    background: #0e0e1e; border: 1px solid #2d2d4a; color: #e8e8f0;
    font: 20px/1.2 Consolas, 'Cascadia Mono', monospace; font-variant-numeric: tabular-nums;
    padding: 7px 10px; width: 100%; border-radius: 5px;
  }
  .so-in.text { font-size: 13px; }
  /* Выключенное поле обязано ВЫГЛЯДЕТЬ выключенным: без этого «Стоп» и
     «Подтягивающая» читались как бутафория (оператор, 18.09.2026). */
  .so-in:disabled { opacity: .45; cursor: not-allowed; }
  .so-enable { margin-top: 6px; background: none; border: 1px dashed #3a3a5a; border-radius: 4px;
    color: #8a90a8; cursor: pointer; font: 10px/1.3 system-ui, sans-serif; padding: 4px 6px;
    text-align: left; }
  .so-enable:hover { color: #e8e8f0; border-color: #4a4a7a; }
  /* Свой выпадающий список инструментов: показывает ВСЕ коды, а не подходящие
     под введённое, и порядок в нём — по частоте использования. */
  .so-code { position: relative; }
  .so-code .so-in { padding-right: 26px; }
  .so-code-btn { position: absolute; right: 1px; top: 1px; bottom: 1px; width: 24px;
    background: #14142a; border: 0; border-left: 1px solid #2d2d4a; color: #8a90a8;
    cursor: pointer; font-size: 12px; }
  .so-code-btn:hover { color: #e8e8f0; }
  .so-code-list { position: absolute; z-index: 20; left: 0; right: 0; top: calc(100% + 2px);
    margin: 0; padding: 2px; list-style: none; max-height: 220px; overflow: auto;
    background: #0e0e1e; border: 1px solid #2d2d4a; }
  .so-code-list button { display: block; width: 100%; text-align: left; background: none;
    border: 0; color: #d7dae8; cursor: pointer; padding: 5px 8px;
    font: 14px/1 Consolas, monospace; }
  .so-code-list button:hover { background: #1b1b34; color: #fff; }
  .so-code-list button.on { color: #7ef0a6; }
  /* Пара «в пунктах | ценой»: цена стоит СПРАВА от пунктов (оператор 18.09). */
  .so-two { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 2px; }
  .so-col { display: grid; gap: 4px; min-width: 0; margin-top: 8px; }
  .so-two .so-col { margin-top: 0; }
  .so-col > b { font: 600 10px/1.4 system-ui, sans-serif; color: #8a90a8;
    letter-spacing: .08em; text-transform: uppercase; display: flex; align-items: center; gap: 4px; }
  .so-calc { font-size: 10px; color: #7f86a6; font-style: normal; margin-top: 2px; }
  /* Итог группы: какая пара ног получилась и куда она уедет. */
  .so-pair { margin: 14px 0 0; padding-top: 10px; border-top: 1px solid #23233f;
    font-size: 11px; line-height: 1.5; color: #9aa1c0; }
  /* Значок подсказки: курсор мыши на нём — всплывает объяснение поля. */
  /* Единица прямо В ПОЛЕ: «п.» видно без наведения на «?». Поля ЦЕНЫ суффикса не
     получают — именно смешение цены и пунктов 18.09 оставило позицию без тейка. */
  .so-unit-wrap { position: relative; display: block; }
  .so-unit-wrap .so-in.pts { padding-right: 30px; }
  .so-unit { position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
    color: #6f7590; font: 12px/1 Consolas, monospace; pointer-events: none; }
  .so-q { width: 13px; height: 13px; border-radius: 50%; border: 1px solid #3a3a5e;
    background: #15152c; color: #9aa1c0; font: 700 9px/1 system-ui, sans-serif;
    cursor: help; padding: 0; flex: none; }
  .so-q:hover { border-color: #6f76a8; color: #dfe6ff; }
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
  /* Ключевые цифры — отдельными колонками крупно (15.09.2026): направление, объём
     и цена тонули в строке условия. Ширины фиксированы, чтобы колонки вставали
     одна под другой от карточки к карточке и глаз шёл по столбцу. */
  /* Заголовок стороны над её заявками: список идёт лестницей уровней, и глазу
     нужен якорь, где кончаются продажи и начинаются покупки. */
  .so-side-h { margin: 10px 0 4px; font: 700 11px/1 system-ui, sans-serif; letter-spacing: .1em;
    color: #ff9d90; display: flex; align-items: center; gap: 8px; }
  .so-side-h.buy { color: #7ef0a6; }
  .so-side-n { font-weight: 500; color: #8a90a8; }
  .so-c-dir { width: 84px; color: #ff9d90; font: 700 15px/1.1 system-ui, sans-serif; letter-spacing: .04em; }
  .so-c-dir.buy { color: #7ef0a6; }
  .so-c-qty { width: 44px; text-align: right; color: #e8e8f0; font: 700 18px/1.1 system-ui, sans-serif;
    font-variant-numeric: tabular-nums; }
  .so-c-px { min-width: 118px; color: #fff; font: 700 18px/1.1 system-ui, sans-serif;
    font-variant-numeric: tabular-nums; display: inline-flex; flex-direction: column; }
  .so-c-px small { font: 500 10px/1.2 system-ui, sans-serif; color: #8a90a8; letter-spacing: .04em; }
  .so-c-cond { color: #8a90a8; font-family: Consolas, monospace; font-size: 11px; }
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
  .so-c-status.warn { color: #ff9d90; font-weight: 700; }
  /* Под охраной терминала — другой цвет и щит: это не «взведена сторожем STL». */
  .so-c-status.native { color: #7ec8f0; }
  .so-c-bad { color: #ff9d90; font-weight: 600; }
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
