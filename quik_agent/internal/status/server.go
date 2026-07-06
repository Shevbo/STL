package status

import (
	_ "embed"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"sync"
)

//go:embed page.html
var pageHTML []byte

// logTailBytes bounds how much of a log file /logs/{name} streams: the last
// 64KiB, which is enough operator context without risking a multi-GB agent
// log flooding the response.
const logTailBytes = 65536

// tailFile writes the last logTailBytes of the file at path to w. Equivalent
// to Seek(-65536, io.SeekEnd) clamped at 0: computed via Stat so a file
// smaller than the window is never seeked to a negative offset.
func tailFile(w io.Writer, path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return err
	}
	var start int64
	if info.Size() > logTailBytes {
		start = info.Size() - logTailBytes
	}
	if _, err := f.Seek(start, io.SeekStart); err != nil {
		return err
	}
	_, err = io.Copy(w, f)
	return err
}

// NewServer builds the local showcase HTTP handler: GET /api/status (the JSON
// snapshot), GET /logs/{name} (tail of an agent/runner log), GET /strategy/{id}
// (strategy doc lookup), POST /api/align (confirm+execute a recon plan),
// POST /api/robot/{id}/params (partial spec edit), POST /api/robot/{id}/mode
// (the paper/real toggle — deliberately absent from the STL-side app; it is
// the real-money arming action and lives ONLY on this agent-local server), and
// GET / (the embedded showcase page). Addr is left unset for the caller.
func NewServer(d Deps) *http.Server {
	return &http.Server{Handler: newMux(d)}
}

func newMux(d Deps) *http.ServeMux {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /api/status", func(w http.ResponseWriter, r *http.Request) {
		data, err := BuildStatus(d)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(data)
	})

	mux.HandleFunc("GET /logs/{name}", func(w http.ResponseWriter, r *http.Request) {
		name := r.PathValue("name")
		path, ok := d.LogPaths[name]
		if !ok || path == "" {
			http.Error(w, "unknown log: "+name, http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		if err := tailFile(w, path); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
		}
	})

	mux.HandleFunc("GET /strategy/{id}", func(w http.ResponseWriter, r *http.Request) {
		id := r.PathValue("id")
		doc, ok, err := loadStrategyDoc(d.DocsPath, id)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		if !ok {
			http.Error(w, "unknown strategy: "+id, http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write(doc)
	})

	align := &alignState{}
	mux.HandleFunc("POST /api/align", func(w http.ResponseWriter, r *http.Request) {
		handleAlign(d, align, w, r)
	})

	mux.HandleFunc("POST /api/robot/{id}/params", func(w http.ResponseWriter, r *http.Request) {
		handleParamsSet(d, w, r)
	})

	// Local-only: the real-money arming action. Deliberately absent from the
	// STL-side HTTP app (invariant #2) — never add this route anywhere but here.
	mux.HandleFunc("POST /api/robot/{id}/mode", func(w http.ResponseWriter, r *http.Request) {
		handleModeSet(d, w, r)
	})

	mux.HandleFunc("GET /", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write(pageHTML)
	})

	return mux
}

// paramsRequest mirrors ParamsUpdate as the wire shape: an absent JSON field
// decodes to a nil pointer (leave unchanged), a present one to a non-nil
// pointer to the decoded value — including an explicit JSON null, which also
// decodes to nil and is therefore indistinguishable from "absent" (accepted:
// neither the brief nor any caller needs to explicitly clear a field to zero).
type paramsRequest struct {
	ParamsJSON  *string `json:"params_json"`
	Schedule    *string `json:"schedule"`
	MaxPosition *int64  `json:"max_position"`
}

// handleParamsSet applies a partial robot spec edit. nil Deps.ParamsSet -> 503
// (mirrors handleAlign's nil-executor gate). Bad JSON -> 400. A nil error from
// Deps.ParamsSet -> 200. ErrUnknownRobot -> 404. Any other error is treated as
// a validation/range failure -> 400 with the error text.
func handleParamsSet(d Deps, w http.ResponseWriter, r *http.Request) {
	if d.ParamsSet == nil {
		http.Error(w, "params not wired", http.StatusServiceUnavailable)
		return
	}

	id := r.PathValue("id")
	var req paramsRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad json: "+err.Error(), http.StatusBadRequest)
		return
	}

	upd := ParamsUpdate{ParamsJSON: req.ParamsJSON, Schedule: req.Schedule, MaxPosition: req.MaxPosition}
	if err := d.ParamsSet(id, upd); err != nil {
		if errors.Is(err, ErrUnknownRobot) {
			http.Error(w, err.Error(), http.StatusNotFound)
			return
		}
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	w.WriteHeader(http.StatusOK)
}

// modeRequest is the paper/real toggle request body. Paper is a pointer so a
// wire-absent key decodes to nil rather than silently defaulting to false —
// see handleModeSet's explicit nil check (an absent key must NEVER be read as
// "arm real").
type modeRequest struct {
	Paper     *bool  `json:"paper"`
	ConfirmID string `json:"confirm_id"`
}

// handleModeSet flips a robot between paper and real — the real-money arming
// action, so it exists ONLY on this agent-local server (never STL). nil
// Deps.ModeSet -> 503 (mirrors handleAlign's nil-executor gate, checked before
// any body parse). Bad JSON -> 400. A request whose "paper" key is absent
// (decodes to a nil pointer) -> 400 without ever calling Deps.ModeSet: a
// missing key must not be silently treated as paper=false (arm real). A nil
// error from Deps.ModeSet -> 200. ErrUnknownRobot -> 404. Any other error is a
// precondition/confirm failure (e.g. "not flat" or a confirm_id mismatch) ->
// 409 with the error text as the body, so the operator sees exactly why the
// flip was refused.
func handleModeSet(d Deps, w http.ResponseWriter, r *http.Request) {
	if d.ModeSet == nil {
		http.Error(w, "mode not wired", http.StatusServiceUnavailable)
		return
	}

	id := r.PathValue("id")
	var req modeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad json: "+err.Error(), http.StatusBadRequest)
		return
	}
	if req.Paper == nil {
		http.Error(w, "missing 'paper'", http.StatusBadRequest)
		return
	}

	if err := d.ModeSet(id, *req.Paper, req.ConfirmID); err != nil {
		if errors.Is(err, ErrUnknownRobot) {
			http.Error(w, err.Error(), http.StatusNotFound)
			return
		}
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}
	w.WriteHeader(http.StatusOK)
}

type alignRequest struct {
	PlanID string `json:"plan_id"`
}

type alignMismatchResponse struct {
	Error string    `json:"error"`
	Recon reconJSON `json:"recon"`
}

type alignOKResponse struct {
	PlanID  string       `json:"plan_id"`
	Results []StepResult `json:"results"`
}

// alignState is per-SERVER (not per-request, not per-Deps-value) execution state: it
// survives across requests to the SAME running status server for as long as the
// process does. Its mutex serializes the whole handleAlign body — including the
// AlignExec call itself — so two POSTs racing (a double click, or a client retry after
// a slow response the operator never saw complete) cannot both execute; the second
// blocks until the first finishes, by which point lastExecuted already names its plan
// and it is refused instead of firing the order/cancel/fix_state actions twice.
type alignState struct {
	mu           sync.Mutex
	lastExecuted string
}

// handleAlign recomputes the recon report FRESH (never trusts a client-held
// plan) so a confirm can only ever execute the plan the operator is actually
// looking at right now. nil AlignExec -> 503 UNCONDITIONALLY, before any body
// parse or plan comparison (without an executor nothing else about the
// request matters). A stale/absent plan_id -> 409 with the fresh plan so the
// page can update and let the operator re-confirm. A plan_id that matches the
// fresh plan but was ALREADY executed -> 409 with a "already executed" note
// (double-fire suppression) rather than silently re-running it. Only once the
// ids match AND the plan has not yet been executed does it call Deps.AlignExec.
func handleAlign(d Deps, st *alignState, w http.ResponseWriter, r *http.Request) {
	if d.AlignExec == nil {
		http.Error(w, "align not wired", http.StatusServiceUnavailable)
		return
	}

	var req alignRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad json: "+err.Error(), http.StatusBadRequest)
		return
	}

	st.mu.Lock()
	defer st.mu.Unlock()

	rep := computeReport(d)
	if rep.Plan == nil || rep.Plan.ID != req.PlanID {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		json.NewEncoder(w).Encode(alignMismatchResponse{
			Error: "plan_id stale or no active plan",
			Recon: toReconJSON(rep),
		})
		return
	}

	if req.PlanID == st.lastExecuted {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		json.NewEncoder(w).Encode(alignMismatchResponse{
			Error: "план уже исполнен",
			Recon: toReconJSON(rep),
		})
		return
	}

	results := d.AlignExec(*rep.Plan)
	// Latch ONLY when at least one step actually executed (OK=true). An all-failed
	// run is the routine first-smoke case — every step errors out (e.g. a
	// disarmed agent rejecting a cancel_order, or a runner-down fix_state) and
	// nothing happened — and must stay retryable: the operator fixes the
	// precondition and confirms the SAME plan again (the tables haven't
	// changed, so the plan id hasn't either). Latching it would
	// wedge the align behind 409 "план уже исполнен" until the tables happened to
	// move. A partial success DOES latch: something real executed, and re-running the
	// remaining steps needs a FRESH plan computed from the post-execution picture.
	for _, res := range results {
		if res.OK {
			st.lastExecuted = rep.Plan.ID
			break
		}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(alignOKResponse{PlanID: rep.Plan.ID, Results: results})
}
