package service

import (
	"os"
	"path/filepath"
	"testing"

	"hpacklab/state"
)

// TestClassifyCategories pins the failure category strings for every
// representative decode error, so log/audit consumers can rely on them.
func TestClassifyCategories(t *testing.T) {
	svc := newTestService(t)
	cases := []struct {
		name  string
		block []byte
		want  string
	}{
		{"index zero", []byte{0x80}, "index-zero"},
		{"index out of range", []byte{0xbe}, "index-out-of-range"},
		{"size update placement", []byte{0x82, 0x20}, "size-update-placement"},
		{"size update too large", []byte{0x3f, 0xe2, 0x1f}, "size-update-too-large"},
		{"truncated", []byte{0x00, 0x05, 'a'}, "truncated"},
		{"huffman padding", []byte{0x00, 0x01, 'x', 0x81, 0x18}, "huffman-padding"},
		{"huffman eos", []byte{0x00, 0x01, 'x', 0x84, 0xff, 0xff, 0xff, 0xff}, "huffman-eos"},
		{"integer overflow", []byte{0x7f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x7f, 0x01, 'a'}, "integer-overflow"},
	}
	for _, tc := range cases {
		conn, err := svc.OpenConn("cat-" + tc.name)
		if err != nil {
			t.Fatal(err)
		}
		res, err := conn.Decode(tc.block)
		if err == nil {
			t.Fatalf("%s: expected failure", tc.name)
		}
		if res.Failure != tc.want {
			t.Fatalf("%s: Failure=%q, want %q", tc.name, res.Failure, tc.want)
		}
	}
	// Desync is a category of its own.
	conn, _ := svc.OpenConn("cat-desync-src")
	if _, err := conn.Decode([]byte{0x80}); err == nil {
		t.Fatal("expected failure")
	}
	res, err := conn.Decode([]byte{0x82})
	if err == nil || res.Failure != "desync" {
		t.Fatalf("desync: Failure=%q err=%v", res.Failure, err)
	}
}

func TestLimitFailureCategories(t *testing.T) {
	audit, err := OpenAudit(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer audit.Close()
	svc, err := New(Config{
		MaxDynamicTableSize: 4096,
		Limits:              state.Limits{MaxHeaderListBytes: 10, MaxStringLen: 2},
		Audit:               audit,
	})
	if err != nil {
		t.Fatal(err)
	}
	c1, _ := svc.OpenConn("lim-1")
	// Two 36-byte fields exceed the 10-byte list limit.
	res, err := c1.Decode([]byte{0x00, 0x01, 'a', 0x01, 'b', 0x00, 0x01, 'c', 0x01, 'd'})
	if err == nil || res.Failure != "header-list-too-large" {
		t.Fatalf("list limit: Failure=%q err=%v", res.Failure, err)
	}
	c2, _ := svc.OpenConn("lim-2")
	// Name "abc" exceeds the 2-byte string limit.
	res, err = c2.Decode([]byte{0x00, 0x03, 'a', 'b', 'c', 0x01, 'd'})
	if err == nil || res.Failure != "string-too-long" {
		t.Fatalf("string limit: Failure=%q err=%v", res.Failure, err)
	}
}

func TestConnLifecycle(t *testing.T) {
	svc := newTestService(t)
	conn, err := svc.OpenConn("lc")
	if err != nil {
		t.Fatal(err)
	}
	if conn.ID() != "lc" {
		t.Fatalf("ID()=%q", conn.ID())
	}
	if svc.Conn("lc") != conn {
		t.Fatal("Conn lookup failed")
	}
	if svc.Conn("missing") != nil {
		t.Fatal("Conn for unknown id must be nil")
	}
	svc.CloseConn("lc")
	if svc.Conn("lc") != nil {
		t.Fatal("CloseConn did not drop the connection")
	}
	if _, err := svc.OpenConn(""); err == nil {
		t.Fatal("empty connection id must be rejected")
	}
	if conn.Encoder() == nil || conn.Decoder() == nil {
		t.Fatal("accessors must expose state machines")
	}
}

// TestFileBackedAudit verifies the audit trail survives in a real SQLite
// file and can be read back per request identity.
func TestFileBackedAudit(t *testing.T) {
	path := filepath.Join(t.TempDir(), "audit.db")
	audit, err := OpenAudit(path)
	if err != nil {
		t.Fatal(err)
	}
	svc, err := New(Config{MaxDynamicTableSize: 4096, Limits: state.DefaultLimits, Audit: audit})
	if err != nil {
		t.Fatal(err)
	}
	conn, _ := svc.OpenConn("file-conn")
	res, err := conn.Decode([]byte{0x82})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Decode([]byte{0x80}); err == nil {
		t.Fatal("expected failure") // also desyncs; fine for this test
	}
	if err := audit.Close(); err != nil {
		t.Fatal(err)
	}
	// Reopen and read back.
	audit2, err := OpenAudit(path)
	if err != nil {
		t.Fatal(err)
	}
	defer audit2.Close()
	rows, err := audit2.RowsFor(res.RequestID)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) < 2 {
		t.Fatalf("persisted rows=%d, want >= 2", len(rows))
	}
	fails, err := audit2.Failures()
	if err != nil {
		t.Fatal(err)
	}
	if len(fails) != 1 {
		t.Fatalf("persisted failures=%d, want 1", len(fails))
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("audit file missing: %v", err)
	}
}

func TestEncodeAuditRows(t *testing.T) {
	svc := newTestService(t)
	conn, _ := svc.OpenConn("enc")
	_, res, err := conn.Encode([]state.Field{{Name: "x-a", Value: "1"}})
	if err != nil {
		t.Fatal(err)
	}
	rows, err := svc.cfg.Audit.RowsFor(res.RequestID)
	if err != nil {
		t.Fatal(err)
	}
	var sawOutcome bool
	for _, r := range rows {
		if r.Phase == "outcome" {
			sawOutcome = true
		}
	}
	if !sawOutcome {
		t.Fatalf("encode audit rows missing outcome: %+v", rows)
	}
}
