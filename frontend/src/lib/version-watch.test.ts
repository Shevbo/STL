import { describe, expect, it } from 'vitest';
import { bundleFromHtml } from './version-watch';

describe('определение свежей сборки по index.html', () => {
  it('вытаскивает имя главного бандла', () => {
    const html = '<script type="module" crossorigin src="/assets/index-DpAaENlm.js"></script>';
    expect(bundleFromHtml(html)).toBe('index-DpAaENlm.js');
  });
  it('не путает главный бандл с ленивыми чанками', () => {
    const html = '<link rel="modulepreload" href="/assets/uPlot.esm-C-vmSHoy.js">'
      + '<script src="/assets/index-ABC123.js"></script>';
    expect(bundleFromHtml(html)).toBe('index-ABC123.js');
  });
  it('мусор не выдаёт ложную версию', () => {
    expect(bundleFromHtml('<html>ошибка nginx</html>')).toBe(null);
    expect(bundleFromHtml('')).toBe(null);
  });
});
