-- Local overrides for shectory_trade.lua — DEPLOYMENT-SPECIFIC, NOT in git.
--
-- Setup (once): copy this file NEXT TO shectory_trade.lua on the QUIK machine,
-- rename to  shectory_trade_config.lua  and fill in the values below.
-- The script merges these over its CONFIG at startup, so future updates of
-- shectory_trade.lua never require editing the script itself again.
--
-- Any CONFIG key can be overridden here (MD_CODES, MD_CLASS, intervals, ...).
return {
  ACCOUNT        = "SPBFUT00000",      -- торговый счёт (как в QUIK, "Торговый счёт")
  -- CLIENT_CODE = "",                 -- код клиента, если требует брокер

  USE_FILE_QUEUE = true,               -- транспорт к агенту: файловая очередь
  QUEUE_DIR      = "C:\\quik-bridge",  -- = agent_config.json trade_queue_dir

  -- MD_CODES    = "RIU6,GZU6,SiU6,SRU6",  -- инструменты для котировок/ленты

  -- Account tables publish cadence for the agent showcase (ms). Default 2000 when unset.
  -- ACC_INTERVAL_MS = 2000,
}
