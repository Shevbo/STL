// Постановка коридора мышкой: общее состояние графика и формы заявки.
//
// Кликает оператор по ГРАФИКУ, а параметры собирает ФОРМА — это два разных
// компонента, и связать их можно было только общим состоянием. Клики держим
// сырыми (время и цена, как отдал график): перевод в параметры живёт в
// corridorFromClicks, чтобы у него были тесты, а у стора — одна забота.
//
// Заказ оператора 29.09.2026: «ставить линиями, а не цифрами».

import type { CorridorClick } from '$lib/smart-order-help';

/** Что сейчас просим кликнуть. Порядок — как объяснено оператору. */
export const CORRIDOR_STEPS = [
  'первая точка ВЕРХНЕЙ границы',
  'вторая точка ВЕРХНЕЙ границы (она задаёт наклон)',
  'уровень НИЖНЕЙ границы — в любом месте, наклон уже известен',
];

let _active = $state(false);
let _clicks = $state<CorridorClick[]>([]);

export const corridorDraw = {
  get active() { return _active; },
  get clicks() { return _clicks; },
  /** Сколько кликов ещё ждём; 0 — коридор задан. */
  get left() { return Math.max(0, 3 - _clicks.length); },
  /** Подсказка на текущий шаг; пусто — режим выключен или клики собраны. */
  get hint() { return _active && _clicks.length < 3 ? CORRIDOR_STEPS[_clicks.length] : ''; },

  start() { _clicks = []; _active = true; },
  /** Лишний клик игнорируем: три точки заданы, четвёртая молча сдвинула бы канал. */
  push(c: CorridorClick) { if (_active && _clicks.length < 3) _clicks = [..._clicks, c]; },
  /** Отменить последний клик — промахнуться мышкой по свече проще, чем кажется. */
  undo() { _clicks = _clicks.slice(0, -1); },
  stop() { _active = false; },
  reset() { _active = false; _clicks = []; },
};
