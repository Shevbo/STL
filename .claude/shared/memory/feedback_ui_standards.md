---
name: ui-standards
description: "STRICT UI standards from the operator: no font below 10px anywhere; lists = collapsed rows that expand on click; every list gets a CSV-export button with ALL fields"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9a04b06b-73aa-4774-b4c8-99559bf8ad84
---

Operator's standing UI rules (2026-07-10):

1. **Минимальный шрифт 10px** везде в UI («глаза слезятся») — no font-size below
   10px in STL frontend or the agent status page. Audit new CSS for violations.
2. **Перечни = схлопнутые строки**, раскрываются кликом по строке (pattern already
   used by Botstore instrument tables and the agent robot card).
3. **«Выгрузить в CSV» над ЛЮБЫМ списком** — standard, not per-request: every table/
   list gets a CSV export button carrying ALL fields (for Excel analysis), not just
   the visible columns.

**Why:** operator analyses everything in Excel and reads dense trading UIs for hours.

**How to apply:** frontend/src/lib has downloadCSV helper (added 2026-07-10); reuse it
for any new table. When building any new list UI, default to collapsed rows + CSV
button without being asked. See [[versioned-script-names]], [[no-external-publications]].
