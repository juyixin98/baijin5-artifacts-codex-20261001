package protocol

import (
	"errors"
	"io"
	"log"
	"strings"
	"testing"

	"smtpsink/internal/storage"
)

func TestStateString(t *testing.T) {
	want := map[State]string{
		StateConnected: "connected",
		StateReady:     "ready",
		StateMail:      "mail",
		StateRcpt:      "rcpt",
		State(99):      "unknown",
	}
	for s, w := range want {
		if got := s.String(); got != w {
			t.Errorf("State(%d).String() = %q, want %q", int(s), got, w)
		}
	}
}

// flakyRW fails writes after writesBeforeFail successful ones, to
// exercise reply-write error paths without a real network.
type flakyRW struct {
	r                io.Reader
	writesBeforeFail int
	n                int
}

func (f *flakyRW) Read(p []byte) (int, error) { return f.r.Read(p) }

func (f *flakyRW) Write(p []byte) (int, error) {
	f.n++
	if f.n > f.writesBeforeFail {
		return 0, errors.New("flakyRW: write failed")
	}
	return len(p), nil
}

func newFlakySession(store storage.Store) *Session {
	return &Session{
		ID:       "s-flaky",
		Hostname: "smtpsink.local",
		Domains:  DomainsFromList([]string{"example.test"}),
		Limits:   defaultLimits(),
		Store:    store,
		Logger:   log.New(io.Discard, "", 0),
	}
}

func TestGreetingWriteFailure(t *testing.T) {
	sess := newFlakySession(&recordingStore{})
	rw := &flakyRW{r: strings.NewReader(""), writesBeforeFail: 0}
	if err := sess.Serve(rw, "flaky"); err == nil {
		t.Fatalf("Serve should fail when the greeting cannot be written")
	}
}

func TestCommandReplyWriteFailure(t *testing.T) {
	sess := newFlakySession(&recordingStore{})
	rw := &flakyRW{r: strings.NewReader("NOOP\r\nNOOP\r\n"), writesBeforeFail: 1}
	if err := sess.Serve(rw, "flaky"); err == nil {
		t.Fatalf("Serve should fail when a command reply cannot be written")
	}
}

func TestClientDisconnectBeforeGreeting(t *testing.T) {
	store := &recordingStore{}
	done, conn, _ := newPipeSession(t, store, defaultLimits())
	conn.Close() // never read the banner
	if err := <-done; err == nil {
		t.Fatalf("Serve should fail when the client drops before the greeting")
	}
}
