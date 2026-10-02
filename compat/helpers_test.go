package compat_test

import (
	"context"
	"encoding/binary"
	"fmt"
	"net"
	"path/filepath"
	"testing"
	"time"

	"modbusfixture/config"
	"modbusfixture/fixture"
)

// latency profile name constants mirrored from config (kept local so the
// black-box package spells the exact configuration values it depends on).
const (
	configLatencyNone   = "none"
	configLatencyJitter = "txn_jitter"
)

// distinct16 returns 16 distinct big-endian-recognizable values used to
// seed unit 1, so a mis-correlated out-of-order response would return the
// wrong value and fail the identity assertions.
func distinct16() []uint16 {
	v := make([]uint16, 16)
	for i := range v {
		v[i] = 0x4000 + uint16(i)
	}
	return v
}

// harness starts the real fixture binary-equivalent on loopback with
// ephemeral ports. Tests talk TCP like any external Modbus master; nothing
// in this package reaches into the server's in-process state except via
// the documented HTTP control plane and the SQLite file.
type harness struct {
	cfg      config.Config
	bundle   *fixture.Bundle
	modbus   string // host:port of the Modbus listener
	control  string // host:port of the HTTP control plane
	dbPath   string
	shutdown func()
}

func startHarness(t *testing.T, latency string, jitterMS int) *harness {
	t.Helper()
	dir := t.TempDir()
	cfg := config.Config{
		Listen:        "127.0.0.1:0",
		ControlListen: "127.0.0.1:0",
		SQLiteDB:      filepath.Join(dir, "audit.db"),
		Workers:       8,
		Latency:       latency,
		JitterSlotMS:  jitterMS,
		Units: []config.UnitConfig{
			// Unit 1: full 125-register bank; the first 16 hold distinct
			// values 0x4000+i so an out-of-order response attached to the
			// wrong request is detectable by value, not just txn id.
			{UnitID: 0x01, RegisterCount: 125, InitialValues: distinct16()},
			// Unit 5: separate bank carrying the hand-computed golden prefix.
			{UnitID: 0x05, RegisterCount: 16,
				InitialValues: []uint16{0x1122, 0x3344}},
			// Unit 0xFF: empty bank used by the range/golden vectors.
			{UnitID: 0xFF, RegisterCount: 125},
		},
	}
	if err := cfg.Validate(); err != nil {
		t.Fatalf("config validate: %v", err)
	}

	bundle, err := fixture.Build(cfg, testLogger(t))
	if err != nil {
		t.Fatalf("build fixture: %v", err)
	}
	if err := bundle.Server.Listen(); err != nil {
		t.Fatalf("listen: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		if err := bundle.Server.Serve(ctx); err != nil {
			t.Logf("serve: %v", err)
		}
	}()
	// Bind synchronously so Addr() is stable before any goroutine reads it.
	if err := bundle.Control.Listen(); err != nil {
		cancel()
		t.Fatalf("control listen: %v", err)
	}
	go func() {
		if err := bundle.Control.Serve(); err != nil {
			t.Logf("control serve: %v", err)
		}
	}()

	h := &harness{
		cfg:     cfg,
		bundle:  bundle,
		modbus:  bundle.Server.Addr().String(),
		control: bundle.Control.Addr().String(),
		dbPath:  cfg.SQLiteDB,
	}
	h.shutdown = func() {
		cancel()
		_ = bundle.Server.Close()
		_ = bundle.Control.Close()
		_ = bundle.Close()
	}
	return h
}

// dial opens a raw TCP connection to the fixture.
func (h *harness) dial(t *testing.T) net.Conn {
	t.Helper()
	c, err := net.Dial("tcp", h.modbus)
	if err != nil {
		t.Fatalf("dial %s: %v", h.modbus, err)
	}
	return c
}

// writeFull sends all bytes (fragmented when frag>0: the first frag bytes
// go in one Write, the rest in a second one, modelling half packets).
func writeFull(t *testing.T, c net.Conn, b []byte, frag int) {
	t.Helper()
	if frag <= 0 || frag >= len(b) {
		if _, err := c.Write(b); err != nil {
			t.Fatalf("write: %v", err)
		}
		return
	}
	if _, err := c.Write(b[:frag]); err != nil {
		t.Fatalf("write frag1: %v", err)
	}
	// Small delay so the peer processes the partial frame first.
	time.Sleep(5 * time.Millisecond)
	if _, err := c.Write(b[frag:]); err != nil {
		t.Fatalf("write frag2: %v", err)
	}
}

// readExactFrame reads one Modbus TCP frame using header-driven sizing,
// independently of the mbap package under test.
func readExactFrame(t *testing.T, c net.Conn) []byte {
	t.Helper()
	hdr := make([]byte, 7)
	if _, err := readFull(c, hdr); err != nil {
		t.Fatalf("read header: %v", err)
	}
	length := binary.BigEndian.Uint16(hdr[4:])
	body := make([]byte, int(length)-1)
	if _, err := readFull(c, body); err != nil {
		t.Fatalf("read body (length=%d): %v", length, err)
	}
	return append(hdr, body...)
}

func readFull(c net.Conn, b []byte) (int, error) {
	n := 0
	for n < len(b) {
		nn, err := c.Read(b[n:])
		n += nn
		if err != nil {
			return n, err
		}
	}
	return n, nil
}

// readFrameBytesOrClose returns one frame, or errConnClosed when the peer
// shuts the connection before a full frame arrives.
func readFrameOrClose(t *testing.T, c net.Conn) ([]byte, error) {
	t.Helper()
	_ = c.SetReadDeadline(time.Now().Add(2 * time.Second))
	hdr := make([]byte, 7)
	if _, err := readFull(c, hdr); err != nil {
		return nil, fmt.Errorf("conn closed before header: %w", err)
	}
	length := binary.BigEndian.Uint16(hdr[4:])
	if length < 2 || length > 254 {
		return hdr, fmt.Errorf("illegal length field %d in response", length)
	}
	body := make([]byte, int(length)-1)
	if _, err := readFull(c, body); err != nil {
		return nil, fmt.Errorf("conn closed mid-body: %w", err)
	}
	return append(hdr, body...), nil
}
