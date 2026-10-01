package protocol

import (
	"bufio"
	"fmt"
	"io"
	"log"
	"net"
	"strings"
	"sync"
	"testing"
	"time"

	"smtpsink/internal/storage"
)

// recordingStore is a test double that records committed envelopes and
// bodies, and can be told to fail at Begin or Commit.
type recordingStore struct {
	mu        sync.Mutex
	beginErr  error
	commitErr error
	committed []committedMsg
	aborted   int
	begun     int
}

type committedMsg struct {
	id   string
	env  storage.Envelope
	body string
}

func (s *recordingStore) Begin(env storage.Envelope) (storage.Pending, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.begun++
	if s.beginErr != nil {
		return nil, s.beginErr
	}
	return &recordingPending{store: s, env: env}, nil
}

func (s *recordingStore) messages() []committedMsg {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]committedMsg(nil), s.committed...)
}

func (s *recordingStore) aborts() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.aborted
}

func (s *recordingStore) begunCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.begun
}

type recordingPending struct {
	store *recordingStore
	env   storage.Envelope
	body  strings.Builder
	done  bool
}

func (p *recordingPending) Write(b []byte) (int, error) { return p.body.Write(b) }

func (p *recordingPending) Commit() (string, error) {
	p.store.mu.Lock()
	defer p.store.mu.Unlock()
	p.done = true
	if p.store.commitErr != nil {
		return "", p.store.commitErr
	}
	id := fmt.Sprintf("test-id-%d", len(p.store.committed)+1)
	p.store.committed = append(p.store.committed, committedMsg{id: id, env: p.env, body: p.body.String()})
	return id, nil
}

func (p *recordingPending) Abort() error {
	p.store.mu.Lock()
	defer p.store.mu.Unlock()
	if !p.done {
		p.store.aborted++
	}
	p.done = true
	return nil
}

// testClient drives one end of a net.Pipe with strict reply assertions.
type testClient struct {
	t    *testing.T
	conn net.Conn
	rd   *bufio.Reader
}

func newSession(t *testing.T, store storage.Store, limits Limits) *testClient {
	t.Helper()
	server, client := net.Pipe()
	sess := &Session{
		ID:       "s-test",
		Hostname: "smtpsink.local",
		Domains:  DomainsFromList([]string{"example.test"}),
		Limits:   limits,
		Store:    store,
		Logger:   log.New(io.Discard, "", 0),
	}
	done := make(chan error, 1)
	go func() { done <- sess.Serve(server, "pipe") }()
	tc := &testClient{t: t, conn: client, rd: bufio.NewReader(client)}
	t.Cleanup(func() {
		client.Close()
		select {
		case <-done:
		case <-time.After(2 * time.Second):
			t.Errorf("session did not exit after client close")
		}
	})
	return tc
}

func defaultLimits() Limits {
	return Limits{MaxLineBytes: 1000, MaxMessageBytes: 1 << 20, MaxRecipients: 100}
}

// newPipeSession starts a session on one end of a net.Pipe and returns a
// channel for its final error plus the client end and a reader for it.
// net.Pipe is synchronous: callers must interleave sends and reply reads.
func newPipeSession(t *testing.T, store storage.Store, limits Limits) (<-chan error, net.Conn, *bufio.Reader) {
	t.Helper()
	server, client := net.Pipe()
	sess := &Session{
		ID:       "s-test",
		Hostname: "smtpsink.local",
		Domains:  DomainsFromList([]string{"example.test"}),
		Limits:   limits,
		Store:    store,
		Logger:   log.New(io.Discard, "", 0),
	}
	done := make(chan error, 1)
	go func() { done <- sess.Serve(server, "pipe") }()
	t.Cleanup(func() { client.Close() })
	return done, client, bufio.NewReader(client)
}

func readLine(t *testing.T, rd *bufio.Reader) string {
	t.Helper()
	line, err := rd.ReadString('\n')
	if err != nil {
		t.Fatalf("readLine: %v", err)
	}
	return strings.TrimRight(line, "\r\n")
}

func sendLines(t *testing.T, conn net.Conn, lines ...string) {
	t.Helper()
	for _, l := range lines {
		if _, err := fmt.Fprintf(conn, "%s\r\n", l); err != nil {
			t.Fatalf("sendLines %q: %v", l, err)
		}
	}
}

// expectReply reads one reply and asserts its exact text. Multi-line replies
// (250-...) are consumed fully and joined with "\n".
func (c *testClient) expectReply(want string) {
	c.t.Helper()
	var lines []string
	for {
		line, err := c.rd.ReadString('\n')
		if err != nil {
			c.t.Fatalf("reading reply: %v (want %q)", err, want)
		}
		line = strings.TrimRight(line, "\r\n")
		lines = append(lines, line)
		if len(line) < 4 || line[3] != '-' {
			break
		}
	}
	got := strings.Join(lines, "\n")
	if got != want {
		c.t.Fatalf("reply mismatch:\n got: %q\nwant: %q", got, want)
	}
}

// expectReplyPrefix asserts only the reply code, for replies carrying
// generated values (message IDs).
func (c *testClient) expectReplyPrefix(want string) {
	c.t.Helper()
	line, err := c.rd.ReadString('\n')
	if err != nil {
		c.t.Fatalf("reading reply: %v (want prefix %q)", err, want)
	}
	if !strings.HasPrefix(line, want) {
		c.t.Fatalf("reply %q does not start with %q", strings.TrimRight(line, "\r\n"), want)
	}
}

func (c *testClient) send(lines ...string) {
	c.t.Helper()
	for _, l := range lines {
		if _, err := fmt.Fprintf(c.conn, "%s\r\n", l); err != nil {
			c.t.Fatalf("send %q: %v", l, err)
		}
	}
}

func (c *testClient) greet() {
	c.t.Helper()
	c.expectReply("220 smtpsink.local Service ready")
	c.send("EHLO client.test")
	c.expectReply("250-smtpsink.local greets client.test\n250-SIZE 1048576\n250 8BITMIME")
}

func TestHappyPathSingleRecipient(t *testing.T) {
	store := &recordingStore{}
	c := newSession(t, store, defaultLimits())
	c.greet()
	c.send("MAIL FROM:<alice@example.test>")
	c.expectReply("250 2.1.0 Sender OK")
	c.send("RCPT TO:<bob@example.test>")
	c.expectReply("250 2.1.5 Recipient OK")
	c.send("DATA")
	c.expectReply("354 End data with <CR><LF>.<CR><LF>")
	c.send("Subject: hi", "", "body line", ".")
	c.expectReplyPrefix("250 2.0.0 Queued as test-id-1")
	c.send("QUIT")
	c.expectReply("221 2.0.0 smtpsink.local closing connection")

	msgs := store.messages()
	if len(msgs) != 1 {
		t.Fatalf("want 1 committed message, got %d", len(msgs))
	}
	m := msgs[0]
	if m.env.MailFrom != "alice@example.test" {
		t.Fatalf("mailFrom = %q", m.env.MailFrom)
	}
	if len(m.env.RcptTo) != 1 || m.env.RcptTo[0] != "bob@example.test" {
		t.Fatalf("rcpts = %v", m.env.RcptTo)
	}
	if m.body != "Subject: hi\r\n\r\nbody line\r\n" {
		t.Fatalf("body = %q", m.body)
	}
}
