package server

import (
	"bufio"
	"encoding/binary"
	"io"
	"net"
	"testing"
	"time"

	"h2svc/internal/config"
	"h2svc/internal/frame"
	"h2svc/internal/hpack"
	"h2svc/internal/journal"
)

// startTestServer boots a server on an ephemeral port with an in-memory
// journal and returns it plus the journal.
func startTestServer(t *testing.T, mutate func(*config.Config)) (*Server, *journal.Journal) {
	t.Helper()
	cfg := config.Default()
	cfg.ListenAddr = "127.0.0.1:0"
	cfg.ReadTimeoutMs = 5000
	cfg.WriteTimeoutMs = 5000
	if mutate != nil {
		mutate(&cfg)
	}
	j, err := journal.Open(":memory:", "e2e-"+t.Name(), "{}")
	if err != nil {
		t.Fatalf("journal: %v", err)
	}
	srv := New(cfg, j)
	if err := srv.Listen(); err != nil {
		t.Fatalf("listen: %v", err)
	}
	go srv.Serve()
	t.Cleanup(func() {
		srv.Close()
		j.Close()
	})
	return srv, j
}

// handshake writes the client preface and an empty SETTINGS, then reads and
// ACKs the server SETTINGS and consumes the server's SETTINGS ACK.
func handshake(t *testing.T, nc net.Conn) {
	t.Helper()
	if _, err := io.WriteString(nc, frame.ClientPreface); err != nil {
		t.Fatalf("preface: %v", err)
	}
	writeFrame(t, nc, frame.Settings, 0, 0, nil)
	h, _ := readFrame(t, nc)
	if h.Type != frame.Settings || h.Flags&frame.FlagAck != 0 {
		t.Fatalf("want server SETTINGS, got %s", h)
	}
	writeFrame(t, nc, frame.Settings, frame.FlagAck, 0, nil)
	h, _ = readFrame(t, nc)
	if h.Type != frame.Settings || h.Flags&frame.FlagAck == 0 {
		t.Fatalf("want server SETTINGS ACK, got %s", h)
	}
}

func writeFrame(t *testing.T, nc net.Conn, typ frame.Type, flags uint8, stream uint32, payload []byte) {
	t.Helper()
	h := frame.Header{Length: uint32(len(payload)), Type: typ, Flags: flags, StreamID: stream}
	hdr := h.Marshal()
	if _, err := nc.Write(append(hdr[:], payload...)); err != nil {
		t.Fatalf("write %s: %v", typ, err)
	}
}

func readFrame(t *testing.T, nc net.Conn) (frame.Header, []byte) {
	t.Helper()
	h, err := frame.ReadHeader(nc)
	if err != nil {
		t.Fatalf("read header: %v", err)
	}
	p := make([]byte, h.Length)
	if _, err := io.ReadFull(nc, p); err != nil {
		t.Fatalf("read payload: %v", err)
	}
	return h, p
}

func dial(t *testing.T, addr string) net.Conn {
	t.Helper()
	nc, err := net.DialTimeout("tcp", addr, 3*time.Second)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	if err := nc.SetDeadline(time.Now().Add(5 * time.Second)); err != nil {
		t.Fatalf("deadline: %v", err)
	}
	t.Cleanup(func() { nc.Close() })
	return nc
}

func requestBlock(path string) []byte {
	var b []byte
	b = append(b, hpack.EncodeIndexed(2)...) // :method: GET
	b = append(b, hpack.EncodeLiteralWithoutIndexing(":path", path)...)
	return b
}

// TestEndToEndGetRoot exercises the full TCP path: preface, SETTINGS
// exchange, GET /, and a framed 200 response, then verifies the journal
// recorded the run.
func TestEndToEndGetRoot(t *testing.T) {
	srv, j := startTestServer(t, nil)
	nc := dial(t, srv.Addr())
	handshake(t, nc)

	writeFrame(t, nc, frame.Headers, frame.FlagEndHeaders|frame.FlagEndStream, 1, requestBlock("/"))

	h, _ := readFrame(t, nc)
	if h.Type != frame.Headers || h.StreamID != 1 {
		t.Fatalf("want response HEADERS on stream 1, got %s", h)
	}
	h, payload := readFrame(t, nc)
	if h.Type != frame.Data || string(payload) != "hello h2\n" {
		t.Fatalf("want DATA 'hello h2\\n', got %s %q", h, payload)
	}
	h, _ = readFrame(t, nc)
	if h.Type != frame.Data || h.Flags&frame.FlagEndStream == 0 {
		t.Fatalf("want END_STREAM DATA, got %s", h)
	}

	// The journal must correlate events with the run identity.
	n, err := j.CountEvents()
	if err != nil {
		t.Fatalf("count events: %v", err)
	}
	if n == 0 {
		t.Fatal("journal recorded no events for a served connection")
	}
	t.Logf("run_id=%s journal_events=%d", j.RunID(), n)
}

// TestEndToEndConnError verifies a connection-level protocol violation
// produces GOAWAY with the right code and the connection closes.
func TestEndToEndConnError(t *testing.T) {
	srv, _ := startTestServer(t, nil)
	nc := dial(t, srv.Addr())
	handshake(t, nc)

	// DATA on stream 0 is a connection-level PROTOCOL_ERROR.
	writeFrame(t, nc, frame.Data, 0, 0, []byte("x"))
	h, payload := readFrame(t, nc)
	if h.Type != frame.GoAway {
		t.Fatalf("want GOAWAY, got %s", h)
	}
	code := frame.ErrCode(binary.BigEndian.Uint32(payload[4:8]))
	if code != frame.ProtocolError {
		t.Fatalf("want PROTOCOL_ERROR, got %s", code)
	}
	// Server must close the connection after GOAWAY.
	if _, err := frame.ReadHeader(nc); err == nil {
		t.Fatal("expected connection close after GOAWAY")
	}
}

// TestEndToEndSlowConsumerNoDeadlock: the client stops reading while the
// server streams a large body. The bounded queue plus pending buffer must
// absorb backpressure without deadlock; once the client resumes, the body
// completes.
func TestEndToEndSlowConsumerNoDeadlock(t *testing.T) {
	srv, _ := startTestServer(t, func(c *config.Config) {
		c.SendQueueCapacity = 4
		c.LargeBodyBytes = 300000
		c.MaxPendingBodyBytes = 1 << 20
	})
	nc := dial(t, srv.Addr())
	handshake(t, nc)

	writeFrame(t, nc, frame.Headers, frame.FlagEndHeaders|frame.FlagEndStream, 1, requestBlock("/large"))

	// Read only the response HEADERS, then stall longer than a flush cycle.
	h, _ := readFrame(t, nc)
	if h.Type != frame.Headers {
		t.Fatalf("want HEADERS, got %s", h)
	}
	time.Sleep(300 * time.Millisecond)

	// Grant a big window and read the whole body; server flow control plus
	// the bounded queue must deliver exactly LargeBodyBytes bytes.
	writeFrame(t, nc, frame.WindowUpdate, 0, 0, mustUint32(t, 1<<20))
	writeFrame(t, nc, frame.WindowUpdate, 0, 1, mustUint32(t, 1<<20))
	total := 0
	for {
		h, payload := readFrame(t, nc)
		if h.Type != frame.Data {
			t.Fatalf("want DATA, got %s", h)
		}
		total += len(payload)
		if h.Flags&frame.FlagEndStream != 0 {
			break
		}
	}
	if total != 300000 {
		t.Fatalf("body bytes: want 300000, got %d", total)
	}
}

func mustUint32(t *testing.T, v uint32) []byte {
	t.Helper()
	p := make([]byte, 4)
	binary.BigEndian.PutUint32(p, v)
	return p
}

// TestEndToEndBadPreface: a non-HTTP/2 greeting is rejected by closing the
// connection without crashing the server.
func TestEndToEndBadPreface(t *testing.T) {
	srv, _ := startTestServer(t, nil)
	nc := dial(t, srv.Addr())
	// A full-length but wrong preface is rejected immediately.
	bad := []byte("GET / HTTP/1.1\r\nHost: x\r\n\r\n")
	if _, err := nc.Write(bad); err != nil {
		t.Fatalf("write: %v", err)
	}
	r := bufio.NewReader(nc)
	if _, err := r.ReadString('\n'); err != io.EOF {
		// Server closes without a response; any read must end in EOF/error.
		if err == nil {
			t.Fatal("expected connection close for bad preface")
		}
	}
}
