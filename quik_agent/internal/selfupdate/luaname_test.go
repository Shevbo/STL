package selfupdate

import "testing"

func TestLuaScriptName(t *testing.T) {
	src := []byte(`-- header
local SCRIPT_VERSION = "2026.07.09-cc3"
local CONFIG = {}`)
	if got := LuaScriptName(src); got != "shectory_trade_v2026.07.09-cc3.lua" {
		t.Fatalf("got %q", got)
	}
	if got := LuaScriptName([]byte("-- no version here")); got != "shectory_trade.lua" {
		t.Fatalf("fallback = %q", got)
	}
}
