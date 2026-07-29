import { describe, it, expect } from 'vitest';
import { clampSize, savedSize } from './Splitter.svelte';

describe('clampSize', () => {
  it('clamps below min', () => expect(clampSize(10, 60, 500)).toBe(60));
  it('clamps above max', () => expect(clampSize(999, 60, 500)).toBe(500));
  it('passes through in range', () => expect(clampSize(200, 60, 500)).toBe(200));
});

describe('savedSize', () => {
  // Инцидент 29.07: Number(null) === 0 и isFinite(0) === true, поэтому на чистом
  // браузере ОТСУТСТВУЮЩИЙ ключ читался как ноль и схлопывал фрейм до min.
  it('нет ключа — оставляем размер родителя', () =>
    expect(savedSize(null, 120, 900, 400)).toBe(400));
  it('пустая строка — тоже размер родителя', () =>
    expect(savedSize('', 120, 900, 400)).toBe(400));
  it('мусор — размер родителя', () =>
    expect(savedSize('abc', 120, 900, 400)).toBe(400));
  it('ноль и отрицательное — размер родителя, а не min', () => {
    expect(savedSize('0', 120, 900, 400)).toBe(400);
    expect(savedSize('-50', 120, 900, 400)).toBe(400);
  });
  it('сохранённое значение применяется и зажимается', () => {
    expect(savedSize('300', 120, 900, 400)).toBe(300);
    expect(savedSize('99', 120, 900, 400)).toBe(120);
    expect(savedSize('5000', 120, 900, 400)).toBe(900);
  });
});
