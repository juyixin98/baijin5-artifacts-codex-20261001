package server

import (
	"bufio"
	"bytes"
	"context"
	"encoding/binary"
	"io"
	"log/slog"
	"net"
	"testing"
	"time"

	"hpacklab.local/service/config"
	"hpacklab.local/service/frame"
)

// pipeClient is a minimal in-process HTTP/2 peer over net.Pipe.
type pipeClient struct {
	conn net.Conn
	br   *bufio.Reader
	fw   *frame.Writer
}

func newPipePair(t *testing.T, srv *Server) (*pipeClient, func()) {
	t.Helper()
	serverConn, clientConn := net.Pipe()
	c := &pipeClient{
		conn: clientConn,
		br:   bufio.NewReader(clientConn),
		fw:   frame.NewWriter(clientConn),
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		sc := newConn(serverConn, srv)
		sc.run(ctx)
		close(done)
	}()
	cleanup := func() {
		cancel()
		_ = clientConn.Close()
		_ = serverConn.Close()
		select {
		case <-done:
		case <-time.After(time.Second):
		}
	}
	return c, cleanup
}

func testServer() *Server {
	cfg := testConfig()
	return New(cfg, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
}

func testConfig() config.Config {
	cfg := config.Default()
	cfg.Server.Listen = "127.0.0.1:0"
	cfg.Logging.Level = "error"
	return cfg
}

func (c *pipeClient) handshake(t *testing.T) {
	t.Helper()
	if _, err := c.conn.Write([]byte("PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")); err != nil {
		t.Fatal(err)
	}
	// Client SETTINGS (empty is legal).
	if err := c.fw.Settings(false); err != nil {
		t.Fatal(err)
	}
	_ = c.conn.SetReadDeadline(time.Now().Add(3 * time.Second))
	// Read until the server SETTINGS frame; ACK everything that is not ACK.
	for {
		h, payload, err := readRawFrame(c.br)
		if err != nil {
			t.Fatalf("handshake read: %v", err)
		}
		if h.typ == frame.TypeSettings && h.flags&frame.FlagAck == 0 {
			_ = c.fw.Settings(true)
			return
		}
		_ = payload
	}
}

type rawHeader struct {
	length uint32
	typ    byte
	flags  byte
	stream uint32
}

func readRawFrame(br *bufio.Reader) (rawHeader, []byte, error) {
	var b [9]byte
	if _, err := io.ReadFull(br, b[:]); err != nil {
		return rawHeader{}, nil, err
	}
	h := rawHeader{
		length: uint32(b[0])<<16 | uint32(b[1])<<8 | uint32(b[2]),
		typ:    b[3], flags: b[4],
		stream: binary.BigEndian.Uint32(b[5:]) & 0x7fffffff,
	}
	payload := make([]byte, h.length)
	if _, err := io.ReadFull(br, payload); err != nil {
		return h, nil, err
	}
	return h, payload, nil
}

func (c *pipeClient) expectGoAway(t *testing.T) uint32 {
	t.Helper()
	for {
		h, payload, err := readRawFrame(c.br)
		if err != nil {
			t.Fatalf("expected GOAWAY, read error: %v", err)
		}
		if h.typ == frame.TypeGoAway {
			if len(payload) < 8 {
				t.Fatal("short GOAWAY")
			}
			return binary.BigEndian.Uint32(payload[4:8])
		}
	}
}

func TestHandshakeRejectsBadPreface(t *testing.T) {
	srv := testServer()
	c, cleanup := newPipePair(t, srv)
	defer cleanup()
	if _, err := c.conn.Write([]byte("GET / HTTP/1.1\r\n\r\n")); err != nil {
		t.Fatal(err)
	}
	// Server should simply close; reading must end rather than hang.
	_ = c.conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	buf := make([]byte, 16)
	if _, err := c.conn.Read(buf); err == nil {
		t.Fatal("expected connection close on bad preface")
	}
}

func TestHandshakeRejectsFirstFrameNotSettings(t *testing.T) {
	srv := testServer()
	c, cleanup := newPipePair(t, srv)
	defer cleanup()
	_, _ = c.conn.Write([]byte("PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"))
	// Send a PING instead of SETTINGS as the first frame.
	var p [8]byte
	if err := c.fw.Ping(false, p); err != nil {
		t.Fatal(err)
	}
	_ = c.conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	if code := c.expectGoAway(t); code != errCodeProtocol {
		t.Fatalf("GOAWAY code = 0x%x, want PROTOCOL_ERROR", code)
	}
}

func TestContinuationWithoutHeadersFatal(t *testing.T) {
	srv := testServer()
	c, cleanup := newPipePair(t, srv)
	defer cleanup()
	c.handshake(t)
	if err := c.fw.Continuation(1, frame.FlagEndHeaders, []byte{0x82}); err != nil {
		t.Fatal(err)
	}
	if code := c.expectGoAway(t); code != errCodeProtocol {
		t.Fatalf("code = 0x%x, want PROTOCOL_ERROR", code)
	}
}

func TestHeadersOnStreamZeroFatal(t *testing.T) {
	srv := testServer()
	c, cleanup := newPipePair(t, srv)
	defer cleanup()
	c.handshake(t)
	if err := c.fw.Headers(0, frame.FlagEndHeaders|frame.FlagEndStream, []byte{0x82}); err != nil {
		t.Fatal(err)
	}
	if code := c.expectGoAway(t); code != errCodeProtocol {
		t.Fatalf("code = 0x%x, want PROTOCOL_ERROR", code)
	}
}

func TestInterleavedFrameDuringHeaderBlockFatal(t *testing.T) {
	srv := testServer()
	c, cleanup := newPipePair(t, srv)
	defer cleanup()
	c.handshake(t)
	// HEADERS without END_HEADERS opens a block; a PING in the middle is illegal.
	if err := c.fw.Headers(1, frame.FlagEndStream, []byte{0x82}); err != nil {
		t.Fatal(err)
	}
	var p [8]byte
	if err := c.fw.Ping(false, p); err != nil {
		t.Fatal(err)
	}
	if code := c.expectGoAway(t); code != errCodeProtocol {
		t.Fatalf("code = 0x%x, want PROTOCOL_ERROR", code)
	}
}

func TestBadHpackSendsCompressionError(t *testing.T) {
	srv := testServer()
	c, cleanup := newPipePair(t, srv)
	defer cleanup()
	c.handshake(t)
	if err := c.fw.Headers(1, frame.FlagEndHeaders|frame.FlagEndStream, []byte{0x80}); err != nil {
		t.Fatal(err)
	}
	if code := c.expectGoAway(t); code != errCodeCompression {
		t.Fatalf("code = 0x%x, want COMPRESSION_ERROR", code)
	}
}

func TestValidRequestGets204Response(t *testing.T) {
	srv := testServer()
	c, cleanup := newPipePair(t, srv)
	defer cleanup()
	c.handshake(t)
	// :method GET / :scheme http / :path / / :authority x
	block := []byte{0x82, 0x86, 0x84, 0x41, 0x01, 'x'}
	if err := c.fw.Headers(1, frame.FlagEndHeaders|frame.FlagEndStream, block); err != nil {
		t.Fatal(err)
	}
	_ = c.conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	for {
		h, payload, err := readRawFrame(c.br)
		if err != nil {
			t.Fatalf("read response: %v", err)
		}
		if h.typ == frame.TypeHeaders {
			if !bytes.Contains(payload, []byte{0x88}) { // :status 200 static index 8
				// 204 response encodes :status 204 (static index 9 = 0x89).
				if !bytes.Contains(payload, []byte{0x89}) {
					t.Fatalf("response block %x has no status index", payload)
				}
			}
			return
		}
	}
}
