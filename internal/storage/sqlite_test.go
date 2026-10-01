package storage

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func openTempStore(t *testing.T) *SQLiteStore {
	t.Helper()
	s, err := OpenSQLite(t.TempDir())
	if err != nil {
		t.Fatalf("OpenSQLite: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

func dirEntries(t *testing.T, dir string) []string {
	t.Helper()
	ents, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("readdir %s: %v", dir, err)
	}
	var names []string
	for _, e := range ents {
		names = append(names, e.Name())
	}
	return names
}

func TestCommitPersistsBodyAndIndex(t *testing.T) {
	s := openTempStore(t)
	p, err := s.Begin(Envelope{MailFrom: "alice@example.test", RcptTo: []string{"bob@example.test"}})
	if err != nil {
		t.Fatalf("Begin: %v", err)
	}
	body := "Subject: hi\r\n\r\n.dotted line after unstuffing\r\n"
	if _, err := p.Write([]byte(body)); err != nil {
		t.Fatalf("Write: %v", err)
	}
	id, err := p.Commit()
	if err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if !strings.HasPrefix(id, "msg-") {
		t.Fatalf("id = %q", id)
	}

	// The .eml file holds exactly the bytes streamed.
	raw, err := os.ReadFile(filepath.Join(s.MsgDir(), id+".eml"))
	if err != nil {
		t.Fatalf("read eml: %v", err)
	}
	if string(raw) != body {
		t.Fatalf("file body = %q want %q", raw, body)
	}

	// The index agrees with the file.
	m, err := s.GetMessage(id)
	if err != nil {
		t.Fatalf("GetMessage: %v", err)
	}
	if m.MailFrom != "alice@example.test" || m.SizeBytes != int64(len(body)) {
		t.Fatalf("message = %+v", m)
	}
	if len(m.RcptTo) != 1 || m.RcptTo[0] != "bob@example.test" {
		t.Fatalf("rcpts = %v", m.RcptTo)
	}

	// Spool directory is clean after commit.
	if ents := dirEntries(t, s.TmpDir()); len(ents) != 0 {
		t.Fatalf("tmp dir not clean: %v", ents)
	}
}

func TestRepeatedRecipientsDeliveredOnce(t *testing.T) {
	s := openTempStore(t)
	env := Envelope{
		MailFrom: "alice@example.test",
		RcptTo:   []string{"bob@example.test", "bob@example.test", "carol@example.test"},
	}
	p, _ := s.Begin(env)
	p.Write([]byte("x\r\n"))
	id, err := p.Commit()
	if err != nil {
		t.Fatalf("Commit: %v", err)
	}
	m, err := s.GetMessage(id)
	if err != nil {
		t.Fatalf("GetMessage: %v", err)
	}
	// Delivery scope is the deduplicated set of accepted recipients.
	if len(m.RcptTo) != 2 || m.RcptTo[0] != "bob@example.test" || m.RcptTo[1] != "carol@example.test" {
		t.Fatalf("rcpts = %v", m.RcptTo)
	}
}

func TestAbortRemovesSpool(t *testing.T) {
	s := openTempStore(t)
	p, _ := s.Begin(Envelope{MailFrom: "a@example.test", RcptTo: []string{"b@example.test"}})
	p.Write([]byte("partial"))
	if err := p.Abort(); err != nil {
		t.Fatalf("Abort: %v", err)
	}
	if ents := dirEntries(t, s.TmpDir()); len(ents) != 0 {
		t.Fatalf("tmp dir not clean after abort: %v", ents)
	}
	msgs, err := s.ListMessages()
	if err != nil || len(msgs) != 0 {
		t.Fatalf("messages = %v, %v", msgs, err)
	}
}

func TestCommitFailureLeavesNoArtifacts(t *testing.T) {
	s := openTempStore(t)
	// Break the messages directory so the rename must fail.
	if err := os.Chmod(s.MsgDir(), 0o500); err != nil {
		t.Fatalf("chmod: %v", err)
	}
	t.Cleanup(func() { os.Chmod(s.MsgDir(), 0o755) })

	p, _ := s.Begin(Envelope{MailFrom: "a@example.test", RcptTo: []string{"b@example.test"}})
	p.Write([]byte("doomed"))
	if _, err := p.Commit(); err == nil {
		t.Fatalf("Commit should fail with unwritable messages dir")
	}
	if ents := dirEntries(t, s.TmpDir()); len(ents) != 0 {
		t.Fatalf("tmp dir not clean after failed commit: %v", ents)
	}
	msgs, _ := s.ListMessages()
	if len(msgs) != 0 {
		t.Fatalf("index has %d messages after failed commit", len(msgs))
	}
}

func TestMessageIDsUnique(t *testing.T) {
	s := openTempStore(t)
	seen := map[string]bool{}
	for i := 0; i < 50; i++ {
		p, _ := s.Begin(Envelope{MailFrom: "a@example.test", RcptTo: []string{"b@example.test"}})
		p.Write([]byte("x"))
		id, err := p.Commit()
		if err != nil {
			t.Fatalf("Commit %d: %v", i, err)
		}
		if seen[id] {
			t.Fatalf("duplicate id %q", id)
		}
		seen[id] = true
	}
	msgs, err := s.ListMessages()
	if err != nil || len(msgs) != 50 {
		t.Fatalf("listed %d messages, err %v", len(msgs), err)
	}
}

func TestWriteAfterCommitRejected(t *testing.T) {
	s := openTempStore(t)
	p, _ := s.Begin(Envelope{MailFrom: "a@example.test", RcptTo: []string{"b@example.test"}})
	p.Write([]byte("x"))
	if _, err := p.Commit(); err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if _, err := p.Write([]byte("y")); err == nil {
		t.Fatalf("write after commit should fail")
	}
	if _, err := p.Commit(); err == nil {
		t.Fatalf("second commit should fail")
	}
}

func TestOpenSQLiteBadDir(t *testing.T) {
	// A path under a file cannot become a directory.
	f, err := os.CreateTemp(t.TempDir(), "file")
	if err != nil {
		t.Fatal(err)
	}
	f.Close()
	if _, err := OpenSQLite(filepath.Join(f.Name(), "sub")); err == nil {
		t.Fatalf("OpenSQLite should fail for uncreatable dir")
	}
}

func TestGetMessageMissing(t *testing.T) {
	s := openTempStore(t)
	if _, err := s.GetMessage("msg-does-not-exist"); err == nil {
		t.Fatalf("expected error for unknown message id")
	}
}

func TestListMessagesEmpty(t *testing.T) {
	s := openTempStore(t)
	msgs, err := s.ListMessages()
	if err != nil {
		t.Fatalf("ListMessages: %v", err)
	}
	if len(msgs) != 0 {
		t.Fatalf("want empty, got %v", msgs)
	}
}

func TestAbortIdempotent(t *testing.T) {
	s := openTempStore(t)
	p, _ := s.Begin(Envelope{MailFrom: "a@example.test", RcptTo: []string{"b@example.test"}})
	if err := p.Abort(); err != nil {
		t.Fatalf("first Abort: %v", err)
	}
	if err := p.Abort(); err != nil {
		t.Fatalf("second Abort should be a no-op: %v", err)
	}
}
