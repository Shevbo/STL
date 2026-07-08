// Package quikdde — the agent's market-data PROVIDER (in-memory hub) plus a
// RETIRED legacy DDE reader.
//
// Provider is the live data hub: the QLua file-queue publisher (ticks, books,
// tape-driven last, instrument params) lands here via SetLuaTick/SetLuaBook/
// SetLuaLast/SetLuaParam (luafeed.go), and the gRPC link (internal/link) reads
// merged views (Ticks/OrderBook/Params/LuaBookCodes) to build the wire frames.
// The package name is historical — the Provider outlived its DDE origins.
//
// DDE itself is RETIRED as a data source (it needed a manual "Начать вывод",
// died on restarts/big tables, and repeatedly went silent in prod). The DDEML
// server code is kept ONLY as a dormant fallback: StartDDE is a no-op unless
// SHECTORY_ENABLE_DDE=1 explicitly resurrects it. When resurrected, merged DDE
// sheet grids overlay into the same Provider (freshest-wins vs the Lua feed).
//
// Environment variables:
//
//	SHECTORY_ENABLE_DDE=1    — resurrect the legacy DDE reader (default OFF).
//	SHECTORY_DDE_DLL=path    — explicit module exporting Dde* (override).
//	SHECTORY_DDE_DEBUG=1     — full DDE trace.
//	SHECTORY_DDE_VERBOSE=1   — per-ADVDATA/POKE noise.
//
// Non-Windows: StartDDE is a no-op (no DDEML available).
package quikdde
