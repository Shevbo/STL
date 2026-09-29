// Состояние постановки коридора мышкой. Кликает оператор по графику, а
// параметры собирает форма — это разные компоненты, и стор между ними обязан
// быть предсказуемым: лишний клик не имеет права сдвинуть уже заданный канал.
import { describe, it, expect, beforeEach } from 'vitest';
import { corridorDraw, CORRIDOR_STEPS } from './stores/corridor-draw.svelte';

const c = (n: number) => ({ ms: n, price: 80_000 + n });

describe('постановка коридора мышкой', () => {
  beforeEach(() => corridorDraw.reset());

  it('выключенный режим кликов не собирает', () => {
    corridorDraw.push(c(1));
    expect(corridorDraw.clicks).toHaveLength(0);
  });

  it('три клика и подсказка на каждый шаг', () => {
    corridorDraw.start();
    expect(corridorDraw.hint).toBe(CORRIDOR_STEPS[0]);
    corridorDraw.push(c(1));
    expect(corridorDraw.hint).toBe(CORRIDOR_STEPS[1]);
    corridorDraw.push(c(2));
    expect(corridorDraw.hint).toBe(CORRIDOR_STEPS[2]);
    corridorDraw.push(c(3));
    expect(corridorDraw.left).toBe(0);
    expect(corridorDraw.hint).toBe('');
  });

  // Канал уже задан: четвёртый клик по графику — это промах или прокрутка, а не
  // намерение. Приняв его, мы бы молча сдвинули стенку.
  it('четвёртый клик игнорируется', () => {
    corridorDraw.start();
    for (const n of [1, 2, 3, 4]) corridorDraw.push(c(n));
    expect(corridorDraw.clicks.map((x) => x.ms)).toEqual([1, 2, 3]);
  });

  it('шаг назад снимает последний клик', () => {
    corridorDraw.start();
    corridorDraw.push(c(1)); corridorDraw.push(c(2));
    corridorDraw.undo();
    expect(corridorDraw.clicks.map((x) => x.ms)).toEqual([1]);
    expect(corridorDraw.hint).toBe(CORRIDOR_STEPS[1]);
  });

  it('стоп оставляет собранные клики, сброс убирает всё', () => {
    corridorDraw.start();
    corridorDraw.push(c(1));
    corridorDraw.stop();
    expect(corridorDraw.active).toBe(false);
    expect(corridorDraw.clicks).toHaveLength(1);
    corridorDraw.reset();
    expect(corridorDraw.clicks).toHaveLength(0);
  });
});
