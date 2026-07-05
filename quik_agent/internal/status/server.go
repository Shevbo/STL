package status

import (
	_ "embed"
	"encoding/json"
	"io"
	"net/http"
	"os"
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
// POST /api/manual-offset (edit the operator's manual position offset), and
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

	mux.HandleFunc("POST /api/align", func(w http.ResponseWriter, r *http.Request) {
		handleAlign(d, w, r)
	})

	mux.HandleFunc("POST /api/manual-offset", func(w http.ResponseWriter, r *http.Request) {
		handleManualOffset(d, w, r)
	})

	mux.HandleFunc("GET /", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write(pageHTML)
	})

	return mux
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

// handleAlign recomputes the recon report FRESH (never trusts a client-held
// plan) so a confirm can only ever execute the plan the operator is actually
// looking at right now. nil AlignExec -> 503 UNCONDITIONALLY, before any body
// parse or plan comparison (without an executor nothing else about the
// request matters). A stale/absent plan_id -> 409 with the fresh plan so the
// page can update and let the operator re-confirm; only once the ids match
// does it call Deps.AlignExec.
func handleAlign(d Deps, w http.ResponseWriter, r *http.Request) {
	if d.AlignExec == nil {
		http.Error(w, "align not wired", http.StatusServiceUnavailable)
		return
	}

	var req alignRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad json: "+err.Error(), http.StatusBadRequest)
		return
	}

	rep := computeReport(d)
	if rep.Plan == nil || rep.Plan.ID != req.PlanID {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		json.NewEncoder(w).Encode(alignMismatchResponse{
			Error: "plan_id stale or no active plan",
			Recon: toReconJSON(rep, d.manualOffsets()),
		})
		return
	}

	results := d.AlignExec(*rep.Plan)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(alignOKResponse{PlanID: rep.Plan.ID, Results: results})
}

// handleManualOffset replaces the operator's manual-offset map wholesale: the
// request body IS the full new map (never a delta), matching ManualGet's
// shape so the page's editor round-trips exactly what it displays.
func handleManualOffset(d Deps, w http.ResponseWriter, r *http.Request) {
	var m map[string]int64
	if err := json.NewDecoder(r.Body).Decode(&m); err != nil {
		http.Error(w, "bad json: "+err.Error(), http.StatusBadRequest)
		return
	}
	if d.ManualSet == nil {
		http.Error(w, "manual offset not wired", http.StatusServiceUnavailable)
		return
	}
	if err := d.ManualSet(m); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusOK)
}
