package trade

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"time"
)

// File-queue transport: used when the QUIK terminal has no LuaSocket. The agent and
// the Lua script share a directory (config QueueDir / Lua CONFIG.QUEUE_DIR). The
// agent APPENDS command lines to cmd.jsonl (Lua reads them by offset) and tails
// evt.jsonl (which Lua appends to) by byte offset. Append-only, lock-free across
// processes; one newline-delimited JSON object per line, identical to the TCP schema.

func (b *Bridge) cmdPath() string { return filepath.Join(b.queueDir, "cmd.jsonl") }
func (b *Bridge) evtPath() string { return filepath.Join(b.queueDir, "evt.jsonl") }

// runFileQueue ensures the queue files exist and polls evt.jsonl for new events until
// ctx is cancelled. It mirrors Run()'s blocking contract.
func (b *Bridge) runFileQueue(ctx context.Context) error {
	if err := os.MkdirAll(b.queueDir, 0o755); err != nil {
		return err
	}
	// Ensure both files exist so the first poll/append never errors.
	for _, p := range []string{b.cmdPath(), b.evtPath()} {
		f, err := os.OpenFile(p, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
		if err != nil {
			return err
		}
		_ = f.Close()
	}
	// Start at the current end of evt.jsonl: we only care about events produced after
	// the agent starts, not stale lines from a previous run.
	if fi, err := os.Stat(b.evtPath()); err == nil {
		b.evtOff = fi.Size()
	}
	b.logf("trade bridge: file-queue mode, dir=%s (cmd.jsonl out, evt.jsonl in)", b.queueDir)

	tick := time.NewTicker(20 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-tick.C:
			b.drainEvents()
		}
	}
}

// drainEvents reads any complete new lines appended to evt.jsonl since evtOff and
// dispatches them. A trailing partial line (no newline yet) is left for the next tick.
func (b *Bridge) drainEvents() {
	f, err := os.Open(b.evtPath())
	if err != nil {
		return
	}
	defer f.Close()
	if _, err := f.Seek(b.evtOff, io.SeekStart); err != nil {
		return
	}
	data, err := io.ReadAll(f)
	if err != nil || len(data) == 0 {
		return
	}
	idx := bytes.LastIndexByte(data, '\n')
	if idx < 0 {
		return // no complete line yet
	}
	complete := data[:idx+1]
	b.evtOff += int64(len(complete))
	for _, line := range bytes.Split(complete, []byte{'\n'}) {
		line = bytes.TrimRight(line, "\r")
		if len(line) == 0 {
			continue
		}
		var ev luaEvent
		if err := json.Unmarshal(line, &ev); err != nil {
			b.logf("trade bridge: bad evt line: %v", err)
			continue
		}
		b.dispatch(ev)
	}
}

// appendCmd appends one JSON command line to cmd.jsonl (file-queue mode).
func (b *Bridge) appendCmd(v any) error {
	buf, err := json.Marshal(v)
	if err != nil {
		return err
	}
	buf = append(buf, '\n')
	b.cmdMu.Lock()
	defer b.cmdMu.Unlock()
	f, err := os.OpenFile(b.cmdPath(), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(buf)
	return err
}
