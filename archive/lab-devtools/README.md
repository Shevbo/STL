# Archived: "Lab" developer-tools panel (lowercase Lab button)

Removed from production on 2026-06-23 by decision: the strategy-developer
toolbar was not going to pay off at the current pace of development. Kept here
for reference; not built, not imported, not type-checked.

## What it was

The lowercase **"Lab"** button in the top bar (distinct from the uppercase
**"LAB"** = Backtest panel, which stays in prod). It toggled `labMode` in
`App.svelte`, which rendered `LabBar` at the bottom of the screen: a
developer toolbar with three tabs.

- **Strategies** tab: a hardcoded `mockStrategies` list (GZM6 trend/mean
  samples), each with Load / Export buttons, plus an Offline checkbox.
- **Backtest** tab: symbol/from/to/strategy inputs and a Run button that hit
  `GET /api/backtest?...` (a route that never existed in the backend, so this
  was a non-functional mock).
- **Scripts** tab: per-strategy Edit buttons that opened `CodeEditor`.

## Files

- `LabBar.svelte` - the toolbar UI (mock data).
- `CodeEditor.svelte` - floating Monaco-based Python script editor; loaded/saved
  via `GET|PUT /api/scripts/{path}` (also never implemented in the backend).
  Pulled in `@monaco-editor/loader` as a dynamic import.
- `offline-player.ts` - replayed a recorded session JSON over the stores in
  place of the live WS feed (the LabBar "Offline" toggle).
- `offline-player.test.ts` - its unit test.

## How it was wired (now removed)

`App.svelte` held: `labMode` state, the `{#if labMode} <LabBar/>` block, the
`{#if editorPath} <CodeEditor/>` block, and handlers `handleRunBacktest`,
`handleLoadStrategy`, `handleOpenEditor`, `handleExportRobot`,
`handleToggleOffline`, `handleEditorSave`, `handleEditorRun`, plus state
`editorPath`, `backtestResult`, `offlinePlayer`.
`TopBar.svelte` held the lowercase "Lab" button and its `labMode` /
`onToggleLab` props. The `Strategy` and `BacktestResult` interfaces in
`lib/types.ts` were used only by this feature.

## Backend impact

None. `/api/backtest` and `/api/scripts` had no backend routes; the real
backtest API is `/api/v1/...` and is used by the separate (kept) Backtest Lab.

## Not removed

`@monaco-editor/loader` stays in `frontend/package.json` (unused now, but
removing it risks lockfile churn for no runtime gain).
