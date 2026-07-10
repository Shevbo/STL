package selfupdate

import (
	"os"
	"path/filepath"
	"regexp"
)

var luaVersionRe = regexp.MustCompile(`SCRIPT_VERSION\s*=\s*"([^"]+)"`)

// LuaScriptName returns the version-stamped install name for the QLua script,
// parsed from its own SCRIPT_VERSION constant: "shectory_trade_v<ver>.lua".
// The operator's QUIK load dialog must show WHICH build he is picking (Lua runs
// from memory; a bare same-named file on disk says nothing about what runs).
// Falls back to the plain name when the constant is missing/unparsable.
func LuaScriptName(src []byte) string {
	if m := luaVersionRe.FindSubmatch(src); m != nil {
		return "shectory_trade_v" + string(m[1]) + ".lua"
	}
	return "shectory_trade.lua"
}

// luaInstallName reads the staged script and returns its version-stamped
// install name (plain name when unreadable — the bat's `if exist` guards make
// that a no-op anyway).
func luaInstallName(stageDir string) string {
	src, err := os.ReadFile(filepath.Join(stageDir, "shectory_trade.lua"))
	if err != nil {
		return "shectory_trade.lua"
	}
	return LuaScriptName(src)
}
