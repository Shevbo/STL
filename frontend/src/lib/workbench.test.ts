// Рабочее место бэктеста: логика экрана редакций.
//
// Заказ оператора 04.10.2026. Правила на сервере, здесь проверяется то, что экран
// говорит человеку: ПОЧЕМУ кнопка не работает, и что «зелёное» значит ровно то же, что
// на сервере (строка "true" не зелёная).
import { describe, it, expect } from 'vitest';
import {
  MESSAGE_MAX, canAccept, canCreate, errorText, gatesSummary, isOpen, messageError,
  statusLabel, statusTone, workerLine, type Revision, type WorkerState,
} from './workbench';

const W = (o: Partial<WorkerState> = {}): WorkerState =>
  ({ alive: true, age_s: 12, busy_with: null, version: 'v1', worker_id: 'w1', ...o });
const R = (o: Partial<Revision> = {}): Revision =>
  ({ id: 1, card: 'c', rev: 1, parent: null, status: 'ready', ...o });

describe('воркер одной фразой', () => {
  it('жив — с версией и возрастом сигнала', () => {
    expect(workerLine(W())).toBe('воркер жив (v1), сигнал 12 с назад');
  });

  it('молчит — минуты, а не «жив»', () => {
    expect(workerLine(W({ alive: false, age_s: 420 }))).toBe('воркер не отвечает: последний сигнал 7 мин назад');
  });

  it('не появлялся ни разу — это не «упал»', () => {
    expect(workerLine(W({ alive: false, age_s: null }))).toBe('воркер ещё ни разу не выходил на связь');
  });

  it('состояния нет — так и говорим', () => {
    expect(workerLine(null)).toContain('неизвестно');
  });
});

describe('можно ли создать редакцию', () => {
  it('воркер жив и ничего не в работе — можно', () => {
    expect(canCreate(W(), [R({ status: 'accepted' })], true)).toEqual({ ok: true, why: '' });
  });

  it('воркер молчит — нельзя, и причина та же, что у сервера', () => {
    // Без воркера редакция встала бы в очередь и выглядела «в работе».
    const c = canCreate(W({ alive: false, age_s: 420 }), [], true);
    expect(c.ok).toBe(false);
    expect(c.why).toContain('не отвечает');
  });

  it('в карточке уже есть редакция в работе — нельзя (одна рабочая на карточку)', () => {
    for (const s of ['queued', 'working', 'gates']) {
      const c = canCreate(W(), [R({ rev: 3, status: s })], true);
      expect(c.ok, s).toBe(false);
      expect(c.why).toContain('редакция 3');
    }
  });

  it('готовая, принятая и упавшая редакции создать не мешают', () => {
    for (const s of ['ready', 'accepted', 'failed']) {
      expect(canCreate(W(), [R({ status: s })], true).ok, s).toBe(true);
    }
  });

  it('пока состояние не загрузилось — нельзя, и это сказано', () => {
    const c = canCreate(null, [], false);
    expect(c.ok).toBe(false);
    expect(c.why).toContain('не загружено');
  });
});

describe('статусы', () => {
  it('человеческие названия', () => {
    expect(statusLabel('queued')).toBe('в очереди');
    expect(statusLabel('ready')).toBe('готова к приёмке');
  });

  it('новый статус сервера печатается кодом, а не исчезает', () => {
    expect(statusLabel('paused_by_worker')).toBe('paused_by_worker');
    expect(statusTone('paused_by_worker')).toBe('unk');
  });

  it('в работе — queued, working, gates', () => {
    expect(['queued', 'working', 'gates'].every((s) => isOpen({ status: s }))).toBe(true);
    expect(['ready', 'failed', 'accepted'].some((s) => isOpen({ status: s }))).toBe(false);
  });
});

describe('ворота', () => {
  it('зелёное — строго ok === true; строка и пустое не считаются', () => {
    const g = gatesSummary({ pytest: { ok: true }, ruff: { ok: 'true' }, flat: 'ok', none: null });
    expect(g.total).toBe(4);
    expect(g.green).toBe(1);
  });

  it('ворот нет — total 0, а не «0 из 0 зелёных»', () => {
    expect(gatesSummary(null)).toEqual({ total: 0, green: 0, items: [] });
  });
});

describe('приёмка', () => {
  const ok = R({ diff: 'diff --git a b', diff_sha: 'abc', gates: { pytest: { ok: true } } });

  it('готовая, с diff и зелёными воротами, после просмотра — можно', () => {
    expect(canAccept(ok, true)).toEqual({ ok: true, why: '' });
  });

  it('без отметки «просмотрел diff» нельзя', () => {
    const c = canAccept(ok, false);
    expect(c.ok).toBe(false);
    expect(c.why).toContain('просмотрели diff');
  });

  it('не готовая — нельзя, статус назван словами', () => {
    const c = canAccept(R({ ...ok, status: 'working' }), true);
    expect(c.ok).toBe(false);
    expect(c.why).toContain('воркер правит');
  });

  it('красные или пустые ворота — нельзя', () => {
    expect(canAccept({ ...ok, gates: { pytest: { ok: false } } }, true).ok).toBe(false);
    expect(canAccept({ ...ok, gates: null }, true).ok).toBe(false);
  });

  it('нет diff — принимать нечего', () => {
    expect(canAccept({ ...ok, diff: null }, true).why).toContain('нет diff');
  });

  it('уже принятая — нельзя второй раз', () => {
    expect(canAccept({ ...ok, status: 'accepted' }, true).why).toBe('уже принята');
  });
});

describe('ошибки сервера', () => {
  it('текст сервера главнее нашего', () => {
    expect(errorText(409, { detail: { code: 'busy', text: 'В карточке уже есть редакция 2 в работе' } }))
      .toBe('В карточке уже есть редакция 2 в работе');
    expect(errorText(403, { detail: 'Принимать редакции может только оператор.' }))
      .toBe('Принимать редакции может только оператор.');
  });

  it('нет тела — код ответа, а не пустота', () => {
    expect(errorText(502, null)).toBe('HTTP 502');
  });
});

describe('сообщение оператора', () => {
  it('пустое и слишком длинное отклоняются заранее', () => {
    expect(messageError('')).toContain('Опишите');
    expect(messageError('   ')).toContain('Опишите');
    expect(messageError('x'.repeat(MESSAGE_MAX + 1))).toContain('Длиннее');
    expect(messageError('добавь фильтр')).toBe('');
  });
});
