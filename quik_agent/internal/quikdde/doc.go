// Package quikdde — read-only DDE reader for QUIK on Windows.
//
// Ported from PiranhaAI local_agent_go (internal/quikdde). Adapted to the Shectory
// QUIK agent: the cloud/HTTP queue was removed; merged DDE grids are kept in memory
// and exposed via Provider so the gRPC link (internal/link) can turn them into
// read-only SecuritiesSnapshot / MarketDataTick / OrderBook / ParamsSnapshot frames.
//
// Phase 1 is READ-ONLY. This package never sends order transactions; DDE is an
// inbound, server-side channel (QUIK -> agent) only.
//
// DDE contract: service SHECTORY_QUIK, topic/book "data", item = QUIK list name
// (e.g. params). QUIK mixes book and list in one HSZ: "[data]params",
// "data [params]", "data[params]".
//
// Implementation: DDEML (Dde* exports from user32.dll per the Microsoft spec; an
// optional side-by-side ddeml.dll is also supported), no CGO. Two binaries ship in a
// release: quik-agent.exe (386) and quik-agent_amd64.exe — the agent process
// bitness must match QUIK.
//
// Data flow:
//   - Each DDE packet (XlTable) is decoded and merged into an in-memory sheet grid
//     keyed by list name. Provider reads those grids.
//   - No disk queue, no cloud POST. The link package pulls snapshots on its own
//     cadence (poll/heartbeat interval from config).
//
// Environment variables:
//
//	SHECTORY_DISABLE_DDE=1   — do not bring up DDE (non-Windows, or to run link-only).
//	SHECTORY_DDE_DLL=path    — explicit module exporting Dde* (override).
//	SHECTORY_DDE_DEBUG=1     — full DDE trace.
//	SHECTORY_DDE_VERBOSE=1   — per-ADVDATA/POKE noise.
//
// Non-Windows: StartDDE is a no-op (no DDEML available).
package quikdde
