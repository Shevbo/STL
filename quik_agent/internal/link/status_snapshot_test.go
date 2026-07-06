package link

import (
	"testing"

	"google.golang.org/grpc"

	quikv1 "shectory/quik_agent/internal/pb"
	"shectory/quik_agent/internal/status"

	"shectory/quik_agent/internal/accounts"
	"shectory/quik_agent/internal/quikdde"
	"shectory/quik_agent/internal/trade"
)

// fakeSessionClient is a minimal fake of quikv1.QuikAgentLink_SessionClient:
// only Send is exercised by these tests (mirroring how the real sendLoop only
// ever calls Send on the published session), so grpc.ClientStream's methods
// are satisfied by embedding the (nil) interface — never invoked here.
type fakeSessionClient struct {
	grpc.ClientStream
	sent []*quikv1.AgentMessage
}

func (f *fakeSessionClient) Send(m *quikv1.AgentMessage) error {
	f.sent = append(f.sent, m)
	return nil
}

func (f *fakeSessionClient) Recv() (*quikv1.OrchestratorMessage, error) {
	return nil, nil
}

// fakeStatusSrc satisfies every narrow interface status.Deps needs (Accounts/
// Robots/Runner/Manager/Provider) in one type, by structural typing — only
// Ticks() varies (via price) so BuildStatus's JSON hash changes between calls.
type fakeStatusSrc struct {
	price float64
}

func (f *fakeStatusSrc) Snapshot() accounts.Snapshot                    { return accounts.Snapshot{} }
func (f *fakeStatusSrc) All() []*quikv1.RobotSpec                       { return nil }
func (f *fakeStatusSrc) Paused(string) bool                             { return false }
func (f *fakeStatusSrc) Times(string) (int64, int64)                    { return 0, 0 }
func (f *fakeStatusSrc) LastStatuses() map[string]*quikv1.RobotStatus   { return nil }
func (f *fakeStatusSrc) LastReportAgeMs() int64                         { return 0 }
func (f *fakeStatusSrc) RunnerHealthy() bool                            { return false }
func (f *fakeStatusSrc) SnapshotWorking() []trade.WorkingSnapshot       { return nil }
func (f *fakeStatusSrc) PendingTransViews() []trade.PendingTransView    { return nil }
func (f *fakeStatusSrc) Ticks() []quikdde.Tick {
	return []quikdde.Tick{{Code: "RIU6", Last: f.price}}
}
func (f *fakeStatusSrc) Params() []quikdde.ParamRow { return nil }

func statusDeps(src *fakeStatusSrc) status.Deps {
	return status.Deps{
		Accounts: src, Robots: src, Runner: src, Manager: src, Provider: src,
		NowMs: func() int64 { return 0 },
	}
}

func TestEmitStatusSnapshot_DropsQuietlyWithoutStream(t *testing.T) {
	l := New(Options{})
	if err := l.EmitStatusSnapshot([]byte(`{}`), 123); err != nil {
		t.Fatalf("EmitStatusSnapshot without a session must drop quietly, got %v", err)
	}
}

func TestEmitStatusSnapshot_SendsFrameWithStream(t *testing.T) {
	l := New(Options{})
	fs := &fakeSessionClient{}
	l.setStream(fs)

	if err := l.EmitStatusSnapshot([]byte(`{"a":1}`), 555); err != nil {
		t.Fatalf("EmitStatusSnapshot: %v", err)
	}
	if len(fs.sent) != 1 {
		t.Fatalf("want 1 sent frame, got %d", len(fs.sent))
	}
	snap := fs.sent[0].GetStatusSnapshot()
	if snap == nil {
		t.Fatal("sent frame carries no StatusSnapshot payload")
	}
	if snap.GetStatusJson() != `{"a":1}` || snap.GetGeneratedAtUnixMs() != 555 {
		t.Errorf("snapshot = %+v", snap)
	}
}

func TestMaybeSendStatusSnapshot_NoDepsIsNoop(t *testing.T) {
	l := New(Options{StatusSnapshotMinSec: 5})
	fs := &fakeSessionClient{}
	l.setStream(fs)

	if err := l.maybeSendStatusSnapshot(1000); err != nil {
		t.Fatalf("maybeSendStatusSnapshot: %v", err)
	}
	if len(fs.sent) != 0 {
		t.Fatalf("without SetStatusDeps nothing should ever send, got %d", len(fs.sent))
	}
}

func TestMaybeSendStatusSnapshot_ChangeGate(t *testing.T) {
	l := New(Options{StatusSnapshotMinSec: 5})
	fs := &fakeSessionClient{}
	l.setStream(fs)
	src := &fakeStatusSrc{price: 100}
	l.SetStatusDeps(statusDeps(src))

	// First observation: always sends (nothing sent yet).
	if err := l.maybeSendStatusSnapshot(0); err != nil {
		t.Fatalf("first send: %v", err)
	}
	if len(fs.sent) != 1 {
		t.Fatalf("want 1 sent frame after first observation, got %d", len(fs.sent))
	}

	// Unchanged content, well past the min interval: must NOT resend.
	if err := l.maybeSendStatusSnapshot(60_000); err != nil {
		t.Fatalf("unchanged resend check: %v", err)
	}
	if len(fs.sent) != 1 {
		t.Fatalf("unchanged content must not resend, got %d sends", len(fs.sent))
	}

	// Content changes, but well within the 5s floor: must NOT resend yet.
	src.price = 200
	if err := l.maybeSendStatusSnapshot(1_000); err != nil {
		t.Fatalf("too-soon resend check: %v", err)
	}
	if len(fs.sent) != 1 {
		t.Fatalf("changed content inside the min-interval floor must not resend, got %d sends", len(fs.sent))
	}

	// Same changed content, now past the floor (>= 5000ms since the last send
	// at t=0): must resend exactly once more.
	if err := l.maybeSendStatusSnapshot(5_000); err != nil {
		t.Fatalf("post-floor resend: %v", err)
	}
	if len(fs.sent) != 2 {
		t.Fatalf("want 2 sent frames after the floor elapsed with changed content, got %d", len(fs.sent))
	}
	if fs.sent[1].GetStatusSnapshot().GetGeneratedAtUnixMs() != 5_000 {
		t.Errorf("second send generated_at_unix_ms = %d, want 5000",
			fs.sent[1].GetStatusSnapshot().GetGeneratedAtUnixMs())
	}
}
