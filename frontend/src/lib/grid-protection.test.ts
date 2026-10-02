// Защита сетки: одна формулировка на форму взвода и на карточку заявки.
//
// Сетка опасна не в боковике, а в тренде — она добирает против хода. Защита
// переводит её в «только на выход» по безубытку. Экран обязан сказать, при
// каком условии это случится, и сказать вслух, когда защиты НЕТ.
import { describe, it, expect } from 'vitest';
import { gridProtectionText, preview } from './smart-order-help';

describe('формулировка защиты', () => {
  it('оба условия: филлы ИЛИ уход цены', () => {
    const t = gridProtectionText({ g_trig_fills: 3, g_trig_move_pct: 0.25, g_trig_touches: 1 });
    expect(t).toContain('3 уровня исполнено');
    expect(t).toContain('или');
    expect(t).toContain('0.25%');
    expect(t).toContain('без убытка');
  });

  it('касания больше одного названы: одиночный выброс сетку не снимает', () => {
    expect(gridProtectionText({ g_trig_move_pct: 0.25, g_trig_touches: 3 }))
      .toContain('3 раза с одной стороны');
  });

  it('одно касание не упоминается: это и есть «на первом же»', () => {
    expect(gridProtectionText({ g_trig_move_pct: 0.25, g_trig_touches: 1 }))
      .not.toContain('с одной стороны');
  });

  it('перевзведение названо сроком, а не фактом', () => {
    expect(gridProtectionText({ g_trig_fills: 3, g_rearm_min: 30 }))
      .toContain('через 30 минут');
    expect(gridProtectionText({ g_trig_fills: 3, g_rearm_min: 0 }))
      .not.toContain('снова');
  });

  it('ОБА УСЛОВИЯ НУЛЯМИ — говорим, что защиты нет', () => {
    // Молчание здесь читалось бы как «всё под контролем», а сетка без защиты и
    // без стопа добирает против тренда, пока хватает денег.
    const t = gridProtectionText({ g_trig_fills: 0, g_trig_move_pct: 0 });
    expect(t).toContain('Защиты нет');
  });

  it('пустой объект — тоже «защиты нет», а не пустая строка', () => {
    expect(gridProtectionText({})).toContain('Защиты нет');
  });
});

describe('фраза перед кнопкой взвода', () => {
  const base = {
    kind: 'grid' as const, side: 'buy' as const, qty: 3, code: 'RIZ6',
    trigger: 0, trailOffset: 0, watchId: '', childPrice: 0,
    gStep: 90, gBuys: 8, gSells: 8, gLot: 3, gStopPts: 260, price: 85000,
  };

  it('обещает защиту вместе с остальным', () => {
    const p = preview({ ...base, gTrigFills: 3, gTrigMovePct: 0.25, gTrigTouches: 1, gRearmMin: 30 });
    expect(p.error).toBe('');
    expect(p.sentence).toContain('Защита');
    expect(p.sentence).toContain('через 30 минут');
  });

  it('без защиты предупреждает в той же фразе', () => {
    const p = preview({ ...base, gTrigFills: 0, gTrigMovePct: 0, gTrigTouches: 0, gRearmMin: 0 });
    expect(p.sentence).toContain('Защиты нет');
  });

  it('поля защиты не ломают проверку обязательных', () => {
    const p = preview({ ...base, gStep: 0, gTrigFills: 3 });
    expect(p.error).toContain('Шаг сетки');
  });
});
