package store

import (
	"strings"
	"testing"
)

func TestOpenRejectsUnknownDriver(t *testing.T) {
	if _, err := Open("postgres", "file::memory:", "r", 1); err == nil ||
		!strings.Contains(err.Error(), "driver") {
		t.Fatalf("unknown driver must fail, got %v", err)
	}
}

func TestCloseIsIdempotent(t *testing.T) {
	st, err := Open("modernc-sqlite", "file::memory:?cache=shared", "r2", 1)
	if err != nil {
		t.Fatal(err)
	}
	if err := st.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}
}

func TestRecentBoundsLimit(t *testing.T) {
	st, err := Open("modernc-sqlite", "file::memory:?cache=shared", "r3", 1)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = st.Close() })
	if err := st.Insert(Record{RunID: "x", Op: "decode", Mode: "BER",
		Status: "ok", InputSHA256: "h"}); err != nil {
		t.Fatal(err)
	}
	rows, err := st.Recent(0) // clamps to default
	if err != nil || len(rows) != 1 {
		t.Fatalf("Recent(0): rows=%d err=%v", len(rows), err)
	}
	rows, err = st.Recent(5000) // clamps to 1000
	if err != nil || len(rows) != 1 {
		t.Fatalf("Recent(5000): rows=%d err=%v", len(rows), err)
	}
}
