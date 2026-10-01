package server

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"strings"
	"testing"
	"time"

	"smtpsink/internal/config"
	"smtpsink/internal/storage"
)

// startTestServer boots a real TCP server with a real SQLite store in a
// temp dir and returns its address and store.
func startTestServer(t *testing.T, mutate func(*config.Config)) (net.Addr, *storage.SQLiteStore) {
	t.Helper()
	cfg := config.Default()
	cfg.Listen = "127.0.0.1:0"
	cfg.StorageDir = t.TempDir()
	if mutate != nil {
		mutate(&cfg)
	}
	store, err := storage.OpenSQLite(cfg.StorageDir)
	if err != nil {
		t.Fatalf("OpenSQLite: %v", err)
	}
	srv := New(cfg, store, log.New(io.Discard, "", 0))
	done := make(chan error, 1)
	go func() { done <- srv.ListenAndServe(context.Background()) }()
	t.Cleanup(func() {
		srv.Shutdown()
		<-done
		store.Close()
	})
	deadline := time.Now().Add(2 * time.Second)
	for srv.Addr() == nil {
		if time.Now().After(deadline) {
			t.Fatalf("server did not bind")
		}
		time.Sleep(5 * time.Millisecond)
	}
	return srv.Addr(), store
}

func dial(t *testing.T, addr net.Addr) (net.Conn, *bufio.Reader) {
	t.Helper()
	conn, err := net.Dial("tcp", addr.String())
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { conn.Close() })
	return conn, bufio.NewReader(conn)
}

// readReply reads one logical reply, following "250-" continuations, and
// returns the raw lines.
func readReply(t *testing.T, rd *bufio.Reader) []string {
	t.Helper()
	var lines []string
	for {
		line, err := rd.ReadString('\n')
		if err != nil {
			t.Fatalf("readReply: %v", err)
		}
		lines = append(lines, strings.TrimRight(line, "\r\n"))
		if len(line) < 5 || line[3] != '-' {
			return lines
		}
	}
}

func send(t *testing.T, conn net.Conn, line string) {
	t.Helper()
	if _, err := fmt.Fprintf(conn, "%s\r\n", line); err != nil {
		t.Fatalf("send %q: %v", line, err)
	}
}

// TestGoldenSession drives a full session over real TCP and compares the
// complete reply transcript and the stored message bytes against
// hand-written reference files under testdata/.
func TestGoldenSession(t *testing.T) {
	addr, store := startTestServer(t, nil)
	conn, rd := dial(t, addr)

	var transcript []string
	collect := func(lines []string) { transcript = append(transcript, lines...) }

	collect(readReply(t, rd)) // banner

	// command steps: each send is followed by one logical reply
	commands := []string{
		"EHLO client.test",
		"MAIL FROM:<alice@example.test>",
		"RCPT TO:<bob@example.test>",
		"RCPT TO:<mallory@outside.example>", // foreign domain: rejected
		"RCPT TO:<carol@example.test>",
		"DATA",
	}
	for _, cmd := range commands {
		send(t, conn, cmd)
		collect(readReply(t, rd))
	}
	// DATA body: dot-leading line sent stuffed; lone dot terminates.
	body := []string{
		"Subject: Sink test",
		"From: alice@example.test",
		"",
		"..dot-leading body line",
		"plain line",
		".",
	}
	for _, l := range body {
		send(t, conn, l)
	}
	collect(readReply(t, rd)) // 250 queued

	send(t, conn, "RSET")
	collect(readReply(t, rd))
	send(t, conn, "QUIT")
	collect(readReply(t, rd))

	// Normalize the generated message ID before comparing to the
	// hand-written golden transcript.
	got := strings.Join(transcript, "\n") + "\n"
	got = normalizeMsgID(t, got)
	want, err := os.ReadFile("testdata/session_basic.replies")
	if err != nil {
		t.Fatalf("read golden: %v", err)
	}
	if got != string(want) {
		t.Fatalf("transcript mismatch:\n--- got ---\n%s\n--- want ---\n%s", got, want)
	}

	// Exactly one message must be stored, with the foreign recipient
	// excluded from the delivery scope.
	msgs, err := store.ListMessages()
	if err != nil || len(msgs) != 1 {
		t.Fatalf("messages = %v, %v", msgs, err)
	}
	m := msgs[0]
	if m.MailFrom != "alice@example.test" {
		t.Fatalf("mailFrom = %q", m.MailFrom)
	}
	if len(m.RcptTo) != 2 || m.RcptTo[0] != "bob@example.test" || m.RcptTo[1] != "carol@example.test" {
		t.Fatalf("delivery scope = %v", m.RcptTo)
	}
	raw, err := os.ReadFile(m.Path)
	if err != nil {
		t.Fatalf("read stored message: %v", err)
	}
	wantBody, err := os.ReadFile("testdata/session_basic.eml")
	if err != nil {
		t.Fatalf("read expected eml: %v", err)
	}
	if string(raw) != string(wantBody) {
		t.Fatalf("stored body mismatch:\n got: %q\nwant: %q", raw, wantBody)
	}
}

// normalizeMsgID replaces the generated ID in "Queued as <id>" lines.
func normalizeMsgID(t *testing.T, transcript string) string {
	t.Helper()
	lines := strings.Split(transcript, "\n")
	for i, l := range lines {
		if strings.HasPrefix(l, "250 2.0.0 Queued as ") {
			lines[i] = "250 2.0.0 Queued as <MSGID>"
		}
	}
	return strings.Join(lines, "\n")
}

func TestSessionLimitRejectsExcessConnections(t *testing.T) {
	addr, _ := startTestServer(t, func(c *config.Config) { c.MaxSessions = 1 })
	// First connection holds the only slot.
	conn1, rd1 := dial(t, addr)
	if banner, _ := rd1.ReadString('\n'); !strings.HasPrefix(banner, "220 ") {
		t.Fatalf("banner = %q", banner)
	}
	// Second connection is refused with 421.
	conn2, rd2 := dial(t, addr)
	reply, err := rd2.ReadString('\n')
	if err != nil {
		t.Fatalf("read 421: %v", err)
	}
	if !strings.HasPrefix(reply, "421 ") {
		t.Fatalf("second connection reply = %q, want 421", reply)
	}
	conn2.Close()
	// First connection still works.
	send(t, conn1, "NOOP")
	replies := readReply(t, rd1)
	if replies[0] != "250 2.0.0 OK" {
		t.Fatalf("NOOP reply = %v", replies)
	}
}

func TestConnectionDropMidDataLeavesNoArtifacts(t *testing.T) {
	addr, store := startTestServer(t, nil)
	conn, rd := dial(t, addr)
	readReply(t, rd) // banner
	for _, cmd := range []string{
		"EHLO client.test",
		"MAIL FROM:<alice@example.test>",
		"RCPT TO:<bob@example.test>",
		"DATA",
	} {
		send(t, conn, cmd)
		readReply(t, rd)
	}
	send(t, conn, "partial body, connection drops now")
	conn.Close()

	// Give the server a moment to observe the drop and abort.
	time.Sleep(300 * time.Millisecond)
	msgs, err := store.ListMessages()
	if err != nil || len(msgs) != 0 {
		t.Fatalf("messages = %v, %v after drop", msgs, err)
	}
	for _, dir := range []string{store.TmpDir(), store.MsgDir()} {
		ents, err := os.ReadDir(dir)
		if err != nil {
			t.Fatalf("readdir: %v", err)
		}
		if len(ents) != 0 {
			t.Fatalf("dir %s has leftovers: %v", dir, ents)
		}
	}
}

func TestStorageFailureReturns451OverTCP(t *testing.T) {
	addr, store := startTestServer(t, nil)
	// Break the spool directory so Begin fails.
	if err := os.Chmod(store.TmpDir(), 0o500); err != nil {
		t.Fatalf("chmod: %v", err)
	}
	t.Cleanup(func() { os.Chmod(store.TmpDir(), 0o755) })

	conn, rd := dial(t, addr)
	readReply(t, rd)
	for _, cmd := range []string{
		"EHLO client.test",
		"MAIL FROM:<alice@example.test>",
		"RCPT TO:<bob@example.test>",
	} {
		send(t, conn, cmd)
		readReply(t, rd)
	}
	send(t, conn, "DATA")
	replies := readReply(t, rd)
	if replies[0] != "451 4.3.0 Local storage error, try again later" {
		t.Fatalf("DATA reply = %v, want 451", replies)
	}
	msgs, _ := store.ListMessages()
	if len(msgs) != 0 {
		t.Fatalf("stored %d messages despite storage failure", len(msgs))
	}
}
