package quikdde

import (
	"strconv"
	"strings"
	"sync"
	"time"
)

// Provider holds the latest merged DDE grids in memory and exposes typed,
// read-only views for the gRPC link. There is exactly one process-wide instance
// (Default); the Windows DDE callback merges into it, and internal/link reads from
// it on its poll cadence.
//
// All exported reads are snapshots (copies); callers may keep them safely.
type Provider struct {
	mu     sync.RWMutex
	sheets map[string]*sheetGrid
}

type sheetGrid struct {
	rows, cols     int
	cell           [][]string
	hadFullLayout  bool
	lastMutationMs int64
}

// Default is the process-wide provider the DDE callback writes to.
var Default = NewProvider()

// NewProvider returns an empty provider.
func NewProvider() *Provider {
	return &Provider{sheets: map[string]*sheetGrid{}}
}

func max2(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func (p *Provider) ensureGridSize(g *sheetGrid, minR, minC int) {
	if minR < 1 {
		minR = 1
	}
	if minC < 1 {
		minC = 1
	}
	if g.cell == nil {
		g.cell = make([][]string, minR)
		for r := range g.cell {
			g.cell[r] = make([]string, minC)
		}
		g.rows, g.cols = minR, minC
		return
	}
	if g.rows >= minR && g.cols >= minC {
		return
	}
	newR := max2(g.rows, minR)
	newC := max2(g.cols, minC)
	nc := make([][]string, newR)
	for r := 0; r < newR; r++ {
		row := make([]string, newC)
		if r < g.rows {
			copy(row, g.cell[r])
		}
		nc[r] = row
	}
	g.cell = nc
	g.rows, g.cols = newR, newC
}

// MergeXlTable decodes a binary XlTable and merges it into the named sheet grid.
// itemName is the DDE item range (R1C1_R35C9). Returns true if it was a recognised
// XlTable payload that merged. Read-only: this only mutates the in-memory grid.
func (p *Provider) MergeXlTable(sheet, itemName string, raw []byte) bool {
	if len(raw) < 8 || !LooksLikeXlTable(raw) {
		return false
	}
	r1, c1, r2, c2, ok := ParseItemRange(itemName)
	if !ok {
		return false
	}
	_, _, cells, err := DecodeXlTable(raw)
	if err != nil {
		return false
	}
	expect := (r2 - r1 + 1) * (c2 - c1 + 1)
	if len(cells) < expect {
		for len(cells) < expect {
			cells = append(cells, "")
		}
	} else {
		cells = cells[:expect]
	}

	nowMs := time.Now().UnixMilli()
	p.mu.Lock()
	defer p.mu.Unlock()
	g := p.sheets[sheet]
	if g == nil {
		g = &sheetGrid{}
		p.sheets[sheet] = g
	}

	fullFromOrigin := r1 == 1 && c1 == 1 && len(cells) == r2*c2
	if fullFromOrigin {
		g.hadFullLayout = true
		g.rows, g.cols = r2, c2
		g.cell = make([][]string, r2)
		k := 0
		for r := 0; r < r2; r++ {
			g.cell[r] = make([]string, c2)
			for c := 0; c < c2; c++ {
				g.cell[r][c] = cells[k]
				k++
			}
		}
	} else {
		p.ensureGridSize(g, r2, c2)
		k := 0
		for r := r1 - 1; r < r2; r++ {
			for c := c1 - 1; c < c2; c++ {
				if k < len(cells) {
					g.cell[r][c] = cells[k]
				}
				k++
			}
		}
	}
	g.lastMutationMs = nowMs
	return true
}

// Sheet returns a deep copy of the named grid as (columns, dataRows), or nil if
// the sheet is unknown or empty. Row 0 is treated as the header row.
func (p *Provider) Sheet(name string) (columns []string, dataRows [][]string, lastMutationMs int64) {
	p.mu.RLock()
	defer p.mu.RUnlock()
	g := p.sheets[strings.TrimSpace(name)]
	if g == nil || g.rows == 0 || g.cols == 0 || len(g.cell) == 0 {
		return nil, nil, 0
	}
	columns = make([]string, g.cols)
	copy(columns, g.cell[0])
	for r := 1; r < g.rows; r++ {
		row := make([]string, g.cols)
		copy(row, g.cell[r])
		dataRows = append(dataRows, row)
	}
	return columns, dataRows, g.lastMutationMs
}

// SheetNames returns the names of all sheets currently held.
func (p *Provider) SheetNames() []string {
	p.mu.RLock()
	defer p.mu.RUnlock()
	out := make([]string, 0, len(p.sheets))
	for k := range p.sheets {
		out = append(out, k)
	}
	return out
}

// LastMutationMs returns the freshest mutation time across all sheets (unix ms),
// or 0 if no data has arrived. Used for Heartbeat.last_tick_age_ms.
func (p *Provider) LastMutationMs() int64 {
	p.mu.RLock()
	defer p.mu.RUnlock()
	var best int64
	for _, g := range p.sheets {
		if g.lastMutationMs > best {
			best = g.lastMutationMs
		}
	}
	return best
}

// ---- column mapping helpers -------------------------------------------------

// colIndex finds the first column whose header contains any of the given
// case-insensitive substrings. Returns -1 if none match.
func colIndex(columns []string, subs ...string) int {
	for i, h := range columns {
		hl := strings.ToLower(strings.TrimSpace(h))
		for _, s := range subs {
			if s != "" && strings.Contains(hl, strings.ToLower(s)) {
				return i
			}
		}
	}
	return -1
}

// colIndexExcl is colIndex but skips columns whose header contains any of the
// exclude substrings. QUIK order books carry "Своя покупка"/"Своя продажа" (own
// orders, usually zero) BEFORE the market "Покупка"/"Продажа" depth columns; a
// plain substring match on "покупка" would pick the own-orders column and yield an
// empty bid side. Excluding "своя" makes the market depth columns win.
func colIndexExcl(columns []string, exclude []string, subs ...string) int {
	for i, h := range columns {
		hl := strings.ToLower(strings.TrimSpace(h))
		skip := false
		for _, e := range exclude {
			if e != "" && strings.Contains(hl, strings.ToLower(e)) {
				skip = true
				break
			}
		}
		if skip {
			continue
		}
		for _, s := range subs {
			if s != "" && strings.Contains(hl, strings.ToLower(s)) {
				return i
			}
		}
	}
	return -1
}

func cellAt(row []string, idx int) string {
	if idx < 0 || idx >= len(row) {
		return ""
	}
	return strings.TrimSpace(row[idx])
}

// parseNum parses a QUIK numeric cell, tolerating a comma decimal separator and
// thousands spaces. Returns ok=false on empty/unparseable input.
func parseNum(s string) (float64, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, false
	}
	s = strings.ReplaceAll(s, " ", "")
	s = strings.ReplaceAll(s, " ", "")
	s = strings.ReplaceAll(s, ",", ".")
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0, false
	}
	return v, true
}

func parseInt(s string) (int64, bool) {
	if v, ok := parseNum(s); ok {
		return int64(v), true
	}
	return 0, false
}
