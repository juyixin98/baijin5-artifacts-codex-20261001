package store_test

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"berd/internal/harness"
	"berd/internal/store"
)

func TestStoreRoundTrip(t *testing.T) {
	h := harness.New(t)
	path := filepath.Join(t.TempDir(), "audit.db")
	st, err := store.Open(path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer st.Close()

	ctx := context.Background()
	entries := []store.Entry{
		{RunID: "run-a", RequestID: "req-1", Time: time.Now(), Remote: "127.0.0.1:1",
			Op: "decode", InputSHA256: "aa", InputBytes: 3, OK: true, Offset: -1},
		{RunID: "run-a", RequestID: "req-2", Time: time.Now(), Remote: "127.0.0.1:2",
			Op: "decode", InputSHA256: "bb", InputBytes: 2, OK: false,
			Category: "truncation", Offset: 2, Message: "content truncated"},
		{RunID: "run-b", RequestID: "req-3", Time: time.Now(), Remote: "127.0.0.1:3",
			Op: "health", InputSHA256: "cc", InputBytes: 0, OK: true, Offset: -1},
	}
	for _, e := range entries {
		if err := st.Log(ctx, e); err != nil {
			t.Fatalf("log: %v", err)
		}
	}

	all, err := st.Count(ctx, "")
	if err != nil {
		t.Fatalf("count all: %v", err)
	}
	h.ExpectEqual("total rows", all, 3)

	runA, err := st.Count(ctx, "run-a")
	if err != nil {
		t.Fatalf("count run-a: %v", err)
	}
	h.ExpectEqual("run-a rows", runA, 2)

	// Reopening the same file must see the persisted rows.
	st2, err := store.Open(path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer st2.Close()
	again, err := st2.Count(ctx, "run-b")
	if err != nil {
		t.Fatalf("count run-b: %v", err)
	}
	h.ExpectEqual("run-b rows after reopen", again, 1)
}

func TestStoreOpenFailure(t *testing.T) {
	h := harness.New(t)
	_, err := store.Open(filepath.Join(t.TempDir(), "no-such-dir", "x.db"))
	if err == nil {
		t.Fatal("expected open error for missing directory")
	}
	h.Step("verdict=PASS basis=open failure surfaced: %v", err)
}

func TestStoreLogAfterCloseFails(t *testing.T) {
	h := harness.New(t)
	st, err := store.Open(filepath.Join(t.TempDir(), "audit.db"))
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	if err := st.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}
	err = st.Log(context.Background(), store.Entry{RunID: "run-x", RequestID: "req-x",
		Time: time.Now(), Op: "decode", Offset: -1})
	if err == nil {
		t.Fatal("expected log error after close")
	}
	h.Step("verdict=PASS basis=write after close surfaced, not swallowed: %v", err)

	_, err = st.Count(context.Background(), "")
	if err == nil {
		t.Fatal("expected count error after close")
	}
	h.Step("verdict=PASS basis=count after close surfaced: %v", err)
}
