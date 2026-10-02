package store

import (
	"path/filepath"
	"testing"
)

func TestOpenInMemoryAndPersistExchange(t *testing.T) {
	st, err := Open("")
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	if err := st.EnsureRun("run-1", "stund", "2026-10-02T00:00:00Z", "note"); err != nil {
		t.Fatal(err)
	}
	x := Exchange{
		RunID: "run-1", TxnID: "abcd", RemoteAddr: "127.0.0.1:99",
		Family: "ipv4", Outcome: "success", MappedIP: "127.0.0.1", MappedPort: 40000,
	}
	if err := st.RecordExchange("2026-10-02T00:00:01Z", x); err != nil {
		t.Fatal(err)
	}
	n, err := st.ExchangeCount("run-1")
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 {
		t.Fatalf("count = %d, want 1", n)
	}
	// A different run sees zero rows.
	n, _ = st.ExchangeCount("other")
	if n != 0 {
		t.Fatalf("cross-run leakage: %d", n)
	}
}

func TestOpenFileSurvivesReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "evidence.db")
	st, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := st.EnsureRun("run-persist", "stunc", "ts", ""); err != nil {
		t.Fatal(err)
	}
	if err := st.InsertEvent("run-persist", "ts", 1, "stunc", "info", "x", "{}"); err != nil {
		t.Fatal(err)
	}
	if err := st.RecordExchange("ts", Exchange{RunID: "run-persist", Outcome: "timeout"}); err != nil {
		t.Fatal(err)
	}
	if err := st.Close(); err != nil {
		t.Fatal(err)
	}

	// Reopen: schema migration must be idempotent and data must survive.
	st2, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer st2.Close()
	if n, err := st2.ExchangeCount("run-persist"); err != nil || n != 1 {
		t.Fatalf("after reopen count=%d err=%v, want 1", n, err)
	}
	// Duplicate EnsureRun is ignored (PRIMARY KEY conflict).
	if err := st2.EnsureRun("run-persist", "stunc", "ts", ""); err != nil {
		t.Fatalf("idempotent EnsureRun: %v", err)
	}
}

func TestOpenRejectsUnwritablePath(t *testing.T) {
	bad := filepath.Join(t.TempDir(), "missing-dir", "evidence.db")
	if _, err := Open(bad); err == nil {
		t.Fatal("expected error opening a path in a missing directory")
	}
}

func TestOperationsAfterCloseReturnErrors(t *testing.T) {
	st, err := Open("")
	if err != nil {
		t.Fatal(err)
	}
	if err := st.Close(); err != nil {
		t.Fatal(err)
	}
	x := Exchange{RunID: "r", Outcome: "success"}
	if err := st.RecordExchange("ts", x); err == nil {
		t.Fatal("RecordExchange on closed DB must error")
	}
	if err := st.EnsureRun("r", "c", "ts", ""); err == nil {
		t.Fatal("EnsureRun on closed DB must error")
	}
	if err := st.InsertEvent("r", "ts", 1, "c", "info", "e", "{}"); err == nil {
		t.Fatal("InsertEvent on closed DB must error")
	}
	if _, err := st.ExchangeCount("r"); err == nil {
		t.Fatal("ExchangeCount on closed DB must error")
	}
}
