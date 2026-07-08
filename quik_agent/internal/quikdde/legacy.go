package quikdde

import (
	"os"
	"strings"
)

// LegacyEnabled reports whether the RETIRED DDE reader was explicitly
// resurrected (SHECTORY_ENABLE_DDE=1). Health classification keys off this:
// with the legacy server deliberately OFF, "server alive" is vacuously true
// and the market-data channel state is driven purely by FEED freshness (the
// QLua publisher) — otherwise every start would raise a false CRITICAL
// DDE_DOWN even though data flows fine.
func LegacyEnabled() bool {
	return strings.TrimSpace(os.Getenv("SHECTORY_ENABLE_DDE")) == "1"
}
