/** Замечать, что вкладка работает на УСТАРЕВШЕЙ сборке.
 *
 * nginx отдаёт index.html без Cache-Control (ассеты — с годовым immutable, у них
 * хэш в имени). Браузер вправе держать старую страницу, и она тянет старый бандл:
 * 06.08.2026 оператор полдня смотрел на вчерашний код и говорил «не работает», а
 * правки были выкачены. Заголовок на сервере — правильное лечение, но пока его нет,
 * приложение обязано замечать это само: молча показывать старое хуже, чем сказать.
 *
 * Механика: раз в `periodMs` тянем index.html БЕЗ кэша и сравниваем имя главного
 * бандла с тем, что реально загружен. Разошлись — зовём onStale ровно один раз.
 */
export function currentBundle(doc: Document = document): string | null {
  const s = Array.from(doc.querySelectorAll('script[src]'))
    .map((e) => (e as HTMLScriptElement).src)
    .find((u) => /\/assets\/index-[^/]+\.js$/.test(u));
  return s ? s.split('/').pop()! : null;
}

export function bundleFromHtml(html: string): string | null {
  const m = html.match(/assets\/(index-[A-Za-z0-9_-]+\.js)/);
  return m ? m[1] : null;
}

export function startVersionWatch(onStale: (fresh: string) => void,
                                  periodMs = 120_000): () => void {
  const mine = currentBundle();
  if (!mine) return () => {};
  let stopped = false;
  let fired = false;
  const tick = async () => {
    if (stopped || fired) return;
    try {
      const r = await fetch('/index.html', { cache: 'no-store' });
      if (!r.ok) return;
      const fresh = bundleFromHtml(await r.text());
      if (fresh && fresh !== mine) { fired = true; onStale(fresh); }
    } catch { /* сеть моргнула — проверим в следующий раз */ }
  };
  const id = setInterval(tick, periodMs);
  void tick();
  return () => { stopped = true; clearInterval(id); };
}
