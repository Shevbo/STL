export async function fetchWithAuth(url: string, options: RequestInit = {}): Promise<Response> {
  // Auth rides on the HttpOnly session cookie (credentials: 'include'). We no longer
  // read a token from localStorage or send a Bearer header — keeping the session out
  // of JS-reachable storage so XSS cannot lift it.
  return fetch(url, { ...options, credentials: 'include' });
}

/** Human message for a non-ok Response. Error bodies come in three flavours: FastAPI
 *  JSON {"detail": …}, plain text, and — while uvicorn is down/restarting — a full
 *  nginx HTML error page. Dumping that HTML into the UI printed six lines of
 *  "<!-- a padding to disable MSIE … -->" as a screen's ENTIRE content (502 during a
 *  backend restart, 03.08.2026). One helper so every caller says what happened. */
export async function errText(res: Response): Promise<string> {
  const body = (await res.text().catch(() => '')).trim();
  if (!body || body.startsWith('<')) {
    return res.status >= 502 && res.status <= 504
      ? `Бэкенд STL недоступен (${res.status}) — идёт перезапуск, повтор через 15 с.`
      : `Ошибка сервера ${res.status} ${res.statusText}`.trim();
  }
  try {
    const d = JSON.parse(body)?.detail;
    if (d) return typeof d === 'string' ? d : JSON.stringify(d);
  } catch { /* not JSON — plain text below */ }
  return body.slice(0, 300);
}
