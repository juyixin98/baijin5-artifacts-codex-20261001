package store

import (
	"context"
	"testing"
)

func TestRecordAndList(t *testing.T) {
	ctx := context.Background()
	s, err := Open(ctx, ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()

	r := Result{
		ID:           "req-1",
		ConnID:       "conn-1",
		StreamID:     1,
		BlockBytes:   42,
		EmittedBytes: 30,
		Fields: []Field{
			{Name: ":method", Value: "GET"},
			{Name: "authorization", Value: "Bearer x", Sensitive: true},
		},
	}
	if err := s.RecordResult(ctx, r); err != nil {
		t.Fatal(err)
	}

	rows, err := s.ListRequests(ctx, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("rows = %d, want 1", len(rows))
	}
	got := rows[0]
	if got.ID != "req-1" || got.ConnID != "conn-1" || got.StreamID != 1 {
		t.Fatalf("correlation fields wrong: %+v", got)
	}
	if got.Status != "ok" || got.FieldCount != 2 || got.BlockBytes != 42 || got.EmittedBytes != 30 {
		t.Fatalf("row metrics wrong: %+v", got)
	}
	if got.ErrorKind.Valid {
		t.Fatalf("successful row should have null error kind, got %q", got.ErrorKind.String)
	}
}

func TestFailedResultRecordsKind(t *testing.T) {
	ctx := context.Background()
	s, err := Open(ctx, ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()

	if err := s.RecordResult(ctx, Result{
		ID: "req-bad", ConnID: "conn-9", StreamID: 3, BlockBytes: 2,
		Failed: true, Kind: "index-zero", Detail: "indexed header field index is 0",
	}); err != nil {
		t.Fatal(err)
	}

	rows, err := s.ListRequests(ctx, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("rows = %d", len(rows))
	}
	if rows[0].Status != "error" || rows[0].ErrorKind.String != "index-zero" {
		t.Fatalf("failure not recorded: %+v", rows[0])
	}

	counts, err := s.CountByStatus(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if counts["error"] != 1 {
		t.Fatalf("counts = %v, want one error", counts)
	}
}

func TestNewestFirstOrdering(t *testing.T) {
	ctx := context.Background()
	s, err := Open(ctx, ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	for _, id := range []string{"a", "b", "c"} {
		if err := s.RecordResult(ctx, Result{ID: id, ConnID: "c", StreamID: 1}); err != nil {
			t.Fatal(err)
		}
	}
	rows, _ := s.ListRequests(ctx, 10)
	if len(rows) != 3 {
		t.Fatalf("rows = %d", len(rows))
	}
	// Same-millisecond timestamps tie-break on id DESC.
	if rows[0].ID != "c" {
		t.Fatalf("newest first violated: first=%s", rows[0].ID)
	}
}
