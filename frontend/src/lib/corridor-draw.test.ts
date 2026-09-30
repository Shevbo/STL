// Состояние постановки коридора мышкой. Кликает оператор по графику, а
// параметры собирает форма — это разные компоненты, и стор между ними обязан
// быть предсказуемым: лишний клик не имеет права сдвинуть уже заданный канал.
import { describe, it, expect, beforeEach } from 'vitest';
import { corridorDraw, CORRIDOR_STEPS, TRIANGLE_STEPS } from './stores/corridor-draw.svelte';

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

  // Треугольнику нужны ОБЕ точки нижней линии: у неё свой угол, и одним кликом
  // её не задать (real-trade 29.09.2026).
  it('треугольник ждёт четыре клика и свои подсказки', () => {
    corridorDraw.start(4);
    expect(corridorDraw.need).toBe(4);
    expect(corridorDraw.hint).toBe(TRIANGLE_STEPS[0]);
    for (const n of [1, 2, 3]) corridorDraw.push(c(n));
    expect(corridorDraw.left).toBe(1);
    expect(corridorDraw.hint).toBe(TRIANGLE_STEPS[3]);
    corridorDraw.push(c(4));
    expect(corridorDraw.left).toBe(0);
    corridorDraw.push(c(5));
    expect(corridorDraw.clicks).toHaveLength(4);
  });

  // Перевели тип с треугольника на коридор — счёт кликов обязан вернуться к трём,
  // иначе форма ждала бы четвёртый клик, которого оператор уже не сделает.
  it('новый заход задаёт своё число кликов', () => {
    corridorDraw.start(4);
    corridorDraw.start(3);
    expect(corridorDraw.need).toBe(3);
    expect(corridorDraw.hint).toBe(CORRIDOR_STEPS[0]);
  });

  // График на странице ОДИН, а заявку можно набирать на другой код. Клик по
  // чужим ценам дал бы правдоподобный коридор на уровнях, которых у этого
  // инструмента не бывает, — поэтому инструмент едет вместе с режимом.
  it('режим помнит, по какому инструменту ставят', () => {
    corridorDraw.start(3, 'RIZ6');
    expect(corridorDraw.code).toBe('RIZ6');
    corridorDraw.reset();
    expect(corridorDraw.code).toBe('');
  });
});
