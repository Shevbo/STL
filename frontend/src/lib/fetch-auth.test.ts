import { describe, it, expect } from 'vitest';
import { errText } from './fetch-auth';

const res = (body: string, status: number, statusText = '') =>
  ({ status, statusText, text: async () => body }) as Response;

describe('errText', () => {
  it('never leaks an nginx HTML error page into the UI', async () => {
    const t = await errText(res(
      '<html>\n<head><title>502 Bad Gateway</title></head>\n<body>...</body>\n</html>\n' +
      '<!-- a padding to disable MSIE and Chrome friendly error page -->', 502));
    expect(t).not.toContain('<');
    expect(t).toContain('502');
  });

  it('unwraps a FastAPI detail', async () => {
    expect(await errText(res('{"detail":"робота нет в зеркале"}', 409)))
      .toBe('робота нет в зеркале');
  });

  it('keeps a plain-text body', async () => {
    expect(await errText(res('robot not found', 404))).toBe('robot not found');
  });

  it('falls back to the status when the body is empty', async () => {
    expect(await errText(res('', 500, 'Internal Server Error')))
      .toBe('Ошибка сервера 500 Internal Server Error');
  });
});
