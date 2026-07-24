import { describe, it, expect } from 'vitest';
import { loadCollapsed } from './Frame.svelte';

describe('loadCollapsed', () => {
  it('defaults false on null', () => expect(loadCollapsed(null)).toBe(false));
  it('parses true', () => expect(loadCollapsed('true')).toBe(true));
  it('parses false', () => expect(loadCollapsed('false')).toBe(false));
});
