// Защита сетки: одна формулировка на форму взвода и на карточку заявки.
//
// Сетка опасна не в боковике, а в тренде — она добирает против хода. Защита
// переводит её в «только на выход» по безубытку. Экран обязан сказать, при
// каком условии это случится, и сказать вслух, когда защиты НЕТ.
import { describe, it, expect } from 'vitest';
import { gridProtectionText, gridSideLevels, gridTargetText, preview } from './smart-order-help';

describe('формулировка защиты', () => {
  it('оба условия: филлы ИЛИ уход цены', () => {
    const t = gridProtectionText({ g_trig_fills: 3, g_trig_move_pct: 0.25, g_trig_touches: 1 });
    expect(t).toContain('набрано 3 уровня в одну сторону');
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

describe('цель прибыли на карточке', () => {
  it('без ₽/пункт не печатает ни рубля набранного', () => {
    // Пункт не рубль: на этом карточка робота уже показывала −11 ₽ вместо
    // −5585 ₽. Нет коэффициента — нет ответа, и цель не сработает.
    const t = gridTargetText({ g_tp_rub: 50000, g_cash_pts: 400, g_pos: 0 }, 85000, 0);
    expect(t).toContain('₽ за пункт неизвестен');
    expect(t).not.toMatch(/набрано/);
  });

  it('считает поток плюс открытую позицию по рынку', () => {
    // поток −85000 пунктов (купили 1 по 85000), позиция +1 по 85400 => +400 п.
    const t = gridTargetText({ g_tp_rub: 1000, g_cash_pts: -85000, g_pos: 1, g_cash_on: true },
                             85400, 1.3);
    expect(t).toContain('цель');
    expect(t).toContain('520');          // 400 п. × 1.3 ₽
  });

  it('поток не заведён — говорим, что счёт не со взвода', () => {
    const t = gridTargetText({ g_tp_rub: 1000, g_cash_pts: 0, g_pos: 0, g_cash_on: false },
                             85000, 1.3);
    expect(t).toContain('с начала учёта');
  });

  it('без цели — пусто, лишней строки на карточке нет', () => {
    expect(gridTargetText({ g_tp_rub: 0 }, 85000, 1.3)).toBe('');
  });

  it('нет цены — не выдумываем прибыль', () => {
    expect(gridTargetText({ g_tp_rub: 1000, g_cash_pts: 10 }, 0, 1.3)).toContain('цены инструмента нет');
  });
});

describe('порог защиты ценой, а не процентом', () => {
  it('знаем базу — называем оба уровня', () => {
    // Требование оператора 02.10.2026: «0.25% надо расшифровать в терминах
    // цены». Процент нечем сверить с графиком, уровень — можно.
    const t = gridProtectionText({ g_trig_move_pct: 0.25, g_trig_touches: 1 }, 85_000);
    expect(t).toContain('0.25%');
    expect(t).toMatch(/ниже 84\s?787/);
    expect(t).toMatch(/выше 85\s?212/);
  });

  it('базы нет — процент остаётся процентом, цена не выдумывается', () => {
    const t = gridProtectionText({ g_trig_move_pct: 0.25 }, 0);
    expect(t).toContain('0.25%');
    expect(t).not.toMatch(/ниже|выше/);
  });

  it('защита только по филлам — порогов цены нет вовсе', () => {
    expect(gridProtectionText({ g_trig_fills: 3, g_trig_move_pct: 0 }, 85_000))
      .not.toMatch(/ниже|выше/);
  });

  it('фраза перед кнопкой берёт порог от текущей цены', () => {
    const p = preview({
      kind: 'grid', side: 'buy', qty: 3, code: 'RIZ6', trigger: 0, trailOffset: 0,
      watchId: '', childPrice: 0, gStep: 10, gBuys: 20, gSells: 20, gLot: 5,
      gStopPts: 40, gTrigFills: 3, gTrigMovePct: 0.25, gTrigTouches: 2, gRearmMin: 60,
      price: 85_000,
    });
    expect(p.sentence).toMatch(/ниже 84\s?787/);
  });
});

describe('защита по уровням меряет НАБОР В ОДНУ СТОРОНУ', () => {
  it('позиция ÷ объём уровня, а не счёт филлов', () => {
    // real-trade 02.10.2026: на GZZ6 старое правило включило защиту на трёх
    // филлах при позиции в один уровень. Чередование вверх-вниз позицию не
    // наращивает, тренд с откатами — наращивает.
    expect(gridSideLevels({ g_pos: 15, g_lot: 5 })).toBe(3);
    expect(gridSideLevels({ g_pos: -15, g_lot: 5 })).toBe(3);   // шорт считается так же
    expect(gridSideLevels({ g_pos: 5, g_lot: 5 })).toBe(1);
    expect(gridSideLevels({ g_pos: 0, g_lot: 5 })).toBe(0);
  });

  it('без объёма уровня числа нет — делить не на что', () => {
    expect(gridSideLevels({ g_pos: 15, g_lot: 0 })).toBeNull();
    expect(gridSideLevels({})).toBeNull();
  });

  it('формулировка говорит «набрано в одну сторону», а не «исполнено»', () => {
    const t = gridProtectionText({ g_trig_fills: 3 });
    expect(t).toContain('в одну сторону');
    expect(t).not.toContain('исполнено');
  });
});

describe('окно выставления', () => {
  it('фраза называет и окно, и запас снятия', () => {
    const p = preview({
      kind: 'grid', side: 'buy', qty: 3, code: 'RIZ6', trigger: 0, trailOffset: 0,
      watchId: '', childPrice: 0, gStep: 90, gBuys: 8, gSells: 8, gLot: 3,
      gStopPts: 260, gWindow: 5, price: 85_000,
    });
    expect(p.sentence).toContain('по 5 ближайших');
    expect(p.sentence).toContain('до 7');     // край окна снимается с запасом
    expect(p.sentence).toContain('ждут в STL');
  });

  it('окна нет — про него молчим', () => {
    const p = preview({
      kind: 'grid', side: 'buy', qty: 3, code: 'RIZ6', trigger: 0, trailOffset: 0,
      watchId: '', childPrice: 0, gStep: 90, gBuys: 8, gSells: 8, gLot: 3,
      gStopPts: 260, gWindow: 0, price: 85_000,
    });
    expect(p.sentence).not.toContain('ждут в STL');
  });
});
