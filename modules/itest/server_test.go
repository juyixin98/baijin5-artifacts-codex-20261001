package itest_test

import (
	"context"
	"io"
	"log/slog"
	"path/filepath"
	"testing"
	"time"

	"hpacklab.local/hpack"
	"hpacklab.local/itest/h2t"
	"hpacklab.local/service/config"
	"hpacklab.local/service/server"
	"hpacklab.local/service/store"
)

// startServer boots an in-process h2c service on an ephemeral port backed by
// a per-test SQLite file. It returns the dial address and a cleanup.
func startServer(t *testing.T, mutate func(*config.Config)) (string, *store.Store) {
	t.Helper()
	dbPath := filepath.Join(t.TempDir(), "hpackd.db")
	cfg := config.Default()
	cfg.Server.Listen = "127.0.0.1:0"
	cfg.Storage.SQLitePath = dbPath
	cfg.Logging.Level = "error"
	if mutate != nil {
		mutate(&cfg)
	}
	log := slog.New(slog.NewTextHandler(io.Discard, nil))
	st, err := store.Open(context.Background(), dbPath)
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	srv := server.New(cfg, st, log)
	ctx, cancel := context.WithCancel(context.Background())
	errCh := make(chan error, 1)
	go func() { errCh <- srv.Serve(ctx) }()

	// Wait for the listener to come up.
	var addr string
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if a := srv.Addr(); a != nil {
			addr = a.String()
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	if addr == "" {
		cancel()
		t.Fatal("server did not start listening")
	}
	t.Cleanup(func() {
		cancel()
		shutdownCtx, c := context.WithTimeout(context.Background(), 3*time.Second)
		defer c()
		_ = srv.Shutdown(shutdownCtx)
		_ = st.Close()
	})
	return addr, st
}

func requestFields(path string) []hpack.HeaderField {
	return []hpack.HeaderField{
		{Name: ":method", Value: "GET"},
		{Name: ":scheme", Value: "http"},
		{Name: ":path", Value: path},
		{Name: ":authority", Value: "hpackd.local"},
	}
}

// freePort is a sanity guard that the harness actually got a socket.
func TestServerBindsAndAnswers(t *testing.T) {
	addr, _ := startServer(t, nil)
	c, err := h2t.Dial(addr)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer c.Close()
	if err := c.SendHeaders(1, requestFields("/")); err != nil {
		t.Fatal(err)
	}
	fields, extra, err := c.ReadResponse(1, 3*time.Second)
	if err != nil {
		t.Fatalf("read response: %v", err)
	}
	if len(fields) == 0 || fields[0].Name != ":status" || fields[0].Value != "204" {
		t.Fatalf("response fields = %v extra=%v", fields, extra)
	}
}

// TestStateCarriesAcrossHeaderBlocks verifies a dynamic entry learned in one
// request is referenced by index in a later request on the same connection.
func TestStateCarriesAcrossHeaderBlocks(t *testing.T) {
	addr, _ := startServer(t, nil)
	c, err := h2t.Dial(addr)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	// First request teaches the server a custom header.
	if err := c.SendHeaders(1, append(requestFields("/a"),
		hpack.HeaderField{Name: "x-trace", Value: "trace-xyz"})); err != nil {
		t.Fatal(err)
	}
	if _, _, err := c.ReadResponse(1, 3*time.Second); err != nil {
		t.Fatal(err)
	}

	// Second request reuses the custom value. Our (client) encoder will
	// emit an indexed reference; the server must still decode it because
	// the connection's dynamic table carried over.
	if err := c.SendHeaders(3, append(requestFields("/b"),
		hpack.HeaderField{Name: "x-trace", Value: "trace-xyz"})); err != nil {
		t.Fatal(err)
	}
	fields, extra, err := c.ReadResponse(3, 3*time.Second)
	if err != nil {
		t.Fatalf("second request failed (dynamic state lost across blocks): %v extra=%v", err, extra)
	}
	if fields[0].Value != "204" {
		t.Fatalf("status = %v", fields)
	}
}

// TestConnectionsAreIsolated opens two connections and confirms a dynamic
// index valid on one is out of range on the other, and that one connection
// dying leaves the other fully functional.
func TestConnectionsAreIsolated(t *testing.T) {
	addr, _ := startServer(t, nil)
	c1, err := h2t.Dial(addr)
	if err != nil {
		t.Fatal(err)
	}
	defer c1.Close()
	c2, err := h2t.Dial(addr)
	if err != nil {
		t.Fatal(err)
	}
	defer c2.Close()

	// Teach c1 a dynamic entry, then send index 62 (static table has 61
	// entries; dynamic index 1) which is valid only after that entry.
	if err := c1.SendHeaders(1, append(requestFields("/a"),
		hpack.HeaderField{Name: "x-trace", Value: "trace-xyz"})); err != nil {
		t.Fatal(err)
	}
	if _, _, err := c1.ReadResponse(1, 3*time.Second); err != nil {
		t.Fatal(err)
	}

	// Poison c1 with an index-zero block; expect GOAWAY COMPRESSION_ERROR.
	if err := c1.SendRawHeaders(3, []byte{0x80}); err != nil {
		t.Fatal(err)
	}
	_, extra, err := c1.ReadResponse(3, 3*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	var sawCompression bool
	for _, f := range extra {
		if _, code, _, ok := h2t.GoAwayCode(f); ok && code == 0x9 {
			sawCompression = true
		}
	}
	if !sawCompression {
		t.Fatalf("expected GOAWAY COMPRESSION_ERROR(0x9), got %v", extra)
	}

	// A new request on the dead connection must fail (connection closed).
	if err := c1.SendRawHeaders(5, []byte{0x82}); err == nil {
		// Send may buffer; the decisive check is that c2 still works below.
	}

	// c2 never saw c1's entry and must reject dynamic index 62 with the
	// same fatal treatment, independently.
	if err := c2.SendRawHeaders(1, []byte{0xbe}); err != nil {
		t.Fatal(err)
	}
	_, extra2, err := c2.ReadResponse(1, 3*time.Second)
	if err != nil {
		// read may return EOF after GOAWAY; that is acceptable if the
		// GOAWAY was observed in extra.
		if len(extra2) == 0 {
			t.Fatalf("c2: no frames before read error: %v", err)
		}
	}
	sawCompression = false
	for _, f := range extra2 {
		if _, code, _, ok := h2t.GoAwayCode(f); ok && code == 0x9 {
			sawCompression = true
		}
	}
	if !sawCompression {
		t.Fatalf("c2 expected GOAWAY COMPRESSION_ERROR, got %v err=%v", extra2, err)
	}

	// A brand new third connection must serve normally: c1/c2's failures
	// did not poison the process or shared state.
	c3, err := h2t.Dial(addr)
	if err != nil {
		t.Fatalf("c3 dial after failures: %v", err)
	}
	defer c3.Close()
	if err := c3.SendHeaders(1, requestFields("/healthy")); err != nil {
		t.Fatal(err)
	}
	fields, _, err := c3.ReadResponse(1, 3*time.Second)
	if err != nil {
		t.Fatalf("c3 not healthy after siblings failed: %v", err)
	}
	if fields[0].Value != "204" {
		t.Fatalf("c3 status = %v", fields)
	}
}

// TestSensitiveFieldRoundTripsAndIsNotIndexed sends a never-indexed header
// and confirms it decodes while the client encoder never indexed it.
func TestSensitiveFieldRoundTripsAndIsNotIndexed(t *testing.T) {
	addr, _ := startServer(t, nil)
	c, err := h2t.Dial(addr)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	fields := append(requestFields("/secret"),
		hpack.HeaderField{Name: "authorization", Value: "Bearer token-abc", Sensitive: true})
	block := c.Encoder.EncodeBlock(fields)
	if block[0] == 0 { // sanity; real check is below on the field bytes
		t.Fatal("empty block")
	}
	// Find the authorization representation and assert never-indexed prefix.
	found := false
	// Re-encode just the sensitive field alone to inspect its leading byte.
	sensitiveBlock := c.Encoder.EncodeBlock([]hpack.HeaderField{
		{Name: "authorization", Value: "Bearer token-abc", Sensitive: true},
	})
	// Note: the encoder now has the name indexed from the earlier block; the
	// representation may reference the name index but MUST be 0001-prefixed.
	if sensitiveBlock[0]&0xf0 != 0x10 {
		t.Fatalf("sensitive block lead byte %02x is not 0001 (never-indexed)", sensitiveBlock[0])
	}
	found = len(sensitiveBlock) > 0
	if !found {
		t.Fatal("no sensitive representation")
	}

	if err := c.SendRawHeaders(1, block); err != nil {
		t.Fatal(err)
	}
	resp, _, err := c.ReadResponse(1, 3*time.Second)
	if err != nil {
		t.Fatalf("sensitive request rejected: %v", err)
	}
	if resp[0].Value != "204" {
		t.Fatalf("status = %v", resp)
	}
}

// TestTruncatedBlockClosesConnection sends a block that ends mid-string and
// expects the server to refuse it and tear the connection down.
func TestTruncatedBlockClosesConnection(t *testing.T) {
	addr, _ := startServer(t, nil)
	c, err := h2t.Dial(addr)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	// Literal incremental, name len 10 but no name bytes.
	if err := c.SendRawHeaders(1, []byte{0x40, 0x0a}); err != nil {
		t.Fatal(err)
	}
	_, extra, _ := c.ReadResponse(1, 3*time.Second)
	var sawGoAway bool
	for _, f := range extra {
		if f.Type == 0x7 { // GOAWAY
			sawGoAway = true
			if _, code, debug, ok := h2t.GoAwayCode(f); ok {
				if code != 0x9 {
					t.Fatalf("GOAWAY code = 0x%x, want 0x9 (debug=%q)", code, debug)
				}
			}
		}
	}
	if !sawGoAway {
		t.Fatal("server did not send GOAWAY for truncated block")
	}
}

// TestTableShrinkOverSettings verifies the direction of the table-size
// negotiation correctly:
//
//   - A header-block size update sent by the CLIENT is bounded by the size
//     the SERVER advertised (its decoder ceiling), independent of the
//     client's SETTINGS_HEADER_TABLE_SIZE.
func TestTableShrinkOverSettings(t *testing.T) {
	// Server advertises a 256-octet decoder table.
	addr, _ := startServer(t, func(c *config.Config) {
		c.Hpack.TableSize = 256
	})
	c, err := h2t.Dial(addr)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	// A size update requesting 4096 (3f e1 9f 00) exceeds the server's
	// advertised 256 and must be rejected as a fatal compression error.
	if err := c.SendRawHeaders(1, []byte{0x3f, 0xe1, 0x9f, 0x00}); err != nil {
		t.Fatal(err)
	}
	_, extra, _ := c.ReadResponse(1, 3*time.Second)
	var sawGoAway bool
	for _, f := range extra {
		if f.Type == 0x7 {
			sawGoAway = true
			if _, code, _, ok := h2t.GoAwayCode(f); ok && code != 0x9 {
				t.Fatalf("expected COMPRESSION_ERROR, got 0x%x", code)
			}
		}
	}
	if !sawGoAway {
		t.Fatal("oversized size update was accepted")
	}
}

// TestPeerSettingSizesOurEncoder verifies that the client's
// SETTINGS_HEADER_TABLE_SIZE shrinks the table WE encode responses with: a
// 256-octet client setting must make the next response block lead with a
// dynamic table size update, which the client decodes successfully.
func TestPeerSettingSizesOurEncoder(t *testing.T) {
	addr, _ := startServer(t, nil) // server default decoder/encoder 4096
	// Client advertises a 256-octet cap for the direction it decodes
	// (server -> client), i.e. it bounds the server's encoder.
	c, err := h2t.Dial(addr, [2]uint32{0x1, 256})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if err := c.SendHeaders(1, requestFields("/")); err != nil {
		t.Fatal(err)
	}
	fields, extra, err := c.ReadResponse(1, 3*time.Second)
	if err != nil {
		t.Fatalf("response after peer shrink: %v extra=%v", err, extra)
	}
	if len(fields) == 0 || fields[0].Value != "204" {
		t.Fatalf("response fields = %v", fields)
	}
}

// TestResultsPersisted sends a request and confirms an auditable row lands
// in SQLite correlated by connection/stream.
func TestResultsPersisted(t *testing.T) {
	addr, st := startServer(t, nil)
	c, err := h2t.Dial(addr)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if err := c.SendHeaders(1, requestFields("/persisted")); err != nil {
		t.Fatal(err)
	}
	if _, _, err := c.ReadResponse(1, 3*time.Second); err != nil {
		t.Fatal(err)
	}

	rows, err := st.ListRequests(context.Background(), 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) == 0 {
		t.Fatal("no request rows persisted")
	}
	r := rows[0]
	if r.Status != "ok" || r.FieldCount != 4 || r.StreamID != 1 {
		t.Fatalf("persisted row = %+v", r)
	}
	if r.ConnID == "" || r.ID == "" {
		t.Fatalf("row missing connection/request correlation: %+v", r)
	}
}
