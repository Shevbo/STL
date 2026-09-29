// Профили доведения до исполнения. Главное, что здесь сторожится, — РАЗНИЦА
// между «нормальным» и нулевыми фазами: движок различает их принципиально
// (ноль = сразу по рынку, normal = по рынку никогда), и написать их одинаково
// значит спрятать от оператора, кто отвечает за неисполнение.
import { describe, it, expect } from 'vitest';
import {
  BUILTIN, DEFAULT_PROFILE, guaranteeText, guaranteedSec, longWaitWarn,
  profileError, profileNames, profileOf, type ExecProfiles,
} from './exec-profiles';

const P: ExecProfiles = {
  aggressive: { hold_sec: 10, chase_sec: 10, chase_every_sec: 2, market: true,
                title: 'агрессивный: 10 + 10 с, дальше рынок' },
  active: { hold_sec: 180, chase_sec: 180, chase_every_sec: 30, market: true,
            title: 'активный: 3 + 3 мин, дальше рынок' },
  normal: { hold_sec: 0, chase_sec: 0, chase_every_sec: 0, market: false,
            title: 'нормальный: заявка стоит лимитом, как было' },
};

describe('гарантия исполнения словами', () => {
  it('штатный профиль называет итог в секундах', () => {
    expect(guaranteedSec(P.aggressive)).toBe(20);
    expect(guaranteeText(P.aggressive)).toContain('20 с');
  });

  it('длинные фазы читаются минутами, а не 360 секундами', () => {
    expect(guaranteedSec(P.active)).toBe(360);
    expect(guaranteeText(P.active)).toContain('6 мин');
  });

  // Разница, ради которой profile вообще существует.
  it('НОРМАЛЬНЫЙ это «по рынку никогда», а не ноль секунд', () => {
    expect(guaranteedSec(P.normal)).toBeNull();
    const t = guaranteeText(P.normal);
    expect(t).toContain('НИКОГДА');
    expect(t).toContain('отвечает');
    expect(t).not.toContain('0 с');
  });

  it('нулевые фазы с добиванием — это «сразу по рынку»', () => {
    const t = guaranteeText({ hold_sec: 0, chase_sec: 0, chase_every_sec: 0, market: true, title: '' });
    expect(t).toContain('СРАЗУ');
    expect(t).not.toContain('НИКОГДА');
  });
});

describe('предупреждение о длинном ожидании', () => {
  it('штатные 10 + 10 с не предупреждают: иначе строку перестанут читать', () => {
    expect(longWaitWarn(P.aggressive)).toBe('');
  });

  it('3 + 3 мин — предупреждаем следствием выбора', () => {
    expect(longWaitWarn(P.active)).toContain('6 мин');
  });

  it('у нормального ждать нечего: числа нет', () => {
    expect(longWaitWarn(P.normal)).toBe('');
  });
});

describe('какой профиль у заявки', () => {
  it('пустое поле — штатный по умолчанию', () => {
    expect(profileOf(P, '')).toBe(P[DEFAULT_PROFILE]);
    expect(profileOf(P, undefined)).toBe(P[DEFAULT_PROFILE]);
  });

  // Движок при незнакомом имени откатывается к штатному, панель обязана
  // показывать ТО ЖЕ САМОЕ, а не пустое место: опечатка не лишает доведения.
  it('незнакомое имя — штатный, как и в движке', () => {
    expect(profileOf(P, 'agressive')).toBe(P[DEFAULT_PROFILE]);
  });
});

describe('порядок в селекте', () => {
  it('штатные в своём порядке, свои — по алфавиту', () => {
    const names = profileNames({ ...P, мой: P.active, авто: P.active });
    expect(names.slice(0, 3)).toEqual(BUILTIN);
    expect(names.slice(3)).toEqual(['авто', 'мой']);
  });
});

describe('правка профиля', () => {
  const ok = { hold_sec: 5, chase_sec: 5, chase_every_sec: 1, market: true, title: 'свой' };

  it('корректный профиль проходит', () => {
    expect(profileError(ok)).toBe('');
  });

  it('отрицательные и дробные секунды не пропускаем', () => {
    expect(profileError({ ...ok, hold_sec: -1 })).toContain('отрицательными');
    expect(profileError({ ...ok, chase_sec: 1.5 })).toContain('целые');
  });

  // Фаза, которая ничего не делает, хуже отсутствующей: оператор думает, что
  // заявка идёт за ценой, а она просто стоит.
  it('погоня без шага перестановки — отказ', () => {
    expect(profileError({ ...ok, chase_sec: 60, chase_every_sec: 0 })).toContain('просто стоять');
  });

  it('без подписи не сохраняем: по ней профиль выбирают на форме', () => {
    expect(profileError({ ...ok, title: '  ' })).toContain('подпись');
  });
});
