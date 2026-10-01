package store_test

import (
	"path/filepath"
	"testing"
	"time"

	"localstun/internal/audit"
	"localstun/internal/store"
)

func TestSQLiteSinkRoundTripAndSummary(t *testing.T) {
	dbPath := filepath.Join(t.TempDir(), "audit.db")
	sink, err := store.OpenSQLite(dbPath)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer sink.Close()

	now := time.Date(2026, 10, 1, 12, 0, 0, 0, time.UTC)
	records := []audit.Record{
		{RunID: "run-A", Seq: 1, Timestamp: now, Component: "server",
			Event: "binding_success", TxID: "aa", SrcAddr: "127.0.0.1:1",
			WireHex: "0001"},
		{RunID: "run-A", Seq: 2, Timestamp: now.Add(time.Millisecond),
			Component: "server", Event: "decode_failed", Kind: "input",
			TxID: "bb", Detail: "bad cookie"},
		{RunID: "run-A", Seq: 3, Timestamp: now.Add(2 * time.Millisecond),
			Component: "server", Event: "tamper", Kind: "integrity",
			TxID: "cc", Detail: "HMAC mismatch"},
		{RunID: "run-B", Seq: 1, Timestamp: now, Component: "client",
			Event: "request_sent", TxID: "dd"},
	}
	for i, r := range records {
		if err := sink.Write(r); err != nil {
			t.Fatalf("write %d: %v", i, err)
		}
	}

	summary, err := sink.SummarizeRun("run-A")
	if err != nil {
		t.Fatal(err)
	}
	if summary.Total != 3 {
		t.Fatalf("run-A total=%d want 3", summary.Total)
	}
	if summary.ByEvent["binding_success"] != 1 ||
		summary.ByEvent["decode_failed"] != 1 || summary.ByEvent["tamper"] != 1 {
		t.Fatalf("event counts = %+v", summary.ByEvent)
	}
	if summary.ByKind["input"] != 1 || summary.ByKind["integrity"] != 1 {
		t.Fatalf("kind counts = %+v", summary.ByKind)
	}

	// Reopen to prove persistence and idempotent schema creation.
	if err := sink.Close(); err != nil {
		t.Fatal(err)
	}
	sink2, err := store.OpenSQLite(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer sink2.Close()
	runs, err := sink2.ListRuns()
	if err != nil {
		t.Fatal(err)
	}
	if len(runs) != 2 {
		t.Fatalf("runs = %d want 2", len(runs))
	}
	found := map[string]bool{}
	for _, ri := range runs {
		found[ri.RunID] = true
	}
	if !found["run-A"] || !found["run-B"] {
		t.Fatalf("missing runs: %+v", runs)
	}
}

func TestSQLiteSinkMemoryDSN(t *testing.T) {
	sink, err := store.OpenSQLite(":memory:")
	if err != nil {
		t.Fatalf("open memory db: %v", err)
	}
	defer sink.Close()
	if err := sink.Write(audit.Record{
		RunID: "r", Seq: 1, Timestamp: time.Now(), Component: "client",
		Event: "request_sent"}); err != nil {
		t.Fatal(err)
	}
}
