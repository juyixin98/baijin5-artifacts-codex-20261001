package store_test

import (
	"strings"
	"testing"
	"time"

	"localstun/internal/audit"
	"localstun/internal/store"
)

func TestOpenSQLite_BadDSN(t *testing.T) {
	// A DSN pointing at a path that is actually a directory fails schema setup.
	dir := t.TempDir()
	if _, err := store.OpenSQLite(dir); err == nil {
		t.Fatal("opening a directory as a database must fail")
	}
}

func TestWrite_AfterCloseErrors(t *testing.T) {
	sink, err := store.OpenSQLite(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	if err := sink.Close(); err != nil {
		t.Fatal(err)
	}
	err = sink.Write(audit.Record{
		RunID: "r", Seq: 1, Timestamp: time.Now(), Component: "c", Event: "e",
	})
	if err == nil || !strings.Contains(err.Error(), "audit") {
		t.Fatalf("write after close must fail with an audit error, got %v", err)
	}
}

func TestSummarizeRun_EmptyAndReopen(t *testing.T) {
	sink, err := store.OpenSQLite(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer sink.Close()
	s, err := sink.SummarizeRun("does-not-exist")
	if err != nil {
		t.Fatal(err)
	}
	if s.Total != 0 {
		t.Fatalf("empty run total=%d", s.Total)
	}
	runs, err := sink.ListRuns()
	if err != nil {
		t.Fatal(err)
	}
	if len(runs) != 0 {
		t.Fatalf("expected no runs, got %d", len(runs))
	}
}
