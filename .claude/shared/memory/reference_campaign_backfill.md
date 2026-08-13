---
name: reference-campaign-backfill
description: "How to backfill a campaign's top-N so its results/curves are viewable in Botstore"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 851d73a4-c9a9-4181-8a39-c141a72dc54e
---

# Campaign result backfill (make top-N viewable)

`optimization_leaderboard` rows store METRICS ONLY — `trades=[]`. So a campaign's
per-rank chart/detail in Botstore is blank until you re-run the top-N and store each
as `<campaign_run>-bf<rank>`. Endpoint that reads them:
`GET /api/v1/lab/campaign-result?campaign_run=<id>&rank=<n>` (`trader/api/app.py`),
querying `backtest_results WHERE run_id='<campaign>-bf<rank>' AND trades IS NOT NULL`.
`Botstore.svelte` throws a visible error (never silent) when a counter has neither a
live-run template nor a stored `-bf` row.

## Backfill recipe (done 2026-07-15 for camp-20260713-contrmacdcrossref top-3)
1. scriptCode from `backtest_runs.job_body` of the campaign's own runs.
2. top-N from `optimization_leaderboard` (filter qty/avg_max, order net desc).
3. per rank: `POST /api/v1/backtest/run` + poll status + `results?full=1` for trades.
4. one txn: DELETE then INSERT parent `backtest_runs` THEN child `backtest_results`
   (id = run_id = `<campaign>-bf<rank>`), storing the LEADERBOARD net_profit (so the
   badge matches витрина; a fresh re-run's net differs ~8%).

## FK / type traps (each cost a failed attempt)
- `backtest_runs.robot_id` -> `robots.id` FK: use a REAL robot id (the campaign's own,
  e.g. `robot-donchian-rts-01`), NOT the strategy id like `macd_cross__inv`.
- `backtest_results.run_id` -> `backtest_runs.id` FK: insert parent run row FIRST.
- No unique constraint on `run_id` -> `ON CONFLICT` fails; use DELETE + INSERT.
- `date_from/to` are timestamptz -> pass `datetime(...,tzinfo=timezone.utc)`, not strings.
- Heredoc f-strings with escaped quotes break; build SQL by concatenation.

Counter strategies suffix `__inv`; scriptCode inverts the base REGISTRY signal.
See [[project-optimization-campaign]].
