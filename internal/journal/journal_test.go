package journal

import (
	"testing"
)

func TestJournalRoundTrip(t *testing.T) {
	j, err := Open(":memory:", "test-run-1", `{"listen_addr":"127.0.0.1:0"}`)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer j.Close()

	j.LogEvent(1, "in", "HEADERS", 1, "len=19", "stream opened")
	j.LogEvent(1, "out", "DATA", 1, "conn_window=65535", "sent")

	n, err := j.CountEvents()
	if err != nil {
		t.Fatalf("count: %v", err)
	}
	if n != 2 {
		t.Fatalf("want 2 events, got %d", n)
	}

	// A second run in the same database keeps events separated by run_id.
	j2, err := Open(":memory:", "test-run-2", "{}")
	if err != nil {
		t.Fatalf("open 2: %v", err)
	}
	defer j2.Close()
	j2.LogEvent(2, "in", "PING", 0, "", "")
	n2, _ := j2.CountEvents()
	if n2 != 1 {
		t.Fatalf("run isolation: want 1 event for run 2, got %d", n2)
	}
}

func TestJournalFileBacked(t *testing.T) {
	path := t.TempDir() + "/journal.db"
	j, err := Open(path, "file-run", "{}")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	j.LogEvent(7, "out", "GOAWAY", 0, "last=1", "connection error: PROTOCOL_ERROR")
	if err := j.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}

	// Reopen and verify persistence.
	j2, err := Open(path, "file-run-2", "{}")
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer j2.Close()
	var count int
	if err := j2.db.QueryRow(`SELECT COUNT(*) FROM events WHERE run_id = 'file-run'`).Scan(&count); err != nil {
		t.Fatalf("query: %v", err)
	}
	if count != 1 {
		t.Fatalf("persisted events: want 1, got %d", count)
	}
}
