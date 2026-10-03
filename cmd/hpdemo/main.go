// Command hpdemo runs a local, self-contained verification session of the
// hpacklab stack: it encodes and decodes a synthetic multi-block session
// over two isolated connections, prints the explainable step log for each
// processed block, persists the audit trail to SQLite, and exits non-zero
// if any expectation fails.
//
// Usage:
//
//	go run ./cmd/hpdemo [-audit /path/to/audit.db]
package main

import (
	"flag"
	"log"
	"os"

	"hpacklab/service"
	"hpacklab/state"
)

func main() {
	auditPath := flag.String("audit", "", "SQLite audit path (default: in-memory)")
	flag.Parse()

	path := *auditPath
	if path == "" {
		path = ":memory:"
	}
	audit, err := service.OpenAudit(path)
	if err != nil {
		log.Fatalf("open audit: %v", err)
	}
	defer audit.Close()

	logger := log.New(os.Stdout, "", 0)
	svc, err := service.New(service.Config{
		MaxDynamicTableSize: 4096,
		Limits:              state.DefaultLimits,
		Audit:               audit,
		Logger:              logger,
	})
	if err != nil {
		log.Fatalf("new service: %v", err)
	}

	failures := 0
	check := func(ok bool, msg string, args ...any) {
		if !ok {
			failures++
			logger.Printf("CHECK-FAIL: "+msg, args...)
		}
	}

	// --- Session 1: multi-block request sequence on one connection. ---
	client, _ := svc.OpenConn("client")
	server, _ := svc.OpenConn("server")

	blocks := [][]state.Field{
		{{Name: ":method", Value: "GET"}, {Name: ":scheme", Value: "http"},
			{Name: ":path", Value: "/"}, {Name: ":authority", Value: "www.example.com"}},
		{{Name: ":method", Value: "GET"}, {Name: ":scheme", Value: "http"},
			{Name: ":path", Value: "/"}, {Name: ":authority", Value: "www.example.com"},
			{Name: "cache-control", Value: "no-cache"}},
		{{Name: ":method", Value: "GET"}, {Name: ":scheme", Value: "https"},
			{Name: ":path", Value: "/index.html"}, {Name: ":authority", Value: "www.example.com"},
			{Name: "custom-key", Value: "custom-value"},
			{Name: "authorization", Value: "Bearer synthetic", Sensitive: true}},
	}
	for i, in := range blocks {
		logger.Printf("=== block %d: encode on conn=client ===", i)
		block, _, err := client.Encode(in)
		if err != nil {
			log.Fatalf("encode block %d: %v", i, err)
		}
		logger.Printf("=== block %d: decode on conn=server (%d bytes) ===", i, len(block))
		res, err := server.Decode(block)
		if err != nil {
			log.Fatalf("decode block %d: %v", i, err)
		}
		check(len(res.Fields) == len(in), "block %d: got %d fields, want %d", i, len(res.Fields), len(in))
		for j := range in {
			if j < len(res.Fields) && res.Fields[j] != in[j] {
				check(false, "block %d field %d: got %+v want %+v", i, j, res.Fields[j], in[j])
			}
		}
	}
	// Encoder and decoder tables must be byte-identical after the session.
	check(client.Encoder().Table().Dyn.Size() == server.Decoder().Table().Dyn.Size(),
		"table size mismatch: enc=%d dec=%d",
		client.Encoder().Table().Dyn.Size(), server.Decoder().Table().Dyn.Size())
	// The sensitive field must not be in either dynamic table.
	for _, e := range server.Decoder().Table().Dyn.Entries() {
		check(e.Name != "authorization", "sensitive field leaked into decoder table: %+v", e)
	}

	// --- Session 2: connection isolation. ---
	other, _ := svc.OpenConn("other")
	check(other.Decoder().Table().Dyn.Len() == 0,
		"fresh connection sees %d dynamic entries, want 0", other.Decoder().Table().Dyn.Len())

	// --- Session 3: failure handling and desync. ---
	bad, _ := svc.OpenConn("bad")
	res, err := bad.Decode([]byte{0x80}) // index 0
	check(err != nil && res.Failure == "index-zero",
		"index-0 failure: err=%v category=%q", err, res.Failure)
	_, err = bad.Decode([]byte{0x82})
	check(err == state.ErrDesync, "post-failure decode: err=%v, want ErrDesync", err)

	// --- Audit summary. ---
	fails, err := audit.Failures()
	if err != nil {
		log.Fatalf("audit failures query: %v", err)
	}
	logger.Printf("=== audit: %d recorded failure(s) ===", len(fails))
	for _, f := range fails {
		logger.Printf("failure req=%s conn=%s %s", f.RequestID, f.ConnID, f.Detail)
	}

	if failures > 0 {
		logger.Printf("RESULT: FAIL (%d check(s) failed)", failures)
		os.Exit(1)
	}
	logger.Printf("RESULT: PASS (all checks succeeded, audit at %q)", path)
}
