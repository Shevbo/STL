<!-- Botstore.svelte — two panels:
     LEFT  = tested catalog (robots found/optimized in background campaigns)
     RIGHT = robots installed on the platform (real DB robots)
     Middle action: "установить на платформу" (create a robot from the template +
     best params). Installed robots have "удалить с платформы". -->
<script lang="ts">
  import { fetchWithAuth } from '../../lib/fetch-auth';

  let loading = $state(true);
  let error = $state('');
  let initialEquity = $state(100000);
  let catalog = $state<any[]>([]);     // tested strategies (left)
  let installed = $state<any[]>([]);   // platform robots (right)
  let strategies = $state<any[]>([]);  // templates (for script_code/schema)
  let stlLinks = $state<any[]>([]);
  let selectedCat = $state<string | null>(null);  // selected catalog strategy id
  let busy = $state(false);
  let notice = $state('');

  const fmtPct = (v: any) => v != null ? (v * 100).toFixed(2) + '%' : '—';
  const fmtMoney = (v: any) => v != null ? Math.round(v).toLocaleString('ru-RU') + ' ₽' : '—';
  const fmtNum = (v: any, d = 2) => v != null ? Number(v).toFixed(d) : '—';
  const fmtDate = (v: any) => v ? new Date(v).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

  function best(robot: any) {
    const rs = robot.results ?? [];
    return rs.length ? rs[0] : null;
  }
  let totalVariants = $derived(catalog.reduce((s, r) => s + (r.variants_tested || 0), 0));

  async function load() {
    loading = true; error = '';
    try {
      const [bs, robs, strs, links] = await Promise.all([
        fetchWithAuth('/api/v1/botstore').then(r => r.ok ? r.json() : null),
        fetchWithAuth('/api/v1/robots').then(r => r.ok ? r.json() : []),
        fetchWithAuth('/api/v1/strategies').then(r => r.ok ? r.json() : []),
        fetchWithAuth('/api/v1/stl-links').then(r => r.ok ? r.json() : []),
      ]);
      if (bs) {
        initialEquity = bs.initial_equity ?? 100000;
        catalog = (bs.catalog ?? []).filter((c: any) => (c.results?.length ?? 0) > 0);
      }
      installed = robs ?? [];
      strategies = strs ?? [];
      stlLinks = links ?? [];
    } catch (e) {
      error = String(e);
    }
    loading = false;
  }

  // Install a tested strategy onto the platform: create a robot from its template
  // (script_code + schema) seeded with the best params found.
  async function install(robot: any) {
    const b = best(robot);
    const tmpl = strategies.find(s => s.id === robot.id);
    if (!tmpl) { notice = `Нет шаблона стратегии для ${robot.name}`; return; }
    if (!stlLinks.length) { notice = 'Нет STL Link (коннектора счёта). Создайте его сначала.'; return; }
    busy = true; notice = '';
    try {
      const params = b?.params ? { ...b.params } : { ...(tmpl.default_params ?? {}) };
      if (b?.symbol) params.symbol = b.symbol;
      const body = {
        userEmail: 'admin',
        stlLinkId: stlLinks[0]?.id ?? '',
        name: `${robot.name} (${params.symbol ?? ''})`.trim(),
        scriptCode: tmpl.script_code,
        paramsJson: params,
        schedule: '09:00-23:55',
      };
      const res = await fetchWithAuth('/api/v1/robots', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      notice = `Установлен на платформу: ${body.name}`;
      await load();
    } catch (e) {
      notice = 'Ошибка установки: ' + String(e);
    }
    busy = false;
  }

  async function remove(r: any) {
    if (!confirm(`Удалить робота «${r.name}» с платформы? Его сделки и метрики будут удалены.`)) return;
    busy = true; notice = '';
    try {
      const res = await fetchWithAuth(`/api/v1/robots/${r.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(await res.text());
      notice = `Удалён с платформы: ${r.name}`;
      await load();
    } catch (e) {
      notice = 'Ошибка удаления: ' + String(e);
    }
    busy = false;
  }

  function symbolOf(r: any) {
    const pj = r.params_json;
    return (typeof pj === 'object' ? pj?.symbol : null) ?? '';
  }

  $effect(() => { load(); });
</script>

<div class="bs-root">
  <div class="bs-preamble">
    <div class="bs-title">Botstore — каталог и установка роботов</div>
    <div class="bs-note">
      Слева — что найдено и протестировано на истории (вариантов: <b>{totalVariants.toLocaleString('ru-RU')}</b>).
      Справа — роботы, установленные на платформе. Доходность от первоначальных инвестиций
      <b>{initialEquity.toLocaleString('ru-RU')} ₽</b>, в рублях по реальной стоимости пункта и ГО (MOEX ISS).
      Результаты — in-sample, без walk-forward: шортлист для форвард-теста, не для слепого запуска.
    </div>
    {#if notice}<div class="bs-notice">{notice}</div>{/if}
  </div>

  {#if loading}
    <div class="bs-msg">Загрузка…</div>
  {:else if error}
    <div class="bs-msg err">{error}</div>
  {:else}
    <div class="bs-cols">
      <!-- LEFT: tested catalog -->
      <div class="bs-col">
        <div class="bs-col-head">Протестировано ({catalog.length})</div>
        <div class="bs-list">
          {#if catalog.length === 0}
            <div class="bs-empty">Фоновые прогоны ещё не накопили результатов.</div>
          {/if}
          {#each catalog as robot}
            {@const b = best(robot)}
            <div class="cat-card" class:sel={selectedCat === robot.id}
                 role="button" tabindex="0"
                 onclick={() => selectedCat = robot.id}
                 onkeydown={(e) => e.key === 'Enter' && (selectedCat = robot.id)}>
              <div class="cc-top">
                <span class="cc-name">{robot.name}</span>
                <span class="cc-variants">{(robot.variants_tested || 0).toLocaleString('ru-RU')} вар.</span>
              </div>
              <div class="cc-metrics">
                <span class="cc-inst">{b?.symbol ?? '—'}</span>
                <span class:pos={b?.total_return > 0} class:neg={b?.total_return < 0}>{fmtPct(b?.total_return)}</span>
                <span class:pos={b?.net_profit > 0} class:neg={b?.net_profit < 0}>{fmtMoney(b?.net_profit)}</span>
                <span class="cc-dd">просадка {fmtPct(b?.max_drawdown)}</span>
                <span class="cc-rf">RF {fmtNum(b?.recovery_factor)}</span>
              </div>
              {#if b?.params}<div class="cc-params">{JSON.stringify(b.params)}</div>{/if}
              <div class="cc-foot">
                <span class="cc-run">прогон {fmtDate(robot.last_run)}</span>
                <button class="cc-install" disabled={busy} onclick={(e) => { e.stopPropagation(); install(robot); }}>
                  Установить на платформу →
                </button>
              </div>
            </div>
          {/each}
        </div>
      </div>

      <!-- RIGHT: installed robots -->
      <div class="bs-col">
        <div class="bs-col-head">Установлено на платформе ({installed.length})</div>
        <div class="bs-list">
          {#if installed.length === 0}
            <div class="bs-empty">Нет установленных роботов. Установите слева.</div>
          {/if}
          {#each installed as r}
            <div class="inst-card">
              <div class="ic-top">
                <span class="ic-dot" class:live={r.deployed}></span>
                <span class="ic-name">{r.name}</span>
                <span class="ic-badge" class:on={r.deployed}>{r.deployed ? 'LIVE' : 'остановлен'}</span>
              </div>
              <div class="ic-meta">
                <span class="ic-inst">{symbolOf(r)}</span>
                <span class="ic-sched">окно {r.schedule}</span>
              </div>
              <div class="ic-params">{JSON.stringify(r.params_json)}</div>
              <div class="ic-foot">
                <button class="ic-remove" disabled={busy} onclick={() => remove(r)}>Удалить с платформы</button>
              </div>
            </div>
          {/each}
        </div>
      </div>
    </div>
    <button class="bs-refresh" onclick={load} disabled={busy}>Обновить</button>
  {/if}
</div>

<style>
  .bs-root { display: flex; flex-direction: column; height: 100%; overflow: hidden; background: #0a0a15; padding: 12px; gap: 10px; }
  .bs-preamble { background: #0f0f1e; border: 1px solid #2d2d4a; border-radius: 5px; padding: 10px 12px; flex-shrink: 0; }
  .bs-title { font-size: 13px; color: #4caf50; font-weight: 600; margin-bottom: 6px; }
  .bs-note { font-size: 11px; color: #888; line-height: 1.5; }
  .bs-note b { color: #ccc; }
  .bs-notice { margin-top: 6px; font-size: 11px; color: #6aa8ff; }

  .bs-msg { font-size: 12px; color: #666; padding: 20px; text-align: center; }
  .bs-msg.err { color: #f44336; }

  .bs-cols { display: flex; gap: 10px; flex: 1; min-height: 0; }
  .bs-col { flex: 1; display: flex; flex-direction: column; min-width: 0; border: 1px solid #1e1e3a; border-radius: 5px; overflow: hidden; }
  .bs-col-head { font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; padding: 7px 10px; background: #0f0f1e; border-bottom: 1px solid #1e1e3a; flex-shrink: 0; }
  .bs-list { flex: 1; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 7px; }
  .bs-empty { font-size: 11px; color: #555; padding: 16px; text-align: center; font-style: italic; }

  /* catalog card */
  .cat-card { background: #0f0f1e; border: 1px solid #2d2d4a; border-radius: 4px; padding: 8px 10px; cursor: pointer; }
  .cat-card:hover { border-color: #3d3d5a; }
  .cat-card.sel { border-color: #4caf5066; background: #0c160c; }
  .cc-top { display: flex; justify-content: space-between; align-items: baseline; }
  .cc-name { font-size: 12px; color: #ccc; font-weight: 600; }
  .cc-variants { font-size: 10px; color: #6aa8ff; }
  .cc-metrics { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; font-size: 11px; color: #999; }
  .cc-inst { color: #6aa8ff; font-family: monospace; }
  .cc-dd, .cc-rf { color: #777; font-size: 10px; }
  .cc-params { margin-top: 4px; font-family: monospace; font-size: 9px; color: #666; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cc-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 6px; }
  .cc-run { font-size: 9px; color: #555; }
  .cc-install { padding: 3px 9px; background: #4caf5018; border: 1px solid #4caf5066; color: #4caf50; border-radius: 3px; font-size: 10px; cursor: pointer; white-space: nowrap; }
  .cc-install:hover { background: #4caf5030; }
  .cc-install:disabled { opacity: 0.5; cursor: default; }

  /* installed card */
  .inst-card { background: #0f0f1e; border: 1px solid #2d2d4a; border-radius: 4px; padding: 8px 10px; }
  .ic-top { display: flex; align-items: center; gap: 7px; }
  .ic-dot { width: 6px; height: 6px; border-radius: 50%; background: #333; flex-shrink: 0; }
  .ic-dot.live { background: #4caf50; box-shadow: 0 0 4px #4caf5088; }
  .ic-name { flex: 1; font-size: 12px; color: #ccc; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ic-badge { font-size: 9px; color: #666; }
  .ic-badge.on { color: #4caf50; }
  .ic-meta { display: flex; gap: 10px; margin-top: 4px; font-size: 10px; color: #888; }
  .ic-inst { color: #6aa8ff; font-family: monospace; }
  .ic-params { margin-top: 4px; font-family: monospace; font-size: 9px; color: #666; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ic-foot { display: flex; justify-content: flex-end; margin-top: 6px; }
  .ic-remove { padding: 3px 9px; background: #1a0a0a; border: 1px solid #f4433655; color: #f44336; border-radius: 3px; font-size: 10px; cursor: pointer; }
  .ic-remove:hover { background: #2a1010; }
  .ic-remove:disabled { opacity: 0.5; cursor: default; }

  .pos { color: #4caf50; }
  .neg { color: #f44336; }
  .bs-refresh { align-self: flex-start; padding: 5px 14px; background: #1a1a2e; border: 1px solid #2d2d4a; color: #aaa; border-radius: 3px; font-size: 11px; cursor: pointer; flex-shrink: 0; }
  .bs-refresh:hover { color: #4caf50; border-color: #4caf5066; }
  .bs-refresh:disabled { opacity: 0.5; }
</style>
