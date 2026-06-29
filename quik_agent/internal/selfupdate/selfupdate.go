// Package selfupdate checks for a newer agent build and restarts into it.
//
// Mirrors PiranhaAI local_agent_go/internal/autoupdate: check on start, daily at
// 03:00 local, and on COMMAND_TYPE_SELF_UPDATE from STL. It compares the running
// build_rev against the release source, downloads ONE ZIP for the process arch,
// stages it, and spawns a detached .bat that waits for this process to exit, copies
// the new exe over the old, and restarts the same exe name.
//
// Disable with SHECTORY_AGENT_NO_SELFUPDATE=1 (or the --no-self-update flag, which
// callers translate into skipping these calls). Windows only; no-op elsewhere.
package selfupdate

import (
	"archive/zip"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// Source provides the latest available build and the update archive.
type Source interface {
	// LatestBuildRev returns the newest published build revision for this arch.
	LatestBuildRev() (uint32, error)
	// DownloadZip writes the update archive for this arch to destPath.
	DownloadZip(destPath string) error
}

// Enabled reports whether self-update is supported on this OS.
func Enabled() bool { return runtime.GOOS == "windows" }

// EnvDisables reports whether SHECTORY_AGENT_NO_SELFUPDATE=1.
func EnvDisables() bool { return os.Getenv("SHECTORY_AGENT_NO_SELFUPDATE") == "1" }

// ArchTag returns the GOARCH tag used to pick a release ("amd64" or "386").
func ArchTag() string {
	if runtime.GOARCH == "amd64" {
		return "amd64"
	}
	return "386"
}

// HTTPSource fetches releases over plain HTTPS from a base URL. The endpoints are:
//
//	GET <BaseURL>/agent_release?arch=<tag>      -> body is the decimal build_rev
//	GET <BaseURL>/agent_release/zip?arch=<tag>  -> body is the ZIP
//
// An optional bearer token (from the same env var the link uses) authenticates.
type HTTPSource struct {
	BaseURL string
	Token   string
	HTTP    *http.Client
}

// NewHTTPSource builds an HTTPSource. baseURL with no trailing slash.
func NewHTTPSource(baseURL, token string) *HTTPSource {
	return &HTTPSource{
		BaseURL: strings.TrimRight(baseURL, "/"),
		Token:   token,
		HTTP:    &http.Client{Timeout: 35 * time.Second},
	}
}

func (s *HTTPSource) get(path string, long bool) (*http.Response, error) {
	req, err := http.NewRequest(http.MethodGet, s.BaseURL+path, nil)
	if err != nil {
		return nil, err
	}
	if s.Token != "" {
		req.Header.Set("Authorization", "Bearer "+s.Token)
	}
	client := s.HTTP
	if long {
		client = &http.Client{Timeout: 15 * time.Minute}
	}
	return client.Do(req)
}

// LatestBuildRev implements Source.
func (s *HTTPSource) LatestBuildRev() (uint32, error) {
	resp, err := s.get("/agent_release?arch="+ArchTag(), false)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return 0, fmt.Errorf("agent_release: %d %s", resp.StatusCode, string(b))
	}
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 64))
	v, err := strconv.ParseUint(strings.TrimSpace(string(b)), 10, 32)
	if err != nil {
		return 0, fmt.Errorf("agent_release: bad build_rev %q", string(b))
	}
	return uint32(v), nil
}

// DownloadZip implements Source.
func (s *HTTPSource) DownloadZip(destPath string) error {
	resp, err := s.get("/agent_release/zip?arch="+ArchTag(), true)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("agent_release zip: %d %s", resp.StatusCode, string(b))
	}
	f, err := os.Create(destPath)
	if err != nil {
		return err
	}
	if _, err := io.Copy(f, resp.Body); err != nil {
		f.Close()
		_ = os.Remove(destPath)
		return err
	}
	return f.Close()
}

// MaybeSelfUpdate checks the source and, if a newer build exists (or force), stages
// the update and spawns the restart helper. Returns (true, nil) when a helper was
// launched and the caller should exit. Read-only with respect to QUIK; it only
// touches the agent's own files.
func MaybeSelfUpdate(src Source, exeDir string, localBuildRev uint32, force bool) (bool, error) {
	if !Enabled() || EnvDisables() {
		return false, nil
	}
	if src == nil {
		return false, fmt.Errorf("selfupdate: nil source")
	}

	if !force {
		latest, err := src.LatestBuildRev()
		if err != nil {
			return false, err
		}
		if latest == 0 || latest == localBuildRev {
			return false, nil
		}
	}

	zipPath := filepath.Join(os.TempDir(), fmt.Sprintf("quik-agent-update-%d.zip", time.Now().UnixNano()))
	if err := src.DownloadZip(zipPath); err != nil {
		_ = os.Remove(zipPath)
		return false, err
	}
	defer os.Remove(zipPath)

	stage, err := os.MkdirTemp("", "quik-agent-stage-*")
	if err != nil {
		return false, err
	}
	if err := unzipToDir(zipPath, stage); err != nil {
		_ = os.RemoveAll(stage)
		return false, err
	}

	restartName := exeBaseName()
	stageExe := filepath.Join(stage, restartName)
	if _, err := os.Stat(stageExe); err != nil {
		// Fall back to the arch-tagged name if the ZIP uses a fixed naming scheme.
		alt := "quik-agent_" + ArchTag() + ".exe"
		if _, e2 := os.Stat(filepath.Join(stage, alt)); e2 == nil {
			stageExe = filepath.Join(stage, alt)
		} else {
			_ = os.RemoveAll(stage)
			return false, fmt.Errorf("update zip missing %s (and %s)", restartName, alt)
		}
	}

	if err := spawnRestart(exeDir, restartName, stage, stageExe); err != nil {
		_ = os.RemoveAll(stage)
		return false, err
	}
	fmt.Println("agent: self-update staged — restarting shortly...")
	return true, nil
}

func exeBaseName() string {
	if exePath, err := os.Executable(); err == nil {
		return filepath.Base(exePath)
	}
	return "quik-agent.exe"
}

func unzipToDir(zipPath, dest string) error {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer r.Close()
	for _, f := range r.File {
		if f.FileInfo().IsDir() {
			continue
		}
		base := filepath.Base(f.Name)
		if base != f.Name {
			// Reject path traversal / nested entries; we only take flat files.
			continue
		}
		if err := extractZipFile(f, filepath.Join(dest, base)); err != nil {
			return err
		}
	}
	return nil
}

func extractZipFile(f *zip.File, dest string) error {
	rc, err := f.Open()
	if err != nil {
		return err
	}
	defer rc.Close()
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}
	tmp := dest + ".tmp"
	w, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o644)
	if err != nil {
		return err
	}
	if _, err := io.Copy(w, rc); err != nil {
		w.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err := w.Close(); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, dest)
}

// nextLocalRun returns the next occurrence of hour:min in local time.
func nextLocalRun(hour, min int) time.Time {
	now := time.Now().Local()
	t := time.Date(now.Year(), now.Month(), now.Day(), hour, min, 0, 0, now.Location())
	if !t.After(now) {
		t = t.Add(24 * time.Hour)
	}
	return t
}

// RunDailyAt blocks, waking at hour:min local each day to run MaybeSelfUpdate.
// On a staged update it calls os.Exit(0) so the restart helper takes over.
func RunDailyAt(src Source, exeDir string, localBuildRev uint32, hour, min int) {
	for {
		d := time.Until(nextLocalRun(hour, min))
		if d < 0 {
			d = time.Minute
		}
		time.Sleep(d)
		if !Enabled() || EnvDisables() {
			continue
		}
		ok, err := MaybeSelfUpdate(src, exeDir, localBuildRev, false)
		if err != nil {
			fmt.Println("agent: scheduled self-update:", err)
			continue
		}
		if ok {
			os.Exit(0)
		}
	}
}
