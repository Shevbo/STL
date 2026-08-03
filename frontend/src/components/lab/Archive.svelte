<!-- Архив разобранных роботов: закрытое дело со всеми материалами.
     Смысл вкладки — чтобы через полгода не начинать тот же разбор заново и не
     «переоткрывать» робота, которого уже признали безнадёжным. Поэтому в строке
     не только вердикт, но и чем он подкреплён: период, лучший финрез, сколько
     комбинаций просмотрено, что было на реале — и разворачиваемый отчёт целиком. -->
<script lang="ts">
  import { downloadCSV } from '$lib/csv';
  import { fetchWithAuth, errText } from '$lib/fetch-auth';
  import ScreenTag from './ScreenTag.svelte';

  let rows = $state<any[]>([]);
  let loading = $state(true);
  let error = $state('');
  let expanded = $state<string | null>(null);
  let adding = $state(false);
  let msg = $state('');
  let filter = $state('');

  const FORM_EMPTY = {
    robot_name: '', symbol: '', robot_id: '',
    bt_from: '', bt_to: '', bt_best_net: '', combos: '',
    verdict: '', real_from: '', real_to: '', real_net: '',
    report_md: '', report_url: '',
  };
  let form = $state({ ...FORM_EMPTY });

  const fmtDay = (iso: any) => iso
    ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' })
    : '—';
  const fmtRub = (v: any) => v == null ? '—'
    : (v >= 0 ? '+' : '') + Math.round(v).toLocaleString('ru-RU') + ' ₽';
  const period = (a: any, b: any) => (a || b) ? `${fmtDay(a)}—${fmtDay(b)}` : '—';

  const shown = $derived.by(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r: any) => [r.robot_name, r.symbol, r.verdict]
      .some((x: any) => String(x ?? '').toLowerCase().includes(q)));
  });

  async function load() {
    loading = true; error = '';
    try {
      const r = await fetchWithAuth('/api/v1/lab/archive');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      rows = await r.json();
    } catch (e) {
      error = 'Не удалось загрузить архив: ' + String(e);
    } finally {
      loading = false;
    }
  }

  async function save() {
    if (!form.robot_name.trim()) { msg = 'Название робота обязательно'; return; }
    msg = 'сохраняю…';
    try {
      const r = await fetchWithAuth('/api/v1/lab/archive', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      if (!r.ok) throw new Error(await errText(r));
      form = { ...FORM_EMPTY };
      adding = false; msg = '';
      await load();
    } catch (e) {
      msg = 'Ошибка: ' + String(e).slice(0, 200);
    }
  }

  async function remove(row: any) {
    if (!window.confirm(`Убрать «${row.robot_name}» из архива? Материалы разбора пропадут.`)) return;
    try {
      const r = await fetchWithAuth(`/api/v1/lab/archive/${encodeURIComponent(row.id)}`,
        { method: 'DELETE' });
      if (r.ok) await load();
    } catch { /* список перечитается вручную */ }
  }

  function csv() {
    downloadCSV(shown.map((r: any, i: number) => ({
      '№': i + 1, 'Робот': r.robot_name, 'Инструмент': r.symbol ?? '',
      'Списан': fmtDay(r.archived_at),
      'Бэктест с': fmtDay(r.bt_from), 'Бэктест по': fmtDay(r.bt_to),
      'Лучший финрез': r.bt_best_net ?? '', 'Комбинаций': r.combos ?? '',
      'Вердикт': r.verdict ?? '',
      'Реал с': fmtDay(r.real_from), 'Реал по': fmtDay(r.real_to),
      'Финрез на реале': r.real_net ?? '',
    })), 'robot-archive');
  }

  /** Лонгрид в ОТДЕЛЬНОМ окне (не вкладке): разбор читают рядом с терминалом.
   *  Размер задаём явно — иначе часть браузеров открывает окно в четверть экрана. */
  function openReport(row: any) {
    const w = Math.min(1180, Math.round(window.screen.availWidth * 0.72));
    const h = Math.round(window.screen.availHeight * 0.9);
    window.open(`/report.html?id=${encodeURIComponent(row.id)}`, `stl-report-${row.id}`,
      `width=${w},height=${h},left=${Math.round((window.screen.availWidth - w) / 2)},top=20`);
  }

  async function copyLink(row: any) {
    const url = `${location.origin}/report.html?id=${encodeURIComponent(row.id)}`;
    try { await navigator.clipboard.writeText(url); msg = 'Ссылка скопирована'; }
    catch { msg = url; }
  }

  $effect(() => { load(); });
</script>

<div class="arc">
  <ScreenTag id="ARCHIVE" name="Архив роботов" />

  <div class="arc-head">
    <div class="arc-title">🗄 Архив разобранных роботов
      <span class="arc-sub">закрытые дела: вердикт и чем он подкреплён</span>
    </div>
    <input class="arc-search" placeholder="фильтр: робот, инструмент, вердикт…" bind:value={filter} />
    <span class="arc-count">{shown.length} из {rows.length}</span>
    <button class="arc-btn" onclick={csv} disabled={!shown.length}>Выгрузить в CSV</button>
    <button class="arc-btn add" onclick={() => adding = !adding}>
      {adding ? '✕ Отмена' : '+ Списать робота'}
    </button>
  </div>

  {#if msg}<div class="arc-msg">{msg}</div>{/if}

  {#if adding}
    <div class="arc-form">
      <div class="arc-fgrid">
        <label>Название робота<input bind:value={form.robot_name} placeholder="Williams %R (GZU6)" /></label>
        <label>Инструмент<input bind:value={form.symbol} placeholder="GZU6" /></label>
        <label>ID робота<input bind:value={form.robot_id} placeholder="f4a9rbens4wbal1b89ns6iph" /></label>
        <label>Бэктест с<input type="date" bind:value={form.bt_from} /></label>
        <label>Бэктест по<input type="date" bind:value={form.bt_to} /></label>
        <label>Лучший финрез, ₽<input type="number" bind:value={form.bt_best_net} placeholder="-28401" /></label>
        <label>Комбинаций<input type="number" bind:value={form.combos} placeholder="2593" /></label>
        <label>Реал с<input type="date" bind:value={form.real_from} /></label>
        <label>Реал по<input type="date" bind:value={form.real_to} /></label>
        <label>Финрез на реале, ₽<input type="number" bind:value={form.real_net} placeholder="-2494" /></label>
        <label class="wide">Вердикт<input bind:value={form.verdict}
          placeholder="преимущества нет: валовый на круг ниже комиссии" /></label>
        <label class="wide">Ссылка на внешний отчёт<input bind:value={form.report_url}
          placeholder="необязательно" /></label>
      </div>
      <label class="arc-md">Подробный отчёт (markdown — хранится здесь, а не файлом)
        <textarea rows="8" bind:value={form.report_md}
          placeholder="Что проверяли, чем мерили, почему вердикт такой."></textarea>
      </label>
      <button class="arc-btn add" onclick={save}>Списать в архив</button>
    </div>
  {/if}

  {#if loading}
    <div class="arc-empty">Загрузка архива…</div>
  {:else if error}
    <div class="arc-err">{error}</div>
  {:else if !rows.length}
    <div class="arc-empty">
      Архив пуст. Сюда списывают роботов, по которым разбор ЗАКОНЧЕН — с периодом,
      числом просмотренных комбинаций и вердиктом, чтобы через полгода не начинать заново.
    </div>
  {:else}
    <div class="arc-scroll">
      <table class="arc-t">
        <thead>
          <tr>
            <th>№</th>
            <th>Робот</th>
            <th>Списан</th>
            <th>Бэктест: период</th>
            <th>Лучший финрез</th>
            <th>Комбинаций</th>
            <th>Вердикт</th>
            <th>Реал: период</th>
            <th>Финрез на реале</th>
            <th>Отчёт</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {#each shown as r, i (r.id)}
            <tr class="arc-row" id={r.id}>
              <td class="num">{i + 1}</td>
              <td class="name">{r.robot_name}{#if r.symbol}<span class="sym">{r.symbol}</span>{/if}</td>
              <td class="mono">{fmtDay(r.archived_at)}</td>
              <td class="mono">{period(r.bt_from, r.bt_to)}</td>
              <td class="mono num" class:pos={(r.bt_best_net ?? 0) > 0} class:neg={(r.bt_best_net ?? 0) < 0}>{fmtRub(r.bt_best_net)}</td>
              <td class="mono num">{r.combos == null ? '—' : r.combos.toLocaleString('ru-RU')}</td>
              <td class="verdict" title={r.verdict ?? ''}>{r.verdict ?? '—'}</td>
              <td class="mono">{period(r.real_from, r.real_to)}</td>
              <td class="mono num" class:pos={(r.real_net ?? 0) > 0} class:neg={(r.real_net ?? 0) < 0}>{fmtRub(r.real_net)}</td>
              <td class="rep">
                <!-- Лонгрид отдельным окном: разбор читают рядом с терминалом, а не
                     вместо него. Тот же вид, что у docs.html. -->
                <button class="arc-rep-btn" title="открыть отчёт лонгридом в отдельном окне"
                        onclick={() => openReport(r)}>Смотреть отчёт ↗</button>
                {#if r.report_md}
                  <button class="arc-link" onclick={() => expanded = expanded === r.id ? null : r.id}>
                    {expanded === r.id ? '▴ свернуть здесь' : '▾ бегло здесь'}
                  </button>
                {/if}
                {#if r.report_url}
                  <a class="arc-link" href={r.report_url} target="_blank" rel="noopener">внешний ↗</a>
                {/if}
              </td>
              <td class="tools">
                <button class="arc-x" title="скопировать ссылку на запись" onclick={() => copyLink(r)}>🔗</button>
                <button class="arc-x" title="убрать из архива" onclick={() => remove(r)}>✕</button>
              </td>
            </tr>
            {#if expanded === r.id && r.report_md}
              <tr class="arc-rep-row">
                <td colspan="11"><pre class="arc-rep">{r.report_md}</pre></td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .arc { height: 100%; overflow-y: auto; padding: 12px 14px; background: #0a0a15;
    display: flex; flex-direction: column; gap: 10px; position: relative; }
  .arc-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .arc-title { font-size: 13px; color: #cde; font-weight: 600; }
  .arc-sub { font-size: 10px; color: #667; font-weight: 400; margin-left: 6px; }
  .arc-search { background: #10101f; border: 1px solid #2d2d4a; color: #cde;
    border-radius: 4px; padding: 3px 8px; font-size: 11px; width: 220px; }
  .arc-count { font-size: 10px; color: #667; }
  .arc-btn { margin-left: auto; background: #16162c; border: 1px solid #2d2d4a; color: #cde;
    padding: 3px 10px; border-radius: 4px; cursor: pointer; font-size: 11px; }
  .arc-btn + .arc-btn { margin-left: 0; }
  .arc-btn:hover { border-color: #4caf50; }
  .arc-btn.add { color: #7fdba0; border-color: #2b5c3a; }
  .arc-msg { font-size: 11px; color: #ffb300; }
  .arc-err { font-size: 12px; color: #f44336; background: #1a0808;
    border: 1px solid #f4433644; border-radius: 5px; padding: 8px 12px; }
  .arc-empty { font-size: 12px; color: #667; padding: 18px 4px; line-height: 1.6; max-width: 720px; }

  .arc-form { border: 1px solid #2b5c3a; border-radius: 6px; padding: 10px;
    background: #0c1a12; display: flex; flex-direction: column; gap: 8px; }
  .arc-fgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 6px 10px; }
  .arc-form label { display: flex; flex-direction: column; gap: 2px; font-size: 10px; color: #8a9; }
  .arc-form label.wide { grid-column: 1 / -1; }
  .arc-form input, .arc-form textarea { background: #10101f; border: 1px solid #2d2d4a;
    color: #cde; border-radius: 3px; padding: 3px 6px; font-size: 11px; font-family: inherit; }
  .arc-md { display: flex; flex-direction: column; gap: 2px; font-size: 10px; color: #8a9; }

  .arc-scroll { overflow-x: auto; }
  .arc-t { width: 100%; border-collapse: collapse; font-size: 11px; }
  .arc-t th { text-align: left; color: #778; font-weight: 500; font-size: 10px;
    padding: 5px 8px; border-bottom: 1px solid #2d2d4a; white-space: nowrap; }
  .arc-t td { padding: 5px 8px; border-bottom: 1px solid #16162c; color: #cde; vertical-align: top; }
  .arc-row:hover td { background: #10101f; }
  .mono { font-family: monospace; white-space: nowrap; }
  .num { text-align: right; }
  .pos { color: #4caf50; }
  .neg { color: #f44336; }
  .name { font-weight: 600; }
  .sym { margin-left: 6px; font-size: 10px; font-weight: 400; color: #7ab8ff;
    background: #12203a; border: 1px solid #24406a; border-radius: 3px; padding: 0 5px; }
  .verdict { max-width: 320px; color: #ffb300; }
  .rep { white-space: nowrap; }
  .arc-rep-btn { background: #12203a; border: 1px solid #24406a; color: #9cf;
    font-size: 10px; border-radius: 3px; padding: 2px 8px; cursor: pointer; margin-right: 6px; }
  .arc-rep-btn:hover { border-color: #4dd0e1; color: #cff; }
  .arc-link { background: none; border: none; color: #7ab8ff; cursor: pointer;
    font-size: 10px; padding: 0; text-decoration: none; }
  .arc-link:hover { text-decoration: underline; }
  .tools { white-space: nowrap; }
  .arc-x { background: none; border: 1px solid #2d2d4a; color: #667; cursor: pointer;
    font-size: 10px; border-radius: 3px; padding: 0 5px; }
  .arc-x:hover { color: #f44336; border-color: #f4433666; }
  .arc-rep-row td { background: #080810; }
  .arc-rep { margin: 0; padding: 10px 14px; font: 11px/1.55 Consolas, "Courier New", monospace;
    color: #9ab; white-space: pre-wrap; word-break: break-word; max-height: 420px; overflow-y: auto; }
</style>
