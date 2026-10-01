# Recent

## 2026-09-20 | main
Deployed orderbook pipeline infra (book_digest.py, book_replay.py, opt_agent.py·437 tests) to i9 with 60 orderbook jobs (run ID collision fixed). Leaderboard script ranked top strategies (Bollinger M1, 2EMA, MACD, SuperTrend) but identified data corruption (dupes, martingale patterns). Harm-filter refined, reg_n gate built, grid-search queued.

## 2026-09-18 | main
Walk-forward 2EMA methodology disputed (4-yr vs 6m data); 2EMA closed (spread costs 4%, frequent exits analyzed). Extracted 830k-bar RI backtest (2022–26, tests green); RI "stop-only" variant pairwise better but statistically null (t=0.30 without 2026). Registered 3EMA (all pairs BR/GD/Si); Si 2EMA yields +92.6k₽ but placebo-gate null (p=0.117). Fixed si_portfolio_ten.py (8ed5df7); 10-robot portfolio leverage 0.93 corr; cost-filter (ATR=0) in progress.

## Identity Candidates
- IDENTITY CANDIDATE: Triangular arbitrage design (144 params) reveals multi-instrument coordination sophistication
- IDENTITY CANDIDATE: Email hook automation (scripts/mail_hook.py) over devmail_install shows infrastructure reliability obsession