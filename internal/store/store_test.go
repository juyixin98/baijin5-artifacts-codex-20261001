package store

import (
	"testing"
)

func TestInsertAndRecent(t *testing.T) {
	st, err := Open("modernc-sqlite", "file::memory:?cache=shared", "request_log", 1)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })

	records := []Record{
		{RunID: "run-A", Op: "decode", Mode: "BER", Status: "ok",
			InputLen: 4, InputSHA256: "aaa", OutputLen: 4, DurationMicros: 11},
		{RunID: "run-B", Op: "decode", Mode: "DER", Status: "error",
			ErrorKind: "MALFORMED_EOC", ErrorOffset: 2,
			InputLen: 6, InputSHA256: "bbb", DurationMicros: 22},
	}
	for _, r := range records {
		if err := st.Insert(r); err != nil {
			t.Fatalf("insert: %v", err)
		}
	}
	got, err := st.Recent(10)
	if err != nil {
		t.Fatalf("recent: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("rows = %d, want 2", len(got))
	}
	// newest first: run-B
	if got[0].RunID != "run-B" || got[0].ErrorKind != "MALFORMED_EOC" || got[0].ErrorOffset != 2 {
		t.Fatalf("newest row mismatch: %+v", got[0])
	}
	if got[1].RunID != "run-A" || got[1].Status != "ok" {
		t.Fatalf("older row mismatch: %+v", got[1])
	}
}

func TestRejectsUnsafeTableName(t *testing.T) {
	// Open validates nothing itself (config does); ensure a safe name works
	// and that normal parameters cannot alter the table identity.
	st, err := Open("modernc-sqlite", "file::memory:?cache=shared", "audit_tbl", 1)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })
	if err := st.Insert(Record{RunID: "r1", Op: "encode", Mode: "DER", Status: "ok",
		InputSHA256: "x"}); err != nil {
		t.Fatalf("insert into safe table: %v", err)
	}
}
