import { describe, it, expect } from 'vitest';
import { clampSize } from './Splitter.svelte';

describe('clampSize', () => {
  it('clamps below min', () => expect(clampSize(10, 60, 500)).toBe(60));
  it('clamps above max', () => expect(clampSize(999, 60, 500)).toBe(500));
  it('passes through in range', () => expect(clampSize(200, 60, 500)).toBe(200));
});
