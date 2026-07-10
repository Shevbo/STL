// downloadCSV — операторский стандарт: кнопка «Выгрузить в CSV» над любым списком,
// со ВСЕМИ полями (не только видимыми колонками). Excel-RU дружелюбно: разделитель ';',
// BOM для кириллицы, десятичная запятая в числах.
export function downloadCSV(rows: Record<string, any>[], filename: string): void {
  if (!rows?.length) return;
  // union of keys across all rows; nested objects (params) are flattened one level
  const flat = rows.map((r) => {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(r ?? {})) {
      if (v && typeof v === 'object' && !Array.isArray(v)) {
        for (const [k2, v2] of Object.entries(v)) out[`${k}.${k2}`] = v2;
      } else {
        out[k] = v;
      }
    }
    return out;
  });
  const keys = [...new Set(flat.flatMap((r) => Object.keys(r)))];
  const esc = (v: any): string => {
    if (v == null) return '';
    let s: string;
    if (typeof v === 'number') s = String(v).replace('.', ',');
    else if (Array.isArray(v) || typeof v === 'object') s = JSON.stringify(v);
    else s = String(v);
    if (/[";\n\r]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
    return s;
  };
  const csv = '﻿' + [keys.join(';'), ...flat.map((r) => keys.map((k) => esc(r[k])).join(';'))].join('\r\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  a.download = filename.endsWith('.csv') ? filename : filename + '.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}
