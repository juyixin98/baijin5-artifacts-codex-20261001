package server

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/hex"
	"net"
	"testing"
	"time"

	"stunlab/internal/evidence"
	"stunlab/internal/store"
	"stunlab/internal/stun"
)

type harness struct {
	srv  *Server
	st   *store.Store
	logs bytes.Buffer
	ctx  context.Context
}

func startServer(t *testing.T, network, key string) *harness {
	t.Helper()
	h := &harness{ctx: context.Background()}
	var err error
	h.st, err = store.Open("")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { h.st.Close() })
	lg := evidence.NewLogger(evidence.NewRunID("stund-test"), "stund", &h.logs, h.st, "")
	addr := "127.0.0.1:0"
	if network == "udp6" {
		addr = "[::1]:0"
	}
	h.srv, err = Listen(Config{
		Network: network, Addr: addr, Key: []byte(key), Logger: lg, Store: h.st,
	})
	if err != nil {
		if network == "udp6" {
			t.Skipf("IPv6 loopback unavailable: %v", err)
		}
		t.Fatal(err)
	}
	t.Cleanup(func() { h.srv.Close() })
	go func() { _ = h.srv.Serve(h.ctx) }()
	return h
}

// udpClient opens a client socket on the same network.
func udpClient(t *testing.T, network string, dst *net.UDPAddr) (*net.UDPConn, *net.UDPAddr) {
	t.Helper()
	c, err := net.DialUDP(network, nil, dst)
	if err != nil {
		if network == "udp6" {
			t.Skipf("IPv6 dial unavailable: %v", err)
		}
		t.Fatal(err)
	}
	t.Cleanup(func() { c.Close() })
	_ = c.SetDeadline(time.Now().Add(2 * time.Second))
	return c, c.LocalAddr().(*net.UDPAddr)
}

func bindingRequest(txn stun.TransactionID, attrs []stun.Attribute, key []byte) []byte {
	var b []byte
	var err error
	if len(key) > 0 {
		b, err = stun.AddMessageIntegrity(stun.MethodBinding, stun.ClassRequest, txn, attrs, key)
	} else {
		b, err = stun.Marshal(stun.MethodBinding, stun.ClassRequest, txn, attrs)
	}
	if err != nil {
		panic(err)
	}
	return b
}

func roundTrip(t *testing.T, c *net.UDPConn, req []byte) (*stun.Message, []byte) {
	t.Helper()
	if _, err := c.Write(req); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 1024)
	n, err := c.Read(buf)
	if err != nil {
		t.Fatalf("read response: %v", err)
	}
	raw := append([]byte(nil), buf[:n]...)
	m, err := stun.UnmarshalMessage(raw)
	if err != nil {
		t.Fatalf("parse response: %v\nhex=%s", err, hex.EncodeToString(raw))
	}
	return m, raw
}

func TestBindingSuccessReflectsIPv4(t *testing.T) {
	h := startServer(t, "udp4", "")
	c, local := udpClient(t, "udp4", h.srv.LocalAddr())
	txn, _ := stun.NewTransactionID()
	m, _ := roundTrip(t, c, bindingRequest(txn, nil, nil))

	if m.Class != stun.ClassSuccessResponse || m.TransactionID != txn {
		t.Fatalf("unexpected response class=%04x txn=%x", uint16(m.Class), m.TransactionID)
	}
	v, ok := m.Attribute(stun.AttrXORMappedAddress)
	if !ok {
		t.Fatal("missing XOR-MAPPED-ADDRESS")
	}
	addr, err := stun.DecodeXORMappedAddress(v, txn)
	if err != nil {
		t.Fatal(err)
	}
	if !addr.IP.Equal(local.IP) || addr.Port != local.Port {
		t.Fatalf("reflected %s:%d != client %s", addr.IP, addr.Port, local)
	}
	// Legacy MAPPED-ADDRESS must agree.
	if v, ok := m.Attribute(stun.AttrMappedAddress); ok {
		ma, err := stun.DecodeMappedAddress(v)
		if err != nil || !ma.IP.Equal(local.IP) || ma.Port != local.Port {
			t.Fatalf("MAPPED-ADDRESS %+v err=%v disagrees", ma, err)
		}
	}
	if n := waitExchangeCount(t, h, h.srv.cfg.Logger.RunID(), 1, time.Second); n != 1 {
		t.Fatalf("exchange rows = %d, want 1", n)
	}
}

// waitExchangeCount polls SQLite because the server legitimately records the
// outcome immediately AFTER transmitting the response.
func waitExchangeCount(t *testing.T, h *harness, runID string, want int, within time.Duration) int {
	t.Helper()
	deadline := time.Now().Add(within)
	for {
		n, err := h.st.ExchangeCount(runID)
		if err != nil {
			t.Fatal(err)
		}
		if n >= want || time.Now().After(deadline) {
			return n
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func TestBindingSuccessReflectsIPv6(t *testing.T) {
	h := startServer(t, "udp6", "")
	c, local := udpClient(t, "udp6", h.srv.LocalAddr())
	txn, _ := stun.NewTransactionID()
	m, _ := roundTrip(t, c, bindingRequest(txn, nil, nil))

	v, ok := m.Attribute(stun.AttrXORMappedAddress)
	if !ok {
		t.Fatal("missing XOR-MAPPED-ADDRESS")
	}
	addr, err := stun.DecodeXORMappedAddress(v, txn)
	if err != nil {
		t.Fatal(err)
	}
	if !addr.IP.Equal(local.IP) || addr.Port != local.Port {
		t.Fatalf("reflected %s:%d != client %s", addr.IP, addr.Port, local)
	}
	if addr.IP.To4() != nil {
		t.Fatal("expected a 16-byte IPv6 address")
	}
}

func TestPaddedAttributeAccepted(t *testing.T) {
	// 3-byte SOFTWARE value exercises the 0-3 padding path on the server.
	h := startServer(t, "udp4", "")
	c, _ := udpClient(t, "udp4", h.srv.LocalAddr())
	txn, _ := stun.NewTransactionID()
	attrs := []stun.Attribute{{Type: stun.AttrSoftware, Value: []byte("abc")}}
	m, _ := roundTrip(t, c, bindingRequest(txn, attrs, nil))
	if m.Class != stun.ClassSuccessResponse {
		t.Fatalf("class = %04x", uint16(m.Class))
	}
}

func TestUnknownRequiredAttributeYields420(t *testing.T) {
	h := startServer(t, "udp4", "")
	c, _ := udpClient(t, "udp4", h.srv.LocalAddr())
	txn, _ := stun.NewTransactionID()
	attrs := []stun.Attribute{
		{Type: stun.AttributeType(0x0099), Value: []byte{0xde, 0xad}}, // unknown, required
		{Type: stun.AttrSoftware, Value: []byte("ok")},                // optional, fine
	}
	m, _ := roundTrip(t, c, bindingRequest(txn, attrs, nil))
	if m.Class != stun.ClassErrorResponse {
		t.Fatalf("class = %04x, want error response", uint16(m.Class))
	}
	ecv, ok := m.Attribute(stun.AttrErrorCode)
	if !ok {
		t.Fatal("missing ERROR-CODE")
	}
	ec, err := stun.DecodeErrorCode(ecv)
	if err != nil || ec.Code != 420 {
		t.Fatalf("error code = %+v err=%v, want 420", ec, err)
	}
	uv, ok := m.Attribute(stun.AttrUnknownAttributes)
	if !ok {
		t.Fatal("420 missing UNKNOWN-ATTRIBUTES")
	}
	unk, err := stun.DecodeUnknownAttributes(uv)
	if err != nil || len(unk) != 1 || unk[0] != 0x0099 {
		t.Fatalf("unknown list = %v err=%v", unk, err)
	}
}

func TestMalformedRequestYields400OrDrop(t *testing.T) {
	h := startServer(t, "udp4", "")
	c, _ := udpClient(t, "udp4", h.srv.LocalAddr())

	// Bad cookie but leading bits zero -> 400 response.
	bad := make([]byte, 24)
	bad[0], bad[1] = 0x00, 0x01
	binary.BigEndian.PutUint16(bad[2:4], 4)
	binary.BigEndian.PutUint32(bad[4:8], 0xdeadbeef)
	m, _ := roundTrip(t, c, bad)
	if m.Class != stun.ClassErrorResponse {
		t.Fatalf("class = %04x", uint16(m.Class))
	}
	ec, _ := stun.DecodeErrorCode(m.Attributes[0].Value)
	if ec.Code != 400 {
		t.Fatalf("code = %d, want 400", ec.Code)
	}

	// Leading bits set: not STUN, must be silently dropped.
	if _, err := c.Write([]byte{0xC0, 0x01, 0, 0}); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 1024)
	if err := c.SetReadDeadline(time.Now().Add(300 * time.Millisecond)); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Read(buf); err == nil {
		t.Fatal("server replied to non-STUN datagram")
	}
}

func TestIntegrityEnforced(t *testing.T) {
	h := startServer(t, "udp4", "labkey")
	c, _ := udpClient(t, "udp4", h.srv.LocalAddr())
	txn, _ := stun.NewTransactionID()

	// Valid integrity -> success.
	m, _ := roundTrip(t, c, bindingRequest(txn, nil, []byte("labkey")))
	if m.Class != stun.ClassSuccessResponse {
		t.Fatalf("valid integrity class = %04x", uint16(m.Class))
	}
	if err := stun.VerifyMessageIntegrity(m.Raw, []byte("labkey")); err != nil {
		t.Fatalf("response integrity: %v", err)
	}

	// Tampered transaction id -> silently discarded.
	raw := bindingRequest(txn, nil, []byte("labkey"))
	raw[8] ^= 0x01
	if _, err := c.Write(raw); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 1024)
	_ = c.SetReadDeadline(time.Now().Add(300 * time.Millisecond))
	if _, err := c.Read(buf); err == nil {
		t.Fatal("server replied to tampered message")
	}

	// Wrong key -> silently discarded.
	txn2, _ := stun.NewTransactionID()
	if _, err := c.Write(bindingRequest(txn2, nil, []byte("wrong"))); err != nil {
		t.Fatal(err)
	}
	_ = c.SetReadDeadline(time.Now().Add(300 * time.Millisecond))
	if _, err := c.Read(buf); err == nil {
		t.Fatal("server replied to wrong-key message")
	}

	// Evidence records the integrity failures (1 success + 2 failures rows).
	if n := waitExchangeCount(t, h, h.srv.cfg.Logger.RunID(), 3, time.Second); n != 3 {
		t.Fatalf("exchange rows = %d, want 3", n)
	}
}

func TestPaddingOverrunRejectedWith400(t *testing.T) {
	h := startServer(t, "udp4", "")
	c, _ := udpClient(t, "udp4", h.srv.LocalAddr())
	// SOFTWARE value length 3 but body declared 7 bytes: padding byte missing.
	b, _ := hex.DecodeString("000100072112a4420102030405060708090a0b0c80220003010203")
	m, _ := roundTrip(t, c, b)
	ec, _ := stun.DecodeErrorCode(m.Attributes[0].Value)
	if ec.Code != 400 {
		t.Fatalf("code = %d, want 400", ec.Code)
	}
}

func TestUnsupportedMethodYields400(t *testing.T) {
	h := startServer(t, "udp4", "")
	c, _ := udpClient(t, "udp4", h.srv.LocalAddr())
	txn, _ := stun.NewTransactionID()
	// Method 0x0002 (SharedSecret) encoded as a request: type = 0x0002.
	b, _ := stun.Marshal(stun.Method(0x0002), stun.ClassRequest, txn, nil)
	m, _ := roundTrip(t, c, b)
	if m.Class != stun.ClassErrorResponse {
		t.Fatalf("class = %04x, want error", uint16(m.Class))
	}
	ec, _ := stun.DecodeErrorCode(m.Attributes[0].Value)
	if ec.Code != 400 {
		t.Fatalf("code = %d, want 400", ec.Code)
	}
}

func TestServerRunsWithoutLoggerOrStore(t *testing.T) {
	// Minimal config exercises the nil-logger / nil-store branches and must
	// still answer correctly.
	srv, err := Listen(Config{Network: "udp4", Addr: "127.0.0.1:0"})
	if err != nil {
		t.Fatal(err)
	}
	defer srv.Close()
	go func() { _ = srv.Serve(context.Background()) }()

	c, local := udpClient(t, "udp4", srv.LocalAddr())
	txn, _ := stun.NewTransactionID()
	m, _ := roundTrip(t, c, bindingRequest(txn, nil, nil))
	v, _ := m.Attribute(stun.AttrXORMappedAddress)
	addr, err := stun.DecodeXORMappedAddress(v, txn)
	if err != nil {
		t.Fatal(err)
	}
	if addr.Port != local.Port {
		t.Fatalf("reflected port %d != %d", addr.Port, local.Port)
	}
}

func TestListenRejectsBadAddressAndNetwork(t *testing.T) {
	if _, err := Listen(Config{Network: "udp4", Addr: "not-an-address"}); err == nil {
		t.Fatal("expected resolve error")
	}
	if _, err := Listen(Config{Network: "udp99", Addr: "127.0.0.1:0"}); err == nil {
		t.Fatal("expected listen error for bogus network")
	}
}
