package service

import (
	"errors"
	"io"
	"log"
	"net"
	"strings"
	"testing"

	"hpacklab/state"
)

func newTestService(t *testing.T) *Service {
	t.Helper()
	audit, err := OpenAudit(":memory:")
	if err != nil {
		t.Fatalf("OpenAudit: %v", err)
	}
	t.Cleanup(func() { audit.Close() })
	svc, err := New(Config{
		MaxDynamicTableSize: 4096,
		Limits:              state.DefaultLimits,
		Audit:               audit,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return svc
}

func TestRequestIdentityAndAudit(t *testing.T) {
	svc := newTestService(t)
	conn, err := svc.OpenConn("c1")
	if err != nil {
		t.Fatal(err)
	}
	res, err := conn.Decode([]byte{0x82})
	if err != nil {
		t.Fatal(err)
	}
	if res.RequestID == "" || res.ConnID != "c1" {
		t.Fatalf("result identity: %+v", res)
	}
	rows, err := svc.cfg.Audit.RowsFor(res.RequestID)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) < 2 {
		t.Fatalf("audit rows=%d, want step + outcome", len(rows))
	}
	var sawStep, sawOutcome bool
	for _, r := range rows {
		if r.ConnID != "c1" || r.RequestID != res.RequestID {
			t.Fatalf("row identity mismatch: %+v", r)
		}
		sawStep = sawStep || r.Phase == "step"
		sawOutcome = sawOutcome || r.Phase == "outcome"
	}
	if !sawStep || !sawOutcome {
		t.Fatalf("rows missing step/outcome: %+v", rows)
	}
}

func TestFailureClassifiedAndAudited(t *testing.T) {
	svc := newTestService(t)
	conn, _ := svc.OpenConn("c1")
	res, err := conn.Decode([]byte{0x80}) // index 0
	if err == nil {
		t.Fatal("expected failure")
	}
	if res.Failure != "index-zero" {
		t.Fatalf("Failure=%q, want index-zero", res.Failure)
	}
	fails, err := svc.cfg.Audit.Failures()
	if err != nil {
		t.Fatal(err)
	}
	if len(fails) != 1 || !strings.Contains(fails[0].Detail, "index-zero") {
		t.Fatalf("failures=%+v", fails)
	}
	// After failure the connection is desynchronized.
	if _, err := conn.Decode([]byte{0x82}); !errors.Is(err, state.ErrDesync) {
		t.Fatalf("got %v, want ErrDesync", err)
	}
}

func TestConnectionStateIsolation(t *testing.T) {
	svc := newTestService(t)
	c1, _ := svc.OpenConn("c1")
	c2, _ := svc.OpenConn("c2")

	// c1's encoder inserts into its own dynamic table.
	block, _, err := c1.Encode([]state.Field{{Name: "x-tenant", Value: "one"}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := c1.Decode(block); err != nil {
		t.Fatal(err)
	}
	if c1.Decoder().Table().Dyn.Len() != 1 {
		t.Fatalf("c1 dyn len=%d, want 1", c1.Decoder().Table().Dyn.Len())
	}
	// c2 must see none of c1's state.
	if c2.Decoder().Table().Dyn.Len() != 0 {
		t.Fatalf("c2 dyn len=%d, want 0 (isolation violated)", c2.Decoder().Table().Dyn.Len())
	}
	// Feeding c1's block to c2 still decodes (literal form is
	// self-contained) but builds c2's own independent table entry.
	if _, err := c2.Decode(block); err != nil {
		t.Fatal(err)
	}
	if c2.Decoder().Table().Dyn.Len() != 1 {
		t.Fatalf("c2 dyn len=%d, want 1", c2.Decoder().Table().Dyn.Len())
	}
	// And a block that depends on c1's table history (indexed reference
	// to dynamic index 62) must fail on a fresh connection.
	c3, _ := svc.OpenConn("c3")
	if _, err := c3.Decode([]byte{0xbe}); err == nil {
		t.Fatal("dynamic index 62 must not resolve on a fresh connection")
	}
}

// TestPipeTransport moves header blocks between two endpoints over
// net.Pipe (stdlib networking) and verifies state consistency across
// multiple blocks on one connection.
func TestPipeTransport(t *testing.T) {
	svc := newTestService(t)
	client, _ := svc.OpenConn("client")
	server, _ := svc.OpenConn("server")

	p1, p2 := net.Pipe()
	defer p1.Close()
	defer p2.Close()

	writeErr := make(chan error, 1)
	go func() {
		for _, fields := range [][]state.Field{
			{{Name: ":method", Value: "GET"}, {Name: "x-req", Value: "1"}},
			{{Name: ":method", Value: "GET"}, {Name: "x-req", Value: "1"}},
		} {
			block, _, err := client.Encode(fields)
			if err != nil {
				writeErr <- err
				return
			}
			var hdr [2]byte
			hdr[0] = byte(len(block) >> 8)
			hdr[1] = byte(len(block))
			if _, err := p1.Write(hdr[:]); err != nil {
				writeErr <- err
				return
			}
			if _, err := p1.Write(block); err != nil {
				writeErr <- err
				return
			}
		}
		writeErr <- nil
	}()

	for i := 0; i < 2; i++ {
		var hdr [2]byte
		if _, err := io.ReadFull(p2, hdr[:]); err != nil {
			t.Fatalf("block %d header: %v", i, err)
		}
		block := make([]byte, int(hdr[0])<<8|int(hdr[1]))
		if _, err := io.ReadFull(p2, block); err != nil {
			t.Fatalf("block %d body: %v", i, err)
		}
		res, err := server.Decode(block)
		if err != nil {
			t.Fatalf("block %d: %v", i, err)
		}
		if len(res.Fields) != 2 || res.Fields[1].Value != "1" {
			t.Fatalf("block %d fields=%+v", i, res.Fields)
		}
	}
	// Second block must have used the dynamic table (indexed reference).
	if server.Decoder().Table().Dyn.Len() != 1 {
		t.Fatalf("server dyn len=%d, want 1", server.Decoder().Table().Dyn.Len())
	}
	if err := <-writeErr; err != nil {
		t.Fatalf("writer goroutine: %v", err)
	}
}

func TestLoggerReceivesExplainableLines(t *testing.T) {
	audit, err := OpenAudit(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer audit.Close()
	var sb strings.Builder
	svc, err := New(Config{
		MaxDynamicTableSize: 4096,
		Limits:              state.DefaultLimits,
		Audit:               audit,
		Logger:              log.New(&sb, "", 0),
	})
	if err != nil {
		t.Fatal(err)
	}
	conn, _ := svc.OpenConn("c-log")
	if _, err := conn.Decode([]byte{0x82}); err != nil {
		t.Fatal(err)
	}
	out := sb.String()
	for _, want := range []string{"req=", "conn=c-log", "phase=step", "phase=outcome", ":method"} {
		if !strings.Contains(out, want) {
			t.Fatalf("log output missing %q:\n%s", want, out)
		}
	}
}

func TestAuditRequired(t *testing.T) {
	if _, err := New(Config{}); err == nil {
		t.Fatal("New without Audit must fail")
	}
}
