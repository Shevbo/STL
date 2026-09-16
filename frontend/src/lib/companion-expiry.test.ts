// Экспирация в компаньонах (docs/design/expiry-roll.md, раздел 6): карточка кампании
// перекладки контрактов и теги состояния у роботов. Блок snapshot.expiry приходит,
// только пока кампания активна, поэтому «нет блока» обязано означать «ничего не
// рисуем», а не пустую карточку. Держим обе страницы: правка одной без другой уже
// стоила оператору повторной жалобы.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const page = (f: string) => readFileSync(resolve(process.cwd(), 'public', f), 'utf8')
  .replace(/\r\n/g, '\n');

const EXP = {
  base: 'RI', old: 'RIU6', new: 'RIZ6', state: 'exiting',
  deadline_ms: Date.now() + 72 * 60_000,
  robots: [
    { name: 'fvg-1', state: 'switched', position: 0, note: '' },
    { name: 'boll-2', state: 'exit_wait', position: -2, note: '' },
    { name: 'usopen', state: 'needs_operator', position: -2, note: 'не вышел к дедлайну' },
  ],
  manual: { old_net: 4, new_net: 0, state: 'ждёт' },
};

function fns(html: string) {
  const grab = (name: string) => {
    const m = html.match(new RegExp('function ' + name + '\\([^)]*\\) \\{[\\s\\S]*?\\n\\}\\n'));
    if (!m) throw new Error('не нашёл ' + name);
    return m[0];
  };
  const consts = html.match(/const EXP_TAG = \{[\s\S]*?\n\};\n/)![0];
  const src = consts + grab('expiryTag') + grab('expiryLeft') + grab('renderExpiry');
  const deps = { esc: (x: unknown) => String(x ?? ''), STL: 'https://stl' };
  return new Function('d', `with (d) { ${src}; return { expiryTag, expiryLeft, renderExpiry }; }`)(deps);
}

describe.each(['companion.html', 'm.html'])('%s — экспирация', (file) => {
  const { expiryTag, expiryLeft, renderExpiry } = fns(page(file));

  it('тег робота говорит его состояние, у переключённого — новый контракт', () => {
    expect(expiryTag('fvg-1', EXP)).toContain('переключён RIZ6');
    expect(expiryTag('boll-2', EXP)).toContain('ждёт выхода');
    expect(expiryTag('usopen', EXP)).toContain('не вышел');
    expect(expiryTag('usopen', EXP)).toContain('exp-bad');
  });

  it('робота нет в кампании или кампании нет — тега нет', () => {
    expect(expiryTag('чужой', EXP)).toBe('');
    expect(expiryTag('fvg-1', null)).toBe('');
    expect(expiryTag('fvg-1', { robots: [] })).toBe('');
  });

  it('карточка: серия, счёт переключённых и застрявший робот отдельной строкой', () => {
    const out = renderExpiry(EXP);
    expect(out).toContain('RIU6 → RIZ6');
    expect(out).toContain('переключено 1/3');
    expect(out).toContain('не вышел к дедлайну');      // note робота, а не общий текст
    expect(out).toContain('RIU6 4 → RIZ6 0');          // ручная позиция
  });

  it('нет кампании — карточки нет вовсе', () => {
    expect(renderExpiry(null)).toBe('');
    expect(renderExpiry({})).toBe('');
  });

  it('срок считается от дедлайна кампании и не врёт после него', () => {
    expect(expiryLeft(Date.now() + 72 * 60_000)).toBe(', до дедлайна 1 ч 12 мин');
    expect(expiryLeft(Date.now() - 60_000)).toBe(', срок вышел');
    expect(expiryLeft(0)).toBe('');
  });
});
