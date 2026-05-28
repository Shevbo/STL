# LAB MVP v1 — Integration Test Checklist

Run after: DB created, LAB_DATABASE_URL set in .env, services started.

## Prerequisites

1. Create `project_stl` PostgreSQL DB on Hoster (see `prisma/SETUP_NOTES.md`)
2. Set `LAB_DATABASE_URL` in `.env`
3. Run: `npm run db:migrate` (from repo root)
4. Start backend: `poetry run uvicorn trader.api.app:create_app --factory --port 8000`
5. Start frontend: `cd frontend && npm run dev`

## Test 1: Create STL Link

```bash
curl -s -X POST http://localhost:8000/api/v1/stl-links \
  -H "Content-Type: application/json" \
  -d '{"userEmail":"test@shectory.ru","accountId":"YOUR_ACCOUNT_ID","instruments":["RIM6"]}'
```
Expected: `{"id":"..."}`

## Test 2: Create Robot

```bash
curl -s -X POST http://localhost:8000/api/v1/robots \
  -H "Content-Type: application/json" \
  -d '{
    "userEmail":"test@shectory.ru",
    "stlLinkId":"STL_LINK_ID_FROM_TEST_1",
    "name":"EMA Test",
    "scriptCode":"from trader.lab.strategies.ema_crossover import on_bar, on_start, on_stop",
    "paramsJson":{"symbol":"RIM6","fast_period":9,"slow_period":21},
    "schedule":"*/5 * * * *"
  }'
```
Expected: `{"id":"..."}`

## Test 3: Run Backtest

```bash
curl -s -X POST http://localhost:8000/api/v1/backtest/run \
  -H "Content-Type: application/json" \
  -d '{
    "robotId":"ROBOT_ID_FROM_TEST_2",
    "symbol":"RIM6",
    "dateFrom":"2026-01-01T00:00:00Z",
    "dateTo":"2026-04-01T00:00:00Z",
    "paramsGrid":{"fast_period":[5,9],"slow_period":[20,30]}
  }'
```
Expected: `{"run_id":"..."}`

Wait 30-60 seconds, then:
```bash
curl -s http://localhost:8000/api/v1/backtest/RUN_ID/results
```
Expected: JSON array with sharpe, max_drawdown, win_rate, total_return fields.

## Test 4: Deploy Robot

```bash
curl -s -X POST http://localhost:8000/api/v1/robots/ROBOT_ID/deploy
```
Expected: `{"ok":true}`

Check backend logs — should see `lab.scheduler.robot_started` event.

## Test 5: UI Smoke Test

- [ ] Open frontend URL, click LAB button in header
- [ ] Tab "Live Robots": robot appears in list with LIVE status
- [ ] Tab "Market Browser": RIM6 chart loads
- [ ] Tab "Backtest Lab": run backtest, results table appears with metrics
- [ ] Click "Deploy" on a result row — robot deploys with new params

## Test 6: Undeploy

```bash
curl -s -X POST http://localhost:8000/api/v1/robots/ROBOT_ID/undeploy
```
Expected: `{"ok":true}`. Backend logs: `lab.scheduler.robot_stopped`.
