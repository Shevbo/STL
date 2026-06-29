// XlTable decoding for DDE (Microsoft Excel "fast table" format).
//
// Format: a sequence of blocks   WORD tdt; WORD cb; BYTE data[cb]  (little-endian,
// as in the Excel SDK / dde.doc). Ported verbatim from PiranhaAI local_agent_go.

package quikdde

import (
	"encoding/binary"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"golang.org/x/text/encoding/charmap"
)

// XlTable block type constants (Microsoft).
const (
	xltdtFloat  = 0x0001
	xltdtString = 0x0002
	xltdtBool   = 0x0003
	xltdtError  = 0x0004
	xltdtBlank  = 0x0005
	xltdtInt    = 0x0006
	xltdtSkip   = 0x0007
	xltdtTable  = 0x0010
)

var itemRangeRe = regexp.MustCompile(`(?i)^R(\d+)C(\d+)[_:]R(\d+)C(\d+)$`)

// SheetNameFromTopic extracts the QUIK list name from a topic ([data]params,
// data [params]). Falls back to "params".
func SheetNameFromTopic(topic string) string {
	t := strings.TrimSpace(topic)
	const pref = "[data]"
	if len(t) >= len(pref) && strings.EqualFold(t[:len(pref)], pref) {
		s := strings.TrimSpace(t[len(pref):])
		if s != "" {
			return s
		}
	}
	if i := strings.IndexByte(t, '['); i > 0 && len(t) >= i+2 && t[len(t)-1] == ']' {
		book := strings.TrimSpace(t[:i])
		if strings.EqualFold(book, "data") {
			s := strings.TrimSpace(t[i+1 : len(t)-1])
			if s != "" {
				return s
			}
		}
	}
	return "params"
}

// ParseItemRange parses a DDE item like R1C1_R35C9 or R1C1:R35C9 (Excel style).
func ParseItemRange(item string) (r1, c1, r2, c2 int, ok bool) {
	m := itemRangeRe.FindStringSubmatch(strings.TrimSpace(item))
	if m == nil {
		return 0, 0, 0, 0, false
	}
	var err error
	r1, err = strconv.Atoi(m[1])
	if err != nil {
		return 0, 0, 0, 0, false
	}
	c1, err = strconv.Atoi(m[2])
	if err != nil {
		return 0, 0, 0, 0, false
	}
	r2, err = strconv.Atoi(m[3])
	if err != nil {
		return 0, 0, 0, 0, false
	}
	c2, err = strconv.Atoi(m[4])
	if err != nil {
		return 0, 0, 0, 0, false
	}
	if r1 < 1 || c1 < 1 || r2 < r1 || c2 < c1 {
		return 0, 0, 0, 0, false
	}
	return r1, c1, r2, c2, true
}

// LooksLikeXlTable reports whether the first block looks like tdtTable.
func LooksLikeXlTable(raw []byte) bool {
	return len(raw) >= 8 && binary.LittleEndian.Uint16(raw[0:2]) == xltdtTable
}

func decodeExcelStringBytes(b []byte) string {
	if len(b) == 0 {
		return ""
	}
	if utf8.Valid(b) {
		return string(b)
	}
	decoded, err := charmap.Windows1251.NewDecoder().Bytes(b)
	if err == nil {
		return string(decoded)
	}
	return string(b)
}

func formatXlFloat(f float64) string {
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return ""
	}
	return strconv.FormatFloat(f, 'f', -1, 64)
}

var xlErrorText = map[uint16]string{
	0:  "#NULL!",
	7:  "#DIV/0!",
	15: "#VALUE!",
	23: "#REF!",
	29: "#NAME?",
	36: "#NUM!",
	42: "#N/A",
}

// DecodeXlTable decodes a binary XlTable into a flat row-major cell list (R1C1...).
func DecodeXlTable(raw []byte) (nRows, nCols int, cells []string, err error) {
	idx := 0
	var total, cur int
	var buf []string

	for idx+4 <= len(raw) {
		tdt := binary.LittleEndian.Uint16(raw[idx:])
		cb := int(binary.LittleEndian.Uint16(raw[idx+2:]))
		idx += 4
		if cb < 0 || idx+cb > len(raw) {
			return 0, 0, nil, fmt.Errorf("xltable: truncated block tdt=0x%x cb=%d at %d (len=%d)", tdt, cb, idx-cb, len(raw))
		}
		chunk := raw[idx : idx+cb]
		idx += cb

		switch tdt {
		case xltdtTable:
			if cb < 4 {
				continue
			}
			nRows = int(binary.LittleEndian.Uint16(chunk[0:2]))
			nCols = int(binary.LittleEndian.Uint16(chunk[2:4]))
			if nRows <= 0 || nCols <= 0 || nRows > 100_000 || nCols > 4096 {
				return 0, 0, nil, fmt.Errorf("xltable: invalid dimensions %dx%d", nRows, nCols)
			}
			total = nRows * nCols
			buf = make([]string, total)
			cur = 0

		case xltdtFloat:
			if buf == nil {
				return 0, 0, nil, fmt.Errorf("xltable: float before tdtTable")
			}
			if len(chunk)%8 != 0 {
				return 0, 0, nil, fmt.Errorf("xltable: float cb=%d not multiple of 8", len(chunk))
			}
			for off := 0; off < len(chunk); off += 8 {
				if cur >= total {
					return 0, 0, nil, fmt.Errorf("xltable: float overflow")
				}
				bits := binary.LittleEndian.Uint64(chunk[off : off+8])
				buf[cur] = formatXlFloat(math.Float64frombits(bits))
				cur++
			}

		case xltdtString:
			if buf == nil {
				return 0, 0, nil, fmt.Errorf("xltable: string before tdtTable")
			}
			off := 0
			for off < len(chunk) {
				cch := int(chunk[off])
				off++
				if cch < 0 || off+cch > len(chunk) {
					return 0, 0, nil, fmt.Errorf("xltable: bad string cch=%d rest=%d", cch, len(chunk)-off)
				}
				if cur >= total {
					return 0, 0, nil, fmt.Errorf("xltable: string overflow")
				}
				buf[cur] = decodeExcelStringBytes(chunk[off : off+cch])
				off += cch
				cur++
			}

		case xltdtBool:
			if buf == nil {
				return 0, 0, nil, fmt.Errorf("xltable: bool before tdtTable")
			}
			if len(chunk)%2 != 0 {
				return 0, 0, nil, fmt.Errorf("xltable: bool cb odd")
			}
			for off := 0; off < len(chunk); off += 2 {
				if cur >= total {
					return 0, 0, nil, fmt.Errorf("xltable: bool overflow")
				}
				v := binary.LittleEndian.Uint16(chunk[off : off+2])
				if v == 0 {
					buf[cur] = "FALSE"
				} else {
					buf[cur] = "TRUE"
				}
				cur++
			}

		case xltdtError:
			if buf == nil {
				return 0, 0, nil, fmt.Errorf("xltable: error before tdtTable")
			}
			if len(chunk)%2 != 0 {
				return 0, 0, nil, fmt.Errorf("xltable: error cb odd")
			}
			for off := 0; off < len(chunk); off += 2 {
				if cur >= total {
					return 0, 0, nil, fmt.Errorf("xltable: error overflow")
				}
				code := binary.LittleEndian.Uint16(chunk[off : off+2])
				if s, ok := xlErrorText[code]; ok {
					buf[cur] = s
				} else {
					buf[cur] = "#ERR(" + strconv.FormatUint(uint64(code), 10) + ")"
				}
				cur++
			}

		case xltdtBlank:
			if buf == nil {
				return 0, 0, nil, fmt.Errorf("xltable: blank before tdtTable")
			}
			if len(chunk) < 2 {
				return 0, 0, nil, fmt.Errorf("xltable: blank too short")
			}
			n := int(binary.LittleEndian.Uint16(chunk[0:2]))
			if cur+n > total {
				return 0, 0, nil, fmt.Errorf("xltable: blank overflow")
			}
			cur += n

		case xltdtSkip:
			if buf == nil {
				return 0, 0, nil, fmt.Errorf("xltable: skip before tdtTable")
			}
			if len(chunk) < 2 {
				return 0, 0, nil, fmt.Errorf("xltable: skip too short")
			}
			n := int(binary.LittleEndian.Uint16(chunk[0:2]))
			if cur+n > total {
				return 0, 0, nil, fmt.Errorf("xltable: skip overflow")
			}
			cur += n

		case xltdtInt:
			if buf == nil {
				return 0, 0, nil, fmt.Errorf("xltable: int before tdtTable")
			}
			if len(chunk)%2 != 0 {
				return 0, 0, nil, fmt.Errorf("xltable: int cb odd")
			}
			for off := 0; off < len(chunk); off += 2 {
				if cur >= total {
					return 0, 0, nil, fmt.Errorf("xltable: int overflow")
				}
				v := binary.LittleEndian.Uint16(chunk[off : off+2])
				buf[cur] = strconv.FormatUint(uint64(v), 10)
				cur++
			}

		default:
			return 0, 0, nil, fmt.Errorf("xltable: unknown tdt=0x%x cb=%d", tdt, cb)
		}
	}

	if buf == nil {
		return 0, 0, nil, fmt.Errorf("xltable: no tdtTable block")
	}
	for cur < total {
		buf[cur] = ""
		cur++
	}
	if cur != total {
		return nRows, nCols, nil, fmt.Errorf("xltable: internal cell count cur=%d total=%d", cur, total)
	}
	return nRows, nCols, buf, nil
}
