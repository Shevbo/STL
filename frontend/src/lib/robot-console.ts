/** Чистая логика «Системного монитора» робота — общая для агентского и бумажного
 *  стендов. Жила в модульном блоке AgentRobotScreen, из-за чего бумажный стенд
 *  монитора не получил вовсе (05.08.2026: оператор сравнил два стенда и не нашёл
 *  ни консоли, ни журнала доходности). Держать в одном месте — единственный способ
 *  не разъехаться снова. */

export type LogKind = 'cmd' | 'ok' | 'err' | 'sys' | 'me' | 'ai';
export type LogLine = { t: number; kind: LogKind; text: string };
/** Снимок состояния робота для ленты переходов. */
export type RobotSnap = { mode: string; run: string; eo: boolean };

/** Лог из localStorage: мусор и битый JSON дают пустую ленту, хвост обрезаем. */
export function parseLog(raw: string | null, max = 1000): LogLine[] {
  try {
    const v = JSON.parse(raw || '[]');
    if (!Array.isArray(v)) return [];
    return v.filter((l) => l && typeof l.text === 'string' && typeof l.t === 'number').slice(-max);
  } catch { return []; }
}

/** Добавление строки с ограничением длины (лента живёт в localStorage). */
export function appendLog(lines: LogLine[], line: LogLine, max = 1000): LogLine[] {
  return [...lines, line].slice(-max);
}

/** Прилипание к низу: пока оператор не отскроллил вверх, новая строка видна сама. */
export function stickToBottom(scrollHeight: number, scrollTop: number, clientHeight: number): boolean {
  return scrollHeight - scrollTop - clientHeight < 24;
}

/**
 * Строки о СМЕНЕ состояния робота — именно их оператор не мог прочитать по
 * бейджам (РЕАЛ + ПАУЗА + «Развёрнут в PAPER» одновременно, 30.07.2026).
 * Первый снимок молчит: это не переход.
 */
export function stateTransitions(prev: RobotSnap | null, next: RobotSnap): string[] {
  if (!prev) return [];
  const out: string[] = [];
  if (prev.mode !== next.mode) out.push(`Режим робота: ${prev.mode} → ${next.mode}.`);
  if (prev.run !== next.run) out.push(`Состояние: ${prev.run} → ${next.run}.`);
  if (prev.eo !== next.eo)
    out.push(next.eo ? 'Робот перешёл в «только на выход».' : 'Робот вернулся в обычный режим.');
  return out;
}
